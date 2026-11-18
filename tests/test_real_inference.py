"""Runs the installed face model, with no mocked landmarks or gaze truth."""
from pathlib import Path
import importlib.util
import pytest


def test_actual_face_iris_model_on_reference_photo():
    pytest.importorskip('mediapipe')
    root=Path(__file__).resolve().parents[1]
    spec=importlib.util.spec_from_file_location('verify_face_inference',root/'scripts'/'verify_face_inference.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result=module.verify(root/'examples'/'reference_face_nasa.png')
    assert result['status']=='passed'
    assert result['landmark_count']==478
    assert result['gaze_accuracy_validated'] is False
