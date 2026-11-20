# Client hardware-in-the-loop and Windows host acceptance

Software: **GazeSpace Studio 0.2.2**. Tested source commit:
51eb728fc478bd1e0ed860eeb62b91cf8924dd13.
Merged main commit: 4041a59ef556ea1b4b7450d7d14804db90c1a039.
Successful Windows CI run: https://github.com/adnanPBI/gazespace-studio/actions/runs/44690120014.

**Important:** This document is a procedure, not proof that physical tests happened.
The hosted Windows test passed 235 tests and built unsigned Studio and CLI apps.
There were no connected physical IR/global-shutter camera, surveyed target rig,
original client Windows app, human gaze trials, synchronized photodiode traces,
or publisher-owned signing certificate in the supplied evidence.

## 1. Prerequisites

- Actual client Windows 10/11 computer; manufacturer camera drivers, exact
  camera make/model/serial/firmware, lens and fixture; verified shutter/trigger
  mode, illumination settings and independent IR eye-safety assessment.
- Original client host application executable/source/interface, UI dispatch
  method, display/DPI mapping and one clear camera owner.
- Consenting participants; independent labeled fixation targets; fresh
  validation sessions different from training sessions; surveyed 3D targets.
- Agreed written thresholds, in named units, for participant/target coverage,
  valid fraction, screen p95 error, jitter RMS, drift and interval, spatial
  angular/metric p95 error and exposure-to-photon p95 latency.
- Approved publisher certificate/private key in Cert:\CurrentUser\My,
  explicit SHA-1 certificate thumbprint and timestamp server.
- Private local folder for raw human and hardware evidence; don't commit
  participant or camera recordings to source control.

## 2. Real camera acceptance

From the project root on the actual Windows machine:

~~~powershell
.\scripts\setup_windows.ps1 -PythonVersion 3.12 -Basler -Depth -NoLaunch
.\scripts\collect_windows_acceptance.ps1 -CameraBackend none -OutputDirectory .\client_evidence\inventory
~~~

That first collector is INVENTORY ONLY, not a physical camera test.

For a real Basler IR/global-shutter rig replace every placeholder with the
actual configuration and client-approved limits. The 1280x720/30 FPS values
below are requested settings only, not measured performance or acceptance gates.

~~~powershell
.\scripts\collect_windows_acceptance.ps1 -CameraBackend pylon -CameraSource "<REAL-SERIAL>" -PixelFormat Mono12 -IrBitDepth 12 -Width 1280 -Height 720 -Fps 30 -SoakSeconds 120 -MinimumFps <AGREED-FPS> -MaximumGapMs <AGREED-GAP-MS> -MaxReadFailures 0 -PhysicalDeviceConfirmed -OutputDirectory .\client_evidence\physical_camera
~~~

Verify shutter/trigger mode, IR safety, exposure/gain control readbacks, camera
exclusivity, reconnect, unplug/replug, sleep/wake, glasses, lighting and head
motion under the client-specified conditions. Check camera_soak and
acceptance_summary.json receipts; simulated-source flags must NOT be overridden
by an operator's PhysicalDeviceConfirmed declaration.

## 3. Independently measured screen gaze, jitter and drift

Calibrate each real participant with the final sensor and monitor. Collect new
held-out targets and label them independently of gaze prediction. Export CSV:

~~~csv
participant_id,validation_session_id,training_session_id,target_id,condition,time_s,target_x_px,target_y_px,gaze_x_px,gaze_y_px,valid,anchor_phase
~~~

Use valid=true or valid=false; leave gaze fields empty for invalid observations.
For repeated-anchor drift, collect the same physical target at separate times
with anchor_phase=initial and anchor_phase=repeat. Record viewing distance and
screen dimensions separately with your session metadata. The scoring tool
calculates per-person p95 screen error, target coverage, valid fraction, RMS
stationary jitter and repeated-anchor drift. It cannot confirm the person was
actually fixating on the instructed point.

## 4. Physical 3D gaze ray accuracy

Use the surveyed world frame and known target coordinates (meters). Record:

~~~csv
participant_id,validation_session_id,training_session_id,target_id,target_x_m,target_y_m,target_z_m,origin_x_m,origin_y_m,origin_z_m,direction_x,direction_y,direction_z
~~~

All vector/point components must share the same coordinate system. The tool
computes the closest-point miss (millimeters) and angular error (degrees).
A software surface hit does not prove physical fixation.

## 5. End-to-end latency with external instruments

Use independently calibrated/synchronized sensor trigger and photodiode or
oscilloscope measurement of actual exposure-to-screen-photon timing. Export:

~~~csv
trial_id,rig_id,exposure_reference_ns,display_photon_ns,clock_domain,synchronization_verified,measurement_method
~~~

Set measurement_method=trigger_photodiode and synchronization_verified=true
only after independently proving timestamp alignment. Software processing_ms,
SDK tick values in unknown epochs and host receive timestamps alone are not
exposure-to-photon latency.

## 6. Original client's host application

Follow docs/INTEGRATION.md and
examples/integrate_existing_windows_app.py. Choose either in-process
GazeEngine/TrackingRuntime or the authenticated localhost API. Never open one
camera concurrently in two applications. The actual original host must emit
newline-delimited JSON events for valid, blink, stale, disconnect, stop and
recovery. Example schema (THESE ARE EXAMPLES, NOT REAL TESTS):

~~~jsonl
{"case":"valid","host_event_time_s":1,"engine_valid":true,"host_gaze_visible":true,"host_screen_xy_px":[200,150]}
{"case":"blink","host_event_time_s":2,"engine_valid":false,"host_gaze_visible":false,"host_screen_xy_px":null}
{"case":"stale","host_event_time_s":3,"engine_valid":false,"host_gaze_visible":false,"host_screen_xy_px":null}
{"case":"disconnect","host_event_time_s":4,"engine_valid":false,"host_gaze_visible":false,"host_screen_xy_px":null}
{"case":"stop","host_event_time_s":5,"engine_valid":false,"host_gaze_visible":false,"host_screen_xy_px":null}
{"case":"recovery","host_event_time_s":6,"engine_valid":true,"host_gaze_visible":true,"host_screen_xy_px":[202,152]}
~~~

On the actual Windows host, validate real events and pin its executable hash:

~~~powershell
python .\scripts\verify_host_contract.py --events .\client_evidence\real_host_events.jsonl --host-executable "C:\ClientApp\ClientApp.exe" --physical-test-confirmed --output .\client_evidence\host_receipt.json
~~~

The verifier will reject a GazeSpace executable masquerading as the client's
original host. This check is not a substitute for inspecting client code and
performing independent manual UI acceptance.

## 7. Publisher signing on the intended Windows computer

The 0.2.2 GitHub Actions builds are UNSIGNED. Do not claim otherwise. An
approved publisher certificate with an accessible private key is required:

~~~powershell
.\scripts\build_windows.ps1 -PythonVersion 3.12 -Basler -Depth -CertificateThumbprint "<APPROVED-40-HEX-THUMBPRINT>" -TimestampServer "http://<APPROVED-TIMESTAMP-SERVER>"
.\scripts\verify_client_signatures.ps1 -StudioExe ".\dist\GazeSpaceStudio\GazeSpaceStudio.exe" -CliExe ".\dist\GazeSpaceCLI\GazeSpaceCLI.exe" -SignerThumbprint "<APPROVED-40-HEX-THUMBPRINT>" -RequireTimestamp -Output ".\client_evidence\signatures.json"
~~~

The signing verifier checks Authenticode status, the exact approved signer,
optional timestamp and executable SHA-256. It does not create a self-signed
certificate, import keys, or fake the publisher's identity.

## 8. Score evidence, then obtain customer sign-off

Copy examples/client_hil_manifest_TEMPLATE.json to a PRIVATE working folder
and fill in all required evidence paths, agreed thresholds, rig metadata and
release identity. Never submit synthetic fixtures as client measurements.

~~~powershell
python .\scripts\evaluate_client_hil.py --manifest .\client_evidence\client_hil_manifest.json --output .\client_evidence\client_hil_scored.json
~~~

Missing or failed evidence returns exit code 2. A complete numerical/receipt
pass is reported only as evidence_thresholds_passed_pending_customer_signoff;
the program always leaves customer_acceptance_passed=null. The client must
review and sign the actual deployment acceptance sheet.

Retain exact source SHA and Git tree, signed EXE hashes, device serials and
firmware, lighting and safety review, surveyed geometry, participant/session
and target IDs, reference technique, calibration and held-out trials, external
clock mapping, original host build hash, thresholds and signed approval.
The collected labels and operator declarations are not independently
authenticated by these tools, so inspect raw evidence before sign-off.
