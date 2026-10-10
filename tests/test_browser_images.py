import os
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from app.browser_images import BrowserSearchSession, NativeBrowserWindows, search_url, split_area


class BrowserImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.backend = Mock()
        self.backend.default_executable.return_value = 'C:/Browser/chrome.exe'
        self.backend.windows.return_value = {10: 'Existing user tab'}
        self.backend.capture.return_value = ((50, 50, 900, 600), False)
        self.backend.exists.return_value = True
        self.backend.work_area.return_value = (-1920, 0, 1920, 1080)
        self.receiver = Mock()
        self.receiver.winId.return_value = 100
        self.receiver.minimumHeight.return_value = 150
        self.receiver.screen.return_value.availableGeometry.return_value.height.return_value = 1080
        self.session = BrowserSearchSession(backend=self.backend)
        self.addCleanup(self.session.finish)

    def test_urls_encode_user_keywords_and_image_mode(self):
        keywords = 'Song & artist + meme'
        for engine in ('duckduckgo', 'google_images'):
            url = search_url(engine, keywords)
            values = parse_qs(urlparse(url).query)
            self.assertEqual(values['q'], [keywords])
            self.assertEqual(urlparse(url).scheme, 'https')
            self.assertTrue(values.get('ia') == ['images'] or values.get('tbm') == ['isch'])

    def test_arranges_only_new_search_window_and_closes_on_exit(self):
        self.session.open('duckduckgo', 'Song Artist', self.receiver, 20)
        self.backend.windows.return_value = {10: 'Existing user tab', 11: 'Song Artist at DuckDuckGo'}
        self.session.poll()
        self.backend.position.assert_called_once_with(11, (-1920, 216, 1920, 864))
        token = self.session.window_token
        self.session.finish()
        self.backend.close.assert_called_once_with(11, token)
        self.backend.release.assert_called_once_with(11, token)
        self.backend.restore.assert_not_called()

    def test_existing_or_ambiguous_windows_are_never_moved(self):
        self.backend.windows.return_value = {10: 'Song Artist at DuckDuckGo'}
        self.session.open('duckduckgo', 'Song Artist', self.receiver, 20)
        self.session.poll()
        self.backend.position.assert_not_called()
        self.backend.windows.return_value = {10: 'Song Artist', 11: 'Song Artist', 12: 'Song Artist'}
        self.session.deadline = time.monotonic() - 1
        self.session.poll()
        self.backend.position.assert_not_called()
        self.assertFalse(self.session.timer.isActive())

    def test_unsupported_default_browser_uses_system_url_handler(self):
        self.backend.default_executable.return_value = None
        with patch('app.browser_images.QDesktopServices.openUrl', return_value=True) as opened:
            self.assertTrue(self.session.open('google_images', 'Song', self.receiver, 20))
        opened.assert_called_once()
        self.backend.launch.assert_not_called()
        self.backend.position.assert_not_called()

    def test_closed_or_reused_handle_is_not_restored(self):
        self.session.open('duckduckgo', 'Song', self.receiver, 20)
        self.backend.windows.return_value = {10: 'Other', 11: 'Song at DuckDuckGo'}
        self.session.poll()
        self.backend.windows.return_value = {10: 'Other'}
        self.session.finish()
        self.backend.restore.assert_not_called()
        self.backend.close.assert_not_called()

    def test_reused_handle_with_same_browser_is_not_closed(self):
        self.session.open('duckduckgo', 'Song', self.receiver, 20)
        self.backend.windows.return_value = {11: 'Song at DuckDuckGo'}
        self.session.poll()
        self.backend.owns.return_value = False
        self.session.finish()
        self.backend.close.assert_not_called()

    def test_close_during_browser_startup_closes_late_identified_window(self):
        self.session.open('duckduckgo', 'Song', self.receiver, 20)
        self.session.finish()
        self.assertTrue(self.session.timer.isActive())
        self.backend.windows.return_value = {10: 'Existing user tab', 11: 'Song at DuckDuckGo'}
        self.session.poll()
        self.backend.close.assert_called_once()
        self.assertEqual(self.backend.close.call_args.args[0], 11)
        self.backend.position.assert_not_called()
        self.assertFalse(self.session.timer.isActive())

    def test_unclaimed_window_is_neither_moved_nor_closed(self):
        self.backend.claim.return_value = False
        self.session.open('duckduckgo', 'Song', self.receiver, 20)
        self.backend.windows.return_value = {11: 'Song at DuckDuckGo'}
        self.session.poll()
        self.session.finish()
        self.backend.position.assert_not_called()
        self.backend.close.assert_not_called()

    def test_arrangement_failure_still_allows_closing_owned_window(self):
        self.backend.position.side_effect = OSError('fixture')
        self.session.open('duckduckgo', 'Song', self.receiver, 20)
        self.backend.windows.return_value = {11: 'Song at DuckDuckGo'}
        self.session.poll()
        token = self.session.window_token
        self.session.finish()
        self.backend.close.assert_called_once_with(11, token)

    def test_supported_browser_launch_flags_without_shell_interpolation(self):
        backend = object.__new__(NativeBrowserWindows)
        url = search_url('google_images', 'Song & title')
        for browser, flag in [('msedge.exe', '--new-window'), ('chrome.exe', '--new-window'),
                              ('firefox.exe', '-new-window')]:
            path = str(Path('C:/Browser') / browser)
            with patch('app.browser_images.subprocess.Popen') as launch:
                backend.launch(path, url)
                self.assertEqual(launch.call_args.args[0], [path, flag, url])
                self.assertNotIn('shell', launch.call_args.kwargs)

    def test_layout_preserves_taskbar_work_area_and_minimum_height(self):
        top, bottom = split_area(100, 30, 1280, 900, 20)
        self.assertEqual(top, (100, 30, 1280, 180))
        self.assertEqual(bottom, (100, 210, 1280, 720))
        top, bottom = split_area(0, 0, 800, 600, 10)
        self.assertEqual(top[3], 150)
        self.assertEqual(top[3] + bottom[3], 600)
