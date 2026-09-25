import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs

from playwright.sync_api import Locator, sync_playwright

from automation import Cancelled, CheckoutLoginError, TicketFlow, login_for_checkout, resource
from config import Settings, load_settings, save_settings

URL = 'https://sales.vscinemas.com.tw/LiveTicketD4/Home/OrderConfirm'
PAYMENT = '<h1>Payment</h1><button id="payment" onclick="window.paymentClicks=1">Pay</button>'


class CredentialTests(unittest.TestCase):
    def test_encrypted_roundtrip_and_no_plaintext(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            settings = Settings(url='demo://ticket', cinema='test', login_email='test@example.com', login_password=' test-password ')
            save_settings(settings, path)
            content = path.read_text(encoding='utf-8')
            self.assertNotIn(settings.login_password, content)
            self.assertNotIn('login_password', json.loads(content))
            self.assertTrue(json.loads(content)['protected_login_password'])
            self.assertEqual(load_settings(path), settings)
            self.assertNotIn(settings.login_password, repr(settings))
            self.assertNotIn(settings.login_email, repr(settings))
            settings.login_password = ''
            save_settings(settings, path)
            self.assertEqual(load_settings(path).login_password, '')

    def test_legacy_settings_and_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            path.write_text(json.dumps({'url': 'demo://ticket', 'cinema': 'test'}))
            self.assertEqual(load_settings(path).login_email, '')
            self.assertEqual(load_settings(path).login_password, '')
        for value in ('not-an-email', 'a b@example.com', 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Settings(url='demo://ticket', cinema='test', login_email=value).validate()

    def test_encryption_failure_preserves_existing_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            settings = Settings(url='demo://ticket', cinema='test')
            save_settings(settings, path)
            original = path.read_bytes()
            settings.login_password = 'test-password'
            with patch('config.protect_password', side_effect=ValueError('Encryption failed')):
                with self.assertRaises(ValueError):
                    save_settings(settings, path)
            self.assertEqual(path.read_bytes(), original)


class CheckoutLoginTests(unittest.TestCase):
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
        self.page = self.context.new_page()
        self.posts = []
        self.fixture = resource('checkout_login_fixture.html').read_text(encoding='utf-8')
        self.failure = False
        def serve(route):
            if route.request.method == 'POST':
                self.posts.append((route.request.url, parse_qs(route.request.post_data)))
                body = self.fixture.replace('>*</span>', '>Wrong credentials</span>') if self.failure else PAYMENT
            else:
                body = self.fixture
            route.fulfill(body=body, content_type='text/html')
        self.context.route('**/*', serve)
        self.page.goto(URL)
        self.settings = Settings(login_email='checkout-test@example.com', login_password=' test-only-pass ')
        self.stop = threading.Event()
        self.logs = []

    def tearDown(self):
        self.context.close()

    def run_login(self):
        return login_for_checkout(self.page, self.settings, self.logs.append, self.stop, timeout=.7, discovery_timeout=.2)

    def test_submit_scoped_login_preserves_hidden_fields_and_password_spaces(self):
        self.assertEqual(self.run_login(), 'submitted')
        self.assertEqual(len(self.posts), 1)
        url, data = self.posts[0]
        self.assertTrue(url.endswith('/Home/VieShowLoginForCheckout'))
        self.assertEqual(data['UserName'], [self.settings.login_email])
        self.assertEqual(data['Password'], [self.settings.login_password])
        self.assertEqual(data['__RequestVerificationToken'], ['local-fixture-token'])
        self.assertIsNone(self.page.evaluate('window.paymentClicks'))
        self.assertNotIn(self.settings.login_email, '\n'.join(self.logs))
        self.assertNotIn(self.settings.login_password, '\n'.join(self.logs))

    def test_version_path_query_and_ids_are_not_hardcoded(self):
        for prefix in ('LiveTicketD8', 'VieShowTicketD4', 'AnotherTicket'):
            with self.subTest(prefix=prefix):
                self.page.goto(URL)
                self.page.locator('form').last.evaluate("(el, prefix) => {el.action='/'+prefix+'/Home/VieShowLoginForCheckout?step=member';el.querySelectorAll('[id]').forEach(n=>n.removeAttribute('id'))}", prefix)
                self.assertEqual(self.run_login(), 'submitted')
                self.assertIn('/' + prefix + '/', self.posts[-1][0])

    def test_already_logged_in_does_not_touch_header_or_pay(self):
        self.page.locator('form').last.evaluate('el => el.remove()')
        self.assertEqual(self.run_login(), 'not_required')
        self.assertFalse(self.posts)
        self.assertEqual(self.page.locator('#headerLogin input[type=email]').input_value(), '')
        self.assertEqual(self.page.locator('#headerLogin input[type=password]').input_value(), '')
        self.assertIsNone(self.page.evaluate('window.paymentClicks'))

    def test_missing_credentials_hands_off_without_partial_fill(self):
        self.settings.login_password = ''
        self.assertEqual(self.run_login(), 'missing_credentials')
        self.assertFalse(self.posts)
        self.assertEqual(self.page.locator('#inputUserName').input_value(), '')

    def test_failed_login_submits_only_once(self):
        self.failure = True
        with self.assertRaisesRegex(CheckoutLoginError, '登入驗證錯誤'):
            self.run_login()
        self.assertEqual(len(self.posts), 1)

    def test_stalled_login_does_not_retry(self):
        self.page.locator('form').last.evaluate('el => el.onsubmit=event=>event.preventDefault()')
        with self.assertRaisesRegex(CheckoutLoginError, '不會自動重試'):
            self.run_login()
        self.assertFalse(self.posts)

    def test_cancel_prevents_fill(self):
        self.stop.set()
        with self.assertRaises(Cancelled):
            self.run_login()
        self.assertFalse(self.posts)
        self.assertEqual(self.page.locator('#inputUserName').input_value(), '')

    def test_rejects_external_action_get_and_untrusted_origin(self):
        for action, method in (('https://example.com/Home/VieShowLoginForCheckout', 'post'),
                               ('/LiveTicketD4/Home/VieShowLoginForCheckout', 'get')):
            self.page.goto(URL)
            self.page.locator('form').last.evaluate('(el, v)=>{el.action=v[0];el.method=v[1]}', [action, method])
            with self.assertRaises(CheckoutLoginError):
                self.run_login()
            self.assertEqual(self.page.locator('#inputUserName').input_value(), '')
        self.page.goto('https://example.com/OrderConfirm')
        with self.assertRaises(CheckoutLoginError):
            self.run_login()
        self.assertFalse(self.posts)

    def test_ambiguous_form_or_fields_are_rejected(self):
        for selector in ('form:last-of-type', '#inputUserName', '#inputPassword', '#btnCheckoutLogin'):
            self.page.goto(URL)
            self.page.locator(selector).evaluate('el => el.after(el.cloneNode(true))')
            with self.assertRaises(CheckoutLoginError):
                self.run_login()
        self.assertFalse(self.posts)

    def test_hidden_form_does_not_trigger_login(self):
        self.page.locator('form').last.evaluate('el => el.hidden=true')
        self.assertEqual(self.run_login(), 'not_required')
        self.assertFalse(self.posts)

    def test_delayed_form_is_detected(self):
        self.page.locator('form').last.evaluate('el => {el.hidden=true;setTimeout(()=>el.hidden=false,100)}')
        self.assertEqual(self.run_login(), 'submitted')

    def test_length_limit_does_not_truncate_or_submit(self):
        self.settings.login_password = 'x' * 21
        with self.assertRaisesRegex(CheckoutLoginError, '長度限制'):
            self.run_login()
        self.assertFalse(self.posts)
        self.assertEqual(self.page.locator('#inputUserName').input_value(), '')

    def test_submit_override_is_rejected(self):
        self.page.locator('#btnCheckoutLogin').evaluate("el => el.setAttribute('formaction','https://example.com/receive')")
        with self.assertRaises(CheckoutLoginError):
            self.run_login()
        self.assertFalse(self.posts)

    def test_action_mutation_during_fill_is_rejected(self):
        self.page.locator('#inputPassword').evaluate("el => el.oninput=()=>el.form.action='/Home/AnotherEndpoint'")
        with self.assertRaisesRegex(CheckoutLoginError, '表單已改變'):
            self.run_login()
        self.assertFalse(self.posts)

    def test_playwright_error_does_not_expose_credentials(self):
        with patch.object(Locator, 'fill', side_effect=RuntimeError(self.settings.login_password)):
            with self.assertRaises(CheckoutLoginError) as caught:
                self.run_login()
        self.assertNotIn(self.settings.login_password, str(caught.exception))

    def test_full_flow_from_quantity_through_seats_and_login(self):
        def serve_flow(route):
            path = route.request.url
            if route.request.method == 'POST':
                self.posts.append((path, parse_qs(route.request.post_data)))
                body = PAYMENT
            elif '/Seats' in path:
                body = resource('seats_fixture.html').read_text(encoding='utf-8')
                body += '<script>document.querySelector("#btnCheckOut").onclick=()=>location.href="/LiveTicketD4/Home/OrderConfirm";</script>'
            elif '/Home/OrderConfirm' in path:
                body = self.fixture
            else:
                body = resource('quantity_fixture.html').read_text(encoding='utf-8')
            route.fulfill(body=body, content_type='text/html')
        self.context.unroute('**/*')
        self.context.route('**/*', serve_flow)
        self.page.goto('https://sales.vscinemas.com.tw/LiveTicketD4/')
        self.settings.seat_mode = 'middle'
        flow = TicketFlow(self.settings, lambda *event: self.logs.append(event), self.stop)
        flow.pending, flow.post_pending, flow.booking_page = False, True, self.page
        flow.tick(self.context.pages)
        self.assertEqual(len(self.posts), 1)
        self.assertTrue(any(event[0] == 'handoff' for event in self.logs))
        flow.tick(self.context.pages)
        self.assertEqual(len(self.posts), 1)
        self.assertIsNone(self.page.evaluate('window.paymentClicks'))
        self.assertFalse(self.page.is_closed())


if __name__ == '__main__':
    unittest.main()
