"""Ingest NBA player box scores from ESPN into SQLite (player_games).

Usage: python ingest/nba_games.py 2026-27 [--from YYYY-MM-DD]
Incremental: games already stored are skipped. Only final games are stored.
Fields verified against a live ESPN summary response (boxscore.players[].statistics[]).
"""
import argparse
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ingest import common as C  # noqa: E402

SITE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"

SCHEMA = """
CREATE TABLE IF NOT EXISTS player_games (
  game_id TEXT, game_date TEXT, season TEXT, team TEXT, opp TEXT, home INTEGER,
  player_id TEXT, player TEXT, minutes INTEGER, pts INTEGER,
  fgm INTEGER, fga INTEGER, fg3m INTEGER, fg3a INTEGER, ftm INTEGER, fta INTEGER,
  reb INTEGER, ast INTEGER, starter INTEGER,
  PRIMARY KEY (game_id, player_id)
);
CREATE INDEX IF NOT EXISTS ix_pg_player ON player_games(player_id, game_date);
"""


def season_bounds(season):
    """'2026-27' -> (2026-10-01, 2027-06-30). ESPN labels this season year 2027."""
    start_year = int(season.split("-")[0])
    return dt.date(start_year, 10, 1), dt.date(start_year + 1, 6, 30)


def split(s):
    """'5-13' -> (5, 13)."""
    a, b = s.split("-")
    return int(a), int(b)


def num(s):
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


def final_games(day):
    d = C.get_json(f"{SITE}/scoreboard", {"dates": day.strftime("%Y%m%d")})
    # season.type: 1 preseason, 2 regular season, 3 playoffs (verified on live scoreboard). Regular season only.
    return [e["id"] for e in d.get("events", [])
            if e["status"]["type"].get("completed") and e["season"]["type"] == 2]


def parse_box(game_id, season):
    d = C.get_json(f"{SITE}/summary", {"event": game_id})
    comp = d["header"]["competitions"][0]
    date = comp["date"][:10] if "date" in comp else d["header"].get("date", "")[:10]
    teams = {c["team"]["id"]: c for c in comp["competitors"]}
    abbr = {tid: c["team"]["abbreviation"] for tid, c in teams.items()}
    rows = []
    for tp in d["boxscore"]["players"]:
        tid = tp["team"]["id"]
        opp = next(a for t, a in abbr.items() if t != tid)
        home = 1 if teams[tid].get("homeAway") == "home" else 0
        for grp in tp["statistics"]:
            keys = grp["keys"]
            for a in grp["athletes"]:
                if a.get("didNotPlay") or not a.get("stats"):
                    continue
                st = dict(zip(keys, a["stats"]))
                fgm, fga = split(st["fieldGoalsMade-fieldGoalsAttempted"])
                m3, a3 = split(st["threePointFieldGoalsMade-threePointFieldGoalsAttempted"])
                ftm, fta = split(st["freeThrowsMade-freeThrowsAttempted"])
                rows.append((game_id, date, season, abbr[tid], opp, home,
                             a["athlete"]["id"], a["athlete"]["displayName"],
                             num(st["minutes"]), num(st["points"]), fgm, fga, m3, a3, ftm, fta,
                             num(st["rebounds"]), num(st["assists"]), 1 if a.get("starter") else 0))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("season", help="e.g. 2026-27")
    ap.add_argument("--from", dest="start", help="override start date YYYY-MM-DD")
    args = ap.parse_args()

    start, end = season_bounds(args.season)
    if args.start:
        start = dt.date.fromisoformat(args.start)
    end = min(end, dt.date.today())

    con = C.connect()
    con.executescript(SCHEMA)
    have = {r[0] for r in con.execute("SELECT DISTINCT game_id FROM player_games")}
    new_games = new_rows = 0
    day = start
    while day <= end:
        for gid in final_games(day):
            if gid in have:
                continue
            try:
                rows = parse_box(gid, args.season)
            except Exception as e:  # keep going; report at end
                print(f"  ! game {gid} ({day}) failed: {e}")
                continue
            con.executemany("INSERT OR REPLACE INTO player_games VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            con.commit()
            new_games += 1
            new_rows += len(rows)
        day += dt.timedelta(days=1)
    total = con.execute("SELECT COUNT(DISTINCT game_id), COUNT(*) FROM player_games").fetchone()
    print(f"{args.season}: +{new_games} games, +{new_rows} player rows (db total {total[0]} games, {total[1]} rows)")


if __name__ == "__main__":
    main()
