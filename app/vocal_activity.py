"""Vocal activity analysis worker. No Qt or playback imports in this process."""
import argparse
import hashlib
import json
from pathlib import Path
import uuid

import numpy as np
import soundfile as sf

VERSION = 1
WINDOW = 0.3


def fingerprint(path):
    path = Path(path).resolve()
    stat = path.stat()
    return {'path': str(path), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
            'version': VERSION, 'window': WINDOW}


def cache_path(path, folder):
    identity = json.dumps(fingerprint(path), sort_keys=True).encode('utf-8')
    return Path(folder) / (hashlib.sha256(identity).hexdigest() + '.json')


def analyze(path, window_seconds=WINDOW):
    energies = []
    with sf.SoundFile(str(path)) as audio:
        frames = max(1, round(audio.samplerate * window_seconds))
        duration = len(audio) / audio.samplerate
        while True:
            block = audio.read(frames, dtype='float32', always_2d=True)
            if not len(block):
                break
            energies.append(float(np.sqrt(np.mean(np.square(block, dtype=np.float64)))))
    if not energies or not np.isfinite(energies).all():
        raise ValueError('人聲音訊為空或包含無效值')
    levels = np.asarray(energies)
    active = levels >= max(0.008, float(np.percentile(levels, 90)) * 0.13)
    # Fill internal gaps up to 0.6 seconds; keep intro/outro and longer interludes still.
    index = 0
    while index < len(active):
        if active[index]:
            index += 1
            continue
        first = index
        while index < len(active) and not active[index]:
            index += 1
        if first > 0 and index < len(active) and (index-first)*window_seconds <= 0.6 + 1e-8:
            active[first:index] = True
    edges = np.minimum(np.arange(len(active)+1)*window_seconds, duration)
    voiced = np.concatenate(([0.0], np.cumsum(np.diff(edges)*active)))
    if voiced[-1] < 0.5:
        raise ValueError('未偵測到足夠的人聲，改用歌曲時間捲動')
    return edges, voiced


def read_cache(path, target):
    try:
        data = json.loads(Path(target).read_text(encoding='utf-8'))
        if data['source'] != fingerprint(path):
            return None
        edges, voiced = np.asarray(data['edges'], dtype=float), np.asarray(data['voiced'], dtype=float)
        if (edges.ndim != 1 or voiced.shape != edges.shape or len(edges) < 2
                or not np.isfinite(edges).all() or not np.isfinite(voiced).all()
                or edges[0] != 0 or voiced[0] != 0 or voiced[-1] <= 0
                or np.any(np.diff(edges) <= 0) or np.any(np.diff(voiced) < 0)
                or np.any(np.diff(voiced) > np.diff(edges) + 1e-6)):
            return None
        return edges, voiced
    except (OSError, ValueError, KeyError, TypeError):
        return None


def build_cache(path, target):
    if read_cache(path, target) is not None:
        return
    source = fingerprint(path)
    edges, voiced = analyze(path)
    if source != fingerprint(path):
        raise ValueError('人聲音檔於分析時變更，請重試')
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temp.write_text(json.dumps({'source': source, 'edges': edges.tolist(),
                                   'voiced': voiced.tolist()}), encoding='utf-8')
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)


if __name__ == '__main__':
    # Lower worker priority on Windows; playback remains in the parent process.
    import os
    if os.name == 'nt':
        import ctypes
        kernel = ctypes.windll.kernel32
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        kernel.SetPriorityClass.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
        kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x4000)
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True)
    parser.add_argument('--cache', required=True)
    args = parser.parse_args()
    build_cache(Path(args.input), Path(args.cache))
