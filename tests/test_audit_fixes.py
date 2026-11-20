"""Regression gates for audit C1-C7 and M1-M5: synthetic/mocked, not HIL acceptance."""
from __future__ import annotations

import os
import numpy as np
import cv2
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gazespace.calibration import fit_gaze_model, update_profile
from gazespace.cli import build_parser, source_context
from gazespace.depth import sample_depth_point, eye_origin_from_depth
from gazespace.geometry import ray_surface_intersection
from gazespace.pupil import detect_pupil
from gazespace.types import CameraExtrinsics, CameraIntrinsics, Surface
from gazespace.vision import GazeTracker, eye_aspect_ratio


def _sample_screen():
    samples = []
    for i, (x,y) in enumerate((x,y) for x in np.linspace(.1,.9,4) for y in np.linspace(.1,.9,4)):
        for n in range(5):
            samples.append({"features":[x,y,x,y,0,0,0,.5,.5,.7],
                            "target":[x,y],"target_id":str(i)})
    return samples


def _sample_spatial():
    samples = []
    rotation = np.eye(3)
    for i,(x,y,z) in enumerate((x,y,z) for x in (-.3,0,.3) for y in (-.2,.2) for z in (-1.,-.7)):
        vector = np.array([x,y,z],float)
        vector /= np.linalg.norm(vector)
        for n in range(5):
            samples.append({"features":[.5+x*.4,.5+y*.4,.5+x*.4,.5+y*.4,0,0,0,.5,.5,.7],
                            "target":vector.tolist(),"target_id":str(i),
                            "head_rotation":rotation.tolist()})
    return samples


def test_c1_desktop_collectors_share_validation_session_id():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from gazespace.ui.calibration_dialogs import TargetSessionDialog, SpatialSessionDialog
    app = QApplication.instance() or QApplication([])
    source = lambda: {"state":"idle","observation":None,"frame":None}
    d = TargetSessionDialog(source,[("t",(.5,.5))],screen_size=(1000,700))
    s = SpatialSessionDialog(source,[{"id":"t","xyz_m":[0,0,0]}],
                             CameraExtrinsics(np.eye(3),np.array([0.,0.,1.])))
    try:
        d.collect()
        s.collect()
        assert d.collector.session_id == d.session_id
        assert s.collector.session_id == s.session_id
    finally:
        d.close()
        s.close()
        app.processEvents()


def test_c3_inverse_brown_depth_is_deprojected_without_opencv_forward_distortion():
    intr = CameraIntrinsics(640,480,
        np.array([[600.,0,320],[0,600.,240],[0,0,1]]),
        np.array([.1,-.05,.001,-.001,0]),source="factory")
    data = np.full((480,640),.7,np.float32)
    pt = sample_depth_point(data,(350.,240.),intr,distortion_model="inverse_brown_conrady")
    assert pt is not None and pt[2] == pytest.approx(.7) and pt[0] > .03
    origin = eye_origin_from_depth(data,
        {"right":(290.,240.),"left":(350.,240.)},intr,
        distortion_model="inverse_brown_conrady")
    assert origin is not None and origin[2] == pytest.approx(.7)
    with pytest.raises(ValueError):
        sample_depth_point(data,(350,240),intr,distortion_model="unsupported")


def test_c5_cli_default_ir_bit_depth_has_same_none_semantics_as_calibration():
    args = build_parser().parse_args(["track","--source","0","--backend","dshow","--subject-id","op1"])
    ctx = source_context(args)
    assert ctx["ir_bit_depth"] is None and ctx["subject_id"] == "op1"


def test_c6_display_only_context_change_keeps_existing_spatial_model(tmp_path):
    path = tmp_path/"profile.json"
    model = fit_gaze_model(_sample_spatial(),"spatial")
    update_profile(path,context={"camera_id":"0","subject_id":"op1"},
                   camera_id="0",subject_id="op1",spatial_model=model.to_dict())
    profile = update_profile(path,context={"camera_id":"0","subject_id":"op1","monitor":"D1"})
    assert profile["spatial_model"] is not None
    assert profile["spatial_model"]["metadata"]["calibration_context_hash"]


def test_c7_roi_mode_does_not_open_unused_face_model_and_nan_eyelids_close():
    tracker = GazeTracker(mode="ir-roi", face_model="/nonexistent/unused.task",
        roi_normalized={"right":(.2,.3,.2,.2),"left":(.6,.3,.2,.2)},
        min_quality=.25)
    try:
        assert tracker.face_model_sha256 is None
    finally:
        tracker.close()
    lm = {i:(100.,100.) for i in (33,160,158,133,153,144)}
    lm[160] = (float("nan"),100.)
    assert eye_aspect_ratio(lm,"right") == 0.0


def test_m2_spatial_prediction_rotates_with_current_head_frame():
    model = fit_gaze_model(_sample_spatial(),"spatial")
    assert model.metadata["spatial_frame"] == "head"
    features = np.array([.5,.5,.5,.5,0,0,0,.5,.5,.7])
    yaw = np.deg2rad(15)
    rot = np.array([[np.cos(yaw),0,np.sin(yaw)],[0,1,0],[-np.sin(yaw),0,np.cos(yaw)]])
    before = model.predict_camera_direction(features, np.eye(3))[0]
    after = model.predict_camera_direction(features, rot)[0]
    assert np.allclose(after, rot @ before, atol=1e-6)


def test_m3_front_surface_accepts_ray_from_viewer_side():
    surface=Surface("screen",(0,0,1),(1,0,0),(0,1,0),1,1,two_sided=False)
    assert ray_surface_intersection([.5,.5,0],[0,0,1],surface) is not None
    assert ray_surface_intersection([.5,.5,2],[0,0,-1],surface) is None


def test_m5_uneven_illumination_and_internal_glint_returns_finite_pupil():
    img=np.linspace(110,210,180,dtype=np.uint8)[None,:].repeat(120,axis=0)
    cv2.circle(img,(90,60),13,18,-1,cv2.LINE_AA)
    cv2.circle(img,(86,56),2,252,-1)
    pupil=detect_pupil(img,(10,10,160,100),min_radius=4,max_radius=25)
    assert pupil is not None and np.hypot(pupil.xy[0]-90,pupil.xy[1]-60)<2.5
    assert pupil.glint_xy is not None
