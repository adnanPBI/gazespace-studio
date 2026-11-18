"""Exercise the real installed Basler binding with its official camera emulator.

Run with an explicit emulator opt-in; no physical device is selected or read.
Output proves SDK integration, not USB/IR/global-shutter hardware performance.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sys


def verify(output: str | None = None) -> dict:
    if os.environ.get('PYLON_CAMEMU') != '1':
        raise RuntimeError('Set PYLON_CAMEMU=1 before starting this fresh Python process')
    from pypylon import pylon
    import numpy as np
    from gazespace.capture import PylonSource
    devices = [d for d in pylon.TlFactory.GetInstance().EnumerateDevices()
               if d.GetDeviceClass() == 'BaslerCamEmu']
    if not devices:
        raise RuntimeError('Basler camera-emulation transport layer is unavailable')
    serial = str(devices[0].GetSerialNumber())
    runs=[]
    for pixel_format, bits in [('Mono8',None),('Mono12',12),('BGR8Packed',None)]:
        source=None
        record={'requested_pixel_format':pixel_format,'ir_bit_depth':bits}
        try:
            source=PylonSource(serial=serial,exposure_us=3000,gain=0,timeout_ms=1500,
                               pixel_format=pixel_format,ir_bit_depth=bits)
            record['capabilities']=source.capabilities()
            frames=[source.read() for _ in range(5)]
            assert all(f is not None and f.image.dtype==np.uint8 and f.image.ndim==3 and f.image.shape[2]==3 for f in frames)
            assert all(f.image.flags.owndata for f in frames)
            assert all(f.source==serial and f.metadata['sdk_emulator'] for f in frames)
            assert all(a.timestamp_s < b.timestamp_s for a,b in zip(frames,frames[1:]))
            assert [f.sequence for f in frames]==[1,2,3,4,5]
            for control in ('ExposureAuto','ExposureTime','GainAuto','Gain','PixelFormat'):
                assert source.controls[control]['applied'],source.controls[control]
            first_digest=hashlib.sha256(frames[0].image.tobytes()).hexdigest()
            # Retrieve another SDK buffer; the previously returned frame must
            # remain independently owned and unchanged after reuse/release.
            source.read()
            assert hashlib.sha256(frames[0].image.tobytes()).hexdigest()==first_digest
            record.update(frames_checked=len(frames),extra_buffer_reuse_frame=1,
                array_shape=list(frames[0].image.shape),array_dtype=str(frames[0].image.dtype),
                min_pixel=int(frames[0].image.min()),max_pixel=int(frames[0].image.max()),
                serial_preserved_as_string=all(isinstance(f.source,str) for f in frames),
                timestamps_strictly_increasing=True,buffers_owned_and_stable=True,
                first_frame_metadata=frames[0].metadata)
        finally:
            if source is not None:
                source.close()
                record['closed_and_not_grabbing']=(not source.cam.IsOpen() and not source.cam.IsGrabbing())
                source.close()
                record['idempotent_close']=True
        record['status']='passed'
        runs.append(record)
    report={'status':'passed','verification':'Real pypylon binding with official Basler camera emulator',
            'verified_at_utc':datetime.now(timezone.utc).isoformat(),
            'environment':{'python':platform.python_version(),'platform':platform.platform(),
                           'pypylon':importlib.metadata.version('pypylon'),'PYLON_CAMEMU':'1'},
            'device':{'serial':serial,'device_class':'BaslerCamEmu','model':devices[0].GetModelName()},
            'physical_hardware_tested':False,'real_eyes_or_gaze_accuracy_tested':False,
            'sdk_emulator_only':True,'runs':runs,
            'official_reference':'https://docs.baslerweb.com/camera-emulation',
            'limitations':['Camera emulator does not validate USB transport, optics, IR illumination, shutter characteristics or physical camera timing.',
                           'Host receive timestamps are monotonic; raw hardware ticks are preserved and not assumed to be synchronized.',
                           'Control availability and value ranges depend on the real camera model.'],
            'reproduce_powershell':['$env:PYLON_CAMEMU="1"','python scripts/verify_pypylon_emulator.py --output release_evidence/pypylon_sdk_emulator.json'],
            'reproduce_bash':'PYLON_CAMEMU=1 python scripts/verify_pypylon_emulator.py --output release_evidence/pypylon_sdk_emulator.json'}
    if output:
        path=Path(output);path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output')
    args=p.parse_args()
    try:
        report=verify(args.output)
    except Exception as exc:
        print(f'SDK emulator verification failed: {type(exc).__name__}: {exc}',file=sys.stderr)
        return 2
    print(json.dumps(report,indent=2,allow_nan=False))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
