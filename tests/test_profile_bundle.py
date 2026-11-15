import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from app.playlist import load_playlist, resolve_item, save_playlist
from app.profile_bundle import BundleError, Cancelled, export_bundle, import_bundle, read_manifest
from app.profiles import ProfileStore, content_key
from app.recordings import load_recordings
from app.storage import atomic_json, scan_songs, save_catalog, song_dir


class Machine:
    def __init__(self, root: Path):
        self.root = root
        self.source = root / 'input'
        self.workspace = root / 'outputs'
        self.source.mkdir(parents=True)
        self.store = ProfileStore(root / 'app_data')

    def songs(self):
        return scan_songs(self.source)


def build_library(machine: Machine):
    audio = machine.source / 'sub' / 'KING.mp3'
    audio.parent.mkdir()
    audio.write_bytes(bytes(range(256)) * 600)
    song = machine.songs()[0]
    root = song_dir(machine.workspace, song['id'])
    instrumental = root / 'separation_jobs' / 'job' / 'stems' / 'instrumental.wav'
    styled = root / 'style_jobs' / 'v1' / 'out.wav'
    for file in (instrumental, styled, root / 'assets' / 'lyrics.txt', root / 'assets' / 'vocal_activity' / 'x.json'):
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text('data', encoding='utf-8')
    save_catalog(machine.workspace, song['id'], {
        'separation': {'source': song['path'], 'manifest': {'stems': {'instrumental': str(instrumental)}}},
        'variants': [{'id': 'v1', 'kind': 'style', 'style_label': 'Jazz', 'audio_path': str(styled)}]})
    for session, profile in (('mine', 'default'), ('theirs', 'guest')):
        folder = root / 'recordings' / session
        folder.mkdir(parents=True)
        (folder / 'microphone.wav').write_bytes(b'wav')
        atomic_json(folder / 'session.json', {'session_id': session, 'song_id': song['id'], 'profile': profile,
                                              'backing_path_at_recording': str(instrumental)})
    (machine.workspace / 'shared_images').mkdir(parents=True)
    (machine.workspace / 'shared_images' / 'bg.jpg').write_bytes(b'jpg')
    machine.store.write('preferences.json', {'music_volume': 41})
    machine.store.save_best(content_key(audio), 'original', 'KING', 1.1, 2)
    save_playlist(machine.store.path('last_playlist.json'), [
        {'song_id': song['id'], 'variant_id': 'original', 'label': 'KING'},
        {'song_id': song['id'], 'variant_id': 'v1', 'label': 'KING jazz'}])
    return song


class ProfileBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.a, self.b = Machine(base / 'a'), Machine(base / 'b')
        self.song = build_library(self.a)
        self.zip = base / 'profile.zip'

    def tearDown(self):
        self.temp.cleanup()

    def export(self, full):
        export_bundle(self.zip, self.a.store, self.a.workspace, self.a.source, self.a.songs(), full)

    def test_full_bundle_recreates_the_environment_on_another_machine(self):
        self.export(True)
        result = import_bundle(self.zip, self.b.store, self.b.workspace, self.b.source, 'default')
        self.assertEqual((result['songs'], result['kept'], result['skipped_playlist']), (1, 0, 0))
        self.assertEqual((self.b.source / 'sub' / 'KING.mp3').read_bytes(), (self.a.source / 'sub' / 'KING.mp3').read_bytes())
        songs = self.b.songs()
        new_id = songs[0]['id']
        root = song_dir(self.b.workspace, new_id)
        self.assertTrue((root / 'assets' / 'lyrics.txt').is_file())
        self.assertFalse((root / 'assets' / 'vocal_activity').exists())
        by_id = {song['id']: song for song in songs}
        playlist = load_playlist(self.b.store.path('last_playlist.json'))
        self.assertEqual([item['song_id'] for item in playlist], [new_id, new_id])
        for item in playlist + [dict(song_id=new_id, variant_id='instrumental', label='')]:
            path = resolve_item(item, by_id, self.b.workspace)
            home = self.b.source if item['variant_id'] == 'original' else self.b.workspace
            self.assertTrue(path and path.is_file() and path.is_relative_to(home), item)
        recordings = load_recordings(self.b.workspace, new_id, 'default')
        self.assertEqual([row['session_id'] for row in recordings], ['mine'])
        self.assertTrue(Path(recordings[0]['backing_path_at_recording']).is_relative_to(self.b.workspace))
        self.assertEqual(self.b.store.read('preferences.json', {}), {'music_volume': 41})
        self.assertEqual(self.b.store.best_for(content_key(self.b.source / 'sub' / 'KING.mp3'), 'original'), (1.1, 2))
        self.assertTrue((self.b.workspace / 'shared_images' / 'bg.jpg').is_file())
        again = import_bundle(self.zip, self.b.store, self.b.workspace, self.b.source, 'copy')
        self.assertEqual((again['songs'], again['kept']), (1, 1))
        self.assertEqual(len(self.b.songs()), 1)
        self.assertEqual(list((self.b.workspace / 'songs').glob('*.importing')), [])

    def test_settings_bundle_has_no_music_and_matches_playlist_by_content(self):
        self.export(False)
        names = zipfile.ZipFile(self.zip).namelist()
        self.assertFalse([n for n in names if n.startswith(('music/', 'songs/'))])
        c = Machine(Path(self.temp.name) / 'c')
        (c.source / 'other').mkdir()
        (c.source / 'other' / 'renamed.mp3').write_bytes((self.a.source / 'sub' / 'KING.mp3').read_bytes())
        result = import_bundle(self.zip, c.store, c.workspace, c.source, 'Friend')
        self.assertEqual(result['profile'], 'Friend')
        self.assertEqual(c.store.active, 'default')
        playlist = load_playlist(c.store.root / 'Friend' / 'last_playlist.json')
        self.assertEqual([item['variant_id'] for item in playlist], ['original'])
        self.assertEqual(result['skipped_playlist'], 1)
        prefs = json.loads((c.store.root / 'Friend' / 'preferences.json').read_text('utf-8'))
        self.assertEqual(prefs, {'music_volume': 41})

    def test_unsafe_or_foreign_archives_are_refused_and_cancel_leaves_nothing(self):
        bad = Path(self.temp.name) / 'bad.zip'
        bad.write_bytes(b'not a zip')
        with self.assertRaises(BundleError):
            read_manifest(bad)
        self.export(True)
        with zipfile.ZipFile(self.zip) as source, zipfile.ZipFile(bad, 'w') as out:
            for info in source.infolist():
                data = source.read(info)
                if info.filename == 'manifest.json':
                    manifest = json.loads(data)
                    manifest['songs'][0]['rel'] = '../evil.mp3'
                    data = json.dumps(manifest).encode()
                out.writestr(info.filename, data)
        with self.assertRaises(BundleError):
            import_bundle(bad, self.b.store, self.b.workspace, self.b.source, 'x')
        self.assertFalse((self.b.root / 'evil.mp3').exists())
        with self.assertRaises(BundleError):
            import_bundle(self.zip, self.b.store, self.b.workspace, None, 'x')
        target = Path(self.temp.name) / 'cancelled.zip'
        with self.assertRaises(Cancelled):
            export_bundle(target, self.a.store, self.a.workspace, self.a.source, self.a.songs(), True,
                          progress=lambda *_: False)
        self.assertEqual(list(Path(self.temp.name).glob('cancelled.zip*')), [])


if __name__ == '__main__':
    unittest.main()
