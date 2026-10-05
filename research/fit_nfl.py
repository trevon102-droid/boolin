"""Fit the NFL baseline coefficients on the development replay, check them on the holdout.

    python -m research.fit_nfl          # writes data/research/validation/nfl_fit.json

Development = replay seasons before 2022, holdout = 2022+ (validate.REPLAY_HOLDOUT_FROM). Only
development games are used to fit. The model form is unchanged (additive in home margin, team
EPA/play regressed n/(n+k)); the fit only replaces the hand-set conversion numbers:

    margin = hfa + off_coef * (off_home - off_away) + def_coef * (def_allowed_away - def_allowed_home)
             + rest_pts_per_day * clip(rest_home - rest_away, +-10)
    total  = base_total + total_coef * (off_home + off_away + def_allowed_home + def_allowed_away)
    win p  = Phi(margin / margin_sd), margin_sd = SD of the development residuals

k_games is chosen by leave-one-season-out log loss on development, but only moved off the current
value when another k is better by more than KEEP_K_TOLERANCE (otherwise the change is noise).
The holdout is scored once, after the fit, and reported next to baseline-0.1 and the market.
The fitted numbers are copied into research/models.py by hand; tests check they match this file.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
from pathlib import Path

from . import models, vmetrics as V
from .validate import REPLAY, REPLAY_HOLDOUT_FROM, RESEARCH, devig_ml, outcome

K_GRID = (2, 3, 4, 6, 8, 12)
KEEP_K_TOLERANCE = 0.001
REST_CLIP = 10


def _games(path: Path) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        data = json.load(f)
    out = []
    for season, games in sorted(data.get("seasons", {}).items()):
        for g in games:
            h, a = (g.get("inputs") or {}).get("home"), (g.get("inputs") or {}).get("away")
            oc = g.get("outcome") or {}
            if not h or not a or oc.get("home_margin") is None or oc.get("total") is None:
                continue
            if max(h["weeks_used"] + a["weeks_used"]) >= g["week"]:      # point in time, as in validate
                continue
            out.append({**g, "season": int(season)})
    return out


def features(g: dict, k: float) -> tuple[list[float], list[float]]:
    h, a = g["inputs"]["home"], g["inputs"]["away"]
    r = lambda x, n: x * n / (n + k)  # noqa: E731
    oh, oa = r(h["off_epa"], h["games"]), r(a["off_epa"], a["games"])
    dh, da = r(h["def_epa"], h["games"]), r(a["def_epa"], a["games"])
    rest = 0.0
    if g.get("home_rest") is not None and g.get("away_rest") is not None:
        rest = max(-REST_CLIP, min(REST_CLIP, g["home_rest"] - g["away_rest"]))
    return [1.0, oh - oa, da - dh, rest], [1.0, oh + oa + dh + da]


def _lstsq(X: list[list[float]], y: list[float]) -> list[float]:
    """Ordinary least squares via the normal equations (small, well-conditioned problem; no numpy)."""
    n = len(X[0])
    A = [[sum(r[i] * r[j] for r in X) for j in range(n)] for i in range(n)]
    b = [sum(r[i] * t for r, t in zip(X, y)) for i in range(n)]
    for c in range(n):                                   # Gauss-Jordan with partial pivoting
        p = max(range(c, n), key=lambda i: abs(A[i][c]))
        A[c], A[p], b[c], b[p] = A[p], A[c], b[p], b[c]
        for i in range(n):
            if i != c and A[c][c]:
                f = A[i][c] / A[c][c]
                A[i] = [x - f * y_ for x, y_ in zip(A[i], A[c])]
                b[i] -= f * b[c]
    return [b[i] / A[i][i] for i in range(n)]


def fit(games: list[dict], k: float) -> dict:
    F = [features(g, k) for g in games]
    ym = [g["outcome"]["home_margin"] for g in games]
    yt = [g["outcome"]["total"] for g in games]
    bm = _lstsq([f[0] for f in F], ym)
    bt = _lstsq([f[1] for f in F], yt)
    res = [y - sum(c * x for c, x in zip(bm, f[0])) for y, f in zip(ym, F)]
    sd = math.sqrt(sum(r * r for r in res) / len(res))
    return {"k_games": k, "hfa": bm[0], "off_coef": bm[1], "def_coef": bm[2], "rest_pts_per_day": bm[3],
            "base_total": bt[0], "total_coef": bt[1], "margin_sd": sd, "n": len(games)}


def predict(params: dict, g: dict) -> tuple[float, float, float]:
    fm, ft = features(g, params["k_games"])
    m = params["hfa"] + params["off_coef"] * fm[1] + params["def_coef"] * fm[2] + params["rest_pts_per_day"] * fm[3]
    t = params["base_total"] + params["total_coef"] * ft[1]
    p = 0.5 * (1 + math.erf(m / params["margin_sd"] / math.sqrt(2)))
    return m, t, p


def loso_log_loss(dev: list[dict], k: float) -> float:
    losses = []
    for s in sorted({g["season"] for g in dev}):
        prm = fit([g for g in dev if g["season"] != s], k)
        for g in (g for g in dev if g["season"] == s):
            _, _, p = predict(prm, g)
            losses.append(V.log_loss(p, outcome(g["outcome"]["home_margin"])))
    return sum(losses) / len(losses)


def _v01(g: dict) -> tuple[float, float, float]:
    h, a = g["inputs"]["home"], g["inputs"]["away"]
    roof = g.get("roof")
    m = models.nfl({"home": {**h, "rest": g.get("home_rest")}, "away": {**a, "rest": g.get("away_rest")},
                    "outdoors": roof in ("outdoors", "open") if roof else None, "wind_mph": None, "neutral": False},
                   models.NFL_V01)
    return m["proj_margin_home"], m["proj_total"], m["home_win_p"]


def score(games: list[dict], pred) -> dict:
    ps, ms, ts, mk = [], [], [], []
    for g in games:
        m, t, p = pred(g)
        y = outcome(g["outcome"]["home_margin"])
        ps.append((p, y))
        ms.append(g["outcome"]["home_margin"] - m)
        ts.append(g["outcome"]["total"] - t)
        mc = g.get("market_close") or {}
        mk.append(devig_ml(mc.get("home_moneyline"), mc.get("away_moneyline")))
    pm = V.prob_metrics(ps)
    pm.pop("buckets", None)
    return {**pm, "margin": V.err_metrics(ms), "total": V.err_metrics(ts), "_pairs": ps, "_market": mk}


def run(replay: Path = REPLAY, out: Path | None = None, current_k: int = 4) -> dict:
    games = _games(replay)
    dev = [g for g in games if g["season"] < REPLAY_HOLDOUT_FROM]
    hold = [g for g in games if g["season"] >= REPLAY_HOLDOUT_FROM]
    cv = {k: round(loso_log_loss(dev, k), 5) for k in K_GRID}
    best = min(cv, key=cv.get)
    k = best if cv[current_k] - cv[best] > KEEP_K_TOLERANCE else current_k
    prm = fit(dev, k)
    prm["rest_cap"] = REST_CLIP * abs(prm["rest_pts_per_day"])
    rounded = {key: (round(v, 4) if key in ("rest_pts_per_day",) else round(v, 2)) for key, v in prm.items()
               if key not in ("n",)}
    rounded["k_games"] = k

    def cand(g):
        return predict({**rounded}, g)
    res = {}
    for name, S in (("development", dev), ("holdout", hold)):
        a, b = score(S, _v01), score(S, cand)
        y = [p[1] for p in a["_pairs"]]
        mk = [(p, t) for p, t in zip(a["_market"], y) if p is not None]
        market = V.prob_metrics(mk)
        market.pop("buckets", None)
        both = [(pa[0], pb[0], m, t) for pa, pb, m, t in zip(a["_pairs"], b["_pairs"], a["_market"], y) if m is not None]
        res[name] = {
            "n": len(S),
            "baseline_0_1": {kk: v for kk, v in a.items() if not kk.startswith("_")},
            "baseline_0_2": {kk: v for kk, v in b.items() if not kk.startswith("_")},
            "market_close": market,
            "0.2_vs_0.1_brier": V.paired([V.brier(p, t) for _, p, _, t in both], [V.brier(p, t) for p, _, _, t in both],
                                         "Brier", ("baseline-0.2", "baseline-0.1")),
            "0.2_vs_0.1_log_loss": V.paired([V.log_loss(p, t) for _, p, _, t in both], [V.log_loss(p, t) for p, _, _, t in both],
                                            "log loss", ("baseline-0.2", "baseline-0.1")),
            "0.2_vs_market_brier": V.paired([V.brier(p, t) for _, p, _, t in both], [V.brier(m, t) for _, _, m, t in both],
                                            "Brier", ("baseline-0.2", "market")),
            "0.2_calibration_buckets": V.buckets(b["_pairs"]),
        }
    body = {"model_version": models.NFL_VERSION, "fit_on": f"nfl_replay seasons < {REPLAY_HOLDOUT_FROM} (development)",
            "checked_on": f"nfl_replay seasons >= {REPLAY_HOLDOUT_FROM} (holdout, scored once after the fit)",
            "form": "margin = hfa + off_coef*dOff + def_coef*dDef + rest_pts_per_day*clip(dRest,+-10); "
                    "p = Phi(margin/margin_sd); total = base_total + total_coef*sumEPA",
            "k_games_cv": {"leave_one_season_out_log_loss": cv, "best": best, "chosen": k,
                           "rule": f"keep k={current_k} unless another k is better by > {KEEP_K_TOLERANCE}"},
            "params": rounded, "n_fit": prm["n"], "results": res,
            "not_fitted": ["wind (no pregame wind history)", "neutral site (the live feed doesn't flag it)",
                           "player availability (not in the inputs)"]}
    out = out or (RESEARCH / "validation" / "nfl_fit.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    from pipeline.common import finite
    out.write_text(json.dumps(finite(body), indent=1, allow_nan=False))
    return body


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="research.fit_nfl")
    ap.add_argument("--replay", default=str(REPLAY))
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    b = run(Path(a.replay), Path(a.out) if a.out else None)
    h = b["results"]["holdout"]
    print(json.dumps({"params": b["params"], "k_games_cv": b["k_games_cv"],
                      "holdout": {"0.1": {k: h["baseline_0_1"][k] for k in ("brier", "log_loss", "calibration_error")},
                                  "0.2": {k: h["baseline_0_2"][k] for k in ("brier", "log_loss", "calibration_error")},
                                  "market": {k: h["market_close"][k] for k in ("brier", "log_loss", "calibration_error")},
                                  "0.2 vs 0.1": h["0.2_vs_0.1_brier"]["verdict"],
                                  "0.2 vs market": h["0.2_vs_market_brier"]["verdict"]}}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
