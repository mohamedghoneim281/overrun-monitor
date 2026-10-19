"""Exceedance-aware response-time analysis of the Rossl scheduler.

Implements Section II of Bedarkar, Ye, Garg, Dreyer, Brandenburg, "From Estimates to
Guarantees: Verified Exceedance Analysis on Commodity Hardware" (RTSS 2026):

  * overhead accounting      C_ovh(.), P_ovh(.), alpha_ovh(.)   (task set M)
  * blocking bound           B(tau)
  * workload bound           H(tau, Delta)
  * busy-window bound        L(e)  >=  e + B(tau) + H(tau, L(e))                (Eq. 2)
  * response-time bound      R(e) = max_{A in SP(e)} R_A(e)                     (Eq. 3)
  * tolerable exceedance     e_hat = max{ e | R(e) <= D }                       (Corollary 1)
  * recovery time and necessary slowdown (Sec. IV-C)

Everything is in integer microseconds; epsilon = 1 us is the smallest time unit.
This is a re-implementation of the *pen-and-paper* equations. It carries none of the
paper's machine-checked guarantees; tests/ and experiments.validate_* check it
against hand calculations, the paper's Table III, and a simulator.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Sequence

EPS = 1
INF = float("inf")


@dataclass(frozen=True)
class Task:
    """A callback chi_i: fixed priority (smaller = higher), NET, periodic arrivals."""
    name: str
    period: int          # minimum inter-arrival time (us)
    net: int             # NetCB_i (us): nominal execution time of the callback itself
    prio: int
    deadline: int
    jitter: int = 0      # release jitter used in the arrival curve (us)
    optional: bool = False


@dataclass(frozen=True)
class Overheads:
    """NETs of the scheduler's basic actions (Table I of the paper) and socket count s."""
    s: int
    net_fr: int      # failed read
    net_sr: int      # successful read
    net_sel: int     # job selection (successful or failed)
    net_disp: int    # dispatch
    net_compl: int   # completion
    net_idling: int  # idling


def ceil_div(a: int, b: int) -> int:
    return -((-a) // b)


def alpha_periodic(period: int, jitter: int = 0) -> Callable[[int], int]:
    """alpha(D) = ceil((D + J)/T) for D > 0, alpha(0) = 0."""
    def alpha(delta: int) -> int:
        return 0 if delta <= 0 else ceil_div(delta + jitter, period)
    return alpha


@dataclass(frozen=True)
class MTask:
    """An element of the overhead-aware task set M (a callback or a pseudo task)."""
    name: str
    prio: int                      # P_ovh
    cost: int                      # C_ovh
    alpha: Callable[[int], int]    # alpha_ovh
    kind: str                      # "callback" | "read" | "idle"
    period: int = 0
    jitter: int = 0
    deadline: int = 0


def inflated_cost(t: Task, o: Overheads) -> int:
    """C_ovh(tau_i^chi) = s*FR + Sel + Disp + CB + Compl + (s-1)*FR."""
    return o.s * o.net_fr + o.net_sel + o.net_disp + t.net + o.net_compl + (o.s - 1) * o.net_fr


def build_model(tasks: Sequence[Task], o: Overheads) -> list[MTask]:
    """Construct M = {tau_i^chi} U {tau_read, tau_idle} (Sec. II-A)."""
    alphas = [alpha_periodic(t.period, t.jitter) for t in tasks]
    M = [MTask(t.name, t.prio, inflated_cost(t, o), a, "callback", t.period, t.jitter, t.deadline)
         for t, a in zip(tasks, alphas)]
    M.append(MTask("read", 0, (2 * o.s - 1) * o.net_fr + o.net_sr,
                   lambda d, _a=tuple(alphas): sum(a(d) for a in _a), "read"))
    M.append(MTask("idle", 1 + max(t.prio for t in tasks),
                   (2 * o.s - 1) * o.net_fr + o.net_sel + o.net_idling,
                   lambda d: max(d, 0), "idle"))
    return M


def blocking(tau: MTask, M: Sequence[MTask]) -> int:
    """B(tau): largest inflated NET (minus eps) of any lower-priority task."""
    return max([m.cost - EPS for m in M if m.prio > tau.prio] + [0])


def workload(tau: MTask, M: Sequence[MTask], delta: int) -> int:
    """H(tau, Delta) = sum over hep(tau) of C_ovh * alpha_ovh(Delta)."""
    return sum(m.cost * m.alpha(delta) for m in M if m.prio <= tau.prio)


def busy_window(tau: MTask, M: Sequence[MTask], e: int, limit: int = 10**10) -> Optional[int]:
    """Least L > 0 with L >= e + B + H(L) (Eq. 2); None if none exists below `limit`."""
    B = blocking(tau, M)
    L = EPS
    while True:
        nxt = e + B + workload(tau, M, L)
        if nxt == L:
            return L
        if nxt > limit:
            return None
        L = nxt


def start_bound(tau: MTask, M: Sequence[MTask], e: int, A: int, limit: int = 10**10) -> Optional[int]:
    """Least F > A with F >= eps + B + H(F) - C + e (relative start time of the job)."""
    B = blocking(tau, M)
    F = A + EPS
    while True:
        nxt = max(A + EPS, EPS + B + workload(tau, M, F) - tau.cost + e)
        if nxt == F:
            return F
        if nxt > limit:
            return None
        F = nxt


def arrival_offsets(tau: MTask, L: int) -> list[int]:
    """SP(e) = {A | A < L and alpha(tau, A) != alpha(tau, A + eps)} for a periodic callback."""
    cands = {0}
    k = 1
    while k * tau.period - tau.jitter < L:
        cands.add(k * tau.period - tau.jitter)
        k += 1
    return sorted(A for A in cands if 0 <= A < L and tau.alpha(A) != tau.alpha(A + EPS))


def response_bound(tau: MTask, M: Sequence[MTask], e: int, limit: int = 10**10) -> float:
    """R(e) of Theorem 1 (Eq. 3); INF if the busy window is unbounded."""
    L = busy_window(tau, M, e, limit)
    if L is None:
        return INF
    best = 0
    for A in arrival_offsets(tau, L):
        F = start_bound(tau, M, e, A, limit)
        if F is None:
            return INF
        best = max(best, F + tau.cost - EPS - A)
    return best


def tolerable_exceedance(tau: MTask, M: Sequence[MTask], deadline: Optional[int] = None) -> Optional[int]:
    """TET e_hat = max{e | R(e) <= D} (Corollary 1). None if not schedulable at e = 0."""
    D = deadline if deadline is not None else tau.deadline
    if response_bound(tau, M, 0) > D:
        return None
    lo, hi = 0, D          # R(e) >= e + B + C, so R(D) > D
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if response_bound(tau, M, mid) <= D:
            lo = mid
        else:
            hi = mid
    return lo


def recovery_time(M: Sequence[MTask], e: int, lowest: Optional[MTask] = None) -> Optional[int]:
    """Busy-window bound L(e) of the lowest-priority callback: after this duration an
    exceedance of e has certainly dissipated and the system is quiet at every priority."""
    cbs = [m for m in M if m.kind == "callback"]
    lowest = lowest or max(cbs, key=lambda m: m.prio)
    return busy_window(lowest, M, e)


def necessary_slowdown(tau: MTask, M: Sequence[MTask], e_hat: int) -> float:
    """s_hat = 1 + e_hat / sum_{j in J} C_ovh (Sec. IV-C), J = hep jobs in the busy window
    of length L(e_hat) plus the largest potential blocker."""
    L = busy_window(tau, M, e_hat)
    if L is None:
        return INF
    hep = sum(m.cost * m.alpha(L) for m in M if m.prio <= tau.prio)
    blk = max([m.cost for m in M if m.prio > tau.prio] + [0])
    return 1.0 + e_hat / (hep + blk)


def robustness_table(tasks: Sequence[Task], o: Overheads) -> list[dict]:
    """Nominal response bound, TET, necessary slowdown and recovery time per callback."""
    M = build_model(tasks, o)
    rows = []
    for m in (x for x in M if x.kind == "callback"):
        tet = tolerable_exceedance(m, M)
        rows.append(dict(
            task=m.name, deadline_us=m.deadline, R0_us=response_bound(m, M, 0), tet_us=tet,
            slowdown=None if tet is None else necessary_slowdown(m, M, tet),
            recovery_us=None if tet is None else recovery_time(M, tet)))
    return rows


def unavoidable_slowdowns(tasks: Sequence[Task], o: Overheads, protected: int = 0) -> list[dict]:
    """Late-warning limit of any causal in-loop monitor.

    A nonpreemptive callback that has started cannot be stopped.  If a single job j of callback
    chi exhibits slowdown s (c(j) = s * C_ovh), it alone contributes (s-1)*C_ovh(chi) of
    exceedance before the monitor can even observe it.  Once that reaches the protected callback's
    TET e_hat, a deadline miss is *possible* whatever the monitor does afterwards.  Hence
        s*(chi) = 1 + e_hat_protected / C_ovh(chi)
    is the smallest single-job slowdown against which no in-loop monitor can offer a guarantee.
    (A monitor may still help when overruns are correlated across jobs, which is the case studied.)
    """
    M = build_model(tasks, o)
    prot = [m for m in M if m.kind == "callback"][protected]
    ehat = tolerable_exceedance(prot, M)
    rows = []
    for t, m in zip(tasks, [x for x in M if x.kind == "callback"]):
        if m is prot:
            continue
        rows.append(dict(protected=prot.name, culprit=t.name, optional=t.optional,
                         tet_protected_ms=ehat / 1000.0, culprit_inflated_cost_ms=m.cost / 1000.0,
                         single_job_slowdown_s_star=1.0 + ehat / m.cost))
    return rows
