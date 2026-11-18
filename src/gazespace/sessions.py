"""Local, opt-in gaze recordings, strict replay and portable CSV export.

Raw camera video is written only when ``record_video=True`` is explicitly
selected. Session timestamps preserve source timestamps and also add an
independent monotonic elapsed time for replay across camera reconnects.
"""
from __future__ import annotations

from bisect import bisect_right
from copy import deepcopy
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Callable
import csv
import json
import math
import os
import uuid
import numpy as np

from .clock import HOST_CLOCK_DOMAIN, host_now_s

SESSION_SCHEMA_VERSION = 2


def _json_value(value):
    if is_dataclass(value):
        return _json_value(asdict(value))
    if isinstance(value, np.ndarray):
        return _json_value(value.tolist())
    if isinstance(value, np.generic):
        return _json_value(value.item())
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("session values must be finite; NaN/Infinity cannot be saved")
        return value
    raise TypeError(f"unsupported session value {type(value).__name__}")


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, content: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(_json_value(content), handle, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _validate_record(record: dict) -> dict:
    data = _json_value(record)
    if not isinstance(data, dict):
        raise ValueError("gaze record must be a JSON object")
    for key in ("timestamp_s", "session_time_s"):
        if key in data and (isinstance(data[key], bool) or not isinstance(data[key], (int, float))):
            raise ValueError(f"{key} must be numeric")
    if "timestamp_s" not in data:
        raise ValueError("gaze record is missing its source timestamp")
    if "valid" not in data or not isinstance(data["valid"], bool):
        raise ValueError("gaze record requires a boolean valid flag")
    for key, length in (("gaze_screen_px", 2), ("gaze_screen_normalized", 2),
                        ("raw_gaze_screen_px", 2), ("surface_xy_m", 2),
                        ("surface_xy_px", 2), ("ray_origin_world_m", 3),
                        ("ray_direction_world", 3)):
        item = data.get(key)
        if item is not None:
            if not isinstance(item, list) or len(item) != length:
                raise ValueError(f"invalid {key} shape")
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in item):
                raise ValueError(f"invalid {key} coordinate")
    return data


class SessionRecorder:
    """Create one unique local session with bounded-memory append operations.

    The caller owns consent/UI and chooses when recording begins. Metadata may
    include screen_size, subject_id, profile_fingerprint, camera and provenance
    (``synthetic``, ``live`` or ``video``). No model guesses physical accuracy.
    """

    def __init__(self, root: str | Path, metadata: dict | None = None, *,
                 record_gaze: bool = True, record_video: bool = False,
                 fps: float = 30.0, clock: Callable[[], float] | None = None):
        if not record_gaze and not record_video:
            raise ValueError("select gaze metadata, camera video, or both for recording")
        if not math.isfinite(fps) or not 1 <= fps <= 240:
            raise ValueError("video FPS must lie within [1,240]")
        self._clock = host_now_s if clock is None else clock
        self._start = float(self._clock())
        if not math.isfinite(self._start):
            raise ValueError("recording clock must be finite")
        self._lock = RLock()
        self._last_elapsed = 0.0
        self._closed = False
        self._video = None
        self._video_size = None
        self._video_count = 0
        self._count = 0
        self._valid = 0
        self._record_gaze = bool(record_gaze)
        self._record_video = bool(record_video)
        self._fps = float(fps)
        self._metadata = _json_value(metadata or {})
        self.session_id = uuid.uuid4().hex
        name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + self.session_id[:10]
        self.path = Path(root).expanduser().resolve() / name
        self.path.mkdir(parents=True, exist_ok=False)
        self.manifest = {
            "schema_version": SESSION_SCHEMA_VERSION,
            "session_time_clock_domain": HOST_CLOCK_DOMAIN if clock is None else "custom_monotonic_relative",
            "session_id": self.session_id,
            "created_utc": _utc_now(), "closed_utc": None,
            "status": "recording", "metadata": self._metadata,
            "provenance": self._metadata.get("provenance", "unclassified"),
            "physical_accuracy_verified": False,
            "record_gaze": self._record_gaze, "record_video": self._record_video,
            "records_file": "gaze.jsonl" if record_gaze else None,
            "events_file": "events.jsonl", "video_file": None,
            "video_fps": self._fps if record_video else None,
            "video_timing": "constant_fps_review_only; use gaze session_time_s for measurements" if record_video else None,
            "records": 0, "valid_records": 0, "video_frames": 0,
            "duration_s": 0.0,
            "privacy": "local_only; video opt-in; no network transfer",
        }
        self._gaze = None
        self._events = None
        try:
            if record_gaze:
                self._gaze = (self.path / "gaze.jsonl").open("x", encoding="utf-8")
            self._events = (self.path / "events.jsonl").open("x", encoding="utf-8")
            _atomic_json(self.path / "manifest.json", self.manifest)
            self.event("recording_started", {"record_gaze": record_gaze, "record_video": record_video})
        except Exception:
            if self._gaze:
                self._gaze.close()
            if self._events:
                self._events.close()
            raise

    @property
    def closed(self):
        return self._closed

    def _elapsed(self):
        value = float(self._clock()) - self._start
        if not math.isfinite(value) or value < self._last_elapsed - 1e-6:
            raise ValueError("recording clock moved backwards or became invalid")
        self._last_elapsed = max(value, self._last_elapsed, 0.0)
        return self._last_elapsed

    def append(self, result, image_bgr: np.ndarray | None = None) -> dict:
        data = result.as_dict() if hasattr(result, "as_dict") else result
        row = _validate_record(data)
        with self._lock:
            if self._closed:
                raise RuntimeError("session is already closed")
            row["session_time_s"] = self._elapsed()
            row["record_sequence"] = self._count
            if self._record_video and image_bgr is not None:
                import cv2
                image = np.asarray(image_bgr)
                if image.ndim != 3 or image.shape[2] != 3 or image.dtype != np.uint8:
                    raise ValueError("recorded video needs BGR uint8 HxWx3 frames")
                size = (int(image.shape[1]), int(image.shape[0]))
                if min(size) < 2:
                    raise ValueError("video frame dimensions are too small")
                if self._video is None:
                    self._video = cv2.VideoWriter(str(self.path / "camera.avi"),
                                                 cv2.VideoWriter_fourcc(*"MJPG"), self._fps, size)
                    if not self._video.isOpened():
                        self._video.release()
                        self._video = None
                        raise OSError("MJPG encoder unavailable; disable optional video recording")
                    self._video_size = size
                    self.manifest["video_file"] = "camera.avi"
                    self.manifest["video_size"] = list(size)
                    _atomic_json(self.path / "manifest.json", self.manifest)
                if size != self._video_size:
                    raise ValueError("camera resolution changed during recording; start a new session")
                self._video.write(np.ascontiguousarray(image))
                row["video_frame"] = self._video_count
                self._video_count += 1
            elif self._record_video:
                row["video_frame"] = None
            if self._gaze is not None:
                self._gaze.write(json.dumps(row, allow_nan=False, separators=(",", ":")) + "\n")
                self._gaze.flush()
            self._count += 1
            self._valid += int(row["valid"])
            return deepcopy(row)

    def event(self, name: str, details: dict | None = None):
        with self._lock:
            if self._closed:
                raise RuntimeError("session is already closed")
            row = {"event": str(name), "session_time_s": self._elapsed(),
                   "details": _json_value(details or {})}
            self._events.write(json.dumps(row, allow_nan=False) + "\n")
            self._events.flush()

    def close(self, error: str | None = None) -> Path:
        with self._lock:
            if self._closed:
                return self.path
            try:
                self.event("recording_stopped", {"error": error})
                for handle in (self._gaze, self._events):
                    if handle:
                        handle.flush()
                        os.fsync(handle.fileno())
                        handle.close()
                if self._video is not None:
                    self._video.release()
                self.manifest.update({"status": "failed" if error else "completed",
                                      "closed_utc": _utc_now(), "error": error,
                                      "records": self._count if self._record_gaze else 0,
                                      "observations_seen": self._count,
                                      "valid_records": self._valid if self._record_gaze else 0,
                                      "video_frames": self._video_count,
                                      "duration_s": self._last_elapsed})
                _atomic_json(self.path / "manifest.json", self.manifest)
            finally:
                self._closed = True
                for handle in (self._gaze, self._events):
                    if handle and not handle.closed:
                        handle.close()
                if self._video is not None:
                    self._video.release()
            return self.path

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close(str(exc) if exc else None)


@dataclass
class SessionData:
    path: Path
    manifest: dict
    records: list[dict]
    events: list[dict]


def _read_json(path: Path):
    def invalid(text):
        raise ValueError(f"nonfinite JSON number {text}")
    return json.loads(path.read_text(encoding="utf-8"), parse_constant=invalid)


def _safe_child(root: Path, name: str):
    result = (root / name).resolve()
    if result.parent != root.resolve():
        raise ValueError("session manifest may only reference local immediate files")
    return result


def load_session(path: str | Path, *, strict: bool = True) -> SessionData:
    root = Path(path).expanduser().resolve()
    if root.name == "manifest.json":
        root = root.parent
    manifest = _read_json(root / "manifest.json")
    if manifest.get("schema_version") != SESSION_SCHEMA_VERSION:
        raise ValueError("unsupported session schema")
    records, events = [], []
    filename = manifest.get("records_file")
    if filename:
        file = _safe_child(root, filename)
        with file.open(encoding="utf-8") as handle:
            previous = -math.inf
            for line_no, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    record = _validate_record(json.loads(line))
                    elapsed = record.get("session_time_s")
                    if elapsed is None or elapsed < 0 or elapsed < previous:
                        raise ValueError("session replay timestamps must be nonnegative and monotonic")
                    previous = elapsed
                    records.append(record)
                except (ValueError, TypeError, KeyError) as exc:
                    if strict:
                        raise ValueError(f"invalid gaze.jsonl line {line_no}: {exc}") from exc
    event_name = manifest.get("events_file")
    if event_name:
        event_path = _safe_child(root, event_name)
        if event_path.exists():
            with event_path.open(encoding="utf-8") as handle:
                for line_no, line in enumerate(handle, 1):
                    if line.strip():
                        event = _json_value(json.loads(line))
                        if not isinstance(event, dict):
                            raise ValueError(f"invalid event line {line_no}")
                        events.append(event)
    if strict and manifest.get("status") == "completed" and len(records) != manifest.get("records", 0):
        raise ValueError("session record count differs from its finalized manifest")
    return SessionData(root, manifest, records, events)


def list_sessions(root: str | Path, limit: int = 200) -> list[dict]:
    if limit < 1:
        return []
    parent = Path(root).expanduser()
    if not parent.exists():
        return []
    found = []
    for path in parent.glob("*/manifest.json"):
        try:
            manifest = _read_json(path)
            if manifest.get("schema_version") == SESSION_SCHEMA_VERSION:
                found.append({**manifest, "path": str(path.parent.resolve())})
        except (OSError, ValueError, TypeError):
            continue
    return sorted(found, key=lambda row: row.get("created_utc", ""), reverse=True)[:limit]


CSV_COLUMNS = ("session_time_s", "timestamp_s", "record_sequence", "valid", "screen_valid", "spatial_valid",
               "reason", "quality", "source", "gaze_x_px", "gaze_y_px", "raw_gaze_x_px", "raw_gaze_y_px",
               "surface_name", "surface_x_m", "surface_y_m", "surface_x_px", "surface_y_px",
               "origin_x_m", "origin_y_m", "origin_z_m", "direction_x", "direction_y", "direction_z",
               "processing_ms", "eye_origin_source", "model_fingerprint", "warnings")


def export_csv(session_or_path: SessionData | str | Path, output: str | Path) -> Path:
    data = session_or_path if isinstance(session_or_path, SessionData) else load_session(session_or_path)
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for record in data.records:
            row = dict(record)
            for field, columns in (("gaze_screen_px", ("gaze_x_px", "gaze_y_px")),
                                   ("raw_gaze_screen_px", ("raw_gaze_x_px", "raw_gaze_y_px")),
                                   ("surface_xy_m", ("surface_x_m", "surface_y_m")),
                                   ("surface_xy_px", ("surface_x_px", "surface_y_px")),
                                   ("ray_origin_world_m", ("origin_x_m", "origin_y_m", "origin_z_m")),
                                   ("ray_direction_world", ("direction_x", "direction_y", "direction_z"))):
                row.update(zip(columns, record.get(field) or [None] * len(columns)))
            row["warnings"] = ";".join(str(x) for x in record.get("warnings", []))
            # Spreadsheet tools can treat leading strings as formulas. Prefix
            # only text values; do not modify legitimate numeric coordinates.
            for key, value in list(row.items()):
                if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
                    row[key] = "'" + value
            writer.writerow(row)
    return out


class SessionReplay:
    """Deterministic metadata replay. No output is sent to input devices."""

    def __init__(self, records_or_session, *, max_gap_s: float = 0.25):
        if not math.isfinite(max_gap_s) or max_gap_s <= 0:
            raise ValueError("replay stale-gap threshold must be finite and positive")
        self.max_gap_s = float(max_gap_s)
        if isinstance(records_or_session, SessionData):
            records = records_or_session.records
        elif isinstance(records_or_session, (str, Path)):
            records = load_session(records_or_session).records
        else:
            records = records_or_session
        self.records = [_validate_record(r) for r in records]
        if self.records:
            raw = [float(r.get("session_time_s", r["timestamp_s"])) for r in self.records]
            if any(b < a for a, b in zip(raw, raw[1:])):
                raise ValueError("replay records must be ordered by timestamp")
            self._times = [t - raw[0] for t in raw]
        else:
            self._times = []
        self.duration_s = self._times[-1] if self._times else 0.0
        self.position_s = 0.0
        self.index = 0 if self.records else -1

    @property
    def finished(self) -> bool:
        return not self.records or self.position_s >= self.duration_s

    def current(self) -> dict | None:
        if self.index < 0:
            return None
        result = deepcopy(self.records[self.index])
        result["replay"] = True
        result["replay_position_s"] = self.position_s
        if self.position_s - self._times[self.index] > self.max_gap_s:
            result["recorded_valid"] = result["valid"]
            result["valid"] = False
            result["screen_valid"] = result["spatial_valid"] = False
            result["reason"] = "replay_gap_no_fresh_sample"
            result["stale"] = True
            for key in ("gaze_screen_px", "gaze_screen_normalized", "raw_gaze_screen_px",
                        "surface_name", "surface_xy_m", "surface_xy_px",
                        "ray_origin_world_m", "ray_direction_world"):
                if key in result:
                    result[key] = None
        return result

    def seek(self, seconds: float) -> dict | None:
        if not math.isfinite(seconds):
            raise ValueError("seek position must be finite")
        self.position_s = max(0.0, min(float(seconds), self.duration_s))
        self.index = max(0, bisect_right(self._times, self.position_s) - 1) if self._times else -1
        return self.current()

    def advance(self, delta_s: float) -> dict | None:
        if not math.isfinite(delta_s) or delta_s < 0:
            raise ValueError("replay step must be finite and nonnegative")
        return self.seek(self.position_s + delta_s)
