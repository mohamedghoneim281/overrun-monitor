"""Grid-extension check.

The best configurations of both families in the main sweep sat on the edge of the grid
(overrun: gamma = 0.05, the smallest value; util: window 50 ms, hold 1000 ms).  Here the grids are
widened, operating points are re-selected on TRAIN seeds over the union of both grids, and only
the newly selected configurations are evaluated on TEST seeds.  If a widened grid changed the
conclusion, this is where it would show.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import analyze as an
from . import experiments as ex

GAMMAS = (0.01, 0.02, 0.05, 0.10, 0.20, 0.35, 0.50, 0.75, 1.00, 1.50, 2.50)
HOLDS = (30, 50, 100, 300, 1000, 2000)
WINDOWS = (20, 30, 50, 100, 200)


def extended_specs():
    have = {s.name for s in ex.policy_grid(False)}
    out = []
    for h in HOLDS:
        for w in WINDOWS:
            for th in ex.UTIL_THETAS:
                out.append(ex.Spec("util", th, w, h))
        for g in GAMMAS:
            out.append(ex.Spec("overrun", g, 0, h))
    return [s for s in out if s.name not in have]


def main(out: Path, train_seeds: int = 16, test_seeds: int = 40, dur: float = 40.0, jobs: int = 0) -> None:
    out = Path(out)
    specs = extended_specs()
    print(f"{len(specs)} extra configurations", flush=True)
    ex.run_grid(specs, ex.SCENARIOS, train_seeds, 0, str(out / "runs_ext_train.csv"), duration_s=dur, jobs=jobs)
    df = an.load([out / "runs.csv", out / "runs_ext_train.csv"])
    pool = an.pooled(df)
    ops = an.select_operating_points(pool[pool.split == "train"])
    grid = {s.name: s for s in ex.policy_grid(False) + specs}
    need = sorted({n for n in ops.values() if n} - {s.name for s in ex.policy_grid(False)})
    if need:
        print("evaluating newly selected configs on test seeds:", need, flush=True)
        ex.run_grid([grid[n] for n in need], ex.SCEN if hasattr(ex, "SCEN") else ex.SCENARIOS, 0, test_seeds,
                    str(out / "runs_ext_test.csv"), duration_s=dur, jobs=jobs)
    paths = [out / "runs.csv", out / "runs_ext_train.csv"] + ([out / "runs_ext_test.csv"] if need else [])
    df = an.load(paths)
    pool = an.pooled(df)
    train, test = pool[pool.split == "train"], pool[pool.split == "test"]
    rows, P = [], {}
    for (fam, tau), name in ops.items():
        if not name:
            continue
        t = test[test.policy == name].set_index("seed").sort_index()
        P[(fam, tau)] = t
        tr = train[train.policy == name]
        r = dict(family=fam, target_opt=tau, policy=name, from_extended_grid=name not in {s.name for s in ex.policy_grid(False)},
                 train_opt=tr.opt_frac.mean(), train_miss_pct=100 * tr.miss_rate.mean())
        for col, sc, nm in (("opt_frac", 1, "opt"), ("miss_rate", 100, "miss_pct")):
            m, lo, hi = an.ci(sc * t[col].to_numpy()); r.update({f"test_{nm}": m, f"test_{nm}_lo": lo, f"test_{nm}_hi": hi})
        rows.append(r)
    pd.DataFrame(rows).to_csv(out / "table_extended_selection.csv", index=False)
    drows = []
    for tau in an.TARGETS:
        if ("util", tau) in P and ("overrun", tau) in P:
            u, o = P[("util", tau)], P[("overrun", tau)]
            d = an.ci_diff(100 * o.miss_rate.to_numpy(), 100 * u.miss_rate.to_numpy())
            do = an.ci_diff(o.opt_frac.to_numpy(), u.opt_frac.to_numpy())
            drows.append(dict(target_opt=tau, d_miss_pct=d[0], lo=d[1], hi=d[2], d_opt=do[0], d_opt_lo=do[1], d_opt_hi=do[2]))
    pd.DataFrame(drows).to_csv(out / "table_extended_paired_difference.csv", index=False)
    print(pd.DataFrame(rows).round(3).to_string()); print(pd.DataFrame(drows).round(3).to_string())
