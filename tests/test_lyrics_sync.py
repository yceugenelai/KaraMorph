import os
import unittest
from unittest.mock import Mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication

from app.lyrics import parse_lrc
from app.lyrics_sync import LyricSyncEditor, format_time, parse_time, rows_from_text, rows_to_lrc


class SyncLogicTests(unittest.TestCase):
    def test_time_round_trip(self):
        self.assertEqual(format_time(83450), '01:23.45')
        self.assertEqual(parse_time('01:23.45'), 83450)
        self.assertIsNone(parse_time(' '))
        with self.assertRaises(ValueError):
            parse_time('abc')

    def test_plain_and_lrc_loading_and_output(self):
        rows, timed = rows_from_text('one\n\ntwo\n')
        self.assertEqual((rows, timed), ([[None, 'one'], [None, 'two']], False))
        rows, timed = rows_from_text('[ti:x]\n[00:10.50]a\n[00:20]')
        self.assertTrue(timed)
        self.assertEqual(rows, [[10500, 'a'], [20000, '']])
        self.assertEqual(parse_lrc(rows_to_lrc(rows)).times, [10500, 20000])
        with self.assertRaises(ValueError):
            rows_to_lrc([[None, 'x']])
        with self.assertRaises(ValueError):
            rows_to_lrc([[2000, 'x'], [1000, 'y']])


class SyncEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make(self, text):
        applied = []
        editor = LyricSyncEditor('', lambda key: text, applied.append)
        editor.player = Mock()
        editor.player.position.return_value = 0
        editor.load_selected_source()
        return editor, applied

    def test_stamp_undo_restamp_nudge_apply(self):
        editor, applied = self.make('a\nb\nc')
        for ms in (1000, 2000, 3000):
            editor.player.position.return_value = ms
            editor.stamp()
        self.assertEqual([r[0] for r in editor.rows], [1000, 2000, 3000])
        editor.undo()
        self.assertEqual((editor.cursor, editor.rows[2][0]), (2, None))
        editor.table.selectRow(1)
        editor.restamp_from_selection()
        editor.player.setPosition.assert_called_with(0)
        editor.player.position.return_value = 2500
        editor.stamp()
        editor.player.position.return_value = 3500
        editor.stamp()
        self.assertEqual([r[0] for r in editor.rows], [1000, 2500, 3500])
        editor.table.selectAll()
        editor.nudge(-100)
        self.assertEqual([r[0] for r in editor.rows], [900, 2400, 3400])
        editor.apply()
        self.assertEqual(parse_lrc(applied[0]).lines, ['a', 'b', 'c'])

    def test_edit_cells_and_apply_blocked_when_untimed(self):
        editor, applied = self.make('[00:01]a\n[00:02]b')
        editor.table.item(0, 2).setText('fixed')
        editor.table.item(1, 1).setText('00:05.00')
        self.assertEqual(editor.rows, [[1000, 'fixed'], [5000, 'b']])
        editor.clear_selected()
        editor.table.selectRow(0)
        editor.clear_selected()
        from unittest.mock import patch
        with patch('app.lyrics_sync.QMessageBox.warning') as warn:
            editor.apply()
        warn.assert_called_once()
        self.assertFalse(applied)


if __name__ == '__main__':
    unittest.main()


class MergeTextTests(unittest.TestCase):
    def test_merge_keeps_times_and_marks_new_lines(self):
        from app.lyrics_sync import merge_text
        rows = [[1000, 'a'], [2000, 'b'], [3000, 'c']]
        self.assertEqual(merge_text(rows, 'a\nb\nc\nd\n\ne'), rows + [[None, 'd'], [None, 'e']])
        self.assertEqual(merge_text(rows, 'a\nB\nc'), [[1000, 'a'], [2000, 'B'], [3000, 'c']])
        self.assertEqual(merge_text(rows, 'a\nx\ny\nc'), [[1000, 'a'], [None, 'x'], [None, 'y'], [3000, 'c']])
        self.assertEqual(merge_text(rows, 'a\nc'), [[1000, 'a'], [3000, 'c']])
