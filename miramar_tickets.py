"""Read Miramar ticket rows without relying on product codes or prices."""
import re
import unicodedata
from urllib.parse import urljoin, urlsplit, parse_qs

from ticket_types import get_miramar_ticket_type


READ_ROWS = """rows => rows.map(row => {
    const selects = row.querySelectorAll('select');
    const select = selects[0];
    const visible = el => !!el && !el.hidden && el.getClientRects().length > 0 &&
        getComputedStyle(el).visibility !== 'hidden';
    return {name: row.getAttribute('tickettypetitlealt'),
        seats: row.getAttribute('tickettypeseats'), count: selects.length,
        active: visible(row) && visible(select) && !select.disabled,
        value: select?.value,
        options: select ? Array.from(select.options).filter(o => !o.disabled &&
            !o.hidden && !o.closest('optgroup[disabled]') && getComputedStyle(o).display !== 'none')
            .map(o => o.value) : []};
})"""


def normalize(text):
    return ' '.join(unicodedata.normalize('NFKC', text or '').split()).casefold()


def ticket_choice(rows, settings):
    name = get_miramar_ticket_type(settings.miramar_ticket_type).name
    matches = [(i, row) for i, row in enumerate(rows) if normalize(row['name']) == normalize(name)]
    if len(matches) != 1:
        raise ValueError(f'美麗華票種「{name}」不存在或不唯一，未改選其他票種。')
    index, row = matches[0]
    if not row['active'] or row['count'] != 1:
        raise ValueError('票種數量欄位不可操作或結構不唯一。')
    seats = row['seats'] or ''
    if not re.fullmatch(r'[1-9][0-9]*', seats):
        raise ValueError('無法辨識票種每份座位數。')
    units, remainder = divmod(settings.tickets, int(seats))
    if units < 1 or remainder:
        raise ValueError(f'此票種每份 {seats} 張，設定 {settings.tickets} 張無法整除，未自動增減張數。')
    if str(units) not in row['options']:
        raise ValueError(f'此票種目前不能選擇 {units} 份（{settings.tickets} 張）。')
    for i, other in enumerate(rows):
        if other['count'] != 1 or (i != index and other['value'] != '0'):
            raise ValueError('其他票種已有數量或欄位結構異常，請手動確認。')
    return index, str(units)


def prepare_tickets(page, settings, movie, session, stop, log):
    """Return the site's original next control after validating the selected count."""
    parts = urlsplit(page.url)
    query = parse_qs(parts.query)
    if (parts.scheme != 'https' or parts.netloc.lower() != 'www.miramarcinemas.tw'
            or parts.path.lower() != '/booking/tickettype'
            or query.get('id') != [movie] or query.get('session') != [session]):
        raise ValueError('票種頁電影或場次與剛才搜尋不符。')
    # The site reuses ticketTypeTable for concessions; identify the row schema.
    table = page.locator('#booking_data table').filter(has=page.locator('tr.TicketTypeData'))
    if table.count() == 0:
        return None
    if table.count() != 1:
        raise ValueError('票種表格不唯一。')
    form = table.locator('xpath=ancestor::form')
    if form.count() != 1:
        raise ValueError('找不到唯一購票表單。')
    action = urlsplit(urljoin(page.url, form.get_attribute('action') or ''))
    if (action.scheme != parts.scheme or action.netloc != parts.netloc
            or action.path.lower() != '/booking/tickettype'):
        raise ValueError('購票表單目的地異常。')
    for key, expected in (('MovieId', movie), ('Session', session)):
        field = form.locator(f'input[name="{key}"]')
        if field.count() != 1 or field.input_value() != expected:
            raise ValueError('購票表單的電影或場次不符。')
    rows = table.locator('tr.TicketTypeData')
    snapshot = rows.evaluate_all(READ_ROWS)
    if not snapshot:
        return None
    index, quantity = ticket_choice(snapshot, settings)
    meals = form.locator('tr.ConcessionData select')
    if any(value != '0' for value in meals.evaluate_all('els => els.map(el => el.value)')):
        raise ValueError('餐點已有數量，請手動確認；程式不會額外加購餐點。')
    button = form.locator('label[onclick], button, a[onclick]').filter(has_text=re.compile(r'^\s*下一步(?:\s|$)'))
    if button.count() != 1 or not button.is_visible() or not button.is_enabled() or button.get_attribute('aria-disabled') == 'true':
        raise ValueError('找不到唯一可操作的下一步。')
    if stop.is_set():
        return None
    select = rows.nth(index).locator('select')
    if select.input_value() != quantity:
        select.select_option(value=quantity, timeout=3000)
    expected = [dict(row) for row in snapshot]
    expected[index]['value'] = quantity
    if rows.evaluate_all(READ_ROWS) != expected:
        raise ValueError('選擇數量後票種清單或張數改變，未點擊下一步。')
    if any(value != '0' for value in meals.evaluate_all('els => els.map(el => el.value)')):
        raise ValueError('選票後出現額外餐點，請手動確認。')
    log(f'美麗華已選擇 {snapshot[index]["name"]}：{quantity} 份，共 {settings.tickets} 張。')
    return button
