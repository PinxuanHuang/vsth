import json
import threading
import time
import unittest
from html import escape

from playwright.sync_api import sync_playwright

from config import Settings
from miramar import MiramarFlow
from miramar_seats import READ_MIRAMAR_SEATS, prepare_seats
from seating import plan_seats, seat_label


def fixture(rows=4, columns=8, occupied=(), count=2, handler=None):
    cells = []
    for r in range(rows):
        row = chr(65+r)
        content = []
        for c in range(columns):
            if c == columns // 2:
                content.append('<td></td>')
            name = row + str(c+1)
            if name in occupied:
                content.append(f'<td isoccupy="1" style="background:gray">{name}</td>')
            else:
                content.append(f'<td isoccupy="0" physicalname="{row}" seatid="{c+1}" '
                               f'areanumber="changed-area" areacategorycode="changed-category" '
                               f'rowindex="{rows-r+17}" columnindex="{columns-c+38}" '
                               f'style="background:white" onclick="selectSeat(this)">{name}</td>')
        cells.append('<tr>'+''.join(content)+'</tr>')
    tickets = escape(json.dumps([dict(Qty=count, TicketTypeSeats='1')]), quote=True)
    handler = handler if handler is not None else "cell.style.backgroundColor='rgb(50, 190, 120)'; window.seatClicks=(window.seatClicks||0)+1"
    return ('<!doctype html><meta charset="utf-8"><style>td{width:30px;height:30px}.seat_mark{background:gray}'
            '.seat_mark_select{background:rgb(50,190,120)}.seat_mark_free{background:white}</style>'
            '<div id="booking_data"><ul class="seat_type"><li><div class="seat_mark"></div></li>'
            '<li><div class="seat_mark seat_mark_select"></div></li><li><div class="seat_mark seat_mark_free"></div></li></ul>'
            '<table id="seatTable"><tr><td colspan="50">銀幕</td></tr>'+''.join(cells)+'</table>'
            '<form action="/Booking/SeatPlan" method="post"><input type="hidden" name="MovieId" value="movie">'
            '<input type="hidden" name="Session" value="session"><input type="hidden" name="Seat">'
            f'<input type="hidden" name="TicketType" value="{tickets}">'
            '<label onclick="window.nextClicks=(window.nextClicks||0)+1">下一步 NEXT</label></form></div>'
            '<script>function selectSeat(cell){'+handler+'}</script>')


class MiramarSeatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(channel='msedge', headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.page.route('**/*', lambda route: route.fulfill(body=fixture(), content_type='text/html; charset=utf-8'))
        self.page.goto('https://www.miramarcinemas.tw/Booking/SeatPlan')
        self.settings = Settings(tickets=2, seat_mode='custom', seat_col_start=0, seat_col_end=100)

    def tearDown(self):
        self.page.close()

    def prepare(self):
        return prepare_seats(self.page, self.settings, 'movie', 'session', threading.Event(), lambda _: None)

    def selected(self):
        return [seat_label(s) for s in self.page.locator('#seatTable').evaluate(READ_MIRAMAR_SEATS)['seats'] if s['selected']]

    def test_preferred_then_next_once(self):
        self.settings.seat_preferred = 'A2,A3'
        flow = MiramarFlow(self.settings, lambda *x: None, threading.Event())
        flow.page, flow.stage, flow.deadline = self.page, 6, time.monotonic()+30
        flow.selected = ['cinema', 'movie', 'date', 'session']
        flow.tick([self.page])
        flow.tick([self.page])
        self.assertEqual(self.selected(), ['A2','A3'])
        self.assertEqual(self.page.evaluate('window.nextClicks'), 1)
        self.assertEqual(flow.stage, 7)

    def test_dynamic_geometry_and_walkway(self):
        for rows, columns in ((3,6),(6,10)):
            self.page.set_content(fixture(rows,columns))
            data=self.page.locator('#seatTable').evaluate(READ_MIRAMAR_SEATS)
            self.assertEqual(len(data['seats']),rows*columns)
            self.assertLess(data['seats'][0]['y'],data['seats'][-1]['y'])
            self.assertGreater(int(data['seats'][0]['backendRow']),int(data['seats'][-1]['backendRow']))
            self.settings.seat_preferred=f'A{columns//2},A{columns//2+1}'
            plan=plan_seats(data['seats'],self.settings)
            self.assertEqual(abs(plan[0]['gridCol']-plan[1]['gridCol']),1)
            self.assertFalse(any(s['merged'] for s in data['seats']))

    def test_sold_preferred_falls_back_inside_range(self):
        self.page.set_content(fixture(occupied=('A2','A3')))
        self.settings.seat_preferred='A2,A3'
        self.settings.seat_row_start=50
        self.prepare().click()
        self.assertTrue(all(name[0] in 'CD' for name in self.selected()))

    def test_insufficient_no_clicks(self):
        self.page.set_content(fixture(rows=1,columns=2,occupied=('A1',)))
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual(self.selected(),[])
        self.assertIsNone(self.page.evaluate('window.nextClicks'))

    def test_no_confirmation_no_next(self):
        self.page.set_content(fixture(handler=''))
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertIsNone(self.page.evaluate('window.nextClicks'))

    def test_missing_legend_wrong_session_and_cancellation(self):
        self.page.locator('.seat_type').evaluate('el => el.remove()')
        with self.assertRaises(ValueError):
            self.prepare()
        self.page.set_content(fixture())
        self.page.locator('input[name="Session"]').evaluate("el => el.value='different'")
        with self.assertRaises(ValueError):
            self.prepare()
        self.page.set_content(fixture())
        stop=threading.Event()
        stop.set()
        self.assertIsNone(prepare_seats(self.page,self.settings,'movie','session',stop,lambda _:None))
        self.assertEqual(self.selected(),[])

    def test_insufficient_hands_off_without_submit(self):
        self.page.set_content(fixture(rows=1,columns=2,occupied=('A1','A2')))
        messages=[]
        flow=MiramarFlow(self.settings,lambda *x: messages.append(x),threading.Event())
        flow.page,flow.stage,flow.deadline=self.page,6,time.monotonic()+30
        flow.selected=['cinema','movie','date','session']
        flow.tick([self.page])
        self.assertTrue(flow.paused)
        self.assertTrue(any(m[0]=='handoff' for m in messages))
        self.assertIsNone(self.page.evaluate('window.nextClicks'))

    def test_manual_existing_selection_and_wrong_count(self):
        self.settings.seat_mode='manual'
        with self.assertRaises(ValueError):
            self.prepare()
        self.settings.seat_mode='custom'
        self.page.locator('td[seatid]').first.click()
        with self.assertRaises(ValueError):
            self.prepare()
        self.page.set_content(fixture(count=3))
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertIsNone(self.page.evaluate('window.nextClicks'))


if __name__ == '__main__':
    unittest.main()
