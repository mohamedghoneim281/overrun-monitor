# Pre-specified analysis plan (written before the test-seed sweep was run)

**Question.** Does a monitor driven by *execution-time overrun relative to NET*, with a budget set from
the analytic tolerable exceedance threshold (TET) of the control callback, give fewer control
deadline misses than a CPU-utilisation monitor at the same amount of optional work completed?

**Policies.** `none`, `always` (shed all optional work), `oracle` (clairvoyant diagnostic), `util`
(windowed utilisation > theta), `overrun` (accumulated exceedance since the last quiet instant >=
gamma * TET_ctrl). Both monitors share the hold time (shedding stays on for H after a trigger).
Grids: util theta x window x hold = 8 x 3 x 3, overrun gamma x hold = 9 x 3 (utilisation gets the
larger tuning grid on purpose: that can only favour the baseline).

**Scenarios** (equal weight in pooled metrics): `none` (nominal + OS noise), `spike`, `burst`, `ramp`.

**Seeds.** 16 TRAIN seeds (0..15) pick operating points; 40 disjoint TEST seeds (1000..1039) are the
only data behind reported numbers. Common random numbers across policies.

**Primary endpoints (per seed, pooled over the four scenarios, then mean over seeds):**
1. control deadline-miss rate (response time > period);
2. optional work completed (cost-weighted fraction of optional jobs executed, not discarded).
Secondary: longest run of consecutive misses; RMS tracking-error excess over the same-seed nominal
run; detection latency; false triggers per minute; miss-cause breakdown (blocked by an optional job
that was dispatched before any warning / blocked by necessary work / backlog).

**Comparison.** (a) *Selected operating points:* for each family and each target pooled optional-work
level in {0.80, 0.85, 0.90}, take the TRAIN configuration with the lowest pooled miss rate that meets
the target; evaluate it on TEST. Paired bootstrap over test seeds (5000 resamples) for the difference
overrun - util. (b) *Frontiers:* lower-left Pareto frontier of (optional work, miss rate) over all
configurations of a family on TEST seeds; miss rate interpolated at the same target levels; bootstrap CI.

**Robustness check.** The selected operating points are re-evaluated on two harsher scenarios
(`burst_harsh`, `ramp_harsh`: only slowdowns of 2.0-3.5x) with fresh seeds, to see whether
the conclusion depends on the presence of benign slowdowns (< ~1.25x, which stay inside the NET headroom).

**Not claimed.** No formal guarantee carries over from the paper to the simulation, nor to the
mitigation (changing the scheduler's behaviour at run time is outside the verified model).
