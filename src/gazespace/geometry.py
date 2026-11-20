"""Camera intrinsics, head pose, 3D rigid transforms and bounded ray/surface mapping."""
from __future__ import annotations
import math
import numpy as np
import cv2
from .types import CameraIntrinsics, CameraExtrinsics, HeadPose, Surface

# Neutral head axes match the OpenCV camera: X right, Y down, Z away.
# Looking straight into the camera is identity pose and gaze direction (0,0,-1).
# This is generic anatomy, not a measurement of the person's eye origin.
FACE_INDICES = (1, 152, 33, 263, 61, 291)
FACE_TEMPLATE_M = np.array([
    (0.000, 0.000, 0.000),       # nose
    (0.000, 0.0675, 0.0260),    # chin, below and behind nose
    (-0.0433, -0.0327, 0.0260), # subject's right / image-left outer eye corner
    (0.0433, -0.0327, 0.0260),  # subject's left / image-right outer eye corner
    (-0.0289, 0.0289, 0.0241),
    (0.0289, 0.0289, 0.0241),
], dtype=np.float64)
EYE_MIDPOINT_HEAD_M = np.array([0.0, -0.031, 0.022], dtype=np.float64)
HEAD_MODEL_VERSION = "generic-camera-facing-v2.1"


def finite_vector(value, name="vector") -> np.ndarray:
    vector = np.asarray(value, dtype=float)
    if vector.shape != (3,) or not np.isfinite(vector).all():
        raise ValueError(f"{name} must be a finite three-vector")
    return vector


def unit_vector(value, name="direction") -> np.ndarray:
    vector = finite_vector(value, name)
    norm = float(np.linalg.norm(vector))
    if norm < 1e-9:
        raise ValueError(f"zero {name}")
    return vector / norm


def approximate_intrinsics(width: int, height: int) -> CameraIntrinsics:
    # Only for rendering/relative head orientation. NOT a calibrated 3D geometry source.
    if min(width, height) < 2:
        raise ValueError("image dimensions must be >= 2")
    fx = float(max(width, height))
    return CameraIntrinsics(width, height,
                            np.array([[fx, 0, width / 2], [0, fx, height / 2],
                                      [0, 0, 1]], dtype=float),
                            np.zeros((5, 1), dtype=float), source="approximate")


def estimate_head_pose(points: dict[int, tuple[float, float]],
                       intr: CameraIntrinsics,
                       max_reprojection_px: float | None = None) -> HeadPose | None:
    """Estimate geometric head pose and reject implausible 2D fit residuals.

    This is a generic anatomical template, not subject-calibrated ground truth.
    """
    intr.validate()
    if any(i not in points for i in FACE_INDICES):
        return None
    img = np.array([points[i] for i in FACE_INDICES], dtype=np.float64)
    if not np.isfinite(img).all():
        return None
    eye_span = float(np.linalg.norm(img[3] - img[2]))
    if img[2, 0] >= img[3, 0] or eye_span < 12:
        return None
    for flag in (getattr(cv2, "SOLVEPNP_SQPNP", cv2.SOLVEPNP_ITERATIVE),
                 cv2.SOLVEPNP_ITERATIVE):
        try:
            ok, rvec, tvec = cv2.solvePnP(FACE_TEMPLATE_M, img, intr.camera_matrix,
                                           intr.distortion, flags=flag)
            if not ok or not np.isfinite(tvec).all() or not .10 < float(tvec[2, 0]) < 5:
                continue
            try:
                rvec, tvec = cv2.solvePnPRefineLM(
                    FACE_TEMPLATE_M, img, intr.camera_matrix, intr.distortion, rvec, tvec)
            except cv2.error:
                pass
            rotation, _ = cv2.Rodrigues(rvec)
            projected, _ = cv2.projectPoints(FACE_TEMPLATE_M, rvec, tvec,
                                             intr.camera_matrix, intr.distortion)
            rms = float(np.sqrt(np.mean(np.sum((projected.reshape(-1, 2) - img) ** 2, axis=1))))
            limit = max(12.0, eye_span*.20) if max_reprojection_px is None else float(max_reprojection_px)
            if not np.isfinite(rms) or rms > limit or not np.isfinite(tvec).all() or not .10 < float(tvec[2, 0]) < 5:
                continue
            angles, *_ = cv2.RQDecomp3x3(rotation)
            pitch, yaw, roll = (float(z) for z in angles)
            return HeadPose(rotation, tvec.reshape(3), yaw, pitch, roll, rms)
        except (cv2.error, ValueError, FloatingPointError):
            continue
    return None

def eye_origin_from_pose(pose: HeadPose) -> np.ndarray:
    return finite_vector(pose.rotation @ EYE_MIDPOINT_HEAD_M + pose.translation_m, "eye origin")


def camera_to_world_point(point_camera: np.ndarray, ext: CameraExtrinsics) -> np.ndarray:
    ext.validate()
    return ext.rotation_world_to_camera.T @ (finite_vector(point_camera, "camera point") - ext.translation_world_to_camera_m)


def world_to_camera_point(point_world: np.ndarray, ext: CameraExtrinsics) -> np.ndarray:
    ext.validate()
    return ext.rotation_world_to_camera @ finite_vector(point_world, "world point") + ext.translation_world_to_camera_m


def camera_to_world_direction(dir_camera: np.ndarray, ext: CameraExtrinsics) -> np.ndarray:
    ext.validate()
    return unit_vector(ext.rotation_world_to_camera.T @ unit_vector(dir_camera))


def project_world_point(point_world: np.ndarray, intr: CameraIntrinsics, ext: CameraExtrinsics) -> np.ndarray:
    rvec, _ = cv2.Rodrigues(ext.rotation_world_to_camera)
    xy, _ = cv2.projectPoints(np.asarray(point_world, dtype=float).reshape(-1, 3),
                              rvec, ext.translation_world_to_camera_m, intr.camera_matrix, intr.distortion)
    return xy.reshape(-1, 2)


def ray_surface_intersection(origin: np.ndarray, direction: np.ndarray,
                             surface: Surface, min_t_m: float = 0.02):
    """Returns dict containing distance, metric uv and pixel uv, or None.

    Rectangular axes must be orthogonal; they are normalized before mapping.
    With U right and V down, front-facing surface normal is cross(v,u).
    Surface.two_sided=False accepts rays traveling with cross(u,v).
    Pixel centers span 0..width-1 and 0..height-1, including boundary hits.
    """
    surface.validate()
    if not np.isfinite(min_t_m) or min_t_m < 0:
        raise ValueError("minimum ray distance must be finite and nonnegative")
    p0 = np.asarray(surface.origin_world_m, dtype=float)
    u, v = np.asarray(surface.u_vector, dtype=float), np.asarray(surface.v_vector, dtype=float)
    u_norm, v_norm = np.linalg.norm(u), np.linalg.norm(v)
    if u_norm < 1e-10 or v_norm < 1e-10:
        raise ValueError("invalid surface axes")
    u, v = u / u_norm, v / v_norm
    n = np.cross(u, v)
    if np.linalg.norm(n) < 1e-8:
        raise ValueError("surface axes are parallel")
    n /= np.linalg.norm(n)
    origin, d = finite_vector(origin, "ray origin"), unit_vector(direction, "gaze direction")
    denom = float(np.dot(n, d))
    if abs(denom) < 1e-8:
        return None
    if not surface.two_sided and denom <= 0:
        return None
    t = float(np.dot(n, p0 - origin) / denom)
    if t < min_t_m:
        return None
    p = origin + t * d
    uv = np.linalg.lstsq(np.column_stack((u, v)), p - p0, rcond=None)[0]
    x, y = float(uv[0]), float(uv[1])
    if not (-1e-6 <= x <= surface.width_m + 1e-6 and -1e-6 <= y <= surface.height_m + 1e-6):
        return None
    x, y = float(np.clip(x, 0, surface.width_m)), float(np.clip(y, 0, surface.height_m))
    pixels = None
    if surface.pixel_size:
        pixels = (x / surface.width_m * (surface.pixel_size[0] - 1),
                  y / surface.height_m * (surface.pixel_size[1] - 1))
    return {"distance_m": t, "xy_m": (x, y), "xy_px": pixels, "point_world_m": tuple(float(z) for z in p)}


def first_surface_hit(origin: np.ndarray, direction: np.ndarray, surfaces: list[Surface]):
    candidates = []
    for surf in surfaces:
        hit = ray_surface_intersection(origin, direction, surf)
        if hit is not None:
            candidates.append((hit["distance_m"], surf, hit))
    return min(candidates, key=lambda x: x[0])[1:] if candidates else None


def angular_error_deg(a: np.ndarray, b: np.ndarray) -> float:
    a, b = unit_vector(a), unit_vector(b)
    return float(math.degrees(math.acos(float(np.clip(np.dot(a, b), -1, 1)))))
