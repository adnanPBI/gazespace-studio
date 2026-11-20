"""Exercise the installed Basler SDK using its official camera emulator only.

Acquisition/control/buffer gates remain strict. JSON is checkpointed before SDK
operations and on failure, so a CI failure or native crash retains its phase.
No camera images are saved. Successful software emulation is not hardware or
human gaze acceptance.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import sys
import tempfile
import time
import traceback

CLOCK_REFERENCE = 'https://docs.python.org/3.13/whatsnew/3.13.html#time'


class EmulatorVerificationError(RuntimeError):
    def __init__(self, report):
        self.report = report
        failure = report['failure']
        super().__init__(f"{failure['phase']}: {failure['exception_type']}: {failure['message']}")


def _plain(value):
    """Preserve diagnostic values without allowing nonfinite JSON constants."""
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return f'nonfinite:{value}'
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, 'item'):
        return _plain(value.item())
    return repr(value)


def _write_report(output, report):
    if output is None:
        return
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(_plain(report), indent=2, allow_nan=False) + '\n'
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.pylon-evidence-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _clock_info(name):
    value = time.get_clock_info(name)
    return {'implementation': value.implementation, 'resolution_s': value.resolution,
            'monotonic': value.monotonic, 'adjustable': value.adjustable}


def _frame_summary(frame, index):
    if frame is None:
        return {'index': index, 'present': False}
    image = getattr(frame, 'image', None)
    flags = getattr(image, 'flags', None)
    return _plain({'index': index, 'present': True,
        'sequence': getattr(frame, 'sequence', None), 'source': getattr(frame, 'source', None),
        'timestamp_s': getattr(frame, 'timestamp_s', None),
        'received_monotonic_s': getattr(frame, 'received_monotonic_s', None),
        'hardware_timestamp_s': getattr(frame, 'hardware_timestamp_s', None),
        'metadata': getattr(frame, 'metadata', None),
        'image_type': type(image).__name__, 'array_shape': getattr(image, 'shape', None),
        'array_dtype': str(getattr(image, 'dtype', None)),
        'array_strides': getattr(image, 'strides', None),
        'owns_data': bool(flags.owndata) if flags is not None else None,
        'c_contiguous': bool(flags.c_contiguous) if flags is not None else None,
        'array_base_type': type(getattr(image, 'base', None)).__name__})


def _check(condition, gate, detail):
    # These gates must also execute when Python is launched with -O.
    if not condition:
        raise AssertionError(f'{gate}: {detail}')


def verify(output: str | None = None) -> dict:
    report = {'status': 'running', 'phase': 'opt_in',
        'verification': 'Real pypylon binding with official Basler camera emulator',
        'started_at_utc': datetime.now(timezone.utc).isoformat(),
        'environment': {'python': platform.python_version(), 'platform': platform.platform(),
                        'python_executable': sys.executable, 'architecture': platform.machine(),
                        'PYLON_CAMEMU': os.environ.get('PYLON_CAMEMU'),
                        'clocks': {name: _clock_info(name) for name in ('monotonic', 'perf_counter')}},
        'physical_hardware_tested': False, 'real_eyes_or_gaze_accuracy_tested': False,
        'sdk_emulator_only': True, 'runs': [],
        'official_reference': 'https://docs.baslerweb.com/camera-emulation',
        'clock_compatibility_reference': CLOCK_REFERENCE,
        'clock_compatibility_note': 'Windows CPython before 3.13 uses a lower-resolution monotonic clock. Strict frame ordering is retained; no delays or fabricated timestamps are added by this verifier.',
        'limitations': [
            'Camera emulator does not validate USB transport, optics, IR illumination, shutter characteristics or physical camera timing.',
            'Host timestamps and raw hardware ticks are recorded separately; hardware clocks are not assumed synchronized.',
            'Control availability and value ranges depend on the real camera model.'],
        'reproduce_powershell': ['$env:PYLON_CAMEMU="1"', 'python scripts/verify_pypylon_emulator.py --output release_evidence/pypylon_sdk_emulator.json'],
        'reproduce_bash': 'PYLON_CAMEMU=1 python scripts/verify_pypylon_emulator.py --output release_evidence/pypylon_sdk_emulator.json'}

    def phase(name, record=None):
        report['phase'] = name
        if record is not None:
            record['phase'] = name
        _write_report(output, report)

    try:
        phase('opt_in')
        if os.environ.get('PYLON_CAMEMU') != '1':
            raise RuntimeError('Set PYLON_CAMEMU=1 before starting this fresh Python process')
        phase('import_sdk')
        from pypylon import pylon
        import numpy as np
        from gazespace.capture import PylonSource
        report['environment'].update(pypylon=importlib.metadata.version('pypylon'), numpy=np.__version__)
        phase('enumerate_emulator')
        devices = [d for d in pylon.TlFactory.GetInstance().EnumerateDevices()
                   if d.GetDeviceClass() == 'BaslerCamEmu']
        if not devices:
            raise RuntimeError('Basler camera-emulation transport layer is unavailable')
        serial = str(devices[0].GetSerialNumber())
        report['device'] = {'serial': serial, 'device_class': 'BaslerCamEmu', 'model': devices[0].GetModelName()}
        for pixel_format, bits in [('Mono8', None), ('Mono12', 8), ('BGR8Packed', None)]:
            # Mono12 intentionally requests stale 8-bit UI scaling; active sensor
            # PixelType must override it, preventing clipped IR frames.
            source, frames = None, []
            record = {'requested_pixel_format': pixel_format, 'ir_bit_depth': bits,
                      'status': 'running', 'frames': [], 'checks': {}, 'read_duration_ms': []}
            report['runs'].append(record)  # Preserve the failing run, not just completed ones.
            failure_in_acquisition = False
            try:
                phase(f'{pixel_format}/open_and_configure', record)
                source = PylonSource(serial=serial, exposure_us=3000, gain=0, timeout_ms=1500,
                                     pixel_format=pixel_format, ir_bit_depth=bits)
                record['capabilities'] = source.capabilities()
                phase(f'{pixel_format}/acquire_five_frames', record)
                # Do not print, write files or sleep between these reads: pacing
                # them could hide a coarse Windows host-clock defect.
                for _ in range(5):
                    started = time.perf_counter()
                    frames.append(source.read())
                    record['read_duration_ms'].append((time.perf_counter()-started)*1000)
                record['frames'] = [_frame_summary(frame, index) for index, frame in enumerate(frames, 1)]
                phase(f'{pixel_format}/frame_contract', record)
                _check(all(f is not None and isinstance(f.image, np.ndarray)
                           and f.image.dtype == np.uint8 and f.image.ndim == 3 and f.image.shape[2] == 3
                           for f in frames), 'bgr8_frames', f'Expected five HxWx3 uint8 frames; observed {record["frames"]}')
                _check(all(f.image.flags.owndata for f in frames), 'owned_buffers', f'Frame ownership: {[f.image.flags.owndata for f in frames]}')
                _check(all(isinstance(f.source, str) and f.source == serial
                           and f.metadata.get('sdk_emulator') is True for f in frames),
                       'emulator_source_identity', f'Expected emulator serial {serial!r}; observed {record["frames"]}')
                record['checks'].update(bgr8_frames=True, owned_buffers=True, emulator_source_identity=True)
                record['timestamps_s'] = [float(f.timestamp_s) for f in frames]
                record['timestamp_deltas_s'] = np.diff(record['timestamps_s']).tolist()
                record['sequences'] = [f.sequence for f in frames]
                phase(f'{pixel_format}/timestamp_and_sequence', record)
                _check(all(np.isfinite(f.timestamp_s) for f in frames)
                       and all(a.timestamp_s < b.timestamp_s for a, b in zip(frames, frames[1:])),
                       'strict_frame_timestamps', f'timestamps={record["timestamps_s"]}; deltas={record["timestamp_deltas_s"]}; clocks={report["environment"]["clocks"]}')
                _check(record['sequences'] == [1, 2, 3, 4, 5], 'frame_sequence', f'Expected [1,2,3,4,5], observed {record["sequences"]}')
                record['checks'].update(strict_frame_timestamps=True, frame_sequence=True)
                phase(f'{pixel_format}/control_readback', record)
                record['control_readback'] = source.controls
                for control in ('ExposureAuto', 'ExposureTime', 'GainAuto', 'Gain', 'PixelFormat'):
                    value = source.controls.get(control)
                    _check(isinstance(value, dict) and value.get('applied') is True
                           and value.get('readback_matches') is True,
                           f'control_{control}', f'Request/application/readback: {value!r}')
                record['checks']['control_readback'] = True
                before = [hashlib.sha256(f.image.tobytes()).hexdigest() for f in frames]
                record['buffer_hashes_before_reuse'] = before
                phase(f'{pixel_format}/additional_buffer_reuse', record)
                extra = source.read()
                record['extra_frame'] = _frame_summary(extra, 6)
                _check(extra is not None and extra.sequence == 6, 'extra_buffer_retrieval', f'Expected sixth SDK frame, observed {record["extra_frame"]}')
                after = [hashlib.sha256(f.image.tobytes()).hexdigest() for f in frames]
                record['buffer_hashes_after_reuse'] = after
                _check(after == before, 'stable_owned_buffers', f'Buffers changed after another SDK retrieval: before={before}; after={after}')
                record['checks']['stable_owned_buffers'] = True
                record.update(frames_checked=len(frames), extra_buffer_reuse_frame=1,
                    array_shape=list(frames[0].image.shape), array_dtype=str(frames[0].image.dtype),
                    min_pixel=int(frames[0].image.min()), max_pixel=int(frames[0].image.max()),
                    serial_preserved_as_string=True, timestamps_strictly_increasing=True,
                    buffers_owned_and_stable=True, first_frame_metadata=frames[0].metadata)
            except Exception as exc:
                failure_in_acquisition = True
                record['status'] = 'failed'
                record['frames'] = [_frame_summary(frame, index) for index, frame in enumerate(frames, 1)]
                record['failure'] = {'phase': record['phase'], 'exception_type': type(exc).__name__,
                                     'message': str(exc), 'traceback': traceback.format_exc()}
                raise
            finally:
                if source is not None:
                    try:
                        phase(f'{pixel_format}/close', record)
                        source.close()
                        record['closed_and_not_grabbing'] = not source.cam.IsOpen() and not source.cam.IsGrabbing()
                        _check(record['closed_and_not_grabbing'], 'closed_camera', 'Camera remains open or grabbing after close()')
                        phase(f'{pixel_format}/idempotent_close', record)
                        source.close()
                        _check(not source.cam.IsOpen() and not source.cam.IsGrabbing(), 'idempotent_close', 'Camera became open/grabbing after a second close()')
                        record['idempotent_close'] = True
                        record['checks'].update(closed_camera=True, idempotent_close=True)
                    except Exception as exc:
                        record['cleanup_failure'] = {'phase': report['phase'], 'exception_type': type(exc).__name__,
                                                     'message': str(exc), 'traceback': traceback.format_exc()}
                        record['status'] = 'failed'
                        if not failure_in_acquisition:
                            raise
                    finally:
                        _write_report(output, report)
            record['status'] = 'passed'
            phase(f'{pixel_format}/complete', record)
        report.update(status='passed', phase='complete', verified_at_utc=datetime.now(timezone.utc).isoformat())
        _write_report(output, report)
        return _plain(report)
    except Exception as exc:
        prior = next((run.get('failure') for run in reversed(report['runs']) if run.get('failure')), None)
        report.update(status='failed', failed_at_utc=datetime.now(timezone.utc).isoformat(),
                      failure=prior or {'phase': report['phase'], 'exception_type': type(exc).__name__,
                                        'message': str(exc), 'traceback': traceback.format_exc()})
        try:
            _write_report(output, report)
        except Exception as write_error:
            report['evidence_write_error'] = f'{type(write_error).__name__}: {write_error}'
        raise EmulatorVerificationError(_plain(report)) from exc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output')
    args = parser.parse_args()
    try:
        report = verify(args.output)
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        if isinstance(exc, EmulatorVerificationError):
            print(json.dumps(exc.report, indent=2, allow_nan=False))
        if args.output:
            print(f'SDK emulator failure evidence: {Path(args.output).resolve()}', file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
