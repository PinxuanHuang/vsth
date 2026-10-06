"""Adapt Miramar's seat grid to the existing preference planner."""
import json
import re
import time
from urllib.parse import urlsplit, urljoin

from seating import plan_seats, seat_label


READ_MIRAMAR_SEATS = r"""table => {
    const root = table.closest('#booking_data');
    const color = el => el ? getComputedStyle(el).backgroundColor : '';
    const selectedColor = color(root.querySelector('.seat_type .seat_mark_select'));
    const freeColor = color(root.querySelector('.seat_type .seat_mark_free'));
    const occupiedColor = color(root.querySelector('.seat_type .seat_mark:not(.seat_mark_select):not(.seat_mark_free)'));
    const verified = selectedColor && !['transparent', 'rgba(0, 0, 0, 0)'].includes(selectedColor)
        && selectedColor !== freeColor && selectedColor !== occupiedColor;
    const rows = Array.from(table.rows).filter(r => r.querySelector('td[isoccupy]'));
    const width = Math.max(0, ...rows.map(r => Array.from(r.cells).reduce((n,c) => n+c.colSpan,0)));
    const areas = [...new Set(Array.from(table.querySelectorAll('td[areanumber][areacategorycode]'))
        .map(c => c.getAttribute('areanumber') + ':' + c.getAttribute('areacategorycode')))];
    return {verified: !!verified, seats: rows.flatMap((row, gridRow) => {
        let col = 0;
        return Array.from(row.cells).flatMap((cell, cellIndex) => {
            const gridCol = col; col += cell.colSpan;
            if (!cell.hasAttribute('isoccupy')) return [];
            const text = cell.textContent.trim().toUpperCase().match(/^([A-Z]+)\s*([0-9]+)$/);
            const physical = cell.getAttribute('physicalname');
            const number = cell.getAttribute('seatid');
            const complete = ['physicalname','seatid','areanumber','areacategorycode','rowindex','columnindex']
                .every(a => cell.hasAttribute(a));
            const selected = !!verified && color(cell) === selectedColor;
            const visible = !!cell.getClientRects().length && getComputedStyle(cell).visibility !== 'hidden';
            const selectable = complete && cell.getAttribute('isoccupy') === '0' && visible &&
                cell.hasAttribute('onclick') && cell.getAttribute('aria-disabled') !== 'true';
            return [{row: physical || text?.[1] || '', label: number || text?.[2] || '',
                area: complete ? cell.getAttribute('areanumber') + ':' + cell.getAttribute('areacategorycode')
                    : areas.length === 1 ? areas[0] : 'unavailable',
                backendRow: cell.getAttribute('rowindex'), backendCol: cell.getAttribute('columnindex'),
                gridRow, gridCol, domRow: row.rowIndex, cellIndex,
                merged: Array.from(row.cells).some(c => c.colSpan !== 1 || c.rowSpan !== 1),
                x: 100*(gridCol+cell.colSpan/2)/width, y: 100*(gridRow+0.5)/rows.length,
                selected, selectable, available: selectable && !selected && color(cell) === freeColor}];
        });
    })};
}"""


def identity(seat):
    return seat['area'], seat['backendRow'], seat['backendCol'], seat['row'], seat['label']


def seat_form(page, movie, session, count):
    parts = urlsplit(page.url)
    if parts.scheme != 'https' or parts.netloc.lower() != 'www.miramarcinemas.tw' or parts.path.lower() != '/booking/seatplan':
        raise ValueError('已離開美麗華座位頁。')
    form = page.locator('#booking_data form').filter(has=page.locator('input[name="Seat"]'))
    if form.count() != 1:
        raise ValueError('找不到唯一座位表單。')
    action = urlsplit(urljoin(page.url, form.get_attribute('action') or ''))
    if action.scheme != parts.scheme or action.netloc != parts.netloc or action.path.lower() != parts.path.lower():
        raise ValueError('座位表單目的地異常。')
    for key, expected in (('MovieId', movie), ('Session', session)):
        field = form.locator(f'input[name="{key}"]')
        if field.count() != 1 or field.input_value() != expected:
            raise ValueError('座位頁電影或場次與設定不符。')
    try:
        tickets = json.loads(form.locator('input[name="TicketType"]').input_value())
        if not isinstance(tickets, list) or not tickets:
            raise ValueError()
        total = 0
        for ticket in tickets:
            qty, seats = str(ticket['Qty']), str(ticket['TicketTypeSeats'])
            if not re.fullmatch(r'[0-9]+', qty) or not re.fullmatch(r'[1-9][0-9]*', seats):
                raise ValueError()
            total += int(qty) * int(seats)
        if total != count:
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise ValueError('座位頁票數與設定不符或無法辨識。') from None
    return form


def prepare_seats(page, settings, movie, session, stop, log):
    """Return next only when every planned seat is visibly selected; never retry a click."""
    if settings.seat_mode == 'manual':
        raise ValueError('目前設定為手動選位，請自行操作。')
    form = seat_form(page, movie, session, settings.tickets)
    table = page.locator('#booking_data #seatTable')
    if table.count() != 1 or not table.is_visible():
        raise ValueError('座位圖不存在或不唯一。')
    data = table.evaluate(READ_MIRAMAR_SEATS)
    if not data['verified']:
        raise ValueError('無法由座位圖例確認已選狀態，請手動操作。')
    seats = data['seats']
    if any(s['selected'] for s in seats):
        raise ValueError('頁面已有已選座位，保留原選擇供手動操作。')
    available = [s for s in seats if s['selectable']]
    if any(not re.fullmatch(r'[A-Za-z]+', s['row']) or not s['label'].isdigit() for s in available):
        raise ValueError('無法辨識座位排號。')
    if len({identity(s) for s in available}) != len(available):
        raise ValueError('座位識別資料重複。')
    plan = plan_seats(seats, settings)
    if len(plan) != settings.tickets:
        raise ValueError('可選座位不足，請手動操作。')
    chosen = set()
    for target in plan:
        if stop.is_set():
            return None
        seat_form(page, movie, session, settings.tickets)
        current = table.evaluate(READ_MIRAMAR_SEATS)['seats']
        if {identity(s) for s in current if s['selected']} != chosen:
            raise ValueError('已選座位被變更，保留現況供手動操作。')
        matches = [s for s in current if identity(s) == identity(target) and s['available']]
        if len(matches) != 1:
            raise ValueError('座位已不可選，請手動操作。')
        cell = table.locator('tr').nth(matches[0]['domRow']).locator(':scope > td, :scope > th').nth(matches[0]['cellIndex'])
        cell.click(timeout=3000)
        chosen.add(identity(target))
        deadline = time.monotonic() + 2
        while True:
            if stop.is_set():
                return None
            current = table.evaluate(READ_MIRAMAR_SEATS)['seats']
            actual = {identity(s) for s in current if s['selected']}
            if actual == chosen:
                break
            if actual - chosen or time.monotonic() >= deadline:
                raise ValueError('網站未確認指定座位，未點擊下一步；請手動操作。')
            page.wait_for_timeout(100)
    form = seat_form(page, movie, session, settings.tickets)
    actual = table.evaluate(READ_MIRAMAR_SEATS)['seats']
    if {identity(s) for s in actual if s['selected']} != chosen or len(chosen) != settings.tickets:
        raise ValueError('已選座位數不足或變更，請手動操作。')
    button = form.locator('label[onclick], button, a[onclick]').filter(has_text=re.compile(r'^\s*下一步(?:\s|$)'))
    if button.count() != 1 or not button.is_visible() or not button.is_enabled() or button.get_attribute('aria-disabled') == 'true':
        raise ValueError('找不到唯一可操作的下一步。')
    log('美麗華已確認座位：' + '、'.join(seat_label(s) for s in plan))
    return button
