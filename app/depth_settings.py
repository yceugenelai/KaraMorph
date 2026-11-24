"""Validated settings shared by depth preprocessing, cache and playback."""
import json

DEFAULTS = {'strength': 30, 'speed': 40, 'minimum': 2, 'maximum': 70,
            'person_expand': 18, 'landscape_expand': 4, 'solid': True}

# The original v1 worker omitted these exact options from its content hash.
# Keep that historical key stable even if the application's defaults change.
LEGACY_MASK_OPTIONS = {'minimum': 2, 'maximum': 70, 'person_expand': 16,
                       'landscape_expand': 0, 'solid': True}


def depth_settings(settings):
    result = dict(DEFAULTS)
    for key, default in DEFAULTS.items():
        value = settings.get('depth_' + key, default)
        if key == 'solid':
            result[key] = value if isinstance(value, bool) else default
        else:
            try:
                result[key] = max(0, min(80 if 'expand' in key else 100, int(value)))
            except (ValueError, TypeError, OverflowError):
                result[key] = default
    if result['minimum'] > result['maximum']:
        result['minimum'], result['maximum'] = DEFAULTS['minimum'], DEFAULTS['maximum']
    return result


def mask_options(settings):
    options = depth_settings(settings)
    return {key: options[key] for key in ('minimum', 'maximum', 'person_expand', 'landscape_expand', 'solid')}


def mask_cache_suffix(options):
    return b'' if options == LEGACY_MASK_OPTIONS else json.dumps(options, sort_keys=True).encode('utf-8')
