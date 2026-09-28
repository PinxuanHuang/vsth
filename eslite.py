"""Eslite movie/session selection using the live links and adjacent date rows."""
import re
import time
import unicodedata
from datetime import date
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import Error


READ_SELECTION = r"""() => {
    const visible = el => !!el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
    const links = root => Array.from(root?.querySelectorAll('a[href]') || [])
        .filter(visible).map(a => ({text: a.innerText.trim(), title: a.title, href: a.href,
            disabled: a.getAttribute('aria-disabled') === 'true' || a.hasAttribute('disabled')}));
    return {
        url: location.href,
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


def movie_link(snapshot, wanted):
    cinema = query_value(snapshot['url'], 'visCinID')
    if not cinema or query_value(snapshot['url'], 'visSearchBy') != 'cin':
        return None
    if not any(is_selection_page(link['href']) and query_value(link['href'], 'visCinID') == cinema
               for link in snapshot['cinemas']):
        return None
    candidates = [link for link in snapshot['movies']
                  if is_selection_page(link['href']) and not link['disabled']
                  and query_value(link['href'], 'visCinID') == cinema
                  and query_value(link['href'], 'visMovieName')
                  and normalized(wanted) in normalized(link['text'])]
    exact = [link for link in candidates if normalized(link['text']) == normalized(wanted)]
    candidates = exact or candidates
    if len(candidates) > 1:
        raise ValueError('有多部電影符合片名，請輸入更完整的中文片名後重新執行。')
    return candidates[0] if candidates else None


def displayed_date(text, today):
    match = re.match(r'^\s*(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*日',
                     unicodedata.normalize('NFKC', text))
    if not match:
        return None
    year, month, day = match.groups()
    month, day = int(month), int(day)
    # Yearless listings refer to the next occurrence, including December -> January.
    year = int(year) if year else today.year + ((month, day) < (today.month, today.day))
    try:
        return date(year, month, day)
    except ValueError:
        return None


def session_link(snapshot, wanted_date, wanted_time, today=None):
    today = today or date.today()
    target_date = date.fromisoformat(wanted_date)
    cinema = query_value(snapshot['url'], 'visCinID')
    matches = []
    for rows in snapshot['tables']:
        for index, row in enumerate(rows[:-1]):
            if displayed_date(row['text'], today) != target_date:
                continue
            next_row = rows[index + 1]
            if displayed_date(next_row['text'], today) is not None:
                continue
            for link in next_row['links']:
                stamp = re.match(r'^(\d{1,2}):([0-5]\d)(?=\s|$)', normalized(link['title'] or link['text']))
                if not stamp or f'{int(stamp[1]):02d}:{stamp[2]}' != wanted_time or link['disabled']:
                    continue
                parts = urlsplit(link['href'])
                if (parts.scheme == 'https' and parts.netloc.lower() == 'arthouse.eslite.com'
                        and parts.path.lower() == '/visselecttickets.aspx'
                        and query_value(link['href'], 'cinemacode') == cinema
                        and query_value(link['href'], 'txtSessionId')):
                    matches.append(link)
    if len(matches) > 1:
        raise ValueError('同日期同時間有多個場次，請手動確認影廳；程式不會任選一場。')
    return matches[0] if matches else None


class EsliteFlow:
    def __init__(self, settings, emit, stop, today=None):
        self.settings, self.emit, self.stop = settings, emit, stop
        self.today = today
        self.arm()

    def arm(self):
        self.active = True
        self.page = None
        self.movie_target = None
        self.session_target = None
        self.deadline = None
        self.last_status = None

    def status(self, message):
        if message != self.last_status:
            self.emit('status', message)
            self.last_status = message

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
            if self.session_target:
                if self.page and not self.page.is_closed() and self.page.url == self.session_target:
                    self.active = False
                    self.emit('handoff', '已選擇誠品電影與指定場次，後續購票請手動操作')
                    return
                if time.monotonic() > self.deadline:
                    raise ValueError('場次已點擊，但尚未確認進入票種頁；請手動確認，不會重複點擊。')
                return
            available = [p for p in pages if not p.is_closed() and is_selection_page(p.url)]
            if self.page and not self.page.is_closed():
                page = self.page
                if not is_selection_page(page.url):
                    self.status('等待手動驗證完成並返回誠品選片頁')
                    return
            elif len(available) == 1:
                page = available[0]
            elif len(available) > 1:
                raise ValueError('有多個誠品選片分頁，請只保留要操作的分頁後重新套用。')
            else:
                self.status('等待手動完成驗證，並在誠品頁面選擇影城')
                return
            try:
                snapshot = page.evaluate(READ_SELECTION)
            except Error as exc:
                if page.is_closed() or any(message in str(exc) for message in (
                    'Execution context was destroyed', 'Cannot find context', 'Target page, context or browser has been closed'
                )):
                    return
                raise
            link = movie_link(snapshot, self.settings.eslite_movie)
            if not link:
                self.status('等待選擇影城或符合設定片名的電影清單')
                return
            self.page = page
            target = link['href']
            if self.movie_target and self.movie_target != target:
                raise ValueError('影城或電影清單已變更，請確認後重新套用。')
            selected = query_value(snapshot['url'], 'visMovieName') == query_value(target, 'visMovieName')
            if not selected:
                if self.movie_target:
                    if time.monotonic() > self.deadline:
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
            session = session_link(snapshot, self.settings.showtime, self.settings.eslite_time, self.today)
            if not session:
                self.status('等待指定日期與時間的可選場次')
                return
            if page.url != snapshot['url']:
                return
            self.session_target = session['href']
            self.deadline = time.monotonic() + 30
            self.click(page, '#box_right', session)
            self.emit('log', f'已點選場次：{self.settings.showtime} {self.settings.eslite_time}')
        except Exception as exc:
            if not self.stop.is_set():
                self.active = False
                self.emit('log', f'誠品自動選片已暫停：{exc}')
                self.status('請手動確認頁面，或按「重新套用」')
