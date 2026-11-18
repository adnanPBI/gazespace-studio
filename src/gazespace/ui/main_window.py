"""Complete native desktop workflow: devices, calibration, live views and sessions."""
from __future__ import annotations
from datetime import datetime
import json
import hashlib
import math
import os
import sys
from pathlib import Path
import threading
import time
import traceback
import numpy as np
import cv2
from PySide6.QtCore import Qt, QTimer, QThreadPool, QUrl, Signal, QByteArray
from PySide6.QtGui import QAction, QKeySequence, QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame, QLabel, QPushButton, QVBoxLayout,
    QHBoxLayout, QGridLayout, QFormLayout, QLineEdit, QComboBox, QSpinBox,
    QDoubleSpinBox, QCheckBox, QStackedWidget, QScrollArea, QFileDialog,
    QTableWidget, QTableWidgetItem, QHeaderView, QListWidget, QPlainTextEdit,
    QSlider, QSplitter, QMessageBox, QDialog, QDialogButtonBox, QProgressBar, QSizePolicy)
from .. import __version__
from ..clock import host_now_s
from ..capture import create_source, list_cameras, list_basler_cameras, SyntheticEyeSource
from ..vision import GazeTracker
from ..engine import GazeEngine
from ..runtime import TrackingRuntime
from ..types import CameraIntrinsics, CameraExtrinsics, Surface
from ..calibration import (new_profile, load_profile, load_json, save_json, update_profile,
                           fit_gaze_model, GazeModel, calibrate_intrinsics,
                           solve_world_board_extrinsics, calibrate_stereo)
from ..sessions import SessionRecorder, load_session, list_sessions, SessionReplay
from ..analytics import analyze_session, export_report, heatmap_svg
from ..demo import build_demo_profile, DEMO_SOURCE_CONTEXT
from .theme import STYLE, TEAL, MUTED
from .fonts import configure_app_fonts
from .widgets import label, card, Metric, VideoCanvas, GazeMap, WorkspaceView
from .jobs import FunctionJob
from .calibration_dialogs import TargetSessionDialog, SpatialSessionDialog


def default_workspace() -> Path:
    base = os.environ.get('LOCALAPPDATA') or str(Path.home()/'.local'/'share')
    return Path(base)/'GazeSpace'


def _button(text, handler, primary=False):
    button=QPushButton(text)
    if primary:
        button.setObjectName('primary')
    button.clicked.connect(handler)
    return button


def _parse_vector(text, n=3):
    values=tuple(float(x.strip()) for x in text.split(','))
    if len(values)!=n or not np.isfinite(values).all():
        raise ValueError(f'Expected {n} finite comma-separated numbers')
    return values


class MainWindow(QMainWindow):
    error_reported = Signal(str)

    def __init__(self, workspace=None, profile=None, *, interactive_errors=True):
        configure_app_fonts()
        super().__init__()
        self.workspace=Path(workspace or default_workspace()).expanduser().resolve()
        for name in ('profiles','board','sessions','reports'):
            (self.workspace/name).mkdir(parents=True,exist_ok=True)
        self.settings_path=self.workspace/'desktop_settings.json'
        self.settings={'source':'0','backend':'dshow' if os.name=='nt' else 'auto',
                       'mode':'rgb','width':1280,'height':720,'fps':30,
                       'subject_id':'operator-1','pupil_polarity':'dark','profile':'',
                       'roi_normalized':{},'face_model':'','exposure_native':None,
                       'exposure_us':None,'gain':None,'ir_bit_depth':8}
        if self.settings_path.exists():
            try:
                self.settings.update(load_json(self.settings_path))
            except (ValueError,OSError):
                pass
        if profile:
            self.settings['profile']=str(Path(profile).resolve())
        self.interactive_errors=interactive_errors
        self.runtime=None
        self.recorder=None
        self.record_lock=threading.RLock()
        self.last_snapshot={}
        self._sequence=None
        self._jobs=[]
        self._api_server=None
        self._api_thread=None
        self._api_token=None
        self._session_dialog=None
        self.current_session=None
        self.replay=None
        self._replay_clock=None
        self._replay_capture=None
        self._replay_index=-1
        self._is_demo=False
        self.setWindowTitle(f'GazeSpace Studio {__version__}')
        self.resize(1440,960)
        self.setMinimumSize(1080,740)
        self.setStyleSheet(STYLE)
        self.pool=QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self._build_ui()
        self._build_menu()
        self._load_form()
        self.refresh_profile()
        self.refresh_sessions()
        self.timer=QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(33)
        self.statusBar().showMessage('Ready · choose a camera or open the synthetic demo')

    def _build_ui(self):
        root=QWidget();root.setObjectName('root');self.setCentralWidget(root)
        main=QHBoxLayout(root);main.setContentsMargins(0,0,0,0);main.setSpacing(0)
        sidebar=QFrame();sidebar.setObjectName('sidebar');sidebar.setMinimumWidth(220)
        # Let the layout's font/style-aware size hint determine the width.
        # A hard 220-pixel cap clips labels with Windows/high-DPI font metrics
        # and cannot respond to font changes after the window is shown.
        sidebar.setSizePolicy(QSizePolicy.Policy.Fixed,QSizePolicy.Policy.Expanding)
        nav=QVBoxLayout(sidebar);nav.setContentsMargins(19,26,19,22);nav.setSpacing(7)
        nav.addWidget(label('G A Z E S P A C E','eyebrow'))
        nav.addWidget(label('Vision Studio','section'))
        nav.addWidget(label('Calibrate. Track. Measure.','muted'))
        nav.addSpacing(27)
        self.pages=QStackedWidget();self.nav_buttons=[]
        entries=[('01   Live workspace',self._live_page),('02   Cameras',self._device_page),
                 ('03   Calibration',self._calibration_page),('04   Workspace',self._workspace_page),
                 ('05   Sessions',self._sessions_page),('06   Diagnostics',self._profiles_page),
                 ('07   Integration',self._integration_page)]
        for index,(title,builder) in enumerate(entries):
            button=QPushButton(title);button.setCheckable(True);button.setObjectName('nav')
            button.clicked.connect(lambda checked=False,i=index:self.set_page(i))
            nav.addWidget(button);self.nav_buttons.append(button)
            page=builder()
            scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(page)
            self.pages.addWidget(scroll)
        nav.addStretch()
        nav.addWidget(label('LOCAL PROCESSING','eyebrow'))
        nav.addWidget(label('Camera video stays on this\ncomputer. Recording is opt-in.','muted',True))
        nav.addSpacing(16)
        self.sidebar_profile=label('No calibrated profile','muted',True);nav.addWidget(self.sidebar_profile)
        nav.addWidget(label(f'Studio {__version__}','muted'))
        main.addWidget(sidebar);main.addWidget(self.pages,1)
        self.set_page(0)

    def _page(self,title,description):
        page=QWidget();page.setObjectName('root')
        layout=QVBoxLayout(page);layout.setContentsMargins(26,24,26,22);layout.setSpacing(16)
        layout.addWidget(label(title,'title'))
        layout.addWidget(label(description,'muted',True))
        return page,layout

    def _live_page(self):
        page,layout=self._page('Live workspace','One place to inspect the camera, gaze output and tracking health.')
        controls=QHBoxLayout()
        self.live_badge=label('READY','badge');controls.addWidget(self.live_badge)
        controls.addStretch()
        self.start_button=_button('Start camera',self.start_tracking,True);controls.addWidget(self.start_button)
        self.demo_button=_button('Open simulation',self.open_demo);controls.addWidget(self.demo_button)
        self.pause_button=_button('Pause',self.toggle_pause);controls.addWidget(self.pause_button)
        self.stop_button=_button('Stop',self.stop_tracking);controls.addWidget(self.stop_button)
        layout.addLayout(controls)
        content=QHBoxLayout();content.setSpacing(16)
        preview,pl=card();pl.addWidget(label('CAMERA  /  LANDMARKS & PUPILS','eyebrow'))
        self.preview=VideoCanvas();self.preview.setMinimumHeight(340);pl.addWidget(self.preview,1)
        self.live_reason=label('No active stream','muted',True);pl.addWidget(self.live_reason)
        self.head_pose_text=label('Head pose: —','muted');pl.addWidget(self.head_pose_text)
        content.addWidget(preview,4)
        metrics=QVBoxLayout();metrics.setSpacing(12)
        self.quality_metric=Metric('Tracking quality','Heuristic signal quality')
        self.fps_metric=Metric('Throughput','Processed frames per second')
        self.latency_metric=Metric('Processing','Recent median / p95')
        self.state_metric=Metric('Gaze output','Requires a compatible profile')
        for m in (self.quality_metric,self.fps_metric,self.latency_metric,self.state_metric):metrics.addWidget(m)
        content.addLayout(metrics,1);layout.addLayout(content,5)
        lower=QHBoxLayout();lower.setSpacing(16)
        smap,sl=card();sl.addWidget(label('Screen gaze','section'));self.gaze_map=GazeMap();sl.addWidget(self.gaze_map,1)
        self.screen_xy=label('Screen coordinate: —','muted');sl.addWidget(self.screen_xy);lower.addWidget(smap,1)
        wmap,wl=card();wl.addWidget(label('3D workspace','section'));self.live_world=WorkspaceView();self.live_world.setMinimumSize(280,140);wl.addWidget(self.live_world,1)
        self.surface_text=label('Surface hit: —','muted');wl.addWidget(self.surface_text);lower.addWidget(wmap,1)
        layout.addLayout(lower,3)
        footer=QHBoxLayout();self.record_button=_button('Record session',self.toggle_record);footer.addWidget(self.record_button)
        self.record_video=QCheckBox('Include camera video');self.record_video.setToolTip('Explicitly saves raw camera images to a local AVI file.');footer.addWidget(self.record_video)
        footer.addStretch();self.record_status=label('No recording · metadata stays local','muted');footer.addWidget(self.record_status)
        layout.addLayout(footer)
        return page

    def _device_page(self):
        page,layout=self._page('Cameras & tracking','Choose the capture adapter, verify actual frame settings and tune pupil detection.')
        columns=QHBoxLayout()
        config,cl=card();cl.addWidget(label('Input configuration','section'))
        form=QFormLayout();form.setSpacing(12)
        self.source_edit=QLineEdit();self.source_edit.setObjectName('sourceInput');form.addRow('Index / serial / video path',self.source_edit)
        self.backend_combo=QComboBox();self.backend_combo.addItems(['auto','dshow','msmf','v4l2','ffmpeg','pylon','realsense','synthetic']);form.addRow('Camera adapter',self.backend_combo)
        self.mode_combo=QComboBox();self.mode_combo.addItems(['rgb','ir','ir-roi','preview']);form.addRow('Tracking mode',self.mode_combo)
        self.width_spin=QSpinBox();self.width_spin.setRange(160,7680);self.height_spin=QSpinBox();self.height_spin.setRange(120,4320)
        dims=QHBoxLayout();dims.addWidget(self.width_spin);dims.addWidget(label('×'));dims.addWidget(self.height_spin);form.addRow('Requested resolution',dims)
        self.fps_spin=QSpinBox();self.fps_spin.setRange(1,240);form.addRow('Requested FPS',self.fps_spin)
        self.polarity_combo=QComboBox();self.polarity_combo.addItems(['dark','bright']);form.addRow('IR pupil polarity',self.polarity_combo)
        self.bits_combo=QComboBox();self.bits_combo.addItems(['8','10','12','14','16']);form.addRow('Mono sensor bit depth',self.bits_combo)
        self.exposure_edit=QLineEdit();self.exposure_edit.setPlaceholderText('Blank: leave camera setting unchanged');form.addRow('Exposure (native / pylon µs)',self.exposure_edit)
        self.gain_edit=QLineEdit();self.gain_edit.setPlaceholderText('Blank: leave camera setting unchanged');form.addRow('Gain (camera units)',self.gain_edit)
        self.face_model_edit=QLineEdit();self.face_model_edit.setPlaceholderText('Optional FaceLandmarker .task path');form.addRow('Face model',self.face_model_edit)
        self.subject_edit=QLineEdit();form.addRow('Operator / subject ID',self.subject_edit)
        cl.addLayout(form)
        actions=QHBoxLayout();actions.addWidget(_button('Apply & start',self.apply_and_start,True));actions.addWidget(_button('Open video…',self.choose_video));cl.addLayout(actions)
        cl.addWidget(label('Global shutter is a sensor capability. Exposure, gain and trigger support are reported from the selected adapter.','muted',True))
        columns.addWidget(config,1)
        right=QVBoxLayout()
        detect,dl=card();dl.addWidget(label('Device discovery','section'))
        dl.addWidget(_button('Scan USB and industrial cameras',self.scan_cameras))
        self.device_report=QPlainTextEdit();self.device_report.setReadOnly(True);self.device_report.setMinimumHeight(160);self.device_report.setPlainText('Click Scan to enumerate available hardware.');dl.addWidget(self.device_report)
        right.addWidget(detect,1)
        roi,rl=card();rl.addWidget(label('Infrared eye regions','section'))
        rl.addWidget(label('For ir-roi mode, select each eye on the preview after capture starts. A static ROI supports constrained head movement.','muted',True))
        self.roi_right_edit=QLineEdit();self.roi_left_edit=QLineEdit()
        rf=QFormLayout();rf.addRow('Right eye x,y,w,h',self.roi_right_edit);rf.addRow('Left eye x,y,w,h',self.roi_left_edit);rl.addLayout(rf)
        ra=QHBoxLayout();ra.addWidget(_button('Select right eye',lambda:self.select_roi('right')));ra.addWidget(_button('Select left eye',lambda:self.select_roi('left')));rl.addLayout(ra)
        rl.addWidget(_button('Start preview for ROI selection',self.start_preview));rl.addWidget(_button('Import ROI JSON…',self.import_rois));rl.addWidget(_button('Save ROI JSON…',self.save_rois))
        right.addWidget(roi,1);columns.addLayout(right,1);layout.addLayout(columns)
        self.device_preview=VideoCanvas();self.device_preview.setMinimumHeight(280)
        self.device_preview.roi_selected.connect(self.roi_selected);layout.addWidget(self.device_preview,1)
        return page

    def _calibration_page(self):
        page,layout=self._page('Calibration & validation','Measure the camera geometry, collect stable fixation samples, then test against independent targets.')
        top=QHBoxLayout();self.cal_profile=QLineEdit();self.cal_profile.setReadOnly(True)
        top.addWidget(label('Profile'));top.addWidget(self.cal_profile,1)
        top.addWidget(_button('New profile…',self.new_profile_dialog));top.addWidget(_button('Load…',self.load_profile_dialog));layout.addLayout(top)
        self.cal_summary=label('No profile selected','muted',True);layout.addWidget(self.cal_summary)
        grid=QGridLayout();grid.setSpacing(16)
        board,bl=card();bl.addWidget(label('01  Camera intrinsics','section'))
        bl.addWidget(label('Use a measured, flat checkerboard. Capture varied positions and tilts at the final focus and resolution.','muted',True))
        bf=QFormLayout();self.cols_spin=QSpinBox();self.cols_spin.setRange(3,30);self.cols_spin.setValue(9)
        self.rows_spin=QSpinBox();self.rows_spin.setRange(3,30);self.rows_spin.setValue(6)
        self.square_spin=QDoubleSpinBox();self.square_spin.setRange(1,500);self.square_spin.setValue(24);self.square_spin.setSuffix(' mm')
        bf.addRow('Internal corners across',self.cols_spin);bf.addRow('Internal corners down',self.rows_spin);bf.addRow('Measured square size',self.square_spin);bl.addLayout(bf)
        self.board_count=label('0 images captured in this workspace','muted');bl.addWidget(self.board_count)
        bl.addWidget(_button('Capture checkerboard image',self.capture_board))
        bl.addWidget(_button('Solve camera intrinsics',self.solve_intrinsics,True))
        bl.addWidget(_button('Use another image directory…',self.choose_board_directory))
        bl.addWidget(_button('Validate on fresh board images…',self.validate_camera_folder))
        bl.addWidget(_button('Export printable checkerboard…',self.print_board))
        bl.addWidget(_button('Use depth-camera factory intrinsics',self.use_factory_intrinsics))
        grid.addWidget(board,0,0)
        screen,sl=card();sl.addWidget(label('02  Screen gaze','section'))
        sl.addWidget(label('A 4 × 4 target session learns your gaze. Each target includes a settling period and a stable observation window.','muted',True))
        self.monitor_combo=QComboBox()
        for monitor in QApplication.screens():self.monitor_combo.addItem(monitor.name() or 'Display')
        sf=QFormLayout();sf.addRow('Calibration display',self.monitor_combo);sl.addLayout(sf)
        sl.addWidget(_button('Calibrate screen gaze',lambda:self.start_screen_session('calibration'),True))
        sl.addWidget(_button('Independent 9-point validation',lambda:self.start_screen_session('validation')))
        sl.addWidget(_button('Repeated-fixation drift check',lambda:self.start_screen_session('drift')))
        sl.addWidget(label('Use the same subject, camera, tracking mode and display for calibration and operation.','muted',True));sl.addStretch()
        grid.addWidget(screen,0,1)
        world,wl=card();wl.addWidget(label('03  World coordinate frame','section'))
        wl.addWidget(label('Place the reference board in its measured workspace position. Keep its origin and orientation consistent across captures.','muted',True))
        wl.addWidget(_button('Use current frame as world board',self.capture_world))
        wl.addWidget(_button('Solve world pose from image…',self.solve_world_file,True))
        wl.addWidget(_button('Stereo camera calibration…',self.stereo_dialog))
        wl.addWidget(label('World coordinates: meters, right-handed. Checkerboard origin is the first internal corner; verify the printed board orientation.','muted',True));wl.addStretch()
        grid.addWidget(world,1,0)
        spatial,spl=card();spl.addWidget(label('04  Physical 3D gaze','section'))
        spl.addWidget(label('Load at least nine measured fixation targets spanning the workspace and multiple depths. Physical geometry must be calibrated first.','muted',True))
        self.targets_edit=QLineEdit();self.targets_edit.setPlaceholderText('Measured target JSON file')
        th=QHBoxLayout();th.addWidget(self.targets_edit,1);th.addWidget(_button('Browse…',self.choose_targets));spl.addLayout(th)
        spl.addWidget(_button('Calibrate spatial gaze',lambda:self.start_spatial_session(False),True))
        spl.addWidget(_button('Independent spatial validation',lambda:self.start_spatial_session(True)))
        spl.addWidget(_button('Save target template…',self.save_target_template))
        spl.addWidget(label('A model-based eye origin remains approximate. Depth assistance and stereo measurements have separate evidence labels.','muted',True));spl.addStretch()
        grid.addWidget(spatial,1,1);layout.addLayout(grid)
        report,rl=card();rl.addWidget(label('Latest calibration result','section'))
        self.cal_report=QPlainTextEdit();self.cal_report.setReadOnly(True);self.cal_report.setMinimumHeight(150);rl.addWidget(self.cal_report);layout.addWidget(report)
        self.board_directory=self.workspace/'board'
        return page

    def _workspace_page(self):
        page,layout=self._page('Spatial workspace','Define measured bounded surfaces and inspect the predicted gaze ray in the same world frame.')
        self.world_view=WorkspaceView();self.world_view.setMinimumHeight(330);layout.addWidget(self.world_view,1)
        self.surfaces_table=QTableWidget(0,7)
        self.surfaces_table.setHorizontalHeaderLabels(['Surface name','Origin x,y,z (m)','U axis','V axis','Width (m)','Height (m)','Pixels w,h (optional)'])
        self.surfaces_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.surfaces_table.setMinimumHeight(180);layout.addWidget(self.surfaces_table)
        actions=QHBoxLayout();actions.addWidget(_button('Add surface',self.add_surface));actions.addWidget(_button('Remove selected',self.remove_surface))
        actions.addWidget(_button('Import JSON…',self.import_surfaces));actions.addWidget(_button('Export JSON…',self.export_surfaces));actions.addStretch()
        actions.addWidget(_button('Save surfaces to profile',self.apply_surfaces,True));layout.addLayout(actions)
        layout.addWidget(label('Set U to the physical right direction and V to down on each surface. Surface UV coordinates use meters; screen pixel coordinates are bounded to the declared size.','muted',True))
        self.world_hit=label('No spatial gaze ray available','muted',True);layout.addWidget(self.world_hit)
        return page

    def _sessions_page(self):
        page,layout=self._page('Sessions & insights','Replay local recordings, inspect fixation stability and export portable reports.')
        top=QHBoxLayout();top.addWidget(_button('Refresh sessions',self.refresh_sessions));top.addWidget(_button('Open session folder…',self.open_session_folder))
        top.addStretch();top.addWidget(_button('Export report + CSV',self.export_session_report,True));layout.addLayout(top)
        row=QHBoxLayout();self.session_list=QListWidget();self.session_list.setMinimumWidth(250);self.session_list.itemSelectionChanged.connect(self.select_session)
        row.addWidget(self.session_list,1)
        preview,pl=card();pl.addWidget(label('Recorded screen gaze','section'));self.replay_map=GazeMap();self.replay_map.setMinimumHeight(210);pl.addWidget(self.replay_map,1)
        self.replay_caption=label('Select a session','muted',True);pl.addWidget(self.replay_caption)
        self.replay_slider=QSlider(Qt.Orientation.Horizontal);self.replay_slider.valueChanged.connect(self.seek_replay);pl.addWidget(self.replay_slider)
        rp=QHBoxLayout();self.replay_button=_button('Play',self.play_replay);rp.addWidget(self.replay_button);rp.addWidget(_button('Pause',self.pause_replay));rp.addStretch();pl.addLayout(rp)
        self.replay_video=VideoCanvas();self.replay_video.setMinimumHeight(170);pl.addWidget(self.replay_video)
        row.addWidget(preview,3);layout.addLayout(row,1)
        lower=QHBoxLayout()
        report,rl=card();rl.addWidget(label('Session measurements','section'));self.session_report=QPlainTextEdit();self.session_report.setReadOnly(True);self.session_report.setMinimumHeight(240);rl.addWidget(self.session_report);lower.addWidget(report,2)
        aoi,al=card();al.addWidget(label('Areas of interest','section'));al.addWidget(label('Optional normalized rectangles for dwell analysis. Format: name, x, y, width, height.','muted',True))
        from PySide6.QtSvgWidgets import QSvgWidget
        self.heatmap_widget=QSvgWidget();self.heatmap_widget.setMinimumHeight(140);al.addWidget(self.heatmap_widget)
        self.aoi_table=QTableWidget(0,5);self.aoi_table.setHorizontalHeaderLabels(['Name','x','y','w','h']);self.aoi_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch);al.addWidget(self.aoi_table)
        ab=QHBoxLayout();ab.addWidget(_button('Add area',self.add_aoi));ab.addWidget(_button('Recalculate',self.analyze_current_session));al.addLayout(ab);lower.addWidget(aoi,2)
        layout.addLayout(lower,1)
        return page

    def _profiles_page(self):
        page,layout=self._page('Profiles & diagnostics','Inspect calibration evidence, compatibility, acquisition health and software readiness.')
        actions=QHBoxLayout();actions.addWidget(_button('New profile…',self.new_profile_dialog));actions.addWidget(_button('Load profile…',self.load_profile_dialog));actions.addWidget(_button('Export diagnostics',self.export_diagnostics,True));actions.addStretch();layout.addLayout(actions)
        self.profile_details=QPlainTextEdit();self.profile_details.setReadOnly(True);self.profile_details.setMinimumHeight(240);layout.addWidget(self.profile_details,2)
        self.health_details=QPlainTextEdit();self.health_details.setReadOnly(True);self.health_details.setMinimumHeight(180);layout.addWidget(self.health_details,1)
        layout.addWidget(label('Event log','section'));self.event_log=QPlainTextEdit();self.event_log.setReadOnly(True);self.event_log.setMaximumBlockCount(400);self.event_log.setMinimumHeight(140);layout.addWidget(self.event_log,1)
        return page

    def _integration_page(self):
        page,layout=self._page('Integration API','Use the Python SDK directly or serve fresh gaze metadata to an existing Windows application over loopback.')
        form,fl=card();fl.addWidget(label('Local HTTP & WebSocket service','section'))
        ff=QFormLayout();self.port_spin=QSpinBox();self.port_spin.setRange(1024,65535);self.port_spin.setValue(8765);ff.addRow('Loopback port',self.port_spin)
        self.token_edit=QLineEdit();self.token_edit.setEchoMode(QLineEdit.EchoMode.Password);self.token_edit.setPlaceholderText('Automatically generated when the API starts');self.token_edit.setReadOnly(True);ff.addRow('Bearer token',self.token_edit)
        fl.addLayout(ff)
        b=QHBoxLayout();self.api_button=_button('Start local API',self.toggle_api,True);b.addWidget(self.api_button);b.addWidget(_button('Copy access token',self.copy_token));b.addStretch();fl.addLayout(b)
        self.api_status=label('API stopped. Start tracking before enabling the service.','muted',True);fl.addWidget(self.api_status);layout.addWidget(form)
        endpoints,el=card();el.addWidget(label('Freshness-aware output','section'))
        el.addWidget(label('The service invalidates coordinates during pause, disconnection, end-of-file or stale capture. It shares the running camera worker and does not open a second camera.','muted',True))
        self.api_snippet=QPlainTextEdit();self.api_snippet.setReadOnly(True);self.api_snippet.setPlainText(
            'GET /health\nGET /v1/gaze\nGET /v1/metrics\nWebSocket /v1/stream\n\n'
            'Authorization: Bearer <your local access token>\n\n'
            'In process:\n  from gazespace.engine import GazeEngine\n'
            '  engine = GazeEngine.from_profile(profile_path, mode="rgb")\n'
            '  result = engine.process(frame_bgr, captured_monotonic_s)\n'
            '  if result.valid and result.gaze_screen_px is not None:\n'
            '      existing_ui.update_gaze(*result.gaze_screen_px)\n')
        self.api_snippet.setMinimumHeight(300);el.addWidget(self.api_snippet);layout.addWidget(endpoints,1)
        return page

    def _build_menu(self):
        file_menu=self.menuBar().addMenu('File')
        for text,shortcut,handler in [('New profile','Ctrl+N',self.new_profile_dialog),('Load profile','Ctrl+O',self.load_profile_dialog),
                                      ('Open video','Ctrl+V',self.choose_video),('Export diagnostics','Ctrl+D',self.export_diagnostics)]:
            action=QAction(text,self);action.setShortcut(QKeySequence(shortcut));action.triggered.connect(handler);file_menu.addAction(action)
        file_menu.addSeparator();exit_action=QAction('Exit',self);exit_action.triggered.connect(self.close);file_menu.addAction(exit_action)
        run_menu=self.menuBar().addMenu('Tracking')
        for text,shortcut,handler in [('Start','Ctrl+Return',self.start_tracking),('Pause / resume','Ctrl+Space',self.toggle_pause),('Record / stop recording','Ctrl+R',self.toggle_record)]:
            action=QAction(text,self);action.setShortcut(QKeySequence(shortcut));action.triggered.connect(handler);run_menu.addAction(action)
        help_menu=self.menuBar().addMenu('Help')
        action=QAction('Open user guide',self);action.triggered.connect(self.open_guide);help_menu.addAction(action)

    def set_page(self,index):
        self.pages.setCurrentIndex(index)
        for i,button in enumerate(self.nav_buttons):button.setChecked(i==index)

    def _log(self,text):
        if hasattr(self,'event_log'):
            self.event_log.appendPlainText(datetime.now().strftime('%H:%M:%S')+'  '+str(text))
        self.statusBar().showMessage(str(text),15000)

    def _error(self,error):
        text=str(error);self._log('Error: '+text);self.error_reported.emit(text)
        if hasattr(self,'cal_report'):self.cal_report.setPlainText('Operation failed\n\n'+text)
        if self.interactive_errors:
            QMessageBox.warning(self,'GazeSpace',text)

    def run_job(self,name,fn,done=None):
        self._log(name+'…')
        job=FunctionJob(fn);self._jobs.append(job)
        job.signals.error.connect(self._error)
        if done:job.signals.result.connect(done)
        job.signals.finished.connect(lambda:self._finish_job(job))
        self.pool.start(job)

    def _finish_job(self,job):
        if job in self._jobs:self._jobs.remove(job)

    def _load_form(self):
        cfg=self.settings
        self.source_edit.setText(str(cfg['source']));self.backend_combo.setCurrentText(cfg['backend']);self.mode_combo.setCurrentText(cfg['mode'])
        self.width_spin.setValue(cfg['width']);self.height_spin.setValue(cfg['height']);self.fps_spin.setValue(cfg['fps'])
        self.polarity_combo.setCurrentText(cfg['pupil_polarity']);self.bits_combo.setCurrentText(str(cfg['ir_bit_depth']))
        self.subject_edit.setText(cfg['subject_id']);self.face_model_edit.setText(cfg.get('face_model',''))
        exposure=cfg.get('exposure_us') if cfg['backend'] in ('pylon','realsense') else cfg.get('exposure_native')
        self.exposure_edit.setText('' if exposure is None else str(exposure));self.gain_edit.setText('' if cfg.get('gain') is None else str(cfg['gain']))
        rois=cfg.get('roi_normalized',{})
        for side,edit in [('right',self.roi_right_edit),('left',self.roi_left_edit)]:
            edit.setText(','.join(f'{v:.6f}' for v in rois.get(side,[])))
        self.device_preview.rois=rois

    def _read_form(self):
        cfg=dict(self.settings)
        cfg.update(source=self.source_edit.text().strip(),backend=self.backend_combo.currentText(),mode=self.mode_combo.currentText(),
                   width=self.width_spin.value(),height=self.height_spin.value(),fps=self.fps_spin.value(),
                   pupil_polarity=self.polarity_combo.currentText(),ir_bit_depth=int(self.bits_combo.currentText()),
                   subject_id=self.subject_edit.text().strip() or 'operator-1',face_model=self.face_model_edit.text().strip())
        if not cfg['source']:raise ValueError('Choose a camera index, serial or video path')
        cfg['exposure_native']=cfg['exposure_us']=None
        if self.exposure_edit.text().strip():
            cfg['exposure_us' if cfg['backend'] in ('pylon','realsense') else 'exposure_native']=float(self.exposure_edit.text())
        cfg['gain']=float(self.gain_edit.text()) if self.gain_edit.text().strip() else None
        rois={}
        for side,edit in [('right',self.roi_right_edit),('left',self.roi_left_edit)]:
            if edit.text().strip():
                x,y,w,h=_parse_vector(edit.text(),4)
                if x<0 or y<0 or w<=0 or h<=0 or x+w>1.000001 or y+h>1.000001:
                    raise ValueError('Eye ROIs must fit within normalized image coordinates [0,1]')
                rois[side]=[x,y,w,h]
        cfg['roi_normalized']=rois
        if cfg['mode']=='ir-roi' and len(rois)!=2:
            raise ValueError('Select both eye ROIs or use RGB / IR face tracking')
        return cfg

    def _persist(self):
        save_json(self.settings_path,self.settings)

    def source_context(self,cfg=None):
        cfg=cfg or self.settings
        return {'camera_id':str(cfg['source']),'backend':cfg['backend'],'subject_id':cfg['subject_id'],
                'exposure_native':cfg.get('exposure_native'),'exposure_us':cfg.get('exposure_us'),
                'gain':cfg.get('gain'),'ir_bit_depth':cfg.get('ir_bit_depth'),
                **({} if cfg['backend']=='synthetic' else self.display_context())}

    def display_context(self):
        screens=QApplication.screens();monitor=screens[self.monitor_combo.currentIndex()]
        return {'monitor':monitor.name(),'screen_size':[round(monitor.geometry().width()*monitor.devicePixelRatio()),round(monitor.geometry().height()*monitor.devicePixelRatio())]}

    def apply_and_start(self):
        try:
            self.settings=self._read_form();self._persist();self.start_tracking()
        except Exception as exc:self._error(exc)

    def _build_engine(self,cfg):
        path=cfg.get('profile')
        if cfg['mode']=='preview':
            from ..preview import PreviewTracker
            return GazeEngine(PreviewTracker())
        if cfg['backend']=='synthetic' and path and Path(path).is_file():
            from ..demo import SyntheticDemoEngine
            return SyntheticDemoEngine.from_profile(path,source_context=cfg.get('runtime_context',{}))
        if path and Path(path).is_file():
            engine=GazeEngine.from_profile(path,mode=cfg['mode'],face_model=cfg.get('face_model') or None,
                        roi_normalized=cfg.get('roi_normalized') or None,pupil_polarity=cfg['pupil_polarity'],
                        source_context=cfg.get('runtime_context', {}))
        else:
            tracker=GazeTracker(mode=cfg['mode'],face_model=cfg.get('face_model') or None,
                               roi_normalized=cfg.get('roi_normalized') or None,pupil_polarity=cfg['pupil_polarity'])
            engine=GazeEngine(tracker)
        return engine

    def start_tracking(self):
        try:
            if not self.stop_tracking():
                raise RuntimeError('The previous camera driver has not finished stopping. Wait before reopening it.')
            cfg=dict(self.settings)
            cfg['runtime_context']=self.source_context(cfg)
            if cfg['mode']=='ir-roi' and len(cfg.get('roi_normalized',{}))!=2:
                raise ValueError('Set right and left eye ROIs in Cameras & tracking first')
            self._is_demo=cfg['backend']=='synthetic'
            self.runtime=TrackingRuntime(
                lambda:create_source(cfg['source'],backend=cfg['backend'],width=cfg['width'],height=cfg['height'],fps=cfg['fps'],
                                     exposure_native=cfg.get('exposure_native'),exposure_us=cfg.get('exposure_us'),
                                     gain_native=cfg.get('gain'),gain=cfg.get('gain'),ir_bit_depth=cfg.get('ir_bit_depth'),
                                     pupil_polarity=cfg.get('pupil_polarity','dark')),
                lambda:self._build_engine(cfg),on_result=self._record_result)
            self._sequence=None;self.runtime.start();self.refresh_profile()
            self._log('Starting '+('synthetic pupil simulation' if self._is_demo else f"{cfg['backend']} source {cfg['source']}"))
        except Exception as exc:self._error(exc)

    def stop_tracking(self):
        self.stop_api()
        self.stop_recording()
        if self.runtime is not None:
            stopped=self.runtime.stop(timeout=1.0)
            if not stopped:
                self._log('Waiting for camera driver shutdown; all gaze coordinates invalidated')
                return False
            self.runtime=None
        if hasattr(self,'live_badge'):self.live_badge.setText('STOPPED')
        if hasattr(self,'gaze_map'):
            self.gaze_map.set_gaze(None,False);self.screen_xy.setText('Screen coordinate: —')
            self.state_metric.set_value('—','Capture stopped');self.live_reason.setText('Capture stopped · preview shows the last frame')
            self.surface_text.setText('Surface hit: —');self.world_hit.setText('No active gaze ray')
            self.live_world.set_scene(self.live_world.surfaces);self.world_view.set_scene(self.world_view.surfaces)
            self.preview.live_state=self.device_preview.live_state='stopped'
            self.preview.set_frame(None,result={},synthetic=self._is_demo);self.device_preview.set_frame(None,result={},synthetic=self._is_demo)
        return True

    def toggle_pause(self):
        if not self.runtime:return
        if self.runtime.snapshot(False)['state']=='paused':
            self.runtime.resume();self.pause_button.setText('Pause')
        else:
            self.runtime.pause();self.pause_button.setText('Resume')
        with self.record_lock:
            if self.recorder:self.recorder.event('capture_state',{'state':self.runtime.snapshot(False)['state']})

    def open_demo(self):
        if not self.stop_tracking():return
        path=self.workspace/'profiles'/'synthetic_demo_v2.json'
        def finish(profile):
            self.settings.update(source='synthetic-v2',backend='synthetic',mode='ir-roi',width=640,height=360,fps=30,
                subject_id='synthetic-fixture',pupil_polarity='dark',profile=str(path),
                roi_normalized=dict(SyntheticEyeSource.DEFAULT_ROIS),exposure_native=None,exposure_us=None,gain=None,ir_bit_depth=8)
            self._load_form();self.refresh_profile();self.set_page(0);self.start_tracking()
            self._log('Synthetic demo running through the pupil detector and calibrated regressor; this is software evidence only')
        self.run_job('Preparing deterministic simulation',lambda:build_demo_profile(path),finish)

    def snapshot(self):
        return self.runtime.snapshot() if self.runtime else {'state':'idle','latest':None,'frame':None,'observation':None,'metrics':{}}

    def _record_result(self,frame,observation,result):
        with self.record_lock:
            if self.recorder is not None:
                self.recorder.append(result,image_bgr=frame.image)

    def toggle_record(self):
        if self.recorder is not None:
            self.stop_recording();return
        if self.runtime is None or self.snapshot()['state']!='running':
            self._error('Start a valid camera or simulation stream before recording');return
        try:
            p=self._profile_data(required=False)
            model=(p or {}).get('screen_model') or {}
            meta={'provenance':'synthetic' if self._is_demo else ('video' if self.settings['backend']=='ffmpeg' else 'live'),
                  'subject_id':self.settings['subject_id'],'camera':self.last_snapshot.get('camera',{}),
                  'profile_path':self.settings.get('profile'),'screen_size':model.get('metadata',{}).get('screen_size'),
                  'tracking_mode':self.settings['mode'],'synthetic':self._is_demo,
                  'calibration_evidence':(p or {}).get('screen_validation')}
            with self.record_lock:
                self.runtime.clear_callback_error()
                self.recorder=SessionRecorder(self.workspace/'sessions',metadata=meta,record_video=self.record_video.isChecked(),fps=self.settings['fps'])
            self.record_button.setText('Stop recording');self.record_button.setObjectName('danger')
            self.record_video.setEnabled(False);self.record_status.setText('RECORDING · '+self.recorder.path.name)
            self._log('Recording started locally'+(' with raw camera video' if self.record_video.isChecked() else ' (gaze metadata only)'))
        except Exception as exc:self._error(exc)

    def stop_recording(self,error=None):
        close_error=None
        try:
            with self.record_lock:
                recorder,self.recorder=self.recorder,None
                if recorder is not None:
                    path=recorder.close(error=error);self._log(('Partial session saved: ' if error else 'Session saved: ')+str(path));self.refresh_sessions()
        except Exception as exc:
            close_error=exc
            self._error('Recording stopped but its final files could not be saved: '+str(exc))
        finally:
            if hasattr(self,'record_button'):
                self.record_button.setText('Record session');self.record_button.setObjectName('')
                self.record_video.setEnabled(True)
                self.record_status.setText('Recording stopped · session may be incomplete' if close_error or error else 'No recording · metadata stays local')

    def poll(self):
        if self.runtime:
            snap=self.runtime.snapshot();self.last_snapshot=snap
            state=snap['state'];result=snap.get('latest') or {}
            if (snap.get('camera') or {}).get('simulated'):self._is_demo=True
            self.live_badge.setText(('SIMULATION  /  ' if self._is_demo else '')+state.upper())
            frame=snap.get('frame')
            if frame is not None:
                sequence=(getattr(frame,'source',''),frame.sequence,frame.timestamp_s)
                if sequence!=self._sequence or not result.get('valid'):
                    self._sequence=sequence
                    self.preview.set_frame(frame.image,result,snap.get('observation'),self._is_demo)
                    self.device_preview.set_frame(frame.image,result,snap.get('observation'),self._is_demo)
            reason=result.get('reason') or snap.get('last_error') or state
            self.live_reason.setText(str(reason).replace('_',' ') + ('  ·  '+', '.join(str(v).replace('_',' ') for v in result.get('warnings',[])[:2]) if result.get('warnings') else ''))
            frame_state='stale' if str(result.get('reason','')).startswith('stale_') else state
            self.preview.live_state=frame_state;self.device_preview.live_state=frame_state
            if self.pages.currentIndex()==1:
                camera_dump=json.dumps(snap.get('camera',{}),indent=2,default=str)
                if camera_dump!=getattr(self,'_camera_dump',None):self.device_report.setPlainText(camera_dump);self._camera_dump=camera_dump
            angles=result.get('yaw_pitch_roll_deg')
            self.head_pose_text.setText('Head pose estimate: '+(f'yaw {angles[0]:.1f}° · pitch {angles[1]:.1f}° · roll {angles[2]:.1f}°' if angles else 'unavailable in this mode'))
            metrics=snap.get('metrics',{})
            if metrics.get('callback_error') and self.recorder is not None:
                recording_error=metrics['callback_error'];self.stop_recording(error=recording_error)
                self._error('Recording stopped because a write failed: '+str(recording_error))
            self.quality_metric.set_value(f"{result.get('quality',0)*100:.0f}%",result.get('source','No observation'))
            fps=metrics.get('processing_fps',metrics.get('processed_fps',0)) or 0
            self.fps_metric.set_value(f'{fps:.1f}',f"{metrics.get('dropped_frames',0)} superseded frames")
            p50=metrics.get('processing_median_ms')
            p95=metrics.get('processing_p95_ms')
            if p50 is None:p50=result.get('processing_ms',0)
            self.latency_metric.set_value(f'{p50 or 0:.1f} ms',f'p95 {p95 or 0:.1f} ms · processing only')
            self.state_metric.set_value('VALID' if result.get('valid') else '—', 'Synthetic estimate' if self._is_demo else reason.replace('_',' '))
            xy=result.get('gaze_screen_normalized')
            self.gaze_map.set_gaze(xy,bool(result.get('valid')))
            self.gaze_map.caption='Synthetic calibrated screen · 1920 × 1080' if self._is_demo else 'Calibrated screen coordinates'
            pixels=result.get('gaze_screen_px');self.screen_xy.setText('Screen coordinate: '+(f'{pixels[0]:.1f}, {pixels[1]:.1f} px' if pixels else '—'))
            surfaces=self.live_world.surfaces
            self.live_world.set_scene(surfaces,result.get('ray_origin_world_m'),result.get('ray_direction_world'))
            self.world_view.set_scene(self.world_view.surfaces,result.get('ray_origin_world_m'),result.get('ray_direction_world'))
            name=result.get('surface_name');uv=result.get('surface_xy_m')
            hit=f'{name}: {uv[0]:.3f}, {uv[1]:.3f} m' if name and uv else '—'
            self.surface_text.setText('Surface hit: '+hit);self.world_hit.setText('Surface hit: '+hit)
            if state=='fault' and snap.get('last_error'):
                self.live_reason.setText('Capture stopped: '+snap['last_error'])
            if self.pages.currentIndex()==5:
                self.health_details.setPlainText(json.dumps({'state':state,'camera':snap.get('camera'),
                    'metrics':metrics,'last_error':snap.get('last_error')},indent=2,default=str))
        if self._replay_clock is not None and self.replay is not None:
            now=host_now_s();record=self.replay.advance(now-self._replay_clock);self._replay_clock=now
            self._show_replay_record(record)
            duration=self.replay.duration_s
            t=getattr(self.replay,'position_s',0)
            self.replay_slider.blockSignals(True);self.replay_slider.setValue(round(t*1000));self.replay_slider.blockSignals(False)
            if t>=duration:self.pause_replay()
        if self._api_server and self._api_server.started:
            self.api_status.setText(f'Listening at http://127.0.0.1:{self.port_spin.value()} · bearer token required · metadata only')
        elif self._api_server and self._api_thread and not self._api_thread.is_alive():
            self.stop_api()
            self.api_status.setText('API failed to start. Check that the chosen local port is available.')
            self._error('The local API stopped before startup completed. Choose a free port and retry.')

    def choose_video(self):
        path,_=QFileDialog.getOpenFileName(self,'Open a video input',str(self.workspace),'Video (*.mp4 *.avi *.mov *.mkv);;All files (*)')
        if not path:return
        self.source_edit.setText(path);self.backend_combo.setCurrentText('ffmpeg');self.set_page(1)
        self._log('Video selected. Choose the appropriate tracking mode, then Apply & start.')

    def scan_cameras(self):
        backend=self.backend_combo.currentText()
        if backend not in ('auto','dshow','msmf','v4l2'):backend='auto'
        if self.runtime is not None:
            self._error('Stop capture before device discovery so camera handles are not opened twice');return
        self.run_job('Scanning camera devices',lambda:{'uvc':list_cameras(6,backend),'basler':list_basler_cameras()},
                     lambda data:self.device_report.setPlainText(json.dumps(data,indent=2)))

    def start_preview(self):
        self.mode_combo.setCurrentText('preview');self.apply_and_start();self.set_page(1)

    def select_roi(self,side):
        if self.device_preview._image is None:
            self._error('Start an image stream first; pupil detection may remain unavailable until both ROIs are selected');return
        self.set_page(1);self.device_preview.set_selection(side)
        self._log(f'Drag a rectangle around the {side} eye in the preview')

    def roi_selected(self,side,roi):
        edit=self.roi_right_edit if side=='right' else self.roi_left_edit
        edit.setText(','.join(f'{v:.6f}' for v in roi))
        self._log(f'{side.title()} eye selected. Apply & start to use the new ROI; recalibrate gaze after ROI changes.')

    def import_rois(self):
        path,_=QFileDialog.getOpenFileName(self,'Load eye ROIs',str(self.workspace),'JSON (*.json)')
        if not path:return
        try:
            data=load_json(path)
            for side,edit in [('right',self.roi_right_edit),('left',self.roi_left_edit)]:
                edit.setText(','.join(str(v) for v in data[side]))
        except Exception as exc:self._error(exc)

    def save_rois(self):
        try:
            cfg=self._read_form();path,_=QFileDialog.getSaveFileName(self,'Save eye ROIs',str(self.workspace/'eye_rois.json'),'JSON (*.json)')
            if path:save_json(path,cfg['roi_normalized']);self._log('Eye ROIs saved')
        except Exception as exc:self._error(exc)

    def _profile_data(self,required=True):
        path=self.settings.get('profile')
        if not path or not Path(path).is_file():
            if required:raise ValueError('Create or load a calibration profile first')
            return None
        return load_profile(path)

    def new_profile_dialog(self):
        path,_=QFileDialog.getSaveFileName(self,'Create calibration profile',str(self.workspace/'profiles'/'operator_camera.json'),'JSON (*.json)')
        if not path:return
        try:
            data=new_profile(camera_id=str(self.settings['source']),subject_id=self.settings['subject_id'],context=self.source_context())
            self.stop_tracking();save_json(path,data);self.settings['profile']=path;self._persist();self.refresh_profile();self._log('New profile created; start capture to collect calibration')
        except Exception as exc:self._error(exc)

    def load_profile_dialog(self):
        path,_=QFileDialog.getOpenFileName(self,'Load calibration profile',str(self.workspace/'profiles'),'JSON (*.json)')
        if not path:return
        try:
            load_profile(path)
            self.stop_tracking();self.settings['profile']=path;self._persist();self.refresh_profile()
            self._log('Profile selected. Choose matching camera and subject before starting.')
        except Exception as exc:self._error(exc)

    def refresh_profile(self):
        path=self.settings.get('profile','')
        self.cal_profile.setText(path)
        try:
            p=self._profile_data(False)
            if not p:
                self.sidebar_profile.setText('No calibrated profile');self.cal_summary.setText('Choose New profile to begin');self.profile_details.setPlainText('No profile loaded');return
            summary={'profile':Path(path).name,'subject_id':p.get('subject_id'),'camera_id':p.get('camera_id'),
                     'intrinsics':bool(p.get('intrinsics')),'world_pose':bool(p.get('extrinsics')),
                     'screen_model':bool(p.get('screen_model')),'spatial_model':bool(p.get('spatial_model')),
                     'screen_validation':p.get('screen_validation'),'spatial_validation':p.get('spatial_validation'),
                     'drift_validation':p.get('drift_validation'),'camera_validation':p.get('camera_validation'),'context':p.get('context'),
                     'recalibration_required':p.get('recalibration_required'),
                     'synthetic':p.get('synthetic',False)}
            self.profile_details.setPlainText(json.dumps(summary,indent=2,default=str))
            self.sidebar_profile.setText(Path(path).name + ('\nSynthetic fixture' if p.get('synthetic') else '\n'+str(p.get('subject_id') or 'Unassigned subject')))
            self.cal_summary.setText('  ·  '.join(f"{k}: {'ready' if summary[k] else 'pending'}" for k in ('intrinsics','world_pose','screen_model','spatial_model')))
            self._set_surface_rows(p.get('surfaces',[]));self.world_view.set_scene(p.get('surfaces',[]));self.live_world.set_scene(p.get('surfaces',[]))
        except Exception as exc:self._log('Profile could not be loaded: '+str(exc))
        if hasattr(self,'board_directory'):
            count=len(list(self.board_directory.glob('board_*.png')))
            self.board_count.setText(f'{count} captured images · {self.board_directory}')

    def _require_capture(self):
        snap=self.snapshot()
        if snap.get('state')!='running' or snap.get('frame') is None:
            raise ValueError('Start a working camera stream first')
        return snap

    def _board_args(self):
        return self.cols_spin.value(),self.rows_spin.value(),self.square_spin.value()

    def capture_board(self):
        try:
            snap=self._require_capture();frame=snap['frame'].image
            from ..calibration import find_board
            cols,rows,_=self._board_args()
            if find_board(frame,cols,rows) is None:raise ValueError('The specified checkerboard was not found. Adjust focus, pose or corner counts.')
            self.board_directory.mkdir(parents=True,exist_ok=True)
            path=self.board_directory/f'board_{time.time_ns()}.png'
            if not cv2.imwrite(str(path),frame):raise OSError('Unable to write board image')
            self.refresh_profile();self._log('Checkerboard image captured')
        except Exception as exc:self._error(exc)

    def choose_board_directory(self):
        path=QFileDialog.getExistingDirectory(self,'Choose checkerboard image folder',str(self.workspace))
        if path:self.board_directory=Path(path);self.refresh_profile()

    def solve_intrinsics(self):
        try:
            self._profile_data();cols,rows,square=self._board_args();path=self.settings['profile'];directory=self.board_directory
            def run():
                images=[cv2.imread(str(p)) for p in sorted(directory.iterdir()) if p.suffix.lower() in ('.png','.jpg','.jpeg','.bmp','.tif')]
                if any(im is None for im in images):raise ValueError('An image in the calibration directory could not be read')
                intr,report=calibrate_intrinsics(images,cols,rows,square,min_views=10)
                from ..validation import camera_training_evidence
                evidence=camera_training_evidence(images,cols=cols,rows=rows,square_mm=square)
                update_profile(path,intrinsics=intr.to_dict(),extrinsics=None,screen_model=None,spatial_model=None,surfaces=[],camera_calibration_evidence=evidence)
                return report
            self.run_job('Solving measured camera intrinsics',run,self.calibration_done)
        except Exception as exc:self._error(exc)

    def use_factory_intrinsics(self):
        try:
            frame=self._require_capture()['frame'];self._profile_data()
            metadata=frame.metadata or {}
            raw=metadata.get('depth_intrinsics')
            if not raw:raise ValueError('The active adapter does not provide calibrated aligned color intrinsics')
            if metadata.get('depth_aligned_to')!='color' or not metadata.get('depth_deprojection_supported',False):
                raise ValueError('Supported color-aligned depth deprojection is required before importing factory intrinsics')
            intr=CameraIntrinsics.from_dict(raw)
            if intr.source!='factory':raise ValueError('Factory calibration provenance is required')
            if (intr.width,intr.height)!=(frame.image.shape[1],frame.image.shape[0]):
                raise ValueError('Factory intrinsic dimensions do not match the active color frame')
            update_profile(self.settings['profile'],intrinsics=intr.to_dict())
            self.calibration_done({'intrinsics':intr.to_dict(),'provenance':'camera SDK factory calibration'})
        except Exception as exc:self._error(exc)

    def print_board(self):
        try:
            cols,rows,square=self._board_args()
            path,_=QFileDialog.getSaveFileName(self,'Save printable board',str(self.workspace/'checkerboard.svg'),'SVG (*.svg)')
            if path:
                from ..validation import write_checkerboard_svg
                write_checkerboard_svg(path,cols,rows,square)
                self._log('Board saved. Print at 100% size and measure the physical squares before calibrating.')
        except Exception as exc:self._error(exc)

    def validate_camera_folder(self):
        try:
            p=self._profile_data();intr=CameraIntrinsics.from_dict(p['intrinsics']);cols,rows,square=self._board_args()
            directory=QFileDialog.getExistingDirectory(self,'Choose independent board images',str(self.workspace))
            if not directory:return
            path=self.settings['profile']
            def run():
                from ..validation import validate_camera_images
                images=[cv2.imread(str(v)) for v in sorted(Path(directory).iterdir()) if v.suffix.lower() in ('.png','.jpg','.jpeg','.bmp')]
                report=validate_camera_images(images,intr,cols=cols,rows=rows,square_mm=square,training_hashes=p.get('camera_calibration_evidence',{}).get('image_sha256'))
                update_profile(path,camera_validation=report)
                return report
            self.run_job('Measuring held-out reprojection error',run,self.calibration_done)
        except Exception as exc:self._error(exc)

    def capture_world(self):
        try:
            frame=self._require_capture()['frame'].image.copy();path=self.workspace/'board'/'world_reference.png'
            if not cv2.imwrite(str(path),frame):raise OSError('Cannot save world-reference frame')
            self._solve_world(path)
        except Exception as exc:self._error(exc)

    def solve_world_file(self):
        path,_=QFileDialog.getOpenFileName(self,'Choose world-reference board image',str(self.workspace),'Images (*.png *.jpg *.bmp *.tif)')
        if path:self._solve_world(path)

    def _solve_world(self,image_path):
        try:
            p=self._profile_data();intr=CameraIntrinsics.from_dict(p['intrinsics']);cols,rows,square=self._board_args();path=self.settings['profile']
            def run():
                image=cv2.imread(str(image_path))
                if image is None:raise ValueError('World board image could not be read')
                ext=solve_world_board_extrinsics(image,intr,cols,rows,square)
                update_profile(path,extrinsics=ext.to_dict(),spatial_model=None,surfaces=[])
                return {'world_pose':ext.to_dict(),'origin':'first internal corner; verify board orientation'}
            self.run_job('Solving reference board pose',run,self.calibration_done)
        except Exception as exc:self._error(exc)

    def calibration_done(self,report):
        self.cal_report.setPlainText(json.dumps(report,indent=2,default=str));self.refresh_profile()
        self._log('Calibration operation completed. Review the measurements; restart capture to apply changed profiles.')
        if self.runtime:self.start_tracking()

    @staticmethod
    def _profile_stamp(profile):
        return hashlib.sha256(json.dumps(profile,sort_keys=True,allow_nan=False).encode()).hexdigest()

    def _guard_acquisition(self,profile):
        if self.settings['mode']=='preview':
            raise ValueError('Select RGB, IR or IR-ROI tracking before calibrating gaze')
        if self.settings['backend'] in ('pylon','realsense') and str(self.settings['source']) in ('0','first'):
            raise ValueError('Use the device serial from Camera discovery before calibrating an industrial or depth camera')
        if self.settings['backend']=='ffmpeg' or Path(str(self.settings['source'])).is_file():
            raise ValueError('Guided calibration requires a live person following the shown targets. Use a live camera; prerecorded video cannot establish fixation labels.')
        if self._jobs:
            raise ValueError('Wait for the current calibration or report operation to finish before starting a target session')

    def _submit_acquisition(self,payload,path,baseline,source_context,spatial=False):
        data=dict(payload);data['_profile_path']=path;data['_baseline']=baseline;data['_source_context']=source_context
        (self.finish_spatial_session if spatial else self.finish_screen_session)(data)

    def _screen_targets(self,purpose):
        if purpose=='drift':return [('anchor_initial',(.5,.5)),('anchor_repeat',(.5,.5))]
        if purpose=='validation':return [(f'heldout-{i}',xy) for i,xy in enumerate(((.2,.22),(.5,.24),(.8,.22),(.25,.5),(.5,.5),(.75,.5),(.2,.78),(.5,.76),(.8,.78)))]
        return [(f'train-{r}-{c}',(float(x),float(y))) for r,y in enumerate(np.linspace(.18,.82,4)) for c,x in enumerate(np.linspace(.09,.91,4))]

    def start_screen_session(self,purpose):
        try:
            snap=self._require_capture();p=self._profile_data();self._guard_acquisition(p)
            if self._is_demo:raise ValueError('The simulation has an automatically measured synthetic profile. Select a real camera and subject for a personal gaze session.')
            if purpose!='calibration' and not p.get('screen_model'):raise ValueError('Calibrate screen gaze before validation or drift testing')
            screens=QApplication.screens();monitor=screens[self.monitor_combo.currentIndex()]
            size=(round(monitor.geometry().width()*monitor.devicePixelRatio()),round(monitor.geometry().height()*monitor.devicePixelRatio()))
            if purpose!='calibration':
                model=GazeModel.from_dict(p['screen_model'])
                if tuple(model.metadata['screen_size'])!=size:raise ValueError('Display pixel dimensions changed; recalibrate this monitor')
            dialog=TargetSessionDialog(self.snapshot,self._screen_targets(purpose),purpose=purpose,screen=monitor,screen_size=size)
            self._session_dialog=dialog
            session_path=self.settings['profile'];baseline=self._profile_stamp(p);context=self.source_context()
            dialog.samples_ready.connect(lambda data:self._submit_acquisition(data,session_path,baseline,context));dialog.cancelled.connect(lambda:self._log('Calibration cancelled; profile unchanged'))
            dialog.begin_fullscreen()
        except Exception as exc:self._error(exc)

    def finish_screen_session(self,payload):
        path=payload.get('_profile_path',self.settings['profile']);purpose=payload['purpose'];source_context=payload.get('_source_context') or self.source_context()
        def run():
            records=payload['records'];size=tuple(payload['screen_size']);p=load_profile(path)
            if payload.get('_baseline') and self._profile_stamp(p)!=payload['_baseline']:
                raise ValueError('Calibration profile changed during acquisition; start a new session')
            context={**payload['context'],**source_context,'monitor':payload.get('monitor','')}
            if purpose=='calibration':
                model=fit_gaze_model(records,'screen',screen_size=size,context=context,session_id=payload['session_id'])
                update_profile(path,context=context,camera_id=source_context['camera_id'],subject_id=source_context['subject_id'],screen_model=model.to_dict())
                return model.report
            model=GazeModel.from_dict(p['screen_model'])
            mismatches=model.context_mismatches(context,require_present=True)
            if mismatches:raise ValueError('Validation configuration differs from calibration: '+', '.join(mismatches))
            if payload['session_id']==model.metadata.get('session_id'):raise ValueError('Validation must use a fresh session')
            if purpose=='validation':
                from ..validation import validate_gaze_records
                report=validate_gaze_records(model,records,context=context,session_id=payload['session_id'])
                update_profile(path,screen_validation=report)
            else:
                from ..workflows import evaluate_drift
                report=evaluate_drift(records,model,size)
                report.update({'session_id':payload['session_id'],'model_fingerprint':model.fingerprint,
                               'purpose':'known-center repeated fixation; no automatic bias correction',
                               'drift_threshold_source':'software default; not a client acceptance limit',
                               'physical_accuracy_verified':False,'acceptance_status':'not_evaluated',
                               'acceptance_note':'Baseline accuracy, drift and elapsed duration require agreed limits; this measurement is not an acceptance pass.'})
                update_profile(path,drift_validation=report)
            return report
        self.run_job('Fitting gaze model' if purpose=='calibration' else 'Measuring '+purpose,run,self.calibration_done)

    def choose_targets(self):
        path,_=QFileDialog.getOpenFileName(self,'Load measured physical targets',str(self.workspace),'JSON (*.json)')
        if path:self.targets_edit.setText(path)

    def save_target_template(self):
        path,_=QFileDialog.getSaveFileName(self,'Save a target template',str(self.workspace/'spatial_targets_TEMPLATE.json'),'JSON (*.json)')
        if not path:return
        data=[{'id':f'MEASURE-{i}','xyz_m':[float(x),float(y),float(z)]} for i,(x,y,z) in enumerate(
            [(x,y,z) for z in (0,.2) for y in (.1,.3) for x in (.1,.3,.5)])]
        save_json(path,data);self._log('Template saved. Replace every coordinate with measured physical target locations.')

    def start_spatial_session(self,validate):
        try:
            self._require_capture();p=self._profile_data();self._guard_acquisition(p)
            if self._is_demo:raise ValueError('Physical spatial calibration needs measured hardware and targets')
            if not p.get('intrinsics') or not p.get('extrinsics'):raise ValueError('Complete measured intrinsic and world calibration first')
            if validate and not p.get('spatial_model'):raise ValueError('Calibrate spatial gaze before independent validation')
            from ..workflows import read_spatial_targets
            targets=read_spatial_targets(self.targets_edit.text())
            coordinates=np.array([t['xyz_m'] for t in targets]);camera=CameraExtrinsics.from_dict(p['extrinsics'])
            camera_points=(camera.rotation_world_to_camera@coordinates.T).T+camera.translation_world_to_camera_m
            if np.ptp(camera_points[:,2])<.03:raise ValueError('Place physical calibration targets at more than one camera depth, spanning at least 3 cm')
            dialog=SpatialSessionDialog(self.snapshot,targets,camera,purpose='spatial validation' if validate else 'spatial calibration',parent=self)
            self._session_dialog=dialog;session_path=self.settings['profile'];baseline=self._profile_stamp(p);context=self.source_context()
            dialog.samples_ready.connect(lambda data:self._submit_acquisition(data,session_path,baseline,context,True));dialog.show()
        except Exception as exc:self._error(exc)

    def finish_spatial_session(self,payload):
        path=payload.get('_profile_path',self.settings['profile']);source_context=payload.get('_source_context') or self.source_context()
        def run():
            records=payload['records'];p=load_profile(path);context={**payload['context'],**source_context}
            if payload.get('_baseline') and self._profile_stamp(p)!=payload['_baseline']:
                raise ValueError('Calibration profile changed during acquisition; start a new session')
            if payload['purpose']=='spatial calibration':
                model=fit_gaze_model(records,'spatial',context=context,session_id=payload['session_id'])
                update_profile(path,spatial_model=model.to_dict())
                return model.report
            model=GazeModel.from_dict(p['spatial_model'])
            mismatches=model.context_mismatches(context,require_present=True)
            if mismatches:raise ValueError('Spatial validation configuration differs: '+', '.join(mismatches))
            if payload['session_id']==model.metadata.get('session_id'):raise ValueError('Validation must use a fresh session')
            from ..validation import validate_gaze_records
            report=validate_gaze_records(model,records,context=context,session_id=payload['session_id'],
                                         extrinsics=CameraExtrinsics.from_dict(p['extrinsics']))
            update_profile(path,spatial_validation=report);return report
        self.run_job('Processing spatial calibration results',run,self.calibration_done)

    def stereo_dialog(self):
        dialog=QDialog(self);dialog.setWindowTitle('Stereo calibration from synchronized image pairs');dialog.resize(620,360)
        layout=QVBoxLayout(dialog);layout.addWidget(label('Use paired images with identical filenames. Capture with hardware synchronization when your application needs it.','muted',True))
        form=QFormLayout();edits={}
        for key,title,isdir in [('left_profile','Left camera profile',False),('right_profile','Right camera profile',False),
                                ('left_images','Left image folder',True),('right_images','Right image folder',True)]:
            row=QHBoxLayout();edit=QLineEdit();edits[key]=edit;row.addWidget(edit)
            def browse(checked=False,e=edit,d=isdir):
                p=QFileDialog.getExistingDirectory(dialog,'Select folder',str(self.workspace)) if d else QFileDialog.getOpenFileName(dialog,'Select profile',str(self.workspace),'JSON (*.json)')[0]
                if p:e.setText(p)
            row.addWidget(_button('Browse',browse));form.addRow(title,row)
        layout.addLayout(form)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);layout.addWidget(buttons)
        buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        data={k:v.text() for k,v in edits.items()};cols,rows,square=self._board_args()
        def run():
            li=CameraIntrinsics.from_dict(load_profile(data['left_profile'])['intrinsics']);ri=CameraIntrinsics.from_dict(load_profile(data['right_profile'])['intrinsics'])
            left,right=Path(data['left_images']),Path(data['right_images'])
            names=sorted({p.name for p in left.iterdir() if p.suffix.lower() in ('.png','.jpg','.bmp')} & {p.name for p in right.iterdir()})
            result=calibrate_stereo([cv2.imread(str(left/n)) for n in names],[cv2.imread(str(right/n)) for n in names],li,ri,cols,rows,square,min_pairs=8)
            output=self.workspace/'profiles'/f'stereo_{time.time_ns()}.json';save_json(output,result)
            return {'output':str(output),**result}
        self.run_job('Solving stereo camera geometry',run,self.calibration_done)

    def _set_surface_rows(self,surfaces):
        self.surfaces_table.setRowCount(0)
        for s in surfaces:
            row=self.surfaces_table.rowCount();self.surfaces_table.insertRow(row)
            values=[s['name'],','.join(map(str,s['origin_world_m'])),','.join(map(str,s['u_vector'])),','.join(map(str,s['v_vector'])),
                    str(s['width_m']),str(s['height_m']),','.join(map(str,s['pixel_size'])) if s.get('pixel_size') else '']
            for col,value in enumerate(values):self.surfaces_table.setItem(row,col,QTableWidgetItem(value))

    def surface_data(self):
        output=[];names=set()
        for row in range(self.surfaces_table.rowCount()):
            text=[self.surfaces_table.item(row,c).text().strip() if self.surfaces_table.item(row,c) else '' for c in range(7)]
            if not text[0] or text[0] in names:raise ValueError('Each surface needs a unique name')
            names.add(text[0]);pixels=None
            if text[6]:
                pp=_parse_vector(text[6],2)
                if min(pp)<2 or any(v!=int(v) for v in pp):raise ValueError('Pixel dimensions must be integers greater than 1')
                pixels=[int(v) for v in pp]
            data={'name':text[0],'origin_world_m':_parse_vector(text[1]),'u_vector':_parse_vector(text[2]),
                  'v_vector':_parse_vector(text[3]),'width_m':float(text[4]),'height_m':float(text[5]),'pixel_size':pixels}
            surf=Surface.from_dict(data)
            from ..geometry import ray_surface_intersection
            ray_surface_intersection(np.zeros(3),np.array([0.,0.,1.]),surf)
            output.append(surf.to_dict())
        return output

    def add_surface(self):
        row=self.surfaces_table.rowCount();self.surfaces_table.insertRow(row)
        values=[f'surface_{row+1}','0,0,0','1,0,0','0,1,0','0.5','0.3','1920,1080']
        for col,value in enumerate(values):self.surfaces_table.setItem(row,col,QTableWidgetItem(value))

    def remove_surface(self):
        row=self.surfaces_table.currentRow()
        if row>=0:self.surfaces_table.removeRow(row)

    def apply_surfaces(self):
        try:
            self._profile_data();surfaces=self.surface_data();update_profile(self.settings['profile'],surfaces=surfaces)
            self.refresh_profile();self._log('Workspace surfaces saved')
            if self.runtime:self.start_tracking()
        except Exception as exc:self._error(exc)

    def import_surfaces(self):
        path,_=QFileDialog.getOpenFileName(self,'Import measured surfaces',str(self.workspace),'JSON (*.json)')
        if not path:return
        try:
            data=load_json(path)
            if not isinstance(data,list):raise ValueError('Expected a JSON array of surfaces')
            for s in data:Surface.from_dict(s)
            self._set_surface_rows(data);self.world_view.set_scene(data)
        except Exception as exc:self._error(exc)

    def export_surfaces(self):
        try:
            data=self.surface_data();path,_=QFileDialog.getSaveFileName(self,'Export workspace surfaces',str(self.workspace/'surfaces.json'),'JSON (*.json)')
            if path:save_json(path,data)
        except Exception as exc:self._error(exc)

    def refresh_sessions(self):
        if not hasattr(self,'session_list'):return
        self.session_list.blockSignals(True);self.session_list.clear()
        self._session_paths=[]
        for manifest_path in sorted((self.workspace/'sessions').glob('*/manifest.json'),reverse=True):
            try:
                data=load_json(manifest_path);metadata=data.get('metadata',{})
                provenance=metadata.get('provenance','session')
                self.session_list.addItem(f"{manifest_path.parent.name}\n{provenance}")
                self._session_paths.append(manifest_path.parent)
            except (ValueError,OSError):continue
        self.session_list.blockSignals(False)

    def select_session(self):
        index=self.session_list.currentRow()
        if 0<=index<len(getattr(self,'_session_paths',[])):self.load_session_path(self._session_paths[index])

    def open_session_folder(self):
        path=QFileDialog.getExistingDirectory(self,'Open a recorded session',str(self.workspace/'sessions'))
        if path:self.load_session_path(path)

    def load_session_path(self,path):
        try:
            self.pause_replay();data=load_session(path);self.current_session=data;self.replay=SessionReplay(data.records)
            blocked=self.replay_slider.blockSignals(True)
            try:
                # Ceiling lets the integer-millisecond slider reach the final
                # sub-millisecond sample; SessionReplay clamps to exact time.
                self.replay_slider.setRange(0,max(1,math.ceil(self.replay.duration_s*1000)))
                self.replay_slider.setValue(0)
            finally:self.replay_slider.blockSignals(blocked)
            self.replay_map.trail=[]
            self.replay_map.caption='Recorded screen coordinates'+(' · synthetic fixture' if data.manifest.get('metadata',{}).get('synthetic') else '')
            self._replay_index=-1
            self.replay_video._image=None;self.replay_video.result={};self.replay_video.update()
            if self._replay_capture is not None:self._replay_capture.release();self._replay_capture=None
            videos=list(Path(path).glob('*.avi'))
            if videos:self._replay_capture=cv2.VideoCapture(str(videos[0]))
            self.analyze_current_session();self._show_replay_record(self.replay.current())
            self._log('Session loaded for offline replay')
        except Exception as exc:self._error(exc)

    def _show_replay_record(self,record):
        if not record:
            # Seeking before the first delivered frame must not show a later
            # frame or gaze trail left over from the previous replay position.
            self.replay_map.trail=[]
            self.replay_map.set_gaze(None,False)
            self.replay_caption.setText('No recorded sample at this time')
            self._replay_index=-1
            self.replay_video._image=None;self.replay_video._bgr=None
            self.replay_video.result={};self.replay_video.observation=None
            self.replay_video.update()
            return
        self.replay_map.set_gaze(record.get('gaze_screen_normalized'),record.get('valid',False))
        t=record.get('session_time_s',0);self.replay_caption.setText(f"{t:.2f}s · {record.get('reason','')} · quality {record.get('quality',0):.2f}")
        if self._replay_capture is not None:
            index=record.get('video_frame')
            if index is None:return
            index=int(index)
            if index!=self._replay_index:
                self._replay_capture.set(cv2.CAP_PROP_POS_FRAMES,index);ok,frame=self._replay_capture.read()
                if ok:
                    self.replay_video.live_state='replay'
                    self.replay_video.set_frame(frame,record,synthetic=bool(self.current_session.manifest.get('metadata',{}).get('synthetic')))
                self._replay_index=index

    def seek_replay(self,value):
        if self.replay:
            self.replay_map.trail=[]
            self._show_replay_record(self.replay.seek(value/1000))
            # A seek while playing starts a fresh elapsed interval; time spent
            # before that seek must not be added to the selected position.
            if self._replay_clock is not None:self._replay_clock=host_now_s()

    def play_replay(self):
        if not self.replay:return
        if getattr(self.replay,'position_s',0)>=self.replay.duration_s:
            self.seek_replay(0)
            self.replay_slider.setValue(0)
        self._replay_clock=host_now_s();self.replay_button.setText('Playing…')

    def pause_replay(self):
        self._replay_clock=None
        if hasattr(self,'replay_button'):self.replay_button.setText('Play')

    def add_aoi(self):
        row=self.aoi_table.rowCount();self.aoi_table.insertRow(row)
        for col,text in enumerate([f'area_{row+1}','0.25','0.25','0.5','0.5']):self.aoi_table.setItem(row,col,QTableWidgetItem(text))

    def aoi_data(self):
        result=[]
        for row in range(self.aoi_table.rowCount()):
            vals=[self.aoi_table.item(row,c).text() if self.aoi_table.item(row,c) else '' for c in range(5)]
            x,y,w,h=map(float,vals[1:])
            result.append({'name':vals[0],'rect':[x,y,w,h]})
        return result

    def analyze_current_session(self):
        if self.current_session is None:return
        try:
            metadata=self.current_session.manifest.get('metadata',{});size=metadata.get('screen_size')
            report=analyze_session(self.current_session.records,screen_size=size,aois=self.aoi_data() or None)
            self.heatmap_widget.load(QByteArray(heatmap_svg(report).encode('utf-8')))
            # Heatmaps are included in the export; keep the on-screen summary readable.
            summary={key:value for key,value in report.items() if key not in ('heatmap','fixations')}
            summary['fixation_count']=len(report.get('fixations',[]))
            self.session_report.setPlainText(json.dumps(summary,indent=2,default=str))
        except Exception as exc:self._error(exc)

    def export_session_report(self):
        if self.current_session is None:self._error('Select a recorded session first');return
        session=self.current_session;aois=self.aoi_data() or None
        output=self.workspace/'reports'/session.path.name
        def done(paths):
            self._log('Report and CSV exported to '+str(output))
            self.session_report.appendPlainText('\nExported files:\n'+json.dumps({k:str(v) for k,v in paths.items()},indent=2))
        self.run_job('Exporting offline report',lambda:export_report(session,output,aois=aois),done)

    def export_diagnostics(self):
        try:
            import platform, importlib.metadata
            deps={}
            for package in ('numpy','opencv-contrib-python','mediapipe','PySide6','pypylon','fastapi'):
                try:deps[package]=importlib.metadata.version(package)
                except importlib.metadata.PackageNotFoundError:deps[package]=None
            data={'app_version':__version__,'platform':platform.platform(),'python':platform.python_version(),
                  'dependencies':deps,'settings':self.settings,'profile_summary':self.profile_details.toPlainText(),
                  'runtime':self.runtime.snapshot(False) if self.runtime else None,
                  'hardware_accuracy_claim':'not established by software diagnostics'}
            if data['runtime']:
                data['runtime'].pop('result',None);data['runtime'].pop('frame',None);data['runtime'].pop('observation',None)
            path=self.workspace/'reports'/f'diagnostics_{time.time_ns()}.json';save_json(path,data)
            self._log('Diagnostics exported: '+str(path))
        except Exception as exc:self._error(exc)

    def toggle_api(self):
        if self._api_server:self.stop_api();return
        if self.runtime is None:self._error('Start tracking before starting the local API');return
        try:
            import secrets,uvicorn
            from ..service import create_api
            token=secrets.token_urlsafe(24);app=create_api(self.runtime,token=token,manage_lifecycle=False)
            config=uvicorn.Config(app,host='127.0.0.1',port=self.port_spin.value(),log_level='warning')
            self._api_server=uvicorn.Server(config);self._api_token=token
            self._api_thread=threading.Thread(target=self._api_server.run,daemon=True,name='gazespace-api')
            self._api_thread.start();self.token_edit.setText(token);self.api_button.setText('Stop local API')
            self.port_spin.setEnabled(False)
            self.api_status.setText('Starting loopback service…');self._log('Local API started; copy the generated token for your application')
        except Exception as exc:self._error(exc)

    def stop_api(self):
        if self._api_server:
            self._api_server.should_exit=True
            if self._api_thread:self._api_thread.join(timeout=1.0)
            self._api_server=None;self._api_thread=None;self._api_token=None
            self.token_edit.clear();self.api_button.setText('Start local API');self.api_status.setText('API stopped')
            self.port_spin.setEnabled(True)

    def copy_token(self):
        if self._api_token:QApplication.clipboard().setText(self._api_token);self._log('Local API access token copied')

    def open_guide(self):
        source_root=Path(__file__).resolve().parents[3]
        roots=[source_root,Path(sys.executable).resolve().parent,Path(sys.prefix)/'share'/'gazespace']
        candidates=[root/'docs'/'USER_GUIDE.md' for root in roots]
        candidates.extend(root/'README.md' for root in roots)
        for path in candidates:
            if path.exists():
                from PySide6.QtWidgets import QTextBrowser
                dialog=QDialog(self);dialog.setWindowTitle('GazeSpace · User guide');dialog.resize(1000,760)
                layout=QVBoxLayout(dialog);browser=QTextBrowser();browser.setOpenExternalLinks(True)
                browser.setSearchPaths([str(path.parent)]);browser.setMarkdown(path.read_text(encoding='utf-8'))
                browser.document().setBaseUrl(QUrl.fromLocalFile(str(path.parent)+os.sep))
                layout.addWidget(browser);dialog.exec();return
        self._log('Open README.md and docs/USER_GUIDE.md in the source package')

    def closeEvent(self,event):
        self.timer.stop();self.pause_replay()
        if self._session_dialog is not None:
            try:self._session_dialog.close()
            except RuntimeError:pass
        if not self.stop_tracking():
            self.timer.start();event.ignore();return
        if self._jobs:
            self._log('Waiting for calibration or report generation to finish before closing');self.timer.start();event.ignore();return
        if self._replay_capture is not None:self._replay_capture.release()
        self._persist();event.accept()
