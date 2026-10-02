"""NBA via ESPN (same shape as wnba.json): today's games, ESPN line + win projection, injuries,
team stats and last-10 logs for each team's stat leaders. Preseason games pull too; in preseason the
team stats are last regular season's (or empty) and minutes are unpredictable."""
from __future__ import annotations

import datetime as dt

from .wnba import pull


def run(day: dt.date) -> dict:
    return pull("NBA", day)
