"""Per-song run log (style_test_manifest.json) and its table view (report.md)."""
import json
import subprocess
import sys
from pathlib import Path

import torch

import style_config as cfg

MANIFEST_NAME = "style_test_manifest.json"


def _git_commit(repo_dir: Path) -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo_dir), capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except Exception:  # noqa: BLE001 -- informational field only
        return "unknown"


def collect_environment_info() -> dict:
    cuda = torch.cuda.is_available()
    return {
        "ace_step_git_commit": _git_commit(cfg.ACE_STEP_ROOT),
        "python_version": sys.version.split()[0],
        "torch_version": torch.__version__,
        "cuda_version": torch.version.cuda,
        "gpu": torch.cuda.get_device_name(0) if cuda else None,
        "vram_total_gb": round(torch.cuda.get_device_properties(0).total_memory / 1e9, 2) if cuda else None,
    }


def load_or_init_style_manifest(style_test_dir: Path, song: str, manifest_path: str, start: float, duration: float) -> dict:
    path = style_test_dir / MANIFEST_NAME
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {
        "source_song": song,
        "source_manifest": manifest_path,
        "segment": {"start": start, "duration": duration},
        "environment": collect_environment_info(),
        "runs": [],
    }


def save_style_manifest(style_test_dir: Path, manifest: dict) -> Path:
    path = style_test_dir / MANIFEST_NAME
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def append_run(style_test_dir: Path, manifest: dict, run: dict) -> None:
    manifest["runs"].append(run)
    save_style_manifest(style_test_dir, manifest)


def _table(header: list[str], rows: list[list]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    return lines + ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]


def _output_or_error(run: dict) -> str:
    return run.get("output") or f"FAILED: {run.get('error')}"


def _short_style(run: dict, width: int = 50) -> str:
    style = run.get("style", "")
    return style if len(style) <= width else style[:width] + "..."


def _cover_rows(runs: list[dict]) -> list[list]:
    return [
        [_short_style(r), r.get("cover_strength"), r.get("cover_noise_strength"), r.get("seed"), r.get("steps"),
         r.get("chroma_similarity"), r.get("rhythm_similarity"), r.get("search_candidate_rank", ""),
         r.get("gain_db"), r.get("elapsed_sec"), r.get("peak_vram_allocated_mb"), _output_or_error(r)]
        for r in runs
    ]


def write_report(style_test_dir: Path, manifest: dict) -> Path:
    env = manifest.get("environment", {})
    runs = manifest["runs"]
    cover_runs = [r for r in runs if r.get("mode") == "cover"]
    failed = [r for r in runs if not r.get("success")]

    lines = [
        f"# Style report -- {manifest.get('source_song')}",
        "",
        "```text",
        f"ACE-Step commit : {env.get('ace_step_git_commit')}",
        f"Python / torch  : {env.get('python_version')} / {env.get('torch_version')} (CUDA {env.get('cuda_version')})",
        f"GPU             : {env.get('gpu')} ({env.get('vram_total_gb')} GB)",
        f"Model           : {cfg.COVER_MODEL}",
        f"Segment         : start={manifest['segment']['start']}s duration={manifest['segment']['duration']}s",
        "```",
        "",
    ]
    if cover_runs:
        lines += ["## Cover", ""]
        lines += _table(
            ["style", "strength", "noise strength", "seed", "steps", "chroma", "rhythm", "candidate #", "gain (dB)", "time (s)", "VRAM (MB)", "output"],
            _cover_rows(cover_runs),
        ) + [""]
    if failed:
        lines += ["## Failed runs", ""] + [f"- `{r.get('id')}`: {r.get('error')}" for r in failed] + [""]

    report_path = style_test_dir / "report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path
