"""Scenario analysis: re-run the baseline model with one uncertain input changed.

Only scenarios whose input the model actually uses are computed. Anything the model can't
represent (e.g. an NFL QB sitting: there is no player-level value in the model) is listed as
unsupported with the reason, never estimated.
"""
from __future__ import annotations

import copy

from . import models


def _result(name: str, base: dict, out: dict, note: str | None = None) -> dict:
    if not out.get("available"):
        return {"name": name, "supported": False, "reason": out.get("reason")}
    r = {"name": name, "supported": True, "home_win_p": out["home_win_p"], "proj_margin_home": out["proj_margin_home"],
         "proj_total": out.get("proj_total"),
         "delta": {"home_win_p": round(out["home_win_p"] - base["home_win_p"], 4),
                   "proj_margin_home": round(out["proj_margin_home"] - base["proj_margin_home"], 3),
                   "proj_total": (round(out["proj_total"] - base["proj_total"], 2)
                                  if out.get("proj_total") is not None and base.get("proj_total") is not None else None)}}
    if note:
        r["note"] = note
    return r


def unsupported(name: str, reason: str) -> dict:
    return {"name": name, "supported": False, "reason": reason}


def build(card: dict, model_input: dict | None, raw_side: dict | None = None) -> dict:
    lg = card["league"]
    base = card.get("model") or {}
    if not base.get("available") or not model_input:
        return {"baseline": None, "scenarios": [], "note": "No baseline projection, so no scenarios."}
    run = lambda inp: models.run(lg, inp)  # noqa: E731
    out = []
    g = card["game"]
    if lg == "NFL":
        if model_input.get("outdoors"):
            for w in (5, 20):
                inp = copy.deepcopy(model_input)
                inp["wind_mph"] = w
                out.append(_result(f"Wind {w} mph", base, run(inp)))
        else:
            out.append(unsupported("Wind", "indoor or roof unknown: wind does not apply"))
        for s in ("away", "home"):
            out.append(unsupported(f"{g[s]['abbr']} QB sits",
                                   "model has no player-level QB value; team EPA can't isolate the QB"))
    elif lg == "NHL":
        for s in ("away", "home"):
            alts = (raw_side or {}).get(s) or []
            cur = ((model_input.get(s) or {}).get("goalie") or {}).get("name")
            for alt in alts:
                if alt.get("name") == cur or alt.get("gp") is None:
                    continue
                inp = copy.deepcopy(model_input)
                inp[s]["goalie"] = {**alt, "status": "projected"}
                out.append(_result(f"{g[s]['abbr']} starts {alt['name']}", base, run(inp),
                                   note=f"GSAx {alt.get('gsax')} over {alt.get('gp')} GP"))
            inp = copy.deepcopy(model_input)
            inp[s]["b2b"] = not inp[s].get("b2b")
            out.append(_result(f"{g[s]['abbr']} {'rested' if model_input[s].get('b2b') else 'on a back-to-back'}",
                               base, run(inp)))
    elif lg == "MLB":
        for s in ("away", "home"):
            inp = copy.deepcopy(model_input)
            inp[s]["starter"] = None
            out.append(_result(f"{g[s]['abbr']} starter replaced by a league-average starter", base, run(inp)))
            inp = copy.deepcopy(model_input)
            tired = bool((inp[s].get("bullpen") or {}).get("tired"))
            inp[s]["bullpen"] = {**(inp[s].get("bullpen") or {}), "tired": not tired}
            out.append(_result(f"{g[s]['abbr']} bullpen {'rested' if tired else 'heavily used'}", base, run(inp)))
    else:
        out.append(unsupported("Player availability", f"no {lg} model yet"))
    return {"baseline": {"home_win_p": base["home_win_p"], "proj_margin_home": base["proj_margin_home"],
                         "proj_total": base.get("proj_total")},
            "scenarios": out,
            "note": "Same uncalibrated baseline model with one input changed; a sensitivity check, not a forecast."}
