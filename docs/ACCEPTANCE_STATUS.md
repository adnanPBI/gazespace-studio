# Acceptance status — GazeSpace Studio 0.2.1

**Status snapshot: 2026-11-18 UTC.**

**The software correction gate is complete: 207 tests passed, with zero failures, errors or skips. Overall physical and client acceptance remains incomplete.** The private source upload and Windows CI are explicitly authorized; no upload or run has yet been confirmed in this snapshot. Physical devices were last observed offline and will be rechecked, and no human gaze-accuracy measurements or integration into the client's existing application have been recorded.

The machine-readable companion is [`acceptance_scope_status.json`](../release_evidence/acceptance_scope_status.json). Its `overall_acceptance_passed` is `null`: the missing measurements are pending rather than assumed passes or measured failures.

## Observed evidence

- The current source gate is [`acceptance_revision_pytest.xml`](../release_evidence/acceptance_revision_pytest.xml) and [its log](../release_evidence/acceptance_revision_pytest.log): **207 passed in 18.425 seconds**.
- The [acceptance-helper receipt](../release_evidence/acceptance_runner_verification.json) records six focused helper tests and a real Basler SDK-emulator capture of **60 frames**, with clean shutdown. Its physical-hardware flag is false.
- Basler resolution/FPS requests now reach supported camera controls and preserve actual, rejected or mismatched readbacks. The suite includes a real-emulator dimensions/rate-control check.
- Desktop drift reports now retain calibrated target error and elapsed time. Validation rejects exact training-array reuse and conflicting session/context records; incomplete evidence remains diagnostic. Neither path invents an accuracy acceptance pass.
- Earlier `0.2.0` reports, including the 173-test release receipt and prior wheel/native screenshots, remain historical evidence. They do not identify a final `0.2.1` Windows artifact.

## Windows execution is authorized and pending

The user explicitly authorized uploading the prepared GazeSpace Studio 0.2.1 source-tree payload to the private repository [adnanPBI/gazespace-studio](https://github.com/adnanPBI/gazespace-studio) and running its Windows CI workflow. The status is **`authorized_pending_run`**.

Automatic approval review previously rejected the upload because the payload and destination required explicit user authorization. That historical blocker is now **resolved by the user's explicit authorization**; no repeated permission request is needed for this approved scope.

The last observed repository state contained only its initial README at HEAD `7fb34ffed7d0743983f76998ad170fd82b0400b9`. No application source upload, Windows CI run or Windows pass has yet been confirmed in this status snapshot. The next action is to upload the authorized snapshot, start CI, and retain the actual commit, run and artifact evidence.

## Acceptance matrix

| Area | Current status | Evidence still required |
|---|---|---|
| Core, desktop, runtime, SDK/API and acceptance-helper regression checks | Verified in software | Run the same gate against the exact final packaged revision. |
| Preview, ROI selection, calibration controls, recording, replay and reports | Verified in software | Exercise the intended Windows display, DPI, monitor and operator workflow. |
| Calibration and validation evidence integrity | Verified in software | Supply fresh acquired target sessions and explicit acceptance criteria. |
| Basler resolution, FPS, pixel formats, controls and buffer lifecycle | Verified with SDK emulator | Repeat on the selected physical serial, lens, driver and trigger mode. |
| Bounded Windows inventory/camera evidence collection tools | Helpers verified; Windows pending | Execute the PowerShell collector on Windows and preserve the exact artifact hashes. |
| Windows CI, frozen CLI/Studio and optional SDK packaging | Authorized; upload/run pending | Upload the authorized source snapshot and start CI. After actual execution, retain the run URL, source commit SHA, logs, artifact hashes and frozen-runtime receipts. |
| Target Windows lifecycle, display/DPI and deployment behavior | Target Windows pending | Run the exact build on the client PC; observe startup/shutdown, camera exclusivity, unplug/replug, sleep/wake and intended monitors. |
| UVC, infrared, global-shutter and RealSense physical acquisition | Physical camera pending | Connect identified cameras and provide exact serials, firmware, optics, illumination, pixel formats and operating settings. Measure sustained delivery and verify physical shutter/trigger behavior; USB or SDK connectivity alone does not establish it. |
| Camera intrinsics, camera/world transform and measured workspace surfaces | Measured geometry pending | Supply measured board dimensions, fresh held-out images, final focus/resolution, world landmarks and measured surfaces. |
| Pupil localization and head-pose accuracy under operating conditions | Reference measurement pending | Collect labeled pupil centers and an independent pose reference across intended users, eyewear, lighting, distance and head motion. |
| Person-specific screen and spatial gaze accuracy | Human measurement pending | Acquire fresh person-specific screen targets and measured nonplanar workspace targets; retain model/profile identity, coverage, eye-origin source and errors. |
| Stationary precision and drift over the required use interval | Human measurement pending | Measure instructed stationary fixations before/after the agreed duration and operating changes; evaluate baseline error, jitter and drift separately. |
| Depth eye-surface origin and synchronized multi-camera geometry | Rig measurement pending | Use calibrated aligned depth or a measured stereo rig; verify correspondence and exposure synchronization independently. |
| Sensor exposure/host receipt to consumer/display latency | Timing measurement pending | Instrument named timestamp boundaries and clock synchronization, include queue/API/render delay, and collect p50/p95 over the agreed load and duration. |
| Integration into the existing client Windows application | Client integration pending | Provide the actual client repository/interface, dependency lock, deployment requirements and baseline; integrate and verify the exact client build. |
| Agreed pass/fail limits and optional publisher signing | Agreed criteria pending | Record metric units, percentiles, duration, users/conditions, target coverage and permitted invalid rates. Supply signing policy/identity if required; attach the resulting signature and exact artifact hashes. |

## Conditions for a truthful pass

Each measured acceptance result must identify the exact build/configuration, source provenance, tested person/condition where relevant, metric and unit, reference method, sample and target coverage, observation duration, and agreed threshold. A numeric result without its criterion remains a measurement; it does not become a customer acceptance pass.

The following boundaries apply to the available reports:

- **Timing:** processing milliseconds, host arrival gaps and receive-to-script age have different boundaries. Exposure-to-display latency requires synchronized sensor/host timing or independent instrumentation plus the consumer/render path.
- **Reliability:** settled calibration samples exclude rejected attempts. Report valid/invalid fractions against the full attempted run; runtime mailbox replacement counts do not measure sensor or USB/SDK frame loss.
- **Geometry:** generic facial anatomy gives an approximate eye origin; aligned depth gives a visible eye-surface midpoint. Perpendicular ray miss and measured surface intersections do not establish unconstrained fixation depth.
- **Provenance:** an emulator remains nonphysical even if an operator confirmation flag is supplied. A new session name alone does not establish fresh validation; collection metadata and nonreused observations must be preserved.
- **Drift:** baseline target error, stationary precision, repeated offset and elapsed interval are separate results. Zero drift between two wrong estimates does not establish accurate tracking.

## Next evidence to collect

1. Upload the explicitly authorized source snapshot to the [private GitHub repository](https://github.com/adnanPBI/gazespace-studio) and run Windows CI. Retain its actual run URL, source commit, logs, frozen CLI/Studio checks and artifact hashes; update the Windows build row only after observing its result.
2. Run the [Windows acceptance collector](WINDOWS_ACCEPTANCE_RUNNER.md) against the exact build on the intended PC and explicitly selected physical cameras. Preserve driver/device/display identity and control responses.
3. Record agreed limits before the physical measurement session. Collect measured board/world geometry, fresh person-specific targets and stationary/drift observations across the intended conditions, following [WINDOWS_HARDWARE_ACCEPTANCE.md](WINDOWS_HARDWARE_ACCEPTANCE.md).
4. Instrument the required end-to-end timing boundary and perform client-repository integration. Attach signature evidence if the deployment policy requires it.

Windows CI can establish Windows software/build behavior. The physical camera, person, measured workspace and client-application rows require their own observed evidence.
