from copy import deepcopy
import numpy as np
import pytest

from gazespace.acquisition import TargetCollector
from gazespace.types import Observation, HeadPose, CameraExtrinsics


def observation(stamp, *, features=None, quality=.9, blink=False, mode='ir-roi'):
    return Observation(stamp, (640, 480), features=np.asarray(features if features is not None else [.5] * 10),
                       quality=quality, blink=blink, eyes_open=not blink,
                       pupil_method='ir_manual_roi', context={'tracking_mode':mode})


def test_counts_unique_frames_and_requires_real_dwell_duration():
    collector = TargetCollector('center', (.5, .5), min_samples=4, min_duration_s=.6)
    collector.start(0)
    for t in np.arange(0, .6, .05):
        assert not collector.add(observation(float(t)))
    assert collector.add(observation(.6))
    for _ in range(20):
        assert not collector.add(observation(.6))
    for t in (.65, .7, .75):
        collector.add(observation(t))
    assert len(collector.observations) == 4
    assert not collector.complete
    for t in (.8, .9, 1., 1.1, 1.2):
        collector.add(observation(t))
    assert collector.complete
    assert collector.progress()['fraction'] == 1
    assert collector.progress()['stable_duration_s'] >= .59


def test_blink_discards_partial_target_and_restabilizes():
    collector = TargetCollector('center', (.5, .5), min_samples=4, min_duration_s=.3)
    collector.start(0)
    for t in np.arange(0, .85, .05):
        collector.add(observation(float(t)))
    assert collector.observations
    assert not collector.add(observation(.85, blink=True))
    assert len(collector.observations) == 0
    assert collector.reason == 'blink_or_closed_eyes'
    for t in np.arange(.9, 1.9, .05):
        collector.add(observation(float(t)))
    assert collector.complete
    assert collector.observations[0].timestamp_s >= 1.45 - .001


def test_capture_gap_does_not_count_as_hold_duration():
    collector = TargetCollector('center', (.5, .5), min_samples=4, min_duration_s=.5)
    collector.start()
    for t in np.arange(0, .9, .05):
        collector.add(observation(float(t)))
    assert not collector.add(observation(2.0))
    assert collector.reason == 'capture_gap'
    assert collector.observations == []
    assert not collector.complete


def test_motion_resets_fixation_and_context_change_aborts():
    collector = TargetCollector('center', (.5,.5), min_samples=16, max_feature_std=.05)
    collector.start(0)
    for t in np.arange(0, .75, .05):
        collector.add(observation(float(t)))
    collector.add(observation(.75, features=[1.] * 10))
    assert collector.reason == 'unstable_fixation_or_head_motion'
    assert collector.observations == []
    collector.add(observation(.8, mode='rgb'))
    assert collector.state == 'incompatible'
    collector.retry()
    assert collector.state == 'settling'


def test_timeout_and_cancel_are_explicit_terminal_states():
    collector = TargetCollector('center', (.5,.5), max_seconds=2)
    collector.start(0)
    for t in np.arange(0, 2.2, .1):
        collector.add(observation(float(t), quality=.1))
    assert collector.state == 'timeout'
    assert collector.observations == []
    collector.retry()
    collector.cancel()
    assert collector.state == 'cancelled'
    assert not collector.add(observation(3.0))


def test_collector_copies_arrays_and_emits_calibration_records():
    collector = TargetCollector('center', (.5,.5), min_samples=4, min_duration_s=.2)
    collector.start(0)
    ob = observation(0)
    for t in np.arange(0, 1., .05):
        ob.timestamp_s = float(t)
        collector.add(ob)
    assert collector.complete
    ob.features[:] = 9
    assert collector.observations[0].features[0] == .5
    records = collector.records(screen_size=(640,480))
    assert len(records) >= 4
    assert records[0]['target_id'] == 'center'
    assert records[0]['session_id'] == collector.session_id
    assert records[0]['target'] == pytest.approx([.5,.5],abs=.002)


def test_spatial_observation_requires_an_eye_origin():
    collector = TargetCollector('point-A', (.2,.3,.4), 'spatial', min_samples=4, min_duration_s=.2)
    collector.start(0)
    collector.add(observation(0))
    assert collector.reason == 'spatial_eye_origin_unavailable'
    pose = HeadPose(np.eye(3), np.array([0,0,.6]), 0,0,0,.1)
    for t in np.arange(.05, 1.1, .05):
        ob = observation(float(t))
        ob.head_pose = pose
        ob.eye_origin_camera_m = np.array([0.,0.,.6])
        collector.add(ob)
    assert collector.complete
    records=collector.records(extrinsics=CameraExtrinsics(np.eye(3), np.array([0.,0.,1.])))
    assert np.linalg.norm(records[0]['target']) == pytest.approx(1.)


@pytest.mark.parametrize('kwargs', [dict(settle_seconds=.1),dict(min_samples=2),dict(max_seconds=.5),dict(max_feature_std=-1)])
def test_invalid_collection_parameters(kwargs):
    with pytest.raises(ValueError):
        TargetCollector('c',(.5,.5),**kwargs)


def test_calibrated_aligned_depth_eye_origin_does_not_require_generic_face_pnp():
    collector=TargetCollector('depth-A',(.3,.1,1.),'spatial',min_samples=4,min_duration_s=.2)
    collector.start(0)
    for t in np.arange(0,1.,.05):
        ob=observation(float(t))
        ob.eye_origin_camera_m=np.array([0.,0.,.6])
        ob.eye_origin_source='aligned_depth_pupil_midpoint'
        collector.add(ob)
    assert collector.complete
    assert collector.observations[0].head_pose is None
