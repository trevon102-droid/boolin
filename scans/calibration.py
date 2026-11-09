"""Is the model's P(Over) calibrated? Walk-forward on history, no sportsbook needed.

For every player-stat game we know the result of, make synthetic X.5 lines around the player's own
median (and a few steps above/below, so we test both likely and long-shot legs), compute the model's
P(over) from data BEFORE that day, and compare with how often the leg actually hit.
Also reports whether the projection adds information vs the raw recent distribution.

Caveat: synthetic lines are not book lines, and one player appears in many rows (clustered), so treat
differences of a few points as noise.
Usage: python scans/calibration.py [--season 2025-26] [--start 2025-11-15]
"""
import argparse
import math
import os
import statistics as st
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ingest import common as C  # noqa: E402
from scans import backtest as B, projections as P  # noqa: E402


def half(x):
    return math.floor(x) + 0.5


def build(plays, cmap=None):
    out = []
    for p in plays:
        step = 2 if p["stat"] == "PTS" else 1
        for k in (-1, 0, 1, 2):
            line = half(p["med"]) + k * step
            if line <= 0.5 - 1e-9 and line < 0.5:
                continue
            d = p["dist"]
            pm = P.p_over(p, line)
            pr = sum(v > line for v in d) / len(d)
            out.append((p["stat"], p["date"], pm, pr, 1 if p["actual"] > line else 0,
                        P.calibrate(pm, cmap) if cmap else pm))
    return out


def fit_map(rows, min_n=300):
    """Reliability curve: per 0.1-wide bin of raw model P -> realized hit rate, forced monotone."""
    xs, ys = [], []
    for lo in [x / 10 for x in range(10)]:
        s = [r for r in rows if lo <= r[2] < lo + 0.1 or (lo == 0.9 and r[2] == 1.0)]
        if len(s) >= min_n:
            xs.append(st.mean(r[2] for r in s))
            ys.append(st.mean(r[4] for r in s))
    for i in range(1, len(ys)):  # monotone non-decreasing
        ys[i] = max(ys[i], ys[i - 1])
    return {"x": xs, "y": ys}


def brier(rows, i):
    return st.mean((r[i] - r[4]) ** 2 for r in rows)


def table(rows, label, i=2):
    print(f"\n{label}")
    print(f"{'P(over) bin':>12}{'n':>8}{'predicted':>11}{'realized':>10}{'gap':>8}")
    for lo in [x / 10 for x in range(10)]:
        s = [r for r in rows if lo <= r[i] < lo + 0.1 or (lo == 0.9 and r[i] == 1.0)]
        if len(s) < 300:
            continue
        pr, rl = st.mean(r[i] for r in s), st.mean(r[4] for r in s)
        print(f"{f'{lo:.1f}-{lo + 0.1:.1f}':>12}{len(s):8}{pr * 100:10.1f}%{rl * 100:9.1f}%{(rl - pr) * 100:+7.1f}")


def slope(rows, i=2):
    xs, ys = [r[i] for r in rows], [r[4] for r in rows]
    mx, my = st.mean(xs), st.mean(ys)
    vx = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / vx, my - sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / vx * mx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2025-26")
    ap.add_argument("--start", default="2025-11-15")
    ap.add_argument("--write", action="store_true", help="fit the calibration map on this run and save it")
    ap.add_argument("--apply-map", action="store_true", help="evaluate the saved map (use on held-out data)")
    a = ap.parse_args()
    plays = B.run(P.load(C.connect(), a.season), a.start)
    cmap = P._cal_map() if a.apply_map else None
    rows = build(plays, cmap)
    base = st.mean(r[4] for r in rows)
    print(f"{len(rows)} synthetic legs from {len(plays)} player-stat games; overall hit rate {base * 100:.1f}%")
    print(f"Brier (lower better): model {brier(rows, 2):.4f} | raw recent dist {brier(rows, 3):.4f} | constant {st.mean((base - r[4]) ** 2 for r in rows):.4f}")
    b, a0 = slope(rows)
    print(f"Calibration slope (1.0 = perfect; <1 = overconfident): {b:.2f}, intercept {a0:+.2f}")
    if cmap:
        print(f"Brier calibrated: {brier(rows, 5):.4f}  (raw model {brier(rows, 2):.4f})")
        table(rows, "== CALIBRATED P(over) vs realized (out-of-sample) ==", 5)
    table(rows, "== Model P(over) vs realized, all legs ==")
    table(rows, "== Raw recent distribution (no projection scaling) ==", 3)
    for stat in P.STATS.values():
        s = [r for r in rows if r[0] == stat]
        print(f"  {stat}: slope {slope(s)[0]:.2f}, Brier model {brier(s, 2):.4f} vs raw {brier(s, 3):.4f}")
    if a.write:
        import json
        m = fit_map(rows)
        json.dump(m, open(P.CAL_PATH, "w"), indent=1)
        print(f"\nWrote {os.path.abspath(P.CAL_PATH)}: {m}")
    dates = sorted({r[1] for r in rows})
    mid = dates[len(dates) // 2]
    for lab, s in (("first half", [r for r in rows if r[1] < mid]), ("second half", [r for r in rows if r[1] >= mid])):
        print(f"  {lab}: slope {slope(s)[0]:.2f}")
    sh = [r for r in rows if r[2] <= 0.3 or r[2] >= 0.7]
    print("\nExtremes only (P<=0.3 or >=0.7), the legs where 'big edges' come from:")
    for lab, s in (("P<=0.30", [r for r in sh if r[2] <= 0.3]), ("P>=0.70", [r for r in sh if r[2] >= 0.7])):
        print(f"  {lab}: n={len(s)} predicted {st.mean(r[2] for r in s) * 100:.1f}% realized {st.mean(r[4] for r in s) * 100:.1f}%")


if __name__ == "__main__":
    main()
