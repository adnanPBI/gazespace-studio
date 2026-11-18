# Release verification — GazeSpace Studio 0.2.1

**18 November 2026.** This checklist distinguishes source/runtime verification, observed Windows packaging, visual review and physical acceptance. [ACCEPTANCE_STATUS.md](ACCEPTANCE_STATUS.md) identifies the current candidate, exact results and remaining gates.

## Implemented and exercised software scope

- [x] Native seven-section Qt desktop workspace connected to the real processing components.
- [x] MediaPipe landmark inference installed and exercised on a reference photograph.
- [x] IR pupil detection, explicit blink/quality failures, corrected head-pose axes and finite-coordinate gates.
- [x] Stable target collection, screen/3D calibration, independent validation and repeated-fixation drift workflows.
- [x] Camera calibration, printable board, fresh-image validation and world-coordinate alignment.
- [x] Bounded ray/surface mapping, frame-pairing and checked stereo/depth geometry utilities.
- [x] Worker-owned camera and engine lifecycle, bounded latest-frame handoff, stale invalidation, pause/resume and reconnect behavior.
- [x] Opt-in local recordings, optional camera video, playback/seek, AOIs, heatmaps and offline report exports.
- [x] Schema-2 context binding, old-model invalidation and calibration-evidence invalidation on relevant changes.
- [x] Local REST/WebSocket telemetry and authentication/host/origin tests.
- [x] Real Basler SDK emulator verification for three pixel formats and requested camera controls.
- [x] Windows setup, verification, packaging and optional signing automation provided.
- [x] A Windows Server 2025 build executed the full SDK-enabled suite and frozen runtime checks; its first screenshot subsequently failed visual review.
- [x] Bundled-font correction verified in a fresh isolated source gate: **225 passed, zero failures/errors/skips, 27.787 seconds**; dependency check passed.
- [ ] Verify corrected fonts in the new frozen Windows build and inspect its actual desktop screenshot before accepting that artifact.

## Repeat source verification

To reproduce the complete cross-platform software gate in one installed
environment, including the actual face model and optional Basler SDK emulator:

```powershell
python scripts/verify_release.py --basler-emulator --output release_check
```

This command writes the test XML/log, dependency check, inference receipts,
recorded synthetic session, offline report and native UI smoke receipt. Install
the `basler` extra before selecting `--basler-emulator`; omit that switch to
verify the other software paths without the vendor SDK. Current acceptance
is recorded in [ACCEPTANCE_STATUS.md](ACCEPTANCE_STATUS.md);
`release_evidence/RELEASE_SUMMARY.md` describes the historical 0.2.0 baseline.

In the installed environment:

```powershell
python -m gazespace doctor
python -m pytest -q
python -m gazespace simulate --output .\release_check\simulation --seconds 8 --record-video
python -m gazespace desktop --demo
```

For a headless Qt check, set `QT_QPA_PLATFORM=offscreen` in that test process. This exercises the actual widget/worker code but does not replace visual verification on a real Windows display.

Basler emulator verification is a separate explicit opt-in:

```powershell
$env:PYLON_CAMEMU = '1'
python scripts\verify_pypylon_emulator.py --output release_check\pypylon_sdk_emulator.json
Remove-Item Env:\PYLON_CAMEMU
```

The environment variable selects Basler's official software emulation transport in a fresh process. The resulting evidence must retain `sdk_emulator_only=true` and `physical_hardware_tested=false`.

## Windows build and verification

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1 -NoLaunch
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify_windows.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1
```

Optional switches include `-PythonVersion 3.12`, `-Basler` and `-Depth` for setup/build, `-BaslerEmulator` for verification, and `-PackagedCli`/`-PackagedStudio` for smoke-testing existing distributions. The corrected Studio gate must verify the actual styled-widget font, printable ASCII and UI symbols, distinct nonblank glyph rasters, every native page, and clean shutdown. Preserve its receipt, font proof and application screenshot; inspect those images as well as automated results. The CLI path runs the synthetic detector/calibration/recording/report workflow. The scripts check subprocess exits before reporting success.

Expected package entrypoints:

- `dist\GazeSpaceStudio\GazeSpaceStudio.exe`
- `dist\GazeSpaceCLI\GazeSpaceCLI.exe`

These are onedir applications. Preserve the complete distribution folders and their `_internal` contents. The combined archive is `dist\GazeSpace-Windows-0.2.1-unsigned.zip` or its verified `-signed.zip` counterpart. Complete separate Studio and CLI ZIPs must each be strictly below **500 MiB (524,288,000 bytes)**; the build verifies decompressed-file hashes and equality to the corresponding combined payload. Retain their actual inventory and release-identity receipts. Wheel/source distributions are placed in `dist\python_packages`. Run packaged `doctor` and `simulate` checks against the exact distribution you will deliver. Use a real camera and the client's display layout for the final desktop smoke test.

Signing is optional and requires an existing authorized CurrentUser code-signing certificate selected through `-CertificateThumbprint`. An optional `-TimestampServer http://YOUR_TSA` controls Windows PowerShell Authenticode timestamping. The script verifies the resulting signature. Without those inputs the correct status is **unsigned developer build**, not signed release.

The supplied CI configuration defines Linux/Windows Python 3.11/3.12 test jobs and a Windows packaging job. [Run 44284381626](https://github.com/adnanPBI/gazespace-studio/actions/runs/44284381626) completed the full Windows build job for source commit `3d2d10035e755b4d3a18dedac6979a0210899cfe`: 222 tests with no skips, actual frozen CLI/face/Studio execution, three Basler emulator formats, zero-device RealSense enumeration and an inventory-only collector run. Compact receipts are available under [`windows_ci_44284381626`](../release_evidence/windows_ci_44284381626/). Seven painted pages were insufficient proof of readable text: the screenshot failed visual review, so that artifact is not the accepted desktop deliverable. Retain the new candidate's distinct-glyph proof and visually inspect the new frozen screenshot before recording success.

Keep the tested source commit separate from any later documentation-only commit. Runner-recorded package hashes do not by themselves establish a successful local download and rehash of those bytes.

## Not established by this source release

- [ ] Physical UVC/IR/global-shutter camera capture and verified USB behavior on the client's Windows PC.
- [ ] RealSense/depth device performance or a hardware-synchronized stereo eye-tracking rig.
- [ ] Illuminator/optics safety and camera-trigger timing acceptance.
- [ ] Human gaze accuracy across the agreed lighting, eyewear and head-motion range.
- [ ] Independent metric workspace accuracy, sustained jitter/drift and capture-to-display latency.
- [ ] Integration into the client's original Windows repository and full host-application acceptance.
- [ ] Exact-release startup, display/DPI and camera lifecycle acceptance on the intended Windows 10/11 workstation; hosted Windows Server 2025 checks are a different environment.
- [ ] Publisher signing if required by deployment policy, and client sign-off. The observed hosted build was an unsigned developer distribution.

## Handoff evidence to retain

Keep the exact source/archive hash, dependency versions, test report, camera/display configuration, profile/model fingerprints, provenance of demonstration data, Windows package hashes and any physical measurement reports. Store current source evidence separately from historical v0.1 logs to avoid quoting stale package or test results.
