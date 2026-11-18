# Acceptance status — GazeSpace Studio 0.2.1

**Status snapshot: 2026-11-18 UTC.**

**The corrected source passed a fresh 225-test gate with zero failures, errors or skips. The earlier Windows candidate passed automated build gates, but its actual desktop screenshot failed visual review because text rendered as missing-glyph boxes.** That artifact is not accepted for desktop handoff. Bundled licensed fonts and a real glyph-raster gate are implemented and locally verified. The corrected frozen Windows build and its screenshot still require verification.

Physical cameras, human gaze accuracy and integration into the client's existing application remain unmeasured. Overall physical/client acceptance remains `null`; pending measurements are neither assumed passes nor measured failures. The machine-readable companion is `release_evidence/acceptance_scope_status.json`.

## Current source evidence

The corrected source passed **225 tests, zero failures, errors or skips, in 27.787 seconds**, including the actual optional Basler SDK emulator. The fresh receipt is [`pre_windows_font_fix_pytest.xml`](../release_evidence/pre_windows_font_fix_pytest.xml). The [isolated-environment receipt](../release_evidence/restored_environment.json) records Python **3.12.14**, no inherited system packages, a successful `pip check`, and the actual identities of both bundled font files and their license.

The source gate covers desktop lifecycle, camera/runtime failures, calibration and context integrity, SDK/API integration, recording/replay and exports. The font gate checks **95 printable ASCII characters, 14 UI Unicode characters and 12 distinct nonblank raster shapes**, using the real styled widget's resolved font. Regressions reject missing glyphs and repeated boxes.

Earlier 222-test, 219-test, 207-test and 0.2.0 173-test baselines remain historical. None substitutes for the new frozen Windows verification after the font and packaging changes.

## Observed Windows build: automated pass, visual defect

[Run 44284381626](https://github.com/adnanPBI/gazespace-studio/actions/runs/44284381626), [Windows build job 134734614731](https://github.com/adnanPBI/gazespace-studio/actions/runs/44284381626/job/134734614731), tested source commit **`3d2d10035e755b4d3a18dedac6979a0210899cfe`**. The diagnostics artifact was downloaded again and SHA-256 verified. Compact receipts are available under [`windows_ci_44284381626`](../release_evidence/windows_ci_44284381626/); full logs remain attached to the run. Its [evidence index](../release_evidence/windows_ci_44284381626/README.md) explicitly records the failed screenshot review.

The actual environment was **Windows Server 2025, build 26100, Python 3.12.10**. The Qt checks used the **offscreen** platform. These are observed Windows software results, not client Windows 10/11 monitor or physical-camera acceptance.

| Executed check | Observed result | Receipt in the run diagnostics |
|---|---|---|
| Full SDK-enabled Windows source suite | **222 passed; 0 failed, errors or skips; 32.948 seconds** | `windows_pytest.xml` |
| Frozen CLI end-to-end workflow | Passed; **60 generated frames**, calibrated screen and spatial outputs, recording and HTML/JSON/CSV exports | `windows_packaged_verification.json`, `windows_packaged_simulation/simulation_summary.json` |
| Frozen face/iris inference | Passed; **478 landmarks, two irises, finite features** from a reference photograph | `windows_packaged_face_inference.json` |
| Basler SDK emulator | **Mono8, Mono12 and BGR8Packed** passed strict timestamp, control-readback, buffer-ownership and shutdown checks | `pypylon_sdk_emulator_windows.json` |
| Frozen Basler capture | **57 simulated frames, 640×480, 2.0146 seconds**, clean shutdown | `windows_packaged_sdk_emulator.json` |
| RealSense SDK context/enumeration | Completed successfully; **zero physical devices** | `windows_packaged_environment.json` |
| Frozen Studio automated smoke | Seven pages painted and closed; bundled guides present | `windows_studio_smoke.json` |
| Actual Studio screenshot review | **Failed: text rendered as missing-glyph boxes.** Automated page-paint success did not establish readable text. | `windows_studio_smoke.png` |
| Full PowerShell acceptance collector | Executed in **inventory-only mode**; no camera requested; environment/CLI/Studio steps completed | `windows_release_acceptance_20261118_103204_705/acceptance_summary.json` |

The reference photograph has no gaze or head-angle ground truth. SDK emulators are nonphysical fixtures; zero-device enumeration proves SDK initialization, not RealSense acquisition. The collector's execution does not turn an inventory-only run into camera acceptance. No receipt establishes live human gaze accuracy.

### Historical artifact identity and correction

The historical `windows_build.json` records an **unsigned** combined archive, `GazeSpace-Windows-0.2.1-unsigned.zip`, containing independent Studio and CLI onedir folders. Each executable requires its complete `_internal` directory. The runner's `release_identity.json` records **863,083,807 bytes** and SHA-256:

```text
DF748AC0870409F61C2AAB3E36440CD3C7D6FA9CFC7EDBF44973EAF0C5EF191D
```

This is a **runner-recorded hash**. A successful local download and rehash of the complete binary archive has not been confirmed. This archive is also the candidate with the observed font defect and is not the accepted desktop handoff artifact.

The correction bundles licensed DejaVu regular/bold fonts, registers them before UI construction, and requires actual distinct nonblank glyph rasterization in the frozen smoke gate. The new build also creates complete separate Studio and CLI ZIPs, each strictly below **500 MiB (524,288,000 bytes)**, and verifies every decompressed file hash against the corresponding combined payload. Actual new archive sizes and hashes remain pending execution. Record the new source commit, Windows run, screenshots and archive identities after verification. Keep the tested source commit separate from a subsequent documentation-only commit.

## Remaining acceptance scope

| Area | Current status | Evidence still required |
|---|---|---|
| Core, desktop, runtime, SDK/API and evidence integrity | Verified in the fresh 225-test source gate | Run the corrected exact candidate through frozen Windows verification. |
| Corrected frozen Studio font rendering and delivery | Windows verification pending | Require glyph proof, inspect the actual screenshot, preserve new run/source/artifact identity, and verify downloaded archive hashes. |
| Target Windows lifecycle and monitors | Client workstation pending | Run the exact distribution on the intended Windows 10/11 PC; check startup/shutdown, DPI, monitors, camera exclusivity, unplug/replug and sleep/wake. |
| Physical UVC/IR/global-shutter/depth acquisition | Physical camera pending | Connect identified serials, drivers, optics and illumination; verify controls, sustained acquisition and shutter/trigger behavior. SDK or USB connectivity alone does not establish global shutter. |
| Intrinsics, world transform and workspace surfaces | Measured geometry pending | Supply measured board dimensions, fresh held-out images, final focus/resolution, world landmarks and measured surfaces. |
| Pupil localization, head pose and person-specific gaze | Human/reference measurement pending | Collect independent labeled/reference measurements and fresh person-specific targets across agreed lighting, eyewear, head motion and distances. Retain coverage and eye-origin provenance. |
| Stationary precision and drift | Human measurement pending | Measure target error, stationary jitter and before/after drift over an agreed elapsed interval and operating conditions. |
| Depth/stereo eye geometry and synchronization | Rig measurement pending | Use calibrated aligned depth or a measured stereo rig; verify point correspondence and exposure synchronization independently. |
| End-to-end latency and reliability | Timing measurement pending | Instrument named sensor/host/consumer/display boundaries; measure p50/p95 and full-run valid/invalid rates under agreed load/duration. |
| Existing client application integration | Client repository pending | Supply the actual repository/interfaces, dependency lock, deployment requirements and baseline; integrate and verify its real build. |
| Customer pass/fail and optional publisher signing | Criteria pending | Agree metric units, thresholds, percentiles, duration and tested conditions. Supply signing identity/policy if required. The observed build is unsigned. |

## Access and authorization

Source upload and Windows CI were explicitly authorized and have executed. The earlier automatic approval rejection is resolved by that authorization and is retained only as history.

Physical remote access is blocked by the provider's last observed response: **Desktop Commander monthly usage is exhausted and calls are paused**. The last available device inventory showed the remote PCs offline; their current connectivity cannot be confirmed while the quota block applies. No repeated reconnect attempt can establish acceptance during that provider block. This is separate from the resolved source-upload authorization.

## Conditions for a truthful physical pass

Each acceptance result must identify the exact build/configuration, source provenance, person/condition where relevant, metric/unit, reference method, sample/target coverage, duration and agreed threshold. A numeric result without its criterion remains a measurement.

- **Timing:** processing time, host arrival gaps and receive-to-script age measure different intervals. They do not establish sensor exposure-to-display latency. Source PTS and hardware clocks must not be compared directly with the host `gazespace.perf_counter` domain.
- **Reliability:** settled calibration samples exclude rejected attempts. Report validity against the full attempted run. Runtime mailbox replacement counts do not measure sensor or USB/SDK loss.
- **Geometry:** generic anatomy gives an approximate eye origin; aligned depth gives a visible eye-surface midpoint. Perpendicular ray miss and measured surface intersections do not establish unconstrained fixation depth.
- **Validation:** a new session label alone does not establish independence. Preserve fresh acquisition metadata and nonreused observations, consistent context, target coverage and model identity.
- **Drift:** baseline target error, stationary precision, repeated offset and elapsed interval are separate results. Zero drift between two wrong estimates does not establish accurate gaze.

After corrected Windows verification and image review pass, run the [Windows collector](WINDOWS_ACCEPTANCE_RUNNER.md) against that exact release on the intended PC and explicitly selected physical cameras. Then collect the agreed human/geometry/timing measurements and integrate the actual client application following [WINDOWS_HARDWARE_ACCEPTANCE.md](WINDOWS_HARDWARE_ACCEPTANCE.md). Hosted software verification cannot complete those physical or client-specific rows.
