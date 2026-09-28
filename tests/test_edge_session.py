import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import sync_playwright

from edge_session import desktop_edge


class DesktopEdgeTests(unittest.TestCase):
    def test_attach_existing_context_persist_profile_and_close_only_owned_browser(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent / 'test-results') as tmp:
            profile = Path(tmp) / 'edge-profile'
            with sync_playwright() as pw, pw.chromium.launch(channel='msedge', headless=True) as unrelated:
                with desktop_edge(pw, 'about:blank', profile, threading.Event()) as browser:
                    context = browser.contexts[0]
                    self.assertGreaterEqual(len(context.pages), 1)
                    context.add_cookies([{'name': 'test_profile', 'value': 'kept', 'domain': 'example.invalid',
                                         'path': '/', 'expires': 2147483647}])
                    self.assertTrue(browser.is_connected())
                self.assertFalse(browser.is_connected())
                self.assertTrue(unrelated.is_connected())
                with desktop_edge(pw, 'about:blank', profile, threading.Event()) as next_browser:
                    cookies = next_browser.contexts[0].cookies('https://example.invalid')
                    self.assertTrue(any(item['name'] == 'test_profile' for item in cookies))

    def test_cancelled_start_does_not_launch_process(self):
        stop = threading.Event()
        stop.set()
        with tempfile.TemporaryDirectory() as tmp, patch('edge_session.subprocess.Popen') as launch:
            with desktop_edge(None, 'about:blank', Path(tmp), stop) as browser:
                self.assertIsNone(browser)
            launch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
