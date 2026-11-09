"""Data-only player projections for a slate (no odds, no lines).

Signals (all from our own box scores):
  1. recent form   : recency-weighted last-10 per-minute rate, blended 50/50 with season rate
  2. minutes trend : recency-weighted last-5 minutes
  3. opponent      : team-level "stat allowed per game" vs league avg, shrunk toward 1 (k=8 games)
                     NOTE: team-level, NOT positional (db has no positions)
Ranking: z = (projection - role-adjusted norm) / norm std. Big |z| = projects far from norm.
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
ROLE_FRAC, MIN_ROLE_GAMES = 0.7, 5
DECAY = 0.8  # weight multiplier per game back
OPP_K = 8    # shrink opponent factor toward 1: n/(n+K)


def wavg(vals, decay=DECAY):
    """vals oldest->newest; newest weighted most."""
    ws = [decay ** i for i in range(len(vals) - 1, -1, -1)]
    return sum(v * w for v, w in zip(vals, ws)) / sum(ws)


def load(con, season):
    """season may be '2026-27' or a comma list '2025-26,2026-27' (later seasons seed earlier ones as history)."""
    seasons = season.split(",")
    q = ",".join("?" * len(seasons))
    rows = con.execute(
        "SELECT game_id, game_date, team, opp, player_id, player, minutes, pts, reb, ast, fg3m "
        f"FROM player_games WHERE season IN ({q}) AND minutes>0 ORDER BY game_date, game_id", seasons).fetchall()
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


ROT_MPG, ROT_GAMES = 20.0, 6        # rotation = avg >=20 min over >=6 games with the team
LIFT_MIN_IN, LIFT_MIN_OUT, LIFT_K = 5, 2, 5
LIFT_MODE = "all"  # "all": shift toward mean-without-teammate vs mean of all games; "in": regime baseline (games w/ teammate)
FRESH_MIN = 3
LIFT_MULTI, LIFT_CAP = 0.75, 0.40   # several teammates out: damp the sum; cap at 40% of base projection


def rotation(rows):
    """{team: {pid: mpg}} for each player's CURRENT (latest) team, rotation players only."""
    by = {}
    for r in rows:
        by.setdefault(r["pid"], []).append(r)
    rot = {}
    for pid, g in by.items():
        team = g[-1]["team"]
        mine = [x["min"] for x in g if x["team"] == team]
        if len(mine) >= ROT_GAMES and st.mean(mine) >= ROT_MPG:
            rot.setdefault(team, {})[pid] = st.mean(mine)
    return rot


def lift_for(g, team, k, stat, played, k_first):
    """Shrunk (mean_out - mean_in) of `stat` for a player's games on `team`, teammate k absent vs present.
    Only games on/after k's first game with the team count (so last year's pre-trade games don't
    look like 'absences'). Returns (lift, n_out) or (0, 0) if samples are too thin."""
    first = k_first.get((k, team))
    if first is None:
        return 0.0, 0
    ins, outs = [], []
    for x in g:
        if x["team"] != team or x["date"] < first or x["min"] < 10:
            continue
        (ins if k in played[x["game_id"]] else outs).append(x[stat] or 0)
    if len(ins) < LIFT_MIN_IN or len(outs) < LIFT_MIN_OUT:
        return 0.0, len(outs)
    ref = st.mean(ins + outs) if LIFT_MODE == "all" else st.mean(ins)
    return (st.mean(outs) - ref) * len(outs) / (len(outs) + LIFT_K), len(outs)


def project(rows, games, inj, outs_override=None, use_lift=False):
    """outs_override: {team: set(pid)} of rotation teammates to treat as out (backtest passes the
    actual absences; live mode derives them from the injury feed's 'Out' status)."""
    opp = opp_factors(rows)
    by_p = {}
    played, k_first = {}, {}
    for r in rows:
        by_p.setdefault(r["pid"], []).append(r)
        played.setdefault(r["game_id"], set()).add(r["pid"])
        k_first.setdefault((r["pid"], r["team"]), r["date"])
    latest_team = {pid: g[-1]["team"] for pid, g in by_p.items()}
    names = {pid: g[-1]["player"] for pid, g in by_p.items()}
    rot = rotation(rows)
    team_gids = {}
    for r in rows:
        lst = team_gids.setdefault(r["team"], [])
        if not lst or lst[-1] != r["game_id"]:
            lst.append(r["game_id"])
    team_dates = {}
    for r in rows:
        team_dates.setdefault(r["team"], set()).add(r["date"])
    plays = []
    for away, home in games:
        for team, opponent, is_home in ((away, home, 0), (home, away, 1)):
            recent_dates = sorted(team_dates.get(team, []))[-5:]
            if outs_override is not None:
                outs = set(outs_override.get(team, ()))
            else:
                outs = {k for k in rot.get(team, {}) if inj.get(k) == "Out"}
            for pid, g in by_p.items():
                if latest_team[pid] != team or g[-1]["date"] < (recent_dates[0] if recent_dates else "9"):
                    continue
                if inj.get(pid) == "Out" or pid in outs or len(g) < MIN_GAMES:
                    continue
                if st.mean([x["min"] for x in g]) < MIN_MPG:
                    continue
                # only FRESHLY-out teammates (played >=3 of the team's last 5 games): longer absences are
                # already in the player's recent form, so lifting for them double-counts.
                recent5 = team_gids.get(team, [])[-5:]
                ks = [k for k in outs if k != pid and k_first.get((k, team))
                      and sum(k in played[gid] for gid in recent5) >= FRESH_MIN] if use_lift else []
                # regime baseline: games where every currently-out teammate was present (and on the team),
                # so the lift below isn't double-counted by recent games that already had them out.
                g_in = [x for x in g if x["team"] == team and all(
                    x["date"] >= k_first[(k, team)] and k in played[x["game_id"]] for k in ks)] if ks else g
                regime = bool(ks) and len(g_in) >= MIN_GAMES
                gg = g_in if (regime and LIFT_MODE == "in") else g
                mins = [x["min"] for x in gg]
                proj_min = wavg(mins[-5:])
                for s, label in STATS.items():
                    vals = [x[s] or 0 for x in gg]
                    season_rate = sum(vals) / sum(mins)
                    last = gg[-10:]
                    recent_rate = wavg([(x[s] or 0) / x["min"] for x in last])
                    rate = 0.5 * season_rate + 0.5 * recent_rate
                    of, on = opp.get((opponent, s), (1.0, 0))
                    proj0 = rate * proj_min * of
                    # role-adjusted baseline: only games at >=70% of current projected minutes,
                    # so early-season ramp-up / garbage-time games don't define "norm".
                    role = [x[s] or 0 for x in gg if x["min"] >= ROLE_FRAC * proj_min]
                    base = role if len(role) >= MIN_ROLE_GAMES else vals
                    mean, sd = st.mean(base), (st.pstdev(base) or 1e-9)
                    if mean < 1.0:  # skip near-zero stats (e.g. 3PM for bigs)
                        continue
                    lift, who = 0.0, []
                    if regime:
                        parts = []
                        for k in ks:
                            l, n_out = lift_for(g, team, k, s, played, k_first)
                            if n_out >= LIFT_MIN_OUT and l:
                                parts.append(l)
                                who.append(f"{names[k].split()[-1]}({n_out})")
                        if parts:
                            lift = sum(parts) * (LIFT_MULTI if len(parts) > 1 else 1.0)
                            cap = LIFT_CAP * max(proj0, 1.0)
                            lift = max(-cap, min(cap, lift))
                    proj = max(0.0, proj0 + lift)
                    z = (proj - mean) / sd
                    cv_min = st.pstdev(mins) / st.mean(mins)
                    conf = min(1.0, len(gg) / 20) * (1 - min(cv_min, 0.5)) * (1 - min(sd / mean, 1.0) * 0.5)
                    plays.append({"player": g[-1]["player"], "team": team, "opp": opponent, "home": is_home,
                                  "stat": label, "proj": proj, "proj0": proj0, "lift": lift, "who": ",".join(who),
                                  "season": mean, "role_n": len(base), "z": z, "conf": conf,
                                  "n": len(g), "proj_min": proj_min, "opp_f": of,
                                  "dtd": inj.get(pid) == "Day-To-Day"})
    return plays


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--season", default="2025-26,2026-27", help="comma list; later seasons seed history")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--lift", action="store_true",
                    help="EXPERIMENTAL teammate-out lift. Backtest showed no predictive value; off by default.")
    a = ap.parse_args()
    con = C.connect()
    rows = load(con, a.season)
    if not rows:
        sys.exit("No data. Run: python ingest/nba_games.py " + a.season)
    games = slate(dt.date.fromisoformat(a.date))
    if not games:
        sys.exit(f"No upcoming games on {a.date}.")
    inj = injuries()
    plays = project(rows, games, inj, use_lift=a.lift)
    print(f"Slate {a.date}: " + ", ".join(f"{x}@{y}" for x, y in games))
    for title, key in (("PROJECTS ABOVE NORM", lambda p: -p["z"]), ("PROJECTS BELOW NORM", lambda p: p["z"])):
        print(f"\n== {title} (ranked by z x confidence) ==")
        print(f"{'player':22}{'tm':4}{'opp':5}{'stat':5}{'proj':>6}{'norm':>6}{'z':>6}{'conf':>6}{'min':>5}{'oppF':>6}{'n':>4}{'lift':>6}  out(games w/o)")
        ranked = sorted(plays, key=lambda p: key(p) * (0.5 + p["conf"]))
        for p in ranked[:a.top]:
            tag = " DTD" if p["dtd"] else ""
            print(f"{p['player'][:21]:22}{p['team']:4}{p['opp']:5}{p['stat']:5}{p['proj']:6.1f}{p['season']:6.1f}"
                  f"{p['z']:6.2f}{p['conf']:6.2f}{p['proj_min']:5.0f}{p['opp_f']:6.2f}{p['n']:4}{p['lift']:6.1f}  {p['who']}{tag}")


if __name__ == "__main__":
    main()
