"""Eslite movie/session selection using the live links and adjacent date rows."""
import re
import time
import unicodedata
from datetime import date
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import Error
from config import parse_eslite_movie_keywords
from eslite_tickets import EsliteTicketFlow, ticket_session
from eslite_confirmation import EsliteConfirmationFlow


READ_SELECTION = r"""() => {
    const visible = el => !!el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
    const links = root => Array.from(root?.querySelectorAll('a[href]') || [])
        .filter(visible).map(a => ({text: a.innerText.trim(), title: a.title, href: a.href,
            disabled: a.getAttribute('aria-disabled') === 'true' || a.hasAttribute('disabled')}));
    return {
        url: location.href,
        challenge: !!document.querySelector('#challenge-stage, #challenge-running')
            || /just a moment|attention required.*cloudflare/i.test(document.title)
            || Array.from(document.querySelectorAll('iframe[src*="challenges.cloudflare.com"]')).some(visible),
        cinemas: links(document.querySelector('#box_left')),
        movies: links(document.querySelector('#box_center')),
        tables: Array.from(document.querySelectorAll('#box_right table')).filter(visible)
            .map(table => Array.from(table.rows).map(row => ({
                text: row.innerText.trim(), links: links(row)
            })))
    };
}"""


def normalized(value):
    return ' '.join(unicodedata.normalize('NFKC', value).split()).casefold()


def is_selection_page(url):
    parts = urlsplit(url)
    return (parts.scheme == 'https' and parts.netloc.lower() == 'arthouse.eslite.com'
            and parts.path.lower() == '/visselect.aspx')


def query_value(url, name):
    values = parse_qs(urlsplit(url).query).get(name, [])
    return values[0] if len(values) == 1 else None


def cinema_link(snapshot, wanted):
    matches = [link for link in snapshot['cinemas']
               if normalized(wanted) in normalized(link['text'])]
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError('有多家影城符合設定名稱，請手動確認。')
    link = matches[0]
    if (link['disabled'] or not is_selection_page(link['href'])
            or query_value(link['href'], 'visSearchBy') != 'cin'
            or not query_value(link['href'], 'visCinID')):
        raise ValueError('設定的影城連結不可用；不會改選其他影城。')
    return link


def movie_link(snapshot, wanted):
    keywords = parse_eslite_movie_keywords(wanted)
    cinema = query_value(snapshot['url'], 'visCinID')
    if not cinema or query_value(snapshot['url'], 'visSearchBy') != 'cin':
        return None
    if not any(is_selection_page(link['href']) and query_value(link['href'], 'visCinID') == cinema
               for link in snapshot['cinemas']):
        return None
    current = query_value(snapshot['url'], 'visMovieName')
    if not keywords and not current:
        return None
    candidates = [link for link in snapshot['movies']
                  if is_selection_page(link['href']) and not link['disabled']
                  and query_value(link['href'], 'visCinID') == cinema
                  and query_value(link['href'], 'visMovieName')
                  and (any(word in normalized(link['text']) for word in keywords) if keywords
                       else query_value(link['href'], 'visMovieName') == current)]
    if len(keywords) == 1:
        exact = [link for link in candidates if normalized(link['text']) == keywords[0]]
        candidates = exact or candidates
    if len(candidates) > 1:
        raise ValueError('有多個電影項目符合關鍵字，請縮小關鍵字範圍後重新執行；不會任選一項。')
    return candidates[0] if candidates else None


def displayed_date(text, today):
    match = re.match(r'^\s*(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*日',
                     unicodedata.normalize('NFKC', text))
    if not match:
        return None
    year, month, day = match.groups()
    month, day = int(month), int(day)
    # The caller supplies the requested year; never roll a past month into next year.
    year = int(year) if year else today.year
    try:
        return date(year, month, day)
    except ValueError:
        return None


def session_link(snapshot, wanted_date, wanted_time='', today=None, session_position='first'):
    target_date = date.fromisoformat(wanted_date)
    cinema = query_value(snapshot['url'], 'visCinID')
    if session_position not in ('first', 'last'):
        raise ValueError('請選擇第一場或最後一場。')
    groups = []
    for rows in snapshot['tables']:
        for index, row in enumerate(rows[:-1]):
            if displayed_date(row['text'], target_date) != target_date:
                continue
            next_row = rows[index + 1]
            if displayed_date(next_row['text'], target_date) is not None:
                continue
            matches = []
            for link in next_row['links']:
                stamp = re.match(r'^(\d{1,2}):([0-5]\d)(?=\s|$)', normalized(link['title'] or link['text']))
                if not stamp or int(stamp[1]) > 23:
                    continue
                if not wanted_time or f'{int(stamp[1]):02d}:{stamp[2]}' == wanted_time:
                    matches.append(link)
            groups.append(matches)
    if len(groups) > 1:
        raise ValueError('指定日期有多組場次清單，請手動確認。')
    matches = groups[0] if groups else []
    if wanted_time and len(matches) > 1:
        raise ValueError('同日期同時間有多個場次，請手動確認影廳；程式不會任選一場。')
    if not matches:
        return None
    link = matches[0 if wanted_time or session_position == 'first' else -1]
    parts = urlsplit(link['href'])
    if (link['disabled'] or parts.scheme != 'https' or parts.netloc.lower() != 'arthouse.eslite.com'
            or parts.path.lower() != '/visselecttickets.aspx'
            or not cinema or query_value(link['href'], 'cinemacode') != cinema
            or not query_value(link['href'], 'txtSessionId')):
        return None
    return link


class EsliteFlow:
    def __init__(self, settings, emit, stop, today=None):
        self.settings, self.emit, self.stop = settings, emit, stop
        self.today = today
        self.attempted_sessions = set()
        self.page = None
        self.session_target = None
        self.confirmation_flow = None
        self.arm()

    def arm(self):
        if self.confirmation_flow is not None:
            self.active = True
            self.confirmation_flow.deadline = time.monotonic() + 30
            return
        resume = (self.page and not self.page.is_closed() and self.session_target
                  and ticket_session(self.page.url) == ticket_session(self.session_target))
        self.active = True
        if not resume:
            self.page = None
            self.session_target = None
        self.movie_target = None
        self.cinema_target = None
        self.ticket_flow = None
        self.retry_at = None
        self.retry_url = None
        self.retry_count = 0
        self.reselect_cinema = False
        self.deadline = time.monotonic() + 30
        self.last_status = None

    def status(self, message):
        if message != self.last_status:
            self.emit('status', message)
            self.last_status = message

    def retry_selection(self, page, reason):
        if (not self.settings.eslite_movie.strip() or self.ticket_flow is not None
                or self.confirmation_flow is not None or page is None or page.is_closed()
                or not is_selection_page(page.url)):
            self.status(reason)
            return
        self.page = page
        if self.retry_at is None:
            self.retry_at = time.monotonic() + 1
            self.retry_url = page.url
        self.status(f'{reason}；1 秒後重新整理並重試影院、電影與場次')

    def reload_if_due(self):
        if self.retry_at is None:
            return False
        page = self.page
        if page is None or page.is_closed():
            raise ValueError('重試中的誠品分頁已關閉。')
        # A manual navigation or a late session navigation takes precedence over a retry.
        if not is_selection_page(page.url) or page.url != self.retry_url:
            self.retry_at = self.retry_url = None
            return False
        if time.monotonic() < self.retry_at:
            return True
        snapshot = page.evaluate(READ_SELECTION)
        if snapshot['challenge']:
            self.status('偵測到網站驗證，暫停重整；請手動完成驗證')
            return True
        if self.stop.is_set() or page.url != snapshot['url']:
            return True
        self.retry_at = self.retry_url = None
        self.cinema_target = self.movie_target = self.session_target = None
        self.reselect_cinema = True
        self.deadline = time.monotonic() + 30
        self.retry_count += 1
        self.status(f'第 {self.retry_count} 次重新整理；重新選擇影院、電影與場次')
        page.reload(wait_until='domcontentloaded', timeout=10000)
        return True

    def click(self, page, scope, link):
        if self.stop.is_set():
            return
        # Resolve the actual element again just before clicking; do not synthesize IDs or URLs.
        locator = page.locator(scope).locator('a[href]').filter(visible=True)
        matches = []
        for item in locator.all():
            if item.evaluate('(a) => a.href') == link['href']:
                matches.append(item)
        if len(matches) != 1 or not matches[0].is_enabled():
            raise ValueError('頁面連結已變更或不唯一，請確認後重新套用。')
        if not self.stop.is_set():
            matches[0].click(timeout=5000)

    def tick(self, pages):
        if not self.active or self.stop.is_set():
            return
        try:
            if self.confirmation_flow is not None:
                if self.confirmation_flow.tick(self.page):
                    self.active = False
                    self.emit('handoff', '誠品確認頁已就緒，請自行檢查訂單與條款，再點「確定」；程式不送出交易')
                return
            if self.reload_if_due():
                return
            if self.session_target:
                if self.page and not self.page.is_closed() and (
                    ticket_session(self.page.url) == ticket_session(self.session_target)
                ):
                    if self.ticket_flow is None:
                        self.ticket_flow = EsliteTicketFlow(
                            self.settings, self.emit, self.stop, self.session_target, self.attempted_sessions)
                    if self.ticket_flow.tick(self.page):
                        self.confirmation_flow = EsliteConfirmationFlow(
                            self.settings, self.emit, self.stop, self.session_target)
                        self.status('已點擊系統選位，等待訂票確認頁')
                    return
                if self.ticket_flow is not None:
                    raise ValueError('票種頁已離開或場次變更，請手動確認後續頁面。')
                if time.monotonic() > self.deadline:
                    if self.page and not self.page.is_closed() and is_selection_page(self.page.url):
                        self.retry_selection(self.page, '場次導頁未完成')
                        if self.retry_at is not None:
                            return
                    raise ValueError('場次已點擊，但尚未確認進入票種頁；請手動確認，不會重複點擊。')
                return
            available = [p for p in pages if not p.is_closed() and is_selection_page(p.url)]
            if self.page and not self.page.is_closed():
                page = self.page
                if not is_selection_page(page.url):
                    self.status('等待手動登入與驗證完成，請點網站「訂票」回到選片頁')
                    return
            elif len(available) == 1:
                page = available[0]
            elif len(available) > 1:
                raise ValueError('有多個誠品選片分頁，請只保留要操作的分頁後重新套用。')
            else:
                self.status('請先手動登入與完成驗證，再點網站「訂票」；進入選片頁後依設定選擇影城')
                return
            try:
                snapshot = page.evaluate(READ_SELECTION)
            except Error as exc:
                if page.is_closed() or any(message in str(exc) for message in (
                    'Execution context was destroyed', 'Cannot find context', 'Target page, context or browser has been closed'
                )):
                    return
                raise
            if snapshot['challenge']:
                self.status('偵測到網站驗證，請手動完成；不自動重新整理')
                return
            cinema = cinema_link(snapshot, self.settings.eslite_cinema)
            if cinema is None:
                self.retry_selection(page, '等待左側符合設定的影城')
                return
            self.page = page
            if self.cinema_target and self.cinema_target != cinema['href']:
                raise ValueError('設定影城的連結已變更，請確認後重新套用。')
            selected_cinema = (query_value(snapshot['url'], 'visSearchBy') == 'cin'
                               and query_value(snapshot['url'], 'visCinID') == query_value(cinema['href'], 'visCinID'))
            if not selected_cinema or self.reselect_cinema:
                if self.cinema_target:
                    if self.movie_target:
                        raise ValueError('已選影城被更改，請確認後重新套用。')
                    if time.monotonic() > self.deadline:
                        self.retry_selection(page, '影院導頁未完成')
                        if self.retry_at is not None:
                            return
                        raise ValueError('影城已點擊，但頁面尚未更新；不會重複點擊。')
                    return
                if page.url != snapshot['url']:
                    return
                self.cinema_target = cinema['href']
                self.reselect_cinema = False
                self.deadline = time.monotonic() + 30
                self.click(page, '#box_left', cinema)
                self.emit('log', f"已選擇誠品影城：{cinema['text']}")
                return
            self.cinema_target = cinema['href']
            link = movie_link(snapshot, self.settings.eslite_movie)
            if not link:
                self.retry_selection(page, '找不到符合設定關鍵字的電影' if self.settings.eslite_movie.strip()
                                     else '未設定片名，請在網頁選擇電影；選好後自動選擇場次')
                return
            self.page = page
            target = link['href']
            if self.movie_target and self.movie_target != target:
                raise ValueError('影城或電影清單已變更，請確認後重新套用。')
            selected = query_value(snapshot['url'], 'visMovieName') == query_value(target, 'visMovieName')
            if not selected:
                if self.movie_target:
                    if time.monotonic() > self.deadline:
                        self.retry_selection(page, '電影導頁未完成')
                        if self.retry_at is not None:
                            return
                        raise ValueError('電影已點擊，但頁面尚未更新；請確認後重新套用。')
                    return
                if page.url != snapshot['url']:
                    return
                self.movie_target = target
                self.deadline = time.monotonic() + 30
                self.click(page, '#box_center', link)
                self.emit('log', f"已選擇誠品電影：{link['text']}")
                return
            self.movie_target = target
            session = session_link(snapshot, self.settings.showtime, self.settings.eslite_time,
                                   self.today, self.settings.session_position)
            if not session:
                self.retry_selection(page, '等待指定日期與時間的可選場次' if self.settings.eslite_time
                                     else '等待指定日期的首場或末場可選場次')
                return
            if page.url != snapshot['url']:
                return
            self.session_target = session['href']
            self.deadline = time.monotonic() + 30
            self.click(page, '#box_right', session)
            self.emit('log', f'已點選場次：{self.settings.showtime} {session["text"]}')
        except Exception as exc:
            if not self.stop.is_set():
                if (isinstance(exc, Error) and self.settings.eslite_movie.strip()
                        and self.ticket_flow is None and self.confirmation_flow is None
                        and self.page and not self.page.is_closed() and is_selection_page(self.page.url)):
                    self.retry_at = self.retry_url = None
                    self.retry_selection(self.page, '選片頁載入或點擊未完成')
                    return
                self.active = False
                self.emit('log', f'誠品自動購票已暫停：{exc}')
                self.status('請手動確認頁面，或按「重新套用」')
