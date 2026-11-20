"""Regression tests for unsafe v1 behaviors, independent of physical-camera claims."""
from copy import deepcopy
import json

import cv2
import numpy as np
import pytest

from gazespace.calibration import (GazeModel, fit_gaze_model, load_json, load_profile,
                                  new_profile, save_json, update_profile)
from gazespace.engine import GazeEngine
from gazespace.filtering import OneEuro
from gazespace.geometry import (FACE_INDICES, FACE_TEMPLATE_M, approximate_intrinsics,
                               estimate_head_pose, ray_surface_intersection)
from gazespace.types import CameraExtrinsics, CameraIntrinsics, Frame, Observation, Surface
from gazespace.vision import GazeTracker, eye_rel_pos, observation_from_landmarks
from gazespace.pupil import detect_pupil


def calibration_rows():
    rows = []
    for i, (x, y) in enumerate((x, y) for x in np.linspace(.1, .9, 4) for y in np.linspace(.1, .9, 4)):
        for _ in range(8):
            rows.append({"features": [x, y, x, y, 0, 0, 0, .5, .5, .7],
                         "target": [x, y], "target_id": str(i)})
    return rows


def make_model(**context):
    return fit_gaze_model(calibration_rows(), screen_size=(640, 480), context=context)


class InjectedTracker:
    def __init__(self, observation):
        self.observation = observation

    def process(self, frame, timestamp):
        result = deepcopy(self.observation)
        result.timestamp_s = timestamp
        return result

    def close(self):
        pass


def center_observation(timestamp=1):
    return Observation(timestamp, (640, 480), eyes_open=True, quality=.9,
                       features=np.array([.5, .5, .5, .5, 0, 0, 0, .5, .5, .7]))


def face_landmarks(blink=False):
    intr = approximate_intrinsics(640, 480)
    xy, _ = cv2.projectPoints(FACE_TEMPLATE_M, np.zeros(3), np.array([0., 0., .65]),
                              intr.camera_matrix, intr.distortion)
    landmarks = {index: tuple(point) for index, point in zip(FACE_INDICES, xy.reshape(-1, 2))}
    # Preserve PnP outer corners and place realistic inner corners between them.
    landmarks[133] = (landmarks[33][0]+25, landmarks[33][1])
    landmarks[362] = (landmarks[263][0]-25, landmarks[263][1])
    aperture = .5 if blink else 6
    for iris, left, right, upper, lower, contour in (
        (468, 33, 133, 159, 145, (33, 160, 158, 133, 153, 144)),
        (473, 362, 263, 386, 374, (362, 385, 387, 263, 373, 380)),
    ):
        a, b = np.array(landmarks[left]), np.array(landmarks[right])
        midpoint = (a+b)/2
        landmarks[iris] = tuple(midpoint)
        landmarks[upper] = tuple(midpoint+[0, -aperture])
        landmarks[lower] = tuple(midpoint+[0, aperture])
        for j, fraction in ((1,.33), (2,.67), (4,.67), (5,.33)):
            landmarks[contour[j]] = tuple(a*(1-fraction)+b*fraction + [0, -aperture if j < 3 else aperture])
    return landmarks


def test_realistic_frontal_landmark_layout_has_neutral_pitch():
    # These manually placed image coordinates are not generated from the template.
    points = {1:(640,360), 152:(640,450), 33:(580,315), 263:(700,315),
              61:(600,400), 291:(680,400)}
    pose = estimate_head_pose(points, approximate_intrinsics(1280, 720))
    assert pose is not None
    assert abs(pose.pitch_deg) < 15
    assert abs(pose.yaw_deg) < 1
    assert abs(pose.roll_deg) < 1


def test_mirrored_eye_landmarks_do_not_emit_false_pose():
    points = face_landmarks()
    points[33], points[263] = points[263], points[33]
    assert estimate_head_pose(points, approximate_intrinsics(640, 480)) is None


def test_vertical_iris_feature_does_not_expand_when_lids_close():
    open_eye = eye_rel_pos((52,22), (20,20), (80,20), (50,10), (50,30))
    narrow_eye = eye_rel_pos((52,22), (20,20), (80,20), (50,18), (50,22))
    assert open_eye == pytest.approx(narrow_eye)


def test_ir_blink_is_not_bypassed_by_roi_fallback():
    class Detector:
        def detect(self, *args, **kwargs):
            return face_landmarks(blink=True)
        def close(self):
            pass
    tracker = GazeTracker(mode="ir", detector=Detector(),
                          roi_normalized={"right":(.2,.3,.2,.3), "left":(.6,.3,.2,.3)})
    image = np.full((480,640,3),180,np.uint8)
    cv2.circle(image,(190,210),18,(10,10,10),-1)
    cv2.circle(image,(450,210),18,(10,10,10),-1)
    observation = tracker.process(image, 1)
    assert observation.blink and observation.features is None
    assert observation.message == "blink_or_occluded_eye"


def test_ir_missing_actual_pupil_does_not_substitute_rgb_iris():
    observation = observation_from_landmarks(face_landmarks(), (640,480),1,
                                              ir_image=np.full((480,640,3),180,np.uint8))
    assert observation.features is None
    assert observation.message.startswith("ir_pupil_unavailable")


def test_nested_dark_pupil_inside_bright_sclera_is_detected():
    eye = np.full((120,180),20,np.uint8)
    cv2.ellipse(eye,(90,60),(65,35),0,0,360,190,-1)
    cv2.circle(eye,(94,60),12,15,-1)
    pupil = detect_pupil(eye,(5,5,170,110),"dark",3,25)
    assert pupil is not None
    assert pupil.xy == pytest.approx((94,60),abs=1)


@pytest.mark.parametrize("failure", ["duplicate_targets", "collinear_targets", "constant_eye_features", "empty", "nonfinite"])
def test_unobservable_calibration_is_rejected(failure):
    rows = calibration_rows()
    if failure == "duplicate_targets":
        for row in rows: row["target"] = [.5,.5]
    elif failure == "collinear_targets":
        for row in rows: row["target"][1] = row["target"][0]
    elif failure == "constant_eye_features":
        for row in rows: row["features"] = [.5,.5,.5,.5,0,0,0,.5,.5,.7]
    elif failure == "empty":
        rows = []
    elif failure == "nonfinite":
        rows[0]["features"][0] = float("nan")
    with pytest.raises(ValueError):
        fit_gaze_model(rows, screen_size=(640,480))


def test_mixed_tracking_context_during_calibration_is_rejected():
    rows = calibration_rows()
    for row in rows: row["context"] = {"tracking_mode":"rgb"}
    rows[-1]["context"] = {"tracking_mode":"ir-roi"}
    with pytest.raises(ValueError, match="context"):
        fit_gaze_model(rows, screen_size=(640,480))


def test_model_rejects_wrong_coefficient_shape_and_zero_scale():
    original = make_model().to_dict()
    malformed = deepcopy(original)
    malformed["coeff"] = [[0,0]]
    with pytest.raises(ValueError, match="coefficient"):
        GazeModel.from_dict(malformed)
    malformed = deepcopy(original)
    malformed["feature_scale"][0] = 0
    with pytest.raises(ValueError, match="normalization"):
        GazeModel.from_dict(malformed)


def test_legacy_profile_cannot_reuse_v1_gaze_weights(tmp_path):
    legacy = {"schema_version":1,"screen_model":{"legacy":"weights"},"screen_validation":{"passed":True},
              "spatial_model":None,"intrinsics":None,"extrinsics":None,"surfaces":[]}
    path = tmp_path/"legacy.json"
    save_json(path,legacy)
    loaded = load_profile(path)
    assert loaded["schema_version"] == 2
    assert loaded["screen_model"] is None
    assert "screen_validation" not in loaded
    assert loaded["migration"]["recalibration_required"]


def test_camera_change_invalidates_fit_and_stale_success_report(tmp_path):
    path = tmp_path/"profile.json"
    update_profile(path,camera_id="one",screen_model=make_model().to_dict())
    update_profile(path,screen_validation={"passed":True})
    changed = update_profile(path,camera_id="two")
    assert changed["screen_model"] is None
    assert "screen_validation" not in changed


def test_camera_identity_change_cannot_reuse_another_lens_intrinsics(tmp_path):
    path = tmp_path/"profile.json"
    intr = approximate_intrinsics(640,480)
    intr.source = "measured"
    update_profile(path,camera_id="first",intrinsics=intr.to_dict())
    changed = update_profile(path,camera_id="second")
    assert changed["intrinsics"] is None
    assert changed["extrinsics"] is None


def test_intrinsic_changes_invalidate_holdout_evidence_unless_replaced(tmp_path):
    path = tmp_path/"profile.json"
    intr = approximate_intrinsics(640,480)
    intr.source = "measured"
    first = update_profile(path,intrinsics=intr.to_dict(),camera_calibration_evidence={"training_image_sha256":["first"]},
                           camera_validation={"passed":True})
    assert first["camera_validation"]["passed"]
    intr.camera_matrix[0,0] += 10
    changed = update_profile(path,intrinsics=intr.to_dict(),camera_calibration_evidence={"training_image_sha256":["second"]})
    assert "camera_validation" not in changed
    assert changed["camera_calibration_evidence"]["training_image_sha256"] == ["second"]


def test_new_context_and_new_fit_can_be_installed_atomically(tmp_path):
    path = tmp_path/"profile.json"
    profile = update_profile(path,camera_id="new-camera",context={"tracking_mode":"ir-roi"},
                             screen_model=make_model(tracking_mode="ir-roi").to_dict())
    assert profile["screen_model"] is not None
    assert load_profile(path)["screen_model"]["metadata"]["calibration_context_hash"]


def test_manual_profile_geometry_edit_is_rejected(tmp_path):
    path = tmp_path/"profile.json"
    update_profile(path,camera_id="correct",screen_model=make_model().to_dict())
    edited = load_json(path)
    edited["camera_id"] = "different"
    save_json(path,edited)
    with pytest.raises(ValueError,match="context changed"):
        load_profile(path)


def test_nonfinite_json_profile_is_rejected(tmp_path):
    path = tmp_path/"nan.json"
    path.write_text('{"schema_version":2,"value":NaN}')
    with pytest.raises(ValueError,match="nonfinite"):
        load_profile(path)


def test_operating_envelope_prevents_free_head_extrapolation():
    ob = center_observation()
    ob.features[4] = 1.2  # 54 degrees, far outside stationary training head poses
    engine = GazeEngine(InjectedTracker(ob),screen_model=make_model())
    result = engine.process_observation(ob)
    assert not result.valid
    assert result.reason == "outside_calibrated_operating_envelope"
    assert result.gaze_screen_px is None


def test_image_resolution_change_invalidates_gaze():
    ob = center_observation()
    ob.image_size = (1280,720)
    engine = GazeEngine(InjectedTracker(ob),screen_model=make_model(image_size=[640,480]))
    result = engine.process_observation(ob)
    assert not result.valid
    assert result.reason == "calibration_context_mismatch"


def test_same_display_with_changed_pixel_dimensions_invalidates_mapping():
    model = make_model(monitor="same-monitor")
    assert model.context_mismatches({"monitor":"same-monitor", "screen_size":[1280,960]}) == ["screen_size"]
    assert model.context_mismatches({"monitor":"same-monitor", "screen_size":[640,480]}) == []
    assert model.context_mismatches({"monitor":"same-monitor"}) == []  # headless caller


def test_nan_quality_and_feature_never_reach_json():
    ob = center_observation()
    ob.features[0] = float("nan")
    ob.quality = float("nan")
    result = GazeEngine(InjectedTracker(ob),screen_model=make_model()).process_observation(ob)
    assert not result.valid
    json.dumps(result.as_dict(),allow_nan=False)


def test_duplicate_timestamp_does_not_reuse_a_valid_coordinate():
    ob = center_observation()
    engine = GazeEngine(InjectedTracker(ob),screen_model=make_model())
    assert engine.process_observation(ob).valid
    repeated = engine.process_observation(ob)
    assert not repeated.valid and repeated.gaze_screen_px is None
    assert "timestamp" in repeated.reason
    assert engine.invalidate("camera_disconnected").gaze_screen_px is None


def test_closed_eye_observation_cannot_emit_a_stale_feature_prediction():
    ob = center_observation()
    ob.eyes_open = False
    result = GazeEngine(InjectedTracker(ob),screen_model=make_model()).process_observation(ob)
    assert not result.valid and result.gaze_screen_px is None


def test_one_euro_derivative_tracks_raw_motion_not_remaining_filter_error():
    filter_ = OneEuro(beta=.5)
    filter_.update([0.],0)
    filter_.update([1.],.1)
    initial_derivative = filter_.dx.copy()
    filter_.update([1.],.2)
    assert filter_.dx[0] < initial_derivative[0]


@pytest.mark.parametrize("direction", [(0,0,0),(0,float("nan"),1),(0,float("inf"),1)])
def test_invalid_ray_directions_are_rejected(direction):
    surface = Surface("screen",(0,0,1),(1,0,0),(0,1,0),1,1)
    with pytest.raises(ValueError):
        ray_surface_intersection(np.zeros(3),direction,surface)


def test_surface_boundary_is_last_pixel_and_single_sided_is_explicit():
    surface = Surface("screen",(0,0,1),(1,0,0),(0,1,0),1,1,(640,480))
    hit = ray_surface_intersection([1,1,0],[0,0,1],surface)
    assert hit["xy_px"] == (639.,479.)
    surface.two_sided = False
    assert ray_surface_intersection([.5,.5,0],[0,0,1],surface) is not None
    assert ray_surface_intersection([.5,.5,2],[0,0,-1],surface) is None


def test_skew_surface_and_nonrotation_extrinsic_are_rejected():
    with pytest.raises(ValueError,match="orthogonal"):
        Surface("bad",(0,0,1),(1,0,0),(1,1,0),1,1).validate()
    with pytest.raises(ValueError,match="orthonormal"):
        CameraExtrinsics(np.eye(3)*2,np.zeros(3)).validate()


def test_aligned_depth_sets_measured_surface_origin_and_semantic_binding():
    ob = center_observation()
    ob.pupils_px = {"right":(295.,240.),"left":(345.,240.)}
    intr = CameraIntrinsics(640,480,np.array([[500.,0,320],[0,500.,240],[0,0,1]]),np.zeros(5),source="factory")
    frame = Frame(np.full((480,640,3),128,np.uint8),1,
                  metadata={"depth_aligned_to":"color","depth_intrinsics":intr.to_dict()},
                  depth_m=np.full((480,640),.6,np.float32))
    engine = GazeEngine(InjectedTracker(ob))
    engine.process_frame(frame)
    measured = engine.last_observation
    assert measured.eye_origin_camera_m == pytest.approx([0,0,.6])
    assert measured.eye_origin_source == "aligned_depth_pupil_midpoint"
    assert measured.context["eye_origin_source"] == measured.eye_origin_source
    assert "optical_center" in measured.warnings[-1]


def test_invalid_depth_never_silently_reuses_generic_origin():
    ob = center_observation()
    ob.pupils_px = {"right":(295.,240.),"left":(345.,240.)}
    ob.eye_origin_camera_m = np.array([0,0,.6])
    ob.eye_origin_source = "generic_face_pnp"
    intr = CameraIntrinsics(640,480,np.array([[500.,0,320],[0,500.,240],[0,0,1]]),np.zeros(5),source="factory")
    frame = Frame(np.full((480,640,3),128,np.uint8),1,
                  metadata={"depth_aligned_to":"color","depth_intrinsics":intr.to_dict()},
                  depth_m=np.zeros((480,640),np.float32))
    engine = GazeEngine(InjectedTracker(ob))
    engine.process_frame(frame)
    assert engine.last_observation.eye_origin_camera_m is None
    assert engine.last_observation.eye_origin_source == "depth_unavailable"
