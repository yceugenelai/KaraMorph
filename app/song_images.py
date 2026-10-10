"""Per-song local artwork; never modify original audio or its metadata."""

import hashlib
import json
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtGui import QImageReader
from app.karaoke_assets import IMAGE_SUFFIXES, song_images_dir
from app.storage import atomic_json, song_dir


def settings_path(workspace, song_id):
    return song_dir(workspace, song_id) / 'assets' / 'images.json'


def image_reader(path):
    path = Path(path)
    if path.suffix.lower() not in IMAGE_SUFFIXES or path.stat().st_size > 20_000_000:
        raise ValueError('Unsupported image or image larger than 20 MB.')
    reader = QImageReader(str(path))
    reader.setDecideFormatFromContent(True)
    reader.setAutoTransform(True)
    size = reader.size()
    if not reader.canRead() or not size.isValid() or size.width() * size.height() > 40_000_000:
        raise ValueError('Invalid image or image larger than 40 megapixels.')
    return reader


def import_image(workspace, song_id, source):
    return import_image_to(song_images_dir(workspace, song_id), source)


def import_image_to(folder, source):
    source = Path(source)
    image_reader(source)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    if source.resolve().parent == folder.resolve():
        return source
    data = source.read_bytes()
    target = folder / f'{source.stem[:80]}_{hashlib.sha256(data).hexdigest()[:12]}{source.suffix.lower()}'
    if not target.exists():
        target.write_bytes(data)
    return target


def chosen_image(workspace, song_id):
    """Resolve only paths inside this song's image directory."""
    try:
        value = json.loads(settings_path(workspace, song_id).read_text(encoding='utf-8')).get('thumbnail')
        if not isinstance(value, str):
            return None
        folder = song_images_dir(workspace, song_id).resolve()
        target = (folder / value).resolve()
        if target.is_relative_to(folder) and target.is_file():
            return target
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return None


def selected_thumbnail(workspace, song_id):
    source = chosen_image(workspace, song_id)
    if source is None:
        return None
    try:
        reader = image_reader(source)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
        target = settings_path(workspace, song_id).parent / f'thumbnail-{digest}.png'
        if not target.exists():
            reader.setScaledSize(reader.size().scaled(160, 160, Qt.AspectRatioMode.KeepAspectRatio))
            image = reader.read()
            if image.isNull() or not image.save(str(target), 'PNG'):
                return None
        return str(target)
    except (OSError, ValueError):
        return None


def save_thumbnail(workspace, song_id, source):
    target = import_image(workspace, song_id, source) if source else None
    state = image_settings(workspace, song_id)
    state['thumbnail'] = target.name if target else None
    atomic_json(settings_path(workspace, song_id), state)


def image_settings(workspace, song_id):
    try:
        state = json.loads(settings_path(workspace, song_id).read_text(encoding='utf-8'))
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def save_image_sources(workspace, song_id, sources):
    state = image_settings(workspace, song_id)
    previous = state.get('sources', {})
    state['sources'] = dict(previous if isinstance(previous, dict) else {}, **sources)
    atomic_json(settings_path(workspace, song_id), state)
