"""Register bundled, licensed UI fonts and verify real glyph rasterization.

Qt's offscreen font database may have no usable system fonts on Windows.
Registering our own TrueType files also makes desktop layout independent of
which optional fonts happen to be installed on a target workstation.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontInfo, QImage, QPainter, QRawFont
from PySide6.QtWidgets import QApplication

from .theme import FONT_FAMILY

FONT_DIRECTORY = Path(__file__).resolve().parents[1] / "assets" / "fonts"
FONT_FILES = ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf")
REQUIRED_ASCII = "".join(chr(value) for value in range(32, 127))
REQUIRED_UNICODE = "°µ·×—…±≤≥→←↑↓✓"
RASTER_PROBES = "ABGWim018?@×"


def configure_app_fonts(app: QApplication | None = None) -> dict:
    """Load package assets before creating/styling any GazeSpace widgets."""
    app = app or QApplication.instance()
    if app is None:
        raise RuntimeError("Create QApplication before registering GazeSpace fonts")
    cached = getattr(app, "_gazespace_font_registration", None)
    if cached is not None:
        return deepcopy(cached)
    license_path = FONT_DIRECTORY / "LICENSE-DejaVu.txt"
    if not license_path.is_file():
        raise RuntimeError(f"The bundled font license is missing: {license_path}")
    loaded = []
    for name in FONT_FILES:
        path = FONT_DIRECTORY / name
        if not path.is_file():
            raise RuntimeError(f"The bundled UI font is missing: {path}")
        font_id = QFontDatabase.addApplicationFont(str(path))
        families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
        if font_id < 0 or FONT_FAMILY not in families:
            raise RuntimeError(f"Qt could not register bundled UI font {name}; platform={app.platformName()}")
        loaded.append({"file": name, "bytes": path.stat().st_size,
                       "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                       "font_id": font_id, "families": families})
    app.setFont(QFont(FONT_FAMILY, 10))
    registration = {"family": FONT_FAMILY, "fonts": loaded,
                    "license": license_path.name, "qt_platform": app.platformName()}
    app._gazespace_font_registration = registration
    return deepcopy(registration)


def _rasterize(character: str, font: QFont) -> QImage:
    image = QImage(64, 64, QImage.Format.Format_ARGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    try:
        painter.setPen(QColor("black"))
        painter.setFont(font)
        painter.drawText(6, 46, character)
    finally:
        painter.end()
    return image


def verify_font_rendering(font: QFont | None = None, *, output_png: str | Path | None = None) -> dict:
    """Fail on missing glyphs or identical tofu rasterization; save a Qt proof.

    This checks the resolved font used by a real widget, not only whether font
    files exist. Distinct visible letter/number shapes must actually rasterize.
    """
    app = QApplication.instance()
    registration = configure_app_fonts(app)
    selected = QFont(font if font is not None else app.font())
    resolved = QFontInfo(selected).family()
    raw = QRawFont.fromFont(selected)
    if resolved != FONT_FAMILY or not raw.isValid():
        raise RuntimeError(f"UI font did not resolve to {FONT_FAMILY}: {resolved!r}")
    required = REQUIRED_ASCII + REQUIRED_UNICODE
    glyphs = raw.glyphIndexesForString(required)
    missing = [f"U+{ord(char):04X}" for char, glyph in zip(required, glyphs) if not glyph]
    if len(glyphs) != len(required) or missing:
        raise RuntimeError("UI font is missing required glyphs: " + ", ".join(missing))
    probe_font = QFont(selected)
    probe_font.setPixelSize(32)
    hashes, ink_pixels, rasters = [], [], []
    for character in RASTER_PROBES:
        raster = _rasterize(character, probe_font)
        data = bytes(raster.constBits())
        ink = sum(data[index:index+4] != b"\xff\xff\xff\xff" for index in range(0, len(data), 4))
        if ink < 10:
            raise RuntimeError(f"UI glyph U+{ord(character):04X} rendered blank")
        hashes.append(hashlib.sha256(data).hexdigest())
        ink_pixels.append(ink)
        rasters.append(raster)
    distinct = len(set(hashes))
    if distinct != len(RASTER_PROBES):
        raise RuntimeError("UI glyphs rasterized as repeated/missing shapes instead of distinct characters")
    if output_png is not None:
        path = Path(output_png)
        path.parent.mkdir(parents=True, exist_ok=True)
        sheet = QImage(64 * len(rasters), 150, QImage.Format.Format_ARGB32)
        sheet.fill(QColor("white"))
        painter = QPainter(sheet)
        try:
            for index, raster in enumerate(rasters):
                painter.drawImage(index * 64, 0, raster)
            painter.setPen(QColor("black"))
            painter.setFont(QFont(FONT_FAMILY, 15))
            painter.drawText(12, 96, "GazeSpace Studio · Cameras × Tracking — 3D calibration")
            painter.drawText(12, 129, "ABCDEFGHIJKLMNOPQRSTUVWXYZ 0123456789 " + REQUIRED_UNICODE)
        finally:
            painter.end()
        if not sheet.save(str(path)):
            raise RuntimeError(f"Unable to save actual Qt font proof: {path}")
    return {"status": "passed", "requested_family": selected.family(),
            "resolved_family": resolved, "raw_font_family": raw.familyName(),
            "ascii_glyphs_checked": len(REQUIRED_ASCII),
            "unicode_glyphs_checked": len(REQUIRED_UNICODE), "missing_glyphs": [],
            "raster_probe_characters": RASTER_PROBES,
            "distinct_probe_rasters": distinct, "expected_probe_rasters": len(RASTER_PROBES),
            "probe_ink_pixels": ink_pixels, "probe_raster_sha256": hashes,
            "registration": registration,
            "raster_probe_png": Path(output_png).name if output_png is not None else None}
