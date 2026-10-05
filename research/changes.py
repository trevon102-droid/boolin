"""'What changed?' — diff two builds of a card into typed change events, keep an append-only
timeline, and explain model moves from stored contributions only.

Change event:
  {"t", "game_key", "type", "field", "old", "new", "source", "cause", ["direction"], ["detail"]}
type: spread | moneyline | total | price | injury_status | starter | lineup | weather | model | source_status

`cause` is never guessed. For market, injury, weather and source changes it is
"Change detected; cause not confirmed." For model changes it lists the model components that
moved (exact, because the model is additive) and the data updates in the same pull.
"""
from __future__ import annotations

import json
from pathlib import Path

from .provenance import val

NO_CAUSE = "Change detected; cause not confirmed."
MODEL_PROB_MIN = 0.005      # ignore model wiggles under 0.5 pp
MODEL_PTS_MIN = 0.1
CONTRIB_MIN = 0.05


def _ev(card, typ, field, old, new, source, cause=NO_CAUSE, **extra):
    return {"t": card["built_at"], "game_key": card["key"], "type": typ, "field": field, "old": old, "new": new,
            "source": source, "cause": cause, **{k: v for k, v in extra.items() if v is not None}}


def _cur(card, kind):
    return ((card.get("market") or {}).get(kind) or {}).get("current") or {}


def diff(old: dict | None, new: dict) -> list[dict]:
    """Change events between the previous build of a card and this one."""
    if not old:
        return []
    out: list[dict] = []
    home, away = new["game"]["home"]["abbr"], new["game"]["away"]["abbr"]
    src_odds = (new.get("freshness") or {}).get("odds", {}).get("source", "odds feed")
    # ---- market lines
    o, n = _cur(old, "spread"), _cur(new, "spread")
    if o.get("home_line") is not None and n.get("home_line") is not None and o["home_line"] != n["home_line"]:
        out.append(_ev(new, "spread", f"{home} spread", f"{home} {o['home_line']:+g}", f"{home} {n['home_line']:+g}", src_odds))
    o, n = _cur(old, "total"), _cur(new, "total")
    if o.get("line") is not None and n.get("line") is not None and o["line"] != n["line"]:
        out.append(_ev(new, "total", "total", o["line"], n["line"], src_odds))
    o, n = _cur(old, "moneyline"), _cur(new, "moneyline")
    for side, team in (("away", away), ("home", home)):
        a, b = (o.get(side) or {}).get("fanduel"), (n.get(side) or {}).get("fanduel")
        if a is not None and b is not None and a != b:
            out.append(_ev(new, "moneyline", f"{team} ML (FanDuel)", a, b, src_odds))
    if o.get("fair_home") is not None and n.get("fair_home") is not None and abs(n["fair_home"] - o["fair_home"]) >= 0.01:
        out.append(_ev(new, "price", f"{home} no-vig win prob", round(o["fair_home"], 4), round(n["fair_home"], 4), src_odds))
    for kind, sides in (("spread", ("away", "home")), ("total", ("over", "under"))):
        o, n = _cur(old, kind), _cur(new, kind)
        same_line = o.get("home_line", o.get("line")) == n.get("home_line", n.get("line"))
        for s in sides:
            a, b = (o.get(s) or {}).get("fanduel"), (n.get(s) or {}).get("fanduel")
            if same_line and a is not None and b is not None and a != b:
                out.append(_ev(new, "price", f"{kind} {s} (FanDuel)", a, b, src_odds))
    # ---- injuries
    oa, na = old.get("availability") or {}, new.get("availability") or {}
    oi = {(r["team"], r["player"]): r for r in oa.get("injuries") or []}
    ni = {(r["team"], r["player"]): r for r in na.get("injuries") or []}
    from .card import STATUS_RANK
    ow, nw = (oa.get("injury_report") or {}).get("week"), (na.get("injury_report") or {}).get("week")
    compare_players = True
    if ow != nw and (ow or nw):
        # a new weekly report replaces the old one; player-by-player diffs would be noise
        out.append(_ev(new, "injury_report", "injury report", f"week {ow}", f"week {nw}",
                       (na.get("injury_report") or {}).get("source", "injury report")))
        compare_players = False
    for side in ("away", "home"):
        t = new["game"][side]["abbr"]
        had = any(k[0] == t for k in oi)
        has = any(k[0] == t for k in ni)
        if not had and has:
            # the source started covering this team's game (e.g. it moved onto today's slate)
            n = sum(1 for k in ni if k[0] == t)
            out.append(_ev(new, "injury_report", f"{t} injury list", "not covered", f"{n} player(s) listed",
                           next(r["source"] for k, r in ni.items() if k[0] == t)))
            for k in [k for k in ni if k[0] == t]:
                oi[k] = ni[k]  # don't also emit per-player "added" events
    for k in (set(oi) | set(ni)) if compare_players else ():
        a, b = oi.get(k), ni.get(k)
        sa, sb = (a or {}).get("status_norm", "not listed"), (b or {}).get("status_norm", "not listed")
        if sa == sb:
            continue
        ra, rb = STATUS_RANK.get(sa, 0 if sa == "not listed" else 3), STATUS_RANK.get(sb, 0 if sb == "not listed" else 3)
        direction = "downgrade" if rb > ra else "upgrade" if rb < ra else None
        out.append(_ev(new, "injury_status", f"{k[1]} ({k[0]})", (a or {}).get("status", "not listed"),
                       (b or {}).get("status", "not listed"), (b or a or {}).get("source", "injury report"),
                       direction=direction))
    # ---- starters / lineups
    for side in ("away", "home"):
        a = ((old.get("availability") or {}).get("starters") or {}).get(side) or {}
        b = ((new.get("availability") or {}).get("starters") or {}).get(side) or {}
        if (a.get("value"), a.get("kind")) != (b.get("value"), b.get("kind")) and (a or b):
            out.append(_ev(new, "starter", f"{new['game'][side]['abbr']} starter",
                           f"{a.get('value') or 'unknown'} ({a.get('kind') or 'not in data'})",
                           f"{b.get('value') or 'unknown'} ({b.get('kind') or 'not in data'})",
                           b.get("source") or a.get("source") or "starter feed"))
        la = ((old.get("availability") or {}).get("lineups") or {}).get(side) or {}
        lb = ((new.get("availability") or {}).get("lineups") or {}).get(side) or {}
        if la and lb and la.get("kind") != lb.get("kind"):
            out.append(_ev(new, "lineup", f"{new['game'][side]['abbr']} lineup",
                           "posted" if val(la) else "not posted", "posted" if val(lb) else "not posted",
                           lb.get("source") or "lineup feed"))
    # ---- weather
    for f in ("wind_mph", "temp_f", "condition"):
        a, b = val((old.get("environment") or {}).get(f)), val((new.get("environment") or {}).get(f))
        if a != b and not (a is None and b is None):
            out.append(_ev(new, "weather", f.replace("_mph", " (mph)").replace("_f", " (°F)"),
                           a if a is not None else "unknown", b if b is not None else "unknown",
                           ((new.get("environment") or {}).get(f) or {}).get("source", "weather feed")))
    # ---- source health
    for comp, b in (new.get("freshness") or {}).items():
        a = (old.get("freshness") or {}).get(comp) or {}
        if a.get("status") and b.get("status") and a["status"] != b["status"]:
            out.append(_ev(new, "source_status", f"{comp} source", a["status"], b["status"], b.get("source", comp)))
    # ---- model (last, so it can point at the other updates)
    out += model_changes(old, new, [e for e in out])
    return out


def model_changes(old: dict, new: dict, same_pull: list[dict]) -> list[dict]:
    mo, mn = old.get("model") or {}, new.get("model") or {}
    if not (mo.get("available") and mn.get("available")):
        if mo.get("available") != mn.get("available"):
            return [_ev(new, "model", "Boolin model availability", "available" if mo.get("available") else "unavailable",
                        "available" if mn.get("available") else f"unavailable ({mn.get('reason')})", "Boolin model")]
        return []
    out = []
    dp = mn["home_win_p"] - mo["home_win_p"]
    dm = (mn.get("proj_margin_home") or 0) - (mo.get("proj_margin_home") or 0)
    dt_ = (mn.get("proj_total") or 0) - (mo.get("proj_total") or 0)
    if abs(dp) < MODEL_PROB_MIN and abs(dm) < MODEL_PTS_MIN and abs(dt_) < MODEL_PTS_MIN:
        return []
    why = explain(mo, mn, same_pull)
    home = new["game"]["home"]["abbr"]
    if abs(dp) >= MODEL_PROB_MIN:
        out.append(_ev(new, "model", f"Boolin {home} win probability", round(mo["home_win_p"], 4),
                       round(mn["home_win_p"], 4), "Boolin model", cause=why["text"], detail=why))
    if abs(dm) >= MODEL_PTS_MIN:
        out.append(_ev(new, "model", "Boolin projected spread", f"{home} {-mo['proj_margin_home']:+.1f}",
                       f"{home} {-mn['proj_margin_home']:+.1f}", "Boolin model", cause=why["text"], detail=why))
    if abs(dt_) >= MODEL_PTS_MIN and mo.get("proj_total") is not None:
        out.append(_ev(new, "model", "Boolin projected total", mo["proj_total"], mn["proj_total"], "Boolin model",
                       cause=why["text"], detail=why))
    return out


def explain(mo: dict, mn: dict, same_pull: list[dict]) -> dict:
    """Structured 'why' for a model move, built only from stored contributions and stored changes."""
    oc = {c["name"]: c["margin"] for c in mo.get("contributions") or []}
    nc = {c["name"]: c["margin"] for c in mn.get("contributions") or []}
    moved = []
    for name in sorted(set(oc) | set(nc)):
        d = round((nc.get(name) or 0) - (oc.get(name) or 0), 3)
        if abs(d) >= CONTRIB_MIN:
            note = next((c.get("note") for c in mn.get("contributions") or [] if c["name"] == name), None)
            moved.append({"component": name, "margin_change": d, "now": note})
    moved.sort(key=lambda x: -abs(x["margin_change"]))
    inputs = [f"{e['field']}: {e['old']} → {e['new']}" for e in same_pull if e["type"] != "model"]
    head = (f"Boolin moved from {mo['home_win_p'] * 100:.1f}% to {mn['home_win_p'] * 100:.1f}% "
            f"(home margin {mo['proj_margin_home']:+.2f} → {mn['proj_margin_home']:+.2f}).")
    if moved:
        parts = [f"{i}. {m['component']} {m['margin_change']:+.2f}" + (f" ({m['now']})" if m.get("now") else "")
                 for i, m in enumerate(moved, 1)]
        text = head + " Model components that moved: " + "; ".join(parts) + "."
        if inputs:
            text += " Data updates in the same pull: " + "; ".join(inputs) + "."
        else:
            text += " No other tracked field changed in the same pull, so the input change behind it is not identified."
    else:
        text = head + (" Projection changed after these updates, but the stored data does not identify a single "
                       "confirmed cause.")
    return {"text": text, "components": moved, "same_pull_updates": inputs}


class Timeline:
    """data/research/timeline/<game date>.json : {game_key: [events, oldest first]} — append-only."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._cache: dict[str, dict] = {}
        self._dirty: set[str] = set()

    def day(self, date: str) -> dict:
        if date not in self._cache:
            f = self.root / f"{date}.json"
            self._cache[date] = json.loads(f.read_text()) if f.exists() else {}
        return self._cache[date]

    def add(self, date: str, events: list[dict]) -> None:
        if not events:
            return
        d = self.day(date)
        for e in events:
            d.setdefault(e["game_key"], []).append(e)
        self._dirty.add(date)

    def for_game(self, date: str, key: str) -> list[dict]:
        return list(self.day(date).get(key, []))

    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for d in sorted(self._dirty):
            (self.root / f"{d}.json").write_text(json.dumps(self._cache[d], indent=1))
        self._dirty.clear()
