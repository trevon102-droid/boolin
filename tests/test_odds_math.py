"""Canonical odds math (pipeline/oddsmath.py): edge cases, shared use, and board-output compatibility."""
import json
import math
from pathlib import Path

import pytest

from pipeline import common as C, odds, oddsmath as M
from research import markets

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "summarize_golden.json").read_text())


# ---------- implied / devig ----------

def test_both_sides_minus_110_is_50_50():
    d = M.devig([-110, -110])
    assert d == pytest.approx([0.5, 0.5]) and sum(d) == pytest.approx(1)


def test_different_juice_each_side_sums_to_one_and_keeps_order():
    d = M.devig([-130, 110])
    assert sum(d) == pytest.approx(1) and d[0] > d[1]
    assert d[0] == pytest.approx(M.implied(-130) / (M.implied(-130) + M.implied(110)))


def test_plus_money_both_sides_is_still_normalized():
    d = M.devig([105, 105])  # negative overround (sum of implied < 1): still normalizes to 50/50
    assert d == pytest.approx([0.5, 0.5])


def test_three_way_market_normalizes_all_outcomes_together():
    d = M.devig([150, 250, 200])
    assert sum(d) == pytest.approx(1) and d[0] > d[2] > d[1]


@pytest.mark.parametrize("prices", [[-110, None], [None, None], [-110], [], None, [-110, "abc"], [-110, float("nan")],
                                    [-110, float("inf")], [-110, True], [-50, 130], [0, -110]])
def test_incomplete_or_invalid_markets_return_none(prices):
    assert M.devig(prices) is None


def test_invalid_american_prices():
    for bad in (None, 0, 50, -99, 99.5, "x", float("nan"), True):
        assert M.implied(bad) is None and M.decimal(bad) is None
    assert M.implied(-100) == pytest.approx(0.5) and M.implied(100) == pytest.approx(0.5)
    assert M.implied("-150") == pytest.approx(0.6)  # numeric strings were accepted before; still are


def test_roundtrips_and_decimal():
    assert M.to_american(0.6) == -150 and M.to_american(0.4) == 150 and M.to_american(0.5) == -100
    assert M.to_american(0) is None and M.to_american(1) is None
    assert M.decimal(-200) == pytest.approx(1.5) and M.decimal(150) == pytest.approx(2.5)


def test_overround():
    assert M.overround([-110, -110]) == pytest.approx(0.0476, abs=1e-4)
    assert M.overround([-110, None]) is None


def test_one_implementation_everywhere():
    assert C.devig is M.devig and C.implied is M.implied and C.to_american is M.to_american
    assert markets.devig is M.devig and markets.implied is M.implied and markets.decimal is M.decimal


# ---------- fair_from_books ----------

def test_pinnacle_is_reference_when_complete():
    r = M.fair_from_books({"pinnacle": {"A": -110, "B": -110}, "fanduel": {"A": -300, "B": 250}}, ["A", "B"])
    assert r["ref"] == "pinnacle" and r["books"] == ["pinnacle"] and r["fair"] == pytest.approx([0.5, 0.5])
    assert r["hold"] == pytest.approx(0.0476, abs=1e-4)


def test_consensus_averages_per_book_no_vig_not_raw_prices():
    bp = {"fanduel": {"O": -115, "U": -105}, "draftkings": {"O": -110, "U": -110}, "betmgm": {"O": -120, "U": 100}}
    r = M.fair_from_books(bp, ["O", "U"])
    each = [M.devig([p["O"], p["U"]]) for p in bp.values()]
    assert r["ref"] == "consensus (3 books)" and r["books"] == ["betmgm", "draftkings", "fanduel"]
    assert r["fair"][0] == pytest.approx(sum(e[0] for e in each) / 3) and sum(r["fair"]) == pytest.approx(1)


def test_book_missing_a_side_never_contributes():
    r = M.fair_from_books({"fanduel": {"A": -200}, "draftkings": {"A": -190, "B": 160}}, ["A", "B"])
    assert r["books"] == ["draftkings"]
    r = M.fair_from_books({"pinnacle": {"A": -200}, "fanduel": {"A": -190, "B": 160}}, ["A", "B"])
    assert r["ref"] == "consensus (1 books)" and r["books"] == ["fanduel"]  # incomplete Pinnacle is not the reference


def test_no_complete_market_gives_no_fair_line():
    assert M.fair_from_books({"fanduel": {"A": -200}}, ["A", "B"])["fair"] is None
    assert M.fair_from_books({}, ["A", "B"]) == {"ref": None, "fair": None, "books": [], "hold": None}


def test_invalid_price_drops_that_book_instead_of_crashing_or_poisoning():
    # Before: a Pinnacle price of -50 raised TypeError inside _summarize. Now Pinnacle is dropped.
    r = M.fair_from_books({"pinnacle": {"A": -50, "B": 130}, "fanduel": {"A": -190, "B": 160}}, ["A", "B"])
    assert r["ref"] == "consensus (1 books)" and r["books"] == ["fanduel"] and r["hold"] is None
    assert sum(r["fair"]) == pytest.approx(1)


def test_books_that_strongly_disagree_are_averaged_not_hidden():
    bp = {"fanduel": {"A": -300, "B": 250}, "draftkings": {"A": 150, "B": -170}, "betmgm": {"A": -110, "B": -110}}
    r = M.fair_from_books(bp, ["A", "B"])
    assert r["ref"] == "consensus (3 books)" and 0.2 < r["fair"][0] < 0.8  # caller can see 3 books in `books`
    spread = max(M.devig([p["A"], p["B"]])[0] for p in bp.values()) - min(M.devig([p["A"], p["B"]])[0] for p in bp.values())
    assert spread > 0.3  # the disagreement is real, and detectable from the contributing books


def test_every_complete_book_sums_to_one():
    for case in GOLDEN["cases"].values():
        for p in case["book_prices"].values():
            vals = [p[n] for n in case["names"] if n in p]
            if len(vals) == len(case["names"]) and (d := M.devig(vals)):
                assert math.isclose(sum(d), 1.0, abs_tol=1e-12)


# ---------- backward compatibility: _summarize output is unchanged ----------

@pytest.mark.parametrize("name", sorted(GOLDEN["cases"]))
def test_summarize_matches_pre_refactor_output(name):
    case = GOLDEN["cases"][name]
    got = odds._summarize(case["book_prices"], case["names"])
    expected = GOLDEN["expected"][name]
    if expected.get("old_code_crashed"):
        # The old code raised TypeError here; the new defined behavior is "drop the bad book".
        assert got["ref"] == "consensus (1 books)" and got["pinnacle_hold"] is None
        # FanDuel -190/+160 de-vigged: 0.6552/0.3846 normalized -> 0.6301/0.3699
        assert [o["fair_prob"] for o in got["outcomes"]] == pytest.approx([0.6301, 0.3699], abs=1e-4)
        return
    assert json.loads(json.dumps(got)) == expected
