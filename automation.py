"""Site adapter and browser worker. All Playwright calls stay on one thread."""
import queue
import re
import sys
import threading
import time
from pathlib import Path
from datetime import datetime
from urllib.parse import parse_qs, urljoin, urlsplit

from playwright.sync_api import Error, sync_playwright
from config import CINEMAS, parse_showtime


def resource(name):
    return Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / name


def target_url(settings):
    return resource("demo.html").as_uri() if settings.url == "demo://ticket" else settings.url


class Cancelled(Exception):
    pass


def is_ticket_page(url):
    parts = urlsplit(url)
    return (parts.scheme in ("http", "https")
            and parts.hostname == "www.vscinemas.com.tw"
            and re.fullmatch(r"/vsticketingsp(?:[1-9][0-9]*)?/ticketing/ticket\.aspx", parts.path, re.IGNORECASE) is not None)


def same_ticket_area(url, base_url):
    """Keep all subsequent navigation in the area the user actually entered."""
    current, base = urlsplit(url), urlsplit(base_url)
    return (is_ticket_page(url) and current.netloc.lower() == base.netloc.lower()
            and current.path.lower() == base.path.lower())


def ticket_page(pages):
    return next((p for p in reversed(pages) if not p.is_closed() and is_ticket_page(p.url)), None)


def select_cinema_and_first_movie(page, settings, log, stop=None, timeout=30):
    """Run exactly the two authorized steps, including the resulting navigations."""
    stop = stop or threading.Event()
    value = CINEMAS.get(settings.cinema)
    if value is None:
        raise ValueError("請從清單重新選擇影城。")
    area_url = page.url

    def check_stop():
        if stop.is_set():
            raise Cancelled()

    def wait_until(predicate, description):
        deadline = time.monotonic() + timeout
        while True:
            check_stop()
            if page.is_closed():
                raise ValueError("購票分頁已關閉。")
            if not same_ticket_area(page.url, area_url):
                raise ValueError("已離開原本的搶票專區，請返回後重新套用。")
            try:
                if predicate():
                    return
            except Error:
                # A document replacement during navigation is temporary.
                pass
            if time.monotonic() >= deadline:
                raise ValueError(f"等待{description}逾時，請確認網站內容後重新套用。")
            page.wait_for_timeout(150)

    if not is_ticket_page(page.url):
        raise ValueError("尚未進入威秀購票頁。")
    theater = page.locator("#theater")
    wait_until(lambda: theater.count() == 1 and theater.is_visible() and theater.is_enabled(), "影城選單")
    check_stop()
    options = theater.locator("option").evaluate_all("els => els.filter(el => el.value).map(el => ({value: el.value, name: el.textContent.trim()}))")
    if not any(option["value"] == value for option in options):
        available = "、".join(option["name"] for option in options) or "目前無影城"
        raise ValueError(f"此搶票專區未提供「{settings.cinema}」。可選影城：{available}")
    log(f"已偵測搶票專區：{urlsplit(area_url).path}")
    if (theater.input_value(timeout=1000) != value
            or parse_qs(urlsplit(page.url).query).get("cinema") != [value]):
        theater.select_option(value=value, timeout=5000)
    wait_until(lambda: parse_qs(urlsplit(page.url).query).get("cinema") == [value]
               and theater.input_value(timeout=1000) == value, "影城頁面更新")
    log(f"已選擇影城：{settings.cinema}（{value}）")

    # Scope to the first li in the exact requested movie list, not any page link.
    first = page.locator("#movieListBox1 .movieList > li").first.locator("a").first
    wait_until(lambda: first.is_visible() and first.is_enabled(), "第一部電影（可能尚無可售電影）")
    href = first.get_attribute("href", timeout=1000)
    destination = urljoin(page.url, href or "")
    query = parse_qs(urlsplit(destination).query)
    if not same_ticket_area(destination, area_url):
        raise ValueError("第一部電影連結指向不同搶票專區，已停止。")
    if query.get("cinema") != [value] or not query.get("movie"):
        raise ValueError("第一部電影連結與影城不符，已停止，請確認網站內容。")
    title = first.inner_text(timeout=1000).strip()
    check_stop()
    first.click(timeout=5000)
    wait_until(lambda: parse_qs(urlsplit(page.url).query).get("movie") == query["movie"]
               and parse_qs(urlsplit(page.url).query).get("cinema") == [value], "電影頁面更新")
    log(f"已點選第一部電影：{title}。")


def find_showtime_url(page_url, sessions, settings):
    """Match a date's list, then take its first/last li in DOM order."""
    wanted = parse_showtime(settings.showtime)
    if settings.session_position not in ("first", "last"):
        raise ValueError("請選擇第一場或最後一場。")
    matches, available = [], []
    for session in sessions:
        date = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", session["date"])
        if not date:
            continue
        try:
            stamp = datetime(*map(int, date.groups()))
        except ValueError:
            continue
        available.append(stamp.strftime("%Y-%m-%d"))
        if stamp == wanted:
            matches.append(session)
    if not matches:
        choices = "、".join(sorted(set(available))) or "無可辨識場次"
        raise ValueError(f"找不到指定場次 {settings.showtime}。頁面場次：{choices}")
    if len(matches) != 1:
        raise ValueError(f"{settings.showtime} 有多個日期清單，請手動確認，程式未自動選擇。")
    entries = matches[0]["entries"]
    if not entries:
        raise ValueError(f"指定場次 {settings.showtime} 目前無法訂票。")
    session = entries[0 if settings.session_position == "first" else -1]
    if session.get("disabled") or not session.get("href"):
        raise ValueError(f"指定場次 {settings.showtime} 目前無法訂票。")
    destination = urljoin(page_url, session["href"])
    parts, base = urlsplit(destination), urlsplit(page_url)
    query = parse_qs(parts.query)
    code = CINEMAS[settings.cinema].split("|")[0]
    if (parts.scheme != base.scheme or parts.netloc.lower() != base.netloc.lower()
            or parts.path.lower() != urlsplit(urljoin(page_url, "booking.aspx")).path.lower()
            or query.get("cinemacode") != [code] or len(query.get("txtSessionId", [])) != 1
            or not query["txtSessionId"][0].isdigit()):
        raise ValueError("場次連結的專區、影城或場次代碼不符，已停止。")
    return destination


def select_showtime(page, settings, log, stop=None, timeout=30):
    stop = stop or threading.Event()
    base_url = page.url
    if not is_ticket_page(base_url):
        raise ValueError("尚未進入電影場次頁。")
    current_query = parse_qs(urlsplit(base_url).query)
    if current_query.get("cinema") != [CINEMAS.get(settings.cinema)] or not current_query.get("movie"):
        raise ValueError("目前電影場次頁的影城與設定不符。")
    deadline = time.monotonic() + timeout
    while True:
        if stop.is_set():
            raise Cancelled()
        if not same_ticket_area(page.url, base_url) or urlsplit(page.url).query != urlsplit(base_url).query:
            raise ValueError("電影場次頁已變更，請重新套用。")
        try:
            sessions = page.locator("section.movieTime .movieDay").evaluate_all("""days => days.flatMap(day =>
                Array.from(day.querySelectorAll('ul.bookList')).map(list => ({
                    date: day.querySelector('h4')?.textContent || '',
                    entries: Array.from(list.querySelectorAll(':scope > li')).map(li => {
                        const a = li.querySelector(':scope > a');
                        return {href: a?.getAttribute('href'), disabled: !a || a.hasAttribute('disabled') ||
                            a.getAttribute('aria-disabled') === 'true' || li.getAttribute('aria-disabled') === 'true' ||
                            ['disabled','soldout','sold-out'].some(c => a.classList.contains(c) || li.classList.contains(c))};
                    })
                })))""")
            if sessions:
                break
        except Error:
            if page.is_closed():
                raise ValueError("場次頁面已關閉。") from None
        if time.monotonic() >= deadline:
            raise ValueError("等待場次清單逾時，頁面可能尚未開放場次，請確認後重新套用。")
        page.wait_for_timeout(150)
    destination = find_showtime_url(base_url, sessions, settings)
    if stop.is_set():
        raise Cancelled()
    position = "第一場" if settings.session_position == "first" else "最後一場"
    log(f"已找到場次：{settings.showtime} {position}；前往 {destination}")
    response = page.goto(destination, wait_until="domcontentloaded", timeout=30000)
    if response is not None and response.status >= 400:
        raise ValueError(f"場次頁面載入失敗（HTTP {response.status}）。")
    log(f"已開啟指定場次入口：{page.url}")


QUANTITY_SELECTOR = 'select.form-control-s'


def wait_booking(page, predicate, stop, description, timeout=30):
    deadline = time.monotonic() + timeout
    while True:
        if stop.is_set():
            raise Cancelled()
        if page.is_closed():
            raise ValueError("購票分頁已關閉。")
        try:
            if predicate():
                return
        except Error:
            pass
        if time.monotonic() >= deadline:
            raise ValueError(f"等待{description}逾時；瀏覽器保留供手動操作，不會重複送出。")
        page.wait_for_timeout(150)


def submit_normal_booking(page, settings, log, stop, timeout=30):
    if not settings.agree:
        return
    scope = page.locator('#bookNormal')
    wait_booking(page, lambda: scope.count() == 1, stop, "一般票種規定", timeout)
    if not scope.is_visible():
        opener = page.locator('a[href="#bookNormal"]:visible')
        if opener.count() == 1:
            if stop.is_set():
                raise Cancelled()
            opener.click(timeout=5000)
    agree = scope.locator('input#agree[type="checkbox"]')
    submit = scope.locator('input[type="submit"]')
    wait_booking(page, lambda: agree.count() == 1 and agree.is_visible() and agree.is_enabled()
                 and submit.count() == 1 and submit.is_visible() and submit.is_enabled(),
                 stop, "一般票種同意與送出按鈕", timeout)
    # Resolve the checkbox's actual form, not another duplicate #checkform elsewhere.
    form = agree.evaluate("el => ({action: el.form?.action, cinema: el.form?.querySelector('[name=cinemacode]')?.value, session: el.form?.querySelector('[name=txtSessionId]')?.value})")
    query = parse_qs(urlsplit(page.url).query)
    if (urlsplit(form.get('action') or '').hostname != 'sales.vscinemas.com.tw'
            or form.get('cinema') != CINEMAS[settings.cinema].split('|')[0]
            or query.get('txtSessionId') != [form.get('session')]):
        raise ValueError("一般票種表單的目的網站、影城或場次不符，請手動確認。")
    if stop.is_set():
        raise Cancelled()
    agree.check(timeout=5000)
    if not agree.is_checked():
        raise ValueError("一般票種的同意欄位未成功勾選。")
    if stop.is_set():
        raise Cancelled()
    log("已勾選一般票種規定，送出該區塊表單。")
    submit.click(timeout=5000)


def normal_ticket_panel(page):
    title = page.locator('.panel-title').filter(has_text=re.compile(r'^\s*一般票種\s*$'))
    return page.locator('.panel').filter(has=title)


def expand_normal_panel(page, panel, log, stop, timeout):
    toggle = panel.locator('.panel-title a[data-toggle="collapse"]')
    if toggle.count() == 0:
        return  # A page may render the normal-ticket table without an accordion.
    if toggle.count() != 1:
        raise ValueError("一般票種的展開按鈕不唯一。")
    target = toggle.get_attribute('data-target') or toggle.get_attribute('href') or ''
    target_id = target.split('#', 1)[1] if '#' in target else ''
    content = panel.locator('.panel-collapse').filter(has=page.locator('table'))
    if not target_id or content.count() != 1 or content.get_attribute('id') != target_id:
        raise ValueError("一般票種的展開按鈕與內容區塊不符。")

    def expanded():
        return content.evaluate("""el => {
            const style = getComputedStyle(el);
            return !el.hidden && style.display !== 'none' && style.visibility !== 'hidden'
                && el.getBoundingClientRect().height > 0 && !el.classList.contains('collapsing')
                && (!el.classList.contains('collapse') || el.classList.contains('in'));
        }""")

    if not expanded():
        if stop.is_set():
            raise Cancelled()
        log("正在展開一般票種區塊…")
        toggle.click(timeout=5000)
        try:
            wait_booking(page, expanded, stop, "一般票種展開動畫", min(timeout, 2))
        except ValueError:
            if page.is_closed() or stop.is_set():
                raise
            # Recover a stale/ineffective collapse handler only within the verified panel.
            content.evaluate("""el => {
                el.hidden = false;
                el.classList.remove('collapsing');
                el.classList.add('collapse', 'in');
                el.setAttribute('aria-expanded', 'true');
                el.style.height = 'auto';
                el.style.display = 'block';
            }""")
            log("展開事件未完成，已同步修正一般票種內容的收合狀態。")
    if stop.is_set():
        raise Cancelled()
    toggle.evaluate("el => {el.classList.remove('collapsed');el.setAttribute('aria-expanded','true')}")
    content.evaluate("el => el.setAttribute('aria-expanded','true')")
    wait_booking(page, expanded, stop, "一般票種內容展開", timeout)


def select_quantity_and_continue(page, settings, log, stop, timeout=30):
    panel = normal_ticket_panel(page)
    wait_booking(page, lambda: panel.count() > 0, stop, "一般票種區塊", timeout)
    if panel.count() != 1:
        raise ValueError("頁面有多個一般票種區塊，請手動確認。")
    expand_normal_panel(page, panel, log, stop, timeout)
    selector = settings.tickets_selector or QUANTITY_SELECTOR
    full_label = page.locator('td .spName, td').filter(has_text=re.compile(r'^\s*全票\s*$'))
    rows = panel.locator('table tr').filter(has=full_label).filter(visible=True)
    wait_booking(page, lambda: rows.count() > 0, stop, "一般票種的全票列", timeout)
    if rows.count() != 1:
        raise ValueError("一般票種區塊有多個全票列，請手動確認。")
    candidates = rows.locator(selector).filter(visible=True)
    wait_booking(page, lambda: candidates.count() > 0, stop, "一般票種全票列的票數選單", timeout)
    if candidates.count() != 1:
        raise ValueError("一般票種的全票列有多個票數選單，請手動確認，或設定該列內唯一的票數 CSS 選擇器後重試。")
    quantity = candidates.first
    if quantity.evaluate("el => el.tagName") != 'SELECT':
        raise ValueError("票數選擇器必須指向 select 下拉選單。")
    options = quantity.locator('option').evaluate_all("els => els.filter(el => !el.disabled).map(el => el.value)")
    if str(settings.tickets) not in options:
        raise ValueError(f"網站不允許選擇 {settings.tickets} 張，可選張數：{'、'.join(options)}。")
    other_selected = quantity.evaluate("(chosen, selector) => Array.from(document.querySelectorAll(selector)).some(el => el !== chosen && Number(el.value) > 0)", QUANTITY_SELECTOR)
    if other_selected:
        raise ValueError("其他票種已有張數，請手動確認總張數後繼續。")
    if stop.is_set():
        raise Cancelled()
    quantity.select_option(value=str(settings.tickets), timeout=5000)
    if quantity.input_value() != str(settings.tickets):
        raise ValueError("網站未接受設定的票數。")
    next_button = page.locator('a#btnDoNext')
    wait_booking(page, lambda: next_button.count() == 1 and next_button.is_visible() and next_button.is_enabled()
                 and next_button.get_attribute('aria-disabled') != 'true', stop, "繼續按鈕", timeout)
    before = page.url
    if stop.is_set():
        raise Cancelled()
    log(f"已設定一般票種／全票 {settings.tickets} 張，點擊繼續。")
    next_button.click(timeout=5000)
    wait_booking(page, lambda: page.url != before or (not next_button.is_visible() and candidates.count() == 0),
                 stop, "下一個購票步驟", timeout)
    log("已進入下一步。請在瀏覽器接手，程式保持執行且不再自動操作。")


class TicketFlow:
    """Armed while user navigates; one attempt per run or explicit retry."""
    def __init__(self, settings, emit, stop):
        self.settings, self.emit, self.stop = settings, emit, stop
        self.pending = True
        self.post_pending = False
        self.booking_page = None
        self.prior_pages = []

    def arm(self):
        self.pending = True
        self.post_pending = False
        self.emit("status", "等待你手動進入購票專區…")

    def tick(self, pages):
        if self.post_pending:
            try:
                # Only follow the current flow's tab or newly opened tabs.
                for candidate in reversed(pages):
                    if candidate.is_closed() or (candidate is not self.booking_page and candidate in self.prior_pages):
                        continue
                    if urlsplit(candidate.url).hostname != 'sales.vscinemas.com.tw':
                        continue
                    if normal_ticket_panel(candidate).count() == 0:
                        continue
                    self.post_pending = False  # At most one continue click per attempt.
                    select_quantity_and_continue(candidate, self.settings, lambda s: self.emit('log', s), self.stop)
                    self.emit('handoff', '已完成票數與繼續，請在瀏覽器接手')
                    return
            except Cancelled:
                raise
            except Exception as exc:
                self.post_pending = False
                self.emit('status', '流程暫停，瀏覽器保留供手動操作')
                self.emit('log', f'未能完成購票設定：{exc}')
            return
        if not self.pending:
            return
        page = ticket_page(pages)
        if page is None:
            return
        self.pending = False
        self.emit("status", "正在選擇影城與第一部電影…")
        try:
            select_cinema_and_first_movie(page, self.settings, lambda s: self.emit("log", s), self.stop)
            if self.settings.showtime:
                self.emit("status", "正在比對日期並選擇第一場／最後一場…")
                select_showtime(page, self.settings, lambda s: self.emit("log", s), self.stop)
                self.booking_page = page
                self.prior_pages = list(pages)
                if self.settings.agree:
                    submit_normal_booking(page, self.settings, lambda s: self.emit('log', s), self.stop)
                    self.emit('status', '已送出一般票種表單，等待購票頁（登入或驗證請手動完成）')
                else:
                    self.emit('status', '請手動同意並前往訂票；進入購票頁後會自動設定票數')
                self.post_pending = True
            else:
                self.emit("status", "已選擇第一部電影，本階段完成")
        except Cancelled:
            raise
        except Exception as exc:
            self.emit("status", "流程暫停，請查看紀錄後重新套用")
            self.emit("log", f"未能完成流程：{exc}")


def apply_settings(page, settings, log, stop=None):
    """Only set requested form fields. Never click purchase/submit controls."""
    stop = stop or threading.Event()

    def check_stop():
        if stop.is_set():
            raise Cancelled()

    def field(selector, label):
        check_stop()
        item = page.locator(selector) if selector else page.get_by_label(label, exact=True)
        item.first.wait_for(state="visible", timeout=5000)
        if item.count() != 1:
            raise ValueError(f"「{label}」找到多個欄位，請在網站設定指定唯一的 CSS 選擇器。")
        return item

    def set_value(item, value, label):
        check_stop()
        tag = item.evaluate("el => el.tagName.toLowerCase()")
        if tag == "select":
            item.select_option(label=value, timeout=5000)
            selected = item.locator("option:checked").inner_text().strip()
            if selected != value:
                raise ValueError(f"{label}選取結果不符。")
        elif tag == "input":
            item.fill(value, timeout=5000)
            item.press("Tab", timeout=5000)
            if item.input_value() != value:
                raise ValueError(f"網站未接受{label}設定。")
        else:
            raise ValueError(f"{label}是自訂元件，需要依實際網站新增操作流程。")

    set_value(field(settings.cinema_selector, "影城"), settings.cinema, "影城")
    log(f"已設定影城：{settings.cinema}")
    set_value(field(settings.tickets_selector, "票數"), str(settings.tickets), "票數")
    log(f"已設定票數：{settings.tickets}")
    if settings.agree:
        item = field(settings.agree_selector, "我同意")
        check_stop()
        item.check(timeout=5000)
        if not item.is_checked():
            raise ValueError("網站未接受同意勾選。")
        log("已勾選我同意。")
    check_stop()


class BrowserWorker(threading.Thread):
    def __init__(self, settings, events):
        super().__init__(daemon=True)
        self.settings = settings
        self.events = events
        self.stop_event = threading.Event()
        self.commands = queue.Queue()

    def emit(self, kind, text):
        self.events.put((kind, text))

    def run(self):
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(channel="msedge", headless=False)
                try:
                    context = browser.new_context()
                    page = context.new_page()
                    page.set_default_timeout(5000)
                    self.emit("status", "正在開啟網站…")
                    try:
                        page.goto(target_url(self.settings), wait_until="domcontentloaded", timeout=30000)
                    except Exception as exc:
                        self.emit("log", f"載入網站未完成：{exc}")
                    demo = self.settings.url == "demo://ticket"
                    pending = demo
                    flow = TicketFlow(self.settings, self.emit, self.stop_event)
                    if not demo:
                        flow.arm()
                    while browser.is_connected() and not self.stop_event.is_set():
                        pages = [p for p in context.pages if not p.is_closed()]
                        if not pages:
                            break
                        page = pages[-1]
                        if not demo:
                            try:
                                flow.tick(pages)
                            except Cancelled:
                                break
                        if pending:
                            self.emit("status", "正在套用購票設定…")
                            try:
                                apply_settings(page, self.settings, lambda s: self.emit("log", s), self.stop_event)
                                self.emit("status", "設定已套用，請在瀏覽器繼續操作")
                                self.emit("log", "已完成表單設定；瀏覽器保持開啟。")
                            except Cancelled:
                                break
                            except Exception as exc:
                                self.emit("status", "等待手動操作或調整網站設定")
                                self.emit("log", f"未能完成設定：{exc}\n若尚未到購票表單，請手動前往後按「重新套用」。")
                            pending = False
                        try:
                            self.commands.get_nowait()
                            if demo:
                                pending = True
                            else:
                                flow.arm()
                        except queue.Empty:
                            pass
                        try:
                            page.wait_for_timeout(200)
                        except Error:
                            if not browser.is_connected():
                                break
                finally:
                    browser.close()
        except Exception as exc:
            if not self.stop_event.is_set():
                self.emit("log", f"瀏覽器錯誤：{exc}\n請確認已安裝 Microsoft Edge。")
        finally:
            self.emit("done", "已停止")
