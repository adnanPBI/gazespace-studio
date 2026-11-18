"""Lifecycle regressions: stale gaze, bounded buffering, camera ownership and API."""
from __future__ import annotations

from threading import Event, current_thread
import time
import numpy as np
import pytest

from gazespace.capture import FrameSource, SyntheticEyeSource
from gazespace.runtime import TrackingRuntime
from gazespace.types import Frame, GazeResult, Observation
from gazespace.service import GazeService, create_api


class FakeEngine:
    def __init__(self, delay=0):
        self.delay = delay
        self.last_observation = None
        self.count = 0
        self.closed_by = None
        self.created_by = current_thread().name

    def process(self, image, timestamp_s):
        if self.delay:
            time.sleep(self.delay)
        self.count += 1
        self.last_observation = Observation(timestamp_s, (image.shape[1], image.shape[0]),
                                             features=np.zeros(10), quality=.9)
        return GazeResult(timestamp_s, True, "screen_gaze_estimated", "test", .9,
                          gaze_screen_px=(100, 80), gaze_screen_normalized=(.5, .5),
                          ray_origin_world_m=(0, 0, 0), ray_direction_world=(0, 0, 1),
                          capture_timestamp_s=timestamp_s)

    def metrics(self):
        return {"frames": self.count}

    def close(self):
        self.closed_by = current_thread().name


def wait_for(predicate, timeout=2):
    limit = time.monotonic() + timeout
    while time.monotonic() < limit:
        if predicate():
            return
        time.sleep(.005)
    assert predicate(), "Timed out waiting for runtime state"


def test_pause_resume_stop_clears_coordinates_and_owns_engine():
    engines = []
    def factory():
        eng = FakeEngine()
        engines.append(eng)
        return eng
    run = TrackingRuntime(lambda: SyntheticEyeSource(fps=60), factory)
    try:
        run.start()
        wait_for(lambda: bool(run.snapshot(False)["latest"]))
        assert run.snapshot(False)["latest"]["valid"]
        assert engines[0].created_by == "gazespace-processing"
        run.pause()
        paused = run.snapshot(False)
        assert paused["state"] == "paused"
        assert paused["latest"]["valid"] is False
        assert paused["latest"]["gaze_screen_px"] is None
        assert paused["latest"]["ray_direction_world"] is None
        run.resume()
        wait_for(lambda: run.snapshot(False)["state"] == "running")
        assert run.snapshot(False)["latest"]["valid"]
    finally:
        assert run.stop()
    assert engines[0].closed_by == "gazespace-processing"
    assert run.snapshot(False)["latest"]["valid"] is False


def test_mailbox_drops_backlog_and_does_not_grow():
    run = TrackingRuntime(lambda: SyntheticEyeSource(fps=300), lambda: FakeEngine(.04))
    try:
        run.start()
        wait_for(lambda: run.snapshot(False)["metrics"]["processed_frames"] >= 4)
        metrics = run.snapshot(False)["metrics"]
        assert metrics["dropped_frames"] > 5
        assert metrics["received_frames"] > metrics["processed_frames"]
    finally:
        run.stop()


def test_camera_disconnect_invalidates_before_retry():
    class Disconnecting(FrameSource):
        is_live = True
        def __init__(self): self.seq = 0
        def read(self):
            self.seq += 1
            time.sleep(.02)
            if self.seq > 4:
                raise OSError("USB disconnected")
            return Frame(np.zeros((120,160,3),np.uint8),time.monotonic(),self.seq)
        def close(self): pass
    run = TrackingRuntime(Disconnecting, FakeEngine, reconnect_delay_s=.3)
    try:
        run.start()
        wait_for(lambda: run.snapshot(False)["state"] == "running")
        wait_for(lambda: run.snapshot(False)["state"] == "reconnecting")
        snap = run.snapshot(False)
        assert not snap["latest"]["valid"]
        assert snap["latest"]["gaze_screen_px"] is None
        assert "USB disconnected" in snap["last_error"]
    finally:
        run.stop()


def test_stale_frame_is_invalid_even_when_read_is_blocked():
    released = Event()
    class Stalling(FrameSource):
        def __init__(self): self.seq=0
        def read(self):
            self.seq += 1
            if self.seq > 1:
                released.wait(2)
                return None
            return Frame(np.zeros((120,160,3),np.uint8),time.monotonic(),1)
        def request_stop(self): released.set()
        def close(self): pass
    run=TrackingRuntime(Stalling,FakeEngine,max_age_s=.04)
    try:
        run.start()
        wait_for(lambda: bool(run.snapshot(False)["latest"]))
        time.sleep(.07)
        snap=run.snapshot(False)
        assert snap["latest"]["reason"]=="stale_capture"
        assert not snap["latest"]["valid"]
        assert snap["observation"] is None
    finally:
        run.stop()


def test_blocked_stop_does_not_cross_thread_release_camera():
    entered, release=Event(), Event()
    sources=[]
    class Blocking(FrameSource):
        def __init__(self): self.closed=False;sources.append(self)
        def read(self): entered.set();release.wait(2);return None
        def close(self): self.closed=True;self.closed_by=current_thread().name
    run=TrackingRuntime(Blocking,FakeEngine)
    run.start()
    assert entered.wait(1)
    assert run.stop(timeout=.02) is False
    assert not sources[0].closed
    assert run.snapshot(False)["state"]=="stopping_blocked"
    with pytest.raises(RuntimeError): run.start()
    release.set()
    assert run.stop(timeout=1)
    assert sources[0].closed
    assert sources[0].closed_by=="gazespace-acquisition"


def test_file_eof_clears_last_gaze():
    run=TrackingRuntime(lambda:SyntheticEyeSource(duration_s=.15,fps=60),FakeEngine)
    run.start()
    wait_for(lambda: run.snapshot(False)["state"]=="exhausted")
    snap=run.snapshot(False)
    assert snap["latest"] is None or not snap["latest"]["valid"]
    assert run.stop()


def test_frame_hook_handles_depth_capable_engines():
    class FrameEngine(FakeEngine):
        called=False
        def process_frame(self,frame):
            self.called=True
            return super().process(frame.image,frame.timestamp_s)
    engine=FrameEngine()
    run=TrackingRuntime(lambda:SyntheticEyeSource(),lambda:engine)
    try:
        run.start()
        wait_for(lambda: engine.called)
    finally:
        run.stop()


def test_callback_errors_are_reported_without_killing_capture():
    def bad_callback(*_): raise OSError("disk full")
    run=TrackingRuntime(lambda:SyntheticEyeSource(),FakeEngine,on_result=bad_callback)
    try:
        run.start()
        wait_for(lambda: run.snapshot(False)["metrics"]["callback_error"] is not None)
        snap=run.snapshot(False)
        assert snap["state"]=="running"
        assert "disk full" in snap["metrics"]["callback_error"]
    finally:
        run.stop()


def test_api_real_http_websocket_auth_and_origin_rules():
    from fastapi.testclient import TestClient
    run=TrackingRuntime(lambda:SyntheticEyeSource(),FakeEngine)
    token="test-only-secret-value-32-chars"
    app=create_api(run,token=token,manage_lifecycle=True)
    with TestClient(app,base_url="http://localhost") as client:
        assert client.get("/health").status_code==401
        headers={"Authorization":"Bearer "+token}
        wait_for(lambda: run.snapshot(False)["state"]=="running")
        assert client.get("/health",headers=headers).json()["ok"]
        payload=client.get("/v1/gaze",headers=headers)
        assert payload.status_code==200
        assert payload.headers["cache-control"]=="no-store"
        assert "frame" not in payload.json() and "observation" not in payload.json()
        assert client.get("/v1/gaze",headers={**headers,"Origin":"https://untrusted.example"}).status_code==403
        assert client.get("/v1/gaze",headers={**headers,"Host":"attacker.example"}).status_code==403
        with client.websocket_connect("ws://localhost/v1/stream",headers=headers) as ws:
            data=ws.receive_json()
            assert data["latest"]["valid"]
        run.pause()
        assert client.get("/v1/gaze",headers=headers).json()["latest"]["valid"] is False
    assert not run.snapshot(False)["alive"]


def test_legacy_service_recording_is_explicit_and_json_only(tmp_path):
    from fastapi.testclient import TestClient
    path=tmp_path/"metadata.jsonl"
    service=GazeService(source_factory=lambda:SyntheticEyeSource(fps=40),
                        engine_factory=FakeEngine,record_jsonl=str(path))
    service.start()
    wait_for(lambda:path.exists() and path.stat().st_size>0)
    assert service.stop()
    import json
    rows=[json.loads(line) for line in path.read_text().splitlines()]
    assert rows and "gaze_screen_px" in rows[0]
    assert "image" not in rows[0]


def test_paused_finite_video_does_not_run_to_eof():
    run=TrackingRuntime(lambda:SyntheticEyeSource(duration_s=.3,fps=60),FakeEngine)
    try:
        run.start()
        wait_for(lambda:run.snapshot(False)["state"]=="running")
        run.pause()
        time.sleep(.35)
        assert run.snapshot(False)["state"]=="paused"
        run.resume()
        wait_for(lambda:run.snapshot(False)["state"]=="running")
    finally:
        run.stop()
