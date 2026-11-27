"""Optional guitar practice tab, independent of the singing player."""
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sys
import uuid

from PySide6.QtCore import QMarginsF, QProcess, Qt, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
from PySide6.QtGui import QImage, QPageLayout, QPageSize, QPdfWriter, QTextCursor, QTextDocument
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDoubleSpinBox, QFileDialog, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QPushButton, QSlider, QSpinBox, QTableWidget,
    QTableWidgetItem, QTextBrowser, QToolButton, QVBoxLayout, QWidget)
from app.i18n import t
from app.lyrics import effective_speed, load_selected
from app.playlist import resolve_item
from app.song_images import chosen_image, image_reader, import_image_to
from app.plugins.guitar.data import source_signature
from app.plugins.guitar.diagram import render_chord_image
from app.plugins.guitar.generated_voicings import VoicingCache
from app.plugins.guitar.voicings import lookup
from app.storage import atomic_json, data_dir, load_catalog, song_dir
from runtime_config import ROOT

NOTES = ['C','C#','D','D#','E','F','F#','G','G#','A','A#','B']
FLATS = {'Db':'C#', 'Eb':'D#', 'Gb':'F#', 'Ab':'G#', 'Bb':'A#', 'Cb':'B', 'Fb':'E', 'E#':'F', 'B#':'C'}
CHORD = re.compile(r'^(?:N|[A-G](?:#|b)?[A-Za-z0-9+#b()/,\-]*)$')

def edit_key(row):
    return f"{row['start']:.3f}:{row['end']:.3f}:{row['chord']}"

def shape(chord, fret):
    def shift(match):
        prefix, note = match.groups()
        note = FLATS.get(note, note)
        return prefix + (NOTES[(NOTES.index(note)-fret) % 12] if note in NOTES else note)
    return re.sub(r'(^|/)([A-G](?:#|b)?)', shift, chord)

def result_path(workspace, song_id, variant):
    name = hashlib.sha256(variant.encode('utf-8')).hexdigest()[:24]
    return song_dir(workspace, song_id) / 'guitar' / (name + '.json')

def header_path(workspace,song_id,variant):
    return result_path(workspace,song_id,variant).with_suffix('.header.json')

def sheet_lines(timeline, speed, delay, segments, duration):
    """LRC line times are exact; within-line placement is only an estimate."""
    if not timeline:
        return [(s['start'], '', [s]) for s in segments]
    lines = []
    starts = [max(0., (ms / speed + delay) / 1000) for ms in timeline.times]
    if starts and starts[0] > 0:
        starts.insert(0, 0.); texts = [''] + list(timeline.lines)
    else:
        texts = list(timeline.lines)
    for i, start in enumerate(starts):
        end = starts[i+1] if i+1 < len(starts) else duration
        if end <= start: continue
        current = next((s for s in reversed(segments) if s['start'] <= start < s['end']), None)
        events = [s for s in segments if start <= s['start'] < end]
        if current and (not events or events[0]['start'] > start + .001):
            events.insert(0, {**current, 'start':start})
        lines.append((start, texts[i], events))
    return lines

class CollapsibleSection(QWidget):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        layout.setSpacing(2)
        self.toggle = QToolButton()
        self.toggle.setText(title)
        self.toggle.setCheckable(True)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.toggle.toggled.connect(self._toggle)
        layout.addWidget(self.toggle)
        self.content = QWidget()
        self.content.hide()
        layout.addWidget(self.content)

    def _toggle(self, expanded):
        self.content.setVisible(expanded)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)

class GuitarWidget(QWidget):
    installation_status = Signal(str)

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.process = None
        self.operation = None
        self.pending = None
        self.payload = None
        self.timeline = None
        self.speed = 1.
        self.delay = 0
        self.lyric_offsets = {}
        cache_path = getattr(controller, 'guitar_voicing_cache_path',None) or data_dir()/'guitar-voicings.json'
        self.voicings = VoicingCache(cache_path)
        self.diagram_events = []
        self.active_diagram = -1
        self._loading = False
        self._pending_position = None
        self.header_state = {}
        self.header_pending_cover = None
        self.header_image = None
        self.player = QMediaPlayer(self)
        self.output = QAudioOutput(self)
        self.player.setAudioOutput(self.output)
        self.player.positionChanged.connect(self._position)
        self.player.mediaStatusChanged.connect(self._media_status)
        self.player.durationChanged.connect(lambda value: self.seek.setMaximum(max(0, value)))
        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        self.song = QComboBox(); self.version = QComboBox(); self.play_source = QComboBox()
        source_row = QHBoxLayout()
        for title, control in ((t('歌曲'),self.song),(t('歌曲版本'),self.version),(t('播放來源'),self.play_source)):
            source_row.addWidget(QLabel(title)); source_row.addWidget(control,1)
        layout.addLayout(source_row)
        self.generate = QPushButton(t('產生和弦譜'))
        self.generate.clicked.connect(self.generate_chords)
        self.cancel = QPushButton(t('取消處理'))
        self.cancel.clicked.connect(self.cancel_work)
        self.cancel.setEnabled(False)
        self.mode = QComboBox(); self.capo = QSpinBox()
        self.diagram_size = QComboBox()
        for label, value in ((t('小尺寸'), 'small'), (t('中尺寸'), 'medium'), (t('大尺寸'), 'large')):
            self.diagram_size.addItem(label,value)
        self.strength = QDoubleSpinBox()
        self.strength.setRange(0,4); self.strength.setSingleStep(.1); self.strength.setValue(1.)
        for title, key in ((t('簡易'), 'simple'), (t('標準'), 'standard'), (t('平滑'), 'smooth'), (t('原始'), 'raw')):
            self.mode.addItem(title,key)
        self.capo.setRange(0,7)
        self.capo.setSuffix(t(' 格'))
        settings_row = QHBoxLayout()
        settings_row.addWidget(self.generate)
        settings_row.addWidget(self.cancel)
        for title,w in ((t('譜面版本'),self.mode),(t('Capo'),self.capo),(t('指法圖大小'),self.diagram_size),(t('平滑強度'),self.strength)):
            settings_row.addWidget(QLabel(title)); settings_row.addWidget(w)
        self.recompute = QPushButton(t('套用平滑設定'))
        self.recompute.clicked.connect(self.recompute_chords)
        settings_row.addWidget(self.recompute)
        self.export = QPushButton(t('匯出譜面 PDF…'))
        self.export.clicked.connect(self.export_chart)
        settings_row.addWidget(self.export)
        settings_row.addStretch(1)
        layout.addLayout(settings_row)
        controls = QHBoxLayout()
        self.play = QPushButton(t('播放／暫停')); self.play.clicked.connect(self.toggle_play)
        self.seek = QSlider(Qt.Orientation.Horizontal); self.seek.sliderMoved.connect(self.player.setPosition)
        self.clock = QLabel('0:00 / 0:00')
        controls.addWidget(self.play); controls.addWidget(self.seek,1); controls.addWidget(self.clock)
        layout.addLayout(controls)
        self.status = QLabel(); self.status.setWordWrap(True); layout.addWidget(self.status)
        self.current = QLabel(); self.current.setWordWrap(True); layout.addWidget(self.current)
        display = QHBoxLayout()
        self.sheet = QTextBrowser(); self.sheet.setOpenLinks(False)
        self.sheet.setToolTip(t('標準調弦，左至右為第六弦到第一弦；數字為手指編號，格數相對 Capo。'))
        self.sheet.anchorClicked.connect(lambda url: self.player.setPosition(int(float(url.toString())*1000)))
        display.addWidget(self.sheet,1)
        layout.addLayout(display,1)
        self.edit_section = CollapsibleSection(t('修正和弦'))
        edit_layout = QVBoxLayout(self.edit_section.content)
        edit_layout.setContentsMargins(8,2,0,2)
        self.table = QTableWidget(0,3)
        self.table.setHorizontalHeaderLabels([t('時間'),t('和弦（實際音高）'),t('指型')])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.cellDoubleClicked.connect(self.edit_chord)
        self.table.cellClicked.connect(self.seek_chord)
        self.table.setMaximumHeight(180)
        edit_layout.addWidget(self.table)
        self.restore = QPushButton(t('還原先前譜面'))
        self.restore.clicked.connect(self.restore_history)
        edit_layout.addWidget(self.restore)
        edit_layout.addWidget(QLabel(t('點選和弦跳轉；雙擊列表修改並保存目前譜面版本的和弦。句內位置依 LRC 時間估算。')))
        layout.addWidget(self.edit_section)
        self.header_section = CollapsibleSection(t('修改譜面資訊'))
        header_layout = QVBoxLayout(self.header_section.content)
        header_layout.setContentsMargins(8,2,0,2)
        metadata_row = QHBoxLayout()
        self.header_title = QLineEdit(); self.header_artist = QLineEdit(); self.header_note = QLineEdit()
        for label,field in ((t('標題'),self.header_title),(t('歌手'),self.header_artist),(t('備註'),self.header_note)):
            metadata_row.addWidget(QLabel(label)); metadata_row.addWidget(field,1)
        header_layout.addLayout(metadata_row)
        header_buttons = QHBoxLayout()
        self.cover_choose = QPushButton(t('選擇譜面圖片…')); self.cover_choose.clicked.connect(self.choose_cover)
        self.cover_reset = QPushButton(t('使用歌曲縮圖')); self.cover_reset.clicked.connect(self.reset_cover)
        self.header_save = QPushButton(t('儲存譜面資訊')); self.header_save.clicked.connect(self.save_header)
        self.cover_label = QLabel()
        for button in (self.cover_choose,self.cover_reset,self.header_save): header_buttons.addWidget(button)
        header_buttons.addWidget(self.cover_label,1)
        header_layout.addLayout(header_buttons)
        layout.addWidget(self.header_section)
        self.song.currentIndexChanged.connect(self._song_changed)
        self.version.currentIndexChanged.connect(self._version_changed)
        self.play_source.currentIndexChanged.connect(self._source_changed)
        self.mode.currentIndexChanged.connect(self._render)
        self.capo.valueChanged.connect(self._capo_changed)
        self.diagram_size.currentIndexChanged.connect(self._diagram_size_changed)

    def refresh(self):
        if self.process: return
        previous = self.song.currentData()
        self._loading = True
        self.song.clear()
        for song in self.controller.songs:
            if song.get('available', True):
                self.song.addItem(song['title'] + (f" — {song['artist']}" if song.get('artist') else ''), song['id'])
        self.song.setCurrentIndex(max(0,self.song.findData(previous)))
        self._loading = False
        self._song_changed()

    def _song_changed(self):
        if self._loading: return
        self.stop_playback()
        previous = self.version.currentData()
        self._loading = True
        self.version.clear()
        sid = self.song.currentData()
        if sid:
            self.version.addItem(t('原曲／分離伴奏'), 'instrumental')
            catalog = load_catalog(self.controller.workspace, sid)
            variants = catalog.get('variants', [])
            from app.consumer import version_label
            by_id = {v['id']:v for v in variants}
            for variant in variants:
                self.version.addItem(version_label(variant, by_id), variant['id'])
        self.version.setCurrentIndex(max(0,self.version.findData(previous)))
        self._loading = False
        self._version_changed()

    def _path(self, variant):
        sid = self.song.currentData()
        return resolve_item({'song_id':sid,'variant_id':variant,'label':''}, self.controller.song_map,
                            self.controller.workspace) if sid else None

    def _version_changed(self):
        if self._loading: return
        self.stop_playback()
        sid, variant = self.song.currentData(), self.version.currentData()
        self._loading = True
        self.play_source.clear()
        if sid and variant == 'instrumental':
            original, backing = self._path('original'), self._path('instrumental')
            if original: self.play_source.addItem(t('原曲'),str(original))
            if backing: self.play_source.addItem(t('純伴奏'),str(backing))
        elif sid and variant:
            path = self._path(variant)
            if path: self.play_source.addItem(t('版本音源'),str(path))
        self._loading = False
        self._source_changed()
        self.payload = None; self.timeline = None; self.speed = 1.; self.delay = 0; self.lyric_offsets = {}
        self._load_header()
        self.capo.blockSignals(True)
        self.capo.setValue(0)
        self.capo.blockSignals(False)
        self.diagram_size.blockSignals(True)
        self.diagram_size.setCurrentIndex(0)
        self.diagram_size.blockSignals(False)
        if sid and variant:
            try:
                _, self.timeline, state, _ = load_selected(self.controller.workspace,sid)
                variants = load_catalog(self.controller.workspace,sid).get('variants',[])
                self.speed = effective_speed(variant,variants)
                self.lyric_offsets = state.get('offsets',{})
                self._sync_playback_timing()
                path = result_path(self.controller.workspace,sid,variant)
                if path.is_file():
                    payload = json.loads(path.read_text(encoding='utf-8'))
                    source = self._path(variant)
                    if source and payload.get('source') == source_signature(source):
                        self.payload = payload
                        self.strength.setValue(float(payload.get('analysis',{}).get('strength',1.)))
                        self.capo.blockSignals(True)
                        self.capo.setValue(int(payload.get('display_capo',0)))
                        self.capo.blockSignals(False)
                        self._set_diagram_size(payload.get('diagram_size','small'))
                        self.status.setText(t('已載入和弦譜。'))
                    else:
                        self.status.setText(t('音檔已變更，請重新產生和弦譜。'))
                else: self.status.setText(t('尚未產生和弦譜。'))
            except (OSError, ValueError, KeyError) as error:
                self.status.setText(str(error))
        self._render()

    def _source_changed(self):
        if self._loading: return
        position = self.player.position()
        playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self.player.stop()
        value = self.play_source.currentData()
        self._pending_position = (position,playing) if value else None
        self.player.setSource(QUrl.fromLocalFile(value) if value else QUrl())
        self._sync_playback_timing()
        if self.payload: self._render()

    def _sync_playback_timing(self):
        variant = self.version.currentData()
        if variant == 'instrumental' and self.play_source.currentText() == t('原曲'):
            variant = 'original'
        self.delay = self.lyric_offsets.get(variant,0)

    def _media_status(self,status):
        if status == QMediaPlayer.MediaStatus.LoadedMedia and self._pending_position:
            position,playing = self._pending_position
            self._pending_position = None
            self.player.setPosition(position)
            if playing: self.player.play()

    def _load_header(self):
        self.header_state = {}
        self.header_pending_cover = None
        self.header_reset_cover = False
        sid, variant = self.song.currentData(), self.version.currentData()
        song = self.controller.song_map.get(sid,{})
        if sid and variant:
            try:
                value = json.loads(header_path(self.controller.workspace,sid,variant).read_text(encoding='utf-8'))
                if isinstance(value,dict): self.header_state = value
            except (OSError,ValueError): pass
        def field(key, default=''):
            value = self.header_state.get(key,default)
            return value if isinstance(value,str) else default
        self.header_title.setText(field('title',song.get('title') or ''))
        self.header_artist.setText(field('artist',song.get('artist') or ''))
        self.header_note.setText(field('note'))
        self.cover_label.setText(t('已設定譜面圖片') if self.header_state.get('cover') else t('使用歌曲縮圖'))

    def _header_cover_path(self):
        sid = self.song.currentData()
        if not sid: return None
        name = self.header_state.get('cover')
        if isinstance(name,str):
            folder = (song_dir(self.controller.workspace,sid)/'guitar'/'covers').resolve()
            candidate = (folder/name).resolve()
            if candidate.is_relative_to(folder) and candidate.is_file(): return candidate
        return chosen_image(self.controller.workspace,sid) or self.controller.song_map.get(sid,{}).get('cover_path')

    def choose_cover(self):
        name,_ = QFileDialog.getOpenFileName(self,t('選擇譜面圖片'),'',
                                              'Images (*.png *.jpg *.jpeg *.webp *.bmp *.svg)')
        if not name: return
        try:
            image_reader(name)
        except (OSError,ValueError) as error:
            self.status.setText(str(error)); return
        self.header_pending_cover = Path(name)
        self.header_reset_cover = False
        self.cover_label.setText(t('待儲存圖片：{p0}',p0=Path(name).name))

    def reset_cover(self):
        self.header_pending_cover = None
        self.header_reset_cover = True
        self.cover_label.setText(t('使用歌曲縮圖（待儲存）'))

    def save_header(self):
        sid,variant = self.song.currentData(),self.version.currentData()
        if not sid or not variant: return
        try:
            state = {'title':self.header_title.text().strip(), 'artist':self.header_artist.text().strip(),
                     'note':self.header_note.text().strip()}
            if self.header_pending_cover:
                folder = song_dir(self.controller.workspace,sid)/'guitar'/'covers'
                state['cover'] = import_image_to(folder,self.header_pending_cover).name
            elif not self.header_reset_cover and isinstance(self.header_state.get('cover'),str):
                state['cover'] = self.header_state['cover']
            atomic_json(header_path(self.controller.workspace,sid,variant),state)
            self.header_state = state
            self.header_pending_cover = None; self.header_reset_cover = False
            self.cover_label.setText(t('已設定譜面圖片') if state.get('cover') else t('使用歌曲縮圖'))
            self.status.setText(t('譜面資訊已儲存。'))
            self._render()
        except (OSError,ValueError) as error:
            self.status.setText(str(error))

    def _header_html(self):
        sid = self.song.currentData()
        if not sid:
            self.header_image = None
            return ''
        song = self.controller.song_map.get(sid,{})
        title = self.header_state.get('title',song.get('title','')) or song.get('title','')
        artist = self.header_state.get('artist',song.get('artist','')) or ''
        note = self.header_state.get('note','') or ''
        image = QImage()
        source = self._header_cover_path()
        if source:
            try:
                reader = image_reader(source)
                reader.setScaledSize(reader.size().scaled(160,160,Qt.AspectRatioMode.KeepAspectRatio))
                image = reader.read()
            except (OSError,ValueError): pass
        if not image.isNull():
            image = image.scaled(144,144,Qt.AspectRatioMode.KeepAspectRatioByExpanding,Qt.TransformationMode.SmoothTransformation)
            image = image.copy((image.width()-144)//2,(image.height()-144)//2,144,144)
        self.header_image = image if not image.isNull() else None
        image_cell = '<td width="80"><img src="guitar-cover:header" width="72" height="72" /></td>' if self.header_image else ''
        parts = [str(artist),self.version.currentText()]
        if self.payload:
            parts += [str(self.payload.get('key','')),f"Capo {self.capo.value()}"]
        details = ' · '.join(html.escape(part) for part in parts if part)
        return (f'<table cellspacing="6"><tr>{image_cell}<td><h2>{html.escape(str(title))}</h2>'
                f'<p>{details}</p><p>{html.escape(str(note))}</p></td></tr></table><hr>')

    def export_chart(self):
        if not self.payload: return
        title = self.header_state.get('title') or self.controller.song_map.get(self.song.currentData(),{}).get('title','chart')
        filename = re.sub(r'[\\/:*?"<>|\r\n]+','_',str(title)).strip() or 'chart'
        path,_ = QFileDialog.getSaveFileName(self,t('匯出譜面 PDF'),str(self.controller.workspace/(filename+'.pdf')),'PDF (*.pdf)')
        if not path: return
        if not path.lower().endswith('.pdf'): path += '.pdf'
        try:
            self.export_pdf(Path(path))
            self.status.setText(t('譜面已匯出：{p0}',p0=path))
        except (OSError,ValueError,RuntimeError) as error:
            self.status.setText(t('譜面匯出失敗：{p0}',p0=error))

    def export_pdf(self,path):
        if not self.payload: raise ValueError(t('請先產生和弦譜。'))
        document = QTextDocument()
        document.setDefaultFont(self.sheet.document().defaultFont())
        document.setHtml(self.sheet.toHtml())
        width,height = {'small':(84,113),'medium':(96,129),'large':(108,145)}.get(self.diagram_size.currentData(),(84,113))
        print_images = {}
        for event in self.diagram_events:
            name = event['name']
            if name not in print_images:
                print_images[name] = render_chord_image(name,event['voicing'],generated=event['generated'],
                                                       width=width,height=height,scale=2)
            document.addResource(QTextDocument.ResourceType.ImageResource,QUrl(event['url']),print_images[name])
        if self.header_image:
            document.addResource(QTextDocument.ResourceType.ImageResource,QUrl('guitar-cover:header'),self.header_image)
        document.markContentsDirty(0,document.characterCount())
        path = Path(path)
        temporary = path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
        try:
            writer = QPdfWriter(str(temporary))
            writer.setResolution(96)
            writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
            writer.setPageMargins(QMarginsF(12,12,12,12),QPageLayout.Unit.Millimeter)
            document.print_(writer)
            del writer
            if not temporary.is_file() or temporary.stat().st_size < 100:
                raise ValueError(t('無法建立 PDF。'))
            with temporary.open('rb') as stream:
                signature = stream.read(5)
            if signature != b'%PDF-':
                raise ValueError(t('無法建立 PDF。'))
            os.replace(temporary,path)
        finally:
            temporary.unlink(missing_ok=True)

    def _segments(self):
        if not self.payload: return []
        mode = self.mode.currentData()
        edits = self.payload.get('edits',{}).get(mode,{})
        return [{**row,'chord':edits.get(edit_key(row),row['chord'])} for row in self.payload.get(mode,[])]

    def _capo_changed(self):
        if self.payload and self.song.currentData() and self.version.currentData():
            self.payload['display_capo'] = self.capo.value()
            try:
                atomic_json(result_path(self.controller.workspace,self.song.currentData(),self.version.currentData()),self.payload)
            except OSError as error:
                self.status.setText(str(error))
        self._render()

    def _set_diagram_size(self, value):
        self.diagram_size.blockSignals(True)
        self.diagram_size.setCurrentIndex(max(0,self.diagram_size.findData(value)))
        self.diagram_size.blockSignals(False)

    def _diagram_size_changed(self):
        if self.payload and self.song.currentData() and self.version.currentData():
            self.payload['diagram_size'] = self.diagram_size.currentData()
            try:
                atomic_json(result_path(self.controller.workspace,self.song.currentData(),self.version.currentData()),self.payload)
            except OSError as error:
                self.status.setText(str(error))
        self._render()

    def recompute_chords(self):
        if not self.payload or not self.payload.get('analysis'): return
        try:
            import numpy as np
            from app.plugins.guitar.optimize import analyze
            raw = self.payload['raw']
            duration = float(self.payload['duration'])
            hop = float(self.payload['analysis']['hop_seconds'])
            if not 0.001 <= hop <= 1 or not 0 < duration < 86400:
                raise ValueError(t('和弦譜資料無效。'))
            times = np.arange(int(np.ceil(duration/hop))) * hop
            labels = list(dict.fromkeys(s['chord'] for s in raw if s['chord'] != 'N')) + ['N']
            scores = np.zeros((len(labels),len(times)),dtype=np.float32)
            for row in raw:
                scores[labels.index(row['chord']),(times >= row['start']) & (times < row['end'])] = 1.
            scores[labels.index('N'),scores.sum(axis=0)==0] = 1.
            data = {'labels':labels,'scores':scores,'times':times,'beats':np.asarray(self.payload['analysis']['beats']),
                    'duration':duration,'tempo':self.payload.get('tempo',0),'timing':{},'engine':'C1',
                    'raw_segments':raw,'raw_model':self.payload.get('raw_model')}
            result = analyze(data,self.strength.value())
            updated = dict(self.payload)
            for key in ('smooth','simple','standard','key','capo'): updated[key] = result[key]
            updated['analysis'] = {**self.payload['analysis'],'strength':self.strength.value()}
            atomic_json(result_path(self.controller.workspace,self.song.currentData(),self.version.currentData()),updated)
            self.payload = updated
            self.status.setText(t('已更新平滑譜；模型不需重新執行。'))
            self._render()
        except (OSError,ValueError,KeyError,TypeError) as error:
            self.status.setText(str(error))

    def _render(self):
        segments = self._segments()
        self.table.setRowCount(len(segments))
        fret = self.capo.value()
        for i,row in enumerate(segments):
            for col,value in enumerate((f"{int(row['start']//60)}:{int(row['start']%60):02d}",row['chord'],shape(row['chord'],fret))):
                self.table.setItem(i,col,QTableWidgetItem(value))
        lines = sheet_lines(self.timeline,self.speed,self.delay,segments,(self.payload or {}).get('duration',0))
        pieces = [self._header_html()]
        images = {}
        variants = {}
        self.diagram_events = []
        self.active_diagram = -1
        image_width,image_height = {'small':(84,113), 'medium':(96,129), 'large':(108,145)}.get(
            self.diagram_size.currentData(),(84,113))
        duration = (self.payload or {}).get('duration',0)
        for i,(start,text,events) in enumerate(lines):
            end = lines[i+1][0] if i+1 < len(lines) else duration
            if not events:
                pieces.append(f'<p><a name="line{i}"></a>{html.escape(text) or "&nbsp;"}</p>'); continue
            indices = [max(0,min(len(text),int((s['start']-start)/max(.001,end-start)*len(text)))) for s in events]
            cells = []
            if indices[0]: cells.append(('',html.escape(text[:indices[0]])))
            for j,event in enumerate(events):
                stop = indices[j+1] if j+1 < len(indices) else len(text)
                name = shape(event['chord'],fret)
                if name not in variants:
                    known = lookup(name)
                    voicing = known or self.voicings.get(name)
                    generated = bool(voicing and not known)
                    variants[name] = (render_chord_image(name,voicing,generated=generated,width=image_width,height=image_height),
                                      render_chord_image(name,voicing,generated=generated,active=True,width=image_width,height=image_height),
                                      voicing,generated)
                number = len(self.diagram_events)
                url = 'guitar-diagram:' + str(number)
                images[url] = variants[name][0]
                next_start = events[j+1]['start'] if j+1 < len(events) else end
                self.diagram_events.append({'start':event['start'],'end':min(event['end'],next_start,end),
                    'url':url,'normal':variants[name][0],'active':variants[name][1],
                    'anchor':'chord'+str(number),'position':None,'name':name,
                    'voicing':variants[name][2],'generated':variants[name][3]})
                chord = (f'<a name="chord{number}"></a><a href="{event["start"]:.3f}">'
                         f'<img src="{url}" width="{image_width}" height="{image_height}" /></a>')
                cells.append((chord,html.escape(text[indices[j]:stop]) or '&nbsp;'))
            pieces.append(f'<a name="line{i}"></a>')
            # Fixed-size diagrams set a minimum column width, so closely spaced chords
            # gain room without overlapping. Split long lines into manageable rows.
            per_row = {'small':7,'medium':6,'large':5}.get(self.diagram_size.currentData(),7)
            for offset in range(0,len(cells),per_row):
                group = cells[offset:offset+per_row]
                pieces.append('<table cellspacing="3"><tr>' +
                    ''.join(f'<td><b>{chord}</b></td>' for chord,_ in group) + '</tr><tr>' +
                    ''.join(f'<td>{lyric}</td>' for _,lyric in group) + '</tr></table>')
        if not lines: pieces.append(f'<p>{html.escape(t("請先選擇歌曲並產生和弦譜。"))}</p>')
        self.sheet.setHtml(''.join(pieces))
        document = self.sheet.document()
        if self.header_image:
            document.addResource(QTextDocument.ResourceType.ImageResource,QUrl('guitar-cover:header'),self.header_image)
        for url,image in images.items():
            document.addResource(QTextDocument.ResourceType.ImageResource,QUrl(url),image)
        if images:
            document.markContentsDirty(0,document.characterCount())
        positions = {event['url']:event for event in self.diagram_events}
        block = document.begin()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.isValid() and fragment.charFormat().isImageFormat():
                    event = positions.get(fragment.charFormat().toImageFormat().name())
                    if event: event['position'] = fragment.position()
                iterator += 1
            block = block.next()
        if self.voicings.save_error:
            self.status.setText(t('指法圖已產生，但無法保存：{p0}',p0=self.voicings.save_error))
        self.export.setEnabled(bool(self.payload))
        self._position(self.player.position())

    def seek_chord(self,row,_column):
        segments = self._segments()
        if 0 <= row < len(segments): self.player.setPosition(round(segments[row]['start']*1000))

    def edit_chord(self,row,_column):
        segments = self._segments()
        if not 0 <= row < len(segments): return
        old = segments[row]['chord']
        value, ok = QInputDialog.getText(self,t('修改和弦'),t('輸入實際音高的和弦（例如 F#m7 或 N）：'),text=old)
        value = value.strip()
        if not ok: return
        if not CHORD.fullmatch(value):
            self.status.setText(t('和弦格式無效，請輸入例如 C、F#m7 或 N。')); return
        edits = self.payload.setdefault('edits',{}).setdefault(self.mode.currentData(),{})
        key = edit_key(self.payload[self.mode.currentData()][row])
        previous = edits.get(key)
        edits[key] = value
        try:
            atomic_json(result_path(self.controller.workspace,self.song.currentData(),self.version.currentData()),self.payload)
        except OSError as error:
            if previous is None: edits.pop(key,None)
            else: edits[key] = previous
            self.status.setText(str(error)); return
        self.status.setText(t('和弦修改已保存。')); self._render()

    def _position(self,ms):
        if not self.seek.isSliderDown(): self.seek.setValue(ms)
        def fmt(v): return f'{v//60000}:{(v//1000)%60:02d}'
        self.clock.setText(fmt(ms)+' / '+fmt(self.player.duration()))
        segments = self._segments()
        index = next((i for i,s in enumerate(segments) if s['start'] <= ms/1000 < s['end']),-1)
        lyric = ''
        if self.timeline:
            idx = self.timeline.index_at(ms,self.speed,self.delay)
            if 0 <= idx < len(self.timeline.lines): lyric = self.timeline.lines[idx]
        if index >= 0:
            chord = segments[index]['chord']; next_chord = segments[index+1]['chord'] if index+1 < len(segments) else '—'
            self.current.setText(t('目前：{p0}　下一個：{p1}\n{p2}',p0=shape(chord,self.capo.value()),p1=shape(next_chord,self.capo.value()),p2=lyric))
            if self.table.currentRow() != index:
                self.table.selectRow(index)
        else:
            self.current.setText(lyric)
        current = next((i for i,event in enumerate(self.diagram_events)
                        if event['start'] <= ms/1000 < event['end']),-1)
        if current != self.active_diagram:
            document = self.sheet.document()
            for number,kind in ((self.active_diagram,'normal'),(current,'active')):
                if 0 <= number < len(self.diagram_events):
                    event = self.diagram_events[number]
                    document.addResource(QTextDocument.ResourceType.ImageResource,QUrl(event['url']),event[kind])
                    if event['position'] is not None:
                        document.markContentsDirty(event['position'],1)
            self.active_diagram = current
            self.sheet.viewport().update()
            if current >= 0:
                event = self.diagram_events[current]
                position = event['position']
                if position is not None:
                    cursor = QTextCursor(document)
                    cursor.setPosition(position)
                    rect = self.sheet.cursorRect(cursor)
                    viewport = self.sheet.viewport().rect()
                    if rect.top() < 8 or rect.bottom() > viewport.height()-8:
                        self.sheet.scrollToAnchor(event['anchor'])
                        bar = self.sheet.verticalScrollBar()
                        height = {'small':113,'medium':129,'large':145}.get(self.diagram_size.currentData(),113)
                        bar.setValue(max(0,bar.value()-height-12))

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause(); return
        if not self.play_source.currentData(): return
        if self.controller.playing_item:
            self.status.setText(t('請先停止唱歌。')); return
        self.controller._stop_preview()
        self.controller.mv._stop_playback()
        box = getattr(self.controller,'audio_device',None)
        device = (box.currentData() if box is not None else None) or QMediaDevices.defaultAudioOutput()
        self.output.setDevice(device)
        self.player.play()

    def stop_playback(self):
        self._pending_position = None
        self.player.stop()

    def _busy(self, busy):
        for control in (self.generate,self.restore,self.recompute,self.table,self.song,self.version): control.setEnabled(not busy)
        self.cancel.setEnabled(busy)

    def install_runtime(self):
        if self.process: return
        if (self.controller.playing_item or self.controller.active_op or self.controller.pending_ops or
                self.controller.jobs.current or self.controller.installation_active() or
                (self.controller.models_dialog is not None and self.controller.models_dialog.worker is not None)):
            message = t('請先停止播放並等待其他處理完成。')
            self.status.setText(message); self.installation_status.emit(message); return
        self.operation = 'install'; self.pending = None
        self._start(sys.executable,[str(ROOT/'workers/guitar_install.py')])
        self.cancel.setEnabled(False)
        message = t('正在安裝吉他插件，首次下載可能需要數分鐘。')
        self.status.setText(message); self.installation_status.emit(message)

    def restore_history(self):
        sid, variant = self.song.currentData(), self.version.currentData()
        if not sid or not variant: return
        target = result_path(self.controller.workspace,sid,variant)
        history = target.parent/'history'
        candidates = sorted(history.glob(target.stem+'-*.json'),key=lambda p:p.stat().st_mtime_ns,reverse=True) if history.is_dir() else []
        if not candidates:
            self.status.setText(t('沒有可還原的先前譜面。')); return
        try:
            restored = json.loads(candidates[0].read_text(encoding='utf-8'))
            source = self._path(variant)
            if not source or restored.get('source') != source_signature(source):
                raise ValueError(t('先前譜面與目前音檔不同，無法還原。'))
            if target.is_file():
                atomic_json(history/(target.stem+'-'+uuid.uuid4().hex+'.json'),json.loads(target.read_text(encoding='utf-8')))
            atomic_json(target,restored)
            self.payload = restored
            self.strength.setValue(float(restored.get('analysis',{}).get('strength',1.)))
            self.capo.blockSignals(True)
            self.capo.setValue(int(restored.get('display_capo',0)))
            self.capo.blockSignals(False)
            self._set_diagram_size(restored.get('diagram_size','small'))
            self._render()
            self.status.setText(t('已還原先前譜面。'))
        except (OSError,ValueError,KeyError) as error:
            self.status.setText(str(error))

    def generate_chords(self):
        if self.process: return
        if self.controller.playing_item:
            self.status.setText(t('請先停止唱歌。')); return
        source = self._path(self.version.currentData()) if self.version.currentData() else None
        if not source:
            self.status.setText(t('找不到伴奏或版本音檔，請先準備歌曲。')); return
        env = ROOT/'.runtime/guitar'
        python = env/('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
        if not python.is_file() or not (env/'karamorph-guitar.ready').is_file():
            self.status.setText(t('請先到「設定 → 插件管理」安裝吉他插件。')); return
        target = result_path(self.controller.workspace,self.song.currentData(),self.version.currentData())
        try:
            target.parent.mkdir(parents=True,exist_ok=True)
            temp = target.with_name(target.stem+'.'+uuid.uuid4().hex+'.tmp')
            signature = source_signature(source)
        except (OSError,ValueError) as error:
            self.status.setText(str(error)); return
        self.pending = (temp,target,signature,source)
        self.operation = 'generate'
        self._start(str(python),[str(ROOT/'workers/guitar_chords.py'),'--audio',str(source),'--output',str(temp)])
        self.status.setText(t('正在產生和弦譜，請稍候。'))

    def _start(self,program,args):
        self._busy(True)
        process = QProcess(self); self.process = process
        process.setWorkingDirectory(str(ROOT))
        process.finished.connect(self._finished)
        process.errorOccurred.connect(self._error)
        process.start(program,args)

    def _error(self,error):
        if error == QProcess.ProcessError.FailedToStart and self.process:
            message = self.process.errorString()
            self.status.setText(message)
            if self.operation == 'install': self.installation_status.emit(message)
            self._cleanup()

    def _finished(self,code,_status):
        if not self.process: return
        error = bytes(self.process.readAllStandardError()).decode('utf-8',errors='replace')[-3000:]
        output = bytes(self.process.readAllStandardOutput()).decode('utf-8',errors='replace')[-3000:]
        operation = self.operation
        try:
            if code: raise ValueError(error or output or t('處理失敗。'))
            if operation == 'generate':
                temp,target,signature,source = self.pending
                result = json.loads(temp.read_text(encoding='utf-8'))
                if result.get('source') != signature or source_signature(source) != signature:
                    raise ValueError(t('音檔已變更，請重新產生和弦譜。'))
                result['display_capo'] = self.capo.value()
                result['diagram_size'] = self.diagram_size.currentData()
                if target.is_file():
                    old = json.loads(target.read_text(encoding='utf-8'))
                    if old.get('edits'):
                        archive = target.parent/'history'/(target.stem+'-'+uuid.uuid4().hex+'.json')
                        atomic_json(archive,old)
                atomic_json(target,result)
                sid, variant = self.song.currentData(), self.version.currentData()
                if sid and variant and result_path(self.controller.workspace,sid,variant) == target:
                    self.payload = result
                    self.strength.setValue(float(result.get('analysis',{}).get('strength',1.)))
                    self.status.setText(t('和弦譜已產生；先前手動修改保存在歷史檔案。') if target.is_file() and 'old' in locals() and old.get('edits') else t('和弦譜已產生。'))
                    self._render()
            else:
                message = t('吉他插件安裝完成。')
                self.status.setText(message); self.installation_status.emit(message)
        except (OSError,ValueError,KeyError) as exc:
            self.status.setText(str(exc))
            if operation == 'install': self.installation_status.emit(str(exc))
        self._cleanup()

    def _cleanup(self):
        if self.pending:
            self.pending[0].unlink(missing_ok=True)
        process = self.process
        self.process = None; self.pending = None; self.operation = None
        if process: process.deleteLater()
        self._busy(False)

    def cancel_work(self):
        if self.process:
            self.process.kill(); self.process.waitForFinished(2000)
            self._cleanup(); self.status.setText(t('已取消'))

    def dispose(self):
        self.cancel_work(); self.stop_playback(); self.player.setSource(QUrl())
