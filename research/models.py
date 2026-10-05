"""Boolin baseline models: small, deterministic and fully explained.

These are *baseline* projections built only from data the pipeline already stores. They are
uncalibrated (no backtest yet) and every output carries that label. Each model is additive in
home-margin units, so every projection decomposes exactly into named contributions; that is
what the "why did it move" explanation reads. Win probability is a fixed transform of the margin.

Small samples are regressed toward a prior with weight n / (n + K), and the regression weight is
part of the output so a 2-game number never looks as solid as a full season.

Leagues without a model here (CFB, NBA, WNBA) return available=False with the reason.
"""
from __future__ import annotations

import math

VERSION = "baseline-0.1 (uncalibrated)"


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def regress(x: float | None, n: float | None, k: float, prior: float = 0.0) -> tuple[float | None, float]:
    """Shrink x toward prior. Returns (value, weight on x)."""
    if x is None or not n:
        return (None, 0.0)
    w = n / (n + k)
    return (prior + w * (x - prior), round(w, 3))


def confidence(weights: list[float]) -> str:
    """high / medium / low from the smallest regression weight among the inputs."""
    if not weights:
        return "low"
    w = min(weights)
    return "high" if w >= 0.6 else "medium" if w >= 0.35 else "low"


def unavailable(reason: str) -> dict:
    return {"available": False, "version": VERSION, "reason": reason}


# ---------------------------------------------------------------- NFL

NFL = {"plays": 62, "hfa": 1.5, "k_games": 4, "base_ppg": 22.5, "margin_sd": 13.5,
       "rest_pts_per_day": 0.1, "rest_cap": 1.0, "wind_floor": 10, "wind_pts_per_mph": 0.15}


def nfl(inp: dict) -> dict:
    """inp = {"home": {"off_epa", "def_epa", "games", "rest"}, "away": {...},
              "outdoors": bool|None, "wind_mph": float|None, "neutral": bool}
    def_epa is EPA/play ALLOWED (lower is better)."""
    P = NFL
    h, a = inp.get("home") or {}, inp.get("away") or {}
    need = [h.get("off_epa"), h.get("def_epa"), a.get("off_epa"), a.get("def_epa")]
    if any(v is None for v in need):
        return unavailable("team EPA missing for one side (nfl.json team_epa)")
    oh, wh = regress(h["off_epa"], h.get("games"), P["k_games"])
    dh, _ = regress(h["def_epa"], h.get("games"), P["k_games"])
    oa, wa = regress(a["off_epa"], a.get("games"), P["k_games"])
    da, _ = regress(a["def_epa"], a.get("games"), P["k_games"])
    contrib = []
    hfa = 0.0 if inp.get("neutral") else P["hfa"]
    contrib.append({"name": "Home field", "margin": hfa,
                    "note": "neutral site" if inp.get("neutral") else f"{P['hfa']} pts standard"})
    contrib.append({"name": "Offense (EPA/play, regressed)", "margin": round(P["plays"] * (oh - oa) / 2, 2),
                    "note": f"home {oh:+.3f} vs away {oa:+.3f} EPA/play"})
    contrib.append({"name": "Defense (EPA/play allowed, regressed)", "margin": round(P["plays"] * (da - dh) / 2, 2),
                    "note": f"home allows {dh:+.3f} vs away allows {da:+.3f}"})
    rest_note, rest = "rest unknown", 0.0
    if h.get("rest") is not None and a.get("rest") is not None:
        d = h["rest"] - a["rest"]
        rest = max(-P["rest_cap"], min(P["rest_cap"], d * P["rest_pts_per_day"]))
        rest_note = f"{h['rest']} vs {a['rest']} days"
    contrib.append({"name": "Rest", "margin": round(rest, 2), "note": rest_note})
    margin = round(sum(c["margin"] for c in contrib), 2)
    total_parts = [{"name": "League baseline", "points": 2 * P["base_ppg"]},
                   {"name": "Both offenses and defenses (EPA/play)",
                    "points": round(P["plays"] * (oh + da + oa + dh) / 2, 2)}]
    wind = inp.get("wind_mph")
    if inp.get("outdoors") and wind is not None and wind > P["wind_floor"]:
        total_parts.append({"name": f"Wind {wind:g} mph", "points": round(-(wind - P["wind_floor"]) * P["wind_pts_per_mph"], 2)})
    total = round(sum(p["points"] for p in total_parts), 1)
    warnings = []
    for side, t in (("home", h), ("away", a)):
        if (t.get("games") or 0) < 3:
            warnings.append(f"Small sample: {side} team EPA from {t.get('games') or 0} game(s); regressed hard toward average.")
    assumptions = ["Team EPA/play stands in for team strength; no player-level injury adjustments.",
                   "Margin SD 13.5 pts converts margin to win probability."]
    if inp.get("outdoors") and wind is None:
        assumptions.append("Wind unknown for an outdoor game: no weather adjustment applied.")
    return {"available": True, "version": VERSION, "league": "NFL",
            "proj_margin_home": margin, "home_win_p": round(_phi(margin / P["margin_sd"]), 4),
            "proj_total": total, "contributions": contrib, "total_contributions": total_parts,
            "regression_weights": {"home": wh, "away": wa}, "confidence": confidence([wh, wa]),
            "sample_warnings": warnings, "assumptions": assumptions}


# ---------------------------------------------------------------- NHL

NHL = {"home_ice": 0.15, "k_gp": 20, "lg_goals": 3.1, "lg_xg5": 1.95, "k_goalie_gp": 30, "b2b": 0.15,
       "margin_sd": 2.4}


def _blend_rate(now: dict | None, prev: dict | None, key: str, k: float) -> tuple[float | None, float]:
    """Per-game rate: this season regressed toward last season."""
    rn = (now or {}).get(key) / now["gp"] if now and now.get("gp") and now.get(key) is not None else None
    rp = (prev or {}).get(key) / prev["gp"] if prev and prev.get("gp") and prev.get(key) is not None else None
    if rn is None and rp is None:
        return (None, 0.0)
    if rn is None:
        return (rp, 0.0)
    if rp is None:
        return (rn, 1.0)
    w = now["gp"] / (now["gp"] + k)
    return (rp + w * (rn - rp), round(w, 3))


def nhl(inp: dict) -> dict:
    """inp = {"home"/"away": {"xg_now", "xg_prev" (MoneyPuck 5v5 {gp, xgf, xga}),
              "goalie": {"name", "gsax", "gp", "status"}, "b2b": bool}}
    Goals for a team = league goals/game x (its 5v5 xGF rate / league) x (opponent xGA rate / league),
    minus the opposing goalie's regressed GSAx/game."""
    P = NHL
    sides = {}
    for s in ("home", "away"):
        t = inp.get(s) or {}
        xgf, w1 = _blend_rate(t.get("xg_now"), t.get("xg_prev"), "xgf", P["k_gp"])
        xga, _ = _blend_rate(t.get("xg_now"), t.get("xg_prev"), "xga", P["k_gp"])
        if xgf is None or xga is None:
            return unavailable(f"5v5 xG missing for the {s} team (MoneyPuck)")
        g = t.get("goalie") or {}
        gs = ((g["gsax"] / g["gp"]) * (g["gp"] / (g["gp"] + P["k_goalie_gp"]))
              if g.get("gp") and g.get("gsax") is not None else 0.0)
        sides[s] = {"off": xgf / P["lg_xg5"], "def": xga / P["lg_xg5"], "xgf": xgf, "xga": xga, "w": w1,
                    "goalie": round(gs, 3), "b2b": bool(t.get("b2b")),
                    "goalie_name": g.get("name"), "goalie_status": g.get("status")}
    h, a = sides["home"], sides["away"]
    G = P["lg_goals"]

    def goals(use_off, use_def, use_g, use_b2b):
        gh = G * (h["off"] if use_off else 1) * (a["def"] if use_def else 1) - (a["goalie"] if use_g else 0)
        ga = G * (a["off"] if use_off else 1) * (h["def"] if use_def else 1) - (h["goalie"] if use_g else 0)
        b = ((P["b2b"] if a["b2b"] else 0) - (P["b2b"] if h["b2b"] else 0)) if use_b2b else 0
        return gh, ga, gh - ga + P["home_ice"] + b

    m0 = goals(0, 0, 0, 0)[2]
    m1 = goals(1, 0, 0, 0)[2]
    m2 = goals(1, 1, 0, 0)[2]
    m3 = goals(1, 1, 1, 0)[2]
    gh, ga, m4 = goals(1, 1, 1, 1)
    contrib = [
        {"name": "Home ice", "margin": round(m0, 3), "note": "0.15 goals"},
        {"name": "Shot quality for (5v5 xGF/gp, blended)", "margin": round(m1 - m0, 3),
         "note": f"home {h['xgf']:.2f} vs away {a['xgf']:.2f} (league ~{P['lg_xg5']})"},
        {"name": "Shot quality against (5v5 xGA/gp, blended)", "margin": round(m2 - m1, 3),
         "note": f"home allows {h['xga']:.2f} vs away allows {a['xga']:.2f}"},
        {"name": "Goaltending (GSAx/gp, regressed)", "margin": round(m3 - m2, 3),
         "note": f"{h['goalie_name'] or 'unknown'} vs {a['goalie_name'] or 'unknown'} (projected starters)"},
        {"name": "Back-to-back", "margin": round(m4 - m3, 3),
         "note": ", ".join(x for x, f in (("away on B2B", a["b2b"]), ("home on B2B", h["b2b"])) if f) or "neither"},
    ]
    margin = round(sum(c["margin"] for c in contrib), 3)
    warnings = [f"Small sample: {s} 5v5 xG leans on last season (this-season weight {sides[s]['w']:.2f})."
                for s in ("home", "away") if sides[s]["w"] < 0.35]
    if h["goalie_status"] != "confirmed" or a["goalie_status"] != "confirmed":
        warnings.append("Goalie inputs use the projected starter (most starts); starters are not confirmed.")
    return {"available": True, "version": VERSION, "league": "NHL",
            "proj_margin_home": margin, "home_win_p": round(_phi(margin / P["margin_sd"]), 4),
            "proj_total": round(gh + ga, 2), "contributions": contrib,
            "total_contributions": [{"name": "Home expected goals", "points": round(gh, 2)},
                                    {"name": "Away expected goals", "points": round(ga, 2)}],
            "regression_weights": {"home": h["w"], "away": a["w"]},
            "confidence": confidence([max(h["w"], 0.35), max(a["w"], 0.35)]) if (h["w"] and a["w"]) else "medium",
            "sample_warnings": warnings,
            "assumptions": [f"League scoring {P['lg_goals']} goals/team/game; 5v5 xG rates scale it.",
                            "Goal-margin SD 2.4 converts margin to win probability (OT/shootout not modeled)."]}


# ---------------------------------------------------------------- MLB

MLB = {"lg_runs": 4.4, "lg_ops": 0.715, "ops_exp": 1.8, "k_pa": 600, "lg_kbb": 0.14, "lg_era": 4.2,
       "era_per_kbb": 12.0, "k_bf": 300, "k_ip": 80, "starter_outs_default": 15.0, "tired_pen": 1.06,
       "tired_pitches": 120, "hfa_runs": 0.15, "pyth": 1.83, "postseason_env": 0.9}


def _runs(off: dict, pitcher: dict | None, pen: dict, P=MLB, env: float = 1.0) -> tuple[float, dict]:
    """Expected runs for a lineup vs a starter + bullpen, with the pieces."""
    ops, w_ops = regress(off.get("ops"), off.get("pa"), P["k_pa"], P["lg_ops"])
    off_f = (ops / P["lg_ops"]) ** P["ops_exp"] if ops else 1.0
    if pitcher and pitcher.get("kbb") is not None:
        kbb, w_p = regress(pitcher["kbb"], pitcher.get("bf"), P["k_bf"], P["lg_kbb"])
        est = P["lg_era"] - P["era_per_kbb"] * (kbb - P["lg_kbb"])           # skill-based run estimate
        if pitcher.get("era") is not None and pitcher.get("ip"):
            era, _ = regress(pitcher["era"], pitcher["ip"], P["k_ip"], P["lg_era"])
            est = 0.5 * est + 0.5 * era                                         # blend with results
        sp_f = max(0.45, est / P["lg_era"])
        outs = pitcher.get("outs_mean") or P["starter_outs_default"]
    else:
        sp_f, w_p, outs = 1.0, 0.0, P["starter_outs_default"]
    share = min(1.0, outs / 27)
    pen_f = P["tired_pen"] if pen.get("tired") else 1.0
    runs = P["lg_runs"] * env * off_f * (share * sp_f + (1 - share) * pen_f)
    return runs, {"off_f": off_f, "sp_f": sp_f, "pen_f": pen_f, "share": share, "w_ops": w_ops or 0.0, "w_p": w_p}


def mlb(inp: dict) -> dict:
    """inp = {"home"/"away": {"bats_vs_opp_hand": {"ops", "pa"}, "starter": {"name", "kbb", "bf",
              "outs_mean", "status"} | None, "bullpen": {"tired": bool, "pitches_last3": n}}}
    Home batters face the AWAY starter and bullpen."""
    P = MLB
    h, a = inp.get("home") or {}, inp.get("away") or {}
    if not h.get("bats_vs_opp_hand") or not a.get("bats_vs_opp_hand"):
        return unavailable("lineup splits vs the opposing starter's hand missing (mlb.json bats)")
    neutral_pen = {"tired": False}
    env = P["postseason_env"] if inp.get("postseason") else 1.0

    def margin_with(use_off: bool, use_sp: bool, use_pen: bool) -> tuple[float, float, float]:
        rh, _ = _runs(h["bats_vs_opp_hand"] if use_off else {}, a.get("starter") if use_sp else None,
                      a.get("bullpen") if use_pen else neutral_pen, env=env)
        ra, _ = _runs(a["bats_vs_opp_hand"] if use_off else {}, h.get("starter") if use_sp else None,
                      h.get("bullpen") if use_pen else neutral_pen, env=env)
        return rh + P["hfa_runs"] / 2, ra - P["hfa_runs"] / 2, rh - ra + P["hfa_runs"]

    _, _, m0 = margin_with(False, False, False)
    _, _, m1 = margin_with(True, False, False)
    _, _, m2 = margin_with(True, True, False)
    rh, ra, m3 = margin_with(True, True, True)
    _, dh = _runs(h["bats_vs_opp_hand"], a.get("starter"), a.get("bullpen") or neutral_pen)
    _, da = _runs(a["bats_vs_opp_hand"], h.get("starter"), h.get("bullpen") or neutral_pen)
    hs, as_ = a.get("starter") or {}, h.get("starter") or {}
    contrib = [
        {"name": "Home field", "margin": round(m0, 3), "note": "0.15 runs"},
        {"name": "Lineups vs starter's hand (OPS, regressed)", "margin": round(m1 - m0, 3),
         "note": f"home {h['bats_vs_opp_hand'].get('ops')} vs away {a['bats_vs_opp_hand'].get('ops')} OPS"},
        {"name": "Starting pitchers (K-BB%, regressed)", "margin": round(m2 - m1, 3),
         "note": f"{as_.get('name') or 'unknown'} (home) vs {hs.get('name') or 'unknown'} (away)"},
        {"name": "Bullpen workload (last 3 days)", "margin": round(m3 - m2, 3),
         "note": ", ".join(x for x, f in (("home pen taxed", (h.get("bullpen") or {}).get("tired")),
                                          ("away pen taxed", (a.get("bullpen") or {}).get("tired"))) if f) or "both normal"},
    ]
    margin = round(sum(c["margin"] for c in contrib), 3)
    p = rh ** P["pyth"] / (rh ** P["pyth"] + ra ** P["pyth"])
    warnings = []
    for side, s in (("home", h.get("starter")), ("away", a.get("starter"))):
        if not s:
            warnings.append(f"No {side} starter in the data: league-average starter assumed.")
        elif s.get("starts", 99) < 5:
            warnings.append(f"Small sample: {side} starter has {s.get('starts')} starts.")
    ws = [x for x in (dh["w_ops"], da["w_ops"], dh["w_p"], da["w_p"]) if x is not None]
    return {"available": True, "version": VERSION, "league": "MLB",
            "proj_margin_home": margin, "home_win_p": round(p, 4), "proj_total": round(rh + ra, 2),
            "contributions": contrib,
            "total_contributions": [{"name": "Home expected runs", "points": round(rh, 2)},
                                    {"name": "Away expected runs", "points": round(ra, 2)}],
            "regression_weights": {"home_lineup": dh["w_ops"], "away_lineup": da["w_ops"],
                                   "away_starter": dh["w_p"], "home_starter": da["w_p"]},
            "confidence": confidence(ws), "sample_warnings": warnings,
            "assumptions": ["Starter covers his average outs; bullpen is league average (x1.06 if taxed).",
                            "Starter run estimate = half K-BB% skill, half regressed ERA."]
                           + (["Postseason scoring environment assumed 10% below the regular season."] if env != 1.0 else []) + [
                            "Pythagorean exponent 1.83 converts runs to win probability.",
                            "Park factors and platoon-level lineups are not modeled."]}


MODELS = {"NFL": nfl, "NHL": nhl, "MLB": mlb}


def run(league: str, inp: dict | None) -> dict:
    fn = MODELS.get(league)
    if not fn:
        return unavailable(f"no Boolin model for {league} yet")
    if not inp:
        return unavailable("model inputs could not be assembled")
    return fn(inp)
