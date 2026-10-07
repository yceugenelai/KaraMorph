import os
from pathlib import Path
import tempfile
import unittest

import numpy as np
import soundfile as sf
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

from app.vocal_activity import analyze, build_cache, cache_path, read_cache
from app.karaoke_assets import ActivityTask


class VocalActivityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def fixture(self, folder):
        path = Path(folder) / 'vocal.wav'
        # Intro 0..1.2, voice 1.2..2.4, interlude 2.4..3.6, voice 3.6..4.8.
        data = np.zeros(4800, dtype=np.float32)
        data[1200:2400] = 0.1
        data[3600:4800] = 0.1
        sf.write(path, data, 1000, subtype='FLOAT')
        return path

    def test_intro_and_interlude_pause_progress(self):
        with tempfile.TemporaryDirectory() as folder:
            edges, voiced = analyze(self.fixture(folder))
            self.assertAlmostEqual(float(np.interp(1.1, edges, voiced)), 0)
            self.assertAlmostEqual(float(np.interp(3.5, edges, voiced)), 1.2)
            self.assertAlmostEqual(voiced[-1], 2.4)

    def test_cache_reuse_and_invalidation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.fixture(folder)
            target = cache_path(path, Path(folder)/'cache')
            build_cache(path, target)
            before = target.stat().st_mtime_ns
            build_cache(path, target)
            self.assertEqual(target.stat().st_mtime_ns, before)
            stat = path.stat()
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns+1000000))
            self.assertIsNone(read_cache(path, target))
            self.assertNotEqual(cache_path(path, target.parent), target)

    def test_real_worker_process_and_cached_second_start(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.fixture(folder)
            for _ in range(2):
                task = ActivityTask(path, Path(folder)/'cache')
                result, errors = [], []
                loop = QEventLoop()
                task.ready.connect(lambda *args: (result.append(args), loop.quit()))
                task.failed.connect(lambda message: (errors.append(message), loop.quit()))
                task.start()
                if not task.done:
                    QTimer.singleShot(10000, loop.quit)
                    loop.exec()
                self.assertFalse(errors, errors)
                self.assertTrue(result)
                self.assertAlmostEqual(result[0][2][-1], 2.4)
                task.cancel()


if __name__ == '__main__':
    unittest.main()
