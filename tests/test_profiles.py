import json
from pathlib import Path
import tempfile
import unittest

from app.profiles import DEFAULT_PROFILE, ProfileStore, content_key, profile_name, variant_key
from app.recordings import load_recordings


class ProfileStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_content_key_ignores_path_and_mtime(self):
        first, second = self.root / 'a.mp3', self.root / 'sub' / 'b.mp3'
        second.parent.mkdir()
        data = bytes(range(256)) * 2000
        first.write_bytes(data)
        second.write_bytes(data)
        second.touch()
        self.assertEqual(content_key(first), content_key(second))
        second.write_bytes(data + b'x')
        self.assertNotEqual(content_key(first), content_key(second))
        empty = self.root / 'empty.mp3'
        empty.touch()
        self.assertTrue(content_key(empty))

    def test_legacy_settings_and_playlist_migrate_into_default_profile(self):
        (self.root / 'last_playlist.json').write_text('{"schema_version": 1, "items": []}', encoding='utf-8')
        store = ProfileStore(self.root, None, legacy_preferences={'music_volume': 42})
        self.assertEqual(store.active, DEFAULT_PROFILE)
        self.assertEqual(store.read('preferences.json', {}), {'music_volume': 42})
        self.assertTrue(store.path('last_playlist.json').is_file())
        self.assertTrue((self.root / 'last_playlist.json').is_file())
        store.write('preferences.json', {'music_volume': 7})
        again = ProfileStore(self.root, None, legacy_preferences={'music_volume': 42})
        self.assertEqual(again.read('preferences.json', {}), {'music_volume': 7})

    def test_best_effects_fallback_clear_and_isolation_between_profiles(self):
        store = ProfileStore(self.root)
        self.assertIsNone(store.best_for('song', 'original'))
        store.save_best('song', 'original', 'Song', 1.1, 2)
        self.assertEqual(store.best_for('song', 'original'), (1.1, 2))
        self.assertEqual(store.best_for('song', 'Jazz|1.000|+0'), (1.1, 2))
        store.save_best('song', 'Jazz|1.000|+0', 'Song', 0.9, -1)
        self.assertEqual(store.best_for('song', 'original'), (1.1, 2))
        self.assertEqual(store.best_for('song', 'other|1.000|+0'), (0.9, -1))
        self.assertEqual(ProfileStore(self.root, 'guest').best_for('song', 'original'), None)
        store.clear_best('song', 'Jazz|1.000|+0')
        self.assertEqual(store.best_for('song', 'Jazz|1.000|+0'), (0.9, -1))
        store.clear_best('song', 'original')
        store.clear_best('song', 'Jazz|1.000|+0')
        self.assertIsNone(store.best_for('song', 'original'))

    def test_invalid_saved_values_are_ignored(self):
        store = ProfileStore(self.root)
        store.write('song_prefs.json', {'schema_version': 1, 'songs': {'s': {'entries': {
            'original': {'speed': 5, 'key': 0}, '*': {'speed': 1.0, 'key': True}}}}})
        self.assertIsNone(store.best_for('s', 'original'))
        store.path('song_prefs.json').write_text('not json', encoding='utf-8')
        self.assertIsNone(store.best_for('s', 'original'))

    def test_names_and_variant_keys(self):
        self.assertEqual(profile_name('../evil'), 'evil')
        self.assertEqual(profile_name('  '), DEFAULT_PROFILE)
        self.assertEqual(variant_key('instrumental', None, 1, 0), 'instrumental')
        self.assertEqual(variant_key('abc', {'style_label': 'Jazz'}, 1.0, 2), 'Jazz|1.000|+2')

    def test_recordings_are_filtered_by_profile_and_legacy_belong_to_default(self):
        for session, profile in (('a', None), ('b', 'guest')):
            folder = self.root / 'songs' / 'song' / 'recordings' / session
            folder.mkdir(parents=True)
            row = {'session_id': session, 'song_id': 'song', 'started_at': session}
            if profile:
                row['profile'] = profile
            (folder / 'session.json').write_text(json.dumps(row), encoding='utf-8')
            (folder / 'microphone.wav').write_bytes(b'')
        ids = lambda profile: [row['session_id'] for row in load_recordings(self.root, 'song', profile)]
        self.assertEqual(ids('default'), ['a'])
        self.assertEqual(ids('guest'), ['b'])
        self.assertEqual(ids(None), ['a', 'b'])


if __name__ == '__main__':
    unittest.main()
