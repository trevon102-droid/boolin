"""NHL from the official NHL web API (free) + MoneyPuck season CSVs (xG, GSAx).

Per game: rest / back-to-back flags, standings form, each team's goalie usage
(this season and last), and 5v5 xG% plus goalie GSAx from MoneyPuck. Starting goalies
are NOT confirmed by any free API; the playbook says to check Daily Faceoff.
"""
from __future__ import annotations

import csv
import datetime as dt
import io

from . import common as C

API = "https://api-web.nhle.com/v1"
MP = "https://moneypuck.com/moneypuck/playerData/seasonSummary/{y}/regular/{f}.csv"


def _games_on(day: dt.date) -> list[dict]:
    wk = C.get_json(f"{API}/schedule/{day.isoformat()}").get("gameWeek", [])
    return next((d.get("games", []) for d in wk if d.get("date") == day.isoformat()), [])


def _moneypuck(y: int) -> dict:
    out: dict = {"teams": {}, "goalies": {}}
    try:
        rows = csv.DictReader(io.StringIO(C.get(MP.format(y=y, f="teams")).text))
        for r in rows:
            if r.get("situation") == "5on5":
                out["teams"][r["team"]] = {
                    "gp": int(float(r.get("games_played") or 0)),
                    "xgf_pct": round(float(r.get("xGoalsPercentage") or 0), 3),
                    "cf_pct": round(float(r.get("corsiPercentage") or 0), 3),
                    "xgf": round(float(r.get("xGoalsFor") or 0), 1),
                    "xga": round(float(r.get("xGoalsAgainst") or 0), 1),
                }
    except Exception as e:  # noqa: BLE001
        out["teams_error"] = str(e)
    try:
        rows = csv.DictReader(io.StringIO(C.get(MP.format(y=y, f="goalies")).text))
        for r in rows:
            if r.get("situation") == "all":
                xg, ga = float(r.get("xGoals") or 0), float(r.get("goals") or 0)
                out["goalies"].setdefault(r["team"], []).append({
                    "name": r.get("name"), "gp": int(float(r.get("games_played") or 0)),
                    "gsax": round(xg - ga, 1),
                })
        for t in out["goalies"]:
            out["goalies"][t].sort(key=lambda g: -g["gp"])
    except Exception as e:  # noqa: BLE001
        out["goalies_error"] = str(e)
    return out


def _club_goalies(abbr: str, season: str | None) -> list[dict]:
    url = f"{API}/club-stats/{abbr}/now" if season is None else f"{API}/club-stats/{abbr}/{season}/2"
    gs = C.get_json(url).get("goalies", [])
    return sorted([{
        "name": f"{(g.get('firstName') or {}).get('default', '')} {(g.get('lastName') or {}).get('default', '')}".strip(),
        "gp": g.get("gamesPlayed"), "gs": g.get("gamesStarted"),
        "sv_pct": g.get("savePercentage"), "gaa": g.get("goalsAgainstAverage"),
    } for g in gs], key=lambda g: -(g["gs"] or 0))


def _xg(row: dict | None) -> dict | None:
    if not row:
        return row
    return {**row, "sample_quality": C.sample_quality(row.get("gp"))}


def run(day: dt.date) -> dict:
    games_raw = _games_on(day)
    if not games_raw:
        C.write("nhl", {"date": day.isoformat(), "games": []}, day)
        return {"status": "ok", "games": 0, "components": {"schedule": "ok"}}
    comps = C.Components()
    comps.mark("schedule", C.OK)
    yesterday = {t for g in (comps.run("back_to_back", _games_on, day - dt.timedelta(days=1)) or [])
                 for t in (g["awayTeam"]["abbrev"], g["homeTeam"]["abbrev"])}

    def _standings():
        st = C.get_json(f"{API}/standings/{day.isoformat()}").get("standings", [])
        return {s["teamAbbrev"]["default"]: {
            "gp": s.get("gamesPlayed"), "pts": s.get("points"), "w": s.get("wins"), "l": s.get("losses"),
            "otl": s.get("otLosses"), "gf": s.get("goalFor"), "ga": s.get("goalAgainst"),
            "l10": f"{s.get('l10Wins')}-{s.get('l10Losses')}-{s.get('l10OtLosses')}",
            "streak": f"{s.get('streakCode', '')}{s.get('streakCount', '')}",
            "sample_quality": C.sample_quality(s.get("gamesPlayed")),
        } for s in st}
    standings = comps.run("standings", _standings) or {}
    y = C.season_year(day, 9)
    prev_label = f"{y - 1}{y}"
    mp_now, mp_prev = _moneypuck(y), _moneypuck(y - 1)
    for label, mp in (("moneypuck_this_season", mp_now), ("moneypuck_last_season", mp_prev)):
        errs = [mp[k] for k in ("teams_error", "goalies_error") if mp.get(k)]
        comps.mark(label, C.ERROR if errs else C.OK, "; ".join(errs) if errs else None)
    games = []
    for g in games_raw:
        row = {
            "id": g.get("id"), "start_et": C.to_et(g.get("startTimeUTC")),
            "game_type": {1: "preseason", 2: "regular", 3: "playoff"}.get(g.get("gameType"), g.get("gameType")),
            "venue": (g.get("venue") or {}).get("default"),
        }
        for side, key in (("away", "awayTeam"), ("home", "homeTeam")):
            abbr = g[key]["abbrev"]
            row[side] = {
                "abbr": abbr,
                "back_to_back": abbr in yesterday,
                "standings": standings.get(abbr),
                "starting_goalie": {"status": "unconfirmed",
                                    "note": "No free API confirms starters. Check Daily Faceoff before betting."},
                "goalies_this_season": comps.run("club_goalies", _club_goalies, abbr, None) or [],
                "goalies_last_season": (comps.run("club_goalies", _club_goalies, abbr, prev_label) or [])[:3],
                "xg_5v5_this_season": _xg(mp_now["teams"].get(abbr)),
                "xg_5v5_last_season": _xg(mp_prev["teams"].get(abbr)),
                "gsax_this_season": mp_now["goalies"].get(abbr, [])[:3],
                "gsax_last_season": mp_prev["goalies"].get(abbr, [])[:3],
            }
        games.append(row)
    C.write("nhl", {"date": day.isoformat(), "season_start_year": y, "games": games,
                    "components": dict(comps.status),
                    "moneypuck_errors": {k: v for k, v in comps.errors.items() if k.startswith("moneypuck")} or None,
                    "note": ("Starting goalies are projected, not confirmed (starting_goalie.status). Check Daily Faceoff. "
                             "sample_quality: tiny < 5 GP, small < 10 GP. Treat tiny samples as descriptive only "
                             "and lean on last season.")}, day)
    return comps.result(core=("schedule",), games=len(games))
