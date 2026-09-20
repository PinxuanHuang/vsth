"""Read seat labels and physical grid positions without assuming hall dimensions."""

READ_SEATS = """tables => tables.flatMap((table, areaIndex) => {
    if (!table.getClientRects().length) return [];
    const rows = Array.from(table.rows);
    const occupied = rows.filter(row => row.querySelector('td[data-name][data-col]'));
    const width = Math.max(...rows.map(row => Array.from(row.cells).reduce((n, c) => n + c.colSpan, 0)));
    const merged = rows.some(row => Array.from(row.cells).some(cell => cell.colSpan !== 1 || cell.rowSpan !== 1));
    return rows.flatMap((row, gridRow) => {
        let col = 0;
        return Array.from(row.cells).flatMap(cell => {
            const gridCol = col;
            col += cell.colSpan;
            if (!cell.matches('td[data-name][data-col][data-row][data-seatnum]')) return [];
            const img = cell.querySelector('img');
            const src = img ? new URL(img.getAttribute('src'), document.baseURI).pathname : '';
            const selected = src.endsWith('_selected.png') || (!src && cell.dataset.status === '5');
            const selectable = cell.dataset.type === 'Empty' && ['0', '5'].includes(cell.dataset.status)
                && !cell.classList.contains('unselectable') && cell.getClientRects().length > 0
                && cell.getAttribute('aria-disabled') !== 'true';
            return [{id: cell.id, area: areaIndex, areaNumber: table.dataset.areaNumber || '',
                areaCode: cell.dataset.areacode || '', row: cell.dataset.name, label: cell.dataset.col,
                backendRow: cell.dataset.row, backendSeat: cell.dataset.seatnum,
                gridRow, gridCol, merged,
                x: 100 * (gridCol + cell.colSpan / 2) / width,
                y: 100 * (occupied.indexOf(row) + 0.5) / occupied.length,
                selected, selectable, available: !selected && selectable
                    && (src ? src.endsWith('standard_available.png') : cell.dataset.status === '0')}];
        });
    });
})"""


def seat_key(seat):
    return seat['area'], seat['gridRow'], seat['gridCol']


def seat_bounds(settings):
    if settings.seat_mode == 'custom':
        return settings.seat_row_start, settings.seat_row_end, settings.seat_col_start, settings.seat_col_end
    start, end = {'front': (0, 100 / 3), 'middle': (100 / 3, 200 / 3),
                  'back': (200 / 3, 100)}[settings.seat_mode]
    return start, end, 25, 75


def plan_seats(seats, settings):
    settings.validate_seats()
    if settings.seat_mode == 'manual':
        return []
    if any(s['merged'] for s in seats):
        raise ValueError('座位圖含合併格，請手動選位。')
    r0, r1, c0, c1 = seat_bounds(settings)
    cx, cy = (c0 + c1) / 2, (r0 + r1) / 2
    direction = 1 if settings.seat_direction == 'left' else -1
    region = [s for s in seats if r0 <= s['y'] <= r1 and c0 <= s['x'] <= c1]
    candidates = [s for s in region if s['available'] or (s['selected'] and s['selectable'])]
    row_priority = {}
    for area in sorted({s['area'] for s in region}):
        rows = {s['gridRow']: s['y'] for s in region if s['area'] == area}
        anchor = min(rows, key=lambda row: (round(abs(rows[row] - cy), 8), row))
        # Sold-out rows still define the anchor; exhaust rear rows before front rows.
        order = [anchor] + sorted(row for row in rows if row > anchor) + sorted(
            (row for row in rows if row < anchor), reverse=True)
        row_priority.update({(area, row): index for index, row in enumerate(order)})

    def rank(s):
        return row_priority[s['area'], s['gridRow']], abs(s['x'] - cx), s['gridRow'], direction * s['gridCol'], s['area']

    plans = []
    if settings.seat_contiguous:
        groups = {}
        for s in candidates:
            groups.setdefault((s['area'], s['gridRow']), []).append(s)
        for row in groups.values():
            row.sort(key=lambda s: s['gridCol'])
            for i in range(len(row) - settings.tickets + 1):
                block = row[i:i + settings.tickets]
                if block[-1]['gridCol'] - block[0]['gridCol'] != settings.tickets - 1:
                    continue
                center = (block[0]['x'] + block[-1]['x']) / 2
                score = (row_priority[block[0]['area'], block[0]['gridRow']], abs(center - cx), block[0]['gridRow'],
                         direction * block[0]['gridCol'], block[0]['area'])
                plans.append((score, sorted(block, key=rank)))
    else:
        for area in sorted({s['area'] for s in candidates}):
            ordered = sorted((s for s in candidates if s['area'] == area), key=rank)
            if len(ordered) >= settings.tickets:
                plans.append((rank(ordered[0]), ordered[:settings.tickets]))
    if not plans:
        kind = '同排連座' if settings.seat_contiguous else '可選座位'
        raise ValueError(f'指定範圍內找不到 {settings.tickets} 個{kind}，請手動選位或調整設定。')
    return min(plans, key=lambda item: item[0])[1]
