"""Camera acquisition with explicit controls, owned buffers and observable failures.

Global shutter and IR sensitivity are properties of the physical sensor. These
adapters report what the driver exposes; they never infer those properties from
an USB connection. Camera SDK imports are optional and delayed until selected.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from threading import Event, Lock
import math
import time
from typing import Any

import cv2
import numpy as np

from .types import Frame

BACKENDS = {"auto": cv2.CAP_ANY, "dshow": cv2.CAP_DSHOW,
            "msmf": cv2.CAP_MSMF, "v4l2": cv2.CAP_V4L2,
            "ffmpeg": cv2.CAP_FFMPEG}


def to_bgr(image: np.ndarray, ir_bit_depth: int | None = None) -> np.ndarray:
    """Copy Mono8/10/12/14/16, BGR8 or BGRA8 into owned contiguous BGR8.

    Unpacked uint16 data must declare its significant bit depth (defaults to
    sixteen). Packed vendor formats must be unpacked by the camera SDK first.
    """
    if not isinstance(image, np.ndarray) or image.size == 0:
        raise ValueError("empty camera frame")
    if image.dtype == np.uint16:
        depth = 16 if ir_bit_depth is None else int(ir_bit_depth)
        if depth not in (8, 10, 12, 14, 16):
            raise ValueError("expected 8/10/12/14/16-bit sensor")
        image = np.clip(image.astype(np.float32) * (255.0 / (2 ** depth - 1)),
                        0, 255).astype(np.uint8)
    elif image.dtype != np.uint8:
        raise ValueError(f"unsupported pixel dtype: {image.dtype}")
    if image.ndim == 2:
        converted = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    elif image.ndim == 3 and image.shape[2] == 1:
        converted = cv2.cvtColor(image[:, :, 0], cv2.COLOR_GRAY2BGR)
    elif image.ndim == 3 and image.shape[2] == 3:
        converted = image
    elif image.ndim == 3 and image.shape[2] == 4:
        converted = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
    else:
        raise ValueError(f"unsupported image shape {image.shape}")
    return np.array(converted, dtype=np.uint8, order="C", copy=True)


def _finite(value: float, default: float = 0.0) -> float:
    value = float(value)
    return value if math.isfinite(value) else default


class FrameSource(ABC):
    """A source is owned and closed by the acquisition worker only.

    request_stop may be called from another thread and must be nonblocking.
    Some vendor/OS read calls cannot be interrupted; bounded read timeouts are
    recommended. The runtime reports a blocked stop instead of closing an SDK
    object while its read call is still active.
    """
    is_live: bool = True
    eof: bool = False

    @abstractmethod
    def read(self) -> Frame | None:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError

    def request_stop(self) -> None:
        pass

    def capabilities(self) -> dict[str, Any]:
        return {"is_live": self.is_live}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class OpenCVSource(FrameSource):
    def __init__(self, device: int | str = 0, backend: str = "auto",
                 width: int = 1280, height: int = 720, fps: float = 30,
                 exposure_native: float | None = None,
                 gain_native: float | None = None,
                 ir_bit_depth: int | None = None, realtime: bool = True,
                 auto_exposure_native: float | None = None,
                 fourcc: str | None = None):
        if backend not in BACKENDS:
            raise ValueError(f"backend must be one of {', '.join(BACKENDS)}")
        if width <= 0 or height <= 0 or fps <= 0:
            raise ValueError("camera dimensions and FPS must be positive")
        if isinstance(device, str) and device.isdecimal():
            device = int(device)
        self.device, self.backend, self.seq = device, backend, 0
        self.ir_bit_depth, self.realtime = ir_bit_depth, bool(realtime)
        self.is_live = isinstance(device, int) or not Path(str(device)).is_file()
        self.eof, self._closed = False, False
        self._stop = Event()
        self._origin: float | None = None
        self.controls: dict[str, dict[str, Any]] = {}
        self.cap = cv2.VideoCapture(device, BACKENDS[backend])
        if not self.cap.isOpened():
            self.cap.release()
            self._closed = True
            raise OSError(f"Cannot open {device!r} using {backend}; check device, driver and camera exclusivity")
        if isinstance(device, int):
            self._control("width", cv2.CAP_PROP_FRAME_WIDTH, width)
            self._control("height", cv2.CAP_PROP_FRAME_HEIGHT, height)
            self._control("fps", cv2.CAP_PROP_FPS, fps)
            self._control("buffer_size", cv2.CAP_PROP_BUFFERSIZE, 1)
            if fourcc is not None:
                if len(fourcc) != 4:
                    self.close()
                    raise ValueError("fourcc must contain four characters, e.g. MJPG")
                self._control("fourcc", cv2.CAP_PROP_FOURCC,
                              cv2.VideoWriter_fourcc(*fourcc))
            if exposure_native is not None:
                # Explicit backend conventions; readback indicates whether the
                # driver accepted them. Unsupported controls are never hidden.
                manual = auto_exposure_native
                if manual is None:
                    manual = {"dshow": 0.25, "msmf": 0.0, "v4l2": 1.0}.get(backend)
                if manual is not None:
                    self._control("auto_exposure", cv2.CAP_PROP_AUTO_EXPOSURE, manual)
                else:
                    self.controls["auto_exposure"] = {
                        "applied": False,
                        "error": "Unknown backend exposure convention; specify auto_exposure_native"}
                self._control("exposure_native", cv2.CAP_PROP_EXPOSURE, exposure_native)
            if gain_native is not None:
                self._control("gain_native", cv2.CAP_PROP_GAIN, gain_native)
        reported = _finite(self.cap.get(cv2.CAP_PROP_FPS), float(fps))
        self.video_fps = reported if reported > 0 else float(fps)
        self._report = {
            "source": str(device),
            "camera_id": f"opencv:{backend}:{device}",
            "identity_strength": "device_index_only" if isinstance(device, int) else "source_path",
            "backend": self.cap.getBackendName(), "requested_backend": backend,
            "width": int(_finite(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))),
            "height": int(_finite(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))),
            "fps_reported": reported, "is_live": self.is_live,
            "controls": self.controls,
            "global_shutter_verified": False,
            "timestamp_kind": "host_receive" if self.is_live else "paced_video_presentation",
            "exposure_native": _finite(self.cap.get(cv2.CAP_PROP_EXPOSURE)),
            "gain_native": _finite(self.cap.get(cv2.CAP_PROP_GAIN)),
        }

    def _control(self, name: str, prop: int, value: float) -> None:
        try:
            accepted = bool(self.cap.set(prop, float(value)))
            actual = _finite(self.cap.get(prop))
            self.controls[name] = {"requested": value, "applied": accepted,
                                   "actual": actual,
                                   "readback_matches": accepted and bool(np.isclose(actual, value, rtol=.02, atol=.01))}
        except cv2.error as exc:
            self.controls[name] = {"requested": value, "applied": False, "error": str(exc)}

    def read(self) -> Frame | None:
        if self._closed or self._stop.is_set():
            return None
        ok, raw = self.cap.read()
        arrived = time.monotonic()
        if not ok or raw is None:
            if not self.is_live:
                self.eof = True
            return None
        # A video is paced at its own rate. Presentation timestamps drive the
        # temporal model even if realtime=False is used for a batch analysis.
        if self._origin is None:
            self._origin = arrived
        pts_s = self.seq / self.video_fps
        if not self.is_live:
            target = self._origin + pts_s
            if self.realtime and arrived - target > max(.25, 5 / self.video_fps):
                # Resume a paused video without emitting a fast catch-up burst.
                self._origin += arrived - target
                target = arrived
            if self.realtime and self._stop.wait(max(0.0, target - arrived)):
                return None
            timestamp = target
        else:
            timestamp = arrived
        self.seq += 1
        return Frame(to_bgr(raw, self.ir_bit_depth), timestamp, self.seq, str(self.device),
                     metadata={"simulated": False, "is_live": self.is_live,
                               "video_pts_s": None if self.is_live else pts_s,
                               "timestamp_kind": self._report["timestamp_kind"]},
                     received_monotonic_s=time.monotonic())

    def capabilities(self) -> dict:
        return dict(self._report)

    def request_stop(self) -> None:
        self._stop.set()

    def close(self) -> None:
        self._stop.set()
        if not self._closed:
            self._closed = True
            self.cap.release()


class PylonSource(FrameSource):
    """Basler adapter with serial binding and explicit control readback.

    Buffers are copied before Release. A short bounded RetrieveResult timeout
    permits shutdown. Timeout, failed grabs and unplug events remain observable
    exceptions so the runtime can reconnect rather than replay stale gaze.
    """
    is_live = True

    def __init__(self, serial: str | None = None, exposure_us: float | None = None,
                 gain: float | None = None, timeout_ms: int = 500,
                 ir_bit_depth: int | None = None, pixel_format: str | None = None,
                 trigger_mode: str | None = None, trigger_source: str | None = None,
                 width: int | None = None, height: int | None = None,
                 fps: float | None = None):
        if timeout_ms <= 0 or timeout_ms > 10000:
            raise ValueError("timeout_ms must be 1..10000")
        if exposure_us is not None and exposure_us <= 0:
            raise ValueError("exposure_us must be positive")
        for name, value in (("width", width), ("height", height)):
            if value is not None and (isinstance(value, bool) or not np.isfinite(value)
                                      or value <= 0 or int(value) != value):
                raise ValueError(f"{name} must be a positive integer")
        if fps is not None and (isinstance(fps, bool) or not np.isfinite(fps) or fps <= 0):
            raise ValueError("fps must be positive and finite")
        try:
            from pypylon import pylon
        except ImportError as exc:
            raise RuntimeError("Install gazespace[basler] and the matching Basler camera driver") from exc
        self.pylon = pylon
        devs = pylon.TlFactory.GetInstance().EnumerateDevices()
        selected = [dev for dev in devs if serial is None or dev.GetSerialNumber() == serial]
        if not selected:
            raise OSError(f"No Basler camera found for serial={serial!r}")
        self.cam = pylon.InstantCamera(pylon.TlFactory.GetInstance().CreateDevice(selected[0]))
        self._closed, self._stop = False, Event()
        self.seq, self.timeout_ms, self.ir_bit_depth = 0, int(timeout_ms), ir_bit_depth
        self.controls = {}
        device_class = selected[0].GetDeviceClass() if hasattr(selected[0], "GetDeviceClass") else "unknown"
        self._sdk_emulator = device_class == "BaslerCamEmu"
        self.info = {"serial": str(selected[0].GetSerialNumber()), "model": selected[0].GetModelName(),
                     "device_class": device_class, "sdk_emulator": self._sdk_emulator,
                     "simulated": self._sdk_emulator, "synthetic": self._sdk_emulator,
                     "camera_id": f"basler:{selected[0].GetSerialNumber()}", "identity_strength": "serial",
                     "backend": "pylon", "is_live": True, "global_shutter_verified": False,
                     "requested_width": width, "requested_height": height, "requested_fps": fps,
                     "timestamp_kind": "host_receive; hardware ticks reported separately"}
        try:
            self.cam.Open()
            if exposure_us is not None:
                auto_ok = self._set_node("ExposureAuto", "Off")
                if auto_ok:
                    self._set_node("ExposureTime", exposure_us)
                else:
                    self.controls["ExposureTime"] = {"requested": exposure_us, "applied": False,
                                                       "error": "Could not disable ExposureAuto"}
            if gain is not None:
                auto_ok = self._set_node("GainAuto", "Off")
                if auto_ok:
                    self._set_node("Gain", gain)
                else:
                    self.controls["Gain"] = {"requested": gain, "applied": False,
                                             "error": "Could not disable GainAuto"}
            for name, value in (("PixelFormat", pixel_format), ("TriggerMode", trigger_mode),
                                ("TriggerSource", trigger_source)):
                if value is not None:
                    self._set_node(name, value)
            # Keep sensor constraints visible instead of silently rounding or
            # changing an existing ROI offset. Rejected settings retain their
            # requested value and the actual device readback in capabilities.
            for name, value in (("Width", width), ("Height", height)):
                if value is not None:
                    self._set_node(name, int(value))
            if fps is not None:
                self._set_frame_rate(float(fps))
            self.converter = pylon.ImageFormatConverter()
            self.converter.OutputPixelFormat = pylon.PixelType_BGR8packed
            if hasattr(pylon, "OutputBitAlignment_MsbAligned"):
                self.converter.OutputBitAlignment = pylon.OutputBitAlignment_MsbAligned
            self.cam.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)
        except Exception:
            self.close()
            raise

    def _set_node(self, name, value) -> bool:
        node = None
        try:
            node = getattr(self.cam, name)
            node.Value = value
            actual = node.Value
            matches = (actual == value if isinstance(value, (str, bool, int))
                       else bool(np.isclose(float(actual), float(value), rtol=.01)))
            self.controls[name] = {"requested": value, "applied": bool(matches), "actual": actual,
                                   "readback_matches": bool(matches)}
            if not matches:
                self.controls[name]["error"] = "Device readback differs from requested value"
            return bool(matches)
        except Exception as exc:  # SDK-specific GenICam exception subclasses vary by release.
            self.controls[name] = {"requested": value, "applied": False,
                                   "readback_matches": False,
                                   "error": f"{type(exc).__name__}: {exc}"}
            if node is not None:
                try:
                    self.controls[name]["actual"] = node.Value
                except Exception:
                    pass
            return False

    def _read_node(self, *names):
        for name in names:
            try:
                return name, getattr(self.cam, name).Value
            except Exception:
                continue
        return None, None

    def _set_frame_rate(self, fps: float) -> None:
        # Legacy cameras use the Abs suffix. Some cameras have no enable node
        # because their rate control is always enabled; the rate write/readback
        # is still attempted. External triggers may further limit throughput.
        name, _ = self._read_node("AcquisitionFrameRate", "AcquisitionFrameRateAbs")
        name = name or "AcquisitionFrameRate"
        enable, _ = self._read_node("AcquisitionFrameRateEnable")
        if enable is not None and not self._set_node(enable, True):
            self.controls[name] = {"requested": fps, "applied": False,
                                   "readback_matches": False,
                                   "error": "Could not enable camera frame-rate control"}
            return
        self._set_node(name, fps)

    def read(self) -> Frame | None:
        if self._closed or self._stop.is_set():
            return None
        if not self.cam.IsGrabbing():
            raise OSError("Basler acquisition stopped")
        result = self.cam.RetrieveResult(self.timeout_ms, self.pylon.TimeoutHandling_ThrowException)
        try:
            if not result.GrabSucceeded():
                code = getattr(result, "ErrorCode", "unknown")
                desc = getattr(result, "ErrorDescription", "grab failed")
                raise OSError(f"Basler grab failed {code}: {desc}")
            if result.PixelType == self.pylon.PixelType_Mono8:
                bgr = to_bgr(result.Array)
            elif self.ir_bit_depth and result.PixelType in {
                    getattr(self.pylon, name, None) for name in
                    ("PixelType_Mono10", "PixelType_Mono12", "PixelType_Mono14", "PixelType_Mono16")}:
                # Only unpacked monochrome data uses this direct uint16 path;
                # packed/Bayer types go through the vendor converter below.
                bgr = to_bgr(result.Array, self.ir_bit_depth)
            else:
                converted = self.converter.Convert(result)
                bgr = to_bgr(converted.Array)
            arrived = time.monotonic()
            ticks = int(getattr(result, "TimeStamp", 0))
        finally:
            result.Release()
        self.seq += 1
        return Frame(bgr, arrived, self.seq, self.info["serial"],
                     metadata={"hardware_ticks": ticks, "simulated": self._sdk_emulator,
                               "synthetic": self._sdk_emulator, "sdk_emulator": self._sdk_emulator,
                               "hardware_clock_synchronized": False},
                     received_monotonic_s=arrived)

    def capabilities(self) -> dict:
        report = {**self.info, "controls": dict(self.controls)}
        for node_name, field in (("Width", "width"), ("Height", "height"),
                                 ("PixelFormat", "pixel_format")):
            try:
                report[field] = getattr(self.cam, node_name).Value
            except Exception:
                report[field] = None
        node, rate = self._read_node("ResultingFrameRate", "ResultingFrameRateAbs",
                                    "AcquisitionFrameRate", "AcquisitionFrameRateAbs")
        report["fps_reported"], report["fps_reported_node"] = rate, node
        report["fps_reported_note"] = "Device rate readback; actual delivered FPS requires frame timestamps"
        return report

    def request_stop(self) -> None:
        self._stop.set()

    def close(self) -> None:
        self._stop.set()
        if not self._closed:
            self._closed = True
            try:
                if self.cam.IsGrabbing():
                    self.cam.StopGrabbing()
            finally:
                if self.cam.IsOpen():
                    self.cam.Close()


class SyntheticEyeSource(FrameSource):
    """Procedural pupil frames for deterministic demonstrations and replay tests.

    The metadata and pixels are prominently marked synthetic. Ground-truth
    target_uv describes the generator, not human gaze accuracy. set_target is
    useful for an explicitly simulated calibration demonstration.
    """
    is_live = False
    DEFAULT_ROIS = {"right": (.23, .39, .22, .24), "left": (.55, .39, .22, .24)}

    def __init__(self, width: int = 640, height: int = 360, fps: float = 30,
                 duration_s: float | None = None, realtime: bool = True,
                 polarity: str = "dark", blink: bool = False):
        if min(width, height) < 120 or fps <= 0 or polarity not in ("dark", "bright"):
            raise ValueError("Invalid synthetic source settings")
        if duration_s is not None and duration_s <= 0:
            raise ValueError("duration_s must be positive")
        self.width, self.height, self.fps = int(width), int(height), float(fps)
        self.duration_s, self.realtime, self.polarity = duration_s, bool(realtime), polarity
        self.blink, self.eof, self.seq = blink, False, 0
        self._stop, self._target_lock = Event(), Lock()
        self._target: tuple[float, float] | None = None
        self._origin: float | None = None
        self.roi_normalized = dict(self.DEFAULT_ROIS)

    def set_target(self, x: float, y: float) -> None:
        if not all(math.isfinite(v) and 0 <= v <= 1 for v in (x, y)):
            raise ValueError("Synthetic target must be in [0,1]")
        with self._target_lock:
            self._target = (float(x), float(y))

    def clear_target(self) -> None:
        with self._target_lock:
            self._target = None

    def read(self) -> Frame | None:
        if self._stop.is_set():
            return None
        elapsed = self.seq / self.fps
        if self.duration_s is not None and elapsed >= self.duration_s:
            self.eof = True
            return None
        if self._origin is None:
            self._origin = time.monotonic()
        stamp = self._origin + elapsed
        now = time.monotonic()
        if self.realtime and now - stamp > max(.25, 5 / self.fps):
            self._origin += now - stamp
            stamp = now
        if self.realtime and self._stop.wait(max(0.0, stamp - time.monotonic())):
            return None
        with self._target_lock:
            target = self._target
        tx, ty = target if target is not None else (.5 + .34 * math.sin(elapsed * .65),
                                                   .5 + .28 * math.cos(elapsed * .49))
        is_blink = self.blink and int(elapsed * 20) % 120 in (116, 117, 118)
        image, pupil_xy = self._render(tx, ty, is_blink)
        self.seq += 1
        return Frame(image, stamp, self.seq, "synthetic:eyes-v2",
                     metadata={"simulated": True, "synthetic": True, "target_normalized": [tx, ty],
                               "pupils_ground_truth_px": pupil_xy, "blink": is_blink,
                               "video_pts_s": elapsed, "timestamp_kind": "synthetic_presentation"},
                     received_monotonic_s=time.monotonic())

    def render_target(self, x: float, y: float) -> np.ndarray:
        """Render a labeled synthetic frame for a known generator target."""
        if not all(math.isfinite(v) and 0 <= v <= 1 for v in (x, y)):
            raise ValueError("Synthetic target must be in [0,1]")
        return self._render(float(x), float(y), False)[0]

    def _render(self, tx: float, ty: float, is_blink: bool):
        w, h = self.width, self.height
        image = np.full((h, w, 3), (31, 37, 48), np.uint8)
        pupil_xy = {}
        for side, roi in self.roi_normalized.items():
            x, y, rw, rh = roi
            cx, cy = int((x + rw / 2) * w), int((y + rh / 2) * h)
            px = int((x + rw * (.5 + .5 * (tx - .5))) * w)
            py = int((y + rh * (.5 + .45 * (ty - .5))) * h)
            # The synthetic fixture keeps the full moving pupil inside its
            # sclera at the advertised target extremes. A smaller eye ellipse
            # caused corner targets to touch the eyelid and become occluded,
            # which is valid rejection by the real detector but unsuitable for
            # a deterministic calibration fixture.
            ax, ay = int(rw * w * .50), int(rh * h * .49)
            bright = 190 if self.polarity == "dark" else 38
            pupil = 15 if self.polarity == "dark" else 235
            cv2.ellipse(image, (cx, cy), (ax, ay), 0, 0, 360, (bright,) * 3, -1, cv2.LINE_AA)
            radius = max(5, int(min(w / 640, h / 360) * 13))
            if not is_blink:
                cv2.circle(image, (px, py), radius, (pupil,) * 3, -1, cv2.LINE_AA)
                if self.polarity == "dark":
                    cv2.circle(image, (px - radius // 3, py - radius // 3), 2, (252,) * 3, -1)
                pupil_xy[side] = [px, py]
            else:
                cv2.line(image, (cx - ax, cy), (cx + ax, cy), (95,) * 3, 3)
            cv2.rectangle(image, (int(x * w), int(y * h)),
                          (int((x + rw) * w), int((y + rh) * h)), (64, 122, 132), 1)
        font_scale = max(.30, w / 1280)
        cv2.putText(image, "SYNTHETIC SENSOR / NO HUMAN GAZE", (max(8, w // 40), max(25, h // 10)),
                    cv2.FONT_HERSHEY_SIMPLEX, font_scale, (199, 224, 238), 1, cv2.LINE_AA)
        return image, pupil_xy

    def capabilities(self) -> dict:
        return {"source": "synthetic", "camera_id": "synthetic:eyes-v2", "backend": "synthetic",
                "width": self.width, "height": self.height, "fps_reported": self.fps,
                "simulated": True, "synthetic": True, "is_live": False, "roi_normalized": self.roi_normalized,
                "global_shutter_verified": False}

    def request_stop(self) -> None:
        self._stop.set()

    def close(self) -> None:
        self._stop.set()


def list_cameras(max_index: int = 6, backend: str = "auto") -> list[dict]:
    if backend not in BACKENDS:
        raise ValueError(f"Unknown backend {backend}")
    if not 0 <= max_index <= 32:
        raise ValueError("max_index must be 0..32")
    found = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i, BACKENDS[backend])
        try:
            if cap.isOpened():
                ok, frame = cap.read()
                found.append({"index": i, "backend": backend, "camera_id": f"opencv:{backend}:{i}",
                              "first_frame": bool(ok), "identity_strength": "device_index_only",
                              "size": list(frame.shape[:2][::-1]) if ok and frame is not None else None})
        finally:
            cap.release()
    return found


def list_basler_cameras() -> list[dict]:
    try:
        from pypylon import pylon
    except ImportError:
        return []
    return [{"serial": v.GetSerialNumber(), "model": v.GetModelName(),
             "camera_id": f"basler:{v.GetSerialNumber()}", "vendor": v.GetVendorName()}
            for v in pylon.TlFactory.GetInstance().EnumerateDevices()]


def create_source(source: int | str = 0, backend: str = "auto", **kwargs) -> FrameSource:
    if backend == "synthetic" or source == "synthetic":
        return SyntheticEyeSource(width=kwargs.get("width", 640), height=kwargs.get("height", 360),
                                  fps=kwargs.get("fps", 30), duration_s=kwargs.get("duration_s"),
                                  realtime=kwargs.get("realtime", True),
                                  polarity=kwargs.get("pupil_polarity", "dark"),
                                  blink=kwargs.get("blink", False))
    if backend == "pylon":
        return PylonSource(serial=None if str(source) in ("first", "0") else str(source),
                           exposure_us=kwargs.get("exposure_us"), gain=kwargs.get("gain"),
                           width=kwargs.get("width"), height=kwargs.get("height"), fps=kwargs.get("fps"),
                           ir_bit_depth=kwargs.get("ir_bit_depth"),
                           timeout_ms=kwargs.get("timeout_ms", 500),
                           pixel_format=kwargs.get("pixel_format"),
                           trigger_mode=kwargs.get("trigger_mode"), trigger_source=kwargs.get("trigger_source"))
    if backend == "realsense":
        from .depth import RealSenseSource
        return RealSenseSource(serial=None if str(source) in ("first", "0") else str(source),
                               width=kwargs.get("width", 640), height=kwargs.get("height", 480),
                               fps=kwargs.get("fps", 30), timeout_ms=kwargs.get("timeout_ms", 500))
    return OpenCVSource(source, backend, width=kwargs.get("width", 1280),
                        height=kwargs.get("height", 720), fps=kwargs.get("fps", 30),
                        ir_bit_depth=kwargs.get("ir_bit_depth"),
                        exposure_native=kwargs.get("exposure_native"),
                        gain_native=kwargs.get("gain_native"), realtime=kwargs.get("realtime", True),
                        auto_exposure_native=kwargs.get("auto_exposure_native"),
                        fourcc=kwargs.get("fourcc"))
