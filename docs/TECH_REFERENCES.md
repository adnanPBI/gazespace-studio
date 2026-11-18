# Technical references and measurement interpretation

**GazeSpace Studio 0.2.1 · 18 November 2026**

The links below are primary project/vendor documentation and original research references. They explain the underlying APIs and geometry; they do not validate this application's real-user accuracy. Runtime results for this particular source version belong in its release evidence.

## Face, iris and pupil estimation

- [MediaPipe Face Landmarker](https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker?tab=Python) documents landmark inference and the Tasks model interface used by the optional supplied `.task` path.
- [MediaPipe Iris documentation](https://chuoling.github.io/mediapipe/solutions/iris.html), the project's legacy Google documentation, describes the refined facial/iris landmark representation and explicitly separates iris tracking from estimating a person's fixation. GazeSpace therefore adds person-specific calibration rather than treating a detected iris center as a ready-made gaze coordinate.
- [MediaPipe 0.10.21 release files](https://pypi.org/project/mediapipe/0.10.21/) identify the pinned compatibility version used in this release. This is a tested version choice, not a claim that the legacy Solutions API receives ongoing feature support. The optional Tasks path is available for a supplied model; changing model identity requires compatible calibration.

The repository's `vision.py` and `pupil.py` define the actual feature geometry and segmentation behavior. The reference-photo inference report is [mediapipe_inference_photo.json](../release_evidence/mediapipe_inference_photo.json). Its successful landmark detection says nothing about the photographed person's true gaze direction.

## Camera calibration and 3D geometry

[OpenCV 4.11 camera calibration and 3D reconstruction](https://docs.opencv.org/4.11.0/d9/d0c/group__calib3d.html) documents the projection, intrinsic/extrinsic calibration, pose and stereo operations used here. GazeSpace adopts the camera-frame relation

`point_camera = R_world_to_camera @ point_world + t_world_to_camera_m`.

World geometry is measured in meters. A profile's image dimensions must match its camera intrinsics. Screen coordinates span pixel centers from zero to `width−1` and `height−1`. A surface intersection is a geometric consequence of a predicted ray and measured plane; it is not independent evidence of where the person actually looked.

The project's `geometry.py`, `calibration.py`, `validation.py`, `depth.py` and `stereo.py` contain the implementation and rejection conditions. The target-acquisition and independent-validation workflows are described in [USER_GUIDE.md](USER_GUIDE.md).

## Industrial and depth cameras

- [Basler's official pypylon repository](https://github.com/basler/pypylon) describes the Python binding and SDK acquisition/control interface. GazeSpace's optional dependency bounds and tested version are specified in `pyproject.toml` and the release evidence.
- [Basler: Working with Images](https://docs.baslerweb.com/pypylon-working-with-images) explains SDK image handling. The GazeSpace adapter copies buffers before releasing a grab result so later SDK reuse does not mutate a delivered frame.
- [Basler: Camera Emulation](https://docs.baslerweb.com/camera-emulation) documents the official software camera transport used by the SDK verification script. The observed result is [pypylon_sdk_emulator.json](../release_evidence/pypylon_sdk_emulator.json). This is actual SDK execution with a virtual camera, and provides no measurement of physical USB transport, optics, shutter exposure or IR lighting.
- [RealSense SDK projection documentation](https://github.com/realsenseai/librealsense/wiki/Projection-in-RealSense-SDK-2.0) describes per-stream intrinsics, distortion, meter-based point coordinates, depth deprojection and alignment. GazeSpace only applies its deprojection path to the supported distortion models and aligned data; it rejects unsupported geometry.

A depth value at a pupil image location estimates a visible surface point. It is not automatically the eyeball's optical center. Triangulation likewise requires actual matched points, measured stereo geometry and a justified synchronization method.

## Temporal filtering and analytics

[Casiez, Roussel and Vogel's original One Euro Filter resource](https://gery.casiez.net/1euro/) describes the speed-dependent filtering approach. Its parameters trade jitter against lag; smoothing is not an accuracy guarantee. GazeSpace resets temporal state when observations become invalid and exposes unfiltered screen predictions separately where available.

The session analytics use explicit definitions:

- **Target error:** distance between predicted coordinates and an instructed known target. The target protocol still assumes the person fixated correctly.
- **Per-axis precision:** standard deviation of X or Y about the samples' centroid.
- **Radial RMS precision:** `sqrt(mean((x−mean(x))² + (y−mean(y))²))`. Standard deviation of radial distances is a different quantity and can misleadingly be zero for a noisy ring.
- **Session dispersion:** spread across an entire session, which includes intended gaze movements and is not stationary-fixation jitter.
- **Dwell:** elapsed time between contiguous valid observations satisfying the configured interval limit. Blinks, invalid samples and long gaps do not create dwell.
- **Drift:** displacement between independently repeated anchor observations, reported together with baseline target error and within-phase precision.
- **Processing latency:** execution time of processing a frame. Capture-to-display latency requires additional camera, transport, scheduling and output instrumentation.

The offline HTML report includes thresholds and provenance so these meanings remain attached to exported values. Neither an AOI dwell estimate nor a cluster classified by the software constitutes physiological ground truth.

## Desktop and distribution

[PyInstaller's operating model](https://pyinstaller.org/en/stable/operating-mode.html) explains why executable bundles are produced for the operating system and Python environment running the build. The project therefore supplies a Windows build workflow rather than claiming that Linux verification produced Windows executables. Its two onedir folders must be distributed with their bundled dependencies.

Source dependencies and any separately supplied model are obtained through their respective distribution channels. Retain applicable dependency/model licenses in the final client distribution and use the authorized publisher certificate if signing is required. See [INTEGRATION.md](INTEGRATION.md) for the actual host-facing contract and [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) for evidence provenance.
