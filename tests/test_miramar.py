import queue
import threading
import unittest
from unittest.mock import patch

from playwright.sync_api import sync_playwright

from automation import BrowserWorker
from config import BOOKING_SITES, Settings
from miramar import MiramarFlow, choose_option
from tests.test_miramar_tickets import fixture as ticket_fixture


def settings(**kwargs):
    return Settings(url=BOOKING_SITES['美麗華影城'], showtime='2031-02-03',
                    eslite_movie='測試電影', **kwargs)


FIXTURE = '''<!doctype html><meta charset="utf-8"><div id="ibooking">
<select id="sel_cinema"><option value="">選擇</option><option value="new-cinema">美麗華影城</option></select>
<select id="sel_movie"></select><select id="sel_show_time"></select>
<select id="sel_show_session"></select><button onclick="search()">搜尋</button></div>
<script>
const cinema = document.querySelector('#sel_cinema'), movie = document.querySelector('#sel_movie'),
date = document.querySelector('#sel_show_time'), session = document.querySelector('#sel_show_session');
const reset = '<option value="" disabled selected>選擇</option>';
cinema.onchange = () => {
 movie.innerHTML = date.innerHTML = session.innerHTML = reset;
 setTimeout(() => movie.innerHTML += '<option value="movie-new">測試電影</option><option value="special">測試電影特別場</option>', 70);
};
movie.onchange = () => {
 date.innerHTML = session.innerHTML = reset;
 setTimeout(() => date.innerHTML += '<option hidden value="2031-02-03T01:00:00">隱藏日期</option><option value="2031-02-03T00:00:00">2月3日 星期一</option>', 70);
};
date.onchange = () => {
 session.innerHTML = reset;
 setTimeout(() => session.innerHTML += '<option disabled value="">任意影廳</option><option disabled value="sold">09:00</option><option style="display:none" value="hidden">10:00</option><option value="session-a">20:50</option><option disabled value="">另一廳</option><option value="session-b">11:10</option>', 70);
};
function search() { location.href='/Booking/TicketType?id='+movie.value+'&session='+session.value; }
</script>'''


class MiramarTests(unittest.TestCase):
    def test_config_validation_and_legacy_load(self):
        settings().validate()
        Settings(url=BOOKING_SITES['美麗華影城']).validate(require_showtime=False)
        for changes in ({'showtime': ''}, {'eslite_movie': ''}, {'eslite_time': '25:00'}, {'miramar_cinema': 'wrong'}):
            value = settings()
            for key, item in changes.items():
                setattr(value, key, item)
            with self.assertRaises(ValueError):
                value.validate()

    def test_ambiguous_movie_and_normalization(self):
        options = [{'value': 'a', 'text': '測試電影'}, {'value': 'b', 'text': '測試電影特別場'}]
        self.assertEqual(choose_option(1, options, settings())['value'], 'a')
        value = settings()
        value.eslite_movie = '電影'
        with self.assertRaises(ValueError):
            choose_option(1, options, value)
        value.eslite_movie = '不存在，測試電影'
        self.assertEqual(choose_option(1, options[:1], value)['value'], 'a')

    def test_dispatch(self):
        worker = BrowserWorker(settings(), queue.Queue())
        with patch.object(worker, 'run_miramar') as run, patch.object(worker, 'run_eslite') as eslite:
            worker.run()
        run.assert_called_once()
        eslite.assert_not_called()

    def test_homepage_flow_offline(self):
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            try:
                for time, position, expected in [('11:10', 'first', 'session-b'), ('12:00', 'first', 'session-a'), ('', 'last', 'session-b')]:
                    with self.subTest(time=time, position=position):
                        context = browser.new_context()
                        requests = []
                        def route_request(route):
                            requests.append(route.request.url)
                            route.fulfill(body=FIXTURE if route.request.url.endswith('/') else ticket_fixture(session=expected), content_type='text/html')
                        context.route('**/*', route_request)
                        page = context.new_page()
                        page.goto('https://www.miramarcinemas.tw/Member/Login')
                        messages = []
                        flow = MiramarFlow(settings(eslite_time=time, session_position=position), lambda *x: messages.append(x), threading.Event())
                        flow.tick([page])
                        self.assertIsNone(flow.page)
                        page.goto('https://www.miramarcinemas.tw/')
                        for _ in range(40):
                            flow.tick([page])
                            page.wait_for_timeout(100)
                            if flow.paused or flow.stage == 6:
                                break
                        self.assertEqual(flow.stage, 6, messages)
                        self.assertIn('session=' + expected, page.url)
                        for _ in range(3):
                            flow.tick([page])
                        self.assertEqual(sum('/Booking/TicketType?' in u for u in requests), 1)
                        context.close()
            finally:
                browser.close()

    def test_cancel(self):
        stop = threading.Event()
        stop.set()
        flow = MiramarFlow(settings(), lambda *x: None, stop)
        flow.tick([])
        self.assertIsNone(flow.page)

    def test_missing_selection_times_out_without_search(self):
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            try:
                page = browser.new_page()
                page.route('**/*', lambda route: route.fulfill(body='<div id="ibooking"></div>', content_type='text/html'))
                page.goto('https://www.miramarcinemas.tw/')
                messages = []
                flow = MiramarFlow(settings(), lambda *x: messages.append(x), threading.Event(), timeout=0)
                flow.tick([page])
                self.assertTrue(flow.paused)
                self.assertEqual(flow.stage, 0)
                self.assertTrue(any('逾時' in m[1] for m in messages))
            finally:
                browser.close()


if __name__ == '__main__':
    unittest.main()
