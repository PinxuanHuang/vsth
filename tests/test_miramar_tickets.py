import threading
import unittest
from html import escape
from playwright.sync_api import sync_playwright

from config import Settings, BOOKING_SITES
from miramar import MiramarFlow
from miramar_tickets import prepare_tickets
from ticket_types import MIRAMAR_TICKET_TYPES


def fixture(movie='movie-new', session='session-b', seats=1, maximum=6):
    rows = []
    for index, kind in enumerate(MIRAMAR_TICKET_TYPES.values()):
        options = ''.join(f'<option value="{n}">{n}</option>' for n in range(maximum + 1))
        rows.append(f'<tr class="TicketTypeData" tickettypetitlealt="{escape(kind.name, quote=True)}" '
                    f'tickettypeseats="{seats}" tickettypecode="changed-{index}" tickettypeprice="99900">'
                    f'<td>{escape(kind.name)}</td><td><select id="dynamic-{index}" '
                    f'onchange="window.changes=(window.changes||0)+1">{options}</select></td></tr>')
    return ('<!doctype html><meta charset="utf-8"><div id="booking_data"><form action="/Booking/TicketType" method="post">'
            f'<input name="MovieId" type="hidden" value="{escape(movie, quote=True)}">'
            f'<input name="Session" type="hidden" value="{escape(session, quote=True)}">'
            '<table id="ticketTypeTable">' + ''.join(rows) + '</table>'
            '<table id="ticketTypeTable"><tr class="ConcessionData"><td><select class="ticket_type_select"><option value="0">0</option><option value="1">1</option></select></td></tr></table>'
            '<label onclick="window.nextClicks=(window.nextClicks||0)+1">下一步 NEXT <i>arrow_forward</i></label>'
            '</form></div>')


class MiramarTicketTests(unittest.TestCase):
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
        self.page.route('**/*', lambda route: route.fulfill(body=fixture(), content_type='text/html'))
        self.page.goto('https://www.miramarcinemas.tw/Booking/TicketType?id=movie-new&session=session-b')
        self.settings = Settings(url=BOOKING_SITES['美麗華影城'], tickets=2)

    def tearDown(self):
        self.page.close()

    def prepare(self):
        return prepare_tickets(self.page, self.settings, 'movie-new', 'session-b', threading.Event(), lambda _: None)

    def test_all_names_with_changed_codes(self):
        self.assertEqual(len(MIRAMAR_TICKET_TYPES), 26)
        for index, key in enumerate(MIRAMAR_TICKET_TYPES):
            self.page.set_content(fixture())
            self.settings.miramar_ticket_type = key
            self.prepare().click()
            self.assertEqual(self.page.locator(f'#dynamic-{index}').input_value(), '2')
            self.assertEqual(self.page.locator('.ConcessionData select').input_value(), '0')

    def test_package_seats_and_limits(self):
        self.page.set_content(fixture(seats=2, maximum=3))
        self.settings.miramar_ticket_type = 'double_hotdog'
        self.settings.tickets = 4
        self.prepare()
        self.assertEqual(self.page.locator('#dynamic-3').input_value(), '2')
        for count in (3, 8):
            self.settings.tickets = count
            with self.assertRaises(ValueError):
                self.prepare()
        self.assertIsNone(self.page.evaluate('window.nextClicks'))

    def test_missing_duplicate_disabled_and_other_purchases_stop(self):
        mutations = [
            "document.querySelector('.TicketTypeData').remove()",
            "document.querySelector('#ticketTypeTable tbody').append(document.querySelector('.TicketTypeData').cloneNode(true))",
            "document.querySelector('#dynamic-0').disabled=true",
            "document.querySelector('#dynamic-0 option[value=\"2\"]').disabled=true",
            "document.querySelector('#dynamic-1').value='1'",
            "document.querySelector('.ConcessionData select').value='1'",
            "document.querySelector('[name=Session]').value='wrong'",
            "document.querySelector('form').action='https://example.com/'",
        ]
        for script in mutations:
            with self.subTest(script=script):
                self.page.set_content(fixture())
                self.page.evaluate(script)
                with self.assertRaises(ValueError):
                    self.prepare()
                self.assertIsNone(self.page.evaluate('window.nextClicks'))

    def test_full_flow_next_is_clicked_once(self):
        flow = MiramarFlow(self.settings, lambda *x: None, threading.Event())
        flow.page, flow.stage = self.page, 5
        flow.selected = ['cinema', 'movie-new', '2031-02-03', 'session-b']
        flow.tick([self.page])
        flow.tick([self.page])
        self.assertEqual(self.page.evaluate('window.nextClicks'), 1)
        self.assertEqual(flow.stage, 6)

    def test_cancel_does_not_change_quantity(self):
        stop = threading.Event()
        stop.set()
        self.assertIsNone(prepare_tickets(self.page, self.settings, 'movie-new', 'session-b', stop, lambda _: None))
        self.assertEqual(self.page.locator('#dynamic-0').input_value(), '0')


if __name__ == '__main__':
    unittest.main()
