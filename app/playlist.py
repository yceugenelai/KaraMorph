"""Versioned text playlists; paths are resolved against the current library."""

import json
from pathlib import Path

from app.storage import atomic_json, load_catalog

SCHEMA_VERSION = 1


def save_playlist(path: Path, items: list[dict]) -> None:
    atomic_json(path, {"schema_version": SCHEMA_VERSION, "items": items})


def load_playlist(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != SCHEMA_VERSION or not isinstance(payload.get("items"), list):
        raise ValueError("Unsupported playlist format")
    result = []
    for item in payload["items"]:
        if not isinstance(item, dict) or not all(isinstance(item.get(key), str) for key in ("song_id", "variant_id", "label")):
            raise ValueError("Invalid playlist item")
        result.append({key: item[key] for key in ("song_id", "variant_id", "label")})
    return result


def resolve_item(item: dict, songs: dict[str, dict], workspace: Path) -> Path | None:
    song = songs.get(item["song_id"])
    if not song:
        return None
    catalog = load_catalog(workspace, item["song_id"])
    variant_id = item["variant_id"]
    if variant_id == "original":
        path = Path(song["path"])
        return path if song.get("available", True) and path.is_file() else None
    if variant_id == "instrumental":
        separation = catalog.get("separation")
        path = Path(separation["manifest"]["stems"]["instrumental"]) if separation else None
    else:
        variant = next((row for row in catalog.get("variants", []) if row["id"] == variant_id), None)
        path = Path(variant["audio_path"]) if variant else None
    return path if path and path.is_file() else None
