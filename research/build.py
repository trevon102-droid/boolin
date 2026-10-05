"""Build the research layer from the raw pipeline files.

    python -m research.build                 # from data/latest (what the workflow runs)
    python -m research.build --backfill      # replay data/days/* in date order (seeds history)
    python -m research.build --no-results    # skip the postgame results fetch (offline)

Flow: raw files -> market history (append-only) -> research cards -> change timeline -> flags
-> summary / scenarios / comparables -> pregame archive -> results + grades -> board -> HTML view.
Writes only under data/research/. Raw files are read-only here.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import sys
import traceback
from pathlib import Path

from . import SCHEMA_VERSION, board, card as C, changes, comparables, flags, grading, markets, notebook, scenarios, summary
from . import timeutil as T

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RESEARCH = DATA / "research"
NFL_HISTORY = DATA / "reference" / "nfl_games_history.json.gz"
RAW_NAMES = ("manifest", "odds", "nfl", "cfb", "mlb", "nhl", "wnba", "nba", "injuries")
RESULT_DELAY_H = 3.5
RESULT_LOOKBACK_DAYS = 4


def load_raw_latest(base: Path = DATA / "latest") -> dict:
    raw = {}
    for n in RAW_NAMES:
        p = base / f"{n}.json"
        if p.exists():
            try:
                raw[n] = json.loads(p.read_text())
            except ValueError:
                raw[n] = None
    return raw


def load_raw_day(day_dir: Path) -> dict:
    raw = {}
    for n in RAW_NAMES:
        for p in (day_dir / f"{n}.json.gz", day_dir / f"{n}.json"):
            if p.exists():
                opener = gzip.open if p.suffix == ".gz" else open
                with opener(p, "rt", encoding="utf-8") as f:
                    raw[n] = json.load(f)
                break
    return raw


def _read(p: Path, default):
    if p.suffix == ".gz":
        if not p.exists():
            return default
        with gzip.open(p, "rt", encoding="utf-8") as f:
            return json.load(f)
    return json.loads(p.read_text()) if p.exists() else default


ARCHIVE_KEYS = ("schema", "key", "league", "status", "date_et", "start_et", "start_label", "built_at", "game",
                "market", "model", "comparison", "environment", "situations")


def slim(card: dict) -> dict:
    """What postgame grading and comparables need from the last pregame card."""
    out = {k: card.get(k) for k in ARCHIVE_KEYS}
    av = card.get("availability") or {}
    out["availability"] = {"starters": av.get("starters"), "injuries": av.get("injuries")}
    sm = card.get("summary") or {}
    out["summary"] = {"thesis_side": sm.get("thesis_side"), "conclusion": sm.get("conclusion"),
                      "unknowns": sm.get("unknowns")}
    out["flags"] = [{"id": f["id"], "severity": f["severity"], "title": f["title"]} for f in card.get("flags") or []]
    return out


def _archive_path(root: Path, date: str) -> Path:
    return root / "archive" / f"{date}.json.gz"


def _write(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=1, ensure_ascii=False, default=str))


def situations(card: dict) -> dict:
    """Small, stable tags stored with archived cards for comparable-game queries."""
    r = card.get("research") or {}
    out = {}
    for s in ("home", "away"):
        b2b = ((r.get(s) or {}).get("back_to_back") or {}).get("value")
        if b2b is not None:
            out[f"{s}_b2b"] = bool(b2b)
        pen = (r.get(s) or {}).get("bullpen") or {}
        if pen:
            out[f"{s}_pen_taxed"] = (pen.get("pitches_last3") or 0) >= 120
    return out


# ---------------------------------------------------------------- results

def espn_results(league: str, date: str) -> list[dict]:
    """Final scores from ESPN's scoreboard (network)."""
    sys.path.insert(0, str(ROOT))
    from pipeline import espn
    rows = []
    for g in espn.scoreboard(league, dt.date.fromisoformat(date)):
        rows.append({"away": (g.get("away") or {}), "home": (g.get("home") or {}), "completed": g.get("completed"),
                     "away_score": g.get("away_score"), "home_score": g.get("home_score")})
    return rows


def match_result(card: dict, rows: list[dict]) -> dict | None:
    from . import teams
    lg = card["league"]
    a, h = card["game"]["away"], card["game"]["home"]
    for r in rows:
        ra, rh = r["away"] or {}, r["home"] or {}
        same = (teams.espn(lg, ra.get("abbr")) == a["abbr"] and teams.espn(lg, rh.get("abbr")) == h["abbr"]) or \
               (ra.get("name") == a["name"] and rh.get("name") == h["name"])
        if same and r.get("completed") and r.get("home_score") is not None:
            hs, as_ = float(r["home_score"]), float(r["away_score"])
            return {"home_score": hs, "away_score": as_, "home_margin": hs - as_, "total": hs + as_,
                    "source": "ESPN scoreboard", "kind": "confirmed"}
    return None


# ---------------------------------------------------------------- build

def build(raw: dict, now: dt.datetime, root: Path = RESEARCH, results_fetcher=espn_results,
          nfl_history: list[dict] | None = None, render_html: bool = True) -> dict:
    now_iso = T.iso_et(now)
    slate_date = ((raw.get("manifest") or {}).get("slate_date")) or T.date_et(now)
    errors: list[str] = []
    history = markets.MarketHistory(root / "markets")
    timeline = changes.Timeline(root / "timeline")
    prev_cards = {c["key"]: c for c in _read(root / "latest" / "cards.json", {}).get("cards", [])}
    if nfl_history is None:
        nfl_history = comparables.load_nfl_history(NFL_HISTORY)
    graded_archive = _graded_archive(root)

    games = C.enumerate_games(raw, slate_date)
    pulled = (raw.get("odds") or {}).get("pulled_at_et")
    new_obs = 0
    for g in games:
        if g.get("odds_event") and pulled:
            meta = {"league": g["league"], "away": g["away"], "home": g["home"],
                    "commence_time": g["odds_event"].get("commence_time"), "event_id": g["odds_event"].get("id")}
            new_obs += history.record(g["key"], g["date"], meta, markets.observation(g["odds_event"], pulled))

    cards, archive_updates = [], {}
    for g in games:
        try:
            c = C.build_card(raw, g, history, now)
            prev = prev_cards.get(g["key"])
            evs = changes.diff(prev, c) if prev and prev.get("built_at") != c["built_at"] else []
            timeline.add(g["date"], evs)
            allev = timeline.for_game(g["date"], g["key"])
            c["recent_changes"] = [e for e in allev if T.parse(e["t"]) and now - T.parse(e["t"]) <= dt.timedelta(hours=24)][-25:]
            c["changes_since_last_pull"] = evs
            c["change_count_total"] = len(allev)
            c["flags"] = flags.build(c)
            nb = notebook.load(root / "notebooks", g["key"])
            c["notebook"] = nb
            c["summary"] = summary.build(c, nb)
            sec_inp = C.SECTIONS.get(g["league"], lambda r, x: None)(raw, g) or {}
            goalie_opts = {s: ((c.get("research") or {}).get(s) or {}).get("goalie_options") or [] for s in ("away", "home")}
            c["scenarios"] = scenarios.build(c, sec_inp.get("model_input"), goalie_opts)
            c["comparables"] = (comparables.nfl(c, nfl_history) if g["league"] == "NFL"
                                else comparables.archive(c, graded_archive))
            if c["status"] == "scheduled":
                c["situations"] = situations(c)
                if g["date"] <= slate_date:  # only the day's games: future games get archived on their day
                    archive_updates.setdefault(g["date"], {})[g["key"]] = slim(c)
            elif prev and prev.get("status") == "scheduled":
                # first build after start: freeze the last pregame card, with the market close attached
                frozen = slim(prev)
                frozen["market"] = c["market"]
                archive_updates.setdefault(g["date"], {})[g["key"]] = frozen
            cards.append(c)
        except Exception as e:  # noqa: BLE001  one bad game never sinks the board
            errors.append(f"{g['key']}: {type(e).__name__}: {e}")
            traceback.print_exc()

    history.save()
    timeline.save()
    for date, upd in archive_updates.items():
        p = _archive_path(root, date)
        arch = _read(p, {})
        arch.update(upd)
        p.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(p, "wt", encoding="utf-8", compresslevel=9) as f:
            json.dump(arch, f, ensure_ascii=False, default=str)

    grades_done, result_errors = grade_finished(root, now, results_fetcher, cards)
    errors += result_errors
    b = board.build(cards, slate_date, now_iso)
    manifest = {
        "schema": SCHEMA_VERSION, "built_at": now_iso, "slate_date": slate_date,
        "status": "ok" if not errors else ("partial" if cards else "error"),
        "cards": len(cards), "new_market_observations": int(new_obs), "grades_written": grades_done,
        "errors": errors[:30],
        "inputs": {n: {"pulled_at_et": (raw.get(n) or {}).get("pulled_at_et") if isinstance(raw.get(n), dict) else None,
                       "status": (((raw.get("manifest") or {}).get("sources") or {}).get(n) or {}).get("status")}
                   for n in RAW_NAMES if n != "manifest"},
        "raw_health": (raw.get("manifest") or {}).get("health"),
    }
    _write(root / "latest" / "cards.json", {"built_at": now_iso, "slate_date": slate_date, "cards": cards})
    _write(root / "latest" / "board.json", b)
    _write(root / "latest" / "manifest.json", manifest)
    if render_html:
        from . import render
        html = render.page(b, cards, manifest)
        (root / "latest" / "index.html").write_text(html)
    return manifest


def _graded_archive(root: Path) -> list[dict]:
    out = []
    for p in sorted((root / "grades").glob("*.json")) if (root / "grades").exists() else []:
        for key, gr in _read(p, {}).items():
            arch = _read(_archive_path(root, p.stem), {}).get(key) or {}
            out.append({"key": key, "league": gr.get("league"), "result": gr.get("result"),
                        "situations": arch.get("situations") or {}})
    return out


def grade_finished(root: Path, now: dt.datetime, fetcher, cards: list[dict]) -> tuple[int, list[str]]:
    """Fetch results for archived games that should be over and grade them once."""
    done, errs = 0, []
    if fetcher is None:
        return 0, []
    by_key = {c["key"]: c for c in cards}
    for back in range(RESULT_LOOKBACK_DAYS + 1):
        date = (T.to_et(now).date() - dt.timedelta(days=back)).isoformat()
        arch = _read(_archive_path(root, date), {})
        if not arch:
            continue
        grades = _read(root / "grades" / f"{date}.json", {})
        results = _read(root / "results" / f"{date}.json", {})
        pending = {k: c for k, c in arch.items() if k not in grades and T.parse(c.get("start_et"))
                   and now - T.parse(c["start_et"]) >= dt.timedelta(hours=RESULT_DELAY_H)}
        if not pending:
            continue
        cache: dict[str, list] = {}
        for k, c in pending.items():
            res = results.get(k)
            if not res:
                lg = c["league"]
                if lg not in cache:
                    try:
                        cache[lg] = fetcher(lg, date)
                    except Exception as e:  # noqa: BLE001
                        cache[lg] = []
                        errs.append(f"results {lg} {date}: {type(e).__name__}: {e}")
                res = match_result(c, cache[lg])
                if res:
                    results[k] = res
            if not res:
                continue
            nb = notebook.load(root / "notebooks", k)
            grades[k] = grading.grade(c, res, nb, by_key.get(k))
            done += 1
            if k in by_key:
                by_key[k]["postgame"] = {"result": res, "grade": grades[k]}
                by_key[k]["status"] = "final"
        _write(root / "results" / f"{date}.json", results)
        _write(root / "grades" / f"{date}.json", grades)
    return done, errs


# ---------------------------------------------------------------- CLI

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="research.build")
    ap.add_argument("--backfill", action="store_true", help="replay data/days/* oldest first")
    ap.add_argument("--no-results", action="store_true", help="don't fetch final scores")
    ap.add_argument("--root", default=str(RESEARCH))
    a = ap.parse_args(argv)
    root = Path(a.root)
    fetcher = None if a.no_results else espn_results
    if a.backfill:
        for day in sorted(p for p in (DATA / "days").iterdir() if p.is_dir()):
            raw = load_raw_day(day)
            t = T.parse((raw.get("odds") or {}).get("pulled_at_et") or (raw.get("manifest") or {}).get("run_at_et"))
            if not t:
                continue
            m = build(raw, t, root, results_fetcher=None, render_html=False)
            print(day.name, m["status"], m["cards"], "cards", m["new_market_observations"], "new obs")
    raw = load_raw_latest()
    m = build(raw, dt.datetime.now(T.UTC), root, results_fetcher=fetcher)
    print(json.dumps({k: m[k] for k in ("status", "cards", "new_market_observations", "grades_written", "errors")}, indent=1))
    return 0 if m["status"] != "error" else 1


if __name__ == "__main__":
    raise SystemExit(main())
