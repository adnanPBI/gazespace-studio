"""Guided calibration and validation workflows using settled target acquisition.

Camera/tracker resources are owned by the caller. Tk is only a presentation
layer; ``ScreenSession.prepare`` and ``consume_frame`` also work headlessly.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import uuid
import numpy as np
import cv2

from .acquisition import TargetCollector
from .clock import host_now_s
from .analytics import compare_anchors
from .capture import FrameSource
from .vision import GazeTracker
from .calibration import (GazeModel, fit_gaze_model, load_profile, update_profile,
                          new_profile, profile_geometry_hash)
from .validation import validate_gaze_records
from .geometry import world_to_camera_point
from .types import CameraExtrinsics, CameraIntrinsics, Frame


def _display_size(size):
    if len(size) != 2 or any(int(v) != v or v < 2 for v in size):
        raise ValueError("display dimensions must be integers >=2")
    return int(size[0]), int(size[1])


def grid_targets(size: tuple[int, int], cols: int = 4, rows: int | None = None):
    rows = rows or cols
    if cols < 3 or rows < 3:
        raise ValueError("calibration grid needs at least 3x3 targets")
    width, height = np.asarray(_display_size(size), float) - 1
    xs, ys = np.linspace(.09, .91, cols), np.linspace(.18, .82, rows)
    return [(f"{r}-{c}", (float(x * width), float(y * height)))
            for r, y in enumerate(ys)
            for c, x in (enumerate(xs) if r % 2 == 0 else reversed(list(enumerate(xs))))]


def offset_validation_targets(size: tuple[int, int]):
    width, height = np.asarray(_display_size(size), float) - 1
    return [(f"validation-{i}", (x * width, y * height)) for i, (x, y) in enumerate([
        (.25, .20), (.75, .20), (.50, .35), (.20, .65), (.80, .65), (.50, .80),
        (.32, .50), (.68, .50), (.50, .50)])]


def _capabilities(source):
    getter = getattr(source, "capabilities", None)
    return dict(getter() or {}) if getter else {}


def _tracking_context(profile, tracker, source, image_size=None, camera_id=None):
    """Build current context from actual inputs, not the saved model's identity."""
    context = deepcopy(tracker.context(image_size))
    caps = _capabilities(source)
    explicit_id = camera_id if camera_id is not None else (
        getattr(source, "device", None) if getattr(source, "device", None) is not None
        else caps.get("source", caps.get("serial")))
    if explicit_id is not None:
        context["camera_id"] = str(explicit_id)
    backend = caps.get("requested_backend", caps.get("backend"))
    if backend is not None:
        context["backend"] = str(backend).lower()
        context["camera_backend"] = str(backend).lower()
    context["subject_id"] = profile.get("subject_id", profile.get("context", {}).get("subject_id"))
    context["mirror"] = False  # CLI paths do not mirror acquisition frames.
    # Requested controls, when present, match GUI setting semantics. A driver
    # readback is separately reported by the source; do not pretend it is the
    # user's requested value when a backend expresses exposure differently.
    context.update({"exposure_native": None, "exposure_us": None, "gain": None, "ir_bit_depth": None})
    controls = caps.get("controls", {})
    for source_key, key in (("exposure_native", "exposure_native"),
                            ("ExposureTime", "exposure_us"),
                            ("gain_native", "gain"), ("Gain", "gain")):
        if source_key in controls and "requested" in controls[source_key]:
            context[key] = controls[source_key]["requested"]
    if hasattr(source, "ir_bit_depth"):
        context["ir_bit_depth"] = source.ir_bit_depth
    return context


def _bind_observation(observation, profile, tracker, source, camera_id=None):
    context = _tracking_context(profile, tracker, source, observation.image_size, camera_id)
    context.update(deepcopy(observation.context))
    context["image_size"] = list(observation.image_size)
    observation.context = context
    return observation


def _validate_context(model, context, profile):
    mismatch = model.context_mismatches(context, require_present=True)
    if mismatch:
        raise ValueError("calibration profile does not match current tracking configuration: " + ", ".join(mismatch))
    expected_hash = model.metadata.get("calibration_context_hash")
    if expected_hash and expected_hash != profile_geometry_hash(profile):
        raise ValueError("camera/user/world geometry changed; recalibrate before validation")


def _ensure_profile_unchanged(path, baseline):
    if not Path(path).exists():
        if baseline.get("screen_model") or baseline.get("spatial_model") or baseline.get("intrinsics"):
            raise ValueError("calibration profile was removed during acquisition")
        return
    current = load_profile(path)
    if profile_geometry_hash(current) != profile_geometry_hash(baseline):
        raise ValueError("calibration profile geometry changed during acquisition; restart the session")
    for field in ("screen_model", "spatial_model"):
        if json.dumps(current.get(field), sort_keys=True) != json.dumps(baseline.get(field), sort_keys=True):
            raise ValueError("a gaze model changed during acquisition; restart the session")


def _validation_report(model, records, kind, screen_size=None, session_id=None, *,
                       context=None, extrinsics=None):
    if kind != model.kind:
        raise ValueError("validation kind differs from the fitted model")
    actual = deepcopy(context if context is not None else records[0].get("context", {}))
    if kind == "screen" and screen_size is not None:
        actual["screen_size"] = list(_display_size(screen_size))
    report = validate_gaze_records(model, records, context=actual,
                                   session_id=session_id, extrinsics=extrinsics)
    # Retain the established CLI field names while using the same evidence gates
    # as the desktop and public SDK validator.
    report.update(timestamp_utc=report["created_utc"],
                  outside_training_envelope_samples=report["out_of_envelope_samples"],
                  outside_training_envelope_fraction=1-report["in_envelope_fraction"],
                  prediction_method="unfiltered predictions on recorded target observations",
                  target_ground_truth="user_instructed_fixation_on_measured_targets",
                  notes=("Screen pixel errors depend on the user's actual fixation; primary-display identity is not independently verified."
                         if kind == "screen" else
                         "Angular error uses the configured eye-origin estimate, which may use generic facial anatomy; validate physical surface hits independently."))
    return report


class ScreenSession:
    def __init__(self, source: FrameSource, tracker: GazeTracker, profile_path: str,
                 samples_per_target: int = 16, grid: int = 4, validate: bool = False,
                 camera_id: str | None = None, max_seconds_per_target: float = 10,
                 drift: bool = False):
        if samples_per_target < 4:
            raise ValueError("need >=4 samples per target")
        if max_seconds_per_target <= 1.2:
            raise ValueError("allow more than 1.2 seconds for settling and target acquisition")
        self.source, self.tracker, self.profile_path = source, tracker, profile_path
        self.samples_per_target, self.grid = int(samples_per_target), int(grid)
        self.validate, self.drift = bool(validate or drift), bool(drift)
        self.camera_id = camera_id
        self.max_seconds_per_target = float(max_seconds_per_target)
        self.samples = []
        self.index = 0
        self.collecting = False
        self.collect_started = 0.0
        self.accepted = 0
        self.aborted = self.completed = False
        self.last_error = ""
        self.session_id = uuid.uuid4().hex
        self.collector: TargetCollector | None = None
        self.profile = None
        self.model = None
        self.actual_context = None
        self._observer = None

    def prepare(self, screen_size: tuple[int, int]):
        """Initialize display targets and profile checks without creating a GUI."""
        self.size = _display_size(screen_size)
        self.profile = load_profile(self.profile_path) if Path(self.profile_path).exists() else new_profile()
        if self.validate:
            if not self.profile.get("screen_model"):
                raise ValueError("first calibrate a compatible version-2 screen model")
            self.model = GazeModel.from_dict(self.profile["screen_model"])
            if tuple(self.model.metadata["screen_size"]) != self.size:
                raise ValueError("screen resolution changed; recalibrate before validation")
            preliminary = _tracking_context(self.profile, self.tracker, self.source, camera_id=self.camera_id)
            mismatch = self.model.context_mismatches(preliminary)
            if mismatch:
                raise ValueError("profile configuration mismatch: " + ", ".join(mismatch))
        center = tuple((np.asarray(self.size, float) - 1) / 2)
        self.targets = ([("anchor_initial", center), ("anchor_repeat", center)] if self.drift else
                        offset_validation_targets(self.size) if self.validate else grid_targets(self.size, self.grid))
        # Use the same depth-aware observation path as live tracking. This engine
        # is deliberately uncalibrated and does not own/close the caller's tracker.
        from .engine import GazeEngine
        self._observer = GazeEngine(self.tracker)
        return self.targets

    def run(self) -> dict | None:
        import tkinter as tk
        import sys
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.user32.SetProcessDPIAware()
            except (OSError, AttributeError):
                pass
        self.root = tk.Tk()
        try:
            self.root.title("GazeSpace — " + ("drift check" if self.drift else "gaze calibration"))
            self.root.configure(bg="#09111e")
            self.root.attributes("-fullscreen", True)
            self.root.attributes("-topmost", True)
            self.root.focus_force()
            self.prepare((self.root.winfo_screenwidth(), self.root.winfo_screenheight()))
            self.canvas = tk.Canvas(self.root, highlightthickness=0, bg="#09111e")
            self.canvas.pack(fill="both", expand=True)
            self.root.bind("<space>", lambda _: self.start_target())
            self.root.bind("<Return>", lambda _: self.start_target())
            self.root.bind("<Escape>", lambda _: self.abort())
            self.root.protocol("WM_DELETE_WINDOW", self.abort)
            self.draw()
            self.root.after(30, self.tick)
            self.root.mainloop()
        finally:
            # No camera/tracker close here: callers may reuse the existing app's
            # camera and are responsible for releasing their own resources.
            try:
                self.root.destroy()
            except tk.TclError:
                pass
        if self.aborted or not self.completed:
            return None
        return self.finish()

    def start_target(self):
        if self.completed or self.collecting or self.aborted:
            return
        if not hasattr(self, "targets"):
            raise RuntimeError("prepare a screen size before starting acquisition")
        target_id, target_px = self.targets[self.index]
        norm = np.asarray(target_px, float) / (np.asarray(self.size, float) - 1)
        self.collector = TargetCollector(target_id, norm, min_samples=self.samples_per_target,
                                         max_seconds=self.max_seconds_per_target,
                                         session_id=self.session_id)
        self.collector.start()
        self.collecting = True
        self.collect_started = host_now_s()
        self.accepted = 0
        self.last_error = "Allow fixation to settle before samples are collected"
        self.draw()

    def abort(self):
        self.aborted = True
        self.collecting = False
        if self.collector:
            self.collector.cancel()
        if hasattr(self, "root"):
            self.root.destroy()

    def consume_frame(self, frame: Frame | None):
        """Process one frame; return collector progress, or None when idle."""
        if self.completed or self.aborted:
            return None
        if frame is None:
            self.last_error = "Camera frame missing or source ended"
            if self.collecting and host_now_s() - self.collect_started > self.max_seconds_per_target:
                self.collector.cancel()
                self.collecting = False
                self.accepted = 0
                self.last_error = "Camera timeout; reconnect, then press SPACE to retry this target"
            return self.collector.progress() if self.collector else None
        self._observer.process_frame(frame)
        observation = self._observer.last_observation
        _bind_observation(observation, self.profile, self.tracker, self.source, self.camera_id)
        # Bind to actual frame size/method/origin even for validation. An invalid
        # detection can lack an eye-origin source; its data will be rejected by
        # acquisition rather than masquerading as a different calibrated method.
        if observation.features is not None and observation.eyes_open and not observation.blink:
            if self.model is not None:
                _validate_context(self.model, observation.context, self.profile)
            if self.actual_context is None:
                self.actual_context = deepcopy(observation.context)
            elif observation.context != self.actual_context:
                raise ValueError("tracking context changed during acquisition; restart calibration")
        if not self.collecting:
            return None
        self.collector.add(observation)
        state = self.collector.progress()
        self.accepted = state["accepted"]
        self.last_error = state["reason"].replace("_", " ")
        if self.collector.complete:
            self.samples.extend(self.collector.records(screen_size=self.size))
            self.collecting = False
            self.index += 1
            self.last_error = "Target captured; look at the next dot, then press SPACE"
            if hasattr(self, "canvas"):
                self.canvas.bell()
            if self.index >= len(self.targets):
                self.completed = True
                if hasattr(self, "root"):
                    self.root.destroy()
        elif state["state"] in ("timeout", "incompatible"):
            self.collecting = False
            self.accepted = 0
            self.last_error = "Target not saved: " + state["reason"].replace("_", " ") + "; press SPACE to retry"
        return state

    def finish(self):
        """Fit or validate after all targets; usable by headless integrations."""
        if not self.completed or not self.samples or self.actual_context is None:
            raise ValueError("all targets must be acquired before finishing")
        _ensure_profile_unchanged(self.profile_path, self.profile)
        if self.validate:
            _validate_context(self.model, self.actual_context, self.profile)
            if self.drift:
                report = evaluate_drift(self.samples, self.model, self.size)
                report.update({"session_id": self.session_id, "model_fingerprint": self.model.fingerprint,
                               "timestamp_utc": datetime.now(timezone.utc).isoformat()})
                update_profile(self.profile_path, drift_validation=report)
            else:
                report = _validation_report(self.model, self.samples, "screen", self.size, self.session_id,
                                            context=self.actual_context)
                update_profile(self.profile_path, screen_validation=report)
            return report
        model = fit_gaze_model(self.samples, "screen", screen_size=self.size,
                               context=self.actual_context, session_id=self.session_id)
        update_profile(self.profile_path, screen_model=model.to_dict(),
                       camera_id=self.actual_context.get("camera_id", self.camera_id),
                       context=self.actual_context)
        return model.report

    def draw(self):
        if not hasattr(self, "canvas"):
            return
        self.canvas.delete("all")
        width, height = self.size
        title = "ANCHOR DRIFT CHECK" if self.drift else "INDEPENDENT VALIDATION" if self.validate else "SCREEN CALIBRATION"
        self.canvas.create_text(width / 2, 40, text="GAZESPACE · " + title,
                                fill="#eff7ff", font=("Segoe UI", 24, "bold"))
        if self.collecting:
            state = self.collector.progress()
            subtitle = f'{state["state"].title()} · {state["accepted"]}/{self.samples_per_target} samples · {state["stable_duration_s"]:.1f}s stable'
        elif self.drift and self.index == 1:
            subtitle = "Wait and move naturally between anchors, then look at the center and press SPACE"
        else:
            subtitle = "Look directly at the dot, hold head naturally, press SPACE to collect"
        self.canvas.create_text(width / 2, 84, text=f"Target {min(self.index + 1, len(self.targets))}/{len(self.targets)}  |  " + subtitle,
                                fill="#93adc5", font=("Segoe UI", 16))
        if self.index < len(self.targets):
            _, (x, y) = self.targets[self.index]
            for radius, color, stroke in ((25, "#21436a", 3), (10, "#40dfc0", 4), (3, "#ffffff", 0)):
                self.canvas.create_oval(x - radius, y - radius, x + radius, y + radius,
                                        outline=color if stroke else "", fill=color if not stroke else "", width=stroke)
        self.canvas.create_text(width / 2, height - 40, text=f"ESC: cancel  |  SPACE: sample  |  {self.last_error}",
                                fill="#f6a67a", font=("Segoe UI", 13))

    def tick(self):
        if self.aborted or self.completed:
            return
        try:
            self.consume_frame(self.source.read())
        except (ValueError, RuntimeError, OSError, cv2.error) as exc:
            self.last_error = f"{type(exc).__name__}: {str(exc)[:130]}"
            if self.collector:
                self.collector.cancel()
            self.collecting = False
            self.accepted = 0
        if not self.completed and not self.aborted:
            self.draw()
            self.root.after(30, self.tick)


def evaluate_drift(samples: list[dict], model: GazeModel, screen_size: tuple[int, int]) -> dict:
    initial = [row for row in samples if row["target_id"] == "anchor_initial"]
    repeated = [row for row in samples if row["target_id"] == "anchor_repeat"]
    if len(initial) < 4 or len(repeated) < 4:
        raise ValueError("drift check needs >=4 samples at each repeated fixation")
    try:
        timestamps = np.asarray([row["time_s"] for row in samples], float)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("drift check requires acquisition timestamps") from exc
    if not np.isfinite(timestamps).all() or np.any(np.diff(timestamps) <= 0):
        raise ValueError("drift timestamps must be finite, unique and increasing")
    if repeated[0]["time_s"] <= initial[-1]["time_s"]:
        raise ValueError("repeat fixation must follow the initial fixation")
    extent = np.asarray(_display_size(screen_size), float) - 1
    first = model.predict(np.asarray([r["features"] for r in initial], float)) * extent
    last = model.predict(np.asarray([r["features"] for r in repeated], float)) * extent
    # In v2 the actual center is halfway between pixel 0 and pixel width-1.
    target = extent / 2
    report = compare_anchors(first, last, target_px=target)
    # compare_anchors also supports a caller-selected diagnostic threshold. This
    # workflow has not collected any agreed threshold, so do not present its
    # library default as a selected limit or as an acceptance result.
    report["threshold_px"] = None
    report["outcome"] = "measurement_only_no_agreed_criteria"
    provenance = [getattr(model, "metadata", {}), *samples,
                  *(row.get("context", {}) for row in samples)]
    synthetic = any(isinstance(value, dict) and (
        bool(value.get("synthetic") or value.get("simulated"))
        or value.get("backend") == "synthetic"
        or value.get("eye_origin_source") == "synthetic_fixture") for value in provenance)
    report.update({"first_mean_px": report["first"]["mean_xy"],
                   "repeat_mean_px": report["repeat"]["mean_xy"],
                   "first_anchor_error_px": float(np.linalg.norm(np.mean(first, axis=0) - target)),
                   "repeat_anchor_error_px": float(np.linalg.norm(np.mean(last, axis=0) - target)),
                   # Historical field names retained, but now hold a correct
                   # RMS measure rather than std(radial distance).
                   "first_jitter_std_px": report["first"]["radial_rms"],
                   "repeat_jitter_std_px": report["repeat"]["radial_rms"],
                   "first_jitter_rms_px": report["first"]["radial_rms"],
                   "repeat_jitter_rms_px": report["repeat"]["radial_rms"],
                   "first_jitter_std_xy_px": report["first"]["std_xy"],
                   "repeat_jitter_std_xy_px": report["repeat"]["std_xy"],
                   "elapsed_s": float(repeated[-1]["time_s"] - initial[0]["time_s"]),
                   "synthetic": synthetic,
                   "measurement_provenance": "synthetic_software_fixture" if synthetic else "user_supplied_repeated_anchors",
                   "physical_accuracy_verified": False,
                   "acceptance_eligible": False,
                   "acceptance_status": "synthetic_software_only" if synthetic else "not_evaluated",
                   "acceptance_criteria": None, "acceptance_passed": None,
                   "acceptance_note": "Repeated center anchors alone do not establish global gaze accuracy; baseline error, drift and operating duration need agreed limits.",
                   "method": "two instructed center fixations; radial RMS and mean offset in calibrated pixels",
                   "warning": "Drift alone does not establish accuracy. Recalibrate if baseline target error or drift is excessive."})
    return report


def read_spatial_targets(path: str) -> list[dict]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list) or len(payload) < 9:
        raise ValueError("spatial target file must be a list of >=9 measured targets")
    keys = set()
    points = []
    for row in payload:
        if not isinstance(row, dict) or "id" not in row or "xyz_m" not in row:
            raise ValueError("each target needs id and finite xyz_m [x,y,z]")
        try:
            point = np.asarray(row["xyz_m"], float)
        except (ValueError, TypeError) as exc:
            raise ValueError("each target needs finite xyz_m [x,y,z]") from exc
        if point.shape != (3,) or not np.isfinite(point).all() or not str(row["id"]).strip():
            raise ValueError("each target needs id and finite xyz_m [x,y,z]")
        if str(row["id"]) in keys:
            raise ValueError("target ids must be unique")
        keys.add(str(row["id"]))
        points.append(point)
    array = np.asarray(points)
    if len(np.unique(np.round(array, 5), axis=0)) != len(array):
        raise ValueError("target coordinates must be distinct; different labels cannot duplicate a fixation")
    singular = np.linalg.svd(array - np.mean(array, axis=0), compute_uv=False)
    if singular[1] < .005:
        raise ValueError("target geometry is collinear or has insufficient horizontal/vertical spread")
    return payload


def _check_spatial_geometry(targets, extrinsics, require_multidepth):
    world = np.asarray([row["xyz_m"] for row in targets], float)
    camera = np.asarray([world_to_camera_point(point, extrinsics) for point in world])
    if np.any(camera[:, 2] <= .02):
        raise ValueError("physical targets must lie in front of the calibrated camera")
    depth_span = float(np.ptp(camera[:, 2]))
    singular = np.linalg.svd(world - world.mean(axis=0), compute_uv=False)
    if require_multidepth and (depth_span < .02 or singular[2] < .005):
        raise ValueError("volumetric calibration needs a nonplanar target arrangement and >=20 mm depth spread in CAMERA coordinates; use single-depth mode only for planar experiments")
    return {"camera_depth_span_m": depth_span, "target_rank": int(np.count_nonzero(singular > .005)),
            "volumetric_target_coverage": bool(depth_span >= .02 and singular[2] >= .005)}


def spatial_session(source: FrameSource, tracker: GazeTracker, profile_path: str, targets_path: str,
                    samples_per_target: int = 16, validate: bool = False,
                    max_seconds: float = 15, require_multidepth: bool = True) -> dict:
    profile = load_profile(profile_path)
    if not profile.get("intrinsics") or not profile.get("extrinsics"):
        raise ValueError("need calibrated intrinsic and board-to-world extrinsic profile")
    if CameraIntrinsics.from_dict(profile["intrinsics"]).source not in ("measured", "factory"):
        raise ValueError("physical workspace calibration cannot use approximate focal length")
    extrinsics = CameraExtrinsics.from_dict(profile["extrinsics"])
    targets = read_spatial_targets(targets_path)
    geometry = _check_spatial_geometry(targets, extrinsics, require_multidepth)
    if samples_per_target < 4:
        raise ValueError("need >=4 samples per target")
    if max_seconds <= 1.2:
        raise ValueError("allow more than 1.2 seconds for each physical target")
    model = None
    if validate:
        if not profile.get("spatial_model"):
            raise ValueError("first calibrate a compatible spatial gaze model")
        model = GazeModel.from_dict(profile["spatial_model"])
        mismatch = model.context_mismatches(_tracking_context(profile, tracker, source))
        if mismatch:
            raise ValueError("spatial profile configuration mismatch: " + ", ".join(mismatch))
    from .engine import GazeEngine
    observer = GazeEngine(tracker)
    records, actual_context = [], None
    session_id = uuid.uuid4().hex
    for index, target in enumerate(targets):
        print(f"\n[{index + 1}/{len(targets)}] Look at physical target {target['id']} at {target['xyz_m']} meters. "
              "Hold a steady fixation; press Enter to start or q to cancel.")
        if input("Target ready? ").strip().lower() == "q":
            raise KeyboardInterrupt("spatial calibration cancelled")
        collector = TargetCollector(str(target["id"]), target["xyz_m"], "spatial",
                                     min_samples=samples_per_target, max_seconds=max_seconds,
                                     session_id=session_id)
        collector.start()
        started = host_now_s()
        while not collector.complete and host_now_s() - started <= max_seconds:
            frame = source.read()
            if frame is None:
                if getattr(source, "eof", False):
                    raise RuntimeError("video ended during spatial target acquisition")
                time.sleep(.02)
                continue
            observer.process_frame(frame)
            observation = observer.last_observation
            _bind_observation(observation, profile, tracker, source)
            if observation.features is not None and observation.eyes_open and not observation.blink:
                if model:
                    _validate_context(model, observation.context, profile)
                if actual_context is None:
                    actual_context = deepcopy(observation.context)
                elif observation.context != actual_context:
                    raise ValueError("tracking context changed during physical target acquisition")
            collector.add(observation)
            if collector.state in ("timeout", "incompatible"):
                break
        if not collector.complete:
            raise RuntimeError(f"Target {target['id']} not saved: {collector.reason}; check pupil visibility, head stability, camera and pose")
        records.extend(collector.records(extrinsics=extrinsics))
        print(f"  Captured {len(collector.observations)} settled samples over {collector.progress()['stable_duration_s']:.2f} seconds")
    _ensure_profile_unchanged(profile_path, profile)
    if validate:
        report = _validation_report(model, records, "spatial", session_id=session_id,
                                    context=actual_context, extrinsics=extrinsics)
        report["target_geometry"] = geometry
        update_profile(profile_path, spatial_validation=report)
        return report
    model = fit_gaze_model(records, "spatial", context=actual_context, session_id=session_id)
    model.report["target_geometry"] = geometry
    update_profile(profile_path, spatial_model=model.to_dict(), context=actual_context,
                   camera_id=actual_context.get("camera_id", profile.get("camera_id")))
    return model.report
