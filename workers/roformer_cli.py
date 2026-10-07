"""Run audio-separator while loading its already-normalized input WAV with soundfile."""

import sys
from pathlib import Path

import os
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime_config import MODEL, MODEL_CONFIG, bootstrap_worker

bootstrap_worker()

model_dir = Path(os.environ["AIK_ROFORMER_MODEL_DIR"])
for filename in (MODEL, MODEL_CONFIG):
    if not (model_dir / filename).is_file():
        raise FileNotFoundError(f"Missing separation asset: {model_dir / filename}. Run scripts/setup.ps1.")

# Model preparation is explicit. Inference must not download alternative models.
def no_network(*args, **kwargs):
    raise RuntimeError("Network access is disabled during separation. Run scripts/prepare_assets.py to prepare assets.")
requests.sessions.Session.request = no_network

import librosa
import soundfile as sf

input_path = Path(sys.argv[1]).resolve()
original_load = librosa.load


def load_audio(path, *args, **kwargs):
    if (isinstance(path, (str, Path)) and Path(path).resolve() == input_path and not args and kwargs.get("mono") is False
            and kwargs.get("sr") == sf.info(path).samplerate and not kwargs.get("offset")
            and kwargs.get("duration") is None):
        audio, sample_rate = sf.read(path, dtype="float32", always_2d=True)
        return audio.T, sample_rate
    return original_load(path, *args, **kwargs)


librosa.load = load_audio

from audio_separator.separator import Separator


def pinned_model_files(self, model_filename):
    # audio-separator's remote catalogue can change independently of our release.
    # Register only the checked local Kim checkpoint and its matching MDXC config.
    if model_filename != MODEL:
        raise ValueError(f"Only the pinned Kim model is supported: {MODEL}")
    self.model_is_uvr_vip = False
    self.model_friendly_name = "Kim Mel-Band RoFormer (MIT)"
    return MODEL, "MDXC", self.model_friendly_name, str(model_dir / MODEL), MODEL_CONFIG


Separator.download_model_files = pinned_model_files
from audio_separator.utils.cli import main  # noqa: E402

main()
