import tempfile
import unittest
import wave
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import sounddevice as sd
from PySide6.QtCore import QCoreApplication
from PySide6.QtMultimedia import QMediaPlayer

from app.exclusive_audio import ExclusivePlayer, device_index
from app.microphone import MicrophoneService


class ExclusiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def make_player(self, dtype='float32', length=480):
        mic = MicrophoneService()
        player = ExclusivePlayer(mic)
        mic.exclusive_player = player
        mic.device = Mock()
        mic.device.description.return_value = 'test mic'
        mic.device.id.return_value = b'test'
        mic._exclusive_started(48000)
        player.backing = np.full((length, 2), 0.2, dtype=np.float32)
        player.channels = 2
        player.dtype = dtype
        player._state = QMediaPlayer.PlaybackState.PlayingState
        return mic, player

    def test_backing_guide_monitor_pause_and_formats(self):
        for dtype in ('float32', 'int16', 'int32'):
            mic, player = self.make_player(dtype)
            player.music_gain = 0.5
            player.guide = np.full((480, 2), 0.1, dtype=np.float32)
            player.guide_enabled = True
            player.guide_gain = 0.5
            mic.set_monitor(True)
            mic.set_monitor_volume(0.5)
            scale = {'float32': 1, 'int16': 32768, 'int32': 2147483648}[dtype]
            output = np.empty((128, 2), dtype=dtype)
            inputs = np.full((128, 2), 0.1 * scale, dtype=dtype)
            player._callback(inputs, output, 128, None, None)
            np.testing.assert_allclose(output.astype(np.float64) / scale, 0.2, atol=0.0001)
            player.pause()
            player._callback(inputs, output, 128, None, None)
            self.assertTrue((output == 0).all())
            self.assertEqual(player.frame, 128)
            mic.stop()

    def test_end_and_recording_are_independent_of_monitor(self):
        mic, player = self.make_player(length=256)
        mic.set_monitor(False)
        with tempfile.TemporaryDirectory() as folder:
            mic.start_recording(Path(folder), 'song', 'variant', Path('backing.wav'))
            inputs = np.full((128, 2), 0.1, dtype=np.float32)
            output = np.empty_like(inputs)
            player._callback(inputs, output, 128, None, None)
            with self.assertRaises(sd.CallbackStop):
                player._callback(inputs, output, 128, None, None)
            mic.finish_recording()
            files = list(Path(folder).rglob('microphone.wav'))
            self.assertEqual(len(files), 1)
            with wave.open(str(files[0])) as recording:
                self.assertEqual(recording.getnframes(), 256)
                self.assertEqual(recording.getnchannels(), 1)
            metadata = json.loads((files[0].parent / 'session.json').read_text())
            self.assertEqual(metadata['played_backing'], 'played_backing.wav')
            with wave.open(str(files[0].parent / 'played_backing.wav')) as backing:
                self.assertEqual(backing.getnframes(), 256)
                self.assertEqual(backing.getnchannels(), 2)
                samples = np.frombuffer(backing.readframes(256), '<i2').reshape(-1, 2)
                np.testing.assert_allclose(samples[:240]/32767, .2, atol=.0001)
            mic.stop()

    def test_effect_audio_and_microphone_stay_separate_and_use_audible_source_time(self):
        mic, player = self.make_player(length=48000)
        player.stream = Mock()
        player.music_gain = .5
        player.guide_gain = .5
        player.guide_enabled = True
        mic.monitor_enabled = True
        mic.monitor_volume = .5
        renderer = Mock()
        renderer.position = 0
        renderer.ended = False
        renderer.error = None
        def rendered(backing, guide, count):
            backing.fill(.3)
            guide.fill(.1)
            renderer.position = 256
            return count
        renderer.read_into.side_effect = rendered
        player.renderer = renderer
        with tempfile.TemporaryDirectory() as folder:
            mic.start_recording(Path(folder), 'song', 'variant', Path('backing.wav'))
            with patch('app.exclusive_audio.time.monotonic', return_value=10.):
                player._callback(np.full((128, 2), .1, np.float32),
                                 output := np.empty((128, 2), np.float32), 128,
                                 SimpleNamespace(outputBufferDacTime=1.1, currentTime=1.), None)
            mic.finish_recording()
            for name, level in [('microphone.wav', .1), ('played_backing.wav', .3)]:
                path = next(Path(folder).rglob(name))
                with wave.open(str(path)) as audio:
                    self.assertEqual(audio.getnframes(), 128)
                    samples = np.frombuffer(audio.readframes(128), '<i2') / 32767
                    np.testing.assert_allclose(samples, level, atol=.0001)
        np.testing.assert_allclose(output, .25, atol=1e-6)
        with patch('app.exclusive_audio.time.monotonic', return_value=10.1):
            self.assertEqual(player.position(), 0)
        with patch('app.exclusive_audio.time.monotonic', return_value=10.2):
            self.assertEqual(player.position(), round(256*1000/48000))
        mic.stop()

    def test_shared_mode_prepares_the_same_pipeline_without_exclusive_device_flags(self):
        mic = MicrophoneService()
        player = ExclusivePlayer(mic, exclusive=False)
        mic.exclusive_player = player
        mic.device = Mock()
        mic.device.description.return_value = 'test mic'
        mic.device.id.return_value = b'test'
        pcm = np.zeros((5000, 2), np.float32)
        stream = Mock()
        stream.latency = (.01, .01)
        with patch('app.exclusive_audio.device_index', return_value=0), \
             patch('app.exclusive_audio.sd.query_devices', return_value=dict(max_input_channels=2, max_output_channels=2, default_samplerate=48000)), \
             patch('app.exclusive_audio.sd.check_input_settings'), patch('app.exclusive_audio.sd.check_output_settings'), \
             patch('app.exclusive_audio.sd.WasapiSettings') as wasapi, \
             patch('app.exclusive_audio.sd.Stream', return_value=stream), \
             patch('app.exclusive_audio.read_audio', return_value=pcm), \
             patch.object(player, '_write_diagnostic'):
            try:
                player.play()
                self.assertIsNotNone(player.renderer)
                self.assertTrue(all(call.kwargs == dict(exclusive=False) for call in wasapi.call_args_list))
                stream.start.assert_called_once()
            finally:
                mic.stop()

    def test_seek_keeps_requested_target_even_if_final_callback_advances_old_clock(self):
        mic, player = self.make_player(length=48000)
        player.stream = Mock()
        player.renderer = Mock()
        player.stream.stop.side_effect = lambda: setattr(player, 'frame', 1234)
        replacement = Mock()
        with patch('app.exclusive_audio.PlaybackRenderer', return_value=replacement) as factory:
            player.setPosition(500)
        self.assertEqual(factory.call_args.args[3], 24000)
        self.assertEqual(player.frame, 24000)
        mic.stop()

    def test_failed_seek_stops_instead_of_resuming_a_closed_worker(self):
        mic, player = self.make_player(length=48000)
        player.stream = Mock()
        player.renderer = Mock()
        with patch('app.exclusive_audio.PlaybackRenderer', side_effect=RuntimeError('fixture')):
            with self.assertRaisesRegex(RuntimeError, 'fixture'):
                player.setPosition(500)
        self.assertIsNone(player.stream)
        self.assertIsNone(player.renderer)
        self.assertEqual(player.playbackState(), QMediaPlayer.PlaybackState.StoppedState)
        mic.stop()

    def test_bounded_recording_buffer_reports_overflow(self):
        mic, player = self.make_player(length=48000)
        player.capture_enabled = True
        for _ in range(256):
            player.capture.append(np.zeros(1, dtype=np.float32))
        player._callback(np.zeros((128, 2), dtype=np.float32), np.empty((128, 2), dtype=np.float32), 128, None, None)
        self.assertTrue(player.capture_overflow)
        self.assertEqual(len(player.capture), 256)
        mic.stop()

    def test_distinguishes_underflow_from_clipping_without_monitor(self):
        mic, player = self.make_player()
        mic.set_monitor(False)
        player.backing.fill(2)
        status = SimpleNamespace(input_overflow=True, output_underflow=True)
        output = np.empty((128, 2), dtype=np.float32)
        player._callback(np.zeros_like(output), output, 128, None, status)
        self.assertEqual(player.output_underflow, 1)
        self.assertEqual(player.input_overflow, 1)
        self.assertEqual(player.clipped_blocks, 1)
        self.assertTrue((output <= 1).all())
        mic.stop()

    def test_device_mapping_uses_wasapi_only_and_rejects_ambiguity(self):
        apis = [{'name': 'MME'}, {'name': 'Windows WASAPI', 'default_input_device': 1}]
        device = Mock()
        device.isNull.return_value = False
        device.description.return_value = 'mic'
        devices = [{'hostapi': 0, 'name': 'mic', 'max_input_channels': 2},
                   {'hostapi': 1, 'name': 'mic', 'max_input_channels': 2}]
        with patch('app.exclusive_audio.sd.query_hostapis', return_value=apis), patch('app.exclusive_audio.sd.query_devices', return_value=devices):
            self.assertEqual(device_index(device, 'input'), 1)
            devices.append(devices[1])
            with self.assertRaises(RuntimeError):
                device_index(device, 'input')


if __name__ == '__main__':
    unittest.main()
