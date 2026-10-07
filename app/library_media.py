"""Read local audio tags and cache small cover thumbnails outside the music folder."""

import hashlib
from pathlib import Path

from tinytag import TinyTag

from app.storage import data_dir


def read_media(path: Path) -> tuple[dict, bytes | None]:
    """Return display tags and embedded cover bytes, tolerating malformed files."""
    info = {"title": path.stem, "artist": "", "album": ""}
    try:
        audio = TinyTag.get(path, image=True)
        if audio.duration is not None:
            info['duration_sec'] = float(audio.duration)
        info.update(title=audio.title or path.stem, artist=audio.artist or '', album=audio.album or '')
        artwork = audio.images.any
        return info, artwork.data if artwork is not None else None
    except Exception:
        return info, None


def cache_thumbnail(song_id: str, artwork: bytes | None) -> str | None:
    if not artwork or len(artwork) > 20_000_000:
        return None
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage

    image = QImage.fromData(artwork)
    if image.isNull():
        return None
    target = data_dir() / "covers" / f"{song_id}_{hashlib.sha256(artwork).hexdigest()[:12]}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file():
        image.scaled(160, 160, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation).save(str(target), "PNG")
    return str(target)


def cache_background_cover(song_id: str, source: Path) -> Path | None:
    """Keep a larger copy of embedded artwork for the karaoke display."""
    _, artwork = read_media(source)
    if not artwork or len(artwork) > 20_000_000:
        return None
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage

    image = QImage.fromData(artwork)
    if image.isNull():
        return None
    target = data_dir() / "covers" / f"{song_id}_{hashlib.sha256(artwork).hexdigest()[:12]}_background.png"
    if not target.is_file():
        image.scaled(1600, 1600, Qt.AspectRatioMode.KeepAspectRatio,
                     Qt.TransformationMode.SmoothTransformation).save(str(target), "PNG")
    return target if target.is_file() else None
