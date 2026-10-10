import json
import os
from pathlib import Path
import tempfile
import unittest
import http.server
import threading
import time
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import Qt, QMimeData, QUrl
from PySide6.QtTest import QTest
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication
from app.application_identity import application_icon, configure_process_identity, APP_ID
from app.audio_preferences import load_preferences
from app.image_assets_widget import ImageAssetsWidget
from app.image_search import ImageSearchRegistry, ImageSearchRequest, ImageSearchResult, SearchMethod
from app.karaoke_assets import song_images_dir, shared_images_dir, image_files
from app.song_images import chosen_image, selected_thumbnail, settings_path


class FakeSearch(ImageSearchRequest):
    def __init__(self, query, parent, path):
        super().__init__(parent)
        self.query, self.path = query, path
        self.cancelled = False

    def start(self):
        self.resultsReady.emit([ImageSearchResult('Example meme', 'https://example.test/image', self.path)])

    def cancel(self):
        self.cancelled = True


class ImageAssetsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / 'workspace'
        self.source = self.root / 'user-picture.png'
        image = QImage(500, 300, QImage.Format.Format_RGB32)
        image.fill(QColor('red'))
        image.save(str(self.source))
        self.song = dict(title='Song', artist='Artist', album='Album')
        self.widget = ImageAssetsWidget(self.workspace, 'song', self.song)

    def tearDown(self):
        self.widget.dispose()
        self.widget.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def test_import_is_immediate_and_close_preserves_import_and_original(self):
        original = self.source.read_bytes()
        self.widget.stage_images([self.source])
        self.widget.choose_local()
        self.assertTrue(song_images_dir(self.workspace, 'song').exists())
        self.assertTrue(settings_path(self.workspace, 'song').exists())
        self.assertEqual(self.source.read_bytes(), original)
        stored = self.widget.selection
        self.assertNotEqual(stored, self.source)
        self.widget.dispose()
        self.assertTrue(stored.exists())

    def test_receiver_selection_removes_only_marked_image(self):
        second = self.root / 'second.png'
        second.write_bytes(self.source.read_bytes())
        self.widget.stage_images([self.source, second])
        self.widget.show_receiver()
        bar = self.widget.receiver
        second_row = next(index for index in range(bar.images.count())
                          if bar.images.item(index).data(Qt.ItemDataRole.UserRole).name.startswith('second_'))
        bar.images.setCurrentRow(second_row)
        self.assertTrue(bar.images.currentItem().isSelected())
        self.assertIn('second_', bar.remove_action.toolTip())
        bar.images.clearSelection()
        bar.remove_selected()
        self.assertEqual(len(self.widget.imports), 2)
        self.assertFalse(bar.remove_action.isEnabled())
        bar.images.setCurrentRow(second_row)
        bar.remove_action.trigger()
        removed = next(path for path in self.widget.imports if path.name.startswith('second_'))
        self.assertEqual(self.widget.removals, {removed})
        self.widget.save()
        self.assertFalse(removed.exists())
        self.assertEqual(len(self.widget.imports), 1)

    def test_receiver_actions_do_not_activate_editor_and_cancel_only_downloads(self):
        self.widget.show_receiver()
        bar = self.widget.receiver
        self.assertTrue(bar.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus)
        self.assertTrue(bar.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating))
        self.assertFalse(bar.cancel_action.isEnabled())
        with patch.object(self.widget, 'raise_') as raised, patch.object(self.widget, 'activateWindow') as activated:
            bar.arrange_action.trigger()
            self.widget.download_image(QUrl(self.image_server() + '/image'))
            self.assertTrue(bar.cancel_action.isEnabled())
            bar.cancel_action.trigger()
            self.assertIsNone(self.widget.download_reply)
            self.assertFalse(bar.cancel_action.isEnabled())
            raised.assert_not_called()
            activated.assert_not_called()

    def test_save_copies_selected_image_and_reopens_then_resets(self):
        self.widget.stage_images([self.source])
        self.widget.choose_local()
        self.widget.save()
        stored = chosen_image(self.workspace, 'song')
        self.assertEqual(stored.read_bytes(), self.source.read_bytes())
        thumbnail = QImage(selected_thumbnail(self.workspace, 'song'))
        self.assertLessEqual(max(thumbnail.width(), thumbnail.height()), 160)
        saved = json.loads(settings_path(self.workspace, 'song').read_text())
        self.assertFalse(Path(saved['thumbnail']).is_absolute())
        other = ImageAssetsWidget(self.workspace, 'song', self.song)
        try:
            self.assertEqual(other.selection, stored)
            other.reset_thumbnail()
            other.save()
            self.assertIsNone(selected_thumbnail(self.workspace, 'song'))
            self.assertTrue(stored.exists())
        finally:
            other.dispose()
            other.deleteLater()

    def test_missing_or_unsafe_selected_path_falls_back(self):
        settings = settings_path(self.workspace, 'song')
        settings.parent.mkdir(parents=True)
        for value in ('../outside.png', str(self.source), 'missing.png'):
            settings.write_text(json.dumps({'thumbnail': value}))
            self.assertIsNone(selected_thumbnail(self.workspace, 'song'))

    def test_shared_import_is_separate_from_song_images_and_original(self):
        shared = ImageAssetsWidget(self.workspace, 'song', self.song, shared=True)
        try:
            shared.stage_images([self.source])
            self.assertIsNone(shared.selection)
            shared.save()
            folder = shared_images_dir(self.workspace)
            stored = next(path for path in image_files(folder) if path.name.startswith('user-picture_'))
            self.assertEqual(stored.read_bytes(), self.source.read_bytes())
            self.widget.reload_gallery()
            self.assertEqual(self.widget.gallery.count(), 1)
            self.assertFalse(song_images_dir(self.workspace, 'song').exists())
            self.assertFalse(settings_path(self.workspace, 'song').exists())
        finally:
            shared.dispose()
            shared.deleteLater()

    def test_song_image_deletion_waits_for_save_and_resets_thumbnail(self):
        self.widget.stage_images([self.source])
        self.widget.save()
        stored = chosen_image(self.workspace, 'song')
        self.widget.dispose()
        self.widget = ImageAssetsWidget(self.workspace, 'song', self.song)
        self.widget.delete_local()
        self.assertTrue(stored.exists())
        self.assertIsNone(self.widget.selection)
        self.widget.save()
        self.assertFalse(stored.exists())
        self.assertIsNone(chosen_image(self.workspace, 'song'))
        self.assertTrue(self.source.exists())

    def test_cancel_preserves_shared_image_and_deleted_defaults_stay_deleted(self):
        shared = ImageAssetsWidget(self.workspace, 'song', self.song, shared=True)
        try:
            stored = shared.gallery.currentItem().data(Qt.ItemDataRole.UserRole)
            shared.delete_local()
            self.assertTrue(stored.exists())
            shared.dispose()
            shared.deleteLater()
            shared = ImageAssetsWidget(self.workspace, 'song', self.song, shared=True)
            paths = [shared.gallery.item(i).data(Qt.ItemDataRole.UserRole) for i in range(shared.gallery.count())]
            self.assertIn(stored, paths)
            shared.gallery.setCurrentRow(paths.index(stored))
            shared.delete_local()
            shared.save()
            self.assertNotIn(stored, image_files(shared_images_dir(self.workspace)))
        finally:
            shared.dispose()
            shared.deleteLater()

    def test_invalid_image_is_not_imported(self):
        invalid = self.root / 'invalid.png'
        invalid.write_bytes(b'not an image')
        with patch('app.image_assets_widget.QMessageBox.warning') as warning:
            self.widget.stage_images([invalid])
        warning.assert_called_once()
        self.assertEqual(self.widget.imports, [])

    def test_clipboard_image_is_imported_without_save(self):
        mime = QMimeData()
        mime.setImageData(QImage(str(self.source)))
        self.widget.receive_mime(mime)
        self.assertEqual(len(self.widget.imports), 1)
        pending = self.widget.imports[0]
        self.assertEqual(self.widget.selection, pending)
        self.assertTrue(settings_path(self.workspace, 'song').exists())
        self.assertTrue(chosen_image(self.workspace, 'song').exists())

    def test_first_image_becomes_thumbnail_later_images_do_not_replace_it(self):
        second = self.root / 'second.png'
        second.write_bytes(self.source.read_bytes())
        self.widget.stage_images([self.source, second])
        first = self.widget.imports[0]
        self.assertEqual(self.widget.selection, first)
        self.widget.remove_pending(first)
        self.assertIsNone(self.widget.selection)
        self.assertIn(first, self.widget.removals)
        self.assertTrue(self.source.exists())
        self.widget.reset_thumbnail()
        self.widget.stage_images([self.source])
        self.assertIsNone(self.widget.selection)

    def test_receiver_and_editor_close_preserve_imported_images(self):
        mime = QMimeData()
        mime.setImageData(QImage(str(self.source)))
        self.widget.show_receiver()
        self.widget.receiver.received.emit(mime)
        pending = self.widget.imports[0]
        self.assertEqual(self.widget.receiver.images.count(), 1)
        self.widget.receiver.close()
        self.assertTrue(pending.exists())
        self.assertEqual(len(self.widget.imports), 1)
        self.widget.dispose()
        self.assertTrue(pending.exists())
        self.assertTrue(settings_path(self.workspace, 'song').exists())

    def test_local_file_drop_does_not_download_or_modify_source(self):
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(self.source))])
        self.widget.receive_mime(mime)
        self.assertEqual(len(self.widget.imports), 1)
        self.assertNotEqual(self.widget.imports[0], self.source)
        self.assertEqual(self.widget.imports[0].read_bytes(), self.source.read_bytes())
        self.assertIsNone(self.widget.download_reply)

    def test_paste_shortcut_only_receives_when_image_area_has_focus(self):
        mime = QMimeData()
        mime.setImageData(QImage(str(self.source)))
        clipboard = Mock()
        clipboard.mimeData.return_value = mime
        self.widget.show()
        self.widget.tabs.setCurrentIndex(0)
        self.widget.gallery.setFocus()
        self.app.processEvents()
        with patch('app.image_receiver.QApplication.clipboard', return_value=clipboard):
            QTest.keyClick(self.widget.gallery, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(len(self.widget.imports), 1)
        self.widget.tabs.setCurrentIndex(1)
        self.widget.keywords.setFocus()
        self.app.processEvents()
        with patch('app.image_receiver.QApplication.clipboard', return_value=clipboard):
            QTest.keyClick(self.widget.keywords, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(len(self.widget.imports), 1)
        self.widget.close()

    def image_server(self):
        payload = self.source.read_bytes()
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                data = b'<html>not an image</html>' if self.path == '/page' else payload
                self.send_response(200)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except OSError:
                    pass

            def log_message(self, *_):
                pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f'http://127.0.0.1:{server.server_port}'

    def wait_download(self):
        deadline = time.monotonic() + 3
        while self.widget.download_reply and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertIsNone(self.widget.download_reply)

    def test_direct_url_download_and_source_are_saved_immediately(self):
        url = self.image_server() + '/image'
        mime = QMimeData()
        mime.setText(url)
        self.widget.receive_mime(mime)
        self.wait_download()
        self.assertEqual(len(self.widget.imports), 1)
        self.assertTrue(settings_path(self.workspace, 'song').exists())
        state = json.loads(settings_path(self.workspace, 'song').read_text())
        self.assertEqual(state['sources'][state['thumbnail']], url)
        self.assertEqual(chosen_image(self.workspace, 'song').read_bytes(), self.source.read_bytes())

    def test_html_link_is_not_imported_and_can_be_cancelled(self):
        url = self.image_server()
        self.widget.download_image(QUrl(url + '/page'))
        self.wait_download()
        self.assertEqual(self.widget.imports, [])
        self.assertIn('複製圖片', self.widget.receive_label.text())
        self.widget.download_image(QUrl(url + '/image'))
        self.widget.cancel_download()
        QTest.qWait(20)
        self.assertEqual(self.widget.imports, [])
        self.assertFalse(self.widget.download_path.exists())

    def test_download_over_size_limit_is_cancelled(self):
        self.widget.download_image(QUrl(self.image_server() + '/image'))
        self.widget.download_bytes = 20_000_001
        self.wait_download()
        self.assertEqual(self.widget.imports, [])
        self.assertFalse(self.widget.download_path.exists())

    def test_search_shell_and_replaceable_method_receive_only_metadata(self):
        self.widget.browser_session.open = Mock(return_value=True)
        self.widget.search()
        self.assertIsNone(self.widget.request)
        self.widget.browser_session.open.assert_called_once()
        args = self.widget.browser_session.open.call_args.args
        self.assertEqual(args[:2], ('duckduckgo', 'Song Artist'))
        self.assertTrue(self.widget.receiver.isVisible())
        requests = []
        def factory(query, parent):
            request = FakeSearch(query, parent, self.source)
            requests.append(request)
            return request
        registry = ImageSearchRegistry()
        registry.register(SearchMethod('custom', 'Test search', factory))
        other = ImageAssetsWidget(self.workspace, 'song', self.song, registry)
        try:
            self.assertEqual(requests, [])
            other.method.setCurrentIndex(other.method.findData('custom'))
            other.keywords.setText('Song funny art')
            other.search_kind.setCurrentIndex(other.search_kind.findData('meme'))
            other.search()
            self.assertEqual(requests[0].query.keywords, 'Song funny art')
            self.assertEqual(requests[0].query.kind, 'meme')
            self.assertEqual(requests[0].query.album, 'Album')
            self.assertEqual(other.results.count(), 1)
            other.results.setCurrentRow(0)
            other.import_result()
            other.choose_local()
            other.cancel_search()
            self.assertTrue(requests[0].cancelled)
            other.save()
            self.assertEqual(chosen_image(self.workspace, 'song').read_bytes(), self.source.read_bytes())
        finally:
            other.dispose()
            other.deleteLater()

    def test_windows_identity_and_native_icon_sizes(self):
        shell = Mock()
        with patch('app.application_identity.sys.platform', 'win32'), patch('ctypes.windll.shell32', shell):
            configure_process_identity()
        shell.SetCurrentProcessExplicitAppUserModelID.assert_called_once_with(APP_ID)
        result = application_icon()
        self.assertFalse(result.isNull())
        self.assertEqual({size.width() for size in result.availableSizes()}, {16, 24, 32, 48, 64, 128, 256})

    def test_bad_preferences_use_safe_defaults(self):
        result = load_preferences({'singing_preferences': dict(music_volume=200, guide_volume=float('nan'),
                                    monitor_enabled='false', guide_enabled=1, monitor_volume=-5)})
        self.assertEqual(result, dict(guide_enabled=False, monitor_enabled=True,
                                    music_volume=100, guide_volume=12, monitor_volume=0))
