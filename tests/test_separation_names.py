import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('safe_names', ROOT / 'workers/separation/safe_names.py')
safe_names = importlib.util.module_from_spec(spec)
spec.loader.exec_module(safe_names)


class SessionFolderNameTests(unittest.TestCase):
    def test_trailing_space_and_dot_are_removed(self):
        # "神様の伝言 - nebusokumoai cover .mp3" has a space before the extension.
        self.assertEqual(Path('神様の伝言 - nebusokumoai cover .mp3').stem, '神様の伝言 - nebusokumoai cover ')
        self.assertEqual(safe_names.session_folder_name('神様の伝言 - nebusokumoai cover '), '神様の伝言 - nebusokumoai cover')
        self.assertEqual(safe_names.session_folder_name('Song.. '), 'Song')

    def test_parentheses_and_normal_names_are_kept(self):
        self.assertEqual(safe_names.session_folder_name('a (b) [c] - x'), 'a (b) [c] - x')

    def test_invalid_reserved_and_empty_names(self):
        self.assertEqual(safe_names.session_folder_name('x:y*z?'), 'x_y_z_')
        self.assertEqual(safe_names.session_folder_name('CON'), 'song_CON')
        self.assertEqual(safe_names.session_folder_name(' . '), 'song')
        self.assertLessEqual(len(safe_names.session_folder_name('あ' * 300)), 80)

    def test_folder_can_be_created_and_resolves_to_the_same_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / safe_names.session_folder_name('神様の伝言 (nebusokumoai) cover ')
            folder.mkdir()
            (folder / 'source.wav').write_bytes(b'x')
            self.assertTrue((folder / 'source.wav').is_file())
            self.assertEqual(folder.name, '神様の伝言 (nebusokumoai) cover')


if __name__ == '__main__':
    unittest.main()
