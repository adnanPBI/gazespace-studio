"""Regressions for requested camera controls and truthful desktop drift evidence."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import numpy as np
import pytest

from gazespace.capture import PylonSource, create_source


class Node:
    def __init__(self, value, *, transform=None, reject=False):
        self._value, self.transform, self.reject = value, transform, reject

    @property
    def Value(self):
        return self._value

    @Value.setter
    def Value(self, value):
        if self.reject:
            raise ValueError("node is not writable in this camera mode")
        self._value = self.transform(value) if self.transform else value


def fake_pylon(monkeypatch, **nodes):
    class Camera:
        def Open(self): self.open = True
        def IsOpen(self): return self.open
        def Close(self): self.open = False
        def StartGrabbing(self, *_): self.grabbing = True
        def IsGrabbing(self): return self.grabbing
        def StopGrabbing(self): self.grabbing = False
    camera = Camera()
    for name, node in nodes.items():
        setattr(camera, name, node)
    device = SimpleNamespace(GetSerialNumber=lambda: "000123", GetModelName=lambda: "control fixture")
    factory = SimpleNamespace(EnumerateDevices=lambda: [device], CreateDevice=lambda d: d)
    pylon = SimpleNamespace(TlFactory=SimpleNamespace(GetInstance=lambda: factory),
                            InstantCamera=lambda d: camera,
                            ImageFormatConverter=lambda: SimpleNamespace(),
                            PixelType_BGR8packed=3, GrabStrategy_LatestImageOnly=1)
    monkeypatch.setitem(sys.modules, "pypylon", SimpleNamespace(pylon=pylon))
    return camera


def test_basler_factory_applies_resolution_and_legacy_rate_without_enable_node(monkeypatch):
    camera = fake_pylon(monkeypatch, Width=Node(1024), Height=Node(768),
                        AcquisitionFrameRateAbs=Node(60.))
    with create_source("000123", backend="pylon", width=640, height=480, fps=25.) as source:
        report = source.capabilities()
        assert (report["width"], report["height"], report["fps_reported"]) == (640, 480, 25.)
        assert report["serial"] == "000123"
        for name in ("Width", "Height", "AcquisitionFrameRateAbs"):
            assert report["controls"][name]["applied"]
            assert report["controls"][name]["readback_matches"]
        assert "AcquisitionFrameRateEnable" not in report["controls"]
    assert not camera.open


def test_basler_rejected_or_rounded_settings_are_not_reported_as_applied(monkeypatch):
    fake_pylon(monkeypatch, Width=Node(1024, transform=lambda n: n+1),
               Height=Node(768, reject=True), AcquisitionFrameRateEnable=Node(False, reject=True),
               AcquisitionFrameRate=Node(60.))
    with PylonSource("000123", width=640, height=480, fps=20.) as source:
        report = source.capabilities()
        for name in ("Width", "Height", "AcquisitionFrameRateEnable", "AcquisitionFrameRate"):
            assert report["controls"][name]["applied"] is False
            assert report["controls"][name]["readback_matches"] is False
            assert report["controls"][name]["error"]
        assert report["controls"]["Width"]["actual"] == 641
        assert report["controls"]["Height"]["actual"] == 768
        assert report["fps_reported"] == 60.


def test_basler_missing_requested_controls_are_explicit(monkeypatch):
    fake_pylon(monkeypatch)
    with PylonSource("000123", width=640, height=480, fps=20.) as source:
        report = source.capabilities()
        assert report["width"] is None and report["height"] is None and report["fps_reported"] is None
        for name in ("Width", "Height", "AcquisitionFrameRate"):
            assert report["controls"][name]["applied"] is False
            assert report["controls"][name]["error"]


@pytest.mark.parametrize("options", [{"width":0},{"height":2.5},{"fps":float("nan")},{"fps":float("inf")}])
def test_basler_invalid_stream_requests_fail_before_opening(options):
    with pytest.raises(ValueError):
        PylonSource(**options)


@pytest.mark.skipif(os.environ.get("GAZESPACE_TEST_PYLON_EMULATOR") != "1",
                    reason="Opt-in real SDK emulator; no physical camera is opened")
def test_real_sdk_emulator_receives_requested_size_and_frame_rate():
    root = Path(__file__).resolve().parents[1]
    script = """
import json
from pypylon import pylon
from gazespace.capture import create_source
serial=next(d.GetSerialNumber() for d in pylon.TlFactory.GetInstance().EnumerateDevices()
            if d.GetDeviceClass()=='BaslerCamEmu')
with create_source(serial,backend='pylon',width=320,height=240,fps=17.,pixel_format='Mono8') as source:
    frame=source.read()
    print(json.dumps({'capabilities':source.capabilities(),'shape':frame.image.shape,'metadata':frame.metadata}))
"""
    result = subprocess.run([sys.executable, "-c", script], cwd=root,
                            env=dict(os.environ, PYLON_CAMEMU="1", PYTHONPATH=str(root/"src")),
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["shape"] == [240, 320, 3]
    report = value["capabilities"]
    assert (report["width"], report["height"]) == (320, 240)
    assert report["controls"]["AcquisitionFrameRate"]["actual"] == pytest.approx(17.)
    assert report["controls"]["AcquisitionFrameRate"]["applied"]
    assert report["sdk_emulator"] and value["metadata"]["simulated"]
    assert report["global_shutter_verified"] is False


def test_desktop_drift_reports_wrong_baseline_and_elapsed_time_without_acceptance_pass(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from gazespace.calibration import GazeModel, update_profile, load_profile
    from gazespace.ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    window = MainWindow(workspace=tmp_path, interactive_errors=False)
    errors = []
    window.error_reported.connect(errors.append)
    path = tmp_path/"profiles"/"drift.json"
    coeff = np.zeros((16,2)); coeff[0] = [.75,.5]
    model = GazeModel("screen",coeff,np.zeros(15),np.ones(15),1,{},
                      {"screen_size":[1000,600],"session_id":"training"})
    update_profile(path,screen_model=model.to_dict())
    window.settings["profile"] = str(path)
    rows = []
    for name,stamp in (("anchor_initial",1.),("anchor_repeat",601.)):
        for index in range(4):
            rows.append({"features":[.5,.5,.5,.5,0,0,0,0,0,0],"target":[.5,.5],
                         "target_id":name,"time_s":stamp+index*.1})
    try:
        window.finish_screen_session({"purpose":"drift","records":rows,"screen_size":(1000,600),
                                      "context":{},"session_id":"fresh-drift","monitor":"fixture",
                                      "_source_context":{"camera_id":"fixture","subject_id":"operator",
                                                         "screen_size":[1000,600]}})
        deadline = time.monotonic()+5
        while window._jobs and time.monotonic()<deadline:
            app.processEvents();time.sleep(.01)
        assert not window._jobs and not errors, errors
        report = load_profile(path)["drift_validation"]
        assert report["drift_px"] == 0
        assert report["first_anchor_error_px"] == pytest.approx(249.75)
        assert report["repeat_anchor_error_px"] == pytest.approx(249.75)
        assert report["target_px"] == [499.5,299.5]
        assert report["elapsed_s"] == pytest.approx(600.3)
        assert report["acceptance_status"] == "not_evaluated"
        assert report["physical_accuracy_verified"] is False
        assert report["accuracy_verified"] is False
    finally:
        window.stop_tracking();window.close();app.processEvents();window.pool.waitForDone(5000)
