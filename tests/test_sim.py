import numpy as np
from ovb.analysis import build_model, response_bound
from ovb.monitors import AlwaysShed, NoMonitor, Oracle, OverrunMonitor, UtilMonitor
from ovb.sim import simulate
from ovb.taskset import CTRL, OVERHEADS, TASKS
from ovb.validate import soundness

DUR = 10_000_000


def run(mk, sc="none", seed=0, **kw):
    return simulate(mk, sc, seed, duration_us=DUR, **kw).metrics


def test_deterministic():
    a, b = run(lambda d: NoMonitor(), "burst", 3), run(lambda d: NoMonitor(), "burst", 3)
    assert a == b or all((x == y) or (np.isnan(x) and np.isnan(y)) for x, y in zip(a.values(), b.values()))


def test_nominal_is_schedulable_and_within_bound():
    r = simulate(lambda d: NoMonitor(), "none", 1, duration_us=DUR, keep_resp=True)
    assert r.metrics["miss_rate"] == 0 and r.metrics["opt_frac"] == 1.0
    M = build_model(TASKS, OVERHEADS)
    for i, tau in enumerate([m for m in M if m.kind == "callback"]):
        resp = np.array(r.logs["end"][i]) - np.array(r.logs["arr"][i][:len(r.logs["end"][i])])
        assert resp.max() <= response_bound(tau, M, 0) + 3_000      # OS-noise events (<= 1 ms each, 30/s) can add a little


def test_common_random_numbers_oracle_equals_none_when_nothing_to_shed():
    a, b = run(lambda d: NoMonitor()), run(lambda d: Oracle(d))
    assert a["rms_err"] == b["rms_err"] and a["miss_rate"] == b["miss_rate"]


def test_monitors_stay_quiet_in_nominal_operation():
    for mk in (lambda d: UtilMonitor(0.6), lambda d: OverrunMonitor(0.5 * 6005)):
        for seed in range(3):
            assert run(mk, "none", seed)["opt_frac"] == 1.0


def test_always_shed_removes_optional_work_and_helps_timing():
    n, a = run(lambda d: NoMonitor(), "spike", 2), run(lambda d: AlwaysShed(), "spike", 2)
    assert a["opt_frac"] == 0.0 and a["miss_rate"] <= n["miss_rate"]


def test_adversarial_soundness_smoke():
    rows = soundness(trials=6, exceedances_ms=(0, 6, 15), horizon_us=2_500_000)
    assert sum(r["violations"] for r in rows) == 0
    assert all(r["ratio"] <= 1.0 for r in rows)


def test_response_time_monitor_triggers_only_after_a_late_control_job():
    from ovb.monitors import RespMonitor
    m = RespMonitor(0.5, 20_000, hold_us=300_000)
    m.on_job_done(0, 9_000, 1_000)             # below 0.5 * D: no trigger
    assert not m.active(1_000)
    m.on_job_done(1, 15_000, 2_000)            # another task: ignored
    assert not m.active(2_000)
    m.on_job_done(0, 11_000, 3_000)            # control job late-ish: shed for the hold time
    assert m.active(3_001) and not m.active(303_001) and len(m.episodes) == 1


def test_unavoidable_single_job_slowdown_matches_closed_form():
    from ovb.analysis import unavoidable_slowdowns
    rows = {r["culprit"]: r for r in unavoidable_slowdowns(TASKS, OVERHEADS)}
    for name, r in rows.items():
        assert abs(r["single_job_slowdown_s_star"] - (1 + r["tet_protected_ms"] / r["culprit_inflated_cost_ms"])) < 1e-9
    assert 1.49 < rows["viz"]["single_job_slowdown_s_star"] < 1.51


def test_noise_scale_one_is_the_default():
    a = run(lambda d: NoMonitor(), "burst", 2)
    b = simulate(lambda d: NoMonitor(), "burst", 2, duration_us=DUR, noise_scale=1.0).metrics
    assert a["miss_rate"] == b["miss_rate"] and a["rms_err"] == b["rms_err"]
