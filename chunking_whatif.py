"""Analytic what-if of Table 'chunk': split each optional callback into k chunks (same total work).
Run from the artefact directory:  python chunking_whatif.py"""
import sys; sys.path.insert(0, ".")
from dataclasses import replace
from ovb.taskset import TASKS, OVERHEADS
from ovb.analysis import build_model, response_bound, tolerable_exceedance
print("k  viz_chunk  tele_chunk  ctrl R0    TET      s*(optional chunk)")
for k in [1, 2, 3, 4, 6, 12]:
    ts = [replace(t, net=t.net // k, period=t.period // k, jitter=(k - 1) * (t.period // k)) if t.optional else t for t in TASKS]
    M = build_model(ts, OVERHEADS); ctrl = M[0]; e = tolerable_exceedance(ctrl, M)
    opt = [m for m, t in zip(M, ts) if t.optional]
    print(f"{k:2d} {12/k:8.2f}ms {8/k:8.2f}ms {response_bound(ctrl, M, 0)/1000:7.2f} {e/1000:7.2f}ms  {1 + e/max(m.cost for m in opt):8.2f}")
