"""User-approved preparation, verification and import of the two model groups."""
import shutil
import threading
import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
                              QProgressBar, QPushButton, QVBoxLayout)
from app.i18n import t
from app.model_store import Cancelled, ModelStore
from app.storage import save_settings
from runtime_config import ROOT, configured_path, runtime


class Preparation(QThread):
    progress = Signal(dict)
    result = Signal(str, str)

    def __init__(self, settings, groups, mode, import_root=None, parent=None):
        super().__init__(parent)
        self.settings, self.groups, self.mode, self.import_root = dict(settings), groups, mode, import_root
        self.cancelled = threading.Event()

    def run(self):
        try:
            ModelStore(self.settings).prepare(self.groups, self.cancelled, self.progress.emit,
                import_root=self.import_root, verify_only=self.mode == 'verify')
            self.result.emit('success', '')
        except Cancelled:
            self.result.emit('cancelled', '')
        except Exception as error:
            self.result.emit('failed', str(error))


class ModelsDialog(QDialog):
    def __init__(self, controller):
        super().__init__(controller)
        self.controller = controller
        self.worker = None
        self.setWindowTitle(t('模型與元件管理'))
        self.resize(740, 490)
        layout = QVBoxLayout(self)
        intro = QLabel(t('模型下載需要網路及磁碟空間。播放與錄音不必下載 AI 模型；準備完成後推論可離線使用。'))
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.separation = QCheckBox(t('人聲分離：Kim Mel-Band RoFormer（MIT）'))
        self.styling = QCheckBox(t('風格轉換：ACE-Step（MIT）＋ Qwen embedding（Apache-2.0）'))
        self.separation.setChecked(True)
        self.styling.setChecked(True)
        layout.addWidget(self.separation)
        layout.addWidget(self.styling)
        self.details = QLabel()
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.details)
        self.separation.toggled.connect(self.refresh)
        self.styling.toggled.connect(self.refresh)
        row = QHBoxLayout()
        self.actions = []
        for title, action in [(t('下載／修復'), lambda: self.start('download')),
                              (t('重新校驗'), lambda: self.start('verify')),
                              (t('匯入已有模型'), self.import_models), (t('選擇模型位置'), self.choose_folder)]:
            button = QPushButton(title)
            button.clicked.connect(action)
            self.actions.append(button)
            row.addWidget(button)
        layout.addLayout(row)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)
        self.status = QLabel(t('只從鎖定的官方來源下載，完成 SHA256 校驗後才啟用。'))
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addStretch()
        bottom = QHBoxLayout()
        self.cancel = QPushButton(t('取消下載'))
        self.cancel.setEnabled(False)
        self.cancel.clicked.connect(self.cancel_work)
        bottom.addWidget(self.cancel)
        close = QPushButton(t('稍後／關閉'))
        close.clicked.connect(self.reject)
        bottom.addWidget(close)
        layout.addLayout(bottom)
        self.refresh()

    def groups(self):
        return ([g for g, box in [('separation', self.separation), ('styling', self.styling)] if box.isChecked()])

    def refresh(self, *_):
        store = ModelStore(self.controller.settings)
        size = store.download_bytes(self.groups()) / 2**30
        free = shutil.disk_usage(ROOT).free / 2**30
        names = []
        for group, title, runtime_key in [('separation', t('分離人聲'), 'roformer_python'),
                                           ('styling', t('製作風格'), 'acestep_python')]:
            state = t('已準備') if store.ready(group) else t('尚未準備')
            if runtime(self.controller.settings, runtime_key) is None:
                state += ' · ' + t('執行元件缺失，請從「設定 → 管理安裝」準備所需元件')
            names.append(f'{title}: {state}')
        self.details.setText('\n'.join(names) + '\n' + t(
            '預估待下載 {size:.2f} GiB；程式磁碟可用 {free:.1f} GiB。另需解壓與暫存空間，下載前會檢查模型磁碟。',
            size=size, free=free) + '\n' + str(configured_path(self.controller.settings, 'roformer_model_dir')) + '\n'
            + str(configured_path(self.controller.settings, 'acestep_model_dir')))

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, t('選擇模型位置'), str(ROOT / 'models'))
        if folder:
            settings = dict(self.controller.settings, roformer_model_dir=str(Path(folder) / 'separation'),
                            acestep_model_dir=str(Path(folder) / 'acestep'))
            try:
                save_settings(settings)
            except OSError as error:
                QMessageBox.warning(self, t('儲存失敗'), str(error))
                return
            self.controller.settings.update(settings)
            self.refresh()

    def import_models(self):
        folder = QFileDialog.getExistingDirectory(self, t('匯入已有模型'), str(ROOT))
        if folder:
            self.start('import', folder)

    def start(self, mode, import_root=None):
        if self.worker is not None or not self.groups():
            return
        if self.controller.active_op is not None or self.controller.playing_item is not None:
            QMessageBox.information(self, t('模型與元件管理'), t('請先停止播放並等待處理完成，再準備模型。'))
            return
        if mode == 'download':
            message = t('將從 Hugging Face／GitHub 下載勾選功能的固定版本資產。請確認來源授權及磁碟空間。是否開始？')
            if QMessageBox.question(self, t('下載／修復'), message,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
        self.worker = Preparation(self.controller.settings, self.groups(), mode, import_root, self)
        for control in self.actions + [self.separation, self.styling]:
            control.setEnabled(False)
        self.cancel.setEnabled(True)
        self.started = time.monotonic()
        self.progress.setRange(0, 0)
        self.worker.progress.connect(self.on_progress)
        self.worker.result.connect(self.on_result)
        self.worker.finished.connect(self.on_finished)
        self.worker.start()

    def on_progress(self, event):
        name = event['file']
        if event['stage'] == 'checking':
            self.progress.setRange(0, 0)
            self.status.setText(t('正在校驗：{name}', name=name))
        else:
            done, total = event.get('done', 0), event.get('total', 0)
            self.progress.setRange(0, 100 if total else 0)
            self.progress.setValue(round(done * 100 / total) if total else 0)
            self.status.setText(t('正在下載：{name} · {done:.1f}／{total:.1f} MiB',
                                  name=name, done=done / 2**20, total=total / 2**20))

    def on_result(self, result, detail):
        self.progress.setRange(0, 100)
        self.progress.setValue(100 if result == 'success' else 0)
        self.status.setText(t('準備完成') if result == 'success' else t('已取消，已完成的資產保留；可重新開始。')
                            if result == 'cancelled' else t('模型準備失敗：{detail}', detail=detail))
        if result == 'failed':
            QMessageBox.warning(self, t('模型準備失敗'), detail)

    def on_finished(self):
        self.worker.deleteLater()
        self.worker = None
        for control in self.actions + [self.separation, self.styling]:
            control.setEnabled(True)
        self.cancel.setEnabled(False)
        self.refresh()

    def cancel_work(self):
        if self.worker is not None:
            self.worker.cancelled.set()
            self.cancel.setEnabled(False)
            self.status.setText(t('正在取消；網路操作可能需要數秒完成。'))

    def reject(self):
        if self.worker is not None:
            self.cancel_work()
            return
        super().reject()

    def closeEvent(self, event):
        if self.worker is not None:
            self.cancel_work()
            event.ignore()
        else:
            super().closeEvent(event)
