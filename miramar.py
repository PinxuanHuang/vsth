"""Miramar booking through seat selection; leave subsequent checkout to the user."""
import re
import time
import unicodedata
from datetime import datetime
from urllib.parse import urlsplit, parse_qs

from config import parse_eslite_movie_keywords
from miramar_tickets import prepare_tickets
from miramar_seats import prepare_seats


SELECTS = ('sel_cinema', 'sel_movie', 'sel_show_time', 'sel_show_session')
LABELS = ('影城', '電影', '日期', '場次')
READ_OPTIONS = """el => Array.from(el.options).filter(o =>
    o.value && !o.disabled && !o.closest('optgroup[disabled]') &&
    !o.hidden && getComputedStyle(o).display !== 'none' &&
    getComputedStyle(o).visibility !== 'hidden').map(o =>
    ({value: o.value, text: o.textContent.trim()}))"""


def normalized(value):
    return ' '.join(unicodedata.normalize('NFKC', value).split()).casefold()


def choose_option(stage, options, settings):
    if stage == 0:
        matches = [o for o in options if normalized(o['text']) == normalized(settings.miramar_cinema)]
    elif stage == 1:
        words = parse_eslite_movie_keywords(settings.eslite_movie)
        matches = [o for o in options if any(w in normalized(o['text']) for w in words)]
        exact = [o for o in matches if len(words) == 1 and normalized(o['text']) == words[0]]
        matches = exact or matches
    elif stage == 2:
        matches = []
        for option in options:
            try:
                date = datetime.fromisoformat(option['value']).date().isoformat()
            except ValueError:
                continue
            if date == settings.showtime:
                matches.append(option)
    else:
        sessions = [o for o in options if re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', o['text'])]
        matches = [o for o in sessions if o['text'] == settings.eslite_time] or sessions
        return matches[0 if settings.session_position == 'first' else -1] if matches else None
    if len(matches) > 1:
        raise ValueError(f'{LABELS[stage]}有多筆符合：' + '、'.join(o['text'] for o in matches) + '；請手動確認或縮小關鍵字。')
    return matches[0] if matches else None


def is_home(url):
    parts = urlsplit(url)
    return parts.scheme == 'https' and parts.netloc.lower() == 'www.miramarcinemas.tw' and parts.path in ('', '/')


class MiramarFlow:
    def __init__(self, settings, emit, stop, timeout=30):
        self.settings, self.emit, self.stop, self.timeout = settings, emit, stop, timeout
        self.arm()

    def arm(self):
        self.page = None
        self.stage = 0
        self.selected = []
        self.paused = False
        self.snapshot = None
        self.deadline = None

    def tick(self, pages):
        if self.paused or self.stop.is_set():
            return
        try:
            self.advance(pages)
        except Exception as exc:
            self.paused = True
            self.emit('log', f'美麗華流程暫停：{exc}')
            self.emit('status', '請確認頁面後重新套用；不會重複搜尋')

    def advance(self, pages):
        if self.page is None:
            homes = [p for p in pages if not p.is_closed() and is_home(p.url)]
            if len(homes) != 1:
                return
            page = homes[0]
            if page.locator('#ibooking').count() != 1:
                return
            self.page = page
            self.deadline = time.monotonic() + self.timeout
        page = self.page
        if page.is_closed():
            raise ValueError('購票分頁已關閉。')
        if self.stage == 5:
            parts = urlsplit(page.url)
            query = parse_qs(parts.query)
            if (parts.scheme == 'https' and parts.netloc.lower() == 'www.miramarcinemas.tw'
                    and parts.path.lower() == '/booking/tickettype'
                    and query.get('id') == [self.selected[1]] and query.get('session') == [self.selected[3]]):
                button = prepare_tickets(page, self.settings, self.selected[1], self.selected[3],
                                         self.stop, lambda text: self.emit('log', text))
                if button is not None and not self.stop.is_set():
                    self.stage = 6
                    self.deadline = time.monotonic() + self.timeout
                    button.click(timeout=3000, no_wait_after=True)
                    self.emit('log', '美麗華已選擇票種與張數，等待座位頁。')
                    return
        elif self.stage == 6:
            parts = urlsplit(page.url)
            if (parts.scheme == 'https' and parts.netloc.lower() == 'www.miramarcinemas.tw'
                    and parts.path.lower() == '/booking/seatplan'):
                if page.locator('#booking_data #seatTable').count() == 0:
                    if time.monotonic() < self.deadline:
                        return
                self.paused = True
                try:
                    button = prepare_seats(page, self.settings, self.selected[1], self.selected[3],
                                           self.stop, lambda text: self.emit('log', text))
                    if button is not None and not self.stop.is_set():
                        self.stage = 7
                        button.click(timeout=3000, no_wait_after=True)
                        self.emit('handoff', '美麗華已選足座位並點擊下一步，後續請手動確認')
                except Exception as exc:
                    self.emit('log', f'美麗華選位交由手動操作：{exc}')
                    self.emit('handoff', '請在瀏覽器完成選位；程式不會送出不足的座位')
                return
        else:
            if not is_home(page.url):
                raise ValueError('已離開首頁，未繼續操作。')
            for index, value in enumerate(self.selected):
                if page.locator(f'#ibooking #{SELECTS[index]}').input_value(timeout=1000) != value:
                    raise ValueError('先前選項已變更，請重新套用。')
            if self.stage < 4:
                control = page.locator(f'#ibooking #{SELECTS[self.stage]}')
                if control.count() == 1 and control.is_visible() and control.is_enabled():
                    options = control.evaluate(READ_OPTIONS)
                    # Require two consecutive snapshots before choosing a fallback session.
                    if options and options == self.snapshot:
                        choice = choose_option(self.stage, options, self.settings)
                        if choice:
                            if self.stop.is_set():
                                return
                            control.select_option(value=choice['value'], timeout=3000)
                            self.emit('log', f'美麗華已選擇{LABELS[self.stage]}：{choice["text"]}')
                            self.selected.append(choice['value'])
                            self.stage += 1
                            self.snapshot = None
                            self.deadline = time.monotonic() + self.timeout
                            return
                    self.snapshot = options
            else:
                button = page.locator('#ibooking').get_by_role('button', name='搜尋')
                if button.count() == 1 and button.is_visible() and button.is_enabled():
                    if self.stop.is_set():
                        return
                    # Commit before clicking: a navigation timeout must not resubmit.
                    self.stage = 5
                    self.deadline = time.monotonic() + self.timeout
                    button.click(timeout=3000, no_wait_after=True)
                    self.emit('log', '美麗華已點擊搜尋。')
                    return
        if time.monotonic() >= self.deadline:
            label = LABELS[self.stage] if self.stage < 4 else '搜尋結果'
            raise ValueError(f'等待{label}逾時或找不到符合設定的選項。')
