## 0.2.2 candidate — awaiting its exact Windows release CI

The 0.2.1 Windows receipts are historical and do not certify 0.2.2. This candidate
changes generic head-pose assumptions and spatial training frames: create new
profiles and independent validation, rather than reusing 0.2.1 fits. Physical
IR/global-shutter USB hardware, clinical/human accuracy, sensor-to-display
latency, eye-safe IR illumination and original-client application integration
remain unverified. Software CI/synthetic/SDK emulator evidence must not be
represented as physical acceptance.

# Acceptance status — GazeSpace Studio 0.2.1

**Status snapshot: 2026-11-18 UTC.**

**Windows software build, frozen-runtime verification and bounded visual review are complete.** The corrected source passed 225 tests locally and 225 tests in the full SDK-enabled Windows gate, with no failures, errors or skips. The real Windows screenshots render readable text; the earlier font defect is resolved. Complete Studio and CLI archives passed runner verification, were downloaded, and then passed an independent check of both archive hashes and **all 7,674 contained files**. Software packaging and download integrity are complete.

Physical cameras, human gaze accuracy and integration into the client's existing application remain unmeasured. Overall physical/client acceptance remains `null`; those pending measurements are neither assumed passes nor measured failures. The machine-readable companion is `release_evidence/acceptance_scope_status.json`.

## Exact tested code and environment

- **Tested source commit:** [`52429351784c4d2a317261cca66cad92fc5d6923`](https://github.com/adnanPBI/gazespace-studio/commit/52429351784c4d2a317261cca66cad92fc5d6923).
- **Source tree:** `daa481f9b040332c714818dbd910c7f0bfb7dd4b`.
- **Successful CI:** [run 44299541968](https://github.com/adnanPBI/gazespace-studio/actions/runs/44299541968), [Windows build job 134786705094](https://github.com/adnanPBI/gazespace-studio/actions/runs/44299541968/job/134786705094).
- **Windows build environment:** Windows Server 2025, build 26100, Python 3.12.10; Qt used its offscreen platform.
- **Current compact evidence:** [`windows_ci_44299541968`](../release_evidence/windows_ci_44299541968/). Full raw archive inventories and logs remain in the run diagnostics.

A later documentation/evidence-only commit does not replace the tested code identity above. Hosted Server 2025 and offscreen rendering do not establish interactive monitor, DPI, camera-driver or lifecycle acceptance on the client's Windows 10/11 workstation.

## Executed verification

| Check | Observed result | Evidence |
|---|---|---|
| Fresh isolated Linux source suite | **225 passed; 0 failures/errors/skips; 27.787 s** | [JUnit](../release_evidence/pre_windows_font_fix_pytest.xml) |
| Isolated environment | Python 3.12.14, no inherited system packages, clean `pip check`, original bundled-font identities | [Environment](../release_evidence/restored_environment.json) |
| Full Windows source suite with SDKs | **225 passed; 0 failures/errors/skips; 30.963 s** | [Windows JUnit](../release_evidence/windows_ci_44299541968/windows_pytest.xml) |
| Four Linux/Windows Python 3.11/3.12 matrix jobs | **223 passed + 2 optional SDK skips each**, zero failures/errors | [Matrix counts](../release_evidence/windows_ci_44299541968/matrix_summary.json) |
| Source and frozen synthetic workflows | Each processed **60 generated frames with 60 valid outputs**, calibrated screen/spatial mapping and HTML/JSON/CSV exports | [Source summary](../release_evidence/windows_ci_44299541968/windows_simulation/simulation_summary.json), [frozen summary](../release_evidence/windows_ci_44299541968/windows_packaged_simulation/simulation_summary.json) |
| Frozen face/iris inference | **478 landmarks, two irises and finite features** in the reference photograph | [Inference receipt](../release_evidence/windows_ci_44299541968/windows_packaged_face_inference.json) |
| Source Basler SDK emulator | **Mono8, Mono12 and BGR8Packed; five frames each**; strict timestamps, control readback, owned buffers and shutdown passed | [SDK receipt](../release_evidence/windows_ci_44299541968/pypylon_sdk_emulator_windows.json) |
| Frozen Basler acquisition | **60 simulated 640×480 frames in 2.0122102 s**, zero read/clock errors and clean shutdown | [Capture receipt](../release_evidence/windows_ci_44299541968/windows_packaged_sdk_emulator.json) |
| RealSense SDK context/enumeration | Completed successfully; **zero physical devices** | [Frozen environment](../release_evidence/windows_ci_44299541968/windows_packaged_environment.json) |
| Frozen Studio automation | **Seven pages painted; 95 ASCII, 14 Unicode and 12 distinct nonblank glyph rasters**; bundled guides and clean shutdown verified | [Studio receipt](../release_evidence/windows_ci_44299541968/windows_studio_smoke.json) |
| Independent bounded visual review | Live workspace screenshot and font proof readable; missing-glyph boxes absent | [Review](../release_evidence/windows_ci_44299541968/visual_review.json), [actual screenshot](../release_evidence/windows_ci_44299541968/windows_studio_smoke.png), [font proof](../release_evidence/windows_ci_44299541968/windows_studio_smoke_font_probe.png) |
| Full PowerShell collectors | Combined archive and both separate archives executed inventory/environment/packaged checks; **no physical camera requested** | [Combined](../release_evidence/windows_ci_44299541968/windows_release_acceptance_combined_20261118_121058_965/acceptance_summary.json), [Studio](../release_evidence/windows_ci_44299541968/windows_release_acceptance_studio_20261118_121212_568/acceptance_summary.json), [CLI](../release_evidence/windows_ci_44299541968/windows_release_acceptance_cli_20261118_121318_071/acceptance_summary.json) |

Matrix durations were Ubuntu/Python 3.11 **18.216 s**, Ubuntu/3.12 **21.592 s**, Windows/3.11 **30.296 s**, Windows/3.12 **29.377 s**. The full Windows gate separately exercised the optional SDK paths without skips.

The source and frozen generated-pupil fixtures reported nine-target synthetic screen errors of approximately **13.94 px median and 17.50 px p95**. Those values describe an artificial fixture, not a human gaze measurement or customer accuracy pass. The reference photograph has no gaze/head-angle ground truth. SDK emulators remain nonphysical, and zero-device enumeration proves SDK initialization rather than RealSense acquisition.

The visual review inspected the actual Live workspace screenshot and font proof. The automation painted seven pages; this does not imply separate visual inspection of all seven pages or interactive physical-display acceptance.

## Verified Windows archive identity

The [build receipt](../release_evidence/windows_ci_44299541968/windows_build.json) records **unsigned developer builds**. Both complete delivery archives contain **3,837 files** and are strictly below **500 MiB (524,288,000 bytes)**. On the Windows runner and again after download, every decompressed file hash was verified and each archive's complete application folder matched the corresponding combined payload.

| Archive | Exact bytes | SHA-256 verified on runner and after download |
|---|---:|---|
| `GazeSpace-Studio-Windows-0.2.1-unsigned.zip` | 419,209,852 | `5d996562f5eaadd13e616c8be8c9e90e5112e41af9f26f6a384c4913cdc524b6` |
| `GazeSpace-CLI-Windows-0.2.1-unsigned.zip` | 419,231,377 | `38f27670f181ebeef081f96a55028b69f7e9f4d450ca7e7b583333096b1fc064` |

The corresponding [Studio identity](../release_evidence/windows_ci_44299541968/windows_release_acceptance_studio_20261118_121212_568/release_identity.json) and [CLI identity](../release_evidence/windows_ci_44299541968/windows_release_acceptance_cli_20261118_121318_071/release_identity.json) also identify the exact executables. These are independent onedir distributions: keep each executable with its entire `_internal` directory.

**Local delivery integrity: passed.** The [independent downloaded-payload receipt](../release_evidence/windows_ci_44299541968/downloaded_payload_verification_summary.json) confirms both exact archive byte counts/hashes and **3,837 files per archive, 7,674 hashes in total**. The complete payloads match the combined manifest, executable identities match all three collector records, and required fonts/license are present. This check verifies downloaded bytes; Windows execution is established separately by the successful frozen-runtime receipts. The larger combined ZIP itself was not downloaded, so no local hash claim is made for that archive.

The actual Windows-built wheel is **922,523 bytes**, SHA-256 `79d26e5979b895301ee7fed62ee1e743e86787f86a1d95f9a505ac8d249f14db`. [Wheel inspection](../release_evidence/windows_ci_44299541968/windows_wheel_resources.json) verified exact font binaries and complete license content; the license text differs from the Linux source only by Windows CRLF line endings. This resource inspection is separate from runtime execution.


## Remaining acceptance scope

| Area | Current status | Evidence still required |
|---|---|---|
| Core, desktop, runtime, SDK/API and calibration evidence integrity | Software verified locally and in Windows CI | Preserve exact tested commit, configuration and receipts. |
| Frozen Windows build, font rendering and complete downloaded archives | Software and download verification complete | Use these exact files for client acceptance; preserve their hashes. |
| Target Windows lifecycle and monitors | Client workstation pending | Check startup/shutdown, DPI, monitors, camera exclusivity, unplug/replug and sleep/wake on the intended Windows 10/11 PC. |
| Physical UVC/IR/global-shutter/depth acquisition | Physical camera pending | Connect identified serials/drivers/optics/illumination; verify controls, sustained acquisition and actual shutter/trigger behavior. |
| Intrinsics, world transform and surfaces | Measured geometry pending | Supply measured boards, fresh held-out images, final focus/resolution, world landmarks and workspace surfaces. |
| Pupil localization, head pose and personal gaze | Human/reference measurement pending | Collect independent labels/reference measurements and fresh targets across agreed people, eyewear, lighting, head motion and distances. |
| Stationary precision and drift | Human measurement pending | Measure baseline target error, fixation jitter and before/after drift over the agreed interval and conditions. |
| Depth/stereo geometry and synchronization | Rig measurement pending | Verify aligned depth or a measured stereo rig, feature correspondence and exposure synchronization. |
| End-to-end latency and reliability | Instrumented measurement pending | Measure named sensor/host/consumer/display boundaries, p50/p95, full-run validity and actual loss under agreed load. |
| Existing client application | Client repository pending | Supply the real repository/interfaces, dependency lock, deployment method and baseline; integrate and verify its actual build. |
| Customer pass/fail and publisher signing | Criteria/policy pending | Agree units, thresholds, percentiles, duration and conditions; supply signing identity if required. |

Each physical acceptance result must identify the build/configuration, person/condition where relevant, reference method, metric/unit, sample/target coverage, duration and agreed threshold. A numeric measurement alone does not establish an acceptance pass.

Processing duration, host arrival gaps and receive-to-script age are different timing boundaries; none automatically measures exposure-to-display latency. Runtime mailbox replacements are not sensor/USB frame-loss counts. Generic facial anatomy gives an approximate eye origin; aligned depth gives a visible eye-surface midpoint. A ray or bounded surface hit does not establish unconstrained fixation depth. New validation labels do not establish independent observations, and zero drift between two wrong estimates does not establish accurate gaze.

## History and physical access

The [earlier run 44284381626](https://github.com/adnanPBI/gazespace-studio/actions/runs/44284381626) passed automated gates but failed screenshot review. Its receipts remain explicitly historical; the corrected code and actual images in the current run resolve the observed defect. The 0.2.0 delivery baseline is preserved in [DELIVERY_STATUS.md](DELIVERY_STATUS.md).

Source upload and Windows CI were explicitly authorized and executed; the earlier upload-approval blocker is resolved. Physical remote access remains blocked by the provider's last observed response: **Desktop Commander monthly usage is exhausted and calls are paused**. Remote PCs were last listed offline, and current connectivity cannot be confirmed through that paused provider. No alternate owner-local remote capability is currently available. This does not block the completed hosted Windows software verification.

Run the [Windows acceptance collector](WINDOWS_ACCEPTANCE_RUNNER.md) against the exact release on the intended PC and explicitly selected physical cameras when access is available. Then collect the agreed geometry, human and timing measurements and integrate the client application following [WINDOWS_HARDWARE_ACCEPTANCE.md](WINDOWS_HARDWARE_ACCEPTANCE.md).
