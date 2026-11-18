"""Additional command workflows used by packaging and reproducible verification."""
from __future__ import annotations
from pathlib import Path
import json
import time


def command_desktop(args):
    from .desktop import launch
    return launch(args.workspace,args.profile,args.demo)


def command_simulate(args):
    from .demo import build_demo_profile, DEMO_SOURCE_CONTEXT, SyntheticDemoEngine
    from .capture import SyntheticEyeSource
    from .engine import GazeEngine
    from .sessions import SessionRecorder
    from .analytics import export_report
    if args.seconds<=0 or args.seconds>3600:raise ValueError('Simulation duration must be in (0,3600] seconds')
    output=Path(args.output).resolve();output.mkdir(parents=True,exist_ok=True)
    profile_path=output/'synthetic_profile.json'
    profile=build_demo_profile(profile_path)
    source=SyntheticEyeSource(fps=30,duration_s=args.seconds,realtime=False)
    engine=SyntheticDemoEngine.from_profile(profile_path,mode='ir-roi',roi_normalized=source.roi_normalized,
                                   source_context=DEMO_SOURCE_CONTEXT)
    start=0.0;elapsed=[0.0]
    recorder=SessionRecorder(output/'sessions',metadata={'provenance':'synthetic','synthetic':True,
                 'screen_size':[1920,1080],'purpose':'rendered pupil image -> detection -> calibrated coordinates -> replay/report'},
                 record_video=args.record_video,clock=lambda:elapsed[0])
    last=None
    try:
        while True:
            frame=source.read()
            if frame is None:break
            elapsed[0]=(frame.sequence-1)/30
            last=engine.process_frame(frame);recorder.append(last,frame.image)
        path=recorder.close()
        paths=export_report(path,output/'report')
        report={'synthetic':True,'human_gaze_accuracy_measured':False,'session':str(path),
                'profile':str(profile_path),'measurements':profile['screen_validation'],
                'runtime':engine.metrics(),'exports':{k:str(v) for k,v in paths.items()}}
        (output/'simulation_summary.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(report,indent=2))
    finally:
        recorder.close();engine.close();source.close()
    return 0


def command_report(args):
    from .analytics import export_report
    aois=json.loads(Path(args.aois).read_text()) if args.aois else None
    paths=export_report(args.session,args.output,aois=aois)
    print(json.dumps({k:str(v) for k,v in paths.items()},indent=2));return 0


def command_replay(args):
    from .sessions import load_session,SessionReplay
    session=load_session(args.session)
    if args.no_preview:
        from .analytics import analyze_session
        report=analyze_session(session.records,screen_size=session.manifest.get('metadata',{}).get('screen_size'))
        print(json.dumps({k:v for k,v in report.items() if k!='heatmap'},indent=2));return 0
    from PySide6.QtWidgets import QApplication
    from .ui.main_window import MainWindow
    app=QApplication.instance() or QApplication([])
    window=MainWindow(workspace=args.workspace)
    window.load_session_path(args.session);window.set_page(4);window.show()
    return app.exec()


def command_board(args):
    from .validation import write_checkerboard_svg
    path=write_checkerboard_svg(args.output,args.cols,args.rows,args.square_mm)
    print(json.dumps({'output':str(path),'print_scale':'100%; measure actual square size after printing'}));return 0


def command_validate_camera(args):
    from .calibration import load_profile,update_profile
    from .types import CameraIntrinsics
    from .validation import validate_camera_images
    from .cli import load_images
    profile=load_profile(args.profile)
    intr=CameraIntrinsics.from_dict(profile['intrinsics'])
    train=profile.get('camera_calibration_evidence',{})
    report=validate_camera_images(load_images(args.images),intr,cols=args.cols,rows=args.rows,square_mm=args.square_mm,
                                  training_hashes=train.get('image_sha256'),min_views=3)
    update_profile(args.profile,camera_validation=report)
    print(json.dumps(report,indent=2));return 0


def command_triangulate(args):
    import numpy as np
    from .calibration import load_profile,load_json,save_json
    from .types import CameraIntrinsics
    from .stereo import triangulate_points_checked
    left=CameraIntrinsics.from_dict(load_profile(args.left_profile)['intrinsics'])
    right=CameraIntrinsics.from_dict(load_profile(args.right_profile)['intrinsics'])
    rig=load_json(args.rig);points=load_json(args.observations)
    result=triangulate_points_checked(np.asarray(points['left_px']),np.asarray(points['right_px']),left,right,
                                     np.asarray(rig['R_right_from_left']),np.asarray(rig['T_right_from_left_m']))
    payload=result.to_dict();save_json(args.output,payload);print(json.dumps(payload,indent=2));return 0


def add_commands(subparsers):
    s=subparsers.add_parser('desktop',help='Open the complete native desktop workspace')
    s.add_argument('--workspace');s.add_argument('--profile');s.add_argument('--demo',action='store_true');s.set_defaults(func=command_desktop)
    s=subparsers.add_parser('simulate',help='Run generated pupil images through calibration, tracking, recording and reporting')
    s.add_argument('--output',required=True);s.add_argument('--seconds',type=float,default=5);s.add_argument('--record-video',action='store_true');s.set_defaults(func=command_simulate)
    s=subparsers.add_parser('report',help='Export an offline session HTML/JSON/CSV report')
    s.add_argument('--session',required=True);s.add_argument('--output',required=True);s.add_argument('--aois');s.set_defaults(func=command_report)
    s=subparsers.add_parser('replay',help='Replay and analyze a recorded local session')
    s.add_argument('--session',required=True);s.add_argument('--no-preview',action='store_true');s.add_argument('--workspace');s.set_defaults(func=command_replay)
    s=subparsers.add_parser('generate-board',help='Create an exact-size printable checkerboard SVG')
    s.add_argument('--output',required=True);s.add_argument('--cols',type=int,default=9);s.add_argument('--rows',type=int,default=6);s.add_argument('--square-mm',type=float,default=24);s.set_defaults(func=command_board)
    s=subparsers.add_parser('validate-camera',help='Evaluate fixed intrinsics on fresh held-out board images')
    s.add_argument('--images',required=True);s.add_argument('--profile',required=True);s.add_argument('--cols',type=int,default=9);s.add_argument('--rows',type=int,default=6);s.add_argument('--square-mm',type=float,default=24);s.set_defaults(func=command_validate_camera)
    s=subparsers.add_parser('triangulate',help='Reconstruct matched image points using a measured stereo rig')
    for key in ('left-profile','right-profile','rig','observations','output'):s.add_argument('--'+key,required=True)
    s.set_defaults(func=command_triangulate)
