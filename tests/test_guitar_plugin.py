import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QBuffer, QEventLoop, QIODevice, QPoint, QSize, QTimer, QUrl
from PySide6.QtGui import QImage, QTextCursor, QTextDocument
from PySide6.QtWidgets import QApplication
from app.lyrics import Timeline
from app.plugins.guitar.widget import (AlignmentDialog, GuitarWidget, alignment_text, parse_alignment_text, header_path, lyric_line_key, lyric_positions,
                                       result_path, shape, sheet_lines, source_signature)
from app.storage import atomic_json

class GuitarPluginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        existing = QApplication.instance()
        if existing is not None and not isinstance(existing,QApplication):
            raise unittest.SkipTest('widget checks require QApplication')
        cls.app = existing or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.audio = root/'original.wav'; self.audio.touch()
        self.backing = root/'backing.wav'; self.backing.touch()
        class Controller: pass
        self.controller = Controller()
        self.controller.workspace = root/'output'
        self.controller.songs = [{'id':'song','title':'Test','path':str(self.audio)}]
        self.controller.song_map = {'song':self.controller.songs[0]}
        self.controller.playing_item = None
        self.controller.guitar_voicing_cache_path = root/'voicing_cache.json'
        atomic_json(self.controller.workspace/'songs/song/catalog.json',
                    {'separation':{'manifest':{'stems':{'instrumental':str(self.backing)}}},'variants':[]})
        self.widget = GuitarWidget(self.controller)
        self.widget.refresh()

    def tearDown(self):
        self.widget.dispose(); self.widget.deleteLater(); self.app.processEvents(); self.temp.cleanup()

    def payload(self):
        segments = [{'start':0.,'end':2.,'chord':'F#'}, {'start':2.,'end':4.,'chord':'G'}]
        return {'source':source_signature(self.backing),'duration':4.,'raw':segments,
                'smooth':segments,'simple':segments,'standard':segments,'raw_model':[{'chord':'F#:maj'}]}

    def test_edit_is_saved_without_changing_raw_and_reloaded(self):
        self.widget.payload = self.payload()
        with patch('app.plugins.guitar.widget.QInputDialog.getText',return_value=('Am',True)):
            self.widget.edit_chord(0,1)
        saved=json.loads(result_path(self.controller.workspace,'song','instrumental').read_text(encoding='utf-8'))
        self.assertEqual(saved['raw'][0]['chord'],'F#')
        self.assertEqual(saved['edits']['simple']['0.000:2.000:F#'],'Am')
        self.widget._version_changed()
        self.assertEqual(self.widget._segments()[0]['chord'],'Am')
        self.widget.mode.setCurrentIndex(self.widget.mode.findData('raw'))
        self.assertEqual(self.widget._segments()[0]['chord'],'F#')

    def test_timing_and_capo_keep_original_pitch(self):
        self.assertEqual(shape('F#/C#',2),'E/B')
        segments=self.payload()['simple']
        lines=sheet_lines(Timeline([2000,6000],['one','two']),2.,500,segments,4.)
        self.assertEqual(lines[1][0],1.5)
        self.assertEqual(lines[1][2][0]['chord'],'F#')
        self.assertEqual(segments[0]['chord'],'F#')

    def test_capo_is_persisted_per_chart(self):
        self.widget.payload=self.payload()
        self.widget.capo.setValue(2)
        self.widget._version_changed()
        self.assertEqual(self.widget.capo.value(),2)
        self.assertEqual(self.widget._segments()[0]['chord'],'F#')

    def test_sections_collapse_and_diagrams_follow_capo_and_position(self):
        self.assertTrue(self.widget.edit_section.content.isHidden())
        self.assertTrue(self.widget.header_section.content.isHidden())
        self.widget.edit_section.toggle.setChecked(True)
        self.widget.header_section.toggle.setChecked(True)
        self.assertFalse(self.widget.edit_section.content.isHidden())
        self.assertFalse(self.widget.header_section.content.isHidden())
        self.widget.payload=self.payload()
        self.widget.capo.setValue(2)
        self.widget._position(0)
        self.assertIn('guitar-diagram:',self.widget.sheet.toHtml())
        self.assertIn('目前：E',self.widget.current.text())
        self.assertIn('下一個：F',self.widget.current.text())
        self.widget._position(2500)
        self.assertIn('目前：F',self.widget.current.text())

    def test_generation_export_and_playback_controls_are_grouped(self):
        self.widget.resize(1150,700)
        self.widget.show()
        self.app.processEvents()
        self.assertFalse(hasattr(self.widget,'install'))
        self.assertLess(self.widget.generate.x(),self.widget.mode.x())
        self.assertLess(self.widget.cancel.x(),self.widget.mode.x())
        self.assertLessEqual(abs(self.widget.generate.y()-self.widget.mode.y()),2)
        self.assertLessEqual(abs(self.widget.export.y()-self.widget.mode.y()),2)
        self.assertGreater(self.widget.play.y(),self.widget.mode.y())
        self.assertLess(self.widget.play.y(),self.widget.sheet.y())

    def test_header_uses_song_thumbnail_and_custom_information_can_be_saved(self):
        from app.karaoke_assets import song_images_dir
        from app.song_images import save_thumbnail
        folder=song_images_dir(self.controller.workspace,'song'); folder.mkdir(parents=True)
        source=folder/'image.png'
        image=QImage(100,80,QImage.Format.Format_RGB32); image.fill(0xff3388cc); image.save(str(source))
        save_thumbnail(self.controller.workspace,'song',source)
        self.widget._version_changed()
        self.assertIsNotNone(self.widget.header_image)
        self.assertIn('Test',self.widget.sheet.toPlainText())
        self.widget.header_title.setText('My chart')
        self.widget.header_artist.setText('Singer')
        self.widget.header_note.setText('Practice')
        self.widget.save_header()
        self.widget._version_changed()
        self.assertEqual(self.widget.header_title.text(),'My chart')
        self.assertIn('My chart',self.widget.sheet.toPlainText())
        self.assertEqual(json.loads(header_path(self.controller.workspace,'song','instrumental').read_text(encoding='utf-8'))['note'],'Practice')
        custom=Path(self.temp.name)/'custom.png'
        other=QImage(60,90,QImage.Format.Format_RGB32); other.fill(0xffee2222); other.save(str(custom))
        self.widget.header_pending_cover=custom
        self.widget.save_header()
        saved=json.loads(header_path(self.controller.workspace,'song','instrumental').read_text(encoding='utf-8'))
        self.assertTrue((self.controller.workspace/'songs/song/guitar/covers'/saved['cover']).is_file())
        self.widget.reset_cover(); self.widget.save_header()
        self.assertNotIn('cover',json.loads(header_path(self.controller.workspace,'song','instrumental').read_text(encoding='utf-8')))
        self.assertEqual(Path(self.widget._header_cover_path()),source)

    def test_pdf_export_contains_chart_and_can_span_pages(self):
        from PySide6.QtPdf import QPdfDocument
        from app.karaoke_assets import song_images_dir
        from app.song_images import save_thumbnail
        folder=song_images_dir(self.controller.workspace,'song'); folder.mkdir(parents=True)
        cover=folder/'cover.png'
        image=QImage(80,80,QImage.Format.Format_RGB32); image.fill(0xffee2222); image.save(str(cover))
        save_thumbnail(self.controller.workspace,'song',cover)
        self.widget.payload=self.payload()
        self.widget.payload['duration']=60.
        self.widget.payload['simple']=[{'start':float(i),'end':float(i+1),'chord':'C' if i%2==0 else 'G'} for i in range(60)]
        self.widget.timeline=Timeline([i*1000 for i in range(60)],[f'Line {i}' for i in range(60)])
        self.widget._render()
        target=Path(self.temp.name)/'chart.pdf'
        self.widget.export_pdf(target)
        self.assertEqual(target.read_bytes()[:5],b'%PDF-')
        pdf=QPdfDocument(self.widget)
        buffer=QBuffer()
        buffer.setData(target.read_bytes())
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        pdf.load(buffer)
        self.assertEqual(pdf.status(),QPdfDocument.Status.Ready)
        self.assertGreater(pdf.pageCount(),1)
        page=pdf.render(0,QSize(596,842))
        red=blue=gold=0
        for y in range(0,450,2):
            for x in range(0,350,2):
                c=page.pixelColor(x,y)
                red += c.red()>200 and c.green()<80 and c.blue()<80 and c.alpha()>200
                blue += c.blue()>60 and c.blue()>c.red()*1.5 and c.green()>40 and c.alpha()>200
                gold += c.red()>210 and 150<c.green()<220 and c.blue()<90 and c.alpha()>200
        self.assertGreater(red,10,'song thumbnail should appear in exported PDF')
        self.assertGreater(blue,5,'chord diagrams should appear in exported PDF')
        self.assertEqual(gold,0,'live playback highlight should not appear in PDF')
        pdf.close(); buffer.close()

    def test_generated_diagrams_are_inline_and_persisted(self):
        self.widget.payload=self.payload()
        self.widget.payload['simple']=[{'start':0.,'end':.2,'chord':'Cdim'},
                                       {'start':.2,'end':4.,'chord':'F#/A#'}]
        self.widget.timeline=Timeline([0],['歌詞測試'])
        self.widget._render()
        html=self.widget.sheet.toHtml()
        self.assertEqual(html.count('<img '),2)
        self.assertIn('歌',html)
        entries=json.loads(self.controller.guitar_voicing_cache_path.read_text(encoding='utf-8'))['entries']
        self.assertIn('Cdim',entries)
        self.assertIn('F#/A#',entries)

    def test_diagram_size_is_small_by_default_and_saved_per_chart(self):
        self.widget.payload=self.payload()
        self.widget._render()
        self.assertEqual(self.widget.diagram_size.currentData(),'small')
        self.assertIn('width="84"',self.widget.sheet.toHtml())
        self.assertEqual(self.widget.diagram_events[0]['normal'].width(),84)
        self.assertEqual(self.widget.diagram_events[0]['normal'].height(),113)
        self.widget.diagram_size.setCurrentIndex(self.widget.diagram_size.findData('large'))
        self.assertIn('width="108"',self.widget.sheet.toHtml())
        self.assertEqual(self.widget.diagram_events[0]['normal'].width(),108)
        self.widget._version_changed()
        self.assertEqual(self.widget.diagram_size.currentData(),'large')

    def test_playback_highlights_correct_occurrence_including_no_chord(self):
        self.widget.payload=self.payload()
        self.widget.payload['simple']=[{'start':0.,'end':1.,'chord':'N'},
            {'start':1.,'end':2.,'chord':'C'},{'start':2.,'end':3.,'chord':'C'}]
        self.widget._render()
        document=self.widget.sheet.document()
        self.widget._position(0)
        first=self.widget.diagram_events[0]
        image=document.resource(QTextDocument.ResourceType.ImageResource,QUrl(first['url']))
        self.assertEqual(image.pixelColor(10,30),first['active'].pixelColor(10,30))
        self.widget._position(2200)
        self.assertEqual(self.widget.active_diagram,2)
        image=document.resource(QTextDocument.ResourceType.ImageResource,QUrl(first['url']))
        self.assertEqual(image.pixelColor(10,30),first['normal'].pixelColor(10,30))
        self.assertNotEqual(first['active'].pixelColor(10,30),first['normal'].pixelColor(10,30))

    def test_auto_scroll_keeps_current_diagram_visible(self):
        self.widget.payload=self.payload()
        self.widget.payload['duration']=12.
        self.widget.payload['simple']=[{'start':float(i),'end':float(i+1),'chord':'C' if i%2==0 else 'G'} for i in range(12)]
        self.widget.timeline=Timeline([i*1000 for i in range(12)],[str(i) for i in range(12)])
        self.widget.resize(850,500)
        self.widget.show()
        self.widget._render()
        self.app.processEvents()
        self.widget._position(9500)
        self.app.processEvents()
        event=self.widget.diagram_events[self.widget.active_diagram]
        cursor=QTextCursor(self.widget.sheet.document())
        cursor.setPosition(event['position'])
        rect=self.widget.sheet.cursorRect(cursor)
        self.assertGreaterEqual(rect.top(),0)
        self.assertLessEqual(rect.bottom(),self.widget.sheet.viewport().height())

    def test_auto_scroll_follows_wrapped_intro_chords(self):
        self.widget.payload=self.payload()
        self.widget.payload['duration']=16.
        self.widget.payload['simple']=[{'start':float(i),'end':float(i+1),'chord':'C' if i%2==0 else 'G'} for i in range(16)]
        self.widget.timeline=Timeline([15000],['歌詞'])
        self.widget.resize(850,450)
        self.widget.show()
        self.widget._render()
        self.app.processEvents()
        self.widget._position(14000)
        self.app.processEvents()
        event=self.widget.diagram_events[self.widget.active_diagram]
        cursor=QTextCursor(self.widget.sheet.document())
        cursor.setPosition(event['position'])
        rect=self.widget.sheet.cursorRect(cursor)
        self.assertGreaterEqual(rect.top(),0)
        self.assertLessEqual(rect.bottom(),self.widget.sheet.viewport().height())

    def test_scrolls_before_current_diagram_leaves_view(self):
        self.widget.payload=self.payload()
        self.widget.payload['duration']=12.
        self.widget.payload['simple']=[{'start':float(i),'end':float(i+1),'chord':'C'} for i in range(12)]
        self.widget.timeline=Timeline([i*1000 for i in range(12)],[str(i) for i in range(12)])
        self.widget.resize(850,800); self.widget.show(); self.widget._render()
        self.app.processEvents()
        bar=self.widget.sheet.verticalScrollBar()
        bar.setValue(0)
        viewport=self.widget.sheet.viewport().height()
        threshold=viewport-2*(113+26)
        candidates=[]
        for event in self.widget.diagram_events[1:]:
            cursor=QTextCursor(self.widget.sheet.document()); cursor.setPosition(event['position'])
            rect=self.widget.sheet.cursorRect(cursor)
            if rect.top() >= threshold and rect.bottom() <= viewport-8:
                candidates.append(event)
        self.assertTrue(candidates,'expected a fully visible diagram in the penultimate row')
        self.widget._position(round(candidates[0]['start']*1000))
        self.app.processEvents()
        self.assertGreater(bar.value(),0,'scroll should advance before the diagram is clipped')

    def test_manual_alignment_is_saved_and_does_not_change_chord_timing(self):
        self.widget.payload=self.payload()
        self.widget.timeline=Timeline([0,4000],['一二三四五六','下一句'])
        self.widget._render()
        original=[dict(row) for row in self.widget.payload['simple']]
        self.widget.table.selectRow(1)
        with patch('app.plugins.guitar.widget.AlignmentDialog') as dialog:
            dialog.return_value.exec.return_value=1; dialog.return_value.positions=[0,4]
            self.widget.align_chord()
        saved=json.loads(result_path(self.controller.workspace,'song','instrumental').read_text(encoding='utf-8'))
        self.assertEqual(saved['simple'],original)
        edits=saved['alignment_edits']['simple']
        self.assertEqual(next(iter(edits.values()))['character'],4)
        self.assertIn('一二三四',self.widget.sheet.toHtml())
        self.assertIn('五六',self.widget.sheet.toHtml())
        self.assertEqual([event['start'] for event in self.widget.diagram_events],[0.,2.])
        with patch('app.plugins.guitar.widget.load_selected',return_value=('',self.widget.timeline,{'offsets':{}},'')):
            self.widget._version_changed()
        self.assertEqual(self.widget.payload['alignment_edits']['simple'],edits)
        self.widget.table.selectRow(1)
        with patch('app.plugins.guitar.widget.AlignmentDialog') as dialog:
            dialog.return_value.exec.return_value=1; dialog.return_value.positions=[0,3]
            self.widget.align_chord()
        self.assertFalse(self.widget.payload['alignment_edits']['simple'])
        self.assertEqual(self.widget.payload['simple'],original)

    def test_move_remaining_chords_to_line_end_and_restore(self):
        self.widget.payload=self.payload()
        self.widget.payload['duration']=5.
        self.widget.payload['simple']=[{'start':float(i),'end':float(i+1),'chord':chord}
                                       for i,chord in enumerate(['C','G','Am','F','D'])]
        self.widget.timeline=Timeline([0,5000],['這句已經唱完','下一句'])
        original=[dict(row) for row in self.widget.payload['simple']]
        self.widget._render(); self.widget.table.selectRow(2)
        with patch('app.plugins.guitar.widget.AlignmentDialog') as dialog:
            dialog.return_value.exec.return_value=1; dialog.return_value.positions=[0,1,6,6,6]
            self.widget.align_chord()
        saved=json.loads(result_path(self.controller.workspace,'song','instrumental').read_text(encoding='utf-8'))
        self.assertEqual(saved['simple'],original)
        edits=saved['alignment_edits']['simple']
        self.assertEqual(len(edits),3)
        self.assertTrue(all(value['character']==len('這句已經唱完') for value in edits.values()))
        self.assertEqual([event['start'] for event in self.widget.diagram_events],[0.,1.,2.,3.,4.])
        self.widget._position(3500)
        self.assertEqual(self.widget.diagram_events[self.widget.active_diagram]['start'],3.)
        with patch('app.plugins.guitar.widget.load_selected',return_value=('',self.widget.timeline,{'offsets':{}},'')):
            self.widget._version_changed()
        self.assertEqual(self.widget.payload['alignment_edits']['simple'],edits)
        self.widget.table.selectRow(2)
        with patch('app.plugins.guitar.widget.AlignmentDialog') as dialog:
            dialog.return_value.exec.return_value=1; dialog.return_value.positions=[0,1,2,3,4]
            self.widget.align_chord()
        self.assertFalse(self.widget.payload['alignment_edits']['simple'])
        self.assertEqual(self.widget.payload['simple'],original)

    def test_alignment_positions_preserve_text_order_and_ignore_changed_lyrics(self):
        events=[{'start':0.,'_alignment_key':'a'},{'start':2.,'_alignment_key':'b'}]
        text='一二三四五六'
        tag=lyric_line_key(0.,text,1.,0)
        edits={'b|'+tag:{'line':tag,'character':4}}
        self.assertEqual(lyric_positions(events,text,0.,4.,edits,tag),[0,4])
        changed=lyric_line_key(0.,text+'七',1.,0)
        self.assertEqual(lyric_positions(events,text+'七',0.,4.,edits,changed),[0,3])
        self.assertEqual(lyric_line_key(1.,text,1.,0),lyric_line_key(1.25,text,1.,250))

    def test_alignment_editor_validates_markers_and_escaped_lyric_characters(self):
        text='開|中\\尾'
        positions=[0,2,5,5]
        encoded=alignment_text(text,positions)
        self.assertEqual(parse_alignment_text(encoded,text,4),positions)
        self.assertIsNone(parse_alignment_text(encoded+'|',text,4))
        self.assertIsNone(parse_alignment_text(encoded+'字',text,4))
        dialog=AlignmentDialog(self.widget,text,[{'chord':'C'}]*4,positions,[0,1,2,3],0)
        dialog.edit.setText(encoded+'|'); dialog.accept()
        self.assertTrue(dialog.error.text())
        self.assertNotEqual(dialog.result(),1)
        dialog.edit.setText(encoded); dialog.accept()
        self.assertEqual(dialog.positions,positions)
        self.assertEqual(dialog.result(),1)

    def test_alignment_of_held_chord_can_be_saved_for_each_line(self):
        self.widget.payload=self.payload()
        self.widget.timeline=Timeline([0,1000,4000],['第一句','第二句','結尾'])
        self.widget._render(); self.widget.table.selectRow(0)
        def choose_line(_p,_t,_l,choices,_c,_e): return choices[1],True
        with patch('app.plugins.guitar.widget.QInputDialog.getItem',side_effect=choose_line), patch('app.plugins.guitar.widget.AlignmentDialog') as dialog:
            dialog.return_value.exec.return_value=1; dialog.return_value.positions=[1,1]
            self.widget.align_chord()
        self.widget.table.selectRow(0)
        with patch('app.plugins.guitar.widget.QInputDialog.getItem',side_effect=lambda _p,_t,_l,choices,_c,_e:(choices[0],True)), patch('app.plugins.guitar.widget.AlignmentDialog') as dialog:
            dialog.return_value.exec.return_value=1; dialog.return_value.positions=[1]
            self.widget.align_chord()
        self.assertEqual(len(self.widget.payload['alignment_edits']['simple']),2)

    def test_right_click_diagram_edits_the_displayed_line_of_a_held_chord(self):
        self.widget.payload=self.payload()
        self.widget.timeline=Timeline([0,1000,4000],['第一句','第二句','結尾'])
        self.widget.resize(850,800); self.widget.show(); self.widget._render(); self.app.processEvents()
        event=next(event for event in self.widget.diagram_events if event['line_index']==1)
        cursor=QTextCursor(self.widget.sheet.document()); cursor.setPosition(event['position'])
        point=self.widget.sheet.cursorRect(cursor).topLeft()+QPoint(10,30)
        self.assertEqual(self.widget.sheet.anchorAt(point),'guitar-chord:1')
        with patch.object(self.widget.player,'setPosition') as seek:
            self.widget._diagram_clicked(QUrl(self.widget.sheet.anchorAt(point)))
            seek.assert_called_once_with(1000)
        original=[dict(row) for row in self.widget.payload['simple']]
        with patch('app.plugins.guitar.widget.QMenu') as menu, patch('app.plugins.guitar.widget.AlignmentDialog') as dialog, patch('app.plugins.guitar.widget.QInputDialog.getItem') as line_prompt:
            menu.return_value.exec.return_value=menu.return_value.addAction.return_value
            dialog.return_value.exec.return_value=1; dialog.return_value.positions=[1,1]
            self.widget._sheet_context_menu(point)
            self.assertEqual(dialog.call_args.args[1],'第二句')
            line_prompt.assert_not_called()
        saved=json.loads(result_path(self.controller.workspace,'song','instrumental').read_text(encoding='utf-8'))
        self.assertEqual(saved['simple'],original)
        self.assertEqual(len(saved['alignment_edits']['simple']),1)
        self.assertEqual(next(iter(saved['alignment_edits']['simple'].values()))['line'],lyric_line_key(1.,'第二句',1.,0))

    def test_regenerating_archives_alignment_only_edits(self):
        target=result_path(self.controller.workspace,'song','instrumental')
        old=self.payload(); old['alignment_edits']={'simple':{'key':{'line':'x','character':2}}}
        atomic_json(target,old)
        temporary=target.with_name('incoming.tmp')
        atomic_json(temporary,self.payload())
        self.widget.process=Mock()
        self.widget.process.readAllStandardError.return_value=b''
        self.widget.process.readAllStandardOutput.return_value=b''
        self.widget.pending=(temporary,target,source_signature(self.backing),self.backing)
        self.widget.operation='generate'
        self.widget._finished(0,None)
        archives=list((target.parent/'history').glob('*.json'))
        self.assertEqual(len(archives),1)
        self.assertEqual(json.loads(archives[0].read_text(encoding='utf-8'))['alignment_edits'],old['alignment_edits'])

    def test_original_and_backing_use_their_saved_lyric_offsets(self):
        state={'offsets':{'original':250,'instrumental':-100}}
        with patch('app.plugins.guitar.widget.load_selected',return_value=('',Timeline([1000],['line']),state,'')):
            self.widget._version_changed()
            self.assertEqual(self.widget.delay,250)
            self.widget.play_source.setCurrentIndex(1)
            self.assertEqual(self.widget.delay,-100)

    def test_changed_audio_is_not_loaded(self):
        target=result_path(self.controller.workspace,'song','instrumental')
        data=self.payload(); data['source']['size']=12345
        atomic_json(target,data)
        self.widget._version_changed()
        self.assertIsNone(self.widget.payload)

    def test_recompute_keeps_raw_and_manual_edit_bound_to_interval(self):
        data=self.payload()
        data['analysis']={'hop_seconds':.1,'beats':[.5,1,1.5,2,2.5,3,3.5],'strength':1.}
        data['tempo']=120.
        data['edits']={'simple':{'0.000:2.000:F#':'Am'}}
        self.widget.payload=data
        self.widget.strength.setValue(.5)
        self.widget.recompute_chords()
        saved=json.loads(result_path(self.controller.workspace,'song','instrumental').read_text(encoding='utf-8'))
        self.assertEqual(saved['raw'][0]['chord'],'F#')
        self.assertEqual(saved['analysis']['strength'],.5)
        self.assertEqual(saved['edits']['simple']['0.000:2.000:F#'],'Am')

    def test_previously_edited_chart_is_restorable(self):
        target=result_path(self.controller.workspace,'song','instrumental')
        old=self.payload(); old['edits']={'simple':{'0.000:2.000:F#':'Am'}}
        atomic_json(target.parent/'history'/(target.stem+'-old.json'),old)
        atomic_json(target,self.payload())
        self.widget.restore_history()
        self.assertEqual(self.widget._segments()[0]['chord'],'Am')

    @unittest.skipUnless(os.environ.get('KARAMORPH_GUITAR_INTEGRATION') == '1', 'optional installed-model integration')
    def test_installed_worker_through_ui_process(self):
        import numpy as np
        import soundfile as sf
        sr=22050
        times=np.arange(sr*3)/sr
        audio=sum(np.sin(2*np.pi*f*times) for f in (261.63,329.63,392.0))*.1
        sf.write(str(self.backing),audio,sr)
        self.widget.generate_chords()
        self.assertIsNotNone(self.widget.process)
        loop=QEventLoop()
        self.widget.process.finished.connect(loop.quit)
        timer=QTimer(); timer.setSingleShot(True); timer.timeout.connect(loop.quit); timer.start(120000)
        loop.exec()
        self.assertIsNone(self.widget.process, 'model worker timed out')
        self.assertIsNotNone(self.widget.payload,self.widget.status.text())
        self.assertTrue(self.widget.payload['raw'])
        self.assertTrue(result_path(self.controller.workspace,'song','instrumental').is_file())

if __name__ == '__main__': unittest.main()
