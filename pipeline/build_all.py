"""Run every source, never let one failure sink the rest, and write data/latest/manifest.json.

Usage:  python -m pipeline.build_all
Env:    SOURCES=nfl,cfb,mlb,nhl,wnba,nba,injuries,odds   (default: all)
        RUN_ODDS=0 to skip odds on this run (saves API credits)
        SLATE_DATE=YYYY-MM-DD to pull a specific day
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sys
import time
import traceback

from . import common as C
from . import cfb, injuries, mlb, nba, nfl, nhl, odds, wnba

SOURCES = {"nfl": nfl, "cfb": cfb, "mlb": mlb, "nhl": nhl, "wnba": wnba, "nba": nba, "injuries": injuries, "odds": odds}
KEEP_DAYS = 45


def prune(day: dt.date) -> None:
    days = C.DATA / "days"
    if not days.exists():
        return
    for p in days.iterdir():
        try:
            if (day - dt.date.fromisoformat(p.name)).days > KEEP_DAYS:
                shutil.rmtree(p)
        except ValueError:
            continue


def audit(name: str, res: dict, day: dt.date) -> dict:
    """Never let a failed piece hide behind "ok": scan the file the source wrote for error keys and
    check it's today's file. ok -> partial when anything inside failed; error when the file is stale."""
    status = res.get("status")
    if status in (C.ERROR, C.SKIPPED):
        if status == C.ERROR:
            res["latest_file_is_stale"] = True
        return res
    latest = C.DATA / "latest" / f"{name}.json"
    if not latest.exists():
        return res
    try:
        payload = json.loads(latest.read_text())
    except (ValueError, OSError) as e:
        return {**res, "status": C.ERROR, "error": f"unreadable output: {e}"}
    file_date = payload.get("date") if isinstance(payload, dict) else None
    pulled = str(payload.get("pulled_at_et", "")) if isinstance(payload, dict) else ""
    if (file_date and file_date != day.isoformat()) or (not file_date and pulled and not pulled.startswith(day.isoformat())):
        return {**res, "status": C.ERROR, "error": f"latest/{name}.json is for {file_date or pulled[:10]}, not {day}",
                "latest_file_is_stale": True}
    errs = C.find_errors(payload)
    if errs:
        res = {**res, "errors": errs}
        if status == C.OK:
            res["status"] = C.PARTIAL
    if pulled:
        res["pulled_at_et"] = pulled
    return res


def main() -> int:
    day = C.slate_date()
    wanted = [s.strip() for s in os.environ.get("SOURCES", ",".join(SOURCES)).split(",") if s.strip()]
    if os.environ.get("RUN_ODDS", "1") == "0" and "odds" in wanted:
        wanted.remove("odds")
    manifest: dict = {"slate_date": day.isoformat(), "run_at_et": C.now_et().isoformat(timespec="minutes"),
                      "sources": {}}
    for name in wanted:
        t0 = time.time()
        try:
            res = SOURCES[name].run(day)
        except Exception as e:  # noqa: BLE001
            res = {"status": "error", "error": f"{type(e).__name__}: {e}",
                   "trace": traceback.format_exc(limit=3)}
        res["seconds"] = round(time.time() - t0, 1)
        res = audit(name, res, day)
        manifest["sources"][name] = res
        print(f"{name:9s} {res.get('status')}  {res}", flush=True)
    # keep odds status from an earlier run today if this run skipped odds
    prev = C.DATA / "latest" / "manifest.json"
    if "odds" not in wanted and prev.exists():
        try:
            old = json.loads(prev.read_text())
            if old.get("slate_date") == day.isoformat() and "odds" in old.get("sources", {}):
                manifest["sources"]["odds"] = {**old["sources"]["odds"], "from_earlier_run": old.get("run_at_et")}
        except (ValueError, OSError):
            pass
    manifest["health"] = C.rollup({k: v.get("status") for k, v in manifest["sources"].items()
                                   if v.get("status") != C.SKIPPED}) if manifest["sources"] else C.OK
    if any(v.get("status") == C.PARTIAL for v in manifest["sources"].values()) and manifest["health"] == C.OK:
        manifest["health"] = C.PARTIAL
    manifest["status_legend"] = ("ok = every component loaded; partial = data written but some components failed "
                                 "(see components / errors); error = source failed, its latest file is NOT today's; "
                                 "skipped = intentionally not pulled")
    C.write("manifest", manifest, day)
    prune(day)
    failed = [n for n, r in manifest["sources"].items() if r.get("status") == "error"]
    # Exit 0 unless everything failed, so partial data still gets committed.
    return 1 if failed and len(failed) == len(wanted) else 0


if __name__ == "__main__":
    sys.exit(main())
