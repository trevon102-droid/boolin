"""Offline tests for the research layer (no network). Fixtures are small hand-built raw files."""
import copy
import datetime as dt
import json
import tempfile
from pathlib import Path

from research import (board, build as B, card as C, changes, compare, comparables, flags, grading, markets, models,
                      notebook, provenance as P, render, scenarios, summary, teams, timeutil as T)

ET = T.ET


# ------------------------------------------------------------------ fixtures

def side(fd=None, dk=None, pin=None, best=None, book=None):
    books = {k: v for k, v in (("fanduel", fd), ("draftkings", dk), ("pinnacle", pin)) if v is not None}
    return {"books": books, "fanduel": fd, "best_price": best if best is not None else (max(books.values()) if books else None),
            "best_book": book or (next(iter(books)) if books else None)}


def odds_event(eid, away, home, start_utc, spread=-3.0, total=47.5, ml=(130, -150), fd_spread=(-110, -110),
               fd_total=(-110, -110), fair_home=0.58, with_total=True, fd=True):
    ev = {"id": eid, "commence_time": start_utc, "away": away, "home": home, "markets": {
        "moneyline": {"ref": "pinnacle", "outcomes": [
            {"name": away, "fair_prob": round(1 - fair_home, 4), **side(ml[0] if fd else None, ml[0] + 5, ml[0] + 3)},
            {"name": home, "fair_prob": fair_home, **side(ml[1] if fd else None, ml[1] - 5, ml[1] + 3)}]},
        "spread": {"ref": "pinnacle", "home_line": spread, "outcomes": [
            {"name": away, "fair_prob": 0.5, **side(fd_spread[0] if fd else None, -108, -105)},
            {"name": home, "fair_prob": 0.5, **side(fd_spread[1] if fd else None, -112, -105)}]}}}
    if with_total:
        ev["markets"]["total"] = {"ref": "pinnacle", "line": total, "outcomes": [
            {"name": "Over", "fair_prob": 0.5, **side(fd_total[0] if fd else None, -110, -104)},
            {"name": "Under", "fair_prob": 0.5, **side(fd_total[1] if fd else None, -110, -104)}]}
    return ev


def raw_fixture(pulled="2026-10-05T10:00-04:00", spread=-3.0, nfl_inj_status="Questionable", nhl_goalie_gs=2,
                mlb_lineup=None, nfl_week=4, odds_status="ok"):
    return {
        "manifest": {"slate_date": "2026-10-05", "run_at_et": pulled, "health": "ok",
                     "sources": {k: {"status": "ok"} for k in ("nfl", "nhl", "mlb", "injuries")} | {"odds": {"status": odds_status}}},
        "odds": {"pulled_at_et": pulled, "sports": {
            "NFL": {"status": "ok", "events": [odds_event("nfl1", "Atlanta Falcons", "New Orleans Saints",
                                                          "2026-10-06T00:15:00Z", spread=spread)]},
            "NHL": {"status": "ok", "events": [odds_event("nhl1", "Philadelphia Flyers", "Tampa Bay Lightning",
                                                          "2026-10-05T23:10:00Z", spread=-1.5, total=6.0, fair_home=0.66)]},
            "MLB": {"status": "ok", "events": [odds_event("mlb1", "New York Yankees", "Tampa Bay Rays",
                                                          "2026-10-06T00:00:00Z", spread=1.5, total=6.5, fair_home=0.46)]}}},
        "nfl": {"pulled_at_et": pulled, "date": "2026-10-05",
                "games": [{"gameday": "2026-10-05", "gametime": "20:15", "away_team": "ATL", "home_team": "NO",
                           "away_rest": 11, "home_rest": 8, "roof": "dome", "wind": None, "temp": None, "div_game": 1,
                           "week": 4, "away_qb_name": "Michael Penix Jr.", "home_qb_name": "Tyler Shough",
                           "stadium": "Caesars Superdome"}],
                "team_epa": {"teams": {
                    "ATL": {"games": 3, "off_epa_play": -0.16, "def_epa_play": -0.06, "off_epa_last3": -0.08, "sample_quality": "small"},
                    "NO": {"games": 3, "off_epa_play": 0.02, "def_epa_play": 0.07, "off_epa_last3": 0.03, "sample_quality": "small"}}},
                "injuries": {"week": nfl_week, "teams": {"NO": [{"player": "Noah Fant", "pos": "TE", "status": nfl_inj_status,
                                                                  "practice": "Limited", "injury": "Abdomen"}],
                                                          "ATL": [{"player": "Backup QB", "pos": "QB", "status": "Out", "injury": "Knee"}]}}},
        "nhl": {"pulled_at_et": pulled, "date": "2026-10-05", "games": [{
            "id": 1, "start_et": "7:10 PM ET", "game_type": "regular", "venue": "Benchmark Arena",
            "away": {"abbr": "PHI", "back_to_back": False, "standings": {"gp": 3, "sample_quality": "tiny"},
                     "starting_goalie": {"status": "unconfirmed"},
                     "goalies_this_season": [{"name": "Joseph Woll", "gp": nhl_goalie_gs, "gs": nhl_goalie_gs}],
                     "goalies_last_season": [{"name": "Dan Vladar", "gp": 52, "gs": 51}],
                     "xg_5v5_this_season": {"gp": 3, "xgf": 3.7, "xga": 9.7, "sample_quality": "tiny"},
                     "xg_5v5_last_season": {"gp": 82, "xgf": 153.8, "xga": 150.0, "sample_quality": "ok"},
                     "gsax_this_season": [{"name": "Joseph Woll", "gp": 2, "gsax": 3.2}],
                     "gsax_last_season": [{"name": "Dan Vladar", "gp": 52, "gsax": 13.8}]},
            "home": {"abbr": "TBL", "back_to_back": True, "standings": {"gp": 2, "sample_quality": "tiny"},
                     "starting_goalie": {"status": "unconfirmed"},
                     "goalies_this_season": [{"name": "Andrei Vasilevskiy", "gp": 2, "gs": 2}],
                     "goalies_last_season": [{"name": "Andrei Vasilevskiy", "gp": 58, "gs": 58}],
                     "xg_5v5_this_season": {"gp": 2, "xgf": 3.7, "xga": 3.2, "sample_quality": "tiny"},
                     "xg_5v5_last_season": {"gp": 82, "xgf": 177.0, "xga": 150.8, "sample_quality": "ok"},
                     "gsax_this_season": [{"name": "Andrei Vasilevskiy", "gp": 2, "gsax": 0.1}],
                     "gsax_last_season": [{"name": "Andrei Vasilevskiy", "gp": 58, "gsax": 24.7}]}}]},
        "mlb": {"pulled_at_et": pulled, "date": "2026-10-05", "games": [{
            "start_et": "8:00 PM ET", "game_type": "D", "series": "ALDS", "series_game": 2, "venue": "Tropicana Field",
            "weather": {"condition": "Dome", "temp": "72", "wind": "0 mph, None"},
            "away": {"abbr": "NYY", "name": "New York Yankees", "lineup": mlb_lineup,
                     "probable": {"name": "Cam Schlittler", "throws": "R", "starts_counted": 34, "sample_quality": "ok",
                                  "season": {"k_pct": 0.314, "bb_pct": 0.064, "bf": 761, "era": "1.95", "ip": "193.2"},
                                  "outs_mean": {"season": 17.4}},
                     "bats": {"vs_RHP": {"ops": ".714", "pa": 4034}, "vs_LHP": {"ops": ".733", "pa": 1963}},
                     "bullpen": {"reliever_pitches_last3": 46, "likely_limited": []}},
            "home": {"abbr": "TB", "name": "Tampa Bay Rays", "lineup": None,
                     "probable": {"name": "Freddy Peralta", "throws": "R", "starts_counted": 32, "sample_quality": "ok",
                                  "season": {"k_pct": 0.214, "bb_pct": 0.086, "bf": 730, "era": "4.42", "ip": "169.0"},
                                  "outs_mean": {"season": 15.8}},
                     "bats": {"vs_RHP": {"ops": ".752", "pa": 4134}},
                     "bullpen": {"reliever_pitches_last3": 130, "likely_limited": ["Pete Fairbanks"]}}}]},
        "injuries": {"pulled_at_et": pulled, "leagues": {"NHL": [{"game": "PHI @ TB", "injuries": {
            "TB": [{"player": "Yanni Gourde", "pos": "C", "status": "Injured Reserve", "detail": "Hip"}]}}],
            "MLB": [{"game": "NYY @ TB", "injuries": {"NYY": [{"player": "Aaron Judge", "pos": "RF", "status": "10-Day-IL"}]}}]}},
    }


def t(s):
    return T.parse(s)


# ------------------------------------------------------------------ market math & history

def test_devig_two_and_three_way():
    two = markets.devig([-110, -110])
    assert abs(two[0] - 0.5) < 1e-9
    three = markets.devig([150, 250, 200])
    assert abs(sum(three) - 1) < 1e-9 and three[0] > three[2] > three[1]
    assert markets.devig([-110, None]) is None
    assert markets.implied(50) is None  # invalid American odds


def test_observation_keeps_books_separate_and_missing_markets_missing():
    ev = odds_event("e", "A", "B", "2026-10-06T00:15:00Z", with_total=False, fd=False)
    o = markets.observation(ev, "2026-10-05T10:00-04:00")
    assert "total" not in o
    assert o["spread"]["home"]["fanduel"] is None and o["spread"]["home"]["draftkings"] == -112
    assert o["spread"]["home"]["best"] is not None  # best price is context, kept separate from fanduel


def test_observation_three_way_draw():
    ev = {"id": "s", "commence_time": "2026-10-06T00:00:00Z", "away": "A", "home": "B", "markets": {"moneyline": {
        "ref": "pinnacle", "outcomes": [{"name": "A", "fair_prob": 0.3, **side(250)}, {"name": "B", "fair_prob": 0.45, **side(120)},
                                        {"name": "Draw", "fair_prob": 0.25, **side(240)}]}}}
    o = markets.observation(ev, "2026-10-05T10:00-04:00")
    assert o["moneyline"]["fair_draw"] == 0.25 and o["moneyline"]["draw"]["fanduel"] == 240


def _hist(tmp):
    return markets.MarketHistory(Path(tmp) / "markets")


def test_history_append_only_open_current_close():
    with tempfile.TemporaryDirectory() as d:
        h = _hist(d)
        meta = {"commence_time": "2026-10-06T00:15:00Z"}
        e1 = odds_event("e", "A", "B", meta["commence_time"], spread=-2.5)
        e2 = odds_event("e", "A", "B", meta["commence_time"], spread=-3.5, fd_spread=(-105, -115))
        assert h.record("k", "2026-10-05", meta, markets.observation(e1, "2026-10-05T09:00-04:00"))
        assert not h.record("k", "2026-10-05", meta, markets.observation(e1, "2026-10-05T10:00-04:00"))  # identical
        assert h.record("k", "2026-10-05", meta, markets.observation(e2, "2026-10-05T12:00-04:00"))
        assert not h.record("k", "2026-10-05", meta, markets.observation(e1, "2026-10-05T11:00-04:00"))  # older replay
        assert not h.record("k", "2026-10-05", meta, markets.observation(e1, "2026-10-05T20:30-04:00"))  # after start
        h.save()
        h2 = _hist(d)  # reload from disk: nothing overwritten
        entry = h2.get("k", "2026-10-05")
        assert len(entry["obs"]) == 2 and entry["obs"][0]["last_seen"] == "2026-10-05T10:00-04:00"
        pre = markets.lines(entry, "2026-10-05T13:00-04:00")
        assert pre["spread"]["open"]["home_line"] == -2.5 and pre["spread"]["current"]["home_line"] == -3.5
        assert pre["spread"]["close"] is None
        assert pre["spread"]["movement"]["home_line"] == -1.0 and pre["spread"]["movement"]["toward"] == "home"
        mv = pre["spread"]["movement"]["fanduel_home"]
        assert mv["from"] == -110 and mv["to"] == -115 and mv["implied_change"] > 0
        post = markets.lines(entry, "2026-10-05T21:00-04:00")
        assert post["spread"]["close"]["home_line"] == -3.5


def test_history_rejects_naive_timestamp():
    with tempfile.TemporaryDirectory() as d:
        h = _hist(d)
        try:
            h.record("k", "2026-10-05", {}, {"t": "2026-10-05T10:00"})
            assert False, "naive timestamp must be rejected"
        except ValueError:
            pass


def test_missing_market_reported_not_filled():
    out = markets.lines({"meta": {}, "obs": [{"t": "2026-10-05T10:00-04:00", "moneyline": {
        "away": {}, "home": {}, "fair_home": 0.5}}]}, "2026-10-05T11:00-04:00")
    assert out["spread"]["available"] is False and "reason" in out["spread"]


def test_clv_spread_total_moneyline():
    close_sp = {"home_line": -4.0, "home": {"fanduel": -120}, "away": {"fanduel": 100}, "fair_home": 0.52}
    c = markets.clv({"market": "spread", "side": "home", "line": -2.5, "price": -110}, close_sp, "spread")
    assert c["line_clv_pts"] == 1.5 and "price_clv" not in c and "note" in c   # different number: line CLV only
    c = markets.clv({"market": "spread", "side": "away", "line": 4.0, "price": -105}, close_sp, "spread")
    assert c["line_clv_pts"] == 0 and "price_clv" in c
    close_tot = {"line": 45.5, "over": {"fanduel": -110}, "under": {"fanduel": -110}, "fair_over": 0.5}
    c = markets.clv({"market": "total", "side": "under", "line": 47.0, "price": -110}, close_tot, "total")
    assert c["line_clv_pts"] == 1.5
    close_ml = {"home": {"fanduel": -150}, "away": {"fanduel": 130}, "fair_home": 0.59}
    c = markets.clv({"market": "moneyline", "side": "home", "price": -130}, close_ml, "moneyline")
    assert c["price_clv"] > 0 and abs(c["prob_clv"] - (0.59 - markets.implied(-130))) < 1e-3
    assert markets.clv({"market": "moneyline", "side": "home", "price": -130}, None, "moneyline")["available"] is False


def test_clv_price_sign():
    close_sp = {"home_line": -4.0, "home": {"fanduel": -120}, "away": {"fanduel": 100}, "fair_home": 0.52}
    c = markets.clv({"market": "spread", "side": "away", "line": 4.0, "price": -105}, close_sp, "spread")
    assert c["price_clv"] < 0  # paid -105 for a number that closed at +100


# ------------------------------------------------------------------ time

def test_dst_start_from_label():
    oct_ = C.start_from_label("2026-10-05", "7:10 PM ET")
    nov = C.start_from_label("2026-11-02", "7:10 PM ET")
    assert oct_.utcoffset() == dt.timedelta(hours=-4) and nov.utcoffset() == dt.timedelta(hours=-5)
    assert C.start_from_label("2026-10-05", "TBD") is None
    assert T.parse("2026-10-05T10:00") is None  # naive rejected


def test_age_labels():
    now = t("2026-10-05T12:00-04:00")
    assert T.age_label(T.age_minutes(t("2026-10-05T11:56-04:00"), now)) == "4 min old"
    assert T.age_label(None) == "unknown age"
    assert T.age_label(1500) == "25 h old"


def test_teams_mapping():
    assert teams.abbr("NHL", "Montréal Canadiens") == "MTL" and teams.abbr("NHL", "St Louis Blues") == "STL"
    assert teams.abbr("NFL", "Nowhere Team") is None
    assert teams.espn("NHL", "TB") == "TBL" and teams.espn("NFL", "NO") == "NO"


# ------------------------------------------------------------------ provenance

def test_provenance_never_fills_missing():
    f = P.fact(None, "confirmed", "x")
    assert f["kind"] == "unknown" and P.is_unknown(f)
    assert P.val(P.fact(3, "projected")) == 3
    try:
        P.fact(1, "guessed")
        assert False
    except ValueError:
        pass


# ------------------------------------------------------------------ models

def _sum(m):
    return round(sum(c["margin"] for c in m["contributions"]), 2)


def test_models_are_additive_and_regress_small_samples():
    nfl_in = {"home": {"off_epa": 0.1, "def_epa": -0.05, "games": 2, "rest": 7},
              "away": {"off_epa": -0.1, "def_epa": 0.05, "games": 2, "rest": 7}, "outdoors": True, "wind_mph": 20}
    m = models.nfl(nfl_in)
    assert abs(_sum(m) - m["proj_margin_home"]) < 0.02
    big = copy.deepcopy(nfl_in); big["home"]["games"] = big["away"]["games"] = 16
    assert models.nfl(big)["proj_margin_home"] > m["proj_margin_home"]  # same rates, more games -> less shrinkage
    assert m["sample_warnings"] and m["confidence"] in ("low", "medium")
    assert any("Wind" in p["name"] for p in m["total_contributions"])
    assert models.nfl({"home": {}, "away": {}})["available"] is False
    raw = raw_fixture()
    g = next(x for x in C.enumerate_games(raw, "2026-10-05") if x["league"] == "NHL")
    nm = models.run("NHL", C.nhl_section(raw, g)["model_input"])
    assert abs(round(sum(c["margin"] for c in nm["contributions"]), 3) - nm["proj_margin_home"]) < 0.005
    g = next(x for x in C.enumerate_games(raw, "2026-10-05") if x["league"] == "MLB")
    mm = models.run("MLB", C.mlb_section(raw, g)["model_input"])
    assert abs(round(sum(c["margin"] for c in mm["contributions"]), 3) - mm["proj_margin_home"]) < 0.005
    assert any("Postseason" in a for a in mm["assumptions"])
    assert models.run("CFB", {})["available"] is False


# ------------------------------------------------------------------ cards, comparison, flags

def _build(raw, now, root, **kw):
    return B.build(raw, t(now), Path(root), results_fetcher=kw.pop("fetcher", None), nfl_history=kw.pop("hist", []),
                   **kw)


def _cards(root):
    return {c["key"]: c for c in json.loads((Path(root) / "latest" / "cards.json").read_text())["cards"]}


def test_card_provenance_and_projected_vs_confirmed():
    with tempfile.TemporaryDirectory() as d:
        _build(raw_fixture(), "2026-10-05T10:05-04:00", d)
        cards = _cards(d)
        nhl = cards["2026-10-05-NHL-PHI-TBL"]
        assert nhl["availability"]["starters"]["away"]["kind"] == "projected"
        mlb = cards["2026-10-05-MLB-NYY-TB"]
        assert mlb["availability"]["lineups"]["away"]["kind"] == "unknown"  # lineup not posted -> unknown, not filled
        assert mlb["availability"]["starters"]["home"]["kind"] == "projected"
        assert mlb["environment"]["park_factor"]["kind"] == "unknown"
        nfl = cards["2026-10-05-NFL-ATL-NO"]
        assert nfl["environment"]["outdoors"] is False
        assert nfl["freshness"]["odds"]["age_min"] == 5.0 and nfl["freshness"]["model"]["kind"] == "derived"
        assert nfl["game"]["tv"]["kind"] == "unknown"


def test_compare_labels_and_no_runline_margin():
    model = {"available": True, "home_win_p": 0.60, "proj_margin_home": 1.0, "proj_total": 8.0, "confidence": "high"}
    mk = {"moneyline": {"current": {"fair_home": 0.55, "ref": "pinnacle"}}, "spread": {"current": {"home_line": -1.5}},
          "total": {"current": {"line": 7.5}}}
    r = compare.compare("MLB", model, mk, "TB", "NYY")
    assert [x["dimension"] for x in r["rows"]] == ["Win probability (home)", "Total"]
    assert r["rows"][0]["label"] == "significant disagreement" and r["overall"] == "significant disagreement"
    r = compare.compare("NFL", {**model, "proj_margin_home": 1.4}, {**mk, "spread": {"current": {"home_line": -1.5}}}, "NO", "ATL")
    sp = next(x for x in r["rows"] if x["dimension"].startswith("Spread"))
    assert sp["label"] == "aligned"
    assert compare.label(None, compare.PROB_PP) is None
    assert compare.label(12, compare.PROB_PP) == "extreme disagreement"
    assert compare.compare("NFL", {"available": False, "reason": "x"}, mk, "A", "B")["available"] is False


def test_flags_explain_why():
    with tempfile.TemporaryDirectory() as d:
        _build(raw_fixture(odds_status="partial"), "2026-10-05T18:30-04:00", d)  # < 3h before NHL/MLB starts
        cards = _cards(d)
        nhl = {f["id"]: f for f in cards["2026-10-05-NHL-PHI-TBL"]["flags"]}
        assert nhl["availability.unconfirmed_starter_away"]["severity"] == "watch"
        assert "not officially confirmed" in nhl["availability.unconfirmed_starter_away"]["why"]
        assert "data.source_odds" in nhl and "partial" in nhl["data.source_odds"]["why"]
        assert "nhl.goalie_sample_away" in nhl
        mlb = {f["id"]: f for f in cards["2026-10-05-MLB-NYY-TB"]["flags"]}
        assert "mlb.bullpen_home" in mlb and "130 pitches" in mlb["mlb.bullpen_home"]["why"]
        assert "data.no_lineup_away" in mlb
    with tempfile.TemporaryDirectory() as d:
        _build(raw_fixture(), "2026-10-05T09:00-04:00", d)  # morning: unconfirmed goalie is only info
        nhl = {f["id"]: f for f in _cards(d)["2026-10-05-NHL-PHI-TBL"]["flags"]}
        assert nhl["availability.unconfirmed_starter_away"]["severity"] == "info"


def test_stale_odds_and_wind_flags():
    card = {"league": "NFL", "status": "scheduled", "game": {"home": {"abbr": "BUF"}, "away": {"abbr": "KC"}},
            "market": {}, "freshness": {"odds": {"age_min": 400, "label": "7 h old", "status": "ok"}},
            "environment": {"outdoors": True, "wind_mph": {"value": 19, "kind": "reported"}}, "availability": {}}
    ids = {f["id"] for f in flags.build(card)}
    assert {"data.stale_odds", "environment.wind", "data.missing_moneyline"} <= ids
    card["environment"]["wind_mph"] = P.unknown("no forecast")
    assert "data.missing_weather" in {f["id"] for f in flags.build(card)}


# ------------------------------------------------------------------ change detection & timeline

def test_change_timeline_and_why():
    with tempfile.TemporaryDirectory() as d:
        _build(raw_fixture(spread=-3.0, nfl_inj_status="Questionable"), "2026-10-05T10:05-04:00", d)
        r2 = raw_fixture(pulled="2026-10-05T12:00-04:00", spread=-4.5, nfl_inj_status="Out")
        r2["nfl"]["team_epa"]["teams"]["NO"]["off_epa_play"] = 0.20  # stats update -> model moves
        _build(r2, "2026-10-05T12:05-04:00", d)
        c = _cards(d)["2026-10-05-NFL-ATL-NO"]
        types = {(e["type"], e["field"]) for e in c["changes_since_last_pull"]}
        assert ("spread", "NO spread") in types and ("injury_status", "Noah Fant (NO)") in types
        sp = next(e for e in c["changes_since_last_pull"] if e["type"] == "spread")
        assert sp["old"] == "NO -3" and sp["new"] == "NO -4.5" and sp["cause"] == changes.NO_CAUSE
        assert sp["t"] == "2026-10-05T12:05-04:00" and sp["source"]
        inj = next(e for e in c["changes_since_last_pull"] if e["type"] == "injury_status")
        assert inj["direction"] == "downgrade"
        mod = next(e for e in c["changes_since_last_pull"] if e["type"] == "model")
        assert "Offense" in mod["cause"] and "NO spread" in mod["cause"]  # grounded: components + same-pull updates
        tl = json.loads((Path(d) / "timeline" / "2026-10-05.json").read_text())
        assert len(tl["2026-10-05-NFL-ATL-NO"]) == len(c["changes_since_last_pull"])
        _build(r2, "2026-10-05T12:30-04:00", d)  # same data again: no new events, history kept
        tl2 = json.loads((Path(d) / "timeline" / "2026-10-05.json").read_text())
        assert tl2["2026-10-05-NFL-ATL-NO"] == tl["2026-10-05-NFL-ATL-NO"]


def test_explain_without_component_move_says_unknown():
    mo = {"home_win_p": 0.55, "proj_margin_home": 1.0, "contributions": [{"name": "A", "margin": 1.0}]}
    mn = {"home_win_p": 0.56, "proj_margin_home": 1.03, "contributions": [{"name": "A", "margin": 1.03}]}
    w = changes.explain(mo, mn, [])
    assert "does not identify a single confirmed cause" in w["text"]


def test_injury_week_rollover_is_one_event():
    with tempfile.TemporaryDirectory() as d:
        _build(raw_fixture(nfl_week=4), "2026-10-05T10:05-04:00", d)
        r = raw_fixture(pulled="2026-10-05T12:00-04:00", nfl_week=5, nfl_inj_status="Probable")
        _build(r, "2026-10-05T12:05-04:00", d)
        ev = _cards(d)["2026-10-05-NFL-ATL-NO"]["changes_since_last_pull"]
        assert [e for e in ev if e["type"] == "injury_report"] and not [e for e in ev if e["type"] == "injury_status"]


def test_weather_and_starter_changes():
    old = {"key": "k", "built_at": "x", "game": {"home": {"abbr": "H"}, "away": {"abbr": "A"}},
           "environment": {"wind_mph": P.fact(9, "reported", "wx")},
           "availability": {"starters": {"home": P.fact("Goalie A", "projected", "nhl")}}}
    new = copy.deepcopy(old)
    new["environment"]["wind_mph"] = P.fact(19, "reported", "wx")
    new["availability"]["starters"]["home"] = P.fact("Goalie A", "confirmed", "dfo")
    ev = changes.diff(old, new)
    assert {e["type"] for e in ev} == {"weather", "starter"}


# ------------------------------------------------------------------ scenarios

def test_scenarios_supported_and_unsupported():
    with tempfile.TemporaryDirectory() as d:
        _build(raw_fixture(), "2026-10-05T10:05-04:00", d)
        cards = _cards(d)
        nfl = cards["2026-10-05-NFL-ATL-NO"]["scenarios"]["scenarios"]
        assert all(not s["supported"] for s in nfl)  # dome: no wind; QB sits not modeled
        assert any("QB" in s["name"] and "player-level" in s["reason"] for s in nfl)
        mlb = cards["2026-10-05-MLB-NYY-TB"]["scenarios"]["scenarios"]
        rep = next(s for s in mlb if "NYY starter replaced" in s["name"])
        assert rep["supported"] and rep["delta"]["home_win_p"] > 0  # losing an ace helps the home team
        nhl = cards["2026-10-05-NHL-PHI-TBL"]["scenarios"]["scenarios"]
        assert any("starts Dan Vladar" in s["name"] for s in nhl)
        rested = next(s for s in nhl if s["name"] == "TBL rested")
        assert rested["delta"]["home_win_p"] > 0


# ------------------------------------------------------------------ comparables

def test_nfl_comparables_historical_context():
    hist = [{"season": 2015 + i % 10, "week": 1 + i % 17, "away": "A", "home": "B", "spread_line": 3.0 + (i % 3) * 0.5,
             "total_line": 44.5, "result": (7 if i % 2 else -3), "total": 40 + i % 9, "home_rest": 7, "away_rest": 7,
             "wind": 5, "div_game": i % 2} for i in range(60)]
    card = {"market": {"spread": {"current": {"home_line": -3.5}}}, "research": {}, "environment": {},
            "game": {"context": {"division_game": True}}}
    out = comparables.nfl(card, hist)
    assert out["label"] == "HISTORICAL CONTEXT" and out["available"]
    band = out["situations"][0]
    assert band["n"] == 60 and 0 <= band["home_cover_pct"] <= 1
    combo = out["situations"][-1]
    assert combo["situation"].startswith("All of") and combo["n"] == 30
    assert comparables.nfl(card, [])["available"] is False
    small = comparables.nfl(card, hist[:5])
    assert "warning" in small["situations"][0]


# ------------------------------------------------------------------ notebook, grading, end to end

def test_notebook_save_merge_validate():
    with tempfile.TemporaryDirectory() as d:
        nb = notebook.save(d, "k", {"thesis": {"statement": "NO run game", "side": "home", "market": "spread"},
                                    "supporting": ["a"], "decision": "monitor", "entry_trigger": "only at -2.5"},
                           now="2026-10-05T10:00-04:00")
        nb = notebook.save(d, "k", {"supporting": ["a", "b"], "unknowns": ["Fant"]}, now="2026-10-05T11:00-04:00")
        assert nb["supporting"] == ["a", "b"] and nb["thesis"]["side"] == "home" and len(nb["history"]) == 2
        assert notebook.load(d, "k")["entry_trigger"] == "only at -2.5"
        for bad in ({"decision": "smash"}, {"thesis": {"side": "middle"}}):
            try:
                notebook.save(d, "k", bad)
                assert False
            except ValueError:
                pass


def test_grading_components():
    card = {"key": "k", "league": "NFL", "model": {"available": True, "home_win_p": 0.7, "proj_margin_home": 4.0,
                                                   "proj_total": 44.0},
            "market": {"spread": {"open": {"home_line": -2.5}, "close": {"home_line": -4.0, "home": {"fanduel": -110},
                                                                       "away": {"fanduel": -110}, "fair_home": 0.5}},
                       "moneyline": {"close": {"fair_home": 0.62}}},
            "environment": {"outdoors": False}}
    res = {"home_score": 27, "away_score": 20, "home_margin": 7, "total": 47}
    nb = {"thesis": {"market": "spread", "side": "home"}, "position": {"market": "spread", "side": "home", "line": -2.5, "price": -110}}
    g = grading.grade(card, res, nb)
    c = g["components"]
    assert c["model"]["gradable"] and c["model"]["brier"] == 0.09 and c["model"]["beat_market"] is True
    assert c["market_read"]["clv"]["line_clv_pts"] == 1.5 and c["market_read"]["grade"] == "A"
    assert c["thesis"]["result_at_close"] == "won" and c["thesis"]["grade"] == "A"
    assert not c["availability"]["gradable"] and not c["weather"]["gradable"]
    assert g["overall"]["grade"] in ("A", "A-") and g["overall"]["graded_components"] == 3
    lost = grading.grade(card, {**res, "home_margin": -3, "home_score": 17}, nb)
    assert lost["components"]["thesis"]["result_at_close"] == "lost"
    none = grading.grade({**card, "model": {"available": False}}, res, None)
    assert none["components"]["market_read"]["gradable"] is False and none["overall"]["grade"] is None
    assert grading.side_won("spread", "home", {"home_line": -7.0}, {"home_margin": 7}) == 0.5   # push


def test_end_to_end_snapshot_to_postgame():
    """source -> snapshot -> research object -> analyst view -> postgame evaluation."""
    with tempfile.TemporaryDirectory() as d:
        key = "2026-10-05-NFL-ATL-NO"
        notebook.save(Path(d) / "notebooks", key, {"thesis": {"statement": "Saints cover", "side": "home",
                                                              "market": "spread"}, "decision": "lean"})
        _build(raw_fixture(spread=-3.0), "2026-10-05T10:05-04:00", d)
        _build(raw_fixture(pulled="2026-10-05T19:00-04:00", spread=-4.0), "2026-10-05T19:05-04:00", d)
        # after kickoff: odds pulled post-start are ignored, card freezes with a close
        _build(raw_fixture(pulled="2026-10-05T21:00-04:00", spread=-9.0), "2026-10-05T21:05-04:00", d)
        arch = B._read(Path(d) / "archive" / "2026-10-05.json.gz", {})
        assert arch[key]["market"]["spread"]["close"]["home_line"] == -4.0

        def fetcher(league, date):
            return [{"away": {"abbr": "ATL", "name": "Atlanta Falcons"}, "home": {"abbr": "NO", "name": "New Orleans Saints"},
                     "completed": True, "away_score": 17, "home_score": 24}] if league == "NFL" else []
        m = _build(raw_fixture(pulled="2026-10-06T01:30-04:00", spread=-4.0), "2026-10-06T01:35-04:00", d, fetcher=fetcher)
        assert m["grades_written"] >= 1
        grades = json.loads((Path(d) / "grades" / "2026-10-05.json").read_text())
        g = grades[key]
        assert g["result"]["home_margin"] == 7 and g["components"]["thesis"]["result_at_close"] == "won"
        assert g["components"]["thesis"]["basis"] == "analyst thesis"
        # notebook untouched by builds, still there after the game
        assert notebook.load(Path(d) / "notebooks", key)["thesis"]["statement"] == "Saints cover"
        # grading happens once
        m2 = _build(raw_fixture(pulled="2026-10-06T02:30-04:00", spread=-4.0), "2026-10-06T02:35-04:00", d, fetcher=fetcher)
        assert m2["grades_written"] == 0
        html = (Path(d) / "latest" / "index.html").read_text()
        assert "Boolin Research Desk" in html and key in html


def test_one_bad_game_does_not_sink_the_build():
    with tempfile.TemporaryDirectory() as d:
        raw = raw_fixture()
        raw["mlb"]["games"][0]["away"]["probable"]["season"] = "corrupt"  # breaks only the MLB card
        m = _build(raw, "2026-10-05T10:05-04:00", d)
        cards = _cards(d)
        assert "2026-10-05-NFL-ATL-NO" in cards and m["status"] in ("partial", "ok")
        if m["status"] == "partial":
            assert any("MLB" in e for e in m["errors"])


def test_results_fetch_failure_is_recorded_not_fatal():
    with tempfile.TemporaryDirectory() as d:
        _build(raw_fixture(), "2026-10-05T10:05-04:00", d)

        def boom(league, date):
            raise RuntimeError("scoreboard down")
        m = _build(raw_fixture(pulled="2026-10-06T02:00-04:00"), "2026-10-06T02:05-04:00", d, fetcher=boom)
        assert any("scoreboard down" in e for e in m["errors"]) and m["status"] == "partial"


def test_board_ranks_attention_over_stable():
    a = {"key": "a", "league": "NFL", "game": {"home_away": "A @ B"}, "date_et": "2026-10-05", "status": "scheduled",
         "flags": [{"severity": "alert", "title": "QB out", "why": "x", "category": "injury"}],
         "changes_since_last_pull": [{"type": "spread", "field": "B spread", "old": -3, "new": -5}],
         "comparison": {"overall": "significant disagreement"}, "freshness": {}}
    s = {"key": "s", "league": "NHL", "game": {"home_away": "C @ D"}, "date_et": "2026-10-05", "status": "scheduled",
         "flags": [], "comparison": {"overall": "aligned"}, "freshness": {}}
    later = {**s, "key": "l", "date_et": "2026-10-11"}
    b = board.build([s, a, later], "2026-10-05", "now")
    assert [r["key"] for r in b["attention"]] == ["a"] and [r["key"] for r in b["stable"]] == ["s"]
    assert [r["key"] for r in b["upcoming"]] == ["l"] and "why_stable" in b["stable"][0]


def test_summary_conclusions():
    base = {"league": "NFL", "game": {"home": {"abbr": "NO"}, "away": {"abbr": "ATL"}, "context": {}},
            "market": {}, "availability": {}, "environment": {}, "flags": []}
    c = {**base, "model": {"available": False, "reason": "x"}, "comparison": {"available": False}}
    assert summary.build(c)["conclusion"]["status"] == "insufficient information"
    m = {"available": True, "contributions": [{"name": "Offense", "margin": 2.0, "note": "n"}], "confidence": "high"}
    c = {**base, "model": m, "comparison": {"available": True, "overall": "aligned", "rows": []}}
    assert summary.build(c)["conclusion"]["status"] == "pass"
    rows = [{"dimension": "Win probability (home)", "difference": 8.0, "label": "significant disagreement", "boolin": 0.6, "market": 0.52}]
    c = {**base, "model": m, "comparison": {"available": True, "overall": "significant disagreement", "rows": rows,
                                            "model_confidence": "high"}}
    s = summary.build(c)
    assert s["conclusion"]["status"] == "lean" and s["conclusion"]["team"] == "NO"
    assert "not a bet signal" in s["conclusion"]["detail"]
    s = summary.build(c, {"thesis": {"side": "away"}, "decision": "pass", "entry_trigger": "ATL +3"})
    assert s["thesis_basis"] == "analyst thesis" and s["conclusion"]["trigger"] == "ATL +3"
    assert s["conclusion"]["analyst_decision"] == "pass"


def test_render_escapes_script_close():
    html = render.page({"attention": [], "stable": [], "upcoming": []},
                       [{"key": "k", "x": "</script><b>"}], {}, standalone=False)
    assert "</script><b>" not in html.split("<script>", 1)[1].rsplit("</script>", 1)[0]


def test_nhl_goalie_tie_goes_to_last_seasons_starter():
    raw = raw_fixture()
    home = raw["nhl"]["games"][0]["home"]
    home["goalies_this_season"] = [{"name": "Backup", "gp": 1, "gs": 1}, {"name": "Starter", "gp": 1, "gs": 1}]
    home["goalies_last_season"] = [{"name": "Starter", "gp": 54, "gs": 54}, {"name": "Backup", "gp": 30, "gs": 28}]
    g = next(x for x in C.enumerate_games(raw, "2026-10-05") if x["league"] == "NHL")
    st = C.nhl_section(raw, g)["starters"]["home"]
    assert st["value"] == "Starter" and st["kind"] == "projected"
