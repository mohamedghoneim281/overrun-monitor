"""Runtime policies that decide whether an *optional* callback is executed or discarded.

All monitors run inside the scheduler loop (as a single-threaded Rossl-style executor would
have to): they observe the duration of every basic action and callback as it completes
(timestamps at marker functions) and are consulted when an optional job is about to be
dispatched.  A causal monitor can never inspect the future execution time of a job that
has already started: a nonpreemptive callback that is running cannot be shed.

  none      never sheds (baseline)
  always    always sheds optional work (lower bound on timing misses / optional work)
  oracle    clairvoyant diagnostic: sheds a job iff it would exceed its NET (not causal)
  util      sheds while windowed CPU utilisation exceeds a threshold      (comparison)
  resp      symptom monitor: sheds once a control job's response time exceeds phi * deadline
  overrun   sheds once the exceedance accumulated since the last quiet instant reaches a
            budget  theta_e = gamma * e_hat_ctrl, where e_hat_ctrl is the analytic tolerable
            exceedance of the control callback (Corollary 1)               (proposed)

util and overrun share the same hold time: once triggered, shedding stays on for `hold_us`
(and is extended by every further trigger), so differences come from the warning *signal*.
"""
from __future__ import annotations

from collections import deque

from .taskset import MONITOR_COST


class Monitor:
    name = "none"
    cost = 0          # extra microseconds added to every dispatch for running the monitor

    def __init__(self):
        self.active_until = -1
        self.episodes: list[list[int]] = []   # [start, end] of each continuous shedding episode

    # -- hooks called by the simulator -------------------------------------------------
    def on_action(self, t_end: int, dur: int, net: int, busy: bool) -> None:
        """An action (basic action or callback) of duration `dur` ended at `t_end`."""

    def on_quiet(self, t: int) -> None:
        """The scheduler found no pending job at any priority (an idling phase ended)."""

    def on_job_done(self, task: int, resp_us: int, t: int) -> None:
        """Callback `task` completed at time t with response time resp_us."""

    def shed(self, t: int, nominal_us: int, net_us: int) -> bool:
        """Decision for an optional job about to be dispatched at time t.
        `nominal_us` is the job's undisturbed execution time, `net_us` its NET; only the
        (non-causal) oracle may use them."""
        return False

    def active(self, t: int) -> bool:
        return t < self.active_until

    def _trigger(self, t: int, hold: int) -> None:
        if t >= self.active_until:
            self.episodes.append([t, t + hold])
        else:
            self.episodes[-1][1] = max(self.episodes[-1][1], t + hold)
        self.active_until = max(self.active_until, t + hold)


class NoMonitor(Monitor):
    name = "none"


class AlwaysShed(Monitor):
    name = "always"

    def shed(self, t, nominal_us, net_us):
        return True

    def active(self, t):
        return True


class Oracle(Monitor):
    """Clairvoyant diagnostic (not causal): sheds an optional job iff running it now would
    make it exceed its NET under the slowdown that will be in force when it starts."""
    name = "oracle"

    def __init__(self, dist):
        super().__init__()
        self.dist = dist

    def shed(self, t, nominal_us, net_us):
        hit = nominal_us * self.dist.factor(t) > net_us
        if hit:
            self._trigger(t, 1)
        return hit

    def active(self, t):
        return t < self.active_until


class UtilMonitor(Monitor):
    name = "util"
    cost = MONITOR_COST

    def __init__(self, theta: float, window_us: int = 100_000, hold_us: int = 500_000):
        super().__init__()
        self.limit = theta * window_us
        self.window = window_us
        self.hold = hold_us
        self.q: deque = deque()
        self.sum = 0

    def on_action(self, t_end, dur, net, busy):
        if not busy:
            return
        q = self.q
        q.append((t_end, dur)); self.sum += dur
        lim = t_end - self.window
        while q and q[0][0] <= lim:
            self.sum -= q.popleft()[1]
        if self.sum > self.limit:
            self._trigger(t_end, self.hold)

    def shed(self, t, nominal_us, net_us):
        return t < self.active_until


class OverrunMonitor(Monitor):
    name = "overrun"
    cost = MONITOR_COST

    def __init__(self, theta_us: float, hold_us: int = 500_000):
        super().__init__()
        self.theta = theta_us
        self.hold = hold_us
        self.e = 0.0           # exceedance observed since the last quiet instant

    def on_action(self, t_end, dur, net, busy):
        if dur > net:
            self.e += dur - net
            if self.e >= self.theta:
                self._trigger(t_end, self.hold)

    def on_quiet(self, t):
        self.e = 0.0

    def shed(self, t, nominal_us, net_us):
        return t < self.active_until


class RespMonitor(Monitor):
    """Symptom-based baseline: react when the protected (control) job was already late-ish."""
    name = "resp"
    cost = MONITOR_COST

    def __init__(self, phi: float, deadline_us: int, hold_us: int = 500_000, task: int = 0):
        super().__init__()
        self.limit = phi * deadline_us
        self.hold = hold_us
        self.task = task

    def on_job_done(self, task, resp_us, t):
        if task == self.task and resp_us > self.limit:
            self._trigger(t, self.hold)

    def shed(self, t, nominal_us, net_us):
        return t < self.active_until
