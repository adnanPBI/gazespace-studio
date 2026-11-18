"""Timestamp-aware One Euro smoothing; rejected observations reset stale output."""
from __future__ import annotations
import math
import numpy as np

class OneEuro:
    def __init__(self, min_cutoff=1.1, beta=0.014, d_cutoff=1.0,
                 max_gap_s=0.35):
        if not np.isfinite([min_cutoff, beta, d_cutoff, max_gap_s]).all() or min_cutoff <= 0 or d_cutoff <= 0 or beta < 0 or max_gap_s <= 0:
            raise ValueError("filter parameters must be finite with positive cutoffs/gap and nonnegative beta")
        self.min_cutoff, self.beta = float(min_cutoff), float(beta)
        self.d_cutoff, self.max_gap_s = float(d_cutoff), float(max_gap_s)
        self.reset()

    def reset(self):
        self.x = self.raw_x = self.dx = self.timestamp = None

    @staticmethod
    def alpha(cutoff, dt):
        tau = 1 / (2 * math.pi * np.asarray(cutoff))
        return 1 / (1 + tau / dt)

    def update(self, x, timestamp_s):
        x = np.asarray(x, dtype=float)
        if not np.all(np.isfinite(x)) or not np.isfinite(timestamp_s):
            raise ValueError("nonfinite filter input")
        if self.timestamp is None or timestamp_s <= self.timestamp or timestamp_s - self.timestamp > self.max_gap_s:
            self.x = x.copy()
            self.raw_x = x.copy()
            self.dx = np.zeros_like(x)
            self.timestamp = timestamp_s
            return x.copy()
        dt = max(1e-4, timestamp_s - self.timestamp)
        if x.shape != self.x.shape:
            raise ValueError("filter input shape changed")
        derivative = (x - self.raw_x) / dt
        self.raw_x = x.copy()
        a_d = self.alpha(self.d_cutoff, dt)
        self.dx = a_d * derivative + (1 - a_d) * self.dx
        cutoff = self.min_cutoff + self.beta * np.abs(self.dx)
        a = self.alpha(cutoff, dt)
        self.x = a * x + (1 - a) * self.x
        self.timestamp = timestamp_s
        return self.x.copy()
