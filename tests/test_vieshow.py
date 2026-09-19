import threading
import unittest
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright

from automation import Cancelled, TicketFlow, is_ticket_page, resource, select_cinema_and_first_movie, ticket_page
from config import CINEMAS, Settings

URL = "https://www.vscinemas.com.tw/vsTicketingSP/ticketing/ticket.aspx"


class VieshowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(channel="msedge", headless=True)
        cls.fixture = resource("flow_fixture.html").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.context = self.browser.new_context()
        self.context.route("https://www.vscinemas.com.tw/**", lambda route: route.fulfill(body=self.fixture, content_type="text/html"))
        self.page = self.context.new_page()
        self.settings = Settings(cinema="新竹大遠百威秀影城", tickets=4, agree=True)
        self.events = []
        self.stop = threading.Event()
        self.flow = TicketFlow(self.settings, lambda *event: self.events.append(event), self.stop)

    def tearDown(self):
        self.context.close()

    def test_all_cinema_values_navigate_to_first_movie_only(self):
        for name, value in CINEMAS.items():
            with self.subTest(cinema=name):
                self.page.goto(URL)
                self.settings.cinema = name
                select_cinema_and_first_movie(self.page, self.settings, lambda _: None)
                query = parse_qs(urlsplit(self.page.url).query)
                self.assertEqual(query, {"cinema": [value], "movie": ["FIRST"]})
                self.assertEqual(self.page.locator("#theater").input_value(), value)
                self.assertEqual(self.page.locator("#tickets").input_value(), "1")
                self.assertFalse(self.page.locator("#agree").is_checked())

    def test_wait_for_manual_navigation_then_run_once(self):
        self.page.goto("https://www.vscinemas.com.tw/")
        self.flow.tick(self.context.pages)
        self.assertTrue(self.flow.pending)
        self.assertEqual(self.page.locator("#theater").input_value(), "")
        self.page.goto(URL)
        self.flow.tick(self.context.pages)
        self.assertIn("movie=FIRST", self.page.url)
        self.assertFalse(self.flow.pending)
        self.page.goto(URL + "?cinema=2%7CHS&movie=SECOND")
        self.flow.tick(self.context.pages)
        self.assertIn("movie=SECOND", self.page.url)
        self.flow.arm()
        self.flow.tick(self.context.pages)
        self.assertIn("movie=FIRST", self.page.url)

    def test_new_tab_and_unrelated_latest_tab(self):
        self.page.goto("https://www.vscinemas.com.tw/")
        popup = self.context.new_page()
        popup.goto(URL)
        unrelated = self.context.new_page()
        self.assertIs(ticket_page(self.context.pages), popup)
        self.flow.tick(self.context.pages)
        self.assertIn("movie=FIRST", popup.url)
        self.assertEqual(unrelated.url, "about:blank")

    def test_empty_movie_list_pauses_without_looping(self):
        self.page.goto(URL + "?cinema=2%7CHS")
        self.page.locator(".movieList").evaluate("el => el.replaceChildren()")
        with self.assertRaisesRegex(ValueError, "第一部電影"):
            select_cinema_and_first_movie(self.page, self.settings, lambda _: None, timeout=0.3)
        self.assertNotIn("movie=", self.page.url)

    def test_cancellation_before_selection(self):
        self.page.goto(URL)
        self.stop.set()
        with self.assertRaises(Cancelled):
            select_cinema_and_first_movie(self.page, self.settings, lambda _: None, self.stop)
        self.assertEqual(self.page.locator("#theater").input_value(), "")

    def test_stale_other_cinema_link_rejected(self):
        self.page.goto(URL + "?cinema=2%7CHS")
        self.page.locator(".movieList a").first.evaluate("el => el.href='?cinema=6%7CKS&movie=WRONG'")
        self.flow.tick(self.context.pages)
        self.assertFalse(self.flow.pending)
        self.assertNotIn("movie=", self.page.url)
        self.assertTrue(any("影城不符" in message for _, message in self.events))

    def test_only_exact_ticket_host_and_path(self):
        self.assertTrue(is_ticket_page(URL + "?cinema=2%7CHS"))
        for url in ("https://example.com/vsTicketingSP/ticketing/ticket.aspx", URL + "/other", "https://www.vscinemas.com.tw.evil.example/vsTicketingSP/ticketing/ticket.aspx"):
            self.assertFalse(is_ticket_page(url))

    def test_each_area_runs_after_manual_entry_and_preserves_path(self):
        for area in ("vsTicketingSP", "vsTicketingSP2", "vsTicketingSP3", "vsTicketingSP12"):
            with self.subTest(area=area):
                self.page.goto("https://www.vscinemas.com.tw/")
                self.flow.arm()
                self.flow.tick(self.context.pages)
                self.assertTrue(self.flow.pending)
                base = f"https://www.vscinemas.com.tw/{area}/ticketing/ticket.aspx"
                self.page.goto(base)
                self.flow.tick(self.context.pages)
                self.assertEqual(urlsplit(self.page.url).path, urlsplit(base).path)
                self.assertEqual(parse_qs(urlsplit(self.page.url).query), {"cinema": ["2|HS"], "movie": ["FIRST"]})
                self.assertTrue(any("本階段完成" in msg for _, msg in self.events))

    def test_movie_link_cannot_switch_areas(self):
        self.page.goto(URL.replace("vsTicketingSP", "vsTicketingSP2") + "?cinema=2%7CHS")
        self.page.locator(".movieList a").first.evaluate("el => el.href='/vsTicketingSP/ticketing/ticket.aspx?cinema=2%7CHS&movie=FIRST'")
        self.flow.tick(self.context.pages)
        self.assertNotIn("movie=", self.page.url)
        self.assertTrue(any("不同搶票專區" in msg for _, msg in self.events))

    def test_unavailable_cinema_has_clear_error(self):
        self.page.goto(URL.replace("vsTicketingSP", "vsTicketingSP3"))
        self.page.locator('option[value="2|HS"]').evaluate("el => el.remove()")
        self.flow.tick(self.context.pages)
        self.assertTrue(any("未提供" in msg and "可選影城" in msg for _, msg in self.events))
        self.assertEqual(self.page.locator("#theater").input_value(), "")

    def test_general_ticket_entry_does_not_trigger_special_flow(self):
        for path in ("vsTicketing", "vsTicketingSPevil", "vsTicketingSP0"):
            self.assertFalse(is_ticket_page(f"https://www.vscinemas.com.tw/{path}/ticketing/ticket.aspx"))


if __name__ == "__main__":
    unittest.main()
