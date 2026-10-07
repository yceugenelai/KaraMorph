"""Small consistent SVG icons, independent of the system emoji font."""
from functools import lru_cache

from PySide6.QtCore import QByteArray
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

PATHS = {
    'play': '<path d="M8 4l13 8-13 8z"/>',
    'pause': '<path d="M8 5v14M16 5v14"/>',
    'stop': '<rect x="6" y="6" width="12" height="12" rx="1"/>',
    'previous': '<path d="M5 5v14M19 5L8 12l11 7z"/>',
    'next': '<path d="M19 5v14M5 5l11 7-11 7z"/>',
    'music': '<path d="M9 17V5l11-2v12M9 7l11-2"/><ellipse cx="6" cy="18" rx="3" ry="2"/><ellipse cx="17" cy="16" rx="3" ry="2"/>',
    'guide': '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M6 10v2a6 6 0 0012 0v-2M12 18v3M9 21h6"/>',
    'guide_on': '<rect x="6" y="3" width="6" height="11" rx="3"/><path d="M3 10v2a6 6 0 0012 0v-2M9 18v3M6 21h6M16 6l2 2 4-5"/>',
    'monitor': '<path d="M4 13v-2a8 8 0 0116 0v2"/><rect x="3" y="12" width="4" height="8" rx="2"/><rect x="17" y="12" width="4" height="8" rx="2"/>',
    'record': '<circle cx="12" cy="12" r="7"/>',
    'record_ready': '<circle cx="12" cy="12" r="7"/><path d="M7 12l3 3 6-6"/>',
    'recording': '<circle cx="12" cy="12" r="7" fill="{color}"/>',
    'more': '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
    'fullscreen': '<path d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5"/>',
    'restore': '<path d="M9 4v5H4M20 9h-5V4M15 20v-5h5M4 15h5v5"/>',
}


@lru_cache(maxsize=64)
def icon(name, color='#edf5fc'):
    result = QIcon()
    for mode, ink in ((QIcon.Mode.Normal, color), (QIcon.Mode.Disabled, '#768697')):
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
               f'<g fill="none" stroke="{ink}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
               + PATHS[name].replace('{color}', ink) + '</g></svg>')
        renderer = QSvgRenderer(QByteArray(svg.encode()))
        for size in (24, 48):
            pix = QPixmap(size, size)
            pix.fill('transparent')
            painter = QPainter(pix)
            renderer.render(painter)
            painter.end()
            result.addPixmap(pix, mode)
    return result
