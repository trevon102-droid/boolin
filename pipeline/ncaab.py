"""Men's college basketball (Division I) via ESPN: today's games with AP rank, records, neutral site,
conference game, ESPN line, ESPN win projection, injuries and team stats.

Main lines only (sides, totals, moneylines): college player props are not offered to Kaire, so no player
game logs are pulled. Opening night can have 150+ games, so per-game summaries and team stats are capped
(ranked games and games with an ESPN line first). Held off until the regular season (START_DATES in odds.py).
"""
from __future__ import annotations

import datetime as dt
import os

from . import common as C
from . import espn
from .odds import START_DATES
from .wnba import _team_stats

MAX_DETAIL = int(os.environ.get("NCAAB_MAX_DETAIL", "60"))


def _priority(g: dict) -> tuple:
    ranked = bool((g.get("away") or {}).get("rank") or (g.get("home") or {}).get("rank"))
    has_line = bool((g.get("espn_line") or {}).get("details"))
    return (not ranked, not has_line, _minutes(g.get("start_et")))


def _minutes(label: str | None) -> int:
    """'7:30 PM ET' -> minutes after midnight, so games sort by tip time (unknown times last)."""
    import re
    m = re.match(r"\s*(\d{1,2}):(\d{2})\s*(AM|PM)", label or "", re.I)
    if not m:
        return 24 * 60
    return (int(m.group(1)) % 12 + (12 if m.group(3).upper() == "PM" else 0)) * 60 + int(m.group(2))


def run(day: dt.date) -> dict:
    start = START_DATES.get("NCAAB")
    if start and day < start:
        return {"status": "skipped", "reason": f"NCAAB held until {start.isoformat()} (regular season)"}
    comps = C.Components({"scoreboard": "ESPN", "summaries": "ESPN", "team_stats": "ESPN"})
    games = comps.run("scoreboard", espn.scoreboard, "NCAAB", day)
    if games is None:
        raise RuntimeError(f"ESPN NCAAB scoreboard failed: {comps.errors.get('scoreboard')}")
    detail = sorted(games, key=_priority)[:MAX_DETAIL]
    sum_errors = 0
    for g in detail:
        try:
            s = espn.summary("NCAAB", g["espn_id"])
            g["injuries"] = s["injuries"]
            g["espn_projection"] = s["predictor"]
        except Exception as e:  # noqa: BLE001
            g["summary_error"] = str(e)
            sum_errors += 1
        for side in ("away", "home"):
            if (g.get(side) or {}).get("id"):
                g[side]["team_stats"] = _team_stats("NCAAB", g[side]["id"])
    if detail:
        if sum_errors == len(detail):
            comps.mark("summaries", C.ERROR, "every game summary failed")
        else:
            comps.mark("summaries", C.OK, f"{sum_errors} of {len(detail)} summaries failed" if sum_errors else None)
        comps.mark("team_stats", C.OK)
    payload = {
        "date": day.isoformat(),
        "note": ("Main lines only: college player props aren't offered. Games sorted ranked-first; "
                 f"injuries/projection/team stats only for the first {MAX_DETAIL} (ranked, then lined)."),
        "games": sorted(games, key=_priority),
        "games_ranked": sum(1 for g in games if (g.get("away") or {}).get("rank") or (g.get("home") or {}).get("rank")),
        "components": dict(comps.status),
    }
    C.write("ncaab", payload, day)
    return comps.result(games=len(games), detailed=len(detail), ranked=payload["games_ranked"])
