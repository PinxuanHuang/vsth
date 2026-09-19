import tempfile
import threading
import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright
from automation import Cancelled, TicketFlow, find_showtime_url, resource, select_showtime
from config import Settings, load_settings, parse_showtime, save_settings

URL = "https://www.vscinemas.com.tw/vsTicketingSP3/ticketing/ticket.aspx?cinema=21%7CMU&movie=FIRST"


class ShowtimeConfigTests(unittest.TestCase):
    def test_valid_invalid_dates_and_persistence(self):
        self.assertEqual(parse_showtime("2028-02-29 00:05").day, 29)
        for stamp in ("2026-02-29 19:20", "2026-09-24 24:00", "2026-09-24 19:60", "", "2026-9-24 19:20"):
            with self.assertRaises(ValueError):
                parse_showtime(stamp)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            settings = Settings(url=URL, cinema="MUVIE CINEMAS 台北松仁", showtime="2026-09-25 19:20")
            save_settings(settings, path)
            self.assertEqual(load_settings(path), settings)
            path.write_text('{"url":"demo://ticket","cinema":"MUVIE CINEMAS 台北松仁"}', encoding="utf-8")
            self.assertEqual(load_settings(path).showtime, "")

    def test_ambiguous_disabled_and_wrong_links(self):
        settings = Settings(cinema="MUVIE CINEMAS 台北松仁", showtime="2026-09-25 19:20")
        row = {"date": "2026 年 09 月 25 日 星期五", "time": "19:20", "href": "booking.aspx?cinemacode=21&txtSessionId=165195"}
        for rows in ([row, row], [dict(row, disabled=True)], [dict(row, href="/vsTicketingSP/ticketing/booking.aspx?cinemacode=21&txtSessionId=165195")], [dict(row, href="booking.aspx?cinemacode=14&txtSessionId=165195")], [dict(row, href="https://sales.vscinemas.com.tw/SessionSeats.aspx")]):
            with self.assertRaises(ValueError):
                find_showtime_url(URL, rows, settings)


class ShowtimeBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(channel="msedge", headless=True)
        cls.sessions = resource("session_fixture.html").read_text(encoding="utf-8")
        cls.movie = resource("flow_fixture.html").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.context = self.browser.new_context()
        self.requests = []
        def route_response(route):
            url = route.request.url
            self.requests.append(url)
            body = "<h1>Booking page</h1>" if "booking.aspx" in url else self.movie + (self.sessions if "movie=" in url else "")
            route.fulfill(body=body, content_type="text/html")
        self.context.route("**/*", route_response)
        self.page = self.context.new_page()
        self.settings = Settings(cinema="MUVIE CINEMAS 台北松仁", showtime="2026-09-25 19:20")

    def tearDown(self):
        self.context.close()

    def test_entire_flow_all_areas_exact_date_time_and_session(self):
        for area in ("vsTicketingSP", "vsTicketingSP2", "vsTicketingSP3"):
            with self.subTest(area=area):
                self.page.goto(f"https://www.vscinemas.com.tw/{area}/ticketing/ticket.aspx")
                flow = TicketFlow(self.settings, lambda *_: None, threading.Event())
                flow.tick(self.context.pages)
                self.assertEqual(self.page.url, f"https://www.vscinemas.com.tw/{area}/ticketing/booking.aspx?cinemacode=21&txtSessionId=165195")
                self.assertEqual(self.page.locator("h1").inner_text(), "Booking page")
                before = len(self.requests)
                flow.tick(self.context.pages)
                self.assertEqual(len(self.requests), before)
        self.assertFalse(any("SessionSeats" in url for url in self.requests))

    def test_missing_date_or_time_never_navigates(self):
        for stamp in ("2026-09-25 15:41", "2026-09-29 19:20"):
            self.page.goto(URL)
            self.settings.showtime = stamp
            with self.assertRaisesRegex(ValueError, "找不到指定場次"):
                select_showtime(self.page, self.settings, lambda _: None)
            self.assertNotIn("booking.aspx", self.page.url)

    def test_cancellation_before_booking(self):
        self.page.goto(URL)
        stop = threading.Event()
        stop.set()
        with self.assertRaises(Cancelled):
            select_showtime(self.page, self.settings, lambda _: None, stop)
        self.assertNotIn("booking.aspx", self.page.url)
