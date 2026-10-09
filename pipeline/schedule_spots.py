"""Schedule spots: travel, rest and trap-game flags for each team in an upcoming game.

Built from nflverse games.csv (the same frame nfl.py already loads). Each team in an upcoming game gets:

  rest, prev (last game: date, opponent, site, stadium, international, margin, won, overtime, was_dog),
  next (next game: date, opponent, division, primetime), flags, hard_pass, cap_tier, why.

Rules (mirrored in TASK.md section 00b):
  INTL_RETURN            previous game was outside the US and there was no bye since  -> HARD PASS
  TRAP                   favored by 6.5+ AND (LOOK_AHEAD or LETDOWN or SHORT_WEEK_AFTER_ROAD) -> cap tier C
  SHORT_WEEK_AFTER_ROAD  4 or fewer days of rest after a road/neutral game            -> cap tier B
  THIRD_STRAIGHT_ROAD    third road/neutral game in a row                             -> cap tier B
  WEST_TO_EAST_EARLY     Pacific/Mountain team playing a 1 PM ET (or earlier) road game in the East -> cap tier B
  LOOK_AHEAD / LETDOWN / SHORT_WEEK / OFF_BYE are context flags.

A hard pass covers the game's sides/totals and that team's player props/TDs. The opponent's props stay playable.
"""
from __future__ import annotations

import pandas as pd

# nflverse stadium_id prefixes for games played outside the US.
INTL_STADIUM_PREFIXES = ("LON", "MEX", "GER", "FRA", "MUN", "SAO", "RIO", "MAD", "DUB", "BER", "MEL", "TOR", "PAR")
INTL_STADIUM_WORDS = ("wembley", "tottenham", "twickenham", "azteca", "banorte", "allianz arena", "deutsche bank park",
                      "frankfurt", "corinthians", "neo química", "neo quimica", "maracan", "bernab", "croke park",
                      "olympiastadion", "melbourne cricket", "rogers centre", "stade de france")
WEST = {"SEA", "SF", "LA", "LAC", "LV", "DEN", "ARI"}
EAST = {"NE", "NYJ", "NYG", "BUF", "MIA", "PHI", "PIT", "BAL", "WAS", "CLE", "CIN", "DET", "IND", "ATL", "CAR",
        "TB", "JAX"}

HARD_PASS_FLAGS = ("INTL_RETURN",)
CAP_C_FLAGS = ("TRAP",)
CAP_B_FLAGS = ("SHORT_WEEK_AFTER_ROAD", "THIRD_STRAIGHT_ROAD", "WEST_TO_EAST_EARLY")
BIG_FAVORITE = 6.5


def _s(v):
    return None if v is None or (not isinstance(v, str) and pd.isna(v)) else v


def is_international(row) -> bool:
    sid = str(_s(row.get("stadium_id")) or "").upper()
    name = str(_s(row.get("stadium")) or "").lower()
    return sid.startswith(INTL_STADIUM_PREFIXES) or any(w in name for w in INTL_STADIUM_WORDS)


def is_primetime(row) -> bool:
    day = str(_s(row.get("weekday")) or "")
    t = str(_s(row.get("gametime")) or "")
    return day in ("Thursday", "Monday") or t >= "20:00"


def _site(row, team: str) -> str:
    if str(_s(row.get("location")) or "").lower() == "neutral" or is_international(row):
        return "neutral"
    return "home" if row["home_team"] == team else "away"


def _team_view(row, team: str) -> dict:
    home = row["home_team"] == team
    opp = row["away_team"] if home else row["home_team"]
    spread = _s(row.get("spread_line"))           # > 0 = home favored
    fav_by = None if spread is None else float(spread) * (1 if home else -1)
    res = _s(row.get("result"))                    # home score - away score
    margin = None if res is None else float(res) * (1 if home else -1)
    return {"date": str(row["gameday"])[:10], "opponent": opp, "site": _site(row, team),
            "stadium": _s(row.get("stadium")), "international": is_international(row),
            "fav_by": fav_by, "margin": margin, "won": None if margin is None else margin > 0,
            "overtime": bool(_s(row.get("overtime")) or 0), "division": bool(_s(row.get("div_game")) or 0),
            "primetime": is_primetime(row), "weekday": _s(row.get("weekday")), "gametime": _s(row.get("gametime"))}


def team_spot(season_games: pd.DataFrame, game, team: str) -> dict:
    """Flags for `team` in upcoming `game` (a row of season_games)."""
    g = season_games.copy()
    g["_d"] = pd.to_datetime(g["gameday"]).dt.date
    day = pd.to_datetime(game["gameday"]).date()
    mine = g[(g["home_team"] == team) | (g["away_team"] == team)].sort_values("_d")
    before = mine[mine["_d"] < day]
    after = mine[mine["_d"] > day]
    now = _team_view(game, team)
    prev = _team_view(before.iloc[-1], team) if len(before) else None
    nxt = _team_view(after.iloc[0], team) if len(after) else None
    rest = (day - before.iloc[-1]["_d"]).days if len(before) else None

    flags, why = [], []
    if prev and prev["international"] and rest is not None and rest < 13:
        flags.append("INTL_RETURN")
        why.append(f"Back from an international game ({prev['stadium']}, {prev['date']}) with no bye: {rest} days rest")
    if rest is not None and rest <= 4:
        flags.append("SHORT_WEEK")
        if prev and prev["site"] != "home":
            flags.append("SHORT_WEEK_AFTER_ROAD")
            why.append(f"{rest} days rest after a road game at {prev['opponent']}")
    if rest is not None and rest >= 13:
        flags.append("OFF_BYE")
    last2 = [_team_view(r, team)["site"] for _, r in before.tail(2).iterrows()]
    if now["site"] != "home" and len(last2) == 2 and all(s != "home" for s in last2):
        flags.append("THIRD_STRAIGHT_ROAD")
        why.append("Third straight road/neutral game")
    if (team in WEST and game["home_team"] in EAST and now["site"] == "away"
            and str(_s(game.get("gametime")) or "99") <= "13:00"):
        flags.append("WEST_TO_EAST_EARLY")
        why.append("West-coast team in a 1 PM ET road game")
    big_fav = now["fav_by"] is not None and now["fav_by"] >= BIG_FAVORITE
    if big_fav and nxt and (nxt["division"] or nxt["primetime"]):
        flags.append("LOOK_AHEAD")
        why.append(f"Favored by {now['fav_by']:g} with {'a division game' if nxt['division'] else 'a primetime game'} "
                   f"vs {nxt['opponent']} next ({nxt['date']})")
    if big_fav and prev and prev["won"] and (prev["overtime"] or (prev["fav_by"] is not None and prev["fav_by"] < 0)
                                             or prev["division"]):
        flags.append("LETDOWN")
        kind = "OT win" if prev["overtime"] else ("upset win" if (prev["fav_by"] or 0) < 0 else "division win")
        why.append(f"Favored by {now['fav_by']:g} right after an emotional {kind} vs {prev['opponent']}")
    if big_fav and any(f in flags for f in ("LOOK_AHEAD", "LETDOWN", "SHORT_WEEK_AFTER_ROAD")):
        flags.append("TRAP")
        why.append(f"TRAP: big favorite (-{now['fav_by']:g}) in a schedule spot")

    hard = any(f in flags for f in HARD_PASS_FLAGS)
    cap = None if hard else ("C" if any(f in flags for f in CAP_C_FLAGS)
                             else "B" if any(f in flags for f in CAP_B_FLAGS) else None)
    return {"team": team, "rest": rest, "prev": prev, "next": nxt, "flags": flags,
            "hard_pass": hard, "cap_tier": cap, "why": why}


def game_spots(season_games: pd.DataFrame, game) -> dict:
    away = team_spot(season_games, game, game["away_team"])
    home = team_spot(season_games, game, game["home_team"])
    hard = [t["team"] for t in (away, home) if t["hard_pass"]]
    return {"away": away, "home": home, "hard_pass_teams": hard,
            "game_hard_pass": bool(hard),
            "rule": ("Hard pass sides/totals and the flagged team's props; the opponent's props stay playable."
                     if hard else None)}
