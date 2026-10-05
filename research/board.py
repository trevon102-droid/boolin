"""Daily research board: rank games by how much research attention they need (what changed,
what's unresolved, where Boolin and the market split, what data is weak), not by bet confidence."""
from __future__ import annotations

SEV_W = {"alert": 3.0, "watch": 1.5, "info": 0.25}
DIS_W = {"significant disagreement": 2.0, "extreme disagreement": 3.0, "mild disagreement": 0.5}
ATTENTION_AT = 3.0


def importance(card: dict) -> tuple[float, list[str]]:
    score, reasons = 0.0, []
    for f in card.get("flags") or []:
        score += SEV_W[f["severity"]]
        if f["severity"] != "info":
            reasons.append(f"{f['title']}: {f['why']}")
    new = [c for c in card.get("changes_since_last_pull") or [] if c["type"] != "source_status"]
    day = [c for c in card.get("recent_changes") or [] if c["type"] != "source_status"]
    score += min(len(new), 4) * 1.0 + min(len(day), 6) * 0.25
    if new:
        reasons.insert(0, f"{len(new)} change(s) since the last pull, latest: {new[-1]['field']} "
                          f"{new[-1]['old']} → {new[-1]['new']}")
    elif day:
        reasons.insert(0, f"{len(day)} change(s) in the last 24 h, latest: {day[-1]['field']} "
                          f"{day[-1]['old']} → {day[-1]['new']}")
    d = (card.get("comparison") or {}).get("overall")
    score += DIS_W.get(d, 0)
    concl = (card.get("summary") or {}).get("conclusion") or {}
    if concl.get("status") == "review required":
        score += 1.0
        reasons.insert(0, "REVIEW REQUIRED: " + "; ".join((concl.get("gate") or {}).get("reasons") or [])
                       + ". Interesting disagreement, not a validated edge.")
    return round(score, 2), reasons


def entry(card: dict) -> dict:
    s, reasons = importance(card)
    fr = card.get("freshness") or {}
    oldest = max((v.get("age_min") or 0 for v in fr.values()), default=None)
    concl = (card.get("summary") or {}).get("conclusion") or {}
    return {"key": card["key"], "league": card["league"], "matchup": card["game"]["home_away"],
            "start": card.get("start_label"), "start_et": card.get("start_et"), "status": card["status"],
            "importance": s, "reasons": reasons[:4], "research_status": concl.get("status"),
            "review_required": concl.get("status") == "review required",
            "validation": (card.get("validation") or {}).get("recommendation"),
            "disagreement": (card.get("comparison") or {}).get("overall"),
            "alerts": sum(1 for f in card.get("flags") or [] if f["severity"] == "alert"),
            "changes": len(card.get("recent_changes") or []),
            "changes_since_last_pull": len(card.get("changes_since_last_pull") or []), "oldest_input_min": oldest}


def build(cards: list[dict], slate_date: str, built_at: str) -> dict:
    today = [c for c in cards if c["date_et"] == slate_date]
    later = [c for c in cards if c["date_et"] > slate_date]
    rows = sorted((entry(c) for c in today), key=lambda e: (-e["importance"], e["start_et"] or ""))
    attention = [r for r in rows if r["importance"] >= ATTENTION_AT or r["alerts"]]
    stable = [r for r in rows if r not in attention]
    for r in stable:
        r["why_stable"] = "No meaningful market or availability change, no strong model/market split, inputs fresh."
    return {"slate_date": slate_date, "built_at": built_at, "games": len(today),
            "attention": attention, "stable": stable,
            "upcoming": sorted((entry(c) for c in later), key=lambda e: (e["start_et"] or "", e["key"]))[:40],
            "review_required": [r["key"] for r in rows if r.get("review_required")],
            "ranking_note": "Ranked by research importance (flags, changes, model/market split), not by bet confidence. "
                            "REVIEW REQUIRED = a strong model/market split the validation evidence doesn't back yet."}
