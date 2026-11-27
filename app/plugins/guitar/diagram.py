"""Compact Qt chord diagram; guitar strings are drawn from low E to high E."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget
from app.i18n import t
from app.plugins.guitar.voicings import lookup

class ChordDiagram(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.chord = ''
        self.setMinimumSize(118,155)
        self.setMaximumWidth(165)
        self.setToolTip(t('標準調弦，左至右為第六弦到第一弦；數字為手指編號，格數相對 Capo。'))

    def set_chord(self,chord):
        if chord != self.chord:
            self.chord = chord
            self.update()

    def paintEvent(self,_event):
        p = QPainter(self)
        draw_chord(p,self.width(),self.height(),self.chord,lookup(self.chord))
        p.end()

def render_chord_image(chord,voicing,generated=False,active=False,width=84,height=113,scale=1):
    image = QImage(width*scale,height*scale,QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.scale(scale,scale)
    draw_chord(painter,width,height,chord,voicing,generated,active)
    painter.end()
    return image

def draw_chord(p,width,height,chord,voicing,generated=False,active=False):
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.fillRect(QRectF(0,0,width,height),QColor('#fff1bd' if active else '#f3f7fa'))
    if active:
        border = QPen(QColor('#c78a00')); border.setWidthF(3); p.setPen(border)
        p.drawRoundedRect(QRectF(2,2,width-4,height-4),4,4)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor('#c78a00'))
        p.drawEllipse(QPointF(width-10,11),4,4)
    p.setPen(QColor('#173148'))
    title = QFont(p.font()); title.setBold(True); title.setPixelSize(max(12,round(width*.145))); p.setFont(title)
    p.drawText(QRectF(2,2,width-4,20),Qt.AlignmentFlag.AlignCenter,chord or '—')
    if not voicing:
        font = QFont(p.font()); font.setBold(False); font.setPixelSize(10); p.setFont(font)
        message = t('無和弦') if chord == 'N' else t('暫無指法圖')
        p.drawText(QRectF(4,36,width-8,85),Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,message)
        return
    x0, x1 = 16., width-10.
    top, bottom = 39., height-(19. if generated else 9.)
    dx, dy = (x1-x0)/5, (bottom-top)/4
    pressed = [f for f in voicing.frets if f is not None and f > 0]
    first = min(pressed,default=1)
    start = 1 if first <= 1 or (0 in voicing.frets and max(pressed,default=0) <= 4) else first
    font = QFont(p.font()); font.setBold(True); font.setPixelSize(10); p.setFont(font)
    if start > 1:
        p.drawText(QRectF(1,top,20,dy),Qt.AlignmentFlag.AlignCenter,str(start))
    pen = QPen(QColor('#243d50')); pen.setWidthF(1.35); p.setPen(pen)
    for i in range(6): p.drawLine(QPointF(x0+i*dx,top),QPointF(x0+i*dx,bottom))
    for i in range(5):
        pen.setWidthF(3 if i == 0 and start == 1 else 1.35)
        p.setPen(pen); p.drawLine(QPointF(x0,top+i*dy),QPointF(x1,top+i*dy))
    groups = {}
    for i,(fret,finger) in enumerate(zip(voicing.frets,voicing.fingers)):
        if fret and finger: groups.setdefault((fret,finger),[]).append(i)
    for (fret,_finger),strings in groups.items():
        if len(strings) < 2: continue
        y = top + (fret-start+.5)*dy
        bar = QPen(QColor('#174d72'))
        bar.setWidthF(min(dx*.7,dy*.5,12.)); bar.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(bar); p.drawLine(QPointF(x0+strings[0]*dx,y),QPointF(x0+strings[-1]*dx,y))
    p.setPen(QColor('#30485b'))
    for i,fret in enumerate(voicing.frets):
        x = x0+i*dx
        if fret is None or fret == 0:
            p.setPen(QColor('#30485b'))
            p.drawText(QRectF(x-dx/2,22,dx,17),Qt.AlignmentFlag.AlignCenter,'×' if fret is None else '○')
            continue
        y = top + (fret-start+.5)*dy
        radius = min(dx*.49,dy*.42,8.)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor('#174d72'))
        p.drawEllipse(QPointF(x,y),radius,radius)
        p.setPen(QColor('white'))
        finger = voicing.fingers[i]
        if finger: p.drawText(QRectF(x-radius,y-radius,2*radius,2*radius),Qt.AlignmentFlag.AlignCenter,str(finger))
    if generated:
        p.setPen(QColor('#405769'))
        font = QFont(p.font()); font.setBold(False); font.setPixelSize(9); p.setFont(font)
        p.drawText(QRectF(0,height-15,width,13),Qt.AlignmentFlag.AlignCenter,t('自動產生'))
