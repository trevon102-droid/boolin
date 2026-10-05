"""Comparable-game research. Everything here is HISTORICAL CONTEXT: how similar spots went in
the past, never proof of how this game will go.

NFL uses nflverse's game history (closing spread/total and results since 2012), which the
pipeline caches at data/reference/nfl_games_history.json.gz. Other leagues use Boolin's own
archive of graded research cards, which starts empty and grows daily; until it has enough games
the section says so instead of showing a thin trend.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

LABEL = "HISTORICAL CONTEXT"
MIN_N = 30


def load_nfl_history(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f).get("games", [])


def _stats(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0}
    ats = [r["result"] - r["spread_line"] for r in rows if r.get("spread_line") is not None]
    covers = sum(1 for x in ats if x > 0)
    pushes = sum(1 for x in ats if x == 0)
    tots = [r["total"] - r["total_line"] for r in rows if r.get("total_line") is not None and r.get("total") is not None]
    overs = sum(1 for x in tots if x > 0)
    tpush = sum(1 for x in tots if x == 0)
    seasons = sorted({r["season"] for r in rows})
    out = {"n": n, "seasons": f"{seasons[0]}–{seasons[-1]}",
           "home_cover_pct": round(covers / max(1, len(ats) - pushes), 3) if ats else None,
           "avg_home_margin_vs_spread": round(sum(ats) / len(ats), 2) if ats else None,
           "over_pct": round(overs / max(1, len(tots) - tpush), 3) if tots else None,
           "avg_total_vs_line": round(sum(tots) / len(tots), 2) if tots else None,
           "recent": [{"game": f"{r['season']} wk{r['week']} {r['away']} @ {r['home']}",
                       "spread_home": -r["spread_line"], "result_home_margin": r["result"],
                       "total_line": r.get("total_line"), "total": r.get("total")}
                      for r in sorted(rows, key=lambda r: (r["season"], r["week"]))[-6:]]}
    if n < MIN_N:
        out["warning"] = f"Only {n} comparable games: too few to read anything into."
    return out


def nfl_criteria(card: dict) -> list[dict]:
    """Which situations describe this game, from stored data only."""
    crit = []
    sp = (((card.get("market") or {}).get("spread") or {}).get("current") or {}).get("home_line")
    if sp is not None:
        fav = -sp  # nflverse spread_line: positive = home favored
        lo, hi = (fav - 1.5, fav + 1.5)
        crit.append({"id": "spread_band", "label": f"Home side priced {sp:+g} (±1.5)",
                     "test": lambda r, lo=lo, hi=hi: r.get("spread_line") is not None and lo <= r["spread_line"] <= hi})
    r = card.get("research") or {}
    hr, ar = ((r.get("home") or {}).get("rest_days") or {}).get("value"), ((r.get("away") or {}).get("rest_days") or {}).get("value")
    if hr is not None and ar is not None and abs(hr - ar) >= 3:
        sign = 1 if hr > ar else -1
        crit.append({"id": "rest_gap", "label": f"Home {'rest advantage' if sign > 0 else 'rest disadvantage'} of 3+ days",
                     "test": lambda x, s=sign: x.get("home_rest") is not None and x.get("away_rest") is not None
                     and (x["home_rest"] - x["away_rest"]) * s >= 3})
    w = ((card.get("environment") or {}).get("wind_mph") or {}).get("value")
    if (card.get("environment") or {}).get("outdoors") and w is not None and w >= 15:
        crit.append({"id": "high_wind", "label": "Wind 15+ mph",
                     "test": lambda x: x.get("wind") is not None and x["wind"] >= 15})
    if ((card.get("game") or {}).get("context") or {}).get("division_game"):
        crit.append({"id": "division", "label": "Division game", "test": lambda x: bool(x.get("div_game"))})
    return crit


def nfl(card: dict, history: list[dict]) -> dict:
    unsupported = ["Similar EPA profile / opponent defense / rush-vs-rush-defense matchups: the history file has "
                   "no per-game team EPA yet, so these comparisons aren't computed."]
    if not history:
        return {"label": LABEL, "available": False, "unsupported": unsupported,
                "reason": "NFL history file not built yet (data/reference/nfl_games_history.json.gz)."}
    crit = nfl_criteria(card)
    groups = []
    for c in crit:
        rows = [r for r in history if c["test"](r)]
        groups.append({"situation": c["label"], **_stats(rows)})
    if len(crit) >= 2:
        rows = [r for r in history if all(c["test"](r) for c in crit)]
        groups.append({"situation": "All of: " + "; ".join(c["label"] for c in crit), **_stats(rows)})
    return {"label": LABEL, "available": True, "source": "nflverse games (closing lines and results)",
            "situations": groups, "unsupported": unsupported,
            "caution": "Past results in similar spots; the market already prices most of these situations."}


def archive(card: dict, graded: list[dict]) -> dict:
    """NHL/MLB/other: comparables from Boolin's own graded cards (grows over time)."""
    lg = card["league"]
    same = [g for g in graded if g.get("league") == lg and g.get("result")]
    crit = []
    if lg == "NHL":
        b2b = {s: ((card.get("research") or {}).get(s) or {}).get("back_to_back", {}).get("value") for s in ("home", "away")}
        for s, f in b2b.items():
            if f:
                crit.append((f"{s} team on a back-to-back", lambda g, s=s: (g.get("situations") or {}).get(f"{s}_b2b")))
    if lg == "MLB":
        for s in ("home", "away"):
            if ((card.get("research") or {}).get(s) or {}).get("bullpen", {}).get("pitches_last3", 0) >= 120:
                crit.append((f"{s} bullpen taxed (120+ pitches, 3 days)",
                             lambda g, s=s: (g.get("situations") or {}).get(f"{s}_pen_taxed")))
    groups = []
    for label, test in crit:
        rows = [g for g in same if test(g)]
        n = len(rows)
        hw = sum(1 for g in rows if g["result"]["home_margin"] > 0)
        groups.append({"situation": label, "n": n, "home_win_pct": round(hw / n, 3) if n else None,
                       **({"warning": f"Only {n} archived games."} if n < MIN_N else {})})
    return {"label": LABEL, "available": bool(groups), "source": "Boolin graded research archive",
            "archive_games": len(same), "situations": groups,
            "reason": None if groups else ("No tracked situation applies to this game." if same else
                                           f"Archive has no graded {lg} games yet; comparables start once games are graded.")}
