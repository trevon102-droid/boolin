"""GameSim v1.1 tests: engine mechanics, market anchoring, distributions, data quality and labels.

Runs with pytest or `python tests/run_offline.py`. Sample sizes are chosen so tolerances are loose
enough not to flake (fixed seeds) yet tight enough to catch a broken mechanic.
"""
import copy
import random

from pipeline import sim

FAST = dict(batch=100, max_batches=12, min_batches=3, pilot=200)


# --------------------------------------------------------------------------- spec builders
def _fb_team(abbr, plays=63, ypc=4.3, ypa=6.9, comp=0.65, pr=0.58, blitz=None, man=None, pressure=None, qb=None, targets=True):
    players = [
        qb or {"name": f"{abbr} QB", "pos": "QB", "gp": 5, "att": 175, "comp": 114, "pass_yds": 1210, "pass_td": 8, "int": 4, "carries": 15, "rush_yds": 60},
        {"name": f"{abbr} RB", "pos": "RB", "gp": 5, "carries": 100, "rush_yds": 430, "rush_td": 3, "targets": 18 if targets else None, "rec": 15, "rec_yds": 100},
        {"name": f"{abbr} WR1", "pos": "WR", "gp": 5, "targets": 45 if targets else None, "rec": 30, "rec_yds": 400, "rec_td": 3},
        {"name": f"{abbr} WR2", "pos": "WR", "gp": 5, "targets": 30 if targets else None, "rec": 20, "rec_yds": 250, "rec_td": 1},
        {"name": f"{abbr} TE", "pos": "TE", "gp": 5, "targets": 25 if targets else None, "rec": 20, "rec_yds": 200, "rec_td": 2},
    ]
    return {"abbr": abbr, "games": 5, "plays_pg": plays, "pass_rate": pr, "ypc": ypc, "ypa": ypa, "comp_pct": comp,
            "int_rate": 0.022, "sack_rate": 0.067,
            "def": {"ypc_allowed": ypc, "ypa_allowed": ypa, "comp_pct_allowed": comp, "sack_rate": 0.067, "int_rate": 0.022,
                    "blitz_pct": blitz, "man_pct": man, "zone_pct": None if man is None else 1 - man, "pressure_pct": pressure},
            "players": players}


def _fb_spec(sport="NFL", market=None, env=None, away=None, home=None, **kw):
    return {"sport": sport, "game": "A @ H", "away": away or _fb_team("A", **kw), "home": home or _fb_team("H", **kw),
            "env": {"neutral": True} if env is None else env, "market": market or {}}


def _batter(name, bats="R", **kw):
    b = dict(name=name, pa=600, ab=535, h=131, hr=18, doubles=26, triples=2, k_pct=0.222, bb_pct=0.085, hbp=6, bats=bats)
    b.update(kw)
    return b


def _pitcher(name, throws="R", bf=23, **kw):
    p = dict(name=name, throws=throws, k_pct=0.222, bb_pct=0.077, hbp_pct=0.008, hr_per_bf=0.030, h_per_bf=0.22, bf_per_start=bf)
    p.update(kw)
    return p


def _mlb_team(abbr, bats="R", throws="R", bf=23, bullpen=None, lineup=None, **st):
    return {"abbr": abbr, "starter": _pitcher(f"{abbr} SP", throws, bf, **st),
            "bullpen": bullpen or dict(_pitcher(f"{abbr} pen"), reliever_pitches_last3=80),
            "lineup": lineup or [_batter(f"{abbr}{i}", bats) for i in range(9)], "lineup_status": "confirmed"}


def _mlb_spec(market=None, **kw):
    return {"sport": "MLB", "game": "A @ H", "away": _mlb_team("A"), "home": _mlb_team("H"), "env": {"temp_f": 70}, "market": market or {}, **kw}


def _skaters(prefix, pp1_first=5, sog=25, goals=3, assists=4):
    return [dict(name=f"{prefix}{i}", pos="D" if i >= 8 else "C", gp=10, sog=sog, goals=goals, assists=assists, toi_pg="18:00",
                 pp1=i < pp1_first, last_season=dict(gp=80, sog=sog * 8, goals=goals * 8, assists=assists * 8)) for i in range(12)]


def _nhl_team(abbr, sv=0.900, pp=0.21, pk=0.79, status="confirmed", skaters=None, **kw):
    t = dict(abbr=abbr, gp=10, shots_pg=29, shots_against_pg=29, pp_pct=pp, pk_pct=pk, pp_opps_pg=3.0, times_shorthanded_pg=3.0,
             goalie=dict(name=f"G{abbr}", sv_pct=sv, shots_against=300, sv_pct_last_season=0.900, status=status),
             skaters=skaters or _skaters(abbr))
    t.update(kw)
    return t


def _nhl_spec(market=None, away=None, home=None):
    return {"sport": "NHL", "game": "A @ H", "away": away or _nhl_team("A"), "home": home or _nhl_team("H"), "market": market or {}}


def _games(factory, n, seed=1, eff=(1.0, 1.0)):
    f = factory(eff)
    rng = random.Random(seed)
    return [f(rng) for _ in range(n)]


def _avg(rows, k):
    return sum(r.get(k, 0.0) for r in rows) / len(rows)


# --------------------------------------------------------------------------- general
def test_same_seed_is_reproducible():
    a = sim.simulate(_fb_spec(market={"spread_home": -3.0, "total": 44.5}), seed=9, **FAST)
    b = sim.simulate(_fb_spec(market={"spread_home": -3.0, "total": 44.5}), seed=9, **FAST)
    assert a["score"] == b["score"] and a["runs"] == b["runs"] and a["players"] == b["players"]


def test_different_seeds_land_close_at_normal_run_count():
    spec = _fb_spec(market={"spread_home": -3.0, "total": 44.5})
    a, b = sim.simulate(spec, seed=1), sim.simulate(spec, seed=2)
    assert a["runs"]["converged"] and b["runs"]["converged"]
    assert abs(a["score"]["home"] - b["score"]["home"]) < 1.0
    assert abs(a["score"]["total"] - b["score"]["total"]) < 1.5
    assert abs(a["score"]["home_win"] - b["score"]["home_win"]) < 0.05


def test_convergence_reporting():
    r = sim.simulate(_fb_spec(market={"spread_home": -3.0, "total": 44.5}), seed=1)
    ru = r["runs"]
    assert ru["batch_size"] == 100 and ru["sims"] == ru["batches"] * 100 and 5 <= ru["batches"] <= 100
    assert ru["converged"] is True and "STABLE" in r["status"]
    assert "not accurate" in ru["rule"]


def test_non_converged_is_reported():
    r = sim.simulate(_fb_spec(market={"spread_home": -3.0, "total": 44.5}), seed=1, batch=50, max_batches=2, min_batches=2, pilot=100)
    assert r["runs"]["converged"] is False and r["runs"]["batches"] == 2
    assert r["status"][0] == "NOT STABLE"


def test_market_anchor_pulls_score_toward_the_line_and_is_auditable():
    spec = _fb_spec(market={"spread_home": -10.0, "total": 60.0})
    raw = sim.simulate(spec, seed=2, w=0.0, **FAST)
    anch = sim.simulate(spec, seed=2, w=0.5, **FAST)
    au = anch["anchor_audit"]
    assert au["market_implied"]["home"] == 35.0 and au["market_implied"]["away"] == 25.0
    assert anch["score"]["home"] > raw["score"]["home"] + 3
    # final mean sits roughly halfway between raw and market
    for side in ("away", "home"):
        mid = (au["raw"][side] + au["market_implied"][side]) / 2
        assert abs(au["anchored"][side] - mid) < 2.0, (side, au)
    assert au["shift"]["home"] > 0 and "never back-solved" in au["method"]


def test_zero_and_full_and_out_of_range_anchor():
    spec = _fb_spec(market={"spread_home": -7.0, "total": 50.0})
    zero = sim.simulate(spec, seed=3, w=0.0, **FAST)
    assert zero["score"]["anchor"]["efficiency"] == [1.0, 1.0] and zero["anchor_audit"]["target"] is None
    full = sim.simulate(spec, seed=3, w=1.0, **FAST)
    assert abs(full["score"]["total"] - 50.0) < 2.5 and abs(full["score"]["margin"] - 7.0) < 2.5
    hi = sim.simulate(spec, seed=3, w=3.0, **FAST)
    assert hi["score"]["anchor"]["weight"] == 1.0
    assert any(f["field"] == "anchor" for f in hi["data_quality"]["flags"])


def test_missing_market_means_raw_only_and_is_flagged():
    r = sim.simulate(_fb_spec(market={}), seed=4, **FAST)
    assert r["anchor_audit"]["market_implied"] is None and "raw simulation only" in r["anchor_audit"]["method"]
    assert any(f["field"] == "market.total" for f in r["data_quality"]["flags"])
    assert "over" not in r["score"] and "home_cover" not in r["score"]
    assert "PARTIAL INPUTS" in r["status"]


def test_malformed_spec_raises():
    for bad in ({}, {"sport": "NBA", "away": {}, "home": {}}, {"sport": "NFL", "away": {}}, "nope"):
        try:
            sim.simulate(bad, **FAST)
        except ValueError as e:
            assert "malformed spec" in str(e)
        else:
            raise AssertionError(f"accepted {bad!r}")


def test_incomplete_spec_runs_but_flags_fallbacks():
    spec = _fb_spec(market={"total": 44.5, "spread_home": -3})
    del spec["away"]["plays_pg"], spec["away"]["ypa"]
    spec["away"]["def"].pop("ypa_allowed")
    r = sim.simulate(spec, seed=5, **FAST)
    fields = {f["field"] for f in r["data_quality"]["flags"]}
    assert "A.offense" in fields and "A.def" in fields
    assert r["data_quality"]["status"] == "partial"


def test_data_quality_states():
    assert sim.simulate(_fb_spec(market={"total": 44.5, "spread_home": -3}, env={"neutral": True, "wind_mph": 5}, blitz=0.3, man=0.3, pressure=0.32), **FAST)["data_quality"]["status"] in ("clean", "partial")
    # missing targets -> partial
    r = sim.simulate(_fb_spec(market={"total": 44.5, "spread_home": -3}, targets=False), **FAST)
    assert any(f["field"].endswith("targets") and f["level"] == "partial" for f in r["data_quality"]["flags"])
    # no players -> degraded
    spec = _fb_spec(market={"total": 44.5, "spread_home": -3})
    spec["home"]["players"] = []
    r = sim.simulate(spec, **FAST)
    assert r["data_quality"]["status"] == "degraded" and "DEGRADED INPUTS" in r["status"]
    # almost nothing about a team -> unknown
    spec = _fb_spec(market={"total": 44.5, "spread_home": -3})
    spec["home"] = {"abbr": "H", "players": spec["home"]["players"]}
    assert sim.simulate(spec, **FAST)["data_quality"]["status"] == "unknown"


def test_labels_never_claim_validation_or_edges():
    r = sim.simulate(_fb_spec(market={"spread_home": -3.0, "total": 44.5, "home_ml": -160, "away_ml": 135}), seed=1, **FAST)
    assert r["label"] == "Market-anchored simulation"
    assert "not calibrated or validated" in r["score"]["frequency_note"]
    assert r["market_diagnostic"]["note"].startswith("Not validated edge")
    banned = ("EDGE", "LOCK", "CONFIDENT", "BEST BET")
    assert not any(b in s for s in r["status"] for b in banned)
    assert "modelP" not in r and "modelP" not in r["score"]


# --------------------------------------------------------------------------- football
def test_league_average_nfl_and_cfb_score_like_the_league():
    for sport, (lo, hi) in (("NFL", (19, 25)), ("CFB", (23, 31))):
        args = dict(plays=63, ypc=4.3, ypa=6.9, comp=0.65, pr=0.58) if sport == "NFL" else dict(plays=70, ypc=5.0, ypa=7.3, comp=0.62, pr=0.5)
        rows = _games(sim.football_factory(_fb_spec(sport, **args)), 1200, seed=3)
        pts = _avg(rows, "total") / 2
        assert lo <= pts <= hi, (sport, pts)


def test_late_down_completions_are_not_forced_past_the_sticks():
    # realised yards per attempt stays close to the input (no blanket "past the sticks" rule)
    rows = _games(sim.football_factory(_fb_spec()), 1200, seed=4)
    ypa = _avg(rows, "0|A QB|pyds") / _avg(rows, "0|A QB|att")
    finish = sim.LEAGUE["NFL"]["finish"]
    assert ypa < 6.9 * finish * 1.06, ypa


def test_passing_and_rushing_volume_follow_pass_rate():
    pass_heavy = _games(sim.football_factory(_fb_spec(pr=0.68)), 600, seed=5)
    run_heavy = _games(sim.football_factory(_fb_spec(pr=0.45)), 600, seed=5)
    assert _avg(pass_heavy, "0|A QB|att") > _avg(run_heavy, "0|A QB|att") + 5
    assert _avg(run_heavy, "0|A RB|car") > _avg(pass_heavy, "0|A RB|car") + 3


def test_pressure_raises_sacks_and_cuts_passing():
    calm = _games(sim.football_factory(_fb_spec(pressure=0.25)), 800, seed=6)
    heat = _games(sim.football_factory(_fb_spec(pressure=0.42)), 800, seed=6)
    assert _avg(heat, "0|team_sacks") > _avg(calm, "0|team_sacks") + 0.4
    assert _avg(heat, "0|A QB|cmp") / _avg(heat, "0|A QB|att") < _avg(calm, "0|A QB|cmp") / _avg(calm, "0|A QB|att")


def test_wind_cuts_passing_and_scoring():
    calm = _games(sim.football_factory(_fb_spec(env={"neutral": True, "wind_mph": 3})), 800, seed=7)
    windy = _games(sim.football_factory(_fb_spec(env={"neutral": True, "wind_mph": 25, "precip": True})), 800, seed=7)
    assert _avg(windy, "0|A QB|pyds") < _avg(calm, "0|A QB|pyds") * 0.92
    assert _avg(windy, "total") < _avg(calm, "total")


def test_qb_replacement_changes_passing():
    good = {"name": "A QB", "pos": "QB", "gp": 5, "att": 175, "comp": 125, "pass_yds": 1450, "pass_td": 12, "int": 2}
    backup = {"name": "A QB", "pos": "QB", "gp": 2, "att": 60, "comp": 33, "pass_yds": 300, "pass_td": 1, "int": 4}
    g = _games(sim.football_factory(_fb_spec(away=_fb_team("A", qb=good), home=_fb_team("H"))), 800, seed=8)
    b = _games(sim.football_factory(_fb_spec(away=_fb_team("A", qb=backup), home=_fb_team("H"))), 800, seed=8)
    assert _avg(g, "0|A QB|pyds") > _avg(b, "0|A QB|pyds") + 10
    assert _avg(b, "0|A QB|int") > _avg(g, "0|A QB|int")


def test_blitz_tilts_targets_to_rb_te():
    base = sim.football_usage(_fb_team("A"), {"blitz": None, "man": None})
    blitz = sim.football_usage(_fb_team("A"), {"blitz": 0.40, "man": None})
    share = lambda u, n: dict(u["targets"])[n]
    assert share(blitz, "A RB") > share(base, "A RB") and share(blitz, "A WR1") < share(base, "A WR1")


def test_cfb_never_ends_tied_and_overtime_happens():
    rows = _games(sim.football_factory(_fb_spec("CFB", plays=70, ypc=5.0, ypa=7.3, comp=0.62, pr=0.5)), 1500, seed=9)
    assert all(r["margin"] != 0 for r in rows)
    ot = [r for r in rows if r["ot"]]
    assert ot and 0.02 < len(ot) / len(rows) < 0.15
    assert all(r["safety_resolved"] == 0 for r in rows)
    assert any(r["ot_periods"] >= 2 for r in ot)


def test_cfb_overtime_rules_directly():
    # regulation tie -> OT. A possession stub that always kicks a FG makes OT1 and OT2 ties, so OT3+
    # must be decided by alternating 2-point plays (only +2 increments from then on).
    pts = [21, 21]
    log = []

    def possession(s, yard, ctx):
        assert yard == 75 and ctx["phase"] == "cfb_ot"
        log.append(ctx["ot_n"])
        pts[s] += 3
        return "fg", None

    info = {"ot": 1, "ot_periods": 0, "tie": 0, "safety": 0}
    sim.cfb_overtime(random.Random(1), pts, possession, info, (1.0, 1.0), sim.LEAGUE["CFB"])
    assert pts[0] != pts[1] and info["ot_periods"] >= 3 and set(log) == {1, 2}
    assert (max(pts) - 27) % 2 == 0  # after the two FG rounds only 2-point plays score


def test_nfl_regular_season_ties_are_rare_and_postseason_has_none():
    rows = _games(sim.football_factory(_fb_spec()), 1500, seed=10)
    tie = _avg(rows, "tie")
    assert tie < 0.03
    post = _games(sim.football_factory(dict(_fb_spec(), postseason=True)), 1500, seed=10)
    assert all(r["margin"] != 0 for r in post)


def test_cfb_vs_nfl_scoring_and_clock():
    nfl = _games(sim.football_factory(_fb_spec("NFL")), 600, seed=11)
    cfb = _games(sim.football_factory(_fb_spec("CFB", plays=72)), 600, seed=11)
    assert _avg(cfb, "0|team_plays") > _avg(nfl, "0|team_plays") + 3   # pace drives plays
    hi = _games(sim.football_factory(_fb_spec("CFB", plays=75, ypc=6.3, ypa=9.5, comp=0.68, pr=0.5)), 600, seed=12)
    lo = _games(sim.football_factory(_fb_spec("CFB", plays=62, ypc=3.6, ypa=5.6, comp=0.55, pr=0.5)), 600, seed=12)
    assert _avg(hi, "total") > 70 and _avg(lo, "total") < 40


# --------------------------------------------------------------------------- MLB
def test_league_average_mlb_runs():
    f = sim.mlb_factory(_mlb_spec())
    rows = _games(f, 2000, seed=4, eff=(sim.MLB_FINISH,) * 2)
    runs = _avg(rows, "total") / 2
    assert 3.9 <= runs <= 5.0, runs


def test_platoon_advantage():
    assert sim.platoon("L", "L")[0] < 1.0 < sim.platoon("L", "R")[0]
    assert sim.platoon("R", "L")[3] < 1.0 < sim.platoon("R", "R")[3]
    vs_rhp = _mlb_spec()
    vs_lhp = _mlb_spec()
    for s in (vs_rhp, vs_lhp):
        s["away"]["lineup"] = [_batter(f"A{i}", "L") for i in range(9)]
    vs_lhp["home"]["starter"]["throws"] = "L"
    vs_lhp["home"]["bullpen"]["throws"] = "L"
    vs_rhp["home"]["bullpen"]["throws"] = "R"
    r_ = _games(sim.mlb_factory(vs_rhp), 1500, seed=5)
    l_ = _games(sim.mlb_factory(vs_lhp), 1500, seed=5)
    hits = lambda rows: sum(_avg(rows, f"0|A{i}|h") for i in range(9))
    ks = lambda rows: sum(_avg(rows, f"0|A{i}|k") for i in range(9))
    assert hits(l_) < hits(r_) and ks(l_) > ks(r_)


def test_starter_leash_and_bullpen_transition():
    short = _mlb_spec()
    short["home"]["starter"]["bf_per_start"] = 12
    long = _mlb_spec()
    long["home"]["starter"]["bf_per_start"] = 26
    s_rows = _games(sim.mlb_factory(short), 600, seed=6)
    l_rows = _games(sim.mlb_factory(long), 600, seed=6)
    assert _avg(l_rows, "1|P:H SP|outs") > _avg(s_rows, "1|P:H SP|outs") + 6
    assert _avg(s_rows, "1|P:bullpen|bf") > _avg(l_rows, "1|P:bullpen|bf") + 8
    assert 10 <= _avg(s_rows, "1|P:H SP|bf") <= 14


def test_opener_then_bulk_then_listed_relievers():
    spec = _mlb_spec()
    spec["home"]["starter"].update(bf_per_start=3, role="opener")
    spec["home"]["bulk"] = dict(_pitcher("H Bulk", "L"), bf_per_start=14)
    spec["home"]["bullpen"]["arms"] = [dict(_pitcher("H Closer"), bf_cap=4, avail="full"), dict(_pitcher("H Tired"), avail="out")]
    rows = _games(sim.mlb_factory(spec), 500, seed=7)
    assert 2.5 <= _avg(rows, "1|P:H SP|bf") <= 3.5
    assert 10 <= _avg(rows, "1|P:H Bulk|bf") <= 16
    assert _avg(rows, "1|P:H Closer|bf") > 2 and _avg(rows, "1|P:H Tired|bf") == 0


def test_extra_innings_never_tie_and_ghost_runner_is_unearned():
    reg = _games(sim.mlb_factory(_mlb_spec()), 2500, seed=8)
    assert all(r["margin"] != 0 for r in reg) and all(r["safety_resolved"] == 0 for r in reg)
    assert 0.04 < _avg(reg, "ot") < 0.16
    post = _games(sim.mlb_factory(_mlb_spec(postseason=True)), 2500, seed=8)
    assert all(r["margin"] != 0 for r in post)
    # the automatic runner makes regular-season extras shorter than postseason extras
    xr = [r["innings"] for r in reg if r["ot"]]
    xp = [r["innings"] for r in post if r["ot"]]
    assert sum(xr) / len(xr) < sum(xp) / len(xp)


def test_runs_vs_earned_runs_bookkeeping():
    rows = _games(sim.mlb_factory(_mlb_spec()), 1500, seed=9)
    for r in rows[:300]:
        for team in (0, 1):
            rk = [k for k in r if k.startswith(f"{team}|P:") and k.endswith("|r")]
            assert sum(r[k] for k in rk) == r["home_pts" if team == 0 else "away_pts"]
            for k in rk:
                assert r.get(k[:-1] + "er", 0) <= r[k]
    tot_r = sum(v for r in rows for k, v in r.items() if "|P:" in k and k.endswith("|r"))
    tot_er = sum(v for r in rows for k, v in r.items() if "|P:" in k and k.endswith("|er"))
    assert 0.9 < tot_er / tot_r < 1.0  # some runs are unearned (errors, automatic runner)


def test_missing_bullpen_workload_is_degraded():
    spec = _mlb_spec(market={"total": 8.5, "home_ml": -120, "away_ml": 100})
    spec["away"]["bullpen"].pop("reliever_pitches_last3")
    r = sim.simulate(spec, **FAST)
    assert r["data_quality"]["status"] == "degraded"
    assert any(f["field"] == "A.bullpen.workload" for f in r["data_quality"]["flags"])


# --------------------------------------------------------------------------- NHL
def test_league_average_nhl_goals_and_ot():
    f, meta = sim.nhl_factory(_nhl_spec())
    rows = _games(f, 4000, seed=3)
    assert 5.5 <= _avg(rows, "total") <= 6.7
    assert all(r["margin"] != 0 for r in rows)
    ot, so = _avg(rows, "ot"), _avg(rows, "shootout")
    assert 0.15 < ot < 0.30 and 0.0 < so < ot


def test_goalie_regression():
    hot = sim.goalie_sv({"sv_pct": 0.950, "shots_against": 120, "sv_pct_last_season": 0.905})
    assert 0.900 < hot < 0.915
    unknown = sim.goalie_sv({})
    assert abs(unknown - 0.900) < 1e-9
    no_last = sim.goalie_sv({"sv_pct": 0.930, "shots_against": 300})
    assert 0.900 < no_last < 0.910


def test_projected_goalie_uses_alternate_some_nights():
    away = _nhl_team("A")
    away["goalie"].update(status="projected", alt={"name": "GA2", "sv_pct": 0.880, "shots_against": 100})
    f, meta = sim.nhl_factory(_nhl_spec(away=away))
    rows = _games(f, 2000, seed=4)
    share_alt = _avg(rows, "0|G:GA2|gp")
    assert 0.12 < share_alt < 0.28  # projected = 80% start probability


def test_pp1_gives_a_conservative_scoring_advantage():
    sk = _skaters("A", pp1_first=0)  # nobody flagged PP1 ...
    sk[0]["pp1"] = True              # ... except player A0; A1 is identical but even-strength only
    f, _ = sim.nhl_factory(_nhl_spec(away=_nhl_team("A", skaters=sk)))
    rows = _games(f, 3000, seed=5)
    p_pp, p_es = _avg(rows, "0|A0|pts"), _avg(rows, "0|A1|pts")
    assert p_pp > p_es * 1.08 and p_pp < p_es * 2.0


def test_pp_pk_matchup_moves_goals():
    weak_pk = _games(sim.nhl_factory(_nhl_spec(home=_nhl_team("H", pk=0.70)))[0], 3000, seed=6)
    strong_pk = _games(sim.nhl_factory(_nhl_spec(home=_nhl_team("H", pk=0.88)))[0], 3000, seed=6)
    assert _avg(weak_pk, "0|team_ppg") > _avg(strong_pk, "0|team_ppg") + 0.08


def test_sog_allocation_follows_shot_rates_and_sums_to_team():
    sk = _skaters("A")
    sk[2]["sog"] = 45
    sk[2]["last_season"]["sog"] = 360
    f, meta = sim.nhl_factory(_nhl_spec(away=_nhl_team("A", skaters=sk)))
    rows = _games(f, 1500, seed=7)
    listed = sum(_avg(rows, f"0|A{i}|sog") for i in range(12))
    assert listed <= _avg(rows, "0|team_sog") + 1e-9
    assert _avg(rows, "0|A2|sog") > _avg(rows, "0|A3|sog") * 1.4
    assert all(0.9 <= x["toi_mod"] <= 1.1 for x in meta["sk"][0])


# --------------------------------------------------------------------------- player distributions
def _all_stats(r):
    for side in ("away", "home"):
        for p in r["players"][side]:
            for stat, d in p["stats"].items():
                yield p["name"], stat, d


def test_quantiles_are_ordered_and_line_probs_are_valid():
    specs = [_fb_spec(market={"spread_home": -3, "total": 44.5}), _mlb_spec(market={"total": 8.5}), _nhl_spec(market={"total": 6.5})]
    specs[0]["away"]["players"][2]["lines"] = {"rec_yds": [49.5, 59.5, 69.5], "rec": 4.5, "td": 0.5}
    specs[1]["props"] = [{"player": "A0", "stat": "h", "line": 0.5}, {"player": "H SP", "stat": "k", "line": 4.5}]
    specs[2]["props"] = [{"player": "A0", "stat": "sog", "lines": [1.5, 2.5]}, {"player": "H1", "stat": "pts", "line": 0.5}]
    n_lines = 0
    for spec in specs:
        r = sim.simulate(spec, **FAST)
        for name, stat, d in _all_stats(r):
            q = [d["p10"], d["p25"], d["p50"], d["p75"], d["p90"]]
            assert q == sorted(q), (name, stat, d)
            assert d["median"] == d["p50"]
            for ln in d.get("lines", []):
                n_lines += 1
                for k in ("over", "under", "push"):
                    assert 0.0 <= ln[k] <= 1.0
                assert abs(ln["over"] + ln["under"] + ln["push"] - 1.0) < 1e-3
    assert n_lines == 10


def test_ladder_rungs_come_from_one_distribution():
    spec = _fb_spec(market={"spread_home": -3, "total": 44.5})
    spec["away"]["players"][2]["lines"] = {"rec_yds": [39.5, 59.5, 79.5, 99.5]}
    r = sim.simulate(spec, **FAST)
    wr = next(p for p in r["players"]["away"] if p["name"] == "A WR1")
    overs = [ln["over"] for ln in wr["stats"]["rec_yds"]["lines"]]
    assert overs == sorted(overs, reverse=True) and overs[0] > overs[-1]


def test_prop_lines_never_change_the_projection():
    spec = _fb_spec(market={"spread_home": -3, "total": 44.5})
    a = sim.simulate(spec, seed=13, **FAST)
    with_line = copy.deepcopy(spec)
    with_line["away"]["players"][2]["lines"] = {"rec_yds": 120.5}
    b = sim.simulate(with_line, seed=13, **FAST)
    wa = next(p for p in a["players"]["away"] if p["name"] == "A WR1")["stats"]["rec_yds"]
    wb = next(p for p in b["players"]["away"] if p["name"] == "A WR1")["stats"]["rec_yds"]
    assert wa["mean"] == wb["mean"] and wa["p90"] == wb["p90"]


# --------------------------------------------------------------------------- market handling
def test_market_baselines():
    fb = sim.market_baseline("NFL", {"total": 47.5, "spread_home": -3.5})
    assert (fb["away"], fb["home"]) == (22.0, 25.5)
    ml = sim.market_baseline("MLB", {"total": 8.0, "home_ml": -150, "away_ml": 130})
    assert ml["home"] > ml["away"] and abs(ml["home"] + ml["away"] - 8.0) < 1e-9
    tot = sim.market_baseline("NHL", {"total": 6.5})
    assert tot["away"] is None and tot["total"] == 6.5
    assert sim.market_baseline("NFL", {}) is None


def test_market_diagnostic_rows_and_uncertainty():
    r = sim.simulate(_fb_spec(market={"spread_home": -3.0, "total": 44.5, "home_ml": -160, "away_ml": 135,
                                      "over_price": -110, "under_price": -110, "home_spread_price": -110, "away_spread_price": -110}), **FAST)
    rows = {x["label"]: x for x in r["market_diagnostic"]["rows"]}
    assert rows["H win"]["market"] is not None and abs(rows["H win"]["diff"] - (rows["H win"]["sim"] - rows["H win"]["market"])) < 1e-3
    assert rows["Projected total"]["market"] == 44.5 and rows["Over 44.5"]["market"] == 0.5
    u = r["uncertainty"]["simulation"]
    for k in ("total", "margin"):
        assert u[k]["ci95"][0] < u[k]["mean"] < u[k]["ci95"][1] and u[k]["se"] > 0
    for k in ("home_win", "over", "home_cover"):
        assert 0 < u[k]["se"] < 0.05
    assert r["uncertainty"]["model_data"]["label"] == "Model / data uncertainty"


def test_total_only_anchor_keeps_raw_split():
    r = sim.simulate(_nhl_spec(market={"total": 7.5}), **FAST)
    au = r["anchor_audit"]
    assert au["market_implied"]["away"] is None and "split kept from the raw simulation" in au["market_implied"]["basis"]
    assert abs(r["score"]["total"] - (au["raw"]["total"] + 7.5) / 2) < 0.35


def test_backward_compatible_fields():
    r = sim.simulate(_nhl_spec(market={"total": 6.5, "home_ml": -130, "away_ml": 110}), **FAST)
    for k in ("away", "home", "total", "total_median", "total_p10_p90", "home_win", "ot_rate", "raw", "anchor", "over", "push"):
        assert k in r["score"]
    for side in ("away", "home"):
        b = r["box"][side]
        assert len(b["rows"]) == 5 and all(len(x["cells"]) == len(b["cols"]) for x in b["rows"])
    assert r["team"]["rows"] and r["inputs"]["rows"] and r["runs"]["batches"] >= 1


# --------------------------------------------------------------------------- review fixes
def test_walk_off_counts_only_the_winning_run_without_a_homer():
    spec = _mlb_spec()
    for side in ("away", "home"):
        spec[side]["starter"]["hr_per_bf"] = 0.0
        spec[side]["bullpen"]["hr_per_bf"] = 0.0
        for b in spec[side]["lineup"]:
            b["hr"] = 0
    rows = _games(sim.mlb_factory(spec), 3000, seed=21)
    xh = [r for r in rows if r["ot"] and r["margin"] > 0]
    assert xh and all(r["margin"] == 1 for r in xh)


def test_goalie_saves_distribution_only_counts_games_he_starts():
    away = _nhl_team("A")
    away["goalie"].update(status="projected", alt={"name": "GA2", "sv_pct": 0.880, "shots_against": 100})
    r = sim.simulate(_nhl_spec(away=away, market={"total": 6.5}), **FAST)
    g = next(p for p in r["players"]["away"] if p["kind"] == "goalie")
    assert 0.7 < g["start_freq"] < 0.9
    assert g["stats"]["saves"]["p10"] > 10  # no zero-save games from nights the backup started


def test_goalie_sv_without_shots_against_is_not_thrown_away():
    assert sim.goalie_sv({"sv_pct": 0.930}) > 0.905
    spec = _nhl_spec(market={"total": 6.5})
    spec["home"]["goalie"].pop("shots_against")
    q, _ = sim.assess_quality(spec)
    assert any(f["field"] == "H.goalie.shots_against" for f in q.flags)
