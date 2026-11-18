import cv2
import numpy as np
import pytest
from gazespace.cli import main


def test_synthetic_ir_video_through_cli(tmp_path,capsys):
    video=str(tmp_path/"eyes.avi")
    writer=cv2.VideoWriter(video,cv2.VideoWriter_fourcc(*"MJPG"),20,(640,360))
    if not writer.isOpened():
        pytest.skip("host lacks MJPG video encoder")
    for frame_i in range(22):
        frame=np.full((360,640,3),180,np.uint8)
        for x in (220,420):
            cv2.circle(frame,(x+frame_i%5,180),16,(10,10,10),-1)
        writer.write(frame)
    writer.release()
    retval=main(["track","--source",video,"--backend","ffmpeg","--mode","ir-roi",
                 "--roi-right","0.25,0.4,0.19,0.22",
                 "--roi-left","0.57,0.4,0.19,0.22",
                 "--no-preview","--max-frames","20","--print-every","10"])
    out=capsys.readouterr().out
    assert retval==0
    assert '"frames": 20' in out
    assert '"right"' in out and '"left"' in out
    assert '"valid": false' in out  # important: no calibration, no invented gaze
