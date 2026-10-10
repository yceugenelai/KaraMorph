"""Explicit paste/drop reception, shared by the editor and its temporary bar."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut, QAction
from PySide6.QtWidgets import (QApplication, QLabel, QListWidget,
    QListWidgetItem, QVBoxLayout, QWidget, QToolBar, QSizePolicy, QAbstractItemView)
from app.i18n import t
from app.browser_images import split_area


class ImageDropArea(QWidget):
    received = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.paste_shortcut = QShortcut(QKeySequence.StandardKey.Paste, self)
        self.paste_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.paste_shortcut.activated.connect(self.paste)
        self.setStyleSheet('ImageDropArea { border: 2px dashed #7094b5; border-radius: 6px; }')

    def paste(self):
        self.received.emit(QApplication.clipboard().mimeData())

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Paste):
            self.paste()
            event.accept()
        else:
            super().keyPressEvent(event)

    def dragEnterEvent(self, event):
        mime = event.mimeData()
        if mime.hasImage() or mime.hasUrls() or mime.hasText():
            event.acceptProposedAction()

    def dropEvent(self, event):
        self.received.emit(event.mimeData())
        event.acceptProposedAction()


class ImageReceiverBar(ImageDropArea):
    closed = Signal()

    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setWindowTitle(t('圖片接收列'))
        self.setMinimumHeight(150)
        self.setMinimumWidth(400)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 5, 8, 5)
        toolbar = QToolBar()
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        receiver_title = t('共用圖片') if editor.shared else editor.song.get('title', '')
        title = QLabel(receiver_title)
        title.setMaximumWidth(160)
        title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        title.setToolTip(receiver_title)
        toolbar.addWidget(title)
        for label, action in [(t('貼上圖片'), self.paste), (t('刪除選取圖片'), self.remove_selected),
                              (t('重新排列搜尋視窗'), editor.rearrange),
                              (t('取消圖片下載'), editor.cancel_download),
                              (t('返回素材並儲存'), editor.return_to_save), (t('關閉接收列'), self.close)]:
            button = QAction(label, self)
            button.triggered.connect(action)
            toolbar.addAction(button)
            if label == t('取消圖片下載'):
                self.cancel_action = button
                button.setEnabled(bool(editor.download_reply))
                button.setToolTip(t('只有圖片網址下載中才能取消；拖曳或貼上不需下載。'))
            elif label == t('刪除選取圖片'):
                self.remove_action = button
            elif label == t('重新排列搜尋視窗'):
                self.arrange_action = button
                button.setToolTip(t('將圖片接收列排在上方，圖片搜尋瀏覽器視窗排在下方。'))
        layout.addWidget(toolbar)
        self.images = QListWidget()
        self.images.setViewMode(QListWidget.ViewMode.IconMode)
        self.images.setFlow(QListWidget.Flow.LeftToRight)
        self.images.setWrapping(False)
        self.images.setMovement(QListWidget.Movement.Static)
        self.images.setIconSize(editor.gallery.iconSize())
        self.images.setMaximumHeight(115)
        self.images.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.images.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.images.setStyleSheet('QListWidget::item { border: 3px solid transparent; padding: 2px; }'
                                  'QListWidget::item:selected { border: 3px solid #1677d2; '
                                  'background: #dceeff; color: #102c45; }')
        self.images.viewport().setAcceptDrops(False)
        self.images.setAcceptDrops(False)
        self.images.currentItemChanged.connect(self.select_image)
        self.images.itemSelectionChanged.connect(self.update_selection)
        layout.addWidget(self.images, 1)
        self.status = QLabel(t('拖曳圖片到這裡，或複製圖片後按「貼上」。'))
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.received.connect(editor.receive_mime)
        self.sync_images()

    def sync_images(self):
        previous = self.images.blockSignals(True)
        self.images.clear()
        for index in range(self.editor.gallery.count()):
            source = self.editor.gallery.item(index)
            path = source.data(Qt.ItemDataRole.UserRole)
            if path in self.editor.imports:
                item = QListWidgetItem(source.icon(), path.name)
                item.setData(Qt.ItemDataRole.UserRole, path)
                self.images.addItem(item)
                if source is self.editor.gallery.currentItem():
                    self.images.setCurrentItem(item)
        self.images.blockSignals(previous)
        self.update_selection()

    def select_image(self, item, _=None):
        if item:
            path = item.data(Qt.ItemDataRole.UserRole)
            for index in range(self.editor.gallery.count()):
                source = self.editor.gallery.item(index)
                if source.data(Qt.ItemDataRole.UserRole) == path:
                    self.editor.gallery.setCurrentItem(source)
                    break
        self.update_selection()

    def update_selection(self):
        item = self.images.currentItem()
        selected = bool(item and item.isSelected())
        self.remove_action.setEnabled(selected)
        if selected:
            path = item.data(Qt.ItemDataRole.UserRole)
            self.remove_action.setToolTip(t('移除選取圖片：{p0}', p0=path.name))
        else:
            self.remove_action.setToolTip(t('請先選取要移除的圖片。'))

    def remove_selected(self):
        item = self.images.currentItem()
        if item and item.isSelected():
            self.editor.remove_pending(item.data(Qt.ItemDataRole.UserRole))

    def arrange(self, percent):
        self.setMinimumHeight(max(150, self.minimumSizeHint().height()))
        screen = self.editor.window().screen() or QApplication.primaryScreen()
        area = screen.availableGeometry()
        top, _ = split_area(area.x(), area.y(), area.width(), area.height(), percent, self.minimumHeight())
        self.setGeometry(*top)

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)
