"""Capture the actual native application running an explicitly synthetic source.

No screenshots are painted separately or reconstructed from design mockups.
The GIF consists of successive QWidget.grab() frames from the running app.
"""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
from io import BytesIO
import json
import os
from pathlib import Path
import socket
import tempfile
import time
import urllib.request

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QBuffer,QIODevice
from PySide6.QtWidgets import QApplication
from PIL import Image
from gazespace.ui.main_window import MainWindow
from gazespace.sessions import load_session


def pump(app,condition=lambda:False,timeout=0.2,required=False):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        app.processEvents()
        if condition():return
        time.sleep(.01)
    if required:raise AssertionError('Native application did not reach the expected state')


def gif_frame(window):
    buffer=QBuffer();buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert window.grab().save(buffer,'PNG')
    return Image.open(BytesIO(bytes(buffer.data()))).convert('RGB')


def capture(output,workspace):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    app=QApplication.instance() or QApplication([])
    window=MainWindow(workspace=workspace,interactive_errors=False)
    errors=[];window.error_reported.connect(errors.append)
    window.resize(1440,1000);window.show();app.processEvents()
    images=[];screenshots=[]
    try:
        window.open_demo()
        pump(app,lambda:window.runtime is not None and
             bool((window.snapshot().get('latest') or {}).get('spatial_valid')) and not window._jobs,
             timeout=20,required=True)
        window.record_video.setChecked(True);window.toggle_record()
        assert window.recorder is not None
        session_path=window.recorder.path
        for index in range(36):
            if index%12==0:
                window.runtime.set_synthetic_target(*[(.24,.30),(.74,.65),(.48,.45)][index//12])
            pump(app,timeout=1/6)
            images.append(gif_frame(window))
        assert window.grab().save(str(output/'desktop_live.png'))
        screenshots.append('desktop_live.png')
        window.stop_recording()
        session=load_session(session_path)
        assert len(session.records)>90 and any(r.get('spatial_valid') for r in session.records)
        window.load_session_path(session_path);window.add_aoi();window.analyze_current_session()
        window.export_session_report();pump(app,lambda:not window._jobs,timeout=10,required=True)
        for page,name in ((1,'cameras'),(2,'calibration'),(3,'workspace'),(4,'sessions'),(5,'diagnostics')):
            window.set_page(page);pump(app,timeout=.25)
            if page==3:
                window.world_view.yaw+=.18;window.world_view.update();app.processEvents()
            if page==4:
                window.replay_slider.setValue(round(window.replay.duration_s*500));pump(app,timeout=.2)
            filename=f'desktop_{name}.png'
            assert window.grab().save(str(output/filename));screenshots.append(filename)
        # Open the real local ASGI server, authenticate and consume its current
        # calibrated gaze. The receipt never includes its short-lived token.
        with socket.socket() as probe:
            probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
        window.port_spin.setValue(port);window.toggle_api()
        pump(app,lambda:window._api_server is not None and window._api_server.started,
             timeout=8,required=True)
        request=urllib.request.Request(f'http://127.0.0.1:{port}/v1/gaze',
                   headers={'Authorization':'Bearer '+window._api_token})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=3) as response:
            payload=json.loads(response.read());http_status=response.status
        window.set_page(6);pump(app,timeout=.2)
        assert window.grab().save(str(output/'desktop_integration.png'))
        screenshots.append('desktop_integration.png')
        runtime=window.runtime.telemetry()
        window.stop_api();window.export_diagnostics()
        assert not errors,errors
        images[0].save(output/'desktop_live.gif',save_all=True,append_images=images[1:],
                       duration=167,loop=0,optimize=True)
        report={'status':'passed','verified_at_utc':datetime.now(timezone.utc).isoformat(),
            'rendering':'Actual PySide6 widgets, native offscreen rendering; screenshots captured with QWidget.grab',
            'source':'Generated pupil camera fixture processed by the actual detector, screen fit and spatial fit',
            'synthetic':True,'physical_hardware_validated':False,'human_gaze_accuracy_validated':False,
            'window_size':[1440,1000],'pages_captured':7,'screenshots':screenshots,
            'gif':{'path':'desktop_live.gif','frame_count':len(images),'interval_ms':167},
            'session_records':len(session.records),'recorded_video_frames':session.manifest['counts']['video_frames'] if 'counts' in session.manifest else session.manifest.get('video_frames'),
            'session_path':str(session_path),'native_ui_errors':errors,
            'real_loopback_http_status':http_status,'real_loopback_gaze_response':payload,
            'runtime_metrics':runtime['metrics']}
        (output/'desktop_verification.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
        return report
    finally:
        window.stop_tracking();window.close();app.processEvents()
        window.pool.waitForDone(5000)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True);parser.add_argument('--workspace')
    args=parser.parse_args()
    if args.workspace:
        result=capture(args.output,args.workspace)
    else:
        with tempfile.TemporaryDirectory(prefix='gazespace-evidence-') as workspace:
            result=capture(args.output,workspace)
    print(json.dumps(result,indent=2,allow_nan=False))
