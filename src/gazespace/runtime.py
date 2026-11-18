"""Thread-owned acquisition and tracking with a bounded latest-frame mailbox.

The GUI polls snapshot(); neither camera IO nor MediaPipe runs on the GUI
thread. Invalid state transitions immediately remove active gaze coordinates.
No source, engine or SDK object is closed from a thread currently using it.
"""
from __future__ import annotations

from collections import deque
from dataclasses import replace
import copy
from threading import Condition, Event, Lock, Thread, current_thread
import time
from typing import Callable, Any

from .types import Frame, GazeResult, Observation


class TrackingRuntime:
    def __init__(self, source_factory: Callable, engine_factory: Callable, *,
                 max_age_s: float = .4, reconnect_attempts: int = 5,
                 reconnect_delay_s: float = .25, on_result: Callable | None = None):
        if max_age_s <= 0 or reconnect_attempts < 0 or reconnect_delay_s < 0:
            raise ValueError("Invalid runtime age/reconnect settings")
        self.source_factory, self.engine_factory = source_factory, engine_factory
        self.max_age_s, self.reconnect_attempts = float(max_age_s), int(reconnect_attempts)
        self.reconnect_delay_s, self.on_result = float(reconnect_delay_s), on_result
        self._lock = Lock()
        self._condition = Condition(self._lock)
        self._stop, self._capture_done = Event(), Event()
        self._capture_thread: Thread | None = None
        self._process_thread: Thread | None = None
        self._source = None
        self._engine = None
        self._state = "idle"
        self._last_error: str | None = None
        self._paused = False
        self._generation = 0
        self._pending: tuple[int, Frame, float] | None = None
        self._frame: Frame | None = None
        self._observation: Observation | None = None
        self._result: GazeResult | None = None
        self._last_received: float | None = None
        self._camera: dict[str, Any] = {}
        self._engine_metrics = {}
        self._capture_times = deque(maxlen=120)
        self._process_times = deque(maxlen=120)
        self._counters = {"received_frames": 0, "processed_frames": 0,
                          "dropped_frames": 0, "stale_frames": 0, "reconnections": 0}
        self._callback_error = None

    @property
    def engine(self):
        """Compatibility read access; do not process/close it outside its worker."""
        with self._lock:
            return self._engine

    def start(self) -> None:
        with self._condition:
            if self._alive_locked():
                raise RuntimeError("Runtime already active or previous camera read has not stopped")
            self._stop.clear()
            self._capture_done.clear()
            self._state, self._last_error, self._paused = "starting", None, False
            self._generation += 1
            self._pending = self._frame = self._observation = self._result = None
            self._last_received = None
            self._capture_times.clear()
            self._process_times.clear()
            self._counters = {key: 0 for key in self._counters}
            self._engine_metrics = {}
            self._callback_error = None
            self._capture_thread = Thread(target=self._capture_loop,
                                          name="gazespace-acquisition", daemon=True)
            self._process_thread = Thread(target=self._process_loop,
                                          name="gazespace-processing", daemon=True)
            self._process_thread.start()
            self._capture_thread.start()

    def pause(self) -> None:
        with self._condition:
            if self._state not in ("starting", "running", "reconnecting", "paused"):
                return
            self._paused = True
            self._state = "paused"
            self._generation += 1
            self._pending = None
            self._invalidate_locked("paused")
            self._condition.notify_all()

    def resume(self) -> None:
        with self._condition:
            if not self._paused or not self._alive_locked():
                return
            self._paused = False
            self._state = "starting"
            self._generation += 1
            self._pending = None
            self._invalidate_locked("awaiting_fresh_frame")
            self._condition.notify_all()

    def stop(self, timeout: float = 3.0) -> bool:
        """Request shutdown; return False when an OS/driver call remains blocked.

        A failed stop remains visibly stopped-for-output, cannot be restarted,
        and is never disguised by concurrently releasing an active camera.
        """
        if timeout < 0:
            raise ValueError("timeout must be nonnegative")
        deadline = time.monotonic() + timeout
        self._stop.set()
        with self._condition:
            self._generation += 1
            self._pending = None
            self._state = "stopping"
            self._invalidate_locked("stopped")
            source = self._source
            self._condition.notify_all()
        if source is not None:
            try:
                source.request_stop()
            except Exception:
                pass  # A request is optional; never cross-thread close the source.
        for worker in (self._capture_thread, self._process_thread):
            if worker is not None and worker is not current_thread():
                worker.join(max(0.0, deadline - time.monotonic()))
        with self._condition:
            completed = not self._alive_locked()
            self._state = "stopped" if completed else "stopping_blocked"
            if not completed:
                self._last_error = "Camera/processing call has not returned; output disabled, restart prevented"
            self._condition.notify_all()
        return completed

    def _alive_locked(self) -> bool:
        return any(t is not None and t.is_alive() for t in (self._capture_thread, self._process_thread))

    @staticmethod
    def _invalid(result: GazeResult, reason: str) -> GazeResult:
        invalid = copy.deepcopy(result)
        invalid.valid = False
        invalid.reason = reason
        for field in ("gaze_screen_px", "gaze_screen_normalized", "raw_gaze_screen_px",
                      "ray_origin_world_m", "ray_direction_world", "surface_name",
                      "surface_xy_m", "surface_xy_px"):
            if hasattr(invalid, field):
                setattr(invalid, field, None)
        for field in ("screen_valid", "spatial_valid"):
            if hasattr(invalid, field):
                setattr(invalid, field, False)
        return invalid

    def _invalidate_locked(self, reason: str) -> None:
        if self._result is not None:
            self._result = self._invalid(self._result, reason)
        self._observation = None

    def _capture_loop(self) -> None:
        consecutive_reconnects = 0
        try:
            while not self._stop.is_set():
                source = None
                try:
                    source = self.source_factory()
                    with self._condition:
                        self._source = source
                        self._camera = copy.deepcopy(source.capabilities()) if hasattr(source, "capabilities") else {}
                    while not self._stop.is_set():
                        # Live devices keep draining during pause so resuming
                        # cannot replay buffered pre-pause camera frames. Files
                        # and generated demos pause their presentation instead.
                        if not getattr(source, "is_live", True):
                            with self._condition:
                                self._condition.wait_for(lambda: not self._paused or self._stop.is_set(), timeout=.2)
                                if self._paused:
                                    continue
                            if self._stop.is_set():
                                break
                        frame = source.read()
                        received = time.monotonic()
                        if self._stop.is_set():
                            break
                        if frame is None:
                            if getattr(source, "eof", False) or not getattr(source, "is_live", True):
                                with self._condition:
                                    if self._state != "fault":
                                        self._state = "exhausted"
                                    self._generation += 1
                                    self._pending = None
                                    self._invalidate_locked("source_exhausted")
                                return
                            raise OSError("Camera returned no frame")
                        frame_received = getattr(frame, "received_monotonic_s", None)
                        if frame_received is not None:
                            received = float(frame_received)
                        consecutive_reconnects = 0
                        with self._condition:
                            self._counters["received_frames"] += 1
                            self._capture_times.append(time.monotonic())
                            if self._paused:
                                continue
                            if self._pending is not None:
                                self._counters["dropped_frames"] += 1
                            self._pending = (self._generation, frame, received)
                            self._condition.notify_all()
                except Exception as exc:
                    if self._stop.is_set():
                        break
                    consecutive_reconnects += 1
                    retry = (source is None or getattr(source, "is_live", True)) and consecutive_reconnects <= self.reconnect_attempts
                    with self._condition:
                        self._last_error = f"{type(exc).__name__}: {exc}"
                        self._state = "reconnecting" if retry else "fault"
                        self._generation += 1
                        self._pending = None
                        self._invalidate_locked("camera_disconnected" if retry else "camera_fault")
                        if retry:
                            self._counters["reconnections"] += 1
                        self._condition.notify_all()
                    if not retry:
                        return
                finally:
                    if source is not None:
                        try:
                            source.close()
                        except Exception as exc:
                            with self._condition:
                                self._last_error = f"Camera close: {type(exc).__name__}: {exc}"
                    with self._condition:
                        if self._source is source:
                            self._source = None
                if self._stop.wait(min(2.0, self.reconnect_delay_s * (2 ** min(consecutive_reconnects - 1, 4)))):
                    break
        finally:
            self._capture_done.set()
            with self._condition:
                self._condition.notify_all()

    def _process_loop(self) -> None:
        engine = None
        previous_generation = None
        try:
            engine = self.engine_factory()
            with self._condition:
                self._engine = engine
            while not self._stop.is_set():
                with self._condition:
                    self._condition.wait_for(lambda: self._stop.is_set() or self._pending is not None or
                                             self._capture_done.is_set(), timeout=.2)
                    if self._stop.is_set():
                        break
                    if self._pending is None:
                        if self._capture_done.is_set():
                            break
                        continue
                    generation, frame, received = self._pending
                    self._pending = None
                    paused = self._paused
                if paused:
                    continue
                if previous_generation is not None and previous_generation != generation:
                    if hasattr(engine, "reset"):
                        engine.reset()
                    else:
                        for name in ("screen_filter", "direction_filter"):
                            filter_obj = getattr(engine, name, None)
                            if filter_obj is not None:
                                filter_obj.reset()
                previous_generation = generation
                # Drop over-age frames before expensive inference.
                if time.monotonic() - received > self.max_age_s:
                    with self._condition:
                        self._counters["stale_frames"] += 1
                        self._invalidate_locked("stale_capture")
                    continue
                result = (engine.process_frame(frame) if hasattr(engine, "process_frame")
                          else engine.process(frame.image, frame.timestamp_s))
                observation = getattr(engine, "last_observation", None)
                metrics = engine.metrics() if hasattr(engine, "metrics") else {}
                with self._condition:
                    if generation != self._generation or self._paused or self._stop.is_set():
                        continue
                    age = time.monotonic() - received
                    if age > self.max_age_s:
                        result = self._invalid(result, "stale_processing_result")
                        self._counters["stale_frames"] += 1
                        observation = None
                    self._frame, self._result, self._observation = frame, result, observation
                    self._last_received = received
                    self._engine_metrics = metrics
                    self._counters["processed_frames"] += 1
                    self._process_times.append(time.monotonic())
                    self._state, self._last_error = "running", None
                if self.on_result is not None:
                    try:
                        self.on_result(frame, observation, result)
                    except Exception as exc:
                        with self._condition:
                            self._callback_error = f"{type(exc).__name__}: {exc}"
        except Exception as exc:
            with self._condition:
                self._last_error = f"{type(exc).__name__}: {exc}"
                self._state = "fault"
                self._generation += 1
                self._pending = None
                self._invalidate_locked("processing_fault")
                source = self._source
            self._stop.set()
            if source is not None:
                try:
                    source.request_stop()
                except Exception:
                    pass
        finally:
            if engine is not None:
                try:
                    engine.close()
                except Exception as exc:
                    with self._condition:
                        self._last_error = f"Engine close: {type(exc).__name__}: {exc}"
            with self._condition:
                self._engine = None
                self._condition.notify_all()

    def set_synthetic_target(self, x: float | None, y: float | None = None) -> bool:
        """Only the synthetic source supports this; no control affects a person."""
        with self._lock:
            source = self._source
        if source is None or not hasattr(source, "set_target"):
            return False
        if x is None:
            source.clear_target()
        else:
            source.set_target(float(x), float(y))
        return True

    def clear_callback_error(self) -> None:
        """Acknowledge a reported sink error before starting a new recording."""
        with self._condition:
            self._callback_error = None

    @staticmethod
    def _fps(times) -> float:
        return (len(times) - 1) / max(1e-9, times[-1] - times[0]) if len(times) > 1 else 0.0

    def snapshot(self, include_frame: bool = True) -> dict:
        now = time.monotonic()
        with self._lock:
            age = None if self._last_received is None else max(0.0, now - self._last_received)
            result = copy.deepcopy(self._result)
            observation = copy.deepcopy(self._observation)
            state = self._state
            if result is not None and (state != "running" or age is None or age > self.max_age_s):
                result = self._invalid(result, "stale_capture" if state == "running" else state)
                observation = None
            frame = None
            if include_frame and self._frame is not None:
                frame = replace(self._frame, image=self._frame.image.copy(),
                                metadata=copy.deepcopy(getattr(self._frame, "metadata", {})),
                                depth_m=None if getattr(self._frame, "depth_m", None) is None else self._frame.depth_m.copy())
            metrics = {**copy.deepcopy(self._engine_metrics), **self._counters,
                       "capture_fps": self._fps(self._capture_times),
                       "processing_fps": self._fps(self._process_times),
                       "capture_age_ms": None if age is None else age * 1000,
                       "callback_error": self._callback_error}
            return {"state": state, "running": state == "running" and self._alive_locked(),
                    "alive": self._alive_locked(), "last_error": self._last_error,
                    "latest": result.as_dict() if result is not None else None,
                    "result": result, "frame": frame, "observation": observation,
                    "metrics": metrics, "camera": copy.deepcopy(self._camera)}

    def telemetry(self) -> dict:
        """JSON-only API representation; never includes image or biometric features."""
        value = self.snapshot(include_frame=False)
        return {k: value[k] for k in ("state", "running", "alive", "last_error", "latest", "metrics", "camera")}
