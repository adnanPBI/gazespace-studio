"""Authenticated local GazeSpace 0.2 HTTP consumer; stdlib HTTP plus shared clock.

Start tracking and the Integration API in Studio, then copy its token:

    $env:GAZESPACE_API_TOKEN = 'PASTE_THE_CURRENT_DESKTOP_TOKEN'
    python examples/consume_local_api.py --once
    python examples/consume_local_api.py

Importing this module makes no requests and starts no threads. Call poll() on a
worker; marshal its result to your existing UI. Clear markers on every invalid
update and when fresh_until_monotonic_s expires on the UI's own timer. The
deadline uses gazespace.clock.host_now_s() in this consumer; use that same
clock in the UI deadline check. Do not compare the server's source
timestamp directly against an unrelated clock. The token is read from the
environment and is never printed. No raw
camera image, recording or system cursor action is requested by this example.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import math
import os
import time
from gazespace.clock import HOST_CLOCK_DOMAIN, host_now_s
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


def unavailable(reason: str) -> dict:
    return {"valid": False, "screen_valid": False, "spatial_valid": False,
            "surface_hit": False, "new_frame": False, "reason": reason,
            "gaze_screen_px": None, "surface_name": None, "surface_xy_m": None,
            "surface_xy_px": None, "ray_origin_world_m": None,
            "ray_direction_world": None, "capture_timestamp_s": None,
            "received_monotonic_s": None, "fresh_until_monotonic_s": None}


def _vector(value, count: int) -> bool:
    return (isinstance(value, (list, tuple)) and len(value) == count
            and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                    and math.isfinite(v) for v in value))


def _reject_nonfinite(value: str):
    raise ValueError(f"Nonfinite JSON number: {value}")


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, "Redirects are not accepted", headers, fp)


class LocalGazeClient:
    def __init__(self, token: str, *, base_url: str = "http://127.0.0.1:8765",
                 timeout_s: float = 0.25, max_age_s: float = 0.4):
        url = urlsplit(base_url)
        if (url.scheme != "http" or url.hostname not in ("127.0.0.1", "localhost", "::1")
                or url.username or url.password or url.query or url.fragment
                or url.path not in ("", "/")):
            raise ValueError("Use the local HTTP server root, e.g. http://127.0.0.1:8765")
        if not isinstance(token, str) or len(token) < 16 or "\r" in token or "\n" in token:
            raise ValueError("Provide the current desktop bearer token (at least 16 characters)")
        if not all(math.isfinite(v) and v > 0 for v in (timeout_s, max_age_s)):
            raise ValueError("Timeout and maximum age must be finite and positive")
        if timeout_s > max_age_s:
            raise ValueError("Request timeout must not exceed the freshness limit")
        self._url = base_url.rstrip("/") + "/v1/gaze"
        self._token = token
        self.timeout_s, self.max_age_s = timeout_s, max_age_s
        # Do not send a loopback token through an environment-configured proxy
        # or follow a redirect that could change its destination.
        self._opener = build_opener(ProxyHandler({}), _NoRedirects())
        self._last_key = None
        self._last_new_frame_at: float | None = None

    def poll(self) -> dict:
        started = host_now_s()
        request = Request(self._url, headers={
            "Authorization": "Bearer " + self._token,
            "Accept": "application/json", "Cache-Control": "no-cache",
        })
        try:
            with self._opener.open(request, timeout=self.timeout_s) as response:
                payload = json.load(response, parse_constant=_reject_nonfinite)
        except HTTPError as exc:
            return unavailable(f"api_http_{exc.code}")
        except (URLError, TimeoutError, OSError, ValueError):
            return unavailable("api_connection_or_payload_error")
        finished = host_now_s()
        return self._select(payload, finished - started, finished)

    def _select(self, payload, round_trip_s: float, now: float) -> dict:
        if not isinstance(payload, dict):
            return unavailable("invalid_api_payload")
        if payload.get("state") != "running" or payload.get("running") is not True:
            return unavailable(str(payload.get("state", "runtime_unavailable")))
        if payload.get("last_error"):
            return unavailable("runtime_error")
        latest, metrics = payload.get("latest"), payload.get("metrics")
        if not isinstance(latest, dict) or not isinstance(metrics, dict):
            return unavailable("awaiting_result")
        age_ms = metrics.get("capture_age_ms")
        if (not isinstance(age_ms, (int, float)) or isinstance(age_ms, bool)
                or not math.isfinite(age_ms) or age_ms < 0
                or age_ms / 1000 + round_trip_s > self.max_age_s):
            return unavailable("stale_capture_or_slow_response")
        stamp = latest.get("capture_timestamp_s")
        if not isinstance(stamp, (int, float)) or isinstance(stamp, bool) or not math.isfinite(stamp):
            return unavailable("invalid_capture_timestamp")
        key = (stamp, latest.get("model_fingerprint"))
        new_frame = key != self._last_key
        if new_frame:
            self._last_key, self._last_new_frame_at = key, now
        elif self._last_new_frame_at is not None and now - self._last_new_frame_at > self.max_age_s:
            return unavailable("repeated_stale_result")
        if latest.get("valid") is not True:
            return unavailable(str(latest.get("reason", "invalid_estimate")))
        screen = latest.get("screen_valid") is True and _vector(latest.get("gaze_screen_px"), 2)
        spatial = (latest.get("spatial_valid") is True
                   and _vector(latest.get("ray_origin_world_m"), 3)
                   and _vector(latest.get("ray_direction_world"), 3))
        hit = (spatial and isinstance(latest.get("surface_name"), str)
               and bool(latest["surface_name"]) and _vector(latest.get("surface_xy_m"), 2))
        if not screen and not spatial:
            return unavailable("missing_finite_coordinates")
        camera = payload.get("camera") if isinstance(payload.get("camera"), dict) else {}
        return {
            "valid": True, "screen_valid": screen, "spatial_valid": spatial,
            "surface_hit": hit, "new_frame": new_frame,
            "reason": str(latest.get("reason", "estimated")),
            "quality": latest.get("quality"),  # heuristic quality, not accuracy probability
            "gaze_screen_px": deepcopy(latest["gaze_screen_px"]) if screen else None,
            "surface_name": latest["surface_name"] if hit else None,
            "surface_xy_m": deepcopy(latest["surface_xy_m"]) if hit else None,
            "surface_xy_px": deepcopy(latest.get("surface_xy_px"))
                if hit and _vector(latest.get("surface_xy_px"), 2) else None,
            "ray_origin_world_m": deepcopy(latest["ray_origin_world_m"]) if spatial else None,
            "ray_direction_world": deepcopy(latest["ray_direction_world"]) if spatial else None,
            "capture_timestamp_s": stamp,
            "capture_age_ms": age_ms,
            "request_round_trip_ms": round_trip_s * 1000,
            "received_monotonic_s": now,
            "host_clock_domain": HOST_CLOCK_DOMAIN,
            "fresh_until_monotonic_s": now + self.max_age_s - age_ms / 1000 - round_trip_s,
            "eye_origin_source": latest.get("eye_origin_source"),
            "source": latest.get("source"),
            "synthetic": bool(camera.get("synthetic") or camera.get("simulated")
                              or latest.get("eye_origin_source") == "synthetic_fixture"),
            "warnings": deepcopy(latest.get("warnings", [])),
        }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--token-env", default="GAZESPACE_API_TOKEN")
    parser.add_argument("--timeout", type=float, default=0.25)
    parser.add_argument("--max-age", type=float, default=0.4)
    parser.add_argument("--interval", type=float, default=0.05)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    if not math.isfinite(args.interval) or args.interval <= 0:
        parser.error("--interval must be finite and positive")
    try:
        client = LocalGazeClient(os.environ.get(args.token_env, ""), base_url=args.url,
                                timeout_s=args.timeout, max_age_s=args.max_age)
    except ValueError as exc:
        parser.error(str(exc))
    try:
        while True:
            print(json.dumps(client.poll(), allow_nan=False), flush=True)
            if args.once:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print(json.dumps(unavailable("consumer_stopped")), flush=True)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
