"""Controller calibration: RMS tracking error of the PI loop under a CONSTANT control latency.

Supports the sentence in MODEL.md / the report that the gains give a delay margin of a few periods.
Run from the repository root:  python calibration_latency.py
"""
import heapq, sys
sys.path.insert(0, ".")
import ovb.plant as pl

T = 20_000_000          # 20 s of simulated time


def rms_for_latency(latency_us: int) -> float:
    P = pl.Plant(T, 500_000)
    ev, ys = [], {}
    for k in range(T // 20_000):
        heapq.heappush(ev, (k * 20_000, 0, k))            # sample the encoder at arrival
    while ev:
        t, kind, k = heapq.heappop(ev)
        if kind == 0:
            ys[k] = P.sample(t); heapq.heappush(ev, (t + latency_us, 1, k))
        else:
            y, r = ys[k]; P.actuate(t, r - y)               # actuate after the latency
    P.advance(T)
    return P.rms_error()


if __name__ == "__main__":
    base = rms_for_latency(0)
    print("latency_ms  rms_error  ratio_to_zero_latency")
    for L in (0, 5, 15, 20, 40, 60, 80, 100):
        r = rms_for_latency(L * 1000)
        print(f"{L:10d}  {r:9.4f}  {r / base:8.2f}")
