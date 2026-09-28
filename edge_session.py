"""Own a separate desktop Edge profile and attach through its local DevTools port."""
import json
import os
import socket
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

from playwright.sync_api import Error


def edge_executable():
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe') as key:
                path = Path(winreg.QueryValue(key, None).strip('"'))
                if path.is_file():
                    return str(path)
        except OSError:
            pass
    for key in ('PROGRAMFILES(X86)', 'PROGRAMFILES', 'LOCALAPPDATA'):
        base = os.environ.get(key)
        if base:
            path = Path(base) / 'Microsoft/Edge/Application/msedge.exe'
            if path.is_file():
                return str(path)
    raise RuntimeError('找不到 Microsoft Edge，請先安裝 Edge。')


@contextmanager
def desktop_edge(pw, url, profile, stop):
    import msvcrt
    profile = Path(profile)
    profile.mkdir(parents=True, exist_ok=True)
    with (profile / 'assistant.lock').open('a+b') as lock:
        lock.seek(0)
        if not lock.read(1):
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise RuntimeError('另一個誠品自動工作階段正在使用設定檔，請先停止該工作階段。') from None
        process = browser = None
        try:
            if stop.is_set():
                yield None
                return
            with socket.socket() as port_socket:
                port_socket.bind(('127.0.0.1', 0))
                port = port_socket.getsockname()[1]
            process = subprocess.Popen([
                edge_executable(), f'--user-data-dir={profile.resolve()}',
                f'--remote-debugging-port={port}', '--remote-debugging-address=127.0.0.1',
                '--no-first-run', '--no-default-browser-check', '--new-window', url,
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            endpoint = f'http://127.0.0.1:{port}'
            opener = build_opener(ProxyHandler({}))
            deadline = time.monotonic() + 30
            while not stop.is_set():
                if process.poll() is not None:
                    raise RuntimeError('誠品 Edge 已關閉，或設定檔已被其他 Edge 視窗使用。')
                try:
                    with opener.open(endpoint + '/json/version', timeout=1) as response:
                        info = json.load(response)
                    if info.get('webSocketDebuggerUrl'):
                        browser = pw.chromium.connect_over_cdp(endpoint, no_defaults=True, timeout=30000)
                        break
                except (URLError, TimeoutError, OSError):
                    pass
                if time.monotonic() >= deadline:
                    raise RuntimeError('無法連接誠品 Edge，請確認 Edge 未禁止本機開發工具連線。')
                stop.wait(0.2)
            yield browser
        finally:
            if browser and browser.is_connected():
                try:
                    browser.new_browser_cdp_session().send('Browser.close')
                except Error:
                    pass
                try:
                    browser.close()
                except Error:
                    pass
            if process and process.poll() is None:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=5)
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
