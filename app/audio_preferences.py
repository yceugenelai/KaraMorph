"""Saved singing preferences, independent of transient playback state."""

DEFAULTS = dict(guide_enabled=False, monitor_enabled=True,
                music_volume=80, guide_volume=12, monitor_volume=25)


def load_preferences(settings):
    saved = settings.get('singing_preferences', {})
    if not isinstance(saved, dict):
        saved = {}
    result = dict(DEFAULTS)
    for key, default in DEFAULTS.items():
        value = saved.get(key, default)
        if isinstance(default, bool):
            result[key] = value if isinstance(value, bool) else default
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                result[key] = min(100, max(0, round(value)))
            except (ValueError, OverflowError):
                pass
    return result
