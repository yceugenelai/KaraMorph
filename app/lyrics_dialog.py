"""Asset editor with asynchronous, cancellable LRCLIB lookup."""

from app.i18n import t
import json
from pathlib import Path

from app.library_media import read_media
from PySide6.QtCore import QTimer, QUrl, QUrlQuery
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox,
    QPlainTextEdit, QPushButton, QTabWidget, QVBoxLayout, QWidget)

from app.karaoke_assets import read_lyrics, save_lyrics, shared_images_dir, song_images_dir
from app.lyrics import assets_dir, load_lyric_state, parse_lrc, save_lyric_state
from app.image_assets_widget import ImageAssetsWidget


class LyricsDialog(QDialog):
    def __init__(self, controller, song_id):
        super().__init__(controller)
        self.controller, self.song_id = controller, song_id
        self.workspace = controller.workspace
        song = controller.song_map.get(song_id, {})
        self.setWindowTitle(t('素材 · {p0}', p0=song.get('title', t('歌曲'))))
        self.resize(780, 720)
        self.network = QNetworkAccessManager(self)
        self.reply = None
        self.candidates = []
        self.chosen = None
        self.query_used = {}
        self.attempt = 0
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.timeout.connect(self._timed_out)
        self.retry = QTimer(self)
        self.retry.setSingleShot(True)
        self.retry.timeout.connect(self._send)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.lyrics_page = QWidget()
        lyrics_layout = QVBoxLayout(self.lyrics_page)
        lyrics_layout.setContentsMargins(0, 0, 0, 0)
        self.tabs.addTab(self.lyrics_page, t('歌詞'))
        self.source = QComboBox()
        for title, key in [(t('人工純文字'), 'manual'), (t('LRCLIB 同步'), 'lrclib'),
                           (t('下載純文字'), 'downloaded'), (t('本機 LRC'), 'local')]:
            self.source.addItem(title, key)
        lyrics_layout.addWidget(QLabel(t('播放歌詞來源（同一首歌的各版本共用；下載不覆蓋人工內容）')))
        lyrics_layout.addWidget(self.source)
        self.lyric_tabs = QTabWidget()
        lyrics_layout.addWidget(self.lyric_tabs, 1)
        self.manual = QPlainTextEdit()
        self.local = QPlainTextEdit()
        self.local.setPlaceholderText(t('貼上原曲時間軸的 LRC，或按「匯入 .lrc」'))
        self.lyric_tabs.addTab(self.manual, t('人工歌詞'))
        local_page = QWidget()
        local_layout = QVBoxLayout(local_page)
        local_layout.addWidget(QLabel(t('LRC 以原曲時間軸解讀；已變速的 LRC 請先換算回原曲時間。')))
        import_button = QPushButton(t('匯入 .lrc'))
        import_button.clicked.connect(self.import_lrc)
        local_layout.addWidget(import_button)
        local_layout.addWidget(self.local, 1)
        self.lyric_tabs.addTab(local_page, t('本機 LRC'))
        search_page = QWidget()
        search_layout = QVBoxLayout(search_page)
        form = QFormLayout()
        self.title_edit = QLineEdit(song.get('title', ''))
        self.artist_edit = QLineEdit(song.get('artist', ''))
        self.album_edit = QLineEdit(song.get('album', ''))
        self.duration_edit = QLineEdit()
        try:
            audio, _ = read_media(Path(song.get('path', '')))
            if audio.get('duration_sec'):
                self.duration_edit.setText(f"{audio['duration_sec']:.2f}")
        except Exception:
            pass
        for label, field in [(t('原曲歌名'), self.title_edit), (t('歌手'), self.artist_edit),
                             (t('專輯'), self.album_edit), (t('原曲秒數（可留白）'), self.duration_edit)]:
            form.addRow(label, field)
        search_layout.addLayout(form)
        row = QHBoxLayout()
        self.search_button = QPushButton(t('從 LRCLIB 搜尋'))
        self.cancel_button = QPushButton(t('取消搜尋'))
        self.cancel_button.setEnabled(False)
        self.search_button.clicked.connect(self.search)
        self.cancel_button.clicked.connect(self.cancel_search)
        row.addWidget(self.search_button)
        row.addWidget(self.cancel_button)
        search_layout.addLayout(row)
        self.status = QLabel(t('搜尋只傳歌曲資訊；套用後保存於本機，播放不需連線。'))
        self.status.setWordWrap(True)
        search_layout.addWidget(self.status)
        self.results = QListWidget()
        self.results.currentRowChanged.connect(self.preview)
        search_layout.addWidget(self.results, 1)
        self.preview_text = QPlainTextEdit()
        self.preview_text.setReadOnly(True)
        search_layout.addWidget(self.preview_text, 1)
        self.apply_button = QPushButton(t('套用選取歌詞（儲存後生效）'))
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self.apply_candidate)
        search_layout.addWidget(self.apply_button)
        self.lyric_tabs.addTab(search_page, 'LRCLIB')
        self.images = ImageAssetsWidget(self.workspace, song_id, song,
                                        getattr(controller, 'image_search_registry', None), self,
                                        settings=controller.settings,
                                        persist=getattr(controller, 'persist_image_search_preferences', None))
        self.images.saveRequested.connect(self.save)
        self.shared_images = ImageAssetsWidget(self.workspace, song_id, song,
                                        getattr(controller, 'image_search_registry', None), self.images,
                                        settings=controller.settings,
                                        persist=getattr(controller, 'persist_image_search_preferences', None),
                                        shared=True)
        self.shared_images.saveRequested.connect(self.save)
        self.images.returnRequested.connect(lambda: self.tabs.setCurrentWidget(self.images))
        self.shared_images.returnRequested.connect(lambda: self.tabs.setCurrentWidget(self.images))
        # Keep each editor's import/download session separate while presenting
        # one flat set of image tabs to the user.
        for _ in range(2):
            page = self.shared_images.tabs.widget(0)
            title = self.shared_images.tabs.tabText(0)
            self.shared_images.tabs.removeTab(0)
            self.images.tabs.addTab(page, title)
        self.shared_images.navigation_tabs = self.images.tabs
        self.shared_images.local_tab_index = 2
        self.shared_images.hide()
        self.tabs.addTab(self.images, t('圖片'))
        for index, title, path in [(0, t('歌曲圖片資料夾'), song_images_dir(self.workspace, song_id)),
                                  (2, t('共用圖片資料夾'), shared_images_dir(self.workspace))]:
            button = QPushButton(title)
            button.clicked.connect(lambda _, target=path: controller._open_asset_folder(target))
            self.images.tabs.widget(index).layout().addWidget(button)
        self.images.tabs.currentChanged.connect(self._image_scope_changed)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.state = load_lyric_state(self.workspace, song_id)
        self.source.setCurrentIndex(max(0, self.source.findData(self.state.get('source', 'manual'))))
        self.manual.setPlainText(read_lyrics(self.workspace, song_id))
        local_path = assets_dir(self.workspace, song_id) / 'local.lrc'
        self.local.setPlainText(local_path.read_text(encoding='utf-8-sig') if local_path.is_file() else '')
        cached = self.state.get('lrclib', {})
        if cached:
            self.status.setText(t('已保存：{p0} · {p1}（ID {p2}）', p0=cached.get('trackName', ''), p1=cached.get('artistName', ''), p2=cached.get('id', '')))
        self.finished.connect(self.cancel_search)
        self.finished.connect(self.images.dispose)
        self.finished.connect(self.shared_images.dispose)

    def _image_scope_changed(self, index):
        inactive = self.images if index >= 2 else self.shared_images
        if inactive.receiver:
            inactive.receiver.close()

    def import_lrc(self):
        filename, _ = QFileDialog.getOpenFileName(self, t('匯入原曲時間軸 LRC'), '', 'LRC (*.lrc)')
        if filename:
            try:
                text = Path(filename).read_text(encoding='utf-8-sig')
                parse_lrc(text)
                self.local.setPlainText(text)
                self.source.setCurrentIndex(self.source.findData('local'))
            except (OSError, UnicodeError, ValueError) as error:
                QMessageBox.warning(self, t('匯入失敗'), str(error))

    def search(self):
        self.cancel_search()
        title, artist = self.title_edit.text().strip(), self.artist_edit.text().strip()
        if not title:
            QMessageBox.warning(self, t('請填歌名'), t('搜尋需要原曲歌名。'))
            return
        duration = self.duration_edit.text().strip()
        try:
            seconds = float(duration) if duration else None
            if seconds is not None and not 1 <= seconds <= 3600:
                raise ValueError()
        except ValueError:
            QMessageBox.warning(self, t('原曲時長無效'), t('請填 1–3600 秒，或留白。'))
            return
        self.query_used = {'track_name': title}
        if artist:
            self.query_used['artist_name'] = artist
        if self.album_edit.text().strip():
            self.query_used['album_name'] = self.album_edit.text().strip()
        self.endpoint = 'get' if artist else 'search'
        self.params = dict(self.query_used)
        if seconds is not None and self.endpoint == 'get':
            self.params['duration'] = str(seconds)
        self.query_used['duration'] = seconds
        self.attempt = 0
        self.results.clear()
        self.preview_text.clear()
        self.candidates = []
        self.search_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self._send()

    def _send(self):
        self.status.setText(t('正在查詢 LRCLIB…'))
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

    def cancel_search(self, *_):
        self.retry.stop()
        self.timeout.stop()
        reply, self.reply = self.reply, None
        if reply:
            reply.abort()
            reply.deleteLater()
        self.search_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.status.setText(t('搜尋已取消；已保存的歌詞不受影響。'))

    def _timed_out(self):
        self.cancel_search()
        self.status.setText(t('LRCLIB 查詢逾時，請稍後再試。'))

    def _received(self):
        reply = self.sender()
        if reply is not self.reply:
            return
        self.reply = None
        self.timeout.stop()
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        content = bytes(reply.readAll())
        error = reply.errorString()
        retry_after = bytes(reply.rawHeader('Retry-After')).decode('ascii', errors='replace')
        reply.deleteLater()
        if status == 404 and self.endpoint == 'get':
            self.endpoint = 'search'
            self.params.pop('duration', None)
            self.retry.start(300)
            return
        if status in {429, 503} and self.attempt < 1:
            try:
                wait = max(1, int(retry_after))
            except ValueError:
                # Do not retry an unknown limit window prematurely.
                wait = 60
            if wait <= 60:
                self.attempt += 1
                self.status.setText(t('服務忙碌／限流，{p0} 秒後重試；可取消。', p0=wait))
                self.retry.start(wait * 1000)
                return
        self.search_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        if status != 200:
            self.status.setText(t('查無歌詞。') if status == 404 else t('查詢失敗：HTTP {p0} · {p1}', p0=status or '—', p1=error) +
                                (t('；請至少等待 {p0} 秒再查詢。', p0=retry_after) if retry_after else ''))
            return
        try:
            payload = json.loads(content)
            rows = payload if isinstance(payload, list) else [payload]
            if any(not isinstance(row, dict) or 'id' not in row for row in rows):
                raise ValueError(t('伺服器回傳格式不符'))
            self.candidates = rows
            for row in rows:
                mode = t('同步') if row.get('syncedLyrics') else t('純文字') if row.get('plainLyrics') else t('無歌詞／純音樂')
                self.results.addItem(t('{p0} · {p1} · {p2} · {p3} 秒 · {p4}', p0=row.get('trackName', ''), p1=row.get('artistName', ''), p2=row.get('albumName', ''), p3=row.get('duration', '?'), p4=mode))
            self.status.setText(t('找到 {p0} 筆；請確認錄音版本，預覽後套用。', p0=len(rows)) if rows else t('查無歌詞；可改歌名／歌手再搜尋，或使用人工／本機 LRC。'))
        except (ValueError, TypeError) as error:
            self.status.setText(t('無法讀取查詢結果：{p0}', p0=error))

    def preview(self, index):
        row = self.candidates[index] if 0 <= index < len(self.candidates) else {}
        self.preview_text.setPlainText(row.get('syncedLyrics') or row.get('plainLyrics') or '')
        self.apply_button.setEnabled(bool(row.get('syncedLyrics') or row.get('plainLyrics')))

    def apply_candidate(self):
        row = self.candidates[self.results.currentRow()]
        if row.get('syncedLyrics'):
            try:
                parse_lrc(row['syncedLyrics'])
            except ValueError as error:
                QMessageBox.warning(self, t('同步格式無法使用'), str(error) + t('；請選下載純文字或使用人工內容。'))
                if not row.get('plainLyrics'):
                    return
                mode = 'downloaded'
            else:
                mode = 'lrclib'
        else:
            mode = 'downloaded'
        self.chosen = dict(row)
        self.chosen_query = dict(self.query_used)
        self.source.setCurrentIndex(self.source.findData(mode))
        self.status.setText(t('已選取；按下方儲存後保存到本機。'))

    def save(self):
        for editor in (self.images, self.shared_images):
            if editor.download_reply:
                self.tabs.setCurrentWidget(self.images)
                self.images.tabs.setCurrentIndex(editor.local_tab_index)
                editor.receive_status(t('請等待圖片下載完成，或先取消下載。'))
                return
        source = self.source.currentData()
        folder = assets_dir(self.workspace, self.song_id)
        try:
            if source == 'local':
                parse_lrc(self.local.toPlainText())
            elif source in {'lrclib', 'downloaded'}:
                field, filename = ('syncedLyrics', 'lrclib.lrc') if source == 'lrclib' else ('plainLyrics', 'lrclib.txt')
                text = self.chosen.get(field) if self.chosen else ((folder / filename).read_text(encoding='utf-8-sig') if (folder / filename).is_file() else '')
                if not text:
                    raise ValueError(t('所選來源沒有歌詞，請先搜尋套用，或選人工／本機來源。'))
                if source == 'lrclib':
                    parse_lrc(text)
            save_lyrics(self.workspace, self.song_id, self.manual.toPlainText())
            (folder / 'local.lrc').write_text(self.local.toPlainText(), encoding='utf-8')
            if self.chosen:
                for field, filename in [('syncedLyrics', 'lrclib.lrc'), ('plainLyrics', 'lrclib.txt')]:
                    (folder / filename).write_text(self.chosen.get(field) or '', encoding='utf-8')
                self.state['lrclib'] = {key: value for key, value in self.chosen.items() if key not in {'syncedLyrics', 'plainLyrics', 'lyricsfile'}}
                self.state['query'] = self.chosen_query
            self.state['source'] = source
            save_lyric_state(self.workspace, self.song_id, self.state)
            self.images.save()
            self.shared_images.save()
        except (OSError, UnicodeError, ValueError) as error:
            QMessageBox.warning(self, t('儲存素材失敗'), str(error))
            return
        self.images.receive_status(t('素材已儲存。'))
        self.shared_images.receive_status(t('素材已儲存。'))
