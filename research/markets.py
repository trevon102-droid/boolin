"""Market history: opening / current / closing lines kept as an append-only record.

An *observation* is what one odds pull saw for one game:
    {"t": ISO ET time of the pull,
     "moneyline": {"away": side, "home": side, "fair_home": p, "ref": "pinnacle"|"consensus (n books)"},
     "spread":    {"home_line": -3.5, "away": side, "home": side, "fair_home": p, "ref": ...},
     "total":     {"line": 47.5, "over": side, "under": side, "fair_over": p, "ref": ...}}
with side = {"fanduel": price, "draftkings": price, "pinnacle": price, "best": price, "best_book": book}.

Price meanings are kept apart on purpose:
  fanduel / draftkings  a specific book's price (fanduel is the actionable one for this desk)
  best / best_book      the best price across all tracked books: market context, NOT a price
                        the analyst can necessarily get
  fair_*                no-vig probability from Pinnacle (else consensus): an estimate, not a price

"Open" is the first time Boolin saw the market (the true opener isn't available on the free feed).
"Close" is the last observation before start time; pulls are sparse, so it is labeled
"last seen before start", not the exchange's official close.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import timeutil as T

BOOKS = ("fanduel", "draftkings", "pinnacle")
ACTIONABLE_BOOK = "fanduel"


# ---------------- odds math (same formulas as pipeline/common.py) ----------------

def implied(american) -> float | None:
    if american is None:
        return None
    a = float(american)
    if -100 < a < 100:
        return None
    return -a / (-a + 100) if a < 0 else 100 / (a + 100)


def decimal(american) -> float | None:
    if american is None:
        return None
    a = float(american)
    if -100 < a < 100:
        return None
    return 1 + (a / 100 if a > 0 else 100 / -a)


def to_american(p: float | None) -> int | None:
    if p is None or not 0 < p < 1:
        return None
    return -round(100 * p / (1 - p)) if p >= 0.5 else round(100 * (1 - p) / p)


def devig(prices: list) -> list[float] | None:
    ps = [implied(p) for p in prices]
    if not ps or any(p is None for p in ps):
        return None
    s = sum(ps)
    return [p / s for p in ps]


# ---------------- observations ----------------

def _side(outcome: dict | None) -> dict:
    if not outcome:
        return {}
    books = outcome.get("books") or {}
    out = {b: books.get(b) for b in BOOKS}
    out["best"] = outcome.get("best_price")
    out["best_book"] = outcome.get("best_book")
    return out


def _find(outcomes: list, name: str) -> dict | None:
    return next((o for o in outcomes if o.get("name") == name), None)


def observation(ev: dict, pulled_at: str) -> dict:
    """One odds.json event -> one observation. Missing markets stay missing (no filling)."""
    mk = ev.get("markets") or {}
    obs: dict = {"t": pulled_at}
    ml = mk.get("moneyline")
    if ml:
        a, h = _find(ml["outcomes"], ev["away"]), _find(ml["outcomes"], ev["home"])
        draw = _find(ml["outcomes"], "Draw")
        obs["moneyline"] = {"away": _side(a), "home": _side(h), "ref": ml.get("ref"),
                            "fair_home": (h or {}).get("fair_prob"), "fair_away": (a or {}).get("fair_prob")}
        if draw:
            obs["moneyline"]["draw"] = _side(draw)
            obs["moneyline"]["fair_draw"] = draw.get("fair_prob")
    sp = mk.get("spread")
    if sp and sp.get("home_line") is not None:
        a, h = _find(sp["outcomes"], ev["away"]), _find(sp["outcomes"], ev["home"])
        obs["spread"] = {"home_line": sp["home_line"], "away": _side(a), "home": _side(h), "ref": sp.get("ref"),
                         "fair_home": (h or {}).get("fair_prob")}
    tot = mk.get("total")
    if tot and tot.get("line") is not None:
        o, u = _find(tot["outcomes"], "Over"), _find(tot["outcomes"], "Under")
        obs["total"] = {"line": tot["line"], "over": _side(o), "under": _side(u), "ref": tot.get("ref"),
                        "fair_over": (o or {}).get("fair_prob")}
    return obs


def _comparable(obs: dict) -> str:
    return json.dumps({k: v for k, v in obs.items() if k != "t"}, sort_keys=True)


# ---------------- history store ----------------

class MarketHistory:
    """data/research/markets/<game date ET>.json : {game_key: {"meta": {...}, "obs": [...]}}.

    Observations are appended, never rewritten. An identical repeat only moves `last_seen` on
    the latest observation. Observations at or after start time are not recorded (live odds
    are a different market), so the last stored observation is the pre-game close.
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self._cache: dict[str, dict] = {}
        self._dirty: set[str] = set()

    def _file(self, date: str) -> Path:
        return self.root / f"{date}.json"

    def day(self, date: str) -> dict:
        if date not in self._cache:
            f = self._file(date)
            self._cache[date] = json.loads(f.read_text()) if f.exists() else {}
        return self._cache[date]

    def record(self, game_key: str, date: str, meta: dict, obs: dict) -> bool:
        """Append obs. Returns True when stored as a new observation."""
        start = T.parse(meta.get("commence_time"))
        t = T.parse(obs.get("t"))
        if not t:
            raise ValueError("observation needs a timezone-aware timestamp")
        if start and t >= start:
            return False
        entry = self.day(date).setdefault(game_key, {"meta": meta, "obs": []})
        entry["meta"] = {**entry["meta"], **meta}
        series = entry["obs"]
        if series:
            last_t = T.parse(series[-1].get("last_seen") or series[-1]["t"])
            if last_t and t <= last_t:
                return False  # older or same pull replayed: history is append-only
            if _comparable(series[-1]) == _comparable(obs):
                series[-1]["last_seen"] = obs["t"]
                self._dirty.add(date)
                return False
        series.append(dict(obs))
        self._dirty.add(date)
        return True

    def get(self, game_key: str, date: str) -> dict | None:
        return self.day(date).get(game_key)

    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for date in sorted(self._dirty):
            from pipeline.common import finite
            self._file(date).write_text(json.dumps(finite(self._cache[date]), indent=1, sort_keys=True, allow_nan=False))
        self._dirty.clear()


# ---------------- open / current / close ----------------

def _point(obs: dict | None, kind: str) -> dict | None:
    """The part of an observation the summary table needs, with its timestamp."""
    if not obs:
        return None
    m = obs.get(kind)
    if not m:
        return None
    t = obs.get("last_seen") or obs["t"]
    if kind == "moneyline":
        return {"t": obs["t"], "last_seen": t, "away": m["away"], "home": m["home"],
                "fair_home": m.get("fair_home"), "ref": m.get("ref")}
    if kind == "spread":
        return {"t": obs["t"], "last_seen": t, "home_line": m["home_line"], "away": m["away"], "home": m["home"],
                "fair_home": m.get("fair_home"), "ref": m.get("ref")}
    return {"t": obs["t"], "last_seen": t, "line": m["line"], "over": m["over"], "under": m["under"],
            "fair_over": m.get("fair_over"), "ref": m.get("ref")}


def lines(entry: dict | None, now: str) -> dict:
    """{moneyline|spread|total: {open, current, close, movement}} from one game's history."""
    out: dict = {}
    if not entry:
        return out
    series = entry.get("obs") or []
    start = T.parse((entry.get("meta") or {}).get("commence_time"))
    started = bool(start and T.parse(now) and T.parse(now) >= start)
    for kind in ("moneyline", "spread", "total"):
        pts = [o for o in series if o.get(kind)]
        if not pts:
            out[kind] = {"available": False, "reason": "no observation of this market"}
            continue
        first, last = _point(pts[0], kind), _point(pts[-1], kind)
        rec = {"available": True, "open": first, "current": last, "observations": len(pts),
               "close": last if started else None,
               "close_note": ("last observation before start (Boolin's pulls are periodic, so this "
                              "is not the official closing number)") if started else "game not started",
               "movement": movement(kind, first, last)}
        out[kind] = rec
    out["series"] = [{"t": o["t"],
                      "spread_home": (o.get("spread") or {}).get("home_line"),
                      "total": (o.get("total") or {}).get("line"),
                      "fair_home": (o.get("moneyline") or {}).get("fair_home"),
                      "fd_home_ml": ((o.get("moneyline") or {}).get("home") or {}).get(ACTIONABLE_BOOK)}
                     for o in series]
    return out


def movement(kind: str, a: dict | None, b: dict | None) -> dict:
    """Line, actionable price and fair-probability movement between two points."""
    if not a or not b:
        return {}
    mv: dict = {}
    if kind == "spread":
        mv["home_line"] = round(b["home_line"] - a["home_line"], 2)
        mv["toward"] = ("home" if mv["home_line"] < 0 else "away") if mv["home_line"] else None
    if kind == "total":
        mv["line"] = round(b["line"] - a["line"], 2)
        mv["toward"] = ("over" if mv["line"] > 0 else "under") if mv["line"] else None
    fair_key = "fair_over" if kind == "total" else "fair_home"
    if a.get(fair_key) is not None and b.get(fair_key) is not None:
        mv[fair_key] = round(b[fair_key] - a[fair_key], 4)
    sides = ("over", "under") if kind == "total" else ("away", "home")
    for s in sides:
        pa, pb = (a.get(s) or {}).get(ACTIONABLE_BOOK), (b.get(s) or {}).get(ACTIONABLE_BOOK)
        if pa is not None and pb is not None and pa != pb:
            mv[f"{ACTIONABLE_BOOK}_{s}"] = {"from": pa, "to": pb,
                                            "implied_change": round(implied(pb) - implied(pa), 4)}
    return mv


# ---------------- CLV ----------------

def clv(position: dict, close: dict | None, kind: str) -> dict:
    """Closing line value for an analyst position.

    position = {"market": "moneyline"|"spread"|"total", "side": "home"|"away"|"over"|"under",
                "line": number (spread: that side's line; total: the total), "price": american}
    close = the `close` point from `lines()` for the same market.
    """
    if not close:
        return {"available": False, "reason": "no closing observation"}
    side = position.get("side")
    price = position.get("price")
    if side not in (close or {}) or implied(price) is None:
        return {"available": False, "reason": "position side or price missing"}
    out: dict = {"available": True, "side": side, "taken": {"line": position.get("line"), "price": price}}
    close_side = close[side] or {}
    close_price = close_side.get(ACTIONABLE_BOOK)
    if kind == "spread":
        close_line = close["home_line"] if side == "home" else -close["home_line"]
        out["close"] = {"line": close_line, "price": close_price}
        if position.get("line") is not None:
            out["line_clv_pts"] = round(position["line"] - close_line, 2)  # + = got more points than close
    elif kind == "total":
        out["close"] = {"line": close["line"], "price": close_price}
        if position.get("line") is not None:
            d = close["line"] - position["line"]
            out["line_clv_pts"] = round(d if side == "over" else -d, 2)
    else:
        out["close"] = {"price": close_price}
    same_line = kind == "moneyline" or out.get("line_clv_pts") == 0
    if close_price is not None and same_line:
        out["price_clv"] = round(decimal(price) / decimal(close_price) - 1, 4)
    fair_key = {"moneyline": "fair_home", "spread": "fair_home", "total": "fair_over"}[kind]
    fair = close.get(fair_key)
    if fair is not None and same_line:
        p_side = fair if side in ("home", "over") else 1 - fair
        out["prob_clv"] = round(p_side - implied(price), 4)  # close no-vig prob minus price you paid
    if not same_line:
        out["note"] = "Line moved; price CLV compares different numbers, so only line CLV is reported."
    return out
