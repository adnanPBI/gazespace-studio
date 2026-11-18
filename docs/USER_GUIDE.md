# GazeSpace Studio — User guide

**Version 0.2.1 · 18 November 2026**

## 1. Launch and choose a workspace

Run `python -m gazespace desktop`. The Windows default workspace is `%LOCALAPPDATA%\GazeSpace`; it contains `profiles`, `board`, `sessions`, `reports` and saved desktop settings. To use a specific location:

```powershell
python -m gazespace desktop --workspace C:\GazeSpaceWork
```

Use `--profile C:\GazeSpaceWork\profiles\operator-camera.json` to load an existing profile. Profiles are tied to a particular configuration and person; a filename alone is not proof of compatibility.

Run `python -m gazespace desktop --demo` for the generated sensor demonstration. The simulation produces artificial pupil images and uses known virtual geometry. Its measurements remain marked as simulated. Select a real camera and subject for personal calibration; prerecorded video cannot establish a live person's fixation on newly displayed targets.

## 2. Live workspace

**Start camera**, **Pause**, **Resume** and **Stop** operate the background capture/inference workers. The live page shows:

- Camera preview with available pupils and facial points.
- Heuristic tracking quality, processing throughput and latency summaries.
- Head-pose angles where a usable facial pose is available.
- A screen-coordinate preview after compatible screen calibration.
- The predicted workspace ray and bounded surface hit after spatial calibration.

A valid estimate means the current calibrated pipeline produced an output that passed its gates. It does not mean an independent instrument has confirmed the person's actual fixation.

Pausing, disconnection, stale frames and end-of-file invalidate active gaze coordinates. Do not interpret a retained camera preview as a fresh gaze estimate. The status and reason fields identify why output is unavailable. Processing latency describes software processing time; it does not include every camera exposure, transport, display and application delay.

## 3. Cameras & tracking

Stop capture before **Scan USB and industrial cameras** to avoid opening a camera twice. Use the returned device index for OpenCV sources and a stable serial number for Basler/RealSense devices. Guided physical calibration requires an explicit Basler/RealSense serial; the aliases `0` and `first` are not accepted as stable device identities for that workflow.

| Adapter | Use |
|---|---|
| `dshow` | Windows DirectShow UVC/USB cameras |
| `msmf` | Windows Media Foundation; control behavior may differ from DirectShow |
| `auto` | OpenCV chooses a backend |
| `ffmpeg` | Recorded video inspection |
| `pylon` | Basler SDK cameras; install `.[basler]` and the matching vendor driver |
| `realsense` | Aligned color/depth capture; install `.[depth]` and compatible device support |
| `synthetic` | Explicit software sensor simulation |

Set requested resolution and FPS. Inspect the device report for actual values; a request can be unsupported. Exposure in the OpenCV path is in backend-specific native units, while Basler `ExposureTime` uses microseconds. Blank exposure/gain fields leave the camera's existing setting unchanged. Software reports control application and readback rather than promising that all USB cameras expose identical controls.

**Global shutter is a physical sensor feature.** Neither a USB connection nor an SDK adapter establishes that a particular device is operating with a global shutter. Record the camera model, firmware, shutter/trigger settings and measured behavior in the acceptance report.

### Tracking modes

- **`preview`:** Camera display and ROI/board setup without a face model or a tracking dependency. Preview never reports valid gaze and cannot collect physical gaze-calibration targets.
- **`rgb`:** MediaPipe face/iris landmarks plus head pose. The packaged compatibility path uses MediaPipe 0.10.21. A supplied FaceLandmarker `.task` file can use the alternate Tasks path.
- **`ir`:** Facial landmarks locate the eyes; actual dark/bright pupil segmentation refines the eye measurements. Failure to detect a required pupil produces an explicit unavailable result.
- **`ir-roi`:** Both eye regions are manually selected. This supports close-up/IR images without a usable facial landmark model. Static regions require constrained head movement and must be recalibrated after moving the camera or ROIs.

For manual regions, select **Start preview for ROI selection**, then **Select right eye** or **Select left eye**, and drag the rectangle on the preview. Coordinates are normalized `x,y,width,height`. Save/import ROI JSON if needed, select the intended tracking mode, then select **Apply & start**. The system does not silently switch from landmark features to manual-ROI features within an already calibrated session.

IR illuminator selection and safe operating conditions belong to the physical rig design. No emitter is controlled or certified by this software.

## 4. Create a profile

On **Calibration**, select **New profile** and provide the operator/subject ID in camera settings. The application stores the intended identity/configuration; it does not identify people biometrically.

Keep separate profiles when changing the user, camera, optics/focus, frame resolution, pupil polarity, feature method, manual ROIs or display geometry. Version-1 fitted gaze models are incompatible with version 2 and require new target acquisition.

## 5. Measure the camera

1. Choose checkerboard internal-corner counts and actual square size. The default 9×6 internal-corner pattern has 10×7 squares.
2. Use **Export printable checkerboard** to create an SVG. Print at 100%; measure the final physical squares. Do not rely on printer scaling settings alone.
3. At the final operational resolution/focus, collect diverse board views using **Capture checkerboard image**. Cover the image field with different tilts, positions and distances; 15–25 good views are a useful collection plan.
4. Select **Solve camera intrinsics**. Review detected/rejected views and reprojection error.
5. Collect a separate set of board images and select **Validate on fresh board images**. The validator keeps intrinsics fixed, rejects duplicate/reused training images when training fingerprints are available, and reports held-out reprojection errors.

The generated SVG is physically large at the default square size; use appropriate paper or change the square size before printing. A board image generated in software is useful for algorithm testing, not for measuring a real camera.

For an aligned RealSense frame, the factory-intrinsics import button can copy the frame's supported color-grid intrinsic calibration into the profile. This requires actual depth metadata at the current image resolution. A factory calibration is distinct from an independent held-out camera validation; inspect and verify it for the intended task. Preview mode also allows board capture before a tracking model has been configured.

CLI equivalents:

```powershell
python -m gazespace generate-board --output board.svg --cols 9 --rows 6 --square-mm 24
python -m gazespace capture-board --backend dshow --source 0 --width 1280 --height 720 --output capture\train --limit 25
python -m gazespace calibrate-camera --images capture\train --profile profiles\operator.json --cols 9 --rows 6 --square-mm 24
python -m gazespace validate-camera --images capture\heldout --profile profiles\operator.json --cols 9 --rows 6 --square-mm 24
```

## 6. Screen gaze calibration, validation and drift

Choose the intended **Calibration display**, start the camera and select **Calibrate screen gaze**. The full-screen workflow displays 16 targets. Follow each target and press the displayed collection control when ready.

The acquisition workflow requires a live tracking mode and rejects preview, arbitrary prerecorded video and the built-in simulation for personal calibration. The simulation uses its own explicitly fictional fixtures.

The collector waits for fixation to settle and requires a contiguous stable observation window. Blinks, missing features, low quality, timestamp gaps and excessive motion interrupt acquisition. A failed target can be retried; partial frames are not silently treated as completed target data.

After fitting, select **Independent 9-point validation**. This creates a fresh session and reports pixel errors against instructed targets. Review both median and p95 errors plus any observations outside the training envelope. The result depends on whether the person actually fixated the shown targets; it is not an independent laboratory gaze measurement.

Use **Repeated-fixation drift check** before and after a meaningful interval or natural movement. Return to the same center target for both phases. The report includes mean displacement and RMS/per-axis dispersion. It does not automatically add a bias correction from a single anchor. Recalibrate when baseline target error, drift or invalid-output rate exceeds the thresholds agreed for your application.

The CLI offers `calibrate-screen`, `validate-screen` and `drift-check`; its Tk workflow uses the primary display. A profile bound to a named desktop display may require the desktop validation path because the CLI cannot independently establish the same monitor identity.

## 7. Physical workspace calibration

### Establish the world frame

Fix the camera mount. Place the reference board in its measured workspace position, preserving its orientation. Use **Use current frame as world board** or **Solve world pose from image**.

World coordinates use meters. The first detected internal corner is the origin; X follows the board columns, Y its rows, and Z follows the right-hand rule. A symmetric checkerboard can be ambiguous after rotation: verify the intended corner and axes against physical reference measurements. Moving the board or camera invalidates that world alignment.

### Measure and collect 3D targets

Save a target template and replace **every** example coordinate with an actual measured fixation target. Use at least nine distinct targets covering two lateral dimensions and multiple camera-relative depths. For volumetric work, use a nonplanar arrangement rather than a tilted single plane.

```json
[
  {"id": "measured_target_A", "xyz_m": [0.10, 0.12, 0.00]},
  {"id": "measured_target_B", "xyz_m": [0.40, 0.32, 0.20]}
]
```

The two rows above illustrate the format only; they are not a sufficient or measured calibration set.

Choose **Calibrate spatial gaze** and follow the physical-target collection dialog. Each completed target is converted into a camera-space direction using the calibrated world transform and current eye-origin estimate. Then collect a separate set/session through **Independent spatial validation**. Report angular errors and available target-ray miss measurements together with their eye-origin assumptions.

An approximate eye origin from a generic face model limits metric accuracy. The optional depth route estimates visible eye surfaces; it does not identify the optical rotation center. A gaze ray does not, by itself, reveal the exact fixation distance along that ray.

### Define surfaces

On **Spatial workspace**, add/import surfaces with:

- Unique name.
- World origin at the chosen top-left corner, in meters.
- Orthogonal U and V direction vectors, pointing right and down on the surface.
- Measured width/height, in meters.
- Optional pixel width/height for monitor mapping.

Select **Save surfaces to profile**. The nearest valid bounded intersection produces surface UV meters and optional pixel coordinates. Parallel, behind-origin and out-of-bounds intersections are rejected. Pixel endpoints are bounded to `width−1` and `height−1`.

## 8. Depth and stereo extensions

**RealSense:** select the `realsense` adapter with the actual serial. Color and depth must be aligned; unsupported distortion models, missing depth or poor depth patches do not produce a fabricated metric origin. The adapter and deprojection paths are implemented, but physical device acceptance is still required.

**Stereo:** calibrate each camera independently. Capture physically synchronized image pairs with identical filenames and run **Stereo camera calibration**. The result contains the right-from-left transform, baseline and epipolar geometry.

`FrameSynchronizer` pairs timestamped frames within a configured skew bound, and `triangulate_points_checked` rejects poorly conditioned or inconsistent reconstructions. These are reusable SDK utilities. The desktop does not promise automatic dual-camera eye-feature correspondence or hardware-trigger setup. See [INTEGRATION.md](INTEGRATION.md).

## 9. Sessions, replay and reports

On **Live workspace**, select **Record session** to record gaze metadata. Check **Include camera video** only when raw local video is wanted. Stop recording before changing capture configuration.

A session contains `manifest.json`, `gaze.jsonl`, `events.jsonl`, and optionally `camera.avi`. The manifest records provenance, timing, configuration and which data was actually written. Video uses a review codec; use metadata timestamps for timing measurements.

On **Sessions & insights**, select a session or open its folder. Use Play/Pause and the slider to inspect recorded coordinates. Optional camera video follows the recorded frame index. Playback blanks estimates during long gaps instead of holding an old valid point through a pause.

Add AOIs as normalized rectangles and select **Recalculate**. Dwell requires contiguous valid observations; blinks, stale results and long gaps do not accrue time. Overlapping AOIs are counted independently. Stable-cluster jitter is radial RMS about the cluster centroid; overall session dispersion also includes intended gaze movement and must not be described as fixation jitter.

**Export report + CSV** writes an offline HTML report with an embedded SVG heatmap, JSON measurements and coordinate CSV. The report describes recorded model estimates, not independently established physiological gaze accuracy.

## 10. Diagnostics and local integration

**Profiles & diagnostics** shows saved calibration, runtime state, device information and an event log. **Export diagnostics** writes a local JSON report. It can contain source paths, subject IDs, camera configuration and calibration metadata; review those fields before sharing.

On **Integration API**, start tracking first, then **Start local API**. Copy the generated token into your local client. The service reuses the existing worker and does not acquire the same camera a second time. The service binds to loopback and exposes structured telemetry, not raw video. Authentication and request examples appear in [INTEGRATION.md](INTEGRATION.md).

## 11. Troubleshooting

| Symptom | What to check |
|---|---|
| Camera cannot open | Device index/serial, vendor driver, USB bandwidth, another application owning it, Windows camera permissions |
| Dark/washed-out IR image | Actual bit depth, exposure/gain readback, illumination, focus and pupil polarity |
| Pupils visible but no gaze | Missing or incompatible person-specific gaze calibration; inspect the output reason |
| Frequent blink/quality rejection | Eye crop size, optics/focus, iris visibility, reflections and head-motion envelope |
| Profile rejected | Same user, camera/backend, controls, ROI, display, frame resolution and feature version as calibration |
| Board is not detected | Internal-corner count, print quality, focus, board visibility and exposure |
| Good training error but poor validation | Overfitting, insufficient target coverage, changed geometry or incorrect fixations; inspect fresh targets |
| No surface hit | World transform, surface axes/dimensions, gaze ray direction, target coverage and origin assumptions |
| Stop remains pending | The OS/vendor driver may still be inside a blocking call; do not reopen the device concurrently |
| API returns 401/403 | Correct token, loopback Host header and local browser Origin; use the desktop's current generated token |
| Recorder fails | Available disk space, output permissions and optional video encoder; retain the diagnostic error |

For release and hardware measurements, follow [WINDOWS_HARDWARE_ACCEPTANCE.md](WINDOWS_HARDWARE_ACCEPTANCE.md) and [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md).
