# Windows and physical-hardware acceptance

**GazeSpace Studio 0.2.1 · 18 November 2026**

The software now includes complete operator workflows and reproducible integration tests. Physical performance remains a measured property of a specific user, camera, optics, lighting and workspace. The source release does not substitute synthetic or emulator results for those measurements.

## Evidence already available

| Evidence | What it establishes | What it does not establish |
|---|---|---|
| Automated core/runtime/desktop tests | Geometry, calibration logic, state transitions, compatibility, recording/replay and exports behave under tested inputs | Accuracy of a real user's gaze |
| Native Qt interface exercised with actual workers | Desktop controls and software paths execute; interface is functional | Windows driver behavior or real-monitor usability on the client's PC |
| MediaPipe inference on a reference photograph | Installed detector returns facial/iris landmarks and finite features on that image | Live eye-tracking precision, robustness across people or gaze ground truth |
| Real pypylon binding with official Basler emulator | Mono8/Mono12/BGR8Packed acquisition, controls, buffer ownership and lifecycle run through the real SDK | USB transport, infrared illumination, global shutter, optics or physical timing |
| Synthetic rendered-pupil/calibration/workspace demonstrations | Complete image→features→model→coordinates→record/replay/report software relationships | Biological eye geometry or independently measured workspace accuracy |
| Depth/stereo geometric fixtures | Coordinate conversion, rejection and triangulation algorithms under known data | A deployed, synchronized multi-camera/RealSense rig |

Review the actual JSON reports in `release_evidence` and the final test results shipped with the release. Do not promote an emulator/source test into a hardware acceptance claim.

## Client inputs needed for final acceptance

1. The existing Windows project or a concrete integration interface, dependency lock and deployment method.
2. Exact camera model/serial/firmware, lens, resolution, pixel format, shutter/trigger mode and vendor SDK/driver versions.
3. IR emitter wavelength, operating distance, illumination layout and appropriate eye-safety evidence from the hardware supplier.
4. A flat measured calibration board, fixed camera mount, measured workspace surfaces and a nonplanar 3D fixation fixture.
5. Intended users, subject-consent process, eyewear conditions, permitted head-motion range and lighting range.
6. The actual Windows display configuration, DPI/scaling settings and any required multi-monitor behavior.
7. Agreed accuracy, precision, latency, FPS, invalid-frame and drift thresholds, stated with units and percentiles.
8. A code-signing identity/deployment policy if signed binaries or an installer are required.

## Acceptance sequence

### A. Environment and baseline

Record Windows edition/build, CPU/GPU, USB controller, device drivers, camera serial, lens/focus, exposure/gain and display settings. Run `doctor`, save diagnostics, and measure the current client's application before integrating changes.

Build and run the Windows distributions on the actual target. Test startup, shutdown, repeated open/close, camera exclusivity, permissions, sleep/wake and device unplug/replug. Do not forcefully release a camera from another thread while its driver call is blocked.

### B. Camera and optics

Capture at the required resolution/FPS for a sustained run. Record dropped frames, actual frame sizes, timestamps and control readbacks. Verify Mono/packed-format handling against the sensor documentation. Confirm the physical sensor's shutter mode; the application never derives global shutter from a camera being USB-connected.

Measure sharpness/pupil pixel diameter over the expected head distances. Test ambient light changes, eyewear reflections, partial eye occlusion, natural blinks and approved head motions. Image quality constraints may require different optics or a dedicated eye camera rather than a different regression model.

### C. Calibration geometry

Capture diverse training board views, preserving the final camera focus/resolution. Measure fresh held-out views with fixed intrinsics. Report both fitted and held-out reprojection errors and rejected views. Keep camera-world origin and board orientation explicit; verify known physical points after solving the transform.

For stereo, capture synchronized pairs and verify baseline/epipolar geometry using additional independent views. Measure inter-camera skew independently where exposure simultaneity matters. Host timestamps and SDK tick counters do not become synchronized simply because they exist.

### D. Gaze validation

Collect person-specific target data using the application. Test fresh targets/session data across the intended screen and workspace. Include different head poses and distances that represent actual use.

Record:

- Median, p95 and maximum screen pixel error; optionally convert to visual angle using measured viewing distance and display geometry.
- Angular spatial-ray error and physical surface miss measurements with explicit eye-origin assumptions.
- Valid/invalid frame fraction and rejection reasons.
- Stationary-fixation RMS jitter and per-axis deviation, separately from intended movement.
- Repeated-target drift before and after the agreed operating interval and movement conditions.
- Capture-to-consumer/display latency, using actual timestamp instrumentation; do not substitute only processing milliseconds.

A generic facial template estimates eye origin. Aligned depth estimates an eye surface. A true pupil-center/corneal-reflection model, calibrated illuminator geometry or independent eye-tracker reference may be required if the accuracy target exceeds the optical/model limits of this implementation.

### E. Existing-application integration and release

Verify the actual in-process or API integration against a known data contract. Reject stale/invalid output, prevent UI blocking, test stopped/faulted states, and validate coordinates against the application's monitor/world conventions. Review logs and optional recordings for data handling and retention.

Build the intended Windows artifacts, run their smoke tests, sign with the authorized publisher identity if required, verify signatures, and run the client acceptance tests against that exact build. Record executable hashes and configuration alongside the result.

## Measurement sheet

| Test | Record | Pass criterion |
|---|---|---|
| Camera throughput | Captured/processed FPS, drop rate, actual size, duration | Client-agreed operating range |
| Pupil detection | Human-labeled center error, valid fraction, lighting/head pose | Agreed image-space threshold |
| Head pose | Error against an independent pose reference | Agreed angular threshold |
| Intrinsics | Train and held-out reprojection RMS/p95 | Agreed pixel threshold |
| Screen gaze | Median/p95 pixels or degrees, target coverage, users | Agreed task-specific threshold |
| Spatial gaze | Angular and metric target/surface miss, eye-origin source | Agreed task-specific threshold |
| Jitter | Fixation radial RMS, per-axis SD, time span | Agreed stationary precision |
| Drift | Known-target baseline error and repeated offset over time | Agreed maximum drift |
| End-to-end latency | Exposure/receive→consumer/display timestamps, p50/p95 | Agreed interactive latency |
| Failure handling | Disconnect, reconnect, pause, stale data, blocked driver | No stale active selection; recover or explicit fault |
| Packaging | Windows tests, artifact hashes, signature status | Exact approved build verified |

Leave unmeasured fields blank or marked pending. Do not insert expected values as though they were observed.
