"""Score the model against real sportsbook lines from a hand-entered CSV (no API/key needed).

CSV columns: date,player,stat,line,over_odds,under_odds,app_last5   (stat = PTS|REB|AST|3PM)
  - O/U markets: both odds given -> book probability is no-vig.
  - X+ ladders (e.g. 3+ threes): enter line = X-0.5, over_odds only -> book probability INCLUDES vig
    (so the book prob is slightly overstated; edges on ladders are slightly flattering).
  - app_last5 (optional): the app's "LAST 5 AVG" to cross-check our data.

Model probability = share of the player's recent role games (<=25, >=70% of projected minutes) that
beat the line, after scaling those games so their mean equals our projection. Small samples: treat as rough.
Usage: python scans/score_lines.py data/lines/2026-11-09.csv
"""
import csv
import os
import statistics as st
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ingest import common as C  # noqa: E402
from scans import projections as P  # noqa: E402



def imp(odds):
    o = float(odds)
    return 100 / (o + 100) if o > 0 else -o / (-o + 100)


def payout(odds):
    o = float(odds)
    return o / 100 if o > 0 else 100 / -o


def main(path):
    lines = list(csv.DictReader(open(path)))
    day = lines[0]["date"]
    import datetime as dt
    rows = P.load(C.connect(), "2025-26,2026-27")
    games = P.slate(dt.date.fromisoformat(day))
    inj = P.injuries()
    plays = {(p["player"], p["stat"]): p for p in P.project(rows, games, inj)}
    by_name = {}
    for r in rows:
        by_name.setdefault(r["player"], []).append(r)
    print(f"{'player':25}{'stat':5}{'line':>6}{'odds':>6}{'book%':>7}{'proj':>6}{'norm':>6}{'L5db':>6}{'L5app':>6}{'model%':>8}{'edge':>7}{'EV/$':>7}")
    for ln in lines:
        name, stat, line = ln["player"], ln["stat"], float(ln["line"])
        g = by_name.get(name)
        pl = plays.get((name, stat))
        if not g or not pl:
            print(f"{name:25}{stat:5}{line:6} -- no projection (not on slate / filtered / Out)")
            continue
        k = {v: kk for kk, v in P.STATS.items()}[stat]
        role = [x[k] or 0 for x in g if x["min"] >= P.ROLE_FRAC * pl["proj_min"]][-25:]
        scale = pl["proj"] / (st.mean(role) or 1)
        p_over_model = sum(v * scale > line for v in role) / len(role)
        l5 = st.mean([x[k] or 0 for x in g[-5:]])
        over, under = float(ln["over_odds"]), ln["under_odds"]
        book = imp(over) / (imp(over) + imp(under)) if under else imp(over)
        edge = p_over_model - book
        ev = p_over_model * payout(over) - (1 - p_over_model)
        print(f"{name[:24]:25}{stat:5}{line:6.1f}{int(over):6d}{book * 100:6.1f}%{pl['proj']:6.1f}{pl['season']:6.1f}"
              f"{l5:6.1f}{ln.get('app_last5') or '':>6}{p_over_model * 100:7.1f}%{edge * 100:+6.1f}%{ev:+7.2f}"
              + ("  DTD" if pl["dtd"] else ""))
        if under:
            pu = 1 - p_over_model
            print(f"{'':25}{'':5}{'':>6}{int(float(under)):6d}{(1 - book) * 100:6.1f}%{'':18}{pu * 100:7.1f}%{(pu - (1 - book)) * 100:+6.1f}%"
                  f"{pu * payout(under) - (1 - pu):+7.2f}   <- UNDER side")


if __name__ == "__main__":
    main(sys.argv[1])
