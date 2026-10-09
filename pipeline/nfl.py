"""NFL from nflverse (free): this week's games with rest/weather/QBs/closing-style lines,
team EPA/success-rate splits from play-by-play, and the latest official injury report."""
from __future__ import annotations

import datetime as dt
import io

import pandas as pd

from . import common as C

GAMES = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
PBP = "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{s}.parquet"
INJ = "https://github.com/nflverse/nflverse-data/releases/download/injuries/injuries_{s}.parquet"
PBP_COLS = ["season_type", "week", "posteam", "defteam", "epa", "success", "pass", "rush", "down"]


def _parquet(url: str, columns: list[str] | None = None) -> pd.DataFrame:
    return pd.read_parquet(io.BytesIO(C.get(url, timeout=120).content), columns=columns)


def _r(x, n=3):
    return None if pd.isna(x) else round(float(x), n)


def team_epa(season: int) -> dict:
    df = _parquet(PBP.format(s=season), PBP_COLS)
    df = df[(df["season_type"] == "REG") | (df["season_type"] == "POST")]
    plays = df[((df["pass"] == 1) | (df["rush"] == 1)) & df["epa"].notna()]
    if plays.empty:
        return {}
    weeks = sorted(plays["week"].unique())
    last_weeks, last_week = weeks[-3:], weeks[-1:]
    out = {}
    for team in sorted(set(plays["posteam"].dropna()) | set(plays["defteam"].dropna())):
        o, d = plays[plays["posteam"] == team], plays[plays["defteam"] == team]
        o3, d3 = o[o["week"].isin(last_weeks)], d[d["week"].isin(last_weeks)]
        o1, d1 = o[o["week"].isin(last_week)], d[d["week"].isin(last_week)]
        n_games = int(o["week"].nunique())
        early = o[o["down"].isin([1, 2])]
        out[team] = {
            "games": n_games,
            "sample_quality": C.sample_quality(n_games, tiny=3, small=6),
            "off_epa_play": _r(o["epa"].mean()), "off_success": _r(o["success"].mean()),
            "off_pass_epa": _r(o.loc[o["pass"] == 1, "epa"].mean()),
            "off_rush_epa": _r(o.loc[o["rush"] == 1, "epa"].mean()),
            "early_down_pass_rate": _r(early["pass"].mean()),
            "def_epa_play": _r(d["epa"].mean()), "def_success": _r(d["success"].mean()),
            "def_pass_epa": _r(d.loc[d["pass"] == 1, "epa"].mean()),
            "def_rush_epa": _r(d.loc[d["rush"] == 1, "epa"].mean()),
            "off_epa_last3": _r(o3["epa"].mean()), "def_epa_last3": _r(d3["epa"].mean()),
            "off_epa_last1": _r(o1["epa"].mean()), "def_epa_last1": _r(d1["epa"].mean()),
        }
    # league ranks (1 = best) so the slate can say "3rd-best defense"
    for key, best_high in (("off_epa_play", True), ("def_epa_play", False)):
        ranked = sorted(out, key=lambda t: (out[t][key] is None, -(out[t][key] or 0) if best_high else (out[t][key] or 0)))
        for i, t in enumerate(ranked, 1):
            out[t][key + "_rank"] = i
    return {"weeks_in_sample": [int(w) for w in weeks],
            "sample_note": "sample_quality: tiny < 3 games, small < 6. Regress tiny samples hard toward last season / market.",
            "teams": out}


def injuries(season: int) -> dict:
    df = _parquet(INJ.format(s=season))
    if df.empty:
        return {}
    wk = df["week"].max()
    cur = df[(df["week"] == wk) & df["report_status"].isin(["Out", "Doubtful", "Questionable"])]
    out: dict = {"week": int(wk), "report_week": int(wk),
                 "note": "Latest weekly report, not inactives. Final status comes ~90 min before kickoff.",
                 "teams": {}}
    for _, r in cur.iterrows():
        out["teams"].setdefault(r["team"], []).append({
            "player": _clean(r.get("full_name")), "pos": _clean(r.get("position")),
            "status": _clean(r.get("report_status")),
            "practice": _clean(r.get("practice_status")), "injury": _clean(r.get("report_primary_injury")),
            "reported": (str(r.get("date_modified"))[:16] if r.get("date_modified") is not None
                         and not pd.isna(r.get("date_modified")) else None),
        })
    return out


HISTORY_COLS = ["season", "week", "gameday", "away_team", "home_team", "result", "total", "spread_line",
                "total_line", "home_rest", "away_rest", "roof", "wind", "temp", "div_game"]
HISTORY_FROM = 2012


def _clean(v):
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return None
    return v if isinstance(v, str) else float(v)


def write_history(allg: pd.DataFrame, season: int) -> str:
    """Slim history of completed games (closing spread/total, result, rest, weather) for the research
    layer's comparable-game queries: data/reference/nfl_games_history.json.gz. Refreshed each run."""
    import gzip
    import json
    try:
        h = allg[(allg["season"] >= HISTORY_FROM) & allg["result"].notna()][[c for c in HISTORY_COLS if c in allg]]
        rows = []
        for r in h.to_dict("records"):
            rows.append({"season": int(r["season"]), "week": int(r["week"]), "gameday": str(r["gameday"])[:10],
                         "away": r["away_team"], "home": r["home_team"],
                         **{k: _clean(r.get(k)) for k in ("result", "total", "spread_line", "total_line", "home_rest",
                                                          "away_rest", "roof", "wind", "temp", "div_game")}})
        out = C.DATA / "reference" / "nfl_games_history.json.gz"
        out.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps({"source": "nflverse games.csv", "built_at_et": C.now_et().isoformat(timespec="minutes"),
                           "note": "spread_line/total_line are closing numbers; spread_line > 0 = home favored; "
                                   "result = home score - away score", "games": rows}, separators=(",", ":"))
        with gzip.open(out, "wt", encoding="utf-8", compresslevel=9) as f:
            f.write(body)
        return "ok"
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"


def run(day: dt.date) -> dict:
    season = day.year if day.month >= 3 else day.year - 1
    allg = pd.read_csv(io.StringIO(C.get(GAMES, timeout=60).text))
    history_status = write_history(allg, season)
    g = allg[allg["season"] == season].copy()
    g["gameday"] = pd.to_datetime(g["gameday"]).dt.date
    upcoming = g[(g["gameday"] >= day) & (g["gameday"] <= day + dt.timedelta(days=7)) & g["result"].isna()]
    if upcoming.empty:
        C.write("nfl", {"date": day.isoformat(), "season": season, "games": [], "note": "No games in the next 7 days"}, day)
        return {"status": "ok", "games": 0}
    cols = ["game_id", "week", "gameday", "weekday", "gametime", "away_team", "home_team", "away_rest", "home_rest",
            "spread_line", "away_spread_odds", "home_spread_odds", "total_line", "over_odds", "under_odds",
            "away_moneyline", "home_moneyline", "div_game", "roof", "surface", "temp", "wind",
            "away_qb_name", "home_qb_name", "referee", "stadium"]
    games = [{k: (None if pd.isna(v) else (v.isoformat() if hasattr(v, "isoformat") else v))
              for k, v in row.items()} for row in upcoming[[c for c in cols if c in upcoming]].to_dict("records")]
    # travel / rest / trap-game flags (TASK.md 00b); never let this break the schedule pull
    from .schedule_spots import game_spots
    for gd, (_, row) in zip(games, upcoming.iterrows()):
        try:
            gd["schedule"] = game_spots(g, row)
        except Exception as e:  # noqa: BLE001
            gd["schedule"] = {"error": str(e)}
    payload: dict = {"date": day.isoformat(), "season": season,
                     "note": "spread_line is home-team margin (positive = home favored), per nflverse. "
                             "Injury statuses are the latest weekly report (see injuries.report_week), "
                             "not game-day inactives: confirm inactives ~90 min before kickoff.",
                     "games": games}
    comps = C.Components({"schedule": "nflverse games.csv", "history": "nflverse games.csv",
                           "team_epa": "nflverse play-by-play", "injuries": "nflverse injury reports"})
    comps.mark("schedule", C.OK)
    comps.mark("history", C.OK if history_status == "ok" else C.ERROR,
               None if history_status == "ok" else history_status)
    epa = comps.run("team_epa", team_epa, season)
    if epa is not None:
        payload["team_epa"] = epa
    else:
        payload["team_epa_error"] = comps.errors.get("team_epa")
    inj = comps.run("injuries", injuries, season)
    if inj is not None:
        payload["injuries"] = inj
    else:
        payload["injuries_error"] = comps.errors.get("injuries")
    payload["components"] = dict(comps.status)
    C.write("nfl", payload, day)
    return comps.result(core=("schedule",), games=len(games))
