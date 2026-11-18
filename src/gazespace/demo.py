"""Reproducible synthetic end-to-end demo using the real pupil detector.

The camera image is generated, so errors here test software relationships only.
They do not measure human gaze accuracy, IR hardware, or clinical performance.
"""
from __future__ import annotations
from pathlib import Path
import numpy as np
from .capture import SyntheticEyeSource
from .vision import GazeTracker
from .calibration import fit_gaze_model, screen_record, spatial_record, new_profile, load_profile, save_json, GazeModel, _error_metrics
from .engine import GazeEngine
from .types import CameraExtrinsics, Surface

DEMO_SOURCE_CONTEXT = {'camera_id':'synthetic-v2','backend':'synthetic','subject_id':'synthetic-fixture'}
VIRTUAL_EYE_ORIGIN = np.array([0.0,0.0,0.65])
VIRTUAL_EXTRINSICS = CameraExtrinsics(np.eye(3),np.zeros(3))


def apply_virtual_origin(observation):
    if observation.features is None:return
    observation.eye_origin_camera_m=VIRTUAL_EYE_ORIGIN.copy()
    observation.eye_origin_source='synthetic_fixture'
    observation.context['eye_origin_source']='synthetic_fixture'
    observation.features=observation.features.copy();observation.features[-1]=VIRTUAL_EYE_ORIGIN[2]
    observation.warnings.append('synthetic_origin_and_surfaces_are_known_virtual_geometry')


class SyntheticDemoEngine(GazeEngine):
    """Explicit software fixture; never guesses geometry for a real camera."""
    @classmethod
    def from_profile(cls,path,*,source_context=None,**_):
        profile=load_profile(path)
        if not profile.get('synthetic') or profile.get('camera_id')!='synthetic-v2':
            raise ValueError('The synthetic engine accepts only its clearly labeled fixture profile')
        sm=GazeModel.from_dict(profile['screen_model'])
        sp=GazeModel.from_dict(profile['spatial_model']) if profile.get('spatial_model') else None
        actual={**DEMO_SOURCE_CONTEXT,**(source_context or {})}
        for model in (sm,sp):
            if model is not None:
                mismatch=model.context_mismatches(actual)
                if mismatch:raise ValueError('Synthetic profile context mismatch: '+', '.join(mismatch))
        tracker=GazeTracker(mode='ir-roi',roi_normalized=profile['context']['roi_normalized'])
        return cls(tracker,sm,sp,VIRTUAL_EXTRINSICS,[Surface.from_dict(v) for v in profile['surfaces']],source_context=actual)

    def process_frame(self,frame):
        if not (frame.metadata or {}).get('synthetic'):
            raise ValueError('A real camera frame cannot use the virtual-eye simulation engine')
        return super().process_frame(frame)

    def _apply_depth(self,frame,observation):
        apply_virtual_origin(observation)


def build_demo_profile(path: str | Path, screen_size=(1920,1080), width=640, height=360) -> dict:
    source = SyntheticEyeSource(width=width,height=height,realtime=False)
    tracker = GazeTracker(mode='ir-roi',roi_normalized=source.roi_normalized)
    records = []
    spatial_records = []
    rng = np.random.default_rng(412)
    timestamp = 10.0
    context = {}
    try:
        for i,y in enumerate(np.linspace(.08,.92,5)):
            for j,x in enumerate(np.linspace(.08,.92,5)):
                for sample in range(12):
                    # A small controlled image perturbation prevents claiming
                    # perfection from numerically identical feature copies.
                    nx,ny=np.asarray([x,y])+rng.normal(0,.002,2)
                    ob = tracker.process(source.render_target(float(nx),float(ny)),timestamp)
                    timestamp += 1/30
                    apply_virtual_origin(ob)
                    if ob.features is None or ob.quality < .35:
                        raise RuntimeError(f'Synthetic pupil detection failed at target {i}-{j}: {ob.message}')
                    context = dict(getattr(ob,'context',{}))
                    records.append(screen_record(ob,f'{i}-{j}',(x*(screen_size[0]-1),y*(screen_size[1]-1)),screen_size))
                    depth=0.0 if sample%2==0 else -0.25
                    scale=(VIRTUAL_EYE_ORIGIN[2]-depth)/VIRTUAL_EYE_ORIGIN[2]
                    point=((x-.5)*.60*scale,(y-.5)*.34*scale,depth)
                    spatial_records.append(spatial_record(ob,f'{i}-{j}',point,VIRTUAL_EXTRINSICS))
        context.update(DEMO_SOURCE_CONTEXT)
        model=fit_gaze_model(records,screen_size=screen_size,context=context,session_id='synthetic-training-v2')
        model.metadata.update({'synthetic':True,'provenance':'generated pupil images; software demo only',
                               'camera_id':'synthetic-v2'})
        spatial_model=fit_gaze_model(spatial_records,'spatial',context=context,session_id='synthetic-spatial-training-v2')
        spatial_model.metadata.update({'synthetic':True,'virtual_target_depths_m':[0.0,-.25],
                                       'provenance':'known virtual eye origin; rendered pupil observations'})
        independent=[]
        target_values=[]
        for x,y in ((.2,.25),(.5,.2),(.8,.25),(.22,.5),(.5,.5),(.78,.5),(.2,.75),(.5,.8),(.8,.75)):
            ob=tracker.process(source.render_target(x,y),timestamp);timestamp+=1/30;apply_virtual_origin(ob)
            independent.append(ob.features);target_values.append([x,y])
        report=_error_metrics(model.predict(np.asarray(independent)),np.asarray(target_values),'screen',
                              screen_size)
        profile=new_profile(camera_id='synthetic-v2',context=context,subject_id='synthetic-fixture')
        profile['screen_model']=model.to_dict()
        profile['spatial_model']=spatial_model.to_dict()
        profile['extrinsics']=VIRTUAL_EXTRINSICS.to_dict()
        profile['surfaces']=[Surface('Virtual display',(-.30,-.17,0.0),(1.,0.,0.),(0.,1.,0.),.60,.34,screen_size).to_dict(),
                             Surface('Virtual workbench',(-.30,.30,-.10),(1.,0.,0.),(0.,0.,1.),.60,.38,None).to_dict()]
        profile['simulation_geometry']={'eye_origin_camera_m':VIRTUAL_EYE_ORIGIN.tolist(),
                                        'source':'explicit simulation fixture; no physical measurements'}
        profile['synthetic']=True
        profile['screen_validation']={'synthetic':True,'independent_targets':True,'screen_error':report,
                                      'note':'Software fixture; does not establish real-person gaze accuracy'}
        save_json(path,profile)
        return profile
    finally:
        tracker.close();source.close()
