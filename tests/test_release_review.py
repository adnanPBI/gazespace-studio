"""Focused regression review for the final desktop integration fixes."""
from __future__ import annotations

import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import numpy as np
import pytest

pytest.importorskip("PySide6", reason="native desktop tests need the desktop extra")
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from gazespace.calibration import GazeModel, new_profile, save_json, load_profile, update_profile
from gazespace.capture import SyntheticEyeSource
from gazespace.sessions import load_session
from gazespace.types import CameraIntrinsics, Frame, GazeResult
from gazespace.ui.main_window import MainWindow


def pump(app, predicate, timeout=10):
    deadline = time.monotonic()+timeout
    while time.monotonic()<deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("release review state transition timed out")


@pytest.fixture
def studio(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(workspace=tmp_path, interactive_errors=False)
    errors = []
    window.error_reported.connect(errors.append)
    window.show()
    app.processEvents()
    try:
        yield app,window,errors
    finally:
        window.stop_tracking()
        window.close()
        app.processEvents()
        window.pool.waitForDone(5000)


def preview(studio):
    app,window,errors = studio
    window.settings.update(source="synthetic-v2",backend="synthetic",mode="ir-roi",
                           width=640,height=360,roi_normalized={},profile="")
    window._load_form()
    window.start_preview()
    pump(app,lambda: window.runtime is not None and window.snapshot()["state"]=="running"
         and window.device_preview._image is not None)
    return window


def test_preview_bootstraps_real_roi_drag_without_face_model(studio,monkeypatch):
    import gazespace.vision
    def forbidden_face_model(*args,**kwargs):
        raise AssertionError("preview/IR-ROI must not load a facial model")
    monkeypatch.setattr(gazespace.vision,"FaceLandmarkDetector",forbidden_face_model)
    app,window,errors=studio
    preview(studio)
    assert window.settings["mode"]=="preview"
    assert not window.snapshot()["latest"]["valid"]
    pump(app,lambda: window.device_preview.display_rect.width()>0)
    canvas=window.device_preview
    for side,box in SyntheticEyeSource.DEFAULT_ROIS.items():
        window.select_roi(side)
        app.processEvents()
        rect=canvas.display_rect
        x,y,w,h=box
        start=QPoint(round(rect.x()+x*rect.width()),round(rect.y()+y*rect.height()))
        end=QPoint(round(rect.x()+(x+w)*rect.width()),round(rect.y()+(y+h)*rect.height()))
        QTest.mousePress(canvas,Qt.MouseButton.LeftButton,pos=start)
        QTest.mouseMove(canvas,end)
        QTest.mouseRelease(canvas,Qt.MouseButton.LeftButton,pos=end)
        app.processEvents()
        edit=window.roi_right_edit if side=="right" else window.roi_left_edit
        assert len(edit.text().split(","))==4
    window.mode_combo.setCurrentText("ir-roi")
    window.apply_and_start()
    pump(app,lambda: window.snapshot()["state"]=="running"
         and len((window.snapshot().get("latest") or {}).get("pupils_px",{}))==2)
    assert not window.snapshot()["latest"]["valid"]  # no fitted user model
    assert not errors,errors


def factory_frame(*,supported=True,aligned="color",size=(640,480)):
    intr=CameraIntrinsics(640,480,np.array([[500.,0,320],[0,500.,240],[0,0,1]]),
                          np.zeros(5),source="factory")
    return Frame(np.full((size[1],size[0],3),128,np.uint8),1,
                 metadata={"depth_intrinsics":intr.to_dict(),"depth_aligned_to":aligned,
                           "depth_deprojection_supported":supported})


def test_factory_intrinsics_import_invalidates_prior_gaze_fit(studio,monkeypatch):
    _,window,errors=studio
    path=window.workspace/"profiles"/"factory.json"
    model=GazeModel("screen",np.zeros((16,2)),np.zeros(15),np.ones(15),1,{},
                    {"screen_size":[640,480]})
    update_profile(path,camera_id="0",screen_model=model.to_dict(),screen_validation={"old":True})
    window.settings["profile"]=str(path)
    monkeypatch.setattr(window,"snapshot",lambda:{"state":"running","frame":factory_frame()})
    window.use_factory_intrinsics()
    loaded=load_profile(path)
    assert loaded["intrinsics"]["source"]=="factory"
    assert loaded["screen_model"] is None
    assert "screen_validation" not in loaded
    assert not errors,errors


@pytest.mark.parametrize("frame",[
    factory_frame(supported=False),factory_frame(aligned="infrared"),factory_frame(size=(320,240))
])
def test_unsupported_or_mismatched_factory_geometry_is_not_imported(studio,monkeypatch,frame):
    _,window,errors=studio
    path=window.workspace/"profiles"/"factory.json"
    save_json(path,new_profile())
    before=path.read_bytes()
    window.settings["profile"]=str(path)
    monkeypatch.setattr(window,"snapshot",lambda:{"state":"running","frame":frame})
    window.use_factory_intrinsics()
    assert errors,"Invalid depth alignment/model/resolution must be reported"
    assert path.read_bytes()==before


def test_write_error_stops_recording_visibly_and_recovery_can_record(studio):
    app,window,errors=studio
    preview(studio)
    window.toggle_record()
    assert window.recorder is not None
    failed_recorder=window.recorder
    failed_path=Path(failed_recorder.path)
    def disk_full(*args,**kwargs):
        raise OSError("simulated disk full")
    failed_recorder.append=disk_full
    pump(app,lambda: window.recorder is None)
    assert any("write failed" in e and "disk full" in e for e in errors)
    assert window.runtime.snapshot(False)["state"]=="running"
    assert "RECORDING" not in window.record_status.text()
    failed_session=load_session(failed_path)
    assert failed_session.manifest["status"]=="failed"
    assert "disk full" in failed_session.manifest["error"]
    # A fresh recorder is writable; the previous callback failure must not be
    # mistaken for a new failure on the next recording attempt.
    errors.clear()
    window.toggle_record()
    assert window.recorder is not None
    recovered_path=Path(window.recorder.path)
    count=window.snapshot()["metrics"]["processed_frames"]
    pump(app,lambda: window.snapshot()["metrics"]["processed_frames"]>=count+5)
    assert window.recorder is not None,"A latched error must not abort a fresh healthy recorder"
    window.stop_recording()
    assert len(load_session(recovered_path).records)>=3
    assert not errors,errors


def test_failed_recording_close_still_resets_recording_controls(studio):
    app,window,errors=studio
    preview(studio)
    window.toggle_record()
    recorder=window.recorder
    assert recorder is not None
    close=recorder.close
    def failed_final_flush(error=None):
        # Release the real recorder's resources, then simulate the final
        # manifest write failing on the same full volume as a frame write.
        close(error=error)
        raise OSError("simulated final manifest flush failure")
    recorder.close=failed_final_flush
    window.stop_recording()
    assert window.recorder is None
    assert window.record_button.text()=="Record session"
    assert window.record_video.isEnabled()
    assert "RECORDING" not in window.record_status.text()
    assert any("manifest flush failure" in error for error in errors)


class SnapshotRuntime:
    def __init__(self):
        self.frame=Frame(np.full((360,640,3),100,np.uint8),1,1,"fixture")
        self.result=GazeResult(1,True,"screen_gaze_estimated","fixture",.8,
                               gaze_screen_normalized=(.3,.6),gaze_screen_px=(576.,648.),
                               screen_valid=True,ray_origin_world_m=(0,0,0),ray_direction_world=(0,0,1))
    def snapshot(self,*args,**kwargs):
        return {"state":"running","latest":self.result.as_dict(),"frame":self.frame,
                "observation":None,"metrics":{},"camera":{},"last_error":None}
    def stop(self,timeout=None):
        return True


def test_stale_and_stopped_ui_clear_coordinates_and_stop_claiming_live_frame(studio):
    _,window,errors=studio
    runtime=SnapshotRuntime()
    window.runtime=runtime
    window.poll()
    assert window.gaze_map.position==(.3,.6)
    runtime.result=GazeResult(1,False,"stale_capture","fixture",0)
    window.poll()
    assert window.gaze_map.position is None
    assert window.live_world.direction is None
    assert window.screen_xy.text()=="Screen coordinate: —"
    assert window.preview.live_state!="running","A stale frame cannot retain a LIVE FRAME badge"
    window.stop_tracking()
    assert window.preview.live_state=="stopped"
    assert window.preview.result=={}
    assert window.gaze_map.position is None
    assert window.world_view.direction is None
    assert not errors,errors


def test_changed_profile_during_acquisition_cannot_save_new_fit(studio):
    app,window,errors=studio
    path=window.workspace/"profiles"/"guarded.json"
    save_json(path,new_profile(camera_id="0"))
    window.settings["profile"]=str(path)
    baseline=window._profile_stamp(load_profile(path))
    update_profile(path,notes="Changed by another calibration operation")
    before=path.read_bytes()
    payload={"purpose":"calibration","records":[],"screen_size":(640,480),"context":{},
             "session_id":"new-session","monitor":"fixture","_profile_path":str(path),
             "_baseline":baseline,"_source_context":window.source_context()}
    window.finish_screen_session(payload)
    pump(app,lambda: not window._jobs)
    assert any("profile changed during acquisition" in error for error in errors)
    assert path.read_bytes()==before
