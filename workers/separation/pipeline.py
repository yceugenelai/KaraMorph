import argparse
import json
import sys
from pathlib import Path

# Windows consoles default to a legacy codepage (e.g. cp932) that can't encode
# every Chinese character used in this file's help/print text; force UTF-8 so
# --help and status output don't crash on Windows regardless of terminal setup.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from audio_ops import convert_to_wav, separate_stems
from config import OUTPUT_ROOT, ROFORMER_MODEL, ROFORMER_MODEL_DIR


def run_pipeline(input_path: Path, max_duration: float | None = None, on_stage=None, estimate_bpm: bool = True) -> dict:
    session_name = input_path.stem
    if max_duration is not None:
        session_name += f"_{int(max_duration)}s"  # keep test runs out of the full run's output dir
    session_dir = OUTPUT_ROOT / session_name
    session_dir.mkdir(parents=True, exist_ok=True)

    if on_stage:
        on_stage("Converting source audio")
    wav_info = convert_to_wav(input_path, session_dir / "source.wav", max_duration=max_duration)
    tempo_info = {"bpm": None}
    if on_stage:
        on_stage("Separating vocals and accompaniment (Kim Mel-Band RoFormer)")
    stem_paths = separate_stems(wav_info["path"], session_dir / "stems")

    provenance_path = ROFORMER_MODEL_DIR / "assets.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8")) if provenance_path.is_file() else {}
    manifest = {
        "separation_model": {"engine": "audio-separator", "model": ROFORMER_MODEL, "assets": provenance},
        "input_file": str(input_path),
        "session_dir": str(session_dir),
        "duration_sec": wav_info["duration"],
        "bpm": tempo_info["bpm"],
        "stems": {name: str(path) for name, path in stem_paths.items()},
    }
    manifest_path = session_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest["manifest_path"] = str(manifest_path)
    return manifest

