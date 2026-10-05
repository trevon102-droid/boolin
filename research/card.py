"""Game research cards: one object per game that joins odds, league data, injuries and the model.

Every value carries its kind (confirmed / reported / projected / derived / unknown), source and
timestamp (see provenance.py). Nothing missing is filled in: it shows up as `unknown` with a reason.
"""
from __future__ import annotations

import datetime as dt
import re

from . import SCHEMA_VERSION, compare, markets, models, teams
from . import timeutil as T
from .provenance import fact, unknown, val

ODDS_SPORTS = ("NFL", "CFB", "MLB", "NHL", "NBA", "WNBA")
STATUS_RANK = {"active": 0, "probable": 1, "day-to-day": 2, "questionable": 3, "doubtful": 4, "out": 5,
               "injured reserve": 6, "suspension": 6}
KEY_POSITIONS = {"NFL": {"QB"}, "NHL": {"G"}, "MLB": {"SP"}}


# ---------------------------------------------------------------- helpers

def game_key(date: str, league: str, away: str, home: str) -> str:
    slug = lambda s: re.sub(r"[^A-Za-z0-9]+", "", s)[:24]  # noqa: E731
    return f"{date}-{league}-{slug(away)}-{slug(home)}"


def start_from_label(date: str, label: str | None) -> dt.datetime | None:
    """'2026-10-05' + '7:10 PM ET' -> aware datetime in ET (DST-correct)."""
    m = re.match(r"\s*(\d{1,2}):(\d{2})\s*(AM|PM)", label or "", re.I)
    if not m:
        return None
    h = int(m.group(1)) % 12 + (12 if m.group(3).upper() == "PM" else 0)
    d = dt.date.fromisoformat(date)
    return dt.datetime(d.year, d.month, d.day, h, int(m.group(2)), tzinfo=T.ET)


def norm_status(s: str | None) -> str:
    s = (s or "").strip().lower()
    for k in STATUS_RANK:
        if s.startswith(k):
            return k
    return s or "unknown"


def _src_meta(raw: dict, name: str) -> tuple[str | None, str | None]:
    """(pulled_at, status) for a raw source from its file and the manifest."""
    f = raw.get(name) or {}
    st = (((raw.get("manifest") or {}).get("sources") or {}).get(name) or {}).get("status")
    return f.get("pulled_at_et") or (raw.get("manifest") or {}).get("run_at_et"), st


# ---------------------------------------------------------------- enumerate games

def enumerate_games(raw: dict, slate_date: str) -> list[dict]:
    """Games from the odds feed (all leagues) plus league-file games on the slate day that the
    odds feed doesn't list. Each: {key, league, date, start (aware dt), away, home, odds_event, ...}."""
    games: dict[str, dict] = {}
    odds = raw.get("odds") or {}
    for league in ODDS_SPORTS:
        for ev in ((odds.get("sports") or {}).get(league) or {}).get("events") or []:
            start = T.parse(ev.get("commence_time"))
            if not start:
                continue
            date = T.date_et(start)
            a = teams.abbr(league, ev["away"]) or ev["away"]
            h = teams.abbr(league, ev["home"]) or ev["home"]
            key = game_key(date, league, a, h)
            games[key] = {"key": key, "league": league, "date": date, "start": start,
                          "away": {"abbr": a, "name": ev["away"]}, "home": {"abbr": h, "name": ev["home"]},
                          "odds_event": ev}
    # league files on the slate day
    for g in (raw.get("nhl") or {}).get("games") or []:
        start = start_from_label(slate_date, g.get("start_et"))
        a, h = g["away"]["abbr"], g["home"]["abbr"]
        key = game_key(slate_date, "NHL", a, h)
        games.setdefault(key, {"key": key, "league": "NHL", "date": slate_date, "start": start,
                               "away": {"abbr": a, "name": a}, "home": {"abbr": h, "name": h}})
    for g in (raw.get("mlb") or {}).get("games") or []:
        start = start_from_label(slate_date, g.get("start_et"))
        a, h = g["away"]["abbr"], g["home"]["abbr"]
        key = game_key(slate_date, "MLB", a, h)
        games.setdefault(key, {"key": key, "league": "MLB", "date": slate_date, "start": start,
                               "away": {"abbr": a, "name": g["away"].get("name") or a},
                               "home": {"abbr": h, "name": g["home"].get("name") or h}})
    for g in (raw.get("nfl") or {}).get("games") or []:
        if g.get("gameday") is None:
            continue
        date = str(g["gameday"])[:10]
        start = start_from_label(date, _clock_24_to_label(g.get("gametime")))
        key = game_key(date, "NFL", g["away_team"], g["home_team"])
        games.setdefault(key, {"key": key, "league": "NFL", "date": date, "start": start,
                               "away": {"abbr": g["away_team"], "name": g["away_team"]},
                               "home": {"abbr": g["home_team"], "name": g["home_team"]}})
    return sorted(games.values(), key=lambda g: (g["start"] or dt.datetime.max.replace(tzinfo=T.UTC), g["key"]))


def _clock_24_to_label(hhmm: str | None) -> str | None:
    if not hhmm or ":" not in str(hhmm):
        return None
    h, m = (int(x) for x in str(hhmm).split(":")[:2])
    return f"{(h % 12) or 12}:{m:02d} {'PM' if h >= 12 else 'AM'}"


# ---------------------------------------------------------------- league sections

def _injury_rows(rows: list, team: str, league: str, source: str, as_of: str | None) -> list[dict]:
    out = []
    for r in rows or []:
        st = r.get("status")
        out.append({"team": team, "player": r.get("player"), "pos": r.get("pos"), "status": st,
                    "status_norm": norm_status(st), "detail": r.get("injury") or r.get("detail"),
                    "practice": r.get("practice"), "kind": "reported", "source": source, "as_of": as_of,
                    "key_player": (r.get("pos") or "") in KEY_POSITIONS.get(league, set())})
    return out


def _espn_injuries(raw: dict, league: str, away: str, home: str) -> tuple[list[dict], str | None]:
    inj = raw.get("injuries") or {}
    as_of = inj.get("pulled_at_et")
    for g in (inj.get("leagues") or {}).get(league) or []:
        if not isinstance(g, dict) or "injuries" not in g:
            continue
        by_team = {teams.espn(league, k): v for k, v in (g.get("injuries") or {}).items()}
        if home in by_team or away in by_team:
            rows = []
            for t in (away, home):
                rows += _injury_rows(by_team.get(t), t, league, "ESPN injury list", as_of)
            return rows, as_of
    return [], as_of


def nfl_section(raw: dict, g: dict) -> dict:
    d = raw.get("nfl") or {}
    as_of, status = _src_meta(raw, "nfl")
    row = next((x for x in d.get("games") or [] if x.get("away_team") == g["away"]["abbr"]
                and x.get("home_team") == g["home"]["abbr"] and str(x.get("gameday"))[:10] == g["date"]), None)
    epa = ((d.get("team_epa") or {}).get("teams")) or {}
    inj = (d.get("injuries") or {}).get("teams") or {}
    research, starters, injuries = {}, {}, []
    for side in ("away", "home"):
        t = g[side]["abbr"]
        e = epa.get(t)
        research[side] = {
            "team_epa": fact(e, "derived", "nflverse play-by-play", as_of,
                             note=f"{(e or {}).get('games')} game(s), sample {(e or {}).get('sample_quality')}") if e
            else unknown("team EPA not in nfl.json", "nflverse", as_of),
            "games_sampled": (e or {}).get("games"),
            "sample_quality": (e or {}).get("sample_quality"),
            "rest_days": fact(row.get(f"{side}_rest"), "reported", "nflverse schedule", as_of) if row
            else unknown("game not in nfl.json"),
        }
        if e and e.get("off_epa_last3") is not None and e.get("off_epa_play") is not None and (e.get("games") or 0) >= 4:
            gap = e["off_epa_last3"] - e["off_epa_play"]
            if abs(gap) >= 0.1:
                research[side]["form_divergence"] = (f"Offense EPA/play last 3 weeks {e['off_epa_last3']:+.3f} "
                                                     f"vs season {e['off_epa_play']:+.3f}.")
        qb = (row or {}).get(f"{side}_qb_name")
        starters[side] = fact(qb, "projected", "nflverse schedule (expected starter)", as_of,
                              note="QB listed by nflverse; confirm at inactives") if qb \
            else unknown("no QB listed", "nflverse")
        injuries += _injury_rows(inj.get(t), t, "NFL", f"NFL injury report week {(d.get('injuries') or {}).get('week')}", as_of)
    roof = (row or {}).get("roof")
    outdoors = roof in ("outdoors", "open") if roof else None
    env = {"roof": fact(roof, "reported", "nflverse", as_of) if roof else unknown("roof not listed"),
           "outdoors": outdoors,
           "wind_mph": fact(row.get("wind"), "reported", "nflverse", as_of) if row and row.get("wind") is not None
           else (unknown("no wind in data (nflverse fills wind after games)", "nflverse", as_of) if outdoors else
                 fact(0, "derived", note="indoor / dome") if outdoors is False else unknown("roof unknown")),
           "temp_f": fact(row.get("temp"), "reported", "nflverse", as_of) if row and row.get("temp") is not None
           else unknown("no temperature in data")}
    model_inp = None
    if epa.get(g["home"]["abbr"]) and epa.get(g["away"]["abbr"]):
        eh, ea = epa[g["home"]["abbr"]], epa[g["away"]["abbr"]]
        model_inp = {"home": {"off_epa": eh.get("off_epa_play"), "def_epa": eh.get("def_epa_play"),
                              "games": eh.get("games"), "rest": (row or {}).get("home_rest")},
                     "away": {"off_epa": ea.get("off_epa_play"), "def_epa": ea.get("def_epa_play"),
                              "games": ea.get("games"), "rest": (row or {}).get("away_rest")},
                     "outdoors": outdoors, "wind_mph": val(env["wind_mph"]) if outdoors else None, "neutral": False}
    context = {"division_game": bool((row or {}).get("div_game")), "week": (row or {}).get("week"),
               "stadium": (row or {}).get("stadium")}
    return {"research": research, "starters": starters, "injuries": injuries, "environment": env,
            "injury_report": {"source": "NFL injury report (nflverse)", "week": (d.get("injuries") or {}).get("week")},
            "model_input": model_inp, "context": context, "venue": (row or {}).get("stadium"),
            "freshness": {"team_stats": (as_of, status, "nflverse play-by-play", "derived"),
                          "injuries": (as_of, status, "nflverse injury report (weekly)", "reported"),
                          "starters": (as_of, status, "nflverse schedule", "projected")}}


def nhl_section(raw: dict, g: dict) -> dict:
    d = raw.get("nhl") or {}
    as_of, status = _src_meta(raw, "nhl")
    row = next((x for x in d.get("games") or [] if x["home"]["abbr"] == g["home"]["abbr"]
                and x["away"]["abbr"] == g["away"]["abbr"]), None)
    if not row:
        return {"research": {}, "starters": {}, "injuries": [], "environment": {"outdoors": False},
                "model_input": None, "context": {}, "note": "game not in nhl.json",
                "freshness": {"team_stats": (as_of, status, "NHL API + MoneyPuck", "derived")}}
    research, starters, mi = {}, {}, {}
    for side in ("away", "home"):
        t = row[side]
        cur = (t.get("goalies_this_season") or [])
        prev = (t.get("goalies_last_season") or [])
        prev_gs = {x.get("name"): x.get("gs") or 0 for x in prev}
        # most starts this season; ties (common in the first weeks) go to last season's workhorse
        ranked = sorted(cur, key=lambda x: (-(x.get("gs") or 0), -prev_gs.get(x.get("name"), 0)))
        pick = ranked[0] if ranked else (prev[0] if prev else None)
        gname = (pick or {}).get("name")
        gs_now = next((x for x in t.get("gsax_this_season") or [] if x.get("name") == gname), None)
        gs_prev = next((x for x in t.get("gsax_last_season") or [] if x.get("name") == gname), None)
        gsax = (gs_now or {}).get("gsax", 0) + (gs_prev or {}).get("gsax", 0) if (gs_now or gs_prev) else None
        ggp = (gs_now or {}).get("gp", 0) + (gs_prev or {}).get("gp", 0) if (gs_now or gs_prev) else None
        conf = (t.get("starting_goalie") or {}).get("status") == "confirmed"
        starters[side] = fact(gname, "confirmed" if conf else "projected", "NHL API goalie usage", as_of,
                              note="most starts this season; not confirmed" if cur else
                              "last season's #1, no starts this season") if gname else unknown("no goalie data")
        options = []
        for nm in dict.fromkeys([x.get("name") for x in cur + prev[:2] if x.get("name")]):
            a_ = next((x for x in t.get("gsax_this_season") or [] if x.get("name") == nm), None)
            b_ = next((x for x in t.get("gsax_last_season") or [] if x.get("name") == nm), None)
            if a_ or b_:
                options.append({"name": nm, "gsax": round((a_ or {}).get("gsax", 0) + (b_ or {}).get("gsax", 0), 1),
                                "gp": (a_ or {}).get("gp", 0) + (b_ or {}).get("gp", 0)})
        research[side] = {
            "goalie_options": options,
            "standings": fact(t.get("standings"), "reported", "NHL API standings", as_of) if t.get("standings")
            else unknown("standings missing"),
            "xg_5v5_this_season": t.get("xg_5v5_this_season"), "xg_5v5_last_season": t.get("xg_5v5_last_season"),
            "sample_quality": (t.get("xg_5v5_this_season") or {}).get("sample_quality"),
            "games_sampled": (t.get("standings") or {}).get("gp"),
            "back_to_back": fact(bool(t.get("back_to_back")), "derived", "NHL schedule", as_of),
            "goalie": {"name": gname, "gp_this_season": (pick or {}).get("gp") if cur else 0,
                       "gsax_combined": gsax, "gp_combined": ggp,
                       "note": "GSAx combines this season and last (MoneyPuck)"},
        }
        mi[side] = {"xg_now": t.get("xg_5v5_this_season"), "xg_prev": t.get("xg_5v5_last_season"),
                    "goalie": {"name": gname, "gsax": gsax, "gp": ggp,
                               "status": "confirmed" if conf else "projected"},
                    "b2b": bool(t.get("back_to_back"))}
    injuries, inj_as_of = _espn_injuries(raw, "NHL", g["away"]["abbr"], g["home"]["abbr"])
    inj_status = _src_meta(raw, "injuries")[1]
    return {"research": research, "starters": starters, "injuries": injuries,
            "environment": {"outdoors": False}, "model_input": mi, "venue": row.get("venue"),
            "context": {"game_type": row.get("game_type")},
            "freshness": {"team_stats": (as_of, status, "NHL API + MoneyPuck", "derived"),
                          "starters": (as_of, status, "NHL API goalie usage (projection)", "projected"),
                          "injuries": (inj_as_of, inj_status, "ESPN injury list", "reported")}}


def mlb_section(raw: dict, g: dict) -> dict:
    d = raw.get("mlb") or {}
    as_of, status = _src_meta(raw, "mlb")
    row = next((x for x in d.get("games") or [] if x["home"]["abbr"] == g["home"]["abbr"]
                and x["away"]["abbr"] == g["away"]["abbr"]), None)
    if not row:
        return {"research": {}, "starters": {}, "injuries": [], "environment": {"outdoors": None},
                "model_input": None, "context": {}, "note": "game not in mlb.json",
                "freshness": {"team_stats": (as_of, status, "MLB Stats API", "reported")}}
    research, starters, lineups, mi = {}, {}, {}, {}
    for side, opp in (("away", "home"), ("home", "away")):
        t, o = row[side], row[opp]
        p = t.get("probable") or {}
        s = p.get("season") or {}
        kbb = (s.get("k_pct") - s.get("bb_pct")) if s.get("k_pct") is not None and s.get("bb_pct") is not None else None
        starters[side] = fact(p.get("name"), "projected", "MLB probable pitcher", as_of,
                              note="announced probable, not a confirmed lineup card") if p.get("name") \
            else unknown("no probable pitcher announced", "MLB Stats API", as_of)
        hand = (o.get("probable") or {}).get("throws")
        bats = (t.get("bats") or {}).get({"L": "vs_LHP", "R": "vs_RHP"}.get(hand or ""), None)
        bp = t.get("bullpen") or {}
        research[side] = {
            "starter": {"name": p.get("name"), "throws": p.get("throws"), "k_pct": s.get("k_pct"),
                        "bb_pct": s.get("bb_pct"), "era": s.get("era"), "starts": p.get("starts_counted"),
                        "sample_quality": p.get("sample_quality"), "k_mean": p.get("k_mean"),
                        "outs_mean": p.get("outs_mean"), "short_start_flag": p.get("short_start_flag")},
            "bats_vs_opp_hand": fact(bats, "reported", f"MLB team splits vs {hand}HP", as_of) if bats
            else unknown("opposing starter's hand or splits unknown"),
            "bullpen": {"pitches_last3": bp.get("reliever_pitches_last3"), "limited": bp.get("likely_limited") or [],
                        "kind": "reported", "source": "MLB boxscores (last 3 days)"},
            "record": t.get("record"),
        }
        lu = t.get("lineup")
        lineups[side] = fact(lu, "confirmed", "MLB lineup card", as_of) if lu \
            else unknown("lineup not posted yet", "MLB Stats API", as_of)
        mi[side] = {"bats_vs_opp_hand": {"ops": float(bats["ops"]) if bats and bats.get("ops") else None,
                                         "pa": (bats or {}).get("pa")} if bats else None,
                    "starter": {"name": p.get("name"), "kbb": kbb, "bf": s.get("bf"),
                                "era": _num(s.get("era")), "ip": _innings(s.get("ip")),
                                "outs_mean": (p.get("outs_mean") or {}).get("season"),
                                "starts": p.get("starts_counted"), "status": "projected"} if p.get("name") else None,
                    "bullpen": {"tired": bool(bp.get("likely_limited")) or (bp.get("reliever_pitches_last3") or 0) >= 120,
                                "pitches_last3": bp.get("reliever_pitches_last3")}}
    if not (mi["home"]["bats_vs_opp_hand"] and mi["away"]["bats_vs_opp_hand"]):
        mi = None
    else:
        mi["postseason"] = row.get("game_type") in ("F", "D", "L", "W")
    w = row.get("weather") or {}
    wind_mph = None
    if w.get("wind"):
        m = re.match(r"\s*(\d+)", str(w["wind"]))
        wind_mph = int(m.group(1)) if m else None
    indoor = (w.get("condition") or "").lower() in ("dome", "roof closed")
    env = {"outdoors": None if not w else not indoor,
           "condition": fact(w.get("condition"), "reported", "MLB Stats API weather", as_of) if w.get("condition")
           else unknown("no weather posted yet", "MLB Stats API", as_of),
           "temp_f": fact(_num(w.get("temp")), "reported", "MLB Stats API weather", as_of) if w.get("temp")
           else unknown("no temperature posted"),
           "wind_mph": fact(wind_mph, "reported", "MLB Stats API weather", as_of, note=w.get("wind"))
           if wind_mph is not None else unknown("no wind posted", "MLB Stats API", as_of),
           "venue": row.get("venue"),
           "park_factor": unknown("park factors not in the data yet")}
    injuries, inj_as_of = _espn_injuries(raw, "MLB", g["away"]["abbr"], g["home"]["abbr"])
    return {"research": research, "starters": starters, "lineups": lineups, "injuries": injuries,
            "environment": env, "model_input": mi, "venue": row.get("venue"),
            "context": {"series": row.get("series"), "series_game": row.get("series_game"),
                        "series_status": row.get("series_status"), "hp_umpire": row.get("hp_umpire")},
            "freshness": {"team_stats": (as_of, status, "MLB Stats API", "reported"),
                          "starters": (as_of, status, "MLB probables", "projected"),
                          "lineups": (as_of, status, "MLB lineup cards", "confirmed" if any(
                              val(v) for v in lineups.values()) else "projected"),
                          "weather": (as_of, status, "MLB Stats API weather", "reported"),
                          "injuries": (inj_as_of, _src_meta(raw, "injuries")[1], "ESPN injury list", "reported")}}


def _innings(ip) -> float | None:
    """'193.2' innings -> 193.667."""
    if ip in (None, ""):
        return None
    whole, _, frac = str(ip).partition(".")
    try:
        return int(whole) + (int(frac) / 3 if frac else 0)
    except ValueError:
        return None


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


SECTIONS = {"NFL": nfl_section, "NHL": nhl_section, "MLB": mlb_section}


# ---------------------------------------------------------------- the card

def build_card(raw: dict, g: dict, history: markets.MarketHistory, now: dt.datetime) -> dict:
    league = g["league"]
    now_iso = T.iso_et(now)
    odds = raw.get("odds") or {}
    odds_as_of = odds.get("pulled_at_et")
    sec = SECTIONS.get(league, lambda r, x: None)(raw, g) or {
        "research": {}, "starters": {}, "injuries": [], "environment": {}, "model_input": None, "context": {},
        "note": f"No {league} league data is joined into research cards yet (market data only).", "freshness": {}}
    entry = history.get(g["key"], g["date"])
    mkt = markets.lines(entry, now_iso)
    mkt["actionable_book"] = markets.ACTIONABLE_BOOK
    mkt["legend"] = {"fanduel": "actionable price (Kaire's book)", "draftkings": "book price, context",
                     "best": "best across tracked books: context only, not necessarily obtainable",
                     "fair": "no-vig probability (Pinnacle, else consensus): an estimate, not a price"}
    if g.get("odds_event"):
        mkt["event_flags"] = g["odds_event"].get("flags") or []
    model = (models.run(league, sec["model_input"]) if sec.get("model_input") else
             models.unavailable(sec.get("note") or ("no Boolin model for " + league if league not in models.MODELS
                                                     else "model inputs missing from the league data")))
    status = "scheduled"
    if g["start"] and now >= g["start"]:
        status = "started"
    card = {
        "schema": SCHEMA_VERSION + "/card", "key": g["key"], "league": league, "status": status,
        "date_et": g["date"], "start_et": T.iso_et(g["start"]) if g["start"] else None,
        "start_label": T.label_et(g["start"], with_day=True) if g["start"] else "start time unknown",
        "built_at": now_iso,
        "game": {"away": g["away"], "home": g["home"], "venue": sec.get("venue"),
                 "home_away": f"{g['away']['abbr']} @ {g['home']['abbr']}", "context": sec.get("context") or {},
                 "tv": unknown("TV not in the data")},
        "market": mkt,
        "research": sec.get("research") or {},
        "availability": {"starters": sec.get("starters") or {}, "lineups": sec.get("lineups") or {},
                         "injuries": sec.get("injuries") or [], "injury_report": sec.get("injury_report")},
        "environment": sec.get("environment") or {},
        "model": model,
        "comparison": compare.compare(league, model, mkt, g["home"]["abbr"], g["away"]["abbr"]),
        "notes": [sec["note"]] if sec.get("note") else [],
    }
    # freshness: one row per component, with kind and the source's own status
    fr: dict = {}
    sp_comp = ((odds.get("components") or {}).get(league)) or ((odds.get("sports") or {}).get(league) or {}).get("status")
    odds_status = ("error" if sp_comp == "error" else
                   (((raw.get("manifest") or {}).get("sources") or {}).get("odds") or {}).get("status"))
    fr["odds"] = (odds_as_of, odds_status, "The Odds API (FanDuel, DraftKings, Pinnacle + others)", "reported")
    fr.update(sec.get("freshness") or {})
    if model.get("available"):
        fr["model"] = (now_iso, "ok", f"Boolin {model['version']}", "derived")
    card["freshness"] = {k: _fresh(v, now) for k, v in fr.items()}
    return card


def _fresh(v: tuple, now: dt.datetime) -> dict:
    as_of, status, source, kind = v
    t = T.parse(as_of)
    age = T.age_minutes(t, now)
    return {"as_of": as_of, "age_min": age, "label": T.age_label(age), "status": status or "unknown",
            "source": source, "kind": kind}
