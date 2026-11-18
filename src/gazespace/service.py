"""Loopback telemetry API with stale-result protection and optional bearer auth.

No endpoint exposes raw camera images or landmark/feature vectors. External
browser origins and non-loopback Host headers are rejected by default.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
import hmac
import json
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit

from .runtime import TrackingRuntime


class GazeService:
    """Compatibility adapter for the CLI and direct embedding.

    Prefer engine_factory=... so MediaPipe is constructed in its worker thread.
    The legacy engine positional argument remains available for host-owned
    engines; service.stop never closes an engine while processing is active.
    """
    def __init__(self, engine=None, source_factory=None, record_jsonl: str | None = None,
                 *, engine_factory=None, max_age_s: float = .4):
        if source_factory is None or (engine is None and engine_factory is None):
            raise ValueError("source_factory and engine/engine_factory are required")
        self.engine = engine
        self._factory = engine_factory or (lambda: engine)
        self.record_jsonl = record_jsonl
        self._stream = None
        self._record_lock = Lock()
        self.runtime = TrackingRuntime(source_factory, self._build_engine,
                                       max_age_s=max_age_s, on_result=self._record)

    def _build_engine(self):
        self.engine = self._factory()
        return self.engine

    def _record(self, frame, observation, result):
        if not self.record_jsonl:
            return
        with self._record_lock:
            if self._stream is None:
                path = Path(self.record_jsonl)
                path.parent.mkdir(parents=True, exist_ok=True)
                self._stream = path.open("a", encoding="utf-8")
            self._stream.write(json.dumps(result.as_dict(), allow_nan=False) + "\n")
            self._stream.flush()

    def start(self):
        self.runtime.start()

    def stop(self, timeout: float = 3.0):
        completed = self.runtime.stop(timeout)
        # The callback owns its stream until the worker has stopped.
        if completed:
            with self._record_lock:
                if self._stream is not None:
                    self._stream.close()
                    self._stream = None
        return completed

    def snapshot(self):
        return self.runtime.telemetry()

    def metrics(self):
        return self.runtime.telemetry()["metrics"]


def _loopback_host(value: str) -> bool:
    try:
        host = urlsplit("//" + value).hostname
    except ValueError:
        return False
    return host in ("localhost", "127.0.0.1", "::1")


def _origin_allowed(value: str | None) -> bool:
    if value is None:
        return True  # Python/native clients do not send a browser Origin.
    try:
        origin = urlsplit(value)
        return origin.scheme in ("http", "https") and origin.hostname in ("localhost", "127.0.0.1", "::1")
    except ValueError:
        return False


def create_api(service: GazeService | TrackingRuntime, *, token: str | None = None,
               manage_lifecycle: bool = False):
    try:
        from fastapi import FastAPI, WebSocket, Request
        from fastapi.responses import JSONResponse
        from starlette.websockets import WebSocketDisconnect
    except ImportError as exc:
        raise RuntimeError("API mode requires pip install 'gazespace[api]'") from exc
    if token is not None and len(token) < 16:
        raise ValueError("API bearer tokens must contain at least 16 characters")

    def snapshot():
        return service.telemetry() if isinstance(service, TrackingRuntime) else service.snapshot()

    def authorized(headers):
        if token is None:
            return True
        supplied = headers.get("authorization", "")
        if supplied.startswith("Bearer "):
            return hmac.compare_digest(supplied[7:], token)
        protocols = [p.strip() for p in headers.get("sec-websocket-protocol", "").split(",")]
        return any(p.startswith("bearer.") and hmac.compare_digest(p[7:], token) for p in protocols)

    @asynccontextmanager
    async def lifespan(app):
        if manage_lifecycle:
            service.start()
        try:
            yield
        finally:
            if manage_lifecycle:
                service.stop()

    app = FastAPI(title="GazeSpace local telemetry", version="0.2.1", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def protect(request: Request, call_next):
        if not _loopback_host(request.headers.get("host", "")) or not _origin_allowed(request.headers.get("origin")):
            return JSONResponse({"detail": "Local host and local origin required"}, status_code=403)
        if not authorized(request.headers):
            return JSONResponse({"detail": "Bearer token required"}, status_code=401,
                                headers={"WWW-Authenticate": "Bearer"})
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/health")
    def health():
        value = snapshot()
        return {"ok": value["running"] and not value["last_error"],
                "state": value.get("state"), "error": value["last_error"]}

    @app.get("/v1/gaze")
    def gaze():
        return snapshot()

    @app.get("/v1/metrics")
    def metrics():
        return snapshot().get("metrics", {})

    # FastAPI resolves annotations through function globals; explicit annotation
    # avoids a function-local WebSocket forward-reference becoming a query arg.
    async def stream(ws):
        import asyncio
        if not _loopback_host(ws.headers.get("host", "")) or not _origin_allowed(ws.headers.get("origin")):
            await ws.close(code=1008)
            return
        if not authorized(ws.headers):
            await ws.close(code=1008)
            return
        protocols = [p.strip() for p in ws.headers.get("sec-websocket-protocol", "").split(",")]
        await ws.accept(subprotocol="gazespace" if "gazespace" in protocols else None)
        try:
            while True:
                await ws.send_json(snapshot())
                await asyncio.sleep(.05)
        except (WebSocketDisconnect, RuntimeError, OSError):
            return
    stream.__annotations__["ws"] = WebSocket
    app.websocket("/v1/stream")(stream)
    return app
