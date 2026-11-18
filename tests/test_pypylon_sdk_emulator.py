"""Opt-in real-SDK test; selects only BaslerCamEmu, never a physical device."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(os.environ.get('GAZESPACE_TEST_PYLON_EMULATOR') != '1',
                    reason='Opt-in: set GAZESPACE_TEST_PYLON_EMULATOR=1 with pypylon installed')
def test_real_pypylon_emulator_acquisition_controls_and_buffers(tmp_path):
    root=Path(__file__).resolve().parents[1]
    evidence=tmp_path/'emulator.json'
    env=dict(os.environ,PYLON_CAMEMU='1',PYTHONPATH=str(root/'src'))
    result=subprocess.run([sys.executable,str(root/'scripts'/'verify_pypylon_emulator.py'),
                           '--output',str(evidence)],cwd=root,env=env,
                          text=True,capture_output=True,timeout=20)
    assert result.returncode==0,result.stderr
    report=json.loads(evidence.read_text(encoding='utf-8'))
    assert report['status']=='passed'
    assert report['sdk_emulator_only']
    assert report['physical_hardware_tested'] is False
    assert report['device']['device_class']=='BaslerCamEmu'
    assert {r['requested_pixel_format'] for r in report['runs']}=={'Mono8','Mono12','BGR8Packed'}
    assert all(r['buffers_owned_and_stable'] and r['closed_and_not_grabbing'] for r in report['runs'])
