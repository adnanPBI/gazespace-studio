"""Embed GazeSpace 0.2 in an existing Windows Python application.

Importing this module never opens a device, loads a face model, starts a worker
or records data. The host continues to own its camera and GUI. Construct the
adapter INSIDE the processing worker and close it on that same worker.

Supply current device/display settings, independently obtained from the host:

    source_context = {
        "camera_id": "0",              # selected UVC index, or actual serial
        "backend": "dshow",            # same convention used for calibration
        "subject_id": "operator-1",    # selected operator, not face identity
        "exposure_native": None,       # None means left unchanged, as in Studio
        "exposure_us": None,
        "gain": None,
        "ir_bit_depth": 8,
        "monitor": current_monitor_name,
        "screen_size": [current_pixel_width, current_pixel_height],
    }

The monitor variables are host integration values, not SDK functions. Include
any additional fields bound by your calibration (for example mirror or monitor
origin). Do not copy expected values from a saved profile as verification of
the current hardware. Recreate the adapter after changing those settings.

For a host-owned BGR camera, timestamp acquisition with
gazespace.clock.host_now_s() (time.perf_counter, not time.monotonic on Windows
Python 3.11/3.12), then pass that value to on_camera_frame. For aligned depth,
call process_frame with a Frame:
depth_m is HxW meters on the BGR grid and metadata contains depth_aligned_to=
"color", depth_intrinsics=CameraIntrinsics.to_dict(), and the truthful
depth_deprojection_supported flag. Preserve hardware_timestamp_s separately;
it must not be treated as a host monotonic timestamp without synchronization.

Copy vendor-owned image/depth buffers BEFORE enqueueing them. Feed only the
newest pending Frame through a bounded host queue. Use snapshot() from the UI
timer to clear a marker after its freshness deadline, including while a camera
read or inference call is blocked. Screen pixels are LOCAL to the calibrated
monitor; the host owns any Windows virtual-desktop/DPI transformation.
"""
from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path
from threading import Event, Lock, get_ident
from gazespace.clock import HOST_CLOCK_DOMAIN, host_now_s
from typing import Callable, Mapping

import numpy as np

from gazespace.engine import GazeEngine
from gazespace.types import Frame


def _unavailable(reason: str) -> dict:
    """Every unavailable update explicitly clears both coordinate paths."""
    return {
        "status": "unavailable", "valid": False, "screen_valid": False,
        "spatial_valid": False, "surface_hit": False, "reason": reason,
        "quality": 0.0, "gaze_screen_px": None,
        "gaze_screen_normalized": None, "raw_gaze_screen_px": None,
        "ray_origin_world_m": None, "ray_direction_world": None,
        "surface_name": None, "surface_xy_m": None, "surface_xy_px": None,
        "capture_timestamp_s": None, "received_monotonic_s": None,
        "fresh_until_monotonic_s": None,
    }


class ExistingAppGazeAdapter:
    """One worker-owned engine with a thread-safe, freshness-aware UI snapshot."""

    def __init__(self, profile_path: str | Path, *, source_context: Mapping,
                 mode: str = "rgb", roi_normalized=None,
                 pupil_polarity: str = "dark", face_model: str | None = None,
                 max_age_s: float = 0.4):
        if not math.isfinite(max_age_s) or max_age_s <= 0:
            raise ValueError("max_age_s must be finite and positive")
        current = deepcopy(dict(source_context))
        for key in ("camera_id", "backend", "subject_id"):
            if not isinstance(current.get(key), str) or not current[key].strip():
                raise ValueError(f"Provide the current {key} as a nonempty string")
        self._owner_thread = get_ident()
        self._lock = Lock()
        self._closed = False
        self._last_source_timestamp: float | None = None
        self._latest = _unavailable("awaiting_frame")
        self.max_age_s = float(max_age_s)
        self._engine = GazeEngine.from_profile(
            profile_path, mode=mode, roi_normalized=roi_normalized,
            pupil_polarity=pupil_polarity, face_model=face_model,
            source_context=current,
        )
        # The SDK permits headless omission; this consumer deliberately requires
        # actual display dimensions before publishing a screen estimate.
        if self._engine.screen_model is not None:
            size = current.get("screen_size")
            if (not isinstance(size, (tuple, list)) or len(size) != 2
                    or any(not isinstance(v, int) or isinstance(v, bool) or v < 2 for v in size)):
                self._engine.close()
                raise ValueError("A screen model requires the actual screen_size [width,height]")

    def _check_owner(self) -> None:
        if get_ident() != self._owner_thread:
            raise RuntimeError("Process/reset/close the adapter on its creating worker thread")
        if self._closed:
            raise RuntimeError("The adapter is closed")

    def _set_latest(self, value: dict) -> dict:
        with self._lock:
            self._latest = deepcopy(value)
        return deepcopy(value)

    def invalidate(self, reason: str = "paused") -> dict:
        """Call on the processing worker for pause, disconnect or host failure."""
        self._check_owner()
        self._engine.invalidate(reason)
        return self._set_latest(_unavailable(reason))

    def reset(self) -> dict:
        """Start a new source timeline after reconnect; context must be unchanged."""
        self._check_owner()
        self._last_source_timestamp = None
        self._engine.reset()
        return self._set_latest(_unavailable("awaiting_fresh_frame"))

    def on_camera_frame(self, bgr_frame: np.ndarray, captured_monotonic_s: float,
                        *, sequence: int = 0) -> dict:
        """Convenience for host-clock BGR input; timestamp it at acquisition.

        Do not replace the timestamp with the later time of dequeuing a frame.
        Use process_frame for a source clock, hardware timestamp or depth data.
        """
        return self.process_frame(Frame(
            image=bgr_frame, timestamp_s=captured_monotonic_s, sequence=sequence,
            source="existing_application",
            metadata={"host_clock_domain": HOST_CLOCK_DOMAIN},
            received_monotonic_s=captured_monotonic_s,
        ))

    def process_frame(self, frame: Frame) -> dict:
        """Process one owned BGR8 frame; keep its optional aligned depth intact."""
        self._check_owner()
        received = frame.received_monotonic_s
        now = host_now_s()
        if received is None or not math.isfinite(received):
            return self.invalidate("missing_host_receive_timestamp")
        if not math.isfinite(frame.timestamp_s):
            return self.invalidate("nonfinite_capture_timestamp")
        declared_clock = frame.metadata.get("host_clock_domain")
        if ((declared_clock is not None and declared_clock != HOST_CLOCK_DOMAIN)
                or received > now):
            return self.invalidate("receive_timestamp_not_in_host_clock")
        if now - received > self.max_age_s:
            return self.invalidate("stale_queued_frame")
        if self._last_source_timestamp is not None and frame.timestamp_s <= self._last_source_timestamp:
            return self.invalidate("duplicate_or_reversed_capture_timestamp")
        self._last_source_timestamp = float(frame.timestamp_s)
        try:
            # process_frame, unlike dropping down to a bare BGR call, preserves
            # aligned depth and checks the origin source used by calibration.
            result = self._engine.process_frame(frame)
            differences = []
            for model in (self._engine.screen_model, self._engine.spatial_model):
                if model is not None:
                    differences.extend(model.context_mismatches(
                        result.tracking_context, require_present=True))
            if differences:
                value = self.invalidate("calibration_context_mismatch")
                value["context_mismatches"] = sorted(set(differences))
                return self._set_latest(value)
            if host_now_s() - received > self.max_age_s:
                return self.invalidate("stale_after_processing")
            value = result.as_dict()
            value.update(
                status="available" if result.valid else "unavailable",
                # A valid ray without a bounded hit cannot select a surface.
                surface_hit=bool(result.spatial_valid and result.surface_name is not None
                                 and result.surface_xy_m is not None),
                received_monotonic_s=float(received),
                fresh_until_monotonic_s=float(received) + self.max_age_s,
                host_clock_domain=HOST_CLOCK_DOMAIN,
            )
            return self._set_latest(value)
        except Exception:
            self.invalidate("processing_error")
            raise

    def snapshot(self) -> dict:
        """Read from any thread; a UI timer should call this even without frames."""
        with self._lock:
            value = deepcopy(self._latest)
        deadline = value.get("fresh_until_monotonic_s")
        if deadline is not None and host_now_s() > deadline:
            return _unavailable("stale_capture")
        return value

    def close(self) -> None:
        if get_ident() != self._owner_thread:
            raise RuntimeError("Close the adapter on its creating worker thread")
        if self._closed:
            return
        try:
            self._engine.close()
        finally:
            self._closed = True
            self._set_latest(_unavailable("stopped"))

    def __enter__(self):
        self._check_owner()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


def run_gaze_worker(*, next_frame: Callable[[float], Frame | None],
                    publish: Callable[[dict], None], stop_event: Event,
                    profile_path: str | Path, source_context: Mapping,
                    **adapter_options) -> None:
    """A real worker entrypoint for an existing application's bounded queue.

    next_frame(timeout_seconds) returns the newest owned Frame, or None on
    timeout. It must return within the timeout so shutdown stays responsive.
    publish must enqueue/signal the update to the GUI, not touch widgets here.
    The GUI must also expire fresh_until_monotonic_s on its own timer even if
    no new update arrives while inference is blocked.
    The host owns camera shutdown and joins this worker before disposing it.
    """
    final_reason = "stopped"
    try:
        with ExistingAppGazeAdapter(profile_path, source_context=source_context,
                                    **adapter_options) as adapter:
            while not stop_event.is_set():
                frame = next_frame(0.05)
                if stop_event.is_set():
                    break
                publish(adapter.snapshot() if frame is None else adapter.process_frame(frame))
    except Exception as exc:
        final_reason = f"worker_error: {type(exc).__name__}: {exc}"
        raise
    finally:
        publish(_unavailable(final_reason))
