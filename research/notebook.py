"""Analyst notebook: one JSON per game in data/research/notebooks/<game_key>.json.

The research build only READS notebooks. They're written by the analyst (this CLI, a commit, or
the daily task) and are never overwritten by data pulls, so they persist through and after the game.

    python -m research.notebook show  <game_key>
    python -m research.notebook set   <game_key> --thesis "..." --side home --market spread \
        --support "..." --contra "..." --unknown "..." --decision monitor --trigger "only at -2.5 or better"
    python -m research.notebook position <game_key> --market spread --side home --line -2.5 --price -110
    python -m research.notebook review <game_key> --text "..." --correct yes|no|partly
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from . import timeutil as T

DECISIONS = ("lean", "pass", "monitor", "entry condition")
SIDES = ("home", "away", "over", "under")
MARKETS = ("moneyline", "spread", "total")


def path(root: Path, key: str) -> Path:
    return Path(root) / f"{key}.json"


def load(root: Path, key: str) -> dict | None:
    p = path(root, key)
    return json.loads(p.read_text()) if p.exists() else None


def _now() -> str:
    return T.iso_et(dt.datetime.now(T.UTC))


def save(root: Path, key: str, changes: dict, now: str | None = None) -> dict:
    """Merge `changes` into the notebook. Lists append (deduplicated); every save is logged."""
    now = now or _now()
    nb = load(root, key) or {"key": key, "created_at": now, "thesis": {}, "supporting": [], "contradicting": [],
                             "unknowns": [], "decision": None, "entry_trigger": None, "position": None,
                             "postgame_review": None, "history": []}
    if "decision" in changes and changes["decision"] not in DECISIONS + (None,):
        raise ValueError(f"decision must be one of {DECISIONS}")
    th = changes.get("thesis")
    if th:
        if th.get("side") and th["side"] not in SIDES:
            raise ValueError(f"side must be one of {SIDES}")
        if th.get("market") and th["market"] not in MARKETS:
            raise ValueError(f"market must be one of {MARKETS}")
        nb["thesis"] = {**nb.get("thesis", {}), **{k: v for k, v in th.items() if v is not None}}
    for k in ("supporting", "contradicting", "unknowns"):
        for item in changes.get(k) or []:
            if item not in nb[k]:
                nb[k].append(item)
    for k in ("decision", "entry_trigger", "position", "postgame_review"):
        if k in changes and changes[k] is not None:
            nb[k] = changes[k]
    nb["updated_at"] = now
    nb["history"].append({"t": now, "changed": sorted(k for k, v in changes.items() if v)})
    p = path(root, key)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(nb, indent=1, ensure_ascii=False))
    return nb


def main(argv=None) -> int:
    from .build import RESEARCH
    ap = argparse.ArgumentParser(prog="research.notebook")
    ap.add_argument("cmd", choices=("show", "set", "position", "review"))
    ap.add_argument("key")
    ap.add_argument("--root", default=str(RESEARCH / "notebooks"))
    ap.add_argument("--thesis"); ap.add_argument("--side"); ap.add_argument("--market")
    ap.add_argument("--support", action="append"); ap.add_argument("--contra", action="append")
    ap.add_argument("--unknown", action="append"); ap.add_argument("--decision"); ap.add_argument("--trigger")
    ap.add_argument("--line", type=float); ap.add_argument("--price", type=int); ap.add_argument("--book", default="FanDuel")
    ap.add_argument("--text"); ap.add_argument("--correct", choices=("yes", "no", "partly"))
    a = ap.parse_args(argv)
    root = Path(a.root)
    if a.cmd == "show":
        print(json.dumps(load(root, a.key), indent=1))
        return 0
    if a.cmd == "set":
        nb = save(root, a.key, {"thesis": {"statement": a.thesis, "side": a.side, "market": a.market},
                                "supporting": a.support, "contradicting": a.contra, "unknowns": a.unknown,
                                "decision": a.decision, "entry_trigger": a.trigger})
    elif a.cmd == "position":
        nb = save(root, a.key, {"position": {"market": a.market, "side": a.side, "line": a.line, "price": a.price,
                                             "book": a.book, "taken_at": _now()}})
    else:
        nb = save(root, a.key, {"postgame_review": {"text": a.text, "thesis_correct": a.correct, "at": _now()}})
    print(json.dumps(nb, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
