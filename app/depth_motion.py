"""Depth-mask selection and bounded motion for karaoke backgrounds."""
import math
import numpy as np


def select_mask(depth, minimum=0.02, maximum=0.70, person_expand=18, landscape_expand=4, solid=True):
    """Return a mask and metadata; depth is normalized with near=1.

    The selection is deliberately heuristic. It must not be used as a semantic
    person or landscape classifier outside this visual effect.
    """
    from scipy.ndimage import maximum_filter, uniform_filter
    depth = np.asarray(depth, dtype=np.float32)
    if depth.ndim != 2 or not depth.size or not np.isfinite(depth).all():
        raise ValueError('Expected a finite, nonempty depth image')
    if not 0 <= minimum <= maximum <= 1:
        raise ValueError('Invalid coverage bounds')
    depth = np.clip(depth, 0, 1)
    h, w = depth.shape
    middle = depth[int(h*.25):max(int(h*.75), 1)]
    if h < 4 or w < 10:
        raise ValueError('Depth image is too small')
    center = middle[:, int(w*.3):int(w*.7)].mean()
    edges = np.concatenate((middle[:, :int(w*.2)].ravel(), middle[:, int(w*.8):].ravel())).mean()
    landscape = bool(center - edges <= .10)
    expansion = round((landscape_expand if landscape else person_expand) * w / 1920)
    ground = depth * (1 - .9 * np.linspace(0, 1, h, dtype=np.float32)[:, None] ** 2)
    detrend = np.clip((depth - depth.mean(axis=1, keepdims=True) - .03) * 3.5, 0, 1)
    local = np.maximum(0, depth - uniform_filter(depth, size=65, mode='nearest'))
    threshold = max(.035, float(np.quantile(local, .75)))
    local = np.clip((local - threshold) / .08, 0, 1)
    methods = {'near': depth, 'ground': ground, 'detrend': detrend,
               'local': local, 'inverse': 1 - depth}
    order = (['ground', 'detrend', 'local', 'inverse'] if landscape else
             ['near', 'ground', 'detrend', 'local', 'inverse'])
    candidates = []
    for name in order:
        mask = methods[name] >= .5 if solid else methods[name].copy()
        if expansion:
            mask = maximum_filter(mask, size=2 * expansion + 1, mode='constant')
        coverage = float(np.count_nonzero(mask >= .1) / mask.size)
        candidates.append((name, mask, coverage))
        if minimum <= coverage <= maximum:
            break
    def distance(item):
        return max(minimum - item[2], 0, item[2] - maximum)
    name, mask, coverage = min(candidates, key=distance)
    return mask, {'method': name, 'landscape': landscape, 'coverage': coverage,
                  'in_range': minimum <= coverage <= maximum,
                  'motion_factor': .35 if landscape and name == 'inverse' else 1.0}


def strip_offset(elapsed_ms, y_fraction, strength=0.3, speed=0.4):
    """POC's slowly varying horizontal and vertical displacements at 1080p."""
    phase = elapsed_ms / 1000 * (.15 + speed * .5)
    x = (math.sin(phase * 2.2 + y_fraction * 5) * .7 +
         math.sin(phase * .61 - y_fraction * 3) * .3) * 32 * strength
    y = (math.sin(phase * 1.5 + y_fraction * 3) * .7 +
         math.sin(phase * .47 - y_fraction * 5) * .3) * 12 * strength
    return x, y
