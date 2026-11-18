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

from gazespace.sessions import SessionRecorder, list_sessions, load_session
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


def test_replay_delayed_first_frame_seek_play_and_initial_gap_are_consistent(
        reviewed_window, monkeypatch):
    """Exercise native replay deterministically, independent of runner speed."""
    import numpy as np
    import gazespace.ui.main_window as desktop

    app, window, errors=reviewed_window
    window.timer.stop()  # drive the real poll handler with an explicit clock
    now=[100.0]
    with SessionRecorder(window.workspace/'sessions',
                         {'provenance':'synthetic','synthetic':True,'screen_size':[101,101]},
                         record_video=True,clock=lambda:now[0]) as recorder:
        for index,elapsed in enumerate((3.0,3.1004,3.2004)):
            now[0]=100.+elapsed
            recorder.append({'timestamp_s':10.+index/10,'valid':True,
                             'screen_valid':True,'spatial_valid':False,'quality':.9,
                             'reason':'synthetic_fixture','source':'synthetic',
                             'gaze_screen_px':[25.+index,50.],
                             'gaze_screen_normalized':[(25.+index)/100.,.5]},
                            np.full((120,160,3),index*50,np.uint8))
    window.load_session_path(recorder.path)
    assert window.replay.duration_s==pytest.approx(3.2004)
    assert window.replay_slider.maximum()==3201
    assert window.replay.current() is None
    assert window.replay_map.position is None
    assert window.replay_video._image is None

    # The rounded seek begins before the middle observation. One deterministic
    # timer tick must cross its actual recording-relative time, without a sleep.
    middle=window.current_session.records[1]
    window.replay_slider.setValue(round(middle['session_time_s']*1000))
    assert window._replay_index==0
    monkeypatch.setattr(desktop,'host_now_s',lambda:now[0])
    window.play_replay()
    now[0]+=.01
    window.poll()
    assert window.replay.position_s>=middle['session_time_s']
    assert window._replay_index==1
    assert window.replay_video._image is not None
    assert window.replay_map.position is not None
    window.pause_replay()

    # Scrubbing backwards must remove the future frame, overlay and trail, then
    # revisiting that same frame must reload it instead of using an old index.
    window.replay_slider.setValue(1000)
    assert window.replay.current() is None
    assert window._replay_index==-1
    assert window.replay_video._image is None
    assert window.replay_video._bgr is None
    assert not window.replay_video.result
    assert window.replay_video.observation is None
    assert window.replay_map.position is None
    assert window.replay_map.trail==[]
    window.replay_slider.setValue(3101)
    assert window._replay_index==1
    assert window.replay_video._image is not None
    assert window.replay_map.position is not None

    # The final slider step must reach a sub-millisecond final observation.
    window.replay_slider.setValue(window.replay_slider.maximum())
    assert window.replay.finished
    assert window._replay_index==2
    window.play_replay()
    assert window.replay.position_s==0
    assert window.replay.current() is None
    assert window.replay_video._image is None
    window.pause_replay()
    assert window._replay_clock is None
    assert not errors,errors
