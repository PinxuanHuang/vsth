import threading
import unittest
from playwright.sync_api import sync_playwright
from automation import Cancelled, TicketFlow, resource, select_quantity_and_continue, submit_normal_booking, expand_normal_panel, normal_ticket_panel
from config import Settings

BOOKING = 'https://www.vscinemas.com.tw/vsTicketingSP2/ticketing/booking.aspx?cinemacode=21&txtSessionId=165195'
SALES = 'https://sales.vscinemas.com.tw/LiveTicketD4/'


class CheckoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(channel='msedge', headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.context = self.browser.new_context()
        self.requests = []
        def fulfill(route):
            url = route.request.url
            self.requests.append(url)
            if '/Seats' in url:
                body = '<h1>Manual handoff</h1>'
            elif 'sales.vscinemas' in url:
                body = resource('quantity_fixture.html').read_text(encoding='utf-8')
            elif 'booking.aspx' in url:
                body = resource('booking_fixture.html').read_text(encoding='utf-8')
            else:
                body = resource('flow_fixture.html').read_text(encoding='utf-8')
                if 'movie=' in url:
                    body += resource('session_fixture.html').read_text(encoding='utf-8')
            route.fulfill(body=body, content_type='text/html')
        self.context.route('**/*', fulfill)
        self.page = self.context.new_page()
        self.settings = Settings(cinema='MUVIE CINEMAS 台北松仁', agree=True, tickets=3, showtime='2026-09-25 19:20')
        self.stop = threading.Event()
        self.events = []

    def tearDown(self):
        self.context.close()

    def test_end_to_end_handoff_keeps_browser_and_does_not_repeat(self):
        self.page.goto('https://www.vscinemas.com.tw/vsTicketingSP2/ticketing/ticket.aspx')
        flow = TicketFlow(self.settings, lambda *x: self.events.append(x), self.stop)
        flow.tick(self.context.pages)
        self.page.wait_for_url(SALES + '*')
        flow.tick(self.context.pages)
        self.assertIn('/Seats?count=3', self.page.url)
        self.assertTrue(any(kind == 'handoff' for kind, _ in self.events))
        self.assertFalse(self.page.is_closed())
        before = len(self.requests)
        flow.tick(self.context.pages)
        self.assertEqual(len(self.requests), before)
        self.assertIn('agree=on', next(url for url in self.requests if 'sales.vscinemas' in url))

    def test_manual_consent_then_automatic_quantity(self):
        self.settings.agree = False
        self.page.goto('https://www.vscinemas.com.tw/vsTicketingSP3/ticketing/ticket.aspx')
        flow = TicketFlow(self.settings, lambda *x: self.events.append(x), self.stop)
        flow.tick(self.context.pages)
        self.assertFalse(self.page.locator('#bookNormal input#agree').is_checked())
        self.assertFalse(self.page.locator('#bookOther input#agree').is_checked())
        self.page.locator('a[href="#bookNormal"]').click()
        self.page.locator('#bookNormal input#agree').check()
        self.page.locator('#bookNormal input[type=submit]').click()
        flow.tick(self.context.pages)
        self.assertIn('/Seats?count=3', self.page.url)

    def test_consent_false_does_nothing(self):
        self.page.goto(BOOKING)
        self.settings.agree = False
        submit_normal_booking(self.page, self.settings, lambda _: None, self.stop)
        self.assertEqual(self.page.url, BOOKING)
        self.assertFalse(self.page.locator('#bookOther input#agree').is_checked())
        self.assertFalse(self.page.locator('#bookNormal input#agree').is_checked())

    def test_ticket_limit_prevents_continue(self):
        self.page.goto(SALES)
        self.settings.tickets = 5
        with self.assertRaisesRegex(ValueError, '不允許'):
            select_quantity_and_continue(self.page, self.settings, lambda _: None, self.stop)
        self.assertEqual(self.page.locator('[data-pricecode]').input_value(), '0')
        self.assertEqual(self.page.url, SALES)

    def test_multiple_ticket_types_require_unambiguous_selector(self):
        self.page.goto(SALES)
        self.page.locator('[data-pricecode]').evaluate("el => {const copy=el.cloneNode(true);copy.id='another';el.after(copy)}")
        with self.assertRaisesRegex(ValueError, '多個票數'):
            select_quantity_and_continue(self.page, self.settings, lambda _: None, self.stop)
        self.settings.tickets_selector = '#DYNAMIC-ID-999'
        select_quantity_and_continue(self.page, self.settings, lambda _: None, self.stop)
        self.assertIn('/Seats?count=3', self.page.url)

    def test_validation_failure_is_not_reported_as_handoff(self):
        self.page.goto(SALES)
        self.page.locator('#btnDoNext').evaluate("el => el.onclick=()=>false")
        with self.assertRaisesRegex(ValueError, '下一個購票步驟'):
            select_quantity_and_continue(self.page, self.settings, lambda _: None, self.stop, timeout=.3)
        self.assertFalse(self.page.is_closed())

    def test_collapsed_normal_panel_and_dynamic_attributes(self):
        self.page.goto(SALES)
        self.page.locator('#random-collapse').evaluate('el => el.hidden=true')
        self.page.locator('.panel-title a').evaluate("el => el.setAttribute('aria-expanded','false')")
        self.page.locator('[data-pricecode]').evaluate("el => {el.id='changed';el.removeAttribute('data-hocode');el.removeAttribute('data-pricecode')}")
        self.page.locator('#btnDoNext').evaluate("el => el.onclick=()=>{location.href='/LiveTicketD4/Seats?count='+document.querySelector('#changed').value+'&bank='+document.querySelector('#bank').value;return false}")
        flow = TicketFlow(self.settings, lambda *x: self.events.append(x), self.stop)
        flow.post_pending = True
        flow.booking_page = self.page
        flow.tick(self.context.pages)
        self.assertIn('/Seats?count=3&bank=0', self.page.url)
        self.assertTrue(any(kind == 'handoff' for kind, _ in self.events))

    def test_missing_normal_title_does_not_select_bank(self):
        self.page.goto(SALES)
        self.page.locator('.panel-title a').evaluate("el => el.textContent='其他一般票種優惠'")
        with self.assertRaisesRegex(ValueError, '一般票種區塊'):
            select_quantity_and_continue(self.page, self.settings, lambda _: None, self.stop, timeout=.3)
        self.assertEqual(self.page.locator('#bank').input_value(), '0')
        self.assertEqual(self.page.url, SALES)

    def test_hidden_other_ticket_quantity_blocks_continue(self):
        self.page.goto(SALES)
        self.page.locator('#bank').evaluate("el => {el.value='3';el.hidden=true}")
        with self.assertRaisesRegex(ValueError, '其他票種已有張數'):
            select_quantity_and_continue(self.page, self.settings, lambda _: None, self.stop)
        self.assertEqual(self.page.locator('[data-pricecode]').input_value(), '0')

    def test_full_price_row_with_discount_first_and_description(self):
        self.page.goto(SALES)
        self.page.locator('.spName').filter(has_text='全票').evaluate("el => el.after(document.createTextNode(' 成人適用'))")
        self.page.locator('#btnDoNext').evaluate("el => el.onclick=()=>{location.href='/LiveTicketD4/Seats?count='+document.querySelector('[data-pricecode]').value+'&discount='+document.querySelector('#discount').value;return false}")
        select_quantity_and_continue(self.page, self.settings, lambda _: None, self.stop)
        self.assertIn('/Seats?count=3&discount=0', self.page.url)
        self.assertFalse(self.page.is_closed())

    def test_plain_td_full_price_name(self):
        self.page.goto(SALES)
        self.page.locator('.spName').filter(has_text='全票').evaluate("el => el.parentElement.textContent=' 全票 '")
        select_quantity_and_continue(self.page, self.settings, lambda _: None, self.stop)
        self.assertIn('/Seats?count=3', self.page.url)

    def test_no_exact_full_price_row_does_not_select_discount(self):
        self.page.goto(SALES)
        self.page.locator('.spName').filter(has_text='全票').evaluate("el => el.textContent='全票優惠套票'")
        with self.assertRaisesRegex(ValueError, '全票列'):
            select_quantity_and_continue(self.page, self.settings, lambda _: None, self.stop, timeout=.3)
        self.assertEqual(self.page.locator('#discount').input_value(), '0')
        self.assertEqual(self.page.url, SALES)

    def test_duplicate_full_price_rows_are_not_guessed(self):
        self.page.goto(SALES)
        self.page.locator('tr').filter(has=self.page.locator('[data-pricecode]')).evaluate("el => el.after(el.cloneNode(true))")
        with self.assertRaisesRegex(ValueError, '多個全票列'):
            select_quantity_and_continue(self.page, self.settings, lambda _: None, self.stop)
        self.assertEqual(self.page.url, SALES)

    def test_cancel_and_mismatched_session(self):
        self.page.goto(BOOKING)
        self.page.locator('#bookNormal input[name=txtSessionId]').evaluate("el => el.value='999'")
        with self.assertRaisesRegex(ValueError, '場次不符'):
            submit_normal_booking(self.page, self.settings, lambda _: None, self.stop)
        self.assertFalse(self.page.locator('#bookNormal input#agree').is_checked())
        self.page.goto(SALES)
        self.stop.set()
        with self.assertRaises(Cancelled):
            select_quantity_and_continue(self.page, self.settings, lambda _: None, self.stop)

    def test_stale_aria_and_inactive_handler_are_repaired(self):
        self.page.goto(SALES)
        toggle = self.page.locator('.panel-title a')
        toggle.evaluate("el => {el.setAttribute('aria-expanded','true');el.onclick=()=>false}")
        expand_normal_panel(self.page, normal_ticket_panel(self.page), lambda _: None, self.stop, .3)
        self.assertEqual(toggle.get_attribute('aria-expanded'), 'true')
        self.assertNotIn('collapsed', toggle.get_attribute('class'))
        content = self.page.locator('#random-collapse')
        self.assertIn('in', content.get_attribute('class').split())
        self.assertEqual(content.get_attribute('aria-expanded'), 'true')
        self.assertTrue(self.page.locator('[data-pricecode]').is_visible())
        self.assertGreater(content.bounding_box()['height'], 0)

    def test_open_content_is_not_toggled_closed_by_stale_aria(self):
        self.page.goto(SALES)
        content = self.page.locator('#random-collapse')
        content.evaluate("el => {el.classList.add('in');el.style.height='auto'}")
        self.page.locator('.panel-title a').evaluate("el => el.onclick=()=>{throw Error('must not click open panel')}")
        errors = []
        self.page.on('pageerror', lambda error: errors.append(str(error)))
        expand_normal_panel(self.page, normal_ticket_panel(self.page), lambda _: None, self.stop, .3)
        self.assertFalse(errors)
        self.assertTrue(content.is_visible())

    def test_waits_for_expand_animation(self):
        self.page.goto(SALES)
        self.page.locator('.panel-title a').evaluate("""el => el.onclick=()=>{
            const body=document.querySelector('#random-collapse');
            body.className='panel-collapse collapsing';body.style.height='1px';
            setTimeout(()=>{body.className='panel-collapse collapse in';body.style.height='auto'},250);
            return false;
        }""")
        logs = []
        expand_normal_panel(self.page, normal_ticket_panel(self.page), logs.append, self.stop, 2)
        self.assertFalse(any('同步修正' in item for item in logs))
        self.assertNotIn('collapsing', self.page.locator('#random-collapse').get_attribute('class'))
