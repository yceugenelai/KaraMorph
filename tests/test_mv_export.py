import os
import subprocess
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
import soundfile as sf
from PySide6.QtCore import QEventLoop, QObject, QTimer, Signal
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QWidget

from app.lyrics import parse_lrc
from app.mv_export import (FPS, RATE, MvJob, MvRenderer, Scene, find_ffmpeg, mix_recording, plan_scenes, scene_state)
from unittest.mock import patch


class SceneTests(unittest.TestCase):
    def test_auto_rotation_pinned_segment_and_repeatability(self):
        images = [Path(f'{n}.png') for n in 'abc']
        scenes = plan_scenes(40000, images, 10000, [(0, None), (20000, Path('pin.png'))], seed=3)
        self.assertEqual([(s.start, s.end) for s in scenes], [(0, 10000), (10000, 20000), (20000, 40000)])
        self.assertEqual(scenes[2].image, Path('pin.png'))
        self.assertNotEqual(scenes[0].image, scenes[1].image)
        self.assertEqual(scenes, plan_scenes(40000, images, 10000, [(0, None), (20000, Path('pin.png'))], seed=3))
        self.assertEqual(plan_scenes(5000, [], 10000)[0].image, None)

    def test_short_tail_is_merged_and_fade_window(self):
        scenes = plan_scenes(12000, [Path('a.png')], 10000)
        self.assertEqual([(s.start, s.end) for s in scenes], [(0, 12000)])
        scenes = [Scene(0, 10000, None), Scene(10000, 20000, None)]
        self.assertEqual(scene_state(scenes, 5000, 1000), (0, None, 1.0))
        self.assertEqual(scene_state(scenes, 10500, 1000), (1, 0, 0.5))
        self.assertEqual(scene_state(scenes, 12000, 1000), (1, None, 1.0))

    def test_mix_aligns_backing_offset(self):
        vocal = np.full((100, 2), .5, dtype=np.float32)
        backing = np.zeros((RATE, 2), dtype=np.float32)
        backing[RATE // 10:RATE // 10 + 10] = .25
        mixed = mix_recording(vocal, backing, 100, 1.0, 1.0)
        self.assertAlmostEqual(float(mixed[0, 0]), .75)
        self.assertAlmostEqual(float(mixed[20, 0]), .5)
        self.assertEqual(len(mixed), 100)

    def test_song_position_follows_recorded_tempo_map_or_constant_rate(self):
        slow = MvRenderer((64, 36), [], None, song_map=[[0, 1000], [1000, 1800], [2000, 2600], [3000, 3400]])
        self.assertEqual(slow.song_position(0), 1000)
        self.assertEqual(slow.song_position(1500), 2200)  # slower than real time: song advanced 0.6-0.8 s per s
        self.assertEqual(slow.song_position(4000), 4200)   # last pace continues past the final sample
        shifted = MvRenderer((64, 36), [], None, song_map=[[0, 1000], [1000, 1800]], shift_ms=500)
        self.assertEqual(shifted.song_position(0), 1400)
        constant = MvRenderer((64, 36), [], None, song_offset_ms=100, song_rate=0.8, shift_ms=50)
        self.assertEqual(constant.song_position(1000), 940)

    def test_drift_always_covers_the_frame_and_moves(self):
        from app.karaoke_window import drift_rect
        for seed in range(6):
            rects = [drift_rect(640, 360, 640, 360, f / 10, seed) for f in range(11)]
            for rect in rects:
                self.assertLessEqual(rect.left(), 0.001)
                self.assertLessEqual(rect.top(), 0.001)
                self.assertGreaterEqual(rect.right(), 639.999)
                self.assertGreaterEqual(rect.bottom(), 359.999)
            self.assertNotEqual(rects[0], rects[-1])


class EncodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_render_and_encode_short_mv(self):
        try:
            find_ffmpeg()
        except RuntimeError:
            self.skipTest('no ffmpeg')
        with tempfile.TemporaryDirectory() as folder:
            image = QImage(64, 36, QImage.Format.Format_RGB32)
            image.fill(QColor('red'))
            path = Path(folder) / 'bg.png'
            image.save(str(path))
            timeline = parse_lrc('[00:00.50]first\n[00:01.50]second')
            scenes = plan_scenes(2000, [path], 10000)
            renderer = MvRenderer((320, 180), scenes, timeline)
            self.assertEqual(len(renderer.frame_bytes(0)), 320 * 180 * 3)
            audio = np.zeros((RATE * 2, 2), dtype=np.float32)
            output = Path(folder) / 'out' / 'mv.mp4'
            job = MvJob(audio, renderer, output)
            result = []
            loop = QEventLoop()
            job.done.connect(lambda ok, message: (result.append((ok, message)), loop.quit()))
            QTimer.singleShot(60000, loop.quit)
            job.start()
            loop.exec()
            self.assertTrue(result and result[0][0], result)
            self.assertGreater(output.stat().st_size, 1000)
            probe = subprocess.run([find_ffmpeg(), '-hide_banner', '-i', str(output)], capture_output=True, text=True)
            self.assertIn('Video: h264', probe.stderr)
            self.assertIn('Audio: aac', probe.stderr)
            self.assertFalse(output.with_name('mv.partial.mp4').exists())

    def test_cancel_leaves_no_partial_file(self):
        try:
            find_ffmpeg()
        except RuntimeError:
            self.skipTest('no ffmpeg')
        with tempfile.TemporaryDirectory() as folder:
            renderer = MvRenderer((320, 180), [], None)
            output = Path(folder) / 'mv.mp4'
            job = MvJob(np.zeros((RATE * 20, 2), dtype=np.float32), renderer, output)
            result = []
            job.done.connect(lambda ok, message: result.append(ok))
            job.start()
            job.cancel()
            self.assertEqual(result, [False])
            self.assertFalse(output.exists() or output.with_name('mv.partial.mp4').exists())

    def test_depth_renderer_animates_selected_area_and_keeps_crossfade(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = QImage(640, 360, QImage.Format.Format_RGB32)
            image.fill(QColor('blue'))
            for x in range(0, 640, 12):
                for y in range(360):
                    image.setPixelColor(x, y, QColor('white'))
            first = root / 'first.png'
            self.assertTrue(image.save(str(first)))
            image.fill(QColor('red'))
            second = root / 'second.png'
            self.assertTrue(image.save(str(second)))
            mask = QImage(640, 360, QImage.Format.Format_Grayscale8)
            mask.fill(QColor('white'))
            mask_path = root / 'mask.png'
            self.assertTrue(mask.save(str(mask_path)))
            data = {'mask': str(mask_path), 'motion_factor': 1.0}
            scenes = [Scene(0, 2000, first), Scene(2000, 4000, second)]
            renderer = MvRenderer((640, 360), scenes, None, motion=True, fade_ms=1000,
                                  depth_results={first: data, second: data})
            before = renderer.frame(0).copy()
            after = renderer.frame(1200).copy()
            self.assertNotEqual(before, after)
            self.assertIsNotNone(renderer.canvas.depth_mask)
            self.assertIsNotNone(renderer.canvas.drift)
            depth_only = MvRenderer((640, 360), scenes, None, motion=False,
                                    depth_results={first: data, second: data})
            depth_frame = depth_only.frame(1200).copy()
            self.assertIsNone(depth_only.canvas.drift)
            self.assertNotEqual(after, depth_frame)
            pan_only = MvRenderer((640, 360), scenes, None, motion=True)
            pan_frame = pan_only.frame(1200).copy()
            self.assertNotEqual(after, pan_frame)
            mixed = renderer.frame(2500)
            color = mixed.pixelColor(10, 10)
            self.assertGreater(color.red(), 60)
            self.assertGreater(color.blue(), 60)


class FakeDepthTask(QObject):
    ready = Signal(str, object)
    failed = Signal(str)

    def __init__(self, image, python, model, parent=None, settings=None):
        super().__init__(parent)
        self.image = image
        self.cancelled = False

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True


class FakeMvJob(QObject):
    progress = Signal(int, int)
    done = Signal(bool, str)

    def __init__(self, audio, renderer, output, parent=None):
        super().__init__(parent)
        self.renderer = renderer
        self.started = False

    def start(self):
        self.started = True

    def cancel(self):
        self.done.emit(False, 'cancelled')


class WidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def depth_widget(self, root):
        from app.karaoke_assets import song_images_dir
        from app.lyrics import assets_dir
        from app.mv_export_widget import MvExportWidget
        sf.write(str(root / 'song.wav'), np.zeros((RATE, 2), dtype=np.float32), RATE)
        folder = song_images_dir(root, 'song')
        folder.mkdir(parents=True)
        image = QImage(640, 360, QImage.Format.Format_RGB32)
        image.fill(QColor('blue'))
        path = folder / 'own.png'
        image.save(str(path))
        assets = assets_dir(root, 'song')
        (assets / 'local.lrc').write_text('[00:00]hello', encoding='utf-8')
        (assets / 'lyric_sources.json').write_text('{"source":"local","offsets":{}}', encoding='utf-8')
        controller = QWidget()
        controller.workspace = root
        controller.song_map = {'song': {'title': 'T', 'path': str(root / 'song.wav'), 'available': True}}
        controller.settings = {}
        controller.runtime = lambda _key: root / 'python.exe'
        return controller, MvExportWidget(controller, 'song'), path

    def test_depth_export_prepares_missing_mask_before_encoding(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            controller, widget, image = self.depth_widget(root)
            self.assertTrue(widget.depth_motion.isChecked())
            mask = QImage(32, 32, QImage.Format.Format_Grayscale8)
            mask.fill(QColor('white'))
            mask_path = root / 'mask.png'
            mask.save(str(mask_path))
            data = {'mask': str(mask_path), 'motion_factor': 1.0}
            stored = {}
            with patch('app.mv_export_widget.QFileDialog.getSaveFileName', return_value=(str(root / 'out.mp4'), '')), \
                 patch('app.model_store.ModelStore.ready', return_value=True), \
                 patch('app.mv_export_widget.lookup_depth', side_effect=lambda *_: stored.get('result')), \
                 patch('app.mv_export_widget.DepthMotionTask', FakeDepthTask), \
                 patch('app.mv_export_widget.MvJob', FakeMvJob), \
                 patch.object(widget, '_build_audio', return_value=np.zeros((RATE, 2), dtype=np.float32)):
                widget.start_export()
                self.assertTrue(widget.preparing_depth)
                self.assertIsNone(widget.job)
                task = widget._depth_task
                stored['result'] = data
                task.ready.emit(str(image), data)
                loop = QEventLoop()
                QTimer.singleShot(100, loop.quit)
                loop.exec()
                self.assertFalse(widget.preparing_depth)
                self.assertTrue(widget.job.started)
                self.assertEqual(widget.job.renderer.depth_results[image], data)
                widget.cancel_export()
            widget.dispose()
            controller.close()

    def test_depth_preparation_can_be_cancelled(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            controller, widget, _ = self.depth_widget(root)
            with patch('app.mv_export_widget.QFileDialog.getSaveFileName', return_value=(str(root / 'out.mp4'), '')), \
                 patch('app.model_store.ModelStore.ready', return_value=True), \
                 patch('app.mv_export_widget.lookup_depth', return_value=None), \
                 patch('app.mv_export_widget.DepthMotionTask', FakeDepthTask):
                widget.start_export()
                task = widget._depth_task
                self.assertTrue(widget.preparing_depth)
                widget.cancel_export()
                self.assertTrue(task.cancelled)
                self.assertFalse(widget.preparing_depth)
                self.assertIsNone(widget.job)
                self.assertFalse((root / 'out.mp4').exists())
            widget.dispose()
            controller.close()

    def test_failed_depth_preparation_does_not_export(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            controller, widget, _ = self.depth_widget(root)
            with patch('app.mv_export_widget.QFileDialog.getSaveFileName', return_value=(str(root / 'out.mp4'), '')), \
                 patch('app.model_store.ModelStore.ready', return_value=True), \
                 patch('app.mv_export_widget.lookup_depth', return_value=None), \
                 patch('app.mv_export_widget.DepthMotionTask', FakeDepthTask), \
                 patch('app.mv_export_widget.MvJob', FakeMvJob):
                widget.start_export()
                widget._depth_task.failed.emit('test error')
                self.assertFalse(widget.preparing_depth)
                self.assertIsNone(widget.job)
                self.assertIn('test error', widget.status.text())
                self.assertTrue(widget.export_button.isEnabled())
                self.assertFalse((root / 'out.mp4').exists())
            widget.dispose()
            controller.close()

    def test_explicit_depth_opt_out_uses_regular_export(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            controller, widget, _ = self.depth_widget(root)
            widget.depth_motion.setChecked(False)
            with patch('app.mv_export_widget.QFileDialog.getSaveFileName', return_value=(str(root / 'out.mp4'), '')), \
                 patch('app.mv_export_widget.DepthMotionTask', FakeDepthTask), \
                 patch('app.mv_export_widget.MvJob', FakeMvJob), \
                 patch.object(widget, '_build_audio', return_value=np.zeros((RATE, 2), dtype=np.float32)):
                widget.start_export()
                self.assertFalse(widget.preparing_depth)
                self.assertTrue(widget.job.started)
                self.assertEqual(widget.job.renderer.depth_results, {})
                widget.cancel_export()
            widget.dispose()
            controller.close()

    def test_cached_depth_is_used_for_preview_and_export_without_processing(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            controller, widget, image = self.depth_widget(root)
            mask = QImage(32, 32, QImage.Format.Format_Grayscale8)
            mask.fill(QColor('white'))
            mask_path = root / 'mask.png'
            mask.save(str(mask_path))
            data = {'mask': str(mask_path), 'motion_factor': 1.0}
            with patch('app.mv_export_widget.QFileDialog.getSaveFileName', return_value=(str(root / 'out.mp4'), '')), \
                 patch('app.mv_export_widget.lookup_depth', return_value=data), \
                 patch('app.mv_export_widget.DepthMotionTask') as task, \
                 patch('app.mv_export_widget.MvJob', FakeMvJob), \
                 patch.object(widget, '_build_audio', return_value=np.zeros((RATE, 2), dtype=np.float32)):
                renderer = widget.make_renderer((640, 360))
                self.assertEqual(renderer.depth_results[image], data)
                widget.start_export()
                task.assert_not_called()
                self.assertTrue(widget.job.started)
                self.assertEqual(widget.job.renderer.depth_results[image], data)
                widget.cancel_export()
            widget.dispose()
            controller.close()

    def test_widget_without_lrc_blocks_export_and_with_lrc_previews(self):
        from app.lyrics import assets_dir
        from app.mv_export_widget import MvExportWidget
        with tempfile.TemporaryDirectory() as folder:
            controller = QWidget()
            controller.workspace = Path(folder)
            controller.song_map = {'song': {'title': 'T', 'path': str(Path(folder) / 'song.wav'), 'available': True}}
            import soundfile as sf
            sf.write(str(Path(folder) / 'song.wav'), np.zeros((RATE * 3, 2), dtype=np.float32), RATE)
            widget = MvExportWidget(controller, 'song')
            self.assertEqual(widget.source.count(), 1)
            self.assertFalse(widget.export_button.isEnabled())
            self.assertIn('LRC', widget.lyric_message)
            assets = assets_dir(Path(folder), 'song')
            assets.mkdir(parents=True)
            (assets / 'local.lrc').write_text('[00:01]hello\n[00:02]', encoding='utf-8')
            (assets / 'lyric_sources.json').write_text('{"source": "local", "offsets": {}}', encoding='utf-8')
            widget.refresh()
            self.assertTrue(widget.export_button.isEnabled())
            widget.seek(1500)
            widget._draw_preview()
            self.assertFalse(widget.preview.pixmap().isNull())
            widget.seek(2500)
            widget.add_cut()
            self.assertEqual(len(widget.segments()), 2)
            widget.delete_cut()
            widget.dispose()
            controller.close()

    def test_shared_images_option_offset_and_motion_frames(self):
        from app.karaoke_assets import shared_images_dir, song_images_dir
        from app.lyrics import assets_dir
        from app.mv_export_widget import MvExportWidget
        import json
        import soundfile as sf
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for target in (song_images_dir(root, 'song') / 'own.png', shared_images_dir(root) / 'shared.png'):
                target.parent.mkdir(parents=True, exist_ok=True)
                image = QImage(64, 36, QImage.Format.Format_RGB32)
                image.fill(QColor('blue'))
                for x in range(0, 64, 8):
                    for y in range(36):
                        image.setPixelColor(x, y, QColor('white'))
                image.save(str(target))
            sf.write(str(root / 'song.wav'), np.zeros((RATE * 3, 2), dtype=np.float32), RATE)
            take = assets_dir(root, 'song').parent / 'recordings' / 'r1'
            take.mkdir(parents=True)
            sf.write(str(take / 'microphone.wav'), np.zeros((RATE, 2), dtype=np.float32), RATE)
            (take / 'session.json').write_text(json.dumps(dict(song_id='song', session_id='r1', profile='default',
                started_at='2026-01-01T00:00:00', duration_ms=1000, playback_start_offset_ms=100,
                variant_id='original')), encoding='utf-8')
            controller = QWidget()
            controller.workspace = root
            controller.song_map = {'song': {'title': 'T', 'path': str(root / 'song.wav'), 'available': True}}
            widget = MvExportWidget(controller, 'song')
            self.assertFalse(widget.use_shared.isChecked())
            self.assertEqual({p.name for p in widget.pool_images()}, {'own.png'})
            self.assertEqual({s.image.name for s in widget.scenes if s.image}, {'own.png'})
            widget.use_shared.setChecked(True)
            self.assertIn('shared.png', {p.name for p in widget.pool_images()})
            widget.use_shared.setChecked(False)
            widget.source.setCurrentIndex(widget.source.count() - 1)
            self.assertTrue(widget.offset.isVisible() or not widget.isVisible())
            widget.offset.setValue(250)
            self.assertEqual(widget.make_renderer((320, 180)).song_position(0), 350)
            audio = widget._build_audio(widget.current())
            self.assertEqual(len(audio), RATE)
            widget.motion.setChecked(True)
            renderer = widget.make_renderer((160, 90))
            self.assertTrue(renderer.motion)
            first, later = renderer.frame(100).copy(), renderer.frame(1800)
            self.assertNotEqual(first, later)
            widget.dispose()
            controller.close()


if __name__ == '__main__':
    unittest.main()
