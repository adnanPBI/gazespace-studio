# GazeSpace Studio 0.2.2 — verified software and pending client acceptance

**Software version:** 0.2.2
**Tested source commit:** 51eb728fc478bd1e0ed860eeb62b91cf8924dd13
**Merged main commit:** 4041a59ef556ea1b4b7450d7d14804db90c1a039
**Source trees:** identical Git tree c390762add5d798a19ebdf220da3218e9162e27c
**Successful Windows release CI:** https://github.com/adnanPBI/gazespace-studio/actions/runs/44690120014

## Software checks performed

- Four Python 3.11/3.12 Linux/Windows matrix jobs passed.
- Windows full release with optional Basler/RealSense SDK packages: **235 passed**;
  SDK/Basler emulator invoked in the dedicated release gate.
- Seven native Studio pages, bundled font glyphs, packaged detector inference,
  generated pupil/calibration/replay/report flow, and camera SDK emulator
  checks passed their automated gates.
- Python wheel, source distribution, and unsigned portable Windows Studio/CLI
  artifacts were produced by the exact release run.
- Separate Windows Studio ZIP: GazeSpace-Studio-Windows-0.2.2-unsigned.zip,
  419239150 bytes, 3839 files, SHA256
  a136ee97ff7b8c1b9ac83bdda5b9a8eeb976634213595185c448ab7b013d05c2.
- Separate CLI ZIP: GazeSpace-CLI-Windows-0.2.2-unsigned.zip,
  419262019 bytes, 3839 files, SHA256
  07a589c17dbfad5eb9b5cb3d8846b39734a291c5d51e9b86a22b40fa5dd446a6.
- These are the **inner distribution ZIPs**, not the GitHub artifact container
  ZIP hashes. Each app must be extracted with its complete _internal directory.
- Release artifacts and detailed receipts:
  https://github.com/adnanPBI/gazespace-studio/actions/runs/44690120014

## Not yet established

| Acceptance gate | Current status | Required external evidence |
| --- | --- | --- |
| Physical IR/global-shutter camera | Pending: Windows computers offline during remote access check; no actual camera connected | Camera make/model/serial/firmware, exact optical setup, driver controls, documented mode and physical soak under conditions |
| Human 2D screen gaze accuracy, jitter and drift | Pending: no consented participant ground-truth dataset | Person-specific fresh validation targets, p95 error, stationary RMS jitter, repeated-anchor interval/drift, client limits |
| Physical 3D gaze | Pending: no surveyed fixture or trials | Calibrated camera/world transform, surveyed targets, angular and metric miss measurements |
| Exposure-to-display latency | Pending: no synchronized physical trigger/photodiode measurements | External timing rig and aligned exposure-to-photon timestamps with p50/p95 and agreed budget |
| Publisher Authenticode signing | Pending: developer EXEs are NotSigned | Publisher-owned certificate/private key, approved thumbprint, timestamp server and post-sign verification |
| Original Windows host application | Pending: client's proprietary host code/executable was not provided | Actual host interface and build, lifecycle/invalid-state integration trace, UI/coordinate/driver validation and acceptance |
| Written customer pass/fail thresholds and approval | Pending | Owner-approved metric limits and final signed acceptance |

## Client acceptance implementation

- scripts/collect_windows_acceptance.ps1 and scripts/camera_acceptance.py:
  measured hardware capture/readback and Windows inventory with simulation
  flags separated from physical capture.
- scripts/evaluate_client_hil.py: evidence-driven screen error/jitter/drift,
  3D ray error and instrumented latency analysis. No arbitrary default
  numerical pass criteria.
- scripts/verify_client_signatures.ps1: fail-closed post-build Authenticode
  identity and executable SHA-256 receipt.
- scripts/verify_host_contract.py: original host UI fail-closed and recovery
  trace validator; no fabricated client-host assertion.
- docs/CLIENT_HIL_RUNBOOK.md: complete run order, data schemas, and instructions.
- examples/client_hil_manifest_TEMPLATE.json: non-passing placeholder manifest.

All receipt fields are assertions based on the data provided by operators and
instruments; a script cannot independently prove provenance or eye safety.
The scorer **always leaves customer_acceptance_passed null** and requires a
separate signed human acceptance decision even after measured thresholds pass.

**Overall status: software/emulator verified; hardware-in-the-loop,
instrumented human gaze/latency, signed publisher release, and original
client-host acceptance pending.** Do not market this as validated clinical
eye tracking or a completed physical IR camera integration.
