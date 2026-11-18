import json
from pathlib import Path
import numpy as np
import cv2
import pytest
from gazespace.calibration import (feature_basis,GazeModel,fit_gaze_model,screen_record,
    spatial_record,new_profile,load_profile,update_profile,save_json,load_json,
    chessboard_points,solve_world_board_extrinsics,calibrate_stereo)
from gazespace.geometry import approximate_intrinsics,world_to_camera_point
from gazespace.types import CameraIntrinsics,CameraExtrinsics,Observation
from gazespace.workflows import grid_targets,offset_validation_targets,read_spatial_targets


def generate_samples(outliers=False):
    rng=np.random.default_rng(7)
    records=[]
    for i,(x,y) in enumerate((x,y) for x in np.linspace(.08,.92,4) for y in np.linspace(.08,.92,4)):
        for k in range(14):
            features=np.array([x,y,x,y,0,0,0,.5,.5,.72])+np.r_[rng.normal(0,.006,4),np.zeros(6)]
            target=[x,y]
            if outliers and k==0: target=[1-x,1-y]
            records.append({"features":features.tolist(),"target":target,"target_id":str(i)})
    return records


def test_feature_basis_dimensions():
    x=np.zeros((4,10))
    assert feature_basis(x).shape==(4,15)
    with pytest.raises(ValueError):feature_basis(np.ones((1,9)))


def test_screen_calibration_fit_and_roundtrip():
    samples=generate_samples()
    model=fit_gaze_model(samples,kind="screen",screen_size=(1920,1080))
    assert model.report["training"]["median"]<30
    assert model.report["leave_target_out"]["p95"]<90
    assert model.report["validation_status"].startswith("UNVERIFIED")
    model2=GazeModel.from_dict(json.loads(json.dumps(model.to_dict())))
    assert np.allclose(model.predict(np.zeros(10)),model2.predict(np.zeros(10)))


def test_robust_ridge_handles_outlier():
    model=fit_gaze_model(generate_samples(outliers=True),screen_size=(1920,1080))
    p=model.predict(np.array([.5,.5,.5,.5,0,0,0,.5,.5,.72]))[0]
    assert np.linalg.norm(p-[.5,.5])<.09

@pytest.mark.parametrize("error",["few_targets","few_samples"])
def test_calibration_rejects_insufficient_targets(error):
    samples=generate_samples()
    if error=="few_targets":samples=[r for r in samples if int(r["target_id"])<5]
    else:samples=[r for r in samples if r["target_id"]!="2" or r is samples[28]]
    with pytest.raises(ValueError):fit_gaze_model(samples,screen_size=(1280,720))


def test_spatial_direction_fit():
    samples=[]
    for i,(yaw,pitch) in enumerate((x,y) for x in np.linspace(-.4,.4,4) for y in np.linspace(-.3,.3,3)):
        for k in range(10):
            features=[yaw,pitch,yaw,pitch,0,0,0,.5,.5,.7]
            direction=np.array([yaw,pitch,1.])
            direction/=np.linalg.norm(direction)
            samples.append({"features":features,"target":direction.tolist(),"target_id":str(i)})
    model=fit_gaze_model(samples,"spatial")
    assert model.report["training"]["median"]<4
    assert np.linalg.norm(model.predict(np.zeros(10))[0])==pytest.approx(1)


def test_spatial_record_targets_camera_frame():
    intr=approximate_intrinsics(640,480)
    ext=CameraExtrinsics(np.eye(3),np.array([0,0,.8]))
    ob=Observation(1,(640,480),features=np.zeros(10),quality=.8,eye_origin_camera_m=np.array([0,0,.6]))
    rec=spatial_record(ob,"A",(0,0,0),ext)
    assert rec["target"]==pytest.approx([0,0,1])


def test_screen_record_normalized():
    ob=Observation(1,(640,480),features=np.zeros(10),quality=.9)
    rec=screen_record(ob,"center",(399.5,299.5),(800,600))
    assert rec["target"]==[.5,.5]


def test_profile_atomic_storage(tmp_path):
    f=tmp_path/"x"/"profile.json"
    save_json(f,new_profile())
    assert load_profile(f)["schema_version"]==2
    update_profile(f,camera_id="123")
    assert load_profile(f)["camera_id"]=="123"
    with pytest.raises(ValueError):update_profile(f,unknown="no")
    d=load_json(f);d["schema_version"]=99;save_json(f,d)
    with pytest.raises(ValueError):load_profile(f)


def test_chessboard_points_units():
    pts=chessboard_points(9,6,24)
    assert pts.shape==(54,3)
    assert pts[-1,:2]==pytest.approx([.192,.120])
    with pytest.raises(ValueError):chessboard_points(2,5,24)


def test_extrinsics_reject_approximate():
    with pytest.raises(ValueError,match="measured"):
        solve_world_board_extrinsics(np.zeros((720,1280,3),np.uint8),approximate_intrinsics(1280,720))


def test_stereo_mismatched_pair_count():
    intr=CameraIntrinsics(640,480,np.eye(3),np.zeros((5,1)))
    with pytest.raises(ValueError,match="paired"):
        calibrate_stereo([np.zeros((480,640,3),np.uint8)],[],intr,intr)


def test_screen_grid_and_validation_points():
    pts=grid_targets((1000,800),4)
    assert len(pts)==16
    assert len({i for i,_ in pts})==16
    assert len(offset_validation_targets((1000,800)))==9
    with pytest.raises(ValueError):grid_targets((1000,800),2)


def test_spatial_target_requirements(tmp_path):
    path=tmp_path/'bad.json'
    path.write_text(json.dumps([{"id":"1","xyz_m":[1,2,3]}]))
    with pytest.raises(ValueError):read_spatial_targets(str(path))
    path.write_text(json.dumps([{"id":str(i),"xyz_m":[float(i),.2,.3]} for i in range(10)]))
    with pytest.raises(ValueError,match="collinear"):
        read_spatial_targets(str(path))
    grid = [{"id":str(i),"xyz_m":[x,y,z]} for i,(x,y,z) in enumerate(
        (x,y,z) for z in (0,.2) for y in (.1,.3) for x in (.1,.3,.5))]
    path.write_text(json.dumps(grid))
    assert len(read_spatial_targets(str(path)))==12
