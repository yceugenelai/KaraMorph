"""MV export: scene planning, frame rendering and FFmpeg encoding. Everything stays local."""

import bisect
import math
import os
import random
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap

from app.i18n import t
from app.karaoke_window import KaraokeCanvas
from runtime_config import ROOT

RATE = 44100
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
RESOLUTIONS = {'540p': (960, 540), '720p': (1280, 720), '1080p': (1920, 1080)}
FPS = 30


def find_ffmpeg() -> str:
    """The FFmpeg copy that the workers expose under .tools, else one on PATH."""
    found = sorted((ROOT / '.tools' / 'ffmpeg').glob('*/ffmpeg*'), key=lambda p: p.stat().st_mtime, reverse=True)
    found = [p for p in found if p.is_file() and p.suffix in {'', '.exe'}]
    if found:
        return str(found[0])
    path = shutil.which('ffmpeg')
    if path:
        return path
    raise RuntimeError(t('找不到 FFmpeg；請先執行一次分離或調整工作以準備內建 FFmpeg。'))


# ----- audio -----
def decode_audio(path, rate=RATE) -> np.ndarray:
    result = subprocess.run([find_ffmpeg(), '-v', 'error', '-i', str(path), '-f', 'f32le', '-ac', '2',
                             '-ar', str(rate), '-'], capture_output=True, creationflags=NO_WINDOW)
    if result.returncode:
        raise RuntimeError(t('無法讀取音檔：{p0}', p0=result.stderr.decode('utf-8', 'replace')[-300:]))
    data = np.frombuffer(result.stdout, dtype='<f4')
    return data[:len(data) // 2 * 2].reshape(-1, 2).copy()


def mix_recording(vocal: np.ndarray, backing: np.ndarray, backing_offset_ms: float,
                  vocal_gain: float, backing_gain: float) -> np.ndarray:
    """Mix a take with its backing on the take's clock; backing sample n+offset sounds with vocal sample n."""
    count = len(vocal)
    start = round(backing_offset_ms * RATE / 1000)
    aligned = np.zeros((count, 2), dtype=np.float32)
    low, high = max(0, start), min(len(backing), start + count)
    if high > low:
        aligned[low - start:high - start] = backing[low:high]
    return np.clip(vocal * vocal_gain + aligned * backing_gain, -1.0, 1.0).astype(np.float32)


# ----- scenes -----
@dataclass
class Scene:
    start: int
    end: int
    image: Path | None


def plan_scenes(duration_ms: int, images: list[Path], interval_ms: int, segments=(), seed: int = 0) -> list[Scene]:
    """Cut the song into image scenes.

    `segments` is [(start_ms, Path | None)]: a path pins that image for the segment,
    None lets the segment loop through `images` in a shuffled order every `interval_ms`.
    """
    duration_ms = max(1, int(duration_ms))
    interval_ms = max(1000, int(interval_ms))
    cuts = sorted({max(0, int(start)): image for start, image in segments}.items())
    if not cuts or cuts[0][0] != 0:
        cuts.insert(0, (0, None))
    rng, bag, last = random.Random(seed), [], [None]

    def next_image():
        if not images:
            return None
        if not bag:
            bag.extend(images)
            rng.shuffle(bag)
            if len(bag) > 1 and bag[0] == last[0]:
                bag[0], bag[1] = bag[1], bag[0]
        last[0] = bag.pop(0)
        return last[0]

    scenes = []
    for index, (start, image) in enumerate(cuts):
        end = cuts[index + 1][0] if index + 1 < len(cuts) else duration_ms
        end = min(end, duration_ms)
        if end <= start:
            continue
        if image is not None:
            scenes.append(Scene(start, end, image))
            continue
        cursor = start
        while cursor < end:
            stop = cursor + interval_ms
            if end - stop < interval_ms * 0.4:
                stop = end
            scenes.append(Scene(cursor, min(stop, end), next_image()))
            cursor = stop
    return scenes


def scene_state(scenes: list[Scene], position_ms: int, fade_ms: int):
    """(current index, previous index or None, blend 0..1 toward current) for a crossfade."""
    starts = [scene.start for scene in scenes]
    index = max(0, bisect.bisect_right(starts, position_ms) - 1)
    if index == 0 or fade_ms <= 0:
        return index, None, 1.0
    fade = min(fade_ms, (scenes[index].end - scenes[index].start) // 2)
    elapsed = position_ms - scenes[index].start
    if fade > 0 and elapsed < fade:
        return index, index - 1, elapsed / fade
    return index, None, 1.0


# ----- rendering -----
class MvRenderer:
    def __init__(self, size, scenes, timeline, speed=1.0, delay_ms=0, song_offset_ms=0,
                 fade_ms=1000, font_scale=1.3, center_ratio=5 / 6, color='#ffffff'):
        self.width, self.height = size
        self.scenes, self.timeline = scenes, timeline
        self.speed, self.delay_ms, self.song_offset_ms, self.fade_ms = speed, delay_ms, song_offset_ms, fade_ms
        self.canvas = KaraokeCanvas()
        self.canvas.resize(self.width, self.height)
        self.canvas.no_image_text = ''
        self.canvas.fill_background = True
        self.canvas.ui_scale = self.width / 1280
        self.canvas.empty_lyrics_text = ''
        self.canvas.font_scale, self.canvas.center_ratio, self.canvas.highlight_color = font_scale, center_ratio, color
        self.canvas.synced = timeline is not None
        self.canvas.lines = list(timeline.lines) if timeline else []
        self._pixmaps = {}
        self._last = (None, None)

    def _pixmap(self, path):
        if path is None:
            return None
        if path not in self._pixmaps:
            if len(self._pixmaps) >= 6:
                self._pixmaps.pop(next(iter(self._pixmaps)))
            pixmap = QPixmap(str(path))
            self._pixmaps[path] = None if pixmap.isNull() else pixmap
        return self._pixmaps[path]

    def _draw(self, scene_index, progress) -> QImage:
        self.canvas.background = self._pixmap(self.scenes[scene_index].image) if self.scenes else None
        self.canvas.scene = scene_index
        self.canvas.progress = progress
        image = QImage(self.width, self.height, QImage.Format.Format_RGB32)
        image.fill(QColor('#000000'))
        self.canvas.render(image)
        return image

    def frame(self, position_ms: int) -> QImage:
        progress = (self.timeline.index_at(position_ms + self.song_offset_ms, self.speed, self.delay_ms)
                    if self.timeline else -1)
        if not self.scenes:
            return self._blank(progress)
        index, previous, blend = scene_state(self.scenes, position_ms, self.fade_ms)
        key = (index, previous, round(blend * 30) if previous is not None else 1, progress)
        if key == self._last[0]:
            return self._last[1]
        image = self._draw(index, progress)
        if previous is not None:
            base = self._draw(previous, progress)
            painter = QPainter(base)
            painter.setOpacity(blend)
            painter.drawImage(0, 0, image)
            painter.end()
            image = base
        self._last = (key, image)
        return image

    def _blank(self, progress):
        self.canvas.background, self.canvas.scene, self.canvas.progress = None, 0, progress
        image = QImage(self.width, self.height, QImage.Format.Format_RGB32)
        self.canvas.render(image)
        return image

    def frame_bytes(self, position_ms: int) -> bytes:
        image = self.frame(position_ms).convertToFormat(QImage.Format.Format_RGB888)
        pixels = np.frombuffer(image.constBits(), dtype=np.uint8).reshape(self.height, image.bytesPerLine())
        return pixels[:, :self.width * 3].tobytes()


# ----- encoding -----
class MvJob(QObject):
    """Renders frames on the GUI thread in small slices and pipes them to FFmpeg."""

    progress = Signal(int, int)
    done = Signal(bool, str)

    def __init__(self, audio: np.ndarray, renderer: MvRenderer, output: Path, parent=None):
        super().__init__(parent)
        self.audio, self.renderer, self.output = audio, renderer, Path(output)
        self.total = max(1, math.ceil(len(audio) / RATE * FPS))
        self.index = 0
        self.process = None
        self.finished = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.work = Path(tempfile.mkdtemp(prefix='karamorph-mv-'))
        self.partial = self.output.with_name(self.output.stem + '.partial.mp4')

    def start(self):
        try:
            self.output.parent.mkdir(parents=True, exist_ok=True)
            wav = self.work / 'audio.wav'
            sf.write(str(wav), self.audio, RATE, subtype='PCM_16')
            self.log = open(self.work / 'ffmpeg.log', 'wb')
            width, height = self.renderer.width, self.renderer.height
            self.process = subprocess.Popen(
                [find_ffmpeg(), '-y', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{width}x{height}',
                 '-r', str(FPS), '-i', '-', '-i', str(wav), '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20',
                 '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k', '-shortest', '-movflags', '+faststart',
                 str(self.partial)],
                stdin=subprocess.PIPE, stderr=self.log, creationflags=NO_WINDOW)
        except (OSError, RuntimeError, sf.LibsndfileError) as error:
            self._finish(False, str(error))
            return
        self.timer.start(0)

    def _tick(self):
        try:
            deadline = time.perf_counter() + 0.03
            while self.index < self.total and time.perf_counter() < deadline:
                self.process.stdin.write(self.renderer.frame_bytes(round(self.index * 1000 / FPS)))
                self.index += 1
            self.progress.emit(self.index, self.total)
            if self.index >= self.total:
                self.timer.stop()
                self.process.stdin.close()
                self.process.wait()
                if self.process.returncode:
                    raise RuntimeError(self._log_tail())
                os.replace(self.partial, self.output)
                self._finish(True, str(self.output))
        except (OSError, RuntimeError, ValueError) as error:
            self._finish(False, self._log_tail() or str(error))

    def _log_tail(self):
        try:
            self.log.flush()
            return (self.work / 'ffmpeg.log').read_text(encoding='utf-8', errors='replace')[-400:].strip()
        except (OSError, AttributeError, ValueError):
            return ''

    def cancel(self):
        self._finish(False, t('已取消'))

    def _finish(self, ok, message):
        if self.finished:
            return
        self.finished = True
        self.timer.stop()
        if self.process and self.process.poll() is None:
            try:
                self.process.stdin.close()
            except (OSError, ValueError):
                pass
            self.process.kill()
            self.process.wait()
        try:
            self.log.close()
        except (OSError, AttributeError):
            pass
        if not ok:
            self.partial.unlink(missing_ok=True)
        shutil.rmtree(self.work, ignore_errors=True)
        self.done.emit(ok, message)
