import numpy as np
import cv2
from gazespace.lighting import frame_quality

def test_over_exposure_alert():
    im=np.full((480,640,3),252,np.uint8)
    issues=frame_quality(im)["warnings"]
    assert "overexposure_or_strong_illumination" in issues
    assert "substantial_bright_clipping" in issues

def test_under_exposure_alert():
    im=np.full((480,640,3),4,np.uint8)
    issues=frame_quality(im)["warnings"]
    assert "underexposure_or_weak_illumination" in issues
    assert "low_dynamic_range" in issues

def test_frame_quality_non_blank():
    rng=np.random.default_rng(5)
    im=rng.integers(20,200,(240,320,3),dtype=np.uint8)
    r=frame_quality(im)
    assert r["contrast"]>25
    assert r["sharpness_laplacian"]>10
