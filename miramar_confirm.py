"""Fill the site's confirmation form; preserve its transaction data and handlers."""
import json
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin, urlsplit

from ticket_types import get_miramar_ticket_type


def confirm_seat_key(seat):
    return tuple(str(seat[key]) for key in ('AreaNumber', 'AreaCategoryCode', 'RowIndex', 'ColumnIndex', 'PhysicalName', 'SeatId'))


def hidden_fields(form):
    values = form.locator('input[type="hidden"][name]').evaluate_all(
        'els => els.map(el => [el.name, el.value])')
    if len({key for key, value in values}) != len(values):
        raise ValueError('確認表單含重複欄位，未送出。')
    return dict(values)


def prepare_confirmation(page, settings, movie, session, expected_seats, stop):
    parts = urlsplit(page.url)
    if parts.scheme != 'https' or parts.netloc.lower() != 'www.miramarcinemas.tw' or parts.path.lower() != '/booking/confirm':
        raise ValueError('目前不是美麗華購票確認頁。')
    form = page.locator('form').filter(has=page.locator('input[name="Name"]')).filter(has=page.locator('input[name="Phone"]'))
    if form.count() != 1:
        raise ValueError('找不到唯一的購票確認表單。')
    destination = urlsplit(urljoin(page.url, form.get_attribute('action') or ''))
    if (destination.scheme != parts.scheme or destination.netloc != parts.netloc
            or destination.path.lower() != parts.path.lower() or (form.get_attribute('method') or '').lower() != 'post'):
        raise ValueError('確認表單提交目的地或方式異常。')
    before = hidden_fields(form)
    if before.get('MovieId') != movie or before.get('Session') != session:
        raise ValueError('確認頁的電影或場次不符，未送出。')
    try:
        tickets = json.loads(before['TicketType'])
        seats = json.loads(before['Seat'])
        wanted = get_miramar_ticket_type(settings.miramar_ticket_type).name
        if not isinstance(tickets, list) or not tickets or not isinstance(seats, list):
            raise ValueError()
        total = 0
        for ticket in tickets:
            qty, units = str(ticket['Qty']), str(ticket['TicketTypeSeats'])
            if not re.fullmatch(r'[0-9]+', qty) or not re.fullmatch(r'[1-9][0-9]*', units):
                raise ValueError()
            if int(qty) and (ticket['TicketTypeTitleAlt'] != wanted or str(ticket['OnlyCashPay']) != '0'):
                raise ValueError()
            total += int(qty) * int(units)
        actual_seats = {confirm_seat_key(seat) for seat in seats}
        if total != settings.tickets or len(seats) != total or len(actual_seats) != total or actual_seats != set(expected_seats):
            raise ValueError()
        if json.loads(before['Concession']) != []:
            raise ValueError()
        for key in ('TotalPrice', 'BookingFee'):
            value = Decimal(before[key])
            if not value.is_finite() or value < 0:
                raise ValueError()
    except (KeyError, TypeError, ValueError, InvalidOperation):
        raise ValueError('票種、張數、座位或付款資料不符，或此票種僅限現場付款；未送出。') from None
    if not settings.miramar_name.strip() or not settings.miramar_phone.strip():
        raise ValueError('請填寫美麗華專用會員姓名與手機號碼，或在網頁手動完成。')
    if stop.is_set():
        return None
    invoice = form.locator('#invoice_select')
    choices = invoice.locator('option').evaluate_all(
        "els => els.filter(el => !el.disabled && el.textContent.trim() === '個人').map(el => el.value)")
    if len(choices) != 1:
        raise ValueError('找不到唯一的個人發票選項。')
    invoice.select_option(value=choices[0], timeout=3000)
    for key, value in (('Name', settings.miramar_name.strip()), ('Phone', settings.miramar_phone.strip()),
                       ('InvoiceVehicle', settings.miramar_carrier.strip())):
        field = form.locator(f'input[name="{key}"]')
        field.fill(value, timeout=3000)
        if field.input_value() != value or not field.evaluate('el => el.checkValidity()'):
            raise ValueError('確認資料未正確填入或未通過欄位驗證。')
    after = hidden_fields(form)
    expected = dict(before, InvoiceType=choices[0])
    if after != expected or invoice.input_value() != choices[0]:
        raise ValueError('發票類型未更新，或訂單資料已變更，未送出。')
    if not settings.agree or stop.is_set():
        return None
    agree = form.locator('input[type="checkbox"]#AgreeRule')
    if agree.count() != 1 or not agree.is_enabled():
        raise ValueError('找不到可操作的同意條款勾選框。')
    if not agree.is_checked():
        # Materialize hides the checkbox itself; click its real wrapping label.
        label = agree.locator('xpath=ancestor::label')
        if label.count() == 1:
            label.click(position={'x': 5, 'y': 5}, timeout=3000)
        else:
            agree.check(timeout=3000)
    if not agree.is_checked() or hidden_fields(form) != expected:
        raise ValueError('同意條款未確認或訂單資料已變更。')
    for key, value in (('Name', settings.miramar_name.strip()), ('Phone', settings.miramar_phone.strip()),
                       ('InvoiceVehicle', settings.miramar_carrier.strip())):
        if form.locator(f'input[name="{key}"]').input_value() != value:
            raise ValueError('會員或發票資料已變更，未送出。')
    button = form.locator('label[onclick], button, a[onclick]').filter(has_text=re.compile(r'^\s*線上付款(?:\s|$)'))
    if button.count() != 1 or not button.is_visible() or not button.is_enabled() or button.get_attribute('aria-disabled') == 'true':
        raise ValueError('找不到唯一可操作的線上付款按鈕。')
    return button
