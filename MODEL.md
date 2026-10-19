# Model and design decisions (read this before you defend the work)

Every choice below is a *modelling decision*, not a fact about the world. Where a result could depend
on one, the last column says how the study checks or exposes that.

## 1. The analysed system (from the RTSS 2026 paper, Sec. II)

| Paper | Code | Notes |
|---|---|---|
| Rossl: single-threaded, nonpreemptive, fixed-priority, polling loop (Fig. 1) | `sim.py` main loop | polling -> selection -> (idle or) dispatch -> execute -> completion |
| basic actions + NETs (Table I) | `taskset.OVERHEADS`, `sim.py` | values are **illustrative** (us), not measured on a Raspberry Pi |
| overhead-aware task set M, `C_ovh`, `tau_read`, `tau_idle` | `analysis.build_model` | `C_ovh = s*FR + Sel + Disp + CB + Compl + (s-1)*FR` |
| blocking `B(tau)`, workload `H(tau, D)` | `analysis.blocking / workload` | |
| busy window `L(e)` (Eq. 2), `R_A(e)`, `R(e)` (Eq. 3) | `analysis.busy_window / start_bound / response_bound` | integer-us fixed points |
| tolerable exceedance threshold (Cor. 1) | `analysis.tolerable_exceedance` | binary search; `R(e)` is monotone in `e` |
| recovery time, necessary slowdown (Sec. IV-C) | `analysis.recovery_time / necessary_slowdown` | |

`analysis.py` re-implements pen-and-paper equations. It is validated three ways (none is a proof):
hand-calculable examples (`tests/test_analysis.py`), the paper's Table III (TETs within 0.1 ms and 6 of 7
recovery times within 0.1 ms; the 7th sits on an arrival-curve step, see the test), and an adversarial
simulation that never exceeds `R(E)` (`validate.soundness`, `figures/fig5_validation.png`).

## 2. The application

| Callback | Period = deadline | NET | Priority | Role |
|---|---|---|---|---|
| `ctrl` | 20 ms | 1.5 ms | 1 | PI motor-speed loop (necessary) |
| `sens` | 40 ms | 4 ms | 2 | sensor processing (necessary) |
| `tele` | 50 ms | 8 ms | 3 | telemetry (optional) |
| `viz` | 100 ms | 12 ms | 4 | visualisation (optional) |

Nominal callback utilisation 45.5%. Analytic result (`results/table_analytic_robustness.csv`): with
optional work running, `ctrl` tolerates **6.0 ms** of exceedance in a busy window (R(0) = 14.0 ms because
`viz` can block it for ~12 ms); with optional work shed it tolerates **14.0 ms**. That 8 ms of extra margin
is the *reason* shedding helps, and 6.0 ms is the budget the overrun monitor is scaled from.

| Decision | Why | Sensitivity |
|---|---|---|
| One socket per callback (`s = 4`), periodic arrivals, random phase per seed | simplest model satisfying the paper's assumptions | arrival curves are exactly respected, so the theorem applies to the no-mitigation system |
| Encoder is latched at message *arrival*; actuation happens at *completion* | makes control performance depend on response time, as in a real loop | -- |
| Plant `tau*dw/dt = -w + u - d`, tau = 0.1 s; PI kp = 2, ki = 20, u in [-1.5, 1.5], conditional-integration anti-windup | first-order motor; gains chosen so that 40 ms of constant latency doubles the error and 80 ms is nearly unstable (delay margin ~2-4 periods) | tracking error moves only ~1% at <1% misses; reported as a secondary metric, honestly small |
| Reference = fixed pseudo-random staircase every 0.5 s | persistent excitation, so late actuations always matter somewhat | -- |

## 3. Execution-time model and disturbances

* Nominal job time = NET * U(0.6, 1.0); basic actions at 0.8 * NET. NET plays the role of a high
  percentile, so nominal jobs mostly sit *below* NET (this headroom is why mild slowdowns are harmless).
* OS noise: 30 events/s of stolen time (lognormal, median 60 us, capped at 1 ms), identical in every
  scenario for a given seed.
* Slowdown windows multiply the duration of every action that *starts* inside them:
  `spike` 10-40 ms at 3-8x (one every ~1.2 s), `burst` 0.3-0.8 s at 1.2-3x (~3 s), `ramp` 1.5-3 s,
  triangular peak 1.3-3x (~4 s). **Window rates and severities were chosen so that the unmitigated
  system misses about 0.5-1% of control deadlines per scenario, enough for statistics.** They contain
  benign slowdowns (< ~1.25x stays inside the NET headroom) as well as harmful ones. The robustness
  check re-runs the conclusions on `burst_harsh`/`ramp_harsh` (only 2-3.5x) to expose how much the
  result depends on that mix.

## 4. The policies

All monitors are *in-loop* (inside the single-threaded scheduler) and causal: they see the duration of
each action when it ends and are asked about an optional job only at its dispatch. A running
nonpreemptive callback cannot be aborted, so the **first** overrunning optional job can never be
prevented (see `fig4_miss_causes.png`).

* `util`: shed while busy-time over a sliding window `W` exceeds `theta * W`.
* `resp`: symptom baseline; shed for the hold time once a control job's response time exceeds `phi * deadline`.
* `overrun`: accumulate `max(0, duration - NET)` over every basic action and callback since the last
  quiet instant (an idling phase = nothing pending at any priority); shed once this reaches
  `gamma * TET_ctrl`. This estimator is *per segment*, not Eq. (1) exactly (Eq. (1) compares whole-job
  inflated costs, which are more pessimistic), so it is not a sound online bound on `e`.
* Both hold shedding on for `H` after a trigger. `H` (3 values) and `W` (3 values, util only) are tuned
  on training seeds like the thresholds. (`resp`: phi x hold = 7 x 3.)
* Shedding replaces the callback body by a 10 us stub; the monitors cost 3 us per dispatch.
* `oracle`: sheds an optional job iff it would exceed its NET given the slowdown at dispatch (uses
  future knowledge; diagnostic ceiling for causal policies). `always`: sheds everything (floor for misses).

## 5. What the analysis does *not* give you

* The theorem covers the unmitigated scheduler. Once a monitor changes scheduling at run time, the
  response-time guarantee is not established; the degraded-mode TET in the analytic table is an
  analysis of the *steady* degraded configuration, not of the mode change itself.
* The simulation shows the monitor *behaves* well in these scenarios, not that it is optimal.
* Novelty relative to the literature (runtime monitoring of exceedance, mode-change analysis) has not
  been checked by a systematic literature search.
