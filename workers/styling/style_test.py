"""ACE-Step 1.5 styling CLI: restyle a separated song's accompaniment (Cover).

Input:  output/<song>/manifest.json (from pipeline.py; only stems.instrumental is read)
Output: output/<song>/style_test/ (wavs, style_test_manifest.json, report.md)
        With --speed/--semitones the song is first copied to output/<song>[_key+-N][_spdNN]/
        and that copy is restyled.

Examples:
    style_test.py king --style "1980s Japanese city pop, ..."                       # cover-explore, whole song
    style_test.py king --style "..." --speed 0.8
    style_test.py king --mode cover --style "..." --cover-strength 0.3 --cover-noise-strength 0.2 --steps 20 --seeds 1234,5678
Use --help for CLI options; the consumer UI supplies its selected settings.
"""
import argparse
import os
import random
import re
import shutil
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import audio_fx
import loudness_ops as loud
import report_ops as report
import style_config as cfg
import style_ops as ops

def slugify(text: str, max_len: int = 20) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "", text)[:max_len] or "style"


def parse_float_list(raw: str) -> list[float]:
    return [float(x) for x in raw.split(",") if x.strip()]


def parse_int_list(raw: str) -> list[int]:
    return [int(x) for x in raw.split(",") if x.strip()]


def resolve_seeds(args) -> list[int]:
    if args.seeds:
        return parse_int_list(args.seeds)
    return [args.seed if args.seed is not None else cfg.DEFAULT_SEEDS[0]]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("song", help="output/<song> path, or bare song name (e.g. king)")
    p.add_argument(
        "--mode", choices=["cover-explore", "cover-search", "cover"], default="cover-explore",
        help="cover-explore (default): vary strength, noise strength and seed together over a few diverse combos, list all. "
        "cover-search: same sampling with strength capped at 0.5, and flag a spread of --candidates by chroma. "
        "cover: the given parameter lists (full cross product).",
    )
    p.add_argument("--style", required=True, help="target style caption (describe instruments/mood, not vocals)")
    p.add_argument("--tag", default=None, help="short label for output filenames (default: slug of --style)")
    p.add_argument("--start", type=float, default=cfg.DEFAULT_START)
    p.add_argument(
        "--duration", type=float, default=cfg.DEFAULT_DURATION,
        help="clip length in seconds; 0 or negative (default) = to the end of the song",
    )
    p.add_argument("--seed", type=int, default=None, help=f"single seed (default {cfg.DEFAULT_SEEDS[0]})")
    p.add_argument("--seeds", type=str, default=None, help="comma-separated seeds; cover runs once per seed, cover-search/cover-explore sample from them")
    p.add_argument("--steps", type=int, default=None, help="inference steps (default depends on --mode)")

    cover = p.add_argument_group("cover / cover-search / cover-explore")
    cover.add_argument("--cover-strength", type=float, default=None, help="audio_cover_strength: how long the source's semantic conditioning is used before the caption takes over")
    cover.add_argument("--cover-strengths", type=str, default=None, help="comma-separated list")
    cover.add_argument("--cover-noise-strength", type=float, default=None, help="how tightly the output is anchored to the source's harmony/melody (0 = not at all)")
    cover.add_argument("--cover-noise-strengths", type=str, default=None, help="comma-separated list")
    cover.add_argument("--candidates", type=int, default=None, help=f"cover-search: how many candidates to flag (default {cfg.DEFAULT_SEARCH_CANDIDATES})")
    cover.add_argument("--explore-count", type=int, default=None, help=f"cover-search/cover-explore: number of combos (default {cfg.DEFAULT_SEARCH_COUNT}/{cfg.DEFAULT_EXPLORE_COUNT})")
    cover.add_argument("--explore-rng-seed", type=int, default=42, help="cover-search/cover-explore: seed for pairing up the parameter pools (not a diffusion seed)")
    cover.add_argument(
        "--no-loudness-match", dest="loudness_match", action="store_false",
        help="keep each Cover at ACE-Step's own level (default: attenuate it to the source instrumental's LUFS so A/B listening is fair)",
    )

    fx = p.add_argument_group("speed / key change before styling")
    audio_fx.add_fx_arguments(fx)
    return p


def run_one_cover_combo(
    args, src_path: Path, style_test_dir: Path, style_manifest: dict,
    strength: float, noise_strength: float, seed: int, steps: int, tag: str, bpm, out_dir: Path,
) -> dict:
    """Generate one Cover, score it against the source, rename the wav with the scores, log it."""
    run_id = f"{tag}_{len(style_manifest['runs']) + 1:02d}"
    print(f"\n=== {run_id} ===")
    result = ops.run_cover(
        src_audio_path=src_path,
        style_prompt=args.style,
        cover_strength=strength,
        cover_noise_strength=noise_strength,
        seed=seed,
        steps=steps,
        bpm=bpm,
        save_dir=out_dir,
    )
    chroma_score = rhythm_score = None
    if result["success"] and result["output"]:
        final_path = out_dir / f"{run_id}.wav"
        try:
            shutil.move(result["output"], final_path)
            result["output"] = str(final_path)
        except Exception as exc:  # noqa: BLE001 -- scoring is a convenience, keep the generated wav
            print(f"  (warning: rename/scoring failed: {exc})")
    loudness = {}
    if args.loudness_match and result["success"] and result["output"]:
        try:
            loudness = loud.match_loudness(Path(result["output"]), src_path)  # after scoring
        except Exception as exc:  # noqa: BLE001 -- keep the un-matched wav
            print(f"  (warning: loudness matching failed: {exc})")
    print(f"  chroma={chroma_score} rhythm={rhythm_score} gain={loudness.get('gain_db')}dB success={result['success']} "
          f"elapsed={result['elapsed_sec']}s vram={result['peak_vram_allocated_mb']}MB")
    print(f"  output={result['output']} error={result['error']}")
    record = {
        "id": run_id,
        "mode": "cover",
        "style": args.style,
        "seed": seed,
        "cover_strength": strength,
        "cover_noise_strength": noise_strength,
        "chroma_similarity": chroma_score,
        "rhythm_similarity": rhythm_score,
        "steps": steps,
        **loudness,
        **result,
    }
    report.append_run(style_test_dir, style_manifest, record)
    return record


def run_cover_mode(
    args, manifest: dict, src_path: Path, style_test_dir: Path, style_manifest: dict,
    default_strengths=None, default_noise_strengths=None, default_steps=None,
) -> list[dict]:
    """Cross product of strengths x noise strengths x seeds; returns the run records."""
    strengths = (
        [args.cover_strength] if args.cover_strength is not None
        else parse_float_list(args.cover_strengths) if args.cover_strengths
        else list(default_strengths if default_strengths is not None else cfg.DEFAULT_COVER_STRENGTHS)
    )
    noise_strengths = (
        [args.cover_noise_strength] if args.cover_noise_strength is not None
        else parse_float_list(args.cover_noise_strengths) if args.cover_noise_strengths
        else list(default_noise_strengths) if default_noise_strengths is not None
        else [cfg.DEFAULT_COVER_NOISE_STRENGTH]
    )
    seeds = resolve_seeds(args)
    steps = args.steps or default_steps or cfg.COVER_INFERENCE_STEPS
    tag = args.tag or slugify(args.style)
    bpm = ops.get_manifest_bpm(manifest)
    out_dir = style_test_dir / "cover"
    return [
        run_one_cover_combo(args, src_path, style_test_dir, style_manifest, strength, noise_strength, seed, steps, tag, bpm, out_dir)
        for strength in strengths
        for noise_strength in noise_strengths
        for seed in seeds
    ]


def select_candidates(runs: list[dict], n: int) -> list[dict]:
    """n runs evenly spaced by chroma rank (not the top n): the usable score threshold differs
    per song, so give a spread to audition."""
    scored = sorted(
        (r for r in runs if r.get("success") and r.get("chroma_similarity") is not None),
        key=lambda r: r["chroma_similarity"],
    )
    if not scored:
        return []
    n = max(1, min(n, len(scored)))
    if n == 1:
        return [scored[len(scored) // 2]]
    return [scored[i] for i in sorted({round(k * (len(scored) - 1) / (n - 1)) for k in range(n)})]


def print_run_summary(title: str, runs: list[dict]) -> None:
    print(f"\n=== {title} ===")
    for r in runs:
        rank = f"#{r['search_candidate_rank']} " if r.get("search_candidate_rank") else ""
        print(
            f"  {rank}strength={r['cover_strength']} cns={r['cover_noise_strength']} seed={r['seed']} "
            f"chroma={r['chroma_similarity']} rhythm={r['rhythm_similarity']}\n       -> {r['output']}"
        )


def run_diverse_cover_combos(
    args, manifest: dict, src_path: Path, style_test_dir: Path, style_manifest: dict,
    default_strengths, default_noise_strengths, default_seeds, default_count: int,
) -> list[dict]:
    """Run `n` combos that differ on strength, noise strength and seed at once (see diverse_combos).
    A single --cover-strength/--cover-noise-strength/--seed pins that axis; the plural flags replace its pool."""
    strengths = (
        [args.cover_strength] if args.cover_strength is not None
        else parse_float_list(args.cover_strengths) if args.cover_strengths
        else list(default_strengths)
    )
    noise_strengths = (
        [args.cover_noise_strength] if args.cover_noise_strength is not None
        else parse_float_list(args.cover_noise_strengths) if args.cover_noise_strengths
        else list(default_noise_strengths)
    )
    seeds = (
        parse_int_list(args.seeds) if args.seeds
        else [args.seed] if args.seed is not None
        else list(default_seeds)
    )
    steps = args.steps or cfg.SEARCH_INFERENCE_STEPS
    tag = args.tag or slugify(args.style)
    bpm = ops.get_manifest_bpm(manifest)
    out_dir = style_test_dir / "cover"
    n = args.explore_count or default_count
    return [
        run_one_cover_combo(args, src_path, style_test_dir, style_manifest, strength, noise_strength, seed, steps, tag, bpm, out_dir)
        for strength, noise_strength, seed in diverse_combos(strengths, noise_strengths, seeds, n, args.explore_rng_seed)
    ]


def run_cover_search_mode(args, manifest: dict, src_path: Path, style_test_dir: Path, style_manifest: dict) -> None:
    runs = run_diverse_cover_combos(
        args, manifest, src_path, style_test_dir, style_manifest,
        default_strengths=cfg.DEFAULT_SEARCH_COVER_STRENGTHS,
        default_noise_strengths=cfg.DEFAULT_SEARCH_COVER_NOISE_STRENGTHS,
        default_seeds=cfg.DEFAULT_SEARCH_SEEDS,
        default_count=cfg.DEFAULT_SEARCH_COUNT,
    )
    candidates = select_candidates(runs, args.candidates or cfg.DEFAULT_SEARCH_CANDIDATES)
    for rank, run in enumerate(candidates, 1):
        run["search_candidate_rank"] = rank  # same dict object as in style_manifest["runs"]
    print_run_summary(f"{len(candidates)} candidates to audition (spread over {len(runs)} runs, low->high chroma)", candidates)
    if not candidates:
        print("  (no successful runs -- see the errors above)")
    report.save_style_manifest(style_test_dir, style_manifest)


def diverse_combos(strengths, noise_strengths, seeds, n: int, rng_seed: int) -> list[tuple]:
    """Pair n values from each pool (each shuffled, then cycled) so every combo differs on all
    three axes; with n == pool size every value is used once (a small Latin-hypercube design)."""
    rng = random.Random(rng_seed)

    def cycle_shuffled(pool):
        pool = list(pool)
        rng.shuffle(pool)
        return [pool[i % len(pool)] for i in range(n)]

    return list(zip(cycle_shuffled(strengths), cycle_shuffled(noise_strengths), cycle_shuffled(seeds)))


def run_cover_explore_mode(args, manifest: dict, src_path: Path, style_test_dir: Path, style_manifest: dict) -> None:
    records = run_diverse_cover_combos(
        args, manifest, src_path, style_test_dir, style_manifest,
        default_strengths=cfg.DEFAULT_EXPLORE_COVER_STRENGTHS,
        default_noise_strengths=cfg.DEFAULT_SEARCH_COVER_NOISE_STRENGTHS,
        default_seeds=cfg.DEFAULT_EXPLORE_SEEDS,
        default_count=cfg.DEFAULT_EXPLORE_COUNT,
    )
    succeeded = sorted(
        (r for r in records if r.get("success")),
        key=lambda r: r.get("chroma_similarity") if r.get("chroma_similarity") is not None else -1,
        reverse=True,
    )
    print_run_summary(f"{len(succeeded)}/{len(records)} succeeded, sorted by chroma (high->low)", succeeded)
    if len(succeeded) < len(records):
        print("  (some combos failed -- see errors above; a late-sweep OOM is a known limit, see usage.md)")


def resolve_duration(args, manifest: dict) -> float:
    if args.duration is not None and args.duration > 0:
        return args.duration
    total = manifest.get("duration_sec")
    if not total:
        raise SystemExit("--duration <=0 means 'to the end of the song', but manifest.json has no duration_sec")
    if total - args.start <= 0:
        raise SystemExit(f"--start {args.start}s is beyond the song length ({total:.1f}s)")
    return total - args.start


def check_duration(duration: float) -> None:
    if duration > cfg.MAX_CLIP_SECONDS:
        raise SystemExit(
            f"{duration:.0f}s exceeds the configured {cfg.MAX_CLIP_SECONDS}s style duration limit. "
            "Use the app for GPU estimation and confirmed CPU processing, or choose a shorter --duration."
        )


def run(args) -> None:
    song_dir = ops.ensure_derived_song(args.song, args.speed, args.semitones, args.formant)
    manifest = ops.load_manifest(song_dir)
    style_test_dir = song_dir / "style_test"
    args.duration = resolve_duration(args, manifest)
    check_duration(args.duration)

    print(f"Song dir: {song_dir}")
    print(f"Clipping [{args.start}s, {args.start + args.duration}s) from stems...")
    src_path = ops.prepare_source_clip(manifest, args.start, args.duration, style_test_dir)
    style_manifest = report.load_or_init_style_manifest(
        style_test_dir, song=song_dir.name, manifest_path=manifest["_manifest_path"],
        start=args.start, duration=args.duration,
    )

    mode_runners = {
        "cover": run_cover_mode,
        "cover-search": run_cover_search_mode,
        "cover-explore": run_cover_explore_mode,
    }
    mode_runners[args.mode](args, manifest, src_path, style_test_dir, style_manifest)

    report_path = report.write_report(style_test_dir, style_manifest)
    print(f"\nrun log + report updated: {report_path}")


def main() -> None:
    run(build_parser().parse_args())


if __name__ == "__main__":
    main()
