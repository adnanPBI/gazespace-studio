# GazeSpace changelog

## 0.2.1 — 18 November 2026

### Acceptance and hardware corrections

- Pass requested width, height and frame rate through the Basler source and report actual GenICam readback or explicit rejection.
- Measure repeated-anchor drift against the requested screen anchor, preserving absolute baseline error, precision, duration and profile/session identity.
- Validate every acquired target's context and independent-session evidence; reject exact training-data reuse and inconsistent spatial reference directions.
- Report incomplete target coverage and acquisition evidence as diagnostic only. Leave acceptance decisions pending agreed criteria rather than inventing a passing threshold.
- Add a bounded Windows environment/camera acceptance collector, exact executable/archive hashes, driver/device/display inventory and privacy-preserving timing/quality statistics.
- Package and exercise the optional Basler and RealSense paths in Windows CI; distinguish SDK emulator, zero-device enumeration and physical-camera evidence.

The revised Linux source suite passed 207 tests. Actual Windows execution results are recorded separately in `ACCEPTANCE_STATUS.md` and downloadable CI artifacts. Physical eye/gaze measurements and client application integration require the real rig and client repository.

## 0.2.0 — 18 November 2026

This release turns the earlier SDK/source reference into an integrated desktop and headless application. The items below describe implemented software behavior. Camera-specific Windows acceptance, human gaze accuracy and integration into a separately supplied client repository remain separate measurements.

### Operator workflows

- Added the native PySide6 desktop with Live workspace, Cameras & tracking, Calibration, Spatial workspace, Sessions & insights, Profiles & diagnostics, and Integration API.
- Added working source selection, device scanning, exposure/gain reporting, preview without a face model, manual ROI selection/import/export and recording controls.
- Connected screen calibration, independent validation, repeated-anchor drift, checkerboard collection/fitting, world alignment, stereo calibration and spatial-target acquisition to the desktop.
- Added printable checkerboard SVG export, fresh-image intrinsic validation and supported aligned-depth factory-intrinsics import.
- Added editable measured surfaces, 3D ray visualization and bounded surface mapping.
- Added session browsing, timestamped replay/seek, optional video review, AOIs and offline HTML/JSON/CSV reports with embedded SVG heatmaps.

### Tracking, calibration and geometry

- Corrected the generic face template and head-pose conventions so neutral frontal pose does not require a 180-degree pitch correction.
- Added explicit eye-open, blink, pupil segmentation, finite-feature and quality gates, with failure reasons and image-quality diagnostics.
- Added stable target acquisition: settling, contiguous dwell, unique monotonic timestamps, blink rejection, motion gates, retries and target/session provenance. Both desktop and legacy CLI workflows use the reusable collector.
- Bound models to applicable feature, subject, camera, image-size, pupil/ROI, geometry and display context. Relevant changes invalidate dependent models or validation evidence.
- Added independent-session gaze validation and training-envelope diagnostics. Camera validation holds intrinsics fixed and rejects known reused training images.
- Added camera-relative spatial-target geometry checks, measured eye-origin compatibility and nearest bounded ray/surface intersections.
- Added aligned-depth eye-surface midpoint estimates, strict depth rejection, bounded timestamp pairing and checked stereo triangulation. These utilities do not supply automatic dual-camera eye correspondence or a full optical corneal-reflection model.
- Corrected screen/surface endpoint normalization to `width−1` and `height−1`.
- Corrected jitter reporting to per-axis and radial RMS about the centroid. Session movement dispersion, stable-cluster precision, target error and repeated-anchor drift are reported separately.

### Runtime and data handling

- Added separate capture/inference workers with bounded newest-frame handoff, stale invalidation, pause/resume, reconnect behavior and explicit blocked-shutdown status.
- Added control readback, explicit unsupported controls, monochrome bit-depth conversion, owned Basler image buffers and separate source/receive/hardware timestamps.
- Added opt-in session metadata recording, separately selected raw video, atomic session manifests, strict finite-data validation, replay gap invalidation and CSV formula protection.
- Added loopback REST/WebSocket services, bearer-token support, local Host/Origin checks and a generated token in the desktop API panel. The API shares the desktop runtime without opening a second camera.
- Added an explicit simulation engine using generated pupil images, learned screen mapping and calibrated 3D rays from a known virtual eye origin. The ordinary physical engine does not invent intrinsics or eye origins from those fixtures.

### Compatibility changes

**Profile schema 2, feature version 2 and model basis version 2 are intentionally incompatible with fitted version-1 models.** Loading an old profile preserves compatible measured geometry, removes obsolete learned gaze models/validation results and requires new person-specific acquisition. Do not relabel old coefficients to bypass migration.

Runtime consumers should check `screen_valid` and `spatial_valid` independently and clear output on stale/faulted states. A valid spatial ray need not hit a surface. Pixels are local to the calibrated monitor; Windows virtual-desktop origins/DPI remain a host-integration concern.

### Verification and delivery

The current environment exercised Python 3.12.14, OpenCV 4.11.0, MediaPipe 0.10.21 and PySide6 6.12.0. Actual MediaPipe inference returned facial/iris landmarks on a reference photograph. Actual pypylon 26.5.0 acquisition/control paths passed with Basler's official software emulator for Mono8, Mono12 and BGR8Packed. Both pieces of evidence retain their limited scope: neither establishes live human gaze performance or physical camera operation.

Windows setup, verification and build scripts now target separate Studio and CLI onedir applications, installed-wheel checking, packaged smoke checks and optional signing with an existing authorized certificate. A Windows build receipt is produced only by running that workflow successfully on Windows.

See [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) for repeatable verification and [WINDOWS_HARDWARE_ACCEPTANCE.md](WINDOWS_HARDWARE_ACCEPTANCE.md) for the remaining client-specific measurements. Exact final automated-test results are supplied with the release evidence.

## 0.1.0 — Earlier source reference

The earlier source package supplied the initial Python tracking/calibration components, command-line flows and synthetic QA artifacts. Historical logs and images describe that earlier environment only. They must not be substituted for the 0.2.0 dependency report, application behavior or release evidence.
