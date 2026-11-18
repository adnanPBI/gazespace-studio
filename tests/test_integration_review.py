"""Independent desktop lifecycle review using actual native widgets and workers.

The generated source is a software fixture, not measured human eye tracking.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
pytest.importorskip("PySide6", reason="native desktop review requires the desktop extra")
from PySide6.QtWidgets import QApplication

from gazespace.sessions import list_sessions, load_session
from gazespace.ui.main_window import MainWindow


def pump_until(app, condition, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return
        # QTest.qWait can retain Python's GIL around this manual event-pump
        # harness and starve QRunnable's Python callback. The production Qt
        # exec loop releases it; explicitly yield here to reproduce that.
        time.sleep(.01)
    raise AssertionError("Desktop worker did not reach the required state in time")


@pytest.fixture
def reviewed_window(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(workspace=tmp_path, interactive_errors=False)
    errors = []
    window.error_reported.connect(errors.append)
    window.show()
    app.processEvents()
    try:
        yield app, window, errors
    finally:
        window.stop_tracking()
        window.close()
        app.processEvents()
        window.pool.waitForDone(5000)


def test_desktop_demo_record_pause_replay_export_lifecycle(reviewed_window):
    app, window, errors = reviewed_window
    window.open_demo()
    pump_until(app, lambda: window.runtime is not None and
               window.snapshot().get("state") == "running" and
               bool((window.snapshot().get("latest") or {}).get("valid")))
    first = window.snapshot()
    assert first["latest"]["screen_valid"]
    assert first["latest"]["tracking_context"]["backend"] == "synthetic"
    assert first["latest"]["tracking_context"]["subject_id"] == "synthetic-fixture"
    assert window.runtime.set_synthetic_target(.25, .70)

    # Record actual detector/model results and optional MJPG frames through the
    # application's live callback, not manually constructed recorder rows.
    window.record_video.setChecked(True)
    window.toggle_record()
    assert window.recorder is not None
    session_path = Path(window.recorder.path)
    starting_frames = first["metrics"].get("processed_frames", 0)
    pump_until(app, lambda: window.snapshot()["metrics"].get("processed_frames", 0) >= starting_frames+15)
    window.toggle_pause()
    pump_until(app, lambda: window.snapshot()["state"] == "paused")
    paused = window.snapshot()["latest"]
    assert not paused["valid"]
    assert paused["gaze_screen_px"] is None
    assert not paused["screen_valid"]
    window.toggle_pause()
    pump_until(app, lambda: window.snapshot()["state"] == "running" and
               bool((window.snapshot().get("latest") or {}).get("valid")))
    resumed_frames = window.snapshot()["metrics"].get("processed_frames", 0)
    pump_until(app, lambda: window.snapshot()["metrics"].get("processed_frames", 0) >= resumed_frames+10)
    window.stop_recording()
    assert window.recorder is None

    loaded = load_session(session_path)
    assert len(loaded.records) >= 20
    assert loaded.manifest["metadata"]["synthetic"] is True
    assert loaded.manifest["metadata"]["provenance"] == "synthetic"
    assert all("video_frame" in row for row in loaded.records)
    assert (session_path/"camera.avi").stat().st_size > 1000
    assert any(row["valid"] and row["screen_valid"] for row in loaded.records)

    window.load_session_path(session_path)
    assert window.current_session is not None
    assert window.replay is not None
    middle = loaded.records[len(loaded.records)//2]
    window.seek_replay(round(middle["session_time_s"] * 1000))
    app.processEvents()
    row = window.replay.current()
    assert window._replay_index == row["video_frame"]
    window.play_replay()
    pump_until(app, lambda: window.replay.position_s >= middle["session_time_s"])
    window.pause_replay()
    assert window._replay_clock is None

    window.export_session_report()
    pump_until(app, lambda: not window._jobs)
    output = window.workspace/"reports"/session_path.name
    assert any(output.glob("*.html"))
    assert any(output.glob("*.csv"))
    assert "<svg" in next(output.glob("*.html")).read_text(encoding="utf-8")
    assert any(output.glob("*.json"))
    assert window.stop_tracking()
    assert window.runtime is None
    assert not errors, errors


def test_wrong_subject_cannot_reuse_demo_profile(reviewed_window):
    app, window, errors = reviewed_window
    window.open_demo()
    pump_until(app, lambda: window.runtime is not None and window.snapshot()["state"] == "running")
    assert window.stop_tracking()
    window.settings["subject_id"] = "different-operator"
    window.start_tracking()
    pump_until(app, lambda: window.runtime is not None and window.snapshot()["state"] == "fault")
    snapshot = window.snapshot()
    assert "subject_id" in snapshot["last_error"]
    assert not snapshot.get("latest") or snapshot["latest"]["valid"] is False
    assert window.stop_tracking()
