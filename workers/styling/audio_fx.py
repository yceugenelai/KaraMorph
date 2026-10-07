"""Change the speed and/or key of one WAV with ffmpeg's rubberband filter (speed and pitch are independent).

Examples:
    audio_fx.py in.wav out.wav --speed 0.8                  # 20% slower, same key
    audio_fx.py in.wav out.wav --semitones -2               # a whole tone lower, same speed
    audio_fx.py in.wav out.wav --speed 0.8 --semitones 2 --formant shifted
Shift the vocal and the accompaniment by the same amount to keep them in the same key.
The app fx_worker.py applies matching adjustments to accompaniment and vocals.
"""
import argparse
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import imageio_ffmpeg


def rubberband_filter(tempo: float, semitones: float, formant: str) -> str:
    if tempo <= 0:
        raise ValueError(f"tempo must be > 0, got {tempo}")
    pitch = 2 ** (semitones / 12)
    return f"rubberband=tempo={tempo:.6f}:pitch={pitch:.6f}:formant={formant}:channels=together"


def process_file(input_path: Path, output_path: Path, tempo: float = 1.0, semitones: float = 0.0, formant: str = "preserved") -> None:
    """tempo > 1 = faster/shorter, < 1 = slower/longer; semitones shifts the key. Writes a 24-bit WAV."""
    if not input_path.is_file():
        raise FileNotFoundError(f"not found: {input_path}")
    if output_path.suffix.lower() != ".wav":
        raise ValueError(f"output must be a .wav file, got {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-i", str(input_path),
        "-filter:a", rubberband_filter(tempo, semitones, formant), "-c:a", "pcm_s24le", str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed on {input_path}: {result.stderr[-500:]}")


def add_fx_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--speed", type=float, default=1.0, help="playback speed multiplier (0.8 = 20%% slower); pitch unchanged")
    p.add_argument("--semitones", type=float, default=0.0, help="key change: positive = higher, negative = lower; tempo unchanged")
    p.add_argument(
        "--formant", choices=["preserved", "shifted"], default="preserved",
        help="preserved keeps the vocal timbre natural (default); shifted moves the formants along with the pitch",
    )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("input", type=Path)
    p.add_argument("output", type=Path)
    add_fx_arguments(p)
    args = p.parse_args()
    if args.speed == 1.0 and not args.semitones:
        raise SystemExit("give --speed and/or --semitones")
    process_file(args.input, args.output, tempo=args.speed, semitones=args.semitones, formant=args.formant)
    print(f"wrote {args.output} (speed {args.speed}x, {args.semitones:+g} semitones)")


