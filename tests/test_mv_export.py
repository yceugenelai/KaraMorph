import os
import subprocess
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QWidget

from app.lyrics import parse_lrc
from app.mv_export import (FPS, RATE, MvJob, MvRenderer, Scene, find_ffmpeg, mix_recording, plan_scenes, scene_state)


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


class WidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

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


if __name__ == '__main__':
    unittest.main()
