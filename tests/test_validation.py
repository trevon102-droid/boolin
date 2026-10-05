"""Offline tests for model validation, the conclusion gate and the MLB starter/bullpen split."""
import datetime as dt
import gzip
import json
import math
import tempfile
from pathlib import Path

import pandas as pd

from pipeline import mlb as MLBP, nfl_replay as NR
from research import card as C, flags, models, render, summary, validate as VA, vmetrics as V

ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------ metrics

def test_brier_log_loss_accuracy():
    assert abs(V.brier(0.7, 1) - 0.09) < 1e-12 and abs(V.brier(0.7, 0) - 0.49) < 1e-12
    assert abs(V.log_loss(0.7, 1) + math.log(0.7)) < 1e-12
    assert abs(V.log_loss(0.5, 0.5) - math.log(2)) < 1e-12           # tie = half a win
    assert V.log_loss(1.0, 0) < 20                                      # clipped, never inf
    assert V.accuracy([(0.7, 1), (0.6, 0), (0.5, 1), (0.8, 0.5)]) == 0.5  # 50% calls and ties left out


def test_calibration_buckets_fold_to_the_favourite():
    pairs = [(0.62, 1), (0.38, 0), (0.61, 0), (0.30, 1), (0.85, 1)]
    b = {x["bucket"]: x for x in V.buckets(pairs)}
    # 0.38 home -> away is a 62% favourite that won; 0.30 home -> away 70% favourite that lost
    assert b["60-65%"]["n"] == 3 and abs(b["60-65%"]["actual_win_rate"] - 2 / 3) < 1e-3
    assert b["70-75%"]["n"] == 1 and b["70-75%"]["actual_win_rate"] == 0.0
    assert b["80%+"]["n"] == 1 and b["50-55%"]["n"] == 0
    ece = V.calibration_error(pairs)
    expect = (3 * abs((0.62 + 0.62 + 0.61) / 3 - 2 / 3) + 1 * 0.70 + 1 * abs(0.85 - 1)) / 5
    assert abs(ece - expect) < 1e-3
    assert V.favorite_overconfidence(pairs) > 0


def test_paired_comparison_needs_evidence():
    assert "too few" in V.paired([0.1] * 10, [0.2] * 10)["verdict"]
    a = [0.20 + 0.01 * (i % 3) for i in range(100)]
    b = [x + 0.03 + 0.01 * (i % 2) for i, x in enumerate(a)]
    assert V.paired(a, b)["verdict"].startswith("Boolin Brier better")
    assert V.paired(b, a)["verdict"].startswith("market Brier better")
    noisy = V.paired([0.2, 0.3] * 50, [0.3, 0.2] * 50)
    assert "no detectable difference" in noisy["verdict"]


# ------------------------------------------------------------------ rows / point in time

def _card(key, league="NFL", start="2026-10-04T13:00-04:00", built="2026-10-04T10:00-04:00", mt=None, p=0.6, mp=0.5,
          conf="medium", weights=None, spread=-3.0, total=44.5, warnings=None, injuries=None):
    mt = mt or built
    return {"key": key, "league": league, "status": "scheduled", "start_et": start, "built_at": built,
            "game": {"away": {"abbr": "AAA"}, "home": {"abbr": "HHH"}},
            "model": {"available": True, "version": models.VERSION, "home_win_p": p, "proj_margin_home": 3.0,
                      "proj_total": 45.0, "confidence": conf, "regression_weights": weights or {"home": 0.5, "away": 0.6},
                      "sample_warnings": warnings or []},
            "market": {"moneyline": {"current": {"t": mt, "fair_home": mp, "ref": "pinnacle"}},
                       "spread": {"current": {"t": mt, "home_line": spread}},
                       "total": {"current": {"t": mt, "line": total}}},
            "availability": {"starters": {"home": {"kind": "projected"}, "away": {"kind": "projected"}},
                             "injuries": injuries or []},
            "flags": [], "situations": {}}


def _write_archive(root: Path, date: str, cards: dict, results: dict):
    (root / "archive").mkdir(parents=True, exist_ok=True)
    (root / "results").mkdir(parents=True, exist_ok=True)
    with gzip.open(root / "archive" / f"{date}.json.gz", "wt") as f:
        json.dump(cards, f)
    (root / "results" / f"{date}.json").write_text(json.dumps(results))


def test_point_in_time_exclusions_are_recorded():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        cards = {"ok": _card("ok"),
                 "late": _card("late", built="2026-10-04T13:05-04:00"),
                 "nores": _card("nores"),
                 "postmkt": _card("postmkt", mt="2026-10-04T13:30-04:00"),
                 "after_build": _card("after_build", mt="2026-10-04T11:00-04:00"),
                 "cfb": {**_card("cfb"), "league": "CFB"}}
        res = {k: {"home_margin": 7, "total": 41, "source": "ESPN scoreboard"} for k in cards if k != "nores"}
        _write_archive(root, "2026-10-04", cards, res)
        rows, ex = VA.live_rows(root)
        keys = {r["key"]: r for r in rows}
        reasons = {e["key"]: e["reason"] for e in ex}
        assert set(keys) == {"ok", "postmkt", "after_build"}
        assert "after the start" in reasons["late"] or "at/after the start" in reasons["late"]
        assert "final score" in reasons["nores"] and "no Boolin model" in reasons["cfb"]
        # a market observation after the start (or after the model build) is dropped, not used
        assert keys["postmkt"]["market_home_p"] is None and any("after start" in n for n in keys["postmkt"]["pit_notes"])
        assert keys["after_build"]["market_home_p"] is None
        ok = keys["ok"]
        assert ok["point_in_time_safe"] and ok["model_built_at"] < ok["game_start"]
        assert ok["actual_home_win"] == 1.0 and ok["provenance"]["card"].endswith("#ok")
        assert ok["model_version"] == "baseline-0.1" and ok["split"] == "development"


def test_chronological_split_and_order():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _write_archive(root, "2026-10-04", {"a": _card("a")}, {"a": {"home_margin": 3, "total": 40}})
        _write_archive(root, "2026-10-12", {"b": _card("b", start="2026-10-12T13:00-04:00", built="2026-10-12T09:00-04:00")},
                       {"b": {"home_margin": -3, "total": 40}})
        rows, _ = VA.live_rows(root)
        assert {r["key"]: r["split"] for r in rows} == {"a": "development", "b": "holdout"}
        out = VA.run(root, root / "none.json.gz", out=root / "v")
        lines = [json.loads(x) for x in (root / "v" / "games.jsonl").read_text().splitlines()]
        assert [x["key"] for x in lines] == ["a", "b"]                        # chronological, never shuffled
        assert out["rows"] == 2
        for name in ("summary", "calibration", "market_comparison", "spread_validation", "total_validation",
                     "confidence_validation", "disagreement_validation", "failure_modes", "model_scorecard"):
            body = json.loads((root / "v" / f"{name}.json").read_text())
            assert body["validation_version"] == VA.VALIDATION_VERSION and body["model_version"] == "baseline-0.1"
            assert "calibration_status" in body


def test_sample_groups():
    assert VA.group_of("NFL", 1) == "1 game" and VA.group_of("NFL", 5) == "4-5 games"
    assert VA.group_of("NFL", 13) == "13+ games" and VA.group_of("NHL", 0) == "0 GP this season"
    # NFL games recovered from the regression weight n/(n+4)
    s = VA.live_sample("NFL", {"regression_weights": {"home": 0.5, "away": 3 / 7}})
    assert s["value"] == 3 and s["group"] == "3 games" and not s["small"]


def _row(i, p, mp, y, conf="medium", league="NFL", margin=3.0, spread=-3.0, small=False, split="holdout", ctx=None):
    return {"key": f"k{i}", "source": "nfl_replay", "league": league, "date": f"2024-10-{(i % 28) + 1:02d}",
            "model_home_p": p, "market_home_p": mp, "actual_home_win": y, "actual_margin": 7.0 if y == 1 else -7.0,
            "actual_total": 44, "model_margin": margin, "model_total": 45, "market_spread_home": spread, "market_total": 44.5,
            "model_confidence": conf, "sample": {"value": 2 if small else 8, "group": "2 games" if small else "6-8 games",
                                                 "small": small, "metric": "x"},
            "context": ctx or {}, "data_quality": {"state": "clean"}, "split": split, "model_version": "baseline-0.1"}


def test_extreme_disagreement_grouping():
    r = _row(1, 0.62, 0.48, 1)
    assert VA.extreme_reasons(r) == ["win probability +14.0 pp"]
    r2 = _row(2, 0.52, 0.50, 1, margin=1.0, spread=6.0)   # Boolin HHH -1, market HHH +6 -> 7 pts
    assert VA.extreme_reasons(r2) == ["spread -7.0 pts"]
    assert VA.extreme_reasons({**r2, "league": "NHL"}) == []    # puck line is not a margin estimate
    rows = [_row(i, 0.65, 0.45, 1 if i % 2 else 0, conf="low" if i < 20 else "medium", small=i < 20) for i in range(60)]
    a = VA.extreme_audit(rows)
    g = {x["group"]: x for x in a["groups"]}
    assert g["all extreme disagreements"]["n"] == 60 and g["low confidence"]["n"] == 20 and g["small samples"]["n"] == 20
    assert g["all extreme disagreements"]["boolin_side_market_implied"] == 0.45
    assert {h["hypothesis"] for h in a["hypotheses"]} >= {"useful contrarian signal", "systematic model bias",
                                                           "early-season / small-sample instability"}


def test_confidence_labels_reported_plainly_when_they_fail():
    rows = ([_row(i, 0.8, 0.6, 0 if i % 2 else 1, conf="high") for i in range(40)] +
            [_row(100 + i, 0.6, 0.6, 1 if i % 5 else 0, conf="medium") for i in range(40)])
    cv = VA.confidence_validation(rows)
    assert cv["validated"] is False and "NOT line up" in cv["verdict"]


def test_scorecard_not_ready_without_holdout_and_status_default():
    rows = [_row(i, 0.6, 0.55, 1, split="development") for i in range(400)] + [_row(1000 + i, 0.6, 0.55, 1) for i in range(20)]
    sc = VA.scorecard_entry("NFL", rows, "test", {})
    assert sc["recommendation"] == "NOT READY" and sc["validation_status"] == "insufficient sample"
    assert sc["lean_allowed"] is False and sc["sample_size"] == 20 and "don't know yet" in sc["reasons"][0]
    st = VA.status_for({}, "NHL")
    assert st["recommendation"] == "NOT READY" and st["lean_allowed"] is False
    # a big, well-calibrated holdout that is worse than the market is never "outperforming"
    good = [_row(i, 0.7, 0.75, 1 if i % 10 < 7 else 0) for i in range(500)]
    sc2 = VA.scorecard_entry("NFL", good, "test", {})
    assert sc2["recommendation"] != "OUTPERFORMING MARKET IN VALIDATED SAMPLE"


# ------------------------------------------------------------------ the gate

def _gcard(overall, conf="medium", lean_ok=False, warnings=None, injuries=None, diff=26.6):
    rows = [{"dimension": "Win probability (home)", "difference": diff, "label": overall, "boolin": 0.536, "market": 0.271}]
    return {"league": "NFL", "game": {"home": {"abbr": "TEN"}, "away": {"abbr": "HOU"}, "context": {}},
            "market": {}, "environment": {}, "flags": [],
            "availability": {"injuries": injuries or []},
            "model": {"available": True, "confidence": conf, "sample_warnings": warnings or [],
                      "contributions": [{"name": "Offense", "margin": 3.0, "note": "n"},
                                        {"name": "Defense", "margin": 2.0, "note": "n"}]},
            "comparison": {"available": True, "overall": overall, "rows": rows, "model_confidence": conf},
            "validation": {"lean_allowed": lean_ok, "calibration_status": "uncalibrated (insufficient sample)",
                           "validation_status": "insufficient sample", "recommendation": "NOT READY",
                           "model_version": "baseline-0.1"}}


def test_uncalibrated_model_cannot_lean_from_extreme_disagreement():
    """The HOU @ TEN case: +26.6 pp, medium confidence, used to come out LEAN TEN."""
    c = summary.build(_gcard("extreme disagreement"))["conclusion"]
    assert c["status"] == "review required" and c["gate"]["ungated_status"] == "lean"
    assert "Extreme model/market disagreement. Model is currently uncalibrated." in c["detail"]
    assert "not evidence of edge" in c["detail"]


def test_gate_rules():
    # aligned + uncalibrated -> pass is fine
    assert summary.build(_gcard("aligned", diff=1.0))["conclusion"]["status"] == "pass"
    # mild + low confidence -> watch
    assert summary.build(_gcard("mild disagreement", conf="low", diff=3.0))["conclusion"]["status"] == "watch"
    # significant + uncalibrated -> never lean
    sig = summary.build(_gcard("significant disagreement", diff=7.0))["conclusion"]
    assert sig["status"] == "watch" and sig["gate"]["applied"]
    # validated league: extreme + small sample -> review required
    small = summary.build(_gcard("extreme disagreement", lean_ok=True,
                                 warnings=["Small sample: home team EPA from 1 game(s); regressed hard toward average."]))
    assert small["conclusion"]["status"] == "review required"
    assert any("small sample" in r for r in small["conclusion"]["gate"]["reasons"])
    # validated league: extreme + unresolved key player -> review required
    inj = [{"player": "QB1", "team": "TEN", "pos": "QB", "status": "Questionable", "status_norm": "questionable",
            "key_player": True}]
    k = summary.build(_gcard("extreme disagreement", lean_ok=True, injuries=inj))["conclusion"]
    assert k["status"] == "review required" and any("key player" in r for r in k["gate"]["reasons"])
    # validated league, clean inputs: a lean is possible again
    assert summary.build(_gcard("extreme disagreement", lean_ok=True))["conclusion"]["status"] == "lean"


# ------------------------------------------------------------------ MLB starter / bullpen

def _team_box(pitchers, started):
    return {"pitchers": pitchers,
            "players": {f"ID{p}": {"person": {"fullName": f"P{p}"},
                                   "stats": {"pitching": {"gamesStarted": 1 if p in started else 0, "numberOfPitches": 20}}}
                        for p in pitchers}}


def test_mlb_starter_from_credited_start_not_list_position():
    split = MLBP.classify_pitchers(_team_box([11, 22, 33], started={22}))   # starter listed second
    assert split["status"] == "confirmed" and split["starter"] == 22 and split["relievers"] == [11, 33]
    none = MLBP.classify_pitchers(_team_box([11, 22], started=set()))
    assert none["status"] == "unknown" and none["starter"] is None and none["relievers"] == []
    two = MLBP.classify_pitchers(_team_box([11, 22], started={11, 22}))
    assert two["status"] == "unknown" and "2 pitchers" in two["reason"]


def test_mlb_bullpen_skips_unclassified_games():
    day = dt.date(2026, 9, 20)

    def fetch(url, params=None):
        if url.endswith("/schedule"):
            mk = lambda pk, d: {"gamePk": pk, "status": {"abstractGameState": "Final"},  # noqa: E731
                                "teams": {"home": {"team": {"id": 1}}, "away": {"team": {"id": 2}}}}
            return {"dates": [{"date": "2026-09-19", "games": [mk(1, "2026-09-19")]},
                              {"date": "2026-09-18", "games": [mk(2, "2026-09-18")]}]}
        pk = int(url.split("/game/")[1].split("/")[0])
        box = _team_box([5, 6, 7], {5}) if pk == 1 else _team_box([5, 6, 7], set())
        return {"teams": {"home": box, "away": box}}
    bp = MLBP.bullpen(1, day, fetch=fetch)
    assert bp["classification"] == "partial" and bp["games_classified"] == 1 and bp["games_considered"] == 2
    assert bp["reliever_pitches_last3"] == 40 and len(bp["unclassified_games"]) == 1   # only game 1's relievers


def test_bullpen_uncertainty_downgrades_the_model_input():
    from tests.test_research import raw_fixture
    raw = raw_fixture()
    raw["mlb"]["games"][0]["home"]["bullpen"] = {"reliever_pitches_last3": 150, "likely_limited": ["X"],
                                                 "classification": "partial", "unclassified_games": [{"game_pk": 1}]}
    g = next(x for x in C.enumerate_games(raw, "2026-10-05") if x["league"] == "MLB")
    sec = C.mlb_section(raw, g)
    assert sec["model_input"]["home"]["bullpen"]["tired"] is None
    assert sec["research"]["home"]["bullpen"]["pitches_last3"] is None
    assert sec["research"]["home"]["bullpen"]["classification"] == "partial"
    m = models.mlb(sec["model_input"])
    assert any("bullpen workload unknown" in w for w in m["sample_warnings"])
    # legacy files without a classification were built from list order: treated as unknown too
    raw["mlb"]["games"][0]["home"]["bullpen"] = {"reliever_pitches_last3": 150, "likely_limited": []}
    assert C.mlb_section(raw, g)["model_input"]["home"]["bullpen"]["tired"] is None
    card = {"league": "MLB", "game": {"home": {"abbr": "TB"}, "away": {"abbr": "NYY"}},
            "research": {"home": sec["research"]["home"], "away": {}}}
    fl = flags.build({**card, "market": {}, "comparison": {}, "freshness": {}, "availability": {}, "model": {},
                      "environment": {}})
    assert any(f["id"] == "data.bullpen_unclassified_home" for f in fl)


# ------------------------------------------------------------------ NFL replay

def test_replay_uses_only_prior_weeks():
    pbp = pd.DataFrame([
        {"season_type": "REG", "week": 1, "posteam": "AAA", "defteam": "BBB", "epa": 0.5, "pass": 1, "rush": 0},
        {"season_type": "REG", "week": 1, "posteam": "BBB", "defteam": "AAA", "epa": -0.1, "pass": 0, "rush": 1},
        {"season_type": "REG", "week": 2, "posteam": "AAA", "defteam": "CCC", "epa": -0.9, "pass": 1, "rush": 0},
        {"season_type": "REG", "week": 2, "posteam": "CCC", "defteam": "AAA", "epa": 0.3, "pass": 1, "rush": 0},
        {"season_type": "PRE", "week": 1, "posteam": "AAA", "defteam": "BBB", "epa": 9.0, "pass": 1, "rush": 0},
        {"season_type": "REG", "week": 1, "posteam": "AAA", "defteam": "BBB", "epa": None, "pass": 1, "rush": 0},
    ])
    t = NR.team_week_table(pbp)
    w2 = NR.inputs_before(t, "AAA", 2)
    assert w2 == {"off_epa": 0.5, "def_epa": -0.1, "games": 1, "weeks_used": [1], "plays": 1}
    w3 = NR.inputs_before(t, "AAA", 3)
    assert w3["games"] == 2 and w3["off_epa"] == -0.2 and w3["weeks_used"] == [1, 2]
    assert NR.inputs_before(t, "AAA", 1) is None                       # nothing before week 1
    assert NR.inputs_before(t, "OAK", 2) is None and NR._fr("OAK") == "LV"


def test_replay_rows_end_to_end():
    games = pd.DataFrame([
        {"season": 2024, "week": 1, "game_id": "g1", "game_type": "REG", "gameday": "2024-09-08", "gametime": "13:00",
         "away_team": "AAA", "home_team": "BBB", "result": -4, "total": 40, "spread_line": 1.5, "total_line": 42.5,
         "home_moneyline": -120, "away_moneyline": 100, "home_rest": 7, "away_rest": 7, "roof": "outdoors",
         "location": "Home", "div_game": 0, "home_qb_name": "Q1", "away_qb_name": "Q2"},
        {"season": 2024, "week": 2, "game_id": "g2", "game_type": "REG", "gameday": "2024-09-15", "gametime": "16:25",
         "away_team": "BBB", "home_team": "AAA", "result": 10, "total": 44, "spread_line": 3.0, "total_line": 44.0,
         "home_moneyline": -160, "away_moneyline": 140, "home_rest": 7, "away_rest": 7, "roof": "dome",
         "location": "Home", "div_game": 1, "home_qb_name": "Q2", "away_qb_name": "Q9"},
    ])
    pbp = pd.DataFrame([
        {"season_type": "REG", "week": 1, "posteam": "AAA", "defteam": "BBB", "epa": 0.2, "pass": 1, "rush": 0},
        {"season_type": "REG", "week": 1, "posteam": "BBB", "defteam": "AAA", "epa": -0.1, "pass": 1, "rush": 0},
        {"season_type": "REG", "week": 2, "posteam": "AAA", "defteam": "BBB", "epa": 5.0, "pass": 1, "rush": 0},
        {"season_type": "REG", "week": 2, "posteam": "BBB", "defteam": "AAA", "epa": 5.0, "pass": 1, "rush": 0},
    ])
    rows = NR.season_rows(games, pbp, 2024)
    assert rows[0]["inputs"]["home"] is None                            # week 1: no prior plays
    g2 = rows[1]
    assert g2["inputs"]["home"]["off_epa"] == 0.2 and g2["inputs"]["home"]["weeks_used"] == [1]   # week-2 plays unused
    assert g2["qb_prev_game"]["away"] == "Q1" and g2["qb_at_kickoff"]["away"] == "Q9"
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "replay.json.gz"
        with gzip.open(p, "wt") as f:
            json.dump({"version": NR.VERSION, "seasons": {"2024": rows}}, f)
        vr, ex, meta = VA.replay_rows(p)
        assert len(vr) == 1 and len(ex) == 1 and "no current-season plays" in ex[0]["reason"]
        r = vr[0]
        assert r["split"] == "holdout" and r["context"]["qb_change"] and r["sample"]["group"] == "1 game"
        assert abs(r["market_home_p"] - (160 / 260) / (160 / 260 + 100 / 240)) < 1e-3
        assert r["market_spread_home"] == -3.0 and r["market_kind"].startswith("closing")
        assert r["model_home_p"] == models.nfl({"home": {"off_epa": 0.2, "def_epa": -0.1, "games": 1, "rest": 7},
                                                "away": {"off_epa": -0.1, "def_epa": 0.2, "games": 1, "rest": 7},
                                                "outdoors": False, "wind_mph": None, "neutral": False})["home_win_p"]


def test_shrinkage_diagnostic_leaves_parameters_alone():
    before = dict(models.NFL)
    g = {"game_id": "g", "roof": "dome", "home_rest": 7, "away_rest": 7,
         "inputs": {"home": {"off_epa": 0.2, "def_epa": -0.1, "games": 1}, "away": {"off_epa": -0.1, "def_epa": 0.1, "games": 1}}}
    r = {"provenance": {"game_id": "g"}, "actual_home_win": 1.0, "context": {"early_season": True}, "market_home_p": 0.6}
    out = VA.shrinkage_diagnostic([r], {"g": g})
    assert models.NFL == before and [x["k_games"] for x in out["table"]] == [2, 4, 6, 8, 12, 16]
    assert "Diagnostic only" in out["reading"]


def test_board_page_carries_the_current_research_module():
    html = (ROOT / render.BOARD).read_text()
    assert render.sync_board(html) == html, "run: python -m research.render --sync-board"
