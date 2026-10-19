"""Discrete-event simulation of a Rossl-style nonpreemptive fixed-priority scheduler.

The loop mirrors Fig. 1 of the RTSS 2026 paper.  Each iteration:
  polling phase  - read sockets round-robin until a read has failed on every socket;
  selection      - pick the highest-priority pending job (FIFO per callback);
  idle           - if nothing is pending: IDLING action, then poll again;
  dispatch/exec/completion - run the callback to completion (no preemption).
A message is readable at time t iff it arrived at or before the start of the read.
Response time = completion marker - message arrival (as in the paper).

The motor plant is co-simulated: the control callback samples the encoder at message
arrival and applies its output when the callback completes.

Two modes
  stochastic  (experiments)  jobs run in U(0.6, 1.0) * NET, basic actions at 0.8 * NET,
                             plus slowdown windows and OS noise from `disturbances`.
  worst_case  (validation)   every action takes exactly its NET, no disturbance;
                             extra time is injected per job via `injections`.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from .disturbances import Disturbance, make_disturbance
from .monitors import Monitor
from .plant import Plant
from .taskset import CTRL, DROP_NET, OVERHEADS, TASKS


@dataclass
class RunResult:
    metrics: dict
    trace: Optional[dict] = None
    logs: Optional[dict] = None     # raw per-task arrivals / dispatch / completion times (us)


def longest_run(flags: np.ndarray) -> int:
    best = cur = 0
    for f in flags:
        cur = cur + 1 if f else 0
        best = max(best, cur)
    return best


def simulate(make_monitor: Callable[[Disturbance], Monitor], scenario: str, seed: int, *,
             duration_us: int = 40_000_000, warmup_us: int = 500_000,
             tasks=TASKS, ovh=OVERHEADS, worst_case: bool = False,
             injections: Optional[dict] = None, phases: Optional[list] = None,
             with_plant: bool = True, trace: bool = False, keep_resp: bool = False,
             light: bool = False, noise_scale: float = 1.0) -> RunResult:
    n = len(tasks)
    s = ovh.s
    assert s == n, "one socket per callback"
    T_end = duration_us + 1_000_000
    dist = Disturbance() if worst_case else make_disturbance(scenario, seed, T_end, noise_scale)
    mon = make_monitor(dist)

    # ---- pre-generated randomness (identical across policies for the same seed) -------
    rng_ph = np.random.default_rng([seed, 3])
    phs = phases if phases is not None else [int(rng_ph.integers(0, t.period)) for t in tasks]
    arr = [list(range(phs[i], T_end, tasks[i].period)) for i in range(n)]
    rng_ex = np.random.default_rng([seed, 1])
    cexec = []
    for i, t in enumerate(tasks):
        m = len(arr[i])
        if worst_case:
            c = [t.net] * m
        else:
            c = np.maximum(1, np.rint(t.net * rng_ex.uniform(0.6, 1.0, m))).astype(int).tolist()
        cexec.append(c)
    if injections:
        for (i, k), x in injections.items():
            if k < len(cexec[i]):
                cexec[i][k] += int(x)

    def basic(net: int) -> int:
        return net if worst_case else max(1, round(0.8 * net))
    b_fr, b_sr, b_sel = basic(ovh.net_fr), basic(ovh.net_sr), basic(ovh.net_sel)
    b_disp, b_compl, b_idle = basic(ovh.net_disp), basic(ovh.net_compl), basic(ovh.net_idling)
    mcost = mon.cost
    n_fr, n_sr, n_sel = ovh.net_fr, ovh.net_sr, ovh.net_sel
    n_disp, n_compl, n_idle = ovh.net_disp + mcost, ovh.net_compl, ovh.net_idling
    nets = [t.net for t in tasks]
    optional = [t.optional for t in tasks]
    order = sorted(range(n), key=lambda i: tasks[i].prio)

    # ---- plant -------------------------------------------------------------------------
    P = Plant(T_end, warmup_us, duration_us, trace=trace) if with_plant else None
    ys: list = []
    samp = 0
    arr_c = arr[CTRL]

    # ---- time with disturbances -----------------------------------------------------------
    nt, nd, nlen = dist.noise_t, dist.noise_d, len(dist.noise_t)
    np_ = 0
    factor = dist.factor
    has_win = bool(dist.starts)

    def act(t: int, d0: int) -> int:
        nonlocal np_
        d = d0
        if has_win:
            f = factor(t)
            if f != 1.0:
                d = int(d0 * f + 0.5)
        if nlen:
            while np_ < nlen and nt[np_] < t:
                np_ += 1
            end = t + d
            while np_ < nlen and nt[np_] < end:
                d += nd[np_]; end += nd[np_]; np_ += 1
        return d

    # ---- logs -------------------------------------------------------------------------------
    disp_t = [[] for _ in range(n)]
    end_t = [[] for _ in range(n)]
    dropped = [[] for _ in range(n)]
    ready = [deque() for _ in range(n)]
    nread = [0] * n
    on_action = mon.on_action
    on_done = mon.on_job_done
    sock = 0
    t = 0
    n_disp_total = 0

    while t < T_end:
        it0 = t
        # --- polling phase -------------------------------------------------------------
        fails = 0
        while fails < s:
            i = sock
            sock = sock + 1 if sock + 1 < s else 0
            k = nread[i]
            a = arr[i]
            if k < len(a) and a[k] <= t:
                d = act(t, b_sr); t += d; on_action(t, d, n_sr, True)
                ready[i].append(k); nread[i] = k + 1; fails = 0
            else:
                d = act(t, b_fr); t += d; on_action(t, d, n_fr, False); fails += 1
        # --- selection -----------------------------------------------------------------
        d = act(t, b_sel); t += d
        pick = -1
        for i in order:
            if ready[i]:
                pick = i
                break
        if pick < 0:
            on_action(t, d, n_sel, False)
            d = act(t, b_idle); t += d; on_action(t, d, n_idle, False)
            mon.on_quiet(t)
            # fast-forward whole idle iterations until shortly before the next arrival
            ta = T_end
            for i in range(n):
                if nread[i] < len(arr[i]) and arr[i][nread[i]] < ta:
                    ta = arr[i][nread[i]]
            di = t - it0
            if di > 0 and ta - t > 4 * di:
                t += ((ta - t) // di - 2) * di
            continue
        on_action(t, d, n_sel, True)
        # --- dispatch, execute, complete --------------------------------------------------
        k = ready[pick].popleft()
        shed = optional[pick] and mon.shed(t, cexec[pick][k], nets[pick])
        t_disp = t
        d = act(t, b_disp + mcost); t += d; on_action(t, d, n_disp, True)
        n_disp_total += 1
        if shed:
            d = act(t, DROP_NET); t += d; on_action(t, d, DROP_NET, True)
        else:
            d = act(t, cexec[pick][k]); t += d; on_action(t, d, nets[pick], True)
        d = act(t, b_compl); t += d; on_action(t, d, n_compl, True)
        disp_t[pick].append(t_disp); end_t[pick].append(t); dropped[pick].append(shed)
        on_done(pick, t - arr[pick][k], t)
        if P is not None and pick == CTRL:
            while samp < len(arr_c) and arr_c[samp] <= t:
                ys.append(P.sample(arr_c[samp])); samp += 1
            y, r = ys[k]
            P.actuate(t, r - y)

    logs = dict(arr=arr, disp=disp_t, end=end_t, dropped=dropped)
    if light:
        return RunResult({}, None, logs)

    # ---- metrics ----------------------------------------------------------------------------------
    M: dict = {}
    if P is not None:
        P.advance(duration_us)
        M["rms_err"] = P.rms_error()

    def eval_arrays(i):
        a = np.array(arr[i])
        e = np.full(len(a), T_end, dtype=np.int64)
        c = len(end_t[i])
        e[:c] = end_t[i]
        mask = (a >= warmup_us) & (a < duration_us)
        return a, e, mask, c

    a_c, e_c, mask_c, done_c = eval_arrays(CTRL)
    resp_c = e_c - a_c
    D_c = tasks[CTRL].deadline
    miss_c = (resp_c > D_c) & mask_c
    M["miss_rate"] = float(miss_c.sum() / mask_c.sum())
    M["miss_run"] = longest_run(miss_c[mask_c])
    rc = resp_c[mask_c] / 1000.0
    M["resp_mean_ms"] = float(rc.mean()); M["resp_p99_ms"] = float(np.percentile(rc, 99))
    M["resp_max_ms"] = float(rc.max())

    opt_done = opt_total = 0.0
    sens_miss = 0.0
    for i in range(n):
        a, e, mask, c = eval_arrays(i)
        if optional[i]:
            comp = np.zeros(len(a), dtype=bool)
            comp[:c] = ~np.array(dropped[i], dtype=bool)
            opt_done += nets[i] * float((comp & mask).sum())
            opt_total += nets[i] * float(mask.sum())
        elif i != CTRL:
            sens_miss = float((((e - a) > tasks[i].deadline) & mask).sum() / mask.sum())
    M["opt_frac"] = opt_done / opt_total if opt_total else 1.0
    M["nec_miss_rate"] = sens_miss
    M["mon_overhead"] = mcost * n_disp_total / T_end

    # miss attribution: what was executing when the missed control message arrived?
    all_s = np.array([x for i in range(n) for x in disp_t[i]])
    all_e = np.array([x for i in range(n) for x in end_t[i]])
    all_i = np.array([i for i in range(n) for _ in disp_t[i]])
    all_x = np.array([d for i in range(n) for d in dropped[i]], dtype=bool)
    srt = np.argsort(all_s); all_s, all_e, all_i, all_x = all_s[srt], all_e[srt], all_i[srt], all_x[srt]
    late = nec = other = 0
    for j in np.flatnonzero(miss_c):
        q = np.searchsorted(all_s, a_c[j], "right") - 1
        if q >= 0 and all_e[q] > a_c[j] and all_i[q] != CTRL:
            if optional[all_i[q]] and not all_x[q]:
                late += 1          # blocked by an optional job dispatched before any warning
            else:
                nec += 1           # blocked by necessary work (or a shed stub)
        else:
            other += 1             # backlog / polling lag, no blocker at arrival
    M["miss_late_warning"], M["miss_necessary_blocker"], M["miss_backlog"] = late, nec, other

    # detection quality against the ground-truth slowdown windows
    eps_ = mon.episodes
    lat, det = [], 0
    for ws, we in zip(dist.starts, dist.ends):
        if dist.peaks[dist.starts.index(ws)] <= 1.0:
            continue
        hit = None
        for es, ee in eps_:
            if es <= ws < ee:
                hit = 0; break
            if ws < es <= we:
                hit = es - ws; break
        if hit is not None:
            det += 1; lat.append(hit / 1000.0)
    M["windows"] = len(dist.starts)
    M["windows_detected"] = det
    M["detect_latency_ms"] = float(np.mean(lat)) if lat else float("nan")
    false = 0
    for es, ee in eps_:
        if not any(ws <= es <= we + 1_000_000 for ws, we in zip(dist.starts, dist.ends)):
            false += 1
    M["false_triggers_per_min"] = false / (duration_us / 6e7)
    M["shed_time_frac"] = float(sum(min(ee, duration_us) - min(es, duration_us) for es, ee in eps_)
                                / duration_us) if mon.name not in ("always",) else 1.0

    tr = None
    if trace:
        tr = dict(ctrl_arr=a_c, ctrl_end=e_c, windows=list(zip(dist.starts, dist.ends, dist.peaks)),
                  episodes=eps_, plant=P.trace if P else None, dist=dist)
    return RunResult(M, tr, logs if keep_resp else None)
