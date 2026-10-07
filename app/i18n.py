"""Consumer UI translations; stable data and worker diagnostics stay unchanged."""
import json
from functools import lru_cache
from pathlib import Path

LANGUAGES = (('繁體中文', 'zh_TW'), ('English', 'en'), ('日本語', 'ja'))
_language = 'zh_TW'
_catalog = {}
_translators = []


@lru_cache(maxsize=3)
def catalog(code):
    path = Path(__file__).with_name('translations') / f'{code}.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}


def translate_in(code, source, **values):
    text = catalog(code).get(source, source)
    return text.format(**values) if values else text


def language():
    return _language


def configure(code='zh_TW'):
    global _language, _catalog
    _language = code if code in dict((code, name) for name, code in LANGUAGES) else 'zh_TW'
    _catalog = catalog(_language)
    # Qt's own buttons and non-native dialogs use its bundled translations.
    try:
        from PySide6.QtCore import QCoreApplication, QLibraryInfo, QTranslator
        app = QCoreApplication.instance()
        if app is not None:
            for translator in _translators:
                app.removeTranslator(translator)
            _translators.clear()
            class CatalogTranslator(QTranslator):
                def isEmpty(self):
                    return False

                def translate(self, context, sourceText, disambiguation=None, n=-1):
                    # A null QString lets Qt use another translator or the source.
                    # Returning an empty string would hide standard button text.
                    return _catalog.get(sourceText) if context == 'AIKaraoke' else None

            consumer = CatalogTranslator(app)
            app.installTranslator(consumer)
            _translators.append(consumer)
            translator = QTranslator(app)
            root = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
            if translator.load(f'qtbase_{_language}', root):
                app.installTranslator(translator)
                _translators.append(translator)
    except ImportError:
        pass


def t(source, **values):
    text = _catalog.get(source, source)
    return text.format(**values) if values else text


def preset_label(key):
    return t(key)
