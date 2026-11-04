"""Per-person data: singing preferences, best speed/key per song, last playlist.

Each profile is a folder under ``.app_data/profiles/``. Songs are keyed by a
content fingerprint rather than ``song_id`` so saved values survive moving the
music or copying the profile to another computer.
"""

import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from app.storage import atomic_json

SCHEMA_VERSION = 1
DEFAULT_PROFILE = 'default'
FALLBACK = '*'
SPEED_RANGE = (0.7, 1.3)
KEY_RANGE = (-6, 6)
_SAMPLE = 64 * 1024


def content_key(path: Path) -> str:
    """Path- and mtime-independent fingerprint: size plus the head and tail of the file."""
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        size = stream.seek(0, 2)
        digest.update(str(size).encode())
        stream.seek(0)
        digest.update(stream.read(_SAMPLE))
        stream.seek(max(size - _SAMPLE, 0))
        digest.update(stream.read(_SAMPLE))
    return digest.hexdigest()[:24]


def variant_key(variant_id: str, variant: dict | None, speed: float, semitones: int) -> str:
    """Stable across machines: built-ins by name, generated versions by style and baked-in adjustments."""
    if variant_id in ('original', 'instrumental') or not variant:
        return variant_id
    kind = variant.get('style_label') or variant.get('kind') or 'version'
    return f'{kind}|{speed:.3f}|{semitones:+d}'


def profile_name(name: str) -> str:
    cleaned = re.sub(r'[^\w\- ]', '', str(name), flags=re.UNICODE).strip()[:40]
    return cleaned or DEFAULT_PROFILE


def _valid_effect(row) -> tuple[float, int] | None:
    if not isinstance(row, dict):
        return None
    speed, key = row.get('speed'), row.get('key')
    if isinstance(speed, bool) or isinstance(key, bool) or not isinstance(speed, (int, float)) \
            or not isinstance(key, int):
        return None
    if not (SPEED_RANGE[0] <= speed <= SPEED_RANGE[1] and KEY_RANGE[0] <= key <= KEY_RANGE[1]):
        return None
    return round(float(speed), 2), key


class ProfileStore:
    def __init__(self, base: Path, active: str | None = None, legacy_preferences=None):
        self.base = Path(base)
        self.root = self.base / 'profiles'
        self.active = profile_name(active or DEFAULT_PROFILE)
        self._legacy_preferences = legacy_preferences
        self.ensure(self.active)

    def path(self, filename: str) -> Path:
        return self.root / self.active / filename

    def ensure(self, name: str) -> None:
        folder = self.root / name
        if (folder / 'profile.json').is_file():
            return
        folder.mkdir(parents=True, exist_ok=True)
        if name == DEFAULT_PROFILE:
            if isinstance(self._legacy_preferences, dict) and not (folder / 'preferences.json').exists():
                atomic_json(folder / 'preferences.json', self._legacy_preferences)
            legacy_playlist = self.base / 'last_playlist.json'
            if legacy_playlist.is_file() and not (folder / 'last_playlist.json').exists():
                shutil.copy2(legacy_playlist, folder / 'last_playlist.json')
        atomic_json(folder / 'profile.json', {'schema_version': SCHEMA_VERSION, 'name': name,
                                              'created_at': datetime.now(timezone.utc).isoformat()})

    def read(self, filename: str, default):
        try:
            return json.loads(self.path(filename).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return default

    def write(self, filename: str, payload: dict) -> None:
        atomic_json(self.path(filename), payload)

    def _songs(self) -> dict:
        payload = self.read('song_prefs.json', {})
        songs = payload.get('songs') if isinstance(payload, dict) else None
        return songs if isinstance(songs, dict) else {}

    def best_for(self, ckey: str, vkey: str) -> tuple[float, int] | None:
        entries = self._songs().get(ckey, {}).get('entries')
        if not isinstance(entries, dict):
            return None
        return _valid_effect(entries.get(vkey)) or _valid_effect(entries.get(FALLBACK))

    def save_best(self, ckey: str, vkey: str, title: str, speed: float, key: int) -> None:
        songs = self._songs()
        row = songs.get(ckey) if isinstance(songs.get(ckey), dict) else {}
        entries = row.get('entries') if isinstance(row.get('entries'), dict) else {}
        value = {'speed': round(float(speed), 2), 'key': int(key)}
        entries[vkey] = value
        entries[FALLBACK] = dict(value)
        songs[ckey] = {'title': title, 'entries': entries}
        self.write('song_prefs.json', {'schema_version': SCHEMA_VERSION, 'songs': songs})

    def clear_best(self, ckey: str, vkey: str) -> None:
        songs = self._songs()
        entries = songs.get(ckey, {}).get('entries')
        if not isinstance(entries, dict):
            return
        if entries.pop(vkey, None) is None:
            entries.clear()  # only the song-wide fallback applied here; drop it
        if not [name for name in entries if name != FALLBACK]:
            songs.pop(ckey, None)
        self.write('song_prefs.json', {'schema_version': SCHEMA_VERSION, 'songs': songs})
