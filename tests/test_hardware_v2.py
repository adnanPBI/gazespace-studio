from __future__ import annotations
import sys
from types import SimpleNamespace
import cv2
import numpy as np
import pytest

from gazespace.capture import SyntheticEyeSource, OpenCVSource, PylonSource, to_bgr
from gazespace.types import CameraIntrinsics, Frame
from gazespace.stereo import FrameSynchronizer, triangulate_points_checked
from gazespace.depth import sample_depth_point, eye_origin_from_depth


def intrinsics():
    return CameraIntrinsics(640,480,np.array([[600,0,320],[0,600,240],[0,0,1.]],float),np.zeros(5))


def test_synthetic_renderer_is_deterministic_and_marked():
    source=SyntheticEyeSource(realtime=False)
    assert np.array_equal(source.render_target(.2,.8),source.render_target(.2,.8))
    frame=source.read()
    assert frame.metadata["synthetic"] and frame.metadata["simulated"]
    assert source.capabilities()["synthetic"]
    source.set_target(.1,.9)
    assert source.read().metadata["target_normalized"]==[.1,.9]
    source.close()
    assert source.read() is None


def test_video_model_time_uses_video_rate(tmp_path):
    path=str(tmp_path/"timing.avi")
    writer=cv2.VideoWriter(path,cv2.VideoWriter_fourcc(*"MJPG"),20,(160,120))
    if not writer.isOpened(): pytest.skip("MJPG codec not present")
    for _ in range(4):writer.write(np.zeros((120,160,3),np.uint8))
    writer.release()
    with OpenCVSource(path,realtime=False) as source:
        frames=[source.read() for _ in range(4)]
        assert frames[1].timestamp_s-frames[0].timestamp_s==pytest.approx(.05,abs=1e-6)
        assert source.read() is None and source.eof
        assert not source.is_live
        assert frames[2].metadata["video_pts_s"]==pytest.approx(.1)


def test_pylon_copies_before_sdk_release_and_reports_controls(monkeypatch):
    backing=np.full((20,30),123,np.uint8)
    class Node:
        def __init__(self,value): self.Value=value
    class Result:
        PixelType=1
        TimeStamp=9876
        Array=backing
        def GrabSucceeded(self):return True
        def Release(self):backing[:]=0
    class Camera:
        ExposureAuto=Node("Continuous");ExposureTime=Node(1200);GainAuto=Node("Continuous");Gain=Node(2)
        def Open(self):self.open=True
        def IsOpen(self):return self.open
        def Close(self):self.open=False
        def StartGrabbing(self,*_):self.grab=True
        def IsGrabbing(self):return self.grab
        def StopGrabbing(self):self.grab=False
        def RetrieveResult(self,*_):return Result()
    camera=Camera()
    device=SimpleNamespace(GetSerialNumber=lambda:"123",GetModelName=lambda:"Fake sensor")
    factory=SimpleNamespace(EnumerateDevices=lambda:[device],CreateDevice=lambda d: d)
    pylon=SimpleNamespace(TlFactory=SimpleNamespace(GetInstance=lambda:factory),
                           InstantCamera=lambda d:camera,ImageFormatConverter=lambda:SimpleNamespace(),
                           PixelType_BGR8packed=3,PixelType_Mono8=1,GrabStrategy_LatestImageOnly=1,
                           TimeoutHandling_ThrowException=1)
    monkeypatch.setitem(sys.modules,"pypylon",SimpleNamespace(pylon=pylon))
    with PylonSource(serial="123",exposure_us=2500,gain=0) as source:
        frame=source.read()
        assert frame.image[0,0,0]==123 and backing[0,0]==0
        assert source.controls["ExposureAuto"]["actual"]=="Off"
        assert source.controls["ExposureTime"]["applied"]
        assert source.capabilities()["camera_id"]=="basler:123"
        assert not source.capabilities()["global_shutter_verified"]
    assert not camera.open


def test_depth_deprojection_holes_and_surface_midpoint():
    intr=intrinsics()
    depth=np.full((480,640),.6,np.float32)
    point=sample_depth_point(depth,(320,240),intr)
    assert point==pytest.approx([0,0,.6])
    origin=eye_origin_from_depth(depth,{"left":(290,240),"right":(350,240)},intr)
    assert origin==pytest.approx([0,0,.6])
    depth[237:244,317:324]=0
    assert sample_depth_point(depth,(320,240),intr) is None
    assert sample_depth_point(depth,(-20,240),intr) is None
    with pytest.raises(ValueError):sample_depth_point(np.zeros((20,20)),(10,10),intr)


def test_frame_pairing_drops_bad_skew_and_requires_real_clock_sync():
    matcher=FrameSynchronizer(max_skew_s=.01,max_queue=2)
    im=np.zeros((2,2,3),np.uint8)
    assert matcher.add("left",Frame(im,1.0)) is None
    assert matcher.add("right",Frame(im,1.1)) is None
    pair=matcher.add("left",Frame(im,1.105))
    assert pair.skew_s==pytest.approx(.005)
    assert pair.timestamp_basis=="host_timestamps_only"
    assert matcher.dropped==1
    hardware=FrameSynchronizer(use_hardware_clock=True)
    with pytest.raises(ValueError,match="synchronized"):
        hardware.add("left",Frame(im,1.0))


def test_stereo_reconstruction_rejects_epipolar_mismatch():
    intr=intrinsics()
    R=np.eye(3);T=np.array([-.06,0,0])
    points=np.array([[0,0,.6],[.04,.03,.8]],float)
    left,_=cv2.projectPoints(points,np.zeros(3),np.zeros(3),intr.camera_matrix,intr.distortion)
    right,_=cv2.projectPoints(points,np.zeros(3),T,intr.camera_matrix,intr.distortion)
    result=triangulate_points_checked(left.reshape(-1,2),right.reshape(-1,2),intr,intr,R,T)
    assert result.valid_mask.all()
    assert np.allclose(result.points_camera_m,points,atol=1e-6)
    bad=right.reshape(-1,2).copy();bad[:,1]+=30
    invalid=triangulate_points_checked(left.reshape(-1,2),bad,intr,intr,R,T)
    assert not invalid.valid_mask.any()
    assert invalid.to_dict()["points_camera_m"]==[None,None]
    with pytest.raises(ValueError,match="baseline"):
        triangulate_points_checked(left.reshape(-1,2),right.reshape(-1,2),intr,intr,R,np.zeros(3))


@pytest.mark.parametrize('size',[(640,360),(1280,720)])
@pytest.mark.parametrize('polarity',['dark','bright'])
def test_synthetic_calibration_grid_is_visible_to_real_detector(size,polarity):
    from gazespace.vision import GazeTracker
    source=SyntheticEyeSource(width=size[0],height=size[1],polarity=polarity,realtime=False)
    tracker=GazeTracker(mode='ir-roi',roi_normalized=source.roi_normalized,pupil_polarity=polarity)
    try:
        sequence=0
        for y in np.linspace(.06,.94,7):
            for x in np.linspace(.06,.94,7):
                sequence+=1
                ob=tracker.process(source.render_target(float(x),float(y)),sequence/30)
                assert ob.features is not None, (x,y,ob.message)
                assert ob.quality>.35, (x,y,ob.quality)
                assert set(ob.pupils_px)=={'left','right'}
    finally:
        tracker.close();source.close()
