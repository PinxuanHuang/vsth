import queue
import unittest
from unittest.mock import patch

from automation import BrowserWorker, target_url
from config import BOOKING_SITES, Settings


ESLITE_URL = "https://arthouse.eslite.com/member/Login.aspx?RedirectUrl=%2fvisMbrBookings.aspx"
VIESHOW_URL = "https://www.vscinemas.com.tw/hold"


class BrowserLaunchTests(unittest.TestCase):
    def test_vieshow_uses_canonical_entry(self):
        self.assertEqual(Settings().url, VIESHOW_URL)
        self.assertEqual(target_url(Settings()), VIESHOW_URL)
        self.assertEqual(list(BOOKING_SITES.values()), [VIESHOW_URL, ESLITE_URL])

    def test_eslite_without_movie_still_monitors_for_manual_movie_selection(self):
        events = queue.Queue()
        worker = BrowserWorker(Settings(url=ESLITE_URL), events)
        with patch("automation.os.startfile") as open_url, patch.object(worker, 'run_eslite') as run:
            worker.run()
        run.assert_called_once()
        open_url.assert_not_called()

    def test_edge_open_failure_is_reported_and_worker_finishes(self):
        events = queue.Queue()
        worker = BrowserWorker(Settings(url=ESLITE_URL), events)
        with patch.object(worker, 'run_eslite', side_effect=OSError("test launch failure")):
            worker.run()
        messages = list(events.queue)
        self.assertEqual(messages[-1][0], "done")
        self.assertTrue(any(kind == "log" and "test launch failure" in text for kind, text in messages))
        self.assertNotIn("Microsoft Edge", messages[-1][1])

    def test_cancelled_worker_does_not_open_browser(self):
        worker = BrowserWorker(Settings(url=ESLITE_URL), queue.Queue())
        worker.stop_event.set()
        with patch("automation.os.startfile") as open_url, patch("automation.sync_playwright") as playwright:
            worker.run()
        open_url.assert_not_called()
        playwright.assert_not_called()


if __name__ == "__main__":
    unittest.main()
