# GazeSpace Studio 0.2.1

**A working Python desktop workspace for pupil/iris tracking, calibrated gaze estimation, 3D workspace mapping, camera integration and local session analysis.** Release date: **18 November 2026**.

This version completes the operator workflows around the original SDK: a seven-section native desktop interface, threaded capture and inference, stable target acquisition, independent validation, recording and replay, heatmaps, diagnostics and an authenticated local API. The tracking engine can also run inside an existing Windows Python application.

Start with the built-in simulation:

```powershell
python -m gazespace desktop --demo
```

The simulation sends **generated pupil images through the real image-processing and calibration code**, including calibrated screen coordinates and 3D rays intersecting a virtual workspace surface. Its eye origin and workspace geometry are explicitly known virtual fixtures. It demonstrates software behavior; it is not evidence of human gaze accuracy or physical IR/global-shutter camera performance.

## Install and launch on Windows

Use **64-bit Python 3.12 or 3.11** on Windows 10/11. From the extracted project directory:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1 -Demo
```

The setup script creates a local virtual environment and installs the desktop, face-tracking, API and test dependencies. `scripts\run_windows.bat -Demo` is the double-click/command-prompt launcher for the same setup flow. Execution policy is scoped to the launched PowerShell process; no administrator access or global policy change is required. Optional camera packages:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1 -Basler -NoLaunch
# Add -Depth for the optional RealSense adapter.
```

Manual installation:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e '.[full,test]'
python -m gazespace doctor
python -m gazespace desktop --demo
```

For a real camera, launch `python -m gazespace desktop`, open **Cameras & tracking**, choose the device and tracking mode, and select **Apply & start**. Use **Calibration** to create a profile and collect fixation targets. See [USER_GUIDE.md](docs/USER_GUIDE.md) for the full sequence.

## What is included

| Area | Functional implementation |
|---|---|
| Eye and facial tracking | MediaPipe facial/iris landmarks, blink rejection, OpenCV head pose with corrected neutral axes, finite-feature and quality checks |
| IR pupil detection | Dark/bright pupil modes, ellipse segmentation, landmark-driven eye regions, manual ROI mode, explicit detection failures and contrast diagnostics |
| Camera acquisition | OpenCV UVC/DirectShow/Media Foundation, video input, Basler pypylon adapter and optional RealSense aligned depth; preview mode works without a tracking model |
| Camera controls | Requested and actual exposure/gain reporting, explicit unsupported-control results, Mono8/Mono12/Mono16 conversion and safe SDK buffer ownership |
| Runtime | Separate capture and processing workers, bounded newest-frame handoff, pause/resume, live-source reconnect, stale-output removal and clean lifecycle handling |
| Screen calibration | Selected display, 4×4 target grid, settling and stable dwell, robust fitting, training-envelope checks, independent nine-point validation and drift measurement |
| Physical 3D mapping | Measured camera intrinsics and world transform, known 3D target acquisition, learned camera-space gaze rays, bounded surface intersections and metric/pixel coordinates |
| Camera and stereo calibration | Printable checkerboard SVG, diverse-image intrinsic fitting, fresh-view reprojection validation, optional aligned-depth factory-intrinsics import, board pose, stereo transforms, checked triangulation and timestamp-pairing utilities |
| Value-added analytics | Local sessions, optional camera-video recording, replay/seek, AOI dwell, stable gaze clusters, RMS jitter, heatmaps and offline HTML/JSON/CSV reports |
| Integration | Direct Python SDK, headless CLI, REST and WebSocket telemetry with local host/origin checks and optional bearer authentication; desktop API generates a token |
| Delivery | Source, automated tests, reproducible simulation, evidence reports, Windows setup/build/verification scripts and release documentation |

The native interface contains **Live workspace; Cameras & tracking; Calibration; Spatial workspace; Sessions & insights; Profiles & diagnostics; Integration API**. These sections are connected to the underlying implementation rather than static mockups.

## Verification available in this package

The current acceptance revision is **0.2.1**. The revised source gate passed **207 tests**. Track Windows build execution and the remaining physical/client-specific evidence in [ACCEPTANCE_STATUS.md](docs/ACCEPTANCE_STATUS.md).

The release was exercised in a Linux environment with Python **3.12.14**, OpenCV **4.11.0**, MediaPipe **0.10.21** and PySide6 **6.12.0**. The exact final test result is supplied with the release evidence rather than hard-coded here.

- Automated tests cover the actual desktop lifecycle, stable calibration acquisition, profile compatibility, geometry, capture failures, API behavior, recording/replay and exports.
- The synthetic image pipeline exercises pupil detection and learned gaze mapping, with explicit simulated provenance.
- Actual MediaPipe inference detected **478 landmarks and both irises in a reference photograph**. That verifies inference integration, not live gaze accuracy. See [mediapipe_inference_photo.json](release_evidence/mediapipe_inference_photo.json).
- The real **pypylon 26.5.0** binding was exercised against Basler's official camera emulator in **Mono8, Mono12 and BGR8Packed**, including control readback, timestamps, copied buffers and shutdown. See [pypylon_sdk_emulator.json](release_evidence/pypylon_sdk_emulator.json). An SDK emulator does not exercise a USB camera, optics, IR illuminator or global-shutter sensor.

**Physical acceptance remains specific to the client environment.** No client repository, actual camera rig, measured workspace fixture or human gaze ground truth was supplied. Windows compilation/signing and real-device user acceptance must be performed on the intended machine. The delivered software supports those workflows; it does not claim certified eye-tracker performance. Details are in [WINDOWS_HARDWARE_ACCEPTANCE.md](docs/WINDOWS_HARDWARE_ACCEPTANCE.md).

## Reproducible end-to-end demonstration

```powershell
python -m gazespace simulate --output .\demo_output --seconds 8 --record-video
python -m gazespace replay --session .\demo_output\sessions\YOUR_SESSION_FOLDER
python -m gazespace report --session .\demo_output\sessions\YOUR_SESSION_FOLDER --output .\review_report
```

The session folder contains the recording manifest, gaze metadata, event log and optional AVI. The report is usable offline. Synthetic measurements must remain identified as synthetic in proposals, demonstrations and client evidence.

## Existing-application integration

Your current application's camera frames can be passed directly into `GazeEngine.process` or the depth-aware `process_frame`. No replacement GUI is required. Construct and use an engine on one processing thread and pass the actual capture context expected by its profile.

```python
from gazespace.engine import GazeEngine

engine = GazeEngine.from_profile(
    "profiles/operator-camera.json",
    mode="rgb",
    source_context={"camera_id": "0", "backend": "dshow", "subject_id": "operator-1"},
)
try:
    result = engine.process(frame_bgr, timestamp_s=captured_monotonic_s)
    if result.screen_valid and result.gaze_screen_px is not None:
        existing_ui.show_gaze(*result.gaze_screen_px)
    else:
        existing_ui.clear_gaze()
finally:
    engine.close()
```

The abbreviated context above must be extended to match a profile's calibrated display and control settings where those fields are bound. See [INTEGRATION.md](docs/INTEGRATION.md) for threading, complete context, output fields, stale handling, depth/stereo and authenticated HTTP/WebSocket examples.

## Profiles and upgrade behavior

Version 0.2 uses **profile schema 2 and feature version 2**. The neutral head axes and eye-feature conventions changed. Loading a schema-1 profile preserves compatible measured geometry but removes its old fitted gaze models and validation reports, and marks the need for recalibration. The application does not silently apply an incompatible old gaze model.

Profiles bind learned models to their applicable camera, subject, tracking mode, ROI, pupil polarity, frame size, feature version, geometry and display context. Changing those inputs requires recalibration or appropriate revalidation. Validation reports are invalidated when the calibration they describe changes.

## Windows developer builds

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1
# Optional: -Basler -Depth
```

The build workflow targets two **onedir** distributions:

- `dist\GazeSpaceStudio\GazeSpaceStudio.exe` — native desktop launcher.
- `dist\GazeSpaceCLI\GazeSpaceCLI.exe` — command-line application.

Distribute each complete directory, including its `_internal` dependencies. The script also creates a combined `GazeSpace-Windows-0.2.1-unsigned.zip` and Python wheel/source distributions under `dist\python_packages`. With an existing authorized code-signing certificate, the archive is labeled `signed` after signature verification. The build runs packaged CLI and native Studio smoke checks and writes its Windows receipt. A successful Linux source run is not a Windows build receipt.

## Documentation

- [USER_GUIDE.md](docs/USER_GUIDE.md) — operate every desktop workflow and CLI equivalent.
- [INTEGRATION.md](docs/INTEGRATION.md) — embed in an existing application or consume the local API.
- [ACCEPTANCE_STATUS.md](docs/ACCEPTANCE_STATUS.md) — observed acceptance results and outstanding measurements.
- [WINDOWS_ACCEPTANCE_RUNNER.md](docs/WINDOWS_ACCEPTANCE_RUNNER.md) — collect Windows, display, driver and camera-soak receipts.
- [WINDOWS_HARDWARE_ACCEPTANCE.md](docs/WINDOWS_HARDWARE_ACCEPTANCE.md) — exact real-device and client acceptance work.
- [RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md) — reproducible software verification and packaging gates.
- [CHANGELOG.md](docs/CHANGELOG.md) — changes from the earlier source reference.
- [TECH_REFERENCES.md](docs/TECH_REFERENCES.md) — official documentation and measurement interpretation.

## Practical limits

A pupil/iris location becomes a gaze estimate only after appropriate calibration. A generic facial model produces an approximate eye origin; aligned depth estimates the visible eye surface, not the optical eyeball center. Stereo triangulation reconstructs matched image points, not a person's fixation automatically. A complete pupil-center/corneal-reflection optical model, controlled emitter geometry and independent physical user trials are outside the current verified implementation.

All processing is local after dependencies and selected models are installed. Gaze recording is an explicit action, raw video is separately opt-in, and the local API publishes metadata rather than camera images. Existing host applications remain responsible for consent, access, retention and any actions triggered from gaze.
