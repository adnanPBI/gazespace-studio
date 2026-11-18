# Collect evidence on the actual Windows computer

The acceptance runner collects the active Windows environment, camera drivers, Qt monitor/DPI values, SDK device identities and exact release hashes. An optional camera soak measures the selected source for a bounded duration. It saves timing and aggregate image-quality statistics; **it does not save camera frames or video**.

Run it on the Windows computer that will use GazeSpace. The script's presence, a synthetic fixture, or an SDK emulator is not evidence that a physical Windows camera or a person's gaze has passed acceptance.

## Inventory without opening a camera

For a source checkout with the environment installed by `setup_windows.ps1`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\collect_windows_acceptance.ps1
```

This enumerates Windows devices and installed SDK devices without starting camera acquisition. It records the actual module imports and Qt display values. Missing optional SDKs, SDK context errors and unavailable inventory queries remain visible in their receipts.

The default source output is a new `release_evidence\windows_acceptance_<UTC time>` directory and a matching ZIP. Reusing a nonempty output directory is rejected so measurements from different runs remain separate. The script prints the evidence ZIP's SHA256.

## Measure the exact packaged release

The Windows distribution includes the collector in each application's `scripts` directory. The following command uses the frozen CLI for dependency/display inventory and capture, and runs the frozen Studio UI smoke. Python is not required when `-PackagedCli` is supplied.

From the directory containing the extracted application folders and original release ZIP:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\GazeSpaceCLI\scripts\collect_windows_acceptance.ps1 -PackagedCli .\GazeSpaceCLI\GazeSpaceCLI.exe -PackagedStudio .\GazeSpaceStudio\GazeSpaceStudio.exe -ReleaseArchive .\GazeSpace-Windows-0.2.1-unsigned.zip
```

Use the actual archive filename for a signed release. For packaged-only use, the default output is under `%LOCALAPPDATA%\GazeSpace\acceptance`. `-OutputDirectory` selects a different new/empty destination. Caller-supplied relative paths are resolved before the script changes working directory.

`release_identity.json` records archive and executable SHA256 hashes plus Authenticode status. An executable hash alone cannot identify its adjacent DLLs and data, so supply `-ReleaseArchive` when evaluating a distributed build. If source files are present, their hashes are recorded separately. A source receipt does not establish the behavior of another executable or an existing client application.

## Capture one selected physical camera

Inspect inventory first and match the device to its actual camera. DirectShow/MSMF indices do not encode a stable camera serial and may refer to virtual cameras. `-PhysicalDeviceConfirmed` records the operator's matching step; it never overrides synthetic or SDK-emulator metadata.

Example: a selected DirectShow camera at index 0, measured for 60 seconds:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\collect_windows_acceptance.ps1 -CameraBackend dshow -CameraSource 0 -Width 1280 -Height 720 -Fps 30 -SoakSeconds 60 -PhysicalDeviceConfirmed
```

The same camera parameters can be appended to the packaged-release command above.

For Basler, select the actual serial returned by SDK inventory:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\collect_windows_acceptance.ps1 -CameraBackend pylon -CameraSource YOUR_CAMERA_SERIAL -Width 1280 -Height 720 -Fps 30 -ExposureUs 3000 -SoakSeconds 60 -PhysicalDeviceConfirmed
```

For RealSense, use a supported resolution/FPS combination and its explicit serial:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\collect_windows_acceptance.ps1 -CameraBackend realsense -CameraSource YOUR_CAMERA_SERIAL -Width 640 -Height 480 -Fps 30 -SoakSeconds 60 -PhysicalDeviceConfirmed
```

Basler/RealSense aliases `0` and `first` are rejected for acceptance. The industrial-camera receipt checks the discovered serial against the selected serial. The Python helper rejects recorded files and network streams for UVC acceptance. The Windows collector never enables simulation implicitly.

### Use your agreed thresholds

By default, capture checks require at least two valid BGR frames, increasing finite frame timestamps, no capture exception, successful cleanup and zero empty reads. They do not invent a customer-specific FPS, jitter or gaze-accuracy requirement.

Add explicit criteria when the operating requirements are known:

```powershell
# Example values only: replace these with the project's agreed limits.
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\collect_windows_acceptance.ps1 -CameraBackend dshow -CameraSource 0 -SoakSeconds 60 -Width 1280 -Height 720 -Fps 30 -MinimumFps 27 -MaximumGapMs 150 -RequireExactSize -PhysicalDeviceConfirmed
```

`-RequiredControl Width,Height,ExposureTime` requires those exact reported GenICam controls to be applied and their readback confirmed. Use names from that adapter's receipt. Unsupported internal buffer-size controls are reported but do not automatically become a user-specified acceptance criterion. Optional `-ExposureNative`, `-ExposureUs`, `-Gain`, `-IrBitDepth`, `-PixelFormat` and `-Fourcc` are passed to adapters that support them; the receipt preserves the actual control responses.

For RealSense, capture checks also require nonempty aligned depth data. Unsupported depth-deprojection geometry or missing valid depth cannot produce a successful aligned-depth check.

## What each receipt means

| File | Contents |
|---|---|
| `acceptance_summary.json` | Actual Windows execution status, step outcomes and explicit unmeasured acceptance areas |
| `windows_hardware_inventory.json` | OS/build, CPU/GPU, USB controllers, camera PnP identities and installed driver versions/signature flags |
| `environment_probe.json` | Actual dependency imports, active Qt monitors/DPI/scaling, local/remote-session metadata, SDK enumeration |
| `release_identity.json` | Exact supplied archive/EXE hashes, EXE signature status and source manifest when available |
| `camera_soak.json` | Configuration, camera identity/control readbacks, checks, metrics and shutdown result |
| `camera_soak.frames.csv` | Per-delivered-frame host timing, shape, raw hardware timestamps when exposed, and scalar luminance/depth statistics |
| `camera_soak.partial.json` | Last worker checkpoint, including when a driver blocks before completion |
| `*.log` | Captured command/SDK diagnostic output |

`capture_checks_passed` means the stated capture checks passed. It does not mean that pupil localization, gaze coordinates, global shutter, stereo timing, personal calibration or integration with a client's application was accepted.

Host arrival gaps are delivery observations. Adapter sequence discontinuities are not sensor-frame loss. Blocking read duration and receive-to-script age are not exposure-to-display latency. Those unsupported quantities are stored as `null` rather than assigned inferred values. Bright/dark clipping and mean luminance are scalar observations, not a measurement of pupil-detection accuracy.

Monitor geometry and DPI describe the active Qt process and session. Remote/virtual displays and monitor-reported physical dimensions are not independent measurements of a physical workspace. Use the application's actual target dialogs on the intended display and verify their physical placement separately.

## Bounded acquisition and cleanup

The camera is created, read and closed by one worker process. The supervisor allows the requested acquisition duration plus 20 seconds for startup and 10 seconds for shutdown. If a driver remains blocked, the supervisor terminates only its own worker and writes a timeout receipt with `clean_shutdown=false`. The outer Windows collector also bounds each launched process and retains logs/checkpoints. It never labels forced termination as successful cleanup.

Acquisition duration is restricted to 1–300 seconds per invocation. Run separate, clearly named receipts for repeated trials or different conditions. The collector does not automate USB unplugging, sleep/wake, changes to IR emitters, focus, camera placement or a person's fixation targets.

## Direct helper commands

Source environment:

```powershell
.venv\Scripts\python.exe scripts\environment_acceptance.py --output environment.json --sdk-inventory all
.venv\Scripts\python.exe scripts\camera_acceptance.py --backend dshow --source 0 --seconds 30 --output camera.json
```

Frozen CLI:

```powershell
GazeSpaceCLI.exe environment-probe --output environment.json --sdk-inventory all
GazeSpaceCLI.exe camera-soak --backend dshow --source 0 --seconds 30 --output camera.json
```

These helper commands are dispatched by the packaged CLI entrypoint. They are not subcommands of `python -m gazespace` in this version. Normal application CLI commands continue through the regular application parser.

The Windows build's Basler option additionally runs a short camera soak through the frozen CLI against the **enumerated SDK emulator**. That validates packaged SDK loading, acquisition and cleanup while retaining nonphysical provenance.

The depth build option verifies RealSense SDK import/context creation and device enumeration in both source and frozen runtimes. A zero-device result is valid SDK-discovery evidence and is not a physical capture test. The supplied Windows CI build enables both `-Basler` and `-Depth`; builds without a particular switch explicitly exclude that optional SDK.

Human gaze accuracy, drift under actual use, measured 3D targets, IR optics and existing-client integration still require their real inputs and observations. Continue with [WINDOWS_HARDWARE_ACCEPTANCE.md](WINDOWS_HARDWARE_ACCEPTANCE.md) for those measurements.
