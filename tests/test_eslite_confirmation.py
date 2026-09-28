import threading
import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright

from config import Settings
from eslite_confirmation import EsliteConfirmationFlow


SESSION = 'https://arthouse.eslite.com/visSelectTickets.aspx?cinemacode=dynamic&txtSessionId=opaque'
CONFIRMATION = 'https://arthouse.eslite.com/dynamic-confirmation.aspx'


def confirmation_fixture():
    return Path(__file__).with_name('eslite_confirmation_fixture.html').read_text(encoding='utf-8')


class EsliteConfirmationTests(unittest.TestCase):
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
        self.page.route('**/*', lambda route: route.fulfill(body=confirmation_fixture(), content_type='text/html; charset=utf-8'))
        self.page.goto(CONFIRMATION)
        self.settings = Settings(agree=True)
        self.stop = threading.Event()
        self.messages = []
        self.flow = EsliteConfirmationFlow(self.settings, lambda *args: self.messages.append(args), self.stop, SESSION)

    def tearDown(self):
        self.assertEqual(self.page.evaluate('window.actions || []'), [])
        self.page.close()

    def test_dynamic_ids_and_names_select_only_terms_once(self):
        self.page.locator('[id], [name]').evaluate_all("els => els.forEach((el,i) => {el.id='dynamic-'+i; el.name='opaque-'+i})")
        self.assertTrue(self.flow.tick(self.page))
        self.assertTrue(self.page.locator('.confirmationcheck input[type=checkbox]').is_checked())
        self.assertFalse(self.page.locator('label input').is_checked())
        self.assertTrue(self.flow.tick(self.page))
        self.assertEqual(len(self.page.evaluate('changes')), 1)
        logs = str(self.messages)
        for personal_value in ('sample@example.com', '測試使用者', '測試手機'):
            self.assertNotIn(personal_value, logs)

    def test_agree_off_preserves_checked_and_unchecked_state(self):
        self.settings.agree = False
        self.assertTrue(self.flow.tick(self.page))
        self.assertFalse(self.page.locator('#chkTerms').is_checked())
        self.page.locator('#chkTerms').check()
        self.flow.done = False
        self.assertTrue(self.flow.tick(self.page))
        self.assertTrue(self.page.locator('#chkTerms').is_checked())
        self.assertEqual(self.page.evaluate('changes'), ['chkTerms'])

    def test_prechecked_terms_are_not_toggled(self):
        self.page.locator('#chkTerms').check()
        self.assertTrue(self.flow.tick(self.page))
        self.assertEqual(self.page.evaluate('changes'), ['chkTerms'])

    def test_unrelated_checkbox_and_terms_outside_confirmation_untouched(self):
        self.page.locator('.confirmationcheck').evaluate("el => el.className = 'other'")
        self.assertFalse(self.flow.tick(self.page))
        self.assertEqual(self.page.evaluate('changes'), [])
        self.flow.deadline = 0
        with self.assertRaisesRegex(ValueError, '尚未確認'):
            self.flow.tick(self.page)

    def test_hidden_or_disabled_checkbox_waits_until_available(self):
        box = self.page.locator('#chkTerms')
        box.evaluate("el => el.style.visibility = 'hidden'")
        self.assertFalse(self.flow.tick(self.page))
        box.evaluate("el => {el.style.visibility = 'visible'; el.disabled=true}")
        self.assertFalse(self.flow.tick(self.page))
        box.evaluate('el => el.disabled=false')
        self.assertTrue(self.flow.tick(self.page))

    def test_duplicate_scopes_labels_and_checkboxes_fail_closed(self):
        for selector in ('.confirmationcheck', '.TermsAndConditions', '#chkTerms'):
            with self.subTest(selector=selector):
                self.page.goto(CONFIRMATION)
                self.page.locator(selector).evaluate('el => el.after(el.cloneNode(true))')
                with self.assertRaises(ValueError):
                    self.flow.tick(self.page)
                self.assertEqual(self.page.evaluate('changes'), [])

    def test_wrong_origin_or_session_and_stop_do_not_check(self):
        self.stop.set()
        self.assertFalse(self.flow.tick(self.page))
        self.stop.clear()
        for url in (CONFIRMATION.replace('arthouse.eslite.com', 'example.com'),
                    CONFIRMATION + '?txtSessionId=wrong', CONFIRMATION + '?cinemacode=wrong'):
            with self.subTest(url=url):
                self.page.goto(url)
                with self.assertRaises(ValueError):
                    self.flow.tick(self.page)
                self.assertEqual(self.page.evaluate('changes'), [])


if __name__ == '__main__':
    unittest.main()
