import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright

from automation import Cancelled, TicketFlow, resource, select_quantity_and_continue, ticket_panel
from config import Settings, load_settings, save_settings
from ticket_types import TICKET_TYPES, TicketType, get_ticket_type


SALES = 'https://sales.vscinemas.com.tw/LiveTicketD4/'
PACKAGE = 'special_single_package'


class TicketTypeConfigTests(unittest.TestCase):
    def test_legacy_defaults_and_package_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            path.write_text(json.dumps({'url': 'demo://ticket', 'cinema': 'test'}))
            self.assertEqual(load_settings(path).ticket_type, 'full_price')
            settings = Settings(url='demo://ticket', cinema='test', ticket_type=PACKAGE)
            save_settings(settings, path)
            self.assertEqual(load_settings(path), settings)

    def test_invalid_type_is_not_silently_replaced(self):
        for value in ('unknown', '', None, [], 1):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, '票種設定'):
                Settings(url='demo://ticket', cinema='test', ticket_type=value).validate()


class TicketTypeBrowserTests(unittest.TestCase):
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
        self.fixture = resource('package_quantity_fixture.html').read_text(encoding='utf-8')
        self.context.route('**/*', lambda route: route.fulfill(body=self.fixture, content_type='text/html'))
        self.page = self.context.new_page()
        self.page.goto(SALES)
        self.settings = Settings(tickets=3, ticket_type=PACKAGE)
        self.stop = threading.Event()
        self.logs = []
        self.observe_submission()

    def tearDown(self):
        self.context.close()

    def observe_submission(self):
        self.page.locator('#btnDoNext').evaluate("""el => el.onclick=event => {
            event.preventDefault();
            window.submitted = [...document.querySelectorAll('select.form-control-s')].map(select => ({
                name: select.closest('tr').querySelector('.spName').textContent.trim(),
                count: select.value, visible: select.getClientRects().length > 0
            }));
            document.body.innerHTML='<h1>Next step</h1>';
        }""")

    def run_selection(self):
        select_quantity_and_continue(self.page, self.settings, self.logs.append, self.stop, timeout=.4)

    def assert_untouched(self):
        self.assertEqual(self.page.url, SALES)
        self.assertIsNone(self.page.evaluate('window.submitted'))
        self.assertTrue(all(value == '0' for value in self.page.locator('select').evaluate_all('els => els.map(el => el.value)')))

    def test_both_products_expand_only_the_selected_category(self):
        for key, kind in TICKET_TYPES.items():
            with self.subTest(key=key):
                self.page.goto(SALES)
                self.observe_submission()
                self.settings.ticket_type = key
                self.run_selection()
                self.assertEqual(self.page.evaluate('expandedTitles'), [kind.category])
                selected = [item for item in self.page.evaluate('submitted') if item['count'] != '0']
                self.assertEqual(selected, [{'name': kind.name, 'count': '3', 'visible': True}])
                self.assertTrue(any(kind.label in line for line in self.logs))

    def test_dynamic_ids_and_codes_do_not_control_selection(self):
        self.page.locator('select').evaluate_all("els => els.forEach(el => {el.id='changed-'+el.id;el.removeAttribute('data-pricecode');el.removeAttribute('data-hocode')})")
        panel = ticket_panel(self.page, '優惠套票')
        panel.locator('.panel-collapse').evaluate("el => el.id='new-collapse'")
        panel.locator('.panel-title a').evaluate("el => el.setAttribute('href','#new-collapse')")
        self.run_selection()
        self.assertEqual(self.page.evaluate('submitted[0].count'), '3')

    def test_inactive_collapse_handler_uses_scoped_recovery(self):
        ticket_panel(self.page, '優惠套票').locator('.panel-title a').evaluate('el => el.onclick=()=>false')
        self.run_selection()
        self.assertEqual(self.page.evaluate('submitted[0].count'), '3')
        self.assertFalse(self.page.evaluate('submitted[1].visible'))
        self.assertTrue(any('同步修正優惠套票' in line for line in self.logs))

    def test_package_name_must_match_exactly(self):
        # The overlay and description still contain the original name.
        ticket_panel(self.page, '優惠套票').locator('.spName').evaluate("el => el.textContent='特殊映演單人套票升級版'")
        with self.assertRaisesRegex(ValueError, '特殊映演單人套票列'):
            self.run_selection()
        self.assert_untouched()

    def test_missing_package_never_falls_back_to_full_price(self):
        ticket_panel(self.page, '優惠套票').evaluate('el => el.remove()')
        with self.assertRaisesRegex(ValueError, '優惠套票區塊'):
            self.run_selection()
        self.assert_untouched()

    def test_duplicate_categories_or_products_stop(self):
        for selector, message in (('.panel', '多個優惠套票區塊'), ('tbody tr', '多個特殊映演單人套票列')):
            with self.subTest(selector=selector):
                self.page.goto(SALES)
                self.observe_submission()
                item = ticket_panel(self.page, '優惠套票') if selector == '.panel' else ticket_panel(self.page, '優惠套票').locator(selector)
                item.evaluate('el => el.after(el.cloneNode(true))')
                with self.assertRaisesRegex(ValueError, message):
                    self.run_selection()
                self.assert_untouched()

    def test_quantity_limit_and_disabled_option_stop(self):
        for disabled in (False, True):
            with self.subTest(disabled=disabled):
                self.settings.tickets = 3 if disabled else 5
                if disabled:
                    ticket_panel(self.page, '優惠套票').locator('option[value="3"]').evaluate('el => el.disabled=true')
                with self.assertRaisesRegex(ValueError, '不允許'):
                    self.run_selection()
                self.assert_untouched()

    def test_other_hidden_ticket_quantity_blocks_submission(self):
        ticket_panel(self.page, '一般票種').locator('select').select_option('2', force=True)
        with self.assertRaisesRegex(ValueError, '其他票種已有張數'):
            self.run_selection()
        self.assertEqual(ticket_panel(self.page, '優惠套票').locator('select').input_value(), '0')
        self.assertIsNone(self.page.evaluate('window.submitted'))

    def test_custom_selector_cannot_escape_requested_product(self):
        self.settings.tickets_selector = '#HO000000010001'
        with self.assertRaisesRegex(ValueError, '特殊映演單人套票列的票數選單'):
            self.run_selection()
        self.assert_untouched()

    def test_duplicate_quantity_can_use_scoped_custom_selector(self):
        ticket_panel(self.page, '優惠套票').locator('select').evaluate("el => {const copy=el.cloneNode(true);copy.id='duplicate';el.after(copy)}")
        with self.assertRaisesRegex(ValueError, '多個票數選單'):
            self.run_selection()
        self.settings.tickets_selector = '#HO000010091032'
        self.run_selection()
        self.assertEqual([item['count'] for item in self.page.evaluate('submitted')], ['3', '0', '0'])

    def test_cancellation_does_not_expand_or_select(self):
        self.stop.set()
        with self.assertRaises(Cancelled):
            self.run_selection()
        self.assertEqual(self.page.evaluate('expandedTitles'), [])
        self.assert_untouched()

    def test_registry_extension_uses_same_workflow_and_literal_names(self):
        kind = TicketType('優惠套票', '測試票種 (A+B)')
        ticket_panel(self.page, kind.category).locator('.spName').evaluate('(el, text) => el.textContent=text', kind.name)
        with patch.dict(TICKET_TYPES, {'new_product': kind}):
            self.settings.ticket_type = 'new_product'
            self.run_selection()
        self.assertEqual(self.page.evaluate('submitted[0].name'), kind.name)

    def test_flow_with_only_package_panel_through_seats_and_login_once(self):
        posts, quantities = [], []
        def serve(route):
            path = urlsplit(route.request.url).path
            if route.request.method == 'POST':
                posts.append(route.request.url)
                body = '<button id="payment" onclick="window.paid=true">Pay</button>'
            elif '/Seats' in path:
                quantities.append(parse_qs(urlsplit(route.request.url).query))
                body = resource('seats_fixture.html').read_text(encoding='utf-8')
                body += '<script>document.querySelector("#btnCheckOut").onclick=()=>location.href="/LiveTicketD4/Home/OrderConfirm";</script>'
            elif '/OrderConfirm' in path:
                body = resource('checkout_login_fixture.html').read_text(encoding='utf-8')
            else:
                body = self.fixture
            route.fulfill(body=body, content_type='text/html')
        self.context.unroute('**/*')
        self.context.route('**/*', serve)
        self.page.goto(SALES)
        ticket_panel(self.page, '一般票種').evaluate('el => el.remove()')
        self.settings.seat_mode, self.settings.seat_preferred = 'middle', 'B1,B2,B3'
        self.settings.login_email, self.settings.login_password = 'test@example.com', 'test-only-password'
        flow = TicketFlow(self.settings, lambda *event: self.logs.append(event), self.stop)
        flow.pending, flow.post_pending, flow.booking_page = False, True, self.page
        flow.tick(self.context.pages)
        self.assertEqual(quantities, [{'count': ['3'], 'product': ['特殊映演單人套票']}])
        self.assertEqual(len(posts), 1)
        self.assertTrue(any(event[0] == 'handoff' for event in self.logs))
        self.assertIsNone(self.page.evaluate('window.paid'))
        flow.tick(self.context.pages)
        self.assertEqual(len(posts), 1)

    def test_flow_reports_missing_category_once(self):
        ticket_panel(self.page, '優惠套票').evaluate('el => el.remove()')
        flow = TicketFlow(self.settings, lambda *event: self.logs.append(event), self.stop)
        flow.pending, flow.post_pending, flow.booking_page = False, True, self.page
        def select_with_short_wait(page, settings, log, stop):
            return select_quantity_and_continue(page, settings, log, stop, timeout=.3)
        with patch('automation.select_quantity_and_continue', side_effect=select_with_short_wait) as select:
            flow.tick(self.context.pages)
            flow.tick(self.context.pages)
            self.assertEqual(select.call_count, 1)
        self.assertTrue(any('優惠套票區塊' in event[1] for event in self.logs))
        self.assert_untouched()


if __name__ == '__main__':
    unittest.main()
