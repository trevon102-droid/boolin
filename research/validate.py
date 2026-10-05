"""Model validation v1: measure the existing models before anyone tunes them.

    python -m research.validate                       # all leagues, all dates -> data/research/validation/
    python -m research.validate --league NFL
    python -m research.validate --since 2026-01-01 --until 2026-10-05

Runs offline from files already in the repo:
  live_archive  data/research/archive/*.json.gz (the last pregame card per game) +
                data/research/results/*.json (final scores). Market = the pregame no-vig price the
                card saw when it was built (same moment as the model).
  nfl_replay    data/reference/nfl_replay_inputs.json.gz (built in CI by pipeline.nfl_replay): the
                inputs the live NFL model would have had before each game since 2012, from
                prior-week play-by-play only. Market = the CLOSING line (a harder benchmark than
                the price at model time, and labelled as such).
The two sources are reported separately and never pooled.

Point in time: a row is kept only when every model input and market observation can be shown to
predate the start. Anything that can't is excluded with the reason (excluded.jsonl).
Splits are chronological, never shuffled: development = games up to the date the baseline-0.1
parameters were last changed (live) / seasons before 2022 (replay); holdout = after.
Nothing here changes a model parameter. The k-games sensitivity table is a diagnostic only.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import gzip
import json
from pathlib import Path

from . import models, vmetrics as V
from . import timeutil as T

ROOT = Path(__file__).resolve().parent.parent
RESEARCH = ROOT / "data" / "research"
OUT = RESEARCH / "validation"
REPLAY = ROOT / "data" / "reference" / "nfl_replay_inputs.json.gz"

VALIDATION_VERSION = "validation-1.0"
MODELED = ("NFL", "NHL", "MLB")
MODEL_FROZEN_AT = "2026-10-05"     # last day baseline-0.1 parameters were changed: live games up to it = development
REPLAY_HOLDOUT_FROM = 2022          # replay seasons >= this = holdout
MIN_HOLDOUT = 150                   # games needed in the holdout before any verdict beyond NOT READY
MIN_GROUP = 30                      # smallest group whose metrics are interpreted
CAL_ECE_OK = 0.025
CAL_OVERCONF_OK = 0.02
MATERIAL_PP = 5.0
EXTREME_PP = 10.0
EXTREME_SPREAD = {"NFL": 5.0, "CFB": 7.0}
GIANT_PP = 15.0
DIS_BUCKETS = ((0, 2.5), (2.5, 5), (5, 10), (10, 15), (15, 20), (20, 1e9))
LEAN_OK = ("CALIBRATED / RESEARCH READY", "OUTPERFORMING MARKET IN VALIDATED SAMPLE")
RECOMMENDATIONS = ("NOT READY", "PROMISING BUT UNCALIBRATED", "CALIBRATION NEEDED") + LEAN_OK


def base_version(v: str | None) -> str | None:
    return v.split(" ")[0] if v else v


def _read(p: Path, default):
    if not p.exists():
        return default
    if p.suffix == ".gz":
        with gzip.open(p, "rt", encoding="utf-8") as f:
            return json.load(f)
    return json.loads(p.read_text())


def _r(x, n=4):
    return None if x is None else round(x, n)


def outcome(margin: float | None) -> float | None:
    if margin is None:
        return None
    return 1.0 if margin > 0 else 0.0 if margin < 0 else 0.5


# ======================================================================== sample groups

NFL_GROUPS = ((1, 1, "1 game"), (2, 2, "2 games"), (3, 3, "3 games"), (4, 5, "4-5 games"), (6, 8, "6-8 games"),
              (9, 12, "9-12 games"), (13, 99, "13+ games"))
NHL_GROUPS = ((0, 0, "0 GP this season"), (1, 3, "1-3 GP"), (4, 10, "4-10 GP"), (11, 20, "11-20 GP"),
              (21, 40, "21-40 GP"), (41, 999, "41+ GP"))
MLB_GROUPS = ((0, 99, "starter <100 BF"), (100, 299, "starter 100-299 BF"), (300, 599, "starter 300-599 BF"),
              (600, 99999, "starter 600+ BF"))


def group_of(league: str, n: float | None) -> str | None:
    if n is None:
        return None
    for lo, hi, label in {"NFL": NFL_GROUPS, "NHL": NHL_GROUPS, "MLB": MLB_GROUPS}[league]:
        if lo <= n <= hi:
            return label
    return None


def _inv_weight(w: float | None, k: float) -> float | None:
    """Recover the sample size from a regression weight w = n / (n + k)."""
    if w is None:
        return None
    if w >= 1:
        return 999
    return round(k * w / (1 - w))


def live_sample(league: str, m: dict) -> dict:
    w = m.get("regression_weights") or {}
    if league == "NFL":
        n = min((_inv_weight(w.get(s), models.NFL["k_games"]) for s in ("home", "away") if w.get(s) is not None),
                default=None)
        return {"metric": "fewer team games this season (home/away)", "value": n, "group": group_of("NFL", n),
                "small": n is not None and n < 3}
    if league == "NHL":
        n = min((_inv_weight(w.get(s), models.NHL["k_gp"]) for s in ("home", "away") if w.get(s) is not None),
                default=None)
        return {"metric": "fewer team GP this season (5v5 xG)", "value": n, "group": group_of("NHL", n),
                "small": n is not None and n < 10}
    n = min((_inv_weight(w.get(s), models.MLB["k_bf"]) for s in ("home_starter", "away_starter")
             if w.get(s) is not None), default=None)
    return {"metric": "fewer batters faced by a starter this season", "value": n, "group": group_of("MLB", n),
            "small": n is not None and n < 100}


# ======================================================================== rows: live archive

def _market_point(card: dict, kind: str, built: dt.datetime, start: dt.datetime) -> tuple[dict | None, str | None]:
    cur = (((card.get("market") or {}).get(kind)) or {}).get("current")
    if not cur:
        return None, None
    t = T.parse(cur.get("t"))
    if not t:
        return None, f"{kind}: observation time missing"
    if t >= start:
        return None, f"{kind}: observation at/after start excluded"
    if t > built:
        return None, f"{kind}: observation after the model build excluded"
    return cur, None


def _live_context(card: dict, m: dict) -> tuple[dict, dict]:
    av = card.get("availability") or {}
    st = av.get("starters") or {}
    flags = card.get("flags") or []
    fids = {f["id"] for f in flags}
    ctx = {
        "starter_unconfirmed": any((x or {}).get("kind") != "confirmed" for x in st.values()) if st else True,
        "key_injury_unresolved": any(r.get("key_player") and r.get("status_norm") in ("questionable", "doubtful", "day-to-day")
                                     for r in av.get("injuries") or []),
        "data_alert": any(f["severity"] == "alert" and f.get("category", "data") == "data" for f in flags
                          if f["id"].startswith("data.")),
        "stale_odds": "data.stale_odds" in fids,
        "small_sample": any("Small sample" in w for w in m.get("sample_warnings") or []),
    }
    lg = card["league"]
    sit = card.get("situations") or {}
    if lg == "NHL":
        ctx["back_to_back"] = bool(sit.get("home_b2b") or sit.get("away_b2b"))
        ctx["goalie_unconfirmed"] = ctx["starter_unconfirmed"]
        ctx["tiny_goalie_sample"] = any(i.startswith("nhl.goalie_sample_") for i in fids)
    if lg == "MLB":
        ctx["opener_or_short_start"] = any(i.startswith("mlb.short_start_") for i in fids)
        ctx["bullpen_taxed"] = bool(sit.get("home_pen_taxed") or sit.get("away_pen_taxed"))
        ctx["bullpen_unclassified"] = any(i.startswith("data.bullpen_unclassified_") for i in fids)
        ctx["lineup_not_posted"] = any(i.startswith("data.no_lineup_") for i in fids)
        ctx["small_starter_sample"] = any("starts" in w and "Small sample" in w for w in m.get("sample_warnings") or [])
    issues = [k for k in ("key_injury_unresolved", "data_alert", "stale_odds", "small_sample") if ctx.get(k)]
    return ctx, {"state": "degraded" if issues else "clean", "issues": issues}


def live_rows(root: Path = RESEARCH) -> tuple[list[dict], list[dict]]:
    rows, excluded = [], []
    for p in sorted((root / "archive").glob("*.json.gz")):
        date = p.name[:10]
        arch = _read(p, {})
        results = _read(root / "results" / f"{date}.json", {})
        for key, c in sorted(arch.items()):
            lg = c.get("league")

            def ex(reason, key=key, lg=lg, date=date):
                excluded.append({"key": key, "league": lg, "date": date, "source": "live_archive", "reason": reason})
            if lg not in MODELED:
                ex("no Boolin model for this league"); continue
            m = c.get("model") or {}
            if not m.get("available"):
                ex(f"model unavailable: {m.get('reason')}"); continue
            start, built = T.parse(c.get("start_et")), T.parse(c.get("built_at"))
            if not start or not built:
                ex("start or build time missing: point in time can't be shown"); continue
            if built >= start:
                ex("pregame card built at/after the start"); continue
            res = results.get(key)
            if not res or res.get("home_margin") is None:
                ex("no confirmed final score on file"); continue
            notes = ["model and market from the last pregame build; final score used only as the outcome"]
            mkt = {}
            for kind in ("moneyline", "spread", "total"):
                pt, why = _market_point(c, kind, built, start)
                mkt[kind] = pt
                if why:
                    notes.append(why)
            ml, sp, tot = mkt["moneyline"] or {}, mkt["spread"] or {}, mkt["total"] or {}
            ctx, dq = _live_context(c, m)
            rows.append({
                "key": key, "source": "live_archive", "evidence": "live pregame research card",
                "league": lg, "date": date, "season": int(date[:4]), "week": None,
                "away": c["game"]["away"]["abbr"], "home": c["game"]["home"]["abbr"],
                "game_start": c["start_et"], "model_built_at": c["built_at"],
                "market_observed_at": ml.get("t") or sp.get("t") or tot.get("t"),
                "validation_cutoff": c["start_et"], "point_in_time_safe": True, "pit_notes": notes,
                "model_version": base_version(m.get("version")), "model_version_full": m.get("version"),
                "model_confidence": m.get("confidence"),
                "model_home_p": m.get("home_win_p"), "model_margin": m.get("proj_margin_home"),
                "model_total": m.get("proj_total"),
                "market_kind": "pregame (no-vig, at model build)", "market_ref": ml.get("ref"),
                "market_home_p": ml.get("fair_home"), "market_spread_home": sp.get("home_line"),
                "market_total": tot.get("line"),
                "actual_home_win": outcome(res["home_margin"]), "actual_margin": res["home_margin"],
                "actual_total": res.get("total"),
                "sample": live_sample(lg, m), "data_quality": dq, "context": ctx,
                "provenance": {"card": f"data/research/archive/{date}.json.gz#{key}",
                               "result": f"data/research/results/{date}.json#{key}", "result_source": res.get("source")},
                "split": "development" if date <= MODEL_FROZEN_AT else "holdout",
            })
    return rows, excluded


# ======================================================================== rows: NFL replay

def _start_et(day: str, hhmm: str | None) -> dt.datetime:
    d = dt.date.fromisoformat(day)
    h, mi = (int(x) for x in (hhmm or "13:00").split(":")[:2])
    return dt.datetime(d.year, d.month, d.day, h, mi, tzinfo=T.ET)


def devig_ml(home_ml, away_ml) -> float | None:
    from .markets import devig
    if home_ml is None or away_ml is None:
        return None
    d = devig([home_ml, away_ml])
    return round(d[0], 4) if d else None


def replay_model(g: dict, P: dict | None = None) -> dict:
    """The live NFL model on the replayed inputs (same function; current parameters unless P is given)."""
    roof = g.get("roof")
    outdoors = roof in ("outdoors", "open") if roof else None
    h, a = g["inputs"]["home"], g["inputs"]["away"]
    return models.nfl({"home": {"off_epa": h["off_epa"], "def_epa": h["def_epa"], "games": h["games"], "rest": g.get("home_rest")},
                       "away": {"off_epa": a["off_epa"], "def_epa": a["def_epa"], "games": a["games"], "rest": g.get("away_rest")},
                       "outdoors": outdoors, "wind_mph": None, "neutral": False}, P)


def replay_rows(path: Path = REPLAY) -> tuple[list[dict], list[dict], dict]:
    data = _read(path, {})
    rows, excluded = [], []
    for season, games in sorted((data.get("seasons") or {}).items()):
        for g in games:
            key = f"{g['gameday']}-NFL-{g['away']}-{g['home']}"

            def ex(reason, key=key, g=g):
                excluded.append({"key": key, "league": "NFL", "date": g["gameday"], "source": "nfl_replay", "reason": reason})
            h, a = (g.get("inputs") or {}).get("home"), (g.get("inputs") or {}).get("away")
            if not h or not a:
                ex("no current-season plays before this week for a team (the live model has no projection either)"); continue
            if max(h.get("weeks_used") or [0]) >= g["week"] or max(a.get("weeks_used") or [0]) >= g["week"]:
                ex("inputs include the game's own week: not point in time"); continue
            oc = g.get("outcome") or {}
            if oc.get("home_margin") is None:
                ex("no final score"); continue
            m = replay_model(g)
            m01 = replay_model(g, models.NFL_V01)
            if not m.get("available"):
                ex(f"model unavailable: {m.get('reason')}"); continue
            mc = g.get("market_close") or {}
            start = _start_et(g["gameday"], g.get("gametime"))
            qb, qbp = g.get("qb_at_kickoff") or {}, g.get("qb_prev_game") or {}
            qb_change = {s: bool(qbp.get(s) and qb.get(s) and qb[s] != qbp[s]) for s in ("home", "away")}
            nmin = min(h["games"], a["games"])
            rest_gap = (abs(g["home_rest"] - g["away_rest"]) if g.get("home_rest") is not None
                        and g.get("away_rest") is not None else None)
            ctx = {"early_season": nmin <= 3, "qb_change": qb_change["home"] or qb_change["away"],
                   "neutral_site": g.get("location") == "Neutral", "rest_gap_3plus": bool(rest_gap and rest_gap >= 3),
                   "short_rest": bool(min(g.get("home_rest") or 7, g.get("away_rest") or 7) <= 5),
                   "extreme_epa": any(abs(x[k]) >= 0.20 for x in (h, a) for k in ("off_epa", "def_epa")),
                   "playoff": (g.get("game_type") or "REG") != "REG", "division_game": bool(g.get("div_game")),
                   "outdoors": (g.get("roof") in ("outdoors", "open"))}
            rows.append({
                "key": key, "source": "nfl_replay",
                "evidence": "historical replay: prior-week play-by-play inputs, closing market",
                "league": "NFL", "date": g["gameday"], "season": int(season), "week": g["week"],
                "away": g["away"], "home": g["home"], "game_start": T.iso_et(start),
                "model_built_at": f"before week {g['week']} (plays from weeks {min(h['weeks_used'])}-{max(h['weeks_used'] + a['weeks_used'])})",
                "market_observed_at": "close (at kickoff)", "validation_cutoff": T.iso_et(start),
                "point_in_time_safe": True,
                "pit_notes": ["team EPA from weeks before the game only", "rest is the scheduled rest",
                              "wind/temp excluded (nflverse records game-time weather)",
                              "market is the closing line: a closing-market comparison",
                              "starting QB is known at kickoff and is used only to tag QB changes"],
                "model_version": base_version(m["version"]), "model_version_full": m["version"],
                "model_confidence": m["confidence"],
                "model_home_p": m["home_win_p"], "model_margin": m["proj_margin_home"], "model_total": m["proj_total"],
                "previous_version": {"model_version": base_version(models.NFL_V01["version"]),
                                     "model_home_p": m01.get("home_win_p"), "model_margin": m01.get("proj_margin_home")},
                "market_kind": "closing (nflverse games.csv, no-vig moneyline)", "market_ref": "nflverse close",
                "market_home_p": devig_ml(mc.get("home_moneyline"), mc.get("away_moneyline")),
                "market_spread_home": -mc["spread_line"] if mc.get("spread_line") is not None else None,
                "market_total": mc.get("total_line"),
                "actual_home_win": outcome(oc["home_margin"]), "actual_margin": oc["home_margin"],
                "actual_total": oc.get("total"),
                "sample": {"metric": "fewer team games this season (home/away)", "value": nmin,
                           "group": group_of("NFL", nmin), "small": nmin < 3},
                "data_quality": {"state": "clean", "issues": [],
                                 "note": "replay has no injury or data-health record; QB changes are tagged instead"},
                "context": ctx,
                "provenance": {"inputs": f"data/reference/nfl_replay_inputs.json.gz#seasons.{season}[{g.get('game_id')}]",
                               "game_id": g.get("game_id"), "replay_version": data.get("version")},
                "split": "holdout" if int(season) >= REPLAY_HOLDOUT_FROM else "development",
            })
    meta = {"version": data.get("version"), "built_at_et": data.get("built_at_et"), "status": data.get("status"),
            "present": bool(data)}
    return rows, excluded, meta


# ======================================================================== analysis

def _pairs(rows, field="model_home_p"):
    return [(r[field], r["actual_home_win"]) for r in rows if r.get(field) is not None and r.get("actual_home_win") is not None]


def _with_market(rows):
    return [r for r in rows if r.get("market_home_p") is not None and r.get("model_home_p") is not None]


def diff_pp(r) -> float | None:
    if r.get("market_home_p") is None or r.get("model_home_p") is None:
        return None
    return round((r["model_home_p"] - r["market_home_p"]) * 100, 2)


def spread_gap(r) -> float | None:
    if r.get("market_spread_home") is None or r.get("model_margin") is None or r["league"] in ("MLB", "NHL"):
        return None
    return round(-r["model_margin"] - r["market_spread_home"], 2)


def calibration(rows) -> dict:
    out = {"boolin": V.prob_metrics(_pairs(rows))}
    wm = _with_market(rows)
    if wm:
        out["market_same_games"] = V.prob_metrics(_pairs(wm, "market_home_p"))
        out["boolin_same_games"] = {k: v for k, v in V.prob_metrics(_pairs(wm)).items() if k != "buckets"}
    return out


def market_comparison(rows) -> dict:
    wm = _with_market(rows)
    if not wm:
        return {"n": 0, "note": "no stored market probability for these games"}
    mb = [V.brier(r["model_home_p"], r["actual_home_win"]) for r in wm]
    kb = [V.brier(r["market_home_p"], r["actual_home_win"]) for r in wm]
    ml = [V.log_loss(r["model_home_p"], r["actual_home_win"]) for r in wm]
    kl = [V.log_loss(r["market_home_p"], r["actual_home_win"]) for r in wm]
    bm, km = V.prob_metrics(_pairs(wm)), V.prob_metrics(_pairs(wm, "market_home_p"))
    out = {"n": len(wm),
           "boolin": {k: bm[k] for k in ("brier", "log_loss", "accuracy", "calibration_error", "favorite_overconfidence")},
           "market": {k: km[k] for k in ("brier", "log_loss", "accuracy", "calibration_error", "favorite_overconfidence")},
           "brier_paired": V.paired(mb, kb, "Brier"), "log_loss_paired": V.paired(ml, kl, "log loss")}
    # When Boolin disagrees: does the side it prefers (relative to the market) win more than the market said?
    buckets = []
    for lo, hi in DIS_BUCKETS:
        b = [r for r in wm if lo <= abs(diff_pp(r)) < hi]
        buckets.append(_disagreement_group(b, f"{lo:g}-{hi:g} pp" if hi < 1e8 else f"{lo:g}+ pp"))
    material = [r for r in wm if abs(diff_pp(r)) >= MATERIAL_PP]
    out["by_disagreement"] = buckets
    out["material_disagreements"] = {
        **_disagreement_group(material, f">= {MATERIAL_PP:g} pp"),
        "games": [{"key": r["key"], "date": r["date"], "market_probability": r["market_home_p"],
                   "boolin_probability": r["model_home_p"], "difference_pp": diff_pp(r),
                   "boolin_result": _boolin_side_result(r), "market_result": _market_fav_result(r),
                   "boolin_brier": _r(V.brier(r["model_home_p"], r["actual_home_win"])),
                   "market_brier": _r(V.brier(r["market_home_p"], r["actual_home_win"]))}
                  for r in material][-400:]}
    return out


def _boolin_side(r) -> str:
    return "home" if r["model_home_p"] > r["market_home_p"] else "away"


def _side_y(r, side) -> float:
    return r["actual_home_win"] if side == "home" else 1 - r["actual_home_win"]


def _boolin_side_result(r) -> str:
    y = _side_y(r, _boolin_side(r))
    return "won" if y == 1 else "lost" if y == 0 else "tie"


def _market_fav_result(r) -> str:
    side = "home" if r["market_home_p"] >= 0.5 else "away"
    y = _side_y(r, side)
    return "won" if y == 1 else "lost" if y == 0 else "tie"


def _disagreement_group(rows, label) -> dict:
    if not rows:
        return {"group": label, "n": 0}
    mb = [V.brier(r["model_home_p"], r["actual_home_win"]) for r in rows]
    kb = [V.brier(r["market_home_p"], r["actual_home_win"]) for r in rows]
    side = []
    for r in rows:
        s = _boolin_side(r)
        pm = r["market_home_p"] if s == "home" else 1 - r["market_home_p"]
        side.append((pm, _side_y(r, s)))
    sr = V.side_rate_test(side)
    bp = [(r["model_home_p"] if _boolin_side(r) == "home" else 1 - r["model_home_p"]) for r in rows]
    return {"group": label, "n": len(rows), "boolin_brier": _r(V.mean(mb)), "market_brier": _r(V.mean(kb)),
            "brier_paired": V.paired(mb, kb, "Brier"),
            "boolin_side_actual_rate": sr["actual_rate"], "boolin_side_market_implied": sr["market_implied_rate"],
            "boolin_side_boolin_implied": _r(V.mean(bp)), "boolin_side_z_vs_market": sr["z"],
            "boolin_beat_market_rate": _r(V.mean(1.0 if a < b else 0.0 for a, b in zip(mb, kb)))}


def extreme_reasons(r) -> list[str]:
    out = []
    d = diff_pp(r)
    if d is not None and abs(d) >= EXTREME_PP:
        out.append(f"win probability {d:+.1f} pp")
    g = spread_gap(r)
    cut = EXTREME_SPREAD.get(r["league"])
    if g is not None and cut and abs(g) >= cut:
        out.append(f"spread {g:+.1f} pts")
    return out


def extreme_audit(rows) -> dict:
    ex = [r for r in _with_market(rows) if extreme_reasons(r)]
    groups = {"all extreme disagreements": ex}
    for c in ("high", "medium", "low"):
        groups[f"{c} confidence"] = [r for r in ex if r.get("model_confidence") == c]
    groups["small samples"] = [r for r in ex if (r.get("sample") or {}).get("small")]
    groups["normal samples"] = [r for r in ex if not (r.get("sample") or {}).get("small")]
    groups["injury / player uncertainty"] = [r for r in ex if (r.get("context") or {}).get("key_injury_unresolved")
                                             or (r.get("context") or {}).get("qb_change")]
    groups["clean data"] = [r for r in ex if (r.get("data_quality") or {}).get("state") == "clean"
                            and not (r.get("context") or {}).get("qb_change")]
    if any(r["source"] == "nfl_replay" for r in ex):
        groups["early season (<=3 games)"] = [r for r in ex if (r.get("context") or {}).get("early_season")]
        groups["later season"] = [r for r in ex if not (r.get("context") or {}).get("early_season")]
    out = {"criteria": {"probability_pp": EXTREME_PP, "spread_pts": EXTREME_SPREAD,
                        "note": "NHL puck lines and MLB run lines are fixed at +/-1.5, so only win probability is used there."},
           "groups": [_extreme_group(v, k) for k, v in groups.items()],
           "games": [{"key": r["key"], "date": r["date"], "reasons": extreme_reasons(r),
                      "boolin_win_p": r["model_home_p"], "market_win_p": r["market_home_p"],
                      "boolin_pick_won": _pick_result(r) == "won", "market_favorite_won": _market_fav_result(r) == "won",
                      "boolin_beat_market": V.brier(r["model_home_p"], r["actual_home_win"]) < V.brier(r["market_home_p"], r["actual_home_win"]),
                      "model_confidence": r.get("model_confidence"), "sample": (r.get("sample") or {}).get("value"),
                      "unresolved_injuries": (r.get("context") or {}).get("key_injury_unresolved"),
                      "qb_change": (r.get("context") or {}).get("qb_change"),
                      "starter_unconfirmed": (r.get("context") or {}).get("starter_unconfirmed"),
                      "data_health": (r.get("data_quality") or {}).get("state"), "model_version": r.get("model_version")}
                     for r in ex][-300:]}
    out["hypotheses"] = hypotheses(rows, ex)
    return out


def _pick_result(r) -> str:
    side = "home" if r["model_home_p"] >= 0.5 else "away"
    y = _side_y(r, side)
    return "won" if y == 1 else "lost" if y == 0 else "tie"


def _extreme_group(rows, label) -> dict:
    g = _disagreement_group(rows, label)
    if rows:
        g["boolin_pick_win_rate"] = _r(V.mean(1.0 if _pick_result(r) == "won" else 0.5 if _pick_result(r) == "tie" else 0.0 for r in rows))
        g["market_favorite_win_rate"] = _r(V.mean(1.0 if _market_fav_result(r) == "won" else 0.5 if _market_fav_result(r) == "tie" else 0.0 for r in rows))
        g["interpretable"] = len(rows) >= MIN_GROUP
    return g


def hypotheses(rows, ex) -> list[dict]:
    """What the giant disagreements are, as far as the data can say. Each verdict is supported /
    not supported / contradicted / inconclusive / untestable, with the numbers behind it."""
    wm = _with_market(rows)
    out = []
    all_g = _disagreement_group(ex, "extremes")
    t = (all_g.get("brier_paired") or {}).get("t")
    if len(ex) < MIN_GROUP or t is None:
        v = "inconclusive"
    else:
        v = "supported" if t <= -2 else "contradicted" if t >= 2 else "not supported"
    out.append({"hypothesis": "useful contrarian signal", "verdict": v,
                "evidence": f"n={len(ex)}; Boolin side won {all_g.get('boolin_side_actual_rate')} vs market-implied "
                            f"{all_g.get('boolin_side_market_implied')} (Boolin said {all_g.get('boolin_side_boolin_implied')}); "
                            f"{(all_g.get('brier_paired') or {}).get('verdict')}"})

    def over_rep(pred, name, testable=True, why=None):
        if not testable:
            return {"hypothesis": name, "verdict": "untestable", "evidence": why}
        a = [r for r in wm if pred(r)]
        share_all = len(a) / len(wm) if wm else 0
        e = [r for r in ex if pred(r)]
        share_ex = len(e) / len(ex) if ex else 0
        rate_in, rate_out = (len(e) / len(a) if a else None), ((len(ex) - len(e)) / (len(wm) - len(a)) if len(wm) > len(a) else None)
        g = _disagreement_group(e, name)
        tt = (g.get("brier_paired") or {}).get("t")
        ratio = (rate_in / rate_out) if rate_in is not None and rate_out else None
        if len(e) < 10 or ratio is None:
            v = "inconclusive"
        elif ratio >= 1.5 and (tt is None or tt > 0):
            v = "supported"
        else:
            v = "not supported"
        return {"hypothesis": name, "verdict": v,
                "evidence": f"{len(e)} of {len(ex)} extremes ({share_ex:.0%}) vs {share_all:.0%} of all games; "
                            f"extreme rate {_pct(rate_in)} in this context vs {_pct(rate_out)} outside "
                            f"(x{ratio:.1f})" if ratio else f"{len(e)} of {len(ex)} extremes; not enough games to compare"}

    replay = any(r["source"] == "nfl_replay" for r in wm)
    live = any(r["source"] == "live_archive" for r in wm)
    out.append(over_rep(lambda r: (r.get("sample") or {}).get("small") or (r.get("context") or {}).get("early_season"),
                        "early-season / small-sample instability"))
    out.append(over_rep(lambda r: (r.get("context") or {}).get("data_alert") or (r.get("context") or {}).get("stale_odds"),
                        "stale or incomplete data", testable=live,
                        why="the replay has no data-health record; only live cards carry stale/missing-data flags"))
    out.append(over_rep(lambda r: (r.get("context") or {}).get("qb_change") or (r.get("context") or {}).get("key_injury_unresolved"),
                        "missing player-level information",
                        testable=replay or live))
    # systematic bias: which way do the extremes lean?
    if len(ex) >= MIN_GROUP:
        dog = V.mean(1.0 if ((r["market_home_p"] < 0.5) == (_boolin_side(r) == "home")) else 0.0 for r in ex)
        home = V.mean(1.0 if _boolin_side(r) == "home" else 0.0 for r in ex)
        lean = max(dog, 1 - dog, home, 1 - home)
        v = "supported" if lean >= 0.7 and (t is None or t > 0) else "not supported"
        out.append({"hypothesis": "systematic model bias", "verdict": v,
                    "evidence": f"Boolin's side is the market underdog in {dog:.0%} of extremes and the home team in "
                                f"{home:.0%}; overall home calibration-in-the-large "
                                f"{_r(V.mean(r['model_home_p'] - r['actual_home_win'] for r in wm), 3)} "
                                "(mean predicted minus actual home win)"})
    else:
        out.append({"hypothesis": "systematic model bias", "verdict": "inconclusive", "evidence": f"n={len(ex)} extremes"})
    return out


def _pct(x):
    return "n/a" if x is None else f"{x:.0%}"


def confidence_validation(rows) -> dict:
    levels = []
    for c in ("high", "medium", "low"):
        rs = [r for r in rows if r.get("model_confidence") == c]
        pm = V.prob_metrics(_pairs(rs))
        mc = market_comparison(rs) if rs else {"n": 0}
        levels.append({"confidence": c, "n": len(rs), "brier": pm.get("brier"), "log_loss": pm.get("log_loss"),
                       "accuracy": pm.get("accuracy"), "calibration_error": pm.get("calibration_error"),
                       "favorite_overconfidence": pm.get("favorite_overconfidence"),
                       "avg_margin_error": (V.err_metrics([r["actual_margin"] - r["model_margin"] for r in rs
                                                           if r.get("model_margin") is not None]) or {}).get("mae"),
                       "market_brier_same_games": (mc.get("market") or {}).get("brier"),
                       "vs_market": (mc.get("brier_paired") or {}).get("verdict"),
                       "interpretable": len(rs) >= MIN_GROUP})
    ok = [l for l in levels if l["interpretable"]]
    if len(ok) < 2:
        verdict, validated = "Not enough games in at least two confidence levels to test the labels.", None
    else:
        ordered = all(a["brier"] <= b["brier"] for a, b in zip(ok, ok[1:]))
        # compare to the market so harder/easier games don't masquerade as label quality
        gaps = [((l["brier"] or 0) - (l["market_brier_same_games"] or l["brier"] or 0)) for l in ok]
        gap_ordered = all(a <= b for a, b in zip(gaps, gaps[1:]))
        validated = ordered and gap_ordered
        names = " < ".join(l["confidence"] for l in ok)
        verdict = (f"Labels behave as intended: Brier and the gap to the market both improve in the order {names}."
                   if validated else
                   "Labels do NOT line up with realized performance: "
                   + ", ".join(f"{l['confidence']} Brier {l['brier']} (market {l['market_brier_same_games']})" for l in ok)
                   + ". Higher confidence is not reliably better.")
    return {"levels": levels, "validated": validated, "verdict": verdict,
            "note": "Thresholds unchanged (high >= 0.6 regression weight, medium >= 0.35). Not redefined here."}


def sample_validation(rows, league) -> dict:
    groups = {"NFL": NFL_GROUPS, "NHL": NHL_GROUPS, "MLB": MLB_GROUPS}[league]
    out = []
    for _, _, label in groups:
        rs = [r for r in rows if (r.get("sample") or {}).get("group") == label]
        pm = V.prob_metrics(_pairs(rs))
        mc = market_comparison(rs) if rs else {"n": 0}
        sp = V.err_metrics([r["actual_margin"] - r["model_margin"] for r in rs if r.get("model_margin") is not None])
        out.append({"group": label, "n": len(rs), "brier": pm.get("brier"), "log_loss": pm.get("log_loss"),
                    "favorite_overconfidence": pm.get("favorite_overconfidence"),
                    "market_brier_same_games": (mc.get("market") or {}).get("brier"),
                    "boolin_minus_market_brier": (mc.get("brier_paired") or {}).get("mean_diff"),
                    "vs_market": (mc.get("brier_paired") or {}).get("verdict"), "margin_mae": sp.get("mae"),
                    "share_extreme": _r(V.mean(1.0 if extreme_reasons(r) else 0.0 for r in _with_market(rs))) if _with_market(rs) else None,
                    "interpretable": len(rs) >= MIN_GROUP})
    ok = [g for g in out if g["interpretable"] and g["boolin_minus_market_brier"] is not None]
    verdict = "Not enough games per sample group to tell."
    if len(ok) >= 2:
        early, late = ok[0], ok[-1]
        worse = early["boolin_minus_market_brier"] - late["boolin_minus_market_brier"]
        verdict = (f"Model is materially worse relative to the market with small samples ({early['group']}: "
                   f"{early['boolin_minus_market_brier']:+.4f} Brier vs market; {late['group']}: "
                   f"{late['boolin_minus_market_brier']:+.4f})." if worse > 0.01 else
                   f"No material small-sample penalty relative to the market ({early['group']} "
                   f"{early['boolin_minus_market_brier']:+.4f} vs {late['group']} {late['boolin_minus_market_brier']:+.4f}).")
    return {"metric": (rows[0]["sample"]["metric"] if rows else None), "groups": out, "verdict": verdict}


@contextlib.contextmanager
def _nfl_params(**kw):
    old = {k: models.NFL[k] for k in kw}
    models.NFL.update(kw)
    try:
        yield
    finally:
        models.NFL.update(old)


def shrinkage_diagnostic(replay_dev: list[dict], raw_games: dict) -> dict:
    """How log loss on the DEVELOPMENT replay games responds to k_games. Diagnostic only: the live
    parameter (k_games=4) is not changed by this module."""
    if not replay_dev:
        return {"n": 0}
    out = []
    for k in (2, 4, 6, 8, 12, 16):
        with _nfl_params(k_games=k):
            ps = []
            for r in replay_dev:
                g = raw_games.get(r["provenance"]["game_id"])
                if g is None:
                    continue
                m = replay_model(g)
                ps.append((m["home_win_p"], r["actual_home_win"], r["context"]["early_season"], r["market_home_p"]))
        allp = [(p, y) for p, y, _, _ in ps]
        early = [(p, y) for p, y, e, _ in ps if e]
        out.append({"k_games": k, "n": len(allp), "log_loss_all": V.prob_metrics(allp).get("log_loss"),
                    "brier_all": V.prob_metrics(allp).get("brier"),
                    "log_loss_early": V.prob_metrics(early).get("log_loss"), "n_early": len(early),
                    "favorite_overconfidence_early": V.prob_metrics(early).get("favorite_overconfidence")})
    best = min(out, key=lambda x: x["log_loss_early"] or 9)
    return {"sample": "nfl_replay development only (holdout untouched)", "current_k_games": 4, "table": out,
            "reading": (f"Lowest early-season log loss at k_games={best['k_games']} on development games. "
                        "Diagnostic only: nothing was changed; any change must be fit on development and "
                        "confirmed on the holdout."),}


def spread_validation(rows, league) -> dict:
    rs = [r for r in rows if r.get("model_margin") is not None and r.get("actual_margin") is not None]
    out = {"boolin_margin": {**V.err_metrics([r["actual_margin"] - r["model_margin"] for r in rs]),
                             "directional_accuracy": V.directional([r["model_margin"] for r in rs], [r["actual_margin"] for r in rs])}}
    if league in ("NHL", "MLB"):
        out["market"] = ("not applicable: puck/run lines are fixed at +/-1.5, so the market's margin estimate "
                         "isn't stored; Boolin's margin is checked against results only")
        return out
    ws = [r for r in rs if r.get("market_spread_home") is not None]
    if not ws:
        out["market"] = "no stored spreads for these games"
        return out
    be = [abs(r["actual_margin"] - r["model_margin"]) for r in ws]
    me = [abs(r["actual_margin"] + r["market_spread_home"]) for r in ws]
    out["same_games"] = {"n": len(ws),
                         "boolin": V.err_metrics([r["actual_margin"] - r["model_margin"] for r in ws]),
                         "market_implied": {**V.err_metrics([r["actual_margin"] + r["market_spread_home"] for r in ws]),
                                            "directional_accuracy": V.directional([-r["market_spread_home"] for r in ws],
                                                                                  [r["actual_margin"] for r in ws])},
                         "abs_error_paired": V.paired(be, me, "margin abs error")}
    ats = []
    for lo, hi in ((1, 2.5), (2.5, 5), (5, 1e9), (1, 1e9)):
        res = []
        for r in ws:
            g = spread_gap(r)
            if g is None or not (lo <= abs(g) < hi):
                continue
            side = "home" if g < 0 else "away"
            cover = r["actual_margin"] + r["market_spread_home"]
            cover = cover if side == "home" else -cover
            res.append(0.5 if cover == 0 else 1.0 if cover > 0 else 0.0)
        dec = [x for x in res if x != 0.5]
        ats.append({"gap": f"{lo:g}-{hi:g} pts" if hi < 1e8 else f"{lo:g}+ pts", "n": len(res), "pushes": len(res) - len(dec),
                    "boolin_side_cover_rate": _r(V.mean(dec)), "interpretable": len(dec) >= MIN_GROUP})
    out["ats_research"] = {"rows": ats, "note": "Research only: how often the side Boolin's line preferred covered the "
                                                "stored market line. Not profit, not edge."}
    return out


def total_validation(rows) -> dict:
    rs = [r for r in rows if r.get("model_total") is not None and r.get("actual_total") is not None]
    out = {"boolin_total": V.err_metrics([r["actual_total"] - r["model_total"] for r in rs])}
    ws = [r for r in rs if r.get("market_total") is not None]
    if ws:
        out["same_games"] = {"n": len(ws), "boolin": V.err_metrics([r["actual_total"] - r["model_total"] for r in ws]),
                             "market_line": V.err_metrics([r["actual_total"] - r["market_total"] for r in ws]),
                             "abs_error_paired": V.paired([abs(r["actual_total"] - r["model_total"]) for r in ws],
                                                          [abs(r["actual_total"] - r["market_total"]) for r in ws],
                                                          "total abs error")}
        side = []
        for r in ws:
            d = r["model_total"] - r["market_total"]
            a = r["actual_total"] - r["market_total"]
            if abs(d) >= 0.5 and a != 0:
                side.append(1.0 if (d > 0) == (a > 0) else 0.0)
        out["directional_accuracy_vs_line"] = {"n": len(side), "rate": _r(V.mean(side)),
                                               "note": "games where Boolin's total was >= 0.5 off the line, pushes left out"}
    out["status"] = "Totals are not validated as an edge; they stay out of the overall model-vs-market label."
    return out


CONTEXTS = {
    "NFL": {"early_season": "early season (<= 3 games)", "extreme_epa": "extreme EPA input (|EPA/play| >= 0.20)",
            "rest_gap_3plus": "rest gap >= 3 days", "short_rest": "a team on <= 5 days rest",
            "qb_change": "starting QB differs from the team's previous game", "neutral_site": "neutral site (model assumes home field)",
            "playoff": "playoff game", "division_game": "division game", "giant_disagreement": "giant disagreement (>= 15 pp)",
            "key_injury_unresolved": "unresolved key-player injury (live)", "stale_odds": "stale odds (live)",
            "small_sample": "small-sample warning (live)"},
    "MLB": {"starter_unconfirmed": "starter not confirmed", "opener_or_short_start": "opener / bulk-relief signal",
            "bullpen_taxed": "bullpen taxed (120+ pitches, 3 days)", "bullpen_unclassified": "bullpen split unknown",
            "lineup_not_posted": "lineup not posted", "small_starter_sample": "starter < 5 starts",
            "giant_disagreement": "giant disagreement (>= 15 pp)"},
    "NHL": {"goalie_unconfirmed": "goalie not confirmed", "tiny_goalie_sample": "goalie < 5 GP this season",
            "small_sample": "team 5v5 sample leans on last season", "back_to_back": "back-to-back",
            "giant_disagreement": "giant disagreement (>= 15 pp)"},
}
UNTESTABLE = {
    "NFL": {"wind": "Historical wind in nflverse is recorded game weather (postgame), and live NFL cards have no "
                    "pregame wind: not testable point in time.",
            "unresolved QB status (pregame)": "Historical weekly injury reports aren't replayed; QB changes known at "
                                              "kickoff are tested instead."},
    "MLB": {"short-term K/outs distributions": "Not a model input in baseline-0.1 (prop context only)."},
    "NHL": {},
}


def failure_modes(rows, league) -> dict:
    for r in rows:
        d = diff_pp(r)
        r.setdefault("context", {})["giant_disagreement"] = bool(d is not None and abs(d) >= GIANT_PP)
    found = []
    for tag, label in CONTEXTS[league].items():
        seg = [r for r in rows if (r.get("context") or {}).get(tag)]
        rest = [r for r in rows if not (r.get("context") or {}).get(tag)]
        if not seg:
            continue
        item = {"context": label, "tag": tag, "n": len(seg), "n_rest": len(rest),
                "boolin_brier": V.prob_metrics(_pairs(seg)).get("brier"),
                "boolin_brier_rest": V.prob_metrics(_pairs(rest)).get("brier"),
                "margin_mae": V.err_metrics([r["actual_margin"] - r["model_margin"] for r in seg if r.get("model_margin") is not None]).get("mae"),
                "margin_mae_rest": V.err_metrics([r["actual_margin"] - r["model_margin"] for r in rest if r.get("model_margin") is not None]).get("mae")}
        sm, rm = _with_market(seg), _with_market(rest)
        if sm and rm:
            gs = [V.brier(r["model_home_p"], r["actual_home_win"]) - V.brier(r["market_home_p"], r["actual_home_win"]) for r in sm]
            gr = [V.brier(r["model_home_p"], r["actual_home_win"]) - V.brier(r["market_home_p"], r["actual_home_win"]) for r in rm]
            ses, ser = V.se(gs), V.se(gr)
            z = ((V.mean(gs) - V.mean(gr)) / ((ses ** 2 + ser ** 2) ** 0.5)) if ses and ser else None
            item.update({"boolin_minus_market_brier": _r(V.mean(gs), 5), "boolin_minus_market_brier_rest": _r(V.mean(gr), 5),
                         "excess_vs_rest": _r(V.mean(gs) - V.mean(gr), 5), "z": _r(z, 2)})
        item["material"] = bool(item.get("z") is not None and item["z"] >= 2 and len(seg) >= MIN_GROUP)
        item["interpretable"] = len(seg) >= MIN_GROUP
        found.append(item)
    found.sort(key=lambda x: (not x["interpretable"], -(x.get("z") if x.get("z") is not None else -99)))
    return {"ranked": found, "untestable": UNTESTABLE[league],
            "method": ("Each context is compared with the rest of the league's games on Boolin-minus-market Brier "
                       "(so harder games don't count against the model). material = worse by z >= 2 with n >= 30.")}


# ======================================================================== scorecard

def scorecard_entry(league: str, primary: list[dict], primary_label: str, extra: dict) -> dict:
    hold = [r for r in primary if r["split"] == "holdout"]
    pm = V.prob_metrics(_pairs(hold))
    mc = market_comparison(hold) if hold else {"n": 0}
    sp = spread_validation(hold, league) if hold else {}
    conf = confidence_validation(hold) if hold else {"validated": None}
    n = len(hold)
    reasons = []
    cal_ok = (n >= 300 and pm.get("calibration_error") is not None and pm["calibration_error"] <= CAL_ECE_OK
              and abs(pm.get("favorite_overconfidence") or 0) <= CAL_OVERCONF_OK)
    t = (mc.get("brier_paired") or {}).get("t")
    if n < MIN_HOLDOUT:
        rec = "NOT READY"
        reasons.append(f"holdout sample {n} < {MIN_HOLDOUT}: we don't know yet")
        vstat = "insufficient sample"
    elif (pm.get("brier") or 1) >= 0.25:
        rec, vstat = "NOT READY", "validated: no skill"
        reasons.append(f"holdout Brier {pm.get('brier')} is no better than a coin flip (0.25)")
    elif cal_ok:
        vstat = "validated"
        if t is not None and t <= -2:
            rec = "OUTPERFORMING MARKET IN VALIDATED SAMPLE"
        else:
            rec = "CALIBRATED / RESEARCH READY"
            reasons.append(f"holdout vs market: {(mc.get('brier_paired') or {}).get('verdict')}")
    else:
        vstat = "validated: calibration off"
        reasons.append(f"holdout calibration error {pm.get('calibration_error')}, favourite overconfidence "
                       f"{pm.get('favorite_overconfidence')} (needs <= {CAL_ECE_OK} / +-{CAL_OVERCONF_OK} with n >= 300)")
        if t is not None and t >= 2:
            rec = "CALIBRATION NEEDED"
            reasons.append(f"worse than the market in the holdout ({(mc.get('brier_paired') or {}).get('verdict')})")
        else:
            rec = "PROMISING BUT UNCALIBRATED"
            reasons.append(f"holdout vs market: {(mc.get('brier_paired') or {}).get('verdict')}")
    cal_status = ("uncalibrated (insufficient sample)" if n < MIN_HOLDOUT else
                  "calibrated in holdout" if cal_ok else "needs_calibration")
    behind_market = t is not None and t >= 2
    if rec in LEAN_OK and behind_market:
        reasons.append("calibrated, but the market is better in the holdout: disagreement isn't backed, so no leans")
    return {"model_version": base_version(models.VERSIONS.get(league, models.VERSION)), "validation_version": VALIDATION_VERSION,
            "data_cutoff": max((r["date"] for r in primary), default=None),
            "primary_evidence": primary_label, "sample_size": n,
            "development_size": len([r for r in primary if r["split"] == "development"]),
            "brier": pm.get("brier"), "market_brier": (mc.get("market") or {}).get("brier"),
            "log_loss": pm.get("log_loss"), "market_log_loss": (mc.get("market") or {}).get("log_loss"),
            "accuracy": pm.get("accuracy"), "calibration_error": pm.get("calibration_error"),
            "favorite_overconfidence": pm.get("favorite_overconfidence"),
            "market_comparison": (mc.get("brier_paired") or {}).get("verdict"),
            "spread_mae": ((sp.get("boolin_margin") or {}).get("mae")),
            "market_spread_mae": (((sp.get("same_games") or {}).get("market_implied") or {}).get("mae")),
            "confidence_validated": conf.get("validated"), "calibration_status": cal_status,
            "validation_status": vstat, "recommendation": rec, "reasons": reasons,
            "lean_allowed": rec in LEAN_OK and not behind_market, **extra}


# ======================================================================== orchestration

def _filter(rows, league, since, until):
    return [r for r in rows if (not league or r["league"] == league) and (not since or r["date"] >= since)
            and (not until or r["date"] <= until)]


def run(root: Path = RESEARCH, replay_path: Path = REPLAY, out: Path | None = None, league: str | None = None,
        since: str | None = None, until: str | None = None, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(T.UTC)
    live, ex_live = live_rows(root)
    replay, ex_replay, replay_meta = replay_rows(replay_path)
    rows = sorted(_filter(live + replay, league, since, until), key=lambda r: (r["game_start"] or "", r["key"], r["source"]))
    excluded = _filter(ex_live + ex_replay, league, since, until)
    versions = sorted({r["model_version"] for r in rows if r.get("model_version")})
    if out is None:
        out = (root / "validation") if not (league or since or until) else \
            (root / "validation" / "filtered" / "-".join(x for x in (league, since, until) if x))
    out.mkdir(parents=True, exist_ok=True)

    head = {"validation_version": VALIDATION_VERSION,
            "model_version": {lg: base_version(models.VERSIONS[lg]) for lg in MODELED},
            "model_versions_in_rows": versions, "built_at": T.iso_et(now),
            "data_cutoff": max((r["date"] for r in rows), default=None), "filters": {"league": league, "since": since, "until": until},
            "calibration_status": {"NFL": f"{models.NFL_VERSION}: coefficients fit on replay seasons < {REPLAY_HOLDOUT_FROM} "
                                          "(research/fit_nfl.py); holdout result in model_scorecard.json",
                                   "NHL": "baseline-0.1: never fit to outcomes (uncalibrated)",
                                   "MLB": "baseline-0.1: never fit to outcomes (uncalibrated)"},
            "splits": {"live_archive": f"development = games on or before {MODEL_FROZEN_AT} (parameters last changed); holdout = after",
                       "nfl_replay": f"development = seasons < {REPLAY_HOLDOUT_FROM}; holdout = {REPLAY_HOLDOUT_FROM}+"}}
    if len(versions) > 1:
        head["warning"] = (f"rows span model versions {versions}: each table is per league and per source, and rows "
                           "from an older version sit in their own '<source> @<version>' slice")

    # one model version per slice: rows from an older version get their own "<source> @<version>" slice
    slices = {}
    for lg in MODELED:
        cur = base_version(models.VERSIONS[lg])
        for src in ("nfl_replay", "live_archive"):
            rs = [r for r in rows if r["league"] == lg and r["source"] == src]
            for v in sorted({r["model_version"] for r in rs}):
                part = [r for r in rs if r["model_version"] == v]
                slices[(lg, src if v == cur else f"{src} @{v}")] = part

    def per_split(fn, *a):
        res = {}
        for (lg, src), rs in slices.items():
            res.setdefault(lg, {})[src] = {sp: fn([r for r in rs if sp == "all" or r["split"] == sp], *a)
                                           for sp in ("development", "holdout", "all")
                                           if sp == "all" or any(r["split"] == sp for r in rs)}
        return res

    def per_split_lg(fn):
        res = {}
        for (lg, src), rs in slices.items():
            res.setdefault(lg, {})[src] = {sp: fn([r for r in rs if sp == "all" or r["split"] == sp], lg)
                                           for sp in ("development", "holdout", "all")
                                           if sp == "all" or any(r["split"] == sp for r in rs)}
        return res

    files = {
        "calibration.json": per_split(calibration),
        "market_comparison.json": per_split(market_comparison),
        "spread_validation.json": per_split_lg(spread_validation),
        "total_validation.json": per_split(total_validation),
        "confidence_validation.json": per_split(confidence_validation),
        "sample_validation.json": per_split_lg(sample_validation),
        "disagreement_validation.json": per_split(extreme_audit),
    }
    # merge failure-mode dicts per league
    fm = {}
    for (lg, src), rs in slices.items():
        fm.setdefault(lg, {})[src] = failure_modes(rs, lg)
    files["failure_modes.json"] = fm

    raw_games = {}
    if replay_path.exists():
        for games in (_read(replay_path, {}).get("seasons") or {}).values():
            for g in games:
                raw_games[g.get("game_id")] = g
    nfl_dev = [r for r in slices.get(("NFL", "nfl_replay"), []) if r["split"] == "development"]
    files["sample_validation.json"].setdefault("NFL", {})["k_games_diagnostic"] = shrinkage_diagnostic(nfl_dev, raw_games)

    # replay fidelity: the same game seen live and in the replay should get (nearly) the same number
    live_nfl = {r["key"]: r for (lg, src), rs in slices.items() if lg == "NFL" and src.startswith("live_archive") for r in rs}
    both = [(live_nfl[r["key"]], r) for r in slices.get(("NFL", "nfl_replay"), []) if r["key"] in live_nfl]

    def same_version_p(live, rep_):
        """The replay's probability under the model version the live card used."""
        if live["model_version"] == rep_["model_version"]:
            return rep_["model_home_p"]
        pv = rep_.get("previous_version") or {}
        return pv.get("model_home_p") if pv.get("model_version") == live["model_version"] else None
    pairs = [(a["model_home_p"], same_version_p(a, b)) for a, b in both]
    pairs = [(x, y) for x, y in pairs if y is not None]
    fidelity = {"n_overlap": len(pairs),
                "identical": sum(1 for x, y in pairs if abs(x - y) < 1e-4),
                "mean_abs_prob_diff": _r(V.mean(abs(x - y) for x, y in pairs)) if pairs else None,
                "max_abs_prob_diff": _r(max((abs(x - y) for x, y in pairs), default=None)),
                "note": "Live card vs replay for the same game, same model version. Gaps mean the live pull saw "
                        "different inputs than the replay (cause not confirmed per game)."}

    score = {}
    for lg in MODELED:
        if league and lg != league:
            continue
        rp, lv = slices.get((lg, "nfl_replay"), []), slices.get((lg, "live_archive"), [])
        lv_all = [r for (l2, src), rs in slices.items() if l2 == lg and src.startswith("live_archive") for r in rs]
        live_info = {"live_archive": {"n": len(lv), "holdout": len([r for r in lv if r["split"] == "holdout"]),
                                      "older_version_rows": len(lv_all) - len(lv),
                                      **({k: v for k, v in market_comparison(lv).items() if k in ("n", "boolin", "market", "brier_paired")} if lv else {})}}
        if lg == "NFL" and rp:
            score[lg] = scorecard_entry(lg, rp, f"nfl_replay holdout ({REPLAY_HOLDOUT_FROM}+), closing-line benchmark",
                                        {**live_info, "replay_fidelity": fidelity})
        else:
            score[lg] = scorecard_entry(lg, lv, f"live_archive holdout (games after {MODEL_FROZEN_AT})",
                                        {**live_info, **({"note": "No replay for this league: only live pregame cards count."}
                                                         if lg != "NFL" else {"note": "NFL replay file not present: live cards only."})})
    files["model_scorecard.json"] = score

    summary = {**head, "rows": len(rows), "excluded": len(excluded),
               "by_source": {f"{lg} {src}": {"rows": len(rs), "development": sum(r["split"] == "development" for r in rs),
                                             "holdout": sum(r["split"] == "holdout" for r in rs),
                                             "model_versions": sorted({r["model_version"] for r in rs}),
                                             "first": rs[0]["date"], "last": rs[-1]["date"]} for (lg, src), rs in slices.items()},
               "excluded_by_reason": _count(excluded),
               "replay": replay_meta, "replay_fidelity": fidelity,
               "headline": {lg: {k: s.get(k) for k in ("sample_size", "brier", "market_brier", "log_loss", "calibration_error",
                                                       "market_comparison", "spread_mae", "confidence_validated",
                                                       "calibration_status", "validation_status", "recommendation")}
                            for lg, s in score.items()}}
    files["summary.json"] = summary
    from pipeline.common import finite
    for name, obj in files.items():
        body = {"validation_version": VALIDATION_VERSION, "model_version": head["model_version"],
                "data_cutoff": head["data_cutoff"], "calibration_status": head["calibration_status"], **obj} \
            if name != "summary.json" else obj
        (out / name).write_text(json.dumps(finite(body), indent=1, ensure_ascii=False, allow_nan=False, default=str))
    with open(out / "games.jsonl", "w") as f:
        for r in rows:
            f.write(json.dumps(finite(r), ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")
    with open(out / "excluded.jsonl", "w") as f:
        for r in excluded:
            f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
    return {"out": str(out), "rows": len(rows), "excluded": len(excluded), "headline": summary["headline"]}


def _count(items) -> dict:
    out: dict = {}
    for x in items:
        k = f"{x['source']}: {x['reason']}"
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


# ======================================================================== status for the research layer

def load_status(root: Path = RESEARCH) -> dict:
    sc = _read(root / "validation" / "model_scorecard.json", {})
    return {lg: v for lg, v in sc.items() if isinstance(v, dict) and lg in MODELED}


def status_for(statuses: dict, league: str) -> dict:
    """What the research layer shows and gates on. No scorecard = not validated."""
    s = statuses.get(league)
    if not s:
        return {"model_version": base_version(models.VERSIONS.get(league, models.VERSION)), "calibration_status": "uncalibrated",
                "validation_status": "not validated (no scorecard)", "recommendation": "NOT READY",
                "lean_allowed": False, "sample_size": 0}
    return {k: s.get(k) for k in ("model_version", "validation_version", "data_cutoff", "calibration_status",
                                  "validation_status", "recommendation", "lean_allowed", "sample_size",
                                  "primary_evidence", "market_comparison")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="research.validate")
    ap.add_argument("--league", choices=MODELED)
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--root", default=str(RESEARCH))
    ap.add_argument("--replay", default=str(REPLAY))
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    r = run(Path(a.root), Path(a.replay), Path(a.out) if a.out else None, a.league, a.since, a.until)
    print(json.dumps(r, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
