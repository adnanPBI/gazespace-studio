"""Typed, framework-independent API data models."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any
import numpy as np

@dataclass
class Frame:
    """Owned image with separate source, host-receive and device timestamps.

    ``timestamp_s`` drives filtering in the source timeline (video PTS may be
    different from arrival time). ``received_monotonic_s`` uses
    ``gazespace.clock.host_now_s()`` / ``time.perf_counter()`` for freshness;
    do not supply ``time.monotonic()`` on Windows Python 3.11/3.12, wall time,
    or a device clock. ``hardware_timestamp_s`` remains in the device domain.
    """
    image: np.ndarray
    timestamp_s: float
    sequence: int = 0
    source: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    received_monotonic_s: float | None = None
    hardware_timestamp_s: float | None = None
    depth_m: np.ndarray | None = None

@dataclass
class HeadPose:
    rotation: np.ndarray  # 3x3, head model -> camera
    translation_m: np.ndarray  # 3, from camera origin in meters; approximate face geometry
    yaw_deg: float
    pitch_deg: float
    roll_deg: float
    reprojection_px: float

@dataclass
class Observation:
    timestamp_s: float
    image_size: tuple[int, int]
    landmarks_px: dict[int, tuple[float, float]] = field(default_factory=dict)
    pupils_px: dict[str, tuple[float, float]] = field(default_factory=dict)
    pupil_method: str = ""
    face_detected: bool = False
    eyes_open: bool = False
    blink: bool = False
    quality: float = 0.0
    features: np.ndarray | None = None
    head_pose: HeadPose | None = None
    eye_origin_camera_m: np.ndarray | None = None
    message: str = "no_face"
    context: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    quality_metrics: dict[str, Any] = field(default_factory=dict)
    eye_origin_source: str = "unavailable"

@dataclass
class GazeResult:
    timestamp_s: float
    valid: bool
    reason: str
    source: str
    quality: float
    gaze_screen_px: tuple[float, float] | None = None
    gaze_screen_normalized: tuple[float, float] | None = None
    ray_origin_world_m: tuple[float, float, float] | None = None
    ray_direction_world: tuple[float, float, float] | None = None
    surface_name: str | None = None
    surface_xy_m: tuple[float, float] | None = None
    surface_xy_px: tuple[float, float] | None = None
    yaw_pitch_roll_deg: tuple[float, float, float] | None = None
    pupils_px: dict[str, tuple[float, float]] = field(default_factory=dict)
    processing_ms: float = 0.0
    capture_timestamp_s: float = 0.0
    warnings: list[str] = field(default_factory=list)
    raw_gaze_screen_px: tuple[float, float] | None = None
    screen_valid: bool = False
    spatial_valid: bool = False
    eye_origin_source: str = "unavailable"
    model_fingerprint: str | None = None
    tracking_context: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

@dataclass
class CameraIntrinsics:
    width: int
    height: int
    camera_matrix: np.ndarray
    distortion: np.ndarray
    reprojection_rms_px: float | None = None
    images_used: int = 0
    source: str = "measured"  # approximate is forbidden for physical calibration

    def validate(self) -> None:
        matrix = np.asarray(self.camera_matrix, dtype=float)
        distortion = np.asarray(self.distortion, dtype=float)
        if self.width < 2 or self.height < 2 or matrix.shape != (3, 3):
            raise ValueError("invalid intrinsic dimensions or camera matrix")
        if not np.isfinite(matrix).all() or not np.isfinite(distortion).all():
            raise ValueError("nonfinite camera intrinsics")
        if matrix[0, 0] <= 0 or matrix[1, 1] <= 0 or not np.allclose(matrix[2], (0, 0, 1)):
            raise ValueError("invalid focal length or homogeneous camera matrix")
        if distortion.size not in (0, 4, 5, 8, 12, 14):
            raise ValueError("unsupported distortion coefficient count")
        if self.source not in ("measured", "approximate", "factory"):
            raise ValueError("unknown intrinsic source")
        if self.reprojection_rms_px is not None and (
            not np.isfinite(self.reprojection_rms_px) or self.reprojection_rms_px < 0
        ):
            raise ValueError("invalid intrinsic reprojection RMS")

    def to_dict(self) -> dict:
        return {"width": self.width, "height": self.height,
                "camera_matrix": self.camera_matrix.tolist(),
                "distortion": self.distortion.ravel().tolist(),
                "reprojection_rms_px": self.reprojection_rms_px,
                "images_used": self.images_used, "source": self.source}

    @classmethod
    def from_dict(cls, d: dict) -> "CameraIntrinsics":
        result = cls(int(d["width"]), int(d["height"]),
                   np.asarray(d["camera_matrix"], dtype=float).reshape(3, 3),
                   np.asarray(d["distortion"], dtype=float).reshape(-1, 1),
                   d.get("reprojection_rms_px"), int(d.get("images_used", 0)),
                   d.get("source", "measured"))
        result.validate()
        return result

@dataclass
class CameraExtrinsics:
    rotation_world_to_camera: np.ndarray
    translation_world_to_camera_m: np.ndarray
    reprojection_rms_px: float | None = None

    def validate(self) -> None:
        rotation = np.asarray(self.rotation_world_to_camera, dtype=float)
        translation = np.asarray(self.translation_world_to_camera_m, dtype=float)
        if rotation.shape != (3, 3) or translation.shape != (3,):
            raise ValueError("invalid extrinsic matrix dimensions")
        if not np.isfinite(rotation).all() or not np.isfinite(translation).all():
            raise ValueError("nonfinite camera extrinsics")
        if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-5) or not np.isclose(np.linalg.det(rotation), 1, atol=1e-5):
            raise ValueError("camera rotation must be a proper orthonormal rotation")

    def to_dict(self) -> dict:
        return {"rotation_world_to_camera": self.rotation_world_to_camera.tolist(),
                "translation_world_to_camera_m": self.translation_world_to_camera_m.tolist(),
                "reprojection_rms_px": self.reprojection_rms_px}

    @classmethod
    def from_dict(cls, d: dict) -> "CameraExtrinsics":
        result = cls(np.asarray(d["rotation_world_to_camera"], dtype=float).reshape(3, 3),
                   np.asarray(d["translation_world_to_camera_m"], dtype=float).reshape(3),
                   d.get("reprojection_rms_px"))
        result.validate()
        return result

@dataclass
class Surface:
    name: str
    origin_world_m: tuple[float, float, float]  # top-left origin
    u_vector: tuple[float, float, float]  # world vector pointing right
    v_vector: tuple[float, float, float]  # world vector pointing down
    width_m: float
    height_m: float
    pixel_size: tuple[int, int] | None = None
    two_sided: bool = True

    def validate(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("surface name cannot be empty")
        vectors = np.asarray([self.origin_world_m, self.u_vector, self.v_vector], dtype=float)
        if vectors.shape != (3, 3) or not np.isfinite(vectors).all():
            raise ValueError("surface points and axes must be finite three-vectors")
        u, v = vectors[1:]
        if min(np.linalg.norm(u), np.linalg.norm(v)) < 1e-9:
            raise ValueError("invalid surface axes")
        cosine = float(u @ v / (np.linalg.norm(u) * np.linalg.norm(v)))
        if abs(cosine) > 1e-5:
            raise ValueError("surface axes must be orthogonal, not parallel or skewed")
        if not np.isfinite([self.width_m, self.height_m]).all() or min(self.width_m, self.height_m) <= 0:
            raise ValueError("surface width and height must be finite and positive")
        if self.pixel_size is not None and (len(self.pixel_size) != 2 or any(int(v) != v or v < 2 for v in self.pixel_size)):
            raise ValueError("surface pixel dimensions must be integer values >= 2")

    @classmethod
    def from_dict(cls, d: dict) -> "Surface":
        result = cls(d["name"], tuple(d["origin_world_m"]), tuple(d["u_vector"]),
                   tuple(d["v_vector"]), float(d["width_m"]), float(d["height_m"]),
                   tuple(d["pixel_size"]) if d.get("pixel_size") else None,
                   bool(d.get("two_sided", True)))
        result.validate()
        return result

    def to_dict(self) -> dict:
        return asdict(self)
