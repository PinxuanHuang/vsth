"""Apply the consent preference without submitting the Eslite transaction."""
import re
import time
from urllib.parse import parse_qs, urlsplit

from eslite_tickets import ticket_session


class EsliteConfirmationFlow:
    def __init__(self, settings, emit, stop, session_url):
        self.settings, self.emit, self.stop = settings, emit, stop
        self.session = ticket_session(session_url)
        if self.session is None:
            raise ValueError('無法確認誠品場次。')
        self.deadline = time.monotonic() + 30
        self.done = False

    def wait(self):
        if time.monotonic() > self.deadline:
            raise ValueError('尚未確認誠品條款勾選區塊，請手動確認；不會重送系統選位或交易。')
        return False

    def tick(self, page):
        if self.stop.is_set():
            return False
        if self.done:
            return True
        if page is None or page.is_closed():
            raise ValueError('誠品購票分頁已關閉。')
        parts = urlsplit(page.url)
        current_url = page.url
        if parts.scheme != 'https' or parts.netloc.lower() != 'arthouse.eslite.com':
            raise ValueError('已離開誠品購票網站，請手動確認。')
        query = parse_qs(parts.query, keep_blank_values=True)
        for key, expected in zip(('cinemacode', 'txtSessionId'), self.session):
            if key in query and query[key] != [expected]:
                raise ValueError('確認頁的影城或場次已變更，請手動確認。')
        # Scope by the confirmation layout and terms row, never by a generated HTML ID.
        roots = page.locator('table.confirmationcheck').filter(visible=True)
        if roots.count() == 0:
            return self.wait()
        if roots.count() != 1:
            raise ValueError('有多個訂票確認區塊，請手動確認。')
        terms = roots.locator('.TermsAndConditions').filter(
            has_text=re.compile(r'我.*閱讀.*同意')).filter(visible=True)
        if terms.count() == 0:
            return self.wait()
        if terms.count() != 1:
            raise ValueError('有多個同意條款，請手動確認。')
        row = terms.locator('xpath=ancestor::tr[1]')
        checkbox = row.locator('input[type="checkbox"]').filter(visible=True)
        if checkbox.count() == 0:
            return self.wait()
        if checkbox.count() != 1:
            raise ValueError('同意條款無法對應唯一 checkbox，請手動確認。')
        if self.settings.agree:
            if not checkbox.is_enabled():
                return self.wait()
            if self.stop.is_set():
                return False
            if page.url != current_url:
                return self.wait()
            if not checkbox.is_checked():
                checkbox.check(timeout=5000)
            if not checkbox.is_checked():
                raise ValueError('同意勾選未生效，請手動確認。')
            self.emit('log', '已依設定勾選誠品訂票條款；未點擊「確定」。')
        else:
            self.emit('log', '未啟用自動同意，保留條款勾選原狀；請自行確認，程式未送出交易。')
        self.done = True
        return True
