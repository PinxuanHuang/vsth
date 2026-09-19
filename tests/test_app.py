import tempfile
import threading
import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright

from automation import Cancelled, apply_settings, target_url
from config import Settings, load_settings, save_settings


class ConfigTests(unittest.TestCase):
    def test_roundtrip_and_invalid_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            settings = Settings(url="https://example.com/ticket", cinema="台北影城", tickets=4, agree=True)
            save_settings(settings, path)
            self.assertEqual(load_settings(path), settings)
            for bad in ("javascript:alert(1)", "file:///C:/test", "https://", "https://user:pass@example.com"):
                settings.url = bad
                with self.assertRaises(ValueError):
                    save_settings(settings, path)
            self.assertEqual(load_settings(path).url, "https://example.com/ticket")

    def test_ticket_limits(self):
        for count in (0, 21, -1, True, 2.5):
            with self.assertRaises(ValueError):
                Settings(url="demo://ticket", cinema="台北示範影城", tickets=count).validate()


class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(channel="msedge", headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.settings = Settings(url="demo://ticket", cinema="高雄示範影城", tickets=4, agree=True)
        self.page.goto(target_url(self.settings))
        self.messages = []

    def tearDown(self):
        self.page.close()

    def test_demo_and_repeat_are_idempotent(self):
        for _ in range(2):
            apply_settings(self.page, self.settings, self.messages.append)
        self.assertEqual(self.page.locator("#cinema").input_value(), "高雄示範影城")
        self.assertEqual(self.page.locator("#tickets").input_value(), "4")
        self.assertTrue(self.page.locator("#agree").is_checked())

    def test_explicit_selectors_and_no_consent(self):
        self.settings.agree = False
        self.settings.cinema_selector = "#cinema"
        self.settings.tickets_selector = "#tickets"
        self.page.locator("#agree").evaluate("el => el.remove()")
        apply_settings(self.page, self.settings, self.messages.append)
        self.assertEqual(self.page.locator("#tickets").input_value(), "4")

    def test_ambiguous_field_fails_without_changing_fields(self):
        self.page.evaluate("document.querySelector('main').insertAdjacentHTML('beforeend', '<input aria-label=\"影城\">')")
        with self.assertRaisesRegex(ValueError, "多個欄位"):
            apply_settings(self.page, self.settings, self.messages.append)
        self.assertEqual(self.page.locator("#cinema").input_value(), "")
        self.assertFalse(self.page.locator("#agree").is_checked())

    def test_cancellation_prevents_actions(self):
        stop = threading.Event()
        stop.set()
        with self.assertRaises(Cancelled):
            apply_settings(self.page, self.settings, self.messages.append, stop)
        self.assertEqual(self.page.locator("#cinema").input_value(), "")

    def test_missing_option_does_not_continue(self):
        self.settings.cinema = "不存在的影城"
        with self.assertRaises(Exception):
            apply_settings(self.page, self.settings, self.messages.append)
        self.assertEqual(self.page.locator("#tickets").input_value(), "1")
        self.assertFalse(self.page.locator("#agree").is_checked())


if __name__ == "__main__":
    unittest.main()
