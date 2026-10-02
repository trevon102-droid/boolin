"""Offline checks for the odds math and summarizers (no network)."""
from pipeline import common as C
from pipeline import odds, mlb


def test_devig_two_way():
    fair = C.devig([-110, -110])
    assert abs(fair[0] - 0.5) < 1e-9 and abs(sum(fair) - 1) < 1e-9


def test_american_roundtrip():
    assert C.to_american(0.5) == -100
    assert C.to_american(0.6) == -150
    assert C.to_american(0.4) == 150
    assert abs(C.implied(-150) - 0.6) < 1e-9


def test_outs():
    assert mlb._outs("5.2") == 17
    assert mlb._outs("6.0") == 18
    assert mlb._outs(None) is None


EVENT = {
    "id": "e1", "commence_time": "2026-09-30T23:30:00Z", "home_team": "Toronto Maple Leafs",
    "away_team": "New York Islanders",
    "bookmakers": [
        {"key": "pinnacle", "markets": [
            {"key": "h2h", "outcomes": [{"name": "New York Islanders", "price": 105}, {"name": "Toronto Maple Leafs", "price": -115}]},
            {"key": "totals", "outcomes": [{"name": "Over", "price": 100, "point": 6.0}, {"name": "Under", "price": -110, "point": 6.0}]},
            {"key": "spreads", "outcomes": [{"name": "New York Islanders", "price": -240, "point": 1.5}, {"name": "Toronto Maple Leafs", "price": 200, "point": -1.5}]},
        ]},
        {"key": "fanduel", "markets": [
            {"key": "h2h", "outcomes": [{"name": "New York Islanders", "price": 102}, {"name": "Toronto Maple Leafs", "price": -122}]},
            {"key": "totals", "outcomes": [{"name": "Over", "price": 116, "point": 6.5}, {"name": "Under", "price": -142, "point": 6.5}]},
        ]},
        {"key": "draftkings", "markets": [
            {"key": "h2h", "outcomes": [{"name": "New York Islanders", "price": 108}, {"name": "Toronto Maple Leafs", "price": -128}]},
        ]},
    ],
}


def test_summarize_event():
    s = odds.summarize_event(EVENT)
    ml = s["markets"]["moneyline"]
    assert ml["ref"] == "pinnacle"
    nyi = ml["outcomes"][0]
    assert nyi["name"] == "New York Islanders"
    assert nyi["best_price"] == 108 and nyi["best_book"] == "draftkings"
    assert 0.47 < nyi["fair_prob"] < 0.49
    assert s["markets"]["total"]["line"] == 6.0  # Pinnacle's line is the main line
    assert s["markets"]["spread"]["home_line"] == -1.5
    assert s["start_et"] == "7:30 PM ET"


def test_props_grouping():
    ev = {"bookmakers": [
        {"key": "pinnacle", "markets": [{"key": "pitcher_strikeouts", "outcomes": [
            {"name": "Over", "description": "Hunter Brown", "price": -150, "point": 5.5},
            {"name": "Under", "description": "Hunter Brown", "price": 120, "point": 5.5}]}]},
        {"key": "fanduel", "markets": [{"key": "pitcher_strikeouts", "outcomes": [
            {"name": "Over", "description": "Hunter Brown", "price": -140, "point": 5.5},
            {"name": "Under", "description": "Hunter Brown", "price": 110, "point": 5.5}]}]},
    ]}
    rows = odds.summarize_props(ev)
    assert len(rows) == 1
    r = rows[0]
    assert r["player"] == "Hunter Brown" and r["line"] == 5.5 and r["ref"] == "pinnacle"
    assert r["outcomes"][0]["name"] == "Over" and r["outcomes"][0]["best_price"] == -140


def test_nba_wired():
    from pipeline import build_all, espn
    assert odds.SPORTS["NBA"] == "basketball_nba"
    assert "basketball_nba" in odds.PROP_MARKETS
    assert espn.LEAGUES["NBA"] == ("basketball", "nba")
    assert "nba" in build_all.SOURCES


def test_nba_held_until_regular_season():
    import datetime as dt
    from pipeline import nba
    assert nba.run(dt.date(2026, 10, 3))["status"] == "skipped"
    assert odds.START_DATES["NBA"] == dt.date(2026, 10, 20)
