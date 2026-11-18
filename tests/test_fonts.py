"""Actual Qt glyph checks for the Windows/offscreen missing-font regression."""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QFont, QImage, QColor
from PySide6.QtWidgets import QApplication

from gazespace.ui import fonts
from gazespace.ui.main_window import MainWindow


def test_bundled_fonts_render_printable_ascii_and_real_styled_widgets(tmp_path):
    app = QApplication.instance() or QApplication([])
    registration = fonts.configure_app_fonts(app)
    assert {item["file"] for item in registration["fonts"]} == set(fonts.FONT_FILES)
    assert all(item["bytes"] > 100_000 for item in registration["fonts"])
    assert (fonts.FONT_DIRECTORY / registration["license"]).is_file()
    window = MainWindow(workspace=tmp_path / "workspace", interactive_errors=False)
    try:
        window.show()
        app.processEvents()
        report = fonts.verify_font_rendering(window.nav_buttons[0].font(), output_png=tmp_path / "actual_qt_glyphs.png")
        assert report["ascii_glyphs_checked"] == 95
        assert report["unicode_glyphs_checked"] == 14
        assert report["distinct_probe_rasters"] == report["expected_probe_rasters"] == 12
        assert not report["missing_glyphs"]
        assert (tmp_path / "actual_qt_glyphs.png").stat().st_size > 1000
        bold = fonts.verify_font_rendering(QFont(fonts.FONT_FAMILY, 13, QFont.Weight.Bold))
        assert bold["status"] == "passed"
    finally:
        window.close()
        app.processEvents()


def test_font_gate_rejects_missing_characters(monkeypatch):
    app = QApplication.instance() or QApplication([])
    fonts.configure_app_fonts(app)
    monkeypatch.setattr(fonts, "REQUIRED_UNICODE", "\U0010ffff")
    with pytest.raises(RuntimeError, match="missing required glyphs"):
        fonts.verify_font_rendering()


def test_font_gate_rejects_repeated_tofu_rasters(monkeypatch):
    app = QApplication.instance() or QApplication([])
    fonts.configure_app_fonts(app)
    def identical_box(*_):
        image = QImage(64, 64, QImage.Format.Format_ARGB32)
        image.fill(QColor("black"))
        return image
    monkeypatch.setattr(fonts, "_rasterize", identical_box)
    with pytest.raises(RuntimeError, match="repeated/missing shapes"):
        fonts.verify_font_rendering()
