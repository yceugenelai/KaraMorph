"""Separate karaoke display driven by the consumer window's existing player."""

from app.i18n import t
from pathlib import Path
import math
import random

import numpy as np
from PySide6.QtCore import QElapsedTimer, QEvent, QRectF, QSize, QTimer, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QProgressBar, QPushButton, QSizePolicy, QSlider, QVBoxLayout, QWidget, QWidgetAction
from PySide6.QtMultimedia import QMediaPlayer
from app.ui_icons import icon


def motion_rect(width, height, image_width, image_height, elapsed_ms, scene=0):
    """Full image stays inside the viewport; movement is bounded by free space."""
    wave = math.sin(math.pi * min(1.0, max(0.0, elapsed_ms / 12000)))
    scale = 1.0 - 0.03 * wave
    w, h = image_width * scale, image_height * scale
    sign = -1 if scene % 2 else 1
    dx = sign * wave * min(width * 0.01, max(0, (width - w) / 2))
    dy = -sign * wave * min(height * 0.01, max(0, (height - h) / 2))
    return QRectF((width - w) / 2 + dx, (height - h) / 2 + dy, w, h)


class KaraokeCanvas(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.lines = []
        self.progress = 0.0
        self.synced = False
        self.background = None
        self.scene = 0
        self._background_key = None
        self._scaled_background = None
        self._lyric_cache = {}
        self.empty_lyrics_text = t('尚未加入歌詞')
        self.motion_enabled = False
        self.motion_ms = 0.0
        self.setMinimumSize(640, 360)

    def visible_line_indices(self):
        current = int(self.progress)
        start = current if self.synced else max(0, current - 1)
        return [index for index in range(start, start + (2 if self.synced else 5))
                if 0 <= index < len(self.lines)]

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        colors = (("#142744", "#551b5c"), ("#083a4a", "#195245"), ("#3f2446", "#745024"))
        top, bottom = colors[self.scene % len(colors)]
        gradient = QLinearGradient(0, 0, 0, rect.height())
        gradient.setColorAt(0, QColor(top))
        gradient.setColorAt(1, QColor(bottom))
        painter.fillRect(rect, gradient)
        if self.background and not self.background.isNull():
            pix = self.background
            key = (pix.cacheKey(), rect.width(), rect.height())
            if key != self._background_key:
                factor = min(rect.width() / pix.width(), rect.height() / pix.height())
                self._scaled_background = pix.scaled(round(pix.width() * factor), round(pix.height() * factor),
                                    Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                self._background_key = key
            scaled = self._scaled_background
            if self.motion_enabled:
                target = motion_rect(rect.width(), rect.height(), scaled.width(), scaled.height(),
                                     self.motion_ms, self.scene)
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                painter.drawPixmap(target, scaled, QRectF(scaled.rect()))
            else:
                painter.drawPixmap((rect.width() - scaled.width()) // 2,
                                   (rect.height() - scaled.height()) // 2, scaled)
        else:
            painter.fillRect(rect, QColor("#13263b"))
            painter.setPen(QColor("#a9b8c8"))
            painter.setFont(QFont("Microsoft JhengHei", 22))
            painter.drawText(rect.adjusted(0, 0, 0, -rect.height() // 2),
                             Qt.AlignmentFlag.AlignCenter, "no images")
        if not self.lines:
            painter.setPen(QColor("#f0f5fa"))
            painter.setFont(QFont("Microsoft JhengHei", 24))
            painter.drawText(rect.adjusted(0, rect.height() * 2 // 3, 0, 0), Qt.AlignmentFlag.AlignCenter, self.empty_lyrics_text)
            return
        line_height = max(32, min(68, rect.height() // 10))
        font = QFont("Microsoft JhengHei")
        font.setPixelSize(max(24, min(42, rect.width() // 27)))
        font.setBold(True)
        painter.setFont(font)
        center_y = rect.height() * 5 // 6
        center_index = int(self.progress)
        start_index = center_index if self.synced else max(0, center_index - 1)
        for index in self.visible_line_indices():
            slot = index - start_index
            line = self.lines[index]
            y = (center_y + (slot - 0.5) * line_height if self.synced else
                 rect.height() - (4.5 - slot) * line_height - 16)
            if y < -line_height or y > rect.height() + line_height:
                continue
            text = line or " "
            bounds = rect.adjusted(52, 0, -52, 0)
            bounds.moveTop(round(y - line_height / 2))
            bounds.setHeight(line_height)
            highlighted = index == center_index
            key = (text, bounds.width(), line_height, font.pixelSize(), highlighted)
            pix = self._lyric_cache.get(key)
            if pix is None:
                if len(self._lyric_cache) >= 256:
                    self._lyric_cache.clear()
                pix = QPixmap(bounds.width() + 12, line_height + 12)
                pix.fill(Qt.GlobalColor.transparent)
                ink = QPainter(pix)
                line_font = QFont(font)
                width = QFontMetrics(line_font).horizontalAdvance(text)
                if width > bounds.width():
                    line_font.setPixelSize(max(16, int(line_font.pixelSize() * bounds.width() / width)))
                ink.setFont(line_font)
                local = pix.rect().adjusted(6, 6, -6, -6)
                ink.setPen(QColor(0, 0, 0, 235))
                for dx, dy in ((-3, -3), (0, -3), (3, -3), (-3, 0), (3, 0),
                               (-3, 3), (0, 3), (3, 3), (0, 5)):
                    ink.drawText(local.translated(dx, dy), Qt.AlignmentFlag.AlignCenter, text)
                ink.setPen(QColor("#ffffff") if highlighted else QColor("#d7e1ea"))
                ink.drawText(local, Qt.AlignmentFlag.AlignCenter, text)
                ink.end()
                self._lyric_cache[key] = pix
            painter.drawPixmap(bounds.left() - 6, bounds.top() - 6, pix)


class KaraokeWindow(QWidget):
    def __init__(self, controller):
        super().__init__(None, Qt.WindowType.Window)
        self.controller = controller
        self.setWindowTitle(t('KaraMorph · 唱歌畫面'))
        self.resize(1100, 680)
        self.setStyleSheet("QWidget { color: white; background: #13263b; font-size: 15px; } "
                           "QPushButton { background: #294862; border: 1px solid #50718b; border-radius: 6px; padding: 8px; } "
                           "QPushButton:hover { background: #3a6281; }")
        self.canvas = KaraokeCanvas(self)
        self.images = []
        self.image_index = 0
        self._image_order = []
        self.duration_ms = 0
        self.activity = None
        self.offset_lines = 0.0
        self.speed = 1.0
        self.anchor_base = 0.0
        self.anchor_scroll = 0.0
        self.timeline = None
        self.playback_speed = 1.0
        self.delay_ms = 0
        self.save_delay = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._build_controls(layout)
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.update_progress)
        self.timer.start()
        self.background_timer = QTimer(self)
        self.background_timer.setInterval(12000)
        self.background_timer.timeout.connect(self.next_background)
        self.background_timer.start()
        self.background_remaining = 12000
        self.motion_clock = QElapsedTimer()
        self.motion_timer = QTimer(self)
        self.motion_timer.setInterval(100)
        self.motion_timer.timeout.connect(self._animate_background)
        self.canvas.motion_enabled = bool(controller.settings.get('background_motion', True))

    def set_background_motion(self, enabled):
        self.canvas.motion_enabled = bool(enabled)
        self._sync_background_motion()
        self.canvas.update()

    def _sync_background_motion(self):
        if not hasattr(self, 'motion_timer'):
            return
        visible_playback = (self.canvas.background is not None and not self.canvas.background.isNull()
                            and self.isVisible() and not self.isMinimized()
                            and self.controller.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState)
        if visible_playback and not self.background_timer.isActive():
            self.background_timer.start(max(1, self.background_remaining))
        elif not visible_playback and self.background_timer.isActive():
            self.background_remaining = max(1, self.background_timer.remainingTime())
            self.background_timer.stop()
        active = self.canvas.motion_enabled and visible_playback
        if active and not self.motion_timer.isActive():
            self.motion_clock.start()
            self.motion_timer.start()
        elif not active and self.motion_timer.isActive():
            self.canvas.motion_ms += self.motion_clock.elapsed()
            self.motion_timer.stop()
            self.motion_clock.invalidate()

    def _animate_background(self):
        self._sync_background_motion()
        if self.motion_timer.isActive():
            self.canvas.motion_ms += self.motion_clock.restart()
            self.canvas.update()

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_background_motion()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._sync_background_motion()

    def _icon_button(self, name, title, action=None, checkable=False):
        button = QPushButton()
        button.setIcon(icon(name))
        button.setIconSize(QSize(22, 22))
        button.setFixedSize(36, 36)
        button.setCheckable(checkable)
        button.setToolTip(title)
        button.setAccessibleName(title)
        if action:
            button.clicked.connect(action)
        return button

    def _short_slider(self, value, title):
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(0, 100)
        slider.setValue(value)
        slider.setFixedWidth(120)
        slider.setAccessibleName(title)
        slider.setToolTip(f"{title} {value}%")
        slider.valueChanged.connect(lambda level: slider.setToolTip(f"{title} {level}%"))
        return slider

    def _menu_panel(self, menu):
        widget = QWidget(menu)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(12, 10, 12, 10)
        action = QWidgetAction(menu)
        action.setDefaultWidget(widget)
        menu.addAction(action)
        return widget, layout

    def _build_controls(self, layout):
        self.setStyleSheet(self.styleSheet() +
                          "QPushButton { padding: 4px; } QPushButton:checked { background: #24699a; border: 2px solid #9cd9ff; } "
                          "QMenu { border: 1px solid #50718b; padding: 4px; } QMenu::item { padding: 8px 18px; }")
        header = QHBoxLayout()
        header.setContentsMargins(12, 4, 8, 4)
        self.title = QLabel(t('準備播放'))
        self.full_title = t('準備播放')
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.title.setStyleSheet("font-size: 17px; font-weight: bold;")
        header.addWidget(self.title, 1)
        self.lyrics_status = QLabel(t('無歌詞'))
        self.lyrics_status.setToolTip(t('目前播放歌曲的歌詞格式'))
        header.addWidget(self.lyrics_status)
        self.fullscreen_button = self._icon_button("fullscreen", t('全螢幕／返回'), self.toggle_fullscreen)
        header.addWidget(self.fullscreen_button)
        layout.addLayout(header)
        layout.addWidget(self.canvas, 1)
        self.controls_panel = QWidget()
        self.controls_panel.setFixedHeight(54)
        bar = QHBoxLayout(self.controls_panel)
        bar.setContentsMargins(10, 8, 10, 8)
        bar.setSpacing(6)
        bar.addWidget(self._icon_button("previous", t('上一首'), self.controller.previous_song))
        self.play_button = self._icon_button("play", t('播放'), self.controller.toggle_pause)
        bar.addWidget(self.play_button)
        bar.addWidget(self._icon_button("stop", t('停止'), self.controller.stop_playback))
        bar.addWidget(self._icon_button("next", t('下一首'), self.controller.next_song))
        bar.addStretch()
        music = QLabel()
        music.setPixmap(icon("music").pixmap(22, 22))
        music.setToolTip(t('音樂音量'))
        bar.addWidget(music)
        self.music_volume = self._short_slider(round(self.controller.audio.volume() * 100), t('音樂音量'))
        self.music_volume.setFixedWidth(80)
        self.music_volume.valueChanged.connect(self._music_volume_changed)
        bar.addWidget(self.music_volume)
        self.guide_checkbox = self._icon_button("guide", t('導唱：關'), checkable=True)
        self.guide_checkbox.setEnabled(False)
        self.guide_checkbox.toggled.connect(self._guide_changed)
        bar.addWidget(self.guide_checkbox)
        self.guide_volume = self._short_slider(round(self.controller.guide_audio.volume() * 100), t('導唱音量'))
        self.guide_volume.setFixedWidth(80)
        self.guide_volume.setEnabled(False)
        self.guide_volume.valueChanged.connect(self._guide_volume_changed)
        self.guide_bar_layout = bar
        bar.addWidget(self.guide_volume)
        bar.addStretch()
        self.record_status = self._icon_button("record", t('未錄音'), self._record_help)
        bar.addWidget(self.record_status)
        self.more_button = self._icon_button("more", t('更多設定'))
        bar.addWidget(self.more_button)
        layout.addWidget(self.controls_panel)
        self.more_menu = QMenu(self)
        self.more_button.clicked.connect(lambda: self.more_menu.popup(self.more_button.mapToGlobal(self.more_button.rect().topLeft())))
        self.guide_menu = self.more_menu.addMenu(t('導唱音量'))
        self.guide_menu_widget, self.guide_menu_layout = self._menu_panel(self.guide_menu)
        self.guide_menu.menuAction().setVisible(False)
        lyrics_menu = self.more_menu.addMenu(t('字幕調整'))
        self.lyric_buttons = []
        _, lyric_layout = self._menu_panel(lyrics_menu)
        for title, action in ((t('歌詞 ↓'), lambda: self.shift_lines(1)), (t('歌詞 ↑'), lambda: self.shift_lines(-1)),
                              (t('速度 −'), lambda: self.change_speed(-.1)), (t('速度 +'), lambda: self.change_speed(.1))):
            button = QPushButton(title)
            button.clicked.connect(action)
            self.lyric_buttons.append(button)
            lyric_layout.addWidget(button)
        self.speed_label = QLabel(t('歌詞 1.0×'))
        lyric_layout.addWidget(self.speed_label)
        self.reset_delay_button = QPushButton(t('重設字幕偏移'))
        self.reset_delay_button.clicked.connect(lambda: self.adjust_delay(-self.delay_ms))
        self.reset_delay_button.hide()
        lyric_layout.addWidget(self.reset_delay_button)
        self.mic_panel = QWidget(self)
        self.mic_panel.setFixedHeight(36)
        mic_layout = QHBoxLayout(self.mic_panel)
        mic_layout.setContentsMargins(0, 0, 0, 0)
        mic_layout.setSpacing(6)
        self.monitor_checkbox = self._icon_button("monitor", t('麥克風監聽開關'), checkable=True)
        self.monitor_checkbox.setToolTip(t('監聽：聽到自己的麥克風聲音'))
        self.monitor_checkbox.setAccessibleName(t('麥克風監聽開關'))
        self.monitor_checkbox.toggled.connect(self._monitor_changed)
        mic_layout.addWidget(self.monitor_checkbox)
        self.monitor_volume = self._short_slider(25, t('監聽音量'))
        self.monitor_volume.setFixedWidth(80)
        self.monitor_volume.valueChanged.connect(lambda value: self.controller.microphone.set_monitor_volume(value / 100))
        mic_layout.addWidget(self.monitor_volume)
        self.mic_level = QProgressBar()
        self.mic_level.setFixedWidth(45)
        self.mic_level.setToolTip(t('麥克風輸入音量'))
        self.mic_level.setAccessibleName(t('麥克風輸入音量'))
        self.mic_level.setRange(0, 100)
        self.mic_level.setTextVisible(False)
        self.controller.microphone.levelChanged.connect(lambda value: self.mic_level.setValue(value) if self.mic_panel.isVisible() else None)
        mic_layout.addWidget(self.mic_level)
        bar.insertWidget(bar.indexOf(self.guide_volume) + 1, self.mic_panel)
        info_menu = self.more_menu.addMenu(t('音訊資訊'))
        _, info_layout = self._menu_panel(info_menu)
        self.device_info = QLabel()
        self.device_info.setWordWrap(True)
        self.device_info.setMaximumWidth(320)
        info_layout.addWidget(self.device_info)
        self.buffer_info = QLabel("")
        self.buffer_info.setWordWrap(True)
        self.buffer_info.setMaximumWidth(320)
        self.controller.microphone.bufferInfoChanged.connect(self.buffer_info.setText)
        info_layout.addWidget(self.buffer_info)
        info_menu.aboutToShow.connect(self._update_device_info)
        self.controller.record_checkbox.toggled.connect(self._update_controls_state)
        self._compact = False
        self._controls_state = None
        self._update_controls_state()

    def _guide_changed(self, checked):
        self.guide_volume.setEnabled(checked and self.guide_checkbox.isEnabled())
        self.guide_checkbox.setIcon(icon("guide_on" if checked else "guide"))
        self.guide_checkbox.setToolTip(t('導唱：開') if checked else t('導唱：關'))
        self.guide_checkbox.setAccessibleName(self.guide_checkbox.toolTip())
        self.controller.set_guide_vocal(checked)

    def _record_help(self):
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(self, t('整首錄音'), self.record_status.toolTip() + t('\n請在歌單頁播放前設定整首錄音，播放中不提供錄音切換。'))

    def _update_device_info(self):
        mode = t('WASAPI 獨占') if self.controller.settings.get("singing_audio_mode", "exclusive") == "exclusive" else t('Qt 共用')
        self.device_info.setText(t('播放：{p0}\n輸出：{p1}\n麥克風：{p2}\n設備選擇請到設定頁。', p0=mode, p1=self.controller.audio_device.currentText(), p2=self.controller.microphone_device.currentText()))

    def _update_controls_state(self, *_):
        self._sync_background_motion()
        state = self.controller.player.playbackState()
        recording = bool(self.controller.microphone.is_recording)
        prepared = self.controller.record_checkbox.isChecked()
        signature = (state, recording, prepared, self.guide_checkbox.isEnabled(), self.guide_checkbox.isChecked())
        if signature == self._controls_state:
            return
        self._controls_state = signature
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_button.setIcon(icon("pause" if playing else "play"))
        self.play_button.setToolTip(t('暫停') if playing else t('播放／繼續'))
        self.play_button.setAccessibleName(self.play_button.toolTip())
        if recording:
            text = t('正在錄音') if playing else t('錄音已暫停') if state == QMediaPlayer.PlaybackState.PausedState else t('正在完成錄音')
            name, color = ("recording", "#ff6675") if playing else ("record_ready", "#ffb777")
        elif prepared:
            text, name, color = t('已準備整首錄音；播放後確認麥克風可用'), "record_ready", "#ffb777"
        else:
            text, name, color = t('未錄音'), "record", "#edf5fc"
        self.record_status.setIcon(icon(name, color))
        self.record_status.setToolTip(text)
        self.record_status.setAccessibleName(text)
        self.guide_volume.setEnabled(self.guide_checkbox.isEnabled() and self.guide_checkbox.isChecked())
        if not self.guide_checkbox.isEnabled():
            self.guide_checkbox.setToolTip(t('無獨立人聲音軌，無法開啟導唱；原曲已包含人聲'))
        else:
            self.guide_checkbox.setToolTip(t('導唱：開') if self.guide_checkbox.isChecked() else t('導唱：關'))
        self.guide_checkbox.setAccessibleName(self.guide_checkbox.toolTip())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, "guide_volume"):
            return
        self.title.setText(self.title.fontMetrics().elidedText(self.full_title, Qt.TextElideMode.ElideRight, self.title.width()))
        compact = self.width() < 820
        if compact != self._compact:
            self._compact = compact
            if compact:
                self.guide_bar_layout.removeWidget(self.guide_volume)
                self.guide_menu_layout.addWidget(self.guide_volume)
            else:
                self.guide_menu_layout.removeWidget(self.guide_volume)
                self.guide_bar_layout.insertWidget(self.guide_bar_layout.indexOf(self.guide_checkbox) + 1, self.guide_volume)
            self.guide_menu.menuAction().setVisible(compact)
            self.guide_volume.show()

    def set_song(self, title: str, lyrics: str, images: list[Path], duration_ms: int,
                 timeline=None, playback_speed=1.0, delay_ms=0, save_delay=None):
        self.title.setText(title)
        self.full_title = title
        self.title.setToolTip(title)
        self.title.setText(self.title.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, self.title.width()))
        self.timeline = timeline
        self.playback_speed = playback_speed
        self.delay_ms = delay_ms
        self.save_delay = save_delay
        self.canvas.synced = timeline is not None
        self.canvas.lines = timeline.lines if timeline else (lyrics.splitlines() if lyrics.strip() else [])
        self.lyrics_status.setText(t('LRC 同步歌詞') if timeline is not None else
                                   t('純文字歌詞') if self.canvas.lines else t('無歌詞'))
        self.canvas._lyric_cache.clear()
        self.images = list(dict.fromkeys(images))
        self.image_index = -1
        self._image_order = []
        self._advance_background()
        self.duration_ms = duration_ms
        self.activity = None
        self.offset_lines = 0.0
        self.speed = 1.0
        self.anchor_base = 0.0
        self.anchor_scroll = 0.0
        self.speed_label.setText(t('歌詞 1.0×'))
        for index, button in enumerate(self.lyric_buttons):
            button.setVisible(not timeline or index < 2)
            button.setText((t('字幕延後 0.2 秒'), t('字幕提早 0.2 秒'))[index] if timeline and index < 2 else
                           (t('歌詞 ↓'), t('歌詞 ↑'), t('速度 −'), t('速度 +'))[index])
        self.reset_delay_button.setVisible(timeline is not None)
        if timeline:
            self.speed_label.setText(t('字幕偏移 {p0:+.1f} 秒', p0=delay_ms / 1000))
        self._show_background()
        self.update_progress()
        self.canvas.update()

    def set_activity(self, edges: np.ndarray, voiced: np.ndarray):
        if self.timeline:
            return
        current_scroll = self.anchor_scroll + (self._base_progress() - self.anchor_base) * self.speed
        self.activity = (edges, voiced)
        self.anchor_base = self._base_progress()
        self.anchor_scroll = current_scroll
        self.update_progress()

    def _base_progress(self):
        if not self.canvas.lines:
            return 0.0
        seconds = self.controller.player.position() / 1000
        if self.activity:
            edges, voiced = self.activity
            fraction = float(np.interp(seconds, edges, voiced) / voiced[-1]) if voiced[-1] else 0.0
        else:
            duration = self.controller.player.duration() or self.duration_ms
            fraction = min(1.0, seconds * 1000 / duration) if duration > 0 else 0.0
        return fraction * max(0, len(self.canvas.lines) - 1)

    def update_progress(self):
        if self.sender() is self.timer and not self.isVisible():
            return
        self._update_controls_state()
        if self.timeline:
            progress = self.timeline.index_at(self.controller.player.position(), self.playback_speed, self.delay_ms)
        else:
            base = self._base_progress()
            progress = math.floor(max(0.0, min(len(self.canvas.lines) - 1,
                self.anchor_scroll + (base - self.anchor_base) * self.speed + self.offset_lines))) if self.canvas.lines else 0
        if progress != self.canvas.progress:
            self.canvas.progress = progress
            self.canvas.update()

    def shift_lines(self, amount):
        if self.timeline:
            self.adjust_delay(amount * 200)
            return
        self.offset_lines += amount
        self.update_progress()

    def adjust_delay(self, amount):
        if not self.timeline:
            return
        previous = self.delay_ms
        self.delay_ms += amount
        if self.save_delay:
            try:
                self.save_delay(self.delay_ms)
            except (OSError, ValueError) as error:
                self.delay_ms = previous
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(self, t('儲存字幕偏移失敗'), str(error))
        self.speed_label.setText(t('字幕偏移 {p0:+.1f} 秒', p0=self.delay_ms / 1000))
        self.update_progress()

    def change_speed(self, amount):
        base = self._base_progress()
        self.anchor_scroll += (base - self.anchor_base) * self.speed
        self.anchor_base = base
        self.speed = round(max(0.5, min(2.0, self.speed + amount)), 1)
        self.speed_label.setText(t('歌詞 {p0:.1f}×', p0=self.speed))
        self.update_progress()

    def _show_background(self):
        if hasattr(self, 'motion_timer'):
            self.motion_timer.stop()
            self.motion_clock.invalidate()
        self.canvas.motion_ms = 0.0
        self.canvas.background = QPixmap(str(self.images[self.image_index])) if self.images else None
        self.canvas.scene = self.image_index
        self._sync_background_motion()
        self.canvas.update()

    def _advance_background(self):
        if not self.images:
            self.image_index = 0
            return
        if not self._image_order:
            self._image_order = list(range(len(self.images)))
            random.shuffle(self._image_order)
            if len(self._image_order) > 1 and self._image_order[0] == self.image_index:
                self._image_order[0], self._image_order[1] = self._image_order[1], self._image_order[0]
        self.image_index = self._image_order.pop(0)

    def next_background(self):
        if not self.images:
            return
        self.background_remaining = 12000
        self.background_timer.setInterval(12000)
        self._advance_background()
        self._show_background()

    def toggle_fullscreen(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()
        self.fullscreen_button.setIcon(icon("restore" if self.isFullScreen() else "fullscreen"))

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._sync_background_motion()
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "fullscreen_button"):
            self.fullscreen_button.setIcon(icon("restore" if self.isFullScreen() else "fullscreen"))

    def _music_volume_changed(self, value):
        self.music_volume.setToolTip(t('音樂音量 {p0}%', p0=value))
        self.controller._set_singing_volume(value)

    def _guide_volume_changed(self, value):
        self.guide_volume.setToolTip(t('導唱音量 {p0}%', p0=value))
        self.controller._set_guide_volume(value)

    def closeEvent(self, event):
        self.guide_checkbox.setChecked(False)
        self.controller.stop_playback()
        self.monitor_checkbox.setChecked(False)
        self.more_menu.close()
        super().closeEvent(event)

    def _monitor_changed(self, enabled):
        try:
            self.controller.microphone.set_monitor(enabled, self.controller.audio_device.currentData())
        except Exception as error:
            self.monitor_checkbox.blockSignals(True)
            self.monitor_checkbox.setChecked(False)
            self.monitor_checkbox.blockSignals(False)
            self.controller.microphone.set_monitor(False)
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, t('監聽無法使用'), str(error))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self.isFullScreen():
            self.showNormal()
            return
        super().keyPressEvent(event)
