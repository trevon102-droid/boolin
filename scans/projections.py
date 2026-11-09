"""Data-only player projections for a slate (no odds, no lines).

Signals (all from our own box scores):
  1. recent form   : recency-weighted last-10 per-minute rate, blended 50/50 with season rate
  2. minutes trend : recency-weighted last-5 minutes
  3. opponent      : team-level "stat allowed per game" vs league avg, shrunk toward 1 (k=8 games)
                     NOTE: team-level, NOT positional (db has no positions)
Ranking: z = (projection - player season mean) / player season std. Big |z| = projects far from norm.
Confidence: from games played, minutes stability and spread of the stat.
Players ESPN lists as Out are dropped; Day-To-Day are kept and tagged.

Usage: python scans/projections.py [--date YYYY-MM-DD] [--season 2026-27] [--top 15]
"""
import argparse
import datetime as dt
import math
import os
import re
import statistics as st
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ingest import common as C  # noqa: E402

SITE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
STATS = {"pts": "PTS", "reb": "REB", "ast": "AST", "fg3m": "3PM"}
MIN_GAMES, MIN_MPG = 6, 15.0
DECAY = 0.8  # weight multiplier per game back
OPP_K = 8    # shrink opponent factor toward 1: n/(n+K)


def wavg(vals, decay=DECAY):
    """vals oldest->newest; newest weighted most."""
    ws = [decay ** i for i in range(len(vals) - 1, -1, -1)]
    return sum(v * w for v, w in zip(vals, ws)) / sum(ws)


def load(con, season):
    rows = con.execute(
        "SELECT game_id, game_date, team, opp, player_id, player, minutes, pts, reb, ast, fg3m "
        "FROM player_games WHERE season=? AND minutes>0 ORDER BY game_date, game_id", (season,)).fetchall()
    cols = ["game_id", "date", "team", "opp", "pid", "player", "min", "pts", "reb", "ast", "fg3m"]
    return [dict(zip(cols, r)) for r in rows]


def opp_factors(rows):
    """For each team and stat: avg stat allowed per game / league avg per team-game, shrunk to 1."""
    per = {}  # (game, team) -> stat totals scored BY team
    for r in rows:
        t = per.setdefault((r["game_id"], r["team"]), {"opp": r["opp"], **{s: 0 for s in STATS}})
        for s in STATS:
            t[s] += r[s] or 0
    league = {s: st.mean(v[s] for v in per.values()) for s in STATS}
    allowed = {}
    for v in per.values():
        for s in STATS:
            allowed.setdefault((v["opp"], s), []).append(v[s])
    out = {}
    for (team, s), vals in allowed.items():
        raw = st.mean(vals) / league[s] if league[s] else 1.0
        n = len(vals)
        out[(team, s)] = (1 + (raw - 1) * n / (n + OPP_K), n)
    return out


def slate(day):
    d = C.get_json(f"{SITE}/scoreboard", {"dates": day.strftime("%Y%m%d")})
    games = []
    for e in d["events"]:
        if e["status"]["type"]["state"] != "pre":
            continue
        cs = {c["homeAway"]: c["team"]["abbreviation"] for c in e["competitions"][0]["competitors"]}
        games.append((cs["away"], cs["home"]))
    return games


def injuries():
    d = C.get_json(f"{SITE}/injuries")
    out = {}
    for t in d["injuries"]:
        for i in t["injuries"]:
            m = None
            for l in i["athlete"].get("links", []):
                m = re.search(r"/id/(\d+)/", l.get("href", ""))
                if m:
                    break
            if m:
                out[m.group(1)] = i["status"]
    return out


def project(rows, games, inj):
    opp = opp_factors(rows)
    by_p = {}
    for r in rows:
        by_p.setdefault(r["pid"], []).append(r)
    latest_team = {pid: g[-1]["team"] for pid, g in by_p.items()}
    team_dates = {}
    for r in rows:
        team_dates.setdefault(r["team"], set()).add(r["date"])
    plays = []
    for away, home in games:
        for team, opponent, is_home in ((away, home, 0), (home, away, 1)):
            recent_dates = sorted(team_dates.get(team, []))[-5:]
            for pid, g in by_p.items():
                if latest_team[pid] != team or g[-1]["date"] < (recent_dates[0] if recent_dates else "9"):
                    continue
                if inj.get(pid) == "Out" or len(g) < MIN_GAMES:
                    continue
                mins = [x["min"] for x in g]
                if st.mean(mins) < MIN_MPG:
                    continue
                proj_min = wavg(mins[-5:])
                for s, label in STATS.items():
                    vals = [x[s] or 0 for x in g]
                    season_rate = sum(vals) / sum(mins)
                    last = g[-10:]
                    recent_rate = wavg([(x[s] or 0) / x["min"] for x in last])
                    rate = 0.5 * season_rate + 0.5 * recent_rate
                    of, on = opp.get((opponent, s), (1.0, 0))
                    proj = rate * proj_min * of
                    mean, sd = st.mean(vals), (st.pstdev(vals) or 1e-9)
                    if mean < 1.0:  # skip near-zero stats (e.g. 3PM for bigs)
                        continue
                    z = (proj - mean) / sd
                    cv_min = st.pstdev(mins) / st.mean(mins)
                    conf = min(1.0, len(g) / 20) * (1 - min(cv_min, 0.5)) * (1 - min(sd / mean, 1.0) * 0.5)
                    plays.append({"player": g[-1]["player"], "team": team, "opp": opponent, "home": is_home,
                                  "stat": label, "proj": proj, "season": mean, "z": z, "conf": conf,
                                  "n": len(g), "proj_min": proj_min, "opp_f": of,
                                  "dtd": inj.get(pid) == "Day-To-Day"})
    return plays


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--season", default="2026-27")
    ap.add_argument("--top", type=int, default=15)
    a = ap.parse_args()
    con = C.connect()
    rows = load(con, a.season)
    if not rows:
        sys.exit("No data. Run: python ingest/nba_games.py " + a.season)
    games = slate(dt.date.fromisoformat(a.date))
    if not games:
        sys.exit(f"No upcoming games on {a.date}.")
    inj = injuries()
    plays = project(rows, games, inj)
    print(f"Slate {a.date}: " + ", ".join(f"{x}@{y}" for x, y in games))
    for title, key in (("PROJECTS ABOVE NORM", lambda p: -p["z"]), ("PROJECTS BELOW NORM", lambda p: p["z"])):
        print(f"\n== {title} (ranked by z x confidence) ==")
        print(f"{'player':22}{'tm':4}{'opp':5}{'stat':5}{'proj':>6}{'szn':>6}{'z':>6}{'conf':>6}{'min':>5}{'oppF':>6}{'n':>4}")
        ranked = sorted(plays, key=lambda p: key(p) * (0.5 + p["conf"]))
        for p in ranked[:a.top]:
            tag = " DTD" if p["dtd"] else ""
            print(f"{p['player'][:21]:22}{p['team']:4}{p['opp']:5}{p['stat']:5}{p['proj']:6.1f}{p['season']:6.1f}"
                  f"{p['z']:6.2f}{p['conf']:6.2f}{p['proj_min']:5.0f}{p['opp_f']:6.2f}{p['n']:4}{tag}")


if __name__ == "__main__":
    main()
