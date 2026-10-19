import math
from ovb.analysis import (INF, Overheads, Task, build_model, busy_window, recovery_time,
                          response_bound, tolerable_exceedance)
from ovb.validate import reproduce_table3

ZERO = Overheads(1, 0, 0, 0, 0, 0, 0)


def two_tasks():
    return [Task("t1", 10, 2, 1, 10), Task("t2", 20, 5, 2, 20)]


def test_hand_calculation_t1():
    # t1 can be blocked by t2 for C2 - eps = 4, then runs 2: R = 6.
    # e = 4 still meets D = 10 (R = 10); e = 5 does not (a second t1 job enters the busy window).
    M = build_model(two_tasks(), ZERO)
    t1 = M[0]
    assert response_bound(t1, M, 0) == 6
    assert response_bound(t1, M, 4) == 10
    assert response_bound(t1, M, 5) == 11
    assert tolerable_exceedance(t1, M) == 4


def test_hand_calculation_t2():
    M = build_model(two_tasks(), ZERO)
    assert response_bound(M[1], M, 0) == 7          # t1 (2) then t2 (5)


def test_response_bound_monotone_in_exceedance():
    from ovb.taskset import OVERHEADS, TASKS
    M = build_model(TASKS, OVERHEADS)
    for tau in M[:4]:
        vals = [response_bound(tau, M, e) for e in range(0, 60_000, 2_500)]
        assert all(a <= b for a, b in zip(vals, vals[1:]))


def test_overload_has_no_bound():
    tasks = [Task("a", 10, 6, 1, 10), Task("b", 10, 6, 2, 10)]      # utilisation 1.2
    M = build_model(tasks, ZERO)
    assert busy_window(M[1], M, 0, limit=10**6) is None
    assert response_bound(M[1], M, 0, limit=10**6) == INF


def test_matches_paper_table3_without_overheads():
    for r in reproduce_table3():
        assert abs(r["tet_ours_ms"] - r["tet_paper_ms"]) < 0.15        # overhead explains <0.1 ms
        if r["task"] != "MATRIX_168":
            assert abs(r["recovery_ours_ms"] - r["recovery_paper_ms"]) < 0.2


def test_recovery_knife_edge_for_lowest_priority_task():
    # L(e) of the lowest-priority task sits exactly on a step of the arrival curves at e = TET,
    # so +1 us changes it from 200 ms to ~293 ms (paper: 292.99 ms). Not an implementation error.
    r = [x for x in reproduce_table3() if x["task"] == "MATRIX_168"][0]
    assert math.isclose(r["recovery_ours_ms"], 200.0, abs_tol=0.01)
    assert abs(r["recovery_ours_at_tet_plus_1us_ms"] - r["recovery_paper_ms"]) < 0.2
