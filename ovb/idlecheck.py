"""Does fast-forwarding idle iterations change the simulation results?

The simulator skips whole idle polling iterations while nothing is pending (otherwise a 40 s run would
need millions of idle actions).  Here a sample of runs is simulated with and without the skip and the
metrics are compared.  Skipped intervals do not process OS-noise events or monitor updates individually,
so exact equality is not guaranteed; this quantifies the difference.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from . import experiments as ex
from .sim import simulate

POLICIES = ["none", "util_t1.00_w50_h1000", "utilx_t0.90_w100_h300", "overrun_g0.05_h100"]


def main(out: Path, seeds: int = 6, duration_s: float = 10.0) -> pd.DataFrame:
    grid = {s.name: s for s in ex.policy_grid(False) + ex.resp_specs() + ex.utilx_specs()}
    ehat = ex.ehat_ctrl()
    rows = []
    for pol in POLICIES:
        for scen in ex.SCENARIOS:
            for seed in range(seeds):
                kw = dict(duration_us=int(duration_s * 1e6))
                a = simulate(grid[pol].factory(ehat), scen, seed, fast_idle=True, **kw).metrics
                b = simulate(grid[pol].factory(ehat), scen, seed, fast_idle=False, **kw).metrics
                rows.append(dict(policy=pol, scenario=scen, seed=seed,
                                 d_miss=abs(a["miss_rate"] - b["miss_rate"]), d_opt=abs(a["opt_frac"] - b["opt_frac"]),
                                 d_rms=abs(a["rms_err"] - b["rms_err"]), miss_fast=a["miss_rate"], miss_full=b["miss_rate"],
                                 opt_fast=a["opt_frac"], opt_full=b["opt_frac"]))
    df = pd.DataFrame(rows)
    Path(out).mkdir(exist_ok=True)
    df.to_csv(Path(out) / "idle_equivalence.csv", index=False)
    print(f"{len(df)} paired runs: identical miss rate in {(df.d_miss == 0).mean():.1%}, optional work in "
          f"{(df.d_opt == 0).mean():.1%}; max |d miss| = {df.d_miss.max():.4f}, max |d opt| = {df.d_opt.max():.4f}, "
          f"max |d rms| = {df.d_rms.max():.2e}")
    print("mean miss rate fast / full:", round(df.miss_fast.mean() * 100, 4), "/", round(df.miss_full.mean() * 100, 4), "%")
    return df
