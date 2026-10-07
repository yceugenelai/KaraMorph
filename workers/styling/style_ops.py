"""Manifest reading, audio clipping, speed/key-changed song copies, and ACE-Step Cover inference.

Kept separate from the main project's audio_ops.py so separation and styling stay loosely coupled.
"""
import json
import time
from math import gcd
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf
import torch
from loguru import logger

import style_config as cfg


class ManifestError(Exception):
    pass


def resolve_song_dir(song_arg: str) -> Path:
    candidates = [Path(song_arg), cfg.MAIN_PROJECT_ROOT / song_arg, cfg.OUTPUT_ROOT / Path(song_arg).name]
    for c in candidates:
        if c.is_dir():
            return c.resolve()
    raise ManifestError(f"No song directory for {song_arg!r}. Tried: " + ", ".join(str(c) for c in candidates))


def load_manifest(song_dir: Path) -> dict:
    manifest_path = song_dir / "manifest.json"
    if not manifest_path.is_file():
        raise ManifestError(f"No manifest.json in {song_dir} -- run pipeline.py on this song first.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["_manifest_path"] = str(manifest_path)
    return manifest


def get_stem_path(manifest: dict, stem: str) -> Path:
    stems = manifest.get("stems", {})
    if stem not in stems:
        raise ManifestError(f"manifest.json has no {stem!r} stem (available: {sorted(stems)}) -- re-run pipeline.py on this song.")
    path = Path(stems[stem])
    if not path.is_file():
        raise ManifestError(f"manifest.json lists {stem!r} at {path}, but that file doesn't exist.")
    return path


def get_manifest_bpm(manifest: dict) -> Optional[int]:
    bpm = manifest.get("bpm")
    return int(round(bpm)) if bpm else None


def _resample(audio: np.ndarray, sr: int, target_sr: int) -> np.ndarray:
    from scipy.signal import resample_poly

    g = gcd(sr, target_sr)
    return resample_poly(audio, target_sr // g, sr // g, axis=0).astype(np.float32)


def _load_audio(path: Path, target_sr: int = cfg.SAMPLE_RATE) -> tuple[np.ndarray, int]:
    audio, sr = sf.read(str(path), dtype="float32", always_2d=True)
    if sr != target_sr:
        audio, sr = _resample(audio, sr, target_sr), target_sr
    return audio, sr


def clip_audio(src_path: Path, start: float, duration: float, out_path: Path) -> Path:
    audio, sr = _load_audio(src_path)
    start_sample = int(round(start * sr))
    if start_sample >= audio.shape[0]:
        raise ValueError(f"{src_path}: --start {start}s is beyond its length ({audio.shape[0] / sr:.1f}s)")
    clip = audio[start_sample:start_sample + int(round(duration * sr))]
    if clip.shape[0] / sr < duration * 0.9:
        logger.warning(f"{src_path.name}: only {clip.shape[0] / sr:.1f}s of the requested {duration}s available")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out_path), clip, sr, subtype="FLOAT")
    return out_path


def prepare_source_clip(manifest: dict, start: float, duration: float, out_dir: Path) -> Path:
    """Clip the BS-Roformer instrumental to [start, start+duration) under out_dir/source/."""
    tag = f"{int(round(start))}_{int(round(start + duration))}"
    src = get_stem_path(manifest, "instrumental")
    return clip_audio(src, start, duration, out_dir / "source" / f"instrumental_{tag}.wav")


DERIVED_STEMS = ("instrumental", "vocals", "drums", "bass", "other")


def derived_song_name(song_name: str, speed: float = 1.0, semitones: float = 0.0) -> str:
    """-2 semitones, 0.8x -> <song>_key-2_spd08"""
    name = song_name
    if semitones:
        key = f"{int(semitones):+d}" if float(semitones).is_integer() else f"{semitones:+.1f}".replace(".", "p")
        name += f"_key{key}"
    if speed != 1.0:
        name += f"_spd{int(round(speed * 10)):02d}"
    return name


def ensure_derived_song(song_arg: str, speed: float = 1.0, semitones: float = 0.0, formant: str = "preserved") -> Path:
    """Build (or complete) output/<song>[_key+-N][_spdNN]/: every stem time-stretched and/or pitch-shifted in
    one rubberband pass, with its own manifest.json (bpm scaled by speed), so it behaves like any other song.
    Stems missing from an already-built directory are filled in. No change -> the original song dir."""
    import audio_fx

    if speed <= 0:
        raise ValueError(f"speed must be > 0, got {speed}")
    orig_dir = resolve_song_dir(song_arg)
    if speed == 1.0 and not semitones:
        return orig_dir
    orig = load_manifest(orig_dir)
    orig_stems = orig.get("stems", {})
    get_stem_path(orig, "instrumental")

    new_dir = cfg.OUTPUT_ROOT / derived_song_name(orig_dir.name, speed, semitones)
    manifest_path = new_dir / "manifest.json"
    derived = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {
        "input_file": orig.get("input_file"),
        "session_dir": str(new_dir),
        "bpm": round(orig["bpm"] * speed, 2) if orig.get("bpm") else None,
        "stems": {},
        "derived_from": {"song": orig_dir.name, "speed": speed, "semitones": semitones},
    }
    todo = [s for s in DERIVED_STEMS if s in orig_stems and not Path(derived["stems"].get(s, "")).is_file()]
    if not todo:
        print(f"[derive] reusing {new_dir}")
        return new_dir

    print(f"[derive] {new_dir.name}: {todo} speed {speed}x, {semitones:+g} semitones ...")
    for stem in todo:
        dst = new_dir / "stems" / f"{stem}.wav"
        audio_fx.process_file(Path(orig_stems[stem]), dst, tempo=speed, semitones=semitones, formant=formant)
        derived["stems"][stem] = str(dst)
    derived["duration_sec"] = round(sf.info(derived["stems"]["instrumental"]).duration, 3)  # measured, stretching isn't exact
    manifest_path.write_text(json.dumps(derived, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[derive] bpm {orig.get('bpm')} -> {derived['bpm']}, duration {orig.get('duration_sec')} -> {derived['duration_sec']}")
    return new_dir


_dit_handlers: dict = {}  # model name -> handler, so a sweep doesn't reload the model every run
# Known limitation: memory held by the cached handler creeps up over ~10 back-to-back
# generations and can OOM near the end of a long sweep (empty_cache() doesn't help). Rebuilding
# the handler mid-run was tried and hard-crashed the process (torchao INT8 fallback path), so a
# failed run is just recorded and the sweep continues. See usage.md.


def get_dit_handler(model_name: str):
    if model_name in _dit_handlers:
        return _dit_handlers[model_name]
    from acestep.handler import AceStepHandler

    class LocalAssetHandler(AceStepHandler):
        def _ensure_models_present(self, *, checkpoint_path, config_path, prefer_source, vae_variant=None):
            # Upstream's generic precheck also requires the unused base model.
            # Our no-LM cover workflow needs only turbo, VAE and embedding.
            required = [checkpoint_path / config_path / "model.safetensors",
                        checkpoint_path / "vae/diffusion_pytorch_model.safetensors",
                        checkpoint_path / "Qwen3-Embedding-0.6B/model.safetensors"]
            missing = [str(path) for path in required if not path.is_file()]
            if missing:
                return "Missing local assets; run scripts/setup.ps1: " + ", ".join(missing), False
            return None

    handler = LocalAssetHandler()
    t0 = time.time()
    status_msg, success = handler.initialize_service(
        project_root=str(cfg.ACE_STEP_ROOT), config_path=model_name, **cfg.DIT_INIT_KWARGS,
    )
    if not success:
        raise RuntimeError(f"ACE-Step DiT init failed for {model_name!r}: {status_msg}")
    if str(handler.device).split(":")[0] != cfg.DIT_INIT_KWARGS["device"]:
        raise RuntimeError(f"ACE-Step device mismatch: requested {cfg.DIT_INIT_KWARGS['device']}, got {handler.device}")
    logger.info(f"DiT handler ready ({model_name}) in {time.time() - t0:.1f}s")
    _dit_handlers[model_name] = handler
    return handler


def _vram_stats() -> dict:
    if not torch.cuda.is_available():
        return {"peak_allocated_mb": None, "peak_reserved_mb": None}
    return {
        "peak_allocated_mb": round(torch.cuda.max_memory_allocated() / 1e6, 1),
        "peak_reserved_mb": round(torch.cuda.max_memory_reserved() / 1e6, 1),
    }


def _run_generation(model_name: str, params, save_dir: Path) -> dict:
    from acestep.inference import GenerationConfig, generate_music

    save_dir.mkdir(parents=True, exist_ok=True)
    error, result, elapsed = None, None, 0.0
    try:
        handler = get_dit_handler(model_name)
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
        config = GenerationConfig(
            batch_size=1,
            use_random_seed=False,
            seeds=[params.seed] if params.seed is not None and params.seed >= 0 else None,
            audio_format="wav",
        )
        t0 = time.time()
        result = generate_music(handler, None, params=params, config=config, save_dir=str(save_dir))
        elapsed = time.time() - t0
    except Exception as exc:  # noqa: BLE001 -- record any failure instead of aborting the sweep
        error = f"{type(exc).__name__}: {exc}"
        logger.exception(f"ACE-Step generation failed ({model_name})")

    stats = _vram_stats()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    success = bool(result and result.success) and error is None
    return {
        "success": success,
        "elapsed_sec": round(elapsed, 2),
        "peak_vram_allocated_mb": stats["peak_allocated_mb"],
        "peak_vram_reserved_mb": stats["peak_reserved_mb"],
        "output": result.audios[0].get("path") if success and result.audios else None,
        "status_message": result.status_message if result else None,
        "error": error or (None if success else (result.status_message if result else "unknown failure")),
    }


def run_cover(
    src_audio_path: Path,
    style_prompt: str,
    cover_strength: float,
    seed: int,
    steps: int,
    bpm: Optional[int],
    save_dir: Path,
    cover_noise_strength: float = cfg.DEFAULT_COVER_NOISE_STRENGTH,
) -> dict:
    from acestep.inference import GenerationParams

    params = GenerationParams(
        task_type="cover",
        src_audio=str(src_audio_path),
        caption=f"{style_prompt}, instrumental, no vocals",
        lyrics="[Instrumental]",
        instrumental=True,
        audio_cover_strength=cover_strength,
        cover_noise_strength=cover_noise_strength,
        bpm=bpm,
        inference_steps=steps,
        shift=cfg.COVER_SHIFT,
        seed=seed,
        thinking=False,
    )
    return _run_generation(cfg.COVER_MODEL, params, save_dir)
