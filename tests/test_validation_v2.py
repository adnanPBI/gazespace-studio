import numpy as np
import pytest
from gazespace.validation import write_checkerboard_svg,validate_camera_images,camera_training_evidence,validate_gaze_records
from gazespace.types import CameraIntrinsics
from gazespace.demo import build_demo_profile
from gazespace.calibration import GazeModel


def test_printable_board_exact_physical_size(tmp_path):
    p=write_checkerboard_svg(tmp_path/'board.svg',9,6,24)
    import xml.etree.ElementTree as ET
    root=ET.fromstring(p.read_text())
    assert root.attrib['width']=='270.0mm'
    assert root.attrib['height']=='210.0mm'
    assert len(root.findall('{http://www.w3.org/2000/svg}rect'))==36


def test_reused_intrinsic_training_image_rejected():
    image=np.zeros((480,640,3),np.uint8)
    intr=CameraIntrinsics(640,480,np.array([[600.,0,320],[0,600,240],[0,0,1]]),np.zeros(5))
    evidence=camera_training_evidence([image])
    with pytest.raises(ValueError,match='already used'):
        validate_camera_images([image],intr,training_hashes=evidence['image_sha256'])


def test_intrinsic_validation_rejects_size_change():
    intr=CameraIntrinsics(640,480,np.array([[600.,0,320],[0,600,240],[0,0,1]]),np.zeros(5))
    with pytest.raises(ValueError,match='resolution'):
        validate_camera_images([np.zeros((240,320,3),np.uint8)],intr)


def test_independent_gaze_validation_checks_identity(tmp_path):
    profile=build_demo_profile(tmp_path/'demo.json');model=GazeModel.from_dict(profile['screen_model'])
    records=[{'features':[.5]*10,'target':[.5,.5],'target_id':'one'}]
    with pytest.raises(ValueError,match='training session'):
        validate_gaze_records(model,records,context=model.metadata,session_id=model.metadata['session_id'])
    context=dict(model.metadata);context['subject_id']='someone-else'
    with pytest.raises(ValueError,match='subject_id'):
        validate_gaze_records(model,records,context=context,session_id='fresh-session')
