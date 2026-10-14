"""Pinned, dynamically loaded SoundTouch; no network access during playback."""
import ctypes as ct
import hashlib
import io
from pathlib import Path
import urllib.request
import zipfile
from runtime_config import ROOT

VERSION = '2.3.3'
URL = 'https://www.surina.net/soundtouch/soundtouch_dll-2.3.3.zip'
ARCHIVE_SHA256 = '9f8e4beab9e4b36e4fed8ab5e86c9d2aaa0c457702058f15581670ee2d6ad8e3'
DLL_SHA256 = '33caafa82b5e89e66d2ca4f9e2137aae1a1e2693f119c62236295878c61406ea'


def library_path(root=ROOT):
    return Path(root) / '.tools/soundtouch' / VERSION / 'SoundTouchDLL_x64.dll'


def ready(root=ROOT):
    path = library_path(root)
    return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == DLL_SHA256


def prepare(root=ROOT):
    folder = library_path(root).parent
    folder.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(URL, timeout=30) as response:
        data = response.read(2_000_001)
    if hashlib.sha256(data).hexdigest() != ARCHIVE_SHA256:
        raise ValueError('SoundTouch archive checksum mismatch')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for name in ('SoundTouchDLL_x64.dll', 'COPYING.TXT', 'SoundTouchDLL.h'):
            destination = folder / name
            temporary = destination.with_suffix(destination.suffix + '.part')
            temporary.write_bytes(archive.read(name))
            temporary.replace(destination)
    return library_path(root)


class SoundTouch:
    def __init__(self, rate, channels, path=None):
        import numpy as np
        path = Path(path) if path else library_path()
        if not path.is_file():
            raise RuntimeError('SoundTouch is not prepared. Run scripts/prepare_soundtouch.py or run installation setup.')
        if hashlib.sha256(path.read_bytes()).hexdigest() != DLL_SHA256:
            raise RuntimeError('SoundTouch DLL checksum mismatch; run scripts/prepare_soundtouch.py to repair it')
        self.lib = ct.CDLL(str(path))
        pointer = ct.POINTER(ct.c_float)
        signatures = {
            'createInstance': ([], ct.c_void_p), 'destroyInstance': ([ct.c_void_p], None),
            'setChannels': ([ct.c_void_p, ct.c_uint], ct.c_int),
            'setSampleRate': ([ct.c_void_p, ct.c_uint], ct.c_int),
            'setTempo': ([ct.c_void_p, ct.c_float], None),
            'setPitchSemiTones': ([ct.c_void_p, ct.c_float], None),
            'putSamples': ([ct.c_void_p, pointer, ct.c_uint], ct.c_int),
            'receiveSamples': ([ct.c_void_p, pointer, ct.c_uint], ct.c_uint),
            'flush': ([ct.c_void_p], ct.c_int),
            'setSetting': ([ct.c_void_p, ct.c_int, ct.c_int], ct.c_int),
        }
        for suffix, (args, result) in signatures.items():
            function = getattr(self.lib, 'soundtouch_' + suffix)
            function.argtypes, function.restype = args, result
        self.handle = self.lib.soundtouch_createInstance()
        if not self.handle:
            raise RuntimeError('SoundTouch instance creation failed')
        self.channels = channels
        self.buffer = np.empty((4096, channels), np.float32)
        try:
            if not self.lib.soundtouch_setChannels(self.handle, channels):
                raise RuntimeError('SoundTouch rejected the channel count')
            if not self.lib.soundtouch_setSampleRate(self.handle, rate):
                raise RuntimeError('SoundTouch rejected the sample rate')
            self.lib.soundtouch_setSetting(self.handle, 0, 1)
            self.lib.soundtouch_setSetting(self.handle, 2, 0)
        except Exception:
            self.close()
            raise

    def configure(self, tempo, key):
        if not .7 <= tempo <= 1.3 or not -6 <= key <= 6:
            raise ValueError('Tempo/key outside supported range')
        self.lib.soundtouch_setTempo(self.handle, tempo)
        self.lib.soundtouch_setPitchSemiTones(self.handle, key)

    def put(self, samples):
        import numpy as np
        samples = np.ascontiguousarray(samples, dtype=np.float32)
        if samples.ndim != 2 or samples.shape[1] != self.channels:
            raise ValueError('Invalid SoundTouch PCM shape')
        if not self.lib.soundtouch_putSamples(self.handle,
                samples.ctypes.data_as(ct.POINTER(ct.c_float)), len(samples)):
            raise RuntimeError('SoundTouch processing failed')

    def receive(self):
        count = self.lib.soundtouch_receiveSamples(self.handle,
            self.buffer.ctypes.data_as(ct.POINTER(ct.c_float)), len(self.buffer))
        return self.buffer[:count].copy()

    def flush(self):
        if not self.lib.soundtouch_flush(self.handle):
            raise RuntimeError('SoundTouch flush failed')

    def close(self):
        if self.handle:
            self.lib.soundtouch_destroyInstance(self.handle)
            self.handle = None
