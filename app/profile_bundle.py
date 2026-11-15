"""Zip export/import of one profile, optionally with the whole music library.

Songs are matched across machines by content fingerprint; on import their
``song_id`` and absolute paths are rewritten for the new location.
"""

import json
import os
import re
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from app.i18n import t
from app.playlist import load_playlist, save_playlist
from app.profiles import ProfileStore, content_key, profile_name, variant_key
from app.storage import load_catalog, scan_songs, song_dir, song_id

FORMAT = 'karamorph-profile'
SCHEMA_VERSION = 1
PROFILE_FILES = ('preferences.json', 'song_prefs.json')
CACHE_DIRS = {'vocal_activity', 'preview'}


class BundleError(ValueError):
    pass


class Cancelled(Exception):
    pass


def _tick(progress, state, label):
    state[0] += 1
    if progress and progress(state[0], state[1], label) is False:
        raise Cancelled()


def _song_files(root: Path, store: ProfileStore):
    """Song data worth moving: no caches, partial files, symlinks or other profiles' recordings."""
    owners = {}
    for file in sorted(root.rglob('*')):
        rel = file.relative_to(root)
        if not file.is_file() or file.is_symlink() or CACHE_DIRS & set(rel.parts) or '.partial' in file.name:
            continue
        if rel.parts[0] == 'recordings' and len(rel.parts) > 2:
            if rel.parts[1] not in owners:
                try:
                    owners[rel.parts[1]] = json.loads((root / 'recordings' / rel.parts[1] / 'session.json')
                                                      .read_text(encoding='utf-8')).get('profile', 'default')
                except (OSError, ValueError, AttributeError):
                    owners[rel.parts[1]] = None
            if owners[rel.parts[1]] != store.active:
                continue
        yield rel, file


def export_bundle(path: Path, store: ProfileStore, workspace: Path, source_dir: Path, songs: list[dict],
                  full: bool, progress=None) -> None:
    path = Path(path)
    by_id = {song['id']: song for song in songs}
    keys = {}

    def key(song):
        if song['id'] not in keys:
            keys[song['id']] = content_key(Path(song['path']))
        return keys[song['id']]

    playlist = []
    try:
        items = load_playlist(store.path('last_playlist.json'))
    except (OSError, ValueError):
        items = []
    for item in items:
        song = by_id.get(item['song_id'])
        if song:
            variants = load_catalog(workspace, song['id']).get('variants', [])
            playlist.append({'content_key': key(song), 'variant_id': item['variant_id'],
                             'variant_key': variant_key(item['variant_id'], variants), 'label': item['label']})
    entries = [(f'profile/{name}', store.path(name)) for name in PROFILE_FILES if store.path(name).is_file()]
    manifest = {'format': FORMAT, 'schema_version': SCHEMA_VERSION, 'kind': 'full' if full else 'settings',
                'profile': store.active, 'created_at': datetime.now(timezone.utc).isoformat(), 'playlist': playlist,
                'songs': []}
    if full:
        for song in songs:
            original = Path(song['path'])
            if not original.is_file():
                continue
            try:
                rel = original.relative_to(source_dir).as_posix()
            except ValueError:
                rel = original.name
            manifest['songs'].append({'id': song['id'], 'content_key': key(song), 'title': song.get('title', ''),
                                      'rel': rel, 'source_path': str(original),
                                      'song_dir': str(song_dir(workspace, song['id']))})
            entries.append((f'music/{song["id"]}/{original.name}', original))
            root = song_dir(workspace, song['id'])
            if root.is_dir():
                entries += [(f'songs/{song["id"]}/{rel.as_posix()}', file) for rel, file in _song_files(root, store)]
        shared = workspace / 'shared_images'
        if shared.is_dir():
            entries += [(f'shared_images/{file.relative_to(shared).as_posix()}', file)
                        for file in sorted(shared.rglob('*')) if file.is_file() and not file.is_symlink()]
    state = [0, len(entries)]
    temp = path.with_name(path.name + '.tmp')
    try:
        with zipfile.ZipFile(temp, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
            for arcname, file in entries:
                _tick(progress, state, file.name)
                archive.write(file, arcname)
        temp.replace(path)
    except Exception:
        temp.unlink(missing_ok=True)
        raise


def read_manifest(path: Path) -> dict:
    try:
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read('manifest.json'))
        if manifest['format'] == FORMAT and manifest['schema_version'] == SCHEMA_VERSION \
                and manifest['kind'] in ('settings', 'full') and isinstance(manifest['profile'], str):
            return manifest
    except (OSError, KeyError, ValueError, TypeError, zipfile.BadZipFile):
        pass
    raise BundleError(t('不是 KaraMorph 個人設定檔封存檔。'))


def _inside(base: Path, rel: str) -> Path:
    """Resolve an archive-supplied relative path, refusing anything that escapes ``base``."""
    parts = PurePosixPath(rel.replace('\\', '/')).parts
    target = base.joinpath(*parts)
    if not parts or PurePosixPath(rel).is_absolute() or ':' in rel or '..' in parts \
            or not target.resolve().is_relative_to(base.resolve()):
        raise BundleError(t('封存檔內容不安全。'))
    return target


def _remap(value, old_id, new_id, old_root, new_root, old_source, new_source):
    if isinstance(value, dict):
        return {k: _remap(v, old_id, new_id, old_root, new_root, old_source, new_source) for k, v in value.items()}
    if isinstance(value, list):
        return [_remap(v, old_id, new_id, old_root, new_root, old_source, new_source) for v in value]
    if isinstance(value, str):
        if value == old_id:
            return new_id
        if os.path.normcase(value) == os.path.normcase(old_source):
            return new_source
        if os.path.normcase(value).startswith(os.path.normcase(old_root)):
            return new_root + value[len(old_root):]
    return value


def _import_song(archive, entry, source_dir, workspace, name, tick):
    """Returns (new song id, whether generated data was added)."""
    old_id = entry['id']
    if not re.fullmatch(r'[0-9a-f]{1,64}', old_id):
        raise BundleError(t('封存檔內容不安全。'))
    music = next((i for i in archive.infolist() if i.filename.startswith(f'music/{old_id}/')), None)
    if music is None:
        return None, False
    dest = _inside(source_dir, entry['rel'])
    if not (dest.is_file() and dest.stat().st_size == music.file_size and content_key(dest) == entry['content_key']):
        counter = 1
        while dest.exists():
            dest = dest.with_name(f'{dest.stem} (imported{"" if counter == 1 else f" {counter}"}){dest.suffix}')
            counter += 1
        dest.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(music) as source, dest.open('wb') as out:
            shutil.copyfileobj(source, out)
    tick(dest.name)
    new_id = song_id(dest)
    target = song_dir(workspace, new_id)
    members = [i for i in archive.infolist() if i.filename.startswith(f'songs/{old_id}/') and not i.is_dir()]
    if target.exists() or not members:
        for _ in members:
            tick(dest.name)
        return new_id, False
    temp = target.with_name(target.name + '.importing')
    shutil.rmtree(temp, ignore_errors=True)
    try:
        for info in members:
            rel = info.filename[len(f'songs/{old_id}/'):]
            out = _inside(temp, rel)
            out.parent.mkdir(parents=True, exist_ok=True)
            if rel.endswith('.json'):
                data = _remap(json.loads(archive.read(info)), old_id, new_id, entry['song_dir'], str(target.resolve()),
                              entry['source_path'], str(dest.resolve()))
                if PurePosixPath(rel).match('recordings/*/session.json') and isinstance(data, dict):
                    data['profile'] = name
                out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            else:
                with archive.open(info) as source, out.open('wb') as handle:
                    shutil.copyfileobj(source, handle)
            tick(out.name)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temp, target)
    except BaseException:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    return new_id, True


def _resolve_playlist(items, source_dir, workspace):
    local = {}
    for song in scan_songs(source_dir) if source_dir and Path(source_dir).is_dir() else []:
        try:
            local[content_key(Path(song['path']))] = song
        except OSError:
            continue
    result, skipped = [], 0
    for item in items:
        song = local.get(item.get('content_key'))
        variants = load_catalog(workspace, song['id']).get('variants', []) if song else []
        variant_id = item.get('variant_id')
        if song and variant_id not in ('original', 'instrumental') and all(v['id'] != variant_id for v in variants):
            variant_id = next((v['id'] for v in variants if variant_key(v['id'], variants) == item.get('variant_key')), None)
        if song and variant_id:
            result.append({'song_id': song['id'], 'variant_id': variant_id, 'label': item['label']})
        else:
            skipped += 1
    return result, skipped


def import_bundle(path: Path, store: ProfileStore, workspace: Path, source_dir, name: str, progress=None) -> dict:
    """Create or overwrite profile ``name`` from the archive; the active profile is left unchanged."""
    manifest = read_manifest(path)
    name = profile_name(name)
    full = manifest['kind'] == 'full'
    source_dir = Path(source_dir) if source_dir else None
    if full and not (source_dir and source_dir.is_dir()):
        raise BundleError(t('請先在設定中選擇音樂資料夾。'))
    result = {'profile': name, 'songs': 0, 'kept': 0, 'skipped_playlist': 0}
    with zipfile.ZipFile(path) as archive:
        state = [0, len(archive.infolist())]
        tick = lambda label: _tick(progress, state, label)
        if full:
            for entry in manifest['songs']:
                new_id, added = _import_song(archive, entry, source_dir, workspace, name, tick)
                result['songs'] += new_id is not None
                result['kept'] += new_id is not None and not added
            shared = workspace / 'shared_images'
            for info in archive.infolist():
                if info.filename.startswith('shared_images/') and not info.is_dir():
                    out = _inside(shared, info.filename[len('shared_images/'):])
                    if not out.exists():
                        out.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(info) as source, out.open('wb') as handle:
                            shutil.copyfileobj(source, handle)
                    tick(out.name)
        store.ensure(name)
        folder = store.root / name
        for filename in PROFILE_FILES:
            (folder / filename).unlink(missing_ok=True)
            if f'profile/{filename}' in archive.namelist():
                (folder / filename).write_bytes(archive.read(f'profile/{filename}'))
        items, result['skipped_playlist'] = _resolve_playlist(manifest.get('playlist', []), source_dir, workspace)
        save_playlist(folder / 'last_playlist.json', items)
    return result
