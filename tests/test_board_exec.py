"""Board execution layer (EXEC in board/rackz-sharp-board.html), run in node: research conviction vs bet
permission, line-aware CLV, SGP method labels and the market-anchor probability label."""
import json
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "board" / "rackz-sharp-board.html"
NOW = "2026-10-05T16:00:00Z"


def _exec_src() -> str:
    m = re.search(r"/\*EXEC_JS_BEGIN\*/(.*?)/\*EXEC_JS_END\*/", BOARD.read_text(), re.S)
    assert m, "EXEC block markers missing from the board page"
    return m.group(1)


def run(js_expr: str):
    """Evaluate a JS expression with EXEC and `now` in scope; return its JSON value."""
    node = shutil.which("node")
    assert node, "node is required for the board execution tests (preinstalled on GitHub runners)"
    prog = _exec_src() + f"\nconst now=Date.parse({json.dumps(NOW)});\nprocess.stdout.write(JSON.stringify({js_expr}));"
    out = subprocess.run([node, "-e", prog], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def status(pick: dict, stale_min=1440):
    return run(f"EXEC.betStatus({json.dumps(pick)}, {{now, staleMin:{stale_min}}})")


FRESH = "2026-10-05T15:30:00Z"


# 1-3: price vs betTo
def test_price_exactly_at_bet_to_is_bettable():
    s = status({"pick": "Under 38.5", "odds": -125, "priceAt": FRESH, "betTo": "38.5 to -125"})
    assert s["bet_status"] == "BET" and s["price_status"] == "fresh"


def test_price_better_than_bet_to_is_bettable():
    assert status({"pick": "Under 38.5", "odds": -110, "priceAt": FRESH, "betTo": "38.5 to -125"})["bet_status"] == "BET"
    assert status({"pick": "Bills ML", "odds": 140, "priceAt": FRESH, "betTo": "+130 or better"})["bet_status"] == "BET"


def test_price_worse_than_bet_to_is_not_bettable():
    s = status({"pick": "Under 38.5", "odds": -130, "priceAt": FRESH, "betTo": "38.5 to -125"})
    assert s["bet_status"] == "NOT BETTABLE AT CURRENT PRICE" and "-130" in s["reason"]
    # worse number at a fine price is still not bettable (no line/price conversion is assumed)
    s = status({"pick": "Under 37.5", "odds": -105, "priceAt": FRESH, "betTo": "38.5 to -125"})
    assert s["bet_status"] == "NOT BETTABLE AT CURRENT PRICE" and "Line" in s["reason"]
    # better number for an Over (lower) is fine
    assert status({"pick": "Over 37.5", "odds": -120, "priceAt": FRESH, "betTo": "38.5 to -125"})["bet_status"] == "BET"
    # spreads: -3 is worse than a -2.5 bet-to
    assert status({"pick": "Chiefs -3", "odds": -110, "priceAt": FRESH, "betTo": "-2.5 to -120"})["bet_status"] == \
        "NOT BETTABLE AT CURRENT PRICE"


# 4-5, 14: stale / missing -> wait, never a bet
def test_stale_price_waits():
    s = status({"pick": "Under 38.5", "odds": -110, "priceAt": "2026-10-03T12:00:00Z", "betTo": "38.5 to -125"})
    assert s["bet_status"] == "WAIT / RECHECK" and s["price_status"] == "stale"


def test_missing_price_waits_and_has_no_permission():
    s = status({"pick": "Falcons ML", "betTo": "+130 or better"})
    assert s["bet_status"] == "WAIT / RECHECK" and s["price_status"] == "missing"
    s = status({"pick": "Falcons ML", "odds": 140, "betTo": "+130 or better"})          # no timestamp
    assert s["bet_status"] == "WAIT / RECHECK" and s["price_status"] == "no timestamp"
    s = status({"pick": "Falcons ML", "odds": 140, "priceAt": FRESH, "betTo": "price is juiced; wait"})
    assert s["bet_status"] == "WAIT / RECHECK"                                          # no machine-readable threshold
    s = status({"pick": "Falcons ML", "odds": 140, "priceAt": FRESH})
    assert s["bet_status"] == "WAIT / RECHECK"


# 6: research stays visible: status is computed, the play is not dropped, tier untouched
def test_research_play_stays_visible_when_not_bettable():
    pick = {"pick": "Saints -2.5", "odds": -135, "priceAt": FRESH, "betTo": "-2.5 to -120", "conviction": "A"}
    s = status(pick)
    assert s["bet_status"] == "NOT BETTABLE AT CURRENT PRICE"
    assert s["current"] == {"price": -135, "line": -2.5} and s["threshold"]["price"] == -120
    assert status({**pick, "pass": True})["bet_status"] == "PASS"
    html = BOARD.read_text()
    assert "Research size only, not a bet" in html and "researchCell(pk, ev, ex)" in html


def test_bet_to_parsing_formats():
    r = run("['38.5 to -125','-120 or better at 6.5','69.5 at -115','fine to -140','+150 or better','Not on FanDuel',"
            "'Over 250.5 to -115'].map(b=>EXEC.parseBetTo({betTo:b}))")
    assert [(x["line"], x["price"]) for x in r] == [(38.5, -125), (6.5, -120), (69.5, -115), (None, -140), (None, 150),
                                                     (None, None), (250.5, -115)]
    assert run("EXEC.parseBetTo({betTo:'junk', betToPrice:-110, betToLine:-3})") ["price"] == -110


# 7-10: line-aware CLV
def clv(b):
    return run(f"EXEC.clvFields({json.dumps(b)})")


def test_line_movement_clv_same_price():
    f = clv({"pick": "Chiefs -2.5", "sport": "NFL", "market_type": "spread", "taken_line": -2.5, "taken_price": -110,
             "close_line": -3, "close_price": -110})
    assert f["line_changed"] is True and f["line_delta"] == -0.5 and f["line_clv"] == 0.5
    assert f["price_clv"] is None and f["combined_clv"] is None and "unavailable" in f["clv_note"]


def test_same_line_price_clv():
    f = clv({"pick": "Under 38.5", "sport": "NFL", "market_type": "total", "taken_line": 38.5, "taken_price": -110,
             "close_line": 38.5, "close_price": -125})
    assert f["line_changed"] is False and f["line_clv"] == 0
    assert abs(f["price_clv"] - ((1 + 100 / 110) / (1 + 100 / 125) - 1)) < 1e-9 and f["combined_clv"] == f["price_clv"]
    ml = clv({"pick": "Bills ML", "market_type": "moneyline", "odds": -150, "close": -165})
    assert ml["price_clv"] > 0 and ml["combined_clv"] == ml["price_clv"] and ml["taken_line"] is None


def test_line_changed_between_take_and_close_over_under():
    over = clv({"pick": "Over 44.5", "market_type": "total", "taken_line": 44.5, "taken_price": -110, "close_line": 46,
                "close_price": -110})
    under = clv({"pick": "Under 44.5", "market_type": "total", "taken_line": 44.5, "taken_price": -110, "close_line": 46,
                 "close_price": -110})
    assert over["line_clv"] == 1.5 and under["line_clv"] == -1.5          # direction-aware
    assert over["price_clv"] is None and under["combined_clv"] is None


def test_key_numbers_three_and_seven():
    f = clv({"pick": "Chiefs -2.5", "sport": "NFL", "market_type": "spread", "taken_line": -2.5, "taken_price": -110,
             "close_line": -3.5, "close_price": -110})
    assert f["key_numbers_crossed"] == [3] and "Key number 3" in f["clv_note"]
    f = clv({"pick": "Jets +6.5", "sport": "NFL", "market_type": "spread", "taken_line": 6.5, "taken_price": -110,
             "close_line": 7.5, "close_price": -110})
    assert f["key_numbers_crossed"] == [7] and f["line_clv"] == -1.0
    f = clv({"pick": "Jets +4.5", "sport": "NFL", "market_type": "spread", "taken_line": 4.5, "taken_price": -110,
             "close_line": 5.5, "close_price": -110})
    assert f["key_numbers_crossed"] == []


def test_legacy_bet_records_stay_readable():
    old = clv({"pick": "Under 38.5", "odds": -110, "close": -120})                   # pre-schema-2 record
    assert old["price_clv"] is not None and old["taken_price"] == -110 and old["close_price"] == -120
    old_line = clv({"pick": "Under 38.5", "market_type": "total", "odds": -110, "close": -120})
    assert old_line["price_clv"] is not None and old_line["combined_clv"] is None and "not recorded" in old_line["clv_note"]


# 11-13: SGPs
def test_sgp_method_labels():
    r = run("[EXEC.sgpMethod({modelP:0.36}), EXEC.sgpMethod({}), EXEC.sgpMethod({modelP:0.3, joint_probability_method:'simulation'}),"
            "EXEC.sgpMethod({modelP:0.3, joint_probability_method:'validated'})]")
    assert r[0]["method"] == "analyst" and r[0]["label"] == "Analyst correlation estimate" and not r[0]["validated"]
    assert r[1]["method"] == "independent"
    assert r[2]["method"] == "simulation" and not r[2]["validated"]
    # no SGP validation exists in the repo: a "validated" claim is shown as an analyst estimate
    assert r[3]["method"] == "analyst" and r[3]["validated"] is False and "no SGP validation" in r[3]["note"]


def test_sgp_needs_a_current_book_price():
    s = run("EXEC.sgpStatus({modelP:0.36, legs:[]}, {now}, 192)")
    assert s["bet_status"] == "WAIT / RECHECK" and s["price_status"] == "missing"
    s = run("EXEC.sgpStatus({modelP:0.36, bookPrice:260}, {now}, 192)")
    assert s["bet_status"] == "WAIT / RECHECK" and s["price_status"] == "no timestamp"
    s = run(f"EXEC.sgpStatus({{modelP:0.36, bookPrice:260, bookPriceAt:{json.dumps(FRESH)}}}, {{now}}, 192)")
    assert s["bet_status"] == "BET"
    s = run(f"EXEC.sgpStatus({{modelP:0.36, bookPrice:170, bookPriceAt:{json.dumps(FRESH)}}}, {{now}}, 192)")
    assert s["bet_status"] == "NOT BETTABLE AT CURRENT PRICE"


def test_sgp_stale_price_waits():
    s = run("EXEC.sgpStatus({modelP:0.36, bookPrice:260, bookPriceAt:'2026-10-03T12:00:00Z'}, {now}, 192)")
    assert s["bet_status"] == "WAIT / RECHECK" and s["price_status"] == "stale"


# 15-16: market anchor and labels
def test_market_prior_plus_analyst_adjustment():
    a = run("EXEC.anchor({modelP:0.57, prior:0.52, priorLabel:'Pinnacle no-vig'})")
    assert a == {"market_prior": 0.52, "research_p": 0.57, "analyst_adjustment": 0.05, "prior_source": "Pinnacle no-vig"}
    a = run("EXEC.anchor({modelP:0.57}, 0.55)")
    assert a["market_prior"] == 0.55 and a["analyst_adjustment"] == 0.02 and a["prior_source"] == "no-vig sharp price"
    html = BOARD.read_text()
    assert "Market prior" in html and "analyst adj" in html


def test_uncalibrated_probability_never_gets_a_validated_label():
    r = run("[EXEC.probLabel(null), EXEC.probLabel({recommendation:'CALIBRATION NEEDED', lean_allowed:false}),"
            "EXEC.probLabel({recommendation:'CALIBRATED / RESEARCH READY', lean_allowed:'yes'}), EXEC.probLabel({lean_allowed:true})]")
    assert [x["validated"] for x in r] == [False, False, False, True]
    assert r[0]["label"] == "Research probability" and r[3]["label"] == "Model probability"


def test_board_page_parses():
    node = shutil.which("node")
    assert node
    html = BOARD.read_text()
    js = html.split("<script>", 1)[1].rsplit("</script>", 1)[0]
    out = subprocess.run([node, "-e", "new Function(require('fs').readFileSync(0,'utf8'))"], input=js,
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
