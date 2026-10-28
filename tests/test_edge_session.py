import tempfile
import threading
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import URLError

from playwright.sync_api import sync_playwright

from edge_session import desktop_edge


class DesktopEdgeReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.profile = Path(self.tmp) / 'profile'
        self.pw = Mock()
        self.browser = self.pw.chromium.connect_over_cdp.return_value
        self.browser.is_connected.return_value = True
        self.process = Mock()
        self.process.poll.return_value = 0
        self.enterContext(patch('edge_session.edge_executable', return_value='test-edge.exe'))
        self.launch = self.enterContext(patch('edge_session.subprocess.Popen', return_value=self.process))
        self.opener = self.enterContext(patch('edge_session.build_opener')).return_value
        self.stop = threading.Event()

    def response(self):
        return BytesIO(b'{"webSocketDebuggerUrl":"ws://127.0.0.1/devtools/browser/test"}')

    def test_exited_launcher_with_live_browser_still_attaches(self):
        self.opener.open.return_value = self.response()
        with desktop_edge(self.pw, 'about:blank', self.profile, self.stop) as browser:
            self.assertIs(browser, self.browser)
            self.pw.chromium.connect_over_cdp.assert_called_once()
            endpoint = self.pw.chromium.connect_over_cdp.call_args.args[0]
            self.assertIn('--remote-debugging-port=' + endpoint.rsplit(':', 1)[1], self.launch.call_args.args[0])
            self.assertTrue(self.pw.chromium.connect_over_cdp.call_args.kwargs['no_defaults'])
        self.browser.new_browser_cdp_session.return_value.send.assert_called_once_with('Browser.close')
        self.process.terminate.assert_not_called()

    def test_exited_launcher_waits_for_delayed_endpoint(self):
        self.opener.open.side_effect = [URLError('not ready'), self.response()]
        with desktop_edge(self.pw, 'about:blank', self.profile, self.stop) as browser:
            self.assertIs(browser, self.browser)
        self.assertEqual(self.opener.open.call_count, 2)

    def test_no_endpoint_reports_conflict_only_after_timeout(self):
        self.opener.open.side_effect = URLError('not ready')
        with patch('edge_session.time.monotonic', side_effect=[0, 31]):
            with self.assertRaisesRegex(RuntimeError, '30 秒內仍未連上'):
                with desktop_edge(self.pw, 'about:blank', self.profile, self.stop):
                    self.fail('Unconnected browser must not be yielded')
        self.pw.chromium.connect_over_cdp.assert_not_called()
        self.process.terminate.assert_not_called()

    def test_running_launcher_without_endpoint_does_not_count_as_ready(self):
        self.process.poll.return_value = None
        self.opener.open.side_effect = URLError('not ready')
        with patch('edge_session.time.monotonic', side_effect=[0, 31]):
            with self.assertRaisesRegex(RuntimeError, '自動流程尚未啟動'):
                with desktop_edge(self.pw, 'about:blank', self.profile, self.stop):
                    self.fail('Unconnected browser must not be yielded')
        self.pw.chromium.connect_over_cdp.assert_not_called()

    def test_cancel_during_startup_does_not_connect(self):
        self.opener.open.side_effect = URLError('not ready')
        with patch.object(self.stop, 'wait', side_effect=lambda _: self.stop.set()):
            with desktop_edge(self.pw, 'about:blank', self.profile, self.stop) as browser:
                self.assertIsNone(browser)
        self.pw.chromium.connect_over_cdp.assert_not_called()


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
