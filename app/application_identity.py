"""Windows shell identity and raster icons for Qt's native windows."""

import sys
from PySide6.QtGui import QIcon
from runtime_config import ROOT

APP_ID = 'KaraMorph.Desktop'


def configure_process_identity():
    # Call before creating QApplication or any native window.
    if sys.platform == 'win32':
        import ctypes
        set_identity = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        set_identity.argtypes = [ctypes.c_wchar_p]
        set_identity.restype = ctypes.c_long
        set_identity(APP_ID)


def application_icon():
    source = QIcon(str(ROOT / 'assets' / 'icon.svg'))
    result = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        result.addPixmap(source.pixmap(size, size))
    return result
