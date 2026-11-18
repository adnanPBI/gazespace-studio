"""One high-resolution monotonic host clock for acquisition and freshness.

On Windows Python 3.11/3.12 ``time.monotonic`` uses a lower-resolution clock
than ``perf_counter``. Using the latter consistently prevents rapid, distinct
camera frames from inheriting the same coarse host tick. Source/video PTS and
device hardware clocks remain separate; only host arrival times and deadlines
use this clock. Its epoch is unspecified and is never a wall-clock timestamp.
"""
from __future__ import annotations

import time

HOST_CLOCK_DOMAIN = "gazespace.perf_counter"


def host_now_s() -> float:
    """Return high-resolution monotonic host seconds (Windows QPC)."""
    return time.perf_counter()


def host_clock_info() -> dict:
    """Describe the actual host clock used by this process for diagnostics."""
    info = time.get_clock_info("perf_counter")
    return {"domain": HOST_CLOCK_DOMAIN, "implementation": info.implementation,
            "resolution_s": info.resolution, "monotonic": info.monotonic,
            "adjustable": info.adjustable}
