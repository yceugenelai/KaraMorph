import ast
import json
from pathlib import Path
from string import Formatter
import unittest

from app.i18n import configure, t


class TranslationTests(unittest.TestCase):
    def tearDown(self):
        configure('zh_TW')

    def test_catalog_coverage_and_placeholders(self):
        root = Path(__file__).resolve().parents[1] / 'app'
        english = json.loads((root / 'translations/en.json').read_text(encoding='utf-8'))
        japanese = json.loads((root / 'translations/ja.json').read_text(encoding='utf-8'))
        self.assertEqual(set(english), set(japanese))
        def fields(text):
            return sorted((name, spec, conversion) for _, name, spec, conversion in Formatter().parse(text)
                          if name is not None)
        for source in english:
            for catalog in (english, japanese):
                self.assertTrue(catalog[source], source)
                self.assertEqual(fields(source), fields(catalog[source]), source)
        for name in ('consumer', 'karaoke_window', 'lyrics_dialog', 'lyrics', 'batch_lyrics',
                     'style_execution', 'karaoke_assets', 'models_dialog', 'image_assets_widget',
                     'image_receiver', 'browser_images'):
            for node in ast.walk(ast.parse((root / f'{name}.py').read_text(encoding='utf-8'))):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 't'
                        and node.args and isinstance(node.args[0], ast.Constant)):
                    self.assertIn(node.args[0].value, english, f'{name}:{node.lineno}')

    def test_fallback_formatting_and_user_values(self):
        configure('invalid')
        self.assertEqual(t('唱歌'), '唱歌')
        configure('en')
        self.assertEqual(t('速度 {p0:g}×', p0=0.9), 'Speed 0.9×')
        self.assertEqual(t('正在播放  {p0}', p0='原曲 {name}'), 'Playing  原曲 {name}')
        self.assertEqual(t('no images'), 'no images')
        configure('ja')
        self.assertEqual(t('no images'), 'no images')
