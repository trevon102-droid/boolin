"""Bounce-back / regression scan on last-game shooting.

For each player, compare the LAST game to their season averages over earlier games
(last game excluded so it doesn't pollute its own baseline):

  BOUNCE_BACK : last FGA >= avg FGA + 3  AND  last FG% <= avg FG% - 15 pts
                (took a lot of shots, cold night -> expect shooting to revert up)
  REGRESSION  : last FGA >= avg FGA + 3  AND  last FG% >= avg FG% + 15 pts
                (hot night on high volume -> expect shooting to revert down)

Season FG% is total FGM / total FGA (volume-weighted), not an average of game percentages.
Usage: python scans/bounce_back.py [--season 2026-27] [--min-games 5] [--min-avg-fga 6]
"""
import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ingest import common as C  # noqa: E402

FGA_EDGE = 3.0
PCT_EDGE = 0.15


def classify(last_fga, last_fgm, avg_fga, avg_pct):
    """Return 'BOUNCE_BACK', 'REGRESSION' or None for one player's last game."""
    if last_fga < avg_fga + FGA_EDGE or last_fga == 0:
        return None
    pct = last_fgm / last_fga
    if pct <= avg_pct - PCT_EDGE:
        return "BOUNCE_BACK"
    if pct >= avg_pct + PCT_EDGE:
        return "REGRESSION"
    return None


def scan(con, season, min_games=5, min_avg_fga=6.0, max_age_days=3):
    rows = con.execute(
        "SELECT player_id, player, team, opp, game_date, fgm, fga FROM player_games "
        "WHERE season=? AND minutes > 0 ORDER BY player_id, game_date", (season,)).fetchall()
    if not rows:
        return []
    latest = max(r[4] for r in rows)
    by_player = {}
    for r in rows:
        by_player.setdefault(r[0], []).append(r)
    out = []
    for games in by_player.values():
        *prior, last = games
        if len(prior) < min_games:
            continue
        # skip players whose last game is stale (injured/traded away)
        if (_d(latest) - _d(last[4])).days > max_age_days:
            continue
        sum_fga = sum(g[6] for g in prior)
        if sum_fga == 0:
            continue
        avg_fga = sum_fga / len(prior)
        avg_pct = sum(g[5] for g in prior) / sum_fga
        if avg_fga < min_avg_fga:
            continue
        flag = classify(last[6], last[5], avg_fga, avg_pct)
        if flag:
            out.append({"flag": flag, "player": last[1], "team": last[2], "opp": last[3], "date": last[4],
                        "last": f"{last[5]}-{last[6]}", "last_pct": last[5] / last[6],
                        "avg_fga": avg_fga, "avg_pct": avg_pct, "games": len(prior)})
    out.sort(key=lambda x: (x["flag"], x["player"]))
    return out


def _d(s):
    import datetime as dt
    return dt.date.fromisoformat(s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2026-27")
    ap.add_argument("--min-games", type=int, default=5)
    ap.add_argument("--min-avg-fga", type=float, default=6.0)
    a = ap.parse_args()
    con = C.connect()
    try:
        res = scan(con, a.season, a.min_games, a.min_avg_fga)
    except sqlite3.OperationalError:
        sys.exit("No data yet. Run: python ingest/nba_games.py " + a.season)
    if not res:
        print("No flags.")
        return
    for flag in ("BOUNCE_BACK", "REGRESSION"):
        sub = [r for r in res if r["flag"] == flag]
        print(f"\n== {flag} ({len(sub)}) ==")
        print(f"{'player':24}{'tm':4}{'opp':5}{'date':12}{'last FG':9}{'last%':7}{'avgFGA':8}{'avg%':7}{'n':>3}")
        for r in sub:
            print(f"{r['player'][:23]:24}{r['team']:4}{r['opp']:5}{r['date']:12}{r['last']:9}"
                  f"{r['last_pct']*100:5.1f}% {r['avg_fga']:6.1f}  {r['avg_pct']*100:5.1f}%{r['games']:>3}")


if __name__ == "__main__":
    main()
