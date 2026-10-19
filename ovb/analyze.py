"""Aggregate sweep results, select operating points on TRAIN seeds, report TEST results,
and draw the figures.  Implements PLAN.md (plus the response-time baseline and checks added later,
which are listed as such in README.md)."""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from . import experiments as ex
from . import robustness
from .analysis import robustness_table, unavoidable_slowdowns
from .sim import simulate
from .taskset import OVERHEADS, TASKS, degraded_tasks

TARGETS = (0.80, 0.85, 0.90)
SCEN = ex.SCENARIOS
BOOT = 5000
FAM_ORDER = ("util", "resp", "overrun")
COL = {"util": "#d95f02", "resp": "#7570b3", "overrun": "#1b9e77", "none": "#444444",
       "always": "#999999", "oracle": "#e7298a"}
LBL = {"util": "utilisation monitor", "resp": "control response-time monitor",
       "overrun": "overrun-budget monitor (fraction of TET)", "none": "no mitigation",
       "always": "always shed", "oracle": "oracle (clairvoyant)"}
rng = np.random.default_rng(2026)


# ------------------------------------------------------------------------------------------
def load(path) -> pd.DataFrame:
    paths = path if isinstance(path, (list, tuple)) else [path]
    df = pd.concat([pd.read_csv(p) for p in paths], ignore_index=True)
    base = df[(df.policy == "none") & (df.scenario == "none")].drop_duplicates("seed").set_index("seed")["rms_err"]
    b = df["seed"].map(base)
    df["rms_excess_pct"] = 100.0 * (df["rms_err"] - b) / b
    return df


def pooled(df: pd.DataFrame) -> pd.DataFrame:
    """Mean over the four scenarios per (policy, seed): the unit of analysis is the seed."""
    keys = ["policy", "family", "theta", "window_ms", "hold_ms", "split", "seed"]
    sub = df[df.scenario.isin(SCEN)]
    g = sub.groupby(keys, as_index=False)[["miss_rate", "opt_frac", "miss_run", "rms_excess_pct"]].mean()
    cnt = sub.groupby(keys).scenario.nunique().reset_index(name="n")
    m = g.merge(cnt, on=keys)
    return m[m["n"] == len(SCEN)].drop(columns="n")


def families(pool: pd.DataFrame) -> list[str]:
    have = set(pool.family.unique())
    return [f for f in FAM_ORDER if f in have]


def boot_means(x: np.ndarray, B: int = BOOT) -> np.ndarray:
    idx = rng.integers(0, len(x), (B, len(x)))
    return x[idx].mean(axis=1)


def ci(x) -> tuple[float, float, float]:
    x = np.asarray(x, float)
    m = boot_means(x)
    return float(x.mean()), float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def ci_diff(a, b) -> tuple[float, float, float]:
    return ci(np.asarray(a, float) - np.asarray(b, float))


# ------------------------------------------------------------------------------------------
def select_operating_points(pool_train: pd.DataFrame) -> dict:
    """For each family and target level: the TRAIN configuration with the fewest misses among those
    that keep at least `target` of the optional work."""
    g = pool_train.groupby(["family", "policy"])[["opt_frac", "miss_rate"]].mean()
    ops = {}
    for fam in families(pool_train):
        gf = g.loc[fam]
        for tau in TARGETS:
            feas = gf[gf.opt_frac >= tau]
            ops[(fam, tau)] = None if feas.empty else \
                feas.sort_values(["miss_rate", "opt_frac"], ascending=[True, False]).index[0]
    return ops


def _mat(pool_test: pd.DataFrame, policies, col) -> np.ndarray:
    p = pool_test.pivot(index="policy", columns="seed", values=col)
    return p.loc[list(policies)].to_numpy()


def frontier_interp(opt: np.ndarray, miss: np.ndarray, targets) -> np.ndarray:
    """Lower-left Pareto frontier (max optional work, min misses), interpolated at targets."""
    order = np.argsort(-opt, kind="stable")
    keep, best = [], np.inf
    for i in order:
        if miss[i] < best - 1e-12:
            keep.append(i); best = miss[i]
    k = np.array(keep)[::-1]
    return np.interp(targets, opt[k], miss[k], left=np.nan, right=np.nan)


def frontier_table(pool_test: pd.DataFrame) -> pd.DataFrame:
    """Per-family frontier on TEST seeds (bootstrap over seeds) and the ratio overrun/other."""
    rows, boots, nres = [], {}, 1000
    for fam in families(pool_test):
        pols = sorted(pool_test[pool_test.family == fam].policy.unique()) + ["none", "always"]
        O, M = _mat(pool_test, pols, "opt_frac"), _mat(pool_test, pols, "miss_rate")
        n = O.shape[1]
        out = np.empty((nres, len(TARGETS)))
        for b in range(nres):
            idx = rng.integers(0, n, n)
            out[b] = frontier_interp(O[:, idx].mean(1), M[:, idx].mean(1), TARGETS)
        boots[fam] = out
        pt = frontier_interp(O.mean(1), M.mean(1), TARGETS)
        for j, tau in enumerate(TARGETS):
            lo, hi = np.nanpercentile(out[:, j], [2.5, 97.5])
            rows.append(dict(family=fam, target_opt=tau, miss_rate=pt[j], lo=lo, hi=hi))
    for other in ("util", "resp"):
        if "overrun" in boots and other in boots:
            for j, tau in enumerate(TARGETS):
                r = boots["overrun"][:, j] / boots[other][:, j]
                r = r[np.isfinite(r)]
                rows.append(dict(family=f"ratio overrun/{other}", target_opt=tau, miss_rate=float(np.median(r)),
                                 lo=float(np.percentile(r, 2.5)), hi=float(np.percentile(r, 97.5))))
    return pd.DataFrame(rows)


def analytic_table() -> pd.DataFrame:
    rows = []
    for mode, ts in (("full", TASKS), ("degraded (optional shed)", degraded_tasks())):
        for r in robustness_table(ts, OVERHEADS):
            rows.append(dict(mode=mode, task=r["task"], deadline_ms=r["deadline_us"] / 1e3,
                             R0_ms=r["R0_us"] / 1e3, TET_ms=r["tet_us"] / 1e3,
                             necessary_slowdown=r["slowdown"], recovery_ms=r["recovery_us"] / 1e3))
    return pd.DataFrame(rows)


# ------------------------------------------------------------------------------------------
def main(out: Path, figdir: Path, cfg: dict | None = None) -> None:
    out, figdir = Path(out), Path(figdir)
    figdir.mkdir(exist_ok=True)
    paths = [out / "runs.csv"] + ([out / "runs_resp.csv"] if (out / "runs_resp.csv").exists() else [])
    df = load(paths)
    pool = pooled(df)
    fams = families(pool)
    train, test = pool[pool.split == "train"], pool[pool.split == "test"]
    ops = select_operating_points(train)
    summary: dict = {"n_train_seeds": int(train.seed.nunique()), "n_test_seeds": int(test.seed.nunique()),
                     "families": fams}

    # --- 1. selected operating points on TEST (bootstrap over seeds) ----------------------------
    def stats_row(t, base):
        r = dict(base)
        for col, scale, nm in (("opt_frac", 1, "opt"), ("miss_rate", 100, "miss_pct"),
                               ("miss_run", 1, "miss_run"), ("rms_excess_pct", 1, "rms_excess_pct")):
            m, lo, hi = ci(scale * t[col].to_numpy())
            r.update({f"test_{nm}": m, f"test_{nm}_lo": lo, f"test_{nm}_hi": hi})
        return r

    rows, P = [], {}
    for (fam, tau), name in ops.items():
        if name is None:
            continue
        t = test[test.policy == name].set_index("seed").sort_index()
        P[(fam, tau)] = t
        tr = train[train.policy == name]
        rows.append(stats_row(t, dict(family=fam, target_opt=tau, policy=name, train_opt=tr.opt_frac.mean(),
                                      train_miss_pct=100 * tr.miss_rate.mean())))
    for ref in ("none", "always", "oracle"):
        rows.append(stats_row(test[test.policy == ref], dict(family=ref, target_opt=np.nan, policy=ref)))
    op_df = pd.DataFrame(rows)
    op_df.to_csv(out / "table_operating_points.csv", index=False)

    diffs = []
    for other in ("util", "resp"):
        if other not in fams:
            continue
        for tau in TARGETS:
            if ("overrun", tau) in P and (other, tau) in P:
                o, u = P[("overrun", tau)], P[(other, tau)]
                dm = ci_diff(100 * o.miss_rate.to_numpy(), 100 * u.miss_rate.to_numpy())
                do = ci_diff(o.opt_frac.to_numpy(), u.opt_frac.to_numpy())
                diffs.append(dict(comparison=f"overrun - {other}", target_opt=tau, d_miss_pct=dm[0], d_miss_lo=dm[1],
                                  d_miss_hi=dm[2], d_opt=do[0], d_opt_lo=do[1], d_opt_hi=do[2]))
    diff_df = pd.DataFrame(diffs)
    diff_df.to_csv(out / "table_paired_difference.csv", index=False)

    # --- 2. frontiers + gamma sensitivity ---------------------------------------------------------
    fr = frontier_table(test)
    fr.to_csv(out / "table_frontier.csv", index=False)
    og = test[test.family == "overrun"].groupby(["theta", "hold_ms"])[["miss_rate", "opt_frac"]].mean().reset_index()
    og["miss_pct"] = 100 * og.miss_rate
    og.to_csv(out / "table_gamma_sensitivity.csv", index=False)

    # --- 3. per-scenario view of the selected (0.85) points ------------------------------------------
    t_runs = df[df.split == "test"]
    sel = {"none": "none", "oracle": "oracle", "always": "always"}
    for (fam, tau), name in ops.items():
        if name and tau == 0.85:
            sel[fam] = name
    srows = []
    for label, name in sel.items():
        for sc in SCEN:
            g = t_runs[(t_runs.policy == name) & (t_runs.scenario == sc)]
            m, lo, hi = ci(100 * g.miss_rate.to_numpy())
            srows.append(dict(policy=label, config=name, scenario=sc, miss_pct=m, miss_lo=lo, miss_hi=hi,
                              miss_run=g.miss_run.mean(), opt=g.opt_frac.mean(), rms_excess_pct=g.rms_excess_pct.mean(),
                              windows_covered_by_shedding=(g.windows_detected.sum() / max(g.windows.sum(), 1)) if sc != "none" else np.nan,
                              false_triggers_per_min=g.false_triggers_per_min.mean(),
                              misses_blocked_by_unwarned_optional=int(g.miss_late_warning.sum()),
                              misses_blocked_by_necessary=int(g.miss_necessary_blocker.sum()),
                              misses_backlog=int(g.miss_backlog.sum())))
    sc_df = pd.DataFrame(srows)
    sc_df.to_csv(out / "table_per_scenario.csv", index=False)
    analytic_table().to_csv(out / "table_analytic_robustness.csv", index=False)
    pd.DataFrame(unavoidable_slowdowns(TASKS, OVERHEADS)).to_csv(out / "table_unavoidable_slowdown.csv", index=False)

    # --- 4. harsher scenarios and noise sensitivity (selected points, not re-tuned) ------------------
    from .extend import extended_specs
    grid = {s.name: s for s in ex.policy_grid(False) + ex.resp_specs() + extended_specs()}
    names = {n for (f, t), n in ops.items() if n and t == 0.85} | {"none", "oracle", "always"}
    ext_sel = out / "table_extended_selection.csv"           # post-hoc wider-grid winners (stage `extend`)
    if ext_sel.exists():
        e = pd.read_csv(ext_sel)
        names |= set(e[np.isclose(e.target_opt, 0.85)].policy)
    names = sorted(names)
    if cfg is None or not cfg.get("small"):
        ex.run_grid([grid[n] for n in names], ("burst_harsh", "ramp_harsh"), 0, int(test.seed.nunique()),
                    str(out / "runs_harsh.csv"), duration_s=(cfg or {}).get("dur", 40.0), jobs=1, verbose=False)
        h = pd.read_csv(out / "runs_harsh.csv")
        hr = []
        for sc in ("burst_harsh", "ramp_harsh"):
            for n in names:
                g = h[(h.policy == n) & (h.scenario == sc)]
                m, lo, hi = ci(100 * g.miss_rate.to_numpy())
                hr.append(dict(scenario=sc, policy=n, miss_pct=m, miss_lo=lo, miss_hi=hi, opt=g.opt_frac.mean(),
                               miss_run=g.miss_run.mean()))
        pd.DataFrame(hr).to_csv(out / "table_robustness_harsh.csv", index=False)
        nz = robustness.run(out, names, seeds=int(test.seed.nunique()), dur=(cfg or {}).get("dur", 40.0))
        robustness.table(nz).to_csv(out / "table_noise_sensitivity.csv", index=False)

    # --- 5. figures ------------------------------------------------------------------------------------------
    fig_pareto(df, test, ops, figdir)
    fig_gamma(og, test, figdir)
    fig_validation(out, figdir)
    fig_causes(sc_df, figdir)
    fig_traces(ops, figdir, int(test.seed.min()))

    summary["operating_points"] = {f"{f}@{t}": n for (f, t), n in ops.items()}
    summary["paired_difference"] = diff_df.to_dict("records")
    summary["frontier"] = fr.to_dict("records")
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=float))
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40)
    print(op_df.drop(columns=[c for c in op_df if c.endswith("_lo") or c.endswith("_hi")]).round(3).to_string())
    print(diff_df.round(3).to_string()); print(fr.round(4).to_string())


# ------------------------------------------------------------------------------------------
def fig_pareto(df, test_pool, ops, figdir):
    t = df[df.split == "test"]
    fams = families(test_pool)
    fig, axs = plt.subplots(2, 2, figsize=(11.5, 8.2), sharex=True)
    panels = [("spike", axs[0, 0]), ("burst", axs[0, 1]), ("ramp", axs[1, 0]), ("pooled (4 scenarios)", axs[1, 1])]
    chosen85 = {v for (f, tt), v in ops.items() if v and tt == 0.85}
    for title, ax in panels:
        if title.startswith("pooled"):
            g = test_pool.groupby(["policy", "family"], as_index=False)[["opt_frac", "miss_rate"]].mean()
        else:
            g = t[t.scenario == title].groupby(["policy", "family"], as_index=False)[["opt_frac", "miss_rate"]].mean()
        g["miss_pct"] = 100 * g.miss_rate
        for fam in fams:
            gf = g[g.family == fam]
            ax.scatter(gf.opt_frac, gf.miss_pct, s=12, alpha=.40, color=COL[fam], label=LBL[fam] + " (all configs)")
            pts = pd.concat([gf, g[g.policy.isin(["none", "always"])]])
            xs = np.linspace(pts.opt_frac.min(), pts.opt_frac.max(), 200)
            ax.plot(xs, frontier_interp(pts.opt_frac.to_numpy(), pts.miss_pct.to_numpy(), xs), color=COL[fam], lw=2)
            for pol in gf.policy:
                if pol in chosen85:
                    r = gf[gf.policy == pol].iloc[0]
                    ax.scatter(r.opt_frac, r.miss_pct, s=110, facecolors="none", edgecolors=COL[fam], lw=2)
        for ref in ("none", "always", "oracle"):
            r = g[g.policy == ref]
            if len(r):
                ax.scatter(r.opt_frac, r.miss_pct, marker="D", s=70, color=COL[ref], label=LBL[ref], zorder=5)
        ax.set_title(title); ax.set_ylabel("control deadline misses (%)"); ax.grid(alpha=.3)
        ax.set_xlabel("optional work completed (fraction)")
    axs[0, 0].legend(fontsize=7, loc="upper left")
    fig.suptitle("Misses vs. optional work preserved (test seeds; circled = point chosen on train seeds for 0.85)")
    fig.tight_layout(); fig.savefig(figdir / "fig3_pareto.png", dpi=170); plt.close(fig)


def fig_gamma(og, test_pool, figdir):
    fig, axs = plt.subplots(1, 2, figsize=(11, 4.2))
    for h, c in zip(sorted(og.hold_ms.unique()), ("#66c2a5", "#1b9e77", "#005c45")):
        g = og[og.hold_ms == h]
        axs[0].plot(g.theta, g.miss_pct, "o-", color=c, label=f"hold {h} ms")
        axs[1].plot(g.theta, g.opt_frac, "o-", color=c, label=f"hold {h} ms")
    for ax in axs:
        ax.set_xscale("log"); ax.axvline(1.0, color="k", ls="--", lw=1); ax.grid(alpha=.3)
        ax.set_xlabel("overrun budget as a fraction of the control TET  (gamma)")
    axs[0].set_ylabel("control deadline misses (%), pooled"); axs[1].set_ylabel("optional work completed")
    axs[0].text(1.05, axs[0].get_ylim()[1] * .9, "trigger at TET", fontsize=8)
    axs[0].legend(fontsize=8)
    fig.suptitle("Overrun-budget monitor (test seeds): misses are flat for gamma <~ 0.5 and rise towards the TET itself (gamma = 1)")
    fig.tight_layout(); fig.savefig(figdir / "fig6_gamma_sensitivity.png", dpi=170); plt.close(fig)


def fig_validation(out, figdir):
    snd = pd.read_csv(out / "validation_soundness.csv") if (out / "validation_soundness.csv").exists() else None
    an = analytic_table()
    fig, axs = plt.subplots(1, 3, figsize=(14, 4.2))
    if snd is not None:
        for ax, task in zip(axs[:2], ("ctrl", "viz")):
            s = snd[snd.task == task]
            ax.plot(s.E_ms, s.bound_ms, "k-o", label="Theorem 1 bound R(E)")
            ax.plot(s.E_ms, s.observed_max_ms, "r-s", label="max observed (adversarial sim)")
            if task == "ctrl":
                ax.axhline(20, color="gray", ls="--", label="deadline (20 ms)")
            ax.set_title(f"{task}: observed vs analytic response time")
            ax.set_xlabel("total injected exceedance E (ms)"); ax.set_ylabel("response time (ms)")
            ax.legend(fontsize=8); ax.grid(alpha=.3)
    a = an.pivot(index="task", columns="mode", values="TET_ms").loc[[t.name for t in TASKS]]
    a.plot.bar(ax=axs[2], color=["#1b9e77", "#d95f02"], rot=0)
    axs[2].set_ylabel("tolerable exceedance threshold (ms)"); axs[2].set_title("Analytic TET: full vs. degraded mode")
    axs[2].grid(alpha=.3, axis="y")
    fig.tight_layout(); fig.savefig(figdir / "fig5_validation.png", dpi=170); plt.close(fig)


def fig_causes(sc_df, figdir):
    d = sc_df[sc_df.scenario != "none"]
    order = [p for p in ("none", "util", "resp", "overrun", "oracle", "always") if p in set(d.policy)]
    fig, axs = plt.subplots(1, 3, figsize=(14, 4.0))
    for ax, sc in zip(axs, ("spike", "burst", "ramp")):
        g = d[d.scenario == sc].set_index("policy").loc[order]
        bot = np.zeros(len(g))
        for col, lab, c in (("misses_blocked_by_unwarned_optional", "blocked by optional job dispatched before any warning", "#d95f02"),
                            ("misses_blocked_by_necessary", "blocked by necessary work", "#7570b3"),
                            ("misses_backlog", "backlog / polling lag", "#999999")):
            ax.bar(g.index, g[col], bottom=bot, label=lab, color=c); bot += g[col].to_numpy()
        ax.set_title(sc); ax.set_ylabel("missed control deadlines (all test seeds)"); ax.grid(alpha=.3, axis="y")
        ax.tick_params(axis="x", labelsize=8)
    axs[0].legend(fontsize=7)
    fig.suptitle("Why deadlines are still missed (selected operating points for 0.85 optional work)")
    fig.tight_layout(); fig.savefig(figdir / "fig4_miss_causes.png", dpi=170); plt.close(fig)


def fig_traces(ops, figdir, seed):
    grid = {s.name: s for s in ex.policy_grid(False) + ex.resp_specs()}
    pol = [("no mitigation", "none", "none")]
    for fam, lab in (("util", "utilisation monitor"), ("resp", "response-time monitor"), ("overrun", "overrun-budget monitor")):
        if ops.get((fam, 0.85)):
            pol.append((lab, fam, ops[(fam, 0.85)]))
    ehat = ex.ehat_ctrl()
    best = None
    for s in range(seed, seed + 12):
        r = simulate(grid["none"].factory(ehat), "burst", s, trace=True, duration_us=20_000_000)
        a, e = r.trace["ctrl_arr"], r.trace["ctrl_end"]
        for ws, we, pk in r.trace["windows"]:
            if pk < 2.0:
                continue
            miss = int(((e - a) > 20_000)[(a >= ws) & (a <= we + 1_000_000)].sum())
            if best is None or miss > best[0]:
                best = (miss, s, ws, we)
    _, s, ws, we = best
    lo, hi = max(ws - 0.4e6, 0), we + 1.3e6
    fig, axs = plt.subplots(len(pol), 2, figsize=(13, 2.5 * len(pol) + .5), sharex=True)
    for row, (label, fam, name) in enumerate(pol):
        r = simulate(grid[name].factory(ehat), "burst", s, trace=True, duration_us=20_000_000)
        a, e = r.trace["ctrl_arr"], r.trace["ctrl_end"]
        m = (a >= lo) & (a <= hi)
        ax = axs[row, 0]
        ax.plot(a[m] / 1e6, (e - a)[m] / 1e3, ".-", ms=3, lw=.8, color=COL[fam])
        ax.axhline(20, color="gray", ls="--", lw=1)
        for w0, w1, pk in r.trace["windows"]:
            ax.axvspan(w0 / 1e6, w1 / 1e6, color="red", alpha=.10)
        for es, ee in r.trace["episodes"]:
            ax.axvspan(es / 1e6, ee / 1e6, ymin=0, ymax=.06, color="blue", alpha=.5)
        ax.set_ylabel(f"{label}\nresponse (ms)"); ax.set_xlim(lo / 1e6, hi / 1e6); ax.set_ylim(0, 33)
        pl = np.array(r.trace["plant"])
        pm = (pl[:, 0] >= lo) & (pl[:, 0] <= hi)
        ax2 = axs[row, 1]
        ax2.plot(pl[pm, 0] / 1e6, pl[pm, 2], "k--", lw=.8, label="reference")
        ax2.plot(pl[pm, 0] / 1e6, pl[pm, 1], color=COL[fam], lw=1.2, label="speed")
        ax2.set_ylabel("motor speed")
    axs[0, 0].set_title("control callback response time (red: slowdown window; blue: shedding on)")
    axs[0, 1].set_title("tracking"); axs[0, 1].legend(fontsize=8)
    for ax in axs[-1]:
        ax.set_xlabel("time (s)")
    fig.tight_layout(); fig.savefig(figdir / "fig1_timing_trace.png", dpi=170); plt.close(fig)
