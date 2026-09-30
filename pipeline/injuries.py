"""Injury lists from ESPN game summaries for today's NHL and MLB games
(WNBA injuries live in wnba.json, NFL in nfl.json)."""
from __future__ import annotations

import datetime as dt

from . import common as C
from . import espn


def run(day: dt.date) -> dict:
    out: dict = {"date": day.isoformat(), "leagues": {}}
    n = 0
    for league in ("NHL", "MLB"):
        games = []
        try:
            for g in espn.scoreboard(league, day):
                try:
                    s = espn.summary(league, g["espn_id"])
                    games.append({"game": g["name"], "start_et": g["start_et"], "injuries": s["injuries"],
                                  "espn_projection": s["predictor"]})
                    n += 1
                except Exception as e:  # noqa: BLE001
                    games.append({"game": g["name"], "error": str(e)})
        except Exception as e:  # noqa: BLE001
            out["leagues"][league] = {"error": str(e)}
            continue
        out["leagues"][league] = games
    C.write("injuries", out, day)
    if all(isinstance(v, dict) and "error" in v for v in out["leagues"].values()):
        return {"status": "error", "error": "; ".join(v["error"] for v in out["leagues"].values())[:500]}
    return {"status": "ok", "games": n}
