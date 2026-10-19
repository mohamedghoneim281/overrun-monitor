"""Policy sweeps: every (policy configuration) x (scenario) x (seed) is one simulation.

Seeds are split into disjoint TRAIN seeds (used only to pick operating points) and TEST seeds
(used for every reported number).  For a given seed all policies see exactly the same message
arrivals, execution-time draws, slowdown windows and OS noise (common random numbers).
Results are appended to a CSV so a long run can be interrupted and resumed.
"""
from __future__ import annotations

import csv
import multiprocessing as mp
import os
import time
from dataclasses import dataclass
from pathlib import Path

from .analysis import build_model, tolerable_exceedance
from .monitors import AlwaysShed, NoMonitor, Oracle, OverrunMonitor, RespMonitor, UtilMonitor
from .sim import simulate
from .taskset import CTRL, OVERHEADS, TASKS

SCENARIOS = ("none", "spike", "burst", "ramp")
TRAIN_SEED0, TEST_SEED0 = 0, 1000           # disjoint seed ranges

UTIL_THETAS = (0.50, 0.55, 0.60, 0.70, 0.80, 0.90, 1.00, 1.20)
UTIL_WINDOWS_MS = (50, 100, 200)
OVERRUN_GAMMAS = (0.05, 0.10, 0.20, 0.35, 0.50, 0.75, 1.00, 1.50, 2.50)
HOLDS_MS = (100, 300, 1000)
RESP_PHIS = (0.25, 0.35, 0.50, 0.65, 0.80, 1.00, 1.25)   # fraction of the control deadline


def ehat_ctrl() -> int:
    """Analytic tolerable exceedance of the control callback with all work enabled (us)."""
    M = build_model(TASKS, OVERHEADS)
    return tolerable_exceedance(M[CTRL], M)


@dataclass(frozen=True)
class Spec:
    family: str            # none | always | oracle | util | overrun
    theta: float = 0.0     # util: threshold on utilisation; overrun: gamma (fraction of e_hat)
    window_ms: int = 0     # util only
    hold_ms: int = 0

    @property
    def name(self) -> str:
        if self.family in ("none", "always", "oracle"):
            return self.family
        if self.family == "util":
            return f"util_t{self.theta:.2f}_w{self.window_ms}_h{self.hold_ms}"
        if self.family == "resp":
            return f"resp_p{self.theta:.2f}_h{self.hold_ms}"
        return f"overrun_g{self.theta:.2f}_h{self.hold_ms}"

    def factory(self, ehat: int):
        if self.family == "none":
            return lambda d: NoMonitor()
        if self.family == "always":
            return lambda d: AlwaysShed()
        if self.family == "oracle":
            return lambda d: Oracle(d)
        if self.family == "util":
            return lambda d: UtilMonitor(self.theta, self.window_ms * 1000, self.hold_ms * 1000)
        if self.family == "resp":
            return lambda d: RespMonitor(self.theta, TASKS[CTRL].deadline, self.hold_ms * 1000, CTRL)
        return lambda d: OverrunMonitor(self.theta * ehat, self.hold_ms * 1000)


def resp_specs() -> list[Spec]:
    return [Spec("resp", p, 0, h) for h in HOLDS_MS for p in RESP_PHIS]


def policy_grid(small: bool = False) -> list[Spec]:
    specs = [Spec("none"), Spec("always"), Spec("oracle")]
    ut, uw, og, hh = UTIL_THETAS, UTIL_WINDOWS_MS, OVERRUN_GAMMAS, HOLDS_MS
    if small:
        ut, uw, og, hh = (0.6, 0.9), (100,), (0.1, 0.5, 1.5), (300,)
    for h in hh:
        for w in uw:
            for th in ut:
                specs.append(Spec("util", th, w, h))
        for g in og:
            specs.append(Spec("overrun", g, 0, h))
    return specs


FIELDS = ["policy", "family", "theta", "window_ms", "hold_ms", "scenario", "seed", "split",
          "rms_err", "miss_rate", "miss_run", "resp_mean_ms", "resp_p99_ms", "resp_max_ms",
          "opt_frac", "nec_miss_rate", "mon_overhead", "miss_late_warning",
          "miss_necessary_blocker", "miss_backlog", "windows", "windows_detected",
          "detect_latency_ms", "false_triggers_per_min", "shed_time_frac"]


def _work(job):
    spec, scenario, seeds, duration_us, ehat = job
    fac = spec.factory(ehat)
    rows = []
    for seed, split in seeds:
        m = simulate(fac, scenario, seed, duration_us=duration_us).metrics
        row = dict(policy=spec.name, family=spec.family, theta=spec.theta, window_ms=spec.window_ms,
                   hold_ms=spec.hold_ms, scenario=scenario, seed=seed, split=split)
        row.update({k: m[k] for k in FIELDS if k in m})
        rows.append(row)
    return rows


def run_grid(specs, scenarios, train_seeds: int, test_seeds: int, out_csv: str,
             duration_s: float = 40.0, jobs: int = 0, verbose: bool = True) -> None:
    out = Path(out_csv)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        with open(out) as f:
            for r in csv.DictReader(f):
                done.add((r["policy"], r["scenario"], int(r["seed"])))
    seeds = [(TRAIN_SEED0 + i, "train") for i in range(train_seeds)] + \
            [(TEST_SEED0 + i, "test") for i in range(test_seeds)]
    ehat = ehat_ctrl()
    work = []
    for sp in specs:
        for sc in scenarios:
            todo = [(s, sp_) for s, sp_ in seeds if (sp.name, sc, s) not in done]
            if todo:
                work.append((sp, sc, todo, int(duration_s * 1e6), ehat))
    if verbose:
        print(f"{len(work)} work items ({sum(len(w[2]) for w in work)} runs) to do; "
              f"{len(done)} runs already on disk")
    new = not out.exists() or out.stat().st_size == 0
    jobs = jobs or os.cpu_count() or 1
    t0 = time.time()
    with open(out, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        it = map(_work, work) if jobs == 1 else mp.Pool(jobs).imap_unordered(_work, work)
        for n, rows in enumerate(it, 1):
            w.writerows(rows); f.flush()
            if verbose and (n % 10 == 0 or n == len(work)):
                el = time.time() - t0
                print(f"  {n}/{len(work)} items  elapsed {el/60:.1f} min  eta {(el/n)*(len(work)-n)/60:.1f} min", flush=True)
