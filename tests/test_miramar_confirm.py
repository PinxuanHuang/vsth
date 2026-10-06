import json
import threading
import time
import unittest
from html import escape
from playwright.sync_api import sync_playwright
from config import Settings
from miramar import MiramarFlow
from miramar_confirm import prepare_confirmation


SEAT = dict(AreaNumber='area', AreaCategoryCode='category', RowIndex='r', ColumnIndex='c', PhysicalName='Z', SeatId='9')
EXPECTED = {tuple(SEAT.values())}


def fixture():
    values = dict(MovieId='movie', Session='session', Seat=json.dumps([SEAT]),
                  TicketType=json.dumps([dict(Qty=1, TicketTypeSeats='1', TicketTypeTitleAlt='活動票', OnlyCashPay='0')]),
                  Concession='[]', TotalPrice='777', BookingFee='31', InvoiceType='donation-id', PayMethod='unchanged',
                  __RequestVerificationToken='dynamic-token')
    inputs = ''.join(f'<input type="hidden" name="{key}" value="{escape(value, quote=True)}">' for key,value in values.items())
    return ('<!doctype html><meta charset="utf-8"><form action="/Booking/Confirm" method="post">'+inputs+
            '<input name="Name"><input name="Phone"><select id="invoice_select" '
            'onchange="document.querySelector(\'[name=InvoiceType]\').value=this.value">'
            '<option value="donation-id">捐贈</option><option value="dynamic-personal">個人</option></select>'
            '<input name="InvoiceVehicle"><label><input id="AgreeRule" type="checkbox">我同意</label>'
            '<label onclick="window.payClicks=(window.payClicks||0)+1">線上付款 PURCHASE NOW</label></form>')


class MiramarConfirmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw=sync_playwright().start()
        cls.browser=cls.pw.chromium.launch(channel='msedge',headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.page=self.browser.new_page()
        self.page.route('**/*',lambda r:r.fulfill(body=fixture(),content_type='text/html; charset=utf-8'))
        self.page.goto('https://www.miramarcinemas.tw/Booking/Confirm')
        self.settings=Settings(tickets=1,agree=True,miramar_name='測試姓名',miramar_phone='0900000000',miramar_carrier='/TEST123')

    def tearDown(self):
        self.page.close()

    def prepare(self):
        return prepare_confirmation(self.page,self.settings,'movie','session',EXPECTED,threading.Event())

    def test_fill_and_submit_once(self):
        flow=MiramarFlow(self.settings,lambda *x:None,threading.Event())
        flow.page,flow.stage,flow.deadline=self.page,7,time.monotonic()+30
        flow.selected=['cinema','movie','date','session']
        flow.confirm_seats=EXPECTED
        flow.tick([self.page]); flow.tick([self.page])
        self.assertEqual(self.page.evaluate('window.payClicks'),1)
        self.assertEqual(flow.stage,8)
        self.assertEqual(self.page.locator('[name=Name]').input_value(),'測試姓名')
        self.assertEqual(self.page.locator('[name=InvoiceVehicle]').input_value(),'/TEST123')
        self.assertEqual(self.page.locator('[name=InvoiceType]').input_value(),'dynamic-personal')
        self.assertEqual(self.page.locator('[name=PayMethod]').input_value(),'unchanged')

    def test_no_consent_fills_but_never_pays(self):
        self.settings.agree=False
        self.assertIsNone(self.prepare())
        self.assertFalse(self.page.locator('#AgreeRule').is_checked())
        self.assertIsNone(self.page.evaluate('window.payClicks'))

    def test_order_mismatch_cash_only_missing_name_stop(self):
        for name,value in [('Session','wrong'),('Seat','[]'),('TotalPrice','NaN'),('Concession','[{}]'),
                           ('TicketType',json.dumps([dict(Qty=1,TicketTypeSeats=1,TicketTypeTitleAlt='活動票',OnlyCashPay='1')]))]:
            self.page.set_content(fixture())
            self.page.locator(f'[name="{name}"]').evaluate('(el,v)=>el.value=v',value)
            with self.assertRaises(ValueError): self.prepare()
            self.assertIsNone(self.page.evaluate('window.payClicks'))
        self.page.set_content(fixture())
        self.settings.miramar_name=''
        with self.assertRaises(ValueError): self.prepare()

    def test_invoice_handler_failure_and_cancel(self):
        self.page.locator('#invoice_select').evaluate("el=>el.removeAttribute('onchange')")
        with self.assertRaises(ValueError): self.prepare()
        self.page.set_content(fixture())
        stop=threading.Event(); stop.set()
        self.assertIsNone(prepare_confirmation(self.page,self.settings,'movie','session',EXPECTED,stop))
        self.assertEqual(self.page.locator('[name=Name]').input_value(),'')


if __name__=='__main__': unittest.main()
