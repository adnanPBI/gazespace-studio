"""Verify actual bundled face/iris inference on the attributed reference photo.

This is an inference integration check. The image has no gaze or pose ground
truth and cannot establish live-camera, head-angle or gaze accuracy.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import sys
import cv2
import numpy as np
from gazespace.vision import GazeTracker
from gazespace import __version__


def verify(image_path, output=None):
    image_path=Path(image_path)
    image=cv2.imread(str(image_path))
    if image is None:
        raise ValueError('Reference photograph could not be decoded')
    tracker=GazeTracker(mode='rgb')
    try:
        ob=tracker.process(image,1.0)
        if not ob.face_detected or len(ob.landmarks_px) != 478:
            raise RuntimeError('Reference photograph must produce a face with 478 refined facial/iris landmarks')
        if len(ob.pupils_px) != 2:
            raise RuntimeError('Reference photograph must produce both iris/pupil observations')
        if ob.features is None or not np.isfinite(ob.features).all():
            raise RuntimeError('Face inference did not produce finite tracking features')
        if ob.head_pose is None:
            raise RuntimeError('Face inference did not produce a head-pose estimate')
        pose=ob.head_pose
        report={'status':'passed','verified_at_utc':datetime.now(timezone.utc).isoformat(),
            'source':'NASA astronaut photograph distributed by scikit-image; still photo without gaze ground truth',
            'source_reference':'https://scikit-image.org/docs/stable/api/skimage.data.html#skimage.data.astronaut',
            'image_sha256':hashlib.sha256(image_path.read_bytes()).hexdigest(),
            'environment':{'python':platform.python_version(),'platform':platform.platform(),
                           'mediapipe':importlib.metadata.version('mediapipe'),
                           'application_version':__version__},
            'frozen_executable':bool(getattr(sys,'frozen',False)),
            'face_detected':ob.face_detected,'iris_count':len(ob.pupils_px),
            'landmark_count':len(ob.landmarks_px),'features_finite':bool(np.isfinite(ob.features).all()),
            'quality':ob.quality,'message':ob.message,
            'head_pose_estimate':{'yaw_deg':pose.yaw_deg,'pitch_deg':pose.pitch_deg,
                                  'roll_deg':pose.roll_deg,'reprojection_px':pose.reprojection_px},
            'hardware_validated':False,'gaze_accuracy_validated':False,'head_angle_accuracy_validated':False,
            'human_gaze_accuracy_measured':False}
        if output:
            path=Path(output);path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
        return report
    finally:
        tracker.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    root=Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[1]
    parser.add_argument('--image',default=str(root/'examples'/'reference_face_nasa.png'))
    parser.add_argument('--output')
    args=parser.parse_args(argv)
    try:
        print(json.dumps(verify(args.image,args.output),indent=2,allow_nan=False))
    except Exception as exc:
        print(f'Face/iris inference verification failed: {type(exc).__name__}: {exc}',file=sys.stderr)
        return 2
    return 0


if __name__=='__main__':
    raise SystemExit(main())
