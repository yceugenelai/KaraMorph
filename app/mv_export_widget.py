"""MV export tab: pick a version or take, arrange images over time, render to MP4."""

import re
import shutil
import tempfile
from pathlib import Path

import soundfile as sf
from PySide6.QtCore import QSize, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QHBoxLayout, QHeaderView, QLabel, QListWidget, QListWidgetItem, QMessageBox, QProgressBar,
    QPushButton, QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from app.i18n import t
from app.karaoke_assets import image_files, shared_images_dir, song_images_dir
from app.library_media import read_media
from app.lyrics import effective_speed, load_selected
from app.lyrics_sync import format_time, parse_time
from app.mv_export import (RESOLUTIONS, MvJob, MvRenderer, decode_audio, mix_recording, plan_scenes)
from app.playlist import resolve_item
from app.recordings import load_recordings
from app.storage import load_catalog



def style_options():
    return ([(t('小'), 1.0), (t('中'), 1.3), (t('大'), 1.6)],
            [(t('下方'), 5 / 6), (t('中央'), 0.5)],
            [(t('白'), '#ffffff'), (t('黃'), '#ffe45c'), (t('青'), '#6ee7ff')])


class SceneBar(QWidget):
    """Timeline strip: each block is one image scene; the red line is the preview position."""

    seekRequested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scenes, self.cuts, self.duration, self.position = [], set(), 1, 0
        self._thumbs = {}
        self.setMinimumHeight(56)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_data(self, scenes, cuts, duration):
        self.scenes, self.cuts, self.duration = scenes, set(cuts), max(1, duration)
        self.update()

    def set_position(self, position):
        self.position = position
        self.update()

    def _thumb(self, path, height):
        if path not in self._thumbs:
            pixmap = QPixmap(str(path)) if path else QPixmap()
            self._thumbs[path] = pixmap.scaledToHeight(height, Qt.TransformationMode.FastTransformation) if not pixmap.isNull() else None
        return self._thumbs[path]

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#1b2430'))
        scale = self.width() / self.duration
        for index, scene in enumerate(self.scenes):
            left, right = round(scene.start * scale), round(scene.end * scale)
            block = self.rect().adjusted(0, 0, 0, -1)
            block.setLeft(left)
            block.setRight(max(left, right - 1))
            painter.save()
            painter.setClipRect(block)
            painter.fillRect(block, QColor('#33475f' if index % 2 else '#2a3b50'))
            thumb = self._thumb(scene.image, block.height())
            if thumb:
                x = block.left()
                while x < block.right():
                    painter.drawPixmap(x, 0, thumb)
                    x += max(1, thumb.width())
                painter.fillRect(block, QColor(0, 0, 0, 90))
            painter.restore()
            painter.setPen(QColor('#ffb84d') if scene.start in self.cuts else QColor('#0d131a'))
            painter.drawLine(left, 0, left, self.height())
        painter.setPen(QColor('#ff4d4d'))
        x = round(self.position * scale)
        painter.drawLine(x, 0, x, self.height())

    def _seek(self, event):
        self.seekRequested.emit(round(max(0, min(self.width(), event.position().x())) / max(1, self.width()) * self.duration))

    mousePressEvent = mouseMoveEvent = _seek


class MvExportWidget(QWidget):
    def __init__(self, controller, song_id, parent=None):
        super().__init__(parent)
        self.controller, self.song_id = controller, song_id
        self.workspace = controller.workspace
        self.song = controller.song_map.get(song_id, {})
        self.seed, self.duration, self.position = 0, 1, 0
        self.sources, self.images = [], []
        self.timeline, self.speed, self.delay, self.lyric_message = None, 1.0, 0, ''
        self.job, self._renderer, self._building, self._key = None, None, False, None
        self._audio_cache, self._mix_count, self._play_dir = {}, 0, None
        self.player = QMediaPlayer(self)
        self.player_output = QAudioOutput(self)
        self.player.setAudioOutput(self.player_output)
        self.player.mediaStatusChanged.connect(self._player_status)
        self.play_timer = QTimer(self)
        self.play_timer.setInterval(50)
        self.play_timer.timeout.connect(self._follow_player)
        self.restart_timer = QTimer(self)
        self.restart_timer.setSingleShot(True)
        self.restart_timer.timeout.connect(self._restart_playback)
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.timeout.connect(self._draw_preview)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        layout = QVBoxLayout(body)

        self.source = QComboBox()
        self.source.currentIndexChanged.connect(lambda *_: self._load_source(False))
        layout.addWidget(QLabel(t('音樂來源（歌曲版本或某次錄音）')))
        layout.addWidget(self.source)
        mix = QHBoxLayout()
        self.vocal_gain, self.backing_gain = QDoubleSpinBox(), QDoubleSpinBox()
        for box, value in ((self.vocal_gain, 1.0), (self.backing_gain, 0.7)):
            box.setRange(0, 2)
            box.setSingleStep(0.1)
            box.setValue(value)
        self.mix_label = QLabel(t('錄音混音：人聲'))
        mix.addWidget(self.mix_label)
        mix.addWidget(self.vocal_gain)
        self.mix_label2 = QLabel(t('伴奏'))
        mix.addWidget(self.mix_label2)
        mix.addWidget(self.backing_gain)
        self.offset_label = QLabel(t('伴奏偏移'))
        self.offset = QSpinBox()
        self.offset.setRange(-3000, 3000)
        self.offset.setSingleStep(50)
        self.offset.setSuffix(' ms')
        self.offset.setToolTip(t('與「處理歌曲」的錄音對齊相同：數值越大，伴奏相對人聲越晚出現；字幕會跟著伴奏。'))
        mix.addWidget(self.offset_label)
        mix.addWidget(self.offset)
        self.rate_label = QLabel(t('錄音當時的播放速度'))
        self.rate = QDoubleSpinBox()
        self.rate.setRange(0.5, 1.5)
        self.rate.setSingleStep(0.05)
        self.rate.setDecimals(2)
        self.rate.setValue(1.0)
        self.rate.setSuffix('×')
        self.rate.setToolTip(t('這次錄音沒有記錄歌曲位置（舊版錄音）。若當時在唱歌畫面調慢或調快，請在此填入同樣的速度，字幕才會對上。'))
        mix.addWidget(self.rate_label)
        mix.addWidget(self.rate)
        mix.addStretch(1)
        layout.addLayout(mix)
        self.lyric_info = QLabel()
        self.lyric_info.setWordWrap(True)
        layout.addWidget(self.lyric_info)

        self.preview = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.preview.setFixedSize(480, 270)
        self.preview.setStyleSheet('background:#000')
        layout.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignHCenter)
        self.bar = SceneBar()
        self.bar.seekRequested.connect(self.seek)
        layout.addWidget(self.bar)
        play_row = QHBoxLayout()
        self.play_button = QPushButton(t('▶ 試聽'))
        self.play_button.setToolTip(t('從預覽位置開始播放目前的混音（含人聲／伴奏音量與偏移），字幕同步預覽。'))
        self.play_button.clicked.connect(self.toggle_playback)
        self.clock = QLabel('00:00.00')
        play_row.addWidget(self.play_button)
        play_row.addWidget(self.clock)
        play_row.addStretch(1)
        layout.addLayout(play_row)

        layout.addWidget(QLabel(t('圖片分段：預設整首「自動輪播」；在預覽位置切段後，可替該段指定固定圖片。')))
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels([t('起點'), t('圖片')])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setMaximumHeight(130)
        self.table.itemChanged.connect(self._table_changed)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        for title, action in [(t('在預覽位置切段'), self.add_cut), (t('刪除選取段落'), self.delete_cut),
                              (t('重新隨機'), self.reshuffle)]:
            button = QPushButton(title)
            button.clicked.connect(lambda _=False, a=action: a())
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)

        self.pool = QListWidget()
        self.pool.setIconSize(QSize(48, 27))
        self.pool.setMaximumHeight(110)
        self.pool.itemChanged.connect(lambda *_: self._replan())
        layout.addWidget(QLabel(t('自動輪播使用的圖片（來自本歌曲與共用圖片資料夾，需已儲存）')))
        self.use_shared = QCheckBox(t('自動輪播包含共用圖片'))
        self.use_shared.setChecked(False)
        self.use_shared.toggled.connect(self._shared_toggled)
        layout.addWidget(self.use_shared)
        layout.addWidget(self.pool)

        form = QFormLayout()
        self.interval = QSpinBox()
        self.interval.setRange(3, 120)
        self.interval.setValue(12)
        self.interval.setSuffix(t(' 秒'))
        self.fade = QDoubleSpinBox()
        self.fade.setRange(0, 3)
        self.fade.setSingleStep(0.5)
        self.fade.setValue(1.0)
        self.fade.setSuffix(t(' 秒'))
        self.resolution = QComboBox()
        for name in RESOLUTIONS:
            self.resolution.addItem(name, name)
        self.resolution.setCurrentText('720p')
        self.size, self.pos, self.color = QComboBox(), QComboBox(), QComboBox()
        for combo, options in zip((self.size, self.pos, self.color), style_options()):
            for title, value in options:
                combo.addItem(title, value)
        self.size.setCurrentIndex(1)
        for label, widget in [(t('每張圖停留'), self.interval), (t('淡入淡出'), self.fade),
                              (t('解析度（30 fps）'), self.resolution), (t('字幕大小'), self.size),
                              (t('字幕位置'), self.pos), (t('目前句顏色'), self.color)]:
            form.addRow(label, widget)
        layout.addLayout(form)
        for widget in (self.interval, self.fade, self.vocal_gain, self.backing_gain, self.offset, self.rate):
            widget.valueChanged.connect(lambda *_: self._invalidate())
        for combo in (self.size, self.pos, self.color):
            combo.currentIndexChanged.connect(lambda *_: self._invalidate())
        self.interval.valueChanged.connect(lambda *_: self._replan())
        self.motion = QCheckBox(t('圖片隨機緩慢移動／放大縮小'))
        self.motion.setChecked(True)
        self.motion.toggled.connect(lambda *_: self._invalidate())
        form.addRow(t('圖片動態'), self.motion)
        for widget in (self.vocal_gain, self.backing_gain, self.offset):
            widget.valueChanged.connect(lambda *_: self.restart_timer.start(400))

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        self.status = QLabel(t('MV 使用已儲存的歌詞、圖片與歌詞調整時間；剛修改的內容請先按儲存。'))
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.export_button = QPushButton(t('匯出 MV…'))
        self.export_button.clicked.connect(self.start_export)
        self.cancel_button = QPushButton(t('取消匯出'))
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_export)
        buttons.addWidget(self.export_button)
        buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)
        self.refresh()

    # ----- data -----
    def refresh(self):
        """Re-read sources, lyrics and images from disk (called whenever the tab is opened)."""
        if self.job:
            return
        self._building = True
        previous = self.source.currentData()
        self.source.clear()
        self.sources = self._find_sources()
        for index, source in enumerate(self.sources):
            self.source.addItem(source['label'], index)
        keep = next((i for i, s in enumerate(self.sources) if s['key'] == (previous or {}).get('key')), 0)
        self.source.setCurrentIndex(keep)
        checked = {self.pool.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.pool.count())
                   if self.pool.item(i).checkState() == Qt.CheckState.Checked}
        known = {self.pool.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.pool.count())}
        shared_dir = shared_images_dir(self.workspace) if self.song_id else None
        own = image_files(song_images_dir(self.workspace, self.song_id)) if self.song_id else []
        self.images = own + (image_files(shared_dir) if shared_dir else [])
        self.pool.clear()
        for path in self.images:
            item = QListWidgetItem(path.name)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            on = str(path) in checked or str(path) not in known
            item.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
            icon = QPixmap(str(path))
            if not icon.isNull():
                item.setIcon(QIcon(icon.scaled(96, 54, Qt.AspectRatioMode.KeepAspectRatio)))
            item.setData(Qt.ItemDataRole.UserRole + 1, shared_dir in path.parents)
            self.pool.addItem(item)
        self._apply_shared_filter()
        self._building = False
        key = (self.current() or {}).get('key')
        self._load_source(key == self._key)
        self._key = key

    def _profile(self):
        return getattr(getattr(self.controller, 'profiles', None), 'active', None)

    def set_song(self, song_id):
        if self.job or song_id == self.song_id:
            return
        self._stop_playback()
        self.song_id, self.song = song_id, self.controller.song_map.get(song_id, {})
        self.seed, self._key = 0, None
        self.refresh()

    def _find_sources(self):
        sources = []
        if not self.song_id or self.song_id not in self.controller.song_map:
            return sources

        def add_version(variant_id, label):
            path = resolve_item({'song_id': self.song_id, 'variant_id': variant_id, 'label': ''},
                                self.controller.song_map, self.workspace)
            if path:
                sources.append({'key': ('version', variant_id), 'label': label, 'kind': 'version',
                                'variant_id': variant_id, 'path': path})
        add_version('original', t('原曲'))
        add_version('instrumental', t('純伴奏'))
        try:
            variants = load_catalog(self.workspace, self.song_id).get('variants', [])
        except (OSError, ValueError):
            variants = []
        by_id = {row['id']: row for row in variants}
        try:
            from app.consumer import version_label
        except ImportError:
            version_label = None
        for row in variants:
            add_version(row['id'], version_label(row, by_id) if version_label else row['id'])
        names = {s['variant_id']: s['label'] for s in sources}
        for index, take in enumerate(load_recordings(self.workspace, self.song_id, self._profile()), 1):
            sources.append({'key': ('recording', take['session_id']), 'kind': 'recording',
                            'variant_id': take.get('variant_id', 'original'), 'take': take,
                            'label': t('錄音 {p0} · {p1} · {p2}', p0=index, p1=take.get('started_at', '')[:16],
                                       p2=names.get(take.get('variant_id'), t('原版本已遺失')))})
        return sources

    def current(self):
        index = self.source.currentData()
        return self.sources[index] if index is not None and 0 <= index < len(self.sources) else None

    def _duration_ms(self, source):
        if source['kind'] == 'recording':
            take = source['take']
            return int(take.get('duration_ms') or sf.info(take['audio_path']).duration * 1000)
        try:
            seconds = read_media(source['path'])[0].get('duration_sec')
        except Exception:
            seconds = None
        return round((seconds or sf.info(str(source['path'])).duration) * 1000)

    def _load_source(self, keep_segments):
        if self._building:
            return
        source = self.current()
        recording = bool(source and source['kind'] == 'recording')
        for widget in (self.mix_label, self.vocal_gain, self.mix_label2, self.backing_gain, self.offset_label, self.offset):
            widget.setVisible(recording)
        needs_rate = bool(recording and not source['take'].get('song_position_map'))
        for widget in (self.rate_label, self.rate):
            widget.setVisible(needs_rate)
        self.timeline, self.speed, self.delay, self.lyric_message = None, 1.0, 0, ''
        self.duration = 1
        if source:
            try:
                self.duration = max(1, self._duration_ms(source))
            except (OSError, RuntimeError, ValueError):
                self.lyric_message = t('無法讀取音檔長度。')
            try:
                _, self.timeline, state, warning = load_selected(self.workspace, self.song_id)
                if self.timeline:
                    variants = load_catalog(self.workspace, self.song_id).get('variants', [])
                    self.speed = effective_speed(source['variant_id'], variants)
                    self.delay = state.get('offsets', {}).get(source['variant_id'], 0)
                else:
                    self.lyric_message = warning or t('此歌曲沒有 LRC 時間軸：請先到「歌詞調整」打好時間，套用並儲存。')
            except (OSError, ValueError) as error:
                self.timeline = None
                self.lyric_message = str(error)
        self.lyric_info.setText(self.lyric_message or t('字幕：LRC 同步歌詞（字幕偏移 {p0:+.1f} 秒）', p0=self.delay / 1000))
        self.lyric_info.setStyleSheet('color:#d33' if self.lyric_message else '')
        self.export_button.setEnabled(bool(source and self.timeline and not self.job))
        if keep_segments:
            self._replan()
            return
        self._stop_playback()
        self.offset.setValue(0)
        self.rate.setValue(1.0)
        self.position = 0
        self._key = (source or {}).get('key')
        self._set_table_default()

    # ----- segments -----
    def _set_table_default(self):
        self._building = True
        self.table.setRowCount(0)
        self._add_row(0, None)
        self._building = False
        self._replan()

    def _add_row(self, start, image):
        row = self.table.rowCount()
        self.table.insertRow(row)
        item = QTableWidgetItem(format_time(start))
        if row == 0:
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row, 0, item)
        combo = QComboBox()
        combo.addItem(t('自動輪播'), None)
        for path in self.images:
            combo.addItem(path.name, str(path))
        if image:
            combo.setCurrentIndex(max(0, combo.findData(str(image))))
        combo.currentIndexChanged.connect(lambda *_: self._replan())
        self.table.setCellWidget(row, 1, combo)

    def segments(self):
        result = []
        for row in range(self.table.rowCount()):
            try:
                start = parse_time(self.table.item(row, 0).text()) or 0
            except ValueError:
                continue
            data = self.table.cellWidget(row, 1).currentData()
            result.append((start, Path(data) if data else None))
        return sorted(result, key=lambda pair: pair[0])

    def add_cut(self):
        start = self.position
        if start < 500 or any(abs(start - s) < 500 for s, _ in self.segments()):
            return
        self._building = True
        self._add_row(start, None)
        self._building = False
        self._replan()

    def delete_cut(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes() if i.row() > 0}, reverse=True)
        for row in rows:
            self.table.removeRow(row)
        self._replan()

    def reshuffle(self):
        self.seed += 1
        self._replan()

    def _table_changed(self, item):
        if not self._building and item.column() == 0:
            self._replan()

    def _apply_shared_filter(self):
        for i in range(self.pool.count()):
            item = self.pool.item(i)
            item.setHidden(bool(item.data(Qt.ItemDataRole.UserRole + 1)) and not self.use_shared.isChecked())

    def _shared_toggled(self, _checked):
        self._apply_shared_filter()
        self._replan()

    def pool_images(self):
        return [Path(self.pool.item(i).data(Qt.ItemDataRole.UserRole)) for i in range(self.pool.count())
                if self.pool.item(i).checkState() == Qt.CheckState.Checked and not self.pool.item(i).isHidden()]

    def _replan(self):
        if self._building:
            return
        self.scenes = plan_scenes(self.duration, self.pool_images(), self.interval.value() * 1000,
                                  self.segments(), self.seed)
        self.bar.set_data(self.scenes, {s for s, _ in self.segments() if s}, self.duration)
        self._invalidate()

    # ----- preview -----
    def seek(self, ms):
        self.position = max(0, min(self.duration, int(ms)))
        if self.play_timer.isActive():
            self.player.setPosition(self.position)
        self.bar.set_position(self.position)
        self.clock.setText(format_time(self.position))
        self.preview_timer.start(60)

    def _invalidate(self):
        self._renderer = None
        self.preview_timer.start(120)

    def make_renderer(self, size):
        source = self.current()
        take = source['take'] if source and source['kind'] == 'recording' else {}
        return MvRenderer(size, self.scenes, self.timeline, self.speed, self.delay,
                          int(take.get('playback_start_offset_ms', 0)), round(self.fade.value() * 1000),
                          self.size.currentData(), self.pos.currentData(), self.color.currentData(),
                          self.motion.isChecked(), self.seed,
                          song_map=take.get('song_position_map'), song_rate=self.rate.value() if take else 1.0,
                          shift_ms=self.offset.value() if take else 0)

    def _draw_preview(self):
        if not hasattr(self, 'scenes') or self.job:
            return
        if self._renderer is None:
            self._renderer = self.make_renderer(RESOLUTIONS['720p'])
        image = self._renderer.frame(self.position)
        self.preview.setPixmap(QPixmap.fromImage(image).scaled(self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                                               Qt.TransformationMode.SmoothTransformation))
        self.clock.setText(format_time(self.position))

    # ----- export -----
    def _safe_name(self):
        source = self.current()
        name = f"{self.song.get('title', 'MV')} - {source['label']}" if source else 'MV'
        return re.sub(r'[\\/:*?"<>|\r\n]+', '_', name).strip()[:120] or 'MV'

    def start_export(self):
        source = self.current()
        if not source or not self.timeline or self.job:
            return
        default = str(self.workspace / 'mv' / f'{self._safe_name()}.mp4')
        filename, _ = QFileDialog.getSaveFileName(self, t('匯出 MV'), default, 'MP4 (*.mp4)')
        if not filename:
            return
        if not filename.lower().endswith('.mp4'):
            filename += '.mp4'
        self._stop_playback()
        self.status.setText(t('正在準備音訊…'))
        self.export_button.setEnabled(False)
        try:
            audio = self._build_audio(source)
            if not len(audio):
                raise ValueError(t('音訊內容是空的'))
            renderer = self.make_renderer(RESOLUTIONS[self.resolution.currentData()])
            self.job = MvJob(audio, renderer, Path(filename), self)
        except (OSError, RuntimeError, ValueError) as error:
            self.status.setText(t('匯出失敗：{p0}', p0=error))
            self.export_button.setEnabled(True)
            return
        self.job.progress.connect(self._progress)
        self.job.done.connect(self._done)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.cancel_button.setEnabled(True)
        self.status.setText(t('正在輸出影片…'))
        self.job.start()

    def _decoded(self, path):
        key = str(path)
        if key not in self._audio_cache:
            if len(self._audio_cache) >= 3:
                self._audio_cache.pop(next(iter(self._audio_cache)))
            self._audio_cache[key] = decode_audio(path)
        return self._audio_cache[key]

    def _build_audio(self, source):
        if source['kind'] != 'recording':
            return self._decoded(source['path'])
        take = source['take']
        backing = self._backing_for(take)
        return mix_recording(self._decoded(take['audio_path']), self._decoded(backing[0]),
                             backing[1] + self.offset.value(), self.vocal_gain.value(), self.backing_gain.value())

    # ----- listening -----
    def toggle_playback(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._stop_playback()
        else:
            self._start_playback()

    def _start_playback(self):
        source = self.current()
        if not source or self.job:
            return
        stop = getattr(self.controller, '_stop_preview', None)
        if stop:
            stop()
        try:
            audio = self._build_audio(source)
            if self._play_dir is None:
                self._play_dir = Path(tempfile.mkdtemp(prefix='karamorph-mvlisten-'))
            self._mix_count += 1
            wav = self._play_dir / f'listen-{self._mix_count}.wav'
            sf.write(str(wav), audio, 44100, subtype='PCM_16')
        except (OSError, RuntimeError, ValueError, sf.LibsndfileError) as error:
            self.status.setText(t('試聽失敗：{p0}', p0=error))
            return
        self.player.setSource(QUrl())
        for old in self._play_dir.glob('listen-*.wav'):
            if old != wav:
                old.unlink(missing_ok=True)
        box = getattr(self.controller, 'audio_device', None)
        device = (box.currentData() if box is not None else None) or QMediaDevices.defaultAudioOutput()
        self.player_output.setDevice(device)
        self.player_output.setVolume(1.0)
        self.player.setSource(QUrl.fromLocalFile(str(wav)))
        self.player.setPosition(self.position)
        self.player.play()
        self.play_button.setText(t('■ 停止試聽'))
        self.play_timer.start()

    def _stop_playback(self):
        self.restart_timer.stop()
        self.play_timer.stop()
        self.player.stop()
        self.play_button.setText(t('▶ 試聽'))

    def _restart_playback(self):
        """Parameters changed while listening: rebuild the mix and continue from the same spot."""
        if self.play_timer.isActive():
            self.position = self.player.position()
            self._stop_playback()
            self._start_playback()

    def _follow_player(self):
        self.position = max(0, min(self.duration, self.player.position()))
        self.bar.set_position(self.position)
        self.clock.setText(format_time(self.position))
        self.preview_timer.start(0)

    def _player_status(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._stop_playback()

    def _backing_for(self, take):
        """(path, offset_ms of the backing relative to the take) — same rule as take preview."""
        played = take.get('played_backing_path')
        if played and Path(played).is_file():
            return Path(played), 0
        if take.get('played_backing') == 'played_backing.wav':
            raise ValueError(t('這次錄音的伴奏快照已遺失，無法對齊混音。'))
        path = resolve_item({'song_id': self.song_id, 'variant_id': take.get('variant_id', 'original'), 'label': ''},
                            self.controller.song_map, self.workspace)
        if not path:
            raise ValueError(t('找不到這次錄音使用的伴奏版本。'))
        return path, int(take.get('playback_start_offset_ms', 0))

    def _progress(self, done, total):
        self.progress.setMaximum(total)
        self.progress.setValue(done)

    def _done(self, ok, message):
        job, self.job = self.job, None
        if job:
            job.deleteLater()
        self.progress.setVisible(False)
        self.cancel_button.setEnabled(False)
        self.export_button.setEnabled(bool(self.timeline))
        self.status.setText(t('MV 已輸出：{p0}', p0=message) if ok else t('匯出失敗：{p0}', p0=message))
        self._invalidate()

    def cancel_export(self):
        if self.job:
            self.job.cancel()

    def dispose(self):
        self.preview_timer.stop()
        self._stop_playback()
        self.player.setSource(QUrl())
        if self._play_dir:
            shutil.rmtree(self._play_dir, ignore_errors=True)
        if self.job:
            self.job.cancel()
