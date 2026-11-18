"""Collect dependency, active Qt display and SDK inventory without acquisition."""
from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import os
from pathlib import Path
import platform
import sys
import time

from camera_acceptance import file_hash, utc_now, write_json


def probe(output: Path, sdk_inventory: str = 'all') -> dict:
    report = {
        'schema_version': 1, 'status': 'collecting', 'started_at_utc': utc_now(),
        'platform': platform.platform(), 'python': platform.python_version(),
        'executable': sys.executable, 'executable_sha256': file_hash(Path(sys.executable)),
        'frozen_executable': bool(getattr(sys, 'frozen', False)),
        'physical_hardware_tested': False, 'camera_acquisition_attempted': False,
        'environment': {name: os.environ.get(name) for name in
                        ('SESSIONNAME', 'QT_QPA_PLATFORM', 'QT_SCALE_FACTOR', 'QT_SCREEN_SCALE_FACTORS', 'PYLON_CAMEMU')},
        'imports': {}, 'displays': [], 'sdk_inventory': {},
    }
    write_json(output, report)
    required = {'numpy': 'numpy', 'cv2': 'opencv-contrib-python', 'scipy': 'scipy',
                'PySide6': 'PySide6', 'mediapipe': 'mediapipe', 'fastapi': 'fastapi'}
    optional = {'pypylon': 'pypylon', 'pyrealsense2': 'pyrealsense2'}
    for module, distribution in {**required, **optional}.items():
        report['active_step'] = f'import:{module}'
        write_json(output, report)
        item = {'required': module in required, 'imported': False, 'distribution_version': None}
        try:
            imported = importlib.import_module(module)
            item['imported'] = True
            item['module_version'] = str(getattr(imported, '__version__', 'not exposed'))
            try:
                item['distribution_version'] = importlib.metadata.version(distribution)
            except importlib.metadata.PackageNotFoundError:
                item['metadata_note'] = 'Distribution metadata unavailable in this runtime; successful module import is recorded separately'
        except Exception as exc:
            item['error'] = f'{type(exc).__name__}: {exc}'
        report['imports'][module] = item
        write_json(output, report)
    report['required_imports_ok'] = all(report['imports'][name]['imported'] for name in required)

    report['active_step'] = 'active_qt_display_inventory'
    write_json(output, report)
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv[:1])
        report['qt_platform'] = app.platformName()
        report['qt_scale_factor_rounding_policy'] = str(app.highDpiScaleFactorRoundingPolicy())
        report['native_windows_display_context'] = sys.platform == 'win32' and app.platformName() == 'windows'
        for screen in app.screens():
            geometry, available = screen.geometry(), screen.availableGeometry()
            physical = screen.physicalSize()
            report['displays'].append({
                'name': screen.name(), 'manufacturer': screen.manufacturer(), 'model': screen.model(),
                'serial_reported': screen.serialNumber(), 'primary': screen == app.primaryScreen(),
                'qt_logical_geometry': {'x': geometry.x(), 'y': geometry.y(), 'width': geometry.width(), 'height': geometry.height()},
                'qt_available_geometry': {'x': available.x(), 'y': available.y(), 'width': available.width(), 'height': available.height()},
                'device_pixel_ratio': float(screen.devicePixelRatio()),
                'logical_dpi_x': float(screen.logicalDotsPerInchX()), 'logical_dpi_y': float(screen.logicalDotsPerInchY()),
                'physical_dpi_x_reported': float(screen.physicalDotsPerInchX()), 'physical_dpi_y_reported': float(screen.physicalDotsPerInchY()),
                'physical_size_mm_reported': [float(physical.width()), float(physical.height())],
                'refresh_hz_reported': float(screen.refreshRate()),
            })
        report['display_inventory_ok'] = bool(report['displays'])
    except Exception as exc:
        report['display_inventory_ok'] = False
        report['display_error'] = f'{type(exc).__name__}: {exc}'
    if sys.platform == 'win32':
        try:
            import ctypes
            report['win32_sm_remotesession'] = bool(ctypes.windll.user32.GetSystemMetrics(0x1000))
        except Exception as exc:
            report['remote_session_probe_error'] = f'{type(exc).__name__}: {exc}'
    report['display_measurement_note'] = 'Qt-reported values for the current process/session. Virtual or remote displays and EDID dimensions are not independent measurements of a physical monitor.'
    write_json(output, report)

    if sdk_inventory in ('basler', 'all'):
        report['active_step'] = 'basler_device_enumeration'
        write_json(output, report)
        started = time.monotonic()
        try:
            from pypylon import pylon
            devices = pylon.TlFactory.GetInstance().EnumerateDevices()
            data = [{'serial': str(d.GetSerialNumber()), 'model': d.GetModelName(), 'vendor': d.GetVendorName(),
                     'device_class': d.GetDeviceClass(), 'sdk_emulator': d.GetDeviceClass() == 'BaslerCamEmu'} for d in devices]
            report['sdk_inventory']['basler'] = {'status': 'enumerated', 'device_count': len(data), 'devices': data}
        except Exception as exc:
            report['sdk_inventory']['basler'] = {'status': 'unavailable', 'device_count': None, 'error': f'{type(exc).__name__}: {exc}'}
        report['sdk_inventory']['basler']['duration_s'] = time.monotonic() - started
        write_json(output, report)
    if sdk_inventory in ('realsense', 'all'):
        report['active_step'] = 'realsense_device_enumeration'
        write_json(output, report)
        started = time.monotonic()
        try:
            import pyrealsense2 as rs
            context = rs.context()
            devices = context.query_devices()
            data = []
            for device in devices:
                item = {}
                for field in ('name', 'serial_number', 'firmware_version', 'usb_type_descriptor'):
                    key = getattr(rs.camera_info, field)
                    if device.supports(key):
                        item[field] = device.get_info(key)
                data.append(item)
            report['sdk_inventory']['realsense'] = {'status': 'enumerated', 'device_count': len(data), 'devices': data}
        except Exception as exc:
            report['sdk_inventory']['realsense'] = {'status': 'unavailable', 'device_count': None, 'error': f'{type(exc).__name__}: {exc}'}
        report['sdk_inventory']['realsense']['duration_s'] = time.monotonic() - started
        write_json(output, report)
    report.update(status='collected', active_step='complete', completed_at_utc=utc_now())
    write_json(output, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--sdk-inventory', choices=('none', 'basler', 'realsense', 'all'), default='all')
    args = parser.parse_args(argv)
    output = args.output.resolve()
    if output.exists():
        parser.error('Choose a new receipt path; an existing measurement will not be overwritten')
    output.parent.mkdir(parents=True, exist_ok=True)
    report = probe(output, args.sdk_inventory)
    print(f"Environment inventory: {report['status']}; receipt: {output}")
    return 0 if report['required_imports_ok'] and report['display_inventory_ok'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
