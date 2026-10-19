"""Sensitivity of the selected operating points to the OS-noise level (not re-tuned).

The best overrun-budget settings use thresholds near the noise floor, so the obvious threat is that
they only look good for the noise model assumed in the main sweep.  Here the OS-noise rate is
multiplied by 1x, 3x, 10x (30, 90, 300 events/s) and the configurations selected on TRAIN seeds for
the 1x world are re-evaluated unchanged on TEST seeds.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from . import experiments as ex
from .sim import simulate

SCALES = (1.0, 3.0, 10.0)


def run(out: Path, names: list[str], seeds: int = 40, dur: float = 40.0) -> pd.DataFrame:
    path = Path(out) / "runs_noise.csv"
    if path.exists():
        return pd.read_csv(path)
    from .extend import extended_specs          # lazy: extend imports analyze imports robustness
    grid = {s.name: s for s in ex.policy_grid(False) + ex.resp_specs() + extended_specs()}
    ehat = ex.ehat_ctrl()
    rows = []
    for sc in SCALES:
        for n in names:
            fac = grid[n].factory(ehat)
            for scen in ex.SCENARIOS:
                for s in range(ex.TEST_SEED0, ex.TEST_SEED0 + seeds):
                    m = simulate(fac, scen, s, duration_us=int(dur * 1e6), noise_scale=sc, with_plant=False).metrics
                    rows.append(dict(noise_scale=sc, policy=n, scenario=scen, seed=s, miss_rate=m["miss_rate"],
                                     opt_frac=m["opt_frac"], false_triggers_per_min=m["false_triggers_per_min"]))
    df = pd.DataFrame(rows)
    df.to_csv(path, index=False)
    return df


def table(df: pd.DataFrame) -> pd.DataFrame:
    p = df.groupby(["noise_scale", "policy", "seed"], as_index=False)[["miss_rate", "opt_frac"]].mean()
    g = p.groupby(["noise_scale", "policy"])[["miss_rate", "opt_frac"]].mean().reset_index()
    g["miss_pct"] = 100 * g.miss_rate
    f = df[df.scenario == "none"].groupby(["noise_scale", "policy"]).false_triggers_per_min.mean().reset_index()
    return g.merge(f, on=["noise_scale", "policy"])[["noise_scale", "policy", "miss_pct", "opt_frac", "false_triggers_per_min"]]
