"""Timezone-aware time helpers. Everything analyst-facing is US/Eastern (DST handled by zoneinfo)."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = dt.timezone.utc


def parse(ts: str | None) -> dt.datetime | None:
    """Parse an ISO timestamp ('...Z', '+00:00', '-04:00'). Naive strings are rejected (None):
    a time with no zone can't be placed on the timeline honestly."""
    if not ts or not isinstance(ts, str):
        return None
    try:
        t = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        return None
    return t


def to_et(t: dt.datetime) -> dt.datetime:
    return t.astimezone(ET)


def iso_et(t: dt.datetime | None) -> str | None:
    return to_et(t).isoformat(timespec="minutes") if t else None


def label_et(t: dt.datetime | None, with_day: bool = False) -> str | None:
    """'7:10 PM ET' or 'Mon Oct 5, 7:10 PM ET'."""
    if not t:
        return None
    e = to_et(t)
    clock = e.strftime("%-I:%M %p ET")
    return f"{e.strftime('%a %b %-d')}, {clock}" if with_day else clock


def date_et(t: dt.datetime) -> str:
    return to_et(t).date().isoformat()


def age_minutes(as_of: dt.datetime | None, now: dt.datetime) -> float | None:
    if not as_of:
        return None
    return round(max(0.0, (now - as_of).total_seconds() / 60), 1)


def age_label(minutes: float | None) -> str:
    if minutes is None:
        return "unknown age"
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{round(minutes)} min old"
    if minutes < 48 * 60:
        h = minutes / 60
        return f"{h:.1f} h old" if h < 10 else f"{round(h)} h old"
    return f"{round(minutes / 1440)} d old"
