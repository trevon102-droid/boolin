"""WNBA and NBA via ESPN: today's games, series notes, ESPN line + win projection, injuries,
and last-10 game logs for each team's stat leaders (for points/rebounds/assists props).
`pull(league, day)` does the work; `run(day)` is the WNBA source and pipeline/nba.py the NBA one."""
from __future__ import annotations

import datetime as dt

from . import common as C
from . import espn

KEEP = ("points", "totalRebounds", "rebounds", "assists", "minutes", "turnovers", "steals", "blocks",
        "threePointFieldGoalsMade-threePointFieldGoalsAttempted", "fieldGoalsMade-fieldGoalsAttempted",
        "PTS", "REB", "AST", "MIN", "3PT", "FG", "TO")


def _team_stats(league: str, team_id: str) -> dict:
    lg = espn.LEAGUES[league][1]
    try:
        d = C.get_json(f"{espn.SITE}/basketball/{lg}/teams/{team_id}/statistics", {"seasontype": "2"})  # regular season
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}
    out = {}
    for cat in ((d.get("results") or {}).get("stats") or {}).get("categories", []):
        for s in cat.get("stats", []):
            if s.get("name") in ("avgPoints", "avgPointsAgainst", "fieldGoalPct", "threePointFieldGoalPct",
                                 "avgRebounds", "avgAssists", "avgTurnovers", "paceFactor"):
                out[s["name"]] = s.get("value")
    return out


def pull(league: str, day: dt.date) -> dict:
    games = espn.scoreboard(league, day)
    for g in games:
        try:
            s = espn.summary(league, g["espn_id"])
        except Exception as e:  # noqa: BLE001
            g["summary_error"] = str(e)
            continue
        g["injuries"] = s["injuries"]
        g["espn_projection"] = s["predictor"]
        players = {}
        for abbr, leaders in s["leaders"].items():
            for ld in leaders[:3]:
                try:
                    log = espn.gamelog(league, ld["id"], last=10)
                    players[ld["name"]] = {"team": abbr, "last10": [
                        {k: v for k, v in row.items() if k in ("date", "opp", "season_type") or k in KEEP}
                        for row in log
                    ]}
                except Exception as e:  # noqa: BLE001
                    players[ld["name"]] = {"team": abbr, "error": str(e)}
        g["player_logs"] = players
        for side in ("away", "home"):
            if g.get(side, {}).get("id"):
                g[side]["team_stats"] = _team_stats(league, g[side]["id"])
    preseason = any("preseason" in (g.get("note") or "").lower() for g in games)
    C.write(league.lower(), {"date": day.isoformat(), "games": games, **({"preseason_note": True} if preseason else {})}, day)
    return {"status": "ok", "games": len(games)}


def run(day: dt.date) -> dict:
    return pull("WNBA", day)
