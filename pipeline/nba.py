"""NBA via ESPN (same shape as wnba.json): today's games, ESPN line + win projection, injuries,
team stats and last-10 logs for each team's stat leaders. Held off until the regular season
(START_DATES in odds.py), so no preseason games are pulled."""
from __future__ import annotations

import datetime as dt

from .odds import START_DATES
from .wnba import pull


def run(day: dt.date) -> dict:
    start = START_DATES.get("NBA")
    if start and day < start:
        return {"status": "skipped", "reason": f"NBA held until {start.isoformat()} (regular season)"}
    return pull("NBA", day)
