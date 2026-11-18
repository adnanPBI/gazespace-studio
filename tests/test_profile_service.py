import numpy as np
import pytest
from gazespace.calibration import (new_profile, update_profile, fit_gaze_model,
                                  save_json)
from gazespace.types import CameraIntrinsics
from gazespace.engine import GazeEngine
from gazespace.cli import build_parser,verify_camera_profile_source
from gazespace.service import GazeService, create_api


def make_sample_model(mode='ir-roi'):
    samples=[]
    for i,x in enumerate(np.linspace(.1,.9,4)):
        for j,y in enumerate(np.linspace(.1,.9,4)):
            for _ in range(5):
                samples.append({'features':[x,y,x,y,0,0,0,.5,.5,0],
                                'target':[x,y],'target_id':f'{i}-{j}'})
    model=fit_gaze_model(samples,'screen',screen_size=(640,480))
    model.metadata['tracking_mode']=mode
    return model


def test_profile_camera_id_guard(tmp_path):
    path=tmp_path/'profile.json'
    data=new_profile(camera_id='camera-abc')
    save_json(path,data)
    args=build_parser().parse_args(['track','--profile',str(path),'--source','camera-def'])
    with pytest.raises(ValueError,match='bound to camera'):
        verify_camera_profile_source(args)


def test_profile_rejects_tracking_model_mismatch(tmp_path):
    path=tmp_path/'profile.json'
    prof=new_profile(camera_id='0')
    prof['screen_model']=make_sample_model().to_dict()
    save_json(path,prof)
    rois={'right':(.2,.2,.2,.2),'left':(.6,.2,.2,.2)}
    with pytest.raises(ValueError,match='different tracking mode'):
        GazeEngine.from_profile(path,mode='rgb')
    engine=GazeEngine.from_profile(path,mode='ir-roi',roi_normalized=rois)
    assert engine.screen_model is not None
    engine.close()


def test_profile_rejects_spatial_without_calibrated_intrinsics(tmp_path):
    path=tmp_path/'profile.json'
    prof=new_profile()
    spatial=make_sample_model()
    spatial.kind='spatial'
    spatial.coeff = np.column_stack([spatial.coeff, np.r_[1., np.zeros(15)]])
    prof['spatial_model']=spatial.to_dict()
    save_json(path,prof)
    rois={'right':(.2,.2,.2,.2),'left':(.6,.2,.2,.2)}
    with pytest.raises(ValueError,match='requires measured'):
        GazeEngine.from_profile(path,mode='ir-roi',roi_normalized=rois)


def test_api_routes_and_no_remote_exposure():
    class DummyEngine:
        def metrics(self):return {'frames':0}
        def close(self):pass
    service=GazeService(DummyEngine(),lambda:None)
    app=create_api(service)
    paths={r.path for r in app.routes}
    assert '/health' in paths
    assert '/v1/gaze' in paths
    assert '/v1/metrics' in paths
    assert '/v1/stream' in paths
