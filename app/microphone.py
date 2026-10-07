"""Qt compatibility capture, exclusive audio bridge, and independent WAV recording."""

import uuid
import wave
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtMultimedia import QtAudio, QAudioFormat, QAudioSink, QAudioSource, QMediaDevices

from app.recordings import recordings_dir
from app.storage import atomic_json


class MicrophoneService(QObject):
    levelChanged = Signal(int)
    failed = Signal(str)
    recordingFinished = Signal(str)
    bufferInfoChanged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.source = None
        self.input_stream = None
        self.monitor = None
        self.monitor_stream = None
        self.monitor_enabled = False
        self.monitor_volume = 0.25
        self.exclusive_player = None
        self.format = None
        self.device = None
        self.record_file = None
        self.record_temp = None
        self.record_metadata = None
        self.record_frames = 0
        self.timer = QTimer(self)
        self.timer.setInterval(20)
        self.timer.timeout.connect(self._drain)

    @property
    def is_recording(self):
        return self.record_file is not None

    def start(self, device=None, output_device=None):
        self.stop()
        self.device = device or QMediaDevices.defaultAudioInput()
        if self.device.isNull():
            raise RuntimeError("找不到可用的麥克風")
        if self.exclusive_player:
            self.exclusive_player.configure(device, output_device)
            self.source = self.exclusive_player
            return
        candidates = []
        for rate in (48000, 44100):
            for channels in (1, 2):
                fmt = QAudioFormat()
                fmt.setSampleRate(rate)
                fmt.setChannelCount(channels)
                fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
                if self.device.isFormatSupported(fmt):
                    candidates.append(fmt)
        preferred = self.device.preferredFormat()
        if preferred.isValid() and self.device.isFormatSupported(preferred):
            candidates.append(preferred)
        if not candidates:
            raise RuntimeError(f"{self.device.description()} 沒有可用的錄音格式")
        attempts = []
        for fmt in candidates:
            source = QAudioSource(self.device, fmt, self)
            stream = source.start()
            if stream is not None and source.error() == QtAudio.Error.NoError:
                self.format, self.source, self.input_stream = fmt, source, stream
                source.stateChanged.connect(self._state_changed)
                break
            attempts.append(f"{fmt.sampleRate()} Hz/{fmt.channelCount()} 聲道/{fmt.sampleFormat().name}: {source.error().name}")
            source.stop()
            source.deleteLater()
        else:
            raise RuntimeError(f"無法開啟麥克風「{self.device.description()}」。嘗試格式：{'；'.join(attempts)}。請檢查 Windows 麥克風存取權、裝置是否被其他程式佔用，以及裝置驅動。")
        self.timer.start()
        self._report_buffers()
        if self.monitor_enabled:
            self._start_monitor(output_device)

    def _start_monitor(self, output_device=None):
        if self.exclusive_player:
            return
        if not self.source or not self.format:
            return
        device = output_device or QMediaDevices.defaultAudioOutput()
        if device.isNull() or not device.isFormatSupported(self.format):
            raise RuntimeError("播放裝置不支援麥克風監聽格式")
        self.monitor = QAudioSink(device, self.format, self)
        self.monitor.setBufferSize(self.format.bytesForDuration(100000))
        self.monitor.setVolume(self.monitor_volume)
        self.monitor_stream = self.monitor.start()
        if self.monitor_stream is None or self.monitor.error() != QtAudio.Error.NoError:
            raise RuntimeError(f"無法啟動監聽：{self.monitor.error().name}")
        self._report_buffers()

    def _report_buffers(self):
        if self.exclusive_player:
            return
        if not self.source or not self.format:
            return
        input_ms = self.format.durationForBytes(self.source.bufferSize()) / 1000
        output_ms = self.format.durationForBytes(self.monitor.bufferSize()) / 1000 if self.monitor else None
        mode = "Qt 共用模式"
        output = f" · 輸出 {output_ms:g} ms" if output_ms is not None else ""
        self.bufferInfoChanged.emit(f"{mode}：實際輸入緩衝 {input_ms:g} ms{output}（非總延遲）")

    def set_monitor(self, enabled, output_device=None):
        self.monitor_enabled = enabled
        if self.exclusive_player:
            return
        if self.monitor:
            self.monitor.stop()
            self.monitor.deleteLater()
            self.monitor = None
            self.monitor_stream = None
        if enabled and self.source:
            self._start_monitor(output_device)
        elif self.source:
            self._report_buffers()

    def set_monitor_volume(self, volume):
        self.monitor_volume = max(0.0, min(1.0, volume))
        if self.monitor:
            self.monitor.setVolume(self.monitor_volume)

    def start_recording(self, workspace: Path, song_id: str, variant_id: str, backing_path: Path,
                        playback_offset_ms=0):
        if not self.source or not self.format or self.is_recording:
            raise RuntimeError("麥克風尚未啟動或已在錄音")
        self._drain()
        session_id = uuid.uuid4().hex
        folder = recordings_dir(workspace, song_id) / session_id
        folder.mkdir(parents=True)
        temp = folder / "microphone.partial.wav"
        recording = wave.open(str(temp), "wb")
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(self.format.sampleRate())
        self.record_temp = temp
        self.record_file = recording
        if self.exclusive_player:
            self.exclusive_player.capture.clear()
            self.exclusive_player.capture_enabled = True
        self.record_frames = 0
        self.record_metadata = {
            "session_id": session_id, "song_id": song_id, "variant_id": variant_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "sample_rate": self.format.sampleRate(), "channels": 1,
            "input_device": self.device.description(),
            "input_device_id": bytes(self.device.id()).hex(),
            "playback_start_offset_ms": int(playback_offset_ms),
            "backing_path_at_recording": str(backing_path),
        }

    def finish_recording(self):
        if not self.record_file:
            return
        if self.exclusive_player:
            self.exclusive_player.capture_enabled = False
        self._drain()
        recording, temp, metadata = self.record_file, self.record_temp, self.record_metadata
        frames = self.record_frames
        self.record_file = None
        self.record_temp = None
        self.record_metadata = None
        self.record_frames = 0
        recording.close()
        if frames == 0:
            temp.unlink(missing_ok=True)
            temp.parent.rmdir()
            return
        final = temp.with_name("microphone.wav")
        temp.replace(final)
        metadata["duration_ms"] = round(frames * 1000 / metadata["sample_rate"])
        atomic_json(final.parent / "session.json", metadata)
        self.recordingFinished.emit(metadata["song_id"])

    def pause(self):
        self._drain()
        if self.source:
            self.source.suspend()
        if self.monitor:
            self.monitor.suspend()

    def resume(self):
        if self.source:
            self.source.resume()
        if self.monitor:
            self.monitor.resume()

    def stop(self):
        self.timer.stop()
        if self.exclusive_player:
            self.exclusive_player.stop()
            self.exclusive_player.capture_enabled = False
            self.finish_recording()
            self.source = None
            self.format = None
            self.levelChanged.emit(0)
            self.bufferInfoChanged.emit("")
            return
        if self.source:
            self._drain()
        if self.record_file:
            self.finish_recording()
        if self.monitor:
            self.monitor.stop()
            self.monitor.deleteLater()
            self.monitor = None
            self.monitor_stream = None
        if self.source:
            source = self.source
            self.source = None
            self.input_stream = None
            source.stop()
            source.deleteLater()
        self.levelChanged.emit(0)
        self.bufferInfoChanged.emit("")

    def _drain(self):
        if self.exclusive_player:
            while self.exclusive_player.capture:
                samples = self.exclusive_player.capture.popleft()
                if self.record_file:
                    self.record_file.writeframesraw((np.clip(samples, -1, 1) * 32767).astype('<i2').tobytes())
                    self.record_frames += len(samples)
            return
        if not self.input_stream:
            return
        raw = bytes(self.input_stream.readAll())
        if not raw:
            return
        sample_format = self.format.sampleFormat()
        dtype = {QAudioFormat.SampleFormat.Int16: "<i2", QAudioFormat.SampleFormat.Int32: "<i4",
                 QAudioFormat.SampleFormat.Float: "<f4", QAudioFormat.SampleFormat.UInt8: "u1"}.get(sample_format)
        if dtype is None:
            return
        width = np.dtype(dtype).itemsize * self.format.channelCount()
        raw = raw[:len(raw) // width * width]
        if not raw:
            return
        samples = np.frombuffer(raw, dtype=dtype).reshape(-1, self.format.channelCount())
        if sample_format == QAudioFormat.SampleFormat.Float:
            samples = np.clip(samples.astype(np.float32), -1, 1) * 32767
        elif sample_format == QAudioFormat.SampleFormat.Int32:
            samples = samples.astype(np.float32) / 65536
        elif sample_format == QAudioFormat.SampleFormat.UInt8:
            samples = (samples.astype(np.float32) - 128) * 256
        data = np.clip(samples.mean(axis=1), -32768, 32767).astype("<i2").tobytes()
        mono = np.frombuffer(data, dtype="<i2")
        level = min(100, round(float(np.sqrt(np.mean(mono.astype(np.float32) ** 2))) * 100 / 8192))
        self.levelChanged.emit(level)
        if self.record_file:
            self.record_file.writeframesraw(data)
            self.record_frames += len(mono)
        if self.monitor_stream and self.monitor:
            writable = min(len(raw), self.monitor.bytesFree())
            writable -= writable % width
            if writable:
                self.monitor_stream.write(raw[:writable])

    def _state_changed(self, state):
        if self.source and state == QtAudio.State.StoppedState and self.source.error() != QtAudio.Error.NoError:
            self.failed.emit(f"麥克風中斷：{self.source.error()}")

    def _exclusive_started(self, rate):
        self.source = self.exclusive_player
        self.format = QAudioFormat()
        self.format.setSampleRate(rate)
        self.format.setChannelCount(1)
        self.format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
