import sys
from pathlib import Path
import argparse
import contextlib
import json
import math
import os
import re
import secrets
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime_config import bootstrap_worker
from app.style_execution import execution_plan


PROTOCOL_STDOUT = sys.stdout


def emit(kind, **fields):
    print(json.dumps({"type": kind, **fields}, ensure_ascii=False), file=PROTOCOL_STDOUT, flush=True)


def prepare_manifest(manifest_path, backing_path, vocal_path):
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if bool(backing_path) != bool(vocal_path):
        raise ValueError("backing and vocal must be supplied together")
    if backing_path:
        import soundfile as sf
        backing = Path(backing_path).resolve(strict=True)
        vocal = Path(vocal_path).resolve(strict=True)
        backing_info, vocal_info = sf.info(backing), sf.info(vocal)
        if abs(backing_info.duration - vocal_info.duration) > 0.25:
            raise ValueError("Backing and vocal durations differ by more than 250 ms")
        manifest["stems"]["instrumental"] = str(backing)
        manifest["stems"]["vocals"] = str(vocal)
        manifest["duration_sec"] = backing_info.duration
        manifest["bpm"] = None
    return manifest


def choose_style_combos(fixed_strength, fixed_noise, strength_pool, noise_pool):
    """Return one anchored candidate and one distinct random candidate."""
    rng = secrets.SystemRandom()
    alternatives = [(strength, noise) for strength in strength_pool for noise in noise_pool
                    if (strength, noise) != (fixed_strength, fixed_noise)]
    if not alternatives:
        raise ValueError("Random style parameter pool needs a pair different from the fixed pair")
    random_strength, random_noise = rng.choice(alternatives)
    fixed_seed, random_seed = rng.sample(range(1000, 10000), 2)
    return [(fixed_strength, fixed_noise, fixed_seed),
            (random_strength, random_noise, random_seed)]


def detect_hardware():
    import torch
    import psutil
    from acestep.gpu_config import get_gpu_config
    cuda = torch.cuda.is_available() and torch.version.hip is None
    result = {"cuda": cuda, "gpu_name": None, "vram_gib": 0,
              "gpu_max_seconds": 0, "ram_available_gib": psutil.virtual_memory().available / 2**30}
    if cuda:
        device = torch.cuda.current_device()
        vram = torch.cuda.get_device_properties(device).total_memory / 2**30
        result.update(gpu_name=torch.cuda.get_device_name(device), vram_gib=vram,
                      gpu_max_seconds=get_gpu_config(vram).max_duration_without_lm)
    return result


def validate_candidate_lengths(records, duration):
    import soundfile as sf
    for record in records:
        if record.get("success") and record.get("output"):
            if not Path(record["output"]).is_file():
                record.update(success=False, error="Generated audio file is missing")
                continue
            output_duration = sf.info(record["output"]).duration
            record["duration_sec"] = output_duration
            if abs(output_duration - duration) > 0.25:
                record.update(success=False, error=f"Generated audio length {output_duration:.3f}s "
                              f"does not match input {duration:.3f}s; incomplete output rejected")


def main():
    bootstrap_worker()
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["probe", "transform"])
    parser.add_argument("--manifest")
    parser.add_argument("--backing")
    parser.add_argument("--vocal")
    parser.add_argument("--job-dir")
    parser.add_argument("--style")
    parser.add_argument("--tag", default="style")
    parser.add_argument("--count", type=int, default=2)
    parser.add_argument("--device", choices=["cuda", "cpu"], default="cuda")
    parser.add_argument("--cpu-confirmed", action="store_true")
    parser.add_argument("--fixed-strength", type=float)
    parser.add_argument("--fixed-noise-strength", type=float)
    parser.add_argument("--strengths", default="0.1,0.2,0.3,0.4,0.5,0.65")
    parser.add_argument("--noise-strengths", default="0.1,0.15,0.2,0.3,0.45,0.6")
    parser.add_argument("--seeds", default="1234,5678,9012,2024,4242,8888")
    args = parser.parse_args()
    if args.command == "transform" and args.device == "cpu":
        if not args.cpu_confirmed:
            parser.error("CPU style conversion requires explicit user confirmation")
        # A CUDA-enabled PyTorch build can also execute CPU inference. Hide CUDA
        # before importing torch so helpers cannot allocate GPU tensors or stats.
        os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
    root = Path(__file__).parent / "styling"
    sys.path.insert(0, str(root))
    import style_config as cfg
    checkpoints = Path(os.environ["ACESTEP_CHECKPOINTS_DIR"])
    required = [cfg.ACE_STEP_ROOT / "acestep/handler.py",
                checkpoints / cfg.COVER_MODEL / "model.safetensors",
                checkpoints / "vae/diffusion_pytorch_model.safetensors",
                checkpoints / "Qwen3-Embedding-0.6B/model.safetensors"]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing ACE-Step assets: " + ", ".join(missing) + "; run scripts/setup.ps1")
    if args.command == "probe":
        with contextlib.redirect_stdout(sys.stderr):
            hardware = detect_hardware()
            plan = None
            if args.backing:
                import soundfile as sf
                plan = execution_plan(hardware, sf.info(args.backing).duration, args.count)
        emit("result", ok=True, engine="acestep", **hardware, plan=plan,
             ace_step_root_exists=cfg.ACE_STEP_ROOT.is_dir(), device="cuda" if hardware["cuda"] else "cpu")
        return
    if not args.manifest or not args.job_dir or not args.style:
        parser.error("transform requires --manifest, --job-dir and --style")
    os.environ["AIK_SKIP_SCORING"] = "1"
    if not 1 <= args.count <= 8:
        parser.error("count must be 1..8")
    if (args.fixed_strength is None) != (args.fixed_noise_strength is None):
        parser.error("fixed strength and fixed noise strength must be supplied together")
    if args.fixed_strength is not None and args.count != 2:
        parser.error("fixed plus random style mode requires exactly two candidates")
    manifest = prepare_manifest(args.manifest, args.backing, args.vocal)
    duration = float(manifest.get("duration_sec") or 0)
    with contextlib.redirect_stdout(sys.stderr):
        hardware = detect_hardware()
        plan = execution_plan(hardware, duration, args.count)
        if args.device == "cuda":
            if plan["device"] != "cuda":
                raise ValueError("GPU unavailable or input exceeds its estimated duration limit; "
                                 "CPU confirmation is required. Retry from the application.")
            cfg.MAX_CLIP_SECONDS = hardware["gpu_max_seconds"]
        else:
            import torch
            import psutil
            torch.set_num_threads(min(8, psutil.cpu_count(logical=False) or 4))
            torch.set_num_interop_threads(1)
            if sys.platform == "win32":
                psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
            cfg.DIT_INIT_KWARGS = dict(device="cpu", use_flash_attention=False,
                                      compile_model=False, offload_to_cpu=False,
                                      offload_dit_to_cpu=False, quantization=None)
            cfg.MAX_CLIP_SECONDS = math.ceil(duration)
            # The pinned no-LM cover path accepts source length directly. This
            # request-specific ceiling does not claim a GPU tier for the CPU.
            os.environ["ACESTEP_GENERATION_TIMEOUT"] = str(plan["generation_timeout"])
    target = Path(args.job_dir)
    target.mkdir(parents=True, exist_ok=False)
    (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    emit("status", message=f"Generating style candidates on {args.device.upper()}", execution=plan)
    with contextlib.redirect_stdout(sys.stderr):
        import style_test
        original_run = style_test.run_one_cover_combo
        completed = 0

        def report_combo(*combo_args, **combo_kwargs):
            nonlocal completed
            emit("progress", value=completed / args.count, message=f"Generating candidate {completed + 1}/{args.count}")
            record = original_run(*combo_args, **combo_kwargs)
            completed += 1
            emit("progress", value=completed / args.count, message=f"Completed candidate {completed}/{args.count}")
            return record

        style_test.run_one_cover_combo = report_combo
        if args.fixed_strength is not None:
            strengths = style_test.parse_float_list(args.strengths)
            noises = style_test.parse_float_list(args.noise_strengths)
            combos = choose_style_combos(args.fixed_strength, args.fixed_noise_strength,
                                         strengths, noises)
            style_test.diverse_combos = lambda *_: combos
            emit("status", message="Generating one fixed and one random style candidate")
        tag = re.sub(r"[^a-zA-Z0-9]+", "", args.tag)[:20] or "style"
        argv = [str(target), "--style", args.style, "--tag", tag, "--mode", "cover-explore", "--explore-count", str(args.count), "--cover-strengths", args.strengths, "--cover-noise-strengths", args.noise_strengths, "--seeds", args.seeds]
        style_test.run(style_test.build_parser().parse_args(argv))
    output = target / "style_test" / "style_test_manifest.json"
    run_manifest = json.loads(output.read_text(encoding="utf-8"))
    records = run_manifest["runs"]
    if any(target.resolve() not in Path(r["output"]).resolve().parents
           for r in records if r.get("success") and r.get("output")):
        raise RuntimeError("Candidate output escaped the style job directory")
    validate_candidate_lengths(records, duration)
    run_manifest["execution"] = plan
    output.write_text(json.dumps(run_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    candidates = [r for r in records if r.get("success") and r.get("output") and Path(r["output"]).is_file()]
    if not candidates:
        errors = "; ".join(str(r.get("error") or r.get("status_message")) for r in records)
        raise RuntimeError("ACE-Step produced no successful candidates: " + errors)
    emit("result", ok=True, candidates=candidates, failures=[r for r in records if not r.get("success")], manifest=str(output), execution=plan)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        traceback.print_exc(file=sys.stderr)
        emit("error", message=str(error))
        sys.exit(1)
