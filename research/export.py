"""Export the research layer for the Sharp Board's Research tab.

The board page can't fetch from GitHub, so the daily slate task (and the re-checks) copy the research
into the board's own database with the ArtifactData tool:

    python -m research.export --out /tmp/research-export [--version 2026-10-05=3 --version 2026-10-05~c1=3 ...]

writes a handful of documents for collection "research" (summary `<date>` + card chunks `<date>~c1..n`)
and `<out>/batch.json`, a ready ArtifactData `batch` write list. Re-running on the same day: `list` the
`research` collection first and pass each existing doc's version with --version.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from pipeline.common import finite

from .build import RESEARCH

MAX_DOC = 140 * 1024       # stored size runs ~1.5x the compact JSON; the db cap is 256 KiB per document


def trim(card: dict) -> dict:
    """Keep one card well under the per-document cap: drop the bulkiest optional parts first."""
    c = dict(card)
    for key in ("recent_changes", "scenarios", "comparables"):
        if len(json.dumps(c, default=str)) <= MAX_DOC // 2:
            break
        if key == "recent_changes":
            c[key] = (c.get(key) or [])[-10:]
        else:
            c[key] = {"trimmed": True, "note": "removed to fit the board's document size limit"}
    return c


def export(root: Path, out: Path, versions: dict[str, int] | None = None) -> dict:
    """Pack the research into a few documents of collection `research`:
         <date>        summary: board, manifest, card keys, number of chunks
         <date>~c1..n  the cards, packed under the 256 KiB document cap
    Few documents keep re-check rewrites simple: only these ids need an if_version."""
    versions = versions or {}
    latest = root / "latest"
    cards = json.loads((latest / "cards.json").read_text())
    board = json.loads((latest / "board.json").read_text())
    manifest = json.loads((latest / "manifest.json").read_text())
    date = cards["slate_date"]
    out.mkdir(parents=True, exist_ok=True)
    chunks, cur, cur_b = [], [], 0
    for c in cards["cards"]:
        tc = finite(trim(c))
        sz = len(json.dumps(tc, default=str))
        if cur and cur_b + sz > MAX_DOC:
            chunks.append(cur); cur, cur_b = [], 0
        cur.append(tc); cur_b += sz
    if cur:
        chunks.append(cur)
    docs = {date: {"date": date, "kind": "summary", "built_at": cards["built_at"], "board": board,
                   "manifest": manifest, "keys": [c["key"] for c in cards["cards"]], "chunks": len(chunks)}}
    for i, ch in enumerate(chunks, 1):
        docs[f"{date}~c{i}"] = {"date": date, "kind": "cards", "part": i, "built_at": cards["built_at"], "cards": ch}
    writes = []
    for doc_id, body in docs.items():
        p = out / f"{doc_id}.json"
        p.write_text(json.dumps(finite(body), default=str, allow_nan=False))
        w = {"op": "set", "collection": "research", "doc_id": doc_id, "file_path": str(p)}
        if doc_id in versions:
            w["if_version"] = versions[doc_id]
        writes.append(w)
    (out / "batch.json").write_text(json.dumps(writes, indent=1))
    return {"date": date, "cards": len(cards["cards"]), "docs": list(docs), "batch": str(out / "batch.json"),
            "with_versions": sum(1 for w in writes if "if_version" in w)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="research.export")
    ap.add_argument("--root", default=str(RESEARCH))
    ap.add_argument("--out", required=True)
    ap.add_argument("--version", action="append", default=[], metavar="DOC_ID=N",
                    help="version of an existing research doc (from an ArtifactData get/list), repeatable")
    a = ap.parse_args(argv)
    vers = {k: int(v) for k, _, v in (x.partition("=") for x in a.version)}
    print(json.dumps(export(Path(a.root), Path(a.out), vers)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
