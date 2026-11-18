"""GazeSpace command-line and Windows-facing app entrypoints."""
from __future__ import annotations
import argparse
import importlib.util
import json
import platform
from pathlib import Path
import sys
import time
import numpy as np
import cv2
from . import __version__
from .capture import create_source, list_cameras, list_basler_cameras
from .types import CameraIntrinsics, CameraExtrinsics, Surface
from .calibration import (GazeModel, calibrate_intrinsics, solve_world_board_extrinsics,
                           calibrate_stereo, fit_gaze_model, load_profile,
                           update_profile, new_profile, save_json)
from .engine import GazeEngine, draw_diagnostics
from .vision import GazeTracker
from .workflows import ScreenSession, spatial_session


def json_print(obj):
    print(json.dumps(obj, indent=2, allow_nan=False))


def parse_roi(value: str):
    data=tuple(float(x) for x in value.split(","))
    if len(data)!=4 or not np.all(np.isfinite(data)):
        raise argparse.ArgumentTypeError("ROI must be normalized x,y,w,h")
    x,y,w,h=data
    if x<0 or y<0 or w<=0 or h<=0 or x+w>1 or y+h>1:
        raise argparse.ArgumentTypeError("ROI must fit in normalized [0,1] frame")
    return data


def add_camera_args(p):
    p.add_argument("--source",default="0",help="USB index (e.g. 0), video file, or Basler serial")
    p.add_argument("--backend",choices=("auto","dshow","msmf","v4l2","ffmpeg","pylon","realsense","synthetic"),default="auto")
    p.add_argument("--width",type=int,default=1280)
    p.add_argument("--height",type=int,default=720)
    p.add_argument("--fps",type=int,default=30)
    p.add_argument("--exposure-native",type=float,help="DirectShow/MSMF-specific exposure value, not microseconds")
    p.add_argument("--exposure-us",type=float,help="Basler/pypylon ExposureTime in microseconds")
    p.add_argument("--gain",type=float)
    p.add_argument("--ir-bit-depth",type=int,choices=(8,10,12,14,16))


def add_tracking_args(p):
    add_camera_args(p)
    p.add_argument("--profile",help="JSON calibration profile")
    p.add_argument("--subject-id",default=None,help="Explicit current operator identity for profile checks")
    p.add_argument("--mode",choices=("rgb","ir","ir-roi","preview"),default="rgb")
    p.add_argument("--face-model",help="Optional MediaPipe Tasks face_landmarker.task model")
    p.add_argument("--roi-right",type=parse_roi)
    p.add_argument("--roi-left",type=parse_roi)
    p.add_argument("--roi-file",help="JSON with normalized right/left eye ROIs from select-rois")
    p.add_argument("--pupil-polarity",choices=("dark","bright"),default="dark")


def make_source(args):
    return create_source(args.source,backend=args.backend,width=args.width,height=args.height,
                         fps=args.fps,exposure_native=args.exposure_native,
                         exposure_us=args.exposure_us,gain_native=args.gain,
                         gain=args.gain,ir_bit_depth=args.ir_bit_depth,pupil_polarity=getattr(args,"pupil_polarity","dark"))


def make_rois(args):
    rois={}
    if args.roi_file:
        raw=json.loads(Path(args.roi_file).read_text(encoding="utf-8"))
        rois={k:parse_roi(",".join(str(v) for v in raw[k])) for k in ("right","left")}
    rois.update({side:value for side,value in (("right",args.roi_right),("left",args.roi_left)) if value})
    return rois


def verify_camera_profile_source(args):
    if not args.profile or not Path(args.profile).exists():
        return
    p=load_profile(args.profile)
    # Host apps using GazeEngine.from_profile are responsible for this hardware binding.
    if p.get("camera_id") and str(args.source) != str(p["camera_id"]):
        raise ValueError(f"profile bound to camera/source {p['camera_id']!r}, not {args.source!r}; recalibrate")


def source_context(args):
    context={"camera_id":str(args.source),"backend":args.backend,
             "exposure_native":args.exposure_native,"exposure_us":args.exposure_us,
             "gain":args.gain,"ir_bit_depth":args.ir_bit_depth or 8}
    if getattr(args,"subject_id",None):context["subject_id"]=args.subject_id
    return context


def make_tracker(args):
    if args.mode=="preview":
        from .preview import PreviewTracker
        return PreviewTracker()
    intr=None
    if getattr(args,"profile",None) and Path(args.profile).exists():
        p=load_profile(args.profile)
        if p.get("intrinsics"):
            intr=CameraIntrinsics.from_dict(p["intrinsics"])
    return GazeTracker(intr,mode=args.mode,face_model=args.face_model,
                       roi_normalized=make_rois(args) or None,pupil_polarity=args.pupil_polarity)


def cmd_doctor(args):
    import os
    payload={"name":"GazeSpace","version":__version__,"os":platform.platform(),
             "python":sys.version.split()[0], "opencv":cv2.__version__,
             "libraries":{k:bool(importlib.util.find_spec(k)) for k in
                          ("numpy","cv2","mediapipe","scipy","PIL","pypylon","pyrealsense2","PySide6","fastapi","uvicorn")},
             "python_supported":(3,11)<=sys.version_info[:2]<(3,13),
             "physical_camera_tested":False,
             "security":"camera data stays local; recording is explicit opt-in"}
    json_print(payload)


def cmd_cameras(args):
    json_print({"uvc":list_cameras(args.max_index,args.backend),"basler":list_basler_cameras()})


def cmd_track(args):
    verify_camera_profile_source(args)
    engine=(GazeEngine.from_profile(args.profile,mode=args.mode,face_model=args.face_model,
             roi_normalized=make_rois(args) or None,pupil_polarity=args.pupil_polarity,source_context=source_context(args))
            if args.profile and Path(args.profile).exists() and args.mode!="preview"
            else GazeEngine(make_tracker(args)))
    stream=None
    count=0
    started=time.monotonic()
    try:
        if args.record:
            dest=Path(args.record)
            dest.parent.mkdir(parents=True,exist_ok=True)
            stream=dest.open("a",encoding="utf-8")
        with make_source(args) as camera:
            if hasattr(camera,"capabilities"):
                json_print({"camera_report":camera.capabilities()})
            print("Press Q or ESC to quit; raw camera frames are not saved.")
            while True:
                frame=camera.read()
                if frame is None:
                    print("End of video or camera frame unavailable")
                    break
                result=engine.process_frame(frame)
                count+=1
                if stream:
                    stream.write(json.dumps(result.as_dict(),allow_nan=False)+"\n")
                    stream.flush()
                if args.no_preview:
                    if count%max(1,args.print_every)==0:
                        json_print(result.as_dict())
                    if args.max_frames and count>=args.max_frames:
                        break
                else:
                    fps=count/max(1e-4,time.monotonic()-started)
                    rendered=draw_diagnostics(frame.image,result,engine.last_observation,fps)
                    cv2.imshow("GazeSpace | calibration-aware tracking",rendered)
                    if cv2.waitKey(1)&0xff in (27,ord("q")):
                        break
                    if args.max_frames and count>=args.max_frames:
                        break
    finally:
        if stream:
            stream.close()
        if not args.no_preview:
            cv2.destroyAllWindows()
        json_print({"summary":engine.metrics()})
        engine.close()


def cmd_api(args):
    verify_camera_profile_source(args)
    if args.host not in ("127.0.0.1","localhost","::1"):
        raise ValueError("API only binds to loopback; use an authenticated gateway for remote deployment")
    import uvicorn
    from .service import GazeService, create_api
    def factory():
        if args.profile and args.mode!="preview":
            return GazeEngine.from_profile(args.profile,mode=args.mode,face_model=args.face_model,
                    roi_normalized=make_rois(args) or None,pupil_polarity=args.pupil_polarity,
                    source_context=source_context(args))
        return GazeEngine(make_tracker(args))
    service=GazeService(None,lambda:make_source(args),args.record,engine_factory=factory)
    app=create_api(service,token=args.token,manage_lifecycle=True)
    uvicorn.run(app,host=args.host,port=args.port,log_level="info")


def cmd_screen(args):
    if args.mode=="preview":raise ValueError("Select eye tracking before gaze calibration")
    if args.backend in ("pylon","realsense") and str(args.source) in ("0","first"):
        raise ValueError("Use an explicit device serial for calibrated industrial-camera profiles")
    if args.command!="calibrate-screen":
        verify_camera_profile_source(args)
    with make_source(args) as source:
        tracker=make_tracker(args)
        session=ScreenSession(source,tracker,args.profile,args.samples,args.grid,
                              validate=args.command in ("validate-screen","drift-check"),camera_id=str(args.source),
                              drift=args.command=="drift-check")
        try:
            report=session.run()
        finally:
            tracker.close()
    if report is None:
        print("Calibration cancelled; previous profile unchanged")
    else:
        json_print(report)


def cmd_spatial(args):
    if args.mode=="preview":raise ValueError("Select eye tracking before gaze calibration")
    if args.backend in ("pylon","realsense") and str(args.source) in ("0","first"):
        raise ValueError("Use an explicit device serial for calibrated industrial-camera profiles")
    verify_camera_profile_source(args)
    with make_source(args) as source:
        tracker=make_tracker(args)
        try:
            report=spatial_session(source,tracker,args.profile,args.targets,args.samples,
                                   validate=args.command=="validate-spatial",
                                   require_multidepth=not args.allow_single_depth)
        finally:
            tracker.close()
    json_print(report)


def cmd_select_rois(args):
    with make_source(args) as source:
        frame=source.read()
        if frame is None:
            raise OSError("cannot read a frame for eye ROI selection")
        im=frame.image
        h,w=im.shape[:2]
        rois={}
        try:
            for side in ("right","left"):
                title=f"Select {side} eye | ENTER/SPACE confirm | C cancel"
                x,y,rw,rh=cv2.selectROI(title,im,showCrosshair=True,fromCenter=False)
                cv2.destroyWindow(title)
                if rw<12 or rh<12:
                    raise ValueError(f"ROI for {side} cancelled/too small")
                rois[side]=[x/w,y/h,rw/w,rh/h]
        finally:
            cv2.destroyAllWindows()
    save_json(args.output,rois)
    json_print({"saved":args.output,"normalized_rois":rois,
                "warning":"ROIs are static; moving heads require reliable facial landmarks or a vendor eye tracker"})


def cmd_capture_board(args):
    dst=Path(args.output)
    dst.mkdir(parents=True,exist_ok=True)
    print("Frame preview: SPACE saves board image; Q/ESC exits; vary tilt/position/distance")
    n=0
    with make_source(args) as source:
        while True:
            frame=source.read()
            if frame is None:
                break
            preview=frame.image.copy()
            cv2.putText(preview,"SPACE capture board | Q quit",(20,30),
                        cv2.FONT_HERSHEY_SIMPLEX,.7,(0,255,170),2)
            cv2.imshow("GazeSpace checkerboard capture",preview)
            key=cv2.waitKey(1)&0xff
            if key in (ord("q"),27):
                break
            if key==32:
                filepath=dst/f"board_{int(time.time()*1000)}_{n:03d}.png"
                if not cv2.imwrite(str(filepath),frame.image):
                    raise OSError("unable to save checkerboard frame")
                print(f"Saved {filepath}")
                n+=1
                if args.limit and n>=args.limit:
                    break
    cv2.destroyAllWindows()
    json_print({"saved":n,"path":str(dst)})


def load_images(path):
    root=Path(path)
    files=sorted(p for p in root.iterdir() if p.suffix.lower() in (".png",".jpg",".jpeg",".bmp",".tif",".tiff"))
    if not files:
        raise ValueError(f"no images in {root}")
    pairs=[(str(p),cv2.imread(str(p),cv2.IMREAD_COLOR)) for p in files]
    for name,img in pairs:
        if img is None:
            raise ValueError(f"unreadable image {name}")
    return [im for _,im in pairs]


def cmd_calibrate_camera(args):
    images=load_images(args.images)
    intr, report=calibrate_intrinsics(images,args.cols,args.rows,args.square_mm,min_views=args.min_views)
    from .validation import camera_training_evidence
    evidence=camera_training_evidence(images,cols=args.cols,rows=args.rows,square_mm=args.square_mm)
    update_profile(args.profile,intrinsics=intr.to_dict(),extrinsics=None,
                   screen_model=None,spatial_model=None,surfaces=[],camera_calibration_evidence=evidence)
    json_print({"intrinsics":intr.to_dict(),"report":report,
                "invalidated":"old gaze models and world calibration because camera intrinsics changed"})


def cmd_calibrate_world(args):
    profile=load_profile(args.profile)
    if not profile.get("intrinsics"):
        raise ValueError("calibrate intrinsics first")
    intr=CameraIntrinsics.from_dict(profile["intrinsics"])
    image=cv2.imread(args.image)
    if image is None:
        raise ValueError("cannot read board image")
    ext=solve_world_board_extrinsics(image,intr,args.cols,args.rows,args.square_mm)
    update_profile(args.profile,extrinsics=ext.to_dict(),screen_model=None,spatial_model=None,surfaces=[])
    json_print({"extrinsics":ext.to_dict(),
                "world_axes":"board first internal corner=origin, X across board, Y down board, Z right-handed",
                "invalidated":"old screen/spatial models and surfaces; camera geometry may have changed"})


def cmd_calibrate_stereo(args):
    left_p=load_profile(args.left_profile);right_p=load_profile(args.right_profile)
    left=CameraIntrinsics.from_dict(left_p["intrinsics"])
    right=CameraIntrinsics.from_dict(right_p["intrinsics"])
    ldir,rdir=Path(args.left_images),Path(args.right_images)
    allowed=(".png",".jpg",".jpeg",".bmp",".tif",".tiff")
    names=sorted({p.name for p in ldir.iterdir() if p.suffix.lower() in allowed} &
                 {p.name for p in rdir.iterdir() if p.suffix.lower() in allowed})
    if len(names)<args.min_pairs:
        raise ValueError("left/right image folders need >= min-pairs identical filenames")
    l=[cv2.imread(str(ldir/n)) for n in names]
    r=[cv2.imread(str(rdir/n)) for n in names]
    report=calibrate_stereo(l,r,left,right,args.cols,args.rows,args.square_mm,args.min_pairs)
    save_json(args.output,report)
    json_print(report)


def cmd_surfaces(args):
    file_data=json.loads(Path(args.surfaces).read_text(encoding="utf-8"))
    if not isinstance(file_data,list):
        raise ValueError("surfaces file should be JSON array")
    from .geometry import ray_surface_intersection
    surfaces=[Surface.from_dict(x) for x in file_data]
    names=set()
    for surf in surfaces:
        if surf.name in names:
            raise ValueError("duplicate surface name")
        names.add(surf.name)
        # geometrically validate axes and dimensions without needing actual ray hit
        u,v=np.array(surf.u_vector),np.array(surf.v_vector)
        if not np.isfinite(u).all() or not np.isfinite(v).all() or np.linalg.norm(np.cross(u,v))<1e-7:
            raise ValueError(f"invalid surface axes: {surf.name}")
        if surf.width_m<=0 or surf.height_m<=0:
            raise ValueError("surface dimensions must be positive")
    update_profile(args.profile,surfaces=[s.to_dict() for s in surfaces])
    json_print({"surfaces_saved":len(surfaces),"names":list(names)})


def cmd_demo(args):
    """Offline math/regression demo: synthetic, never described as real eye test."""
    rng=np.random.default_rng(28)
    samples=[]
    target_xy=[(x,y) for y in np.linspace(.1,.9,4) for x in np.linspace(.1,.9,4)]
    for i,(x,y) in enumerate(target_xy):
        for _ in range(20):
            noise=rng.normal(0,.015,4)
            feat=np.array([x,y,x,y,0,0,0,.5,.5,.6])
            feat[:4]+=noise
            samples.append({"features":feat.tolist(),"target":[float(x),float(y)],"target_id":str(i)})
    model=fit_gaze_model(samples,screen_size=(1920,1080))
    report={"demo":"SYNTHETIC math-only calibration (NOT a real accuracy claim)",
            "profile_schema":2,"model_report":model.report,
            "example_prediction_normalized":model.predict(np.array([.5,.5,.5,.5,0,0,0,.5,.5,.6])).tolist()}
    if args.output:
        save_json(args.output,report)
    json_print(report)


def build_parser():
    p=argparse.ArgumentParser(prog="gazespace",description="Windows eye/pupil tracking and calibrated workspace gaze")
    p.add_argument("--version",action="version",version=__version__)
    sp=p.add_subparsers(dest="command",required=True)
    s=sp.add_parser("doctor",help="Report dependency and platform readiness");s.set_defaults(func=cmd_doctor)
    s=sp.add_parser("cameras",help="Probe local UVC/USB and Basler cameras")
    s.add_argument("--max-index",type=int,default=6)
    s.add_argument("--backend",choices=("auto","dshow","msmf","v4l2"),default="auto")
    s.set_defaults(func=cmd_cameras)
    s=sp.add_parser("track",help="Run live calibrated gaze display or headless capture")
    add_tracking_args(s)
    s.add_argument("--record",help="Optional gaze JSONL log (no raw images)")
    s.add_argument("--no-preview",action="store_true")
    s.add_argument("--max-frames",type=int)
    s.add_argument("--print-every",type=int,default=30)
    s.set_defaults(func=cmd_track)
    s=sp.add_parser("api",help="Local-only gaze telemetry API and WebSocket")
    add_tracking_args(s)
    s.add_argument("--host",default="127.0.0.1")
    s.add_argument("--port",type=int,default=8765)
    s.add_argument("--record",help="Optional gaze metadata JSONL log")
    s.add_argument("--token",help="Optional bearer token of >=16 characters; keep it secret")
    s.set_defaults(func=cmd_api)
    for name in ("calibrate-screen","validate-screen","drift-check"):
        s=sp.add_parser(name,help="Fullscreen guided screen gaze session")
        add_tracking_args(s)
        s.get_default("profile")
        for action in s._actions:
            if action.dest == "profile": action.required = True
        s.add_argument("--grid",type=int,default=4)
        s.add_argument("--samples",type=int,default=16)
        s.set_defaults(func=cmd_screen)
    for name in ("calibrate-spatial","validate-spatial"):
        s=sp.add_parser(name,help="Physical 3D target ray-model session")
        add_tracking_args(s)
        for action in s._actions:
            if action.dest == "profile": action.required = True
        s.add_argument("--targets",required=True)
        s.add_argument("--samples",type=int,default=16)
        s.add_argument("--allow-single-depth",action="store_true",help="Only for planar experiments; not volumetric 3D")
        s.set_defaults(func=cmd_spatial)
    s=sp.add_parser("select-rois",help="Select two infrared eye ROIs with the mouse")
    add_camera_args(s)
    s.add_argument("--output",required=True)
    s.set_defaults(func=cmd_select_rois)
    s=sp.add_parser("capture-board",help="Capture images of chessboard at diverse positions")
    add_camera_args(s)
    s.add_argument("--output",required=True)
    s.add_argument("--limit",type=int,default=25)
    s.set_defaults(func=cmd_capture_board)
    s=sp.add_parser("calibrate-camera",help="Intrinsic camera calibration from chessboard images")
    s.add_argument("--images",required=True)
    s.add_argument("--profile",required=True)
    s.add_argument("--cols",type=int,default=9)
    s.add_argument("--rows",type=int,default=6)
    s.add_argument("--square-mm",type=float,default=24)
    s.add_argument("--min-views",type=int,default=8)
    s.set_defaults(func=cmd_calibrate_camera)
    s=sp.add_parser("calibrate-world",help="Attach world frame to a measured reference board")
    s.add_argument("--image",required=True)
    s.add_argument("--profile",required=True)
    s.add_argument("--cols",type=int,default=9)
    s.add_argument("--rows",type=int,default=6)
    s.add_argument("--square-mm",type=float,default=24)
    s.set_defaults(func=cmd_calibrate_world)
    s=sp.add_parser("calibrate-stereo",help="Paired left/right chessboard multi-camera calibration")
    for key in ("left-profile","right-profile","left-images","right-images","output"):
        s.add_argument("--"+key,required=True)
    s.add_argument("--cols",type=int,default=9)
    s.add_argument("--rows",type=int,default=6)
    s.add_argument("--square-mm",type=float,default=24)
    s.add_argument("--min-pairs",type=int,default=8)
    s.set_defaults(func=cmd_calibrate_stereo)
    s=sp.add_parser("set-surfaces",help="Install measured bounded workspace surfaces")
    s.add_argument("--profile",required=True)
    s.add_argument("--surfaces",required=True)
    s.set_defaults(func=cmd_surfaces)
    s=sp.add_parser("demo",help="Run deterministic SYNTHETIC calibration math demo")
    s.add_argument("--output")
    s.set_defaults(func=cmd_demo)
    from .commands import add_commands
    add_commands(sp)
    return p


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    if not arguments:arguments = ["desktop"]
    args=build_parser().parse_args(arguments)
    try:
        return args.func(args) or 0
    except (OSError,RuntimeError,ValueError,ImportError,KeyboardInterrupt) as exc:
        print(f"GazeSpace error: {type(exc).__name__}: {exc}",file=sys.stderr)
        return 2

if __name__=="__main__":
    sys.exit(main())
