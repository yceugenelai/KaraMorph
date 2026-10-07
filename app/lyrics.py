"""Local lyric sources and original-timeline LRC lookup; no playback networking."""

from app.i18n import t
from bisect import bisect_right
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re

from app.storage import atomic_json, song_dir

STAMP = re.compile(r'\[(\d+):([0-5]\d)(?:[.:](\d{1,3}))?\]')
META = re.compile(r'^\[(?!offset:)[a-z][a-z0-9_-]*:.*\]$', re.I)


@dataclass
class Timeline:
    times: list[int]
    lines: list[str]

    def index_at(self, position_ms: int, speed: float = 1.0, delay_ms: int = 0) -> int:
        return bisect_right(self.times, (position_ms - delay_ms) * speed) - 1


def parse_lrc(text: str) -> Timeline:
    entries, errors, offset = [], [], 0
    for number, raw in enumerate(text.lstrip('\ufeff').splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        match = re.fullmatch(r'\[offset:([+-]?\d+)\]', line, re.I)
        if match:
            offset = int(match[1])
            continue
        if META.fullmatch(line):
            continue
        stamps = list(STAMP.finditer(line))
        # Only consecutive leading timestamps are accepted; never render bad tags.
        prefix = ''.join(m.group() for m in stamps)
        if not stamps or not line.startswith(prefix):
            errors.append(str(number))
            continue
        lyric = line[len(prefix):].strip()
        if (lyric.startswith('[') and re.match(r'\[\d', lyric)) or re.search(r'<\d+:\d', lyric):
            errors.append(str(number))
            continue
        for stamp in stamps:
            minute, second, fraction = stamp.groups()
            ms = (int(minute) * 60 + int(second)) * 1000 + int((fraction or '').ljust(3, '0'))
            entries.append((ms, lyric))
    if errors:
        raise ValueError(t('LRC 格式無法解析，請檢查第 ') + '、'.join(errors[:8]) + t(' 行'))
    if not entries:
        raise ValueError(t('沒有有效的 LRC 逐行時間戳'))
    # Positive LRC offset advances lyrics; user delay is separately in playback ms.
    entries.sort(key=lambda entry: entry[0])
    return Timeline([ms - offset for ms, _ in entries], [line for _, line in entries])


def plain_from_lrc(text: str) -> str:
    lines = []
    for raw in text.splitlines():
        if META.fullmatch(raw.strip()) or re.fullmatch(r'\[offset:.*\]', raw.strip(), re.I):
            continue
        lines.append(re.sub(r'\[[^\]]*\]', '', raw).strip())
    return '\n'.join(lines)


def assets_dir(workspace: Path, song_id: str) -> Path:
    return song_dir(workspace, song_id) / 'assets'


def load_lyric_state(workspace: Path, song_id: str) -> dict:
    path = assets_dir(workspace, song_id) / 'lyric_sources.json'
    state = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {'source': 'manual', 'offsets': {}}
    if not isinstance(state, dict) or state.get('source', 'manual') not in {'manual', 'downloaded', 'lrclib', 'local'}:
        raise ValueError(t('歌詞來源設定無效'))
    offsets = state.get('offsets', {})
    if not isinstance(offsets, dict) or any(not isinstance(value, int) for value in offsets.values()):
        raise ValueError(t('歌詞時間偏移設定無效'))
    return state


def save_lyric_state(workspace: Path, song_id: str, state: dict):
    atomic_json(assets_dir(workspace, song_id) / 'lyric_sources.json', state)


def save_delay(workspace: Path, song_id: str, variant_id: str, delay_ms: int):
    state = load_lyric_state(workspace, song_id)
    state.setdefault('offsets', {})[variant_id] = delay_ms
    save_lyric_state(workspace, song_id, state)


def load_selected(workspace: Path, song_id: str):
    """Return plain text, timeline (if valid), state and a visible fallback reason."""
    folder = assets_dir(workspace, song_id)
    state = load_lyric_state(workspace, song_id)
    source = state.get('source', 'manual')
    name = {'manual': 'lyrics.txt', 'downloaded': 'lrclib.txt',
            'lrclib': 'lrclib.lrc', 'local': 'local.lrc'}.get(source, 'lyrics.txt')
    path = folder / name
    text = path.read_text(encoding='utf-8-sig') if path.is_file() else ''
    if source in {'lrclib', 'local'}:
        try:
            return plain_from_lrc(text), parse_lrc(text), state, ''
        except ValueError as error:
            return plain_from_lrc(text), None, state, t('{p0}；暫用純文字估算字幕。', p0=error)
    return text, None, state, ''


def effective_speed(variant_id: str, variants: list[dict]) -> float:
    by_id = {row['id']: row for row in variants}
    speed, seen = 1.0, set()
    current = variant_id
    while current not in {None, 'original', 'instrumental'}:
        if current in seen:
            raise ValueError(t('版本來源鏈有循環，無法換算歌詞時間'))
        seen.add(current)
        row = by_id.get(current)
        if row is None:
            raise ValueError(t('版本來源缺失，無法換算歌詞時間'))
        if row.get('kind') == 'fx':
            try:
                value = float(row.get('speed', 1))
            except (TypeError, ValueError):
                raise ValueError(t('版本速度無效，無法換算歌詞時間')) from None
            if not math.isfinite(value) or value <= 0:
                raise ValueError(t('版本速度無效，無法換算歌詞時間'))
            speed *= value
        current = row.get('source_id')
    if not math.isfinite(speed) or speed <= 0:
        raise ValueError(t('累積版本速度無效'))
    return speed


def lyric_status(workspace: Path, song_id: str) -> tuple[str, str]:
    """Describe the selected playback source, without any network requests."""
    try:
        text, timeline, state, warning = load_selected(workspace, song_id)
    except (OSError, ValueError) as error:
        return t('⚠ 無法讀取'), str(error)
    source = state.get('source', 'manual')
    name = {'manual': t('人工'), 'downloaded': 'LRCLIB', 'lrclib': 'LRCLIB', 'local': t('本機 LRC')}[source]
    if not text.strip():
        return t('○ 無歌詞'), warning or t('目前選用「{p0}」，尚無歌詞內容。可按素材新增或切換來源。', p0=name)
    mode = t('同步') if timeline else t('純文字')
    label = f'{name} · {mode}'
    if warning:
        label = '⚠ ' + label
    return label, warning or t('目前唱歌使用：{p0}。同首歌曲各版本共用歌詞來源，可在素材切換。', p0=label)
