"""Walk-forward backtest of scans/projections.py. No lookahead, no odds.

For each past game day D: project every player using ONLY games before D, then compare to what
they actually did on D. No lines exist here, so the "play" test is: when the model projects a
player ABOVE his norm, did he actually finish above his norm (and vice versa)? The norm is a weak
stand-in for a book line (real lines are sharper), so treat hit rates as an upper bound.

Caveat: no historical injury feed, so players who didn't play are simply absent from scoring.

Usage: python scans/backtest.py [--season 2026-27] [--start 2026-10-28] [--zmin 0.4]
"""
import argparse
import os
import statistics as st
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ingest import common as C  # noqa: E402
from scans import projections as P  # noqa: E402


def run(rows, start, end="9999", use_lift=False):
    dates = sorted({r["date"] for r in rows if r["date"] >= start and r["date"] <= end})
    actual = {(r["date"], r["pid"]): r for r in rows}
    out = []
    for d in dates:
        past = [r for r in rows if r["date"] < d]
        day = [r for r in rows if r["date"] == d]
        games = sorted({(r["opp"], r["team"]) for r in day if _home(day, r)})
        # actual absences that day = perfect injury info (upper bound; real reports lag/late scratches)
        played_today = {r["pid"] for r in day}
        outs = {t: {k for k in pl if k not in played_today} for t, pl in P.rotation(past).items()}
        for p in P.project(past, games, {}, outs_override=outs, use_lift=use_lift):
            pid = next((r["pid"] for r in day if r["player"] == p["player"] and r["team"] == p["team"]), None)
            if pid is None:
                continue  # didn't play
            a = actual[(d, pid)]
            key = {v: k for k, v in P.STATS.items()}[p["stat"]]
            # naive comparators from the same history
            hist = [x[key] or 0 for x in past if x["pid"] == pid]
            p.update(date=d, actual=a[key] or 0, naive_season=st.mean(hist), naive_last5=st.mean(hist[-5:]))
            out.append(p)
    return out


def lift_report(plays, zmin):
    aff = [p for p in plays if p["lift"] != 0]
    print(f"\n== Teammate-out lift: {len(aff)} projections had a lift ({len(aff) / len(plays) * 100:.1f}% of all) ==")
    if not aff:
        return
    print("MAE on affected plays (lower is better):")
    for stat in P.STATS.values():
        s = [p for p in aff if p["stat"] == stat]
        if s:
            m = lambda f: st.mean(abs(f(p) - p["actual"]) for p in s)
            print(f"  {stat:4} n={len(s):5}  with lift {m(lambda p: p['proj']):.2f} | no lift {m(lambda p: p['proj0']):.2f}"
                  f" | season-avg {m(lambda p: p['naive_season']):.2f}")
    up = [p for p in aff if p["lift"] > 0]
    dn = [p for p in aff if p["lift"] < 0]
    for lab, s, sign in (("lift UP  ", up, 1), ("lift DOWN", dn, -1)):
        if s:
            beat = sum(1 for p in s if abs(p["proj"] - p["actual"]) < abs(p["proj0"] - p["actual"]))
            side = sum(1 for p in s if (p["actual"] - p["proj0"]) * sign > 0)
            print(f"  {lab} n={len(s):5}  lift moved us closer to actual {beat / len(s) * 100:.1f}% of the time; "
                  f"actual landed on lift's side of no-lift proj {side / len(s) * 100:.1f}%")
    for label, f in (("ABOVE", lambda p: p["z"] >= zmin), ("BELOW", lambda p: p["z"] <= -zmin)):
        s = [p for p in aff if f(p) and p["actual"] != p["season"]]
        if s:
            sign = 1 if label == "ABOVE" else -1
            print(f"  affected, projects {label} norm: n={len(s)} hit {sum((p['actual'] - p['season']) * sign > 0 for p in s) / len(s) * 100:.1f}%")


def _home(day, r):
    return any(x["team"] == r["team"] and x["opp"] == r["opp"] for x in day)


def report(plays, zmin):
    print(f"{len(plays)} player-stat projections scored over {len({p['date'] for p in plays})} days\n")
    print("Accuracy (MAE, lower is better) -- model vs naive:")
    for stat in P.STATS.values():
        s = [p for p in plays if p["stat"] == stat]
        mae = lambda f: st.mean(abs(f(p) - p["actual"]) for p in s)
        print(f"  {stat:4} n={len(s):4}  model {mae(lambda p: p['proj']):.2f} | season-avg {mae(lambda p: p['naive_season']):.2f}"
              f" | last5-avg {mae(lambda p: p['naive_last5']):.2f}")
    print(f"\nDirection test: did actual land on the projected side of the norm? (|z| >= {zmin}, ties excluded)")
    print(f"{'bucket':26}{'n':>6}{'hit%':>8}{'avg miss':>10}")
    for label, f in (("projects ABOVE norm", lambda p: p["z"] >= zmin), ("projects BELOW norm", lambda p: p["z"] <= -zmin)):
        for conf_label, cf in (("all", lambda p: True), ("conf>=0.45", lambda p: p["conf"] >= 0.45)):
            s = [p for p in plays if f(p) and cf(p) and p["actual"] != p["season"]]
            if not s:
                continue
            sign = 1 if p_above(label) else -1
            hits = sum(1 for p in s if (p["actual"] - p["season"]) * sign > 0)
            miss = st.mean((p["actual"] - p["season"]) * sign for p in s)
            print(f"{label[:14]+' '+conf_label:26}{len(s):>6}{hits / len(s) * 100:7.1f}%{miss:10.2f}")
    base = [p for p in plays if abs(p["z"]) < 0.1 and p["actual"] != p["season"]]
    if base:
        print(f"\nControl (|z|<0.1, model sees no edge): actual>norm {sum(p['actual'] > p['season'] for p in base) / len(base) * 100:.1f}% of {len(base)}")


def p_above(label):
    return "ABOVE" in label


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2025-26")
    ap.add_argument("--start", default="2026-10-28")
    ap.add_argument("--zmin", type=float, default=0.4)
    ap.add_argument("--lift", action="store_true", help="enable experimental teammate-out lift")
    a = ap.parse_args()
    rows = P.load(C.connect(), a.season)
    plays = run(rows, a.start, use_lift=a.lift)
    report(plays, a.zmin)
    lift_report(plays, a.zmin)


if __name__ == "__main__":
    main()
