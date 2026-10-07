"""Create one aligned accompaniment/vocal pair with independent tempo and pitch."""

import sys
from pathlib import Path
import argparse
import json
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime_config import bootstrap_worker



def emit(kind, **fields):
    print(json.dumps({"type": kind, **fields}, ensure_ascii=False), flush=True)


def main():
    bootstrap_worker()
    parser = argparse.ArgumentParser()
    parser.add_argument("--backing", required=True)
    parser.add_argument("--vocal")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--speed", required=True, type=float)
    parser.add_argument("--semitones", required=True, type=int)
    args = parser.parse_args()
    if not 0.5 <= args.speed <= 2.0 or not -12 <= args.semitones <= 12:
        parser.error("speed must be 0.5–2.0 and key shift must be -12..12 semitones")
    if args.speed == 1.0 and args.semitones == 0:
        parser.error("choose a speed or key change")
    source_backing = Path(args.backing).resolve(strict=True)
    source_vocal = Path(args.vocal).resolve(strict=True) if args.vocal else None
    target = Path(args.output_dir).resolve()
    target.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(Path(__file__).parent / "styling"))
    from audio_fx import process_file

    backing = target / "backing.wav"
    vocal = target / "vocal.wav" if source_vocal else None
    emit("status", message="Adjusting accompaniment")
    process_file(source_backing, backing, tempo=args.speed, semitones=args.semitones)
    if source_vocal:
        emit("progress", value=0.5, message="Adjusting vocal")
        process_file(source_vocal, vocal, tempo=args.speed, semitones=args.semitones)
    emit("result", ok=True, backing=str(backing), vocal=str(vocal) if vocal else None, speed=args.speed, semitones=args.semitones)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        traceback.print_exc(file=sys.stderr)
        emit("error", message=str(error))
        sys.exit(1)
