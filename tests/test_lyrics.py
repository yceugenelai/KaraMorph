import tempfile
import unittest
from pathlib import Path

from app.karaoke_assets import read_lyrics, save_lyrics
from app.lyrics import (assets_dir, effective_speed, load_selected, parse_lrc,
                        save_delay, save_lyric_state)


class LyricsTests(unittest.TestCase):
    def test_timing_intro_interlude_seek_tempo_and_offsets(self):
        timeline = parse_lrc('[ar:Artist]\n[offset:100]\n[00:10.00]one\n[00:20.00]\n[01:00.00][02:00.00]repeat')
        self.assertEqual(timeline.index_at(0), -1)
        self.assertEqual(timeline.index_at(9900), 0)
        self.assertEqual(timeline.lines[timeline.index_at(25000)], '')
        self.assertEqual(timeline.index_at(59899), 1)
        self.assertEqual(timeline.index_at(59900), 2)
        self.assertEqual(timeline.index_at(119900), 3)
        self.assertEqual(timeline.index_at(10000), 0)  # seek backwards
        plain = parse_lrc('[01:00.00]line')
        for speed, target in [(0.8, 75000), (1.2, 50000), (0.72, 60000/0.72)]:
            self.assertEqual(plain.index_at(target-1, speed), -1)
            self.assertEqual(plain.index_at(target, speed), 0)
            self.assertEqual(plain.index_at(target+200-1, speed, 200), -1)
            self.assertEqual(plain.index_at(target+200, speed, 200), 0)

    def test_malformed_and_unsupported_never_silently_partially_parse(self):
        for text in ['', '[00:99]bad', '[00:10]good\nbad', '[00:10][00:99]bad', '[00:10]<00:10.00>word']:
            with self.assertRaises(ValueError, msg=text):
                parse_lrc(text)
        self.assertEqual(parse_lrc('[00:01.2]a\n[00:02.123]b').times, [1200, 2123])

    def test_offline_sources_preserve_manual_and_per_version_delay(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            save_lyrics(root, 'song', 'manual\n\nlyrics')
            folder = assets_dir(root, 'song')
            (folder/'lrclib.lrc').write_text('[00:10]download', encoding='utf-8')
            (folder/'lrclib.txt').write_text('download', encoding='utf-8')
            save_lyric_state(root, 'song', {'source': 'lrclib', 'offsets': {}})
            save_delay(root, 'song', 'slow', 200)
            save_delay(root, 'song', 'fast', -400)
            text, timeline, state, warning = load_selected(root, 'song')
            self.assertEqual(text, 'download')
            self.assertEqual(timeline.times, [10000])
            self.assertEqual(state['offsets'], {'slow': 200, 'fast': -400})
            self.assertFalse(warning)
            self.assertEqual(read_lyrics(root, 'song'), 'manual\n\nlyrics')
            (folder/'lrclib.lrc').write_text('[00:99]bad', encoding='utf-8')
            text, timeline, _, warning = load_selected(root, 'song')
            self.assertIsNone(timeline)
            self.assertTrue(warning)
            self.assertNotIn('[', text)
            save_lyric_state(root, 'song', {'source': 'manual'})
            self.assertEqual(load_selected(root, 'song')[0], 'manual\n\nlyrics')

    def test_source_chain_validation_and_style_inheritance(self):
        rows = [{'id': 'a', 'kind': 'fx', 'speed': .8, 'source_id': 'instrumental'},
                {'id': 'b', 'kind': 'fx', 'speed': .9, 'source_id': 'a'},
                {'id': 'jazz', 'source_id': 'b'}]
        self.assertAlmostEqual(effective_speed('jazz', rows), .72)
        self.assertEqual(effective_speed('original', rows), 1)
        for broken in [[{'id': 'a', 'source_id': 'a'}],
                       [{'id': 'a', 'source_id': 'missing'}],
                       [{'id': 'a', 'kind': 'fx', 'speed': float('nan')}],
                       [{'id': 'a', 'kind': 'fx', 'speed': 0}]]:
            with self.assertRaises(ValueError):
                effective_speed('a', broken)


if __name__ == '__main__':
    unittest.main()
