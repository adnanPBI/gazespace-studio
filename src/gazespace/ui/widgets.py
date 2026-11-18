"""Native-painted camera, gaze and 3D workspace views (no network UI)."""
from __future__ import annotations
import math
import numpy as np
from PySide6.QtCore import Qt, QPointF, QRectF, Signal
from PySide6.QtGui import QImage, QPainter, QPen, QColor, QFont, QPolygonF
from PySide6.QtWidgets import QWidget, QLabel, QFrame, QVBoxLayout
from .theme import BG, PANEL, BORDER, TEXT, MUTED, TEAL, BLUE, AMBER


def label(text: str, kind: str = '', wrap: bool = False) -> QLabel:
    obj = QLabel(text)
    if kind:
        obj.setObjectName(kind)
    obj.setWordWrap(wrap)
    return obj


def card() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName('card')
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(10)
    return frame, layout


class Metric(QFrame):
    def __init__(self, title, hint=''):
        super().__init__()
        self.setObjectName('card')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 13, 15, 13)
        layout.addWidget(label(title.upper(), 'muted'))
        self.value = label('—', 'metric')
        self.hint = label(hint, 'muted', True)
        layout.addWidget(self.value)
        layout.addWidget(self.hint)

    def set_value(self, value, hint=None):
        self.value.setText(str(value))
        if hint is not None:
            self.hint.setText(str(hint))


class VideoCanvas(QWidget):
    roi_selected = Signal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(360, 220)
        self._image = None
        self._bgr = None
        self.result = None
        self.observation = None
        self.synthetic = False
        self.live_state = "idle"
        self.selection_side = None
        self._drag_start = None
        self._drag_end = None
        self.rois = {}
        self.display_rect = QRectF()
        self.setMouseTracking(True)
        self.setObjectName('cameraCanvas')

    def set_frame(self, bgr, result=None, observation=None, synthetic=False):
        self.result = result
        self.observation = observation
        self.synthetic = synthetic
        if bgr is not None:
            self._bgr = bgr.copy()
            rgb = np.ascontiguousarray(bgr[:, :, ::-1])
            self._image = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format.Format_RGB888).copy()
        self.update()

    def set_selection(self, side):
        self.selection_side = side
        self.setCursor(Qt.CursorShape.CrossCursor if side else Qt.CursorShape.ArrowCursor)
        self.update()

    def map_image(self, xy):
        if self._image is None:
            return QPointF()
        return QPointF(self.display_rect.x() + xy[0] / self._image.width() * self.display_rect.width(),
                       self.display_rect.y() + xy[1] / self._image.height() * self.display_rect.height())

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor('#080f1b'))
        if self._image is None:
            p.setPen(QColor(MUTED))
            p.setFont(QFont('Segoe UI', 16))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       'Camera preview\n\nStart a camera or open the built-in simulation')
            return
        scale = min(self.width() / self._image.width(), self.height() / self._image.height())
        w, h = self._image.width() * scale, self._image.height() * scale
        self.display_rect = QRectF((self.width() - w)/2, (self.height()-h)/2, w, h)
        p.drawImage(self.display_rect, self._image)
        res = self.result
        pupils = (res.get('pupils_px', {}) if isinstance(res, dict) else getattr(res, 'pupils_px', {})) or {}
        for side, xy in pupils.items():
            point = self.map_image(xy)
            p.setPen(QPen(QColor(TEAL if side == 'right' else BLUE), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(point, 7, 7)
            p.drawLine(point + QPointF(-13, 0), point + QPointF(13, 0))
            p.drawLine(point + QPointF(0, -13), point + QPointF(0, 13))
        if self.observation is not None:
            points = getattr(self.observation, 'landmarks_px', {})
            p.setPen(QPen(QColor('#9bb9e0'), 2))
            for idx in (1, 152, 33, 263, 61, 291):
                if idx in points:
                    p.drawEllipse(self.map_image(points[idx]), 2, 2)
        for side, box in self.rois.items():
            x, y, rw, rh = box
            r = QRectF(self.display_rect.x() + x*w, self.display_rect.y()+y*h, rw*w, rh*h)
            p.setPen(QPen(QColor(TEAL if side == 'right' else BLUE), 1, Qt.PenStyle.DashLine))
            p.drawRect(r)
        if self._drag_start is not None and self._drag_end is not None:
            p.setPen(QPen(QColor(AMBER), 2))
            p.drawRect(QRectF(self._drag_start, self._drag_end).normalized())
        badge = 'SIMULATION · synthetic pupils' if self.synthetic else 'LIVE FRAME · local processing'
        if self.live_state=='replay':
            badge=('SIMULATION · ' if self.synthetic else '')+'RECORDED FRAME'
        elif self.live_state!='running':
            badge=('SIMULATION · ' if self.synthetic else '')+'LAST FRAME / '+self.live_state.upper()
        if self.selection_side:
            badge = f'Drag around the {self.selection_side} eye, then release'
        p.fillRect(QRectF(12, 12, min(self.width()-24, 370), 29), QColor('#dd102033'))
        p.setPen(QColor(AMBER if self.synthetic else TEAL))
        p.setFont(QFont('Segoe UI', 10, QFont.Weight.DemiBold))
        p.drawText(QRectF(23, 12, self.width()-40, 29), Qt.AlignmentFlag.AlignVCenter, badge)

    def mousePressEvent(self, event):
        if self.selection_side and self.display_rect.contains(event.position()):
            self._drag_start = event.position()
            self._drag_end = event.position()
            self.update()

    def mouseMoveEvent(self, event):
        if self._drag_start is not None:
            self._drag_end = event.position()
            self.update()

    def mouseReleaseEvent(self, event):
        if self._drag_start is None or not self.selection_side:
            return
        r = QRectF(self._drag_start, event.position()).normalized().intersected(self.display_rect)
        self._drag_start = self._drag_end = None
        if r.width() >= 12 and r.height() >= 12:
            d = self.display_rect
            roi = ((r.x()-d.x())/d.width(), (r.y()-d.y())/d.height(), r.width()/d.width(), r.height()/d.height())
            side = self.selection_side
            self.rois[side] = roi
            self.set_selection(None)
            self.roi_selected.emit(side, roi)
        self.update()


class GazeMap(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(280, 140)
        self.position = None
        self.trail = []
        self.aois = []
        self.caption = 'Calibrate a screen to see gaze coordinates'

    def set_gaze(self, xy, valid=True):
        self.position = xy if valid and xy is not None else None
        if self.position is not None:
            self.trail.append(tuple(xy))
            self.trail = self.trail[-100:]
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(18, 12, self.width()-36, self.height()-40)
        p.setBrush(QColor('#0b1626'))
        p.setPen(QPen(QColor(BORDER), 1))
        p.drawRoundedRect(r, 7, 7)
        for i in range(1, 4):
            p.setPen(QPen(QColor('#1e3047'), 1))
            p.drawLine(QPointF(r.x()+i*r.width()/4, r.y()), QPointF(r.x()+i*r.width()/4, r.bottom()))
            p.drawLine(QPointF(r.x(), r.y()+i*r.height()/4), QPointF(r.right(), r.y()+i*r.height()/4))
        for i, (x,y) in enumerate(self.trail):
            col = QColor(TEAL)
            col.setAlpha(20+int(i/max(1,len(self.trail))*100))
            p.setBrush(col)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(r.x()+x*r.width(), r.y()+y*r.height()), 2.5, 2.5)
        if self.position is not None:
            x,y = self.position
            q = QPointF(r.x()+x*r.width(), r.y()+y*r.height())
            p.setPen(QPen(QColor(TEAL), 2))
            p.setBrush(QColor('#3845dfbd'))
            p.drawEllipse(q, 11, 11)
            p.setBrush(QColor(TEAL))
            p.drawEllipse(q, 3, 3)
        p.setPen(QColor(MUTED))
        p.setFont(QFont('Segoe UI', 9))
        p.drawText(QRectF(0,self.height()-24,self.width(),20), Qt.AlignmentFlag.AlignCenter, self.caption)


class WorkspaceView(QWidget):
    """Interactive isometric rendering of measured surfaces and the gaze ray."""
    def __init__(self):
        super().__init__()
        self.setMinimumSize(360, 240)
        self.surfaces = []
        self.origin = self.direction = None
        self.yaw = -0.55
        self.pitch = 0.55
        self.zoom = 1.0
        self._last = None
        self._projector = None

    def set_scene(self, surfaces, origin=None, direction=None):
        self.surfaces = surfaces or []
        self.origin, self.direction = origin, direction
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor('#0b1626'))
        points = [np.asarray(s['origin_world_m'], float) for s in self.surfaces]
        center = np.mean(points, axis=0) if points else np.array([0.3, 0.2, 0.2])
        cy,sy,cp,sp = math.cos(self.yaw),math.sin(self.yaw),math.cos(self.pitch),math.sin(self.pitch)
        rotate = np.array([[cy,0,sy],[sp*sy,cp,-sp*cy],[-cp*sy,sp,cp*cy]])
        scale = min(self.width(),self.height())*.65*self.zoom
        def project(point):
            q = rotate @ (np.asarray(point, float)-center)
            return QPointF(self.width()*.5+q[0]*scale, self.height()*.53+q[1]*scale)
        self._projector = project
        p.setPen(QPen(QColor('#23384c'),1))
        for t in np.linspace(-.2,.8,11):
            p.drawLine(project([t,0,-.2]),project([t,0,.8]))
            p.drawLine(project([-.2,0,t]),project([.8,0,t]))
        for i,s in enumerate(self.surfaces):
            o = np.asarray(s['origin_world_m'],float)
            u = np.asarray(s['u_vector'],float); u = u / max(np.linalg.norm(u),1e-9) * s['width_m']
            v = np.asarray(s['v_vector'],float); v = v / max(np.linalg.norm(v),1e-9) * s['height_m']
            color = QColor(TEAL if i%2==0 else BLUE)
            color.setAlpha(48)
            p.setBrush(color)
            p.setPen(QPen(QColor(TEAL if i%2==0 else BLUE),1.7))
            p.drawPolygon(QPolygonF([project(q) for q in (o,o+u,o+u+v,o+v)]))
            p.setPen(QColor(TEXT))
            p.drawText(project(o)+QPointF(6,-8),s['name'])
        if self.origin is not None and self.direction is not None:
            o = np.asarray(self.origin);d=np.asarray(self.direction)
            p.setPen(QPen(QColor(AMBER),2))
            p.drawLine(project(o),project(o+d*1.2))
            p.setBrush(QColor(AMBER))
            p.drawEllipse(project(o),4,4)
        for xyz,col,name in (([.18,0,0],'#ff9da7','X'),([0,.18,0],TEAL,'Y'),([0,0,.18],BLUE,'Z')):
            p.setPen(QPen(QColor(col),2))
            p.drawLine(project([0,0,0]),project(xyz))
            p.drawText(project(xyz)+QPointF(4,-4),name)
        p.setPen(QColor(MUTED));p.setFont(QFont('Segoe UI',9))
        p.drawText(QRectF(12,10,self.width()-24,25),'WORLD FRAME · meters   /   drag to orbit · wheel to zoom')
        if not self.surfaces:
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, 'Add measured workspace surfaces\nThe ray appears after spatial calibration')

    def mousePressEvent(self,event):
        self._last=event.position()

    def mouseMoveEvent(self,event):
        if self._last is not None:
            delta=event.position()-self._last
            self.yaw+=delta.x()*.008
            self.pitch=float(np.clip(self.pitch+delta.y()*.008,-1.4,1.4))
            self._last=event.position();self.update()

    def mouseReleaseEvent(self,event):
        self._last=None

    def wheelEvent(self,event):
        self.zoom=float(np.clip(self.zoom*(1.12 if event.angleDelta().y()>0 else .89),.25,5))
        self.update()
