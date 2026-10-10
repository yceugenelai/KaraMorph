"""Open image search in the user's browser; arrange only a verified new window."""

import ctypes
from ctypes import wintypes
from pathlib import Path
import subprocess
import sys
import time
import uuid
from urllib.parse import urlencode
from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from app.i18n import t


def search_url(engine, keywords):
    if engine == 'duckduckgo':
        return 'https://duckduckgo.com/?' + urlencode(dict(q=keywords, ia='images', iax='images'))
    if engine == 'google_images':
        return 'https://www.google.com/search?' + urlencode(dict(q=keywords, tbm='isch'))
    raise ValueError('Unknown image search engine')


def split_area(x, y, width, height, percent, minimum=150):
    percent = min(30, max(10, int(percent)))
    top = min(max(minimum, round(height * percent / 100)), max(1, height - 250))
    return (x, y, width, top), (x, y + top, width, height - top)


class NativeBrowserWindows:
    """Small Win32 adapter. No browser page automation or result scraping."""
    def __init__(self):
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.shell = ctypes.WinDLL('shlwapi', use_last_error=True)
        self.callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        signatures = [
            (self.user.EnumWindows, [self.callback_type, wintypes.LPARAM], wintypes.BOOL),
            (self.user.IsWindowVisible, [wintypes.HWND], wintypes.BOOL),
            (self.user.IsWindow, [wintypes.HWND], wintypes.BOOL),
            (self.user.GetWindowThreadProcessId, [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
            (self.user.GetWindowTextW, [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            (self.user.GetClassNameW, [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            (self.user.GetWindowRect, [wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            (self.user.SetWindowPos, [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT], wintypes.BOOL),
            (self.user.ShowWindow, [wintypes.HWND, ctypes.c_int], wintypes.BOOL),
            (self.user.IsZoomed, [wintypes.HWND], wintypes.BOOL),
            (self.user.IsIconic, [wintypes.HWND], wintypes.BOOL),
            (self.user.SetPropW, [wintypes.HWND, wintypes.LPCWSTR, wintypes.HANDLE], wintypes.BOOL),
            (self.user.GetPropW, [wintypes.HWND, wintypes.LPCWSTR], wintypes.HANDLE),
            (self.user.RemovePropW, [wintypes.HWND, wintypes.LPCWSTR], wintypes.HANDLE),
            (self.user.PostMessageW, [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], wintypes.BOOL),
            (self.user.MonitorFromWindow, [wintypes.HWND, wintypes.DWORD], wintypes.HANDLE),
            (self.kernel.OpenProcess, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            (self.kernel.QueryFullProcessImageNameW, [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            (self.kernel.CloseHandle, [wintypes.HANDLE], wintypes.BOOL),
            (self.shell.AssocQueryStringW, [wintypes.DWORD, ctypes.c_int, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)], ctypes.c_long),
        ]
        for function, args, result in signatures:
            function.argtypes, function.restype = args, result

    def default_executable(self):
        import winreg
        associations = []
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r'Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice') as key:
                association = winreg.QueryValueEx(key, 'ProgId')[0]
                associations.append(association)
        except OSError:
            pass
        associations.append('https')
        for association in associations:
            buffer = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buffer))
            if self.shell.AssocQueryStringW(0, 2, association, 'open', buffer, ctypes.byref(size)) == 0:
                path = Path(buffer.value)
                if path.is_file() and path.name.lower() in {'msedge.exe', 'chrome.exe', 'firefox.exe'}:
                    return str(path)
        return None

    def windows(self, executable):
        result = {}
        def visit(hwnd, _):
            if not self.user.IsWindowVisible(hwnd):
                return True
            name = ctypes.create_unicode_buffer(256)
            self.user.GetClassNameW(hwnd, name, len(name))
            if name.value not in {'Chrome_WidgetWin_1', 'MozillaWindowClass'}:
                return True
            pid = wintypes.DWORD()
            self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            process = self.kernel.OpenProcess(0x1000, False, pid.value)
            if not process:
                return True
            try:
                path = ctypes.create_unicode_buffer(32768)
                size = wintypes.DWORD(len(path))
                if (self.kernel.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(size))
                        and Path(path.value).resolve() == Path(executable).resolve()):
                    title = ctypes.create_unicode_buffer(2048)
                    self.user.GetWindowTextW(hwnd, title, len(title))
                    result[int(hwnd)] = title.value
            finally:
                self.kernel.CloseHandle(process)
            return True
        self.user.EnumWindows(self.callback_type(visit), 0)
        return result

    def launch(self, executable, url):
        flag = '-new-window' if Path(executable).name.lower() == 'firefox.exe' else '--new-window'
        subprocess.Popen([executable, flag, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def exists(self, hwnd):
        return bool(self.user.IsWindow(hwnd))

    def work_area(self, hwnd):
        class MonitorInfo(ctypes.Structure):
            _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', wintypes.RECT),
                        ('rcWork', wintypes.RECT), ('dwFlags', wintypes.DWORD)]
        function = self.user.GetMonitorInfoW
        function.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
        function.restype = wintypes.BOOL
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        monitor = self.user.MonitorFromWindow(hwnd, 2)
        if not function(monitor, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        r = info.rcWork
        return r.left, r.top, r.right - r.left, r.bottom - r.top

    def capture(self, hwnd):
        rect = wintypes.RECT()
        if not self.user.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise ctypes.WinError(ctypes.get_last_error())
        return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top), bool(self.user.IsZoomed(hwnd))

    def position(self, hwnd, area):
        if self.user.IsZoomed(hwnd) or self.user.IsIconic(hwnd):
            self.user.ShowWindow(hwnd, 9)  # SW_RESTORE
        if not self.user.SetWindowPos(hwnd, None, *area, 0x0014):  # NOACTIVATE | NOZORDER
            raise ctypes.WinError(ctypes.get_last_error())

    def restore(self, hwnd, original):
        if self.exists(hwnd):
            self.position(hwnd, original[0])
            if original[1]:
                self.user.ShowWindow(hwnd, 3)

    def claim(self, hwnd, token):
        # A property belongs to this particular window, not a reused HWND/PID.
        return bool(self.user.SetPropW(hwnd, token, 1))

    def owns(self, hwnd, token):
        return bool(token and self.user.GetPropW(hwnd, token) == 1)

    def release(self, hwnd, token):
        if self.owns(hwnd, token):
            self.user.RemovePropW(hwnd, token)

    def close(self, hwnd, token):
        if self.owns(hwnd, token):
            if not self.user.PostMessageW(hwnd, 0x0010, 0, 0):  # Normal WM_CLOSE, never kill the process.
                raise ctypes.WinError(ctypes.get_last_error())


class BrowserSearchSession(QObject):
    status = Signal(str)

    def __init__(self, parent=None, backend=None):
        super().__init__(parent)
        self.backend = backend
        if self.backend is None and sys.platform == 'win32':
            self.backend = NativeBrowserWindows()
        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.poll)
        self.hwnd = None
        self.original = None
        self.browser = None
        self.before = {}
        self.receiver = None
        self.window_token = None
        self.close_pending = False

    def open(self, engine, keywords, receiver, percent):
        self.finish(wait_for_window=False)
        self.close_pending = False
        url = search_url(engine, keywords)
        self.receiver, self.percent, self.keywords = receiver, percent, keywords
        self.browser = self.backend.default_executable() if self.backend else None
        if not self.browser:
            if not QDesktopServices.openUrl(QUrl(url)):
                self.status.emit(t('無法開啟瀏覽器。'))
                return False
            self.status.emit(t('已開啟預設瀏覽器；請自行將視窗排在接收列下方。'))
            return True
        try:
            self.before = self.backend.windows(self.browser)
            self.backend.launch(self.browser, url)
        except OSError:
            if not QDesktopServices.openUrl(QUrl(url)):
                self.status.emit(t('無法開啟瀏覽器。'))
                return False
            self.status.emit(t('已開啟預設瀏覽器；請自行將視窗排在接收列下方。'))
            return True
        self.deadline = time.monotonic() + 10
        self.timer.start()
        self.status.emit(t('正在排列新開的搜尋視窗…'))
        return True

    def poll(self):
        windows = self.backend.windows(self.browser)
        query = ' '.join(self.keywords.casefold().split())
        candidates = [hwnd for hwnd, title in windows.items() if hwnd not in self.before
                      and query in ' '.join(title.casefold().split())]
        if len(candidates) == 1:
            self.hwnd = candidates[0]
            self.timer.stop()
            token = 'KaraMorph.ImageSearch.' + uuid.uuid4().hex
            if not self.backend.claim(self.hwnd, token):
                self.hwnd = None
                self.status.emit(t('無法確認新搜尋視窗；原有視窗不會移動，請自行排列。'))
                return
            self.window_token = token
            if self.close_pending:
                self.finish()
                return
            try:
                self.original = self.backend.capture(self.hwnd)
                if self.arrange():
                    self.status.emit(t('把圖片拖到上方接收列，或複製圖片後按「貼上」。'))
                else:
                    self.status.emit(t('無法自動排列；請自行調整瀏覽器位置。'))
            except OSError:
                self.status.emit(t('無法自動排列；請自行調整瀏覽器位置。'))
        elif time.monotonic() >= self.deadline:
            self.timer.stop()
            self.status.emit(t('無法確認新搜尋視窗；原有視窗不會移動，請自行排列。'))

    def arrange(self):
        if self.hwnd and self.receiver and self.backend.exists(self.hwnd):
            if (self.hwnd not in self.backend.windows(self.browser)
                    or not self.backend.owns(self.hwnd, self.window_token)):
                self.hwnd, self.original, self.window_token = None, None, None
                return False
            physical = self.backend.work_area(int(self.receiver.winId()))
            logical = self.receiver.screen().availableGeometry()
            minimum = round(self.receiver.minimumHeight() * physical[3] / max(1, logical.height()))
            _, bottom = split_area(*physical, self.percent, minimum)
            self.backend.position(self.hwnd, bottom)
            return True
        return False

    def finish(self, close_window=True, wait_for_window=True):
        # A browser may still be starting when the receiver is closed.
        # Keep the bounded identification timer, but never arrange a closed bar.
        if close_window and wait_for_window and self.timer.isActive() and not self.hwnd:
            self.close_pending = True
            self.receiver = None
            return
        self.timer.stop()
        if self.backend and self.hwnd and self.window_token:
            try:
                if (self.hwnd in self.backend.windows(self.browser)
                        and self.backend.owns(self.hwnd, self.window_token)):
                    if close_window:
                        self.backend.close(self.hwnd, self.window_token)
                    elif self.original:
                        self.backend.restore(self.hwnd, self.original)
                    self.backend.release(self.hwnd, self.window_token)
            except OSError:
                self.status.emit(t('無法關閉搜尋視窗，請在瀏覽器中手動關閉。'))
        self.hwnd, self.original, self.receiver, self.window_token = None, None, None, None
