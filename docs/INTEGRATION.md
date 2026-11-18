# GazeSpace 0.2 — Integration contract

**18 November 2026.** The Python engine, capture/runtime components and local telemetry service can be integrated into an existing Windows application. The client's original repository was not supplied, so the deliverable provides working interfaces and examples rather than claiming an already completed proprietary-code integration.

## 1. Choose one integration boundary

| Boundary | Use when | Responsibility |
|---|---|---|
| `GazeEngine.process` | Your application already owns a BGR camera stream | Host controls frame acquisition, timestamps, worker thread and UI updates |
| `GazeEngine.process_frame` | Frames also contain aligned depth and source metadata | Host preserves calibrated depth alignment and metadata |
| `TrackingRuntime` | GazeSpace should own acquisition and inference workers | GUI polls snapshots or receives an explicit result callback |
| Local HTTP/WebSocket | Existing code is a separate process/language | Consumer authenticates, checks freshness and clears invalid estimates |

Do not open the same camera simultaneously through an existing acquisition loop and a second GazeSpace source. Reuse the current frames or move ownership deliberately.

## 2. Frame and timestamp contract

`process(image_bgr, timestamp_s)` expects an unsigned 8-bit BGR array of shape `(height,width,3)`. The array must match the intrinsic calibration's exact resolution. Use `capture.to_bgr(raw, ir_bit_depth=12)` for a declared unpacked Mono12 source; packed industrial formats should be decoded through the vendor adapter.

Use a strictly increasing source timestamp for filtering. A host-receive timestamp is useful for processing, but it is not the sensor's exposure time. Preserve synchronized hardware timestamps separately when available. Video sources preserve presentation-time behavior instead of deriving motion timing from decoder throughput.

`Frame` also supports `metadata`, `received_monotonic_s`, `hardware_timestamp_s`, and optional `depth_m`. An aligned depth image uses meters and the color image's pixel grid.

**Host arrival times and freshness deadlines use `gazespace.clock.host_now_s()`, backed by `time.perf_counter()`.** Its reported domain is `gazespace.perf_counter`; the epoch is unspecified. Use this same function in custom acquisition callbacks and UI freshness checks. On Windows Python 3.11/3.12, `time.monotonic()` has a different, coarser clock implementation; mixing its absolute values with `perf_counter()` breaks age checks and can duplicate rapid camera timestamps. Python documents the Windows implementation change in its [3.13 time release notes](https://docs.python.org/3.13/whatsnew/3.13.html#time). The engine does not fabricate increments to make source timestamps appear distinct.

For a custom live source, timestamp and copy the owned buffer at acquisition:

```python
from gazespace.clock import HOST_CLOCK_DOMAIN, host_now_s
from gazespace.capture import to_bgr
from gazespace.types import Frame

raw = existing_camera.read()
received = host_now_s()
frame = Frame(
    image=to_bgr(raw),
    timestamp_s=received,
    received_monotonic_s=received,
    metadata={"host_clock_domain": HOST_CLOCK_DOMAIN},
)
```

For a video or a device with its own source timeline, keep `timestamp_s` in that timeline and set `received_monotonic_s` separately with `host_now_s()`. Never compare video PTS, a hardware timestamp, UTC, or a remote process's source timestamp with the host freshness clock. `TrackingRuntime` rejects nonfinite, future, or explicitly mismatched receive clocks and clears active gaze when a frame expires. Source clock resets still require restarting/resetting the tracking sequence. Existing recordings retain their relative `session_time_s` replay timeline; no recorded timestamps are rewritten by this change.

One engine is stateful. Construct and use it on the same processing thread, process frames sequentially, and close it after that thread has stopped. MediaPipe state and One Euro filters must not be called concurrently. Pass UI updates through the GUI framework's thread-safe mechanism.

## 3. Profile compatibility is part of integration

Schema-2 profiles contain measured geometry, fitted models, feature versions, validation evidence and context. Model metadata can bind:

- Camera/source ID, backend and selected subject ID.
- Exposure/gain configuration and declared monochrome bit depth.
- Tracking mode, pupil polarity, selected ROI, feature method/version and face-model identity.
- Actual frame resolution and intrinsic-calibration fingerprint.
- Eye-origin method, such as generic face PnP or aligned-depth pupil midpoint.
- Display identity and calibrated pixel dimensions for screen mapping.

Supply the **actual current configuration**, not a copy of the saved model's expected values. The engine checks provided fields and frame-derived context. Low-level/headless callers may omit some external display/source information; omission is not a verified match. Hosts requiring strict acceptance should call `model.context_mismatches(actual_context, require_present=True)` after assembling every applicable field and reject unresolved differences before consuming output.

Version-1 learned models are invalidated when loaded because head and eye-feature conventions changed. Do not attempt to repair old model coefficients by renaming their version fields.

### In-process example

```python
from gazespace.engine import GazeEngine

# Obtain these values from the host's current settings/device/display.
current_context = {
    "camera_id": "0",
    "backend": "dshow",
    "subject_id": "operator-1",
    "exposure_native": None,
    "exposure_us": None,
    "gain": None,
    "ir_bit_depth": 8,
    "monitor": actual_monitor_name,
    "screen_size": [actual_pixel_width, actual_pixel_height],
}

# Construct this inside your processing worker, not the GUI constructor.
engine = GazeEngine.from_profile(
    "profiles/operator-camera.json",
    mode="rgb",
    source_context=current_context,
)
try:
    for frame_bgr, captured_monotonic_s in existing_camera_frames():
        result = engine.process(frame_bgr, captured_monotonic_s)
        if result.screen_valid and result.gaze_screen_px is not None:
            publish_to_gui(result.as_dict())
        elif result.spatial_valid and result.surface_name is not None:
            publish_to_gui(result.as_dict())
        else:
            clear_host_gaze_selection(result.reason)
finally:
    engine.close()
```

The functions and display variables named `existing_*`, `actual_*` and `publish_*` are the integration points in the host application. Do not treat them as GazeSpace APIs.

## 4. Runtime-managed acquisition

```python
from gazespace.capture import create_source
from gazespace.engine import GazeEngine
from gazespace.runtime import TrackingRuntime

runtime = TrackingRuntime(
    source_factory=lambda: create_source(0, backend="dshow", width=1280, height=720, fps=30),
    engine_factory=lambda: GazeEngine.from_profile(
        "profiles/operator-camera.json", mode="rgb", source_context=current_context
    ),
    max_age_s=0.4,
    reconnect_attempts=5,
)
runtime.start()
```

The source is created in the acquisition worker; the engine is created in the inference worker. A bounded newest-frame mailbox drops obsolete pending frames instead of building an unbounded latency backlog. The UI can poll:

```python
snapshot = runtime.snapshot()           # local Frame/Observation/Result plus metrics
telemetry = runtime.telemetry()         # JSON-safe metadata representation
runtime.pause()
runtime.resume()
fully_stopped = runtime.stop(timeout=3)
```

When `stop()` returns `False`, a driver/OS call is still active. Output is invalidated, and the runtime must not be replaced by another instance opening the same device until shutdown completes. The application does not attempt unsafe cross-thread destruction of a camera currently in use.

`on_result(frame, observation, result)` can record or forward each processed frame exactly once. Keep the callback short; it executes in the processing worker. Callback errors are exposed in runtime metrics. Metadata recording avoids raw images unless separately selected.

## 5. Result semantics

Important `GazeResult` fields:

| Field | Meaning |
|---|---|
| `valid` | At least one usable calibrated estimate exists for this observation |
| `screen_valid`, `spatial_valid` | Validity of each output path separately |
| `reason`, `warnings` | Explicit tracking, calibration, envelope or geometry conditions |
| `quality` | Heuristic tracking quality, not a calibrated probability of accuracy |
| `gaze_screen_px` | Filtered local coordinates on the calibrated display |
| `raw_gaze_screen_px` | Unfiltered model prediction when available |
| `gaze_screen_normalized` | Screen XY normalized to `[0,1]` |
| `ray_origin_world_m` | Eye-origin estimate in world meters |
| `ray_direction_world` | Unit gaze direction in the same world frame |
| `surface_name`, `surface_xy_m` | Nearest bounded hit and its local metric UV |
| `surface_xy_px` | Optional surface UV mapped to declared display pixels |
| `eye_origin_source` | Explicit source of metric eye-position geometry |
| `processing_ms` | Processing duration for this frame |
| `capture_timestamp_s` | Source timestamp used by the temporal pipeline |
| `model_fingerprint`, `tracking_context` | Provenance/compatibility data |

A spatial ray may be valid without hitting any configured surface. `spatial_ray_only_no_surface_hit` must not activate a surface selection. Clear prior selections when validity is lost; do not keep acting on the last successful point.

## 6. Coordinate conventions

- **Camera:** X right, Y down, Z forward; distances in meters.
- **World:** the measured reference-board frame. Extrinsics satisfy `point_camera = R_world_to_camera @ point_world + t_world_to_camera_m`.
- **Head:** the corrected camera-facing generic template has a neutral frontal orientation near zero rather than requiring a 180-degree pitch offset. Its anthropometric scale remains an approximation.
- **Screen:** top-left origin, X right, Y down; normalized 1 maps to `width−1` or `height−1`. Coordinates are local to the calibrated monitor, not automatically global Windows virtual-desktop coordinates.
- **Surface:** top-left metric UV relative to orthogonal U/V axes. The world-space origin, dimensions and optional pixel extent must be physically measured.

For Windows global cursor coordinates, the host would need to apply the actual monitor origin/DPI convention. GazeSpace does not move the system cursor or trigger clicks by default.

## 7. Local REST and WebSocket API

The desktop generates a fresh bearer token when its API starts and shares the running runtime. Bind address is `127.0.0.1`; choose the port in **Integration API**.

The CLI can launch its own service:

```powershell
$token = python -c "import secrets; print(secrets.token_urlsafe(32))"
python -m gazespace api --backend dshow --source 0 --mode rgb --profile profiles\operator.json --subject-id operator-1 --token $token --port 8765
```

For CLI operation, use a compatible CLI profile or supply an embedding context that covers the intended display/controls. `--token` is optional at the low-level CLI; use it when local consumers require authenticated access.

| Endpoint | Response |
|---|---|
| `GET /health` | Runtime state, running/error status |
| `GET /v1/gaze` | State, latest estimate, metrics and camera report |
| `GET /v1/metrics` | Runtime/engine metrics |
| `WS /v1/stream` | Periodic JSON telemetry updates |

Every HTTP request must use a loopback Host header. Browser origins must be localhost/loopback; external origins are rejected. Native clients normally omit the Origin header. The API does not provide an internet/LAN deployment mode.

Python client:

```python
import httpx
response = httpx.get(
    "http://127.0.0.1:8765/v1/gaze",
    headers={"Authorization": f"Bearer {token}"},
    timeout=2,
)
response.raise_for_status()
status = response.json()
latest = status.get("latest")
if status.get("state") == "running" and latest and latest.get("screen_valid"):
    consume_screen_point(latest["gaze_screen_px"])
else:
    clear_gaze()
```

Browser WebSocket from a permitted local origin:

```javascript
const socket = new WebSocket("ws://127.0.0.1:8765/v1/stream", [
  "gazespace", "bearer." + token
]);
socket.onmessage = event => {
  const status = JSON.parse(event.data);
  const gaze = status.latest;
  if (status.state === "running" && gaze?.screen_valid) {
    renderGaze(gaze.gaze_screen_px);
  } else {
    clearGaze();
  }
};
```

Stream updates may repeat the same capture observation; use the capture timestamp if you need exactly-once frame processing. No endpoint exposes raw image arrays, facial landmark arrays or model feature vectors. Output still contains sensitive behavioral coordinates and should be handled accordingly.

## 8. Depth and stereo integration

`RealSenseSource` produces color-aligned depth and its intrinsic metadata. `GazeEngine.process_frame` can use supported aligned depth to estimate a binocular visible-eye midpoint. Missing/invalid patches and unsupported distortion models are rejected. The origin is explicitly labeled `aligned_depth_pupil_midpoint`, which is not an optical eyeball center.

For custom depth adapters, preserve:

```python
frame.depth_m                         # HxW metric depth aligned to BGR color
frame.metadata["depth_aligned_to"]   # "color"
frame.metadata["depth_intrinsics"]   # measured/factory CameraIntrinsics.to_dict()
frame.metadata["depth_deprojection_supported"]  # truthful compatibility flag
```

`FrameSynchronizer(max_skew_s=.008, max_queue=6)` pairs left/right frames with bounded queues. Hardware timestamp comparison is allowed only when the adapter explicitly declares synchronized clocks. Host-receive pairing cannot prove simultaneous exposure.

`triangulate_points_checked(left_xy, right_xy, left_intr, right_intr, R, T)` reconstructs matched image points and reports validity, reprojection error and convergence angle. Supply corresponding unduplicated landmarks from both cameras; the utility does not solve eye-feature correspondence automatically. A metric pupil-surface point still requires gaze-direction calibration.

CLI triangulation input:

```json
{"left_px": [[320.0, 240.0]], "right_px": [[300.0, 240.0]]}
```

```powershell
python -m gazespace triangulate --left-profile profiles\left.json --right-profile profiles\right.json --rig profiles\stereo.json --observations matched_points.json --output reconstructed_points.json
```

## 9. Calibration and session extensions

`TargetCollector` is independent of GUI and camera ownership. It returns deep-copied settled observations, progress and explicit rejection reasons. Complete screen samples use normalized targets; spatial samples use measured world coordinates and current eye origin. New models should be fitted with `context=tracker.context(actual_image_size)` augmented by actual source/user/display settings and a distinct session ID.

`SessionRecorder`, `SessionReplay`, `analyze_session` and `export_report` can be used without the desktop. A recorder adds a monotonic session timeline separate from the original source timestamp, enabling replay after reconnects. Its default clock is `host_now_s()` and the saved timeline remains elapsed seconds from recording start. A custom injected recording clock remains supported; new manifests describe its clock provenance without changing the session schema or old-file replay. Metadata is validated before writing. Camera video requires explicit `record_video=True`.

Reports distinguish screen pixel error from dispersion, processing time from capture-to-display latency, and simulated evidence from physical measurements. Preserve those distinctions when forwarding data to clients.
