import io
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
import zipfile

import numpy as np
from app.playback_effects import PlaybackRenderer
from app.soundtouch import SoundTouch, ready, library_path, prepare


def drain(renderer):
    result, positions = [], []
    deadline = time.monotonic()+10
    while not renderer.ended and time.monotonic() < deadline:
        backing, guide = np.empty((257, 2), np.float32), np.empty((257, 2), np.float32)
        count = renderer.read_into(backing, guide, 257)
        if count:
            result.append(np.column_stack((backing[:count], guide[:count])))
            positions.append(renderer.position)
        else:
            time.sleep(.001)
    if renderer.error:
        raise RuntimeError(renderer.error)
    assert renderer.ended
    return np.concatenate(result), positions


class PlaybackEffectsTests(unittest.TestCase):
    def test_neutral_audio_is_unchanged_and_guide_has_same_clock(self):
        audio = np.random.default_rng(42).uniform(-.2, .2, (8000, 2)).astype(np.float32)
        factory = Mock(side_effect=AssertionError('Neutral playback must bypass DSP'))
        renderer = PlaybackRenderer(audio, audio*.5, 48000, factory=factory)
        try:
            output, positions = drain(renderer)
            np.testing.assert_array_equal(output[:, :2], audio)
            np.testing.assert_array_equal(output[:, 2:], audio*.5)
            self.assertEqual(positions[-1], len(audio))
            self.assertTrue(all(a <= b for a, b in zip(positions, positions[1:])))
            self.assertLessEqual(renderer.blocks.maxsize, 4)
        finally:
            renderer.close()

    def test_seek_source_position_and_worker_cancellation(self):
        audio = np.full((10000, 2), .2, np.float32)
        renderer = PlaybackRenderer(audio, None, 48000, start=5000)
        try:
            output, positions = drain(renderer)
            self.assertEqual(len(output), 5000)
            self.assertGreater(positions[0], 5000)
            self.assertTrue((output[:, 2:] == 0).all())
        finally:
            renderer.close()
        self.assertFalse(renderer.thread.is_alive())

    def test_worker_failure_is_not_silent_or_deadlocked(self):
        with self.assertRaisesRegex(RuntimeError, 'fixture'):
            PlaybackRenderer(np.zeros((5000, 2), np.float32), None, 48000,
                settings=(.8, 2), factory=Mock(side_effect=RuntimeError('fixture')))

    def test_asset_download_checks_archive_and_extracts_only_named_files(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as zipped:
            for name in ('SoundTouchDLL_x64.dll', 'COPYING.TXT', 'SoundTouchDLL.h'):
                zipped.writestr(name, name)
            zipped.writestr('../outside.txt', 'not extracted')
        data = archive.getvalue()
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = data
        with tempfile.TemporaryDirectory() as folder, patch('app.soundtouch.urllib.request.urlopen', return_value=response):
            with self.assertRaisesRegex(ValueError, 'checksum'):
                prepare(Path(folder))
            self.assertFalse(library_path(Path(folder)).exists())
            with patch('app.soundtouch.ARCHIVE_SHA256', hashlib.sha256(data).hexdigest()):
                path = prepare(Path(folder))
            self.assertEqual(path.read_bytes(), b'SoundTouchDLL_x64.dll')
            self.assertFalse((Path(folder)/'outside.txt').exists())

    @unittest.skipUnless(ready(), 'Native SoundTouch must be prepared separately')
    def test_native_pitch_tempo_and_four_channel_alignment(self):
        rate = 48000
        tone = (.1*np.sin(2*np.pi*440*np.arange(rate*2)/rate)).astype(np.float32)
        audio = np.column_stack((tone, tone))
        for tempo, key in ((.7, -6), (1., 6), (1.3, 0), (1.3, 6)):
            renderer = PlaybackRenderer(audio, audio*.5, rate, settings=(tempo, key))
            try:
                output, positions = drain(renderer)
                self.assertLess(abs(len(output)/rate-2/tempo), .08)
                np.testing.assert_allclose(output[:, 2:], output[:, :2]*.5, atol=1e-6)
                np.testing.assert_allclose(output[:, 0], output[:, 1], atol=1e-6)
                center = output[len(output)//3:len(output)//3+rate//2, 0]
                peak = np.argmax(abs(np.fft.rfft(center*np.hanning(len(center)))))*rate/len(center)
                self.assertLess(abs(peak-440*2**(key/12)), 4)
                self.assertTrue(all(a <= b for a, b in zip(positions, positions[1:])))
            finally:
                renderer.close()

    @unittest.skipUnless(ready(), 'Native SoundTouch must be prepared separately')
    def test_live_parameter_changes_do_not_restart_worker(self):
        audio = np.random.default_rng(4).uniform(-.1, .1, (48000, 2)).astype(np.float32)
        renderer = PlaybackRenderer(audio, None, 48000)
        thread = renderer.thread
        try:
            scratch = np.empty((1024, 2), np.float32)
            renderer.read_into(scratch, scratch.copy(), 1024)
            renderer.settings = (.7, -6)
            renderer.read_into(scratch, scratch.copy(), 1024)
            renderer.settings = (1.3, 6)
            output, positions = drain(renderer)
            self.assertTrue(np.isfinite(output).all())
            self.assertIs(renderer.thread, thread)
        finally:
            renderer.close()
