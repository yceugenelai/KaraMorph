import os
from pathlib import Path
import torch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_ROOT = Path(os.environ.get("AIK_OUTPUT_ROOT", PROJECT_ROOT / "outputs"))
SAMPLE_RATE = 44100
DEVICE = os.environ.get("AIK_DEVICE", "auto")
if DEVICE == "auto":
    DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
ROFORMER_MODEL = "MelBandRoformer.ckpt"
ROFORMER_MODEL_DIR = Path(os.environ.get("AIK_ROFORMER_MODEL_DIR", PROJECT_ROOT / "models/separation"))
