import unittest
import tempfile
from pathlib import Path

import numpy as np

from app.depth_motion import select_mask, strip_offset
from app.depth_cache import lookup, remember
from app.depth_settings import LEGACY_MASK_OPTIONS, depth_settings, mask_cache_suffix, mask_options


class DepthMotionTests(unittest.TestCase):
    def test_centered_subject_and_landscape_choose_different_masks(self):
        subject = np.full((270, 480), .1, dtype=np.float32)
        subject[70:200, 150:330] = .9
        mask, details = select_mask(subject)
        self.assertFalse(details['landscape'])
        self.assertEqual(details['method'], 'near')
        self.assertTrue(mask[100, 200])
        self.assertFalse(mask[100, 30])
        landscape = np.broadcast_to(np.linspace(0, 1, 270, dtype=np.float32)[:, None], (270, 480))
        mask, details = select_mask(landscape)
        self.assertTrue(details['landscape'])
        self.assertNotEqual(details['method'], 'near')
        self.assertEqual(mask.dtype, np.bool_)

    def test_coverage_fallback_and_invalid_data(self):
        mask, info = select_mask(np.zeros((40, 40), dtype=np.float32))
        self.assertFalse(info['in_range'])
        self.assertFalse(mask.any())
        with self.assertRaises(ValueError):
            select_mask(np.full((10, 10), np.nan))
        with self.assertRaises(ValueError):
            select_mask(np.zeros((10, 10)), minimum=.8, maximum=.2)

    def test_motion_is_bounded_and_strength_scales(self):
        for time in range(0, 120000, 197):
            x, y = strip_offset(time, .5)
            self.assertLessEqual(abs(x), 32 * .4)
            self.assertLessEqual(abs(y), 12 * .4)
        self.assertEqual(strip_offset(1000, .5, strength=0), (0, 0))

    def test_soft_mask_and_expansion_are_configurable(self):
        depth = np.full((270, 480), .1, dtype=np.float32)
        depth[80:190, 150:330] = .7
        solid, _ = select_mask(depth, person_expand=0)
        expanded, _ = select_mask(depth, person_expand=20)
        soft, _ = select_mask(depth, person_expand=0, solid=False)
        self.assertGreater(expanded.sum(), solid.sum())
        self.assertTrue(((soft > 0) & (soft < 1)).any())

    def test_index_invalidates_changed_image_model_or_mask_options(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model, cache = root / 'model', root / 'cache'
            model.mkdir()
            cache.mkdir()
            for name in ('config.json', 'preprocessor_config.json', 'model.safetensors'):
                (model / name).write_bytes(name.encode())
            image, mask = root / 'photo.png', cache / 'mask.png'
            image.write_bytes(b'image')
            mask.write_bytes(b'mask')
            result = {'mask': str(mask)}
            remember(cache, image, model, {}, result)
            self.assertEqual(lookup(cache, image, model, {}), result)
            self.assertEqual(lookup(cache, image, model, {'depth_strength': 20}), result)
            self.assertIsNone(lookup(cache, image, model, {'depth_person_expand': 20}))
            image.write_bytes(b'changed image')
            self.assertIsNone(lookup(cache, image, model, {}))
            remember(cache, image, model, {}, result)
            (model / 'config.json').write_bytes(b'changed model')
            self.assertIsNone(lookup(cache, image, model, {}))

    def test_invalid_settings_use_safe_defaults(self):
        self.assertEqual(depth_settings({'depth_minimum': 90, 'depth_maximum': 2})['maximum'], 70)
        self.assertEqual(depth_settings({'depth_strength': 'bad'})['strength'], 30)

    def test_new_defaults_preserve_historical_cache_identity(self):
        options = depth_settings({})
        self.assertEqual((options['strength'], options['speed']), (30, 40))
        self.assertEqual((options['person_expand'], options['landscape_expand']), (18, 4))
        self.assertEqual(mask_cache_suffix(LEGACY_MASK_OPTIONS), b'')
        self.assertNotEqual(mask_cache_suffix(mask_options({})), b'')


if __name__ == '__main__':
    unittest.main()
