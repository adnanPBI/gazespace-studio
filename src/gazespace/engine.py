"""End-to-end gaze engine; dependency-injectable into an existing Windows app."""
from __future__ import annotations
from collections import deque
from pathlib import Path
import time
import numpy as np
import cv2
from .types import Observation, GazeResult, CameraIntrinsics, CameraExtrinsics, Surface, Frame
from .filtering import OneEuro
from .geometry import camera_to_world_point, camera_to_world_direction, first_surface_hit, unit_vector
from .calibration import GazeModel, load_profile
from .vision import GazeTracker, FEATURE_VERSION, FEATURE_NAMES
from .lighting import frame_quality

class GazeEngine:
    def __init__(self, tracker: GazeTracker,
                 screen_model: GazeModel | None = None,
                 spatial_model: GazeModel | None = None,
                 extrinsics: CameraExtrinsics | None = None,
                 surfaces: list[Surface] | None = None,
                 min_quality: float = 0.35, filter_min_cutoff: float = 1.2,
                 filter_beta: float = 0.015, source_context: dict | None = None):
        if not np.isfinite(min_quality) or not 0 <= min_quality <= 1:
            raise ValueError("minimum quality must be in [0,1]")
        self.tracker = tracker
        self.screen_model, self.spatial_model = screen_model, spatial_model
        self.extrinsics, self.surfaces = extrinsics, surfaces or []
        self.min_quality = min_quality
        self.source_context = dict(source_context or {})
        if extrinsics is not None:
            extrinsics.validate()
        for surface in self.surfaces:
            surface.validate()
        self.last_capture_timestamp = None
        self.screen_filter = OneEuro(min_cutoff=filter_min_cutoff, beta=filter_beta)
        self.direction_filter = OneEuro(min_cutoff=filter_min_cutoff, beta=filter_beta)
        self.recent_times = deque(maxlen=250)
        self.total, self.valid, self.rejected, self.blinks = 0, 0, 0, 0
        self.last_result: GazeResult | None = None
        self.last_observation: Observation | None = None
        self.last_lighting: dict | None = None

    @classmethod
    def from_profile(cls, path: str | Path, *, mode: str = "rgb",
                     roi_normalized=None, face_model: str | None = None,
                     pupil_polarity: str = "dark", source_context: dict | None = None, **kwargs):
        profile = load_profile(path)
        intr = CameraIntrinsics.from_dict(profile["intrinsics"]) if profile.get("intrinsics") else None
        ext = CameraExtrinsics.from_dict(profile["extrinsics"]) if profile.get("extrinsics") else None
        sm = GazeModel.from_dict(profile["screen_model"]) if profile.get("screen_model") else None
        sp = GazeModel.from_dict(profile["spatial_model"]) if profile.get("spatial_model") else None
        for model in (sm, sp):
            if model and model.metadata.get("tracking_mode") and model.metadata["tracking_mode"] != mode:
                raise ValueError("model was calibrated for a different tracking mode; recalibrate")
            if model and model.metadata.get("pupil_polarity") and model.metadata["pupil_polarity"] != pupil_polarity:
                raise ValueError("model was calibrated for different IR pupil polarity; recalibrate")
            if model and source_context:
                mismatch = model.context_mismatches(source_context)
                if mismatch:
                    raise ValueError("profile source context mismatch: " + ", ".join(mismatch))
        if sp and (not intr or intr.source not in ("measured", "factory") or ext is None):
            raise ValueError("spatial model requires measured camera intrinsics + world extrinsics")
        tracker = GazeTracker(intr, mode, face_model, roi_normalized, pupil_polarity)
        for model in (sm, sp):
            if model:
                mismatch = model.context_mismatches(tracker.context())
                if mismatch:
                    tracker.close()
                    raise ValueError("profile tracking context mismatch: " + ", ".join(mismatch))
        return cls(tracker, sm, sp, ext,
                   [Surface.from_dict(s) for s in profile.get("surfaces",[])],
                   source_context=source_context, **kwargs)

    def process(self, image_bgr: np.ndarray, timestamp_s: float | None = None) -> GazeResult:
        return self.process_frame(Frame(image_bgr, time.monotonic() if timestamp_s is None else timestamp_s))

    def process_frame(self, frame: Frame) -> GazeResult:
        started = time.monotonic()
        image_bgr, timestamp_s = frame.image, frame.timestamp_s
        ob = self.tracker.process(image_bgr, timestamp_s)
        self._apply_depth(frame, ob)
        result = self.process_observation(ob)
        if self.total == 1 or self.total % 20 == 0:
            self.last_lighting = frame_quality(image_bgr)
        if self.last_lighting:
            result.warnings.extend(self.last_lighting["warnings"])
        result.processing_ms = (time.monotonic() - started)*1000
        self.recent_times.append(result.processing_ms)
        self.last_observation = ob
        return result

    def _apply_depth(self, frame: Frame, ob: Observation) -> None:
        """Use calibrated aligned depth as a visible-eye surface origin estimate.

        The result is explicitly not the optical eyeball center. Once trained with
        this origin source, missing depth invalidates the corresponding fit.
        """
        ob.context["eye_origin_source"] = ob.eye_origin_source
        if frame.depth_m is None:
            return
        if ob.features is None or ob.blink:
            return
        metadata = frame.metadata or {}
        raw_intrinsics = metadata.get("depth_intrinsics")
        if metadata.get("depth_aligned_to") != "color" or not metadata.get("depth_deprojection_supported", True) or not raw_intrinsics:
            ob.warnings.append("depth_alignment_or_intrinsics_unavailable")
            ob.eye_origin_camera_m = None
            ob.eye_origin_source = "depth_unavailable"
        else:
            from .depth import eye_origin_from_depth
            intrinsics = CameraIntrinsics.from_dict(raw_intrinsics)
            point = eye_origin_from_depth(frame.depth_m, ob.pupils_px, intrinsics)
            if point is None:
                ob.eye_origin_camera_m = None
                ob.eye_origin_source = "depth_unavailable"
                ob.warnings.append("invalid_depth_at_pupils")
            else:
                ob.eye_origin_camera_m = point
                ob.eye_origin_source = "aligned_depth_pupil_midpoint"
                ob.features = ob.features.copy()
                ob.features[-1] = float(point[2])
                ob.warnings = [w for w in ob.warnings if w != "eye_origin_uses_generic_anatomy"]
                ob.warnings.append("depth_origin_is_visible_eye_surface_not_optical_center")
        ob.context["eye_origin_source"] = ob.eye_origin_source

    def invalidate(self, reason: str, timestamp_s: float | None = None) -> GazeResult:
        self.screen_filter.reset()
        self.direction_filter.reset()
        self.last_observation = None
        self.last_capture_timestamp = None
        stamp = time.monotonic() if timestamp_s is None else float(timestamp_s)
        self.last_result = GazeResult(stamp, False, reason, "unavailable", 0.0,
                                      capture_timestamp_s=stamp)
        return self.last_result

    def reset(self) -> None:
        """Clear all temporal prediction state after source restart or seek."""
        self.invalidate("tracking_reset")

    def process_observation(self, ob: Observation) -> GazeResult:
        if not np.isfinite(ob.timestamp_s):
            raise ValueError("observation timestamp must be finite")
        self.total += 1
        quality = float(np.clip(ob.quality, 0, 1)) if np.isfinite(ob.quality) else 0.0
        pupil_points = {side:tuple(float(v) for v in xy) for side,xy in ob.pupils_px.items()
                        if len(xy) == 2 and np.isfinite(xy).all()}
        context = {**self.source_context, **ob.context, "image_size": list(ob.image_size)}
        result = GazeResult(ob.timestamp_s, False, ob.message, ob.pupil_method,
                            quality, pupils_px=pupil_points, capture_timestamp_s=ob.timestamp_s,
                            warnings=list(ob.warnings), tracking_context=context,
                            eye_origin_source=ob.eye_origin_source)
        if ob.head_pose is not None:
            angles = (ob.head_pose.yaw_deg, ob.head_pose.pitch_deg, ob.head_pose.roll_deg)
            if np.isfinite(angles).all():
                result.yaw_pitch_roll_deg = tuple(float(v) for v in angles)
            else:
                result.warnings.append("nonfinite_head_pose")
        self.last_result = result
        if ob.blink:
            self.blinks += 1
        if self.last_capture_timestamp is not None and ob.timestamp_s <= self.last_capture_timestamp:
            result.reason = "duplicate_or_reversed_capture_timestamp"
            self.screen_filter.reset()
            self.direction_filter.reset()
            self.rejected += 1
            return result
        self.last_capture_timestamp = ob.timestamp_s
        features_valid = ob.features is not None and np.asarray(ob.features).shape == (len(FEATURE_NAMES),) and np.isfinite(ob.features).all()
        if not features_valid or quality < self.min_quality or ob.blink or not ob.eyes_open:
            if ob.features is not None and not features_valid:
                result.reason = "nonfinite_or_invalid_features"
            elif not ob.eyes_open:
                result.reason = ob.message if ob.blink else "eyes_not_confirmed_open"
            self.screen_filter.reset()
            self.direction_filter.reset()
            self.rejected += 1
            return result

        def model_ready(model) -> bool:
            if model is None:
                return False
            mismatch = model.context_mismatches(context) if hasattr(model, "context_mismatches") else []
            outside = model.outside_envelope(ob.features) if hasattr(model, "outside_envelope") else []
            if mismatch:
                if not result.valid:
                    result.reason = "calibration_context_mismatch"
                result.warnings.append("Changed calibration context: " + ", ".join(mismatch))
                return False
            if outside:
                if not result.valid:
                    result.reason = "outside_calibrated_operating_envelope"
                result.warnings.append("Outside learned range: " + ", ".join(outside))
                return False
            if hasattr(model, "fingerprint"):
                result.model_fingerprint = model.fingerprint
            return True

        if model_ready(self.screen_model):
            try:
                screen = self.screen_model.predict(ob.features)[0]
            except (ValueError, FloatingPointError, np.linalg.LinAlgError) as exc:
                screen = np.array([np.nan, np.nan])
                result.warnings.append(str(exc))
            # Do not silently clamp off-screen data into plausible in-screen coordinates.
            if np.all(np.isfinite(screen)) and np.all(screen >= 0) and np.all(screen <= 1):
                filtered = self.screen_filter.update(screen, ob.timestamp_s)
                size = self.screen_model.metadata["screen_size"]
                result.gaze_screen_normalized = tuple(float(x) for x in filtered)
                scale = np.asarray(size, dtype=float) - 1
                result.raw_gaze_screen_px = tuple(float(v) for v in screen * scale)
                result.gaze_screen_px = tuple(float(v) for v in filtered * scale)
                result.valid = result.screen_valid = True
                result.reason = "screen_gaze_estimated"
            else:
                self.screen_filter.reset()
                result.reason = "screen_prediction_out_of_bounds"
        elif self.screen_model is not None:
            self.screen_filter.reset()
        if model_ready(self.spatial_model):
            if self.extrinsics is None or ob.eye_origin_camera_m is None:
                result.warnings.append("world_geometry_unavailable_for_this_frame")
            else:
                try:
                    predicted_cam = self.spatial_model.predict(ob.features)[0]
                    origin = camera_to_world_point(ob.eye_origin_camera_m, self.extrinsics)
                    direction = camera_to_world_direction(predicted_cam, self.extrinsics)
                    direction = self.direction_filter.update(direction, ob.timestamp_s)
                    direction = unit_vector(direction)
                    result.ray_origin_world_m = tuple(float(x) for x in origin)
                    result.ray_direction_world = tuple(float(x) for x in direction)
                    hit = first_surface_hit(origin, direction, self.surfaces)
                    if hit:
                        surface, coord = hit
                        result.surface_name = surface.name
                        result.surface_xy_m = coord["xy_m"]
                        result.surface_xy_px = coord["xy_px"]
                        result.valid = result.spatial_valid = True
                        result.reason = "spatial_surface_hit"
                    else:
                        result.spatial_valid = True
                        if not result.valid:
                            result.valid = True
                            result.reason = "spatial_ray_only_no_surface_hit"
                except (ValueError, FloatingPointError, np.linalg.LinAlgError) as e:
                    self.direction_filter.reset()
                    result.warnings.append(f"invalid_spatial_ray: {e}")
        elif self.spatial_model is not None:
            self.direction_filter.reset()
        if self.screen_model is None and self.spatial_model is None:
            result.reason = "tracking_only_uncalibrated"
        if result.valid:
            self.valid += 1
        else:
            self.rejected += 1
        return result

    def metrics(self) -> dict:
        latencies = np.asarray(tuple(self.recent_times),float)
        return {"frames":self.total, "gaze_valid":self.valid,
                "rejected":self.rejected, "blink_frames":self.blinks,
                "processing_median_ms":float(np.median(latencies)) if len(latencies) else None,
                "processing_p95_ms":float(np.percentile(latencies,95)) if len(latencies) else None,
                "calibrated_screen":self.screen_model is not None,
                "calibrated_spatial":self.spatial_model is not None,
                "physical_accuracy_verified":False,
                "lighting":self.last_lighting}

    def close(self):
        self.tracker.close()


def draw_diagnostics(image_bgr: np.ndarray, result: GazeResult,
                     ob: Observation | None = None, fps: float | None = None) -> np.ndarray:
    frame = image_bgr.copy()
    h,w = frame.shape[:2]
    for side, pupil in result.pupils_px.items():
        cv2.circle(frame, (round(pupil[0]), round(pupil[1])), 5, (0,255,170), 2)
    if ob and ob.landmarks_px:
        for i in (1,152,33,263,61,291):
            if i in ob.landmarks_px:
                x,y = ob.landmarks_px[i]
                cv2.circle(frame,(round(x),round(y)),3,(255,125,40),-1)
    cv2.rectangle(frame, (6,6), (min(w-6,600),min(h-6,115)),(18,24,34),-1)
    lines = [f"GAZESPACE  {'VALID' if result.valid else 'NO GAZE'}  | {result.reason}",
             f"quality {result.quality:.2f} | {result.source} | processing {result.processing_ms:.1f}ms",
             f"FPS {fps:.1f}" if fps is not None else "FPS sampling...",
             "Q quit | gaze requires calibration, not landmarks alone"]
    for i,line in enumerate(lines):
        cv2.putText(frame,line[:85],(14,27+i*24),cv2.FONT_HERSHEY_SIMPLEX,0.52,
                    (120,235,190) if i==0 else (210,218,224),1,cv2.LINE_AA)
    if result.gaze_screen_normalized:
        # visualizations are camera overlays, NOT actual physical on-screen markers
        view_w,view_h = min(300,w-24), min(170,h-140)
        if view_w>50 and view_h>50:
            x0,y0 = w-view_w-12,h-view_h-12
            cv2.rectangle(frame,(x0,y0),(x0+view_w,y0+view_h),(40,48,65),-1)
            cv2.rectangle(frame,(x0,y0),(x0+view_w,y0+view_h),(170,170,170),1)
            gx,gy = result.gaze_screen_normalized
            cv2.drawMarker(frame,(int(x0+gx*view_w),int(y0+gy*view_h)),
                           (0,215,255),cv2.MARKER_CROSS,18,2)
            cv2.putText(frame,"SCREEN GAZE (INSET)",(x0+7,y0+18),
                        cv2.FONT_HERSHEY_SIMPLEX,0.4,(220,220,220),1)
    return frame
