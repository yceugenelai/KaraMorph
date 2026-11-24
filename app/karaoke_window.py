"""Separate karaoke display driven by the consumer window's existing player."""

from app.i18n import t
from pathlib import Path
import math
import random
import time

import numpy as np
from PySide6.QtCore import QElapsedTimer, QEvent, QRectF, QSize, QTimer, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QProgressBar, QPushButton, QSizePolicy, QSlider, QSpinBox, QVBoxLayout, QWidget, QWidgetAction
from PySide6.QtMultimedia import QMediaPlayer
from app.ui_icons import icon
from app.application_identity import application_icon


def motion_rect(width, height, image_width, image_height, elapsed_ms, scene=0):
    """Full image stays inside the viewport; movement is bounded by free space."""
    wave = math.sin(math.pi * min(1.0, max(0.0, elapsed_ms / 12000)))
    scale = 1.0 - 0.03 * wave
    w, h = image_width * scale, image_height * scale
    sign = -1 if scene % 2 else 1
    dx = sign * wave * min(width * 0.01, max(0, (width - w) / 2))
    dy = -sign * wave * min(height * 0.01, max(0, (height - h) / 2))
    return QRectF((width - w) / 2 + dx, (height - h) / 2 + dy, w, h)


def drift_rect(width, height, image_width, image_height, fraction, seed=0):
    """Slow zoom in/out plus a pan; the image always covers the viewport (no gaps)."""
    rng = random.Random(seed * 7919 + 13)
    zoom_in = rng.random() < 0.5
    amount = rng.uniform(0.08, 0.14)
    pan_x, pan_y = rng.uniform(-1, 1), rng.uniform(-1, 1)
    p = min(1.0, max(0.0, fraction))
    zoom = 1.0 + amount * (p if zoom_in else 1.0 - p)
    w, h = image_width * zoom, image_height * zoom
    slack_x, slack_y = max(0.0, (w - width) / 2), max(0.0, (h - height) / 2)
    return QRectF((width - w) / 2 - pan_x * slack_x * (2 * p - 1) * 0.8,
                  (height - h) / 2 - pan_y * slack_y * (2 * p - 1) * 0.8, w, h)


def depth_mask_pixmap(path):
    """Convert a grayscale depth-motion mask to an alpha pixmap."""
    source = QPixmap(str(path))
    if source.isNull():
        return None
    gray = source.toImage().convertToFormat(QImage.Format.Format_Grayscale8)
    alpha = np.frombuffer(gray.constBits(), dtype=np.uint8, count=gray.sizeInBytes()).reshape(
        gray.height(), gray.bytesPerLine())[:, :gray.width()]
    rgba = np.full((gray.height(), gray.width(), 4), 255, dtype=np.uint8)
    rgba[:, :, 3] = alpha
    return QPixmap.fromImage(QImage(rgba.data, gray.width(), gray.height(), rgba.strides[0],
                                    QImage.Format.Format_RGBA8888).copy())


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
        self.depth_mask = None
        self.depth_motion_factor = 1.0
        self.depth_strength = .3
        self.depth_speed = .4
        self._depth_mask_key = None
        self._scaled_depth_mask = None
        self.previous_background_frame = None
        self.transition_ms = 0.0
        self.transition_duration_ms = 2000
        # Appearance hooks used by the MV exporter; defaults match the live karaoke view.
        self.no_image_text = "no images"
        self.font_scale = 1.0
        self.drift = None  # (fraction through the scene, seed) turns on the MV zoom/pan motion
        self.fill_background = False  # crop to fill the frame instead of letterboxing
        self.ui_scale = None  # set for resolution-independent sizing (1.0 = 1280x720 look)
        self.center_ratio = 5 / 6
        self.highlight_color = "#ffffff"
        self.setMinimumSize(640, 360)

    def visible_line_indices(self):
        current = int(self.progress)
        start = current if self.synced else max(0, current - 1)
        return [index for index in range(start, start + (2 if self.synced else 5))
                if 0 <= index < len(self.lines)]

    def _paint_background(self, painter, rect):
        colors = (("#142744", "#551b5c"), ("#083a4a", "#195245"), ("#3f2446", "#745024"))
        top, bottom = colors[self.scene % len(colors)]
        gradient = QLinearGradient(0, 0, 0, rect.height())
        gradient.setColorAt(0, QColor(top))
        gradient.setColorAt(1, QColor(bottom))
        painter.fillRect(rect, gradient)
        if self.background and not self.background.isNull():
            pix = self.background
            key = (pix.cacheKey(), rect.width(), rect.height(), self.fill_background)
            if key != self._background_key:
                factor = (max if self.fill_background else min)(rect.width() / pix.width(), rect.height() / pix.height())
                fill = self.fill_background
                self._scaled_background = pix.scaled(
                    (math.ceil if fill else round)(pix.width() * factor), (math.ceil if fill else round)(pix.height() * factor),
                    Qt.AspectRatioMode.IgnoreAspectRatio if fill else Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
                self._background_key = key
            scaled = self._scaled_background
            if self.motion_enabled and self.depth_mask is not None and not self.depth_mask.isNull():
                from app.depth_motion import strip_offset
                mask_key = (self.depth_mask.cacheKey(), scaled.width(), scaled.height())
                if mask_key != self._depth_mask_key:
                    self._scaled_depth_mask = self.depth_mask.scaled(
                        scaled.size(), Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.FastTransformation)
                    self._depth_mask_key = mask_key
                left = (rect.width() - scaled.width()) // 2
                top = (rect.height() - scaled.height()) // 2
                if self.drift is None:
                    painter.drawPixmap(left, top, scaled)
                moving = QPixmap(scaled.size())
                moving.fill(Qt.GlobalColor.transparent)
                layer = QPainter(moving)
                layer.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                scale = scaled.height() / 1080
                strip = max(1, round(12 * scale))
                pad = max(2, math.ceil(32 * self.depth_strength * self.depth_motion_factor * scale) + 2)
                for y in range(0, scaled.height(), strip):
                    height = min(strip, scaled.height() - y)
                    x0, v0 = strip_offset(self.motion_ms, y / scaled.height(),
                                          strength=self.depth_strength * self.depth_motion_factor, speed=self.depth_speed)
                    x1, v1 = strip_offset(self.motion_ms, (y + height) / scaled.height(),
                                          strength=self.depth_strength * self.depth_motion_factor, speed=self.depth_speed)
                    sy0 = max(0.0, min(float(scaled.height() - 1), y + v0 * scale))
                    sy1 = max(sy0 + .01, min(float(scaled.height()), y + height + v1 * scale))
                    layer.save()
                    layer.setClipRect(QRectF(0, y, scaled.width(), height))
                    layer.translate(x0 * scale, y)
                    layer.shear((x1 - x0) * scale / height, 0)
                    layer.drawPixmap(QRectF(0, 0, scaled.width(), height), scaled,
                                     QRectF(0, sy0, scaled.width(), sy1 - sy0))
                    layer.drawPixmap(QRectF(-pad, 0, pad, height), scaled, QRectF(0, sy0, 1, sy1 - sy0))
                    layer.drawPixmap(QRectF(scaled.width(), 0, pad, height), scaled,
                                     QRectF(scaled.width() - 1, sy0, 1, sy1 - sy0))
                    layer.restore()
                layer.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
                layer.drawPixmap(0, 0, self._scaled_depth_mask)
                layer.end()
                if self.drift is None:
                    painter.drawPixmap(left, top, moving)
                else:
                    combined = QPixmap(scaled.size())
                    combined.fill(Qt.GlobalColor.transparent)
                    composite = QPainter(combined)
                    composite.drawPixmap(0, 0, scaled)
                    composite.drawPixmap(0, 0, moving)
                    composite.end()
                    target = drift_rect(rect.width(), rect.height(), scaled.width(), scaled.height(), *self.drift)
                    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                    painter.drawPixmap(target, combined, QRectF(combined.rect()))
            elif self.drift is not None:
                target = drift_rect(rect.width(), rect.height(), scaled.width(), scaled.height(), *self.drift)
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                painter.drawPixmap(target, scaled, QRectF(scaled.rect()))
            elif self.motion_enabled:
                target = motion_rect(rect.width(), rect.height(), scaled.width(), scaled.height(),
                                     self.motion_ms, self.scene)
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                painter.drawPixmap(target, scaled, QRectF(scaled.rect()))
            else:
                painter.drawPixmap((rect.width() - scaled.width()) // 2,
                                   (rect.height() - scaled.height()) // 2, scaled)
        else:
            painter.fillRect(rect, QColor("#13263b"))
            if self.no_image_text:
                painter.setPen(QColor("#a9b8c8"))
                painter.setFont(QFont("Microsoft JhengHei", 22))
                painter.drawText(rect.adjusted(0, 0, 0, -rect.height() // 2),
                                 Qt.AlignmentFlag.AlignCenter, self.no_image_text)
        if self.previous_background_frame is not None:
            painter.save()
            painter.setOpacity(max(0.0, 1.0 - self.transition_ms / self.transition_duration_ms))
            painter.drawPixmap(QRectF(rect), self.previous_background_frame,
                               QRectF(self.previous_background_frame.rect()))
            painter.restore()

    def capture_background(self):
        """Capture only the background, never the lyrics, for the next transition."""
        if not self.background or self.background.isNull() or self.size().isEmpty():
            return None
        frame = QPixmap(self.size())
        frame.fill(Qt.GlobalColor.transparent)
        painter = QPainter(frame)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_background(painter, self.rect())
        painter.end()
        return frame

    def start_background_transition(self, frame):
        self.previous_background_frame = frame if frame and not frame.isNull() else None
        self.transition_ms = 0.0

    def advance_background_transition(self, elapsed_ms):
        if self.previous_background_frame is not None:
            self.transition_ms += max(0, elapsed_ms)
            if self.transition_ms >= self.transition_duration_ms:
                self.previous_background_frame = None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        self._paint_background(painter, rect)
        if not self.lines:
            painter.setPen(QColor("#f0f5fa"))
            painter.setFont(QFont("Microsoft JhengHei", 24))
            painter.drawText(rect.adjusted(0, rect.height() * 2 // 3, 0, 0), Qt.AlignmentFlag.AlignCenter, self.empty_lyrics_text)
            return
        line_height = (round(68 * self.ui_scale) if self.ui_scale else max(32, min(68, rect.height() // 10)))
        font = QFont("Microsoft JhengHei")
        font.setPixelSize(max(24, round((42 * self.ui_scale if self.ui_scale else min(42, rect.width() // 27)) * self.font_scale)))
        font.setBold(True)
        painter.setFont(font)
        center_y = round(rect.height() * self.center_ratio)
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
            key = (text, bounds.width(), line_height, font.pixelSize(), highlighted, self.highlight_color)
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
                ink.setPen(QColor(self.highlight_color) if highlighted else QColor("#d7e1ea"))
                ink.drawText(local, Qt.AlignmentFlag.AlignCenter, text)
                ink.end()
                self._lyric_cache[key] = pix
            painter.drawPixmap(bounds.left() - 6, bounds.top() - 6, pix)


class KaraokeWindow(QWidget):
    def __init__(self, controller):
        super().__init__(None, Qt.WindowType.Window)
        self.controller = controller
        self.setWindowTitle(t('KaraMorph · 唱歌畫面'))
        self.setWindowIcon(application_icon())
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
        self.depth_enabled = bool(controller.settings.get('background_depth_motion', True))
        from app.depth_settings import depth_settings, mask_options
        options = depth_settings(controller.settings)
        self.canvas.depth_strength = options['strength'] / 100
        self.canvas.depth_speed = options['speed'] / 100
        self._depth_configuration = mask_options(controller.settings)
        self._depth_task = None
        self._depth_task_key = None
        self._depth_task_started = 0.0
        self._depth_results = {}
        self._depth_failed = set()

    def set_depth_motion(self, enabled):
        from app.depth_settings import depth_settings, mask_options
        options = depth_settings(self.controller.settings)
        self.canvas.depth_strength = options['strength'] / 100
        self.canvas.depth_speed = options['speed'] / 100
        config = mask_options(self.controller.settings)
        if config != self._depth_configuration:
            if self._depth_task:
                self._depth_task.cancel()
                self._depth_task = None
                self._depth_task_key = None
            self._depth_results.clear()
            self._depth_failed.clear()
            self.canvas.depth_mask = None
            self._depth_configuration = config
        self.depth_enabled = bool(enabled)
        if not self.depth_enabled:
            if self._depth_task:
                self._depth_task.cancel()
                self._depth_task = None
                self._depth_task_key = None
            self.canvas.depth_mask = None
            self.motion_timer.setInterval(100)
            self._set_background_status(t('背景：一般動態') if self.canvas.motion_enabled else t('背景：靜態'))
        else:
            self._start_depth_current()
        self.canvas.update()

    def _depth_current_key(self):
        if not self.images or not 0 <= self.image_index < len(self.images):
            return None
        path = Path(self.images[self.image_index]).resolve()
        try:
            stat = path.stat()
        except OSError:
            return None
        return str(path), stat.st_size, stat.st_mtime_ns

    def _start_depth_current(self):
        if not self.canvas.motion_enabled:
            self._set_background_status(t('背景：靜態'))
            return
        if not self.depth_enabled:
            self._set_background_status(t('背景：一般動態'))
            return
        if self._depth_task:
            self._set_background_status(t('背景：一般動態 · 等待深度處理'),
                                        t('正在處理圖片；若處理期間切換背景，完成後會繼續處理目前圖片。'))
            return
        key = self._depth_current_key()
        if key is None:
            self._set_background_status(t('背景：沒有圖片'))
            return
        if key in self._depth_failed:
            self._set_background_status(t('背景：一般動態 · 深度處理失敗'))
            return
        if key in self._depth_results:
            self._apply_depth_result(self._depth_results[key])
            return
        from runtime_config import ROOT
        from app.karaoke_assets import DepthMotionTask
        from app.model_store import ModelStore
        from app.depth_cache import lookup
        model = ROOT / 'models/depth/Depth-Anything-V2-Small-hf'
        if not ModelStore(self.controller.settings).ready('depth'):
            self._set_background_status(t('背景：一般動態 · 深度模型未準備'),
                                        t('請至模型與元件管理準備背景深度動畫模型。'))
            return
        cached = lookup(ROOT / '.app_data/cache/depth-motion', key[0], model, self.controller.settings)
        if cached:
            self._depth_results[key] = cached
            self._apply_depth_result(cached)
            return
        python = self.controller.runtime('acestep_python')
        if not python:
            self._set_background_status(t('背景：一般動態 · 缺少執行元件'),
                                        t('請至設定中的管理安裝準備風格轉換執行元件。'))
            return
        task = DepthMotionTask(key[0], python, model, self, settings=self.controller.settings)
        self._depth_task = task
        self._depth_task_key = key
        self._depth_task_started = time.monotonic()
        self._set_background_status(t('背景：一般動態 · 深度處理中'),
                                    t('首次處理可能超過背景切換時間；完成結果會快取。'))
        task.ready.connect(lambda _image, result, key=key: self._depth_ready(key, result))
        task.failed.connect(lambda message, key=key: self._depth_error(key, message))
        task.start()

    def _apply_depth_result(self, result):
        mask = depth_mask_pixmap(result['mask'])
        if mask is None:
            self._set_background_status(t('背景：一般動態 · 深度遮罩無效'))
            return
        self.canvas.depth_mask = mask
        self.canvas.depth_motion_factor = float(result.get('motion_factor', 1.0))
        names = {'near': 'A3-1', 'ground': 'A3-3', 'detrend': 'A3-4',
                 'inverse': 'A3-2', 'local': 'A3-7'}
        name = names.get(result.get('method'), 'A3')
        coverage = float(result.get('coverage', 0)) * 100
        self._set_background_status(t('背景：深度 {mode} · {coverage:.0f}%', mode=name, coverage=coverage),
                                    t('目前顯示深度局部動畫；覆蓋率為遮罩比例。') if result.get('in_range', True)
                                    else t('遮罩覆蓋率未達預設範圍，效果可能不明顯或範圍過大。'))
        self.motion_timer.setInterval(33)
        self.canvas.update()

    def _depth_ready(self, key, result):
        self._depth_task = None
        self._depth_task_key = None
        self._depth_results[key] = result
        refresh = getattr(self.controller, 'refresh_depth_indicator', None)
        if callable(refresh):
            refresh()
        if self.depth_enabled and key == self._depth_current_key():
            self._apply_depth_result(result)
            if self.canvas.depth_mask is not None:
                if self.background_timer.isActive() and self.background_timer.remainingTime() < 5000:
                    self.background_timer.start(5000)
                self.background_remaining = max(self.background_remaining, 5000)
        else:
            self._start_depth_current()

    def _depth_error(self, key, message):
        import logging
        logging.getLogger(__name__).warning('Depth animation unavailable: %s', message)
        self._depth_task = None
        self._depth_task_key = None
        self._depth_failed.add(key)
        if key == self._depth_current_key():
            self._set_background_status(t('背景：一般動態 · 深度處理失敗'), message)
        else:
            self._start_depth_current()

    def set_background_motion(self, enabled):
        self.canvas.motion_enabled = bool(enabled)
        if not enabled and self._depth_task:
            self._depth_task.cancel()
            self._depth_task = None
            self._depth_task_key = None
        if enabled and self.depth_enabled:
            self._start_depth_current()
        elif not enabled:
            self._set_background_status(t('背景：靜態'))
        else:
            self._set_background_status(t('背景：一般動態'))
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
        transitioning = self.canvas.previous_background_frame is not None
        interval = 33 if transitioning or (self.canvas.motion_enabled and self.canvas.depth_mask is not None) else 100
        if self.motion_timer.interval() != interval:
            self.motion_timer.setInterval(interval)
        active = (self.canvas.motion_enabled or transitioning) and visible_playback
        if active and not self.motion_timer.isActive():
            self.motion_clock.start()
            self.motion_timer.start()
        elif not active and self.motion_timer.isActive():
            elapsed = self.motion_clock.elapsed()
            if self.canvas.motion_enabled:
                self.canvas.motion_ms += elapsed
            self.canvas.advance_background_transition(elapsed)
            self.motion_timer.stop()
            self.motion_clock.invalidate()

    def _animate_background(self):
        self._sync_background_motion()
        if self.motion_timer.isActive():
            elapsed = self.motion_clock.restart()
            if self.canvas.motion_enabled:
                self.canvas.motion_ms += elapsed
            was_transitioning = self.canvas.previous_background_frame is not None
            self.canvas.advance_background_transition(elapsed)
            self.canvas.update()
            if was_transitioning and self.canvas.previous_background_frame is None:
                self._sync_background_motion()

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

    def _set_background_status(self, text, detail=None):
        self.background_status.setText(text)
        self.background_status.setToolTip(detail or text)

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
        self.background_status = QLabel(t('背景：一般動態'))
        self.background_status.setStyleSheet('font-size: 12px; color: #b8d0e4;')
        header.addWidget(self.background_status)
        self.lyrics_status = QLabel(t('無歌詞'))
        self.lyrics_status.setToolTip(t('目前播放歌曲的歌詞格式'))
        header.addWidget(self.lyrics_status)
        self.fullscreen_button = self._icon_button("fullscreen", t('全螢幕／返回'), self.toggle_fullscreen)
        header.addWidget(self.fullscreen_button)
        layout.addLayout(header)
        layout.addWidget(self.canvas, 1)
        self.controls_panel = QWidget()
        self.controls_panel.setFixedHeight(54)
        self.controls_panel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.controls_layout = QVBoxLayout(self.controls_panel)
        self.controls_layout.setContentsMargins(10, 8, 10, 8)
        self.controls_layout.setSpacing(4)
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        self.controls_layout.addLayout(bar)
        bar.setSpacing(6)
        bar.addWidget(self._icon_button("previous", t('上一首'), self.controller.previous_song))
        self.play_button = self._icon_button("play", t('播放'), self.controller.toggle_pause)
        bar.addWidget(self.play_button)
        bar.addWidget(self._icon_button("stop", t('停止'), self.controller.stop_playback))
        bar.addWidget(self._icon_button("next", t('下一首'), self.controller.next_song))
        self.bottom_effects = QWidget()
        effect_bar = QHBoxLayout(self.bottom_effects)
        effect_bar.setContentsMargins(0, 0, 0, 0)
        effect_bar.setSpacing(6)
        self.speed_down = QPushButton('-')
        self.speed_up = QPushButton('+')
        self.bottom_speed_label = QLabel('1.00×')
        for button, step in ((self.speed_down, -10), (self.speed_up, 10)):
            button.setFixedSize(28, 32)
            button.setToolTip(t('播放速度') + f' {step / 100:+.1f}×')
            button.setAccessibleName(button.toolTip())
            button.clicked.connect(lambda checked=False, delta=step:
                                   self.live_speed.setValue(self.live_speed.value() + delta))
        effect_bar.addWidget(self.speed_down)
        effect_bar.addWidget(self.bottom_speed_label)
        effect_bar.addWidget(self.speed_up)
        effect_bar.addWidget(QLabel('Key'))
        self.live_key = QSpinBox()
        self.live_key.setRange(-6, 6)
        self.live_key.setFixedWidth(100)
        self.live_key.setAccessibleName(t('升降 Key（半音）'))
        effect_bar.addWidget(self.live_key)
        self.best_button = self._icon_button('star', t('儲存為我的最佳'), self._best_clicked)
        self.best_button.setFixedSize(32, 32)
        self.original_button = QPushButton(t('原始'))
        self.original_button.clicked.connect(lambda: self.reset_live_effects())
        effect_bar.addWidget(self.best_button)
        effect_bar.addWidget(self.original_button)
        bar.addWidget(self.bottom_effects)
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
        self.effects_menu = self.more_menu.addMenu(t('播放速度／Key'))
        _, effects_layout = self._menu_panel(self.effects_menu)
        self.live_speed_label = QLabel(t('播放速度 {p0:.2f}×', p0=1.))
        effects_layout.addWidget(self.live_speed_label)
        self.live_speed = QSlider(Qt.Orientation.Horizontal)
        self.live_speed.setRange(70, 130)
        self.live_speed.setValue(100)
        self.live_speed.setMinimumWidth(220)
        self.live_speed.setAccessibleName(t('播放速度'))
        effects_layout.addWidget(self.live_speed)
        reset_effects = QPushButton(t('重設播放速度／Key'))
        reset_effects.clicked.connect(lambda: self.reset_live_effects())
        effects_layout.addWidget(reset_effects)
        self.best_status = QLabel()
        effects_layout.addWidget(self.best_status)
        self.apply_best_button = QPushButton(t('套用我的最佳'))
        self.apply_best_button.clicked.connect(lambda: self._apply_best())
        effects_layout.addWidget(self.apply_best_button)
        self.clear_best_button = QPushButton(t('清除已存設定'))
        self.clear_best_button.clicked.connect(lambda: self.controller.clear_best_effects())
        effects_layout.addWidget(self.clear_best_button)
        self.set_best_state(None, False)
        effects_note = QLabel(t('只調整歌曲與導唱；麥克風維持原聲。有存過最佳設定的歌會自動套用，否則從 1.0×／0 半音開始。'))
        effects_note.setWordWrap(True)
        effects_note.setMaximumWidth(280)
        effects_layout.addWidget(effects_note)
        self.effects_timer = QTimer(self)
        self.effects_timer.setSingleShot(True)
        self.effects_timer.setInterval(180)
        self.effects_timer.timeout.connect(self._apply_live_effects)
        self.live_speed.valueChanged.connect(self._live_effects_changed)
        self.live_key.valueChanged.connect(self._live_effects_changed)
        self.more_button.clicked.connect(lambda: self.more_menu.popup(self.more_button.mapToGlobal(self.more_button.rect().topLeft())))
        self.guide_menu = self.more_menu.addMenu(t('導唱音量'))
        self.guide_menu_widget, self.guide_menu_layout = self._menu_panel(self.guide_menu)
        self.guide_menu.menuAction().setVisible(False)
        self.mic_panel = QWidget(self)
        self.mic_panel.setFixedHeight(36)
        mic_layout = QHBoxLayout(self.mic_panel)
        mic_layout.setContentsMargins(0, 0, 0, 0)
        mic_layout.setSpacing(6)
        self.monitor_checkbox = self._icon_button("monitor", t('麥克風監聽開關'), checkable=True)
        self.monitor_checkbox.setToolTip(t('監聽：聽到自己的麥克風聲音'))
        self.monitor_checkbox.setAccessibleName(t('麥克風監聽開關'))
        self.monitor_checkbox.setChecked(self.controller.audio_preferences['monitor_enabled'])
        self.monitor_checkbox.toggled.connect(self._monitor_changed)
        mic_layout.addWidget(self.monitor_checkbox)
        self.monitor_volume = self._short_slider(self.controller.audio_preferences['monitor_volume'], t('監聽音量'))
        self.monitor_volume.setFixedWidth(80)
        self.monitor_volume.valueChanged.connect(self.controller._set_monitor_volume)
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
        self.controller.remember_audio_preference('guide_enabled', checked)
        self.guide_volume.setEnabled(checked and self.guide_checkbox.isEnabled())
        self.guide_checkbox.setIcon(icon("guide_on" if checked else "guide"))
        self.guide_checkbox.setToolTip(t('導唱：開') if checked else t('導唱：關'))
        self.guide_checkbox.setAccessibleName(self.guide_checkbox.toolTip())
        self.controller.set_guide_vocal(checked)

    def set_guide_available(self, available):
        self.guide_checkbox.setEnabled(available)
        previous = self.guide_checkbox.blockSignals(True)
        self.guide_checkbox.setChecked(available and self.controller.audio_preferences['guide_enabled'])
        self.guide_checkbox.blockSignals(previous)
        self.guide_checkbox.setIcon(icon('guide_on' if self.guide_checkbox.isChecked() else 'guide'))
        self._update_controls_state()

    def set_monitor_checked(self, enabled):
        previous = self.monitor_checkbox.blockSignals(True)
        self.monitor_checkbox.setChecked(enabled)
        self.monitor_checkbox.blockSignals(previous)

    def _record_help(self):
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(self, t('整首錄音'), self.record_status.toolTip() + t('\n請在歌單頁播放前設定整首錄音，播放中不提供錄音切換。'))

    def _update_device_info(self):
        mode = t('WASAPI 獨占') if self.controller.settings.get("singing_audio_mode", "exclusive") == "exclusive" else t('WASAPI 共用')
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
        compact = self.width() < 1020
        if compact != self._compact:
            self._compact = compact
            if compact:
                self.guide_bar_layout.removeWidget(self.bottom_effects)
                self.controls_layout.addWidget(self.bottom_effects, 0, Qt.AlignmentFlag.AlignHCenter)
                self.controls_panel.setFixedHeight(94)
                self.guide_bar_layout.removeWidget(self.guide_volume)
                self.guide_menu_layout.addWidget(self.guide_volume)
            else:
                self.controls_layout.removeWidget(self.bottom_effects)
                self.guide_bar_layout.insertWidget(4, self.bottom_effects)
                self.controls_panel.setFixedHeight(54)
                self.guide_menu_layout.removeWidget(self.guide_volume)
                self.guide_bar_layout.insertWidget(self.guide_bar_layout.indexOf(self.guide_checkbox) + 1, self.guide_volume)
            self.guide_menu.menuAction().setVisible(compact)
            self.guide_volume.show()
            self.bottom_effects.show()

    def set_song(self, title: str, lyrics: str, images: list[Path], duration_ms: int,
                 timeline=None, playback_speed=1.0, delay_ms=0):
        previous_path = (self.images[self.image_index] if self.images and
                         0 <= self.image_index < len(self.images) else None)
        previous_frame = self.canvas.capture_background() if self.isVisible() and previous_path else None
        self.title.setText(title)
        self.full_title = title
        self.title.setToolTip(title)
        self.title.setText(self.title.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, self.title.width()))
        self.timeline = timeline
        self.playback_speed = playback_speed
        self.delay_ms = delay_ms
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
        current_path = self.images[self.image_index] if self.images else None
        self._show_background(previous_frame if current_path and current_path != previous_path else None)
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

    def _show_background(self, transition_from=None):
        if hasattr(self, 'motion_timer'):
            self.motion_timer.stop()
            self.motion_clock.invalidate()
        self.canvas.motion_ms = 0.0
        self.canvas.background = QPixmap(str(self.images[self.image_index])) if self.images else None
        self.canvas.start_background_transition(
            transition_from if self.canvas.background and not self.canvas.background.isNull() else None)
        self.canvas.depth_mask = None
        self.motion_timer.setInterval(100)
        self.canvas.scene = self.image_index
        self._start_depth_current()
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
        if (self.depth_enabled and self._depth_task and
                self._depth_task_key == self._depth_current_key() and
                time.monotonic() - self._depth_task_started < 25):
            self.background_remaining = 1000
            self.background_timer.start(1000)
            return
        self.background_remaining = 12000
        self.background_timer.setInterval(12000)
        if len(self.images) == 1:
            if self.canvas.depth_mask is None:
                self.canvas.motion_ms = 0.0
                self.canvas.update()
            return
        previous_frame = self.canvas.capture_background()
        self._advance_background()
        self._show_background(previous_frame)

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

    def _live_effects_changed(self, *_):
        self.live_speed_label.setText(t('播放速度 {p0:.2f}×', p0=self.live_speed.value()/100))
        self._sync_effect_buttons()
        self.effects_timer.start()

    def _sync_effect_buttons(self):
        self.bottom_speed_label.setText(f'{self.live_speed.value()/100:.2f}×')
        self._refresh_best_button()
        self.speed_down.setEnabled(self.live_speed.value() > self.live_speed.minimum())
        self.speed_up.setEnabled(self.live_speed.value() < self.live_speed.maximum())

    def _apply_live_effects(self):
        try:
            self.controller.set_live_effects(self.live_speed.value()/100, self.live_key.value())
        except Exception as error:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, t('播放調整失敗'), str(error))
            settings = getattr(self.controller.player, 'effects', (1., 0))
            self.reset_live_effects(apply=False, settings=settings)

    def set_best_state(self, saved, can_save):
        """saved is this profile's (speed, key) for the playing version, or None."""
        self.best_status.setText(t('我的最佳：{p0:.2f}×，Key {p1:+d}', p0=saved[0], p1=saved[1]) if saved
                                 else t('尚未儲存最佳設定'))
        self.apply_best_button.setEnabled(saved is not None)
        self.clear_best_button.setEnabled(saved is not None)
        self._best_saved, self._best_can_save = saved, can_save
        self._refresh_best_button()

    def _best_matches_current(self):
        saved = self._best_saved
        return bool(saved) and round(saved[0] * 100) == self.live_speed.value() and saved[1] == self.live_key.value()

    def _at_original(self):
        return self.live_speed.value() == 100 and self.live_key.value() == 0

    def _refresh_best_button(self):
        """One star: empty = save, filled = already best, marked = current values differ (click updates)."""
        saved = self._best_saved
        if saved is None:
            name, tip = 'star', t('儲存為我的最佳')
        elif self._best_matches_current():
            name, tip = 'star_on', t('已是我的最佳：{p0:.2f}×，Key {p1:+d}', p0=saved[0], p1=saved[1])
        elif self._at_original():
            name, tip = 'star_swap', t('切換到我的最佳（{p0:.2f}×，Key {p1:+d}）', p0=saved[0], p1=saved[1])
        else:
            name, tip = 'star_edit', t('更新我的最佳（目前已存 {p0:.2f}×，Key {p1:+d}）', p0=saved[0], p1=saved[1])
        self.best_button.setIcon(icon(name))
        self.best_button.setToolTip(tip)
        self.best_button.setAccessibleName(tip)
        self.best_button.setEnabled(self._best_can_save)

    def _best_clicked(self):
        if self._best_saved is not None and not self._best_matches_current() and self._at_original():
            self._apply_best()  # toggle back from original; never overwrite the saved value
        elif self._best_saved is None or not self._best_matches_current():
            self.controller.save_best_effects(self.live_speed.value() / 100, self.live_key.value())

    def _apply_best(self):
        saved = self.controller.saved_effects()
        if saved:
            self.reset_live_effects(settings=saved)

    def reset_live_effects(self, apply=True, settings=(1., 0)):
        self.effects_timer.stop()
        self.live_speed.blockSignals(True)
        self.live_key.blockSignals(True)
        self.live_speed.setValue(round(settings[0]*100))
        self.live_key.setValue(settings[1])
        self.live_speed.blockSignals(False)
        self.live_key.blockSignals(False)
        self.live_speed_label.setText(t('播放速度 {p0:.2f}×', p0=settings[0]))
        self._sync_effect_buttons()
        if apply:
            self._apply_live_effects()

    def _guide_volume_changed(self, value):
        self.guide_volume.setToolTip(t('導唱音量 {p0}%', p0=value))
        self.controller._set_guide_volume(value)

    def closeEvent(self, event):
        if self._depth_task:
            self._depth_task.cancel()
            self._depth_task = None
            self._depth_task_key = None
        self.effects_timer.stop()
        self.set_guide_available(False)
        self.controller.stop_playback()
        self.set_monitor_checked(False)
        self.more_menu.close()
        super().closeEvent(event)

    def _monitor_changed(self, enabled):
        self.controller.remember_audio_preference('monitor_enabled', enabled)
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
