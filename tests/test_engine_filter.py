import numpy as np
import pytest
from gazespace.types import Observation, CameraExtrinsics,Surface
from gazespace.engine import GazeEngine
from gazespace.calibration import fit_gaze_model
from gazespace.filtering import OneEuro

class FakeTracker:
    def __init__(self,ob):self.ob=ob
    def process(self,_,time):self.ob.timestamp_s=time;return self.ob
    def close(self):pass

def samples():
    items=[]
    for x in np.linspace(.1,.9,4):
        for y in np.linspace(.1,.9,4):
            for k in range(6):
                items.append({"features":[x,y,x,y,0,0,0,.5,.5,.7],"target":[x,y],"target_id":f"{x}-{y}"})
    return items

def test_engine_uncalibrated_returns_invalid():
    ob=Observation(1,(640,480),features=np.zeros(10),quality=.85,eyes_open=True)
    eng=GazeEngine(FakeTracker(ob))
    res=eng.process(np.zeros((480,640,3),np.uint8),timestamp_s=1)
    assert not res.valid and res.reason=="tracking_only_uncalibrated"
    assert eng.metrics()["frames"]==1

def test_engine_screen_prediction():
    model=fit_gaze_model(samples(),screen_size=(640,480))
    ob=Observation(1,(640,480),features=np.array([.5,.5,.5,.5,0,0,0,.5,.5,.7]),quality=.85,eyes_open=True)
    eng=GazeEngine(FakeTracker(ob),screen_model=model)
    res=eng.process(np.zeros((480,640,3),np.uint8),timestamp_s=1)
    assert res.valid
    assert res.gaze_screen_px==pytest.approx((320,240),abs=6)
    assert eng.metrics()["processing_median_ms"]>=0

def test_blink_fails_closed_reset_filter():
    model=fit_gaze_model(samples(),screen_size=(640,480))
    ob=Observation(1,(640,480),features=np.zeros(10),quality=0,blink=True,message="blink")
    eng=GazeEngine(FakeTracker(ob),screen_model=model)
    res=eng.process_observation(ob)
    assert not res.valid
    assert eng.metrics()["blink_frames"]==1


def test_one_euro_reject_nonfinite_and_resets_after_gap():
    f=OneEuro()
    assert f.update(np.array([1.,2.]),1).tolist()==[1,2]
    assert np.all(f.update(np.array([2.,3.]),1.05)>np.array([1,2]))
    assert f.update(np.array([5.,6.]),2).tolist()==[5,6]
    with pytest.raises(ValueError):f.update([float('nan'),2],2.1)


def test_one_euro_reject_invalid_cutoff():
    with pytest.raises(ValueError):OneEuro(min_cutoff=0)


def test_spatial_ray_surface_hit_with_dummy_model():
    class DummyModel:
        def predict(self,x):return np.array([[0.,0.,1.]])
    ob=Observation(1,(640,480),features=np.zeros(10),quality=.9,eyes_open=True,
                   eye_origin_camera_m=np.array([.2,.15,0.]))
    surf=Surface("desk",(0,0,1),(1,0,0),(0,1,0),.6,.3,(1920,1080))
    eng=GazeEngine(FakeTracker(ob),spatial_model=DummyModel(),
                   extrinsics=CameraExtrinsics(np.eye(3),np.zeros(3)),surfaces=[surf])
    res=eng.process_observation(ob)
    assert res.valid and res.surface_name=="desk"
    assert res.surface_xy_px==pytest.approx((1919/3,1079/2))


def test_spatial_missing_head_pose_not_fabricated():
    class DummyModel:
        def predict(self,x):return np.array([[0.,0.,1.]])
    ob=Observation(1,(640,480),features=np.zeros(10),quality=.9,eyes_open=True)
    eng=GazeEngine(FakeTracker(ob),spatial_model=DummyModel(),
                   extrinsics=CameraExtrinsics(np.eye(3),np.zeros(3)))
    res=eng.process_observation(ob)
    assert not res.valid
    assert "world_geometry_unavailable_for_this_frame" in res.warnings
