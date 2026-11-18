# GazeSpace Studio — delivery and verification history

**Current acceptance revision: 0.2.1.** See [ACCEPTANCE_STATUS.md](ACCEPTANCE_STATUS.md) for the current **225-test local and full Windows passes**, verified frozen UI/font rendering, independently verified downloaded packages and remaining physical/client work. [Windows run 44299541968](https://github.com/adnanPBI/gazespace-studio/actions/runs/44299541968) supersedes the earlier font-defective candidate. The **0.2.0** baseline below is preserved as historical evidence.

**18 November 2026 — version 0.2.0.** This release supplies the working Python application,
native desktop, SDK, CLI and local API. It extends the earlier GazeSpace source
into an integrated operator workflow. Test evidence below is from execution,
not a proposed test plan.

## Verified results

| Check | Executed result | Evidence |
|---|---|---|
| Automated software suite | **173 passed; 0 failures, 0 errors, 0 skipped** | `pytest.log`, `pytest.xml` |
| Isolated dependency installation | Passed without system packages; `pip check` clean | `isolated_install.json`, `isolated_installed_packages.json` |
| Actual MediaPipe inference | Face, **478 landmarks**, two iris observations, finite features | `mediapipe_inference_photo.json` |
| Actual Basler SDK emulator | Mono8, Mono12, BGR8Packed; control readback, timestamps, buffer lifetime, shutdown | `pypylon_sdk_emulator.json` |
| RealSense optional SDK | Installed/imported 2.59.2.11152; context enumeration blocked by container udev monitor | `realsense_sdk_smoke.json` |
| Deterministic end-to-end run | **240 generated camera frames**, detection, screen and 3D fitted models, recording and reports | `simulation/` |
| Native application walkthrough | Seven actual Qt pages captured; **284 camera frames recorded**; replay and exports succeeded | `desktop_verification.json`, `desktop_recording/`, `desktop_report/` |
| Authenticated real localhost HTTP | **HTTP 200**, current screen coordinates, 3D ray and bounded surface hit | `desktop_verification.json` |
| Native startup smoke | Actual Qt widgets initialize, paint and close | `studio_smoke.json` |
| Built Python distribution | Non-editable wheel installed; actual package imports, simulation and native startup passed | `python_distribution_verification.json` |
| Windows binary execution | Not run in this Linux environment | `packaging_source_review.json` |
| Physical/human gaze accuracy | Not measured | `WINDOWS_HARDWARE_ACCEPTANCE.md` |

The native preview images are captured from `QWidget.grab()`; the GIF is a
sequence of those actual running-app frames. Generated pupils, known virtual
eye origin and virtual surfaces are visibly labeled **simulation**. No
photorealistic or designed image is used as an application screenshot.

## Coverage of the requested brief

| Requirement | Delivered implementation | Physical acceptance still needed |
|---|---|---|
| Eyes, pupils and head pose | Facial/iris landmarks, dark/bright pupil ellipse segmentation, landmark-driven and manual eye ROIs, blink/quality rejection, corrected head axes | Real operators, eyewear, head positions, lens and IR imagery |
| Gaze estimation | Person-specific fitted screen mapping and calibrated spatial direction, operating-envelope rejection | Independent user targets and calibrated reference measurements |
| IR/global-shutter USB cameras | UVC/DirectShow/Media Foundation controls; Basler serial selection, pixel formats, exposure/gain and readback; RealSense optional adapter | Exact camera, driver, sensor shutter mode, USB timing and IR setup |
| Stable real-time tracking | Capture/processing workers, newest-frame handoff, bounded age, smoothing, pause, reconnect, valid-state clearing, lighting diagnostics | Sustained load and capture-to-display latency on target Windows PC |
| 3D spatial calibration | Intrinsics, fresh-view reprojection validation, world-board pose, measured target acquisition, eye-origin provenance, spatial fit | Measured workspace, actual camera geometry and human target fixation |
| Screen/spatial coordinates | Calibrated screen pixels, normalized values, world ray and nearest bounded surface in meters/pixels | Monitor origin/DPI and physical fixture validation |
| Jitter and drift | One Euro filtering, quality gates, RMS/fixation statistics, repeated-anchor drift checks and recalibration workflow | Long-duration measurements under agreed lighting/head-motion conditions |
| Existing application | SDK adapter, queue-worker example, headless commands, authenticated REST/WebSocket endpoints | Wire into the client's actual repository and UI lifecycle |
| Depth/multiple cameras | Aligned-depth eye-surface origin, factory intrinsics import, stereo calibration, timestamp pairing and checked triangulation | Real sensors, physical synchronization and eye correspondence |
| Added operator features | Profiles, live diagnostics, session recording, optional raw video, replay, heatmaps, AOIs, reports, printable calibration board, native help | Final operator acceptance |

The optional RealSense SDK was also installed and imported successfully.
Its device context could not initialize this container’s udev monitor, so
device enumeration and physical acquisition are explicitly unverified.

## What the evidence means

The still photograph verifies that the installed landmark model executes; it
has no gaze or head-angle ground truth. The Basler emulator verifies the SDK
integration, not a physical camera. The generated-pupil recordings verify the
software route from images to calibrated coordinates, sessions and exports.
They do not measure human gaze accuracy.

The head-pose template supplies approximate anatomy. The RealSense origin is a
visible eye-surface midpoint, not a recovered optical eyeball center. A 3D gaze
ray can intersect a measured surface; the software does not claim to infer an
unconstrained fixation distance or supply a complete pupil-center/corneal-
reflection optical model. Stereo utilities require matched points and an
appropriate synchronization setup.

No client repository or actual Windows camera rig was supplied. The SDK and
API integration paths are delivered and exercised. Windows setup/build/verify
scripts and CI configuration are supplied; an actual Windows binary and
hardware acceptance receipt must be produced on that environment. No unsigned
Linux artifact is presented as a Windows executable.

## Reproduce

From the extracted source folder, with 64-bit Python 3.11 or 3.12 installed:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1 -Demo
```

The simulation requires no camera. For developer verification:

```powershell
python scripts/verify_release.py --basler-emulator --output release_check
python scripts/capture_desktop_evidence.py --output desktop_review
```

Install the Basler extra before the first command, or omit
`--basler-emulator`. The application does not bundle camera drivers.

Windows packaging:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1 -Basler
```

See `README.md`, `docs/USER_GUIDE.md`, `docs/INTEGRATION.md` and
`docs/WINDOWS_HARDWARE_ACCEPTANCE.md` in the source archive for detailed steps.

## Environment and observed timings

Python 3.12.14; Linux; NumPy 1.26.4;
OpenCV 4.11.0.86;
MediaPipe 0.10.21;
PySide6 6.12.0.

During the recorded native simulation, capture was about
30.0 frames/s, processing throughput
29.5 frames/s, median processing
3.65 ms and p95 processing
10.53 ms. These numbers describe this
short synthetic run on the current host. They exclude sensor/exposure and
capture-to-display latency and are not a promise for RGB neural inference or
physical IR/depth hardware. Jitter/accuracy values in generated fixtures must
retain their synthetic label when reused.

## Package contents

The source archive contains application modules, tests, integration examples,
operator/engineering guides, Windows launch/build scripts, the CI definition,
Python distributions and compact verification receipts. The verification/demo
archive contains screenshots, the running-app GIF, complete logs, the
replayable synthetic sessions, offline reports and the measurements above.
