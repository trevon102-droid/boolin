"""Postgame grading: judge the research, not just win/loss.

Components (each graded only when the stored data can support it; otherwise 'not gradable' with
the reason):
  model      Boolin's pregame win probability vs the result (Brier score), side by side with the
             market's no-vig probability at close
  market     the analyst's position or thesis vs the closing line (CLV / line movement)
  thesis     the thesis side vs the result at the closing number
  weather    pregame wind vs the wind recorded after the game (NFL, when both exist)
  availability  not gradable yet: actual starters/inactives aren't stored
Overall = average of the gradable components.
"""
from __future__ import annotations

from . import markets

GRADES = ((3.85, "A"), (3.5, "A-"), (3.15, "B+"), (2.85, "B"), (2.5, "B-"), (2.15, "C+"), (1.85, "C"),
          (1.5, "C-"), (1.0, "D"), (-1, "F"))


def letter(score: float | None) -> str | None:
    if score is None:
        return None
    return next(g for cut, g in GRADES if score >= cut)


def _band(x: float, cuts: list[tuple[float, float]], floor: float = 0.0) -> float:
    for c, s in cuts:
        if x >= c:
            return s
    return floor


def brier(p: float, outcome: float) -> float:
    return round((p - outcome) ** 2, 4)


def outcome_home(result: dict) -> float | None:
    m = result.get("home_margin")
    if m is None:
        return None
    return 1.0 if m > 0 else 0.0 if m < 0 else 0.5


def grade_model(card: dict, result: dict) -> dict:
    m = card.get("model") or {}
    y = outcome_home(result)
    if not m.get("available") or y is None:
        return {"gradable": False, "reason": "no pregame projection" if not m.get("available") else "no result"}
    b = brier(m["home_win_p"], y)
    score = _band(-b, [(-0.10, 4.0), (-0.16, 3.3), (-0.22, 2.7), (-0.28, 2.0), (-0.35, 1.0)])
    out = {"gradable": True, "score": score, "grade": letter(score), "brier": b,
           "pregame_home_win_p": m["home_win_p"], "margin_error": round(result["home_margin"] - m["proj_margin_home"], 2)}
    if m.get("proj_total") is not None and result.get("total") is not None:
        out["total_error"] = round(result["total"] - m["proj_total"], 2)
    close = ((card.get("market") or {}).get("moneyline") or {}).get("close") or {}
    if close.get("fair_home") is not None:
        out["market_brier"] = brier(close["fair_home"], y)
        out["beat_market"] = b < out["market_brier"]
    return out


def side_won(market: str, side: str, close: dict, result: dict) -> float | None:
    """1 win, 0 loss, 0.5 push, at the closing number."""
    hm, tot = result.get("home_margin"), result.get("total")
    if market == "moneyline" and hm is not None:
        w = hm > 0 if side == "home" else hm < 0
        return 0.5 if hm == 0 else float(w)
    if market == "spread" and hm is not None and close.get("home_line") is not None:
        cover = hm + close["home_line"]
        if side == "away":
            cover = -cover
        return 0.5 if cover == 0 else float(cover > 0)
    if market == "total" and tot is not None and close.get("line") is not None:
        d = tot - close["line"]
        if side == "under":
            d = -d
        return 0.5 if d == 0 else float(d > 0)
    return None


def _thesis(card: dict, notebook: dict | None) -> tuple[str | None, str | None, str]:
    th = (notebook or {}).get("thesis") or {}
    pos = (notebook or {}).get("position") or {}
    if pos.get("market") and pos.get("side"):
        return pos["market"], pos["side"], "analyst position"
    if th.get("market") and th.get("side"):
        return th["market"], th["side"], "analyst thesis"
    c = ((card.get("summary") or {}).get("conclusion")) or {}
    if c.get("status") == "lean" and c.get("side") in ("home", "away"):
        return "spread", c["side"], "Boolin research lean (no analyst thesis)"
    return None, None, "no thesis recorded"


def grade_market(card: dict, notebook: dict | None) -> dict:
    mkt, side, basis = _thesis(card, notebook)
    if not mkt:
        return {"gradable": False, "reason": basis}
    m = (card.get("market") or {}).get(mkt) or {}
    close, opn = m.get("close"), m.get("open")
    if not close:
        return {"gradable": False, "reason": "no closing observation for that market"}
    pos = (notebook or {}).get("position")
    if pos and pos.get("market") == mkt:
        c = markets.clv(pos, close, mkt)
        if c.get("prob_clv") is not None:
            pp = c["prob_clv"] * 100
            score = _band(pp, [(3, 4.0), (1, 3.3), (0, 2.7), (-1, 2.0), (-3, 1.0)])
            return {"gradable": True, "score": score, "grade": letter(score), "basis": basis, "clv": c}
        if c.get("line_clv_pts") is not None:
            score = _band(c["line_clv_pts"], [(1.5, 4.0), (0.5, 3.3), (0, 2.7), (-0.5, 2.0), (-1.5, 1.0)])
            return {"gradable": True, "score": score, "grade": letter(score), "basis": basis, "clv": c}
        return {"gradable": False, "reason": c.get("reason") or "CLV not computable", "clv": c}
    # no position: did the market move toward the thesis between first sighting and close?
    if mkt == "spread" and opn:
        d = close["home_line"] - opn["home_line"]
        toward = -d if side == "home" else d
        score = _band(toward, [(1.5, 4.0), (0.5, 3.3), (0, 2.7), (-0.5, 2.0), (-1.5, 1.0)])
        return {"gradable": True, "score": score, "grade": letter(score), "basis": basis,
                "line_move_toward_thesis_pts": round(toward, 2)}
    if mkt == "total" and opn:
        d = close["line"] - opn["line"]
        toward = d if side == "over" else -d
        score = _band(toward, [(1.5, 4.0), (0.5, 3.3), (0, 2.7), (-0.5, 2.0), (-1.5, 1.0)])
        return {"gradable": True, "score": score, "grade": letter(score), "basis": basis,
                "line_move_toward_thesis_pts": round(toward, 2)}
    if mkt == "moneyline" and opn and opn.get("fair_home") is not None and close.get("fair_home") is not None:
        d = (close["fair_home"] - opn["fair_home"]) * 100
        toward = d if side == "home" else -d
        score = _band(toward, [(3, 4.0), (1, 3.3), (0, 2.7), (-1, 2.0), (-3, 1.0)])
        return {"gradable": True, "score": score, "grade": letter(score), "basis": basis,
                "fair_prob_move_toward_thesis_pp": round(toward, 2)}
    return {"gradable": False, "reason": "not enough market history"}


def grade_thesis(card: dict, notebook: dict | None, result: dict, market_grade: dict) -> dict:
    mkt, side, basis = _thesis(card, notebook)
    if not mkt:
        return {"gradable": False, "reason": basis}
    close = ((card.get("market") or {}).get(mkt) or {}).get("close") or {}
    won = side_won(mkt, side, close, result)
    if won is None:
        return {"gradable": False, "reason": "result or closing number missing"}
    good_price = (market_grade.get("score") or 0) >= 2.7 if market_grade.get("gradable") else None
    if won == 0.5:
        score = 2.5
    elif won:
        score = 4.0 if good_price in (True, None) else 3.0
    else:
        score = 2.0 if good_price else 0.7
    reviewed = ((notebook or {}).get("postgame_review") or {}).get("thesis_correct")
    return {"gradable": True, "score": score, "grade": letter(score), "basis": basis, "market": mkt, "side": side,
            "result_at_close": {1.0: "won", 0.0: "lost", 0.5: "push"}[won], "analyst_self_review": reviewed}


def grade_weather(card: dict, final_card: dict | None) -> dict:
    if card.get("league") != "NFL" or not (card.get("environment") or {}).get("outdoors"):
        return {"gradable": False, "reason": "weather not a factor (indoor or not NFL)"}
    pre = ((card.get("environment") or {}).get("wind_mph") or {}).get("value")
    post = (((final_card or {}).get("environment") or {}).get("wind_mph") or {}).get("value")
    if pre is None or post is None:
        return {"gradable": False, "reason": "pregame or recorded wind missing"}
    err = abs(post - pre)
    score = _band(-err, [(-3, 4.0), (-6, 3.0), (-10, 2.0)], 1.0)
    return {"gradable": True, "score": score, "grade": letter(score), "pregame_wind": pre, "recorded_wind": post}


def grade(card: dict, result: dict, notebook: dict | None = None, final_card: dict | None = None) -> dict:
    """card = the last pregame build of the research card (with market close)."""
    model = grade_model(card, result)
    market = grade_market(card, notebook)
    thesis = grade_thesis(card, notebook, result, market)
    weather = grade_weather(card, final_card)
    avail = {"gradable": False, "reason": "actual starters / inactives aren't stored yet, so availability calls "
                                          "can't be checked against what happened"}
    comps = {"model": model, "market_read": market, "thesis": thesis, "weather": weather, "availability": avail}
    scores = [c["score"] for c in comps.values() if c.get("gradable")]
    overall = round(sum(scores) / len(scores), 2) if scores else None
    return {"key": card["key"], "league": card["league"], "result": result, "components": comps,
            "overall": {"score": overall, "grade": letter(overall), "graded_components": len(scores)},
            "note": "Grades the research process. A sound read can lose and a weak one can win; look at many games."}
