"""Windows clock regressions: coarse legacy ticks and different clock epochs.

Linux commonly backs monotonic/perf_counter with the same clock. Deliberately
separating them exposes mixed-domain freshness bugs without needing a camera.
"""
from __future__ import annotations

import sys
from threading import Event
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

import gazespace.clock as clock_module
from gazespace.capture import FrameSource, OpenCVSource, PylonSource, SyntheticEyeSource
from gazespace.clock import HOST_CLOCK_DOMAIN, host_now_s
from gazespace.engine import GazeEngine
from gazespace.runtime import TrackingRuntime
from gazespace.sessions import SessionRecorder, load_session
from gazespace.types import Frame, GazeResult, Observation


class HostClock:
    def __init__(self, value=100_000.0, step=0.0):
        self.value, self.step = value, step

    def __call__(self):
        current = self.value
        self.value += self.step
        return current


def separate_clocks(monkeypatch, *, step=0.0):
    host = HostClock(step=step)
    # Many rapid reads share a legacy Windows tick, at a different epoch.
    monkeypatch.setattr(clock_module.time, "monotonic", lambda: 17.0)
    monkeypatch.setattr(clock_module.time, "perf_counter", host)
    return host


def fake_pylon(monkeypatch):
    class Node:
        def __init__(self, value): self.Value = value

    class Result:
        PixelType, TimeStamp = 1, 987654321
        Array = np.full((12, 16), 127, np.uint8)
        def GrabSucceeded(self): return True
        def Release(self): pass

    class Camera:
        Width, Height = Node(16), Node(12)
        def Open(self): self.open = True
        def IsOpen(self): return self.open
        def Close(self): self.open = False
        def StartGrabbing(self, *_): self.grabbing = True
        def IsGrabbing(self): return self.grabbing
        def StopGrabbing(self): self.grabbing = False
        def RetrieveResult(self, *_): return Result()

    device = SimpleNamespace(GetSerialNumber=lambda: "00017", GetModelName=lambda: "Clock fixture")
    factory = SimpleNamespace(EnumerateDevices=lambda: [device], CreateDevice=lambda value: value)
    pylon = SimpleNamespace(TlFactory=SimpleNamespace(GetInstance=lambda: factory),
                            InstantCamera=lambda _: Camera(), ImageFormatConverter=lambda: SimpleNamespace(),
                            PixelType_Mono8=1, PixelType_BGR8packed=3,
                            GrabStrategy_LatestImageOnly=1, TimeoutHandling_ThrowException=1)
    monkeypatch.setitem(sys.modules, "pypylon", SimpleNamespace(pylon=pylon))


@pytest.mark.parametrize("backend", ["opencv", "pylon"])
def test_rapid_live_capture_uses_high_resolution_host_clock(monkeypatch, backend):
    host = separate_clocks(monkeypatch, step=.0001)
    if backend == "pylon":
        fake_pylon(monkeypatch)
        source = PylonSource(serial="00017")
    else:
        class Capture:
            def isOpened(self): return True
            def set(self, *_): return True
            def get(self, prop): return {cv2.CAP_PROP_FPS: 30, cv2.CAP_PROP_FRAME_WIDTH: 16,
                                       cv2.CAP_PROP_FRAME_HEIGHT: 12}.get(prop, 0)
            def getBackendName(self): return "clock-fixture"
            def read(self): return True, np.zeros((12, 16, 3), np.uint8)
            def release(self): pass
        monkeypatch.setattr(cv2, "VideoCapture", lambda *_: Capture())
        source = OpenCVSource(0)
    with source:
        frames = [source.read() for _ in range(8)]
        assert source.capabilities()["host_clock_domain"] == HOST_CLOCK_DOMAIN
        if backend == "pylon":
            assert source.capabilities()["serial"] == "00017"
            assert all(f.metadata["hardware_ticks"] == 987654321 for f in frames)
        stamps = [f.timestamp_s for f in frames]
        assert all(b > a for a, b in zip(stamps, stamps[1:]))
        assert stamps[-1] - stamps[0] < .0156
        assert all(100_000 <= f.timestamp_s <= f.received_monotonic_s <= host.value for f in frames)
        assert all(f.metadata["host_clock_domain"] == HOST_CLOCK_DOMAIN for f in frames)


def test_runtime_age_uses_receive_clock_and_preserves_independent_source_pts(monkeypatch):
    host = separate_clocks(monkeypatch)
    delivered, release = Event(), Event()

    class Source(FrameSource):
        def __init__(self): self.reads = 0
        def read(self):
            self.reads += 1
            if self.reads > 1:
                release.wait(2)
                return None
            return Frame(np.zeros((12, 16, 3), np.uint8), 2.5,
                         received_monotonic_s=host_now_s(),
                         metadata={"host_clock_domain": HOST_CLOCK_DOMAIN})
        def request_stop(self): release.set()
        def close(self): pass

    class Engine:
        def process(self, image, stamp):
            return GazeResult(stamp, True, "fixture", "clock-test", .9,
                              gaze_screen_px=(10, 20), screen_valid=True)
        def close(self): pass

    run = TrackingRuntime(Source, Engine, max_age_s=.2,
                          on_result=lambda *_: delivered.set())
    try:
        run.start()
        assert delivered.wait(2), "Runtime rejected a correctly timed source"
        fresh = run.snapshot(False)
        assert fresh["latest"]["valid"] and fresh["latest"]["timestamp_s"] == 2.5
        assert fresh["metrics"]["capture_age_ms"] == 0
        host.value += .25
        stale = run.snapshot(False)
        assert not stale["latest"]["valid"]
        assert stale["latest"]["reason"] == "stale_capture"
        assert stale["latest"]["gaze_screen_px"] is None
        assert stale["metrics"]["capture_age_ms"] == pytest.approx(250)
    finally:
        release.set()
        assert run.stop(1)


@pytest.mark.parametrize("received", [float("nan"), float("inf"), 100_001.0])
def test_runtime_snapshot_never_clamps_wrong_clock_to_fresh(monkeypatch, received):
    separate_clocks(monkeypatch)
    run = TrackingRuntime(lambda: None, lambda: None)
    run._state, run._last_received = "running", received
    run._result = GazeResult(3, True, "fixture", "test", .9,
                             gaze_screen_px=(10, 20), screen_valid=True)
    value = run.snapshot(False)
    assert value["latest"]["reason"] == "invalid_host_clock"
    assert value["latest"]["gaze_screen_px"] is None
    assert not value["latest"]["screen_valid"]
    assert value["metrics"]["capture_age_ms"] is None


def test_engine_duration_resolves_submillisecond_work_with_coarse_legacy_clock(monkeypatch):
    host = separate_clocks(monkeypatch)
    class Tracker:
        def process(self, image, stamp):
            host.value += .0007
            return Observation(stamp, (16, 12))
        def close(self): pass
    engine = GazeEngine(Tracker())
    result = engine.process(np.zeros((12, 16, 3), np.uint8), timestamp_s=7.5)
    assert result.timestamp_s == 7.5
    assert result.processing_ms == pytest.approx(.7, abs=1e-6)


def test_default_recording_clock_preserves_relative_session_time(monkeypatch, tmp_path):
    host = separate_clocks(monkeypatch)
    with SessionRecorder(tmp_path, {"provenance": "synthetic"}) as recorder:
        host.value += .003
        row = recorder.append(GazeResult(7.5, False, "fixture", "test", 0))
        assert row["session_time_s"] == pytest.approx(.003, abs=1e-8)
        assert row["timestamp_s"] == 7.5
    loaded = load_session(recorder.path)
    assert loaded.manifest["session_time_clock_domain"] == HOST_CLOCK_DOMAIN
    assert loaded.records[0]["session_time_s"] == pytest.approx(.003, abs=1e-8)


def test_generated_source_pts_is_not_replaced_by_fast_decode_arrival(monkeypatch):
    separate_clocks(monkeypatch, step=.0001)
    with SyntheticEyeSource(realtime=False, fps=20) as source:
        first, second = source.read(), source.read()
    assert second.timestamp_s - first.timestamp_s == pytest.approx(.05)
    assert second.metadata["video_pts_s"] == .05
    assert second.received_monotonic_s - first.received_monotonic_s < .001
