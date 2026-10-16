import random

from pipeline import sim


def _fb_team(abbr, plays=63, ypc=4.3, ypa=6.9, comp=0.65, pr=0.58, blitz=None, man=None):
    return {"abbr": abbr, "plays_pg": plays, "pass_rate": pr, "ypc": ypc, "ypa": ypa, "comp_pct": comp,
            "int_rate": 0.022, "sack_rate": 0.067,
            "def": {"ypc_allowed": ypc, "ypa_allowed": ypa, "comp_pct_allowed": comp, "sack_rate": 0.067,
                    "int_rate": 0.022, "blitz_pct": blitz, "man_pct": man, "zone_pct": None if man is None else 1 - man},
            "players": [
                {"name": "QB", "pos": "QB", "gp": 1, "carries": 3, "rush_yds": 12},
                {"name": "RB", "pos": "RB", "gp": 1, "carries": 20, "rush_yds": 86, "rec": 3, "rec_yds": 20},
                {"name": "WR1", "pos": "WR", "gp": 1, "rec": 6, "rec_yds": 80},
                {"name": "WR2", "pos": "WR", "gp": 1, "rec": 4, "rec_yds": 50},
                {"name": "TE", "pos": "TE", "gp": 1, "rec": 4, "rec_yds": 40},
            ]}


def _fb_spec(sport="NFL", market=None, **kw):
    return {"sport": sport, "game": "A @ H", "away": _fb_team("A", **kw), "home": _fb_team("H", **kw),
            "env": {"neutral": True}, "market": market or {}}


def test_league_average_nfl_scores_like_the_league():
    spec = _fb_spec()
    e = sim.LEAGUE["NFL"]["finish"]
    f = sim.football_factory(spec)((e, e))
    rng = random.Random(3)
    pts = [f(rng)["total"] / 2 for _ in range(1500)]
    avg = sum(pts) / len(pts)
    assert 19 <= avg <= 25, avg


def test_runs_stop_when_stable_and_report_count():
    r = sim.simulate(_fb_spec(market={"spread_home": -3.0, "total": 44.5}), seed=1)
    runs = r["runs"]
    assert runs["batch_size"] == 100
    assert 5 <= runs["batches"] <= 100
    assert runs["sims"] == runs["batches"] * 100
    assert runs["converged"] is True
    assert len(r["box"]["away"]["rows"]) == 5 and len(r["box"]["home"]["rows"]) == 5


def test_market_anchor_pulls_score_toward_the_line():
    raw = sim.simulate(_fb_spec(), seed=2, w=0.0)["score"]
    anch = sim.simulate(_fb_spec(market={"spread_home": -10.0, "total": 60.0}), seed=2, w=0.5)["score"]
    # market says home 35, away 25: the anchored home score moves up, away down vs raw
    assert anch["home"] > raw["home"] + 3
    assert anch["home"] > anch["away"]


def test_same_seed_is_reproducible():
    a = sim.simulate(_fb_spec(market={"spread_home": -3.0, "total": 44.5}), seed=9)
    b = sim.simulate(_fb_spec(market={"spread_home": -3.0, "total": 44.5}), seed=9)
    assert a["score"] == b["score"] and a["runs"] == b["runs"]


def test_blitz_tilts_targets_to_rb_te():
    base = sim.football_usage(_fb_team("A"), {"blitz": None, "man": None})
    blitz = sim.football_usage(_fb_team("A"), {"blitz": 0.40, "man": None})
    share = lambda u, n: dict(u["targets"])[n]
    assert share(blitz, "RB") > share(base, "RB")
    assert share(blitz, "WR1") < share(base, "WR1")


def test_league_average_mlb_and_nhl_are_sane():
    b = dict(name="x", pa=600, ab=535, h=131, hr=18, doubles=26, triples=2, k_pct=0.222, bb_pct=0.085, hbp=6)
    p = dict(name="SP", k_pct=0.222, bb_pct=0.077, hbp_pct=0.008, hr_per_bf=0.030, h_per_bf=0.22, bf_per_start=23)
    team = lambda a: dict(abbr=a, starter=dict(p), bullpen=dict(p), lineup=[dict(b, name=f"b{i}") for i in range(9)])
    f = sim.mlb_factory(dict(sport="MLB", game="g", away=team("A"), home=team("H"), env={}, market={}))((1.0, 1.0))
    rng = random.Random(4)
    runs = sum(f(rng)["total"] for _ in range(2000)) / 4000
    assert 3.6 <= runs <= 4.9, runs
    sk = [dict(name=f"s{i}", pos="C", gp=10, sog=25, goals=3, assists=4, toi_pg="18:00") for i in range(12)]
    nt = lambda a: dict(abbr=a, shots_pg=29, shots_against_pg=29, goalie=dict(name="G", sv_pct=0.900, shots_against=300, sv_pct_last_season=0.900), skaters=sk)
    r = sim.simulate(dict(sport="NHL", game="A @ H", away=nt("A"), home=nt("H"), market={}), seed=5)
    assert 5.0 <= r["score"]["total"] <= 7.5, r["score"]
