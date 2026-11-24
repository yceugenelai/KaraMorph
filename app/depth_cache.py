"""Cheap, conservative cache index; the worker's content hash remains authoritative."""
import hashlib
import json
import os
from pathlib import Path

from app.depth_settings import mask_options

INDEX_VERSION = 1


def _signature(image, model, settings):
    image = Path(image).resolve()
    model = Path(model).resolve()
    def stat(path):
        s = path.stat()
        return [s.st_size, s.st_mtime_ns]
    return {'version': INDEX_VERSION, 'image': str(image), 'image_stat': stat(image),
            'model': str(model), 'model_stats': {name: stat(model / name) for name in
                ('config.json', 'preprocessor_config.json', 'model.safetensors')},
            'options': mask_options(settings)}


def _index_path(cache, image, settings):
    key = json.dumps([str(Path(image).resolve()), mask_options(settings)], sort_keys=True).encode('utf-8')
    return Path(cache) / 'index' / (hashlib.sha256(key).hexdigest() + '.json')


def has_index(cache, image, settings):
    return _index_path(cache, image, settings).is_file()


def lookup(cache, image, model, settings):
    try:
        entry = json.loads(_index_path(cache, image, settings).read_text(encoding='utf-8'))
        result = entry['result']
        mask = Path(result['mask']).resolve()
        if (entry['signature'] == _signature(image, model, settings) and
                mask.parent == Path(cache).resolve() and mask.is_file()):
            return result
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def remember(cache, image, model, settings, result):
    path = _index_path(cache, image, settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    try:
        temp.write_text(json.dumps({'signature': _signature(image, model, settings), 'result': result}), encoding='utf-8')
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
