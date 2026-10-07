import sys
from pathlib import Path
import argparse
import contextlib
import json
import os
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime_config import bootstrap_worker


PROTOCOL_STDOUT = sys.stdout


def emit(kind, **fields):
    print(json.dumps({"type": kind, **fields}, ensure_ascii=False), file=PROTOCOL_STDOUT, flush=True)


def main():
    bootstrap_worker()
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["probe", "separate"])
    parser.add_argument("--input")
    parser.add_argument("--output-root")
    args = parser.parse_args()
    if args.output_root:
        os.environ["AIK_OUTPUT_ROOT"] = args.output_root
    root = Path(__file__).parent / "separation"
    sys.path.insert(0, str(root))
    if args.command == "probe":
        with contextlib.redirect_stdout(sys.stderr):
            import torch
            from config import ROFORMER_MODEL_DIR, ROFORMER_MODEL, DEVICE
        from runtime_config import MODEL_CONFIG
        missing = [str(ROFORMER_MODEL_DIR / name) for name in (ROFORMER_MODEL, MODEL_CONFIG) if not (ROFORMER_MODEL_DIR / name).is_file()]
        if missing:
            raise FileNotFoundError("Missing separation assets: " + ", ".join(missing) + "; run scripts/setup.ps1")
        emit("result", ok=True, engine="kim-melband-roformer", device=DEVICE, cuda=torch.cuda.is_available(), model_exists=True, model=ROFORMER_MODEL)
        return
    if not args.input or not args.output_root:
        parser.error("separate requires --input and --output-root")
    source = Path(args.input)
    if not source.is_file():
        raise FileNotFoundError(source)
    emit("status", message="Loading separation pipeline")
    with contextlib.redirect_stdout(sys.stderr):
        from pipeline import run_pipeline
        from config import DEVICE
        emit("status", message="分離人聲：載入模型", device=DEVICE)
        manifest = run_pipeline(source, on_stage=lambda message: emit("status", message=message), estimate_bpm=False)
    for stem in ("vocals", "instrumental"):
        if not Path(manifest["stems"].get(stem, "")).is_file():
            raise RuntimeError(f"Separation did not produce {stem}.wav")
    emit("result", ok=True, manifest=manifest)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        traceback.print_exc(file=sys.stderr)
        emit("error", message=str(error))
        sys.exit(1)
