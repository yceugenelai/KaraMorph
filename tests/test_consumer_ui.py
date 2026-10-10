import json
import time
import threading
import http.server
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QByteArray, QObject, QPoint, Qt, Signal, QUrl
from PySide6.QtWidgets import QApplication, QAbstractItemView, QMessageBox, QPushButton, QStyleFactory
from PySide6.QtTest import QTest

from app.consumer import ConsumerWindow
from app.karaoke_window import KaraokeWindow
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtNetwork import QNetworkRequest
from app.batch_lyrics import LyricsLookup, choose_candidate, has_lrclib, store_candidate
from app.lyrics import assets_dir, load_lyric_state, save_lyric_state
from app.playlist import load_playlist, save_playlist
from app.storage import save_catalog


class Reply(QObject):
    finished = Signal()

    def __init__(self, status, payload=None, retry=''):
        super().__init__()
        self.code, self.payload, self.retry = status, payload, retry
        self.aborted = False

    def attribute(self, _):
        return self.code

    def readAll(self):
        return QByteArray(json.dumps(self.payload).encode())

    def rawHeader(self, name):
        if not isinstance(name, str):
            raise TypeError("rawHeader requires str")
        return QByteArray(self.retry.encode())

    def errorString(self):
        return 'fixture'

    def abort(self):
        self.aborted = True
        self.finished.emit()


class ConsumerUiTests(unittest.TestCase):
    def test_closing_assets_refreshes_immediate_imports_even_on_cancel(self):
        dialog = Mock()
        dialog.exec.return_value = 0
        with patch('app.lyrics_dialog.LyricsDialog', return_value=dialog), \
                patch.object(self.window, 'refresh_tree') as tree, \
                patch.object(self.window, 'refresh_queue_labels') as queue:
            self.window.edit_song_assets('song')
        tree.assert_called_once()
        queue.assert_called_once()
        dialog.deleteLater.assert_called_once()

    @classmethod
    def setUpClass(cls):
        existing = QApplication.instance()
        if existing is not None and not isinstance(existing, QApplication):
            raise unittest.SkipTest('Run widget checks separately: test_consumer_ui.py')
        cls.app = existing or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = {'workspace': str(self.root / 'outputs'), 'source_dir': ''}
        self.patches = [patch('app.consumer.load_settings', return_value=self.settings),
                        patch('app.consumer.save_settings'), patch('app.consumer.data_dir', return_value=self.root),
                        patch('app.consumer.QMessageBox.information')]
        for item in self.patches:
            item.start()
        self.window = ConsumerWindow()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def populate(self, count=35):
        audio = self.root / 'original.mp3'
        audio.touch()
        songs = [dict(id=f's{i}', title=f'Song {i}', artist='Singer', duration_sec=100,
                      path=str(audio), available=True) for i in range(count)]
        self.window.songs = songs
        self.window.song_map = {song['id']: song for song in songs}
        self.window.refresh_tree()
        self.window.centralWidget().setCurrentIndex(1)
        self.window.resize(1450, 720)
        self.window.show()
        self.app.processEvents()

    def test_refresh_preserves_scroll_selection_and_checks(self):
        self.populate()
        tree = self.window.tree
        item = tree.topLevelItem(24)
        item.setCheckState(0, Qt.CheckState.Checked)
        tree.setCurrentItem(item)
        tree.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtTop)
        before = self.window._capture_tree_view()
        self.window.refresh_tree()
        self.app.processEvents()
        after = self.window._capture_tree_view()
        self.assertEqual(before['current'], after['current'])
        self.assertEqual(before['top'], after['top'])
        self.assertLessEqual(abs(before['vertical'] - after['vertical']), 2)
        self.assertEqual(self.window.selected_song_ids(), ['s24'])

    def test_deleted_version_selects_next_and_buttons_stay_compact(self):
        self.populate(2)
        folder = self.window.workspace / 'songs/s0/style_jobs/job'
        folder.mkdir(parents=True)
        variants = []
        for index in range(3):
            path = folder / f'{index}.wav'
            path.touch()
            variants.append(dict(id=f'v{index}', audio_path=str(path), style='Jazz', source_id='instrumental',
                                 seed=1234, cover_strength=.2, cover_noise_strength=.2))
        save_catalog(self.window.workspace, 's0', {'separation': None, 'variants': variants})
        self.window.refresh_tree()
        root = self.window.tree.topLevelItem(0)
        root.setExpanded(True)
        self.window.tree.setCurrentItem(root.child(2))  # v1 after original and v0
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes):
            self.window.delete_version('s0', 'v1')
        self.assertEqual(self.window.tree.currentItem().data(0, Qt.ItemDataRole.UserRole), ('s0', 'v2'))
        root = self.window.tree.topLevelItem(0)
        self.assertTrue(root.isExpanded())
        for column, width in ((4, 120), (5, 110), (6, 75)):
            button = self.window.tree.itemWidget(root, column).findChild(QPushButton)
            self.assertEqual(button.minimumWidth(), width)
            self.assertGreaterEqual(button.maximumWidth(), button.minimumSizeHint().width())
            self.assertEqual(button.height(), 32)

    def test_all_selection_and_single_dialog_target_snapshot(self):
        self.populate(3)
        self.window.tree.topLevelItem(0).setCheckState(0, Qt.CheckState.Checked)
        self.assertEqual(self.window.select_all.checkState(), Qt.CheckState.PartiallyChecked)
        self.window.select_all.click()
        self.assertEqual(len(self.window.selected_song_ids()), 3)
        self.window.select_all.click()
        self.assertEqual(self.window.selected_song_ids(), [])
        with patch.object(self.window.version_dialog, 'exec'):
            self.window.open_version_dialog(['s1'])
        self.window.speed.setValue(.9)
        with patch.object(self.window, '_submit') as submit:
            self.window._submit_version_dialog('style')
        self.assertEqual(submit.call_args.args[0], ['s1'])
        request = submit.call_args.args[2]
        self.window.speed.setValue(1.2)
        self.assertEqual(request['speed'], .9)

    def test_version_spin_buttons_click_up_and_down_across_styles(self):
        original_style = self.app.style().objectName()
        try:
            for style_name in QStyleFactory.keys():
                with self.subTest(style=style_name):
                    self.app.setStyle(style_name)
                    self.window.version_dialog.show()
                    self.app.processEvents()
                    for widget, start, step in ((self.window.speed, 1.0, .05),
                                                (self.window.key, 0, 1)):
                        with self.subTest(widget=type(widget).__name__):
                            widget.setValue(start)
                            x = widget.width() - 11
                            up = QPoint(x, widget.height() // 4)
                            down = QPoint(x, widget.height() * 3 // 4)
                            QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=up)
                            self.assertAlmostEqual(widget.value(), start + step)
                            QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=down)
                            self.assertAlmostEqual(widget.value(), start)
                            widget.setValue(widget.maximum())
                            QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=up)
                            self.assertEqual(widget.value(), widget.maximum())
                            widget.setValue(widget.minimum())
                            QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=down)
                            self.assertEqual(widget.value(), widget.minimum())
        finally:
            self.window.version_dialog.close()
            self.app.setStyle(original_style)

    def test_deduplication_cancel_and_batch_summary(self):
        self.populate(2)
        with patch.object(self.window, '_start_next_operation'):
            self.window._submit(['s0', 's1'], 'lyrics')
            self.window._submit(['s0'], 'lyrics')
        self.assertEqual(len(self.window.pending_ops), 2)
        self.assertEqual(self.window.batches[self.window.last_batch]['counts']['skipped'], 1)
        self.window.active_op = self.window.pending_ops.pop(0)
        self.window.lyric_lookup.cancel = Mock()
        self.window.cancel_batch()
        self.assertIsNone(self.window.active_op)
        self.assertEqual(self.window.pending_ops, [])
        self.assertIn('跳過 3', self.window.process_status.text())

    def test_resume_playlist_duplicates_missing_and_empty(self):
        items = [dict(song_id='missing', variant_id='fx', label='Jazz')] * 2
        save_playlist(self.root / 'last_playlist.json', items)
        self.window._restore_last_queue()
        self.assertEqual(self.window.queue_items(), items)
        self.assertIn('無法使用', self.window.queue.item(0).text())
        self.assertIsNone(self.window.playing_item)
        self.window.close()
        self.assertEqual(load_playlist(self.root / 'last_playlist.json'), items)
        self.window.queue.clear()
        self.window.show()
        self.window.close()
        self.assertEqual(load_playlist(self.root / 'last_playlist.json'), [])

    def test_match_preserves_manual_and_marks_download(self):
        song = dict(id='song', title='KING', artist='Singer', duration_sec=100)
        row = dict(id=1, trackName='KING', artistName='Singer', duration=100,
                   syncedLyrics='[00:01]hello', plainLyrics='hello')
        self.assertEqual(choose_candidate([row], song)[0], row)
        self.assertIsNone(choose_candidate([row, row | {'id': 2}], song)[0])
        self.assertIsNone(choose_candidate([row | {'duration': 105}], song)[0])
        folder = assets_dir(self.window.workspace, 'song')
        folder.mkdir(parents=True)
        (folder / 'lyrics.txt').write_text('manual untouched', encoding='utf-8')
        save_lyric_state(self.window.workspace, 'song', {'source': 'manual', 'offsets': {'fx': 200}})
        store_candidate(self.window.workspace, 'song', row, {})
        self.assertTrue(has_lrclib(self.window.workspace, 'song'))
        self.assertEqual(load_lyric_state(self.window.workspace, 'song')['source'], 'manual')
        self.assertEqual((folder / 'lyrics.txt').read_text(), 'manual untouched')
        store_candidate(self.window.workspace, 'empty', row, {})
        self.assertEqual(load_lyric_state(self.window.workspace, 'empty')['source'], 'lrclib')

    def test_network_fallback_rate_limit_timeout_and_cancellation(self):
        lookup = LyricsLookup()
        done = Mock()
        lookup.done.connect(done)
        network = Mock()
        lookup.network = network
        first, second = Reply(404), Reply(429, retry='120')
        network.get.side_effect = [first, second]
        lookup.start(dict(id='song', title='KING', artist='Singer', duration_sec=100), self.window.workspace)
        first.finished.emit()
        lookup.retry.stop()
        lookup._send()
        self.assertEqual(lookup.endpoint, 'search')
        second.finished.emit()
        self.assertGreater(lookup.retry.interval(), 60_000)
        lookup.cancel()
        self.assertFalse(lookup.retry.isActive())
        third = Reply(200)
        network.get.side_effect = None
        network.get.return_value = third
        lookup.next_allowed = 0
        lookup.start(dict(id='song', title='KING', artist='Singer', duration_sec=100), self.window.workspace)
        lookup._timeout()
        self.assertTrue(third.aborted)
        done.assert_called_once_with('failed', 'LRCLIB 查詢逾時')

    def test_batch_lyrics_real_qt_replies_finish_every_song(self):
        self.populate(3)
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                from urllib.parse import urlparse, parse_qs
                url = urlparse(self.path)
                title = parse_qs(url.query)['track_name'][0]
                status = 404 if title == 'Song 1' and url.path == '/get' else 200
                row = dict(id=title, trackName=title, artistName='Singer', duration=100,
                           syncedLyrics='[00:01]hello', plainLyrics='hello')
                payload = json.dumps([] if url.path == '/search' else row).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            def log_message(self, *args):
                pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        native = self.window.lyric_lookup.network
        def request_local(request):
            redirected = QNetworkRequest(request)
            url = QUrl(f'http://127.0.0.1:{server.server_port}/' + request.url().path().rsplit('/', 1)[-1])
            url.setQuery(request.url().query())
            redirected.setUrl(url)
            return native.get(redirected)
        self.window.lyric_lookup.network = Mock()
        self.window.lyric_lookup.network.get.side_effect = request_local
        self.window._submit(['s0', 's1', 's2'], 'lyrics')
        deadline = time.monotonic() + 5
        while (self.window.active_op or self.window.pending_ops) and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertIsNone(self.window.active_op)
        self.assertEqual(self.window.pending_ops, [])
        self.assertEqual(self.window.batches[self.window.last_batch]['counts'], {'success': 2, 'not_found': 1})
        self.assertFalse(self.window.lyric_lookup.timeout.isActive())

    def test_batch_continues_after_reply_processing_exception(self):
        self.populate(2)
        first = Reply(200)
        first.rawHeader = Mock(side_effect=TypeError('unexpected reply API'))
        second = Reply(200, dict(id=2, trackName='Song 1', artistName='Singer', duration=100,
                                plainLyrics='hello'))
        lookup = self.window.lyric_lookup
        lookup.network = Mock()
        lookup.network.get.side_effect = [first, second]
        self.window._submit(['s0', 's1'], 'lyrics')
        first.finished.emit()
        self.app.processEvents()
        self.assertEqual(self.window.active_op['song_id'], 's1')
        second.finished.emit()
        self.app.processEvents()
        self.assertIsNone(self.window.active_op)
        self.assertEqual(self.window.batches[self.window.last_batch]['counts'], {'failed': 1, 'success': 1})

    def test_reload_reads_new_song_and_keeps_checks_and_versions(self):
        source = self.root / 'music'
        source.mkdir()
        original = source / 'original.mp3'
        original.touch()
        self.window.settings['source_dir'] = str(source)
        def run_scan(task):
            self.assertFalse(self.window.reload_library.isEnabled())
            task.run()
        with patch('app.consumer.merge_library', side_effect=lambda source, songs: songs), \
                patch('app.consumer.QThreadPool.globalInstance') as pool:
            pool.return_value.start.side_effect = run_scan
            self.window.reload_library.click()
            self.assertEqual(len(self.window.songs), 1)
            song = self.window.songs[0]
            catalog = {'separation': None, 'variants': [{'id': 'existing', 'audio_path': song['path'], 'label': 'existing'}]}
            save_catalog(self.window.workspace, song['id'], catalog)
            self.window.refresh_tree()
            self.window.tree.topLevelItem(0).setCheckState(0, Qt.CheckState.Checked)
            self.window.tree.topLevelItem(0).setExpanded(True)
            new_audio = source / 'new song.mp3'
            new_audio.write_bytes(b'new fixture')
            self.window.reload_library.click()
        self.assertTrue(self.window.reload_library.isEnabled())
        self.assertEqual(len(self.window.song_map), 2)
        self.assertTrue(any(row['path'] == str(new_audio.resolve()) for row in self.window.songs))
        existing = next(self.window.tree.topLevelItem(i) for i in range(2)
                        if self.window.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) == song['id'])
        self.assertEqual(existing.checkState(0), Qt.CheckState.Checked)
        self.assertTrue(existing.isExpanded())
        self.assertTrue(any(existing.child(i).data(0, Qt.ItemDataRole.UserRole) == (song['id'], 'existing')
                            for i in range(existing.childCount())))
        from app.storage import load_catalog
        self.assertEqual(load_catalog(self.window.workspace, song['id']), catalog)

    def test_batch_skips_existing_result_and_downloads_next_song(self):
        self.populate(2)
        row = dict(id=1, trackName='Song 0', artistName='Singer', duration=100,
                   syncedLyrics='[00:01]hello', plainLyrics='hello')
        store_candidate(self.window.workspace, 's0', row, {})
        reply = Reply(200, row | {'id': 2, 'trackName': 'Song 1'})
        self.window.lyric_lookup.network = Mock()
        self.window.lyric_lookup.network.get.return_value = reply
        self.window._submit(['s0', 's1'], 'lyrics')
        self.app.processEvents()
        self.assertEqual(self.window.active_op['song_id'], 's1')
        reply.finished.emit()
        self.app.processEvents()
        self.assertIsNone(self.window.active_op)
        self.assertEqual(self.window.batches[self.window.last_batch]['counts'], {'skipped': 1, 'success': 1})
        self.assertTrue(has_lrclib(self.window.workspace, 's1'))
        self.assertIn('🌐', self.window.tree.topLevelItem(1).text(2))

    def test_download_preserves_local_source_and_offsets(self):
        row = dict(id=1, syncedLyrics='[00:01]downloaded', plainLyrics='downloaded')
        folder = assets_dir(self.window.workspace, 'song')
        folder.mkdir(parents=True)
        (folder / 'local.lrc').write_text('[00:02]local untouched', encoding='utf-8')
        save_lyric_state(self.window.workspace, 'song', {'source': 'local', 'offsets': {'fx': 200}})
        store_candidate(self.window.workspace, 'song', row, {})
        state = load_lyric_state(self.window.workspace, 'song')
        self.assertEqual(state['source'], 'local')
        self.assertEqual(state['offsets'], {'fx': 200})
        self.assertEqual((folder / 'local.lrc').read_text(), '[00:02]local untouched')

    def karaoke_fixture(self):
        self.window.player = Mock()
        self.window.player.playbackState.return_value = QMediaPlayer.PlaybackState.StoppedState
        self.window.microphone = Mock()
        self.window.microphone.is_recording = False
        view = KaraokeWindow(self.window)
        self.window.karaoke_window = view
        view.show()
        self.app.processEvents()
        self.addCleanup(view.deleteLater)
        return view

    def test_audio_preferences_survive_close_restart_and_unavailable_guide(self):
        view = self.karaoke_fixture()
        self.assertTrue(view.monitor_checkbox.isChecked())
        self.assertFalse(view.guide_checkbox.isChecked())
        view.set_guide_available(True)
        view.guide_checkbox.click()
        view.music_volume.setValue(63)
        view.guide_volume.setValue(21)
        view.monitor_volume.setValue(37)
        view.monitor_checkbox.click()
        view.set_guide_available(False)
        self.assertTrue(self.window.audio_preferences['guide_enabled'])
        view.set_guide_available(True)
        self.assertTrue(view.guide_checkbox.isChecked())
        view.close()
        self.window.close()
        expected = dict(guide_enabled=True, monitor_enabled=False,
                        music_volume=63, guide_volume=21, monitor_volume=37)
        self.assertEqual(self.settings['singing_preferences'], expected)
        other = ConsumerWindow()
        try:
            self.assertEqual(other.audio_preferences, expected)
            self.assertEqual(other.sing_volume.value(), 63)
            self.assertAlmostEqual(other.guide_audio.volume(), .21, places=5)
            self.assertAlmostEqual(other.microphone.monitor_volume, .37)
            self.assertIsNone(other.microphone.source)
        finally:
            other.close()
            other.deleteLater()

    def test_monitor_failure_does_not_overwrite_desired_preference(self):
        view = self.karaoke_fixture()
        self.window.microphone.set_monitor.side_effect = [RuntimeError('unsupported'), None]
        with patch('PySide6.QtWidgets.QMessageBox.warning'):
            view._monitor_changed(True)
        self.assertFalse(view.monitor_checkbox.isChecked())
        self.assertTrue(self.window.audio_preferences['monitor_enabled'])
        view.close()

    def test_volume_changes_are_saved_automatically_without_closing(self):
        with patch('app.consumer.save_settings') as save:
            self.window._set_singing_volume(50)
            self.window._set_singing_volume(61)
            self.window._set_guide_volume(20)
            save.assert_not_called()
            QTest.qWait(350)
            save.assert_called_once()
            prefs = save.call_args.args[0]['singing_preferences']
            self.assertEqual(prefs['music_volume'], 61)
            self.assertEqual(prefs['guide_volume'], 20)

    def test_custom_thumbnail_reappears_after_library_reload(self):
        from PySide6.QtGui import QImage, QColor
        from app.song_images import save_thumbnail, selected_thumbnail
        self.populate(1)
        path = self.root / 'picture.png'
        image = QImage(100, 60, QImage.Format.Format_RGB32)
        image.fill(QColor('red'))
        image.save(str(path))
        save_thumbnail(self.window.workspace, 's0', path)
        self.window.add_to_queue('s0', 'original')
        self.window.refresh_tree()
        expected = QColor('red').rgb()
        self.assertEqual(self.window.tree.topLevelItem(0).icon(0).pixmap(48).toImage().pixel(20, 10), expected)
        self.assertEqual(self.window.queue.item(0).icon().pixmap(48).toImage().pixel(20, 10), expected)
        self.window.song_map['s0']['cover_path'] = None
        self.window.refresh_tree()
        self.window.refresh_queue_labels()
        self.assertIsNotNone(selected_thumbnail(self.window.workspace, 's0'))
        self.assertEqual(self.window.song_thumbnail(self.window.song_map['s0']), selected_thumbnail(self.window.workspace, 's0'))

    def test_plain_lyrics_preview_and_vocal_pauses(self):
        import numpy as np
        view = self.karaoke_fixture()
        player = self.window.player
        player.position.return_value = 0
        player.duration.return_value = 20000
        view.set_song('Song', '\n'.join(f'Line {i}' for i in range(11)), [], 20000)
        self.assertEqual(view.canvas.progress, 0)
        self.assertEqual(view.canvas.visible_line_indices(), [0, 1, 2, 3, 4])
        view.set_activity(np.array([0, 5, 10, 15, 20]), np.array([0, 0, 5, 5, 10]))
        player.position.return_value = 5000
        view.update_progress()
        self.assertEqual(view.canvas.progress, 0)
        player.position.return_value = 10000
        view.update_progress()
        self.assertEqual(view.canvas.progress, 5)
        self.assertEqual(view.canvas.visible_line_indices(), [4, 5, 6, 7, 8])
        player.position.return_value = 14000
        view.update_progress()
        self.assertEqual(view.canvas.progress, 5)
        view.canvas.grab()
        view.close()

    def test_late_vocal_analysis_preserves_plain_lyric_position(self):
        import numpy as np
        view = self.karaoke_fixture()
        self.window.player.position.return_value = 4000
        self.window.player.duration.return_value = 20000
        view.set_song('Song', '\n'.join(f'Line {i}' for i in range(11)), [], 20000)
        before = view.canvas.progress
        view.set_activity(np.array([0, 5, 10, 15, 20]), np.array([0, 0, 5, 5, 10]))
        self.assertEqual(view.canvas.progress, before)
        view.close()

    def test_playlist_navigation_starts_karaoke_session_from_idle(self):
        self.populate(3)
        for i in range(3):
            self.window._append_queue({'song_id': f's{i}', 'variant_id': 'original', 'label': f'Song {i}'})
        self.window.microphone = Mock()
        self.window.exclusive_player = Mock()
        self.window._sync_karaoke_song = Mock()
        with patch('app.consumer.KaraokeWindow') as factory:
            factory.return_value.isVisible.return_value = True
            self.window.queue.setCurrentRow(1)
            self.window.previous_song()
            self.assertEqual(self.window.queue.currentRow(), 0)
            self.assertIs(self.window.player, self.window.exclusive_player)
            factory.return_value.show.assert_called_once()
            self.window.microphone.start.assert_called_once()
            self.window.next_song()
            self.assertEqual(self.window.queue.currentRow(), 1)
            self.window.microphone.start.assert_called_once()
            self.window.stop_playback()
            factory.return_value.hide()
            factory.return_value.isVisible.return_value = False
            self.window.next_song()
            self.assertEqual(self.window.queue.currentRow(), 2)
            self.assertEqual(factory.return_value.show.call_count, 2)
            self.assertEqual(self.window.microphone.start.call_count, 2)
            self.window.next_song()
            self.assertIsNone(self.window.playing_item)
            self.assertEqual(self.window.microphone.start.call_count, 2)
        self.window.karaoke_window = None

    def test_compact_karaoke_controls_resize_and_volume_sync(self):
        view = self.karaoke_fixture()
        self.assertEqual(view.controls_panel.height(), 54)
        self.assertTrue(view.controls_panel.isVisible())
        self.assertTrue(view.mic_panel.isVisible())
        self.assertTrue(view.monitor_checkbox.isVisible())
        self.assertTrue(view.monitor_volume.isVisible())
        self.assertEqual(view.music_volume.width(), 80)
        self.assertIs(view.mic_panel.parentWidget(), view.controls_panel)
        self.assertLess(view.guide_bar_layout.indexOf(view.guide_checkbox),
                        view.guide_bar_layout.indexOf(view.mic_panel))
        self.assertFalse(view.guide_volume.isEnabled())
        view.music_volume.setValue(55)
        self.assertEqual(self.window.sing_volume.value(), 55)
        self.assertAlmostEqual(self.window.audio.volume(), .55, places=2)
        view.resize(720, 600)
        self.app.processEvents()
        self.assertTrue(view.guide_menu.menuAction().isVisible())
        self.assertIs(view.guide_volume.parentWidget(), view.guide_menu_widget)
        self.assertTrue(view.monitor_checkbox.isVisible())
        self.assertTrue(view.monitor_volume.isVisible())
        self.assertLessEqual(view.mic_panel.geometry().right(), view.controls_panel.width())
        view.resize(1100, 680)
        self.app.processEvents()
        self.assertFalse(view.guide_menu.menuAction().isVisible())
        self.assertIs(view.guide_volume.parentWidget(), view.controls_panel)
        view.close()

    def test_karaoke_header_lyric_format_changes_with_song(self):
        from app.lyrics import parse_lrc
        view = self.karaoke_fixture()
        self.window.player.position.return_value = 0
        self.window.player.duration.return_value = 20000
        view.set_song('Plain', 'First\nSecond', [], 20000)
        self.assertEqual(view.lyrics_status.text(), '純文字歌詞')
        view.set_song('Synced', '', [], 20000, timeline=parse_lrc('[00:01]First\n[00:03]Second'))
        self.assertEqual(view.lyrics_status.text(), 'LRC 同步歌詞')
        view.set_song('Empty', '', [], 20000)
        self.assertEqual(view.lyrics_status.text(), '無歌詞')
        self.assertTrue(view.lyrics_status.isVisible())
        view.close()

    def test_record_icon_distinguishes_prepared_actual_and_paused(self):
        view = self.karaoke_fixture()
        self.assertFalse(self.window.record_checkbox.isChecked())
        self.assertIn('未錄音', view.record_status.toolTip())
        self.window.record_checkbox.click()
        self.assertIn('播放時錄音', self.window.record_checkbox.text())
        self.assertIn('準備', view.record_status.toolTip())
        self.window.player.playbackState.return_value = QMediaPlayer.PlaybackState.PlayingState
        view._update_controls_state()
        self.assertNotIn('正在錄音', view.record_status.toolTip())
        self.window.microphone.is_recording = True
        view._update_controls_state()
        self.assertIn('正在錄音', view.record_status.toolTip())
        self.assertEqual(view.play_button.toolTip(), '暫停')
        self.window.player.playbackState.return_value = QMediaPlayer.PlaybackState.PausedState
        view._update_controls_state()
        self.assertIn('暫停', view.record_status.toolTip())
        self.window.record_checkbox.setEnabled(False)
        self.window.record_checkbox.click()
        self.assertTrue(self.window.record_checkbox.isChecked())
        view.close()

    def test_guide_toggle_enables_short_slider_without_changing_saved_gain(self):
        view = self.karaoke_fixture()
        self.window.set_guide_vocal = Mock()
        view.guide_checkbox.setEnabled(True)
        view.guide_checkbox.click()
        self.assertTrue(view.guide_volume.isEnabled())
        self.window.set_guide_vocal.assert_called_with(True)
        view.guide_volume.setValue(18)
        view.guide_checkbox.click()
        self.assertFalse(view.guide_volume.isEnabled())
        self.assertEqual(view.guide_volume.value(), 18)
        self.window.set_guide_vocal.assert_called_with(False)
        view.close()

    def test_save_background_settings_in_exclusive_mode(self):
        source = self.root / 'input'
        source.mkdir()
        self.window.source_field.setText(str(source))
        self.window.output_field.setText(str(self.root / 'outputs'))
        self.window.background_mode.setCurrentIndex(self.window.background_mode.findData('song_first'))
        self.window.image_receiver_percent.setValue(27)
        self.window.singing_audio_mode.setCurrentIndex(self.window.singing_audio_mode.findData('exclusive'))
        with patch('app.consumer.save_settings') as save, patch('app.consumer.test_exclusive_device') as probe, \
                patch.object(self.window, 'scan_library'), patch('app.consumer.QMessageBox.warning') as warning, \
                patch('app.consumer.QMessageBox.information') as message:
            self.window.save_settings()
            save.assert_called_once()
            self.assertEqual(save.call_args.args[0]['background_mode'], 'song_first')
            self.assertEqual(save.call_args.args[0]['image_receiver_percent'], 27)
            self.assertEqual(save.call_args.args[0]['singing_audio_mode'], 'exclusive')
            self.assertEqual(self.window.info.text(), '設定已儲存')
            probe.assert_not_called()
            warning.assert_not_called()
            message.assert_called_once_with(self.window, '設定', '設定已儲存')

    def test_three_languages_keep_style_ids_and_prompts(self):
        from app.i18n import configure
        from app.style_presets import PRESETS
        try:
            for code, tab, lyric in [('zh_TW', '唱歌', '純文字歌詞'),
                                     ('en', 'Sing', 'Plain-text lyrics'), ('ja', '歌う', 'テキスト歌詞')]:
                self.settings['ui_language'] = code
                other = ConsumerWindow()
                try:
                    self.assertEqual(other.centralWidget().tabText(0), tab)
                    self.assertTrue(other.background_motion.isChecked())
                    other.style.setCurrentIndex(other.style.findData('Lounge Jazz'))
                    request = other._request()
                    self.assertEqual(request['caption'], PRESETS['Lounge Jazz'])
                    self.assertEqual(request['tag'], 'Lounge Jazz')
                    self.assertEqual(request['style_label'], 'Lounge Jazz')
                    other._remember_version_options()
                    self.assertEqual(other.settings['version_options']['style'], 'Lounge Jazz')
                    other.style.setCurrentIndex(other.style.findData('custom'))
                    other.custom_style.setText('My original description 我的描述')
                    self.assertEqual(other._request()['caption'], 'My original description 我的描述')
                    other.player = Mock()
                    other.player.position.return_value = 0
                    other.player.duration.return_value = 20000
                    other.player.playbackState.return_value = QMediaPlayer.PlaybackState.StoppedState
                    view = KaraokeWindow(other)
                    other.karaoke_window = view
                    view.set_song('Original title 原名', 'Original lyrics 原詞', [], 20000)
                    self.assertEqual(view.full_title, 'Original title 原名')
                    self.assertEqual(view.canvas.lines, ['Original lyrics 原詞'])
                    self.assertEqual(view.lyrics_status.text(), lyric)
                    view.deleteLater()
                    other.karaoke_window = None
                finally:
                    other.close()
                    other.deleteLater()
        finally:
            configure('zh_TW')

    def test_language_save_requires_restart_and_saves_motion(self):
        from app.i18n import language
        source = self.root / 'input'
        source.mkdir()
        self.window.source_field.setText(str(source))
        self.window.ui_language.setCurrentIndex(self.window.ui_language.findData('ja'))
        self.window.background_motion.setChecked(False)
        with patch.object(self.window, 'scan_library'), patch('app.consumer.save_settings') as save:
            self.window.save_settings()
            self.assertEqual(save.call_args.args[0]['ui_language'], 'ja')
            self.assertFalse(save.call_args.args[0]['background_motion'])
            self.assertEqual(language(), 'zh_TW')
            self.assertIn('重新啟動', self.window.info.text())
            self.assertEqual(self.window.centralWidget().tabText(0), '唱歌')

    def test_standard_dialog_buttons_and_version_column_fit_three_languages(self):
        from app.i18n import configure
        try:
            for code in ('zh_TW', 'en', 'ja'):
                self.settings['ui_language'] = code
                other = ConsumerWindow()
                try:
                    box = QMessageBox(other)
                    box.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
                    self.assertTrue(box.button(QMessageBox.StandardButton.Ok).text().strip())
                    self.assertTrue(box.button(QMessageBox.StandardButton.Cancel).text().strip())
                    self.assertIn(box.button(QMessageBox.StandardButton.Ok).text().replace('&', ''), ('OK', '確定'))
                    audio = self.root / 'test.mp3'
                    audio.touch()
                    song = dict(id='s0', title='Song', path=str(audio), available=True)
                    other.songs = [song]
                    other.song_map = {'s0': song}
                    other.refresh_tree()
                    other.centralWidget().setCurrentIndex(1)
                    other.show()
                    self.app.processEvents()
                    row = other.tree.itemWidget(other.tree.topLevelItem(0), 5)
                    button = row.findChild(QPushButton)
                    self.assertGreaterEqual(button.width(), button.minimumSizeHint().width())
                    self.assertLessEqual(button.geometry().right(), row.width())
                    self.assertGreaterEqual(other.tree.columnWidth(5), row.minimumSizeHint().width())
                    other.version_dialog.show()
                    self.app.processEvents()
                    for button in other.version_dialog.findChildren(QPushButton):
                        if button.isVisible():
                            self.assertGreaterEqual(button.width(), button.minimumSizeHint().width())
                    other.version_dialog.hide()
                    box.deleteLater()
                finally:
                    other.close()
                    other.deleteLater()
        finally:
            configure('zh_TW')

    def test_top_right_language_saves_immediately_and_prompts_restart(self):
        from app.i18n import language
        corner = self.window.centralWidget().cornerWidget(Qt.Corner.TopRightCorner)
        self.assertTrue(corner.isAncestorOf(self.window.ui_language))
        with patch('app.consumer.save_settings') as save, patch('app.consumer.QMessageBox.information') as message:
            self.window.ui_language.setCurrentIndex(self.window.ui_language.findData('en'))
            save.assert_called_once()
            self.assertEqual(save.call_args.args[0]['ui_language'], 'en')
            self.assertIn('重新啟動', message.call_args.args[2])
            self.assertEqual(language(), 'zh_TW')
            self.assertEqual(self.window.centralWidget().tabText(0), '唱歌')

    def test_language_save_failure_restores_selection(self):
        with patch('app.consumer.save_settings', side_effect=OSError('Disk unavailable')), \
                patch('app.consumer.QMessageBox.warning') as warning, \
                patch('app.consumer.QMessageBox.information') as message:
            self.window.ui_language.setCurrentIndex(self.window.ui_language.findData('ja'))
            self.assertEqual(self.window.ui_language.currentData(), 'zh_TW')
            self.assertNotEqual(self.window.settings.get('ui_language'), 'ja')
            warning.assert_called_once()
            message.assert_not_called()

    def test_saved_playlist_generated_labels_translate_without_data_changes(self):
        from app.i18n import configure
        self.populate(1)
        generated = {'song_id': 's0', 'variant_id': 'original', 'label': 'Song 0 · 原曲 — Singer'}
        custom = dict(generated, label='My favorite 原曲')
        try:
            configure('en')
            self.window._append_queue(generated)
            self.window._append_queue(custom)
            self.assertEqual(self.window.queue.item(0).text(), 'Song 0 · Original — Singer')
            self.assertEqual(self.window.queue.item(1).text(), 'My favorite 原曲')
            self.assertEqual(self.window.queue_items(), [generated, custom])
            configure('ja')
            self.window.refresh_queue_labels()
            self.assertEqual(self.window.queue.item(0).text(), 'Song 0 · 原曲 — Singer')
            self.assertEqual(self.window.queue_items(), [generated, custom])
        finally:
            configure('zh_TW')

    def test_background_shuffle_covers_each_round_without_adjacent_repeats(self):
        view = self.karaoke_fixture()
        self.window.player.position.return_value = 0
        self.window.player.duration.return_value = 20000
        images = [self.root / f'{i}.png' for i in range(3)]
        orders = iter([(0, 1, 2), (2, 1, 0), (0, 2, 1)])
        def shuffle(indices):
            indices[:] = next(orders)
        with patch('app.karaoke_window.random.shuffle', side_effect=shuffle) as shuffled:
            view.set_song('Song', '', images + [images[0]], 20000)
            sequence = [view.image_index]
            for _ in range(8):
                view.next_background()
                sequence.append(view.image_index)
            self.assertEqual(shuffled.call_count, 3)
            self.assertEqual(view.images, images)
            for start in (0, 3, 6):
                self.assertEqual(set(sequence[start:start + 3]), {0, 1, 2})
            self.assertTrue(all(a != b for a, b in zip(sequence, sequence[1:])))
        view.set_song('Single', '', images[:1], 20000)
        view.next_background()
        self.assertEqual(view.image_index, 0)
        view.set_song('Empty', '', [], 20000)
        view.next_background()
        self.assertIsNone(view.canvas.background)
        self.assertFalse(view.motion_timer.isActive())
        view.close()

    def test_background_motion_freezes_and_reuses_pixmap_cache(self):
        from PySide6.QtGui import QPixmap, QColor
        from app.karaoke_window import motion_rect
        view = self.karaoke_fixture()
        player = self.window.player
        player.position.return_value = 0
        player.duration.return_value = 20000
        pix = QPixmap(1200, 800)
        pix.fill(QColor('blue'))
        path = self.root / 'background.png'
        pix.save(str(path))
        view.set_song('Song', 'First\nSecond', [path], 20000)
        self.assertFalse(view.motion_timer.isActive())
        player.playbackState.return_value = QMediaPlayer.PlaybackState.PlayingState
        view.update_progress()
        self.assertEqual(view.motion_timer.interval(), 100)
        self.assertTrue(view.motion_timer.isActive())
        view.canvas.grab()
        cached = view.canvas._scaled_background.cacheKey()
        view.canvas.motion_ms = 6000
        view.canvas.grab()
        self.assertEqual(view.canvas._scaled_background.cacheKey(), cached)
        for w, h in [(640, 360), (1920, 1080)]:
            for iw, ih in [(w, h), (w, h / 3), (w / 3, h)]:
                for ms in [0, 1000, 6000, 12000]:
                    rect = motion_rect(w, h, iw, ih, ms)
                    self.assertGreaterEqual(rect.left(), -0.001)
                    self.assertGreaterEqual(rect.top(), -0.001)
                    self.assertLessEqual(rect.right(), w + 0.001)
                    self.assertLessEqual(rect.bottom(), h + 0.001)
        player.playbackState.return_value = QMediaPlayer.PlaybackState.PausedState
        view.update_progress()
        self.assertFalse(view.motion_timer.isActive())
        self.assertFalse(view.background_timer.isActive())
        position = view.canvas.motion_ms
        player.playbackState.return_value = QMediaPlayer.PlaybackState.PlayingState
        view.update_progress()
        self.assertEqual(view.canvas.motion_ms, position)
        view.hide()
        self.assertFalse(view.motion_timer.isActive())
        view.show()
        self.assertTrue(view.motion_timer.isActive())
        view.set_background_motion(False)
        self.assertFalse(view.motion_timer.isActive())
        view.set_song('No images', '', [], 20000)
        view.set_background_motion(True)
        self.assertFalse(view.motion_timer.isActive())
        view.close()

    def test_output_test_uses_exclusive_device(self):
        device = Mock()
        device.isNull.return_value = False
        device.description.return_value = 'Test DAC'
        self.window.audio_device.blockSignals(True)
        self.window.audio_device.addItem('Test DAC', device)
        self.window.audio_device.setCurrentIndex(self.window.audio_device.count() - 1)
        self.window.singing_audio_mode.setCurrentIndex(self.window.singing_audio_mode.findData('exclusive'))
        with patch('app.consumer.test_exclusive_device', return_value=(0, 48000, 'float32')) as probe:
            self.window.test_audio_output()
            probe.assert_called_once_with(device, 'output')
            self.assertIn('獨占播放已開啟', self.window.device_test_status.text())
        self.window.audio_device.removeItem(self.window.audio_device.currentIndex())
        self.window.audio_device.blockSignals(False)

    def test_background_modes_cover_and_empty_song_images(self):
        self.populate(1)
        self.assertEqual(self.window.background_mode.currentData(), 'combined')
        self.assertEqual(self.window.background_mode.itemText(0), '只用歌曲影像')
        local = assets_dir(self.window.workspace, 's0') / 'images'
        shared = self.window.workspace / 'shared_images'
        for folder in (local, shared):
            folder.mkdir(parents=True)
        local_image, shared_image = local / 'song.png', shared / 'common.jpg'
        local_image.touch()
        shared_image.touch()
        from app.karaoke_assets import shared_images_dir
        shared_images_dir(self.window.workspace)
        from app.karaoke_assets import image_files
        for default in image_files(shared):
            if default != shared_image:
                default.unlink()
        self.window.karaoke_window = Mock()
        item = dict(song_id='s0', variant_id='original', label='Song')
        with patch('app.consumer.cache_background_cover', return_value=None):
            for mode, expected in [('song_first', [local_image]), ('combined', [local_image, shared_image]), ('shared_only', [shared_image])]:
                self.window.settings['background_mode'] = mode
                self.window._sync_karaoke_song(item)
                self.assertEqual(self.window.karaoke_window.set_song.call_args.args[2], expected)
            local_image.unlink()
            self.window.settings['background_mode'] = 'song_first'
            self.window._sync_karaoke_song(item)
            self.assertEqual(self.window.karaoke_window.set_song.call_args.args[2], [])
            shared_image.unlink()
            self.window.settings['background_mode'] = 'shared_only'
            self.window._sync_karaoke_song(item)
            self.assertEqual(self.window.karaoke_window.set_song.call_args.args[2], [])
        local_image.touch()
        shared_image.touch()
        cover = self.root / 'cover.jpg'
        with patch('app.consumer.cache_background_cover', return_value=cover):
            for mode, expected in [('song_first', [local_image, cover]),
                                   ('combined', [local_image, shared_image, cover]),
                                   ('shared_only', [shared_image])]:
                self.window.settings['background_mode'] = mode
                self.window._sync_karaoke_song(item)
                self.assertEqual(self.window.karaoke_window.set_song.call_args.args[2], expected)
            local_image.unlink()
            self.window.settings['background_mode'] = 'song_first'
            self.window._sync_karaoke_song(item)
            self.assertEqual(self.window.karaoke_window.set_song.call_args.args[2], [cover])

    def test_plain_lyrics_start_independent_vocal_analysis(self):
        self.populate(1)
        folder = assets_dir(self.window.workspace, 's0')
        folder.mkdir(parents=True)
        (folder / 'lyrics.txt').write_text('plain lyric', encoding='utf-8')
        vocal = self.root / 'vocals.wav'
        vocal.touch()
        self.window.karaoke_window = Mock()
        with patch.object(self.window, '_karaoke_vocal_path', return_value=vocal), patch('app.consumer.ActivityTask') as task:
            self.window._sync_karaoke_song(dict(song_id='s0', variant_id='original', label='Song'))
            task.assert_called_once()
            self.assertEqual(task.call_args.args[0], vocal)
            task.return_value.start.assert_called_once()
            self.assertIsNone(self.window.karaoke_window.set_song.call_args.kwargs['timeline'])


if __name__ == '__main__':
    unittest.main()


    def test_reload_removes_missing_song_and_restores_returned_file(self):
        source = self.root / 'music'
        source.mkdir()
        for title in ('A', 'B', 'C'):
            (source / (title + '.mp3')).touch()
        self.window.settings['source_dir'] = str(source)
        with patch('app.storage.data_dir', return_value=self.root), \
             patch('app.consumer.QThreadPool.globalInstance') as pool:
            pool.return_value.start.side_effect = lambda task: task.run()
            self.window.reload_library.click()
            song = next(row for row in self.window.songs if row['title'] == 'B')
            tree = self.window.tree
            tree.setCurrentItem(tree.topLevelItem(1))
            self.window._append_queue(dict(song_id=song['id'], variant_id='original', label='B'))
            generated = self.window.workspace / 'songs' / song['id'] / 'assets/lyrics.txt'
            generated.parent.mkdir(parents=True)
            generated.write_text('keep')
            original = Path(song['path'])
            moved = self.root / 'moved.mp3'
            original.rename(moved)
            self.window.reload_library.click()
            self.assertEqual([row['title'] for row in self.window.songs], ['A', 'C'])
            self.assertEqual(tree.currentItem().data(0, Qt.ItemDataRole.UserRole), self.window.songs[1]['id'])
            self.assertIn('無法使用', self.window.queue.item(0).text())
            self.assertEqual(generated.read_text(), 'keep')
            moved.rename(original)
            self.window.reload_library.click()
            self.assertIn(song['id'], self.window.song_map)
            self.assertNotIn('無法使用', self.window.queue.item(0).text())
