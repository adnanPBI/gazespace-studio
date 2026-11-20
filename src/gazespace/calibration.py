"""Measured intrinsics/extrinsics, stereo and robust user-specific gaze calibration.

Spatial regressors learn rays toward KNOWN 3D targets (multiple depths
recommended). Never infer gaze from iris landmarks without a trained model.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import tempfile
import hashlib
import uuid
import copy
import cv2
import numpy as np
from .types import CameraIntrinsics, CameraExtrinsics, Observation
from .geometry import world_to_camera_point, angular_error_deg
from .vision import FEATURE_NAMES, FEATURE_VERSION
from .geometry import HEAD_MODEL_VERSION

PROFILE_SCHEMA_VERSION = 2
BASIS_VERSION = 2
CONTEXT_KEYS = ("tracking_mode", "pupil_polarity", "image_size", "roi_normalized",
                "feature_version", "feature_method", "head_model", "face_model_sha256",
                "subject_id", "camera_id", "camera_backend", "backend", "mirror", "source_identity",
                "eye_origin_source", "exposure_native", "exposure_us", "gain", "ir_bit_depth",
                "monitor", "monitor_serial", "monitor_origin_px", "monitor_dpr", "intrinsics_fingerprint")
DISPLAY_CONTEXT_KEYS = frozenset({"monitor", "monitor_serial", "monitor_origin_px", "monitor_dpr", "screen_size"})


def chessboard_points(cols: int, rows: int, square_mm: float) -> np.ndarray:
    if cols < 3 or rows < 3 or square_mm <= 0:
        raise ValueError("board must contain >=3x3 internal corners and positive square size")
    pts = np.zeros((rows * cols, 3), dtype=np.float32)
    pts[:, :2] = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2)
    pts *= square_mm / 1000.0
    return pts


def find_board(image: np.ndarray, cols: int, rows: int):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    ok, corners = cv2.findChessboardCornersSB(gray, (cols, rows), flags=cv2.CALIB_CB_NORMALIZE_IMAGE)
    return corners.reshape(-1, 2).astype(np.float32) if ok else None


def calibrate_intrinsics(images: list[np.ndarray], cols: int = 9, rows: int = 6,
                         square_mm: float = 24.0, min_views: int = 8):
    object_pts, image_pts, rejected = [], [], 0
    size = None
    template = chessboard_points(cols, rows, square_mm)
    for im in images:
        h,w = im.shape[:2]
        if size and size != (w,h):
            raise ValueError("all calibration images must share exact resolution")
        size = (w,h)
        corners = find_board(im, cols, rows)
        if corners is None:
            rejected += 1
            continue
        object_pts.append(template.copy())
        image_pts.append(corners)
    if len(image_pts) < min_views:
        raise ValueError(f"need >= {min_views} detected board views, have {len(image_pts)} (rejected {rejected})")
    rms, matrix, dist, rvecs, tvecs = cv2.calibrateCamera(object_pts, image_pts, size, None, None)
    if not np.isfinite(rms) or rms <= 0:
        raise ValueError("camera calibration failed")
    return CameraIntrinsics(size[0], size[1], matrix, dist, float(rms), len(image_pts)), {
        "views_used": len(image_pts), "views_rejected": rejected, "rms_px": float(rms),
        "image_size": size, "recommendation": "Use held-out reprojection images and cover entire sensor field"}


def solve_world_board_extrinsics(image: np.ndarray, intr: CameraIntrinsics,
                                 cols: int = 9, rows: int = 6, square_mm: float = 24.0):
    if (image.shape[1], image.shape[0]) != (intr.width, intr.height):
        raise ValueError("extrinsic capture resolution differs from intrinsic calibration")
    if intr.source not in ("measured", "factory"):
        raise ValueError("physical world calibration requires measured intrinsics")
    xy = find_board(image, cols, rows)
    if xy is None:
        raise ValueError("board corners not detected; check focus, lighting, orientation")
    obj = chessboard_points(cols, rows, square_mm).astype(np.float64)
    ok, rv, tv = cv2.solvePnP(obj, xy.astype(np.float64), intr.camera_matrix, intr.distortion)
    if not ok:
        raise ValueError("solvePnP failed")
    rotation, _ = cv2.Rodrigues(rv)
    projected, _ = cv2.projectPoints(obj, rv, tv, intr.camera_matrix, intr.distortion)
    rms = float(np.sqrt(np.mean(np.sum((projected.reshape(-1,2)-xy)**2,axis=1))))
    if float(tv[2,0]) <= 0 or rms > 10:
        raise ValueError(f"inconsistent world board pose (reprojection={rms:.2f} px)")
    return CameraExtrinsics(rotation, tv.reshape(3), rms)


def calibrate_stereo(left_images: list[np.ndarray], right_images: list[np.ndarray],
                     left_intr: CameraIntrinsics, right_intr: CameraIntrinsics,
                     cols: int = 9, rows: int = 6, square_mm: float = 24.0,
                     min_pairs: int = 8) -> dict:
    if len(left_images) != len(right_images):
        raise ValueError("paired image counts differ")
    if (left_intr.width, left_intr.height) != (right_intr.width, right_intr.height):
        raise ValueError("this stereo workflow needs matching calibrated resolutions")
    if left_intr.source not in ("measured", "factory") or right_intr.source not in ("measured", "factory"):
        raise ValueError("stereo calibration requires measured intrinsics")
    size = (left_intr.width, left_intr.height)
    objpts, lpts, rpts = [], [], []
    template = chessboard_points(cols, rows, square_mm)
    for left,right in zip(left_images, right_images):
        if left.shape[1::-1] != size or right.shape[1::-1] != size:
            raise ValueError("stereo images differ from intrinsic image size")
        l,r = find_board(left, cols, rows), find_board(right, cols, rows)
        if l is None or r is None:
            continue
        objpts.append(template.copy())
        lpts.append(l)
        rpts.append(r)
    if len(objpts) < min_pairs:
        raise ValueError(f"need >= {min_pairs} synchronous detected board pairs, got {len(objpts)}")
    rms, _, _, _, _, R, T, E, F = cv2.stereoCalibrate(
        objpts, lpts, rpts, left_intr.camera_matrix.copy(), left_intr.distortion.copy(),
        right_intr.camera_matrix.copy(), right_intr.distortion.copy(), size,
        flags=cv2.CALIB_FIX_INTRINSIC)
    return {"rms_px": float(rms), "pairs_used": len(objpts),
            "R_right_from_left": R.tolist(), "T_right_from_left_m": T.reshape(3).tolist(),
            "baseline_m": float(np.linalg.norm(T)), "essential": E.tolist(),
            "fundamental": F.tolist(),
            "warning": "Software pair matching is not hardware synchronization; use trigger/genlock where necessary"}


def feature_basis(features: np.ndarray) -> np.ndarray:
    """Small, deliberately regularized basis for calibrated gaze responses."""
    X = np.atleast_2d(np.asarray(features, float))
    if X.ndim != 2 or X.shape[1] != len(FEATURE_NAMES) or not np.all(np.isfinite(X)):
        raise ValueError(f"expected finite Nx{len(FEATURE_NAMES)} feature matrix")
    # No raw z polynomial: depth is sensitive to approximate anthropometry.
    r_x, r_y, l_x, l_y = [X[:, i] for i in range(4)]
    eye_x = (r_x + l_x)/2
    eye_y = (r_y + l_y)/2
    basis = np.column_stack([X, eye_x**2, eye_y**2, eye_x*eye_y,
                             eye_x*X[:,4], eye_y*X[:,5]])
    return basis


@dataclass
class GazeModel:
    kind: str
    coeff: np.ndarray  # basis+intercept x output dims
    feature_mean: np.ndarray
    feature_scale: np.ndarray
    ridge: float
    report: dict
    metadata: dict

    def validate(self) -> None:
        output_count = {"screen": 2, "spatial": 3}.get(self.kind)
        if output_count is None:
            raise ValueError("unknown gaze model kind")
        if np.asarray(self.coeff).shape != (16, output_count):
            raise ValueError("invalid gaze model coefficient dimensions")
        if np.asarray(self.feature_mean).shape != (15,) or np.asarray(self.feature_scale).shape != (15,):
            raise ValueError("invalid gaze feature normalization dimensions")
        if not all(np.isfinite(v).all() for v in (self.coeff, self.feature_mean, self.feature_scale)):
            raise ValueError("nonfinite gaze model coefficients")
        if np.any(self.feature_scale <= 0) or not np.isfinite(self.ridge) or self.ridge <= 0:
            raise ValueError("invalid gaze model normalization or regularization")
        if self.kind == "screen":
            size = self.metadata.get("screen_size")
            if not size or len(size) != 2 or any(int(v) != v or v < 2 for v in size):
                raise ValueError("screen model requires integer display dimensions >= 2")

    @property
    def fingerprint(self) -> str:
        payload = {"kind": self.kind, "coeff": self.coeff.tolist(),
                   "mean": self.feature_mean.tolist(), "scale": self.feature_scale.tolist(),
                   "metadata": self.metadata, "basis_version": BASIS_VERSION}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()

    def outside_envelope(self, features) -> list[str]:
        values = np.asarray(features, dtype=float)
        if values.shape != (len(FEATURE_NAMES),) or not np.isfinite(values).all():
            return ["invalid_features"]
        envelope = self.metadata.get("training_envelope")
        if not envelope:
            return []
        lower, upper = np.asarray(envelope["lower"]), np.asarray(envelope["upper"])
        return [FEATURE_NAMES[i] for i in np.flatnonzero((values < lower) | (values > upper))]

    def context_mismatches(self, actual: dict, *, require_present: bool = False) -> list[str]:
        mismatches = []
        # Integrations with an actual display size must not reuse pixel mappings
        # after a DPI/resolution change. Headless integrations can omit it.
        if self.kind == "screen" and "screen_size" in actual:
            if json.dumps(actual["screen_size"]) != json.dumps(self.metadata.get("screen_size")):
                mismatches.append("screen_size")
        for key in CONTEXT_KEYS:
            if self.kind == "spatial" and key in ("monitor", "monitor_serial", "monitor_origin_px", "monitor_dpr"):
                continue
            if key not in self.metadata:
                continue
            if key not in actual:
                if require_present:
                    mismatches.append(key)
                continue
            # JSON normalization removes tuple/list differences but preserves ROI values.
            if json.dumps(self.metadata[key], sort_keys=True) != json.dumps(actual[key], sort_keys=True):
                mismatches.append(key)
        return mismatches

    def predict_raw(self, features: np.ndarray) -> np.ndarray:
        basis = feature_basis(features)
        z = (basis - self.feature_mean) / self.feature_scale
        return np.column_stack([np.ones(len(z)), z]) @ self.coeff

    def predict(self, features: np.ndarray) -> np.ndarray:
        pred = self.predict_raw(features)
        if not np.isfinite(pred).all():
            raise ValueError("nonfinite gaze prediction")
        if self.kind == "spatial":
            denom = np.linalg.norm(pred, axis=1, keepdims=True)
            if np.any(denom < 1e-6):
                raise ValueError("spatial model predicted a zero direction")
            return pred / denom
        return pred

    def predict_camera_direction(self, features: np.ndarray,
                                 head_rotation: np.ndarray | None = None) -> np.ndarray:
        if self.kind != "spatial":
            raise ValueError("predict_camera_direction requires a spatial model")
        pred = self.predict(features)
        if self.metadata.get("spatial_frame") != "head":
            return pred
        if head_rotation is None:
            raise ValueError("head-frame spatial gaze requires current head rotation")
        rotation = np.asarray(head_rotation, dtype=float)
        if rotation.shape == (3, 3) and np.isfinite(rotation).all():
            transformed = pred @ rotation.T
        elif rotation.shape == (len(pred), 3, 3) and np.isfinite(rotation).all():
            transformed = np.einsum("nij,nj->ni", rotation, pred)
        else:
            raise ValueError("invalid head rotation dimensions")
        norm = np.linalg.norm(transformed, axis=1, keepdims=True)
        if np.any(norm < 1e-6):
            raise ValueError("zero rotated direction")
        return transformed / norm

    def to_dict(self) -> dict:
        self.validate()
        return {"kind": self.kind, "coeff": self.coeff.tolist(),
                "feature_mean": self.feature_mean.tolist(),
                "feature_scale": self.feature_scale.tolist(),
                "ridge": self.ridge, "report": self.report, "metadata": self.metadata,
                "feature_names": list(FEATURE_NAMES), "basis_version": BASIS_VERSION,
                "feature_version": FEATURE_VERSION}

    @classmethod
    def from_dict(cls, d: dict) -> "GazeModel":
        if d.get("feature_names") != list(FEATURE_NAMES) or d.get("basis_version") != BASIS_VERSION or d.get("feature_version") != FEATURE_VERSION:
            raise ValueError("incompatible gaze feature format")
        result = cls(d["kind"], np.asarray(d["coeff"],float),
                   np.asarray(d["feature_mean"],float), np.asarray(d["feature_scale"],float),
                   float(d["ridge"]), d.get("report",{}), d.get("metadata",{}))
        result.validate()
        return result


def _fit_ridge(X: np.ndarray, y: np.ndarray, ridge: float) -> tuple:
    basis = feature_basis(X)
    mean = np.mean(basis, axis=0)
    std = np.std(basis, axis=0)
    std = np.maximum(std, 0.03)  # avoid exploding low-variance features
    design = np.column_stack([np.ones(len(basis)), (basis-mean)/std])
    penalty = np.diag([0] + [ridge]*basis.shape[1])
    weights = np.ones(len(X))
    for _ in range(6):
        coefs = np.linalg.solve(design.T @ (weights[:,None]*design) + penalty,
                                design.T @ (weights[:,None]*y))
        residuals = np.linalg.norm(design@coefs-y,axis=1)
        delta = max(1e-5, 1.5 * float(np.median(residuals)) + 1e-4)
        weights = np.minimum(1.0, delta / np.maximum(residuals, 1e-8))
    return coefs, mean, std


def _error_metrics(pred: np.ndarray, y: np.ndarray, kind: str, screen_size=None) -> dict:
    pred, y = np.asarray(pred, dtype=float), np.asarray(y, dtype=float)
    if pred.shape != y.shape or pred.ndim != 2 or not len(pred) or not np.isfinite(pred).all() or not np.isfinite(y).all():
        raise ValueError("error metrics require matching finite nonempty arrays")
    if kind == "spatial":
        err = np.array([angular_error_deg(p,t) for p,t in zip(pred,y)])
        unit = "degrees"
    else:
        scale = np.asarray(screen_size, dtype=float) - 1 if screen_size else np.ones(2)
        err = np.linalg.norm((pred-y)*scale,axis=1)
        unit = "pixels" if screen_size else "normalized_units"
    return {"median": float(np.median(err)), "p95": float(np.percentile(err,95)),
            "mean": float(np.mean(err)), "max": float(np.max(err)), "unit": unit,
            "count": len(err)}


def fit_gaze_model(samples: list[dict], kind: str = "screen", ridge: float = 1.0,
                   screen_size: tuple[int,int] | None = None, min_targets: int = 9,
                   min_samples_per_target: int = 4, do_group_validation: bool = True,
                   context: dict | None = None, session_id: str | None = None) -> GazeModel:
    """Fit robust ridge map to screen normalized XY or 3D gaze directions.

    Requires diverse target groups; testing by randomly splitting adjacent
    video frames would overstate accuracy due to temporal leakage.
    """
    if kind not in ("screen", "spatial"):
        raise ValueError("kind must be screen or spatial")
    if kind == "screen" and (not screen_size or len(screen_size) != 2 or min(screen_size) < 2):
        raise ValueError("screen dimensions required")
    if not np.isfinite(ridge) or ridge <= 0:
        raise ValueError("regularization must be positive")
    if not samples or min_targets < 4 or min_samples_per_target < 4:
        raise ValueError("calibration needs samples, >=4 targets and >=4 samples per target")
    try:
        X = np.array([r["features"] for r in samples], dtype=float)
        Y = np.array([r["target"] for r in samples], dtype=float)
        labels = np.array([str(r["target_id"]) for r in samples])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid calibration sample schema") from exc
    feature_basis(X)
    if Y.ndim != 2 or Y.shape[0] != X.shape[0]:
        raise ValueError("expected finite Nx2 screen or Nx3 direction target array")
    groups, counts = np.unique(labels, return_counts=True)
    if len(groups) < min_targets or np.any(counts < min_samples_per_target):
        raise ValueError(f"need >={min_targets} distinct targets and >={min_samples_per_target} observations/target; "
                         f"have {len(groups)} and min {min(counts) if len(counts) else 0}")
    if Y.shape[1] != (2 if kind == "screen" else 3):
        raise ValueError("expected 2D screen or 3D directional targets")
    if not np.all(np.isfinite(Y)):
        raise ValueError("nonfinite calibration targets")
    if kind == "spatial":
        if np.any(np.linalg.norm(Y,axis=1) < 1e-6):
            raise ValueError("zero direction target")
        Y /= np.linalg.norm(Y,axis=1)[:,None]
    elif np.any(Y < 0) or np.any(Y > 1):
        raise ValueError("screen targets must be normalized within [0,1]")
    centroids = np.asarray([np.mean(Y[labels == group], axis=0) for group in groups])
    unique_count = len(np.unique(np.round(centroids, 5), axis=0))
    if unique_count < min_targets:
        raise ValueError("target labels refer to duplicate target geometry")
    centered = centroids - centroids.mean(axis=0)
    singular_values = np.linalg.svd(centered, compute_uv=False)
    if len(singular_values) < 2 or singular_values[1] < (0.05 if kind == "screen" else 0.01):
        raise ValueError("target geometry is collinear or has inadequate two-dimensional coverage")
    geometry = {"distinct_target_geometry": unique_count, "spread_singular_values": singular_values.tolist()}
    if kind == "screen":
        span = np.ptp(centroids, axis=0)
        hull = cv2.convexHull(centroids.astype(np.float32))
        area = float(cv2.contourArea(hull))
        if span[0] < 0.4 or span[1] < 0.35 or area < 0.12:
            raise ValueError("screen targets must cover both axes and a useful display area")
        geometry.update({"normalized_span": span.tolist(), "normalized_hull_area": area})
    eye_means = np.asarray([np.mean(X[labels == group, :4], axis=0) for group in groups])
    eye_singular = np.linalg.svd(eye_means - eye_means.mean(axis=0), compute_uv=False)
    if eye_singular[0] < 0.01 or eye_singular[1] < 0.002:
        raise ValueError("eye observations lack two-dimensional response; repeat fixation calibration")
    recorded_contexts = [row.get("context") for row in samples if row.get("context")]
    context = copy.deepcopy({**(recorded_contexts[0] if recorded_contexts else {}), **(context or {})})
    for recorded in recorded_contexts:
        for key in CONTEXT_KEYS:
            if key in context and key in recorded and json.dumps(context[key], sort_keys=True) != json.dumps(recorded[key], sort_keys=True):
                raise ValueError(f"calibration changes tracking context within the session: {key}")
    context.setdefault("feature_version", FEATURE_VERSION)
    context.setdefault("head_model", HEAD_MODEL_VERSION)
    if context["feature_version"] != FEATURE_VERSION:
        raise ValueError("calibration feature version mismatch")
    spatial_frame = "camera"
    rotations = None
    Y_fit = Y
    if kind == "spatial" and all(row.get("head_rotation") is not None for row in samples):
        rotations = np.asarray([row["head_rotation"] for row in samples], dtype=float)
        if rotations.shape != (len(samples), 3, 3) or not np.isfinite(rotations).all():
            raise ValueError("head_rotation must be finite 3x3 matrices")
        for rotation in rotations:
            if (not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-4)
                    or not np.isclose(np.linalg.det(rotation), 1.0, atol=1e-4)):
                raise ValueError("head_rotation must be an orthonormal proper rotation")
        Y_fit = np.einsum("nij,nj->ni", rotations.transpose(0, 2, 1), Y)
        Y_fit = Y_fit / np.linalg.norm(Y_fit, axis=1, keepdims=True)
        spatial_frame = "head"
    coeff, mean, std = _fit_ridge(X, Y_fit, ridge)
    margins = np.maximum(np.ptp(X, axis=0) * .25,
                         np.array([.06,.045,.06,.045,.35,.35,.35,.12,.12,.15]))
    metadata = {**context, "calibrated_utc": datetime.now(timezone.utc).isoformat(),
                "target_groups": len(groups), "observations": len(samples),
                "session_id": session_id or uuid.uuid4().hex,
                "training_digest": hashlib.sha256(X.tobytes()+Y.tobytes()).hexdigest(),
                "training_envelope": {"lower": (X.min(axis=0)-margins).tolist(),
                                      "upper": (X.max(axis=0)+margins).tolist()},
                "screen_size": list(screen_size) if screen_size else None}
    if kind == "spatial":
        metadata["spatial_frame"] = spatial_frame
    model = GazeModel(kind, coeff, mean, std, ridge, {}, metadata)
    train_pred = (model.predict_camera_direction(X, rotations)
                  if kind == "spatial" and spatial_frame == "head" else model.predict(X))
    train = _error_metrics(train_pred, Y, kind, screen_size)
    crossval = None
    if do_group_validation and len(groups) >= min_targets:
        cv_preds = np.zeros_like(Y)
        for group in groups:
            train_mask, test_mask = (labels != group), (labels == group)
            c, mu, sig = _fit_ridge(X[train_mask], Y_fit[train_mask], ridge)
            cv_model = GazeModel(kind, c, mu, sig, ridge, {}, model.metadata)
            if kind == "spatial" and spatial_frame == "head":
                cv_preds[test_mask] = cv_model.predict_camera_direction(X[test_mask], rotations[test_mask])
            else:
                cv_preds[test_mask] = cv_model.predict(X[test_mask])
        crossval = _error_metrics(cv_preds, Y, kind, screen_size)
    model.report = {"training":train, "leave_target_out":crossval,
                    "validation_status":"UNVERIFIED: independent held-out session required",
                    "target_count":len(groups), "samples":len(samples), "geometry":geometry,
                    "physical_accuracy_verified":False}
    model.validate()
    return model


def screen_record(ob: Observation, target_id: str, target_px: tuple[float,float],
                  screen_size: tuple[int,int]) -> dict:
    if ob.features is None or ob.quality < 0.3 or ob.blink:
        raise ValueError("invalid gaze observation")
    feature_basis(ob.features)
    w,h = screen_size
    if min(w, h) < 2 or not np.isfinite(target_px).all() or not (0 <= target_px[0] <= w-1 and 0 <= target_px[1] <= h-1):
        raise ValueError("screen target must be inside the calibrated display pixels")
    return {"features": ob.features.tolist(), "target": [target_px[0]/(w-1), target_px[1]/(h-1)],
            "target_id":target_id, "time_s":ob.timestamp_s, "quality":ob.quality,
            "context": copy.deepcopy(ob.context)}


def spatial_record(ob: Observation, target_id: str, target_world_m: tuple[float,float,float],
                   extrinsics: CameraExtrinsics) -> dict:
    if ob.features is None or ob.eye_origin_camera_m is None or ob.quality < 0.3 or ob.blink:
        raise ValueError("requires detected face, head pose and open eyes")
    target_cam = world_to_camera_point(np.asarray(target_world_m,float),extrinsics)
    direct = target_cam - ob.eye_origin_camera_m
    if np.linalg.norm(direct) < 0.05:
        raise ValueError("3D target too close to eye origin")
    record = {"features": ob.features.tolist(), "target":(direct/np.linalg.norm(direct)).tolist(),
              "target_id":target_id, "target_world_m":list(target_world_m),
              "eye_origin_camera_m": ob.eye_origin_camera_m.tolist(),
              "eye_origin_source": ob.eye_origin_source,
              "time_s": ob.timestamp_s, "quality":ob.quality, "context": copy.deepcopy(ob.context)}
    if ob.head_pose is not None:
        record["head_rotation"] = np.asarray(ob.head_pose.rotation, dtype=float).tolist()
    return record


def save_json(path: str | Path, data: dict):
    """Atomic profile save; never writes frames/biometric images."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(data, indent=2, allow_nan=False).encode("utf-8")
    with tempfile.NamedTemporaryFile(mode="wb",dir=str(path.parent),prefix=".gaze-",delete=False) as f:
        tmp = f.name
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    try:
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def load_json(path: str | Path) -> dict:
    def reject_constant(value):
        raise ValueError(f"nonfinite JSON constant: {value}")
    return json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=reject_constant)


def new_profile(intrinsics: CameraIntrinsics | None = None,
                extrinsics: CameraExtrinsics | None = None, camera_id: str | None = None,
                context: dict | None = None, subject_id: str | None = None) -> dict:
    return {"schema_version":PROFILE_SCHEMA_VERSION,
            "created_utc":datetime.now(timezone.utc).isoformat(),
            "camera_id":camera_id,
            "subject_id":subject_id,
            "context":copy.deepcopy(context or {}),
            "intrinsics":intrinsics.to_dict() if intrinsics else None,
            "extrinsics":extrinsics.to_dict() if extrinsics else None,
            "screen_model":None, "spatial_model":None, "surfaces":[]}


def load_profile(path: str | Path) -> dict:
    data = load_json(path)
    if not isinstance(data, dict):
        raise ValueError("profile must be a JSON object")
    if data.get("schema_version") == 1:
        # Version 1 used opposite neutral head axes and lid-height normalization.
        # Keep measured geometry, but never silently reuse incompatible learned data.
        data = copy.deepcopy(data)
        data["schema_version"] = PROFILE_SCHEMA_VERSION
        for key in ("screen_model", "spatial_model", "screen_validation", "spatial_validation", "drift_validation"):
            data.pop(key, None)
        data["screen_model"] = data["spatial_model"] = None
        data["migration"] = {"from_version":1, "recalibration_required":True,
                             "reason":"Feature geometry and neutral head axes changed in version 2"}
        data.setdefault("context", {})
    if data.get("schema_version") != PROFILE_SCHEMA_VERSION:
        raise ValueError("unknown profile schema; please upgrade explicitly")
    validate_profile(data)
    return data


def profile_geometry_hash(profile: dict) -> str:
    payload = {key:profile.get(key) for key in ("intrinsics", "extrinsics", "context", "subject_id", "camera_id")}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()


def validate_profile(profile: dict) -> None:
    from .types import Surface
    if profile.get("schema_version") != PROFILE_SCHEMA_VERSION:
        raise ValueError("unknown profile schema")
    if not isinstance(profile.get("context", {}), dict):
        raise ValueError("profile context must be a JSON object")
    if profile.get("intrinsics"):
        CameraIntrinsics.from_dict(profile["intrinsics"])
    if profile.get("extrinsics"):
        CameraExtrinsics.from_dict(profile["extrinsics"])
    names = set()
    for item in profile.get("surfaces", []):
        surface = Surface.from_dict(item)
        if surface.name in names:
            raise ValueError("duplicate surface name")
        names.add(surface.name)
    for kind in ("screen", "spatial"):
        if profile.get(kind + "_model"):
            model = GazeModel.from_dict(profile[kind + "_model"])
            if model.kind != kind:
                raise ValueError("model kind does not match profile field")
            bound_hash = model.metadata.get("calibration_context_hash")
            if bound_hash and bound_hash != profile_geometry_hash(profile):
                raise ValueError("profile calibration context changed; recalibration required")


def update_profile(path: str | Path, **changes) -> dict:
    profile = load_profile(path) if Path(path).exists() else new_profile()
    allowed = {"intrinsics","extrinsics","screen_model","spatial_model","surfaces","camera_id",
               "subject_id","context","screen_validation","spatial_validation","drift_validation",
               "camera_validation","camera_calibration_evidence","stereo","notes","drift_correction"}
    if set(changes) - allowed:
        raise ValueError("unknown profile keys")
    changed = {key for key, value in changes.items()
               if json.dumps(profile.get(key),sort_keys=True) != json.dumps(value,sort_keys=True)}
    before = profile.get("context") or {}
    after = (changes.get("context") or {}) if "context" in changed else before
    context_keys = {key for key in (set(before)|set(after))
                    if json.dumps(before.get(key),sort_keys=True) != json.dumps(after.get(key),sort_keys=True)}
    non_display_changed = bool(context_keys-DISPLAY_CONTEXT_KEYS)
    display_changed = bool(context_keys&DISPLAY_CONTEXT_KEYS)
    camera_geometry_changed = "camera_id" in changed and profile.get("camera_id") is not None
    for key in ("image_size","source_identity","camera_id","camera_backend","backend","mirror"):
        if key in before and before.get(key) != after.get(key):
            camera_geometry_changed = True
    if camera_geometry_changed:
        profile["intrinsics"] = profile["extrinsics"] = None
        profile["surfaces"] = []
        profile.pop("stereo",None)
        profile.pop("camera_validation",None)
        profile.pop("camera_calibration_evidence",None)
    if changed & {"intrinsics","subject_id","camera_id"} or non_display_changed:
        for key in ("screen_model","spatial_model","screen_validation","spatial_validation",
                    "drift_validation","drift_correction"):
            profile.pop(key,None)
    elif display_changed:
        for key in ("screen_model","screen_validation","drift_validation","drift_correction"):
            profile.pop(key,None)
    if "intrinsics" in changed:
        profile["extrinsics"] = None
        profile["surfaces"] = []
        profile.pop("stereo",None)
        profile.pop("camera_validation",None)
        profile.pop("camera_calibration_evidence",None)
    if "extrinsics" in changed:
        profile.pop("spatial_model",None)
        profile.pop("spatial_validation",None)
        profile["surfaces"] = []
    if "screen_model" in changed:
        for key in ("screen_validation","drift_validation","drift_correction"):
            profile.pop(key,None)
    if changed & {"spatial_model","surfaces"}:
        profile.pop("spatial_validation",None)
    profile.update(copy.deepcopy(changes))
    profile.setdefault("screen_model",None)
    profile.setdefault("spatial_model",None)
    profile["updated_utc"] = datetime.now(timezone.utc).isoformat()
    context_hash = profile_geometry_hash(profile)
    for key in ("screen_model","spatial_model"):
        if key in changes and profile.get(key):
            profile[key].setdefault("metadata",{})["calibration_context_hash"]=context_hash
    if changed <= {"extrinsics","surfaces","spatial_model","spatial_validation"} and profile.get("screen_model"):
        profile["screen_model"]["metadata"]["calibration_context_hash"]=context_hash
    if (not (changed & {"intrinsics","extrinsics","subject_id","camera_id"})
            and not non_display_changed and profile.get("spatial_model")):
        profile["spatial_model"]["metadata"]["calibration_context_hash"]=context_hash
    validate_profile(profile)
    save_json(path, profile)
    return profile
