import json
import numpy as np
import pytest

from gazespace.types import Frame, Observation, CameraExtrinsics
from gazespace.vision import GazeTracker
from gazespace.calibration import load_profile
from gazespace.workflows import (ScreenSession, read_spatial_targets, _check_spatial_geometry,
                                 evaluate_drift)


class Source:
    def __init__(self,source='synthetic'):
        self.source=source
        self.closed=False
    def capabilities(self):
        return {'source':self.source,'backend':'synthetic','width':640,'height':480}
    def close(self): self.closed=True


class Tracker:
    def __init__(self):
        self.real=GazeTracker(mode='ir-roi',roi_normalized={'right':(.1,.2,.2,.2),'left':(.6,.2,.2,.2)})
        self.target=(.5,.5)
        self.closed=False
        self.blink=False
    def context(self,image_size=None):return self.real.context(image_size)
    def process(self,frame,t):
        x,y=self.target
        return Observation(t,(640,480),features=np.array([x,y,x,y,0,0,0,.5,.5,0]),
                           quality=.9,blink=self.blink,eyes_open=not self.blink,
                           pupil_method='ir_manual_roi',context=self.context((640,480)))
    def close(self): self.closed=True


def complete_session(session,tracker,t=0.):
    image=np.full((480,640,3),127,np.uint8)
    for _,point in session.targets:
        tracker.target=tuple(np.asarray(point)/(np.asarray(session.size)-1))
        session.start_target()
        first_t=t
        assert session.collecting
        for _ in range(100):
            t+=1/30
            progress=session.consume_frame(Frame(image,t))
            if not session.collecting:break
        assert progress['state']=='complete'
        assert t-first_t>=1.1
    assert session.completed
    return t


def test_headless_screen_workflow_calibrates_validates_and_preserves_ownership(tmp_path):
    path=tmp_path/'profile.json'
    tracker,source=Tracker(),Source()
    session=ScreenSession(source,tracker,str(path),samples_per_target=4,grid=3,camera_id='synthetic')
    session.prepare((1001,601))
    t=complete_session(session,tracker)
    report=session.finish()
    profile=load_profile(path)
    assert profile['schema_version']==2
    assert profile['screen_model']['metadata']['image_size']==[640,480]
    assert profile['screen_model']['metadata']['session_id']==session.session_id
    assert report['target_count']==9
    assert not tracker.closed and not source.closed
    validate=ScreenSession(source,tracker,str(path),samples_per_target=4,validate=True,camera_id='synthetic')
    validate.prepare((1001,601))
    complete_session(validate,tracker,t+1)
    report=validate.finish()
    assert report['independent_session'] is True
    assert report['session_id']!=session.session_id
    assert report['screen_error']['count']>=9*4
    saved=load_profile(path)
    assert saved['screen_validation']['model_fingerprint']==report['model_fingerprint']
    assert not tracker.closed and not source.closed


def test_screen_workflow_does_not_accumulate_partial_target_on_blink(tmp_path):
    tracker,source=Tracker(),Source()
    session=ScreenSession(source,tracker,str(tmp_path/'p.json'),samples_per_target=16,grid=3)
    session.prepare((1001,601))
    session.start_target()
    image=np.zeros((480,640,3),np.uint8)
    for i in range(28):session.consume_frame(Frame(image,i/30))
    assert session.accepted>0 and session.samples==[]
    tracker.blink=True
    session.consume_frame(Frame(image,28/30))
    assert session.accepted==0 and session.samples==[]
    session.abort()
    assert not tracker.closed and not source.closed


def test_validation_context_rejects_a_different_camera_before_collecting(tmp_path):
    path=tmp_path/'p.json'
    tracker,source=Tracker(),Source()
    session=ScreenSession(source,tracker,str(path),samples_per_target=4,grid=3,camera_id='synthetic')
    session.prepare((1001,601))
    complete_session(session,tracker)
    session.finish()
    other=ScreenSession(Source('other'),tracker,str(path),validate=True,camera_id='other')
    with pytest.raises(ValueError,match='camera_id'):
        other.prepare((1001,601))


def test_spatial_targets_reject_duplicate_and_collinear_geometry(tmp_path):
    file=tmp_path/'targets.json'
    file.write_text(json.dumps([{'id':str(i),'xyz_m':[i*.1,.3,.5]} for i in range(9)]))
    with pytest.raises(ValueError,match='collinear'):read_spatial_targets(str(file))
    targets=[{'id':f'{x}-{y}','xyz_m':[x*.1,y*.1,0]} for x in range(3) for y in range(3)]
    targets[-1]['xyz_m']=targets[0]['xyz_m']
    file.write_text(json.dumps(targets))
    with pytest.raises(ValueError,match='distinct'):read_spatial_targets(str(file))


def test_depth_coverage_checked_in_camera_frame_and_nonplanar_volume():
    # World Z has ample depth, but the camera looks along world Y: the actual
    # depth spread below is only 1 mm and is rejected despite world-Z variation.
    R=np.array([[1,0,0],[0,0,-1],[0,1,0]],float)
    ext=CameraExtrinsics(R,np.array([0,0,1.]))
    targets=[{'id':f'{x}-{y}-{z}','xyz_m':[x*.2,y*.001,z*.2]}
             for x in range(3) for y in range(2) for z in range(2)]
    with pytest.raises(ValueError,match='CAMERA'):_check_spatial_geometry(targets,ext,True)
    for target in targets:target['xyz_m'][1]*=100
    report=_check_spatial_geometry(targets,ext,True)
    assert report['camera_depth_span_m']==pytest.approx(.1)
    assert report['volumetric_target_coverage'] is True
    planar=[{'id':f'{x}-{y}','xyz_m':[x*.2,y*.2,0]} for x in range(3) for y in range(3)]
    with pytest.raises(ValueError,match='nonplanar'):_check_spatial_geometry(planar,ext,True)
    assert _check_spatial_geometry(planar,ext,False)['volumetric_target_coverage'] is False


def test_legacy_drift_report_uses_actual_radial_rms_and_pixel_extent():
    class Model:
        def predict(self,features):return np.asarray(features)[:,:2]
    samples=[]
    offsets=[(.01,0),(0,.01),(-.01,0),(0,-.01)]
    for group,time in [('anchor_initial',1),('anchor_repeat',10)]:
        for i,(x,y) in enumerate(offsets):
            samples.append({'target_id':group,'features':[.5+x,.5+y]+[0]*8,'time_s':time+i*.1})
    report=evaluate_drift(samples,Model(),(1001,1001))
    assert report['first_jitter_rms_px']==pytest.approx(10.)
    assert report['first_jitter_std_px']==pytest.approx(10.)
    assert report['first_anchor_error_px']==pytest.approx(0)
    assert report['drift_px']==pytest.approx(0)


def test_spatial_cli_workflow_collects_complete_targets_then_validates(tmp_path,monkeypatch):
    from gazespace.workflows import spatial_session
    from gazespace.types import HeadPose,CameraIntrinsics
    from gazespace.calibration import new_profile,save_json
    targets=[{'id':f'{x}-{y}-{z}','xyz_m':[x,y,z]}
             for x in (.1,.3,.5) for y in (.1,.4) for z in (0.,.2)]
    target_path=tmp_path/'targets.json'
    target_path.write_text(json.dumps(targets))
    profile_path=tmp_path/'spatial.json'
    intr=CameraIntrinsics(640,480,np.array([[640.,0,320],[0,640,240],[0,0,1]]),np.zeros((5,1)))
    ext=CameraExtrinsics(np.eye(3),np.array([0.,0.,1.]))
    save_json(profile_path,new_profile(intr,ext,camera_id='synthetic'))
    class SpatialTracker(Tracker):
        def process(self,frame,t):
            ob=super().process(frame,t)
            ob.head_pose=HeadPose(np.eye(3),np.array([.01,-.02,.6]),0,0,0,.1)
            ob.eye_origin_camera_m=np.array([.01,-.02,.6])
            ob.eye_origin_source='generic_face_pnp'
            return ob
    tracker=SpatialTracker()
    class SpatialSource(Source):
        def __init__(self):
            super().__init__()
            self.stamp=0.
            self.image=np.full((480,640,3),120,np.uint8)
        def read(self):
            self.stamp+=1/30
            return Frame(self.image,self.stamp)
    source=SpatialSource()
    pointer=[0]
    def ready(_):
        target=targets[pointer[0] % len(targets)]['xyz_m']
        tracker.target=(.3+target[0],.3+target[1])
        pointer[0]+=1
        return ''
    monkeypatch.setattr('builtins.input',ready)
    report=spatial_session(source,tracker,str(profile_path),str(target_path),samples_per_target=4)
    assert report['target_count']==12
    assert report['target_geometry']['volumetric_target_coverage'] is True
    saved=load_profile(profile_path)
    assert saved['spatial_model']['metadata']['eye_origin_source']=='generic_face_pnp'
    # The deterministic tracker reproduces exactly the original feature/target
    # arrays for these targets. New timestamps/session labels cannot turn that
    # fixture into independent evidence.
    with pytest.raises(ValueError,match='exact model training observations'):
        spatial_session(source,tracker,str(profile_path),str(target_path),samples_per_target=4,validate=True)
    # Acquire a genuinely different held-out target set for the successful path.
    for target in targets:
        target['xyz_m']=(np.asarray(target['xyz_m'])+.01).tolist()
    target_path.write_text(json.dumps(targets))
    validation=spatial_session(source,tracker,str(profile_path),str(target_path),samples_per_target=4,validate=True)
    assert validation['independent_session'] is True
    assert validation['angular_error']['count']>=12*4
    assert load_profile(profile_path)['spatial_validation']['session_id']==validation['session_id']
    assert not source.closed and not tracker.closed
