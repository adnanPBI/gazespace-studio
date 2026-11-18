"""Measured stereo triangulation and bounded frame timestamp pairing.

Software timestamp pairing is explicitly different from hardware exposure
synchronization. A stereo point can provide a measured eye surface position;
it does not identify a user's fixation direction without gaze calibration.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math

import cv2
import numpy as np

from .types import CameraIntrinsics, Frame


@dataclass
class PairedFrames:
    left: Frame
    right: Frame
    skew_s: float
    timestamp_basis: str


class FrameSynchronizer:
    def __init__(self, max_skew_s: float = .008, max_queue: int = 6,
                 use_hardware_clock: bool = False):
        if max_skew_s <= 0 or max_queue < 1:
            raise ValueError("Positive skew limit and queue bound required")
        self.max_skew_s, self.max_queue = float(max_skew_s), int(max_queue)
        self.use_hardware_clock = bool(use_hardware_clock)
        self.queues = {"left": deque(), "right": deque()}
        self.paired = self.dropped = 0

    def _stamp(self, frame):
        if self.use_hardware_clock:
            if not frame.metadata.get("hardware_clock_synchronized") or frame.hardware_timestamp_s is None:
                raise ValueError("Hardware clocks must be explicitly synchronized before comparing them")
            stamp = frame.hardware_timestamp_s
        else:
            stamp = frame.timestamp_s
        if not math.isfinite(stamp):
            raise ValueError("Nonfinite frame timestamp")
        return stamp

    def add(self, side: str, frame: Frame) -> PairedFrames | None:
        if side not in self.queues:
            raise ValueError("side must be left or right")
        stamp = self._stamp(frame)
        queue = self.queues[side]
        if queue and stamp <= self._stamp(queue[-1]):
            raise ValueError("Timestamps must increase on each camera")
        queue.append(frame)
        if len(queue) > self.max_queue:
            queue.popleft()
            self.dropped += 1
        left, right = self.queues["left"], self.queues["right"]
        while left and right:
            lt, rt = self._stamp(left[0]), self._stamp(right[0])
            delta = abs(lt - rt)
            if delta <= self.max_skew_s:
                lframe, rframe = left.popleft(), right.popleft()
                self.paired += 1
                return PairedFrames(lframe, rframe, delta,
                    "synchronized_hardware_clock" if self.use_hardware_clock else "host_timestamps_only")
            (left if lt < rt else right).popleft()
            self.dropped += 1
        return None


@dataclass
class TriangulationResult:
    points_camera_m: np.ndarray  # NaN rows are invalid and must not be used.
    valid_mask: np.ndarray
    reprojection_px: np.ndarray
    convergence_deg: np.ndarray

    def to_dict(self) -> dict:
        return {"points_camera_m": [row.tolist() if valid else None
                                     for row, valid in zip(self.points_camera_m, self.valid_mask)],
                "valid_mask": self.valid_mask.tolist(),
                "reprojection_px": [float(x) if np.isfinite(x) else None for x in self.reprojection_px],
                "convergence_deg": [float(x) if np.isfinite(x) else None for x in self.convergence_deg]}


def triangulate_points_checked(left_xy: np.ndarray, right_xy: np.ndarray,
                               left_intr: CameraIntrinsics, right_intr: CameraIntrinsics,
                               rotation_right_from_left: np.ndarray,
                               translation_right_from_left_m: np.ndarray, *,
                               max_reprojection_px: float = 2.0,
                               min_convergence_deg: float = .1,
                               max_depth_m: float = 10.0) -> TriangulationResult:
    if left_intr.source not in ("measured", "factory") or right_intr.source not in ("measured", "factory"):
        raise ValueError("Stereo triangulation requires measured intrinsics")
    left, right = np.asarray(left_xy, float), np.asarray(right_xy, float)
    if left.ndim != 2 or left.shape[1] != 2 or right.shape != left.shape or len(left) == 0:
        raise ValueError("Matching nonempty Nx2 pixel coordinates required")
    if not np.isfinite(left).all() or not np.isfinite(right).all():
        raise ValueError("Pixel coordinates must be finite")
    R = np.asarray(rotation_right_from_left, float)
    T = np.asarray(translation_right_from_left_m, float).reshape(3)
    if R.shape != (3, 3) or not np.isfinite(R).all() or not np.isfinite(T).all():
        raise ValueError("Invalid stereo transform")
    if not np.allclose(R.T @ R, np.eye(3), atol=1e-6) or not np.isclose(np.linalg.det(R), 1, atol=1e-6):
        raise ValueError("Stereo rotation must be proper orthonormal")
    if np.linalg.norm(T) < .001:
        raise ValueError("Stereo baseline is too small for metric triangulation")
    if max_reprojection_px <= 0 or min_convergence_deg < 0 or max_depth_m <= 0:
        raise ValueError("Invalid triangulation limits")
    for xy, intr in ((left, left_intr), (right, right_intr)):
        if np.any(xy < 0) or np.any(xy[:, 0] >= intr.width) or np.any(xy[:, 1] >= intr.height):
            raise ValueError("Pixel coordinates outside calibrated images")
    lnorm = cv2.undistortPoints(left.reshape(-1, 1, 2), left_intr.camera_matrix,
                                left_intr.distortion).reshape(-1, 2)
    rnorm = cv2.undistortPoints(right.reshape(-1, 1, 2), right_intr.camera_matrix,
                                right_intr.distortion).reshape(-1, 2)
    P1 = np.column_stack((np.eye(3), np.zeros(3)))
    P2 = np.column_stack((R, T))
    homogeneous = cv2.triangulatePoints(P1, P2, lnorm.T, rnorm.T).T
    w = homogeneous[:, 3]
    candidate = np.full((len(left), 3), np.nan)
    finite = np.abs(w) > 1e-10
    candidate[finite] = homogeneous[finite, :3] / w[finite, None]
    errors, convergence = np.full(len(left), np.inf), np.zeros(len(left))
    valid = finite & np.isfinite(candidate).all(axis=1)
    for i in np.flatnonzero(valid):
        point = candidate[i]
        right_point = R @ point + T
        if not (.01 < point[2] < max_depth_m and .01 < right_point[2] < max_depth_m):
            valid[i] = False
            continue
        projected_l, _ = cv2.projectPoints(point.reshape(1, 3), np.zeros(3), np.zeros(3),
                                           left_intr.camera_matrix, left_intr.distortion)
        rvec, _ = cv2.Rodrigues(R)
        projected_r, _ = cv2.projectPoints(point.reshape(1, 3), rvec, T,
                                           right_intr.camera_matrix, right_intr.distortion)
        errors[i] = max(np.linalg.norm(projected_l.reshape(2) - left[i]),
                        np.linalg.norm(projected_r.reshape(2) - right[i]))
        camera_right_in_left = -R.T @ T
        d1, d2 = point, point - camera_right_in_left
        cosang = np.dot(d1, d2) / (np.linalg.norm(d1) * np.linalg.norm(d2))
        convergence[i] = np.degrees(np.arccos(np.clip(cosang, -1, 1)))
        valid[i] = errors[i] <= max_reprojection_px and convergence[i] >= min_convergence_deg
    candidate[~valid] = np.nan
    return TriangulationResult(candidate, valid, errors, convergence)
