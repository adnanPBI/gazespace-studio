"""Bounded capture measurements for a selected live camera; no images are saved.

Run directly with Python, or through ``GazeSpaceCLI camera-soak``. A separate
worker owns the camera so a blocked native driver cannot leave the acceptance
command waiting indefinitely. A timed-out worker is reported as a failed clean
shutdown, never as a successful camera test.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
from gazespace.clock import HOST_CLOCK_DOMAIN, host_now_s, host_clock_info


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_value(value):
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, 'item'):
        return json_value(value.item())
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def write_json(path: Path, report: dict) -> None:
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(json_value(report), indent=2, allow_nan=False) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def summarize(values: list[float]) -> dict:
    if not values:
        return {'count': 0, 'minimum': None, 'median': None, 'p95': None, 'maximum': None}
    import numpy as np
    data = np.asarray(values, dtype=float)
    data = data[np.isfinite(data)]
    if not len(data):
        return summarize([])
    return {'count': len(data), 'minimum': float(data.min()), 'median': float(np.median(data)),
            'p95': float(np.percentile(data, 95)), 'maximum': float(data.max())}


def wait_bounded(process: subprocess.Popen, timeout_s: float) -> tuple[int, bool]:
    """Only terminate the worker started by this acceptance command."""
    try:
        return process.wait(timeout=timeout_s), False
    except subprocess.TimeoutExpired:
        process.kill()
        return process.wait(timeout=10), True


def run_worker(config: dict, checkpoint: Path) -> int:
    import cv2
    import numpy as np
    from gazespace import __version__
    from gazespace.capture import create_source

    report = {
        'schema_version': 1, 'status': 'opening_camera', 'started_at_utc': utc_now(),
        'application_version': __version__, 'python': platform.python_version(),
        'platform': platform.platform(), 'frozen_executable': bool(getattr(sys, 'frozen', False)),
        'executable_sha256': file_hash(Path(sys.executable)), 'configuration': config,
        'physical_hardware_tested': False, 'camera_opened': False, 'frames_received': 0,
        'host_clock': host_clock_info(),
        'camera_acquisition_attempted': True,
        'simulated': False, 'images_saved': False, 'clean_shutdown': False,
        'real_person_gaze_accuracy_established': False,
        'sensor_frames_dropped': None, 'exposure_to_display_latency_ms': None,
        'global_shutter_verified': False,
        'measurement_scope': 'Camera delivery and software read timing; no gaze model or fixation ground truth',
    }
    write_json(checkpoint, report)
    source = None
    frame_times: list[float] = []
    read_times: list[float] = []
    ages: list[float] = []
    luminance: list[float] = []
    dark_clipping: list[float] = []
    bright_clipping: list[float] = []
    depth_coverage: list[float] = []
    shapes: Counter = Counter()
    invalid_frames = 0
    read_failures = 0
    timestamp_errors = 0
    host_clock_errors = 0
    sequence_discontinuities = 0
    previous_stamp = None
    previous_sequence = None
    actual_duration = 0.0
    acquisition_error = None
    close_error = None
    capture_started = None
    opened = host_now_s()
    last_checkpoint = opened
    timing_path = Path(config['timing_csv'])
    fields = ['sequence', 'elapsed_host_s', 'host_receive_monotonic_s', 'frame_timestamp_s',
              'hardware_timestamp_s', 'hardware_ticks', 'blocking_read_ms',
              'receive_to_script_ms', 'arrival_gap_ms', 'width', 'height',
              'mean_luminance_u8', 'dark_clipped_fraction', 'bright_clipped_fraction',
              'valid_depth_fraction']
    try:
        source = create_source(
            config['source'], backend=config['backend'], width=config['width'], height=config['height'],
            fps=config['fps'], exposure_native=config.get('exposure_native'),
            exposure_us=config.get('exposure_us'), gain=config.get('gain'), gain_native=config.get('gain'),
            ir_bit_depth=config.get('ir_bit_depth'), pixel_format=config.get('pixel_format'),
            fourcc=config.get('fourcc'), timeout_ms=1000, realtime=True,
        )
        capabilities = json_value(source.capabilities())
        simulated = any(bool(capabilities.get(k)) for k in ('synthetic', 'simulated', 'sdk_emulator'))
        report.update(camera=capabilities, camera_opened=True, simulated=simulated,
                      camera_open_duration_ms=(host_now_s() - opened) * 1000)
        if simulated and not config['allow_simulated']:
            raise ValueError('Selected source is simulated; use --allow-simulated only for a software fixture, never physical acceptance')
        if not simulated and not capabilities.get('is_live', getattr(source, 'is_live', False)):
            raise ValueError('Recorded files cannot establish live camera acceptance')
        if config['backend'] in ('pylon', 'realsense') and str(capabilities.get('serial')) != config['source']:
            raise ValueError('Discovered device serial differs from the explicitly selected serial')
        report['status'] = 'capturing'
        write_json(checkpoint, report)
        capture_started = host_now_s()
        deadline = capture_started + config['seconds']
        with timing_path.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            while host_now_s() < deadline:
                before = host_now_s()
                frame = source.read()
                arrived = host_now_s()
                if frame is None:
                    read_failures += 1
                    time.sleep(0.005)
                    continue
                if any(bool(frame.metadata.get(k)) for k in ('synthetic', 'simulated', 'sdk_emulator')):
                    report['simulated'] = True
                    if not config['allow_simulated']:
                        raise ValueError('Frame metadata identifies simulated capture')
                image = frame.image
                if not isinstance(image, np.ndarray) or image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3 or image.size == 0:
                    invalid_frames += 1
                    continue
                height, width = image.shape[:2]
                shapes[f'{width}x{height}'] += 1
                stamp = float(frame.timestamp_s)
                if not math.isfinite(stamp) or (previous_stamp is not None and stamp <= previous_stamp):
                    timestamp_errors += 1
                previous_stamp = stamp
                if previous_sequence is not None and frame.sequence != previous_sequence + 1:
                    sequence_discontinuities += 1
                previous_sequence = frame.sequence
                receive = frame.received_monotonic_s
                declared_clock = frame.metadata.get('host_clock_domain')
                clock_valid = (receive is not None and math.isfinite(receive) and receive <= arrived
                               and (declared_clock is None or declared_clock == HOST_CLOCK_DOMAIN))
                if not clock_valid:
                    host_clock_errors += 1
                age_ms = (arrived - receive) * 1000 if clock_valid else None
                if age_ms is not None:
                    ages.append(age_ms)
                read_ms = (arrived - before) * 1000
                read_times.append(read_ms)
                gap_ms = None if not frame_times else (arrived - frame_times[-1]) * 1000
                frame_times.append(arrived)
                stride = max(1, math.ceil(max(width, height) / 320))
                gray = cv2.cvtColor(image[::stride, ::stride], cv2.COLOR_BGR2GRAY)
                mean = float(gray.mean())
                dark = float(np.mean(gray <= 2))
                bright = float(np.mean(gray >= 253))
                luminance.append(mean)
                dark_clipping.append(dark)
                bright_clipping.append(bright)
                valid_depth = None
                if frame.depth_m is not None:
                    depth = np.asarray(frame.depth_m)
                    valid_depth = float(np.mean(np.isfinite(depth) & (depth > 0)))
                    depth_coverage.append(valid_depth)
                writer.writerow(dict(zip(fields, [
                    int(frame.sequence), arrived - capture_started, receive, stamp,
                    frame.hardware_timestamp_s, frame.metadata.get('hardware_ticks'), read_ms,
                    age_ms, gap_ms, width, height, mean, dark, bright, valid_depth,
                ])))
                report['frames_received'] = len(frame_times)
                report['non_simulated_source_frames_received'] = not report['simulated']
                report['physical_hardware_tested'] = not report['simulated'] and config['physical_device_confirmed']
                if len(frame_times) == 1:
                    report['first_frame_metadata'] = json_value(frame.metadata)
                    report['first_frame_after_open_ms'] = (arrived - capture_started) * 1000
                    write_json(checkpoint, report)
                if arrived - last_checkpoint >= 1.0:
                    report['capture_elapsed_s'] = arrived - capture_started
                    write_json(checkpoint, report)
                    stream.flush()
                    last_checkpoint = arrived
            actual_duration = host_now_s() - capture_started
        report['camera_after_capture'] = json_value(source.capabilities())
    except Exception as exc:
        acquisition_error = f'{type(exc).__name__}: {exc}'
    finally:
        if capture_started is not None:
            actual_duration = host_now_s() - capture_started
        report.update(status='closing_camera', frames_received=len(frame_times), acquisition_error=acquisition_error)
        write_json(checkpoint, report)
        if source is not None:
            closing = host_now_s()
            try:
                source.request_stop()
                source.close()
                report['clean_shutdown'] = True
            except Exception as exc:
                close_error = f'{type(exc).__name__}: {exc}'
            report['close_duration_ms'] = (host_now_s() - closing) * 1000

    gaps = [(b - a) * 1000 for a, b in zip(frame_times, frame_times[1:])]
    fps = (len(frame_times) - 1) / (frame_times[-1] - frame_times[0]) if len(frame_times) > 1 else 0.0
    controls = report.get('camera_after_capture', report.get('camera', {})).get('controls', {})
    unconfirmed = [name for name, value in controls.items()
                   if value.get('applied') is not True or value.get('readback_matches') is False]
    checks = {
        'at_least_two_frames': len(frame_times) >= 2,
        'no_capture_exception': acquisition_error is None,
        'valid_bgr_frames': invalid_frames == 0,
        'finite_increasing_frame_timestamps': timestamp_errors == 0,
        'valid_host_receive_clock': host_clock_errors == 0,
        'clean_shutdown': bool(report['clean_shutdown']) and close_error is None,
        'read_failures_within_limit': read_failures <= config['max_read_failures'],
    }
    if config.get('minimum_fps') is not None:
        checks['explicit_minimum_fps'] = fps >= config['minimum_fps']
    if config.get('maximum_gap_ms') is not None:
        checks['explicit_maximum_arrival_gap'] = bool(gaps) and max(gaps) <= config['maximum_gap_ms']
    if config['require_exact_size']:
        checks['explicit_frame_size'] = set(shapes) == {f"{config['width']}x{config['height']}"}
    if config['backend'] == 'realsense':
        checks['aligned_depth_received'] = len(depth_coverage) == len(frame_times) and bool(depth_coverage) and min(depth_coverage) > 0
    for name in config['required_control']:
        value = controls.get(name, {})
        checks[f'explicit_control:{name}'] = value.get('applied') is True and value.get('readback_matches') is not False
    passed = all(checks.values())
    report.update(
        status=('software_fixture_passed' if report['simulated'] else 'capture_checks_passed') if passed else 'capture_checks_failed',
        completed_at_utc=utc_now(), capture_duration_s=actual_duration,
        frames_received=len(frame_times), observed_delivery_fps=fps, actual_frame_shapes=dict(shapes),
        read_failures=read_failures, invalid_frames=invalid_frames, timestamp_errors=timestamp_errors,
        host_clock_errors=host_clock_errors,
        adapter_sequence_discontinuities=sequence_discontinuities,
        blocking_read_ms=summarize(read_times), host_arrival_gap_ms=summarize(gaps),
        host_receive_to_script_ms=summarize(ages), mean_luminance_u8=summarize(luminance),
        dark_clipped_fraction=summarize(dark_clipping), bright_clipped_fraction=summarize(bright_clipping),
        valid_depth_fraction=summarize(depth_coverage), requested_controls_unconfirmed=unconfirmed,
        checks=checks, close_error=close_error,
        limitations=[
            'Passing these checks establishes bounded capture behavior, not human gaze or pupil accuracy.',
            'Arrival gaps and adapter sequences do not measure sensor or SDK frame loss.',
            'Blocking read and receive-to-script timings do not measure exposure-to-display latency.',
            'Global shutter, optical focus, infrared illumination and anatomical eye geometry require separate physical validation.',
            'Only explicitly supplied throughput, gap, frame-size and control thresholds are enforced.',
            'The physical-device flag is an operator attestation; a UVC index alone does not identify physical versus virtual camera hardware.',
        ],
    )
    write_json(checkpoint, report)
    return 0 if passed else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', required=True, choices=('auto', 'dshow', 'msmf', 'v4l2', 'pylon', 'realsense', 'synthetic'))
    parser.add_argument('--source', default='0', help='UVC device index or explicit industrial camera serial')
    parser.add_argument('--seconds', type=float, default=30)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--width', type=int, default=1280)
    parser.add_argument('--height', type=int, default=720)
    parser.add_argument('--fps', type=int, default=30)
    parser.add_argument('--exposure-native', type=float)
    parser.add_argument('--exposure-us', type=float)
    parser.add_argument('--gain', type=float)
    parser.add_argument('--ir-bit-depth', type=int, choices=(8, 10, 12, 14, 16))
    parser.add_argument('--pixel-format')
    parser.add_argument('--fourcc')
    parser.add_argument('--minimum-fps', type=float)
    parser.add_argument('--maximum-gap-ms', type=float)
    parser.add_argument('--max-read-failures', type=int, default=0)
    parser.add_argument('--require-exact-size', action='store_true')
    parser.add_argument('--required-control', action='append', default=[])
    parser.add_argument('--allow-simulated', action='store_true', help='Software fixture only; explicitly prevents a physical acceptance claim')
    parser.add_argument('--physical-device-confirmed', action='store_true', help='Operator has matched the selected device/index/serial to the actual physical camera; never overrides simulated metadata')
    parser.add_argument('--startup-grace-seconds', type=float, default=20)
    parser.add_argument('--shutdown-grace-seconds', type=float, default=10)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ['_worker']:
        worker = argparse.ArgumentParser()
        worker.add_argument('_worker')
        worker.add_argument('--config', required=True, type=Path)
        worker.add_argument('--checkpoint', required=True, type=Path)
        options = worker.parse_args(arguments)
        return run_worker(json.loads(options.config.read_text(encoding='utf-8')), options.checkpoint)
    parser = build_parser()
    args = parser.parse_args(arguments)
    for name, minimum, maximum in [('seconds', 1, 300), ('startup_grace_seconds', 1, 60), ('shutdown_grace_seconds', 1, 30)]:
        value = getattr(args, name)
        if not math.isfinite(value) or not minimum <= value <= maximum:
            parser.error(f'{name} must be finite and between {minimum} and {maximum}')
    if min(args.width, args.height, args.fps) <= 0 or args.max_read_failures < 0:
        parser.error('Dimensions/FPS must be positive and max-read-failures must be nonnegative')
    for name in ('minimum_fps', 'maximum_gap_ms', 'exposure_us'):
        value = getattr(args, name)
        if value is not None and (not math.isfinite(value) or value <= 0):
            parser.error(f'{name} must be finite and positive')
    for name in ('exposure_native', 'gain'):
        value = getattr(args, name)
        if value is not None and not math.isfinite(value):
            parser.error(f'{name} must be finite')
    if args.backend in ('pylon', 'realsense') and args.source in ('0', 'first', ''):
        parser.error('An explicit camera serial is required for industrial-camera acceptance')
    if args.backend not in ('pylon', 'realsense', 'synthetic') and not args.source.isdecimal():
        parser.error('UVC acceptance requires a device index; files and network streams are not accepted')
    if args.backend == 'synthetic' and not args.allow_simulated:
        parser.error('Synthetic capture requires --allow-simulated and is never physical acceptance')
    if args.fourcc is not None and len(args.fourcc) != 4:
        parser.error('fourcc must contain four characters')
    output = args.output.resolve()
    if output.exists():
        parser.error(f'Output already exists; choose a new receipt path: {output}')
    output.parent.mkdir(parents=True, exist_ok=True)
    config = vars(args).copy()
    config['output'] = str(output)
    config['timing_csv'] = str(output.with_suffix('.frames.csv'))
    config_path = output.with_suffix('.configuration.json')
    checkpoint = output.with_suffix('.partial.json')
    log_path = output.with_suffix('.worker.log')
    write_json(config_path, config)
    if getattr(sys, 'frozen', False):
        command = [sys.executable, 'camera-soak', '_worker']
    else:
        command = [sys.executable, str(Path(__file__).resolve()), '_worker']
    command += ['--config', str(config_path), '--checkpoint', str(checkpoint)]
    timeout = args.seconds + args.startup_grace_seconds + args.shutdown_grace_seconds
    started = host_now_s()
    with log_path.open('w', encoding='utf-8') as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        code, terminated = wait_bounded(process, timeout)
    try:
        report = json.loads(checkpoint.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        report = {'schema_version': 1, 'status': 'worker_failed_before_receipt', 'physical_hardware_tested': False,
                  'camera_opened': False, 'frames_received': 0, 'clean_shutdown': False, 'configuration': config}
    report.update(worker_exit_code=code, worker_forced_termination=terminated,
                  bounded_total_seconds=timeout, actual_command_duration_s=host_now_s() - started,
                  worker_log=log_path.name, timing_csv=Path(config['timing_csv']).name,
                  real_person_gaze_accuracy_established=False, images_saved=False)
    if terminated:
        report.update(status='driver_or_worker_timeout', clean_shutdown=False,
                      error='The bounded worker was terminated; successful capture/clean shutdown is not established')
    elif code and report.get('status') not in ('capture_checks_failed',):
        report['status'] = 'worker_failed'
    write_json(output, report)
    print(json.dumps({'status': report['status'], 'receipt': str(output), 'frames_received': report.get('frames_received', 0),
                      'physical_hardware_tested': report.get('physical_hardware_tested', False),
                      'worker_forced_termination': terminated}, indent=2))
    return 0 if code == 0 and not terminated else 2


if __name__ == '__main__':
    raise SystemExit(main())
