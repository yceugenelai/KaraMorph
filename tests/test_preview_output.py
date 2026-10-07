import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from app.consumer import ConsumerWindow


class PreviewOutputTests(unittest.TestCase):
    def test_selected_device_reaches_all_playback_paths_without_save(self):
        device = Mock()
        window = SimpleNamespace(audio_device=Mock(), audio=Mock(), guide_audio=Mock(),
                                 preview_audio=Mock(), record_backing_audio=Mock(), stem_preview=Mock())
        window.audio_device.currentData.return_value = device
        ConsumerWindow._apply_output_device(window)
        for output in (window.audio, window.guide_audio, window.preview_audio, window.record_backing_audio):
            output.setDevice.assert_called_once_with(device)
        window.stem_preview.set_device.assert_called_once_with(device)
