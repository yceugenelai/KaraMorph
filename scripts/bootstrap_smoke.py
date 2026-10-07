"""Bootstrap UI-only acceptance: no model weights or AI runtime required."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import sys
from pathlib import Path
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
assert Path(sys.prefix).resolve() == root / '.runtime/ui', sys.prefix
assert sys.flags.no_user_site
assert not any(Path(p).resolve().name == 'site-packages' and not Path(p).resolve().is_relative_to(root / '.runtime/ui') for p in sys.path if p)
from PySide6.QtWidgets import QApplication
from app.consumer import ConsumerWindow
from app.i18n import configure, language as current_language
from unittest.mock import patch
from app.storage import load_settings
from app.models_dialog import ModelsDialog
import numpy, scipy, soundfile, sounddevice, tinytag
app = QApplication([])
selected = load_settings().get('ui_language', 'zh_TW')
window = ConsumerWindow()
assert current_language() == selected
window.close()
for language in ('zh_TW','en','ja'):
    configure(language)
    with patch('app.consumer.load_settings', return_value=dict(load_settings(), ui_language=language)):
        window = ConsumerWindow()
    assert current_language() == language
    assert window.windowTitle() == 'KaraMorph'
    dialog = ModelsDialog(window)
    dialog.close()
    window.close()
print('Bootstrap UI smoke passed: three languages, native audio packages, own Python, no user site')
