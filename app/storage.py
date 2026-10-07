import hashlib
import json
from pathlib import Path

FORMATS = {".wav", ".flac", ".mp3", ".m4a", ".aac"}
from runtime_config import ROOT as PROJECT_ROOT


def data_dir() -> Path:
    root = PROJECT_ROOT / ".app_data"
    root.mkdir(parents=True, exist_ok=True)
    return root


def default_workspace() -> Path:
    return PROJECT_ROOT / "outputs"


def load_settings() -> dict:
    path = data_dir() / "settings.json"
    if path.exists():
        settings = json.loads(path.read_text(encoding="utf-8"))
        old_root = settings.get('_install_root')
        if old_root and Path(old_root).resolve() != PROJECT_ROOT:
            for key in ('workspace', 'source_dir', 'roformer_model_dir', 'acestep_model_dir', 'ace_step_root'):
                value = settings.get(key)
                if value and Path(value).is_absolute() and Path(value).is_relative_to(Path(old_root)):
                    settings[key] = str(PROJECT_ROOT / Path(value).relative_to(Path(old_root)))
        settings.setdefault("singing_audio_mode", "exclusive")
        return settings
    return {"source_dir": "", "workspace": str(default_workspace()), "roformer_python": "", "acestep_python": "", "roformer_model_dir": "", "ace_step_root": "", "singing_audio_mode": "exclusive"}


def save_settings(settings: dict) -> None:
    path = data_dir() / "settings.json"
    atomic_json(path, dict(settings, _install_root=str(PROJECT_ROOT)))


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def song_id(path: Path) -> str:
    stat = path.stat()
    key = f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]


def scan_songs(source: Path) -> list[dict]:
    result = []
    for path in source.rglob("*"):
        if path.is_file() and path.suffix.lower() in FORMATS:
            try:
                result.append({"id": song_id(path), "title": path.stem, "path": str(path.resolve()), "available": True})
            except OSError:
                continue
    return sorted(result, key=lambda row: row["title"].casefold())


def library_index_path() -> Path:
    return data_dir() / "library.json"


def merge_library(source: Path, scanned: list[dict]) -> list[dict]:
    """Replace the library with the current source-directory scan.

    Old missing entries and legacy removed_ids are discarded. Generated assets
    remain in the workspace and can reconnect when the same source returns.
    """
    current = sorted(scanned, key=lambda row: row["title"].casefold())
    atomic_json(library_index_path(), {"source_dir": str(source.resolve()), "songs": current})
    return current


def song_dir(workspace: Path, identifier: str) -> Path:
    return workspace / "songs" / identifier


def load_catalog(workspace: Path, identifier: str) -> dict:
    path = song_dir(workspace, identifier) / "catalog.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"separation": None, "variants": []}


def save_catalog(workspace: Path, identifier: str, catalog: dict) -> None:
    atomic_json(song_dir(workspace, identifier) / "catalog.json", catalog)
