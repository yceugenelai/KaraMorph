"""Local image gallery and an optional, provider-independent search shell."""

from pathlib import Path
from tempfile import TemporaryDirectory
import shutil
from PySide6.QtCore import QSize, Qt, QUrl, QTimer, Signal
from PySide6.QtGui import QIcon, QPixmap, QImage
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QTabWidget, QVBoxLayout, QWidget, QApplication)
from app.i18n import t
from app.image_search import ImageSearchQuery, ImageSearchRegistry
from app.karaoke_assets import image_files, shared_images_dir, song_images_dir
from app.song_images import chosen_image, image_reader, import_image_to, save_thumbnail
from app.storage import atomic_json
from app.browser_images import BrowserSearchSession
from app.image_receiver import ImageDropArea, ImageReceiverBar
from app.song_images import save_image_sources


class ImageAssetsWidget(QWidget):
    saveRequested = Signal()
    returnRequested = Signal()

    def __init__(self, workspace, song_id, song, registry=None, parent=None, settings=None, persist=None, shared=False):
        super().__init__(parent)
        self.workspace, self.song_id, self.song = workspace, song_id, song
        self.shared = shared
        self.removals = set()
        self.registry = registry if registry is not None else ImageSearchRegistry()
        self.selection = None if shared else chosen_image(workspace, song_id)
        self.auto_thumbnail_allowed = not shared and self.selection is None
        self.settings = settings if settings is not None else {}
        self.persist = persist
        self.receiver = None
        self.source_urls = {}
        self.download_reply = None
        self.download_file = None
        self.download_bytes = 0
        self.network = QNetworkAccessManager(self)
        self.download_timeout = QTimer(self)
        self.download_timeout.setSingleShot(True)
        self.download_timeout.timeout.connect(self.download_timed_out)
        self.browser_session = BrowserSearchSession(self)
        self.browser_session.status.connect(self.receive_status)
        self.imports = []
        self.request = None
        self.candidates = []
        self.downloads = TemporaryDirectory(prefix='karamorph-images-')
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.navigation_tabs = self.tabs
        self.local_tab_index = 0
        layout.addWidget(self.tabs)
        local = ImageDropArea()
        self.drop_area = local
        local.received.connect(self.receive_mime)
        local_layout = QVBoxLayout(local)
        self.gallery = QListWidget()
        self.gallery.setViewMode(QListWidget.ViewMode.IconMode)
        self.gallery.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.gallery.setMovement(QListWidget.Movement.Static)
        self.gallery.setIconSize(QSize(96, 96))
        self.gallery.setGridSize(QSize(135, 130))
        self.gallery.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.gallery.setStyleSheet('QListWidget::item { border: 3px solid transparent; padding: 2px; }'
                                  'QListWidget::item:selected { border: 3px solid #1677d2; '
                                  'background: #dceeff; color: #102c45; }')
        self.gallery.setAcceptDrops(False)
        self.gallery.viewport().setAcceptDrops(False)
        self.gallery.currentItemChanged.connect(self.preview_local)
        self.gallery.itemSelectionChanged.connect(self.update_delete_button)
        local_layout.addWidget(self.gallery, 1)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(130)
        self.preview.setMaximumHeight(180)
        local_layout.addWidget(self.preview)
        buttons = QHBoxLayout()
        self.import_button = QPushButton(t('匯入本機圖片'))
        self.import_button.clicked.connect(self.browse)
        self.paste_button = QPushButton(t('貼上圖片'))
        self.paste_button.clicked.connect(self.drop_area.paste)
        self.cancel_download_button = QPushButton(t('取消圖片下載'))
        self.cancel_download_button.setEnabled(False)
        self.cancel_download_button.clicked.connect(self.cancel_download)
        self.choose_button = QPushButton(t('設為清單縮圖'))
        self.choose_button.clicked.connect(self.choose_local)
        self.reset_button = QPushButton(t('恢復自動封面'))
        self.reset_button.clicked.connect(self.reset_thumbnail)
        self.delete_button = QPushButton(t('刪除選取圖片'))
        self.delete_button.clicked.connect(self.delete_local)
        for button in (self.import_button, self.paste_button, self.choose_button, self.reset_button):
            buttons.addWidget(button)
        buttons.addWidget(self.delete_button)
        local_layout.addLayout(buttons)
        local_layout.addWidget(self.cancel_download_button)
        self.selection_label = QLabel()
        self.selection_label.setWordWrap(True)
        local_layout.addWidget(self.selection_label)
        note = QLabel(t('匯入後立即存入共用資料夾，所有歌曲都可使用；原始圖片檔案不變。') if shared else t('匯入後立即存入歌曲資料夾；原始檔案不變。縮圖設定與刪除仍需按儲存。'))
        note.setWordWrap(True)
        local_layout.addWidget(note)
        self.receive_label = QLabel(t('可貼上或拖入圖片，完成後立即匯入。'))
        self.receive_label.setWordWrap(True)
        local_layout.addWidget(self.receive_label)
        self.tabs.addTab(local, t('共用圖片選取') if shared else t('歌曲圖片選取'))
        self._build_search()
        self.reload_gallery()
        self.update_selection_label()
        if shared:
            for widget in (self.choose_button, self.reset_button, self.selection_label):
                widget.hide()
        QApplication.instance().screenRemoved.connect(self.rearrange)
        for screen in QApplication.screens():
            screen.availableGeometryChanged.connect(self.rearrange)

    def add_image(self, path, label):
        try:
            reader = image_reader(path)
            reader.setScaledSize(reader.size().scaled(96, 96, Qt.AspectRatioMode.KeepAspectRatio))
            image = reader.read()
            if image.isNull():
                return None
        except (OSError, ValueError):
            return None
        item = QListWidgetItem(QIcon(QPixmap.fromImage(image)), label)
        item.setData(Qt.ItemDataRole.UserRole, Path(path))
        item.setToolTip(str(path))
        self.gallery.addItem(item)
        return item

    def reload_gallery(self):
        self.gallery.clear()
        automatic = QListWidgetItem(t('自動封面'))
        cover = self.song.get('cover_path')
        if cover and Path(cover).is_file():
            automatic.setIcon(QIcon(cover))
        if not self.shared:
            self.gallery.addItem(automatic)
        current = None if self.shared else automatic
        groups = [(image_files(self.image_folder()), t('共用圖片') if self.shared else t('歌曲'))]
        seen = set()
        for paths, label in groups:
            for path in paths:
                path = Path(path).resolve()
                if path in seen or path in self.removals:
                    continue
                seen.add(path)
                item = self.add_image(path, f'{label}\n{path.name}')
                if current is None and item:
                    current = item
                if item and self.selection and path == self.selection.resolve():
                    current = item
        self.gallery.setCurrentItem(current)
        if self.receiver:
            self.receiver.sync_images()

    def show_preview(self, label, path):
        label.clear()
        if not path:
            label.setText(t('沒有圖片'))
            return
        try:
            reader = image_reader(path)
            reader.setScaledSize(reader.size().scaled(440, 175, Qt.AspectRatioMode.KeepAspectRatio))
            image = reader.read()
            if image.isNull():
                raise ValueError()
            label.setPixmap(QPixmap.fromImage(image))
        except (OSError, ValueError):
            label.setText(t('圖片無法讀取'))

    def preview_local(self, item, _=None):
        path = item.data(Qt.ItemDataRole.UserRole) if item else None
        if item is not None and path is None:
            path = self.song.get('cover_path')
        self.show_preview(self.preview, path)
        self.choose_button.setEnabled(item is not None)
        self.update_delete_button()

    def update_delete_button(self):
        item = self.gallery.currentItem()
        path = item.data(Qt.ItemDataRole.UserRole) if item and item.isSelected() else None
        self.delete_button.setEnabled(bool(path))
        self.delete_button.setToolTip(t('移除選取圖片：{p0}', p0=path.name) if path else t('請先選取要移除的圖片。'))

    def image_folder(self):
        return shared_images_dir(self.workspace) if self.shared else song_images_dir(self.workspace, self.song_id)

    def delete_local(self):
        item = self.gallery.currentItem()
        path = item.data(Qt.ItemDataRole.UserRole) if item and item.isSelected() else None
        if not path:
            return
        # Stage managed-file deletion until Save; never unlink an imported original.
        folder = self.image_folder().resolve()
        if path.resolve().parent != folder or path.is_symlink():
            return
        self.removals.add(path.resolve())
        if self.selection and self.selection.resolve() == path.resolve():
            self.selection = None
        self.reload_gallery()
        self.update_selection_label()
        self.receive_status(t('圖片已標記刪除；儲存後生效，取消則保留。'))

    def browse(self):
        files, _ = QFileDialog.getOpenFileNames(self, t('匯入本機圖片'), '',
                                               'Images (*.jpg *.jpeg *.png *.webp *.bmp *.svg)')
        self.stage_images(files)

    def stage_images(self, files):
        last = None
        added = []
        errors = []
        for filename in files:
            path = Path(filename).resolve()
            try:
                reader = image_reader(path)
                reader.setScaledSize(reader.size().scaled(160, 160, Qt.AspectRatioMode.KeepAspectRatio))
                if reader.read().isNull():
                    raise ValueError(t('圖片無法讀取'))
                path = import_image_to(self.image_folder(), path).resolve()
                if path not in self.imports:
                    self.removals.discard(path)
                    self.imports.append(path)
                    added.append(path)
                    if self.selection is None and self.auto_thumbnail_allowed:
                        self.selection = path
                        save_thumbnail(self.workspace, self.song_id, path)
                last = path
            except (OSError, ValueError) as error:
                errors.append(f'{path.name}: {error}')
        self.reload_gallery()
        if last:
            for index in range(self.gallery.count()):
                item = self.gallery.item(index)
                if item.data(Qt.ItemDataRole.UserRole) == last:
                    self.gallery.setCurrentItem(item)
                    break
        self.update_selection_label()
        if self.receiver:
            self.receiver.sync_images()
        if added:
            self.receive_status(t('已匯入 {p0} 張圖片。', p0=len(added)))
            if self.receiver is None or not self.receiver.isVisible():
                self.navigation_tabs.setCurrentIndex(self.local_tab_index)
        if errors:
            QMessageBox.warning(self, t('匯入失敗'), '\n'.join(errors))
        return added

    def choose_local(self):
        item = self.gallery.currentItem()
        if item:
            self.selection = item.data(Qt.ItemDataRole.UserRole)
            self.update_selection_label()

    def reset_thumbnail(self):
        self.auto_thumbnail_allowed = False
        self.selection = None
        self.update_selection_label()
        self.gallery.setCurrentRow(0)

    def update_selection_label(self):
        name = self.selection.name if self.selection else t('自動封面')
        self.selection_label.setText(t('清單縮圖：{p0}（儲存後生效）', p0=name))

    def save(self):
        sources = {}
        folder = self.image_folder().resolve()
        for path in self.removals:
            if path.parent != folder or path.is_symlink():
                raise ValueError('Cannot delete an image outside the managed folder')
        sources = {path.name: url for path, url in self.source_urls.items() if path.exists()}
        if not self.shared:
            save_thumbnail(self.workspace, self.song_id, self.selection)
        if sources and not self.shared:
            save_image_sources(self.workspace, self.song_id, sources)
        if self.shared and sources:
            import json
            source_file = folder / '.karamorph-image-sources.json'
            try:
                existing = json.loads(source_file.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                existing = {}
            atomic_json(source_file, dict(existing if isinstance(existing, dict) else {}, **sources))
        for path in self.removals:
            path.unlink(missing_ok=True)
            if path in self.imports:
                self.imports.remove(path)
            self.source_urls.pop(path, None)
        self.removals.clear()
        self.reload_gallery()
        self.update_selection_label()

    def _build_search(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        self.keywords = QLineEdit('' if self.shared else ' '.join(filter(None, (self.song.get('title'), self.song.get('artist')))))
        self.search_kind = QComboBox()
        for label, key in [(t('相關圖片'), 'related'), (t('相關作品'), 'work'),
                           (t('作者／歌手'), 'artist'), (t('梗圖'), 'meme'), (t('專輯封面'), 'cover')]:
            self.search_kind.addItem(label, key)
        self.method = QComboBox()
        for method in self.registry.available_methods():
            if method.factory is None and method.key not in {'duckduckgo', 'google_images'}:
                continue
            label = t('自訂搜尋') if method.key == 'custom' and method.factory is None else method.label
            self.method.addItem(label, method.key)
        index = self.method.findData(self.settings.get('image_search_engine', 'duckduckgo'))
        self.method.setCurrentIndex(max(0, index))
        form.addRow(t('關鍵字'), self.keywords)
        form.addRow(t('圖片類型'), self.search_kind)
        form.addRow(t('搜尋方式'), self.method)
        layout.addLayout(form)
        row = QHBoxLayout()
        self.search_button = QPushButton(t('搜尋圖片'))
        self.search_button.clicked.connect(self.search)
        self.cancel_button = QPushButton(t('取消搜尋'))
        self.cancel_button.clicked.connect(self.cancel_search)
        self.cancel_button.setEnabled(False)
        row.addWidget(self.search_button)
        row.addWidget(self.cancel_button)
        layout.addLayout(row)
        self.search_status = QLabel()
        self.search_status.setWordWrap(True)
        self.search_status.setText(t('用預設瀏覽器搜尋；選好圖片後貼上或拖到接收列。'))
        layout.addWidget(self.search_status)
        self.results = QListWidget()
        self.results.currentRowChanged.connect(self.preview_search)
        layout.addWidget(self.results, 1)
        self.search_preview = QLabel()
        self.search_preview.setMinimumHeight(100)
        self.search_preview.setMaximumHeight(175)
        self.search_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.search_preview)
        self.use_result = QPushButton(t('匯入選取圖片'))
        self.use_result.setEnabled(False)
        self.use_result.clicked.connect(self.import_result)
        layout.addWidget(self.use_result)
        self.tabs.addTab(page, t('共用網路圖片搜尋') if self.shared else t('歌曲網路圖片搜尋'))
        self.receiver_button = QPushButton(t('顯示圖片接收列'))
        self.receiver_button.clicked.connect(self.show_receiver)
        layout.addWidget(self.receiver_button)
        self.browser_paste = QPushButton(t('貼上圖片'))
        self.browser_paste.clicked.connect(self.drop_area.paste)
        layout.addWidget(self.browser_paste)
        layout.addStretch()
        self.method.currentIndexChanged.connect(self.cancel_search)
        self.method.currentIndexChanged.connect(self.update_search_mode)
        self.update_search_mode()

    def update_search_mode(self, *_):
        method = self.registry.methods.get(self.method.currentData())
        integrated = bool(method and method.factory)
        for widget in (self.results, self.search_preview, self.use_result, self.cancel_button):
            widget.setVisible(integrated)

    def search(self):
        self.cancel_search()
        self.results.clear()
        self.candidates = []
        method = self.registry.methods.get(self.method.currentData())
        if method and method.factory is None and method.key in {'duckduckgo', 'google_images'}:
            keywords = self.keywords.text().strip()
            if not keywords:
                self.search_status.setText(t('請輸入圖片搜尋關鍵字。'))
                return
            self.settings['image_search_engine'] = method.key
            if self.persist:
                self.persist()
            kind = self.search_kind.currentData()
            suffix = {'work': t('作品'), 'artist': t('歌手'), 'meme': t('梗圖'), 'cover': t('專輯封面')}.get(kind, '')
            if suffix:
                keywords += ' ' + suffix
            self.show_receiver()
            self.browser_session.open(method.key, keywords, self.receiver, self.receiver_percent())
            return
        if method is None or method.factory is None:
            self.search_status.setText(t('此搜尋方式尚未實作；目前不會連線。'))
            return
        query = ImageSearchQuery(self.keywords.text().strip(), self.search_kind.currentData(),
                                 '' if self.shared else self.song.get('title', ''),
                                 '' if self.shared else self.song.get('artist', ''),
                                 '' if self.shared else self.song.get('album', ''))
        if not query.keywords:
            self.search_status.setText(t('請輸入圖片搜尋關鍵字。'))
            return
        try:
            request = method.factory(query, self)
            self.request = request
            request.resultsReady.connect(lambda results: self.search_received(request, results))
            request.failed.connect(lambda error: self.search_failed(request, error))
            self.search_button.setEnabled(False)
            self.cancel_button.setEnabled(True)
            self.search_status.setText(t('正在搜尋圖片…'))
            request.start()
        except Exception as error:
            self.cancel_search()
            self.search_status.setText(str(error))

    def search_received(self, request, results):
        if self.request is not request:
            return
        self.candidates = list(results)
        self.results.clear()
        for candidate in self.candidates:
            item = QListWidgetItem(candidate.title)
            item.setToolTip(candidate.source_url)
            self.results.addItem(item)
        self.search_button.setEnabled(True)
        self.search_status.setText(t('找到 {p0} 張圖片', p0=len(self.candidates)))

    def search_failed(self, request, error):
        if self.request is request:
            self.cancel_search()
            self.search_status.setText(error)

    def preview_search(self, row):
        candidate = self.candidates[row] if 0 <= row < len(self.candidates) else None
        path = candidate.local_path if candidate else None
        self.show_preview(self.search_preview, path)
        self.use_result.setEnabled(bool(path and Path(path).is_file()))

    def import_result(self):
        row = self.results.currentRow()
        if not 0 <= row < len(self.candidates):
            return
        source = self.candidates[row].local_path
        if not source:
            return
        try:
            image_reader(source)
            target = Path(self.downloads.name) / f'{len(self.imports)}{Path(source).suffix}'
            shutil.copyfile(source, target)
            added = self.stage_images([target])
            if added and self.candidates[row].source_url:
                self.remember_source(added[0], self.candidates[row].source_url)
            self.navigation_tabs.setCurrentIndex(self.local_tab_index)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, t('匯入失敗'), str(error))

    def cancel_search(self, *_):
        request, self.request = self.request, None
        self.cancel_button.setEnabled(False)
        self.search_button.setEnabled(True)
        self.use_result.setEnabled(False)
        self.candidates = []
        self.results.clear()
        self.search_preview.clear()
        if request:
            request.cancel()
            request.deleteLater()

    def dispose(self, *_):
        self.cancel_download()
        if self.receiver:
            self.receiver.close()
        self.browser_session.finish()
        self.cancel_search()
        self.downloads.cleanup()

    def receiver_percent(self):
        try:
            return min(30, max(10, int(self.settings.get('image_receiver_percent', 20))))
        except (ValueError, TypeError, OverflowError):
            return 20

    def show_receiver(self):
        if self.receiver is None:
            self.receiver = ImageReceiverBar(self)
            self.receiver.closed.connect(self.browser_session.finish)
        self.receiver.arrange(self.receiver_percent())
        self.receiver.show()
        self.receiver.raise_()

    def rearrange(self, *_):
        if self.receiver and self.receiver.isVisible():
            self.receiver.arrange(self.receiver_percent())
            self.browser_session.percent = self.receiver_percent()
            try:
                if self.browser_session.arrange():
                    self.receive_status(t('已重新排列接收列與搜尋視窗。'))
                else:
                    self.receive_status(t('已排列接收列；尚未確認搜尋視窗，請手動調整瀏覽器。'))
            except OSError:
                self.receive_status(t('無法自動排列；請自行調整瀏覽器位置。'))

    def return_to_save(self):
        if self.receiver:
            self.receiver.close()
        self.navigation_tabs.setCurrentIndex(self.local_tab_index)
        self.returnRequested.emit()
        self.window().show()
        self.window().raise_()
        self.window().activateWindow()
        self.saveRequested.emit()

    def receive_status(self, message):
        self.receive_label.setText(message)
        self.search_status.setText(message)
        if self.receiver:
            self.receiver.status.setText(message)
            self.receiver.cancel_action.setEnabled(bool(self.download_reply))

    def remove_pending(self, path):
        if path in self.imports:
            self.removals.add(path)
            if self.selection == path:
                self.selection = None
            self.reload_gallery()
            self.update_selection_label()
            self.receive_status(t('圖片已標記刪除；儲存後生效，取消則保留。'))

    def remember_source(self, path, url):
        self.source_urls[path] = url
        if not self.shared:
            save_image_sources(self.workspace, self.song_id, {path.name: url})
        else:
            import json
            target = self.image_folder() / '.karamorph-image-sources.json'
            try:
                state = json.loads(target.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                state = {}
            state = state if isinstance(state, dict) else {}
            state[path.name] = url
            atomic_json(target, state)

    def receive_mime(self, mime):
        # Consume MIME data synchronously; drag/clipboard objects are borrowed.
        if mime.hasImage():
            data = mime.imageData()
            image = data.toImage() if isinstance(data, QPixmap) else QImage(data)
            if image.isNull() or image.width() * image.height() > 40_000_000:
                self.receive_status(t('圖片無法讀取'))
                return
            path = Path(self.downloads.name) / f'pasted-{len(list(Path(self.downloads.name).iterdir()))}.png'
            if not image.save(str(path), 'PNG') or path.stat().st_size > 20_000_000:
                path.unlink(missing_ok=True)
                self.receive_status(t('圖片超過 20 MB，請縮小後再試。'))
                return
            added = self.stage_images([path])
            if added and mime.hasUrls():
                sources = [url.toString() for url in mime.urls() if url.scheme() in {'http', 'https'}]
                if sources:
                    self.remember_source(added[0], sources[0])
            return
        urls = list(mime.urls()) if mime.hasUrls() else []
        if not urls and mime.hasText():
            candidate = QUrl(mime.text().strip())
            if candidate.scheme() in {'https', 'http', 'file'}:
                urls = [candidate]
        local = [url.toLocalFile() for url in urls if url.isLocalFile()]
        if local:
            self.stage_images(local)
            return
        remote = [url for url in urls if url.scheme() in {'http', 'https'} and url.host()]
        if remote:
            self.download_image(remote[0])
        else:
            self.receive_status(t('請複製圖片本身或拖入圖片檔案。'))

    def download_image(self, url):
        if self.download_reply:
            self.receive_status(t('圖片下載中，請等待或取消。'))
            return
        request = QNetworkRequest(url)
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        request.setRawHeader(b'User-Agent', b'KaraMorph/0.1 image-import')
        self.download_source = url.toString()
        self.download_bytes = 0
        path = Path(self.downloads.name) / f'download-{len(list(Path(self.downloads.name).iterdir()))}.png'
        self.download_path = path
        try:
            self.download_file = path.open('wb')
        except OSError as error:
            self.receive_status(str(error))
            return
        reply = self.network.get(request)
        self.download_reply = reply
        reply.readyRead.connect(lambda: self.download_chunk(reply))
        reply.finished.connect(lambda: self.download_finished(reply))
        self.download_timeout.start(20000)
        self.cancel_download_button.setEnabled(True)
        self.receive_status(t('正在下載選取圖片…'))

    def download_chunk(self, reply):
        if self.download_reply is not reply:
            return
        data = bytes(reply.readAll())
        self.download_bytes += len(data)
        if self.download_bytes > 20_000_000:
            self.cancel_download()
            self.receive_status(t('圖片超過 20 MB，請縮小後再試。'))
            return
        try:
            self.download_file.write(data)
        except OSError as error:
            self.cancel_download()
            self.receive_status(str(error))

    def download_finished(self, reply):
        if self.download_reply is not reply:
            return
        self.download_chunk(reply)
        if self.download_reply is not reply:
            return
        self.download_file.close()
        self.download_file = None
        self.download_reply = None
        self.download_timeout.stop()
        self.cancel_download_button.setEnabled(False)
        ok = reply.error() == reply.NetworkError.NoError
        reply.deleteLater()
        if ok:
            try:
                reader = image_reader(self.download_path)
                suffix = {b'png': '.png', b'jpeg': '.jpg', b'webp': '.webp',
                          b'bmp': '.bmp', b'svg': '.svg'}.get(bytes(reader.format()))
                if suffix is None:
                    raise ValueError('Unsupported image format')
                if self.download_path.suffix != suffix:
                    renamed = self.download_path.with_suffix(suffix)
                    self.download_path.replace(renamed)
                    self.download_path = renamed
                added = self.stage_images([self.download_path])
                if added:
                    self.remember_source(added[0], self.download_source)
                    return
            except (OSError, ValueError):
                pass
        self.download_path.unlink(missing_ok=True)
        self.receive_status(t('這個連結無法取得圖片；請在瀏覽器複製圖片後貼上。'))

    def cancel_download(self):
        reply, self.download_reply = self.download_reply, None
        self.download_timeout.stop()
        if self.download_file:
            self.download_file.close()
            self.download_file = None
        if reply:
            reply.abort()
            reply.deleteLater()
            self.download_path.unlink(missing_ok=True)
            self.receive_status(t('圖片下載已取消。'))
        if hasattr(self, 'cancel_download_button'):
            self.cancel_download_button.setEnabled(False)

    def download_timed_out(self):
        self.cancel_download()
        self.receive_status(t('圖片下載逾時；可改用複製圖片後貼上。'))
