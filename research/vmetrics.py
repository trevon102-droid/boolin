"""Scoring functions for model validation. Pure, deterministic, no I/O.

Conventions
  p      Boolin's (or the market's) probability that the HOME team wins.
  y      the home outcome: 1 win, 0 loss, 0.5 tie (ties score as half a win everywhere).
  folded the same pair seen from the favourite's side: (max(p, 1-p), outcome for that side).
         Calibration buckets use folded pairs, so "60-65%" means "Boolin made this side a
         60-65% favourite", whichever side that was.
"""
from __future__ import annotations

import math
import statistics

EPS = 1e-6
BUCKETS = ((0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 0.70), (0.70, 0.75), (0.75, 0.80), (0.80, 1.0000001))


def bucket_label(lo: float, hi: float) -> str:
    return f"{round(lo * 100)}%+" if hi > 1 else f"{round(lo * 100)}-{round(hi * 100)}%"


def _clip(p: float) -> float:
    return min(1 - EPS, max(EPS, p))


def brier(p: float, y: float) -> float:
    return (p - y) ** 2


def log_loss(p: float, y: float) -> float:
    p = _clip(p)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def fold(p: float, y: float) -> tuple[float, float]:
    return (p, y) if p >= 0.5 else (1 - p, 1 - y)


def _r(x, n=4):
    return None if x is None else round(x, n)


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


def se(xs) -> float | None:
    xs = list(xs)
    return statistics.stdev(xs) / math.sqrt(len(xs)) if len(xs) > 1 else None


def accuracy(pairs) -> float | None:
    """Share of games where the side given > 50% won. Ties and exact 50% calls are left out."""
    hits = [(p > 0.5) == (y == 1) for p, y in pairs if y != 0.5 and p != 0.5]
    return mean(hits)


def buckets(pairs) -> list[dict]:
    folded = [fold(p, y) for p, y in pairs]
    out = []
    for lo, hi in BUCKETS:
        b = [(p, y) for p, y in folded if lo <= p < hi]
        if not b:
            out.append({"bucket": bucket_label(lo, hi), "n": 0})
            continue
        ap, ar = mean(p for p, _ in b), mean(y for _, y in b)
        out.append({"bucket": bucket_label(lo, hi), "n": len(b), "avg_predicted": _r(ap), "actual_win_rate": _r(ar),
                    "abs_calibration_error": _r(abs(ap - ar)), "brier": _r(mean(brier(p, y) for p, y in b))})
    return out


def calibration_error(pairs) -> float | None:
    """Expected calibration error: bucket-size-weighted |avg predicted - actual| over the folded buckets."""
    bs = [b for b in buckets(pairs) if b["n"]]
    n = sum(b["n"] for b in bs)
    return _r(sum(b["n"] * b["abs_calibration_error"] for b in bs) / n) if n else None


def favorite_overconfidence(pairs) -> float | None:
    """Mean favourite probability minus the favourite's actual win rate. Positive = the favourites it
    names win less often than it says (overconfident); negative = underconfident."""
    folded = [fold(p, y) for p, y in pairs]
    if not folded:
        return None
    return _r(mean(p for p, _ in folded) - mean(y for _, y in folded))


def prob_metrics(pairs) -> dict:
    pairs = [(p, y) for p, y in pairs if p is not None and y is not None]
    if not pairs:
        return {"n": 0}
    return {"n": len(pairs), "brier": _r(mean(brier(p, y) for p, y in pairs)),
            "log_loss": _r(mean(log_loss(p, y) for p, y in pairs)), "accuracy": _r(accuracy(pairs)),
            "mean_predicted_home": _r(mean(p for p, _ in pairs)), "actual_home_win_rate": _r(mean(y for _, y in pairs)),
            "calibration_error": calibration_error(pairs), "favorite_overconfidence": favorite_overconfidence(pairs),
            "buckets": buckets(pairs)}


def paired(a: list[float], b: list[float], what: str = "Brier", names=("Boolin", "market"), t_cut: float = 2.0) -> dict:
    """Paired comparison of per-game losses (lower is better). Verdict only when |t| >= t_cut."""
    d = [x - y for x, y in zip(a, b)]
    if not d:
        return {"n": 0, "verdict": "no games to compare"}
    m, s = mean(d), se(d)
    t = (m / s) if s else None
    if len(d) < 30 or t is None:
        verdict = f"too few games to tell (n={len(d)})"
    elif t <= -t_cut:
        verdict = f"{names[0]} {what} better (t={t:.1f})"
    elif t >= t_cut:
        verdict = f"{names[1]} {what} better (t={t:.1f})"
    else:
        verdict = f"no detectable difference in {what} (t={t:.1f})"
    return {"n": len(d), "mean_diff": _r(m, 5), "se": _r(s, 5), "t": _r(t, 2), "verdict": verdict,
            "note": f"mean of {names[0]} minus {names[1]} per-game {what}; negative favours {names[0]}"}


def side_rate_test(rows: list[tuple[float, float]]) -> dict:
    """rows = (market probability for the side Boolin preferred, outcome for that side).
    Does that side win more often than the market implied? z uses the market's own variance."""
    if not rows:
        return {"n": 0}
    pm = [p for p, _ in rows]
    act = mean(y for _, y in rows)
    var = sum(p * (1 - p) for p in pm)
    z = (sum(y for _, y in rows) - sum(pm)) / math.sqrt(var) if var else None
    return {"n": len(rows), "actual_rate": _r(act), "market_implied_rate": _r(mean(pm)), "z": _r(z, 2)}


def err_metrics(errors: list[float]) -> dict:
    """errors = actual - projected."""
    e = [x for x in errors if x is not None]
    if not e:
        return {"n": 0}
    return {"n": len(e), "mae": _r(mean(abs(x) for x in e), 3), "rmse": _r(math.sqrt(mean(x * x for x in e)), 3),
            "mean_error": _r(mean(e), 3), "median_error": _r(statistics.median(e), 3)}


def directional(proj: list[float], actual: list[float]) -> float | None:
    hits = [(p > 0) == (a > 0) for p, a in zip(proj, actual) if p and a]
    return _r(mean(hits))
