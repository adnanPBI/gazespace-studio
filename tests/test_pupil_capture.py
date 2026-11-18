import cv2
import numpy as np
import pytest
from gazespace.pupil import detect_pupil, preprocess_ir
from gazespace.capture import to_bgr
from gazespace.vision import observation_from_rois

def fake_eye(shape=(130,220),xy=(110,65),radius=17,polarity="dark"):
    canvas=np.full(shape,200 if polarity=="dark" else 20,np.uint8)
    cv2.circle(canvas,xy,radius,20 if polarity=="dark" else 240,-1,cv2.LINE_AA)
    return canvas

@pytest.mark.parametrize("polarity",["dark","bright"])
def test_pupil_centroid_and_radius(polarity):
    im=fake_eye(polarity=polarity)
    result=detect_pupil(im,(55,28,110,80),polarity,4,35)
    assert result is not None
    assert np.linalg.norm(np.array(result.xy)-[110,65])<3
    assert 10<result.radius_px<23
    assert result.confidence>.3

def test_reject_borders_and_missing_roi():
    im=np.zeros((100,100),dtype=np.uint8)
    assert detect_pupil(im,(3,4,5,5)) is None
    assert detect_pupil(im,(0,0,90,90)) is None

def test_invalid_polarity():
    with pytest.raises(ValueError):
        detect_pupil(fake_eye(),(0,0,200,120),polarity="unknown")

def test_preprocess_ir_grayscale():
    im=np.tile(np.arange(64,dtype=np.uint8),(64,1))
    out=preprocess_ir(im)
    assert out.shape==im.shape
    assert out.max()>out.min()

def test_mono12_to_8bgr():
    raw=np.array([[0,2048,4095]],dtype=np.uint16)
    bgr=to_bgr(raw,12)
    assert bgr.shape==(1,3,3)
    assert list(bgr[0,:,0])==[0,127,255]

def test_bgra_to_bgr_and_unsupported():
    x=np.zeros((3,4,4),dtype=np.uint8)
    assert to_bgr(x).shape==(3,4,3)
    with pytest.raises(ValueError):
        to_bgr(np.zeros((2,2,2),dtype=np.uint8))

def test_ir_manual_two_eye_detection():
    frame=np.full((200,500,3),185,dtype=np.uint8)
    cv2.circle(frame,(160,100),17,(18,18,18),-1)
    cv2.circle(frame,(355,100),17,(18,18,18),-1)
    rois={"right":(.20,.30,.25,.40),"left":(.60,.30,.25,.40)}
    ob=observation_from_rois(frame,12.3,rois)
    assert ob.features is not None
    assert ob.quality>.3
    assert ob.message=="ok"
    assert np.linalg.norm(np.array(ob.pupils_px["right"])-[160,100])<3
    assert len(ob.features)==10

def test_ir_missing_one_eye_rejected():
    frame=np.full((200,500,3),200,dtype=np.uint8)
    cv2.circle(frame,(160,100),18,(10,10,10),-1)
    rois={"right":(.20,.30,.25,.40),"left":(.60,.30,.25,.40)}
    assert observation_from_rois(frame,1,rois).features is None
