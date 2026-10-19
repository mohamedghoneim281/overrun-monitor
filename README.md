# Overrun-budget monitoring for a control loop on a nonpreemptive executor (simulation study)

An empirical prototype built on exceedance analysis (Zini et al., RTSS 2024) and its verified
instantiation for the Rossl scheduler (Bedarkar et al., RTSS 2026). It tests, in simulation, the
suggestion at the end of the 2026 paper: *use the tolerable exceedance threshold (TET) to decide when
to switch a system to a degraded mode* (the authors leave this to future work).

**Read first:** `MODEL.md` (every modelling choice), `PLAN.md` (endpoints fixed before test seeds were used).

## Result in one paragraph
A control callback (50 Hz PI loop) shares a nonpreemptive core with necessary sensor processing and two
optional callbacks. Under correlated execution-time slowdowns, a monitor that sheds optional work when the
**accumulated overrun relative to nominal execution times (NET)** has **2-3x fewer** control deadline misses
than a CPU-utilisation monitor or a control-response-time monitor **at the same amount of
optional work preserved** (40 held-out seeds; 95% bootstrap CIs exclude equality). The reason is
mechanical: 90-99% of unmitigated misses (spike 90%, burst 99%, ramp 99%) are a control job blocked by an optional job that had already been
dispatched when nothing warned yet; overrun in the *short* jobs that run first is an earlier, more specific
precursor than load or lateness.

## Key numbers (test seeds 1000-1039; pooled over nominal, spike, burst, ramp scenarios)
| Policy (chosen on train seeds, target >= 0.85 optional work) | Control misses | Optional work kept |
|---|---|---|
| none | 0.62% [0.57, 0.68] | 100% |
| utilisation monitor | 0.23% [0.21, 0.26] | 84% |
| response-time monitor | 0.22% [0.20, 0.24] | 77% |
| overrun-budget monitor | **0.09%** [0.08, 0.10] | 86% |
| oracle (clairvoyant, diagnostic) / always shed | 0.03% / 0.03% | 90% / 0% |

* Paired difference overrun - util at 0.85: -0.14 pp [-0.16, -0.12]; overrun - resp: -0.13 pp [-0.15, -0.11].
* Equal-optional-work frontiers (ratio overrun/util): 0.41 [0.34, 0.49] @0.80, 0.37 [0.30, 0.44] @0.85, 0.53 [0.42, 0.65] @0.90.
* Post-hoc wider grids (stage `extend`, re-selected on train): overrun 0.033% [0.023, 0.043] @0.86 vs util 0.249% @0.85; the gap grows (-0.22 pp [-0.24, -0.19]).
* Only severe slowdowns (`burst_harsh`, `ramp_harsh`): same ordering (burst_harsh: none 2.19%, util 0.30-0.37%, overrun 0.004-0.056%).
* Per scenario (0.85 points): spike 0.88 -> util 0.69 -> overrun 0.35% (oracle 0.12%, floor 0.11%); burst 0.97 -> 0.15 -> 0.01%; ramp 0.65 -> 0.08 -> 0.00%.

## Findings that cut against the original idea (reported on purpose)
1. **The TET is not the right trigger level.** Misses are flat for budgets <= ~0.5 x TET and rise toward
   the TET (`fig6`); triggering *at* the TET is too late. The best budgets (down to ~60 us for a 6 ms TET)
   sit at the OS-noise floor. The TET is useful as an upper scale and for the analytic margin, not as an optimum.
2. **Late warning is a hard limit** (`fig4`, `table_unavoidable_slowdown.csv`): a single job slowed by
   s* = 1 + TET/C_ovh (viz 1.50x, tele 1.74x, sens 2.47x) can cause a miss before any in-loop monitor can
   see it. Spikes therefore stay hard (0.35% vs 0.12% for the clairvoyant oracle).
3. **Noise-floor dependence** (`table_noise_sensitivity.csv`, not re-tuned): at 3x and 10x OS noise the miss
   benefit holds (0.03-0.09%) but the sensitive setting fires 108-349 times/min and optional work drops from
   0.86 to 0.71. The threshold must be calibrated to a *measured* noise floor.
4. **Tracking error** moves only ~0.8% (pooled) at these miss rates; monitors cannot be told apart on it.

## Is it publishable? (honest assessment)
* **Not new:** discarding low-criticality work when an execution-time budget is overrun is classical
  (AUTOSAR execution-time monitoring; mixed-criticality mode switches such as AMC; related work I found
  while checking, to read before citing: PAStime (ECRTS 2020), server-based overrun management (MDU),
  Lancaster/AUTOSAR protection work). Exceedance analysis itself is the Brandenburg/Zini/Biondi line.
* **Narrow, defensible new content:** (i) a first empirical test of the 2026 paper's own proposal;
  (ii) the comparison of three warning signals under a common, pre-specified train/test protocol with
  matched optional work; (iii) the late-warning limit and the finding that sub-TET budgets are needed.
* **Weaknesses a reviewer will raise:** simulation only; workload, disturbances and plant are our own
  choices (rates were set so the baseline misses ~0.5-1%); one core, one control loop; baselines are simple
  (no AMC/server baselines); no guarantee covers the mitigation (the theorem is for the unmitigated
  scheduler); best thresholds sit at the noise floor; overheads/NETs are illustrative, not measured.
* **Realistic venues now:** a work-in-progress / workshop paper, an arXiv technical report, or an
  application sample (this is what it is solid enough for). **Nobody can guarantee publication.**
* **To reach a full paper (RTAS/ECRTS/RTSS/EMSOFT-level):** (1) run the real Rossl (or a ROS 2 executor) on a
  Raspberry Pi 5 / Jetson with measured NETs and stress-ng/IPI disturbances and check the simulator against
  it; (2) analyse the mode change (a bound on misses under the monitor, not just the steady degraded mode);
  (3) use the paper's matrix workload and real traces; (4) add AMC-style and budget-server baselines;
  (5) make the optional jobs chunkable so the late-warning limit can be attacked, not only measured.

## Reproduce
Beginner route: see `START_HERE.md` (one command: `bash setup_and_run.sh full`, ends with a PASS/FAIL report).
Tested clean on Python 3.9, 3.10 and 3.12; re-simulated runs agree with the shipped CSVs to ~1e-16.
```
pip install -r requirements.txt
python -m pytest -q tests                 # 15 tests
python run_all.py --mode full --jobs N    # validate + sweep + analyse (~30 min on 1 core)
python run_all.py --mode full --stage resp      # response-time baseline (~6 min)
python run_all.py --mode full --stage extend    # optional wider-grid check (~15 min)
python run_all.py --mode full --stage analyze   # tables + figures (~8 min)
```
Seeds are fixed, so a re-run should reproduce the CSVs exactly with the same numpy version.
Layout: `ovb/analysis.py` (paper's equations), `sim.py` (scheduler + plant), `monitors.py`,
`experiments.py`, `extend.py`, `robustness.py`, `analyze.py`; results in `results/`, figures in `figures/`.

## Figures
`fig1_timing_trace` (one burst, four policies) - `fig3_pareto` (misses vs optional work) -
`fig4_miss_causes` - `fig5_validation` (analysis vs adversarial simulation; analytic TET) - `fig6_gamma_sensitivity`.

## Validation of the analysis code
Paper's Table III (zero overheads, since the paper omits them): TETs within 0.1 ms for 7/7 callbacks, 6/7 recovery
times within 0.1 ms (the 7th sits on an arrival-curve step: 200.0 ms vs 292.9 ms at TET+1 us; paper: 292.99).
Adversarial simulation: 0 violations of R(E) in 4,200 runs, observed/bound up to 0.99.
