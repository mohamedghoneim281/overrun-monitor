#!/usr/bin/env python3
"""Automatic PASS/FAIL check of a fresh run (results/) against the claims in README.md.

Run by setup_and_run.sh.  Exit code 0 = every check passed.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent
R = ROOT / "results"
REF = ROOT / "results_reference"
ok_all = True


def check(name, ok, detail="", warn_only=False):
    global ok_all
    tag = "PASS" if ok else ("WARN" if warn_only else "FAIL")
    if not ok and not warn_only:
        ok_all = False
    print(f"[{tag}] {name}" + (f"  ({detail})" if detail else ""))


def get(op, fam, tau=0.85):
    r = op[(op.family == fam) & (np.isclose(op.target_opt, tau))]
    return None if r.empty else r.iloc[0]


# ---- 1. is the analysis code right? -------------------------------------------------------
snd = pd.read_csv(R / "validation_soundness.csv")
check("analysis is never violated by the adversarial simulation", int(snd.violations.sum()) == 0,
      f"{int(snd.violations.sum())} violations; worst observed/bound = {snd.ratio.max():.3f}")
check("observed response times reach >=80% of the bound at E=0 (bound is tight, simulator is not trivially slow)",
      bool((snd[snd.E_ms == 0].ratio >= 0.8).all()), f"min ratio at E=0: {snd[snd.E_ms == 0].ratio.min():.3f}")
t3 = pd.read_csv(R / "validation_table3.csv")
dev = (t3.tet_ours_ms - t3.tet_paper_ms).abs().max()
check("tolerable exceedance thresholds match the paper's Table III", dev < 0.15, f"max deviation {dev:.3f} ms")

summ = json.loads((R / "summary.json").read_text()) if (R / "summary.json").exists() else {}
n_test = summ.get("n_test_seeds", 0)
if n_test < 15:
    print(f"\nSmoke mode ({n_test} test seeds): only the pipeline and the analysis code were checked.")
    print("\nSMOKE RUN OK" if ok_all else "\nSOMETHING FAILED")
    sys.exit(0 if ok_all else 1)

# ---- 2. headline claims ---------------------------------------------------------------------------
op = pd.read_csv(R / "table_operating_points.csv")
none = op[op.family == "none"].iloc[0]
u, o, p = get(op, "util"), get(op, "overrun"), get(op, "resp")
check("unmitigated system misses a sensible fraction of control deadlines (0.3%-1.0%)",
      0.3 < none.test_miss_pct < 1.0, f"{none.test_miss_pct:.3f}%")
check("overrun monitor has fewer misses than utilisation monitor (selected on train, tested on held-out seeds)",
      o.test_miss_pct < u.test_miss_pct, f"{o.test_miss_pct:.3f}% vs {u.test_miss_pct:.3f}%")
if p is not None:
    check("overrun monitor has fewer misses than control response-time monitor",
          o.test_miss_pct < p.test_miss_pct, f"{o.test_miss_pct:.3f}% vs {p.test_miss_pct:.3f}%")
check("overrun monitor keeps at least ~80% of optional work", o.test_opt >= 0.80, f"{o.test_opt:.3f}")
pdiff = pd.read_csv(R / "table_paired_difference.csv")
check("paired difference overrun - others is negative with 95% CI excluding 0 at every optional-work target",
      bool((pdiff.d_miss_hi < 0).all()), f"{len(pdiff)} comparisons")
fr = pd.read_csv(R / "table_frontier.csv")
rat = fr[fr.family.str.startswith("ratio")]
check("at equal optional work the miss-rate ratio overrun/other has a CI entirely below 1",
      bool((rat.hi < 1).all()), "ratios " + ", ".join(f"{x:.2f}" for x in rat.miss_rate))
caus = pd.read_csv(R / "table_per_scenario.csv")
nn = caus[(caus.policy == "none") & (caus.scenario != "none")]
share = nn.misses_blocked_by_unwarned_optional.sum() / max(1, nn[["misses_blocked_by_unwarned_optional", "misses_blocked_by_necessary", "misses_backlog"]].sum().sum())
check("most unmitigated misses are a control job blocked by an optional job that started before any warning",
      share > 0.8, f"{100 * share:.0f}%")

# ---- 3. robustness checks (selected configs, not re-tuned) ----------------------------------------------
if (R / "table_robustness_harsh.csv").exists():
    h = pd.read_csv(R / "table_robustness_harsh.csv")
    good = True
    for sc in h.scenario.unique():
        g = h[h.scenario == sc]
        mo = g[g.policy.str.startswith("overrun")].miss_pct.min()
        mu = g[g.policy.str.startswith("util")].miss_pct.min()
        good &= bool(mo < mu)
    check("severe-slowdown scenarios: best overrun configuration beats best utilisation configuration", good)
if (R / "table_noise_sensitivity.csv").exists():
    nz = pd.read_csv(R / "table_noise_sensitivity.csv")
    good = True
    for s, g in nz.groupby("noise_scale"):
        good &= bool(g[g.policy.str.startswith("overrun")].miss_pct.max() < g[g.policy.str.startswith("util")].miss_pct.min())
    check("1x/3x/10x OS noise: every overrun configuration still has fewer misses than every utilisation one", good)

# ---- 4. do the numbers reproduce the shipped ones? ----------------------------------------------------------
if (REF / "table_operating_points.csv").exists() and n_test == 40:
    ro = pd.read_csv(REF / "table_operating_points.csv")
    worst = 0.0
    for fam in ("none", "util", "resp", "overrun"):
        a, b = (op[op.family == fam].iloc[0], ro[ro.family == fam].iloc[0]) if fam == "none" else (get(op, fam), get(ro, fam))
        if a is not None and b is not None:
            worst = max(worst, abs(a.test_miss_pct - b.test_miss_pct))
    check("headline miss rates reproduce the numbers shipped with the code (within 0.02 percentage points)",
          worst < 0.02, f"largest difference {worst:.4f} pp (different library versions can cause tiny differences)",
          warn_only=True)
elif n_test == 40:
    print("[INFO] no shipped reference results found; skipped the number-by-number comparison")
else:
    print(f"[INFO] {n_test} test seeds (the shipped run used 40): numbers differ slightly by design; only the conclusions were checked.")

print("\nALL CHECKS PASSED" if ok_all else "\nSOME CHECKS FAILED - copy this whole screen and send it to me")
sys.exit(0 if ok_all else 1)
