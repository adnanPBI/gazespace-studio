"""Facial/iris landmarks, IR fallback pupil tracking, approximate head pose.

MediaPipe iris landmarks DO NOT independently estimate where a person looks:
all gaze estimation is explicitly supervised and user-calibrated elsewhere.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import numpy as np
import cv2
from .types import Observation, CameraIntrinsics
from .geometry import approximate_intrinsics, estimate_head_pose, eye_origin_from_pose, HEAD_MODEL_VERSION
from .pupil import detect_pupil, preprocess_ir

RIGHT_CORNERS = (33, 133)
LEFT_CORNERS = (362, 263)
RIGHT_LIDS = (159, 145)
LEFT_LIDS = (386, 374)
RIGHT_IRIS = 468
LEFT_IRIS = 473
FEATURE_NAMES = ("right_iris_x", "right_iris_y", "left_iris_x", "left_iris_y",
                 "yaw_45", "pitch_45", "roll_45", "nose_x", "nose_y", "depth_m")
FEATURE_VERSION = 2


def validate_rois(rois):
    if rois is None:
        return None
    if set(rois) != {"left", "right"}:
        raise ValueError("both left and right normalized eye ROIs are required")
    result = {}
    for side, rect in rois.items():
        values = np.asarray(rect, dtype=float)
        if values.shape != (4,) or not np.isfinite(values).all():
            raise ValueError("ROI must be finite normalized x,y,width,height")
        x, y, w, h = values
        if min(x, y) < 0 or min(w, h) <= 0 or x + w > 1 or y + h > 1:
            raise ValueError("normalized ROI must fit inside the image")
        result[side] = tuple(float(v) for v in values)
    return result


def eye_aspect_ratio(lm: dict[int, tuple[float, float]], side: str) -> float:
    indices = (33, 160, 158, 133, 153, 144) if side == "right" else (362, 385, 387, 263, 373, 380)
    if any(i not in lm for i in indices):
        return 0.0
    p = [np.asarray(lm[i], dtype=float) for i in indices]
    if not all(pt.shape == (2,) and np.isfinite(pt).all() for pt in p):
        return 0.0
    return float((np.linalg.norm(p[1] - p[5]) + np.linalg.norm(p[2] - p[4])) /
                 max(1e-6, 2 * np.linalg.norm(p[0] - p[3])))


def eye_rel_pos(iris, c0, c1, top, bottom) -> tuple[float, float]:
    """Eye-frame coordinates; vertical displacement uses eye width, not lid opening.

    Normalizing vertical position by a closing lid gap produced large false gaze
    jumps. Version 2 uses stable corner distance on both axes, with neutral .5.
    """
    ir, a, b, top, bottom = (np.asarray(p, dtype=float) for p in (iris, c0, c1, top, bottom))
    horizontal = b - a
    width = float(np.linalg.norm(horizontal))
    if width < 4:
        raise ValueError("insufficient eye pixel resolution")
    axis = horizontal / width
    vertical = np.array([-axis[1], axis[0]])
    if vertical @ (bottom - top) < 0:
        vertical *= -1
    x = float((ir - a) @ axis / width)
    y = float(0.5 + (ir - (top + bottom) / 2) @ vertical / width)
    return x, y


def observation_from_landmarks(lm: dict[int, tuple[float, float]], image_size: tuple[int, int],
                               timestamp_s: float, intrinsics: CameraIntrinsics | None = None,
                               ir_image: np.ndarray | None = None, pupil_polarity: str = "dark",
                               min_quality: float = .30) -> Observation:
    w, h = image_size
    ob = Observation(timestamp_s, image_size, landmarks_px=lm, face_detected=True)
    needed = (RIGHT_IRIS, LEFT_IRIS, *RIGHT_CORNERS, *LEFT_CORNERS,
              *RIGHT_LIDS, *LEFT_LIDS, 1)
    if any(i not in lm for i in needed):
        ob.message = "missing_iris_landmarks"
        return ob
    if not all(np.isfinite(lm[i]).all() for i in needed):
        ob.message = "nonfinite_landmarks"
        return ob
    ear_r, ear_l = eye_aspect_ratio(lm, "right"), eye_aspect_ratio(lm, "left")
    ob.quality_metrics.update({"right_ear": ear_r, "left_ear": ear_l})
    ob.blink = (ear_r < 0.13 or ear_l < 0.13)
    ob.eyes_open = not ob.blink
    if ob.blink:
        ob.message = "blink_or_occluded_eye"
        return ob
    p_r, p_l = lm[RIGHT_IRIS], lm[LEFT_IRIS]
    quality = 0.95
    for side, center, corners, lids in (("right", p_r, RIGHT_CORNERS, RIGHT_LIDS),
                                       ("left", p_l, LEFT_CORNERS, LEFT_LIDS)):
        if ir_image is not None:
            # restrict ellipse fit to eye region; do not mistake cheek/nose shadows for pupil
            pts = np.array([lm[i] for i in (*corners, *lids)], float)
            left, top = pts.min(axis=0)
            right, bottom = pts.max(axis=0)
            padding_x = max(4, (right - left) * 0.18)
            padding_y = max(4, (bottom - top) * 0.9)
            roi = (int(left-padding_x), int(top-padding_y),
                   int((right-left)+2*padding_x), int((bottom-top)+2*padding_y))
            pupil = detect_pupil(ir_image, roi, polarity=pupil_polarity)
            if pupil is None or pupil.confidence < 0.4:
                ob.message = f"ir_pupil_unavailable_{side}"
                return ob
            center = pupil.xy
            quality = min(quality, pupil.confidence)
            ob.quality_metrics[f"{side}_pupil_confidence"] = pupil.confidence
        ob.pupils_px[side] = (float(center[0]), float(center[1]))
    try:
        right_xy = eye_rel_pos(ob.pupils_px["right"], lm[33], lm[133], lm[159], lm[145])
        left_xy = eye_rel_pos(ob.pupils_px["left"], lm[362], lm[263], lm[386], lm[374])
    except ValueError:
        ob.message = "insufficient_eye_resolution"
        return ob
    if any(not 0 <= value <= 1 for value in (*right_xy, *left_xy)):
        ob.message = "pupil_outside_eye_geometry"
        return ob
    intr = intrinsics if intrinsics is not None else approximate_intrinsics(w, h)
    pose = estimate_head_pose(lm, intr)
    ob.head_pose = pose
    if pose is None:
        ob.message = "head_pose_unavailable"
        return ob
    if abs(pose.yaw_deg) > 65 or abs(pose.pitch_deg) > 60 or abs(pose.roll_deg) > 60:
        ob.message = "head_pose_outside_operating_range"
        return ob
    yaw, pitch, roll, depth = (pose.yaw_deg / 45.0, pose.pitch_deg / 45.0,
                               pose.roll_deg / 45.0, float(pose.translation_m[2]))
    ob.eye_origin_camera_m = eye_origin_from_pose(pose)
    ob.eye_origin_source = "generic_face_pnp"
    ob.warnings.append("eye_origin_uses_generic_anatomy")
    if intr.source == "approximate":
        ob.warnings.append("approximate_camera_intrinsics")
    eye_span = float(np.linalg.norm(np.array(lm[33]) - np.array(lm[263])))
    reprojection_limit = max(6.0, eye_span * 0.12)
    ob.quality_metrics["head_reprojection_px"] = pose.reprojection_px
    if pose.reprojection_px > reprojection_limit:
        ob.message = "head_pose_reprojection_too_large"
        return ob
    quality *= float(np.clip(1.0 - pose.reprojection_px / (2 * reprojection_limit), 0, 1))
    ob.features = np.asarray([*right_xy, *left_xy, yaw, pitch, roll,
                               lm[1][0] / (w-1), lm[1][1] / (h-1), depth], dtype=float)
    ob.quality = quality
    ob.pupil_method = "iris_landmarks" if ir_image is None else "landmark_roi_ir_ellipse"
    ob.message = "ok" if ob.quality >= min_quality else "low_quality_landmarks"
    return ob


def observation_from_rois(image: np.ndarray, timestamp_s: float,
                          roi_normalized: dict[str, tuple[float, float, float, float]],
                          polarity: str = "dark", min_radius: float = 3,
                          max_radius: float = 40, min_quality: float = .30) -> Observation:
    h, w = image.shape[:2]
    ob = Observation(timestamp_s, (w, h), pupil_method="ir_manual_roi")
    try:
        roi_normalized = validate_rois(roi_normalized)
    except ValueError:
        ob.message = "invalid_roi"
        return ob
    if roi_normalized is None:
        ob.message = "roi_missing"
        return ob
    ob.warnings.append("static_eye_rois_require_stable_head_position")
    vals = []
    scores = []
    for side in ("right", "left"):
        if side not in roi_normalized:
            ob.message = "roi_missing"
            return ob
        rect = roi_normalized[side]
        if len(rect) != 4 or not all(np.isfinite(v) for v in rect):
            ob.message = "invalid_roi"
            return ob
        x,y,rw,rh = rect
        if rw <= 0 or rh <= 0:
            ob.message = "invalid_roi"
            return ob
        px_rect = (round(x*w), round(y*h), round(rw*w), round(rh*h))
        pupil = detect_pupil(image, px_rect, polarity, min_radius, max_radius)
        if pupil is None or pupil.confidence < min_quality:
            ob.message = f"pupil_missing_{side}"
            return ob
        ob.pupils_px[side] = pupil.xy
        vals.extend(((pupil.xy[0]-px_rect[0])/px_rect[2],
                     (pupil.xy[1]-px_rect[1])/px_rect[3]))
        scores.append(pupil.confidence)
        ob.quality_metrics[f"{side}_pupil_confidence"] = pupil.confidence
    ob.eyes_open, ob.blink = True, False
    ob.quality = float(min(scores))
    ob.features = np.array([*vals, 0, 0, 0, 0.5, 0.5, 0], dtype=float)
    ob.message = "ok" if ob.quality >= min_quality else "low_quality_pupil"
    return ob


class FaceLandmarkDetector:
    """MediaPipe legacy mesh or Tasks FaceLandmarker (with a supplied model)."""
    def __init__(self, task_model: str | None = None, confidence: float = 0.5):
        try:
            import mediapipe as mp
        except ImportError as e:
            raise RuntimeError("Face tracking needs 'pip install gazespace[face]' or --face-model .task") from e
        self.mp = mp
        self.task_model = task_model
        self.previous_timestamp_ms = -1
        if task_model:
            from mediapipe.tasks import python
            from mediapipe.tasks.python import vision
            self.vision = vision
            options = vision.FaceLandmarkerOptions(
                base_options=python.BaseOptions(model_asset_path=task_model),
                running_mode=vision.RunningMode.VIDEO,
                num_faces=1, min_face_detection_confidence=confidence,
                min_tracking_confidence=confidence)
            self.worker = vision.FaceLandmarker.create_from_options(options)
            self.kind = "tasks"
        elif hasattr(mp, "solutions") and hasattr(mp.solutions, "face_mesh"):
            self.worker = mp.solutions.face_mesh.FaceMesh(
                static_image_mode=False, max_num_faces=1, refine_landmarks=True,
                min_detection_confidence=confidence, min_tracking_confidence=confidence)
            self.kind = "legacy"
        else:
            raise RuntimeError("Installed MediaPipe has no legacy FaceMesh. Supply --face-model /path/to/face_landmarker.task")

    def detect(self, image_bgr: np.ndarray, timestamp_s: float,
               ir_mode: bool = False) -> dict[int, tuple[float, float]] | None:
        h, w = image_bgr.shape[:2]
        if ir_mode:
            enhanced = preprocess_ir(image_bgr)
            image_bgr = cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)
        rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        if self.kind == "tasks":
            stamp = max(self.previous_timestamp_ms + 1, int(timestamp_s * 1000))
            self.previous_timestamp_ms = stamp
            result = self.worker.detect_for_video(
                self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb), stamp)
            faces = result.face_landmarks
        else:
            result = self.worker.process(rgb)
            faces = [f.landmark for f in result.multi_face_landmarks] if result.multi_face_landmarks else []
        if not faces:
            return None
        return {i: (float(p.x * w), float(p.y * h)) for i, p in enumerate(faces[0])}

    def close(self):
        self.worker.close()


class GazeTracker:
    def __init__(self, intrinsics: CameraIntrinsics | None = None,
                 mode: str = "rgb", face_model: str | None = None,
                 roi_normalized: dict[str, tuple[float, float, float, float]] | None = None,
                 pupil_polarity: str = "dark", min_quality: float = 0.30,
                 detector: FaceLandmarkDetector | None = None):
        if mode not in ("rgb", "ir", "ir-roi"):
            raise ValueError("mode must be rgb, ir or ir-roi")
        if pupil_polarity not in ("dark", "bright"):
            raise ValueError("pupil polarity must be dark or bright")
        if intrinsics is not None:
            intrinsics.validate()
        self.intrinsics, self.mode = intrinsics, mode
        self.roi_normalized, self.pupil_polarity = validate_rois(roi_normalized), pupil_polarity
        self.face_model_sha256 = (None if mode == "ir-roi" else
                                  hashlib.sha256(Path(face_model).read_bytes()).hexdigest()
                                  if face_model else "mediapipe-legacy-0.10.21")
        self.min_quality = min_quality
        if mode == "ir-roi" and self.roi_normalized is None:
            raise ValueError("ir-roi mode needs manually specified eye ROIs")
        self.detector = detector if detector is not None else (
            None if mode == "ir-roi" else FaceLandmarkDetector(task_model=face_model))

    def context(self, image_size=None) -> dict:
        context = {"feature_version": FEATURE_VERSION, "tracking_mode": self.mode,
                   "pupil_polarity": self.pupil_polarity, "head_model": HEAD_MODEL_VERSION,
                   "roi_normalized": {key:list(value) for key,value in self.roi_normalized.items()} if self.roi_normalized else None,
                   "feature_method": {"rgb":"iris_landmarks", "ir":"landmark_roi_ir_ellipse", "ir-roi":"ir_manual_roi"}[self.mode]}
        context["intrinsics_fingerprint"] = (hashlib.sha256(json.dumps(self.intrinsics.to_dict(), sort_keys=True).encode()).hexdigest()
                                              if self.intrinsics is not None else "approximate")
        if self.mode != "ir-roi":
            context["face_model_sha256"] = self.face_model_sha256
        if image_size is not None:
            context["image_size"] = list(image_size)
        return context

    def process(self, image_bgr: np.ndarray, timestamp_s: float) -> Observation:
        if image_bgr is None or image_bgr.ndim != 3 or image_bgr.shape[2] != 3 or image_bgr.dtype != np.uint8:
            raise ValueError("tracker expects uint8 BGR image")
        if not np.isfinite(timestamp_s):
            raise ValueError("capture timestamp must be finite")
        h,w = image_bgr.shape[:2]
        if self.intrinsics and (self.intrinsics.width != w or self.intrinsics.height != h):
            return Observation(timestamp_s, (w, h), message="intrinsics_resolution_mismatch")
        if self.mode == "ir-roi":
            ob = observation_from_rois(image_bgr, timestamp_s, self.roi_normalized,
                                       self.pupil_polarity, min_quality=self.min_quality)
        elif self.detector is not None:
            lm = self.detector.detect(image_bgr, timestamp_s, ir_mode=self.mode == "ir")
            if lm:
                ob = observation_from_landmarks(lm, (w,h), timestamp_s, self.intrinsics,
                                                ir_image=image_bgr if self.mode == "ir" else None,
                                                pupil_polarity=self.pupil_polarity,
                                                min_quality=self.min_quality)
            else:
                ob = Observation(timestamp_s, (w, h), message="face_or_iris_not_detected")
        else:
            ob = Observation(timestamp_s, (w, h), message="detector_unavailable")
        # Never switch calibrated feature semantics or bypass a blink with ROI fallback.
        ob.context = self.context((w, h))
        ob.context["eye_origin_source"] = ob.eye_origin_source
        return ob

    def close(self):
        if self.detector is not None:
            self.detector.close()
