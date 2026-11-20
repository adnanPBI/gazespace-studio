"""Verify actual host application gaze-state lifecycle evidence.

The original host must export newline-delimited events for its own UI/consumer.
This checks contracts, not the host's proprietary internals or human accuracy.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys

CASES = {"valid", "blink", "stale", "disconnect", "stop", "recovery"}


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_events(events):
    checks = {key: False for key in CASES}
    last_time = None
    expected_order = ("valid", "blink", "stale", "disconnect", "stop", "recovery")
    stage = 0
    for index, record in enumerate(events, 1):
        if not isinstance(record, dict):
            raise ValueError(f"host event {index} must be a JSON object")
        name = str(record.get("case", "")).strip()
        if name not in CASES:
            raise ValueError(f"host event {index} has unknown test case")
        if stage < len(expected_order) and name == expected_order[stage]:
            stage += 1
        elif stage == 0 or name != expected_order[stage - 1]:
            raise ValueError(f"host event {index}: unexpected lifecycle order")
        time_s = float(record.get("host_event_time_s", float("nan")))
        if not math.isfinite(time_s) or (last_time is not None and time_s <= last_time):
            raise ValueError(f"host event {index} requires increasing host timestamps")
        last_time = time_s
        visible = record.get("host_gaze_visible")
        engine_valid = record.get("engine_valid")
        pixels = record.get("host_screen_xy_px")
        if not isinstance(visible, bool) or not isinstance(engine_valid, bool):
            raise ValueError(f"host event {index} requires explicit boolean states")
        if name in ("valid", "recovery"):
            if not (visible and engine_valid and isinstance(pixels, list)
                    and len(pixels) == 2 and all(isinstance(v, (float, int))
                    and math.isfinite(v) for v in pixels)):
                raise ValueError(f"host event {index}: valid gaze must be shown with finite coordinates")
        else:
            if visible or engine_valid or pixels is not None:
                raise ValueError(f"host event {index}: invalid gaze must be cleared from the host")
        checks[name] = True
    if stage != len(expected_order):
        raise ValueError("host event trace is missing required lifecycle stages")
    return checks


def verify(event_path, host_executable, *, physical_test_confirmed=False):
    if host_executable.name.lower() in {"gazespacecli.exe", "gazespacestudio.exe"}:
        raise ValueError("Provide the original client's host application, not GazeSpace itself")
    events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()
              if line.strip()]
    if not events:
        raise ValueError("Empty host event trace")
    cases = validate_events(events)
    original_confirmed = bool(physical_test_confirmed and sys.platform == "win32"
                              and host_executable.is_file())
    return {
        "schema_version": 1,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "status": "passed" if all(cases.values()) and original_confirmed else "pending_original_host_test",
        "cases": cases,
        "original_client_app_tested": original_confirmed,
        "host_application": host_executable.name,
        "host_application_build_sha256": digest(host_executable) if host_executable.is_file() else None,
        "event_trace_sha256": digest(event_path),
        "event_count": len(events),
        "scope": "Original host UI clearing and recovery states; independent operator attestation required",
        "limitations": ["A supplied event trace does not by itself prove how the original host produced it.",
                        "Camera physics, measured gaze accuracy and sensor-to-display latency are separate gates."],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--host-executable", type=Path, required=True)
    parser.add_argument("--physical-test-confirmed", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = verify(args.events, args.host_executable,
                        physical_test_confirmed=args.physical_test_confirmed)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        report = {"schema_version": 1, "status": "failed",
                  "original_client_app_tested": False, "error": str(exc)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"{report['status']}: {args.output}")
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    sys.exit(main())
