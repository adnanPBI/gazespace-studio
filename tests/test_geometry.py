import math
import numpy as np
import cv2
import pytest
from gazespace.types import CameraExtrinsics, Surface
from gazespace.geometry import (approximate_intrinsics, FACE_TEMPLATE_M, FACE_INDICES,
    estimate_head_pose, eye_origin_from_pose, camera_to_world_point, world_to_camera_point,
    camera_to_world_direction, ray_surface_intersection, first_surface_hit, angular_error_deg)

@pytest.fixture
def surf():
    return Surface("screen",(0,0,1),(1,0,0),(0,1,0),0.6,0.3,(1920,1080))

def test_approximate_intrinsics_marked_unsafe():
    obj=approximate_intrinsics(1280,720)
    assert obj.source=="approximate"
    assert obj.camera_matrix[0,2]==640

@pytest.mark.parametrize("xy",[(0,0),(.15,.10),(.6,.3)])
def test_plane_hit(surf,xy):
    o=np.array([xy[0],xy[1],0])
    hit=ray_surface_intersection(o,np.array([0,0,1]),surf)
    assert np.allclose(hit["xy_m"],xy,atol=1e-8)
    assert hit["xy_px"] == pytest.approx((xy[0]/.6*1919,xy[1]/.3*1079))

@pytest.mark.parametrize("direction",[(0,0,-1),(1,0,0),(0,0,0.001)])
def test_no_plane_hit_when_behind_or_parallel(surf,direction):
    o=np.array([.8,.4,0])
    assert ray_surface_intersection(o,np.array(direction),surf) is None

def test_surface_hit_behind_origin(surf):
    assert ray_surface_intersection(np.zeros(3),np.array([0,0,-1]),surf) is None

def test_surface_must_not_be_parallel():
    s=Surface("bad",(0,0,1),(1,0,0),(2,0,0),1,1)
    with pytest.raises(ValueError,match="parallel"):
        ray_surface_intersection(np.zeros(3),np.array([0,0,1]),s)

def test_surface_minimum_ray_hit():
    near=Surface("near",(0,0,1),(1,0,0),(0,1,0),1,1)
    far=Surface("far",(0,0,2),(1,0,0),(0,1,0),1,1)
    hit=first_surface_hit(np.array([.5,.5,0]),np.array([0,0,1]),[far,near])
    assert hit[0].name=="near"

def test_world_camera_round_trip():
    r,_=cv2.Rodrigues(np.array([.1,-.2,.05]))
    e=CameraExtrinsics(r,np.array([.02,.1,.7]))
    p=np.array([.3,-.1,.4]);pc=world_to_camera_point(p,e)
    assert np.allclose(camera_to_world_point(pc,e),p,atol=1e-10)
    assert np.linalg.norm(camera_to_world_direction([0,1,0],e))==pytest.approx(1)

def test_synthetic_head_pose_projection():
    intr=approximate_intrinsics(1280,720)
    rvec=np.array([[.04],[-.07],[.02]])
    tvec=np.array([[0],[0],[.7]])
    img,_=cv2.projectPoints(FACE_TEMPLATE_M,rvec,tvec,intr.camera_matrix,intr.distortion)
    points={idx:tuple(map(float,px)) for idx,px in zip(FACE_INDICES,img.reshape(-1,2))}
    pose=estimate_head_pose(points,intr)
    assert pose is not None
    assert pose.reprojection_px<.0001
    assert np.allclose(pose.translation_m,tvec.ravel(),atol=.001)
    assert .6<eye_origin_from_pose(pose)[2]<.8

def test_head_pose_missing_points():
    assert estimate_head_pose({1:(0,0)},approximate_intrinsics(640,480)) is None

@pytest.mark.parametrize("a,b,angle",[((1,0,0),(1,0,0),0),((1,0,0),(0,1,0),90),((1,0,0),(-1,0,0),180)])
def test_angular_distance(a,b,angle):
    assert angular_error_deg(a,b)==pytest.approx(angle)
