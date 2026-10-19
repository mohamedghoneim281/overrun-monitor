#!/usr/bin/env python3
"""Reproduce every result and figure.

    python run_all.py --mode full            # ~25-30 min single core (use --jobs N for more)
    python run_all.py --mode quick           # a few minutes, coarser statistics
    python run_all.py --mode smoke           # ~1 minute, tiny grid (sanity check)
    python run_all.py --stage analyze        # re-do tables/figures from results/runs.csv

Stages: validate -> sweep -> resp -> analyze (harsh/noise checks run inside analyze);
`--stage extend` is an optional grid-extension check (run after the sweep).
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

MODES = {"smoke": dict(train=3, test=4, small=True, trials=10, dur=20.0),
         "quick": dict(train=8, test=20, small=False, trials=60, dur=40.0),
         "full": dict(train=16, test=40, small=False, trials=150, dur=40.0)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=MODES, default="quick")
    ap.add_argument("--stage", choices=["all", "validate", "sweep", "analyze", "extend", "resp"], default="all")
    ap.add_argument("--jobs", type=int, default=0, help="worker processes (default: all cores)")
    ap.add_argument("--out", default=str(ROOT / "results"))
    a = ap.parse_args()
    cfg = MODES[a.mode]
    out = Path(a.out); out.mkdir(exist_ok=True)

    if a.stage in ("all", "validate"):
        import csv
        from ovb import validate
        print("== validation ==", flush=True)
        t3 = validate.reproduce_table3()
        snd = validate.soundness(trials=cfg["trials"])
        for name, rows in (("validation_table3.csv", t3), ("validation_soundness.csv", snd)):
            with open(out / name, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
        print(f"soundness: {sum(r['violations'] for r in snd)} violations of R(E) in "
              f"{len(snd) * cfg['trials']} adversarial runs; max observed/bound = "
              f"{max(r['ratio'] for r in snd):.3f}")

    if a.stage in ("all", "sweep"):
        from ovb import experiments as ex
        print("== sweep ==", flush=True)
        ex.run_grid(ex.policy_grid(cfg["small"]), ex.SCENARIOS, cfg["train"], cfg["test"],
                    str(out / "runs.csv"), duration_s=cfg["dur"], jobs=a.jobs)

    if a.stage in ("all", "resp"):   # symptom-based (control response time) baseline
        from ovb import experiments as ex
        ex.run_grid(ex.resp_specs(), ex.SCENARIOS, cfg["train"], cfg["test"], str(out / "runs_resp.csv"),
                    duration_s=cfg["dur"], jobs=a.jobs)

    if a.stage == "extend":       # optional, ~10 min: widen the grids and re-select on train seeds
        from ovb import extend
        extend.main(out, cfg["train"], cfg["test"], cfg["dur"], a.jobs)

    if a.stage in ("all", "analyze"):
        from ovb import analyze
        print("== analysis ==", flush=True)
        analyze.main(out, ROOT / "figures", cfg)


if __name__ == "__main__":
    main()
