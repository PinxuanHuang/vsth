import datetime
import calendar
import json
import queue
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from automation import BrowserWorker, apply_settings, resource, select_cinema_and_first_movie, select_showtime, target_url, submit_normal_booking, select_quantity_and_continue
from config import CINEMAS, SEAT_MODES, Settings, load_settings, parse_showtime, save_settings


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("電影購票助手")
        self.geometry("820x870")
        self.minsize(720, 750)
        self.configure(bg="#edf2f8")
        self.worker = None
        self.events = queue.Queue()
        self.closing = False
        self.after_id = None
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#edf2f8")
        style.configure("TLabel", background="#edf2f8", font=("Microsoft JhengHei UI", 10))
        style.configure("TButton", font=("Microsoft JhengHei UI", 10), padding=(12, 8))
        style.configure("TCheckbutton", background="#edf2f8", font=("Microsoft JhengHei UI", 10))
        style.configure("TEntry", padding=7)
        style.configure("Accent.TButton", foreground="white", background="#285acc")
        frame = ttk.Frame(self, padding=26)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="電影購票助手", font=("Microsoft JhengHei UI", 23, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(frame, text="手動進入購票專區後，自動選影城、電影與指定場次。", foreground="#52627a").grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 22))
        self.variables = {name: tk.StringVar() for name in ("url", "cinema", "tickets", "cinema_selector", "tickets_selector", "agree_selector", "login_email", "login_password")}
        self.agree = tk.BooleanVar()
        self.inputs = []
        for row, (label, key) in enumerate((("購票網址", "url"), ("影城名稱", "cinema"), ("購票張數", "tickets")), 2):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 18), pady=7)
            if key == "cinema":
                entry = ttk.Combobox(frame, textvariable=self.variables[key], values=["請選擇影城", *CINEMAS], state="readonly", height=15)
                self.cinema_combo = entry
            elif key == "tickets":
                entry = ttk.Spinbox(frame, from_=1, to=20, textvariable=self.variables[key], width=8)
            else:
                entry = ttk.Entry(frame, textvariable=self.variables[key])
            entry.grid(row=row, column=1, columnspan=2, sticky="ew", pady=7)
            self.inputs.append(entry)
        check = ttk.Checkbutton(frame, text="自動勾選網站的「我同意」", variable=self.agree)
        check.grid(row=5, column=1, columnspan=2, sticky="w", pady=10)
        self.inputs.append(check)
        date_frame = ttk.Frame(frame)
        date_frame.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(0, 14))
        ttk.Label(date_frame, text="場次日期").grid(row=0, column=0, padx=(0, 14))
        self.date_vars = {key: tk.StringVar() for key in ("year", "month", "day")}
        self.date_combos = {}
        year = datetime.datetime.now().year
        for col, (key, label, values) in enumerate((
            ("year", "年", range(year, year + 11)), ("month", "月", range(1, 13)),
            ("day", "日", range(1, 32))
        )):
            combo = ttk.Combobox(date_frame, width=5 if key == "year" else 3, state="readonly",
                                 textvariable=self.date_vars[key], values=[f"{n:02d}" for n in values])
            combo.grid(row=0, column=1 + col * 2)
            ttk.Label(date_frame, text=label).grid(row=0, column=2 + col * 2, padx=(3, 8))
            self.date_combos[key] = combo
            self.inputs.append(combo)
            if key in ("year", "month"):
                combo.bind("<<ComboboxSelected>>", self.update_days)
        self.session_position = tk.StringVar(value="第一場")
        self.session_combo = ttk.Combobox(date_frame, width=8, state="readonly",
                                         textvariable=self.session_position, values=("第一場", "最後一場"))
        self.session_combo.grid(row=0, column=7, padx=(8, 0))
        self.inputs.append(self.session_combo)
        tabs = ttk.Notebook(frame)
        tabs.grid(row=7, column=0, columnspan=3, sticky="ew")
        seats = ttk.Frame(tabs, padding=12)
        advanced = ttk.Frame(tabs, padding=12)
        tabs.add(seats, text="座位偏好")
        tabs.add(advanced, text="進階欄位設定")
        member = ttk.Frame(tabs, padding=12)
        member.columnconfigure(1, weight=1)
        tabs.add(member, text="會員登入")
        for row, (label, key) in enumerate((("帳號（信箱）", "login_email"), ("密碼", "login_password"))):
            ttk.Label(member, text=label).grid(row=row, column=0, sticky="w", padx=(0, 15), pady=5)
            entry = ttk.Entry(member, textvariable=self.variables[key], show='*' if key == 'login_password' else '')
            entry.grid(row=row, column=1, sticky="ew", pady=5)
            self.inputs.append(entry)
            if key == 'login_password':
                self.password_entry = entry
        self.show_password = tk.BooleanVar(value=False)
        reveal = ttk.Checkbutton(member, text='顯示密碼', variable=self.show_password,
                                 command=lambda: self.password_entry.configure(show='' if self.show_password.get() else '*'))
        reveal.grid(row=2, column=1, sticky='w')
        self.inputs.append(reveal)
        self.seat_mode = tk.StringVar(value="手動選位")
        self.seat_direction = tk.StringVar(value="左側優先")
        self.seat_contiguous = tk.BooleanVar(value=True)
        self.seat_preferred = tk.StringVar()
        ttk.Label(seats, text="選位方式").grid(row=0, column=0, sticky="w", pady=4)
        self.seat_combo = ttk.Combobox(seats, textvariable=self.seat_mode, values=list(SEAT_MODES), state="readonly", width=15)
        self.seat_combo.grid(row=0, column=1, columnspan=2, sticky="w", padx=8)
        self.seat_combo.bind('<<ComboboxSelected>>', self.update_seat_controls)
        self.direction_combo = ttk.Combobox(seats, textvariable=self.seat_direction, values=("左側優先", "右側優先"), state="readonly", width=10)
        self.direction_combo.grid(row=0, column=3, padx=8)
        self.seat_check = ttk.Checkbutton(seats, text="同排連座", variable=self.seat_contiguous)
        self.seat_check.grid(row=0, column=4, sticky="w")
        self.inputs.extend([self.seat_combo, self.direction_combo, self.seat_check])
        self.seat_ranges = {}
        self.seat_range_inputs = []
        for row, (axis, label) in enumerate((("row", "自訂前後 %（前 → 後）"), ("col", "自訂左右 %（左 → 右）")), 1):
            ttk.Label(seats, text=label).grid(row=row, column=0, sticky="w", pady=5)
            for col, bound in ((1, "start"), (3, "end")):
                key = f"seat_{axis}_{bound}"
                self.seat_ranges[key] = tk.StringVar()
                entry = ttk.Spinbox(seats, from_=0, to=100, width=6, textvariable=self.seat_ranges[key])
                entry.grid(row=row, column=col, padx=8, sticky="w")
                self.seat_range_inputs.append(entry)
                self.inputs.append(entry)
            ttk.Label(seats, text="至").grid(row=row, column=2)
        self.seat_demo_button = ttk.Button(seats, text="載入座位測試", command=self.seat_demo)
        self.seat_demo_button.grid(row=2, column=4, sticky="e")
        self.inputs.append(self.seat_demo_button)
        ttk.Label(seats, text="優先座位（逗號分隔）").grid(row=3, column=0, sticky="w", pady=5)
        self.seat_preferred_entry = ttk.Entry(seats, textvariable=self.seat_preferred)
        self.seat_preferred_entry.grid(row=3, column=1, columnspan=4, sticky="ew", padx=8, pady=5)
        self.inputs.append(self.seat_preferred_entry)
        ttk.Label(seats, text="例如 N7,N8,N9,M7,M8,M9；自動選位時依序優先，無可行組合則沿用原規則。",
                  wraplength=660).grid(row=4, column=0, columnspan=5, sticky="w")
        advanced.columnconfigure(1, weight=1)
        ttk.Label(advanced, text="多票種時可指定唯一票數 CSS；影城與同意 CSS 僅供本機示範使用。", wraplength=680).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 7))
        for row, (label, key) in enumerate((("影城欄位", "cinema_selector"), ("票數欄位", "tickets_selector"), ("同意欄位", "agree_selector")), 1):
            ttk.Label(advanced, text=label).grid(row=row, column=0, padx=(0, 15), pady=3)
            entry = ttk.Entry(advanced, textvariable=self.variables[key])
            entry.grid(row=row, column=1, sticky="ew", pady=3)
            self.inputs.append(entry)
        actions = ttk.Frame(frame)
        actions.grid(row=8, column=0, columnspan=3, sticky="ew", pady=18)
        self.start_button = ttk.Button(actions, text="儲存並執行", style="Accent.TButton", command=self.start)
        self.start_button.pack(side="left")
        self.retry_button = ttk.Button(actions, text="重新套用", command=self.retry, state="disabled")
        self.retry_button.pack(side="left", padx=7)
        self.stop_button = ttk.Button(actions, text="停止並關閉瀏覽器", command=self.stop, state="disabled")
        self.stop_button.pack(side="left")
        self.demo_button = ttk.Button(actions, text="載入示範設定", command=self.demo)
        self.demo_button.pack(side="right")
        self.status = tk.StringVar(value="準備就緒")
        ttk.Label(frame, textvariable=self.status, foreground="#285acc").grid(row=9, column=0, columnspan=3, sticky="w")
        self.logs = ScrolledText(frame, height=9, wrap="word", font=("Microsoft JhengHei UI", 10), relief="flat", bg="#111d32", fg="#e3eaf5", padx=12, pady=12, state="disabled")
        self.logs.grid(row=10, column=0, columnspan=3, sticky="nsew", pady=(10, 0))
        frame.rowconfigure(10, weight=1)
        try:
            self.populate(load_settings())
        except Exception as exc:
            self.populate(Settings())
            self.log(f"無法讀取上次設定，已使用預設值：{exc}")
        self.log("執行後請自行點選購票專區。程式會選影城與第一部電影，再依指定日期選擇第一場或最後一場。")
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after_id = self.after(100, self.poll)

    def populate(self, settings):
        self.show_password.set(False)
        self.password_entry.configure(show='*')
        for key, variable in self.variables.items():
            variable.set(str(getattr(settings, key)))
        self.agree.set(settings.agree)
        self.seat_mode.set(next(label for label, value in SEAT_MODES.items() if value == settings.seat_mode))
        self.seat_direction.set("左側優先" if settings.seat_direction == "left" else "右側優先")
        self.seat_contiguous.set(settings.seat_contiguous)
        self.seat_preferred.set(settings.seat_preferred)
        for key, variable in self.seat_ranges.items():
            variable.set(str(getattr(settings, key)))
        self.update_seat_controls()
        self.session_position.set("第一場" if settings.session_position == "first" else "最後一場")
        stamp = parse_showtime(settings.showtime) if settings.showtime else None
        if stamp:
            years = set(self.date_combos["year"]["values"])
            years.add(str(stamp.year))
            self.date_combos["year"].configure(values=sorted(years))
        for key, variable in self.date_vars.items():
            variable.set(f"{getattr(stamp, key):02d}" if stamp else "")
        self.update_days()
        if settings.cinema not in CINEMAS:
            self.variables["cinema"].set("請選擇影城")
            if settings.cinema:
                self.log("原先的影城名稱不在目前清單中，請重新選擇影城。")

    def update_days(self, event=None):
        year, month = self.date_vars["year"].get(), self.date_vars["month"].get()
        limit = calendar.monthrange(int(year), int(month))[1] if year and month else 31
        self.date_combos["day"].configure(values=[f"{n:02d}" for n in range(1, limit + 1)])
        if self.date_vars["day"].get() and int(self.date_vars["day"].get()) > limit:
            self.date_vars["day"].set("")

    def selected_showtime(self):
        values = {key: variable.get() for key, variable in self.date_vars.items()}
        stamp = "{year}-{month}-{day}".format(**values)
        parse_showtime(stamp)
        return stamp

    def demo(self):
        self.populate(Settings(url="demo://ticket", cinema="新竹大遠百威秀影城", tickets=2, agree=True))
        self.log("已載入本機示範設定，請按「儲存並執行」。")

    def seat_demo(self):
        self.populate(Settings(url="demo://seats", cinema="新竹大遠百威秀影城", tickets=2, seat_mode="middle"))
        self.log("已載入本機座位測試，可調整偏好與張數後執行，不會連線購票。")

    def update_seat_controls(self, event=None, running=False):
        manual = self.seat_mode.get() == "手動選位"
        self.seat_combo.configure(state="disabled" if running else "readonly")
        self.direction_combo.configure(state="disabled" if running or manual else "readonly")
        self.seat_check.configure(state="disabled" if running or manual else "normal")
        self.seat_preferred_entry.configure(state="disabled" if running or manual else "normal")
        for entry in self.seat_range_inputs:
            entry.configure(state="normal" if not running and self.seat_mode.get() == "自訂範圍" else "disabled")

    def selected_seat_settings(self):
        try:
            values = {key: int(variable.get()) for key, variable in self.seat_ranges.items()}
        except ValueError:
            raise ValueError("座位範圍請輸入 0～100 的整數。") from None
        return dict(values, seat_mode=SEAT_MODES[self.seat_mode.get()],
                    seat_direction="left" if self.seat_direction.get() == "左側優先" else "right",
                    seat_contiguous=self.seat_contiguous.get(), seat_preferred=self.seat_preferred.get().strip())

    def log(self, text):
        self.logs.configure(state="normal")
        self.logs.insert("end", f"[{datetime.datetime.now():%H:%M:%S}] {text}\n")
        self.logs.see("end")
        self.logs.configure(state="disabled")

    def busy(self, running):
        for widget in self.inputs + [self.start_button, self.demo_button]:
            widget.configure(state="disabled" if running else "normal")
        self.cinema_combo.configure(state="disabled" if running else "readonly")
        self.session_combo.configure(state="disabled" if running else "readonly")
        self.update_seat_controls(running=running)
        for combo in self.date_combos.values():
            combo.configure(state="disabled" if running else "readonly")
        for widget in (self.retry_button, self.stop_button):
            widget.configure(state="normal" if running else "disabled")

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        try:
            values = {key: variable.get() if key == 'login_password' else variable.get().strip()
                      for key, variable in self.variables.items()}
            try:
                values["tickets"] = int(values["tickets"])
            except ValueError:
                raise ValueError("票數必須是 1～20 的整數。") from None
            settings = Settings(**values, agree=self.agree.get(),
                                **self.selected_seat_settings(),
                                session_position="first" if self.session_position.get() == "第一場" else "last")
            if settings.url not in ("demo://ticket", "demo://seats"):
                settings.showtime = self.selected_showtime()
            if settings.cinema not in CINEMAS:
                raise ValueError("請從下拉選單選擇影城。")
            save_settings(settings)
        except Exception as exc:
            messagebox.showerror("設定無法儲存", str(exc), parent=self)
            return
        self.busy(True)
        self.status.set("正在啟動 Microsoft Edge…")
        self.log("設定已儲存，正在啟動新的瀏覽器工作階段。")
        self.worker = BrowserWorker(settings, self.events)
        self.worker.start()

    def retry(self):
        if self.worker and self.worker.commands.empty():
            self.worker.commands.put("apply")
            self.log("已重新啟用流程，等待購票頁；將使用執行時選擇的影城。")

    def stop(self):
        if self.worker:
            self.worker.stop_event.set()
            self.status.set("正在停止…（等待目前操作結束，最長約 30 秒）")
            self.retry_button.configure(state="disabled")
            self.stop_button.configure(state="disabled")

    def close(self):
        self.closing = True
        self.stop()
        if not self.worker or not self.worker.is_alive():
            self.destroy()

    def poll(self):
        try:
            while True:
                kind, text = self.events.get_nowait()
                if kind == "log":
                    self.log(text)
                else:
                    self.status.set(text)
                    if kind == "done":
                        self.busy(False)
                    elif kind == "handoff":
                        self.retry_button.configure(state="disabled")
        except queue.Empty:
            pass
        if self.closing and (not self.worker or not self.worker.is_alive()):
            self.destroy()
            return
        self.after_id = self.after(100, self.poll)


def smoke_test(output):
    """Exercise Tk, packaged resources and browser automation without real purchases."""
    from playwright.sync_api import sync_playwright
    app = App()
    app.withdraw()
    app.update()
    app.populate(Settings(cinema="MUVIE CINEMAS 台北松仁", showtime="2028-02-29"))
    assert app.selected_showtime() == "2028-02-29"
    assert set(app.date_vars) == {"year", "month", "day"}
    assert app.session_position.get() == "第一場"
    app.populate(Settings(showtime="2028-02-29", session_position="last"))
    assert app.session_position.get() == "最後一場"
    assert len(app.date_combos["day"]["values"]) == 29
    app.date_vars["year"].set("2027")
    app.update_days()
    assert app.date_vars["day"].get() == ""
    app.busy(True)
    app.busy(False)
    assert all(str(combo["state"]) == "readonly" for combo in app.date_combos.values())
    assert str(app.session_combo["state"]) == "readonly"
    app.populate(Settings(seat_mode='custom', seat_row_start=40, seat_row_end=90,
                          seat_direction='right', seat_contiguous=False, seat_preferred='N7,N8,N9,M7,M8,M9'))
    values = app.selected_seat_settings()
    assert values['seat_row_start'] == 40 and values['seat_row_end'] == 90
    assert values['seat_direction'] == 'right' and not values['seat_contiguous']
    assert values['seat_preferred'] == 'N7,N8,N9,M7,M8,M9'
    app.busy(True)
    assert all(str(entry['state']) == 'disabled' for entry in app.seat_range_inputs)
    assert str(app.seat_preferred_entry['state']) == 'disabled'
    app.busy(False)
    assert all(str(entry['state']) == 'normal' for entry in app.seat_range_inputs)
    assert str(app.seat_preferred_entry['state']) == 'normal'
    app.populate(Settings(seat_preferred='N7'))
    assert str(app.seat_preferred_entry['state']) == 'disabled'
    app.seat_demo()
    assert app.variables['url'].get() == 'demo://seats'
    assert app.selected_seat_settings()['seat_mode'] == 'middle'
    assert app.selected_seat_settings()['seat_preferred'] == ''
    app.populate(Settings(login_email='smoke@example.com', login_password=' local-test-pass '))
    assert app.variables['login_password'].get() == ' local-test-pass '
    assert app.password_entry['show'] == '*'
    app.busy(True)
    assert str(app.password_entry['state']) == 'disabled'
    app.busy(False)
    assert str(app.password_entry['state']) == 'normal'
    import tempfile
    with tempfile.TemporaryDirectory() as temporary:
        credential_path = Path(temporary) / 'settings.json'
        credential_settings = Settings(url='demo://ticket', cinema='test', login_email='smoke@example.com', login_password=' local-test-pass ')
        save_settings(credential_settings, credential_path)
        assert load_settings(credential_path) == credential_settings
        assert credential_settings.login_password not in credential_path.read_text(encoding='utf-8')
    app.destroy()
    settings = Settings(url="demo://ticket", cinema="台中示範影城", tickets=3, agree=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        try:
            page = browser.new_page()
            page.goto(target_url(settings))
            apply_settings(page, settings, lambda _: None)
            assert page.locator("#cinema").input_value() == settings.cinema
            assert page.locator("#tickets").input_value() == "3"
            assert page.locator("#agree").is_checked()
            fixture = resource("flow_fixture.html").read_text(encoding="utf-8")
            page.route("https://www.vscinemas.com.tw/**", lambda route: route.fulfill(body=fixture, content_type="text/html"))
            for area in ("vsTicketingSP", "vsTicketingSP2", "vsTicketingSP3"):
                page.goto(f"https://www.vscinemas.com.tw/{area}/ticketing/ticket.aspx")
                select_cinema_and_first_movie(page, Settings(cinema="新竹大遠百威秀影城"), lambda _: None)
                assert f"/{area}/" in page.url and "movie=FIRST" in page.url
            assert page.locator("#tickets").input_value() == "1"
            assert not page.locator("#agree").is_checked()
            page.goto("https://www.vscinemas.com.tw/vsTicketingSP3/ticketing/ticket.aspx?cinema=21%7CMU&movie=FIRST")
            page.set_content(resource("session_fixture.html").read_text(encoding="utf-8"))
            select_showtime(page, Settings(cinema="MUVIE CINEMAS 台北松仁", showtime="2026-09-25", session_position="last"), lambda _: None)
            assert "/vsTicketingSP3/ticketing/booking.aspx?cinemacode=21&txtSessionId=165195" in page.url
            import threading
            page.set_content(resource('booking_fixture.html').read_text(encoding='utf-8'))
            page.route('https://sales.vscinemas.com.tw/**', lambda route: route.fulfill(content_type='text/html', body='<h1>Manual handoff</h1>' if '/Seats' in route.request.url else resource('quantity_fixture.html').read_text(encoding='utf-8')))
            checkout = Settings(cinema='MUVIE CINEMAS 台北松仁', agree=True, tickets=3)
            submit_normal_booking(page, checkout, lambda _: None, threading.Event())
            page.wait_for_url('https://sales.vscinemas.com.tw/**')
            select_quantity_and_continue(page, checkout, lambda _: None, threading.Event())
            assert '/Seats?count=3' in page.url and not page.is_closed()
            from automation import select_seats_and_continue
            page.goto(resource('seats_fixture.html').as_uri())
            page.locator('#B-1').click()
            page.locator('#B-2').click()
            page.evaluate("""() => {
                window.SelectSeats=['B-1','B-2'];
                document.querySelectorAll('td[data-type="Empty"]').forEach(el => el.onclick=()=>{
                    if(SelectSeats.indexOf(el.id)>0) return;
                    if(SelectSeats.length===2) document.getElementById(SelectSeats.shift()).dataset.status='0';
                    SelectSeats.push(el.id); el.dataset.status='5';
                });
            }""")
            select_seats_and_continue(page, Settings(seat_mode='middle', tickets=2, seat_preferred='K7,K8'), lambda _: None, threading.Event())
            assert '測試完成' in page.locator('#result').inner_text()
            assert page.locator('#result').inner_text() == '測試完成：K-7、K-8'
        finally:
            browser.close()
    Path(output).write_text(json.dumps({"ok": True, "frozen": bool(getattr(sys, "frozen", False)), "checks": ["tkinter", "date dropdowns", "leap year", "Edge", "three areas", "date and session position", "scoped normal consent", "dynamic quantity ID", "continue and keep browser", "seat settings", "preferred seat input and locking", "preferred seating and checkout"]}), encoding="utf-8")
            assert 'B-1' not in page.locator('#result').inner_text()
            from automation import login_for_checkout
            login_posts = []
            def login_response(route):
                if route.request.method == 'POST':
                    login_posts.append(route.request.url)
                    body = '<h1>Payment</h1><button id="payment" onclick="window.paid=true">Pay</button>'
                else:
                    body = resource('checkout_login_fixture.html').read_text(encoding='utf-8')
                route.fulfill(body=body, content_type='text/html')
            page.route('https://sales.vscinemas.com.tw/**', login_response)
            page.goto('https://sales.vscinemas.com.tw/LiveTicketD4/Home/OrderConfirm')
            assert login_for_checkout(page, credential_settings, lambda _: None, threading.Event()) == 'submitted'
            assert len(login_posts) == 1 and page.locator('#payment').is_visible()
            assert page.evaluate('window.paid') is None
            assert login_for_checkout(page, credential_settings, lambda _: None, threading.Event(), discovery_timeout=0) == 'not_required'
            assert len(login_posts) == 1
        finally:
            browser.close()
    Path(output).write_text(json.dumps({"ok": True, "frozen": bool(getattr(sys, "frozen", False)), "checks": ["tkinter", "date dropdowns", "leap year", "Edge", "three areas", "date and session position", "scoped normal consent", "dynamic quantity ID", "continue and keep browser", "seat settings", "automatic seating and checkout", "masked login settings", "Windows encrypted credential storage", "checkout login once", "payment untouched"]}), encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--smoke-test":
        smoke_test(sys.argv[2])
    else:
        App().mainloop()
