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
from config import CINEMAS, parse_preferred_seats, parse_showtime
from seating import READ_SEATS, plan_seats, seat_key, seat_label
from ticket_types import get_ticket_type


def resource(name):
    return Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / name


def target_url(settings):
    if settings.url == "demo://seats":
        return resource("seats_fixture.html").as_uri()
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


def ticket_panel(page, category):
    title = page.locator('.panel-title').filter(has_text=re.compile(r'^\s*' + re.escape(category) + r'\s*$'))
    return page.locator('.panel').filter(has=title)


def expand_ticket_panel(page, panel, category, log, stop, timeout):
    toggle = panel.locator('.panel-title a[data-toggle="collapse"]')
    if toggle.count() == 0:
        return  # A page may render the ticket table without an accordion.
    if toggle.count() != 1:
        raise ValueError(f"{category}的展開按鈕不唯一。")
    target = toggle.get_attribute('data-target') or toggle.get_attribute('href') or ''
    target_id = target.split('#', 1)[1] if '#' in target else ''
    content = panel.locator('.panel-collapse').filter(has=page.locator('table'))
    if not target_id or content.count() != 1 or content.get_attribute('id') != target_id:
        raise ValueError(f"{category}的展開按鈕與內容區塊不符。")

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
        log(f"正在展開{category}區塊…")
        toggle.click(timeout=5000)
        try:
            wait_booking(page, expanded, stop, f"{category}展開動畫", min(timeout, 2))
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
            log(f"展開事件未完成，已同步修正{category}內容的收合狀態。")
    if stop.is_set():
        raise Cancelled()
    toggle.evaluate("el => {el.classList.remove('collapsed');el.setAttribute('aria-expanded','true')}")
    content.evaluate("el => el.setAttribute('aria-expanded','true')")
    wait_booking(page, expanded, stop, f"{category}內容展開", timeout)


def select_quantity_and_continue(page, settings, log, stop, timeout=30):
    kind = get_ticket_type(settings.ticket_type)
    panel = ticket_panel(page, kind.category)
    wait_booking(page, lambda: panel.count() > 0, stop, f"{kind.category}區塊", timeout)
    if panel.count() != 1:
        raise ValueError(f"頁面有多個{kind.category}區塊，請手動確認。")
    expand_ticket_panel(page, panel, kind.category, log, stop, timeout)
    selector = settings.tickets_selector or QUANTITY_SELECTOR
    label = page.locator('td .spName, td').filter(has_text=re.compile(r'^\s*' + re.escape(kind.name) + r'\s*$'))
    rows = panel.locator('table tr').filter(has=label).filter(visible=True)
    wait_booking(page, lambda: rows.count() > 0, stop, f"{kind.category}的{kind.name}列", timeout)
    if rows.count() != 1:
        raise ValueError(f"{kind.category}區塊有多個{kind.name}列，請手動確認。")
    candidates = rows.locator(selector).filter(visible=True)
    wait_booking(page, lambda: candidates.count() > 0, stop, f"{kind.category}{kind.name}列的票數選單", timeout)
    if candidates.count() != 1:
        raise ValueError(f"{kind.category}的{kind.name}列有多個票數選單，請手動確認，或設定該列內唯一的票數 CSS 選擇器後重試。")
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
    log(f"已設定{kind.label} {settings.tickets} 張，點擊繼續。")
    next_button.click(timeout=5000)
    wait_booking(page, lambda: page.url != before or (not next_button.is_visible() and candidates.count() == 0),
                 stop, "下一個購票步驟", timeout)
    log("已完成票數設定並進入下一步。")


def select_seats_and_continue(page, settings, log, stop, timeout=30):
    if settings.seat_mode == 'manual':
        log('未設定自動選位，請在瀏覽器選位。')
        return
    tables = page.locator('#divSeatMap table.Seating-Area')

    def read():
        return tables.evaluate_all(READ_SEATS)

    wait_booking(page, lambda: bool(read()), stop, '座位圖', timeout)
    initial = read()
    plan = plan_seats(initial, settings)
    preferences = parse_preferred_seats(settings.seat_preferred)
    if preferences:
        matched = [seat_label(s) for s in plan if seat_label(s) in preferences]
        if matched:
            log('優先使用指定座位：' + '、'.join(matched) + '；其餘座位依選位規則補足。')
        else:
            log('指定座位不存在、不可選或無法依設定湊足張數，改用原本選位規則。')
    expected = {seat_key(s) for s in initial if s['selected']}
    wanted = {seat_key(s) for s in plan}

    def seat_cell(seat):
        return tables.nth(seat['area']).locator('tr').nth(seat['gridRow']).locator(':scope > td, :scope > th').nth(seat['gridCol'])

    def checked_seat(seat):
        current = read()
        if {seat_key(s) for s in current if s['selected']} != expected:
            raise ValueError('已選座位發生變動，請手動確認。')
        match = next((s for s in current if seat_key(s) == seat_key(seat)), None)
        if not match or any(match[k] != seat[k] for k in ('row', 'label', 'backendRow', 'backendSeat', 'areaCode')):
            raise ValueError('座位圖改變，請手動確認。')
        return match

    log('預計選取：' + '、'.join(f"{s['row']} 排 {s['label']} 號" for s in plan))
    site_selection = page.evaluate('() => Array.isArray(window.SelectSeats) ? [...window.SelectSeats] : null')
    if site_selection is not None:
        target_ids = {s['id'] for s in plan}
        if len(target_ids) != settings.tickets or not all(target_ids):
            raise ValueError('座位 ID 不明確，請手動確認。')
        def site_matches(ids):
            return (page.evaluate('() => window.SelectSeats') == ids
                    and {s['id'] for s in read() if s['selected']} == set(ids))

        wait_booking(page, lambda: site_matches(page.evaluate('() => [...window.SelectSeats]')),
                     stop, '網站選位清單與座位圖片同步', timeout)
        site_selection = page.evaluate('() => [...window.SelectSeats]')
        if len(site_selection) > settings.tickets or len(set(site_selection)) != len(site_selection):
            raise ValueError('網站選位清單與設定張數不符，請手動確認。')
        # The site's click handler appends a seat and evicts the oldest at capacity.
        # An overlapping target can be evicted, so recompute missing targets each time.
        for _ in range(settings.tickets * 2):
            if set(site_selection) == target_ids:
                break
            if stop.is_set():
                raise Cancelled()
            seat = next(s for s in plan if s['id'] not in site_selection)
            match = checked_seat(seat)
            if not match['available'] or not site_matches(site_selection):
                raise ValueError('預計座位已不可選或網站選位清單變動，請手動確認。')
            after = site_selection[1:] if len(site_selection) == settings.tickets else list(site_selection)
            after.append(seat['id'])
            log(f"正在點選 {seat['row']} 排 {seat['label']} 號（網站將自動替換最早的預選座位）。")
            seat_cell(seat).click(timeout=5000)
            wait_booking(page, lambda: site_matches(after), stop,
                         f"網站確認替換座位 {seat['row']}-{seat['label']}", timeout)
            site_selection = after
            expected = {seat_key(s) for s in read() if s['selected']}
            log('網站目前選位：' + '、'.join(site_selection))
        if set(site_selection) != target_ids:
            raise ValueError('網站未完成指定座位替換，請手動確認。')
        # All desired seats are now selected; the toggle adapter below has no work.
        initial = read()
    for seat in initial:
        if not seat['selected'] or seat_key(seat) in wanted:
            continue
        if stop.is_set():
            raise Cancelled()
        checked_seat(seat)
        log(f"正在取消預選 {seat['row']} 排 {seat['label']} 號。")
        seat_cell(seat).click(timeout=5000)
        expected.remove(seat_key(seat))
        wait_booking(page, lambda: {seat_key(s) for s in read() if s['selected']} == expected,
                     stop, f"取消預選 {seat['row']}-{seat['label']}（未確認時請手動接手）", timeout)
        log(f"已取消預選 {seat['row']} 排 {seat['label']} 號。")
    for seat in plan:
        if stop.is_set():
            raise Cancelled()
        match = checked_seat(seat)
        if seat_key(seat) in expected:
            log(f"保留預選 {seat['row']} 排 {seat['label']} 號。")
            continue
        if not match['available']:
            raise ValueError('預計座位已不可選或座位圖改變，請手動確認。')
        log(f"正在點選 {seat['row']} 排 {seat['label']} 號。")
        seat_cell(seat).click(timeout=5000)
        expected.add(seat_key(seat))
        wait_booking(page, lambda: {seat_key(s) for s in read() if s['selected']} == expected,
                     stop, f"網站確認 {seat['row']}-{seat['label']}（未確認時請手動接手）", timeout)
        log(f"已選取 {seat['row']} 排 {seat['label']} 號。")
    button = page.locator('button#btnCheckOut')
    wait_booking(page, lambda: button.count() == 1 and button.is_visible() and button.is_enabled()
                 and button.get_attribute('aria-disabled') != 'true'
                 and 'disabled' not in (button.get_attribute('class') or '').split(), stop, '選位繼續按鈕', timeout)
    if {seat_key(s) for s in read() if s['selected']} != expected or len(expected) != settings.tickets:
        raise ValueError('選取座位與設定張數不符，請手動確認。')
    if stop.is_set():
        raise Cancelled()
    if site_selection is not None and not site_matches(site_selection):
        raise ValueError('送出前網站選位清單發生變動，請手動確認。')
    before = page.url
    log('已核對選取座位與張數，正在點擊繼續並等待網站驗證。')
    button.click(timeout=5000)
    wait_booking(page, lambda: page.url != before or (not button.is_visible() and not read()),
                 stop, '選位後的下一步（不重複提交）', timeout)
    if urlsplit(page.url).path.rstrip('/').lower().endswith('/error'):
        raise ValueError('網站選位驗證或保留座位失敗，已進入錯誤頁，請手動確認。')
    log('已完成選位並進入下一步。')


class CheckoutLoginError(ValueError):
    """A credential-free diagnostic safe to display in the application log."""


def checkout_login_form(page):
    forms = page.locator('form')
    metadata = forms.evaluate_all("els => els.map((el, index) => ({index, action: el.action, method: el.method}))")
    candidates = []
    origin = urlsplit(page.url)
    for item in metadata:
        action = urlsplit(item['action'])
        if not action.path.rstrip('/').lower().endswith('/home/vieshowloginforcheckout'):
            continue
        form = forms.nth(item['index'])
        if not form.is_visible():
            continue
        if (origin.scheme != 'https' or origin.hostname != 'sales.vscinemas.com.tw'
                or action.scheme != origin.scheme or action.netloc.lower() != origin.netloc.lower()
                or action.username or action.password or item['method'].lower() != 'post'):
            raise CheckoutLoginError('結帳登入表單的提交位置或方法不符，請手動確認。')
        candidates.append(form)
    if len(candidates) > 1:
        raise CheckoutLoginError('頁面出現多個結帳登入表單，請手動確認。')
    return candidates[0] if candidates else None


def login_for_checkout(page, settings, log, stop, timeout=30, discovery_timeout=2):
    """Submit only the checkout login form, once; never operate payment controls."""
    try:
        if stop.is_set():
            raise Cancelled()
        page.wait_for_load_state('domcontentloaded', timeout=timeout * 1000)
        deadline = time.monotonic() + min(discovery_timeout, timeout)
        while True:
            if stop.is_set():
                raise Cancelled()
            form = checkout_login_form(page)
            if form is not None:
                break
            if time.monotonic() >= deadline:
                log('未出現結帳登入表單，保留目前頁面，付款流程請手動操作。')
                return 'not_required'
            page.wait_for_timeout(100)
        if not settings.login_email or not settings.login_password:
            log('已發現結帳登入表單，但尚未設定完整帳密，請在瀏覽器手動登入。')
            return 'missing_credentials'
        email = form.locator('input[name="UserName"]')
        password = form.locator('input[name="Password"]')
        for field, kind, value in ((email, 'email', settings.login_email),
                                   (password, 'password', settings.login_password)):
            if (field.count() != 1 or (field.get_attribute('type') or '').lower() != kind
                    or not field.is_visible() or not field.is_editable()
                    or field.get_attribute('form') is not None):
                raise CheckoutLoginError('結帳登入欄位不明確或無法輸入，請手動確認。')
            limit = field.evaluate('el => el.maxLength')
            if limit >= 0 and len(value.encode('utf-16-le')) // 2 > limit:
                raise CheckoutLoginError('設定的登入資料超過網站欄位長度限制，請重新設定。')
        submit = form.locator('button[type="submit"], button:not([type]), input[type="submit"]').filter(visible=True)
        if submit.count() != 1:
            raise CheckoutLoginError('結帳登入的送出按鈕不明確，請手動確認。')
        if (submit.get_attribute('form') is not None or submit.get_attribute('formaction') is not None
                or submit.get_attribute('formmethod') is not None
                or submit.get_attribute('formtarget') not in (None, '', '_self')
                or form.get_attribute('target') not in (None, '', '_self')):
            raise CheckoutLoginError('結帳登入按鈕有額外提交設定，請手動確認。')
        wait_booking(page, lambda: submit.is_enabled()
                     and submit.get_attribute('aria-disabled') != 'true'
                     and 'disabled' not in (submit.get_attribute('class') or '').split(),
                     stop, '結帳登入按鈕', timeout)

        def errors(current):
            texts = current.locator('.validation-summary-errors, .field-validation-error, .alert-danger, [role="alert"]').filter(visible=True).all_text_contents()
            return tuple(text.strip() for text in texts if text.strip() not in ('', '*'))

        initial_errors = errors(form)
        destination = form.evaluate('el => el.action')
        if stop.is_set():
            raise Cancelled()
        email.fill(settings.login_email, timeout=5000)
        if stop.is_set():
            raise Cancelled()
        password.fill(settings.login_password, timeout=5000)
        if not email.evaluate('el => el.checkValidity()') or not password.evaluate('el => el.checkValidity()'):
            raise CheckoutLoginError('登入資料未通過網站欄位驗證，請手動確認。')
        if stop.is_set():
            raise Cancelled()
        if checkout_login_form(page) is None or form.evaluate('el => el.action') != destination:
            raise CheckoutLoginError('填寫後結帳登入表單已改變，請手動確認。')
        if any(submit.get_attribute(name) is not None for name in ('form', 'formaction', 'formmethod', 'formtarget')):
            raise CheckoutLoginError('填寫後登入按鈕的提交設定已改變，請手動確認。')
        log('已填入會員登入欄位，正在送出結帳登入表單。')
        submit.click(timeout=5000)

        def finished():
            if urlsplit(page.url).path.rstrip('/').lower().endswith('/error'):
                raise CheckoutLoginError('登入後進入網站錯誤頁，請手動確認。')
            current = checkout_login_form(page)
            if current is None:
                return True
            current_errors = errors(current)
            if current_errors and current_errors != initial_errors:
                raise CheckoutLoginError('網站顯示登入驗證錯誤，請在瀏覽器確認；不會重複送出。')
            return False

        wait_booking(page, finished, stop, '結帳登入結果', timeout)
        log('結帳登入表單已送出，後續付款或額外驗證請在瀏覽器操作。')
        return 'submitted'
    except (Cancelled, CheckoutLoginError):
        raise
    except Exception:
        # Playwright error messages can include fill values; never expose them.
        raise CheckoutLoginError('登入操作未完成或等待逾時，請在瀏覽器確認；不會自動重試。') from None


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
                    # Either requested or other ticket panels identify the quantity page.
                    # Once it is ready, let the shared selector report a missing product.
                    if candidate.locator('.panel .panel-title').count() == 0 or candidate.locator('a#btnDoNext').count() == 0:
                        continue
                    self.post_pending = False  # At most one continue click per attempt.
                    select_quantity_and_continue(candidate, self.settings, lambda s: self.emit('log', s), self.stop)
                    if self.settings.seat_mode != 'manual':
                        self.emit('status', '正在依偏好選位…')
                        select_seats_and_continue(candidate, self.settings, lambda s: self.emit('log', s), self.stop)
                        self.emit('status', '正在檢查結帳會員登入…')
                        outcome = login_for_checkout(candidate, self.settings, lambda s: self.emit('log', s), self.stop)
                        self.emit('handoff', '請在瀏覽器手動登入並操作付款' if outcome == 'missing_credentials'
                                  else '已完成自動流程，付款或額外驗證請在瀏覽器操作')
                    else:
                        self.emit('handoff', '已完成票數與繼續，請在瀏覽器接手選位')
                    return
            except Cancelled:
                raise
            except Exception as exc:
                self.post_pending = False
                self.emit('handoff', '流程暫停，瀏覽器保留供手動操作')
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
                    demo = self.settings.url in ("demo://ticket", "demo://seats")
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
                                if self.settings.url == 'demo://seats':
                                    select_seats_and_continue(page, self.settings, lambda s: self.emit('log', s), self.stop_event)
                                    self.emit('handoff', '座位測試完成，請在瀏覽器查看結果')
                                else:
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
