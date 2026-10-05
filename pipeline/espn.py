"""ESPN public site API helpers (no key): scoreboard, per-game summary (injuries, predictor,
ESPN's posted line), and player game logs. Used for WNBA/NBA and for injuries in every sport."""
from __future__ import annotations

import datetime as dt

from . import common as C

SITE = "https://site.api.espn.com/apis/site/v2/sports"
WEB = "https://site.web.api.espn.com/apis/common/v3/sports"

LEAGUES = {
    "WNBA": ("basketball", "wnba"),
    "NBA": ("basketball", "nba"),
    "NHL": ("hockey", "nhl"),
    "NFL": ("football", "nfl"),
    "MLB": ("baseball", "mlb"),
    "CFB": ("football", "college-football"),
}
# Extra scoreboard params: CFB needs groups=80 (all FBS) or ESPN only returns featured games.
EXTRA = {"CFB": {"groups": "80", "limit": "300"}}


def scoreboard(league: str, day: dt.date) -> list[dict]:
    sport, lg = LEAGUES[league]
    data = C.get_json(f"{SITE}/{sport}/{lg}/scoreboard", {"dates": day.strftime("%Y%m%d"), **EXTRA.get(league, {})})
    out = []
    for ev in data.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        teams = {}
        for c in comp.get("competitors", []):
            t = c.get("team", {})
            rank = (c.get("curatedRank") or {}).get("current")
            teams[c.get("homeAway")] = {
                "id": t.get("id"), "abbr": t.get("abbreviation"), "name": t.get("displayName"),
                "score": c.get("score"),
                "record": next((r.get("summary") for r in c.get("records", []) if r.get("type") in ("total", None)), None),
                "rank": rank if rank and rank <= 25 else None,
                "conference_id": t.get("conferenceId"),
            }
        odds = (comp.get("odds") or [{}])[0]
        venue = comp.get("venue") or {}
        out.append({
            "espn_id": ev.get("id"), "name": ev.get("shortName"), "start_et": C.to_et(ev.get("date")),
            "status": ((ev.get("status") or {}).get("type") or {}).get("description"),
            "completed": bool(((ev.get("status") or {}).get("type") or {}).get("completed")),
            "away_score": _score((teams.get("away") or {}).get("score")),
            "home_score": _score((teams.get("home") or {}).get("score")),
            "note": "; ".join(n.get("headline", "") for n in comp.get("notes", []) if n.get("headline")) or None,
            "away": teams.get("away"), "home": teams.get("home"),
            "neutral_site": comp.get("neutralSite"), "conference_game": comp.get("conferenceCompetition"),
            "venue": venue.get("fullName"), "indoor": venue.get("indoor"),
            "tv": ", ".join(b.get("names", [""])[0] for b in comp.get("broadcasts", []) if b.get("names")) or None,
            "espn_line": {"details": odds.get("details"), "total": odds.get("overUnder"),
                          "provider": (odds.get("provider") or {}).get("name")} if odds else None,
        })
    return out


def _score(x):
    try:
        return float(x) if x not in (None, "") else None
    except (TypeError, ValueError):
        return None


def summary(league: str, event_id: str) -> dict:
    sport, lg = LEAGUES[league]
    s = C.get_json(f"{SITE}/{sport}/{lg}/summary", {"event": event_id})
    injuries = {}
    for blk in s.get("injuries", []):
        abbr = (blk.get("team") or {}).get("abbreviation")
        injuries[abbr] = [{
            "player": (i.get("athlete") or {}).get("displayName"),
            "pos": ((i.get("athlete") or {}).get("position") or {}).get("abbreviation"),
            "status": i.get("status"),
            "detail": " ".join(filter(None, [(i.get("details") or {}).get("type"),
                                             (i.get("details") or {}).get("detail")])) or None,
        } for i in blk.get("injuries", [])]
    pred = s.get("predictor") or {}
    predictor = None
    if pred:
        predictor = {
            "home_win_pct": (pred.get("homeTeam") or {}).get("gameProjection"),
            "away_win_pct": (pred.get("awayTeam") or {}).get("gameProjection"),
        }
    leaders = {}
    for blk in s.get("leaders", []):
        abbr = (blk.get("team") or {}).get("abbreviation")
        ids = []
        for cat in blk.get("leaders", []):
            for ld in cat.get("leaders", [])[:1]:
                a = ld.get("athlete") or {}
                if a.get("id") and a["id"] not in [x["id"] for x in ids]:
                    ids.append({"id": a["id"], "name": a.get("displayName"), "cat": cat.get("name")})
        leaders[abbr] = ids
    return {"injuries": injuries, "predictor": predictor, "leaders": leaders,
            "weather": (s.get("gameInfo") or {}).get("weather")}


def gamelog(league: str, athlete_id: str, last: int = 10) -> list[dict]:
    """Most recent `last` games for a player as {date, opp, stat: value} rows."""
    sport, lg = LEAGUES[league]
    g = C.get_json(f"{WEB}/{sport}/{lg}/athletes/{athlete_id}/gamelog")
    names = g.get("names") or []
    meta = g.get("events") or {}
    rows = []
    for st in g.get("seasonTypes", []):
        for cat in st.get("categories", []):
            for e in cat.get("events", []):
                m = meta.get(e.get("eventId"), {})
                row = {"date": (m.get("gameDate") or "")[:10], "opp": (m.get("opponent") or {}).get("abbreviation"),
                       "season_type": st.get("displayName")}
                row.update(dict(zip(names, e.get("stats", []))))
                rows.append(row)
    rows.sort(key=lambda r: r["date"])
    return rows[-last:]
