"""Shared helpers: dates in ET, a polite HTTP session with retries, and JSON output."""
from __future__ import annotations

import datetime as dt
import gzip
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
    """Write data/latest/<name>.json (plain) and a gzipped daily snapshot data/days/<day>/<name>.json.gz.

    Snapshots are gzipped (~10x smaller) so the daily history committed to git stays small.
    Every dict payload gets `pulled_at_et` so readers can tell how fresh it is.
    """
    if isinstance(payload, dict) and "pulled_at_et" not in payload:
        payload = {"pulled_at_et": now_et().isoformat(timespec="minutes"), **payload}
    body = json.dumps(payload, indent=1, ensure_ascii=False, default=str)
    out = DATA / "days" / day.isoformat() / f"{name}.json.gz"
    out.parent.mkdir(parents=True, exist_ok=True)
    plain = out.with_suffix("")  # an older plain snapshot of the same file, if any
    if plain.exists():
        plain.unlink()
    with gzip.open(out, "wt", encoding="utf-8", compresslevel=9) as f:
        f.write(body)
    latest = DATA / "latest" / f"{name}.json"
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(body)
    return out


# ---------- health: component-level status so a partial failure never reads as "ok" ----------

OK, PARTIAL, ERROR, SKIPPED = "ok", "partial", "error", "skipped"


class Components:
    """Track each piece of a source.  comps.run("epa", fn, *args) -> fn's result, or None on failure.

    Statuses: ok | error | skipped.  `overall(core=...)` rolls them up into ok / partial / error.
    """

    def __init__(self) -> None:
        self.status: dict[str, str] = {}
        self.errors: dict[str, str] = {}

    def run(self, name: str, fn, *args, **kw):
        try:
            out = fn(*args, **kw)
        except Exception as e:  # noqa: BLE001
            self.mark(name, ERROR, f"{type(e).__name__}: {e}")
            return None
        if self.status.get(name) is None:
            self.mark(name, OK)
        return out

    def mark(self, name: str, status: str, error: str | None = None) -> None:
        # a component that failed once stays failed (e.g. one game's summary out of ten)
        if self.status.get(name) == ERROR and status != ERROR:
            return
        self.status[name] = status
        if error:
            self.errors[name] = error[:300]

    def overall(self, core: tuple[str, ...] = ()) -> str:
        return rollup(self.status, core)

    def result(self, core: tuple[str, ...] = (), **extra) -> dict:
        res = {"status": self.overall(core), "components": dict(self.status), **extra}
        if self.errors:
            res["component_errors"] = dict(self.errors)
        return res


def rollup(components: dict[str, str], core: tuple[str, ...] = ()) -> str:
    """ok if nothing failed; error if a core component failed (or all did); partial otherwise."""
    if not components:
        return OK
    failed = [k for k, v in components.items() if v == ERROR]
    if not failed:
        return OK
    if any(k in core for k in failed) or len(failed) == len(components):
        return ERROR
    return PARTIAL


def find_errors(obj: Any, path: str = "", limit: int = 25) -> list[str]:
    """Paths of every `error` / `*_error` key inside a payload (recursive)."""
    found: list[str] = []

    def walk(o: Any, p: str) -> None:
        if len(found) >= limit:
            return
        if isinstance(o, dict):
            for k, v in o.items():
                kp = f"{p}.{k}" if p else str(k)
                if (k == "error" or str(k).endswith("_error")) and v:
                    found.append(f"{kp}: {str(v)[:120]}")
                else:
                    walk(v, kp)
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, f"{p}[{i}]")

    walk(obj, path)
    return found


def sample_quality(n: int | None, tiny: int = 5, small: int = 10) -> str:
    """Label a sample so tiny early-season numbers aren't read as signal: tiny | small | ok | none."""
    if not n:
        return "none"
    if n < tiny:
        return "tiny"
    if n < small:
        return "small"
    return "ok"


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
