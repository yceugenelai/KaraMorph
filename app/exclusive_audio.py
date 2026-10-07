"""One WASAPI exclusive clock for backing, guide vocal and live microphone."""
from collections import deque
from math import gcd
import shutil
import subprocess
import sys
import json
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf
from scipy.signal import resample_poly
from PySide6.QtCore import QEventLoop, QObject, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QMediaPlayer


def device_index(device, direction):
    apis = sd.query_hostapis()
    api = next((i for i, a in enumerate(apis) if 'WASAPI' in a['name']), None)
    if api is None:
        raise RuntimeError('找不到 WASAPI 音訊後端')
    if device is None or device.isNull():
        index = apis[api][f'default_{direction}_device']
        if index < 0:
            raise RuntimeError(f'找不到預設 {direction} 音訊設備')
        return index
    matches = [i for i, d in enumerate(sd.query_devices()) if d['hostapi'] == api
               and d[f'max_{direction}_channels'] and d['name'] == device.description()]
    if len(matches) != 1:
        raise RuntimeError(f'無法唯一對應 WASAPI 設備「{device.description()}」，請重新選擇設備。')
    return matches[0]


def read_audio(path, rate, channels):
    try:
        data, original = sf.read(str(path), dtype='float32', always_2d=True)
    except sf.LibsndfileError:
        ffmpeg = shutil.which('ffmpeg')
        if not ffmpeg:
            raise ValueError('獨占播放無法解碼此格式；請轉成 WAV／MP3，安裝 FFmpeg 或在設定切換 Qt 共用模式。')
        result = subprocess.run([ffmpeg, '-v', 'error', '-i', str(path), '-f', 'f32le',
                                 '-ac', str(channels), '-ar', str(rate), 'pipe:1'],
                                capture_output=True, check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        data = np.frombuffer(result.stdout, dtype='<f4').reshape(-1, channels)
        original = rate
    if not len(data) or not np.isfinite(data).all():
        raise ValueError('音訊為空或含有無效數值')
    if original != rate:
        divisor = gcd(original, rate)
        data = resample_poly(data, rate // divisor, original // divisor, axis=0)
    if channels == 1:
        data = data.mean(axis=1, keepdims=True)
    elif data.shape[1] == 1:
        data = np.repeat(data, channels, axis=1)
    return np.ascontiguousarray(data[:, :channels], dtype=np.float32)


def test_exclusive_device(device, direction):
    """Probe the selected exclusive endpoint, with no recording file."""
    index = device_index(device, direction)
    info = sd.query_devices(index)
    channels = min(2, info[f'max_{direction}_channels'])
    errors = []
    for rate in dict.fromkeys([int(info['default_samplerate']), 48000, 44100]):
        for dtype in ('float32', 'int16', 'int32'):
            stream = None
            try:
                peak = [0.0]
                frame = [0]
                scale = {'float32': 1, 'int16': 32767, 'int32': 2147483520}[dtype]
                tone = (np.sin(2*np.pi*440*np.arange(round(rate*0.5))/rate)*0.15*scale).astype(dtype)
                def callback(data, frames, timing, status):
                    if direction == 'input':
                        peak[0] = max(peak[0], abs(float(data.min()))/scale, abs(float(data.max()))/scale)
                    else:
                        data.fill(0)
                        count = min(frames, len(tone)-frame[0])
                        for channel in range(channels):
                            data[:count, channel] = tone[frame[0]:frame[0]+count]
                        frame[0] += count
                        if frame[0] == len(tone):
                            raise sd.CallbackStop
                cls = sd.InputStream if direction == 'input' else sd.OutputStream
                stream = cls(device=index, channels=channels, samplerate=rate, dtype=dtype,
                             extra_settings=sd.WasapiSettings(exclusive=True), callback=callback,
                             latency='low', blocksize=0)
                stream.start()
                loop = QEventLoop()
                QTimer.singleShot(900 if direction == 'input' else 650, loop.quit)
                loop.exec()
                if direction == 'input' and not stream.active:
                    raise RuntimeError('麥克風測試串流提前停止')
                return peak[0], rate, dtype
            except Exception as exc:
                errors.append(f'{rate} Hz/{dtype}: {exc}')
            finally:
                if stream:
                    stream.close()
    raise RuntimeError('WASAPI 獨占設備測試失敗：\n' + '\n'.join(errors))


class ExclusivePlayer(QObject):
    positionChanged = Signal(int)
    playbackStateChanged = Signal(object)
    mediaStatusChanged = Signal(object)
    errorOccurred = Signal(object, str)

    def __init__(self, microphone, parent=None):
        super().__init__(parent)
        self.mic = microphone
        self.stream = None
        self._source = QUrl()
        self._state = QMediaPlayer.PlaybackState.StoppedState
        self.rate = 48000
        self.frame = 0
        self.backing = None
        self.guide = None
        self.guide_enabled = False
        self.music_gain = 0.8
        self.guide_gain = 0.12
        self.input_device = self.output_device = None
        self.capture = deque(maxlen=256)
        self.capture_enabled = False
        self.capture_overflow = False
        self.peak = 0.0
        self.failure = None
        self.ended = False
        self.input_overflow = self.output_underflow = self.clipped_blocks = 0
        self.callback_count = 0
        self.max_callback_seconds = 0.0
        self.last_frames = 0
        self._previous_switch_interval = None
        self.log_path = None
        self._last_diagnostic = 0.0
        self.started = 0.0
        self.input_scratch = np.empty((65536, 2), dtype=np.float32)
        self.output_scratch = np.empty((65536, 2), dtype=np.float32)
        self.mix_scratch = np.empty((65536, 2), dtype=np.float32)
        self.timer = QTimer(self)
        self.timer.setInterval(20)
        self.timer.timeout.connect(self._poll)

    def configure(self, input_device, output_device):
        self.input_device, self.output_device = input_device, output_device

    def source(self):
        return self._source

    def duration(self):
        return round(len(self.backing) * 1000 / self.rate) if self.backing is not None else 0

    def position(self):
        return round(self.frame * 1000 / self.rate)

    def playbackState(self):
        return self._state

    def setSource(self, url):
        self.stop()
        self._source = url
        self.backing = self.guide = None

    def setPosition(self, value):
        self.frame = max(0, min(len(self.backing), round(value * self.rate / 1000))) if self.backing is not None else 0

    def set_guide(self, path):
        self.guide = read_audio(path, self.rate, self.channels) if path else None

    def play(self):
        if self.stream:
            self._state = QMediaPlayer.PlaybackState.PlayingState
            self.playbackStateChanged.emit(self._state)
            return
        try:
            inp = device_index(self.input_device, 'input')
            out = device_index(self.output_device, 'output')
            di, do = sd.query_devices(inp), sd.query_devices(out)
            cin, cout = min(2, di['max_input_channels']), min(2, do['max_output_channels'])
            failures = []
            for rate in dict.fromkeys([int(do['default_samplerate']), 48000, 44100]):
                for dtype in ('float32', 'int16', 'int32'):
                    stream = None
                    try:
                        settings = (sd.WasapiSettings(exclusive=True), sd.WasapiSettings(exclusive=True))
                        sd.check_input_settings(device=inp, channels=cin, samplerate=rate, dtype=dtype, extra_settings=settings[0])
                        sd.check_output_settings(device=out, channels=cout, samplerate=rate, dtype=dtype, extra_settings=settings[1])
                        self.backing = read_audio(self._source.toLocalFile(), rate, cout)
                        self.rate, self.channels, self.dtype = rate, cout, dtype
                        self.frame = 0
                        self.failure = None
                        self.ended = self.capture_overflow = False
                        self.input_overflow = self.output_underflow = self.clipped_blocks = 0
                        self.callback_count = 0
                        self.max_callback_seconds = 0.0
                        self.started = time.monotonic()
                        self.capture.clear()
                        stream = sd.Stream(device=(inp, out), channels=(cin, cout), samplerate=rate,
                                           dtype=dtype, latency='low', blocksize=0,
                                           extra_settings=settings, callback=self._callback)
                        self.stream = stream
                        self._state = QMediaPlayer.PlaybackState.PlayingState
                        self.mic._exclusive_started(rate)
                        # UI rendering and QRunnables share Python's GIL with this callback.
                        # Reduce the preferred timeslice; this is not a real-time guarantee.
                        self._previous_switch_interval = sys.getswitchinterval()
                        sys.setswitchinterval(min(self._previous_switch_interval, 0.001))
                        stream.start()
                        folder = Path(__file__).resolve().parents[1] / '.app_data/audio_logs'
                        folder.mkdir(parents=True, exist_ok=True)
                        self.log_path = folder / (datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.jsonl')
                        self._write_diagnostic('start')
                        self.timer.start()
                        self.playbackStateChanged.emit(self._state)
                        self.mic.bufferInfoChanged.emit(f'WASAPI 獨占 · {rate} Hz · {dtype} · API 輸入／輸出 '
                            f'{stream.latency[0]*1000:.1f}／{stream.latency[1]*1000:.1f} ms（非實測）')
                        return
                    except Exception as exc:
                        if stream:
                            stream.close()
                        self.stream = None
                        self._restore_switch_interval()
                        failures.append(f'{rate} Hz/{dtype}: {exc}')
            raise RuntimeError('WASAPI 獨占無法啟動，未切換其他模式。\n' + '\n'.join(failures))
        except Exception:
            self.stop()
            raise

    def _callback(self, indata, outdata, frames, timing, status):
        begin = time.perf_counter()
        outdata.fill(0)
        try:
            self.callback_count += 1
            self.last_frames = frames
            if status:
                self.input_overflow += int(status.input_overflow)
                self.output_underflow += int(status.output_underflow)
            if frames > len(self.input_scratch):
                raise RuntimeError('音訊 callback 超過預配置容量')
            if self._state != QMediaPlayer.PlaybackState.PlayingState:
                return
            inputs = self.input_scratch[:frames, :indata.shape[1]]
            scale = {'float32': 1, 'int16': 1/32768, 'int32': 1/2147483648}[self.dtype]
            np.multiply(indata, scale, out=inputs, casting='unsafe')
            self.peak = max(abs(float(inputs.min())), abs(float(inputs.max())))
            if self.capture_enabled:
                if len(self.capture) == self.capture.maxlen:
                    self.capture_overflow = True
                else:
                    # Bounded transfer only; WAV writes happen on the UI side.
                    self.capture.append(inputs[:, 0].copy())
            mixed = self.output_scratch[:frames, :self.channels]
            mixed.fill(0)
            start = self.frame
            count = min(frames, max(0, len(self.backing) - start))
            if count:
                np.multiply(self.backing[start:start+count], self.music_gain, out=mixed[:count])
            scratch = self.mix_scratch[:frames, :self.channels]
            if self.guide_enabled and self.guide is not None:
                n = min(frames, max(0, len(self.guide) - start))
                if n:
                    np.multiply(self.guide[start:start+n], self.guide_gain, out=scratch[:n])
                    np.add(mixed[:n], scratch[:n], out=mixed[:n])
            if self.mic.monitor_enabled:
                for channel in range(self.channels):
                    np.multiply(inputs[:, 0], self.mic.monitor_volume, out=scratch[:, channel])
                np.add(mixed, scratch, out=mixed)
            self.clipped_blocks += int(float(mixed.max()) > 1 or float(mixed.min()) < -1)
            np.clip(mixed, -1, 1, out=mixed)
            multiplier = {'float32': 1, 'int16': 32767, 'int32': 2147483520}[self.dtype]
            np.multiply(mixed, multiplier, out=outdata, casting='unsafe')
            self.frame += count
            if self.frame >= len(self.backing):
                self.ended = True
                raise sd.CallbackStop
        except sd.CallbackStop:
            raise
        except Exception as exc:
            self.failure = str(exc)
            outdata.fill(0)
            raise sd.CallbackAbort
        finally:
            self.max_callback_seconds = max(self.max_callback_seconds, time.perf_counter() - begin)

    def _poll(self):
        self.mic.levelChanged.emit(min(100, round(self.peak * 100)))
        self.mic._drain()
        self.positionChanged.emit(self.position())
        if time.monotonic() - self._last_diagnostic >= 1:
            self._last_diagnostic = time.monotonic()
            self._write_diagnostic('stats')
            self.mic.bufferInfoChanged.emit(f'WASAPI 獨占 · 輸出缺資料 {self.output_underflow} · '
                f'輸入溢位 {self.input_overflow} · 削波 {self.clipped_blocks} · callback 最大 '
                f'{self.max_callback_seconds*1000:.2f} ms')
        if self.capture_overflow:
            self.capture_overflow = False
            self.mic.finish_recording()
            self.mic.failed.emit('錄音緩衝已滿，這次錄音已結束；請降低背景工作負載。')
        if self.stream and not self.stream.active:
            if self.ended:
                self.stop()
                self.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
            else:
                message = self.failure or 'WASAPI 獨占串流中斷，請檢查設備連接。'
                self.stop()
                self.errorOccurred.emit(QMediaPlayer.Error.ResourceError, message)

    def pause(self):
        self._state = QMediaPlayer.PlaybackState.PausedState
        self.playbackStateChanged.emit(self._state)

    def resume(self):
        if self.stream:
            self.play()

    def suspend(self):
        self.pause()

    def stop(self):
        self.timer.stop()
        if self.stream:
            stream, self.stream = self.stream, None
            try:
                try:
                    stream.abort()
                finally:
                    stream.close()
                self._write_diagnostic('stop')
            finally:
                self._restore_switch_interval()
        self._restore_switch_interval()
        self._state = QMediaPlayer.PlaybackState.StoppedState
        self.frame = 0
        self.playbackStateChanged.emit(self._state)

    def _restore_switch_interval(self):
        if self._previous_switch_interval is not None:
            sys.setswitchinterval(self._previous_switch_interval)
            self._previous_switch_interval = None

    def _write_diagnostic(self, event):
        if not self.log_path:
            return
        record = {'event': event, 'elapsed': time.monotonic() - self.started,
                  'rate': self.rate, 'dtype': self.dtype, 'input': str(self.input_device.description()) if self.input_device else 'default',
                  'output': str(self.output_device.description()) if self.output_device else 'default',
                  'output_underflow': self.output_underflow, 'input_overflow': self.input_overflow,
                  'clipped_blocks': self.clipped_blocks, 'callback_count': self.callback_count,
                  'last_frames': self.last_frames, 'max_callback_ms': self.max_callback_seconds*1000,
                  'cpu_load': self.stream.cpu_load if self.stream else None,
                  'monitor': self.mic.monitor_enabled, 'recording': self.capture_enabled,
                  'guide': self.guide_enabled, 'music_gain': self.music_gain}
        record.update({'guide_gain': self.guide_gain, 'monitor_gain': self.mic.monitor_volume,
                       'input_peak': self.peak, 'source': self._source.toLocalFile(),
                       'api_latency': self.stream.latency if self.stream else None})
        try:
            with self.log_path.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + '\n')
        except OSError:
            # Diagnostic storage failure must not interrupt audio playback.
            pass
