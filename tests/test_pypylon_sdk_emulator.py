"""Opt-in real SDK checks plus failure-evidence regressions without hardware."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace

import numpy as np
import pytest


def _load_verifier():
    path = Path(__file__).resolve().parents[1] / 'scripts' / 'verify_pypylon_emulator.py'
    spec = importlib.util.spec_from_file_location('gazespace_pylon_verifier_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_emulator_opt_in_failure_keeps_json_phase_and_traceback(tmp_path, monkeypatch):
    verifier = _load_verifier()
    monkeypatch.delenv('PYLON_CAMEMU', raising=False)
    evidence = tmp_path / 'failure.json'
    with pytest.raises(verifier.EmulatorVerificationError, match='opt_in'):
        verifier.verify(str(evidence))
    report = json.loads(evidence.read_text(encoding='utf-8'))
    assert report['status'] == 'failed'
    assert report['failure']['phase'] == 'opt_in'
    assert 'RuntimeError' in report['failure']['traceback']
    assert set(report['environment']['clocks']) == {'monotonic', 'perf_counter'}
    assert report['physical_hardware_tested'] is False


def test_duplicate_clock_failure_preserves_frames_phase_and_cleanup(tmp_path, monkeypatch):
    """Inject duplicate ticks; never turn the strict ordering gate into >=."""
    from gazespace.types import Frame
    import gazespace.capture
    verifier = _load_verifier()
    monkeypatch.setenv('PYLON_CAMEMU', '1')
    device = SimpleNamespace(GetDeviceClass=lambda: 'BaslerCamEmu',
                             GetSerialNumber=lambda: 'EMULATOR_TEST',
                             GetModelName=lambda: 'diagnostic-test-fixture')
    pylon = SimpleNamespace(TlFactory=SimpleNamespace(GetInstance=lambda: SimpleNamespace(
        EnumerateDevices=lambda: [device])))
    monkeypatch.setitem(sys.modules, 'pypylon', SimpleNamespace(pylon=pylon))
    original_version = verifier.importlib.metadata.version
    monkeypatch.setattr(verifier.importlib.metadata, 'version',
                        lambda name: 'test-fixture' if name == 'pypylon' else original_version(name))
    instances = []

    class Source:
        def __init__(self, **kwargs):
            self.sequence, self.closed, self.close_count = 0, False, 0
            self.cam = SimpleNamespace(IsOpen=lambda: not self.closed, IsGrabbing=lambda: not self.closed)
            self.controls = {}
            instances.append(self)
        def capabilities(self):
            return {'sdk_emulator': True, 'test_failure_injection': 'duplicate_host_ticks'}
        def read(self):
            self.sequence += 1
            return Frame(np.zeros((8, 8, 3), np.uint8), 1.0, self.sequence,
                         'EMULATOR_TEST', {'sdk_emulator': True}, received_monotonic_s=1.0)
        def close(self):
            self.closed = True
            self.close_count += 1

    monkeypatch.setattr(gazespace.capture, 'PylonSource', Source)
    evidence = tmp_path / 'clock_failure.json'
    with pytest.raises(verifier.EmulatorVerificationError, match='strict_frame_timestamps'):
        verifier.verify(str(evidence))
    report = json.loads(evidence.read_text(encoding='utf-8'))
    assert report['status'] == 'failed'
    assert report['failure']['phase'] == 'Mono8/timestamp_and_sequence'
    assert 'strict_frame_timestamps' in report['failure']['traceback']
    run = report['runs'][0]
    assert run['timestamps_s'] == [1.0]*5
    assert run['timestamp_deltas_s'] == [0.0]*4
    assert run['sequences'] == [1, 2, 3, 4, 5]
    assert len(run['frames']) == 5
    assert all(frame['owns_data'] for frame in run['frames'])
    assert run['closed_and_not_grabbing'] and run['idempotent_close']
    assert instances[0].closed and instances[0].close_count == 2


def _text(value):
    return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else value or ''


@pytest.mark.skipif(os.environ.get('GAZESPACE_TEST_PYLON_EMULATOR') != '1',
                    reason='Opt-in: set GAZESPACE_TEST_PYLON_EMULATOR=1 with pypylon installed')
def test_real_pypylon_emulator_acquisition_controls_and_buffers(tmp_path):
    root = Path(__file__).resolve().parents[1]
    evidence = tmp_path / 'emulator.json'
    # Pytest stays isolated; Windows CI may opt into a dedicated diagnostics path.
    path_override = os.environ.get('GAZESPACE_PYTEST_EVIDENCE_DIR', '').strip()
    saved = Path(path_override).resolve() if path_override else tmp_path
    saved.mkdir(parents=True, exist_ok=True)
    stem = ('windows_' if os.name == 'nt' else '') + 'pypylon_pytest'
    env = dict(os.environ, PYLON_CAMEMU='1', PYTHONPATH=str(root/'src'),
               PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')
    command = [sys.executable, str(root/'scripts'/'verify_pypylon_emulator.py'),
               '--output', str(evidence)]
    started = time.perf_counter()
    timed_out = False
    try:
        result = subprocess.run(command, cwd=root, env=env, text=True, encoding='utf-8',
                                errors='replace', capture_output=True, timeout=45)
        returncode, stdout, stderr = result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        returncode, stdout, stderr = None, _text(exc.stdout), _text(exc.stderr)
    elapsed = time.perf_counter()-started
    (saved/f'{stem}.stdout.log').write_text(stdout, encoding='utf-8')
    (saved/f'{stem}.stderr.log').write_text(stderr, encoding='utf-8')
    if evidence.exists():
        shutil.copyfile(evidence, saved/f'{stem}.json')
    process = {'command': command, 'returncode': returncode, 'timed_out': timed_out,
               'elapsed_s': elapsed, 'evidence_created': evidence.exists(),
               'stdout_log': f'{stem}.stdout.log', 'stderr_log': f'{stem}.stderr.log',
               'physical_hardware_tested': False}
    (saved/f'{stem}.process.json').write_text(json.dumps(process, indent=2)+'\n', encoding='utf-8')
    detail = (f'Real SDK verifier returncode={returncode}, timed_out={timed_out}, elapsed={elapsed:.2f}s; '
              f'preserved evidence: {saved/f"{stem}.json"}\n'
              f'STDERR:\n{stderr[-16000:]}\nSTDOUT/partial report:\n{stdout[-18000:]}')
    assert not timed_out, detail
    assert returncode == 0, detail
    assert evidence.exists(), detail
    report = json.loads(evidence.read_text(encoding='utf-8'))
    assert report['status'] == 'passed', report
    assert report['sdk_emulator_only']
    assert report['physical_hardware_tested'] is False
    assert report['device']['device_class'] == 'BaslerCamEmu'
    assert {r['requested_pixel_format'] for r in report['runs']} == {'Mono8', 'Mono12', 'BGR8Packed'}
    assert all(r['buffers_owned_and_stable'] and r['closed_and_not_grabbing']
               and r['timestamps_strictly_increasing'] and r['idempotent_close'] for r in report['runs']), report
