"""College football (FBS), ranked and unranked.

ESPN (free): every FBS game today with AP rank, neutral site / conference flags, ESPN FPI win
projection, weather, injuries, plus the next 3 days' schedule so a Thursday run can see Saturday.
CollegeFootballData (optional, free key in CFBD_API_KEY): team EPA (PPA) splits and SP+ ratings.
"""
from __future__ import annotations

import datetime as dt
import os

from . import common as C
from . import espn

CFBD = "https://api.collegefootballdata.com"


def ap_poll() -> list[dict]:
    d = C.get_json(f"{espn.SITE}/football/college-football/rankings")
    poll = next((r for r in d.get("rankings", []) if "AP" in (r.get("name") or "")), None) or \
        (d.get("rankings") or [None])[0]
    if not poll:
        return []
    return [{"rank": r.get("current"), "team": (r.get("team") or {}).get("location"),
             "abbr": (r.get("team") or {}).get("abbreviation"), "record": r.get("recordSummary"),
             "prev": r.get("previous")} for r in poll.get("ranks", [])]


def cfbd(season: int) -> dict:
    key = os.environ.get("CFBD_API_KEY")
    if not key:
        return {"status": "skipped", "reason": "CFBD_API_KEY not set (optional)"}
    hdr = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    out: dict = {"status": "ok", "teams": {}}

    def call(path: str, params: dict) -> list:
        r = C.SESSION.get(f"{CFBD}{path}", params=params, headers=hdr, timeout=30)
        r.raise_for_status()
        return r.json()

    try:
        for t in call("/ppa/teams", {"year": season, "excludeGarbageTime": "true"}):
            o, d = t.get("offense") or {}, t.get("defense") or {}
            out["teams"].setdefault(t["team"], {}).update({
                "conf": t.get("conference"),
                "off_epa": o.get("overall"), "off_pass_epa": o.get("passing"), "off_rush_epa": o.get("rushing"),
                "def_epa": d.get("overall"), "def_pass_epa": d.get("passing"), "def_rush_epa": d.get("rushing"),
            })
    except Exception as e:  # noqa: BLE001
        out["ppa_error"] = str(e)
    try:
        for t in call("/ratings/sp", {"year": season}):
            if t.get("team") == "nationalAverages":
                continue
            out["teams"].setdefault(t["team"], {}).update({
                "sp_plus": t.get("rating"), "sp_rank": t.get("ranking"),
                "sp_off": (t.get("offense") or {}).get("rating"), "sp_def": (t.get("defense") or {}).get("rating"),
            })
    except Exception as e:  # noqa: BLE001
        out["sp_error"] = str(e)
    return out


def run(day: dt.date) -> dict:
    season = day.year if day.month >= 3 else day.year - 1
    comps = C.Components()
    games = comps.run("scoreboard", espn.scoreboard, "CFB", day)
    if games is None:
        raise RuntimeError(f"ESPN CFB scoreboard failed: {comps.errors.get('scoreboard')}")
    for g in games:
        s = comps.run("summaries", espn.summary, "CFB", g["espn_id"])
        if s is None:
            g["summary_error"] = comps.errors.get("summaries")
            continue
        g["fpi_projection"] = s["predictor"]
        g["injuries"] = s["injuries"] or None
        g["weather"] = s["weather"]
    ahead = []
    for i in range(1, 4):
        d = day + dt.timedelta(days=i)
        for g in comps.run("next_3_days", espn.scoreboard, "CFB", d) or []:
            ahead.append({"date": d.isoformat(), "name": g["name"], "start_et": g["start_et"],
                          "away_rank": (g.get("away") or {}).get("rank"),
                          "home_rank": (g.get("home") or {}).get("rank"),
                          "espn_line": g.get("espn_line")})
    ranked = sum(1 for g in games if (g.get("away") or {}).get("rank") or (g.get("home") or {}).get("rank"))
    poll = comps.run("ap_poll", ap_poll)
    if not poll:
        comps.mark("ap_poll", C.ERROR, "AP poll empty or unavailable")
    adv = cfbd(season)
    if adv["status"] == "skipped":
        comps.mark("cfbd", C.SKIPPED)
    elif adv.get("ppa_error") or adv.get("sp_error"):
        comps.mark("cfbd", C.ERROR, adv.get("ppa_error") or adv.get("sp_error"))
    else:
        comps.mark("cfbd", C.OK)
    payload = {"date": day.isoformat(), "season": season, "games": games,
               "games_ranked": ranked, "games_unranked": len(games) - ranked,
               "ap_top25": poll or [], "next_3_days": ahead, "advanced": adv,
               "components": dict(comps.status),
               "note": "cfbd 'skipped' means team EPA / SP+ are NOT in this file (no CFBD_API_KEY)."}
    C.write("cfb", payload, day)
    return comps.result(core=("scoreboard",), games=len(games), ranked_games=ranked,
                        upcoming_3d=len(ahead), cfbd=adv["status"])
