"""Project-owned paths shared by both UIs, workers and preparation scripts."""
import os
import sys
from pathlib import Path

ROOT = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
def interpreter(group):
    portable = ROOT / '.runtime' / group / 'python.exe'
    return portable if portable.is_file() else ROOT / '.runtime' / group / 'Scripts/python.exe'

DEFAULTS = {
    "roformer_python": interpreter('separation'),
    "acestep_python": interpreter('styling'),
    "roformer_model_dir": ROOT / "models/separation",
    "ace_step_root": ROOT / "third_party/ACE-Step-1.5",
    "acestep_model_dir": ROOT / "models/acestep",
}
MODEL = "MelBandRoformer.ckpt"
MODEL_CONFIG = "config_vocals_mel_band_roformer_kim.yaml"


def configured_path(settings, key):
    value = settings.get(key)
    path = Path(value).expanduser() if value else DEFAULTS[key]
    return (path if path.is_absolute() else ROOT / path).resolve()


def runtime(settings, key):
    path = configured_path(settings, key)
    return path if path.is_file() else None


def worker_env(settings=None):
    settings = settings or {}
    cache = ROOT / ".app_data/cache"
    paths = {
        "AIK_LOG_DIR": ROOT / ".app_data/logs",
        "AIK_ROFORMER_MODEL_DIR": configured_path(settings, "roformer_model_dir"),
        "AIK_ACE_STEP_ROOT": configured_path(settings, "ace_step_root"),
        "ACESTEP_PROJECT_ROOT": configured_path(settings, "ace_step_root"),
        "ACESTEP_CHECKPOINTS_DIR": configured_path(settings, "acestep_model_dir"),
        "HF_HOME": cache / "huggingface",
        "HF_HUB_CACHE": cache / "huggingface/hub",
        "HF_MODULES_CACHE": cache / "huggingface/modules",
        "TORCH_HOME": cache / "torch",
        "TORCHINDUCTOR_CACHE_DIR": cache / "torchinductor",
        "TRITON_CACHE_DIR": cache / "triton",
        "MODELSCOPE_CACHE": cache / "modelscope",
        "MPLCONFIGDIR": cache / "matplotlib",
        "GRADIO_TEMP_DIR": cache / "gradio",
        "NUMBA_CACHE_DIR": cache / "numba",
        "XDG_CACHE_HOME": cache,
        "PIP_CACHE_DIR": cache / "pip",
        "UV_CACHE_DIR": cache / "uv",
        "UV_CREDENTIALS_DIR": cache / "uv-credentials",
        "TMP": cache / "tmp",
        "TEMP": cache / "tmp",
    }
    asset_keys = {"AIK_ROFORMER_MODEL_DIR", "AIK_ACE_STEP_ROOT",
                  "ACESTEP_PROJECT_ROOT", "ACESTEP_CHECKPOINTS_DIR"}
    for key, path in paths.items():
        if key not in asset_keys:
            path.mkdir(parents=True, exist_ok=True)
    return {**{key: str(value) for key, value in paths.items()},
            "PYTHONUTF8": "1", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}


def prepare_ffmpeg():
    """Expose imageio's bundled executable to tools that invoke `ffmpeg` by name."""
    import hashlib
    import shutil
    import uuid
    import imageio_ffmpeg
    source = Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve()
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    directory = ROOT / '.tools' / 'ffmpeg' / digest
    target = directory / ('ffmpeg.exe' if os.name == 'nt' else 'ffmpeg')
    directory.mkdir(parents=True, exist_ok=True)
    if not target.is_file() or target.stat().st_size != source.stat().st_size:
        temporary = directory / (target.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            shutil.copy2(source, temporary)
            if not target.is_file() or target.stat().st_size != source.stat().st_size:
                temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    entries = os.environ.get('PATH', '').split(os.pathsep)
    os.environ['PATH'] = os.pathsep.join([str(directory), *[entry for entry in entries if entry != str(directory)]])
    return target


def bootstrap_worker():
    # Keep explicit asset selections, but always put tool caches in the project.
    settings = {key: os.environ[env_key] for key, env_key in (
        ("roformer_model_dir", "AIK_ROFORMER_MODEL_DIR"),
        ("ace_step_root", "AIK_ACE_STEP_ROOT"),
        ("acestep_model_dir", "ACESTEP_CHECKPOINTS_DIR"),
    ) if os.environ.get(env_key)}
    os.environ.update(worker_env(settings))
    prepare_ffmpeg()


def migration_issues(settings):
    issues = []
    for key in DEFAULTS:
        if settings.get(key):
            path = configured_path(settings, key)
            if "ai_karaoke_tool_test" in {part.lower() for part in path.parts}:
                issues.append(f"{key}: {path}（仍指向參考 PoC）")
            elif not path.exists():
                issues.append(f"{key}: {path}（路徑不存在）")
    return issues
