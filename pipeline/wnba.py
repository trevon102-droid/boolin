"""WNBA via ESPN: today's games, series notes, ESPN line + win projection, injuries,
and last-10 game logs for each team's stat leaders (for points/rebounds/assists props)."""
from __future__ import annotations

import datetime as dt

from . import common as C
from . import espn

KEEP = ("PTS", "REB", "AST", "3PM", "MIN", "FG", "3PT", "TO", "points", "rebounds", "assists", "minutes")


def _team_stats(team_id: str) -> dict:
    try:
        d = C.get_json(f"{espn.SITE}/basketball/wnba/teams/{team_id}/statistics")
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}
    out = {}
    for cat in ((d.get("results") or {}).get("stats") or {}).get("categories", []):
        for s in cat.get("stats", []):
            if s.get("name") in ("avgPoints", "avgPointsAgainst", "fieldGoalPct", "threePointFieldGoalPct",
                                 "avgRebounds", "avgAssists", "avgTurnovers", "paceFactor"):
                out[s["name"]] = s.get("value")
    return out


def run(day: dt.date) -> dict:
    games = espn.scoreboard("WNBA", day)
    for g in games:
        try:
            s = espn.summary("WNBA", g["espn_id"])
        except Exception as e:  # noqa: BLE001
            g["summary_error"] = str(e)
            continue
        g["injuries"] = s["injuries"]
        g["espn_projection"] = s["predictor"]
        players = {}
        for abbr, leaders in s["leaders"].items():
            for ld in leaders[:3]:
                try:
                    log = espn.gamelog("WNBA", ld["id"], last=10)
                    players[ld["name"]] = {"team": abbr, "last10": [
                        {k: v for k, v in row.items() if k in ("date", "opp", "season_type") or k in KEEP}
                        for row in log
                    ]}
                except Exception as e:  # noqa: BLE001
                    players[ld["name"]] = {"team": abbr, "error": str(e)}
        g["player_logs"] = players
        for side in ("away", "home"):
            if g.get(side, {}).get("id"):
                g[side]["team_stats"] = _team_stats(g[side]["id"])
    C.write("wnba", {"date": day.isoformat(), "games": games}, day)
    return {"status": "ok", "games": len(games)}
