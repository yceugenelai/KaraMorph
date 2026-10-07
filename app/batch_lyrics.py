"""Conservative LRCLIB matching and one cancellable asynchronous lookup."""
import json
import math
import time
import unicodedata
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from app.i18n import t
from PySide6.QtCore import QObject, QTimer, QUrl, QUrlQuery, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest

from app.lyrics import assets_dir, load_selected, load_lyric_state, parse_lrc, save_lyric_state


def has_lrclib(workspace, song_id):
    folder = assets_dir(workspace, song_id)
    for name in ('lrclib.lrc', 'lrclib.txt'):
        path = folder / name
        try:
            text = path.read_text(encoding='utf-8-sig') if path.is_file() else ''
        except (OSError, UnicodeError):
            continue
        if text.strip():
            if name.endswith('.lrc'):
                try:
                    parse_lrc(text)
                except ValueError:
                    continue
            return True
    return False


def normalize(value):
    return ' '.join(unicodedata.normalize('NFKC', str(value or '')).casefold().split())


def choose_candidate(rows, song):
    if not song.get('artist') or not song.get('duration_sec'):
        return None, t('需確認：缺少歌手或原曲時長')
    matches = []
    for row in rows:
        try:
            duration = float(row.get('duration'))
        except (TypeError, ValueError):
            continue
        if (normalize(row.get('trackName')) == normalize(song['title']) and
                normalize(row.get('artistName')) == normalize(song['artist']) and
                math.isfinite(duration) and abs(duration - song['duration_sec']) <= 2):
            matches.append(row)
    matches = list({row['id']: row for row in matches}.values())
    if len(matches) != 1:
        return None, t('需確認：沒有唯一的歌名／歌手／時長匹配')
    row = matches[0]
    if row.get('instrumental') or not (row.get('syncedLyrics') or row.get('plainLyrics')):
        return None, t('查無：純音樂或沒有歌詞')
    return row, ''


def store_candidate(workspace, song_id, row, query):
    folder = assets_dir(workspace, song_id)
    state = load_lyric_state(workspace, song_id)
    text, _, _, _ = load_selected(workspace, song_id)
    synced = row.get('syncedLyrics') or ''
    plain = row.get('plainLyrics') or ''
    if not isinstance(synced, str) or not isinstance(plain, str):
        raise ValueError(t('伺服器歌詞內容不是文字'))
    if synced:
        try:
            parse_lrc(synced)
        except ValueError:
            synced = ''
    if not synced and not plain.strip():
        raise ValueError(t('下載的歌詞格式無法使用，請手動確認'))
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'lrclib.lrc').write_text(synced, encoding='utf-8')
    (folder / 'lrclib.txt').write_text(plain, encoding='utf-8')
    state['lrclib'] = {k: v for k, v in row.items() if k not in {'syncedLyrics', 'plainLyrics'}}
    state['query'] = query
    if not text.strip():
        state['source'] = 'lrclib' if synced else 'downloaded'
    save_lyric_state(workspace, song_id, state)


class LyricsLookup(QObject):
    done = Signal(str, str)
    status = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.network = QNetworkAccessManager(self)
        self.reply = None
        self.next_allowed = 0
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.timeout.connect(self._timeout)
        self.retry = QTimer(self)
        self.retry.setSingleShot(True)
        self.retry.timeout.connect(self._send)

    def start(self, song, workspace):
        self.cancel()
        self.song, self.workspace = dict(song), Path(workspace)
        self.attempt = 0
        self.endpoint = 'get'
        self.params = {'track_name': song['title'], 'artist_name': song.get('artist', '')}
        if song.get('duration_sec'):
            self.params['duration'] = str(song['duration_sec'])
        if not song.get('artist'):
            self._finish('review', t('需確認：缺少歌手，請從素材搜尋'))
            return
        self._send()

    def cancel(self):
        self.timeout.stop()
        self.retry.stop()
        reply, self.reply = self.reply, None
        if reply:
            reply.abort()
            reply.deleteLater()

    def _finish(self, outcome, detail):
        self.cancel()
        self.done.emit(outcome, detail)

    def _timeout(self):
        self._finish('failed', t('LRCLIB 查詢逾時'))

    def _send(self):
        wait = math.ceil(self.next_allowed - time.monotonic())
        if wait > 0:
            self.status.emit(t('服務要求等待，{p0} 秒後查詢；可取消', p0=wait))
            self.retry.start(min(wait * 1000, 2_000_000_000))
            return
        url = QUrl('https://lrclib.net/api/' + self.endpoint)
        query = QUrlQuery()
        for key, value in self.params.items():
            query.addQueryItem(key, str(value))
        url.setQuery(query)
        request = QNetworkRequest(url)
        request.setRawHeader(b'User-Agent', b'KaraMorph/0.1.0-preview')
        self.reply = self.network.get(request)
        self.reply.finished.connect(self._received)
        self.timeout.start(15000)
        self.status.emit(t('正在查詢 LRCLIB'))

    def _received(self):
        reply = self.sender()
        if reply is not self.reply:
            return
        self.reply = None
        self.timeout.stop()
        try:
            self._process_reply(reply)
        except Exception as error:
            # Exceptions in Qt callbacks do not reach the batch scheduler.
            # Always finish this item so subsequent songs can continue.
            self._finish('failed', str(error))
        finally:
            reply.deleteLater()

    def _process_reply(self, reply):
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        raw = bytes(reply.readAll())
        retry_after = bytes(reply.rawHeader('Retry-After')).decode('ascii', errors='replace')
        error = reply.errorString()
        if status == 404 and self.endpoint == 'get':
            self.endpoint = 'search'
            self.params.pop('duration', None)
            self.retry.start(300)
            return
        if status in {429, 503}:
            try:
                wait = max(1, int(retry_after))
            except ValueError:
                try:
                    wait = max(1, math.ceil((parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds()))
                except (ValueError, TypeError, OverflowError):
                    wait = 60
            self.next_allowed = time.monotonic() + wait
            if self.attempt < 1:
                self.attempt += 1
                self.status.emit(t('服務忙碌，{p0} 秒後重試；可取消', p0=wait))
                self.retry.start(min(wait * 1000, 2_000_000_000))
                return
        if status != 200:
            self._finish('not_found' if status == 404 else 'failed',
                         t('查無歌詞') if status == 404 else f'HTTP {status or "—"}：{error}')
            return
        try:
            payload = json.loads(raw)
            rows = payload if isinstance(payload, list) else [payload]
            if any(not isinstance(row, dict) or 'id' not in row for row in rows):
                raise ValueError(t('伺服器回傳格式不符'))
            if not rows:
                self._finish('not_found', t('查無歌詞'))
                return
            row, reason = choose_candidate(rows, self.song)
            if row is None:
                self._finish('not_found' if reason.startswith(t('查無')) else 'review', reason)
                return
            store_candidate(self.workspace, self.song['id'], row, dict(self.params))
            self._finish('success', t('已下載 LRCLIB 歌詞'))
        except (OSError, ValueError, TypeError) as error:
            self._finish('failed', str(error))
