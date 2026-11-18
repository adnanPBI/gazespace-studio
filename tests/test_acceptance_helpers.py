"""Material acceptance-runner checks: bounds, provenance and enforced limits."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'camera_acceptance.py'
SPEC = importlib.util.spec_from_file_location('camera_acceptance_test_module', SCRIPT)
HELPER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPER)


def test_blocking_worker_is_terminated_within_a_bound():
    process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    code, terminated = HELPER.wait_bounded(process, 0.15)
    assert terminated is True
    assert code != 0
    assert process.poll() is not None


@pytest.mark.parametrize('backend,source', [('dshow', 'recording.avi'), ('auto', 'rtsp://example.test/feed'), ('pylon', 'first')])
def test_recorded_or_ambiguous_source_cannot_be_accepted(tmp_path, backend, source):
    receipt = tmp_path / 'invalid.json'
    with pytest.raises(SystemExit) as error:
        HELPER.main(['--backend', backend, '--source', source, '--output', str(receipt)])
    assert error.value.code == 2
    assert not receipt.exists()


@pytest.mark.parametrize('minimum_fps,expected_exit', [(1, 0), (10000, 2)])
def test_real_worker_preserves_synthetic_provenance_and_enforces_limit(tmp_path, minimum_fps, expected_exit):
    receipt = tmp_path / 'fixture.json'
    result = subprocess.run([
        sys.executable, str(SCRIPT), '--backend', 'synthetic', '--source', 'synthetic',
        '--seconds', '1', '--width', '320', '--height', '180', '--fps', '30',
        '--minimum-fps', str(minimum_fps), '--allow-simulated', '--physical-device-confirmed',
        '--output', str(receipt),
    ], capture_output=True, text=True, timeout=15)
    assert result.returncode == expected_exit, result.stdout + result.stderr
    data = json.loads(receipt.read_text())
    assert data['frames_received'] >= 2
    assert data['clean_shutdown'] is True
    assert data['worker_forced_termination'] is False
    assert data['simulated'] is True
    assert data['physical_hardware_tested'] is False
    assert data['real_person_gaze_accuracy_established'] is False
    assert data['sensor_frames_dropped'] is None
    assert data['exposure_to_display_latency_ms'] is None
    assert data['images_saved'] is False
    assert data['checks']['explicit_minimum_fps'] is (expected_exit == 0)
    assert receipt.with_suffix('.frames.csv').exists()
    assert not list(tmp_path.glob('*.png'))
    assert not list(tmp_path.glob('*.avi'))
