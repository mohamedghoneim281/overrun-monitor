"""Motor-speed plant + discrete PI controller, co-simulated with the scheduler.

Plant (normalised):  tau * dw/dt = -w + K*u - d(t),   tau = 0.1 s, K = 1.
The encoder is latched by hardware at the *arrival* of each control message, so the PI
sample is y(t_arrival); the actuation u is applied when the control callback *completes*.
Hence control performance degrades through response time (latency) and missed periods.

Between events the plant has a closed form; the integral of squared tracking error is
accumulated exactly (no integration step), so results do not depend on a time grid.
"""
from __future__ import annotations

import math
from bisect import bisect_right

TAU_S = 0.1
GAIN = 1.0
KP, KI = 2.0, 20.0
U_MAX = 1.5
T_CTRL_S = 0.02

# Reference: a fixed pseudo-random staircase (a new level every 0.5 s, identical in every run)
# so that the loop is persistently excited; load torque: pulses of 0.15.
import numpy as np
REF_STEP_US = 500_000
REF_LEVELS = np.random.default_rng(12345).uniform(0.2, 0.9, 400).tolist()
LOAD_PERIOD_US = 5_100_000
LOAD_ON_US = 2_300_000


class PI:
    """Discrete PI with conditional-integration anti-windup (integrates only when not
    saturated, or when the error would unwind the saturation)."""

    def __init__(self, i0: float):
        self.i = i0

    def update(self, e: float) -> float:
        u_pre = KP * e + self.i + KI * T_CTRL_S * e
        u = min(max(u_pre, -U_MAX), U_MAX)
        if u == u_pre or (u_pre > U_MAX and e < 0) or (u_pre < -U_MAX and e > 0):
            self.i += KI * T_CTRL_S * e
        return u


class Plant:
    def __init__(self, horizon_us: int, warmup_us: int = 0, stop_us: int | None = None,
                 trace: bool = False):
        self.horizon = horizon_us
        self.warmup = warmup_us
        self.stop = stop_us if stop_us is not None else horizon_us
        # breakpoints of the piecewise-constant reference and load, plus the metric window edges
        bps = {warmup_us, self.stop}
        t = REF_STEP_US
        while t < horizon_us:
            bps.add(t); t += REF_STEP_US
        t = 0
        while t < horizon_us:
            bps.add(t + LOAD_PERIOD_US - LOAD_ON_US); bps.add(t + LOAD_PERIOD_US); t += LOAD_PERIOD_US
        self.bps = sorted(b for b in bps if 0 < b < horizon_us) + [10**18]
        self.k = 0                                  # index of the next breakpoint
        self.t = 0
        self.r = self._ref(0)
        self.d = self._load(0)
        self.w = self.r - 0.0                       # start at steady state for the first reference
        self.u = self.r                             # steady-state input
        self.ise = 0.0
        self.pi = PI(self.r)
        self.trace = [] if trace else None

    @staticmethod
    def _ref(t_us: int) -> float:
        return REF_LEVELS[t_us // REF_STEP_US]

    @staticmethod
    def _load(t_us: int) -> float:
        return 0.15 if (t_us % LOAD_PERIOD_US) >= LOAD_PERIOD_US - LOAD_ON_US else 0.0

    def advance(self, t_us: int) -> None:
        """Advance the plant state to time t_us under the current actuation."""
        while self.t < t_us:
            seg_end = min(t_us, self.bps[self.k])
            dt = (seg_end - self.t) * 1e-6
            a = math.exp(-dt / TAU_S)
            wss = GAIN * self.u - self.d
            if self.warmup <= self.t < self.stop:
                p = self.r - wss
                q = wss - self.w
                self.ise += (p * p * dt + 2 * p * q * TAU_S * (1 - a)
                             + q * q * TAU_S * 0.5 * (1 - a * a))
            self.w = wss + (self.w - wss) * a
            self.t = seg_end
            if seg_end == self.bps[self.k]:
                self.k += 1
                self.r = self._ref(seg_end)
                self.d = self._load(seg_end)
        if self.trace is not None:
            self.trace.append((self.t, self.w, self.r, self.u))

    def sample(self, t_us: int) -> tuple[float, float]:
        """Latch (y, r) at time t_us."""
        self.advance(t_us)
        return self.w, self.r

    def actuate(self, t_us: int, e: float) -> None:
        """Run the PI update on error e and apply the result at time t_us."""
        self.advance(t_us)
        self.u = self.pi.update(e)
        if self.trace is not None:
            self.trace.append((self.t, self.w, self.r, self.u))

    def rms_error(self) -> float:
        span = (self.stop - self.warmup) * 1e-6
        return math.sqrt(self.ise / span)
