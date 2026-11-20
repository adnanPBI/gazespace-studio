"""Optional RealSense acquisition and measured depth deprojection.

The SDK is imported only for an actual selected RealSense device. Depth must be
aligned to the color image used by the eye detector; arbitrary depth/IR image
coordinates are never mixed. A missing/invalid depth returns no metric point.
"""
from __future__ import annotations

from threading import Event

import cv2
import numpy as np

from .capture import FrameSource, to_bgr
from .clock import HOST_CLOCK_DOMAIN, host_now_s
from .types import CameraIntrinsics, Frame


SUPPORTED_DEPTH_DISTORTIONS = ("none", "brown_conrady", "inverse_brown_conrady")


def _deproject_pixel(x: float, y: float, z: float, intrinsics: CameraIntrinsics,
                     distortion_model: str = "brown_conrady") -> np.ndarray:
    """Use the inverse polynomial only for RealSense inverse Brown-Conrady models."""
    if distortion_model == "inverse_brown_conrady":
        fx, fy = float(intrinsics.camera_matrix[0, 0]), float(intrinsics.camera_matrix[1, 1])
        ppx, ppy = float(intrinsics.camera_matrix[0, 2]), float(intrinsics.camera_matrix[1, 2])
        coeffs = np.asarray(intrinsics.distortion, dtype=float).ravel()
        k1, k2, p1, p2, k3 = (float(coeffs[i]) if i < len(coeffs) else 0.0 for i in range(5))
        nx, ny = (x - ppx) / fx, (y - ppy) / fy
        r2 = nx*nx + ny*ny
        radial = 1 + k1*r2 + k2*r2*r2 + k3*r2*r2*r2
        ux = nx*radial + 2*p1*nx*ny + p2*(r2 + 2*nx*nx)
        uy = ny*radial + 2*p2*nx*ny + p1*(r2 + 2*ny*ny)
        return np.array([ux*z, uy*z, z], dtype=float)
    if distortion_model in ("none", "brown_conrady"):
        distortion = np.zeros_like(intrinsics.distortion) if distortion_model == "none" else intrinsics.distortion
        uv = cv2.undistortPoints(np.array([[[x, y]]], dtype=float),
                                intrinsics.camera_matrix, distortion).reshape(2)
        return np.array([uv[0]*z, uv[1]*z, z], dtype=float)
    raise ValueError(f"Unsupported depth distortion model: {distortion_model}")


def sample_depth_point(depth_m: np.ndarray, xy_px: tuple[float, float],
                       intrinsics: CameraIntrinsics, *, radius: int = 2,
                       min_depth_m: float = .10, max_depth_m: float = 3.0,
                       max_patch_spread_m: float = .03,
                       distortion_model: str = "brown_conrady") -> np.ndarray | None:
    """Robust patch median followed by calibrated, distortion-aware deprojection."""
    if (depth_m.ndim != 2 or depth_m.shape != (intrinsics.height, intrinsics.width) or
            intrinsics.source not in ("measured", "factory")):
        raise ValueError("Aligned depth must match measured intrinsics and image resolution")
    if distortion_model not in SUPPORTED_DEPTH_DISTORTIONS:
        raise ValueError(f"Unsupported depth distortion model: {distortion_model}")
    if radius < 0 or min_depth_m <= 0 or max_depth_m <= min_depth_m:
        raise ValueError("Invalid depth sampling limits")
    if not np.isfinite(xy_px).all():
        return None
    x, y = map(float, xy_px)
    px, py = int(round(x)), int(round(y))
    if not (0 <= px < intrinsics.width and 0 <= py < intrinsics.height):
        return None
    patch = np.asarray(depth_m[max(0, py-radius):min(intrinsics.height, py+radius+1),
                               max(0, px-radius):min(intrinsics.width, px+radius+1)], float)
    values = patch[np.isfinite(patch) & (patch >= min_depth_m) & (patch <= max_depth_m)]
    if len(values) < max(1, int(patch.size * .4)):
        return None
    if float(np.percentile(values, 90) - np.percentile(values, 10)) > max_patch_spread_m:
        return None
    return _deproject_pixel(x, y, float(np.median(values)), intrinsics, distortion_model)


def eye_origin_from_depth(depth_m: np.ndarray, pupils_px: dict,
                          intrinsics: CameraIntrinsics, *,
                          distortion_model: str = "brown_conrady") -> np.ndarray | None:
    """Approximate visible eye surface midpoint; NOT the eye rotation center."""
    if not all(side in pupils_px for side in ("right", "left")):
        return None
    points = [sample_depth_point(depth_m, pupils_px[side], intrinsics,
                                 distortion_model=distortion_model) for side in ("right", "left")]
    if any(point is None for point in points):
        return None
    right, left = points
    if abs(right[2] - left[2]) > .08:
        return None
    separation = float(np.linalg.norm(right - left))
    if not .025 <= separation <= .10:
        return None
    return (right + left) / 2

class RealSenseSource(FrameSource):
    is_live = True

    def __init__(self, serial: str | None = None, width: int = 640, height: int = 480,
                 fps: int = 30, timeout_ms: int = 500):
        if width <= 0 or height <= 0 or fps <= 0 or not 1 <= timeout_ms <= 10000:
            raise ValueError("Invalid RealSense stream settings")
        try:
            import pyrealsense2 as rs
        except ImportError as exc:
            raise RuntimeError("RealSense mode requires pyrealsense2 and compatible camera firmware/drivers") from exc
        self.rs, self.timeout_ms = rs, int(timeout_ms)
        self._closed, self._started = False, False
        self._stop, self.seq = Event(), 0
        self.pipeline = rs.pipeline()
        config = rs.config()
        if serial:
            config.enable_device(serial)
        config.enable_stream(rs.stream.color, width, height, rs.format.bgr8, fps)
        config.enable_stream(rs.stream.depth, width, height, rs.format.z16, fps)
        try:
            profile = self.pipeline.start(config)
            self._started = True
            self.align = rs.align(rs.stream.color)
            device = profile.get_device()
            self.depth_scale = float(device.first_depth_sensor().get_depth_scale())
            actual_serial = device.get_info(rs.camera_info.serial_number)
            color = profile.get_stream(rs.stream.color).as_video_stream_profile().get_intrinsics()
            distortion_map = {
                rs.distortion.none: "none",
                rs.distortion.brown_conrady: "brown_conrady",
                rs.distortion.inverse_brown_conrady: "inverse_brown_conrady",
            }
            self._distortion_model = distortion_map.get(color.model, "unsupported")
            self._cv_depth_supported = self._distortion_model in SUPPORTED_DEPTH_DISTORTIONS
            self._opencv_intrinsics_compatible = self._distortion_model in ("none", "brown_conrady")
            distortion = np.zeros(5) if color.model == rs.distortion.none else np.asarray(color.coeffs, float)
            self.intrinsics = CameraIntrinsics(color.width, color.height,
                np.array([[color.fx, 0, color.ppx], [0, color.fy, color.ppy], [0, 0, 1]], dtype=float),
                distortion, source="factory")
            self.info = {"camera_id": f"realsense:{actual_serial}", "serial": actual_serial,
                         "model": device.get_info(rs.camera_info.name), "backend": "realsense",
                         "identity_strength": "serial", "width": color.width, "height": color.height,
                         "fps_reported": fps, "is_live": True, "depth_aligned_to": "color",
                         "host_clock_domain": HOST_CLOCK_DOMAIN,
                         "intrinsics": self.intrinsics.to_dict(), "depth_scale_m": self.depth_scale,
                         "depth_deprojection_supported": self._cv_depth_supported,
                         "depth_distortion_model": self._distortion_model,
                         "opencv_factory_intrinsics_supported": self._opencv_intrinsics_compatible,
                         "global_shutter_verified": False}
        except Exception:
            self.close()
            raise

    def read(self) -> Frame | None:
        if self._closed or self._stop.is_set():
            return None
        frames = self.pipeline.wait_for_frames(self.timeout_ms)
        aligned = self.align.process(frames)
        color, depth = aligned.get_color_frame(), aligned.get_depth_frame()
        if not color or not depth:
            raise OSError("RealSense frame missing aligned color/depth")
        received = host_now_s()
        image = to_bgr(np.asanyarray(color.get_data()))
        metric_depth = np.asanyarray(depth.get_data()).astype(np.float32) * self.depth_scale
        self.seq += 1
        return Frame(image, received, self.seq, self.info["serial"],
                     metadata={"simulated": False, "depth_aligned_to": "color",
                               "host_clock_domain": HOST_CLOCK_DOMAIN,
                               "depth_intrinsics": self.intrinsics.to_dict(),
                               "depth_deprojection_supported": self._cv_depth_supported,
                               "depth_distortion_model": self._distortion_model,
                               "opencv_factory_intrinsics_supported": self._opencv_intrinsics_compatible,
                               "hardware_clock_synchronized": False},
                     received_monotonic_s=received,
                     hardware_timestamp_s=float(color.get_timestamp()) / 1000.0,
                     depth_m=metric_depth.copy() if self._cv_depth_supported else None)

    def capabilities(self) -> dict:
        return dict(self.info)

    def request_stop(self) -> None:
        self._stop.set()

    def close(self) -> None:
        self._stop.set()
        if not self._closed:
            self._closed = True
            if self._started:
                self.pipeline.stop()
                self._started = False
