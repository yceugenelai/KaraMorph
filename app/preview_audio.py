"""One-output, seekable preview for a backing track and its vocal stem."""

from pathlib import Path

import numpy as np
import soundfile as sf
from PySide6.QtCore import QIODevice
from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices


class StemStream(QIODevice):
    def __init__(self, backing: Path, vocal: Path, parent=None):
        super().__init__(parent)
        self.backing = sf.SoundFile(str(backing))
        self.vocal = sf.SoundFile(str(vocal))
        self.rate = self.backing.samplerate
        self.frames = len(self.backing)
        self.frame = 0
        self.backing_gain = 0.7
        self.vocal_gain = 0.7
        self.vocal_enabled = False
        if abs(self.frames / self.rate - len(self.vocal) / self.vocal.samplerate) > 0.25:
            self.close_files()
            raise ValueError("伴奏與人聲長度相差超過 250 毫秒")
        if self.backing.channels not in (1, 2) or self.vocal.channels not in (1, 2):
            self.close_files()
            raise ValueError("試聽目前只支援單聲道或雙聲道音訊")
        self.open(QIODevice.OpenModeFlag.ReadOnly)

    def isSequential(self):
        return True

    def bytesAvailable(self):
        return max(0, self.frames - self.frame) * 4 + super().bytesAvailable()

    def atEnd(self):
        return self.frame >= self.frames

    def seek_ms(self, milliseconds):
        self.frame = min(self.frames, max(0, round(milliseconds * self.rate / 1000)))
        self.backing.seek(self.frame)

    def readData(self, maxlen):
        count = min(maxlen // 4, self.frames - self.frame, 4096)
        if count <= 0:
            return b""
        backing = self.backing.read(count, dtype="float32", always_2d=True)
        count = len(backing)
        if count == 0:
            return b""
        if backing.shape[1] == 1:
            backing = np.repeat(backing, 2, axis=1)
        mixed = backing * self.backing_gain
        if self.vocal_enabled and self.vocal_gain:
            # Both stems use the backing's clock. Interpolate when their sample rates differ.
            positions = (self.frame + np.arange(count)) * self.vocal.samplerate / self.rate
            first = int(positions[0])
            last = min(len(self.vocal), int(positions[-1]) + 2)
            self.vocal.seek(min(first, len(self.vocal)))
            samples = self.vocal.read(max(0, last - first), dtype="float32", always_2d=True)
            if len(samples):
                if samples.shape[1] == 1:
                    samples = np.repeat(samples, 2, axis=1)
                indices = np.clip(positions - first, 0, len(samples) - 1)
                left = np.floor(indices).astype(int)
                right = np.minimum(left + 1, len(samples) - 1)
                fraction = (indices - left)[:, None]
                mixed += (samples[left] * (1 - fraction) + samples[right] * fraction) * self.vocal_gain
        self.frame += count
        return (np.clip(mixed, -1, 1) * 32767).astype("<i2").tobytes()

    def close_files(self):
        self.close()
        self.backing.close()
        self.vocal.close()


class StemPreview:
    def __init__(self):
        self.stream = None
        self.sink = None
        self.device = QMediaDevices.defaultAudioOutput()
        self.base_ms = 0

    @property
    def duration_ms(self):
        return round(self.stream.frames * 1000 / self.stream.rate) if self.stream else 0

    @property
    def position_ms(self):
        if not self.sink:
            return self.base_ms if self.stream else 0
        return min(self.duration_ms, self.base_ms + self.sink.processedUSecs() // 1000)

    def start(self, backing, vocal, backing_gain, vocal_gain, vocal_enabled):
        self.stop()
        stream = StemStream(backing, vocal)
        self.stream = stream
        stream.backing_gain = backing_gain
        stream.vocal_gain = vocal_gain
        stream.vocal_enabled = vocal_enabled
        try:
            self._start_sink()
        except Exception:
            self.stop()
            raise

    def _start_sink(self):
        fmt = QAudioFormat()
        fmt.setSampleRate(self.stream.rate)
        fmt.setChannelCount(2)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        if not self.device.isFormatSupported(fmt):
            raise ValueError("播放裝置不支援此音檔的取樣率")
        self.sink = QAudioSink(self.device, fmt)
        self.sink.setBufferSize(self.stream.rate * 4 // 8)
        self.sink.start(self.stream)

    def seek(self, milliseconds):
        if not self.stream:
            return
        if self.sink:
            self.sink.stop()
            self.sink = None
        self.stream.seek_ms(milliseconds)
        self.base_ms = round(self.stream.frame * 1000 / self.stream.rate)
        if self.stream.frame < self.stream.frames:
            self._start_sink()

    def stop(self):
        if self.sink:
            self.sink.stop()
            self.sink = None
        if self.stream:
            self.stream.close_files()
            self.stream = None
        self.base_ms = 0

    def set_device(self, device):
        self.device = device
        if self.stream:
            self.seek(self.position_ms)
