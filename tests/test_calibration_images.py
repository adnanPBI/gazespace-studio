import cv2
import numpy as np
import pytest
from gazespace.calibration import find_board, calibrate_intrinsics, solve_world_board_extrinsics
from gazespace.types import CameraIntrinsics


def checkerboard_view(index=0,width=1200,height=800):
    # Warped raster test: validates board corner extraction but does not represent
    # independently measured physical camera geometry.
    sq=55
    board=np.full((7*sq+120,10*sq+120,3),255,np.uint8)
    for row in range(7):
        for col in range(10):
            if (row+col)%2==0:
                cv2.rectangle(board,(60+col*sq,60+row*sq),
                              (60+(col+1)*sq,60+(row+1)*sq),(0,0,0),-1)
    src=np.float32([[0,0],[board.shape[1]-1,0],[board.shape[1]-1,board.shape[0]-1],[0,board.shape[0]-1]])
    i=index
    dst=np.float32([[120+i*12,80+i*4],[880-i*9,100+11*i],
                    [920-i*8,675-i*5],[80+10*i,655-i*2]])
    matrix=cv2.getPerspectiveTransform(src,dst)
    return cv2.warpPerspective(board,matrix,(width,height),borderValue=(255,255,255))


def test_find_54_board_corners():
    found=find_board(checkerboard_view(),9,6)
    assert found is not None
    assert found.shape==(54,2)


def test_camera_intrinsics_accept_multiple_views():
    images=[checkerboard_view(i) for i in range(8)]
    intr,meta=calibrate_intrinsics(images,cols=9,rows=6,square_mm=24,min_views=8)
    assert intr.images_used==8
    assert intr.camera_matrix.shape==(3,3)
    assert np.isfinite(intr.camera_matrix).all()
    assert meta["views_rejected"]==0


def test_insufficient_board_views_rejected():
    with pytest.raises(ValueError,match="need >="):
        calibrate_intrinsics([checkerboard_view(0)],min_views=8)


def test_intrinsic_resolution_mismatch():
    with pytest.raises(ValueError,match="resolution"):
        calibrate_intrinsics([checkerboard_view(0),checkerboard_view(0,width=640,height=480)],min_views=2)
