"""Generate one cached depth-motion mask in a separate model process."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from runtime_config import worker_env
from app.depth_settings import DEFAULTS, mask_cache_suffix, mask_options
os.environ.update(worker_env())


def digest_file(path, digest):
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(block)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--minimum', type=int, default=2)
    parser.add_argument('--maximum', type=int, default=70)
    parser.add_argument('--person-expand', type=int, default=DEFAULTS['person_expand'])
    parser.add_argument('--landscape-expand', type=int, default=DEFAULTS['landscape_expand'])
    parser.add_argument('--soft-mask', action='store_true')
    args = parser.parse_args()
    args.image, args.model, args.cache = args.image.resolve(), args.model.resolve(), args.cache.resolve()
    from app.depth_cache import remember
    settings = {'depth_minimum': args.minimum, 'depth_maximum': args.maximum,
                'depth_person_expand': args.person_expand, 'depth_landscape_expand': args.landscape_expand,
                'depth_solid': not args.soft_mask}
    options = mask_options(settings)
    image_stat = args.image.stat()
    required = ('config.json', 'preprocessor_config.json', 'model.safetensors')
    model_stats = [(args.model / name).stat() for name in required]
    def sources_unchanged():
        current = args.image.stat()
        if (current.st_size, current.st_mtime_ns) != (image_stat.st_size, image_stat.st_mtime_ns):
            return False
        return all((now.st_size, now.st_mtime_ns) == (before.st_size, before.st_mtime_ns)
                   for before, now in zip(model_stats, ((args.model / name).stat() for name in required)))
    digest = hashlib.sha256(b'karamorph-depth-motion-v1')
    digest_file(args.image, digest)
    for filename in required:
        digest_file(args.model / filename, digest)
    digest.update(mask_cache_suffix(options))
    key = digest.hexdigest()
    args.cache.mkdir(parents=True, exist_ok=True)
    result_file = args.cache / f'{key}.json'
    mask_file = args.cache / f'{key}.png'
    if result_file.is_file() and mask_file.is_file():
        metadata = json.loads(result_file.read_text(encoding='utf-8'))
        if metadata.get('mask') == str(mask_file):
            if sources_unchanged():
                remember(args.cache, args.image, args.model, settings, metadata)
            print(json.dumps(metadata), flush=True)
            return

    import numpy as np
    import torch
    from PIL import Image, ImageOps
    from transformers import AutoImageProcessor, AutoModelForDepthEstimation
    from app.depth_motion import select_mask

    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    image = ImageOps.exif_transpose(Image.open(args.image)).convert('RGB')
    processor = AutoImageProcessor.from_pretrained(args.model, local_files_only=True)
    model = AutoModelForDepthEstimation.from_pretrained(args.model, local_files_only=True).to(device).eval()
    inputs = {key: value.to(device) for key, value in processor(images=image, return_tensors='pt').items()}
    with torch.inference_mode():
        predicted = model(**inputs).predicted_depth
        predicted = torch.nn.functional.interpolate(predicted.unsqueeze(1), size=(270, 480),
                                                     mode='bicubic', align_corners=False).squeeze()
    depth = predicted.cpu().numpy()
    depth = (depth - depth.min()) / max(float(np.ptp(depth)), 1e-8)
    mask, info = select_mask(depth, minimum=options['minimum'] / 100, maximum=options['maximum'] / 100,
                             person_expand=options['person_expand'], landscape_expand=options['landscape_expand'],
                             solid=options['solid'])
    tmp_mask = args.cache / f'{key}.{os.getpid()}.tmp.png'
    tmp_result = args.cache / f'{key}.{os.getpid()}.tmp.json'
    try:
        Image.fromarray((mask * 255).astype(np.uint8)).save(tmp_mask)
        tmp_mask.replace(mask_file)
        metadata = {'mask': str(mask_file), **info}
        tmp_result.write_text(json.dumps(metadata), encoding='utf-8')
        tmp_result.replace(result_file)
        if sources_unchanged():
            remember(args.cache, args.image, args.model, settings, metadata)
    finally:
        tmp_mask.unlink(missing_ok=True)
        tmp_result.unlink(missing_ok=True)
    print(json.dumps(metadata), flush=True)


if __name__ == '__main__':
    main()
