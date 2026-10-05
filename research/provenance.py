"""Every research value says what kind of fact it is, where it came from and when.

kind:
  confirmed  official (posted lineup, final injury designation, confirmed starter)
  reported   from a feed or report, not an official confirmation (weekly injury report, ESPN list)
  projected  expected but not confirmed (probable starter, likely goalie, projected lineup)
  derived    calculated by Boolin (model, fair probability, regressed stat)
  unknown    not available; never silently filled
"""
from __future__ import annotations

from typing import Any

KINDS = ("confirmed", "reported", "projected", "derived", "unknown")


def fact(value: Any, kind: str, source: str | None = None, as_of: str | None = None,
         note: str | None = None, **extra) -> dict:
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}")
    if value is None and kind != "unknown":
        return unknown(note or "value missing from source", source=source, as_of=as_of)
    out = {"value": value, "kind": kind}
    if source:
        out["source"] = source
    if as_of:
        out["as_of"] = as_of
    if note:
        out["note"] = note
    out.update({k: v for k, v in extra.items() if v is not None})
    return out


def unknown(reason: str, source: str | None = None, as_of: str | None = None) -> dict:
    out = {"value": None, "kind": "unknown", "reason": reason}
    if source:
        out["source"] = source
    if as_of:
        out["as_of"] = as_of
    return out


def is_unknown(f: Any) -> bool:
    return not isinstance(f, dict) or f.get("kind") == "unknown" or f.get("value") is None


def val(f: Any, default=None):
    return default if is_unknown(f) else f["value"]
