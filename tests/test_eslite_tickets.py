import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import sync_playwright

from config import BOOKING_SITES, Settings, load_settings, save_settings
from eslite_tickets import EsliteTicketFlow, READ_TICKETS, ticket_name, ticket_session
from ticket_types import ESLITE_TICKET_TYPES


URL = 'https://arthouse.eslite.com/visSelectTickets.aspx?cinemacode=cin-test&txtSessionId=session-test'


def ticket_fixture():
    markup = Path(__file__).with_name('eslite_tickets_fixture.html').read_text(encoding='utf-8')
    return '<!doctype html><meta charset="utf-8"><form>' + markup + r'''</form><script>
        window.posts = [];
        window.changes = [];
        window.holdButtons = false;
        window.holdTotals = false;
        window.rejectQuantity = false;
        window.CheckAvailableSeats = function(available, value, id) {
            changes.push([id, value]);
            if (rejectQuantity) document.getElementById(id).value = '0';
        };
        window.CalculateTotals = function(amount, digits, cents, subtotal) {
            if (!holdTotals) subtotal.value = Number(amount) * Number(cents) / 100;
            if (!holdButtons) {
                document.getElementById('divOrderTickets').style.visibility = 'visible';
                document.getElementById('divSelectSeats').style.visibility = 'visible';
            }
        };
        window.DisableButtonsA = function() { return true; };
        window.__doPostBack = function(target) { posts.push(target); };
        document.getElementById('divOrderTickets').style.visibility = 'hidden';
        document.getElementById('divSelectSeats').style.visibility = 'hidden';
    </script>'''


class EsliteTicketSettingsTests(unittest.TestCase):
    def test_independent_settings_roundtrip_and_legacy_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'settings.json'
            settings = Settings(url=BOOKING_SITES['誠品'], showtime='2032-10-03', ticket_type='special_single_package',
                                eslite_ticket_type='member')
            save_settings(settings, path)
            self.assertEqual(load_settings(path), settings)
            path.write_text(json.dumps({'url': BOOKING_SITES['誠品'], 'showtime': '2032-10-03'}), encoding='utf-8')
            self.assertEqual(load_settings(path).eslite_ticket_type, 'full_price')
            with self.assertRaises(ValueError):
                Settings(url=BOOKING_SITES['誠品'], eslite_ticket_type='special_single_package').validate()

    def test_session_validation(self):
        self.assertEqual(ticket_session(URL), ('cin-test', 'session-test'))
        for url in (URL.replace('https:', 'http:'), URL.replace('arthouse.eslite.com', 'example.com'),
                    URL + '&txtSessionId=duplicate', URL.replace('visSelectTickets', 'visSelect')):
            self.assertIsNone(ticket_session(url))


class EsliteTicketBrowserTests(unittest.TestCase):
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
        self.page.route('**/*', lambda route: route.fulfill(body=ticket_fixture(), content_type='text/html; charset=utf-8'))
        self.page.goto(URL)
        self.stop = threading.Event()
        self.settings = Settings(url=BOOKING_SITES['誠品'], tickets=3)
        self.messages = []
        self.attempted = set()
        self.flow = self.new_flow()

    def tearDown(self):
        self.page.close()

    def new_flow(self):
        return EsliteTicketFlow(self.settings, lambda *args: self.messages.append(args),
                               self.stop, URL, self.attempted)

    def test_supplied_dom_and_every_ticket_type(self):
        for index, key in enumerate(ESLITE_TICKET_TYPES):
            with self.subTest(key=key):
                self.page.goto(URL)
                self.attempted.clear()
                self.settings.eslite_ticket_type = key
                self.flow = self.new_flow()
                rows = self.page.evaluate(READ_TICKETS)
                self.assertEqual(len(rows), 5)
                self.assertEqual(ticket_name(rows[index]), ESLITE_TICKET_TYPES[key].name)
                self.assertFalse(self.flow.tick(self.page))
                self.assertEqual(self.page.locator('select').nth(index).input_value(), '3')
                self.assertTrue(self.flow.tick(self.page))
                self.page.wait_for_function('posts.length === 1')
                self.assertEqual(self.page.evaluate('posts'), ['ibtnOrderTickets'])
                self.assertEqual(len(self.page.evaluate('changes')), 1)
                with self.assertRaisesRegex(ValueError, '已點擊過'):
                    self.new_flow().tick(self.page)
                self.assertEqual(self.page.evaluate('posts'), ['ibtnOrderTickets'])

    def test_dynamic_price_ids_codes_order_and_option_values(self):
        self.page.evaluate(r'''() => {
            const rows = Array.from(document.querySelectorAll('.TicketType')).map(el => el.closest('tr'));
            rows.forEach((row, i) => {
                row.id = 'dynamic-row-' + i;
                row.querySelectorAll('[id], [name]').forEach((el, j) => {
                    el.id = 'dynamic-' + i + '-' + j;
                    el.name = el.id;
                });
                const select = row.querySelector('select');
                select.setAttribute('identity', 'opaque-type-' + i);
                select.removeAttribute('onchange');
                Array.from(select.options).forEach(o => o.value = 'count-' + o.textContent);
                select.onchange = () => {
                    row.querySelector('.TicketTypeSubTotal').value =
                        Number(select.selectedOptions[0].textContent) * Number(row.querySelector('.TicketTypePrice').value);
                    document.getElementById('divOrderTickets').style.visibility = 'visible';
                };
            });
            rows[0].querySelector('.TicketType').textContent = '全票420:';
            rows[0].querySelector('.TicketTypePrice').value = '420';
            rows[0].querySelector('select').setAttribute('price', '42000');
            rows[0].parentElement.append(rows[0]);
            document.getElementById('tblTickets').id = 'dynamic-table';
            document.getElementById('ibtnOrderTickets').id = 'dynamic-action';
        }''')
        self.assertFalse(self.flow.tick(self.page))
        self.assertEqual(self.page.locator('select').last.input_value(), 'count-3')
        self.assertTrue(self.flow.tick(self.page))
        self.page.wait_for_function('posts.length === 1')
        self.assertEqual(self.page.evaluate('posts'), ['ibtnOrderTickets'])

    def test_clears_other_ticket_quantities(self):
        self.page.locator('select').nth(2).select_option('2')
        self.flow.tick(self.page)
        self.assertEqual(self.page.locator('select').nth(2).input_value(), '0')
        self.assertEqual(self.page.evaluate('posts'), [])
        self.flow.tick(self.page)
        self.assertTrue(self.flow.tick(self.page))
        self.assertEqual([row['quantity'] for row in self.page.evaluate(READ_TICKETS)], ['3', '0', '0', '0', '0'])

    def test_unavailable_or_disabled_quantity_does_not_change_any_row(self):
        self.settings.tickets = 7
        with self.assertRaisesRegex(ValueError, '7 張'):
            self.flow.tick(self.page)
        self.settings.tickets = 3
        self.page.locator('select').first.locator('option[value="3"]').evaluate('el => el.disabled = true')
        with self.assertRaisesRegex(ValueError, '3 張'):
            self.flow.tick(self.page)
        self.assertEqual(self.page.evaluate('changes'), [])
        self.assertEqual(self.page.evaluate('posts'), [])

    def test_waits_for_visible_enabled_button_and_processing(self):
        self.page.evaluate('holdButtons = true')
        self.flow.tick(self.page)
        self.assertFalse(self.flow.tick(self.page))
        self.page.locator('#divOrderTickets').evaluate("el => el.style.visibility = 'visible'")
        self.page.locator('#ibtnOrderTickets').evaluate("el => el.setAttribute('aria-disabled', 'true')")
        self.assertFalse(self.flow.tick(self.page))
        self.page.locator('#ibtnOrderTickets').evaluate("el => el.removeAttribute('aria-disabled')")
        self.page.locator('#ProcAnimation').evaluate("el => el.style.display = 'block'")
        self.assertFalse(self.flow.tick(self.page))
        self.assertEqual(self.page.evaluate('posts'), [])
        self.page.locator('#ProcAnimation').evaluate("el => el.style.display = 'none'")
        self.assertTrue(self.flow.tick(self.page))

    def test_waits_for_totals_and_does_not_retry_rejected_quantity(self):
        for mode in ('holdTotals', 'rejectQuantity'):
            with self.subTest(mode=mode):
                self.page.goto(URL)
                self.flow = self.new_flow()
                self.page.evaluate(f'{mode} = true')
                self.flow.tick(self.page)
                self.assertFalse(self.flow.tick(self.page))
                self.assertEqual(len(self.page.evaluate('changes')), 1)
                self.flow.deadline = 0
                with self.assertRaisesRegex(ValueError, '逾時'):
                    self.flow.tick(self.page)
                self.assertEqual(self.page.evaluate('posts'), [])

    def test_missing_ambiguous_ticket_or_action_never_posts(self):
        self.page.locator('.TicketType').first.evaluate("el => el.textContent = '其他票種310:'")
        with self.assertRaisesRegex(ValueError, '找不到唯一'):
            self.flow.tick(self.page)
        self.page.goto(URL)
        self.page.locator('#Row0').evaluate('el => el.after(el.cloneNode(true))')
        with self.assertRaisesRegex(ValueError, '找不到唯一'):
            self.flow.tick(self.page)
        self.page.goto(URL)
        self.flow.tick(self.page)
        self.page.locator('#ibtnOrderTickets').evaluate('el => el.after(el.cloneNode(true))')
        with self.assertRaisesRegex(ValueError, '多個系統選位'):
            self.flow.tick(self.page)
        self.assertEqual(self.page.evaluate('posts'), [])

    def test_stop_wrong_session_and_disabled_select_do_not_act(self):
        self.stop.set()
        self.assertFalse(self.flow.tick(self.page))
        self.stop.clear()
        self.page.locator('select').first.evaluate('el => el.disabled = true')
        self.assertFalse(self.flow.tick(self.page))
        self.assertEqual(self.page.evaluate('changes'), [])
        self.page.goto(URL.replace('session-test', 'other'))
        with self.assertRaisesRegex(ValueError, '場次已變更'):
            self.flow.tick(self.page)
        self.assertEqual(self.page.evaluate('posts'), [])

    def test_uncertain_click_is_not_repeated(self):
        self.flow.tick(self.page)
        with patch('playwright.sync_api.Locator.click', side_effect=RuntimeError('uncertain navigation')):
            with self.assertRaisesRegex(RuntimeError, 'uncertain navigation'):
                self.flow.tick(self.page)
        self.assertIn(ticket_session(URL), self.attempted)
        with self.assertRaisesRegex(ValueError, '已點擊過'):
            self.new_flow().tick(self.page)
        self.assertEqual(self.page.evaluate('posts'), [])

    def test_member_variants_are_ambiguous_not_arbitrarily_selected(self):
        self.settings.eslite_ticket_type = 'member'
        self.page.locator('#Row3').evaluate("""el => {
            const other = el.cloneNode(true);
            other.querySelector('.TicketType').textContent = '誠品會員 -- X:';
            other.querySelector('select').setAttribute('identity', 'other-channel');
            el.after(other);
        }""")
        with self.assertRaisesRegex(ValueError, '找不到唯一'):
            self.flow.tick(self.page)
        self.assertEqual(self.page.evaluate('changes'), [])
        self.assertEqual(self.page.evaluate('posts'), [])


if __name__ == '__main__':
    unittest.main()
