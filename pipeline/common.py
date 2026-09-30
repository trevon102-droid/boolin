"""Shared helpers: dates in ET, a polite HTTP session with retries, and JSON output."""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "boolin-data/1.0 (github actions; personal research)"})


def now_et() -> dt.datetime:
    return dt.datetime.now(ET)


def slate_date() -> dt.date:
    """The ET calendar day being pulled. Override with SLATE_DATE=YYYY-MM-DD."""
    override = os.environ.get("SLATE_DATE")
    if override:
        return dt.date.fromisoformat(override)
    return now_et().date()


def to_et(iso_utc: str | None) -> str | None:
    """'2026-09-30T23:00:00Z' -> '7:00 PM ET'."""
    if not iso_utc:
        return None
    try:
        t = dt.datetime.fromisoformat(iso_utc.replace("Z", "+00:00")).astimezone(ET)
        return t.strftime("%-I:%M %p ET")
    except ValueError:
        return None


def get(url: str, params: dict | None = None, *, retries: int = 3, timeout: int = 25) -> requests.Response:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            r = SESSION.get(url, params=params, timeout=timeout)
            if r.status_code == 429 or r.status_code >= 500:
                raise requests.HTTPError(f"{r.status_code} from {url}")
            r.raise_for_status()
            return r
        except (requests.RequestException,) as e:  # noqa: PERF203
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET failed after {retries} tries: {url} ({last})")


def get_json(url: str, params: dict | None = None, **kw) -> Any:
    return get(url, params, **kw).json()


def write(name: str, payload: Any, day: dt.date) -> Path:
    """Write data/days/<day>/<name>.json and data/latest/<name>.json."""
    body = json.dumps(payload, indent=1, ensure_ascii=False, default=str)
    out = DATA / "days" / day.isoformat() / f"{name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body)
    latest = DATA / "latest" / f"{name}.json"
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(body)
    return out


# ---------- odds math (same formulas the Sharp Board uses) ----------

def implied(american: float | None) -> float | None:
    if american is None:
        return None
    a = float(american)
    if -100 < a < 100:
        return None
    return -a / (-a + 100) if a < 0 else 100 / (a + 100)


def to_american(p: float | None) -> int | None:
    if p is None or not 0 < p < 1:
        return None
    return -round(100 * p / (1 - p)) if p >= 0.5 else round(100 * (1 - p) / p)


def decimal_to_american(d: float) -> int:
    return round((d - 1) * 100) if d >= 2 else round(-100 / (d - 1))


def devig(prices: list[float]) -> list[float] | None:
    """Multiplicative devig of a full set of American prices (2- or 3-way)."""
    ps = [implied(p) for p in prices]
    if any(p is None for p in ps):
        return None
    total = sum(ps)  # type: ignore[arg-type]
    return [p / total for p in ps]  # type: ignore[operator]


def season_year(day: dt.date, starts_month: int) -> int:
    """Season label for leagues whose season spans years (NHL: Oct start -> label by start year)."""
    return day.year if day.month >= starts_month else day.year - 1
