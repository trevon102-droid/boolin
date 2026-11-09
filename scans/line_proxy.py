"""How do Over/Under legs hit when the line sits near the player's own average/median? (no odds needed)

Proxy line = player's role-adjusted average (or median) rounded to the nearest .5, using only games
before the date (walk-forward, reuses scans/backtest.py). Real books shade lines and add vig:
at -110 you need >52.4% to profit, so a leg near 50/50 loses money even though it is "close to avg".

Usage: python scans/line_proxy.py [--season 2025-26] [--start 2025-11-15]
"""
import argparse
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ingest import common as C  # noqa: E402
from scans import backtest as B, projections as P  # noqa: E402

BE = 0.524  # break-even hit rate at -110


def half(x):
    return math.floor(x) + 0.5  # x.5 line: no pushes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2025-26")
    ap.add_argument("--start", default="2025-11-15")
    a = ap.parse_args()
    plays = B.run(P.load(C.connect(), a.season), a.start)
    print(f"{len(plays)} player-stat games scored\n")
    for basis in ("season", "med"):
        print(f"== Proxy line = role-adjusted {'AVERAGE' if basis == 'season' else 'MEDIAN'} -> nearest .5 ==")
        print(f"{'stat':5}{'n':>7}{'Over%':>8}{'Under%':>8}")
        for stat in P.STATS.values():
            s = [p for p in plays if p["stat"] == stat]
            ov = sum(p["actual"] > half(p[basis]) for p in s) / len(s)
            print(f"{stat:5}{len(s):7}{ov * 100:7.1f}%{(1 - ov) * 100:7.1f}%")
        print()
    print(f"== Does the MODEL's gap vs the median-proxy line pick winners? (break-even at -110 = {BE * 100:.1f}%) ==")
    print(f"{'proj - line':>14}{'side':>7}{'n':>7}{'hit%':>8}")
    edges = [(-9, -1.5), (-1.5, -0.75), (-0.75, -0.25), (-0.25, 0.25), (0.25, 0.75), (0.75, 1.5), (1.5, 9)]
    for lo, hi in edges:
        s = [p for p in plays if lo <= p["proj"] - half(p["med"]) < hi]
        if len(s) < 200:
            continue
        over = (lo + hi) / 2 > 0
        hit = sum((p["actual"] > half(p["med"])) == over for p in s) / len(s)
        print(f"{f'{lo:+.2f}..{hi:+.2f}':>14}{'OVER' if over else 'UNDER':>7}{len(s):7}{hit * 100:7.1f}%")


if __name__ == "__main__":
    main()
