"""Select Eslite ticket quantities through the site's own form controls."""
import re
import time
import unicodedata
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urlsplit

from ticket_types import get_eslite_ticket_type


READ_TICKETS = r"""() => {
    const visible = el => !!el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
    return Array.from(document.querySelectorAll('.TicketType')).flatMap((label, index) => {
        const row = label.closest('tr');
        if (!row || !visible(row)) return [];
        const selects = row.querySelectorAll('select');
        const select = selects[0];
        const field = selector => {
            const el = row.querySelector(selector);
            return el ? (el.value ?? el.textContent).trim() : '';
        };
        return [{index, label: label.textContent.trim(), count: selects.length,
            identity: select?.getAttribute('identity'), cents: select?.getAttribute('price'),
            price: field('.TicketTypePrice'), subtotal: field('.TicketTypeSubTotal'),
            value: select?.value, quantity: select?.selectedOptions[0]?.textContent.trim(),
            enabled: !!select && visible(select) && !select.matches(':disabled')
                && select.getAttribute('aria-disabled') !== 'true',
            options: Array.from(select?.options || []).map(o => ({value: o.value,
                text: o.textContent.trim(), disabled: o.disabled || !!o.closest('optgroup[disabled]')}))}];
    });
}"""


def ticket_session(url):
    parts = urlsplit(url)
    if (parts.scheme != 'https' or parts.netloc.lower() != 'arthouse.eslite.com'
            or parts.path.lower() != '/visselecttickets.aspx'):
        return None
    query = parse_qs(parts.query)
    values = [query.get(key, []) for key in ('cinemacode', 'txtSessionId')]
    return tuple(value[0] for value in values) if all(len(value) == 1 for value in values) else None


def normalized(value):
    return ' '.join(unicodedata.normalize('NFKC', value).split()).casefold()


def money(value):
    value = normalized(value).replace(',', '')
    value = re.sub(r'^(?:nt\$|\$)\s*', '', value)
    if not re.fullmatch(r'\d+(?:\.\d+)?', value):
        raise ValueError('無法確認票價或小計，請手動確認票種頁。')
    try:
        return Decimal(value)
    except InvalidOperation:
        raise ValueError('無法確認票價或小計。') from None


def ticket_name(row):
    text = normalized(row['label']).rstrip(':').strip()
    price = money(row['price'])
    suffix = re.search(r'\s*(\d+(?:\.\d+)?)$', text)
    if suffix and money(suffix[1]) == price:
        text = text[:suffix.start()].strip()
    # The site appends a member-channel label, e.g. "-- W", not part of the ticket name.
    return text.split('--', 1)[0].strip()


def quantity(row):
    text = normalized(row['quantity'] or '')
    if not re.fullmatch(r'\d+', text):
        raise ValueError('票數選單格式已變更，請手動確認。')
    return int(text)


def quantity_option(row, amount):
    options = [o for o in row['options'] if not o['disabled']
               and normalized(o['text']) == str(amount)]
    if len(options) != 1:
        raise ValueError(f'「{row["label"]}」沒有唯一且可用的 {amount} 張選項；不會改選其他張數。')
    return options[0]['value']


class EsliteTicketFlow:
    def __init__(self, settings, emit, stop, expected_url, attempted):
        self.settings, self.emit, self.stop = settings, emit, stop
        self.session = ticket_session(expected_url)
        if not self.session:
            raise ValueError('無法確認誠品場次。')
        self.attempted = attempted
        self.deadline = time.monotonic() + 30
        self.pending = None
        self.last_status = None

    def wait(self, message):
        if time.monotonic() > self.deadline:
            raise ValueError(f'{message}逾時；請確認頁面，不會自動重送。')
        if self.last_status != message:
            self.emit('status', message)
            self.last_status = message

    def row_locator(self, page, row):
        return page.locator('.TicketType').nth(row['index']).locator('xpath=ancestor::tr[1]')

    def change(self, page, row, amount):
        value = quantity_option(row, amount)
        if not row['enabled']:
            self.wait('等待票數選單可用')
            return
        locator = self.row_locator(page, row)
        if (locator.locator('.TicketType').inner_text().strip() != row['label']
                or locator.locator('select').count() != 1
                or locator.locator('select').get_attribute('identity') != row['identity']):
            raise ValueError('票種列已變更，請確認後重新套用。')
        if self.stop.is_set():
            return
        if ticket_session(page.url) != self.session:
            raise ValueError('票種頁的場次已變更，請手動確認。')
        self.pending = (row['label'], row['identity'], amount)
        self.deadline = time.monotonic() + 30
        locator.locator('select').select_option(value=value, timeout=5000)
        self.emit('log', f'誠品票數：{row["label"]} {amount} 張')

    def tick(self, page):
        if self.stop.is_set():
            return False
        if ticket_session(page.url) != self.session:
            raise ValueError('票種頁的影城或場次已變更，請手動確認。')
        if self.session in self.attempted:
            raise ValueError('此場次已點擊過系統選位；為避免重複送出，請手動確認後續頁面。')
        rows = page.evaluate(READ_TICKETS)
        if not rows:
            self.wait('等待誠品票種表格')
            return False
        if any(row['count'] != 1 for row in rows):
            raise ValueError('票種與數量欄位無法唯一對應，請手動確認。')
        wanted = get_eslite_ticket_type(self.settings.eslite_ticket_type).name
        matches = [row for row in rows if ticket_name(row) == normalized(wanted)]
        if len(matches) != 1:
            raise ValueError(f'找不到唯一的「{wanted}」票種；不會改選其他票種。')
        target = matches[0]
        quantity_option(target, self.settings.tickets)
        for row in rows:
            quantity(row)
        if self.pending:
            label, identity, amount = self.pending
            pending_rows = [row for row in rows if (row['label'], row['identity']) == (label, identity)]
            if len(pending_rows) != 1:
                raise ValueError('票數變更後票種列已改變，請手動確認。')
            row = pending_rows[0]
            if quantity(row) != amount or money(row['subtotal']) != money(row['price']) * amount:
                self.wait('等待網站確認票數與小計')
                return False
            self.pending = None
            self.deadline = time.monotonic() + 30
        # A single ticket type is configured; clear other quantities before adding it.
        for row in rows:
            if row is not target and quantity(row) != 0:
                self.change(page, row, 0)
                return False
        if quantity(target) != self.settings.tickets:
            self.change(page, target, self.settings.tickets)
            return False
        if not target['enabled']:
            self.wait('等待票數選單可用')
            return False
        if any(money(row['subtotal']) != money(row['price']) * quantity(row) for row in rows):
            self.wait('等待網站確認票數與小計')
            return False
        if page.locator('.ImageProcessing').filter(visible=True).count():
            self.wait('等待網站完成票數檢查')
            return False
        form = self.row_locator(page, target).locator('xpath=ancestor::form[1]')
        scope = form if form.count() == 1 else page
        action = scope.get_by_role('link', name='系統選位', exact=True).or_(
            scope.get_by_role('button', name='系統選位', exact=True)).filter(visible=True)
        if action.count() > 1:
            raise ValueError('有多個系統選位按鈕，請手動確認。')
        if (action.count() == 0 or not action.is_enabled()
                or action.get_attribute('disabled') is not None):
            self.wait('等待系統選位按鈕可用')
            return False
        if self.stop.is_set():
            return False
        if ticket_session(page.url) != self.session:
            raise ValueError('票種頁的場次已變更，請手動確認。')
        # Mark before clicking: even a navigation timeout must never cause a second postback.
        self.attempted.add(self.session)
        action.click(timeout=5000)
        self.emit('log', f'已選擇誠品「{wanted}」{self.settings.tickets} 張並點擊系統選位')
        return True
