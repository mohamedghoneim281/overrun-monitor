"""Execution-time disturbances, identical across policies for a given (scenario, seed).

Two mechanisms, both expressed purely in terms of time (so they do not depend on what the
scheduler decides to run):

* slowdown windows: during a window every action that *starts* inside it takes
  s(t) times longer (memory-bandwidth contention, thermal throttling, ...);
* OS noise: short intervals of stolen time (interrupts) that extend whichever action is
  running; present in every scenario, including the nominal one.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field

import numpy as np

SCENARIOS = ("none", "spike", "burst", "ramp")
_SCEN_ID = {"none": 0, "spike": 1, "burst": 2, "ramp": 3, "burst_harsh": 4, "ramp_harsh": 5}

# scenario -> (mean gap between windows [s], duration range [s], peak slowdown range, shape)
_PARAMS = {
    "spike": (1.2, (0.010, 0.040), (3.0, 8.0), "step"),
    "burst": (3.0, (0.30, 0.80), (1.2, 3.0), "step"),
    "ramp": (4.0, (1.5, 3.0), (1.3, 3.0), "tri"),
    # supplementary: only severe slowdowns (sensitivity of the conclusions to benign windows)
    "burst_harsh": (3.0, (0.30, 0.80), (2.0, 3.5), "step"),
    "ramp_harsh": (4.0, (1.5, 3.0), (2.0, 3.5), "tri"),
}
NOISE_RATE_HZ = 30.0


@dataclass
class Disturbance:
    starts: list = field(default_factory=list)   # window start (us)
    ends: list = field(default_factory=list)     # window end (us)
    peaks: list = field(default_factory=list)    # peak slowdown factor
    shape: str = "step"
    noise_t: list = field(default_factory=list)  # OS-noise instants (us)
    noise_d: list = field(default_factory=list)  # stolen time (us)

    def factor(self, t: int) -> float:
        """Slowdown factor s(t) >= 1."""
        i = bisect_right(self.starts, t) - 1
        if i < 0 or t >= self.ends[i]:
            return 1.0
        if self.shape == "step":
            return self.peaks[i]
        x = (t - self.starts[i]) / (self.ends[i] - self.starts[i])   # triangular profile
        prof = x / 0.7 if x < 0.7 else (1.0 - x) / 0.3
        return 1.0 + (self.peaks[i] - 1.0) * prof


def make_disturbance(scenario: str, seed: int, horizon_us: int, noise_scale: float = 1.0) -> Disturbance:
    D = Disturbance()
    # OS noise: Poisson in time, lognormal stolen time (median ~60 us, capped at 1 ms).
    # Its stream depends on the seed only, so every scenario of a seed shares the same noise
    # and the "none" scenario is the exact counterfactual for paired comparisons.
    rng_n = np.random.default_rng([seed, 77, 2])
    n = rng_n.poisson(NOISE_RATE_HZ * noise_scale * horizon_us * 1e-6)
    D.noise_t = sorted(rng_n.integers(0, horizon_us, n).tolist())
    D.noise_d = np.minimum(rng_n.lognormal(np.log(60.0), 0.8, n), 1000.0).astype(int).tolist()
    if scenario == "none":
        return D
    rng = np.random.default_rng([seed, _SCEN_ID[scenario], 2])
    gap, (dmin, dmax), (pmin, pmax), shape = _PARAMS[scenario]
    D.shape = shape
    t = rng.exponential(gap) + 0.5
    while t * 1e6 < horizon_us:
        dur = rng.uniform(dmin, dmax)
        D.starts.append(int(t * 1e6)); D.ends.append(int((t + dur) * 1e6))
        D.peaks.append(float(rng.uniform(pmin, pmax)))
        t += dur + 0.2 + rng.exponential(gap)            # >= 0.2 s between windows
    return D
