import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app.storage import merge_library, scan_songs


class LibrarySyncTests(unittest.TestCase):
    def test_reload_matches_actual_files_and_preserves_generated_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'input'
            source.mkdir()
            first, second = source / 'A.mp3', source / 'B.mp3'
            first.write_bytes(b'first')
            second.write_bytes(b'second')
            with patch('app.storage.data_dir', return_value=root):
                initial = merge_library(source, scan_songs(source))
                generated = root / 'outputs/songs' / initial[0]['id'] / 'keep.txt'
                generated.parent.mkdir(parents=True)
                generated.write_text('keep')
                moved = root / 'moved.mp3'
                first.rename(moved)
                self.assertEqual([s['title'] for s in merge_library(source, scan_songs(source))], ['B'])
                self.assertEqual(generated.read_text(), 'keep')
                moved.rename(first)
                self.assertEqual(merge_library(source, scan_songs(source)), initial)
                first.unlink()
                second.unlink()
                self.assertEqual(merge_library(source, scan_songs(source)), [])

    def test_legacy_blacklist_and_missing_entries_are_discarded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'input'
            source.mkdir()
            (source / 'present.mp3').write_bytes(b'present')
            present = scan_songs(source)[0]
            (root / 'library.json').write_text(json.dumps({'source_dir':str(source.resolve()),
                'songs':[dict(id='missing', title='Missing', path=str(source/'missing.mp3'), available=False)],
                'removed_ids':[present['id']]}))
            with patch('app.storage.data_dir', return_value=root):
                self.assertEqual(merge_library(source, scan_songs(source)), [present])
            saved = json.loads((root / 'library.json').read_text())
            self.assertEqual(saved['songs'], [present])
            self.assertNotIn('removed_ids', saved)
