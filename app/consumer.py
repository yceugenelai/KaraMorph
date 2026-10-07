"""Song-first consumer interface for KaraMorph."""

from app.i18n import t
from app.i18n import LANGUAGES, configure, language, preset_label, translate_in
import base64
import shutil
import sys
import time
import uuid
from pathlib import Path

import numpy as np
from PySide6.QtCore import QBuffer, QByteArray, QEventLoop, QIODevice, QObject, QPoint, QProcess, QRunnable, QThreadPool, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPixmap
from PySide6.QtMultimedia import QtAudio, QAudioFormat, QAudioOutput, QAudioSink, QMediaDevices, QMediaPlayer
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFileDialog,
                               QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSlider, QSpinBox,
                               QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
                               QAbstractItemView, QSizePolicy)
from PySide6.QtWidgets import QHeaderView, QTreeWidgetItemIterator

from runtime_config import DEFAULTS, migration_issues, runtime as resolve_runtime, worker_env as resolve_worker_env

from app.jobs import JobManager
from app.karaoke_assets import (ActivityTask, image_files,
                                shared_images_dir, song_images_dir)
from app.lyrics import effective_speed, load_selected, lyric_status, save_delay
from app.karaoke_window import KaraokeWindow
from app.library_media import cache_background_cover, cache_thumbnail, read_media
from app.playlist import load_playlist, resolve_item, save_playlist
from app.microphone import MicrophoneService
from app.exclusive_audio import ExclusivePlayer, test_exclusive_device
from app.recordings import load_recordings, delete_recording
from app.preview_audio import StemPreview
from app.storage import (data_dir, load_catalog, load_settings, merge_library, save_catalog,
                         save_settings, scan_songs, song_dir)
from app.style_presets import PRESETS
from app.style_execution import confirm_execution
from app.batch_lyrics import LyricsLookup, has_lrclib
from app.ui_icons import icon

from runtime_config import ROOT
LEVELS = {
    "小": (0.3, 0.25, "0.3,0.35,0.4", "0.2,0.25,0.3"),
    "適中": (0.2, 0.2, "0.17,0.2,0.25", "0.15,0.2,0.25"),
    "明顯": (0.15, 0.15, "0.1,0.15,0.18", "0.1,0.15,0.2"),
}


class ScanSignals(QObject):
    done = Signal(str, list)
    error = Signal(str)


class LibraryScan(QRunnable):
    def __init__(self, source: Path):
        super().__init__()
        self.source = source
        self.signals = ScanSignals()

    def run(self):
        try:
            songs = scan_songs(self.source)
            for song in songs:
                path = Path(song["path"])
                tags, artwork = read_media(path)
                song.update(tags)
                song["cover_path"] = cache_thumbnail(song["id"], artwork)
            self.signals.done.emit(str(self.source.resolve()), songs)
        except Exception as error:
            self.signals.error.emit(str(error))


def cover_icon(path: str | None) -> QIcon:
    if path and Path(path).is_file():
        return QIcon(path)
    pixmap = QPixmap(72, 72)
    pixmap.fill(QColor("#314b63"))
    painter = QPainter(pixmap)
    painter.setPen(QColor("#ffffff"))
    font = painter.font()
    font.setPixelSize(38)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "♫")
    painter.end()
    return QIcon(pixmap)


def _effective_adjustments(variant: dict, variants_by_id: dict[str, dict]) -> tuple[float, int]:
    """Accumulate the speed/key changes inherited from parent variants."""
    speed, semitones = 1.0, 0
    current = variant
    seen = set()
    while current and current.get("id") not in seen:
        seen.add(current.get("id"))
        if current.get("kind") == "fx":
            speed *= float(current.get("speed", 1))
            semitones += int(current.get("semitones", 0))
        current = variants_by_id.get(current.get("source_id"))
    return speed, semitones


def version_label(variant: dict, variants_by_id: dict[str, dict] | None = None, translate=t) -> str:
    speed, semitones = _effective_adjustments(variant, variants_by_id or {})
    if variant.get("kind") == "fx":
        parts = []
        if semitones:
            parts.append(f"♯ Key {semitones:+d}")
        if abs(speed - 1.0) > 0.001:
            parts.append(translate('◴ 速度 {p0:g}×', p0=speed))
        return " · ".join(parts) or translate('調整版本')
    style_name = variant.get('style_label')
    if style_name in PRESETS or style_name == '自訂風格':
        style_name = translate(style_name)
    parts = ["♪ " + (style_name or translate('風格版本'))]
    if semitones:
        parts.append(f"Key {semitones:+d}")
    if abs(speed - 1.0) > 0.001:
        parts.append(translate('速度 {p0:g}×', p0=speed))
    if all(key in variant for key in ("cover_strength", "cover_noise_strength", "seed")):
        parts.append(f"(s:{float(variant['cover_strength']):g}, cns:{float(variant['cover_noise_strength']):g}, se:{variant['seed']})")
    return " · ".join(parts)


class ConsumerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("KaraMorph")
        self.setWindowIcon(QIcon(str(ROOT / "assets" / "icon.svg")))
        self.resize(1160, 760)
        self.settings = load_settings()
        configure(self.settings.get('ui_language', 'zh_TW'))
        self.workspace = Path(self.settings["workspace"]).expanduser().resolve()
        self.songs = []
        self.song_map = {}
        self.pending_ops = []
        self.active_op = None
        self.batches = {}
        self.last_batch = None
        self.work_started = None
        self.work_stage = t('準備就緒')
        self.work_device = ""
        self.work_candidate = ""
        self.lyric_lookup = LyricsLookup(self)
        self.lyric_lookup.done.connect(self._lyrics_finished)
        self.lyric_lookup.status.connect(self._lyrics_status)
        self.playing_item = None
        self.models_dialog = None
        self.install_process = None
        self.karaoke_window = None
        self.activity_tasks = {}
        self.activity_cache = {}
        self.current_activity_key = None
        self.expanded_song_ids = set()
        self.current_preview_key = None
        self.preview_checks = {}
        self.preview_buttons = {}
        self.preview_vocal_choices = {}
        self.stem_preview = StemPreview()
        self.microphone = MicrophoneService(self)
        self.microphone.recordingFinished.connect(lambda _: self.refresh_tree())
        self.microphone.failed.connect(lambda message: QMessageBox.warning(self, t('麥克風錯誤'), message))
        self.job_state = {}
        self.failures = {}
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.8)
        self.player.setAudioOutput(self.audio)
        self.player.mediaStatusChanged.connect(self._media_status)
        self.player.errorOccurred.connect(self._media_error)
        self.guide_player = QMediaPlayer(self)
        self.guide_audio = QAudioOutput(self)
        self.guide_audio.setVolume(self.audio.volume() * 0.15)
        self.guide_player.setAudioOutput(self.guide_audio)
        self.guide_vocal_path = None
        self.guide_player.mediaStatusChanged.connect(self._guide_media_status)
        self.player.positionChanged.connect(self._sync_guide_position)
        self.player.playbackStateChanged.connect(self._sync_guide_state)
        self.qt_player = self.player
        self.exclusive_player = ExclusivePlayer(self.microphone, self)
        self.exclusive_player.mediaStatusChanged.connect(self._media_status)
        self.exclusive_player.errorOccurred.connect(self._exclusive_error)
        self.audio.volumeChanged.connect(lambda value: setattr(self.exclusive_player, 'music_gain', value))
        self.guide_audio.volumeChanged.connect(lambda value: setattr(self.exclusive_player, 'guide_gain', value))
        self.jobs = JobManager(self)
        self.jobs.event.connect(self._job_event)
        self.jobs.finished.connect(self._job_finished)

        tabs = QTabWidget()
        self.setCentralWidget(tabs)
        self.sing_page = QWidget()
        self.process_page = QWidget()
        self.settings_page = QWidget()
        tabs.addTab(self.sing_page, t('唱歌'))
        tabs.addTab(self.process_page, t('處理歌曲'))
        tabs.addTab(self.settings_page, t('設定'))
        language_panel = QWidget(tabs)
        language_layout = QHBoxLayout(language_panel)
        language_layout.setContentsMargins(8, 0, 12, 0)
        language_layout.addWidget(QLabel(t('介面語言')))
        self.ui_language = QComboBox()
        for label, code in LANGUAGES:
            self.ui_language.addItem(label, code)
        self.ui_language.setCurrentIndex(self.ui_language.findData(language()))
        self.ui_language.setToolTip(t('介面語言（儲存後重新啟動生效）'))
        self.ui_language.setAccessibleName(t('介面語言'))
        language_layout.addWidget(self.ui_language)
        tabs.setCornerWidget(language_panel, Qt.Corner.TopRightCorner)
        self.ui_language.currentIndexChanged.connect(self._language_changed)
        self._build_sing_page()
        self._build_process_page()
        self._build_settings_page()
        self._closing = False
        self.work_timer = QTimer(self)
        self.work_timer.setInterval(1000)
        self.work_timer.timeout.connect(self._update_work_summary)
        self.work_timer.start()
        self._restore_last_queue()
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #f5f7fa; color: #203044; font-size: 14px; }
            QTabWidget::pane { border: 0; }
            QTabBar::tab { padding: 12px 24px; background: #e6edf3; }
            QTabBar::tab:selected { background: #fff; color: #1464a0; font-weight: bold; }
            QPushButton { background: #e1ebf4; border: 1px solid #c6d6e4; border-radius: 7px; padding: 7px 12px; }
            QPushButton:hover { background: #cddfec; }
            QPushButton#primary { background: #176da9; color: white; border: 0; }
            QListWidget, QTreeWidget, QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox { background: white; border: 1px solid #d5e0e9; border-radius: 6px; padding: 4px; }
            QDoubleSpinBox, QSpinBox { padding-right: 24px; }
            QDoubleSpinBox::up-button, QSpinBox::up-button {
                subcontrol-origin: border; subcontrol-position: top right; width: 22px;
            }
            QDoubleSpinBox::down-button, QSpinBox::down-button {
                subcontrol-origin: border; subcontrol-position: bottom right; width: 22px;
            }
            QGroupBox { border: 1px solid #d5e0e9; border-radius: 8px; margin-top: 12px; padding: 12px; font-weight: bold; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; }
        """)
        if self.settings.get("source_dir"):
            self.scan_library()
        else:
            self.info.setText(t('請先到設定選擇音樂資料夾'))

    def installation_active(self):
        return self.install_process is not None and self.install_process.state() != QProcess.ProcessState.NotRunning

    def open_install_manager(self):
        if self.installation_active():
            return
        if self.playing_item or self.active_op or self.pending_ops or self.jobs.current or (self.models_dialog is not None and self.models_dialog.worker is not None):
            QMessageBox.information(self, t('管理安裝'), t('請先停止播放並等待處理完成。'))
            return
        process = QProcess(self)
        self.install_process = process
        self._stop_preview()
        self.centralWidget().setEnabled(False)
        process.finished.connect(self._installation_finished)
        process.errorOccurred.connect(self._installation_error)
        process.start(str(ROOT / 'KaraMorph.exe'), ['--setup', '--no-launch'])

    def _installation_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.centralWidget().setEnabled(True)
            QMessageBox.warning(self, t('管理安裝'), self.install_process.errorString())

    def _installation_finished(self, *args):
        self.centralWidget().setEnabled(True)
        updated = load_settings()
        selected = updated.get('ui_language', language())
        self.settings['ui_language'] = selected
        self.ui_language.blockSignals(True)
        self.ui_language.setCurrentIndex(self.ui_language.findData(selected))
        self.ui_language.blockSignals(False)
        if selected != language():
            QMessageBox.information(self, t('介面語言'), t('介面語言已儲存；請重新啟動程式以套用。'))

    def open_models(self):
        if self.installation_active():
            QMessageBox.information(self, t('管理安裝'), t('安裝管理中，請先關閉安裝視窗。'))
            return
        from app.models_dialog import ModelsDialog
        if self.models_dialog is None:
            self.models_dialog = ModelsDialog(self)
        self.models_dialog.refresh()
        self.models_dialog.show()
        self.models_dialog.raise_()

    def _button(self, text, callback, primary=False):
        button = QPushButton(text)
        button.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        if primary:
            button.setObjectName("primary")
        button.clicked.connect(callback)
        return button

    def _build_sing_page(self):
        layout = QVBoxLayout(self.sing_page)
        title = QLabel(t('今天想唱的歌'))
        title.setStyleSheet("font-size: 24px; font-weight: bold; padding: 8px 0;")
        layout.addWidget(title)
        self.now_playing = QLabel(t('從「處理歌曲」加入歌曲，開始你的歌單'))
        self.now_playing.setStyleSheet("font-size: 17px; padding: 12px; background: white; border-radius: 8px;")
        layout.addWidget(self.now_playing)
        self.queue = QListWidget()
        self.queue.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.queue.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.queue.setIconSize(QPixmap(48, 48).size())
        self.queue.itemDoubleClicked.connect(lambda _: self.play_selected())
        layout.addWidget(self.queue, 1)
        controls = QHBoxLayout()
        for label, action in ((t('上一首'), self.previous_song), (t('播放'), self.play_selected),
                              (t('暫停／繼續'), self.toggle_pause), (t('下一首'), self.next_song),
                              (t('移出歌單'), self.remove_queue_item)):
            controls.addWidget(self._button(label, action, label == t('播放')))
        layout.addLayout(controls)
        self.record_checkbox = QPushButton(t('整首錄音'))
        self.record_checkbox.setIcon(icon("record", "#b74754"))
        self.record_checkbox.setCheckable(True)
        self.record_checkbox.setAccessibleName(t('整首錄音：關'))
        self.record_checkbox.setToolTip(t('播放前開啟：每首歌獨立錄下麥克風歌聲；播放中不能切換。'))
        self.record_checkbox.setStyleSheet("QPushButton:checked { background: #ffe1e5; border: 2px solid #bc4656; color: #8a2036; }")
        self.record_checkbox.setChecked(False)
        self.record_checkbox.toggled.connect(self._record_choice_changed)
        controls.insertWidget(1, self.record_checkbox)
        layout.addWidget(self._button(t('停止播放'), self.stop_playback))
        volume_row = QHBoxLayout()
        volume_row.addWidget(QLabel(t('音量')))
        volume = QSlider(Qt.Orientation.Horizontal)
        volume.setRange(0, 100)
        volume.setValue(80)
        volume.valueChanged.connect(self._set_singing_volume)
        self.sing_volume = volume
        volume_row.addWidget(volume)
        layout.addLayout(volume_row)
        files = QHBoxLayout()
        files.addWidget(self._button(t('儲存歌單'), self.save_queue))
        files.addWidget(self._button(t('載入歌單'), self.load_queue))
        files.addStretch()
        layout.addLayout(files)

    def _set_singing_volume(self, value):
        self.audio.setVolume(value / 100)
        if self.sing_volume.value() != value:
            self.sing_volume.setValue(value)
        if self.karaoke_window and self.karaoke_window.music_volume.value() != value:
            self.karaoke_window.music_volume.setValue(value)

    def _set_guide_volume(self, value):
        self.guide_audio.setVolume(value / 100)

    def _build_process_page(self):
        layout = QVBoxLayout(self.process_page)
        title = QLabel(t('處理歌曲'))
        title.setStyleSheet("font-size: 24px; font-weight: bold; padding: 8px 0;")
        layout.addWidget(title)
        self.info = QLabel(t('讀取音樂資料夾中…'))
        layout.addWidget(self.info)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([t('歌曲與版本'), t('狀態'), t('歌詞'), t('試聽'), t('加入唱歌頁'), t('製作版本'), t('素材')])
        self.tree.setColumnWidth(0, 525)
        self.tree.setColumnWidth(1, 140)
        self.tree.setColumnWidth(2, 155)
        self.tree.setColumnWidth(3, 180)
        self.tree.setColumnWidth(4, 130)
        self.tree.setColumnWidth(5, 120)
        self.tree.setColumnWidth(6, 85)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (4, 5, 6):
            self.tree.header().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.tree.setIconSize(QPixmap(54, 54).size())
        self.tree.itemSelectionChanged.connect(self._update_selection)
        self.tree.itemChanged.connect(lambda *_: self._update_selection())
        self.tree.itemClicked.connect(self._tree_clicked)
        self.tree.itemExpanded.connect(lambda item: self.expanded_song_ids.add(item.data(0, Qt.ItemDataRole.UserRole)) if item.parent() is None else None)
        self.tree.itemCollapsed.connect(lambda item: self.expanded_song_ids.discard(item.data(0, Qt.ItemDataRole.UserRole)) if item.parent() is None else None)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        actions = QHBoxLayout()
        self.select_all = QCheckBox(t('全選'))
        self.select_all.setTristate(True)
        self.select_all.clicked.connect(self._select_all_songs)
        actions.addWidget(self.select_all)
        self.selected_count = QLabel(t('尚未勾選歌曲'))
        actions.addWidget(self.selected_count)
        actions.addStretch()
        self.reload_library = self._button(t('重新讀取'), self.scan_library)
        actions.addWidget(self.reload_library)
        actions.addWidget(self._button(t('批次分離人聲'), self.batch_separate))
        actions.addWidget(self._button(t('批次抓取歌詞'), self.batch_lyrics))
        actions.addWidget(self._button(t('批次風格轉換'), lambda: self.open_version_dialog(self._selected_for_batch())))
        actions.addWidget(self._button(t('重新嘗試失敗項'), self.retry_failed))
        layout.addLayout(actions)
        layout.addWidget(self.tree, 1)

        self.version_dialog = QDialog(self)
        self.version_dialog.setWindowTitle(t('製作新版本'))
        self.version_dialog.resize(470, 350)
        dialog_layout = QVBoxLayout(self.version_dialog)
        self.version_targets_label = QLabel()
        dialog_layout.addWidget(self.version_targets_label)
        options = QWidget(self.version_dialog)
        form = QFormLayout(options)
        self.speed = QDoubleSpinBox()
        self.speed.setRange(0.5, 2.0)
        self.speed.setSingleStep(0.05)
        self.speed.setValue(1.0)
        self.speed.setSuffix(t(' 倍'))
        form.addRow(t('播放速度'), self.speed)
        self.key = QSpinBox()
        self.key.setRange(-12, 12)
        self.key.setSuffix(t(' 半音'))
        form.addRow(t('升降 Key'), self.key)
        self.style = QComboBox()
        for name in PRESETS:
            self.style.addItem(preset_label(name), name)
        self.style.addItem(t('自訂…'), 'custom')
        self.style.currentTextChanged.connect(self._style_changed)
        form.addRow(t('風格'), self.style)
        self.custom_style = QLineEdit()
        self.custom_style.setPlaceholderText(t('描述想要的樂器與氣氛'))
        self.custom_style.hide()
        form.addRow(t('自訂描述'), self.custom_style)
        level_row = QWidget()
        level_layout = QVBoxLayout(level_row)
        level_layout.setContentsMargins(0, 0, 0, 0)
        self.level = QSlider(Qt.Orientation.Horizontal)
        self.level.setRange(0, 2)
        self.level.setSingleStep(1)
        self.level.setPageStep(1)
        self.level.setValue(1)
        self.level.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.level.setTickInterval(1)
        level_layout.addWidget(self.level)
        markers = QHBoxLayout()
        for label in LEVELS:
            markers.addWidget(QLabel(t(label)))
            if label != '明顯':
                markers.addStretch()
        level_layout.addLayout(markers)
        form.addRow(t('風格變化'), level_row)
        dialog_layout.addWidget(options)
        bottom = QHBoxLayout()
        bottom.addWidget(self._button(t('只調整速度／Key'), lambda: self._submit_version_dialog("fx")))
        bottom.addWidget(self._button(t('製作風格版本'), lambda: self._submit_version_dialog("style"), True))
        bottom.addWidget(self._button(t('取消'), self.version_dialog.reject))
        bottom.addStretch()
        dialog_layout.addLayout(bottom)
        self.version_dialog.finished.connect(self._remember_version_options)
        self._load_version_options()
        work_row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFixedWidth(130)
        self.process_status = QLabel(t('準備就緒'))
        self.process_status.setWordWrap(True)
        work_row.addWidget(self.process_status, 1)
        work_row.addWidget(self.progress)
        self.cancel_work = self._button(t('取消處理'), self.cancel_batch)
        self.cancel_work.setEnabled(False)
        work_row.addWidget(self.cancel_work)
        layout.addLayout(work_row)
        preview_box = QGroupBox(t('目前試聽'))
        preview_layout = QVBoxLayout(preview_box)
        self.preview_title = QLabel(t('尚未試聽'))
        preview_layout.addWidget(self.preview_title)
        timeline = QHBoxLayout()
        self.preview_elapsed = QLabel("0:00")
        self.preview_total = QLabel("0:00")
        self.preview_seek = QSlider(Qt.Orientation.Horizontal)
        self.preview_seek.setRange(0, 0)
        self.preview_seek.setEnabled(False)
        self.preview_seek.sliderMoved.connect(lambda value: self.preview_elapsed.setText(self._time_label(value)))
        self.preview_seek.sliderReleased.connect(self._preview_drag_end)
        timeline.addWidget(self.preview_elapsed)
        timeline.addWidget(self.preview_seek, 1)
        timeline.addWidget(self.preview_total)
        preview_layout.addLayout(timeline)
        levels = QHBoxLayout()
        for label, name in ((t('音樂'), "backing"), (t('人聲'), "vocal")):
            levels.addWidget(QLabel(label))
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, 100)
            slider.setValue(70)
            slider.valueChanged.connect(lambda value, channel=name: self._preview_gain(channel, value))
            levels.addWidget(slider, 1)
            setattr(self, f"preview_{name}_volume", slider)
        preview_layout.addLayout(levels)
        layout.addWidget(preview_box)
        self.preview_player = QMediaPlayer(self)
        self.preview_audio = QAudioOutput(self)
        self.preview_player.setAudioOutput(self.preview_audio)
        self.preview_audio.setVolume(0.7)
        self.preview_player.positionChanged.connect(lambda _: self._update_preview_progress())
        self.preview_player.durationChanged.connect(lambda _: self._update_preview_progress())
        self.preview_player.mediaStatusChanged.connect(self._preview_media_status)
        self.preview_player.errorOccurred.connect(self._preview_error)
        self.record_backing_player = QMediaPlayer(self)
        self.record_backing_audio = QAudioOutput(self)
        self.record_backing_player.setAudioOutput(self.record_backing_audio)
        self.record_backing_audio.setVolume(0.7)
        self.record_backing_player.mediaStatusChanged.connect(self._record_backing_ready)
        self.record_preview = None
        self.record_preview_check = QCheckBox(t('錄音試聽加入伴奏'))
        self.record_preview_check.setChecked(True)
        self.record_preview_check.toggled.connect(self._record_preview_option_changed)
        preview_layout.addWidget(self.record_preview_check)
        offset_row = QHBoxLayout()
        offset_row.addWidget(QLabel(t('錄音對齊 (毫秒)')))
        self.record_offset = QSpinBox()
        self.record_offset.setRange(-3000, 3000)
        self.record_offset.setSingleStep(50)
        self.record_offset.valueChanged.connect(self._record_preview_option_changed)
        offset_row.addWidget(self.record_offset)
        preview_layout.addLayout(offset_row)
        self.preview_timer = QTimer(self)
        self.preview_timer.setInterval(100)
        self.preview_timer.timeout.connect(self._update_preview_progress)

    def _build_settings_page(self):
        layout = QVBoxLayout(self.settings_page)
        title = QLabel(t('設定'))
        title.setStyleSheet("font-size: 24px; font-weight: bold; padding: 8px 0;")
        layout.addWidget(title)
        form = QFormLayout()
        layout.addLayout(form)
        self.source_field = QLineEdit(self.settings.get("source_dir", ""))
        self.output_field = QLineEdit(self.settings.get("workspace", ""))
        for label, field, picker in ((t('音樂資料夾'), self.source_field, self.choose_source),
                                     (t('處理結果資料夾'), self.output_field, self.choose_output)):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.addWidget(field)
            row_layout.addWidget(self._button(t('選擇…'), picker))
            form.addRow(label, row)
        self.background_mode = QComboBox()
        for label, value in ((t('只用歌曲影像'), "song_first"),
                             (t('使用共用影像 + 目錄影像'), "combined"),
                             (t('只使用共用影像'), "shared_only")):
            self.background_mode.addItem(label, value)
        self.background_mode.setCurrentIndex(max(0, self.background_mode.findData(self.settings.get("background_mode", "combined"))))
        self.background_mode.setToolTip(t('歌曲影像包含音樂檔封面與素材 images 資料夾圖片；沒有可用影像時顯示 no images。預設包含共用影像。'))
        form.addRow(t('唱歌背景影像'), self.background_mode)
        self.background_motion = QCheckBox(t('背景微動態'))
        self.background_motion.setChecked(bool(self.settings.get('background_motion', True)))
        self.background_motion.setToolTip(t('輕微縮放與移動，保留完整圖片；若音訊不穩可關閉比較。'))
        form.addRow(self.background_motion)
        self.advanced_fields = {}
        self.runtime_path_status = QLabel(t('執行元件與模型由模型管理頁檢查。'))
        self.runtime_path_status.setWordWrap(True)
        layout.addWidget(self._button(t('模型與元件管理'), self.open_models))
        if (ROOT / 'bootstrap-launcher.json').is_file():
            layout.addWidget(self._button(t('管理安裝'), self.open_install_manager))
        layout.addWidget(self.runtime_path_status)
        self.audio_device = QComboBox()
        self.audio_device.addItem(t('系統預設輸出'), None)
        saved_device = self.settings.get("audio_device", "")
        for device in QMediaDevices.audioOutputs():
            self.audio_device.addItem(device.description(), device)
            if base64.b64encode(bytes(device.id())).decode("ascii") == saved_device:
                self.audio_device.setCurrentIndex(self.audio_device.count() - 1)
                self.audio.setDevice(device)
                self.guide_audio.setDevice(device)
                self.preview_audio.setDevice(device)
                self.stem_preview.set_device(device)
        output_row = QWidget()
        output_layout = QHBoxLayout(output_row)
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.addWidget(self.audio_device, 1)
        output_layout.addWidget(self._button(t('測試播放'), self.test_audio_output))
        form.addRow(t('播放裝置'), output_row)
        self.microphone_device = QComboBox()
        self.microphone_device.addItem(t('系統預設麥克風'), None)
        saved_microphone = self.settings.get("microphone_device", "")
        for device in QMediaDevices.audioInputs():
            self.microphone_device.addItem(device.description(), device)
            if base64.b64encode(bytes(device.id())).decode("ascii") == saved_microphone:
                self.microphone_device.setCurrentIndex(self.microphone_device.count() - 1)
        microphone_row = QWidget()
        microphone_layout = QHBoxLayout(microphone_row)
        microphone_layout.setContentsMargins(0, 0, 0, 0)
        microphone_layout.addWidget(self.microphone_device, 1)
        microphone_layout.addWidget(self._button(t('測試麥克風'), self.test_microphone))
        form.addRow(t('麥克風'), microphone_row)
        self.singing_audio_mode = QComboBox()
        self.singing_audio_mode.addItem(t('WASAPI 獨占（低延遲，預設）'), "exclusive")
        self.singing_audio_mode.addItem(t('Qt 共用（相容模式）'), "shared")
        self.singing_audio_mode.setCurrentIndex(1 if self.settings.get("singing_audio_mode", "exclusive") == "shared" else 0)
        form.addRow(t('唱歌音訊模式'), self.singing_audio_mode)
        audio_note = QLabel(t('獨占會將伴奏、導唱與麥克風一起播放；設備需允許獨占。切換模式前請停止唱歌。'))
        audio_note.setWordWrap(True)
        form.addRow(audio_note)
        self.device_test_status = QLabel(t('選擇裝置後按測試；播放測試會發出短提示音。'))
        self.device_test_status.setWordWrap(True)
        form.addRow(t('裝置測試'), self.device_test_status)
        self.audio_device.currentIndexChanged.connect(self._apply_output_device)
        self._apply_output_device()
        layout.addWidget(self._button(t('儲存設定'), self.save_settings, True))
        layout.addStretch()

    def _style_changed(self, name):
        self.custom_style.setVisible(self.style.currentData() == 'custom')

    def _apply_output_device(self, *_):
        device = self.audio_device.currentData() or QMediaDevices.defaultAudioOutput()
        # A running exclusive stream keeps its endpoint until stopped.
        for output in (self.audio, self.guide_audio, self.preview_audio, self.record_backing_audio):
            output.setDevice(device)
        try:
            self.stem_preview.set_device(device)
        except Exception as error:
            self._stop_preview()
            self.process_status.setText(t('切換試聽裝置失敗：{p0}', p0=error))

    def level_text(self):
        return list(LEVELS)[self.level.value()]

    def choose_source(self):
        folder = QFileDialog.getExistingDirectory(self, t('選擇音樂資料夾'), self.source_field.text() or str(ROOT / "input"))
        if folder:
            self.source_field.setText(folder)

    def choose_output(self):
        folder = QFileDialog.getExistingDirectory(self, t('選擇處理結果資料夾'), self.output_field.text() or str(ROOT / "outputs"))
        if folder:
            self.output_field.setText(folder)

    def test_microphone(self):
        if self.playing_item or self.microphone.source:
            self.device_test_status.setText(t('請先停止唱歌，再測試麥克風。'))
            return
        device = self.microphone_device.currentData() or QMediaDevices.defaultAudioInput()
        if self.singing_audio_mode.currentData() == "exclusive":
            self.device_test_status.setText(t('正在測試 WASAPI 獨占麥克風：{p0}，請說話…', p0=device.description()))
            QApplication.processEvents()
            try:
                peak, rate, dtype = test_exclusive_device(device, 'input')
                self.device_test_status.setText(t('獨占麥克風已開啟：{p0} Hz/{p1}，峰值 {p2:.2f}。', p0=rate, p1=dtype, p2=peak) + (t('未偵測到明顯聲音，請檢查輸入音量。') if peak < 0.001 else ""))
            except Exception as error:
                self.device_test_status.setText(str(error))
            return
        probe = MicrophoneService(self)
        levels = []
        probe.levelChanged.connect(lambda level: levels.append(level))
        self.device_test_status.setText(t('正在測試麥克風：{p0}，請說話…', p0=device.description()))
        QApplication.processEvents()
        try:
            probe.start(device)
            loop = QEventLoop()
            QTimer.singleShot(900, loop.quit)
            loop.exec()
            if probe.source and probe.source.error() != QtAudio.Error.NoError:
                raise RuntimeError(t('錄音中斷：{p0}', p0=probe.source.error().name))
            peak = max(levels, default=0)
            self.device_test_status.setText(
                t('麥克風「{p0}」已開啟；偵測到聲音（最高 {p1}%）。', p0=device.description(), p1=peak) if peak else
                t('麥克風「{p0}」已開啟，但未偵測到聲音；請說話並檢查系統輸入音量。', p0=device.description()))
        except Exception as error:
            self.device_test_status.setText(t('麥克風測試失敗：{p0}', p0=error))
        finally:
            probe.stop()
            probe.deleteLater()

    def test_audio_output(self):
        if self.playing_item:
            self.device_test_status.setText(t('請先停止唱歌，再測試播放裝置。'))
            return
        device = self.audio_device.currentData() or QMediaDevices.defaultAudioOutput()
        if device.isNull():
            self.device_test_status.setText(t('播放測試失敗：找不到輸出裝置。'))
            return
        if self.singing_audio_mode.currentData() == "exclusive":
            self.device_test_status.setText(t('正在測試 WASAPI 獨占播放：{p0}…', p0=device.description()))
            QApplication.processEvents()
            try:
                _, rate, dtype = test_exclusive_device(device, 'output')
                self.device_test_status.setText(t('獨占播放已開啟：{p0} Hz/{p1}；請確認聽到提示音。', p0=rate, p1=dtype))
            except Exception as error:
                self.device_test_status.setText(str(error))
            return
        fmt = QAudioFormat()
        fmt.setSampleRate(48000)
        fmt.setChannelCount(1)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        if not device.isFormatSupported(fmt):
            fmt = device.preferredFormat()
        rate, channels = fmt.sampleRate(), fmt.channelCount()
        tone_times = np.arange(max(1, round(rate * 0.5)), dtype=np.float32) / rate
        tone = (np.sin(2 * np.pi * 440 * tone_times) * 0.15).astype(np.float32)
        samples = np.repeat(tone[:, None], channels, axis=1)
        encoding = fmt.sampleFormat()
        if encoding == QAudioFormat.SampleFormat.Int16:
            payload = (samples * 32767).astype("<i2").tobytes()
        elif encoding == QAudioFormat.SampleFormat.Int32:
            payload = (samples * 2147483647).astype("<i4").tobytes()
        elif encoding == QAudioFormat.SampleFormat.Float:
            payload = samples.astype("<f4").tobytes()
        elif encoding == QAudioFormat.SampleFormat.UInt8:
            payload = (samples * 127 + 128).astype("u1").tobytes()
        else:
            self.device_test_status.setText(t('播放測試失敗：輸出裝置的音訊格式不受支援。'))
            return
        buffer = QBuffer(QByteArray(payload), self)
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        sink = QAudioSink(device, fmt, self)
        self.device_test_status.setText(t('正在測試播放：{p0}…', p0=device.description()))
        QApplication.processEvents()
        try:
            sink.start(buffer)
            loop = QEventLoop()
            QTimer.singleShot(650, loop.quit)
            loop.exec()
            if sink.error() != QtAudio.Error.NoError:
                raise RuntimeError(sink.error().name)
            self.device_test_status.setText(t('播放裝置「{p0}」已開啟並送出提示音；請確認有聽到聲音。', p0=device.description()))
        except Exception as error:
            self.device_test_status.setText(t('播放測試失敗：{p0}', p0=error))
        finally:
            sink.stop()
            sink.deleteLater()
            buffer.close()
            buffer.deleteLater()

    def _language_changed(self, *_):
        previous = self.settings.get('ui_language', language())
        selected = self.ui_language.currentData()
        if selected == previous:
            return
        updated = dict(self.settings, ui_language=selected)
        try:
            save_settings(updated)
        except Exception as error:
            self.ui_language.blockSignals(True)
            self.ui_language.setCurrentIndex(self.ui_language.findData(previous))
            self.ui_language.blockSignals(False)
            QMessageBox.warning(self, t('儲存失敗'), str(error))
            return
        self.settings['ui_language'] = selected
        QMessageBox.information(self, t('介面語言'), t('介面語言已儲存；請重新啟動程式以套用。'))

    def save_settings(self):
        if self.installation_active():
            QMessageBox.information(self, t('管理安裝'), t('安裝管理中，請先關閉安裝視窗。'))
            return
        if self.playing_item:
            QMessageBox.warning(self, t('設定'), t('請先停止唱歌，再儲存音訊設定。'))
            return
        source_text, output_text = self.source_field.text().strip(), self.output_field.text().strip()
        if not source_text or not Path(source_text).is_dir() or not output_text:
            QMessageBox.warning(self, t('設定'), t('請選擇有效的音樂資料夾與處理結果資料夾'))
            return
        source, output = Path(source_text).resolve(), Path(output_text).expanduser().resolve()
        if output == source or source in output.parents:
            QMessageBox.warning(self, t('設定'), t('處理結果資料夾不能放在音樂資料夾內'))
            return
        changed = str(source) != self.settings.get("source_dir") or output != self.workspace
        self.settings.update({"source_dir": str(source), "workspace": str(output)})
        self.settings.update({key: field.text().strip() for key, field in self.advanced_fields.items()})
        issues = migration_issues(self.settings)
        self.runtime_path_status.setText(t('路徑設定需要遷移：\n') + "\n".join(issues) if issues else t('模型與元件可透過模型管理準備。'))
        device = self.audio_device.currentData()
        self.settings["audio_device"] = base64.b64encode(bytes(device.id())).decode("ascii") if device else ""
        mic = self.microphone_device.currentData()
        self.settings["microphone_device"] = base64.b64encode(bytes(mic.id())).decode("ascii") if mic else ""
        self.settings["singing_audio_mode"] = self.singing_audio_mode.currentData()
        self.settings["background_mode"] = self.background_mode.currentData()
        self.settings['background_motion'] = self.background_motion.isChecked()
        self.settings['ui_language'] = self.ui_language.currentData()
        self._apply_output_device()
        save_settings(self.settings)
        self.workspace = output
        self.info.setText(t('設定已儲存'))
        if self.settings['ui_language'] != language():
            self.info.setText(t('設定已儲存；介面語言將在重新啟動後生效。'))
        if self.karaoke_window is not None:
            self.karaoke_window.set_background_motion(self.settings['background_motion'])
        if changed:
            self.scan_library()
        message = (t('設定已儲存；介面語言將在重新啟動後生效。')
                   if self.settings['ui_language'] != language() else t('設定已儲存'))
        QMessageBox.information(self, t('設定'), message)

    def scan_library(self):
        if getattr(self, "scan_task", None) is not None:
            return
        source = Path(self.settings.get("source_dir") or "")
        if not source.is_dir():
            self.info.setText(t('音樂資料夾不存在，請到設定重新選擇'))
            return
        if source.resolve() == self.workspace or source.resolve() in self.workspace.parents:
            self.info.setText(t('請將處理結果資料夾移出音樂資料夾'))
            return
        self.info.setText(t('正在整理歌曲…'))
        task = LibraryScan(source)
        task.signals.done.connect(self._scan_done)
        task.signals.error.connect(self._scan_failed)
        self.scan_task = task
        self.reload_library.setEnabled(False)
        QThreadPool.globalInstance().start(task)

    def _scan_failed(self, message):
        self.scan_task = None
        self.reload_library.setEnabled(True)
        self.info.setText(t('讀取失敗：{p0}', p0=message))

    def _scan_done(self, source_path, songs):
        self.scan_task = None
        self.reload_library.setEnabled(True)
        if source_path != str(Path(self.settings.get("source_dir") or "").resolve()):
            self.scan_library()
            return
        self.songs = merge_library(Path(source_path), songs)
        self.song_map = {song["id"]: song for song in self.songs}
        self.info.setText(t('找到 {p0} 首歌曲 · 勾選歌曲後可批次處理', p0=len(songs)))
        self.refresh_tree()
        self.refresh_queue_labels()

    def refresh_tree(self):
        view = self._capture_tree_view()
        checked = {self.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
                   for i in range(self.tree.topLevelItemCount())
                   if self.tree.topLevelItem(i).checkState(0) == Qt.CheckState.Checked}
        self.tree.blockSignals(True)
        self.tree.clear()
        self.preview_checks = {}
        self.preview_buttons = {}
        for song in self.songs:
            song_id = song["id"]
            catalog = load_catalog(self.workspace, song_id)
            count = len(catalog.get("variants", []))
            state = self.job_state.get(song_id)
            summary = state or (t('✓ 有伴奏') if catalog.get("separation") else t('○ 尚未分離'))
            if count:
                summary += t('  ·  ♪ {p0} 個版本', p0=count)
            display_name = song["title"] + (f"  —  {song['artist']}" if song.get("artist") else "")
            lyrics_label, lyrics_hint = lyric_status(self.workspace, song_id)
            root = QTreeWidgetItem(self.tree, [display_name, summary, lyrics_label, "", "", ""])
            root.setToolTip(2, lyrics_hint)
            root.setIcon(0, cover_icon(song.get("cover_path")))
            root.setToolTip(0, " · ".join(x for x in (song.get("artist"), song.get("album"), song["path"]) if x))
            root.setData(0, Qt.ItemDataRole.UserRole, song_id)
            root.setFlags(root.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            root.setCheckState(0, Qt.CheckState.Checked if song_id in checked else Qt.CheckState.Unchecked)
            if has_lrclib(self.workspace, song_id):
                root.setText(2, lyrics_label + " · 🌐")
                root.setToolTip(2, lyrics_hint + t('\n已有 LRCLIB 下載結果，可在素材切換來源。'))
            self.tree.setItemWidget(root, 5, self._compact_button(t('製作版本'), lambda _, sid=song_id: self.open_version_dialog([sid]), 110))
            self.tree.setItemWidget(root, 6, self._compact_button(t('素材'), lambda _, sid=song_id: self.edit_song_assets(sid), 75))
            if state:
                root.setForeground(1, QColor("#176da9" if "⚠" not in state else "#b54c25"))
                font = root.font(1)
                font.setBold(True)
                root.setFont(1, font)
            if not song.get("available", True):
                root.setText(1, t('⚠ 來源檔案無法使用'))
            else:
                self.tree.setItemWidget(root, 3, self._preview_widget(song_id, "original", False))
                self.tree.setItemWidget(root, 4, self._compact_button(t('加入原曲'), lambda _, sid=song_id: self.add_to_queue(sid, "original"), 120))
            self._add_version(root, song, "original", t('原曲'), Path(song["path"]))
            separation = catalog.get("separation")
            if separation:
                self._add_version(root, song, "instrumental", t('純伴奏'), Path(separation["manifest"]["stems"]["instrumental"]))
            variants_by_id = {item["id"]: item for item in catalog.get("variants", [])}
            for variant in catalog.get("variants", []):
                self._add_version(root, song, variant["id"], version_label(variant, variants_by_id), Path(variant["audio_path"]))
            recordings = load_recordings(self.workspace, song_id)
            if recordings:
                group = QTreeWidgetItem(root, [t('錄音 ({p0})', p0=len(recordings)), "", "—", "", "", ""])
                group.setData(0, Qt.ItemDataRole.UserRole, ("recordings", song_id))
                labels = {"original": t('原曲'), "instrumental": t('純伴奏')}
                labels.update({variant["id"]: version_label(variant, variants_by_id)
                               for variant in catalog.get("variants", [])})
                for index, recording in enumerate(recordings, 1):
                    version = labels.get(recording.get("variant_id"), t('原版本已遺失'))
                    take = QTreeWidgetItem(group, [t('錄音 {p0} · {p1}', p0=index, p1=recording.get('started_at', '')[:16]),
                                                   version, "—", "", "", ""])
                    take.setData(0, Qt.ItemDataRole.UserRole, ("recording", song_id, recording["session_id"]))
                    self.tree.setItemWidget(take, 3, self._button(t('試聽'), lambda _, sid=song_id, rid=recording["session_id"]: self.preview_recording(sid, rid)))
            root.setExpanded(song_id in self.expanded_song_ids)
        self.tree.blockSignals(False)
        self._restore_tree_view(view)
        self._update_selection()

    def _compact_button(self, text, callback, width):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        button = self._button(text, callback)
        button.setMinimumWidth(width)
        button.setFixedHeight(32)
        layout.addWidget(button)
        layout.addStretch()
        return row

    def _tree_items(self):
        iterator = QTreeWidgetItemIterator(self.tree)
        items = []
        while iterator.value():
            items.append(iterator.value())
            iterator += 1
        return items

    def _capture_tree_view(self):
        items = self._tree_items()
        key = lambda item: item.data(0, Qt.ItemDataRole.UserRole) if item else None
        current = self.tree.currentItem()
        top = self.tree.itemAt(QPoint(3, 1))
        siblings = []
        if current:
            parent = current.parent()
            siblings = ([parent.child(i) for i in range(parent.childCount())] if parent else
                        [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())])
            index = siblings.index(current)
            siblings = siblings[index+1:] + list(reversed(siblings[:index]))
            if parent:
                siblings.append(parent)
        return {"current": key(current), "neighbors": [key(item) for item in siblings],
                "top": key(top), "offset": self.tree.visualItemRect(top).top() if top else 0,
                "vertical": self.tree.verticalScrollBar().value(),
                "horizontal": self.tree.horizontalScrollBar().value(),
                "expanded": {key(item) for item in items if item.isExpanded()}}

    def _restore_tree_view(self, view):
        items = {item.data(0, Qt.ItemDataRole.UserRole): item for item in self._tree_items()}
        for key in view["expanded"]:
            if key in items:
                items[key].setExpanded(True)
        current = items.get(view["current"])
        if current is None:
            current = next((items[key] for key in view["neighbors"] if key in items), None)
        if current:
            self.tree.setCurrentItem(current)
        self.tree.doItemsLayout()
        top = items.get(view["top"])
        if top:
            self.tree.scrollToItem(top, QAbstractItemView.ScrollHint.PositionAtTop)
            self.tree.verticalScrollBar().setValue(self.tree.verticalScrollBar().value() - view["offset"])
        else:
            self.tree.verticalScrollBar().setValue(view["vertical"])
        self.tree.horizontalScrollBar().setValue(view["horizontal"])

    def _add_version(self, parent, song, variant_id, label, path):
        available = path.is_file() and (variant_id != "original" or song.get("available", True))
        item = QTreeWidgetItem(parent, [label, t('✓ 可播放') if available else t('⚠ 檔案遺失'), parent.text(2), "", "", ""])
        item.setToolTip(2, parent.toolTip(2))
        item.setData(0, Qt.ItemDataRole.UserRole, (song["id"], variant_id))
        item.setToolTip(0, label)
        if available:
            _, vocal = self._preview_paths(song["id"], variant_id)
            self.tree.setItemWidget(item, 3, self._preview_widget(song["id"], variant_id, bool(vocal)))
            button = self._compact_button(t('加入歌單'), lambda _, sid=song["id"], vid=variant_id: self.add_to_queue(sid, vid), 120)
            self.tree.setItemWidget(item, 4, button)

    def _preview_widget(self, song_id, variant_id, has_vocal):
        row = QWidget()
        controls = QHBoxLayout(row)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(4)
        check = QCheckBox(t('人聲'))
        check.setEnabled(has_vocal)
        check.setToolTip(t('原曲已包含人聲') if variant_id == "original" else t('試聽時混入這個版本對應的人聲'))
        key = (song_id, variant_id)
        check.setChecked(bool(self.preview_vocal_choices.get(key, False) and has_vocal))
        check.toggled.connect(lambda checked, item_key=key: self._preview_vocal_changed(item_key, checked))
        self.preview_checks[key] = check
        button = self._button(t('停止') if self.current_preview_key == key else t('試聽'),
                              lambda _, sid=song_id, vid=variant_id: self.preview_version(sid, vid))
        button.setFixedHeight(32)
        self.preview_buttons.setdefault(key, []).append(button)
        controls.addWidget(button)
        controls.addWidget(check)
        return row

    def _tree_clicked(self, item, column):
        if item.parent() is None and column not in {3, 4, 5, 6}:
            item.setExpanded(not item.isExpanded())

    def _context_menu(self, point):
        item = self.tree.itemAt(point)
        if not item:
            return
        self.tree.setCurrentItem(item)
        key = item.data(0, Qt.ItemDataRole.UserRole)
        menu = QMenu(self.tree)
        if isinstance(key, str):
            song_id = key
            assets = menu.addAction(t('編輯歌詞與背景素材'))
            assets.triggered.connect(lambda: self.edit_song_assets(song_id))
            add = menu.addAction(t('加入原曲到歌單'))
            add.triggered.connect(lambda: self.add_to_queue(song_id, "original"))
            preview = menu.addAction(t('試聽原曲'))
            preview.triggered.connect(self.toggle_preview)
            catalog = load_catalog(self.workspace, song_id)
            if not catalog.get("separation"):
                separate = menu.addAction(t('分離人聲'))
                separate.triggered.connect(lambda: self._submit([song_id], "separate"))
            if catalog.get("separation") or catalog.get("variants"):
                menu.addSeparator()
                remove = menu.addAction(t('刪除這首歌的處理結果…'))
                remove.triggered.connect(lambda: self.delete_song_results(song_id))
        elif isinstance(key, tuple) and key[0] == "recording":
            _, song_id, session_id = key
            menu.addAction(t('試聽錄音'), lambda: self.preview_recording(song_id, session_id))
            menu.addAction(t('刪除錄音…'), lambda: self.remove_recording(song_id, session_id))
        elif isinstance(key, tuple) and key[0] == "recordings":
            return
        else:
            song_id, variant_id = key
            preview = menu.addAction(t('試聽這個版本'))
            preview.triggered.connect(self.toggle_preview)
            add = menu.addAction(t('加入唱歌頁'))
            add.triggered.connect(lambda: self.add_to_queue(song_id, variant_id))
            if variant_id not in {"original", "instrumental"}:
                menu.addSeparator()
                remove = menu.addAction(t('刪除這個版本…'))
                remove.triggered.connect(lambda: self.delete_version(song_id, variant_id))
        menu.exec(self.tree.viewport().mapToGlobal(point))

    def edit_song_assets(self, song_id):
        from app.lyrics_dialog import LyricsDialog
        try:
            dialog = LyricsDialog(self, song_id)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, t('讀取歌曲素材失敗'), str(error))
            return
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        dialog.deleteLater()
        if accepted:
            self.refresh_tree()
            if self.karaoke_window and self.karaoke_window.isVisible() and self.playing_item:
                item = self.playing_item.data(Qt.ItemDataRole.UserRole)
                if item["song_id"] == song_id:
                    self._sync_karaoke_song(item)

    def _open_asset_folder(self, folder):
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve()))):
                raise OSError(t('無法開啟資料夾'))
        except OSError as error:
            QMessageBox.warning(self, t('開啟素材資料夾失敗'), str(error))

    def _song_is_processing(self, song_id):
        return ((self.active_op and self.active_op["song_id"] == song_id)
                or any(operation["song_id"] == song_id for operation in self.pending_ops)
                or (self.jobs.current and self.jobs.current["song_id"] == song_id)
                or any(job["song_id"] == song_id for job in self.jobs.queue))

    def _release_audio_files(self):
        self.microphone.stop()
        self.record_checkbox.setEnabled(True)
        self.player.stop()
        self.guide_player.stop()
        self.guide_player.setSource(QUrl())
        self.guide_vocal_path = None
        if self.karaoke_window:
            self.karaoke_window.guide_checkbox.setEnabled(False)
            self.karaoke_window.guide_volume.setEnabled(False)
            self.karaoke_window.guide_checkbox.setChecked(False)
        self._stop_preview()
        self.player.setSource(QUrl())
        self.preview_player.setSource(QUrl())
        QApplication.processEvents()

    def delete_version(self, song_id, variant_id):
        if self._song_is_processing(song_id):
            QMessageBox.warning(self, t('無法刪除'), t('這首歌正在處理，請等待完成或取消工作'))
            return
        catalog = load_catalog(self.workspace, song_id)
        variant = next((row for row in catalog.get("variants", []) if row["id"] == variant_id), None)
        if not variant:
            return
        if any(row.get("source_id") == variant_id for row in catalog["variants"]):
            QMessageBox.warning(self, t('無法刪除'), t('這個版本仍被其他版本使用，請先刪除衍生版本'))
            return
        if QMessageBox.question(self, t('刪除版本'), t('刪除此生成版本？原曲不會刪除，歌單中的項目會標示為無法使用。')) != QMessageBox.StandardButton.Yes:
            return
        base = (song_dir(self.workspace, song_id) / ("fx_jobs" if variant.get("kind") == "fx" else "style_jobs")).resolve()
        audio = Path(variant["audio_path"]).resolve()
        song_root = song_dir(self.workspace, song_id).resolve()
        if song_root.parent != (self.workspace.resolve() / "songs").resolve() or base.parent != song_root or base not in audio.parents:
            QMessageBox.warning(self, t('無法刪除'), t('版本音檔不在此歌曲的處理資料夾'))
            return
        self._release_audio_files()
        try:
            audio.unlink(missing_ok=True)
            if variant.get("kind") == "fx" and variant.get("vocal_path"):
                vocal = Path(variant["vocal_path"]).resolve()
                if base in vocal.parents:
                    vocal.unlink(missing_ok=True)
            (song_dir(self.workspace, song_id) / "preview" / f"consumer_{variant_id}_with_vocal.wav").unlink(missing_ok=True)
        except OSError as error:
            QMessageBox.warning(self, t('刪除失敗'), str(error))
            return
        catalog["variants"] = [row for row in catalog["variants"] if row["id"] != variant_id]
        save_catalog(self.workspace, song_id, catalog)
        self.refresh_tree()
        self.refresh_queue_labels()

    def delete_song_results(self, song_id):
        if self._song_is_processing(song_id):
            QMessageBox.warning(self, t('無法刪除'), t('這首歌正在處理，請等待完成或取消工作'))
            return
        if QMessageBox.question(self, t('刪除處理結果'), t('刪除此歌曲的分離與生成結果？錄音、歌詞、圖片、原曲和歌單會保留。')) != QMessageBox.StandardButton.Yes:
            return
        songs_root = (self.workspace.resolve() / "songs").resolve()
        target = song_dir(self.workspace, song_id).resolve()
        if target.parent != songs_root or target.name != song_id or song_id not in self.song_map:
            QMessageBox.warning(self, t('無法刪除'), t('處理資料夾位置不正確'))
            return
        self._release_audio_files()
        try:
            for name in ("separation_jobs", "fx_jobs", "style_jobs", "preview"):
                child = (target / name).resolve()
                if child.parent != target:
                    raise ValueError(t('處理資料夾位置不正確'))
                if child.is_dir():
                    shutil.rmtree(child)
            save_catalog(self.workspace, song_id, {"separation": None, "variants": []})
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, t('刪除失敗'), str(error))
            return
        self.job_state.pop(song_id, None)
        self.failures.pop(song_id, None)
        self.refresh_tree()
        self.refresh_queue_labels()

    def _update_selection(self):
        selected = self.selected_song_ids()
        self.selected_count.setText(t('已勾選 {p0} 首', p0=len(selected)) if selected else t('尚未勾選歌曲'))
        self.select_all.blockSignals(True)
        self.select_all.setCheckState(Qt.CheckState.Unchecked if not selected else
                                     Qt.CheckState.Checked if len(selected) == self.tree.topLevelItemCount() else
                                     Qt.CheckState.PartiallyChecked)
        self.select_all.blockSignals(False)

    def _select_all_songs(self):
        checked = len(self.selected_song_ids()) != self.tree.topLevelItemCount()
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setCheckState(0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self.tree.blockSignals(False)
        self._update_selection()

    def selected_song_ids(self):
        return [self.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
                for i in range(self.tree.topLevelItemCount())
                if self.tree.topLevelItem(i).checkState(0) == Qt.CheckState.Checked]

    def version_path(self, song_id, variant_id):
        return resolve_item({"song_id": song_id, "variant_id": variant_id, "label": ""}, self.song_map, self.workspace)

    def add_to_queue(self, song_id, variant_id):
        if not self.version_path(song_id, variant_id):
            QMessageBox.warning(self, t('無法加入'), t('這個版本的音檔目前無法使用'))
            return
        song = self.song_map[song_id]
        if variant_id == "original":
            version = t('原曲')
        elif variant_id == "instrumental":
            version = t('純伴奏')
        else:
            catalog = load_catalog(self.workspace, song_id)
            variant = next(v for v in catalog["variants"] if v["id"] == variant_id)
            version = version_label(variant, {item["id"]: item for item in catalog["variants"]})
        label = f"{song['title']} · {version}"
        if song.get("artist"):
            label += f" — {song['artist']}"
        self._append_queue({"song_id": song_id, "variant_id": variant_id, "label": label})

    def queue_display_label(self, item):
        """Translate recognizable generated labels without modifying saved/custom labels."""
        song = self.song_map.get(item['song_id'])
        if not song:
            return item['label']
        variant_id = item['variant_id']
        variants = {}
        if variant_id not in ('original', 'instrumental'):
            variants = {row['id']: row for row in load_catalog(self.workspace, item['song_id']).get('variants', [])}
            if variant_id not in variants:
                return item['label']

        def label(translate):
            version = (translate('原曲') if variant_id == 'original' else translate('純伴奏')
                       if variant_id == 'instrumental' else version_label(variants[variant_id], variants, translate))
            value = f"{song['title']} · {version}"
            return value + (f" — {song['artist']}" if song.get('artist') else '')

        known = [label(lambda source, **values: translate_in(code, source, **values))
                 for _, code in LANGUAGES]
        known.append(label(lambda source, **values: (source if source in PRESETS else
                          translate_in('zh_TW', source, **values))))
        return label(t) if item['label'] in known else item['label']

    def _append_queue(self, item):
        from PySide6.QtWidgets import QListWidgetItem
        row = QListWidgetItem(self.queue_display_label(item))
        song = self.song_map.get(item["song_id"])
        if song:
            row.setIcon(cover_icon(song.get("cover_path")))
        row.setData(Qt.ItemDataRole.UserRole, item)
        self.queue.addItem(row)

    def queue_items(self):
        return [self.queue.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.queue.count())]

    def refresh_queue_labels(self):
        for i in range(self.queue.count()):
            row = self.queue.item(i)
            item = row.data(Qt.ItemDataRole.UserRole)
            missing = self.version_path(item["song_id"], item["variant_id"]) is None
            row.setText((t('⚠ 無法使用 · ') if missing else "") + self.queue_display_label(item))
            if item["song_id"] in self.song_map:
                row.setIcon(cover_icon(self.song_map[item["song_id"]].get("cover_path")))

    def play_selected(self):
        if not self.queue.count():
            return
        self._start_singing_from(max(self.queue.currentRow(), 0))

    def _start_singing_from(self, start):
        self.stop_playback()
        exclusive = self.settings.get("singing_audio_mode", "exclusive") == "exclusive"
        self.player = self.exclusive_player if exclusive else self.qt_player
        self.microphone.exclusive_player = self.exclusive_player if exclusive else None
        self.exclusive_player.music_gain = self.audio.volume()
        self.exclusive_player.guide_gain = self.guide_audio.volume()
        if self.karaoke_window is None:
            self.karaoke_window = KaraokeWindow(self)
        self.karaoke_window.show()
        self.karaoke_window.raise_()
        try:
            self.microphone.start(self.microphone_device.currentData(), self.audio_device.currentData())
        except Exception as error:
            QMessageBox.warning(self, t('麥克風無法使用'), str(error))
        self._play_from(start)

    def _navigate_song(self, start):
        if not self.queue.count():
            return
        if start >= self.queue.count() or (self.playing_item is not None and
                self.karaoke_window is not None and self.karaoke_window.isVisible()):
            self._play_from(start)
        else:
            self._start_singing_from(start)

    def _play_from(self, start):
        self.microphone.finish_recording()
        self.microphone.resume()
        self._stop_preview()
        self.guide_vocal_path = None
        self.guide_player.stop()
        for index in range(start, self.queue.count()):
            item = self.queue.item(index).data(Qt.ItemDataRole.UserRole)
            path = self.version_path(item["song_id"], item["variant_id"])
            if path:
                self.queue.setCurrentRow(index)
                self.playing_item = self.queue.item(index)
                self.now_playing.setText(t('正在播放  {p0}', p0=self.queue_display_label(item)))
                self.player.setSource(QUrl.fromLocalFile(str(path)))
                try:
                    self.player.play()
                except Exception as error:
                    self.stop_playback()
                    QMessageBox.warning(self, t('唱歌音訊無法使用'), str(error))
                    return
                self.record_checkbox.setEnabled(False)
                if self.record_checkbox.isChecked() and self.microphone.source:
                    try:
                        self.microphone.start_recording(self.workspace, item["song_id"], item["variant_id"], path)
                    except Exception as error:
                        QMessageBox.warning(self, t('錄音失敗'), str(error))
                if self.karaoke_window and self.karaoke_window.isVisible():
                    self._sync_karaoke_song(item)
                return
        self.player.stop()
        self.guide_player.stop()
        self.guide_vocal_path = None
        if self.karaoke_window:
            self.karaoke_window.guide_checkbox.setEnabled(False)
            self.karaoke_window.guide_volume.setEnabled(False)
            self.karaoke_window.guide_checkbox.setChecked(False)
        self.playing_item = None
        self.microphone.stop()
        self.record_checkbox.setEnabled(True)
        self.now_playing.setText(t('歌單已播完，或剩餘歌曲無法使用'))
        if self.karaoke_window and self.karaoke_window.isVisible():
            self.karaoke_window.set_song(t('歌單已播完'), "", [], 0)

    def _sync_karaoke_song(self, item):
        song_id, variant_id = item["song_id"], item["variant_id"]
        song = self.song_map.get(song_id, {})
        timeline, state, reason, playback_speed = None, {}, '', 1.0
        lyrics = ''
        try:
            lyrics, timeline, state, reason = load_selected(self.workspace, song_id)
            if timeline:
                playback_speed = effective_speed(variant_id, load_catalog(self.workspace, song_id).get('variants', []))
        except (OSError, ValueError) as error:
            reason = t('歌詞讀取／時間換算失敗：{p0}', p0=error)
            timeline = None
            reason += t('；暫用純文字估算字幕。')
        if reason:
            self.info.setText(reason)
            QMessageBox.warning(self.karaoke_window, t('歌詞無法同步'), reason)
        mode = self.settings.get("background_mode", "combined")
        local = image_files(song_images_dir(self.workspace, song_id))
        shared = image_files(shared_images_dir(self.workspace))
        images = (shared if mode == "shared_only" else local + shared if mode == "combined" else local)
        if mode != "shared_only":
            source = Path(song.get("path", ""))
            if source.is_file():
                cover = cache_background_cover(song_id, source)
                if cover:
                    images.append(cover)
        self.karaoke_window.set_song(self.queue_display_label(item), lyrics, images, self.player.duration(),
            timeline=timeline, playback_speed=playback_speed,
            delay_ms=state.get('offsets', {}).get(variant_id, 0),
            save_delay=lambda value: save_delay(self.workspace, song_id, variant_id, value))
        key = (song_id, variant_id)
        self.current_activity_key = key
        for pending, task in list(self.activity_tasks.items()):
            if pending != key or timeline:
                task.cancel()
                self.activity_tasks.pop(pending, None)
        guide = self._preview_paths(song_id, variant_id)[1] if variant_id != "original" else None
        self._set_guide_source(guide)
        if timeline:
            return
        vocal = self._karaoke_vocal_path(song_id, variant_id)
        if vocal:
            try:
                stat = vocal.stat()
                cache_key = (str(vocal), stat.st_mtime_ns, stat.st_size)
            except OSError:
                return
            cached = self.activity_cache.get(cache_key)
            if cached:
                self.karaoke_window.set_activity(*cached)
                return
            previous = self.activity_tasks.get(key)
            if previous:
                previous.cancel()
            task = ActivityTask(vocal, song_dir(self.workspace, song_id) / 'assets' / 'vocal_activity', self)
            self.activity_tasks[key] = task
            task.signals.ready.connect(lambda _, edges, voiced, selected=key, cache=cache_key:
                self._activity_ready(selected, cache, edges, voiced))
            task.signals.failed.connect(lambda message, selected=key: self._activity_failed(selected, message))
            task.start()

    def _karaoke_vocal_path(self, song_id, variant_id):
        if variant_id != "original":
            return self._preview_paths(song_id, variant_id)[1]
        separation = load_catalog(self.workspace, song_id).get("separation")
        if not separation:
            return None
        path = Path(separation["manifest"]["stems"]["vocals"])
        return path if path.is_file() else None

    def _activity_ready(self, selected, cache_key, edges, voiced):
        self.activity_tasks.pop(selected, None)
        self.activity_cache[cache_key] = (edges, voiced)
        if self.playing_item and selected == self.current_activity_key and self.karaoke_window and self.karaoke_window.isVisible():
            self.karaoke_window.set_activity(edges, voiced)

    def _activity_failed(self, selected, message):
        self.activity_tasks.pop(selected, None)
        if selected == self.current_activity_key:
            self.info.setText(t('人聲分析未完成，暫用歌曲時間捲動。'))
            self.info.setToolTip(message)

    def closeEvent(self, event):
        if self.installation_active():
            QMessageBox.information(self, t('管理安裝'), t('安裝管理中，請先關閉安裝視窗。'))
            event.ignore()
            return
        if self.models_dialog is not None and self.models_dialog.worker is not None:
            self.models_dialog.cancel_work()
            event.ignore()
            return
        self._closing = True
        try:
            save_playlist(data_dir() / "last_playlist.json", self.queue_items())
        except Exception as error:
            QMessageBox.warning(self, t('無法保存上次歌單'), str(error))
        self.lyric_lookup.cancel()
        self.pending_ops.clear()
        self.jobs.cancel()
        for task in list(self.activity_tasks.values()):
            task.cancel()
        self.activity_tasks.clear()
        self.stop_playback()
        self._stop_preview()
        if self.karaoke_window:
            self.karaoke_window.hide()
        super().closeEvent(event)

    def _set_guide_source(self, path):
        self.guide_vocal_path = path
        self.guide_player.stop()
        checkbox = self.karaoke_window.guide_checkbox
        checkbox.setEnabled(bool(path))
        self.karaoke_window.guide_volume.setEnabled(bool(path) and checkbox.isChecked())
        if not path:
            checkbox.setChecked(False)
        self.karaoke_window._update_controls_state()
        if self.player is self.exclusive_player:
            try:
                self.exclusive_player.set_guide(path)
                self.exclusive_player.guide_enabled = bool(path) and checkbox.isChecked()
            except Exception as error:
                checkbox.setChecked(False)
                QMessageBox.warning(self, t('導唱無法使用'), str(error))
            return
        if not path:
            checkbox.setChecked(False)
            self.guide_player.setSource(QUrl())
        elif checkbox.isChecked():
            self.set_guide_vocal(True)

    def set_guide_vocal(self, enabled):
        if self.player is self.exclusive_player:
            self.exclusive_player.guide_enabled = bool(enabled and self.guide_vocal_path)
            return
        if not enabled or not self.guide_vocal_path:
            self.guide_player.stop()
            return
        self.guide_player.setSource(QUrl.fromLocalFile(str(self.guide_vocal_path)))
        self.guide_player.setPosition(self.player.position())
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.guide_player.play()

    def _sync_guide_position(self, position):
        if (self.guide_vocal_path and self.karaoke_window and self.karaoke_window.guide_checkbox.isChecked()
                and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
                and abs(self.guide_player.position() - position) > 250):
            self.guide_player.setPosition(position)

    def _sync_guide_state(self, state):
        if not self.guide_vocal_path or not self.karaoke_window or not self.karaoke_window.guide_checkbox.isChecked():
            return
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.guide_player.play()
            self._sync_guide_position(self.player.position())
        elif state == QMediaPlayer.PlaybackState.PausedState:
            self.guide_player.pause()
        else:
            self.guide_player.stop()

    def _guide_media_status(self, status):
        if (status == QMediaPlayer.MediaStatus.LoadedMedia and self.guide_vocal_path
                and self.karaoke_window and self.karaoke_window.guide_checkbox.isChecked()):
            self.guide_player.setPosition(self.player.position())

    def _media_status(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.next_song()

    def _media_error(self, error, message):
        if error != QMediaPlayer.Error.NoError:
            self.now_playing.setText(t('播放失敗：{p0}，跳至下一首', p0=message))
            self.next_song()

    def _exclusive_error(self, error, message):
        self.stop_playback()
        QMessageBox.warning(self, t('唱歌音訊中斷'), message)

    def previous_song(self):
        if self.queue.count():
            row = self.queue.row(self.playing_item) if self.playing_item else self.queue.currentRow()
            self._navigate_song(max(0, row - 1))

    def next_song(self):
        row = self.queue.row(self.playing_item) if self.playing_item else self.queue.currentRow()
        self._navigate_song(row + 1)

    def toggle_pause(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.microphone.pause()
        elif self.player.source().isValid():
            self.player.play()
            self.microphone.resume()
        else:
            self.play_selected()

    def stop_playback(self):
        self.player.stop()
        self.guide_player.stop()
        self.microphone.stop()
        self.playing_item = None
        self.record_checkbox.setEnabled(True)
        self.now_playing.setText(t('已停止播放'))

    def _record_choice_changed(self, checked):
        self.record_checkbox.setText(t('播放時錄音') if checked else t('整首錄音'))
        self.record_checkbox.setIcon(icon("record_ready" if checked else "record", "#b74754"))
        self.record_checkbox.setAccessibleName(t('整首錄音：開') if checked else t('整首錄音：關'))
        if self.karaoke_window:
            self.karaoke_window._update_controls_state()

    def remove_queue_item(self):
        row = self.queue.currentRow()
        if row < 0:
            return
        if self.queue.item(row) is self.playing_item:
            self.stop_playback()
            self.player.stop()
            self.guide_player.stop()
            self.guide_vocal_path = None
            if self.karaoke_window:
                self.karaoke_window.guide_checkbox.setEnabled(False)
                self.karaoke_window.guide_volume.setEnabled(False)
                self.karaoke_window.guide_checkbox.setChecked(False)
            self.playing_item = None
        self.queue.takeItem(row)
        self.now_playing.setText(t('已從歌單移除；音檔仍保留'))

    def save_queue(self):
        path, _ = QFileDialog.getSaveFileName(self, t('儲存歌單'), str(ROOT / t('歌單1.txt')), t('歌單文字檔 (*.txt)'))
        if path:
            try:
                save_playlist(Path(path), self.queue_items())
                self.now_playing.setText(t('歌單已儲存：{p0}', p0=path))
            except Exception as error:
                QMessageBox.warning(self, t('儲存失敗'), str(error))

    def _restore_last_queue(self):
        path = data_dir() / "last_playlist.json"
        if not path.is_file():
            return
        try:
            items = load_playlist(path)
            for item in items:
                self._append_queue(item)
            self.refresh_queue_labels()
            self.now_playing.setText(t('已還原上次歌單：{p0} 首', p0=len(items)))
        except Exception as error:
            QTimer.singleShot(0, lambda detail=str(error): QMessageBox.warning(self, t('上次歌單無法還原'), detail))

    def load_queue(self):
        path, _ = QFileDialog.getOpenFileName(self, t('載入歌單'), str(ROOT), t('歌單文字檔 (*.txt)'))
        if not path:
            return
        try:
            items = load_playlist(Path(path))
            self.stop_playback()
            self.player.stop()
            self.guide_player.stop()
            self.guide_vocal_path = None
            if self.karaoke_window:
                self.karaoke_window.guide_checkbox.setEnabled(False)
                self.karaoke_window.guide_volume.setEnabled(False)
                self.karaoke_window.guide_checkbox.setChecked(False)
            self.playing_item = None
            self.queue.clear()
            for item in items:
                self._append_queue(item)
            self.refresh_queue_labels()
            missing = sum(self.version_path(item["song_id"], item["variant_id"]) is None for item in items)
            self.now_playing.setText(t('已載入 {p0} 首；{p1} 首目前無法使用', p0=len(items), p1=missing))
            if missing:
                QMessageBox.information(self, t('歌單已載入'), t('{p0} 個項目找不到音檔，已保留在歌單中並標記。播放時會跳過。', p0=missing))
        except Exception as error:
            QMessageBox.warning(self, t('載入失敗'), str(error))

    def toggle_preview(self):
        item = self.tree.currentItem()
        key = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if isinstance(key, str):
            key = (key, "original")
        if isinstance(key, tuple) and key[0] == "recording":
            self.preview_recording(key[1], key[2])
        elif isinstance(key, tuple) and key[0] != "recordings":
            self.preview_version(*key)

    def preview_version(self, song_id, variant_id):
        key = (song_id, variant_id)
        if self.current_preview_key == key:
            self._stop_preview()
            return
        self._stop_preview()
        backing, vocal = self._preview_paths(song_id, variant_id)
        if not backing:
            self.process_status.setText(t('音檔目前無法試聽'))
            return
        self.stop_playback()
        self._apply_output_device()
        if vocal:
            try:
                self.stem_preview.start(backing, vocal, self.preview_backing_volume.value() / 100,
                                        self.preview_vocal_volume.value() / 100,
                                        self.preview_vocal_choices.get(key, False))
            except Exception as error:
                self.process_status.setText(t('試聽失敗：{p0}', p0=error))
                return
            self.current_preview_key = key
            self._preview_started(key, self.stem_preview.duration_ms)
        else:
            self._play_preview(backing, key)

    def _preview_paths(self, song_id, variant_id):
        backing = self.version_path(song_id, variant_id)
        if not backing or variant_id == "original":
            return backing, None
        catalog = load_catalog(self.workspace, song_id)
        if variant_id == "instrumental":
            separation = catalog.get("separation")
            vocal_path = separation["manifest"]["stems"].get("vocals") if separation else None
        else:
            variant = next((item for item in catalog.get("variants", []) if item["id"] == variant_id), None)
            vocal_path = variant.get("vocal_path") if variant else None
        vocal = Path(vocal_path) if vocal_path else None
        return backing, vocal if vocal and vocal.is_file() else None

    def _play_preview(self, path, key):
        self.current_preview_key = key
        self.preview_player.setSource(QUrl.fromLocalFile(str(path)))
        self.preview_player.play()
        self._preview_started(key, 0)

    def _preview_started(self, key, duration):
        song = self.song_map.get(key[0], {})
        self.preview_title.setText(t('{p0} · {p1}', p0=song.get('title', t('歌曲')), p1=key[1] if key[1] != 'original' else t('原曲')))
        self.preview_seek.setEnabled(duration > 0)
        self.preview_seek.setRange(0, duration)
        self.preview_total.setText(self._time_label(duration))
        for button in self.preview_buttons.get(key, []):
            button.setText(t('停止'))
        self.preview_timer.start()

    def _stop_preview(self):
        old_key = self.current_preview_key
        self.current_preview_key = None
        self.preview_timer.stop()
        self.preview_player.stop()
        self.record_backing_player.stop()
        self.record_preview = None
        self.stem_preview.stop()
        for button in self.preview_buttons.get(old_key, []):
            button.setText(t('試聽'))
        self.preview_title.setText(t('尚未試聽'))
        self.preview_seek.setEnabled(False)
        self.preview_seek.setRange(0, 0)
        self.preview_elapsed.setText("0:00")
        self.preview_total.setText("0:00")

    @staticmethod
    def _time_label(milliseconds):
        seconds = max(0, milliseconds // 1000)
        return f"{seconds // 60}:{seconds % 60:02d}"

    def _preview_gain(self, channel, value):
        if channel == "backing":
            self.record_backing_audio.setVolume(value / 100)
            if self.stem_preview.stream:
                self.stem_preview.stream.backing_gain = value / 100
            else:
                self.preview_audio.setVolume(value / 100)
        elif self.stem_preview.stream:
            self.stem_preview.stream.vocal_gain = value / 100
        elif self.record_preview:
            self.preview_audio.setVolume(value / 100)

    def _preview_vocal_changed(self, key, checked):
        self.preview_vocal_choices[key] = checked
        if key == self.current_preview_key and self.stem_preview.stream:
            self.stem_preview.stream.vocal_enabled = checked

    def _update_preview_progress(self):
        if not self.current_preview_key or self.preview_seek.isSliderDown():
            return
        if self.stem_preview.stream:
            duration = self.stem_preview.duration_ms
            position = self.stem_preview.position_ms
            if position >= duration and duration:
                self._stop_preview()
                return
        else:
            duration = self.preview_player.duration()
            position = self.preview_player.position()
            if self.record_preview:
                self._sync_record_backing()
        self.preview_seek.setEnabled(duration > 0 and (bool(self.stem_preview.stream) or self.preview_player.isSeekable()))
        self.preview_seek.setRange(0, duration)
        self.preview_seek.setValue(position)
        self.preview_elapsed.setText(self._time_label(position))
        self.preview_total.setText(self._time_label(duration))

    def _preview_drag_end(self):
        position = self.preview_seek.value()
        if position >= self.preview_seek.maximum():
            self._stop_preview()
            return
        if self.stem_preview.stream:
            try:
                self.stem_preview.seek(position)
            except Exception as error:
                self.process_status.setText(t('跳轉失敗：{p0}', p0=error))
                self._stop_preview()
                return
        else:
            self.preview_player.setPosition(position)
            if self.record_preview:
                self._sync_record_backing(force=True)
        self._update_preview_progress()

    def preview_recording(self, song_id, session_id):
        row = next((entry for entry in load_recordings(self.workspace, song_id)
                    if entry["session_id"] == session_id), None)
        if not row:
            QMessageBox.warning(self, t('試聽失敗'), t('錄音檔案不存在'))
            return
        key = ("recording", song_id, session_id)
        if self.current_preview_key == key:
            self._stop_preview()
            return
        self._stop_preview()
        self.stop_playback()
        self._apply_output_device()
        self.record_preview = row
        backing = self.version_path(song_id, row.get("variant_id", "original"))
        self.record_preview_check.setEnabled(bool(backing))
        if not backing:
            self.record_preview_check.setChecked(False)
        self.record_offset.setValue(0)
        self.current_preview_key = key
        self.preview_player.setSource(QUrl.fromLocalFile(row["audio_path"]))
        self.preview_player.play()
        self.preview_title.setText(t('{p0} · 錄音', p0=self.song_map.get(song_id, {}).get('title', t('歌曲'))))
        self.preview_timer.start()
        if backing and self.record_preview_check.isChecked():
            self.record_backing_player.setSource(QUrl.fromLocalFile(str(backing)))
            self.record_backing_player.play()
            self._sync_record_backing(force=True)

    def _record_backing_ready(self, status):
        if self.record_preview and status == QMediaPlayer.MediaStatus.LoadedMedia:
            self._sync_record_backing(force=True)

    def _record_preview_option_changed(self, *_):
        if not self.record_preview:
            return
        if not self.record_preview_check.isChecked():
            self.record_backing_player.stop()
            return
        backing = self.version_path(self.record_preview["song_id"], self.record_preview.get("variant_id", "original"))
        if backing:
            if self.record_backing_player.source().toLocalFile() != str(backing):
                self.record_backing_player.setSource(QUrl.fromLocalFile(str(backing)))
            self.record_backing_player.play()
            self._sync_record_backing(force=True)

    def _sync_record_backing(self, force=False):
        if not self.record_preview or not self.record_preview_check.isChecked():
            return
        target = max(0, self.preview_player.position() + self.record_offset.value()
                     + int(self.record_preview.get("playback_start_offset_ms", 0)))
        if force or abs(self.record_backing_player.position() - target) > 250:
            self.record_backing_player.setPosition(target)
        state = self.preview_player.playbackState()
        if state == QMediaPlayer.PlaybackState.PausedState:
            self.record_backing_player.pause()
        elif state == QMediaPlayer.PlaybackState.PlayingState and self.record_backing_player.playbackState() != state:
            self.record_backing_player.play()

    def remove_recording(self, song_id, session_id):
        if QMessageBox.question(self, t('刪除錄音'), t('確定刪除這次錄音？')) != QMessageBox.StandardButton.Yes:
            return
        self._stop_preview()
        try:
            delete_recording(self.workspace, song_id, session_id)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, t('刪除失敗'), str(error))
            return
        self.refresh_tree()

    def _preview_media_status(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self.current_preview_key and not self.stem_preview.stream:
            self._stop_preview()

    def _preview_error(self, error, message):
        if error != QMediaPlayer.Error.NoError and self.current_preview_key and not self.stem_preview.stream:
            self.process_status.setText(t('試聽失敗：{p0}', p0=message))
            self._stop_preview()

    def runtime(self, key):
        return resolve_runtime(self.settings, key)

    def worker_env(self):
        return resolve_worker_env(self.settings)

    def _selected_for_batch(self):
        ids = [song_id for song_id in self.selected_song_ids() if self.song_map.get(song_id, {}).get("available", True)]
        if not ids:
            QMessageBox.information(self, t('先勾選歌曲'), t('請在歌曲列表左側勾選要處理的歌曲'))
        return ids

    def _request(self):
        name = self.style.currentData()
        caption = self.custom_style.text().strip() if name == 'custom' else PRESETS[name]
        if not caption:
            QMessageBox.warning(self, t('風格'), t('請輸入自訂風格描述'))
            return None
        fixed_strength, fixed_noise, strengths, noises = LEVELS[self.level_text()]
        return {"speed": self.speed.value(), "semitones": self.key.value(), "caption": caption,
                "style_label": '自訂風格' if name == 'custom' else name,
                "tag": "custom" if name == 'custom' else name,
                "fixed_strength": fixed_strength, "fixed_noise": fixed_noise,
                "strengths": strengths, "noises": noises, "count": 2}

    def _load_version_options(self):
        options = self.settings.get("version_options", {})
        self.speed.setValue(float(options.get("speed", 1)))
        self.key.setValue(int(options.get("semitones", 0)))
        name = options.get('style', 'City Pop')
        if name == '自訂…':
            name = 'custom'
        self.style.setCurrentIndex(max(0, self.style.findData(name)))
        self.custom_style.setText(options.get("custom", ""))
        self.level.setValue(int(options.get("level", 1)))

    def _remember_version_options(self):
        self.settings["version_options"] = {"speed": self.speed.value(), "semitones": self.key.value(),
                                           "style": self.style.currentData(), "custom": self.custom_style.text(),
                                           "level": self.level.value()}
        save_settings(self.settings)

    def open_version_dialog(self, ids):
        if not ids:
            return
        self.version_targets = list(ids)
        label = self.song_map.get(ids[0], {}).get("title", t('歌曲')) if len(ids) == 1 else t('{p0} 首勾選歌曲', p0=len(ids))
        self.version_targets_label.setText(t('為 {p0} 製作新版本 · 風格每首產生 2 個版本', p0=label))
        self.version_dialog.exec()

    def _submit_version_dialog(self, mode):
        if mode == "fx":
            request = {"speed": self.speed.value(), "semitones": self.key.value()}
            if request["speed"] == 1 and request["semitones"] == 0:
                QMessageBox.information(self.version_dialog, t('沒有變更'), t('請先調整速度或 Key'))
                return
        else:
            request = self._request()
            if request is None:
                return
        self._submit(self.version_targets, mode, request)
        self.version_dialog.accept()

    def batch_lyrics(self):
        self._submit(self._selected_for_batch(), "lyrics")

    def _lyrics_status(self, detail):
        if self.active_op and self.active_op["mode"] == "lyrics":
            self.work_stage = detail
            self._update_work_summary()

    def _lyrics_finished(self, outcome, detail):
        if not self.active_op or self.active_op["mode"] != "lyrics":
            return
        self._finish_operation(outcome == "success", detail, show_error=False, outcome=outcome)

    def _update_work_summary(self):
        op = self.active_op
        batch = self.batches.get(op.get("batch_id") if op else self.last_batch)
        self.cancel_work.setEnabled(bool(op or self.pending_ops))
        if not batch:
            return
        counts = batch["counts"]
        completed = sum(counts.values())
        total = batch["total"]
        totals = (t('完成 {p0} · 失敗 {p1} · 跳過 {p2} · 查無 {p3} · 需確認 {p4}', p0=counts.get('success', 0), p1=counts.get('failed', 0), p2=counts.get('skipped', 0), p3=counts.get('not_found', 0), p4=counts.get('review', 0)))
        if op:
            title = self.song_map.get(op["song_id"], {}).get("title", t('歌曲'))
            elapsed = round(time.monotonic() - self.work_started) if self.work_started else 0
            self.process_status.setText(t('第 {p0}／{p1} 首 · {p2} · {p3} {p4} {p5} · 已用 {p6}:{p7:02d} · 等待 {p8} · {p9}', p0=min(completed + 1, total), p1=total, p2=title, p3=self.work_stage, p4=self.work_device, p5=self.work_candidate, p6=elapsed // 60, p7=elapsed % 60, p8=len(self.pending_ops), p9=totals))
            self.progress.setRange(0, 0)
        else:
            if not self.pending_ops:
                all_counts = {}
                for entry in self.batches.values():
                    for name, value in entry["counts"].items():
                        all_counts[name] = all_counts.get(name, 0) + value
                total = sum(entry["total"] for entry in self.batches.values())
                completed = sum(all_counts.values())
                totals = (t('完成 {p0} · 失敗 {p1} · 跳過 {p2} · 查無 {p3} · 需確認 {p4}', p0=all_counts.get('success', 0), p1=all_counts.get('failed', 0), p2=all_counts.get('skipped', 0), p3=all_counts.get('not_found', 0), p4=all_counts.get('review', 0)))
            self.progress.setRange(0, max(1, total))
            self.progress.setValue(completed)
            self.process_status.setText(t('{p0} · {p1} 首工作 · {p2}', p0=t('等待下一首') if self.pending_ops else t('批次結束'), p1=total, p2=totals))
            self.process_status.setToolTip("\n".join(detail for entry in self.batches.values() for detail in entry["details"]))

    def _submit(self, ids, mode, request=None):
        if self.installation_active():
            QMessageBox.information(self, t('管理安裝'), t('安裝管理中，請先關閉安裝視窗。'))
            return
        if not ids:
            return
        from app.model_store import ModelStore
        if self.models_dialog is not None and self.models_dialog.worker is not None:
            QMessageBox.information(self, t('模型與元件管理'), t('模型準備中，請等待完成。'))
            return
        groups = ['separation'] if mode == 'separate' else ['styling'] if mode == 'style' else []
        if mode == 'style' and any(not load_catalog(self.workspace, sid).get('separation') for sid in ids):
            groups.append('separation')
        if any(not ModelStore(self.settings).ready(group) for group in groups):
            self.open_models()
            return
        if mode in {"separate", "style"} and not self.runtime("roformer_python"):
            QMessageBox.warning(self, t('無法處理'), t('執行元件缺失，請從「設定 → 管理安裝」準備所需元件'))
            return
        if mode in {"style", "fx"} and not self.runtime("acestep_python"):
            QMessageBox.warning(self, t('無法處理'), t('執行元件缺失，請從「設定 → 管理安裝」準備所需元件'))
            return
        batch_id = uuid.uuid4().hex
        batch = {"total": len(set(ids)), "counts": {}, "details": []}
        self.batches[batch_id] = batch
        self.last_batch = batch_id
        busy = {(op["song_id"], op["mode"]) for op in self.pending_ops}
        if self.active_op:
            busy.add((self.active_op["song_id"], self.active_op["mode"]))
        accepted = []
        for sid in dict.fromkeys(ids):
            if (sid, mode) in busy:
                batch["counts"]["skipped"] = batch["counts"].get("skipped", 0) + 1
                batch["details"].append(t('{p0}：同類工作已在佇列，跳過', p0=self.song_map.get(sid, {}).get('title', sid)))
                continue
            accepted.append(sid)
            self.pending_ops.append({"song_id": sid, "mode": mode, "request": dict(request or {}), "batch_id": batch_id})
        for sid in accepted:
            if self.active_op and self.active_op["song_id"] == sid:
                continue
            self.job_state[sid] = t('◷ 等待處理')
        if not accepted:
            self.info.setText(t('選取的歌曲已有同類工作，已跳過重複排入。'))
        self.refresh_tree()
        self.process_status.setText(t('已排入 {p0} 首歌曲', p0=len(ids)))
        self._start_next_operation()
        self._update_work_summary()

    def batch_separate(self):
        self._submit(self._selected_for_batch(), "separate")

    def retry_failed(self):
        ids = self.selected_song_ids()
        retries = [self.failures.pop(sid) for sid in ids if sid in self.failures]
        if not retries:
            QMessageBox.information(self, t('沒有失敗項'), t('請勾選顯示失敗狀態的歌曲'))
            return
        for operation in retries:
            self._submit([operation["song_id"]], operation["mode"], operation["request"])

    def cancel_batch(self):
        for operation in self.pending_ops:
            batch = self.batches.get(operation.get("batch_id"))
            if batch:
                batch["counts"]["skipped"] = batch["counts"].get("skipped", 0) + 1
                batch["details"].append(t('{p0}：等待工作已取消', p0=self.song_map.get(operation['song_id'], {}).get('title', '')))
            if operation["song_id"] != (self.active_op or {}).get("song_id"):
                self.job_state.pop(operation["song_id"], None)
        self.pending_ops.clear()
        if self.active_op and self.active_op["mode"] == "lyrics":
            self.lyric_lookup.cancel()
            self._finish_operation(False, t('已取消'), show_error=False)
        else:
            self.jobs.cancel()
        self.process_status.setText(t('已取消目前與等待中的處理'))
        self.refresh_tree()
        self._update_work_summary()

    def _start_next_operation(self):
        if self._closing or self.active_op or not self.pending_ops:
            return
        self.active_op = self.pending_ops.pop(0)
        self.work_started = time.monotonic()
        self.work_stage, self.work_device, self.work_candidate = t('準備處理'), "", ""
        song_id = self.active_op["song_id"]
        song = self.song_map.get(song_id)
        if not song or not song.get("available", True) or not Path(song["path"]).is_file():
            self._finish_operation(True, t('來源音檔無法使用，已跳過'), outcome="skipped")
            return
        catalog = load_catalog(self.workspace, song_id)
        if self.active_op["mode"] == "lyrics":
            if has_lrclib(self.workspace, song_id):
                self._finish_operation(True, t('已有 LRCLIB 結果，已跳過'), outcome="skipped")
            else:
                self.job_state[song_id] = t('◷ 正在查詢歌詞')
                self.refresh_tree()
                self.work_stage = t('正在查詢 LRCLIB')
                self._update_work_summary()
                self.lyric_lookup.start(song, self.workspace)
        elif self.active_op["mode"] == "separate":
            if catalog.get("separation"):
                self._finish_operation(True, t('已分離，跳過'), outcome="skipped")
            else:
                self._enqueue_separation(song)
        elif self.active_op["mode"] == "style":
            if catalog.get("separation"):
                self._continue_style(catalog)
            else:
                self._enqueue_separation(song)
        else:
            request = self.active_op["request"]
            if catalog.get("separation"):
                stems = catalog["separation"]["manifest"]["stems"]
                self._enqueue_fx(song_id, Path(stems["instrumental"]), Path(stems["vocals"]), request)
            else:
                self._enqueue_fx(song_id, Path(song["path"]), None, request)

    def _enqueue_separation(self, song):
        python = self.runtime("roformer_python")
        song_id = song["id"]
        output = song_dir(self.workspace, song_id) / "separation_jobs" / uuid.uuid4().hex
        self._enqueue("separate", python, ROOT / "workers/separation_worker.py",
                      ["separate", "--input", song["path"], "--output-root", str(output)], song_id,
                      {"AIK_OUTPUT_ROOT": str(output)})

    def _continue_style(self, catalog):
        song_id = self.active_op["song_id"]
        request = self.active_op["request"]
        manifest = catalog["separation"]["manifest"]
        backing = Path(manifest["stems"]["instrumental"])
        vocal = Path(manifest["stems"]["vocals"])
        if request["speed"] != 1.0 or request["semitones"] != 0:
            self._enqueue_fx(song_id, backing, vocal, request)
        else:
            self._enqueue_style(song_id, backing, vocal, "instrumental", request,
                                catalog["separation"]["manifest_path"])

    def _enqueue_fx(self, song_id, backing, vocal, request):
        if not backing.is_file() or (vocal and not vocal.is_file()):
            self._finish_operation(False, t('音訊檔案無法使用'))
            return
        target = song_dir(self.workspace, song_id) / "fx_jobs" / uuid.uuid4().hex
        args = ["--backing", str(backing), "--output-dir", str(target),
                "--speed", str(request["speed"]), "--semitones", str(request["semitones"])]
        if vocal:
            args += ["--vocal", str(vocal)]
        self._enqueue("fx", self.runtime("acestep_python"), ROOT / "workers/fx_worker.py", args, song_id)

    def _enqueue_style(self, song_id, backing, vocal, source_id, request, manifest_path):
        if not backing.is_file() or not vocal or not vocal.is_file():
            self._finish_operation(False, t('伴奏或人聲檔案無法使用'))
            return
        if not self.active_op and not self.pending_ops:
            self.batches.clear()
        self._enqueue("style_probe", self.runtime("acestep_python"), ROOT / "workers/style_worker.py",
                      ["probe", "--backing", str(backing), "--count", str(request["count"])], song_id,
                      extra={"backing": str(backing), "vocal_path": str(vocal), "source_id": source_id,
                             "manifest_path": str(manifest_path)})

    def _queue_planned_style(self, song_id, metadata, request, plan):
        if not confirm_execution(self, plan):
            self._finish_operation(False, t('已取消風格轉換'), show_error=False)
            return
        backing, vocal = metadata["backing"], metadata["vocal_path"]
        source_id, manifest_path = metadata["source_id"], metadata["manifest_path"]
        target = song_dir(self.workspace, song_id) / "style_jobs" / uuid.uuid4().hex
        args = ["transform", "--manifest", str(manifest_path), "--job-dir", str(target),
                "--style", request["caption"], "--backing", str(backing), "--vocal", str(vocal),
                "--tag", request["tag"], "--count", str(request["count"]),
                "--fixed-strength", str(request["fixed_strength"]),
                "--fixed-noise-strength", str(request["fixed_noise"]),
                "--strengths", request["strengths"], "--noise-strengths", request["noises"]]
        args += ["--device", plan["device"]]
        if plan["device"] == "cpu":
            args += ["--cpu-confirmed"]
        self._enqueue("style", self.runtime("acestep_python"), ROOT / "workers/style_worker.py",
                      args, song_id, extra={"source_id": source_id, "vocal_path": str(vocal)})

    def _enqueue(self, stage, python, worker, args, song_id, env_extra=None, extra=None):
        env = self.worker_env() | (env_extra or {})
        metadata = {"workspace": str(self.workspace), "stage": stage} | (extra or {})
        self.job_state[song_id] = {"separate": t('◷ 正在分離人聲'), "fx": t('◷ 正在調整音訊'), "style": t('◷ 正在製作風格'), "style_probe": t('◷ 正在估算處理方式')}[stage]
        self.refresh_tree()
        self.work_stage = {"separate": t('分離人聲'), "fx": t('調整速度／Key'), "style": t('製作風格'), "style_probe": t('估算處理方式')}[stage]
        self.work_device = "CPU" if stage == "fx" else ""
        self.work_candidate = ""
        self._update_work_summary()
        self.jobs.enqueue(stage, python, worker, args, env, song_id, metadata)

    def _job_event(self, event):
        kind = event.get("type")
        if kind in {"running", "status", "progress"}:
            if event.get("device"):
                self.work_device = "GPU" if event["device"] == "cuda" else "CPU"
            if event.get("execution"):
                self.work_device = "CPU" if event["execution"]["device"] == "cpu" else "GPU"
            if kind == "progress":
                count = (self.active_op or {}).get("request", {}).get("count", 2)
                completed = round(float(event.get("value", 0)) * count)
                self.work_candidate = t('候選完成 {p0}／{p1}', p0=completed, p1=count)
        if kind == "status" and event.get("message") and not event.get("message", "").startswith("Generating"):
            self.work_stage = event["message"]
        self._update_work_summary()

    def _job_finished(self, job, ok):
        if self._closing:
            return
        if not self.active_op or job["song_id"] != self.active_op["song_id"]:
            return
        if not ok:
            if job.get("cancelled"):
                self._finish_operation(False, t('已取消'), show_error=False)
                return
            stage = {"separate": t('分離人聲'), "fx": t('調整速度／Key'), "style": t('製作風格版本')}.get(job["kind"], job["kind"])
            reason = job.get("error") or t('工作結束碼：{p0}', p0=job.get('exit_code', t('未知')))
            log_path = Path(job["log_path"]) if job.get("log_path") else None
            self._finish_operation(False, t('{p0}失敗：{p1}', p0=stage, p1=reason), log_path=log_path)
            return
        song_id = job["song_id"]
        result = job["result"]
        if job["kind"] == "style_probe":
            self._queue_planned_style(song_id, job["metadata"], self.active_op["request"], result["plan"])
            return
        catalog = load_catalog(Path(job["metadata"]["workspace"]), song_id)
        request = self.active_op["request"]
        if job["kind"] == "separate":
            manifest = result["manifest"]
            catalog["separation"] = {"manifest": manifest, "manifest_path": manifest["manifest_path"]}
            save_catalog(self.workspace, song_id, catalog)
            if self.active_op["mode"] == "style":
                self._continue_style(catalog)
                return
        elif job["kind"] == "fx":
            variant_id = uuid.uuid4().hex
            catalog["variants"].append({"id": variant_id, "kind": "fx", "audio_path": result["backing"],
                "vocal_path": result.get("vocal"), "speed": result["speed"], "semitones": result["semitones"],
                "source_id": "instrumental" if catalog.get("separation") else None,
                "source_label": '純伴奏' if catalog.get("separation") else '原曲'})
            save_catalog(self.workspace, song_id, catalog)
            if self.active_op["mode"] == "style":
                self._enqueue_style(song_id, Path(result["backing"]), Path(result["vocal"]), variant_id,
                                    request, catalog["separation"]["manifest_path"])
                return
        elif job["kind"] == "style":
            for candidate in result["candidates"]:
                catalog["variants"].append({"id": uuid.uuid4().hex, "audio_path": candidate["output"],
                    "vocal_path": job["metadata"]["vocal_path"], "source_id": job["metadata"]["source_id"],
                    "style": candidate["style"], "style_label": request["style_label"],
                    "seed": candidate["seed"], "cover_strength": candidate["cover_strength"],
                    "cover_noise_strength": candidate["cover_noise_strength"], "steps": candidate["steps"],
                    "execution": result.get("execution")})
            save_catalog(self.workspace, song_id, catalog)
        detail = (t('完成 {p0} 個版本，{p1} 個版本失敗', p0=len(result['candidates']), p1=len(result.get('failures', [])))
                  if job["kind"] == "style" else "")
        self._finish_operation(True, detail)

    def _finish_operation(self, ok, detail="", log_path=None, show_error=True, outcome=None):
        if not self.active_op:
            return
        operation = self.active_op
        song_id = operation["song_id"]
        outcome = outcome or ("success" if ok else "skipped" if not show_error and detail.startswith(t('已取消')) else "failed")
        batch = self.batches.get(operation.get("batch_id"))
        if batch:
            batch["counts"][outcome] = batch["counts"].get(outcome, 0) + 1
            label = {"success": t('已完成'), "failed": t('失敗'), "skipped": t('跳過'), "not_found": t('查無'), "review": t('需確認')}[outcome]
            batch["details"].append(f"{self.song_map.get(song_id, {}).get('title', song_id)}：{detail or label}")
            self.last_batch = operation["batch_id"]
        if outcome in {"review", "not_found"}:
            self.job_state[song_id] = "○ " + detail
            self.failures.pop(song_id, None)
        elif ok:
            self.job_state.pop(song_id, None)
            self.failures.pop(song_id, None)
            self.process_status.setText(t('{p0} 已完成', p0=self.song_map.get(song_id, {}).get('title', t('歌曲'))))
        elif not show_error and detail.startswith(t('已取消')):
            self.job_state.pop(song_id, None)
            self.failures.pop(song_id, None)
            self.process_status.setText(t('{p0}：{p1}', p0=self.song_map.get(song_id, {}).get('title', t('歌曲')), p1=detail))
        else:
            self.job_state[song_id] = t('⚠ 處理失敗')
            self.failures[song_id] = operation
            self.process_status.setText(t('{p0}：{p1}', p0=self.song_map.get(song_id, {}).get('title', t('歌曲')), p1=detail))
        self.active_op = None
        self.refresh_tree()
        QTimer.singleShot(0, self._start_next_operation)
        self._update_work_summary()
        if not ok and show_error:
            dialog = QMessageBox(self)
            dialog.setIcon(QMessageBox.Icon.Critical)
            dialog.setWindowTitle(t('歌曲處理失敗'))
            dialog.setText(t('{p0}：{p1}', p0=self.song_map.get(song_id, {}).get('title', t('歌曲')), p1=detail))
            if log_path:
                details = t('工作日誌：{p0}', p0=log_path)
                try:
                    if log_path.is_file():
                        with log_path.open("rb") as handle:
                            handle.seek(max(0, log_path.stat().st_size - 12000))
                            details += t('\n\n日誌末段：\n') + handle.read().decode("utf-8", errors="replace")
                except OSError as error:
                    details += t('\n\n無法讀取日誌：{p0}', p0=error)
                dialog.setDetailedText(details)
                dialog.setInformativeText(t('工作日誌：{p0}', p0=log_path))
            dialog.exec()


def main():
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(str(ROOT / "assets" / "icon.svg")))
    window = ConsumerWindow()
    window.show()
    from app.model_store import ModelStore
    if not (ROOT / 'bootstrap-launcher.json').is_file() and not all(ModelStore(window.settings).ready(group) for group in ('separation', 'styling')):
        QTimer.singleShot(0, window.open_models)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
