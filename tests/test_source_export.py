import tempfile
import unittest
from pathlib import Path
from scripts.export_source import source_paths, export_directory, FILES, DIRECTORIES


class SourceExportTests(unittest.TestCase):
    def test_private_folders_excluded_and_license_text_retained(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in FILES:
                (root / name).write_text('source', encoding='utf-8')
            for name in DIRECTORIES:
                (root / name).mkdir()
            (root / 'docs/licenses').mkdir()
            notice = root / 'docs/licenses/ACE-Step-1.5-LICENSE'
            notice.write_text('license', encoding='utf-8')
            (root / 'app/backup').mkdir()
            (root / 'app/backup/private.json').write_text('{}')
            exported = source_paths(root)
            self.assertIn(notice, exported)
            self.assertNotIn(root / 'app/backup/private.json', exported)
            private = root / 'app/sample.mp3'
            private.write_bytes(b'private audio')
            with self.assertRaisesRegex(ValueError, 'Unexpected public source file'):
                source_paths(root)
            private.unlink()
            credential = root / 'scripts/.env.local'
            credential.write_text('secret', encoding='utf-8')
            with self.assertRaises(ValueError):
                source_paths(root)

    def test_static_artwork_allowed_only_in_designated_directories(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in FILES:
                (root / name).write_text('source', encoding='utf-8')
            for name in DIRECTORIES:
                (root / name).mkdir()
            (root / 'docs/images').mkdir()
            icon = root / 'assets/icon.png'
            screenshot = root / 'docs/images/processing.png'
            icon.write_bytes(b'placeholder')
            screenshot.write_bytes(b'placeholder')
            self.assertIn(icon, source_paths(root))
            self.assertIn(screenshot, source_paths(root))
            (root / 'app/private.png').write_bytes(b'private')
            with self.assertRaisesRegex(ValueError, 'Unexpected public source file'):
                source_paths(root)

    def test_repository_directory_matches_source_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'source'
            root.mkdir()
            for name in FILES:
                (root / name).write_text('source', encoding='utf-8')
            for name in DIRECTORIES:
                (root / name).mkdir()
            (root / '.app_data').mkdir()
            (root / '.app_data/private.json').write_text('private')
            destination = Path(temporary) / 'KaraMorph'
            export_directory(destination, root)
            exported = {p.relative_to(destination) for p in destination.rglob('*') if p.is_file()}
            self.assertEqual(exported, {p.relative_to(root) for p in source_paths(root)})
            self.assertFalse((destination / '.git').exists())
            self.assertFalse((destination / '.app_data').exists())
            sentinel = destination / 'README.md'
            sentinel.write_text('user changes')
            with self.assertRaisesRegex(ValueError, 'Existing source directory is preserved'):
                export_directory(destination, root)
            self.assertEqual(sentinel.read_text(), 'user changes')
