"""Validation of the analysis implementation (step 2 of the study).

  1. reproduce the paper's Table III (TET, recovery) from its published workload;
  2. adversarial soundness check: simulate with worst-case-style behaviour (every action
     at exactly its NET) plus a total exceedance of E injected where it hurts most, and
     check that no observed response time exceeds R(E) from Theorem 1.
"""
from __future__ import annotations

import numpy as np

from .analysis import (Overheads, Task, build_model, recovery_time, response_bound,
                       tolerable_exceedance)
from .monitors import NoMonitor
from .sim import simulate
from .taskset import OVERHEADS, TASKS

MS = 1000

PAPER_TABLE_III = [  # period ms, NetCB ms, TET ms, recovery ms (Bedarkar et al. 2026, Table III)
    ("MATRIX_120", 125, 7.27, 97.85, 352.40), ("MATRIX_128", 100, 10.47, 62.39, 198.59),
    ("MATRIX_136", 125, 10.55, 66.36, 295.55), ("MATRIX_144", 200, 12.56, 110.95, 365.51),
    ("MATRIX_152", 100, 14.88, 24.39, 160.59), ("MATRIX_160", 200, 17.38, 63.80, 293.00),
    ("MATRIX_168", 200, 19.83, 63.80, 292.99)]


def reproduce_table3() -> list[dict]:
    """Our TET / recovery on the paper's workload with scheduler overheads set to zero
    (the paper omits the basic-action NETs, so small deviations are expected)."""
    tasks = [Task(n, int(p * MS), int(round(c * MS)), i + 1, int(p * MS))
             for i, (n, p, c, _, _) in enumerate(PAPER_TABLE_III)]
    M = build_model(tasks, Overheads(1, 0, 0, 0, 0, 0, 0))
    rows = []
    for (n, _, _, tet_p, rec_p), m in zip(PAPER_TABLE_III, [x for x in M if x.kind == "callback"]):
        tet = tolerable_exceedance(m, M)
        rec = recovery_time(M, tet)
        rec_up = recovery_time(M, tet + 1)      # the recovery bound can sit on an arrival-curve step
        rows.append(dict(task=n, tet_paper_ms=tet_p, tet_ours_ms=tet / MS,
                         recovery_paper_ms=rec_p, recovery_ours_ms=rec / MS,
                         recovery_ours_at_tet_plus_1us_ms=rec_up / MS))
    return rows


def _adversarial_injection(rng, logs, victim: int, E: int, tasks) -> dict:
    """Split E over 1-3 jobs that execute shortly before/while a random job of `victim` runs."""
    arr, disp, end = logs["arr"], logs["disp"], logs["end"]
    ks = [k for k in range(len(end[victim])) if 1_000_000 <= arr[victim][k] <= 2_500_000]
    if not ks:
        return {}
    kv = int(rng.choice(ks))
    lo, hi = arr[victim][kv] - 120_000, end[victim][kv]
    cand = [(i, k) for i in range(len(tasks)) for k in range(len(disp[i])) if lo <= disp[i][k] <= hi]
    if not cand:
        return {}
    m = int(rng.integers(1, 4))
    pick = [cand[j] for j in rng.choice(len(cand), size=min(m, len(cand)), replace=False)]
    w = rng.dirichlet(np.ones(len(pick)))
    parts = np.floor(w * E).astype(int)
    parts[0] += E - parts.sum()
    return {p: int(x) for p, x in zip(pick, parts) if x > 0}


def soundness(trials: int = 60, exceedances_ms=(0, 2, 4, 6, 10, 15, 25), seed: int = 0,
              tasks=TASKS, ovh=OVERHEADS, horizon_us: int = 3_500_000) -> list[dict]:
    """For each callback and total exceedance E: max observed response time over `trials`
    randomised adversarial runs vs the bound R(E)."""
    rng = np.random.default_rng(seed)
    M = build_model(tasks, ovh)
    cb = [m for m in M if m.kind == "callback"]
    rows = []
    for vi, tau in enumerate(cb):
        for E_ms in exceedances_ms:
            E = int(E_ms * MS)
            bound = response_bound(tau, M, E)
            worst, violations = 0, 0
            for tr in range(trials):
                if rng.random() < 0.5:                      # near-synchronous releases
                    phases = [int(rng.integers(0, 400)) for _ in tasks]
                else:
                    phases = [int(rng.integers(0, t.period)) for t in tasks]
                base = simulate(lambda d: NoMonitor(), "none", 0, worst_case=True, phases=phases,
                                with_plant=False, duration_us=horizon_us, light=True)
                inj = _adversarial_injection(rng, base.logs, vi, E, tasks) if E else {}
                run = simulate(lambda d: NoMonitor(), "none", 0, worst_case=True, phases=phases,
                               injections=inj, with_plant=False, duration_us=horizon_us, light=True)
                a = np.array(run.logs["arr"][vi][:len(run.logs["end"][vi])])
                r = np.array(run.logs["end"][vi]) - a
                if len(r):
                    worst = max(worst, int(r.max()))
                    violations += int((r > bound).sum())
            rows.append(dict(task=tau.name, E_ms=E_ms, bound_ms=bound / MS, observed_max_ms=worst / MS,
                             ratio=worst / bound, violations=violations))
    return rows
