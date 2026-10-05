"""Market vs Boolin. Disagreement is reported as a size label, never as an edge or a pick."""
from __future__ import annotations

# (aligned below, mild below, significant below, else extreme)
PROB_PP = (2.5, 5.0, 10.0)
SPREAD_PTS = {"NFL": (1.0, 2.5, 5.0), "CFB": (1.5, 3.5, 7.0), "NHL": (0.25, 0.5, 1.0), "MLB": (0.3, 0.6, 1.2)}
TOTAL_PTS = {"NFL": (1.5, 3.0, 6.0), "CFB": (2.0, 4.0, 8.0), "NHL": (0.25, 0.5, 1.0), "MLB": (0.4, 0.8, 1.5)}
LABELS = ("aligned", "mild disagreement", "significant disagreement", "extreme disagreement")


def label(x: float | None, cuts: tuple) -> str | None:
    if x is None:
        return None
    x = abs(x)
    for i, c in enumerate(cuts):
        if x < c:
            return LABELS[i]
    return LABELS[3]


def _worst(labels: list[str | None]) -> str | None:
    ls = [x for x in labels if x]
    return max(ls, key=LABELS.index) if ls else None


def compare(league: str, model: dict, market: dict, home: str, away: str) -> dict:
    """market = card['market'] (current points). Returns per-dimension rows and an overall label."""
    if not model.get("available"):
        return {"available": False, "reason": model.get("reason", "no model")}
    rows = []
    ml = (market.get("moneyline") or {}).get("current") or {}
    if ml.get("fair_home") is not None:
        d = round((model["home_win_p"] - ml["fair_home"]) * 100, 1)
        rows.append({"dimension": "Win probability (home)", "market": ml["fair_home"], "boolin": model["home_win_p"],
                     "difference": d, "unit": "pp", "label": label(d, PROB_PP),
                     "direction": f"Boolin higher on {home}" if d > 0 else f"Boolin higher on {away}" if d < 0 else "same",
                     "market_source": f"no-vig {ml.get('ref')}"})
    sp = (market.get("spread") or {}).get("current") or {}
    # MLB run lines / NHL puck lines are fixed at ±1.5, so they aren't a market margin estimate.
    if league not in ("MLB", "NHL") and sp.get("home_line") is not None and model.get("proj_margin_home") is not None:
        boolin_line = round(-model["proj_margin_home"], 1)
        d = round(boolin_line - sp["home_line"], 2)  # + means Boolin likes home less than the market
        rows.append({"dimension": f"Spread ({home})", "market": sp["home_line"], "boolin": boolin_line,
                     "difference": d, "unit": "pts", "label": label(d, SPREAD_PTS.get(league, SPREAD_PTS["NFL"])),
                     "direction": f"Boolin less on {home}" if d > 0 else f"Boolin more on {home}" if d < 0 else "same"})
    tot = (market.get("total") or {}).get("current") or {}
    if tot.get("line") is not None and model.get("proj_total") is not None:
        d = round(model["proj_total"] - tot["line"], 2)
        rows.append({"dimension": "Total", "market": tot["line"], "boolin": model["proj_total"], "difference": d,
                     "unit": "pts", "label": label(d, TOTAL_PTS.get(league, TOTAL_PTS["NFL"])),
                     "direction": "Boolin higher" if d > 0 else "Boolin lower" if d < 0 else "same"})
    if not rows:
        return {"available": False, "reason": "no market to compare against"}
    for r in rows:
        if r["dimension"] == "Total":
            r["note"] = "Totals are uncalibrated in this model version: shown, but not used for the overall label."
    overall = _worst([r["label"] for r in rows if r["dimension"] != "Total"]) or "aligned"
    conf = model.get("confidence")
    caveat = None
    if conf == "low" and overall != "aligned":
        caveat = "Model confidence is low (thin samples); disagreement may reflect missing data, not a market error."
    return {"available": True, "rows": rows, "overall": overall, "model_confidence": conf,
            "model_version": model.get("version"), "caveat": caveat,
            "reminder": "Disagreement is not an edge: the baseline model is uncalibrated."}
