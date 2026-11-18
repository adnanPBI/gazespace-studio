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
| Delivery | Source, automated tests, reproducible simulation, evidence reports, Windows Studio/CLI packaging and verification, and release documentation; exact artifact acceptance is recorded separately |

The native interface contains **Live workspace; Cameras & tracking; Calibration; Spatial workspace; Sessions & insights; Profiles & diagnostics; Integration API**. These sections are connected to the underlying implementation rather than static mockups.

## Verification available in this package

**The corrected Windows software build and visual verification passed.** [Run 44299541968](https://github.com/adnanPBI/gazespace-studio/actions/runs/44299541968), [build job 134786705094](https://github.com/adnanPBI/gazespace-studio/actions/runs/44299541968/job/134786705094), tested source commit `52429351784c4d2a317261cca66cad92fc5d6923`. Current compact receipts are in [`windows_ci_44299541968`](release_evidence/windows_ci_44299541968/). The tested code commit is distinct from any later documentation-only update.

| Executed check | Result |
|---|---|
| Fresh isolated Linux suite | **225 passed**, no failures/errors/skips, **27.787 s**; dependency check clean. [JUnit](release_evidence/pre_windows_font_fix_pytest.xml), [environment](release_evidence/restored_environment.json). |
| Full Windows suite with optional SDKs | **225 passed**, no failures/errors/skips, **30.963 s**. [JUnit](release_evidence/windows_ci_44299541968/windows_pytest.xml). |
| Linux/Windows Python 3.11/3.12 matrix | All four jobs passed: **223 passed + 2 optional SDK skips each**. The separate full Windows gate above exercised those SDK paths. [Matrix counts](release_evidence/windows_ci_44299541968/matrix_summary.json). |
| Frozen Studio and font rendering | Seven pages painted; **95 ASCII, 14 Unicode and 12 distinct glyph rasters** passed. The actual Live workspace screenshot and font proof passed independent visual review. [Receipt](release_evidence/windows_ci_44299541968/windows_studio_smoke.json), [visual review](release_evidence/windows_ci_44299541968/visual_review.json). |
| Frozen detector/calibration/report pipeline | **60 generated frames, 60 valid outputs**, screen/spatial calibration and exports. Actual face inference found **478 landmarks and two irises** in a reference photograph. [Simulation](release_evidence/windows_ci_44299541968/windows_packaged_simulation/simulation_summary.json), [inference](release_evidence/windows_ci_44299541968/windows_packaged_face_inference.json). |
| Camera SDK integration | Real pypylon passed three emulator formats, five frames each; frozen acquisition received **60 simulated 640×480 frames**. RealSense enumeration succeeded with **zero devices**. [SDK checks](release_evidence/windows_ci_44299541968/pypylon_sdk_emulator_windows.json), [frozen capture](release_evidence/windows_ci_44299541968/windows_packaged_sdk_emulator.json), [environment](release_evidence/windows_ci_44299541968/windows_packaged_environment.json). |
| Windows distribution and collector | Complete Studio/CLI archives verified on the runner and after download: **7,674 file hashes** checked. Collector ran for combined and separate archives in **inventory-only mode**. [Build receipt](release_evidence/windows_ci_44299541968/windows_build.json), [download verification](release_evidence/windows_ci_44299541968/downloaded_payload_verification_summary.json). |

The Windows environment was **Windows Server 2025, build 26100, Python 3.12.10**, with Qt offscreen rendering. It does not establish the client's Windows 10/11 monitor, DPI or driver acceptance. The prior candidate's font defect is documented in the [historical evidence](release_evidence/windows_ci_44284381626/README.md); it was corrected before this successful run.

**Windows software verification is complete; physical/client measurements remain pending.** The photograph has no gaze or head-angle ground truth. Generated pupils and SDK emulators do not measure human accuracy, USB transport, IR illumination or global-shutter operation. No actual client repository, measured workspace or human reference session was supplied. Both complete Windows archives have been downloaded and independently hash-verified. Exact identities and physical/client acceptance boundaries are recorded in [ACCEPTANCE_STATUS.md](docs/ACCEPTANCE_STATUS.md) and [WINDOWS_HARDWARE_ACCEPTANCE.md](docs/WINDOWS_HARDWARE_ACCEPTANCE.md).

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
    result = engine.process(frame_bgr, timestamp_s=captured_host_timestamp_s)
    if result.screen_valid and result.gaze_screen_px is not None:
        existing_ui.show_gaze(*result.gaze_screen_px)
    else:
        existing_ui.clear_gaze()
finally:
    engine.close()
```

Capture `captured_host_timestamp_s` with `gazespace.clock.host_now_s()` at frame receipt. Keep video PTS and hardware timestamps separate from this host clock. The abbreviated context above must be extended to match a profile's calibrated display and control settings where those fields are bound. See [INTEGRATION.md](docs/INTEGRATION.md) for threading, complete context, output fields, stale handling, depth/stereo and authenticated HTTP/WebSocket examples.

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

Distribute each complete directory, including its `_internal` dependencies. The script creates a combined `GazeSpace-Windows-0.2.1-unsigned.zip` plus separate complete `GazeSpace-Studio-Windows-0.2.1-unsigned.zip` and `GazeSpace-CLI-Windows-0.2.1-unsigned.zip` archives. Each separate archive must be **strictly below 500 MiB (524,288,000 bytes)**. Verification hashes every decompressed file, confirms that each complete folder matches its payload in the combined archive, and checks the exact release identity. The successful Windows run verified **3,837 files per archive**: Studio **419,209,852 bytes**, CLI **419,231,377 bytes**. Matching runner and independently verified download hashes are recorded in [ACCEPTANCE_STATUS.md](docs/ACCEPTANCE_STATUS.md).

Python wheel/source distributions are placed under `dist\python_packages`. With an existing authorized code-signing certificate, archives are labeled `signed` after signature verification. The build runs packaged CLI and native Studio smoke checks and writes its Windows receipt. A successful Linux source run is not a Windows build receipt.

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
