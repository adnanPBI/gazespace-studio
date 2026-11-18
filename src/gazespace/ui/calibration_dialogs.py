"""Guided acquisition dialogs share the same temporal collector as the SDK."""
from __future__ import annotations
import uuid
import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal, QRectF, QPointF
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QKeyEvent
from PySide6.QtWidgets import QWidget, QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QProgressBar
from ..acquisition import TargetCollector
from ..clock import host_now_s
from .widgets import label, VideoCanvas
from .theme import TEAL, MUTED, AMBER, TEXT, BG


class TargetSessionDialog(QWidget):
    samples_ready = Signal(object)
    cancelled = Signal()

    def __init__(self, snapshot, targets, *, purpose='calibration', screen=None,
                 screen_size=None, collector_options=None, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle('GazeSpace · ' + purpose)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.snapshot = snapshot
        self.targets = targets
        self.purpose = purpose
        self.screen = screen
        self.screen_size = screen_size
        self.options = collector_options or {}
        self.index = 0
        self.collector = None
        self.records = []
        self.context = {}
        self.session_id = str(uuid.uuid4())
        self.status_text = 'Look at the target, then press SPACE to collect'
        self.started = False
        self._finished = False
        self._active = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(20)
        if screen is not None:
            self.setGeometry(screen.geometry())
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.resize(1280,800)

    def begin_fullscreen(self):
        if self.screen is not None:
            self.setGeometry(self.screen.geometry())
        self.show()
        if self.screen is not None and self.windowHandle() is not None:
            self.windowHandle().setScreen(self.screen)
        self.showFullScreen()
        self.activateWindow()
        self.setFocus()
        if not self.screen_size:
            dpr = self.devicePixelRatioF()
            self.screen_size = (round(self.width()*dpr),round(self.height()*dpr))

    def collect(self):
        if self._active or self.index >= len(self.targets):
            return
        tid, xy = self.targets[self.index]
        self.collector = TargetCollector(tid, xy, kind='screen', **self.options)
        self.collector.start()
        self.wall_started=host_now_s()
        self._active = True
        self.status_text = 'Settling on target… keep your eyes comfortably open'
        self.update()

    def tick(self):
        if self._finished or not self._active:
            return
        if host_now_s()-self.wall_started>self.collector.max_seconds:
            self._active=False;self.status_text='No stable observations in time. Press SPACE to retry, or ESC to cancel.';self.update();return
        state = self.snapshot()
        ob = state.get('observation')
        if state.get('state') != 'running' or ob is None:
            self.status_text = 'Waiting for a current camera observation'
            self.update()
            return
        self.context = dict(getattr(ob, 'context', {}) or self.context)
        self.collector.add(ob)
        progress = self.collector.progress()
        self.status_text = (f"{progress['accepted']}/{progress['required']} accepted · "
                            f"{progress['stable_duration_s']:.1f}s stable · {progress['reason']}")
        if self.collector.complete:
            self.records.extend(self.collector.records(screen_size=self.screen_size))
            self.index += 1
            self._active = False
            self.status_text = ('Take a break or move naturally, then fixate the center and press SPACE'
                                if self.purpose == 'drift' else 'Next target ready · look at the dot and press SPACE')
            if self.index >= len(self.targets):
                self._finished = True
                self.timer.stop()
                payload = {'records':self.records, 'context':self.context,
                           'session_id':self.session_id, 'screen_size':self.screen_size,
                           'purpose':self.purpose, 'monitor':self.screen.name() if self.screen else ''}
                self.samples_ready.emit(payload)
                self.close()
                return
        elif progress['state'] in ('failed','timeout','timed_out','incompatible'):
            self._active = False
            self.status_text = 'Collection timed out. Improve tracking, then press SPACE to retry this target.'
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(),QColor('#091221'))
        p.setPen(QColor(TEAL));p.setFont(QFont('Segoe UI',13,QFont.Weight.DemiBold))
        p.drawText(QRectF(30,24,self.width()-60,25),Qt.AlignmentFlag.AlignCenter,'GAZESPACE  /  '+self.purpose.upper())
        p.setPen(QColor(TEXT));p.setFont(QFont('Segoe UI',19,QFont.Weight.DemiBold))
        p.drawText(QRectF(30,56,self.width()-60,35),Qt.AlignmentFlag.AlignCenter,
                   f'Target {min(self.index+1,len(self.targets))} of {len(self.targets)}')
        p.setPen(QColor(MUTED));p.setFont(QFont('Segoe UI',11))
        p.drawText(QRectF(30,self.height()-80,self.width()-60,28),Qt.AlignmentFlag.AlignCenter,self.status_text)
        p.drawText(QRectF(30,self.height()-45,self.width()-60,24),Qt.AlignmentFlag.AlignCenter,
                   'SPACE  collect / retry     ·     ESC  cancel without changing calibration')
        if self.index < len(self.targets):
            _,(x,y) = self.targets[self.index]
            size=self.screen_size or (round(self.width()*self.devicePixelRatioF()),round(self.height()*self.devicePixelRatioF()))
            point = QPointF(x*(size[0]-1)/self.devicePixelRatioF(),y*(size[1]-1)/self.devicePixelRatioF())
            p.setPen(QPen(QColor('#315773'),2));p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(point,29,29)
            p.setPen(QPen(QColor(TEAL),3));p.drawEllipse(point,11,11)
            p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor('#ffffff'));p.drawEllipse(point,3,3)
        if self.collector and self._active:
            fraction = self.collector.progress()['fraction']
            p.fillRect(QRectF(0,self.height()-5,self.width()*fraction,5),QColor(TEAL))

    def keyPressEvent(self,event):
        if event.key() in (Qt.Key.Key_Space,Qt.Key.Key_Return):
            self.collect()
        elif event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def closeEvent(self,event):
        self.timer.stop()
        if not self._finished:
            self.cancelled.emit()
        super().closeEvent(event)


class SpatialSessionDialog(QDialog):
    samples_ready = Signal(object)

    def __init__(self, snapshot, targets, extrinsics, *, purpose='spatial calibration', parent=None):
        super().__init__(parent)
        self.setWindowTitle('GazeSpace · ' + purpose)
        self.resize(820,650)
        self.snapshot, self.targets, self.extrinsics = snapshot, targets, extrinsics
        self.purpose = purpose
        self.records = []
        self.index = 0
        self.collector = None
        self.context = {}
        self.session_id = str(uuid.uuid4())
        layout = QVBoxLayout(self)
        self.heading = label('Physical target acquisition','title');layout.addWidget(self.heading)
        self.instructions = label('',wrap=True);layout.addWidget(self.instructions)
        self.video = VideoCanvas();layout.addWidget(self.video,1)
        self.progressbar = QProgressBar();layout.addWidget(self.progressbar)
        self.status = label('Fixate the physical marker at its measured coordinates, then collect.','muted',True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.button = QPushButton('Collect current target');self.button.setObjectName('primary')
        self.button.clicked.connect(self.collect)
        cancel = QPushButton('Cancel');cancel.clicked.connect(self.reject)
        buttons.addWidget(self.button);buttons.addStretch();buttons.addWidget(cancel)
        layout.addLayout(buttons)
        self.timer = QTimer(self);self.timer.timeout.connect(self.tick);self.timer.start(30)
        self.finished.connect(lambda _: self.timer.stop())
        self.refresh_target()

    def refresh_target(self):
        t = self.targets[self.index]
        self.instructions.setText(f"Target {self.index+1}/{len(self.targets)}: {t['id']}\n"
                                  f"World coordinate (meters): {t['xyz_m']} · keep natural head posture")
        self.button.setEnabled(True)
        self.progressbar.setValue(0)

    def collect(self):
        target = self.targets[self.index]
        self.collector = TargetCollector(str(target['id']),target['xyz_m'],kind='spatial')
        self.collector.start()
        self.wall_started = host_now_s()
        self.button.setEnabled(False)

    def tick(self):
        if self.collector and host_now_s()-self.wall_started > self.collector.max_seconds:
            self.collector = None
            self.button.setEnabled(True)
            self.status.setText('No stable observations in time. Restore the camera and retry this target.')
            return
        state = self.snapshot()
        frame = state.get('frame')
        if frame is not None:
            self.video.live_state = state.get('state', 'idle')
            self.video.set_frame(frame.image,state.get('latest'),state.get('observation'))
        ob = state.get('observation')
        if not self.collector or state.get('state') != 'running' or ob is None:
            return
        if getattr(ob,'eye_origin_camera_m',None) is None:
            self.status.setText('A measured or model-based eye origin and valid face pose are required for spatial calibration.')
        self.collector.add(ob)
        progress = self.collector.progress()
        self.status.setText(f"{progress['accepted']}/{progress['required']} · {progress['reason']}")
        self.progressbar.setValue(round(100*progress['fraction']))
        if self.collector.complete:
            self.context = dict(getattr(ob,'context',{}))
            self.records.extend(self.collector.records(extrinsics=self.extrinsics))
            self.collector = None
            self.index += 1
            if self.index == len(self.targets):
                self.samples_ready.emit({'records':self.records,'context':self.context,'session_id':self.session_id,
                                         'purpose':self.purpose,'targets':self.targets})
                self.accept()
            else:
                self.refresh_target()
        elif progress['state'] in ('failed','timeout','timed_out','incompatible'):
            self.collector = None;self.button.setEnabled(True)
            self.status.setText('Collection timed out. Adjust lighting and head pose, then retry.')
