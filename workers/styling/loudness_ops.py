"""Loudness (ITU-R BS.1770 LUFS) measurement and matching, so A/B listening isn't decided by level."""
from functools import lru_cache
from pathlib import Path

import numpy as np
import pyloudnorm
import soundfile as sf


def _integrated_lufs(data: np.ndarray, sample_rate: int) -> float:
    return float(pyloudnorm.Meter(sample_rate).integrated_loudness(data))


@lru_cache(maxsize=8)
def measure_lufs(path: str) -> float:
    data, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    return _integrated_lufs(data, sample_rate)


def match_loudness(target_path: Path, reference_path: Path) -> dict:
    """Attenuate target_path in place to the reference's integrated LUFS; never amplifies
    (ACE-Step output is already peak-normalized to -1 dBFS, so there is no headroom)."""
    reference = measure_lufs(str(reference_path))
    data, sample_rate = sf.read(str(target_path), dtype="float32", always_2d=True)
    before = _integrated_lufs(data, sample_rate)
    gain_db = min(0.0, reference - before) if np.isfinite(reference) and np.isfinite(before) else 0.0
    if gain_db < 0:
        subtype = sf.info(str(target_path)).subtype
        sf.write(str(target_path), data * np.float32(10 ** (gain_db / 20)), sample_rate, subtype=subtype)
    return {"lufs_reference": round(reference, 2), "lufs_before": round(before, 2), "gain_db": round(gain_db, 2)}
