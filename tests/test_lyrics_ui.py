import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QByteArray, QObject, Signal
from PySide6.QtWidgets import QApplication, QCheckBox, QWidget

from app.karaoke_window import KaraokeWindow
from app.consumer import ConsumerWindow
from app.lyrics import assets_dir, load_selected, parse_lrc, save_lyric_state
from app.lyrics_dialog import LyricsDialog
from app.storage import save_catalog


class Reply(QObject):
    finished = Signal()

    def __init__(self, status=200, payload=None, retry=''):
        super().__init__()
        self.status, self.payload, self.retry = status, payload, retry
        self.aborted = False

    def attribute(self, _):
        return self.status

    def readAll(self):
        return QByteArray(json.dumps(self.payload).encode())

    def rawHeader(self, _):
        return QByteArray(self.retry.encode())

    def errorString(self):
        return 'fixture'

    def abort(self):
        self.aborted = True
        self.finished.emit()


class LyricsUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        existing = QApplication.instance()
        if existing is not None and not isinstance(existing, QApplication):
            raise unittest.SkipTest('Widget checks require a separate process: python -m unittest discover -s tests -p test_lyrics_ui.py')
        cls.app = existing or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.controller = QWidget()
        self.controller.workspace = Path(self.temp.name)
        self.controller.settings = {}
        self.controller.song_map = {'song': {'title': 'Original', 'artist': 'Singer', 'path': 'missing.mp3'}}
        self.controller._open_asset_folder = Mock()
        self.dialog = LyricsDialog(self.controller, 'song')

    def tearDown(self):
        self.dialog.reject()
        self.controller.close()
        self.controller.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def respond(self, reply):
        self.dialog.reply = reply
        reply.finished.connect(self.dialog._received)
        reply.finished.emit()

    def test_request_preview_apply_and_offline_reopen_preserve_manual(self):
        reply = Reply()
        with patch.object(self.dialog.network, 'get', return_value=reply) as get:
            self.dialog.search()
            request = get.call_args.args[0]
            self.assertIn('api/get', request.url().toString())
            self.assertIn('track_name=Original', request.url().toString())
            self.assertEqual(b'KaraMorph/0.1.0-preview', bytes(request.rawHeader('User-Agent')))
            self.dialog.cancel_search()
            self.assertTrue(reply.aborted)
        candidate = {'id': 1, 'trackName': 'Original', 'artistName': 'Singer', 'duration': 100,
                     'plainLyrics': 'download', 'syncedLyrics': '[00:10]download'}
        self.respond(Reply(200, [candidate]))
        self.dialog.results.setCurrentRow(0)
        self.assertIn('download', self.dialog.preview_text.toPlainText())
        self.dialog.manual.setPlainText('my manual')
        self.dialog.apply_candidate()
        self.dialog.save()
        text, timeline, state, warning = load_selected(self.controller.workspace, 'song')
        self.assertEqual(timeline.times, [10000])
        self.assertFalse(warning)
        self.assertEqual((assets_dir(self.controller.workspace, 'song')/'lyrics.txt').read_text(encoding='utf-8'), 'my manual')
        self.assertEqual(state['lrclib']['id'], 1)
        # Reopening cached sources must not send requests.
        with patch('app.lyrics_dialog.QNetworkAccessManager.get', side_effect=AssertionError('offline')):
            reopened = LyricsDialog(self.controller, 'song')
            self.assertEqual(reopened.source.currentData(), 'lrclib')
            reopened.reject()

    def test_no_results_bad_response_fallback_limits_and_cancel(self):
        self.respond(Reply(200, []))
        self.assertIn('查無', self.dialog.status.text())
        self.respond(Reply(200, {'wrong': 'payload'}))
        self.assertIn('無法', self.dialog.status.text())
        self.dialog.endpoint, self.dialog.params = 'get', {'track_name': 'Title', 'duration': '100'}
        self.respond(Reply(404))
        self.assertEqual(self.dialog.endpoint, 'search')
        self.assertNotIn('duration', self.dialog.params)
        self.assertTrue(self.dialog.retry.isActive())
        self.dialog.cancel_search()
        self.respond(Reply(429, retry='5'))
        self.assertGreaterEqual(self.dialog.retry.remainingTime(), 4500)
        self.dialog.cancel_search()
        self.assertFalse(self.dialog.retry.isActive())
        self.dialog._timed_out()
        self.assertIn('逾時', self.dialog.status.text())
        self.respond(Reply(200, [{'id': 2, 'plainLyrics': 'plain only'}]))
        self.dialog.results.setCurrentRow(0)
        self.dialog.apply_candidate()
        self.assertEqual(self.dialog.source.currentData(), 'downloaded')

    def test_canvas_tempo_seek_delay_and_only_repaint_on_line_change(self):
        c = self.controller
        for name in ['previous_song', 'toggle_pause', 'stop_playback', 'next_song', 'set_guide_vocal',
                     '_set_singing_volume', '_set_guide_volume']:
            setattr(c, name, Mock())
        c.audio, c.guide_audio = Mock(), Mock()
        c.audio.volume.return_value = .6
        c.guide_audio.volume.return_value = .12
        c.record_checkbox = QCheckBox()
        c.microphone = Mock()
        c.player = Mock()
        c.player.position.return_value = 0
        c.player.duration.return_value = 150000
        window = KaraokeWindow(c)
        window.set_song('Title', '', [], 150000, timeline=parse_lrc('[01:00]first\n[01:10]'), playback_speed=.8)
        self.assertEqual(window.canvas.progress, -1)
        with patch.object(window.canvas, 'update') as update:
            window.update_progress()
            update.assert_not_called()
            c.player.position.return_value = 75000
            window.update_progress()
            update.assert_called_once()
        self.assertEqual(window.canvas.progress, 0)
        saved = []
        window.save_delay = saved.append
        window.shift_lines(1)
        self.assertEqual(saved, [200])
        self.assertEqual(window.canvas.progress, -1)
        window.adjust_delay(-200)
        c.player.position.return_value = 90000
        window.update_progress()
        self.assertEqual(window.canvas.lines[int(window.canvas.progress)], '')
        c.player.position.return_value = 75000
        window.update_progress()
        self.assertEqual(window.canvas.progress, 0)
        window.show()
        window.grab()  # exercise cached text drawing
        window.set_song('Plain', 'previous\ncurrent\nnext', [], 150000)
        window.canvas.progress = 1
        window.grab()
        self.assertFalse(window.canvas.synced)
        window.close()
        window.deleteLater()

    def test_consumer_sync_uses_variant_speed_and_cancels_vocal_analysis(self):
        c = self.controller
        folder = assets_dir(c.workspace, 'song')
        folder.mkdir(parents=True)
        (folder/'local.lrc').write_text('[01:00]line', encoding='utf-8')
        save_lyric_state(c.workspace, 'song', {'source': 'local', 'offsets': {'slow': 400}})
        save_catalog(c.workspace, 'song', {'variants': [{'id': 'slow', 'kind': 'fx', 'speed': .8}]})
        c.karaoke_window, c.info, c.player = Mock(), Mock(), Mock()
        c.player.duration.return_value = 150000
        task = Mock()
        c.activity_tasks = {('song', 'slow'): task}
        c._preview_paths = Mock(return_value=(None, None))
        c._set_guide_source = Mock()
        c._karaoke_vocal_path = Mock()
        c.queue_display_label = lambda item: ConsumerWindow.queue_display_label(c, item)
        ConsumerWindow._sync_karaoke_song(c, {'song_id': 'song', 'variant_id': 'slow', 'label': 'Slow'})
        kwargs = c.karaoke_window.set_song.call_args.kwargs
        self.assertEqual(kwargs['playback_speed'], .8)
        self.assertEqual(kwargs['delay_ms'], 400)
        self.assertEqual(kwargs['timeline'].index_at(75400, .8, 400), 0)
        task.cancel.assert_called_once()
        c._karaoke_vocal_path.assert_not_called()
        self.assertFalse(c.activity_tasks)
        kwargs['save_delay'](600)
        self.assertEqual(load_selected(c.workspace, 'song')[2]['offsets']['slow'], 600)


if __name__ == '__main__':
    unittest.main()
