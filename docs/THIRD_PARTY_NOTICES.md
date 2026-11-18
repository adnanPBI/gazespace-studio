# Third-party materials

GazeSpace's original application source is released under the accompanying MIT
license. Runtime dependencies and vendor drivers retain their own licenses.
Review the installed distributions' notices when redistributing a packaged app.

## Reference photograph

`examples/reference_face_nasa.png` is the NASA photograph of astronaut Eileen
Collins distributed as `skimage.data.astronaut`. The scikit-image documentation
identifies the NASA Great Images source and describes the image as released into
the public domain with no known copyright restrictions:

https://scikit-image.org/docs/stable/api/skimage.data.html#skimage.data.astronaut

The photograph is used solely to exercise actual face/iris inference. The person
is not an application user or endorser. There are no gaze labels, head-angle
labels or camera-calibration measurements for the image.

## Bundled UI fonts

The unmodified `DejaVuSans.ttf` and `DejaVuSans-Bold.ttf` files are copied from
Matplotlib's distributed DejaVu font assets. The complete Bitstream Vera and
Arev copyright and permission notices accompany them in
`src/gazespace/assets/fonts/LICENSE-DejaVu.txt`. Upstream font project:
https://dejavu-fonts.github.io/.

The wheel includes the fonts and their license as package data. The Windows
portable applications retain them under `_internal/gazespace/assets/fonts`.
Keep that license with the font files when redistributing the application.
These fonts retain their original family names and glyphs; no font design was
altered. Their license is separate from the original application's MIT license.

## Components

The application uses NumPy, SciPy, OpenCV, Pillow, MediaPipe and optional PySide6,
FastAPI/Uvicorn, Basler pypylon and Intel RealSense bindings. These components
are installed through their published Python distributions. Hardware drivers,
vendor SDK transport layers, optional model assets and code-signing certificates
are not replaced by this source release. See `TECH_REFERENCES.md` for primary
documentation. The final distributor should inventory and retain the licenses
and notices of every bundled dependency, including transitive packages and
vendor runtime components. PyInstaller hooks alone do not establish that this
inventory is complete for a particular packaged build.
