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
from PySide6.QtCore import QByteArray, QObject, QPoint, QTimer, Qt, Signal, QUrl
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
    def test_bottom_speed_buttons_sync_fine_slider_and_stop_at_limits(self):
        view = self.karaoke_fixture()
        self.window.set_live_effects = Mock()
        view.speed_up.click()
        self.assertEqual(view.live_speed.value(), 110)
        self.assertEqual(view.bottom_speed_label.text(), '1.10×')
        view.live_speed.setValue(113)
        view.speed_down.click()
        self.assertEqual(view.live_speed.value(), 103)
        view.live_speed.setValue(130)
        self.assertFalse(view.speed_up.isEnabled())
        view.live_speed.setValue(70)
        self.assertFalse(view.speed_down.isEnabled())
        self.assertIs(view.live_key.parentWidget(), view.bottom_effects)
        view.resize(640, 480)
        self.app.processEvents()
        self.assertEqual(view.width(), 640)
        self.assertEqual(view.controls_panel.height(), 94)
        view.resize(1100, 680)
        self.app.processEvents()
        self.assertEqual(view.controls_panel.height(), 54)
        QTest.qWait(230)
        self.window.set_live_effects.assert_called_once_with(.7, 0)
        view.reset_live_effects(apply=False)
        self.assertTrue(view.speed_down.isEnabled())
        self.assertTrue(view.speed_up.isEnabled())
        self.assertEqual(view.bottom_speed_label.text(), '1.00×')
        view.close()

    def test_soundtouch_management_is_independent_of_ai_model_selection(self):
        from app.models_dialog import ModelsDialog, Preparation
        with patch('app.models_dialog.soundtouch.ready', return_value=False):
            dialog = ModelsDialog(self.window)
        self.assertIn('尚未準備', dialog.soundtouch_status.text())
        dialog.separation.setChecked(False)
        dialog.styling.setChecked(False)
        self.window.active_op = None
        self.window.playing_item = None
        with patch('app.models_dialog.QMessageBox.question', return_value=QMessageBox.StandardButton.Yes), \
                patch('app.models_dialog.Preparation') as worker:
            dialog.soundtouch_download.click()
            self.assertEqual(worker.call_args.args[2], 'soundtouch')
            worker.return_value.start.assert_called_once()
            self.assertFalse(dialog.soundtouch_download.isEnabled())
            dialog.on_finished()
        with patch('app.models_dialog.soundtouch.ready', return_value=True):
            dialog.refresh()
        self.assertIn('已準備', dialog.soundtouch_status.text())
        self.assertTrue(dialog.soundtouch_download.isEnabled())
        preparation = Preparation({}, [], 'soundtouch')
        results = []
        preparation.result.connect(lambda *args: results.append(args))
        with patch('app.models_dialog.soundtouch.prepare') as download, \
                patch('app.models_dialog.ModelStore') as models:
            preparation.run()
            download.assert_called_once()
            models.assert_not_called()
        self.assertEqual(results, [('success', '')])
        with patch('app.models_dialog.soundtouch.prepare', side_effect=ValueError('checksum mismatch')):
            preparation.run()
        self.assertEqual(results[-1], ('failed', 'checksum mismatch'))
        dialog.close()

    def test_live_effect_controls_are_debounced_and_reset_without_reprocessing_versions(self):
        view = self.karaoke_fixture()
        self.window.set_live_effects = Mock()
        view.live_speed.setValue(70)
        view.live_key.setValue(6)
        self.window.set_live_effects.assert_not_called()
        QTest.qWait(230)
        self.window.set_live_effects.assert_called_once_with(.7, 6)
        view.reset_live_effects()
        self.window.set_live_effects.assert_called_with(1., 0)
        self.assertEqual(view.live_speed.maximum(), 130)
        self.assertEqual(view.live_key.minimum(), -6)
        view.close()

    def test_recording_preview_prefers_the_backing_actually_played(self):
        played = self.root / 'played_backing.wav'
        played.write_bytes(b'recorded backing fixture')
        self.window.version_path = Mock()
        row = dict(song_id='song', variant_id='v', played_backing_path=str(played))
        self.assertEqual(self.window._recorded_backing(row), played)
        self.window.version_path.assert_not_called()

    def test_missing_transformed_recording_backing_does_not_play_wrong_tempo(self):
        self.window.version_path = Mock()
        row = dict(song_id='song', variant_id='v', played_backing='played_backing.wav')
        self.assertIsNone(self.window._recorded_backing(row))
        self.window.version_path.assert_not_called()

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
        self.settings = {'workspace': str(self.root / 'outputs'), 'source_dir': '', 'background_depth_motion': False}
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

    def test_recording_group_toggles_on_single_click_and_mv_tab_follows_selection(self):
        import numpy as np
        import soundfile as sf
        from app.recordings import recordings_dir
        self.populate(2)
        take = recordings_dir(self.window.workspace, 's1') / 'r1'
        take.mkdir(parents=True)
        sf.write(str(take / 'microphone.wav'), np.zeros((4410, 2), dtype=np.float32), 44100)
        (take / 'session.json').write_text(json.dumps(dict(song_id='s1', session_id='r1', profile='default',
            started_at='2026-01-01T00:00:00', duration_ms=100, variant_id='original')), encoding='utf-8')
        self.window.refresh_tree()
        root = self.window.tree.topLevelItem(1)
        root.setExpanded(True)
        group = root.child(root.childCount() - 1)
        self.assertEqual(group.data(0, Qt.ItemDataRole.UserRole), ('recordings', 's1'))
        self.assertFalse(group.isExpanded())
        self.window._tree_clicked(group, 0)
        self.assertTrue(group.isExpanded())
        self.window._tree_clicked(group, 0)
        self.assertFalse(group.isExpanded())
        self.assertFalse(self.window.tree.expandsOnDoubleClick())
        tabs = self.window.centralWidget()
        self.assertEqual(tabs.tabText(2), 'MV 匯出')
        self.window.tree.setCurrentItem(root)
        tabs.setCurrentWidget(self.window.mv_page)
        self.assertEqual(self.window.mv.song_id, 's1')
        self.assertEqual(self.window.mv_song.count(), 2)

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

    def test_depth_preprocess_uses_selected_backgrounds_and_continues_after_failure(self):
        self.populate(1)
        first, second = self.root / 'first.png', self.root / 'second.png'
        self.window.active_op = {'song_id': 's0', 'mode': 'depth', 'request': {}}
        with patch.object(self.window, '_background_images', return_value=[first, second]), \
             patch.object(self.window, 'runtime', return_value=self.root / 'python.exe'), \
             patch('app.consumer.DepthMotionTask') as task_class, \
             patch.object(self.window, '_finish_operation') as finish:
            self.window._start_depth_preprocess('s0')
            self.assertEqual(task_class.call_args.args[0], first)
            self.window._depth_preprocess_finished(first, 'failed image')
            QTest.qWait(20)
            self.assertEqual(task_class.call_args.args[0], second)
            self.window._depth_preprocess_finished()
            QTest.qWait(20)
            self.assertFalse(finish.call_args.args[0])
            self.assertIn('完成 1／2', finish.call_args.args[1])
            self.assertIn('first.png', finish.call_args.args[1])
        self.window.active_op = None
        self.window.depth_batch_task = None

    def test_depth_preprocess_and_playback_share_background_selection(self):
        from app.karaoke_assets import song_images_dir
        self.populate(1)
        local_dir = song_images_dir(self.window.workspace, 's0')
        shared_dir = self.root / 'shared'
        local_dir.mkdir(parents=True)
        shared_dir.mkdir()
        local, shared, cover = local_dir / 'song.png', shared_dir / 'shared.png', self.root / 'cover.png'
        for path in (local, shared, cover):
            path.touch()
        with patch('app.consumer.shared_images_dir', return_value=shared_dir), \
             patch('app.consumer.cache_background_cover', return_value=cover):
            self.window.settings['background_mode'] = 'combined'
            self.assertEqual(self.window._background_images('s0'), [local, shared, cover])
            self.window.settings['background_mode'] = 'song_first'
            self.assertEqual(self.window._background_images('s0'), [local, cover])
            self.window.settings['background_mode'] = 'shared_only'
            self.assertEqual(self.window._background_images('s0'), [shared])

    def test_depth_preprocess_requires_stopped_playback(self):
        self.window.playing_item = {'song_id': 's0'}
        with patch.object(self.window, '_submit') as submit:
            self.window.batch_depth_motion()
            submit.assert_not_called()
        self.window.playing_item = None

    def test_song_context_menu_prepares_clicked_song(self):
        self.populate(1)
        item = self.window.tree.topLevelItem(0)
        point = self.window.tree.visualItemRect(item).center()
        with patch.object(self.window, 'prepare_depth_motion') as prepare:
            def trigger_action():
                menu = QApplication.activePopupWidget()
                action = next(action for action in menu.actions() if action.text() == '預處理背景動畫')
                action.trigger()
                menu.close()
            QTimer.singleShot(0, trigger_action)
            self.window._context_menu(point)
            prepare.assert_called_once_with(['s0'])

    def test_song_depth_icon_reports_partial_and_complete_cache(self):
        self.populate(1)
        (self.root / '.app_data/cache/depth-motion/index').mkdir(parents=True)
        images = [self.root / 'one.png', self.root / 'two.png']
        with patch('app.consumer.ROOT', self.root), \
             patch('app.model_store.ModelStore.ready', return_value=True), \
             patch.object(self.window, '_background_images', return_value=images), \
             patch('app.consumer.image_files', return_value=images), \
             patch('app.depth_cache.has_index', return_value=True), \
             patch('app.depth_cache.lookup', side_effect=lambda cache, image, model, settings: {} if image == images[0] else None):
            self.window.refresh_tree()
            item = self.window.tree.topLevelItem(0)
            from app.consumer import DEPTH_STATUS_ROLE
            self.assertEqual(item.data(1, DEPTH_STATUS_ROLE), 'partial')
            self.assertIn('1／2', item.toolTip(1))

    def test_strength_change_preserves_preprocessed_indicator(self):
        from app.consumer import DEPTH_STATUS_ROLE
        from app.depth_cache import remember
        self.populate(1)
        source = self.root / 'input'
        source.mkdir()
        self.window.source_field.setText(str(source))
        image = self.root / 'background.png'
        image.touch()
        model = self.root / 'models/depth/Depth-Anything-V2-Small-hf'
        model.mkdir(parents=True)
        for name in ('config.json', 'preprocessor_config.json', 'model.safetensors'):
            (model / name).touch()
        cache = self.root / '.app_data/cache/depth-motion'
        cache.mkdir(parents=True)
        mask = cache / 'mask.png'
        mask.touch()
        remember(cache, image, model, self.window.settings, {'mask': str(mask)})
        with patch('app.consumer.ROOT', self.root), \
             patch('app.model_store.ModelStore.ready', return_value=True), \
             patch.object(self.window, '_background_images', return_value=[image]), \
             patch('app.consumer.image_files', return_value=[image]), \
             patch.object(self.window, 'scan_library'):
            self.window.refresh_tree()
            self.assertEqual(self.window.tree.topLevelItem(0).data(1, DEPTH_STATUS_ROLE), 'complete')
            self.window.depth_fields['strength'].setValue(25)
            self.window.save_settings()
            self.assertEqual(self.window.tree.topLevelItem(0).data(1, DEPTH_STATUS_ROLE), 'complete')

    def test_scrolling_does_not_change_depth_settings(self):
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QWheelEvent
        field = self.window.depth_fields['landscape_expand']
        original = field.value()
        event = QWheelEvent(QPointF(2, 2), QPointF(2, 2), QPoint(), QPoint(0, 120),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.ScrollUpdate, False)
        QApplication.sendEvent(field, event)
        self.assertEqual(field.value(), original)
        field.stepUp()
        self.assertEqual(field.value(), original + 1)

    def test_depth_indicator_is_small_and_after_status_text(self):
        from PySide6.QtCore import QRect
        from PySide6.QtGui import QFontMetrics, QImage, QPainter
        from PySide6.QtWidgets import QStyleOptionViewItem
        from app.consumer import DEPTH_STATUS_ROLE
        self.populate(1)
        item = self.window.tree.topLevelItem(0)
        item.setText(1, 'Ready')
        item.setData(1, DEPTH_STATUS_ROLE, 'complete')
        index = self.window.tree.indexFromItem(item, 1)
        option = QStyleOptionViewItem()
        option.initFrom(self.window.tree)
        option.rect = QRect(0, 0, 200, 30)
        surface = QImage(200, 30, QImage.Format.Format_ARGB32)
        surface.fill(Qt.GlobalColor.transparent)
        painter = QPainter(surface)
        marker = Mock()
        try:
            with patch('app.consumer.icon', return_value=marker):
                self.window.tree.itemDelegateForColumn(1).paint(painter, option, index)
            rect = marker.paint.call_args.args[1]
            self.assertEqual(rect.size().width(), 15)
            self.assertGreater(rect.left(), QFontMetrics(option.font).horizontalAdvance('Ready'))
            self.assertLessEqual(rect.right(), option.rect.right())
        finally:
            painter.end()

    def test_animation_settings_are_expandable_and_saved(self):
        source = self.root / 'input'
        source.mkdir()
        self.window.source_field.setText(str(source))
        self.window.show()
        self.assertFalse(self.window.background_animation_panel.isVisible())
        self.window.background_animation_toggle.click()
        self.window.centralWidget().setCurrentWidget(self.window.settings_page)
        self.app.processEvents()
        self.assertTrue(self.window.background_animation_panel.isVisible())
        self.window.depth_fields['strength'].setValue(62)
        self.window.depth_fields['person_expand'].setValue(20)
        self.window.depth_solid.setChecked(False)
        with patch('app.consumer.save_settings') as save, patch.object(self.window, 'scan_library'):
            self.window.save_settings()
            self.assertEqual(save.call_args.args[0]['depth_strength'], 62)
            self.assertEqual(save.call_args.args[0]['depth_person_expand'], 20)
            self.assertFalse(save.call_args.args[0]['depth_solid'])

    def test_animation_settings_reject_inverted_coverage(self):
        self.window.depth_fields['minimum'].setValue(80)
        self.window.depth_fields['maximum'].setValue(70)
        with patch('app.consumer.save_settings') as save, patch('app.consumer.QMessageBox.warning') as warning:
            self.window.save_settings()
            save.assert_not_called()
            warning.assert_called_once()

    def test_depth_animation_defaults_on_but_respects_saved_opt_out(self):
        settings = {'workspace': str(self.root / 'outputs'), 'source_dir': ''}
        with patch('app.consumer.load_settings', return_value=settings):
            other = ConsumerWindow()
            self.assertTrue(other.background_depth_motion.isChecked())
            other.close()
            other.deleteLater()
        self.assertFalse(self.window.background_depth_motion.isChecked())

    def test_cancel_depth_preprocess_stops_task(self):
        self.populate(1)
        self.window.active_op = {'song_id': 's0', 'mode': 'depth', 'request': {}}
        task = Mock()
        self.window.depth_batch_task = task
        self.window.depth_batch_images = [self.root / 'image.png']
        self.window.cancel_batch()
        task.cancel.assert_called_once()
        self.assertIsNone(self.window.depth_batch_task)
        self.assertFalse(self.window.depth_batch_images)

    def test_resume_playlist_duplicates_missing_and_empty(self):
        items = [dict(song_id='missing', variant_id='fx', label='Jazz')] * 2
        save_playlist(self.root / 'profiles' / 'default' / 'last_playlist.json', items)
        self.window._restore_last_queue()
        self.assertEqual(self.window.queue_items(), items)
        self.assertIn('無法使用', self.window.queue.item(0).text())
        self.assertIsNone(self.window.playing_item)
        self.window.close()
        self.assertEqual(load_playlist(self.root / 'profiles' / 'default' / 'last_playlist.json'), items)
        self.window.queue.clear()
        self.window.show()
        self.window.close()
        self.assertEqual(load_playlist(self.root / 'profiles' / 'default' / 'last_playlist.json'), [])

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
        self.assertEqual(json.loads((self.root / 'profiles' / 'default' / 'preferences.json').read_text(encoding='utf-8')), expected)
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

    def test_best_speed_key_is_saved_per_song_and_applied_on_play(self):
        self.populate(1)
        view = self.karaoke_fixture()
        self.window._append_queue(dict(song_id='s0', variant_id='original', label='Song 0'))
        self.window._play_from(0)
        self.assertEqual((view.live_speed.value(), view.live_key.value()), (100, 0))
        self.assertFalse(view.apply_best_button.isEnabled())
        self.assertEqual(view.best_button.toolTip(), '儲存為我的最佳')
        view.live_speed.setValue(110)
        view.live_key.setValue(2)
        view.best_button.click()
        self.assertTrue(view.apply_best_button.isEnabled())
        self.assertIn('1.10', view.best_status.text())
        self.assertIn('已是我的最佳', view.best_button.toolTip())
        view.live_key.setValue(3)
        self.assertIn('更新我的最佳', view.best_button.toolTip())
        view.original_button.click()
        self.assertEqual((view.live_speed.value(), view.live_key.value()), (100, 0))
        self.assertIn('切換到我的最佳', view.best_button.toolTip())
        view.best_button.click()
        self.assertEqual((view.live_speed.value(), view.live_key.value()), (110, 2))
        view.original_button.click()
        view.apply_best_button.click()
        self.assertEqual((view.live_speed.value(), view.live_key.value()), (110, 2))
        view.original_button.click()
        self.window._play_from(0)
        self.assertEqual((view.live_speed.value(), view.live_key.value()), (110, 2))
        view.clear_best_button.click()
        self.assertFalse(view.apply_best_button.isEnabled())
        self.assertEqual(view.best_button.toolTip(), '儲存為我的最佳')
        self.window._play_from(0)
        self.assertEqual((view.live_speed.value(), view.live_key.value()), (100, 0))
        view.close()

    def test_profiles_keep_preferences_playlist_and_recordings_separate(self):
        self.populate(1)
        self.window._set_singing_volume(33)
        self.window._append_queue(dict(song_id='s0', variant_id='original', label='Song 0'))
        self.window._switch_profile('Guest')
        self.assertEqual(self.window.profiles.active, 'Guest')
        self.assertEqual(self.settings['active_profile'], 'Guest')
        self.assertEqual(self.window.sing_volume.value(), 80)
        self.assertEqual(self.window.queue.count(), 0)
        self.assertEqual(self.window.profile_box.currentData(), 'Guest')
        self.assertTrue(self.window.profile_delete.isEnabled())
        self.window._set_singing_volume(55)
        self.window._switch_profile('default')
        self.assertEqual(self.window.sing_volume.value(), 33)
        self.assertEqual(self.window.queue.count(), 1)
        self.assertFalse(self.window.profile_delete.isEnabled())
        self.window._switch_profile('Guest')
        self.assertEqual(self.window.sing_volume.value(), 55)
        with patch('app.consumer.QMessageBox.question', return_value=QMessageBox.StandardButton.Yes):
            self.window._delete_profile()
        self.assertEqual(self.window.profiles.active, 'default')
        self.assertEqual(self.window.profiles.names(), ['default'])

    def test_profile_export_and_import_from_settings(self):
        self.populate(1)
        self.settings['source_dir'] = str(self.root)
        self.window._set_singing_volume(33)
        self.window._append_queue(dict(song_id='s0', variant_id='original', label='Song 0'))
        target = self.root / 'mine.zip'
        with patch('app.consumer.QFileDialog.getSaveFileName', return_value=(str(target), '')):
            self.window._export_profile(False)
        self.assertTrue(target.is_file())
        with patch('app.consumer.QFileDialog.getOpenFileName', return_value=(str(target), '')),                 patch('app.consumer.QInputDialog.getText', return_value=('Friend', True)):
            self.window._import_profile()
        self.assertEqual(self.window.profiles.active, 'Friend')
        self.assertEqual(self.window.sing_volume.value(), 33)
        self.assertEqual(self.window.queue.count(), 1)
        self.assertEqual(self.window.profile_box.currentData(), 'Friend')

    def test_profile_import_rejects_non_archives(self):
        bad = self.root / 'bad.zip'
        bad.write_bytes(b'nope')
        with patch('app.consumer.QFileDialog.getOpenFileName', return_value=(str(bad), '')),                 patch('app.consumer.QMessageBox.warning') as warning:
            self.window._import_profile()
        warning.assert_called_once()
        self.assertEqual(self.window.profiles.active, 'default')

    def test_profile_switch_is_refused_while_singing(self):
        self.window.playing_item = object()
        self.window._switch_profile('Guest')
        self.assertEqual(self.window.profiles.active, 'default')
        self.window.playing_item = None

    def test_monitor_failure_does_not_overwrite_desired_preference(self):
        view = self.karaoke_fixture()
        self.window.microphone.set_monitor.side_effect = [RuntimeError('unsupported'), None]
        with patch('PySide6.QtWidgets.QMessageBox.warning'):
            view._monitor_changed(True)
        self.assertFalse(view.monitor_checkbox.isChecked())
        self.assertTrue(self.window.audio_preferences['monitor_enabled'])
        view.close()

    def test_volume_changes_are_saved_automatically_without_closing(self):
        path = self.root / 'profiles' / 'default' / 'preferences.json'
        self.window._set_singing_volume(50)
        self.window._set_singing_volume(61)
        self.window._set_guide_volume(20)
        self.assertFalse(path.exists())
        QTest.qWait(350)
        prefs = json.loads(path.read_text(encoding='utf-8'))
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

    def test_background_crossfade_blends_without_capturing_lyrics(self):
        from PySide6.QtGui import QColor, QPixmap
        from app.karaoke_window import KaraokeCanvas
        canvas = KaraokeCanvas()
        canvas.resize(640, 360)
        canvas.background = QPixmap(640, 360)
        canvas.background.fill(QColor('red'))
        canvas.lines = ['visible lyric']
        old = canvas.capture_background()
        canvas.background = QPixmap(640, 360)
        canvas.background.fill(QColor('blue'))
        canvas.start_background_transition(old)
        self.assertEqual(canvas.grab().toImage().pixelColor(10, 10).name(), '#ff0000')
        canvas.advance_background_transition(1000)
        color = canvas.grab().toImage().pixelColor(10, 10)
        self.assertTrue(125 <= color.red() <= 130)
        self.assertTrue(125 <= color.blue() <= 130)
        # The captured frame contains no text, including where lyrics are drawn.
        self.assertEqual(old.toImage().pixelColor(320, 300).name(), '#ff0000')
        canvas.advance_background_transition(1000)
        self.assertIsNone(canvas.previous_background_frame)
        self.assertEqual(canvas.grab().toImage().pixelColor(10, 10).name(), '#0000ff')
        canvas.close()

    def test_background_crossfade_pauses_and_runs_with_motion_disabled(self):
        from PySide6.QtGui import QColor, QPixmap
        view = self.karaoke_fixture()
        player = self.window.player
        player.position.return_value = 0
        player.duration.return_value = 20000
        paths = []
        for name in ('red', 'blue'):
            pix = QPixmap(640, 360)
            pix.fill(QColor(name))
            path = self.root / (name + '.png')
            self.assertTrue(pix.save(str(path)))
            paths.append(path)
        view.set_background_motion(False)
        view.set_song('Song', '', paths, 20000)
        player.playbackState.return_value = QMediaPlayer.PlaybackState.PlayingState
        view.next_background()
        self.assertIsNotNone(view.canvas.previous_background_frame)
        self.assertTrue(view.motion_timer.isActive())
        QTest.qWait(120)
        self.assertGreater(view.canvas.transition_ms, 0)
        player.playbackState.return_value = QMediaPlayer.PlaybackState.PausedState
        view._sync_background_motion()
        paused_at = view.canvas.transition_ms
        self.assertFalse(view.motion_timer.isActive())
        QTest.qWait(100)
        self.assertEqual(view.canvas.transition_ms, paused_at)
        player.playbackState.return_value = QMediaPlayer.PlaybackState.PlayingState
        view._sync_background_motion()
        self.assertTrue(view.motion_timer.isActive())
        view.canvas.advance_background_transition(2000)
        view._sync_background_motion()
        self.assertFalse(view.motion_timer.isActive())
        view.close()

    def test_single_background_keeps_motion_without_crossfade(self):
        from PySide6.QtGui import QColor, QPixmap
        view = self.karaoke_fixture()
        pix = QPixmap(640, 360)
        pix.fill(QColor('red'))
        path = self.root / 'single.png'
        self.assertTrue(pix.save(str(path)))
        view.set_song('Song', '', [path], 20000)
        view.canvas.motion_ms = 12000
        view.next_background()
        self.assertEqual(view.canvas.motion_ms, 0)
        self.assertIsNone(view.canvas.previous_background_frame)
        view.canvas.depth_mask = QPixmap(640, 360)
        view.canvas.depth_mask.fill(QColor('white'))
        view.canvas.motion_ms = 12000
        view.next_background()
        self.assertEqual(view.canvas.motion_ms, 12000)
        view.close()

    def test_depth_mask_is_opaque_only_on_selected_pixels_and_falls_back(self):
        from PySide6.QtGui import QColor, QImage, QPixmap
        view = self.karaoke_fixture()
        image = QImage(100, 60, QImage.Format.Format_RGB32)
        image.fill(QColor('black'))
        for y in range(60):
            for x in range(50, 100):
                image.setPixelColor(x, y, QColor('white'))
        path = self.root / 'depth-mask.png'
        self.assertTrue(image.save(str(path)))
        view._apply_depth_result({'mask': str(path), 'motion_factor': .35,
                                  'method': 'inverse', 'coverage': .25})
        result = view.canvas.depth_mask.toImage()
        self.assertEqual(result.pixelColor(20, 20).alpha(), 0)
        self.assertEqual(result.pixelColor(80, 20).alpha(), 255)
        self.assertEqual(view.canvas.depth_motion_factor, .35)
        self.assertIn('A3-2', view.background_status.text())
        self.assertIn('25%', view.background_status.text())
        view.canvas.background = QPixmap(100, 60)
        view.canvas.background.fill(QColor('blue'))
        view.canvas.motion_enabled = True
        view.canvas.grab()
        view.set_depth_motion(False)
        self.assertIsNone(view.canvas.depth_mask)
        self.assertIn('一般動態', view.background_status.text())
        self.assertEqual(view.motion_timer.interval(), 100)
        view.close()

    def test_depth_mask_preserves_soft_alpha(self):
        from PySide6.QtGui import QColor, QImage
        view = self.karaoke_fixture()
        image = QImage(16, 16, QImage.Format.Format_Grayscale8)
        image.fill(QColor(100, 100, 100))
        path = self.root / 'soft-mask.png'
        self.assertTrue(image.save(str(path)))
        view._apply_depth_result({'mask': str(path), 'coverage': .5})
        self.assertEqual(view.canvas.depth_mask.toImage().pixelColor(8, 8).alpha(), 100)
        view.close()

    def test_depth_motion_interpolates_across_strip_boundaries(self):
        from PySide6.QtGui import QColor, QImage, QPixmap
        from app.karaoke_window import KaraokeCanvas
        canvas = KaraokeCanvas()
        canvas.resize(640, 360)
        source = QImage(640, 360, QImage.Format.Format_RGB32)
        for y in range(360):
            shade = y * 255 // 360
            for x in range(640):
                source.setPixelColor(x, y, QColor(shade, shade, shade))
        canvas.background = QPixmap.fromImage(source)
        canvas.depth_mask = QPixmap(640, 360)
        canvas.depth_mask.fill(QColor('white'))
        canvas.motion_enabled = True
        canvas.motion_ms = 12345
        image = canvas.grab().toImage()
        values = [image.pixelColor(300, y).red() for y in range(20, 180)]
        self.assertLessEqual(max(abs(b-a) for a, b in zip(values, values[1:])), 3)
        canvas.close()

    def test_depth_result_for_previous_image_is_not_applied(self):
        view = self.karaoke_fixture()
        view.depth_enabled = True
        old_key = ('old.png', 1, 1)
        result = {'mask': 'old.png', 'motion_factor': 1.0}
        with patch.object(view, '_depth_current_key', return_value=('new.png', 1, 1)), \
             patch.object(view, '_apply_depth_result') as apply, \
             patch.object(view, '_start_depth_current') as start:
            view._depth_ready(old_key, result)
            apply.assert_not_called()
            start.assert_called_once()
            self.assertEqual(view._depth_results[old_key], result)
        task = Mock()
        view._depth_task = task
        view.set_depth_motion(False)
        task.cancel.assert_called_once()
        self.assertIsNone(view._depth_task)
        view.close()

    def test_depth_status_reports_processing_and_failure(self):
        view = self.karaoke_fixture()
        view.depth_enabled = True
        view.canvas.motion_enabled = True
        view._depth_task = Mock()
        view._start_depth_current()
        self.assertIn('等待深度處理', view.background_status.text())
        key = ('bad.png', 1, 1)
        with patch.object(view, '_depth_current_key', return_value=key):
            view._depth_error(key, 'test failure')
        self.assertIn('深度處理失敗', view.background_status.text())
        self.assertEqual(view.background_status.toolTip(), 'test failure')
        view.close()

    def test_background_waits_briefly_for_current_depth(self):
        view = self.karaoke_fixture()
        view.depth_enabled = True
        view.images = [self.root / 'first.png', self.root / 'second.png']
        view.image_index = 0
        key = ('first', 1, 1)
        view._depth_task = Mock()
        view._depth_task_key = key
        view._depth_task_started = time.monotonic()
        with patch.object(view, '_depth_current_key', return_value=key), \
             patch.object(view, '_advance_background') as advance, \
             patch.object(view, '_show_background'):
            view.next_background()
            advance.assert_not_called()
            self.assertEqual(view.background_timer.interval(), 1000)
            view._depth_task_started = time.monotonic() - 26
            view.next_background()
            advance.assert_called_once()
        view._depth_task = None
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
