import json
import queue
import tempfile
import threading
import unittest
from datetime import date
from html import escape
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch

from playwright.sync_api import Error, sync_playwright

from automation import BrowserWorker
from config import BOOKING_SITES, Settings, load_settings, save_settings
from eslite import EsliteFlow, READ_SELECTION, displayed_date, movie_link, session_link
from tests.test_eslite_tickets import ticket_fixture
from tests.test_eslite_confirmation import confirmation_fixture


BASE = 'https://arthouse.eslite.com/visSelect.aspx'
ENTRY = BOOKING_SITES['誠品']
TITLE = '測試片名：修復版'
CINEMA = 'changed-cinema-57'
MOVIE = 'opaque+movie/value='
SELECTED = BASE + '?' + urlencode({'visSearchBy': 'cin', 'visCinID': CINEMA})
MOVIE_URL = SELECTED + '&' + urlencode({'visMovieName': MOVIE})
SESSION_URL = 'https://arthouse.eslite.com/visSelectTickets.aspx?' + urlencode({'cinemacode': CINEMA, 'txtSessionId': 'dynamic-session-82'})
CONFIRMATION_URL = 'https://arthouse.eslite.com/confirmation-test.aspx'


def anchor(url, text, extra=''):
    return f'<a href="{escape(url, quote=True)}" {extra}>{escape(text)}</a>'


def fixture(show_movies=True, show_sessions=False):
    movies = '<tr><td>' + anchor(MOVIE_URL, TITLE + ' Sample Movie') + '</td></tr>' if show_movies else ''
    sessions = ''
    if show_sessions:
        wrong = SESSION_URL.replace('dynamic-session-82', 'wrong-day')
        sessions = ('<tr><td>10月2日 星期六</td></tr><tr><td>' + anchor(wrong, '18:45 A廳') + '</td></tr>'
                    '<tr><td>10月3日 星期日</td></tr><tr><td>' + anchor(SESSION_URL, '18:45 B廳', 'title="18:45"') + '</td></tr>')
    return ('<!doctype html><meta charset="utf-8"><div id="box_left"><table><tr><td>'
            + anchor(SELECTED, '任意影城') + '</td></tr></table></div><div id="box_center"><table>'
            + movies + '</table></div><div id="box_right"><table>' + sessions + '</table></div>')


class EsliteConfigTests(unittest.TestCase):
    def test_roundtrip_and_old_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'settings.json'
            settings = Settings(url=ENTRY, eslite_movie=TITLE, eslite_time='18:45', showtime='2032-10-03')
            save_settings(settings, path)
            self.assertEqual(load_settings(path), settings)
            path.write_text(json.dumps({'url': ENTRY, 'showtime': '2032-10-03'}), encoding='utf-8')
            self.assertEqual(load_settings(path).eslite_movie, '')
            for old in ('https://arthouse.eslite.com/visSelect.asp', 'https://arthouse.eslite.com/visSelect.aspx'):
                path.write_text(json.dumps({'url': old, 'showtime': '2032-10-03', 'eslite_ticket_type': 'member', 'agree': True}), encoding='utf-8')
                migrated = load_settings(path)
                self.assertEqual(migrated.url, ENTRY)
                self.assertEqual(migrated.eslite_ticket_type, 'member')
                self.assertTrue(migrated.agree)
            path.write_text(json.dumps({'url': ENTRY, 'eslite_ticket_type': 'member'}), encoding='utf-8')
            legacy = load_settings(path)
            self.assertEqual(legacy.eslite_ticket_type, 'member')
            with self.assertRaises(ValueError):
                save_settings(legacy, path)

    def test_auto_requires_date_and_exact_time_but_vieshow_ignores_fields(self):
        for stamp in ('24:00', '9:30', '18:60'):
            with self.subTest(stamp=stamp), self.assertRaises(ValueError):
                Settings(url=ENTRY, eslite_movie=TITLE, eslite_time=stamp, showtime='2032-10-03').validate()
        with self.assertRaises(ValueError):
            Settings(url=ENTRY, eslite_movie=TITLE, eslite_time='18:45').validate()
        Settings(cinema='test', eslite_movie=TITLE, eslite_time='ignored').validate()
        Settings(url=ENTRY, showtime='2032-10-03').validate()
        with self.assertRaises(ValueError):
            Settings(url=ENTRY).validate()

    def test_yearless_dates_use_requested_year_and_preserve_explicit_year(self):
        self.assertEqual(displayed_date('1月2日 星期日', date(2032, 12, 31)), date(2032, 1, 2))
        self.assertEqual(displayed_date('2031年10月3日', date(2032, 1, 1)), date(2031, 10, 3))
        self.assertIsNone(displayed_date('2月30日', date(2032, 1, 1)))

    def test_worker_routes_auto_mode_without_manual_protocol_launch(self):
        worker = BrowserWorker(Settings(url=ENTRY, eslite_movie=TITLE), queue.Queue())
        with patch.object(worker, 'run_eslite') as run, patch('automation.os.startfile') as start:
            worker.run()
        run.assert_called_once()
        start.assert_not_called()


class EsliteBrowserTests(unittest.TestCase):
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
        self.requests = []
        self.system_posts = []
        self.messages = []
        self.stop = threading.Event()
        self.settings = Settings(url=ENTRY, eslite_movie=TITLE, eslite_time='18:45', showtime='2032-10-03')
        self.settings.eslite_cinema = '任意影城'
        self.flow = EsliteFlow(self.settings, lambda *args: self.messages.append(args), self.stop, today=date(2032, 9, 29))
        def respond(route):
            self.requests.append(route.request.url)
            parts = urlsplit(route.request.url)
            query = parse_qs(parts.query)
            body = ticket_fixture() if parts.path.lower() == '/visselecttickets.aspx' else fixture('visCinID' in query, 'visMovieName' in query)
            if route.request.url == CONFIRMATION_URL:
                body = confirmation_fixture()
                self.system_posts.append(route.request.url)
            elif parts.path.lower() == '/visselecttickets.aspx':
                body += '<script>window.__doPostBack = () => { location.href = "' + CONFIRMATION_URL + '"; };</script>'
            route.fulfill(body=body, content_type='text/html; charset=utf-8')
        self.page.route('**/*', respond)

    def tearDown(self):
        self.page.close()

    def test_auto_first_cinema_movie_time_tickets_and_confirmation_once(self):
        self.page.goto(BASE)
        self.flow.tick([self.page])
        self.assertEqual(self.page.url, SELECTED)
        self.flow.tick([self.page])
        self.assertEqual(self.page.url, MOVIE_URL)
        self.flow.tick([self.page])
        self.assertEqual(self.page.url, SESSION_URL)
        self.flow.tick([self.page])
        self.flow.tick([self.page])
        self.page.wait_for_url(CONFIRMATION_URL)
        self.flow.tick([self.page])
        self.assertEqual(self.requests.count(SESSION_URL), 1)
        self.assertTrue(any(kind == 'handoff' for kind, _ in self.messages))
        self.assertEqual(len(self.system_posts), 1)
        self.assertFalse(self.page.locator('#chkTerms').is_checked())
        self.assertEqual(self.page.evaluate('actions'), [])
        self.flow.arm()
        self.flow.tick([self.page])
        self.assertFalse(self.flow.active)
        self.assertEqual(len(self.system_posts), 1)
        self.assertEqual(self.page.evaluate('actions'), [])

    def test_retry_on_ticket_page_before_submission(self):
        self.page.goto(MOVIE_URL)
        self.flow.tick([self.page])
        self.page.locator('.TicketType').first.evaluate("el => el.textContent = '暫時無票310:'")
        self.flow.tick([self.page])
        self.assertFalse(self.flow.active)
        self.assertEqual(self.page.evaluate('posts'), [])
        self.page.locator('.TicketType').first.evaluate("el => el.textContent = '全票310:'")
        self.flow.arm()
        self.flow.tick([self.page])
        self.flow.tick([self.page])
        self.page.wait_for_url(CONFIRMATION_URL)
        self.settings.agree = True
        self.flow.tick([self.page])
        self.assertTrue(self.page.locator('#chkTerms').is_checked())
        self.assertEqual(len(self.system_posts), 1)
        self.assertEqual(self.page.evaluate('actions'), [])

    def test_first_cinema_uses_list_order_and_disabled_first_is_not_skipped(self):
        self.page.goto(BASE)
        self.page.locator('#box_left table').evaluate("el => el.insertAdjacentHTML('beforeend', '<tr><td><a href=\"visSelect.aspx?visSearchBy=cin&visCinID=second\">另一家影城</a></td></tr>')")
        self.page.locator('#box_left a').first.evaluate("el => el.setAttribute('aria-disabled', 'true')")
        self.flow.tick([self.page])
        self.assertFalse(self.flow.active)
        self.assertEqual(self.page.url, BASE)
        self.page.locator('#box_left a').first.evaluate("el => el.removeAttribute('aria-disabled')")
        self.flow.arm()
        self.flow.tick([self.page])
        self.assertEqual(self.page.url, SELECTED)

    def test_waits_on_login_page_without_skipping_login(self):
        self.page.goto(ENTRY)
        self.page.set_content('<input type="password"><button>登入</button>')
        self.flow.tick([self.page])
        self.assertEqual(self.page.url, ENTRY)
        self.assertEqual(len(self.requests), 1)
        self.assertTrue(self.flow.active)

    def test_cinema_click_does_not_repeat_when_navigation_fails(self):
        self.page.goto(BASE)
        self.page.locator('#box_left a').evaluate('el => el.onclick = event => event.preventDefault()')
        self.flow.tick([self.page])
        self.flow.tick([self.page])
        self.assertEqual(len(self.requests), 1)
        self.flow.deadline = 0
        self.flow.tick([self.page])
        self.assertFalse(self.flow.active)

    def test_supplied_dom_uses_chinese_movie_and_adjacent_date_row(self):
        self.page.goto(BASE + '?visSearchBy=cin&visCinID=1001')
        self.page.set_content(Path(__file__).with_name('eslite_selection_fixture.html').read_text(encoding='utf-8'))
        snapshot = self.page.evaluate(READ_SELECTION)
        link = movie_link(snapshot, '八月三十一日，我在奧斯陸')
        self.assertIn('visMovieName=', link['href'])
        self.assertEqual(len(snapshot['movies']), 21)
        session = session_link(snapshot, '2026-09-30', '13:20', date(2026, 9, 29))
        self.assertEqual(query_value_for_test(session['href'], 'txtSessionId'), '119850')

    def test_full_supplied_page_already_selected_does_not_reclick_cinema_or_movie(self):
        self.page.goto(BASE + '?visSearchBy=cin&visCinID=1001')
        self.page.set_content(Path(__file__).with_name('eslite_full_selection_fixture.html').read_text(encoding='utf-8'))
        self.page.evaluate('history.replaceState(null, "", document.forms[0].action)')
        self.settings.eslite_cinema = '誠品電影院(松菸)'
        self.settings.eslite_movie = '八月三十一日，我在奧斯陸'
        self.settings.eslite_time = ''
        self.settings.showtime = '2026-09-30'
        self.settings.session_position = 'last'
        before = len(self.requests)
        self.flow.tick([self.page])
        self.assertEqual(query_value_for_test(self.page.url, 'txtSessionId'), '119850')
        navigations = [url for url in self.requests[before:]
                       if urlsplit(url).path.lower() in ('/visselect.aspx', '/visselecttickets.aspx')]
        self.assertEqual(navigations, [self.page.url])

    def test_blank_movie_and_time_resume_after_manual_movie_choice(self):
        self.settings.eslite_movie = ''
        self.settings.eslite_time = ''
        self.settings.session_position = 'last'
        self.page.goto(SELECTED)
        self.flow.tick([self.page])
        self.assertEqual(self.page.url, SELECTED)
        self.page.locator('#box_center a').click()
        self.flow.tick([self.page])
        self.assertEqual(self.page.url, SESSION_URL)

    def test_ambiguous_movie_stops_without_clicking(self):
        self.page.goto(SELECTED)
        self.page.locator('#box_center table').evaluate("el => el.insertAdjacentHTML('beforeend', '<tr><td><a href=\"' + el.querySelector('a').href + '&edition=2\">測試片名：修復版 Special</a></td></tr>')")
        self.flow.tick([self.page])
        self.assertEqual(self.page.url, SELECTED)
        self.assertFalse(self.flow.active)

    def test_duplicate_showtime_stops_without_clicking(self):
        self.page.goto(MOVIE_URL)
        self.page.locator('#box_right tr').last.evaluate("el => el.appendChild(el.querySelector('td').cloneNode(true))")
        self.flow.tick([self.page])
        self.assertEqual(self.page.url, MOVIE_URL)
        self.assertFalse(self.flow.active)

    def test_wrong_day_does_not_fall_back_to_same_time_on_other_day(self):
        self.page.goto(MOVIE_URL)
        self.settings.showtime = '2032-10-04'
        self.flow.tick([self.page])
        self.assertEqual(self.page.url, MOVIE_URL)
        self.assertTrue(self.flow.active)

    def test_missing_movie_waits_until_list_appears(self):
        self.page.goto(SELECTED)
        self.page.locator('#box_center table').evaluate("el => el.replaceChildren()")
        self.flow.tick([self.page])
        self.assertEqual(self.page.url, SELECTED)
        self.page.set_content(fixture())
        self.flow.tick([self.page])
        self.assertEqual(self.page.url, MOVIE_URL)

    def test_manual_navigation_during_snapshot_does_not_disarm_flow(self):
        self.page.goto(SELECTED)
        with patch.object(self.page, 'evaluate', side_effect=Error('Execution context was destroyed')):
            self.flow.tick([self.page])
        self.assertTrue(self.flow.active)
        self.assertEqual(self.page.url, SELECTED)
        self.flow.tick([self.page])
        self.assertEqual(self.page.url, MOVIE_URL)

    def test_disabled_or_wrong_cinema_or_external_session_is_never_clicked(self):
        for mutation in ("a.setAttribute('aria-disabled', 'true')", "a.href=a.href.replace('changed-cinema-57','other')", "a.href=a.href.replace('arthouse.eslite.com','example.com')"):
            with self.subTest(mutation=mutation):
                self.flow.arm()
                self.page.goto(MOVIE_URL)
                self.page.locator('#box_right tr').last.locator('a').evaluate('(a) => {' + mutation + '}')
                self.flow.tick([self.page])
                self.assertEqual(self.page.url, MOVIE_URL)

    def test_cancel_and_challenge_page_are_not_touched(self):
        self.page.goto(SELECTED)
        self.stop.set()
        self.flow.tick([self.page])
        self.assertEqual(self.page.url, SELECTED)
        self.stop.clear()
        self.page.goto('https://arthouse.eslite.com/visAgreement.aspx')
        self.flow.tick([self.page])
        self.assertTrue(self.page.url.endswith('visAgreement.aspx'))

    def test_changed_cinema_does_not_use_previous_movie_sessions(self):
        self.page.goto(SELECTED)
        self.flow.tick([self.page])
        self.page.goto(MOVIE_URL.replace(CINEMA, 'other'))
        self.flow.tick([self.page])
        self.assertNotEqual(self.page.url, SESSION_URL)


def query_value_for_test(url, name):
    return parse_qs(urlsplit(url).query)[name][0]


if __name__ == '__main__':
    unittest.main()
