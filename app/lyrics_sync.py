"""Line-level lyric timing editor: tap along to music to build or re-time an LRC."""

import re
from difflib import SequenceMatcher
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtGui import QBrush, QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QPlainTextEdit, QHBoxLayout, QHeaderView, QLabel,
    QMessageBox, QPushButton, QSlider, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from app.i18n import t
from app.lyrics import parse_lrc

PREROLL_MS = 3000
SEEK_MS = 3000
TIME_TEXT = re.compile(r'\s*(\d+):([0-5]?\d(?:\.\d{1,3})?)\s*')


def format_time(ms: int) -> str:
    centis = max(0, int(ms)) // 10
    return f'{centis // 6000:02d}:{centis // 100 % 60:02d}.{centis % 100:02d}'


def parse_time(text: str):
    """'mm:ss.xx' -> ms, '' -> None; raises ValueError for anything else."""
    if not text.strip():
        return None
    match = TIME_TEXT.fullmatch(text)
    if not match:
        raise ValueError(text)
    return round((int(match[1]) * 60 + float(match[2])) * 1000)


def rows_from_text(text: str):
    """Load LRC (keeping its timing) or plain text (no timing) as [time_ms|None, line] rows."""
    try:
        timeline = parse_lrc(text)
    except ValueError:
        timeline = None
    if timeline:
        return [[max(0, ms), line] for ms, line in zip(timeline.times, timeline.lines)], True
    return [[None, line.strip()] for line in text.lstrip('﻿').splitlines() if line.strip()], False


def merge_text(rows, text):
    """Apply an edited lyric text to rows, keeping the time of every line that survives.

    Unchanged lines keep their time, an in-place rewrite of the same number of lines
    (typo fixes) keeps times too, and inserted lines start untimed. New blank lines are dropped.
    """
    old = [line for _, line in rows]
    new = [line.strip() for line in text.splitlines()]
    result = []
    for tag, a0, a1, b0, b1 in SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag == 'equal':
            result += [list(row) for row in rows[a0:a1]]
        elif tag == 'replace' and a1 - a0 == b1 - b0:
            result += [[rows[a0 + i][0], new[b0 + i]] for i in range(b1 - b0)]
        elif tag in ('replace', 'insert'):
            result += [[None, line] for line in new[b0:b1] if line]
    return result


def rows_to_lrc(rows) -> str:
    """Serialise fully timed rows; raises ValueError naming the first problem."""
    previous = 0
    for number, (ms, _) in enumerate(rows, 1):
        if ms is None:
            raise ValueError(t('第 {p0} 句尚未設定時間', p0=number))
        if ms < previous:
            raise ValueError(t('第 {p0} 句的時間早於前一句，請調整', p0=number))
        previous = ms
    text = '\n'.join(f'[{format_time(ms)}]{line}' for ms, line in rows)
    parse_lrc(text)
    return text + '\n'


class LyricSyncEditor(QWidget):
    """Space stamps the next line at the playhead; every stamp can be re-done or nudged."""

    def __init__(self, audio_path, load_source, apply_lrc, parent=None, output_device=None):
        super().__init__(parent)
        self.load_source, self.apply_lrc = load_source, apply_lrc
        # Callable returning the QAudioDevice chosen in Settings (None: system default).
        self.output_device = output_device
        self.rows, self.cursor, self.history, self.dirty = [], 0, [], False
        self.player = None
        self.output = None
        self._building = False
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.source = QComboBox()
        for title, key in [(t('人工純文字'), 'manual'), (t('LRCLIB 同步'), 'lrclib'),
                           (t('下載純文字'), 'downloaded'), (t('本機 LRC'), 'local')]:
            self.source.addItem(title, key)
        load = self._button(t('載入此來源'), self.load_selected_source)
        top.addWidget(QLabel(t('編輯來源')))
        top.addWidget(self.source, 1)
        top.addWidget(load)
        layout.addLayout(top)
        self.help = QLabel(t('空白鍵：標記下一句　Backspace：復原　P：播放／暫停　←→：前後 3 秒　'
                             '[ ]：選取句 −/+0.1 秒　R：從選取句重打'))
        self.help.setWordWrap(True)
        layout.addWidget(self.help)

        transport = QHBoxLayout()
        self.play_button = self._button(t('播放／暫停'), self.toggle_play)
        transport.addWidget(self.play_button)
        transport.addWidget(self._button('◀ 3s', lambda: self.seek_by(-SEEK_MS)))
        transport.addWidget(self._button('3s ▶', lambda: self.seek_by(SEEK_MS)))
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.sliderMoved.connect(self.seek)
        transport.addWidget(self.slider, 1)
        self.clock = QLabel('00:00.00')
        transport.addWidget(self.clock)
        layout.addLayout(transport)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['#', t('時間'), t('歌詞')])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.itemChanged.connect(self._item_changed)
        layout.addWidget(self.table, 1)

        edit = QHBoxLayout()
        for title, action in [(t('編輯歌詞文字'), self.edit_text),
                              (t('從選取句重打'), self.restamp_from_selection),
                              (t('清除選取時間'), self.clear_selected),
                              (t('插入一句'), self.insert_row), (t('刪除'), self.delete_rows)]:
            edit.addWidget(self._button(title, action))
        edit.addStretch(1)
        for delta in (-500, -100, 100, 500):
            edit.addWidget(self._button(f'{delta / 1000:+g}s', lambda _=False, d=delta: self.nudge(d)))
        layout.addLayout(edit)
        self.status = QLabel(t('先載入來源，再按播放，跟著歌曲按空白鍵標記每一句。'))
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.apply_button = self._button(t('套用為本機 LRC（儲存後生效）'), self.apply)
        layout.addWidget(self.apply_button)

        for key, action in [('Space', self.stamp), ('Backspace', self.undo), ('P', self.toggle_play),
                            ('Left', lambda: self.seek_by(-SEEK_MS)), ('Right', lambda: self.seek_by(SEEK_MS)),
                            ('[', lambda: self.nudge(-100)), (']', lambda: self.nudge(100)),
                            ('R', self.restamp_from_selection)]:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(action)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(100)
        self._open_audio(audio_path)

    def _button(self, title, action):
        button = QPushButton(title)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.clicked.connect(lambda _=False: action())
        return button

    # ----- audio -----
    def _open_audio(self, path):
        if not path or not Path(path).is_file():
            self.status.setText(t('找不到原曲音檔，無法播放；仍可編輯文字與時間。'))
            return
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        self.player = QMediaPlayer(self)
        self.output = QAudioOutput(self)
        self.player.setAudioOutput(self.output)
        self._apply_output_device()
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.durationChanged.connect(lambda ms: self.slider.setRange(0, max(0, ms)))

    def _apply_output_device(self):
        """Follow the output device selected in Settings, even if it changed after opening."""
        if self.output is None:
            return
        from PySide6.QtMultimedia import QMediaDevices
        device = (self.output_device() if self.output_device else None) or QMediaDevices.defaultAudioOutput()
        if self.output.device() != device:
            self.output.setDevice(device)

    def position_ms(self) -> int:
        return int(self.player.position()) if self.player else 0

    def toggle_play(self):
        if not self.player:
            return
        if self.player.isPlaying():
            self.player.pause()
        else:
            self._apply_output_device()
            self.player.play()

    def seek(self, ms):
        if self.player:
            self.player.setPosition(max(0, int(ms)))

    def seek_by(self, delta):
        self.seek(self.position_ms() + delta)

    def _tick(self):
        position = self.position_ms()
        if not self.slider.isSliderDown():
            self.slider.setValue(position)
        self.clock.setText(format_time(position))

    def dispose(self):
        self.timer.stop()
        if self.player:
            self.player.stop()
            self.player.setSource(QUrl())

    # ----- table -----
    def load_selected_source(self):
        if self.rows and self.dirty and QMessageBox.question(
                self, t('載入來源'), t('目前的調整尚未套用，載入會覆蓋它。要繼續嗎？')) != QMessageBox.StandardButton.Yes:
            return
        text = self.load_source(self.source.currentData())
        if not text.strip():
            self.status.setText(t('此來源目前沒有內容。'))
            return
        self.set_rows(*rows_from_text(text))

    def set_rows(self, rows, timed):
        self.rows, self.history, self.dirty = rows, [], False
        self.cursor = next((i for i, row in enumerate(rows) if row[0] is None), 0) if not timed else 0
        self._refresh()
        self.status.setText(
            t('已載入 {p0} 句（含時間，可微調或重打）。', p0=len(rows)) if timed
            else t('已載入 {p0} 句純文字；按播放後依序按空白鍵標記。', p0=len(rows)))

    def _refresh(self):
        self._building = True
        selected = self.selected_rows()
        self.table.setRowCount(len(self.rows))
        for index, (ms, line) in enumerate(self.rows):
            for column, text in enumerate((str(index + 1), '' if ms is None else format_time(ms), line)):
                item = self.table.item(index, column)
                created = item is None
                if created:
                    item = QTableWidgetItem()
                if item.text() != text:
                    item.setText(text)
                flags = item.flags() | Qt.ItemFlag.ItemIsEditable if column else item.flags() & ~Qt.ItemFlag.ItemIsEditable
                item.setFlags(flags)
                if index == self.cursor:
                    item.setBackground(QBrush(QColor('#fff2b3')))
                    item.setForeground(QBrush(QColor('#222')))
                else:
                    item.setData(Qt.ItemDataRole.BackgroundRole, None)
                    item.setData(Qt.ItemDataRole.ForegroundRole, None)
                if created:
                    self.table.setItem(index, column, item)
        self.table.clearSelection()
        for index in selected:
            if index < len(self.rows):
                self.table.selectRow(index)
        self._building = False

    def selected_rows(self):
        return sorted({index.row() for index in self.table.selectedIndexes()})

    def _item_changed(self, item):
        if self._building:
            return
        row, column = item.row(), item.column()
        if column == 1:
            try:
                self.rows[row][0] = parse_time(item.text())
            except ValueError:
                self.status.setText(t('時間格式請用 分:秒.小數，例如 01:23.45'))
        elif column == 2:
            self.rows[row][1] = item.text().strip()
        self.dirty = True
        self._refresh()

    # ----- tapping -----
    def stamp(self):
        if not self.rows:
            return
        if self.cursor >= len(self.rows):
            self.status.setText(t('所有句子都已標記；可按「套用」或選取句子重打。'))
            return
        self.history.append((self.cursor, self.rows[self.cursor][0]))
        self.rows[self.cursor][0] = self.position_ms()
        self.cursor += 1
        self.dirty = True
        self._refresh()
        self.table.scrollToItem(self.table.item(min(self.cursor, len(self.rows) - 1), 2))
        if self.cursor >= len(self.rows):
            self.status.setText(t('已標記到最後一句。'))
        else:
            self.status.setText(t('下一句：{p0}', p0=self.rows[self.cursor][1] or t('（空白）')))

    def undo(self):
        if not self.history:
            return
        self.cursor, previous = self.history.pop()
        self.rows[self.cursor][0] = previous
        self._refresh()
        if self.player and previous is not None:
            self.seek(previous - PREROLL_MS)
        self.status.setText(t('已復原；目前待標記：第 {p0} 句', p0=self.cursor + 1))

    def restamp_from_selection(self):
        selected = self.selected_rows()
        if not selected or not self.rows:
            self.status.setText(t('請先選取要重打的句子。'))
            return
        self.cursor = selected[0]
        self.history = []
        start = self.rows[self.cursor][0]
        if start is not None:
            self.seek(start - PREROLL_MS)
        if self.player:
            self._apply_output_device()
            self.player.play()
        self._refresh()
        self.status.setText(t('從第 {p0} 句重打：{p1}', p0=self.cursor + 1, p1=self.rows[self.cursor][1] or t('（空白）')))

    def nudge(self, delta):
        for index in self.selected_rows():
            if self.rows[index][0] is not None:
                self.rows[index][0] = max(0, self.rows[index][0] + delta)
                self.dirty = True
        self._refresh()

    def clear_selected(self):
        for index in self.selected_rows():
            self.rows[index][0] = None
            self.dirty = True
        selected = self.selected_rows()
        if selected:
            self.cursor = selected[0]
        self._refresh()

    def insert_row(self):
        selected = self.selected_rows()
        position = selected[-1] + 1 if selected else len(self.rows)
        self.rows.insert(position, [None, ''])
        if self.cursor >= position:
            self.cursor += 1
        self.dirty = True
        self._refresh()
        self.table.editItem(self.table.item(position, 2))

    def delete_rows(self):
        for index in reversed(self.selected_rows()):
            del self.rows[index]
            if index < self.cursor:
                self.cursor -= 1
        self.cursor = min(self.cursor, len(self.rows))
        self.history = []
        self.dirty = True
        self._refresh()

    def edit_text(self):
        """Edit the lyric text in a separate window; times of untouched lines are kept."""
        dialog = QDialog(self)
        dialog.setWindowTitle(t('編輯歌詞文字'))
        dialog.resize(520, 560)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(t('一行一句。可直接補上缺少的歌詞；未改動的句子會保留時間，新增的句子需要再標記。')))
        editor = QPlainTextEdit(chr(10).join(line for _, line in self.rows))
        layout.addWidget(editor, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        self.text_dialog = dialog
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.apply_text(editor.toPlainText())

    def apply_text(self, text):
        before = len(self.rows)
        self.rows = merge_text(self.rows, text)
        self.history = []
        self.dirty = True
        untimed = next((i for i, row in enumerate(self.rows) if row[0] is None), None)
        self.cursor = untimed if untimed is not None else min(self.cursor, len(self.rows))
        self._refresh()
        self.status.setText(t('歌詞文字已更新（{p0} → {p1} 句）；未標記的句子請繼續打拍。', p0=before, p1=len(self.rows)))

    def apply(self):
        try:
            text = rows_to_lrc(self.rows)
        except ValueError as error:
            QMessageBox.warning(self, t('無法套用'), str(error))
            return
        self.apply_lrc(text)
        self.dirty = False
        self.status.setText(t('已套用到「本機 LRC」，並設為播放來源；按儲存後生效。'))
