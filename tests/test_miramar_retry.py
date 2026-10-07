import threading
import unittest
from unittest.mock import MagicMock, patch

from playwright.sync_api import sync_playwright
from miramar import MiramarFlow
from tests.test_miramar import FIXTURE, settings


class MiramarRetryTests(unittest.TestCase):
    def flow(self, stage=1):
        page=MagicMock()
        page.url='https://www.miramarcinemas.tw/'
        page.is_closed.return_value=False
        page.locator.return_value.count.return_value=0
        messages=[]
        flow=MiramarFlow(settings(),lambda *x:messages.append(x),threading.Event())
        flow.page,flow.stage,flow.deadline=page,stage,30
        return flow,page,messages

    def test_one_second_and_shared_five_minute_deadline(self):
        flow,page,messages=self.flow()
        with patch('miramar.time.monotonic',return_value=10): flow.tick([page])
        self.assertEqual(flow.retry_until,310)
        with patch('miramar.time.monotonic',return_value=10.99): flow.tick([page])
        page.reload.assert_not_called()
        with patch('miramar.time.monotonic',return_value=11): flow.tick([page])
        page.reload.assert_called_once()
        self.assertEqual(flow.stage,0)
        self.assertEqual(flow.selected,[])
        flow.stage=2
        with patch('miramar.time.monotonic',return_value=100): flow.tick([page])
        self.assertEqual(flow.retry_until,310)
        with patch('miramar.time.monotonic',return_value=310): flow.tick([page])
        self.assertTrue(flow.paused)
        self.assertEqual(page.reload.call_count,1)
        self.assertTrue(any('5 分鐘' in m[1] for m in messages))

    def test_stop_leave_home_and_closed_page_prevent_reload(self):
        for change in ('stop','url','closed'):
            flow,page,_=self.flow()
            with patch('miramar.time.monotonic',return_value=0): flow.tick([page])
            if change=='stop': flow.stop.set()
            elif change=='url': page.url='https://www.miramarcinemas.tw/Member/Login'
            else: page.is_closed.return_value=True
            with patch('miramar.time.monotonic',return_value=2): flow.tick([page])
            page.reload.assert_not_called()

    def test_session_failure_does_not_refresh(self):
        flow,page,_=self.flow(stage=3)
        with patch('miramar.time.monotonic',return_value=31): flow.tick([page])
        self.assertTrue(flow.paused)
        self.assertIsNone(flow.retry_until)
        page.reload.assert_not_called()

    def test_movie_or_date_appears_after_reload(self):
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                for missing in ('movie','date'):
                    context=browser.new_context()
                    loads=[]
                    absent=FIXTURE.replace('測試電影','其他電影') if missing=='movie' else FIXTURE.replace('2031-02-03','2031-02-04')
                    def route(r):
                        loads.append(r.request.url)
                        r.fulfill(body=absent if len(loads)==1 else FIXTURE,content_type='text/html; charset=utf-8')
                    context.route('**/*',route)
                    page=context.new_page()
                    page.goto('https://www.miramarcinemas.tw/')
                    flow=MiramarFlow(settings(),lambda *x:None,threading.Event())
                    for step in range(40):
                        with patch('miramar.time.monotonic',return_value=step*.2): flow.tick([page])
                        page.wait_for_timeout(100)
                        if flow.stage==3 or flow.paused: break
                    self.assertEqual(flow.stage,3,missing)
                    self.assertEqual(len(loads),2)
                    self.assertIsNone(flow.retry_until)
                    self.assertEqual(page.locator('#sel_cinema').input_value(),'new-cinema')
                    context.close()
            finally:
                browser.close()


if __name__=='__main__': unittest.main()
