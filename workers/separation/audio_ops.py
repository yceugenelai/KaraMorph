import json
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg
import soundfile as sf

from config import (
    ROFORMER_MODEL,
    ROFORMER_MODEL_DIR,
    SAMPLE_RATE,
)

FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()


def convert_to_wav(input_path: Path, out_path: Path, max_duration: float | None = None) -> dict:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [FFMPEG_EXE, "-y", "-i", str(input_path)]
    if max_duration is not None:
        cmd += ["-t", str(max_duration)]
    cmd += ["-ar", str(SAMPLE_RATE), "-ac", "2", str(out_path)]
    subprocess.run(cmd, check=True, capture_output=True)
    duration = sf.info(str(out_path)).duration
    return {"path": out_path, "sample_rate": SAMPLE_RATE, "duration": duration}


def _run_roformer_vocal_split(wav_path: Path, out_dir: Path) -> dict:
    """Run the two-stem model in a subprocess so VRAM is released on completion."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, str(Path(__file__).resolve().parent.parent / "roformer_cli.py"),
        str(wav_path),
        "--model_filename", ROFORMER_MODEL,
        "--model_file_dir", str(ROFORMER_MODEL_DIR),
        "--output_dir", str(out_dir),
        "--output_format", "WAV",
        "--custom_output_names", json.dumps({"Vocals": "vocals", "Instrumental": "instrumental"}),
    ]
    subprocess.run(cmd, check=True, stdout=sys.stderr, stderr=sys.stderr)
    return {"vocals": out_dir / "vocals.wav", "instrumental": out_dir / "instrumental.wav"}


def separate_stems(wav_path: Path, out_dir: Path) -> dict:
    stems = _run_roformer_vocal_split(wav_path, out_dir / "roformer")
    infos = [sf.info(str(stems[name])) for name in ("vocals", "instrumental")]
    if any(info.samplerate != SAMPLE_RATE or info.channels != 2 for info in infos):
        raise RuntimeError("Separation must produce stereo 44100 Hz WAV files")
    if abs(infos[0].frames - infos[1].frames) > 1:
        raise RuntimeError("Separated vocal and accompaniment are not aligned")
    if abs(infos[0].duration - sf.info(str(wav_path)).duration) > 0.05:
        raise RuntimeError("Separated audio duration differs from the source")
    return stems
