"""Canonical odds math. The ONE implementation used by pipeline/ and research/ (no network, no deps).

Conventions
  * American odds only; |a| < 100 is not a valid price (None).
  * `implied` is the RAW implied probability (includes the book's margin). Never call it "fair".
  * `devig` removes the margin PROPORTIONALLY across a complete set of outcomes (2- or 3-way) and
    returns probabilities that sum to 1. Proportional is the standard here, not a claim that every
    book spreads its margin proportionally; other methods can be added behind `method=` later.
  * Anything incomplete, invalid or non-finite yields None. Callers must treat None as "no price",
    never substitute a default.
"""
from __future__ import annotations

import math
import statistics

REF_BOOK = "pinnacle"


def _num(x) -> float | None:
    """Coerce to a finite float, rejecting bool/None/NaN/inf/garbage (numeric strings are accepted)."""
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def implied(american) -> float | None:
    """Raw implied probability (includes vig). None for missing/invalid prices."""
    a = _num(american)
    if a is None or -100 < a < 100:
        return None
    return -a / (-a + 100) if a < 0 else 100 / (a + 100)


def decimal(american) -> float | None:
    a = _num(american)
    if a is None or -100 < a < 100:
        return None
    return 1 + (a / 100 if a > 0 else 100 / -a)


def to_american(p: float | None) -> int | None:
    if p is None or not 0 < p < 1:
        return None
    return -round(100 * p / (1 - p)) if p >= 0.5 else round(100 * (1 - p) / p)


def devig(prices: list) -> list[float] | None:
    """Proportional no-vig probabilities for ONE book's complete market (>= 2 outcomes).
    None if any outcome is missing/invalid or fewer than 2 outcomes were given."""
    if not prices or len(prices) < 2:
        return None
    ps = [implied(p) for p in prices]
    if any(p is None for p in ps):
        return None
    total = sum(ps)  # type: ignore[arg-type]
    return [p / total for p in ps]  # type: ignore[operator]


def overround(prices: list) -> float | None:
    """The book's margin on a complete market: sum of raw implied probabilities minus 1."""
    ps = [implied(p) for p in prices]
    if not ps or any(p is None for p in ps):
        return None
    return sum(ps) - 1  # type: ignore[arg-type]


def fair_from_books(book_prices: dict[str, dict], names: list[str], ref_book: str = REF_BOOK) -> dict:
    """Fair (no-vig) probabilities for one line.

    book_prices: {book: {outcome_name: american}}. Only books quoting EVERY outcome are eligible
    (an incomplete market can't be de-vigged, so it never contributes). Each eligible book is de-vigged
    on its own, then:
      * if `ref_book` (Pinnacle) is eligible and valid, it is the reference;
      * otherwise the fair line is the mean of the eligible books' de-vigged probabilities.
    A book with an invalid price is dropped, not allowed to poison the result.

    Returns {"ref": label|None, "fair": [p per name]|None, "books": [contributing books], "hold": float|None}.
    """
    full = {b: p for b, p in book_prices.items() if all(n in p for n in names)}
    devigged = {b: devig([p[n] for n in names]) for b, p in full.items()}
    devigged = {b: d for b, d in devigged.items() if d}
    if ref_book in devigged:
        return {"ref": ref_book, "fair": devigged[ref_book], "books": [ref_book],
                "hold": overround([full[ref_book][n] for n in names])}
    if devigged:
        books = sorted(devigged)
        fair = [statistics.fmean(devigged[b][i] for b in books) for i in range(len(names))]
        return {"ref": f"consensus ({len(books)} books)", "fair": fair, "books": books, "hold": None}
    return {"ref": None, "fair": None, "books": [], "hold": None}
