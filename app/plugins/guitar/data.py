"""Stable identity for the exact audio analyzed by the guitar plugin."""
import hashlib
from pathlib import Path

def source_signature(path):
    path = Path(path).resolve()
    before = path.stat()
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024),b''):
            digest.update(chunk)
    after = path.stat()
    if (before.st_size,before.st_mtime_ns) != (after.st_size,after.st_mtime_ns):
        raise ValueError('Audio changed while computing its signature')
    return {'size':after.st_size,'sha256':digest.hexdigest()}
