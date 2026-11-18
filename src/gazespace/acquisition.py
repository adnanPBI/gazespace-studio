"""Stable target acquisition shared by GUI and integration clients.

The collector measures quality and feature stability while a user is instructed
to fixate a known target. It cannot prove that the user actually looked at it.
No GUI, camera ownership, sleeps, or calibration-profile writes occur here.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
import math
import json
import uuid
import numpy as np

from .types import Observation, CameraExtrinsics


class TargetCollector:
    """Collect a contiguous, settled batch of observations at one known target.

    ``target`` is normalized screen XY for ``kind='screen'`` or world XYZ in
    meters for ``kind='spatial'``. Call ``start()`` when the target is displayed,
    then ``add(observation)`` for each unique processed frame. With no explicit
    start timestamp, the first observation anchors the source-time clock.
    """

    def __init__(self, target_id: str, target, kind: str = "screen", *,
                 min_samples: int = 16, settle_seconds: float = 0.6,
                 min_duration_s: float = 0.6, quality_threshold: float = 0.4,
                 max_seconds: float = 15.0, max_feature_std: float | None = 0.12,
                 max_gap_s: float = 0.25, session_id: str | None = None):
        if kind not in ("screen", "spatial"):
            raise ValueError("target kind must be screen or spatial")
        coords = np.asarray(target, dtype=float)
        if coords.shape != ((2,) if kind == "screen" else (3,)) or not np.isfinite(coords).all():
            raise ValueError("target must contain finite normalized XY or world XYZ")
        if kind == "screen" and (np.any(coords < 0) or np.any(coords > 1)):
            raise ValueError("screen target coordinates must be normalized to [0,1]")
        if not str(target_id).strip():
            raise ValueError("target_id cannot be empty")
        if int(min_samples) != min_samples or min_samples < 4:
            raise ValueError("at least four distinct observations are required")
        if not all(math.isfinite(float(v)) for v in
                   (settle_seconds, min_duration_s, quality_threshold, max_seconds, max_gap_s)):
            raise ValueError("collector thresholds must be finite")
        if settle_seconds < 0.5 or min_duration_s < 0.1:
            raise ValueError("allow at least 0.5 seconds to settle and 0.1 seconds to measure")
        if not 0 <= quality_threshold <= 1 or max_gap_s <= 0:
            raise ValueError("invalid quality or temporal gap threshold")
        if max_seconds <= settle_seconds + min_duration_s:
            raise ValueError("timeout must exceed settling plus minimum acquisition duration")
        if max_feature_std is not None and (not math.isfinite(max_feature_std) or max_feature_std <= 0):
            raise ValueError("feature stability threshold must be positive or None")
        self.target_id = str(target_id)
        self.target = tuple(float(v) for v in coords)
        self.kind = kind
        self.min_samples = int(min_samples)
        self.settle_seconds = float(settle_seconds)
        self.min_duration_s = float(min_duration_s)
        self.quality_threshold = float(quality_threshold)
        self.max_seconds = float(max_seconds)
        self.max_feature_std = max_feature_std
        self.max_gap_s = float(max_gap_s)
        self.session_id = session_id or uuid.uuid4().hex
        self.observations: list[Observation] = []
        self.rejections: Counter[str] = Counter()
        self.state = "waiting"
        self.reason = "ready_to_start"
        self._started: float | None = None
        self._last_time: float | None = None
        self._settle_until: float | None = None
        self._stable_start: float | None = None
        self._context_key = None
        self._image_size = None
        self._method = None
        self._seen = 0

    @property
    def complete(self) -> bool:
        return self.state == "complete"

    def start(self, timestamp_s: float | None = None) -> None:
        if timestamp_s is not None and not math.isfinite(timestamp_s):
            raise ValueError("start timestamp must be finite")
        self.observations.clear()
        self.rejections.clear()
        self._started = timestamp_s
        self._last_time = None
        self._settle_until = None if timestamp_s is None else timestamp_s + self.settle_seconds
        self._stable_start = None
        self._context_key = self._image_size = self._method = None
        self._seen = 0
        self.state, self.reason = "settling", "allow_fixation_to_settle"

    def retry(self, timestamp_s: float | None = None) -> None:
        self.start(timestamp_s)

    def cancel(self) -> None:
        self.observations.clear()
        self.state, self.reason = "cancelled", "cancelled_by_user"

    def _reject(self, reason: str, stamp: float, *, reset: bool = True) -> bool:
        self.reason = reason
        self.rejections[reason] += 1
        if reset:
            self.observations.clear()
            self._stable_start = None
            self._settle_until = stamp + self.settle_seconds
            self.state = "settling"
        return False

    def add(self, observation: Observation) -> bool:
        """Return True only when this observation was accepted for the batch."""
        if self.state in ("waiting", "complete", "timeout", "cancelled", "incompatible"):
            return False
        stamp = float(observation.timestamp_s)
        if not math.isfinite(stamp):
            self.rejections["nonfinite_timestamp"] += 1
            self.reason = "nonfinite_timestamp"
            return False
        if self._started is None:
            self._started, self._settle_until = stamp, stamp + self.settle_seconds
        if stamp < self._started or (self._last_time is not None and stamp <= self._last_time):
            return self._reject("duplicate_or_reversed_timestamp", stamp, reset=False)
        if stamp - self._started > self.max_seconds:
            self.state, self.reason = "timeout", "insufficient_contiguous_quality_samples"
            self.observations.clear()
            return False
        previous_stamp = self._last_time
        self._last_time = stamp
        self._seen += 1
        if previous_stamp is not None and stamp - previous_stamp > self.max_gap_s:
            return self._reject("capture_gap", stamp)
        if observation.blink or not observation.eyes_open:
            return self._reject("blink_or_closed_eyes", stamp)
        if not math.isfinite(float(observation.quality)) or observation.quality < self.quality_threshold:
            return self._reject("low_tracking_quality", stamp)
        if observation.features is None:
            return self._reject("features_unavailable", stamp)
        features = np.asarray(observation.features, dtype=float)
        if features.ndim != 1 or len(features) < 4 or not np.isfinite(features).all():
            return self._reject("nonfinite_or_invalid_features", stamp)
        if self.kind == "spatial":
            origin = observation.eye_origin_camera_m
            if origin is None or np.asarray(origin).shape != (3,) or not np.isfinite(origin).all():
                return self._reject("spatial_eye_origin_unavailable", stamp)
            measured_origin = observation.eye_origin_source in (
                "aligned_depth_pupil_midpoint", "stereo_triangulated_pupil_midpoint", "stereo_pupil_midpoint")
            if observation.head_pose is None and not measured_origin:
                return self._reject("spatial_head_pose_unavailable", stamp)
        # A change in camera resolution, feature method or eye ROI invalidates
        # the batch, rather than blending incompatible coordinate systems.
        try:
            context = json.dumps(getattr(observation, "context", {}), sort_keys=True,
                                 separators=(",", ":"), allow_nan=False,
                                 default=lambda value: value.tolist() if isinstance(value, np.ndarray)
                                 else value.item() if isinstance(value, np.generic) else str(value))
        except (TypeError, ValueError):
            return self._reject("invalid_tracking_context", stamp)
        if self._context_key is None:
            self._context_key = context
            self._image_size = tuple(observation.image_size)
            self._method = observation.pupil_method
        elif (context != self._context_key or tuple(observation.image_size) != self._image_size
              or observation.pupil_method != self._method):
            self.observations.clear()
            self.state, self.reason = "incompatible", "tracking_configuration_changed"
            self.rejections[self.reason] += 1
            return False
        if stamp + 1e-9 < self._settle_until:
            self.state, self.reason = "settling", "allow_fixation_to_settle"
            return False
        if self.max_feature_std is not None and self.observations:
            # Normalized pupil XY and normalized head rotations share roughly
            # comparable scales. Use recent observations to reject a moving
            # fixation, then restart dwell instead of averaging a transition.
            recent = [np.asarray(o.features, float)[:7] for o in self.observations[-24:]]
            candidate = np.vstack([*recent, features[:7]])
            if float(np.max(np.std(candidate, axis=0))) > self.max_feature_std:
                return self._reject("unstable_fixation_or_head_motion", stamp)
        if self._stable_start is None:
            self._stable_start = stamp
        self.observations.append(deepcopy(observation))
        self.state, self.reason = "collecting", "collecting_stable_samples"
        if len(self.observations) >= self.min_samples and stamp - self._stable_start + 1e-9 >= self.min_duration_s:
            self.state, self.reason = "complete", "stable_target_collected"
        return True

    def progress(self) -> dict:
        elapsed = max(0.0, (self._last_time or 0) - self._started) if self._started is not None and self._last_time is not None else 0.0
        stable = max(0.0, self._last_time - self._stable_start) if self._last_time is not None and self._stable_start is not None else 0.0
        fraction = min(1.0, len(self.observations) / self.min_samples,
                       stable / self.min_duration_s)
        return {"state": self.state, "reason": self.reason, "target_id": self.target_id,
                "accepted": len(self.observations), "required": self.min_samples,
                "elapsed_s": elapsed, "stable_duration_s": stable,
                "fraction": 1.0 if self.complete else fraction,
                "rejected": int(sum(self.rejections.values())),
                "rejection_reasons": dict(self.rejections), "frames_seen": self._seen}

    def records(self, *, screen_size: tuple[int, int] | None = None,
                extrinsics: CameraExtrinsics | None = None) -> list[dict]:
        """Convert a completed batch to core calibration records."""
        if not self.complete:
            raise ValueError("target collection is not complete")
        from .calibration import screen_record, spatial_record
        if self.kind == "screen":
            if not screen_size or len(screen_size) != 2 or min(screen_size) < 2:
                raise ValueError("screen dimensions >=2 are required")
            target_px = (self.target[0] * (screen_size[0] - 1),
                         self.target[1] * (screen_size[1] - 1))
            records = [screen_record(o, self.target_id, target_px, screen_size)
                       for o in self.observations]
        else:
            if extrinsics is None:
                raise ValueError("world extrinsics are required for spatial samples")
            records = [spatial_record(o, self.target_id, self.target, extrinsics)
                       for o in self.observations]
        for row in records:
            row["session_id"] = self.session_id
            row["acquisition"] = {"settle_seconds": self.settle_seconds,
                                  "stable_duration_s": self.progress()["stable_duration_s"],
                                  "target_assumption": "user_instructed_fixation"}
        return records
