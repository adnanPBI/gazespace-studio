import subprocess
import sys
from pathlib import Path
import numpy as np
import cv2
import pytest
from gazespace.cli import build_parser,parse_roi,main
from gazespace.geometry import FACE_TEMPLATE_M,FACE_INDICES,approximate_intrinsics
from gazespace.vision import observation_from_landmarks,eye_aspect_ratio,GazeTracker


def test_cli_parse():
    p=build_parser()
    assert p.parse_args(['track','--backend','dshow','--mode','ir-roi','--roi-right','.1,.2,.2,.3']).command=='track'
    assert parse_roi('.2,.2,.1,.1')==(.2,.2,.1,.1)
    with pytest.raises(Exception):parse_roi('.9,.9,.4,.4')


def test_doctor_and_demo_runs(tmp_path,capsys):
    assert main(['doctor'])==0
    assert main(['demo','--output',str(tmp_path/'demo.json')])==0
    assert (tmp_path/'demo.json').exists()
    assert 'SYNTHETIC' in capsys.readouterr().out


def test_synthetic_landmarks_observation():
    intr=approximate_intrinsics(1280,720)
    pos,_=cv2.projectPoints(FACE_TEMPLATE_M,np.zeros((3,1)),np.array([[0],[0],[.8]]),
                             intr.camera_matrix,intr.distortion)
    lm={i:tuple(map(float,p)) for i,p in zip(FACE_INDICES,pos.reshape(-1,2))}
    # Artificial eye patch features, projection-aware PnP landmarks stay untouched.
    for iris,left,right,top,bottom in [(468,33,133,159,145),(473,362,263,386,374)]:
        if left not in lm:lm[left]=((lm[right][0]-80.,lm[right][1]) if right in lm else (420.,340.))
        if right not in lm:lm[right]=(lm[left][0]+80.,lm[left][1])
        center=(np.array(lm[left])+np.array(lm[right]))/2
        lm[iris]=tuple(center)
        lm[top]=tuple(center+[0,-14]);lm[bottom]=tuple(center+[0,14])
    for ids in [(33,160,158,133,153,144),(362,385,387,263,373,380)]:
        p=np.array(lm[ids[0]]);q=np.array(lm[ids[3]])
        for j,alpha in ((1,.33),(2,.67),(4,.67),(5,.33)):
            xy=p*(1-alpha)+q*alpha
            xy[1]+= -15 if j in (1,2) else 15
            lm[ids[j]]=tuple(xy)
    ob=observation_from_landmarks(lm,(1280,720),1,intr)
    assert ob.features is not None
    assert ob.head_pose is not None
    assert ob.eye_origin_camera_m is not None
    assert not ob.blink
    assert len(ob.features)==10


def test_tracker_requires_rois_in_ir_roi_mode():
    with pytest.raises(ValueError):GazeTracker(mode='ir-roi')


def test_empty_landmarks_rejected():
    ob=observation_from_landmarks({1:(10,10)},(1280,720),1)
    assert ob.features is None
    assert ob.message=='missing_iris_landmarks'
