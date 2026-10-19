"""The application studied in the experiments (all times in microseconds).

One nonpreemptive fixed-priority Rossl instance on one core, four callbacks, one UDP-like
socket per callback (so s = 4).  The numbers are illustrative: replace NETs/overheads by
measurements from your own platform if you port the study to real hardware.
"""
from .analysis import Task, Overheads

MS = 1000

CTRL, SENS, TELE, VIZ = 0, 1, 2, 3

TASKS = [
    # 50 Hz motor-speed PI loop. Highest priority; deadline = period (implicit).
    Task("ctrl", period=20 * MS, net=1_500, prio=1, deadline=20 * MS),
    # Necessary sensor processing (filtering / monitoring), not optional.
    Task("sens", period=40 * MS, net=4_000, prio=2, deadline=40 * MS),
    # Optional work: telemetry packing and visualisation frame encoding.
    Task("tele", period=50 * MS, net=8_000, prio=3, deadline=50 * MS, optional=True),
    Task("viz", period=100 * MS, net=12_000, prio=4, deadline=100 * MS, optional=True),
]

OVERHEADS = Overheads(s=len(TASKS), net_fr=8, net_sr=25, net_sel=10, net_disp=10,
                      net_compl=10, net_idling=20)

# Cost of the stub that discards a shed optional job (replaces the callback body).
DROP_NET = 10

# Extra per-dispatch cost of running a monitor inside the scheduler loop.
MONITOR_COST = 3


def degraded_tasks():
    """Task set in degraded mode: optional callbacks only run the discard stub."""
    from dataclasses import replace
    return [replace(t, net=DROP_NET) if t.optional else t for t in TASKS]
