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
        self.assertEqual(parse_showtime("2028-02-29").day, 29)
        for stamp in ("2026-02-29", "2026-09-24 24:00", "2026-09-24 19:60", "", "2026-9-24 19:20"):
            with self.assertRaises(ValueError):
                parse_showtime(stamp)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            settings = Settings(url=URL, cinema="MUVIE CINEMAS 台北松仁", showtime="2026-09-25", session_position="last")
            save_settings(settings, path)
            self.assertEqual(load_settings(path), settings)
            path.write_text('{"url":"demo://ticket","cinema":"MUVIE CINEMAS 台北松仁"}', encoding="utf-8")
            self.assertEqual(load_settings(path).showtime, "")

    def test_ambiguous_disabled_and_wrong_links(self):
        settings = Settings(cinema="MUVIE CINEMAS 台北松仁", showtime="2026-09-25", session_position="last")
        row = {"date": "2026 年 09 月 25 日 星期五", "time": "19:20", "href": "booking.aspx?cinemacode=21&txtSessionId=165195"}
        for rows in ([dict(row, disabled=True)], [dict(row, href="/vsTicketingSP/ticketing/booking.aspx?cinemacode=21&txtSessionId=165195")], [dict(row, href="booking.aspx?cinemacode=14&txtSessionId=165195")], [dict(row, href="https://sales.vscinemas.com.tw/SessionSeats.aspx")]):
            with self.assertRaises(ValueError):
                find_showtime_url(URL, [{"date": row["date"], "entries": rows}], settings)


    def test_legacy_date_migration_and_position_validation(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps(dict(url=URL, cinema="MUVIE CINEMAS 台北松仁",
                                            showtime="2026-09-25 19:20")), encoding="utf-8")
            loaded = load_settings(path)
            self.assertEqual(loaded.showtime, "2026-09-25")
            self.assertEqual(loaded.session_position, "first")
        for position in ("", "middle", None):
            with self.assertRaises(ValueError):
                Settings(url=URL, cinema="MUVIE CINEMAS 台北松仁", session_position=position).validate()


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
        self.settings = Settings(cinema="MUVIE CINEMAS 台北松仁", showtime="2026-09-25", session_position="last")

    def tearDown(self):
        self.context.close()

    def test_entire_flow_all_areas_date_and_last_session(self):
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

    def test_missing_date_never_navigates(self):
        for stamp in ("2026-09-29", "2027-09-25"):
            self.page.goto(URL)
            self.settings.showtime = stamp
            with self.assertRaisesRegex(ValueError, "找不到指定場次"):
                select_showtime(self.page, self.settings, lambda _: None)
            self.assertNotIn("booking.aspx", self.page.url)


    def test_first_and_last_follow_li_order_without_time_parsing(self):
        for position, session_id in (("first", "165194"), ("last", "165195")):
            self.page.goto(URL)
            # Time text is deliberately unsuitable for sorting or exact-time matching.
            self.page.locator(".movieDay").nth(1).locator("li > a").evaluate_all(
                "els => { els[0].textContent = '23:50'; els[1].textContent = '24:30'; }")
            self.settings.session_position = position
            select_showtime(self.page, self.settings, lambda _: None)
            self.assertTrue(self.page.url.endswith("txtSessionId=" + session_id))
        self.assertFalse(any("SessionSeats" in url for url in self.requests))

    def test_unavailable_endpoint_never_falls_back(self):
        for position in ("first", "last"):
            for mutation in ("li.classList.add('soldout')", "li.querySelector('a').remove()"):
                self.page.goto(URL)
                entries = self.page.locator(".movieDay").nth(1).locator("ul.bookList > li")
                entries.nth(0 if position == "first" else -1).evaluate("li => {" + mutation + "}")
                self.settings.session_position = position
                with self.assertRaisesRegex(ValueError, "無法訂票"):
                    select_showtime(self.page, self.settings, lambda _: None)
                self.assertNotIn("booking.aspx", self.page.url)

    def test_duplicate_lists_and_empty_date_list_never_navigate(self):
        for script, message in (("day.append(day.querySelector('ul').cloneNode(true))", "多個日期清單"),
                                ("day.querySelector('ul').innerHTML = ''", "無法訂票")):
            self.page.goto(URL)
            self.page.locator(".movieDay").nth(1).evaluate("day => {" + script + "}")
            with self.assertRaisesRegex(ValueError, message):
                select_showtime(self.page, self.settings, lambda _: None)
            self.assertNotIn("booking.aspx", self.page.url)

    def test_cancellation_before_booking(self):
        self.page.goto(URL)
        stop = threading.Event()
        stop.set()
        with self.assertRaises(Cancelled):
            select_showtime(self.page, self.settings, lambda _: None, stop)
        self.assertNotIn("booking.aspx", self.page.url)
