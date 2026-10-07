"""Song-linked microphone takes stored independently of generated music."""

import json
import shutil
from pathlib import Path

from app.storage import song_dir


def recordings_dir(workspace: Path, song_id: str) -> Path:
    return song_dir(workspace, song_id) / "recordings"


def load_recordings(workspace: Path, song_id: str) -> list[dict]:
    root = recordings_dir(workspace, song_id)
    if not root.is_dir():
        return []
    result = []
    for folder in root.iterdir():
        if not folder.is_dir():
            continue
        metadata, audio = folder / "session.json", folder / "microphone.wav"
        if not metadata.is_file() or not audio.is_file():
            continue
        try:
            row = json.loads(metadata.read_text(encoding="utf-8"))
            if row.get("song_id") != song_id or row.get("session_id") != folder.name:
                continue
            result.append({**row, "audio_path": str(audio)})
        except (OSError, ValueError, TypeError):
            continue
    return sorted(result, key=lambda row: (row.get("started_at", ""), row["session_id"]))


def delete_recording(workspace: Path, song_id: str, session_id: str) -> None:
    songs_root = (workspace.resolve() / "songs").resolve()
    song_root = song_dir(workspace, song_id).resolve()
    root = recordings_dir(workspace, song_id).resolve()
    target = (root / session_id).resolve()
    if (song_root.parent != songs_root or song_root.name != song_id or root.parent != song_root
            or target.parent != root or not target.is_dir() or target.name != session_id):
        raise ValueError("錄音資料夾位置不正確")
    shutil.rmtree(target)
