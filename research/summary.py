"""Research summary: why it matters, supporting / contradicting evidence, unknowns, conclusion.

Built only from the card's structured fields. Evidence is judged relative to a *thesis side*:
the analyst's notebook thesis if there is one, otherwise the side Boolin rates higher than the
market. The conclusion is a research status (lean / watch / review required / pass / insufficient
information), not a bet instruction. A validation gate (see `gate`) keeps strong disagreement from
becoming a `lean` while the league's model has no validated holdout evidence.
"""
from __future__ import annotations

from .provenance import val

CONTRIB_EVIDENCE = {"NFL": 0.75, "NHL": 0.05, "MLB": 0.08}   # home-margin units


def thesis_side(card: dict, notebook: dict | None) -> tuple[str | None, str]:
    th = (notebook or {}).get("thesis") or {}
    if th.get("side") in ("home", "away"):
        return th["side"], "analyst thesis"
    if th.get("side") in ("over", "under"):
        return th["side"], "analyst thesis"
    cmp_ = card.get("comparison") or {}
    for r in cmp_.get("rows") or []:
        if r["dimension"].startswith("Win probability") and r["label"] != "aligned":
            return ("home" if r["difference"] > 0 else "away"), "Boolin vs market"
        if r["dimension"].startswith("Spread") and r["label"] != "aligned":
            return ("home" if r["difference"] < 0 else "away"), "Boolin vs market"
    return None, "no thesis (model and market aligned or unavailable)"


def build(card: dict, notebook: dict | None = None) -> dict:
    lg = card["league"]
    g = card["game"]
    team = {"home": g["home"]["abbr"], "away": g["away"]["abbr"]}
    side, basis = thesis_side(card, notebook)
    matters, sup, con, unk = [], [], [], []

    # why it matters
    ctx = g.get("context") or {}
    if ctx.get("series"):
        matters.append(f"{ctx['series']}" + (f", game {ctx['series_game']}" if ctx.get("series_game") else "")
                       + (f" ({ctx['series_status']})" if ctx.get("series_status") else ""))
    if ctx.get("division_game"):
        matters.append("Division game.")
    for f in card.get("flags") or []:
        if f["severity"] in ("alert", "watch") and f["category"] in ("market", "injury", "environment") \
                and len(matters) < 8:
            matters.append(f"{f['title']}: {f['why']}")
    cmp_ = card.get("comparison") or {}
    if cmp_.get("overall") and cmp_["overall"] != "aligned":
        matters.append(f"Boolin vs market: {cmp_['overall']}.")
    if not matters:
        matters.append("No market move, availability news or model disagreement stands out yet.")

    # evidence relative to the thesis side
    m = card.get("model") or {}
    if side in ("home", "away") and m.get("available"):
        sign = 1 if side == "home" else -1
        for c in m.get("contributions") or []:
            if abs(c["margin"]) < CONTRIB_EVIDENCE.get(lg, 0.5) or c["name"] in ("Home field", "Home ice"):
                continue
            item = f"{c['name']}: {c['margin'] * sign:+.2f} toward {team[side]} ({c.get('note')})."
            (sup if c["margin"] * sign > 0 else con).append(item)
    if side in ("over", "under") and m.get("available") and m.get("proj_total") is not None:
        tl = val_line(card)
        if tl is not None:
            d = m["proj_total"] - tl
            item = f"Boolin total {m['proj_total']} vs market {tl}."
            (sup if (d > 0) == (side == "over") else con).append(item)
    mv = ((card.get("market") or {}).get("spread") or {}).get("movement") or {}
    if side in ("home", "away") and mv.get("home_line"):
        toward = "home" if mv["home_line"] < 0 else "away"
        item = f"Spread moved {mv['home_line']:+g} toward {team[toward]} since first seen."
        (sup if toward == side else con).append(item)
    mmv = ((card.get("market") or {}).get("moneyline") or {}).get("movement") or {}
    if side in ("home", "away") and mmv.get("fair_home"):
        toward = "home" if mmv["fair_home"] > 0 else "away"
        item = f"No-vig win probability moved {abs(mmv['fair_home']) * 100:.1f} pp toward {team[toward]}."
        (sup if toward == side else con).append(item)
    for r in cmp_.get("rows") or []:
        if side in ("home", "away") and r["dimension"].startswith("Win probability") and r["label"] != "aligned":
            fav = "home" if r["difference"] > 0 else "away"
            item = f"Boolin {r['boolin'] * 100:.1f}% vs market {r['market'] * 100:.1f}% on {team['home']}."
            (sup if fav == side else con).append(item)
    for f in card.get("flags") or []:
        if f["category"] == "injury" and f["id"].startswith("injury.downgrade") and side in ("home", "away"):
            hit = f"({team[side]})" in f["title"]
            (con if hit else sup).append(f"Injury: {f['why']}")

    # unknowns
    av = card.get("availability") or {}
    for s, st in (av.get("starters") or {}).items():
        if st.get("kind") in ("unknown", "projected", "reported"):
            unk.append(f"{team[s]} starter: {st.get('value') or 'unknown'} ({st.get('kind')}).")
    for s, lu in (av.get("lineups") or {}).items():
        if lu.get("kind") == "unknown":
            unk.append(f"{team[s]} lineup not posted.")
    for r in av.get("injuries") or []:
        if r.get("status_norm") in ("questionable", "doubtful", "day-to-day"):
            unk.append(f"{r['player']} ({r['team']}, {r.get('pos') or '?'}) {r['status']}.")
    env = card.get("environment") or {}
    if env.get("outdoors") and (env.get("wind_mph") or {}).get("kind") == "unknown":
        unk.append("Weather/wind not in the data.")
    for w in m.get("sample_warnings") or []:
        unk.append(w)
    if not m.get("available"):
        unk.append(f"No Boolin projection: {m.get('reason')}.")
    for n in (notebook or {}).get("unknowns") or []:
        unk.append(f"Analyst: {n}")

    return {"thesis_side": side, "thesis_team": team.get(side, side), "thesis_basis": basis,
            "why_it_matters": matters, "supporting": sup, "contradicting": con, "unknowns": unk[:14],
            "conclusion": conclude(card, side, sup, con, unk, notebook)}


def val_line(card):
    return (((card.get("market") or {}).get("total") or {}).get("current") or {}).get("line")


REVIEW = "review required"
EXTREME_TEXT = ("Extreme model/market disagreement. Model is currently {cal}. "
                "Review required; disagreement is not evidence of edge.")


def _base(card, side, sup, con) -> dict:
    """The research status before the validation gate (what the evidence alone would say)."""
    m = card.get("model") or {}
    cmp_ = card.get("comparison") or {}
    team = {"home": card["game"]["home"]["abbr"], "away": card["game"]["away"]["abbr"]}
    critical = [f for f in card.get("flags") or [] if f["severity"] == "alert" and f["category"] in ("injury", "data")]
    if not m.get("available"):
        return {"status": "insufficient information", "detail": f"No Boolin projection ({m.get('reason')})."}
    if critical:
        return {"status": "insufficient information",
                "detail": "Unresolved: " + "; ".join(f["title"] for f in critical) + "."}
    if not cmp_.get("available"):
        return {"status": "insufficient information", "detail": "No market to compare against."}
    if cmp_["overall"] == "aligned":
        return {"status": "pass", "detail": "Boolin and the market agree; nothing in the data separates them."}
    if cmp_["overall"] == "mild disagreement" or cmp_.get("model_confidence") == "low":
        return {"status": "watch", "detail": f"{cmp_['overall'].capitalize()} with model confidence "
                                              f"{cmp_.get('model_confidence')}; not enough to form a view yet."}
    if side not in ("home", "away"):
        return {"status": "watch", "detail": "Disagreement without a clear side; recheck after the next pull."}
    n_sup, n_con = len(sup), len(con)
    if n_con > n_sup:
        return {"status": "watch", "detail": f"Boolin differs from the market toward {team.get(side, side)}, "
                                              f"but more evidence points the other way ({n_con} vs {n_sup})."}
    return {"status": "lean", "side": side, "team": team.get(side, side),
            "detail": f"Research lean {team.get(side, side)}: {cmp_['overall']}, model confidence "
                      f"{cmp_.get('model_confidence')}, {n_sup} supporting vs {n_con} contradicting items. "
                      "Research direction, not a bet signal."}


def _unresolved_key_player(card) -> list[str]:
    av = card.get("availability") or {}
    out = [f"{r.get('player')} ({r.get('team')}, {r.get('pos')}) {r.get('status')}" for r in av.get("injuries") or []
           if r.get("key_player") and r.get("status_norm") in ("questionable", "doubtful", "day-to-day")]
    out += [f["title"] for f in card.get("flags") or [] if f["severity"] == "alert" and f["category"] == "injury"]
    return out


def gate(card, base: dict) -> dict:
    """Validation gate. Strong disagreement is only allowed to become a `lean` once the league's
    model has validated holdout evidence; until then it becomes `watch` or `review required`.
      significant/extreme + model not validated     -> extreme: review required; significant: never lean
      significant/extreme + small sample            -> review required
      significant/extreme + unresolved key player   -> review required
    Returns the gated conclusion with `gate` = {applied, reasons, ungated_status, validation}."""
    m = card.get("model") or {}
    cmp_ = card.get("comparison") or {}
    v = card.get("validation") or {"calibration_status": "uncalibrated", "validation_status": "not validated",
                                   "recommendation": "NOT READY", "lean_allowed": False}
    overall = cmp_.get("overall") if cmp_.get("available") else None
    info = {"applied": False, "reasons": [], "ungated_status": base["status"],
            "validation": {k: v.get(k) for k in ("model_version", "calibration_status", "validation_status",
                                                 "recommendation", "lean_allowed")}}
    if not m.get("available") or overall not in ("significant disagreement", "extreme disagreement"):
        if base["status"] == "lean" and not v.get("lean_allowed"):   # belt and braces: no lean without validation
            info.update(applied=True, reasons=[f"model {v.get('calibration_status')}; validation: {v.get('validation_status')}"])
            return {"status": "watch", "detail": base["detail"].replace("Research lean", "Possible direction") +
                    " Not a lean: the model isn't validated yet.", "gate": info}
        return {**base, "gate": info}
    reasons = []
    unvalidated = not v.get("lean_allowed")
    if unvalidated:
        reasons.append(f"model {v.get('calibration_status')}; validation: {v.get('validation_status')} "
                       f"({v.get('recommendation')})")
    small = [w for w in m.get("sample_warnings") or [] if w.startswith("Small sample")] or \
        (["model confidence low"] if m.get("confidence") == "low" else [])
    if small:
        reasons.append("small sample: " + "; ".join(small[:2]))
    keyp = _unresolved_key_player(card)
    if keyp:
        reasons.append("unresolved key player: " + "; ".join(keyp[:3]))
    if not reasons:
        return {**base, "gate": info}
    info.update(applied=True, reasons=reasons)
    cal = "uncalibrated" if unvalidated else "validated"
    if overall == "extreme disagreement" or small or keyp:
        head = EXTREME_TEXT.format(cal=cal) if overall == "extreme disagreement" else \
            f"{overall.capitalize()} with {'a small sample' if small else 'an unresolved key player'}. Review required."
        return {"status": REVIEW, "detail": head, "gate": info,
                **({"side": base["side"], "team": base.get("team")} if base.get("side") else {})}
    # significant disagreement, unvalidated model, nothing else wrong: at most a watch
    if base["status"] == "lean":
        return {"status": "watch", "detail": f"Significant disagreement toward {base.get('team')}, but the model is "
                f"{cal} ({v.get('validation_status')}): a research direction to check, not a lean.", "gate": info}
    return {**base, "gate": info}


def conclude(card, side, sup, con, unk, notebook) -> dict:
    nb = notebook or {}
    out = gate(card, _base(card, side, sup, con))
    out["trigger"] = nb.get("entry_trigger") or trigger(card)
    if nb.get("decision"):
        out["analyst_decision"] = nb["decision"]
    out["would_change_if"] = changers(card, unk)
    return out


def trigger(card) -> str | None:
    """The market number at which Boolin and the market would line up (or split further)."""
    for r in (card.get("comparison") or {}).get("rows") or []:
        if r["dimension"].startswith("Spread"):
            return (f"Boolin's line is {card['game']['home']['abbr']} {r['boolin']:+g}; market {r['market']:+g}. "
                    "The gap closes if the market moves to Boolin's number.")
    return None


def changers(card, unk) -> list[str]:
    out = []
    av = card.get("availability") or {}
    for s, st in (av.get("starters") or {}).items():
        if st.get("kind") != "confirmed":
            out.append(f"{card['game'][s]['abbr']} starter confirmed as someone other than {st.get('value') or 'the projection'}.")
    if any("Questionable" in u or "Doubtful" in u or "Day-To-Day" in u for u in unk):
        out.append("A listed questionable/doubtful player is ruled out or cleared.")
    if (card.get("environment") or {}).get("outdoors"):
        out.append("Wind forecast crossing 15 mph.")
    out.append("A market move that closes or widens the gap to Boolin's number.")
    return out
