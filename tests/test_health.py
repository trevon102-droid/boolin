"""Offline checks for health reporting, data-quality flags and snapshots (no network).

These use only plain asserts plus a temp data dir, so they run under pytest or `python tests/run_offline.py`.
"""
import datetime as dt
import gzip
import json
import tempfile
from contextlib import contextmanager
from pathlib import Path

from pipeline import build_all, common as C, mlb, odds

DAY = dt.date(2026, 10, 5)


@contextmanager
def temp_data():
    """Point the pipeline's data dir at a temp folder for the duration of a test."""
    old = C.DATA
    with tempfile.TemporaryDirectory() as d:
        C.DATA = Path(d)
        try:
            yield Path(d)
        finally:
            C.DATA = old


class _Src:
    """A fake source module: writes `payload` (if any) and returns `res`, or raises."""

    def __init__(self, name, res=None, payload=None, boom=False):
        self.name, self.res, self.payload, self.boom = name, res, payload, boom

    def run(self, day):
        if self.boom:
            raise RuntimeError("source blew up")
        if self.payload is not None:
            C.write(self.name, self.payload, day)
        return dict(self.res)


@contextmanager
def fake_sources(srcs, env=None):
    import os
    old = build_all.SOURCES
    build_all.SOURCES = {s.name: s for s in srcs}
    saved = {k: os.environ.get(k) for k in ("SOURCES", "RUN_ODDS", "SLATE_DATE")}
    os.environ["SLATE_DATE"] = DAY.isoformat()
    os.environ.pop("SOURCES", None)
    os.environ["RUN_ODDS"] = "1"
    try:
        yield
    finally:
        build_all.SOURCES = old
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _manifest(d: Path) -> dict:
    return json.loads((d / "latest" / "manifest.json").read_text())


# ---------- rollup / components ----------

def test_rollup():
    assert C.rollup({}) == "ok"
    assert C.rollup({"a": "ok", "b": "skipped"}) == "ok"
    assert C.rollup({"a": "ok", "b": "error"}) == "partial"
    assert C.rollup({"a": "error", "b": "error"}) == "error"
    assert C.rollup({"schedule": "error", "epa": "ok"}, core=("schedule",)) == "error"


def test_components_track_failures():
    comps = C.Components()
    assert comps.run("epa", lambda: 1) == 1
    assert comps.run("injuries", lambda: 1 / 0) is None
    comps.run("injuries", lambda: 2)          # a later success doesn't hide the earlier failure
    res = comps.result(games=3)
    assert res["status"] == "partial"
    assert res["components"] == {"epa": "ok", "injuries": "error"}
    assert "ZeroDivisionError" in res["component_errors"]["injuries"]
    assert res["games"] == 3


def test_find_errors_is_recursive():
    payload = {"games": [{"summary_error": "timeout"}, {"ok": 1}], "advanced": {"sp_error": ""},
               "leagues": {"NHL": [{"game": "A @ B", "error": "500"}]}}
    errs = C.find_errors(payload)
    assert len(errs) == 2  # empty error strings don't count
    assert any("games[0].summary_error" in e for e in errs)
    assert any("leagues.NHL[0].error" in e for e in errs)


def test_sample_quality():
    assert C.sample_quality(0) == "none"
    assert C.sample_quality(2) == "tiny"
    assert C.sample_quality(7) == "small"
    assert C.sample_quality(40) == "ok"


# ---------- build_all: partial failures never read as ok ----------

def test_ok_source_with_inner_error_becomes_partial():
    with temp_data() as d, fake_sources([
        _Src("nfl", {"status": "ok", "games": 1},
             {"date": DAY.isoformat(), "games": [1], "team_epa_error": "pbp 404"}),
        _Src("mlb", {"status": "ok", "games": 2}, {"date": DAY.isoformat(), "games": [1, 2]}),
    ]):
        assert build_all.main() == 0
        m = _manifest(d)
        assert m["sources"]["nfl"]["status"] == "partial"
        assert "team_epa_error" in m["sources"]["nfl"]["errors"][0]
        assert m["sources"]["mlb"]["status"] == "ok"
        assert m["health"] == "partial"


def test_one_source_raises_others_still_written():
    with temp_data() as d, fake_sources([
        _Src("nfl", boom=True),
        _Src("mlb", {"status": "ok", "games": 2}, {"date": DAY.isoformat(), "games": [1, 2]}),
    ]):
        assert build_all.main() == 0
        m = _manifest(d)
        assert m["sources"]["nfl"]["status"] == "error"
        assert m["sources"]["nfl"]["latest_file_is_stale"] is True
        assert m["sources"]["mlb"]["status"] == "ok"
        assert m["health"] == "partial"


def test_all_sources_fail_exit_1():
    with temp_data() as d, fake_sources([_Src("nfl", boom=True), _Src("mlb", boom=True)]):
        assert build_all.main() == 1
        assert _manifest(d)["health"] == "error"


def test_stale_latest_file_is_error():
    with temp_data() as d, fake_sources([_Src("nfl", {"status": "ok", "games": 0})]):
        (d / "latest").mkdir(parents=True)
        (d / "latest" / "nfl.json").write_text(json.dumps({"date": "2026-10-04", "games": []}))
        build_all.main()
        src = _manifest(d)["sources"]["nfl"]
        assert src["status"] == "error" and "2026-10-04" in src["error"]


def test_skipped_stays_skipped():
    with temp_data() as d, fake_sources([
        _Src("nba", {"status": "skipped", "reason": "held"}),
        _Src("mlb", {"status": "ok", "games": 1}, {"date": DAY.isoformat(), "games": [1]}),
    ]):
        build_all.main()
        m = _manifest(d)
        assert m["sources"]["nba"]["status"] == "skipped"
        assert m["health"] == "ok"


# ---------- snapshots ----------

def test_write_gzips_daily_snapshot_and_stamps_freshness():
    with temp_data() as d:
        C.write("mlb", {"date": DAY.isoformat(), "games": []}, DAY)
        snap = d / "days" / DAY.isoformat() / "mlb.json.gz"
        assert snap.exists() and not snap.with_suffix("").exists()
        with gzip.open(snap, "rt") as f:
            body = json.load(f)
        assert body["date"] == DAY.isoformat() and "pulled_at_et" in body
        latest = json.loads((d / "latest" / "mlb.json").read_text())
        assert latest == body


# ---------- odds data quality ----------

def _ev(eid, commence, fd=True):
    books = [{"key": "pinnacle", "markets": [{"key": "h2h", "outcomes": [
        {"name": "A", "price": 105}, {"name": "B", "price": -115}]}]}]
    if fd:
        books.append({"key": "fanduel", "markets": [{"key": "h2h", "outcomes": [
            {"name": "A", "price": 104}, {"name": "B", "price": -122}]}]})
    return {"id": eid, "commence_time": commence, "home_team": "B", "away_team": "A", "bookmakers": books}


def test_event_date_is_et_not_utc():
    # 00:15 UTC on Oct 6 is 8:15 PM ET on Oct 5 (Monday Night Football)
    s = odds.summarize_event(_ev("mnf", "2026-10-06T00:15:00Z"))
    assert s["date_et"] == "2026-10-05" and s["start_et"] == "8:15 PM ET"
    assert "flags" not in s


def test_missing_fanduel_flagged():
    s = odds.summarize_event(_ev("x", "2026-10-05T23:00:00Z", fd=False))
    assert s["flags"] and "no fanduel price" in s["flags"][0]


def _prop_rows(price_over, price_under, n):
    rows = []
    for i in range(n):
        rows.append({"market": "player_reception_yds", "player": f"P{i}", "line": 40.5, "outcomes": [
            {"name": "Over", "fanduel": price_over}, {"name": "Under", "fanduel": price_under}]})
    return rows


def test_uniform_prop_prices_flagged():
    q = odds.prop_quality(_prop_rows(-113, -113, 12))
    assert any("placeholder" in f for f in q["flags"])


def test_normal_prop_prices_not_flagged():
    rows = _prop_rows(-113, -113, 6) + _prop_rows(-125, 100, 6)
    assert "flags" not in odds.prop_quality(rows)


def test_props_without_fanduel_flagged():
    q = odds.prop_quality(_prop_rows(None, None, 3))
    assert q["rows_with_fanduel"] == 0 and q["flags"]


# ---------- MLB distributions ----------

def test_mlb_distributions_windows():
    starts = [{"k": k, "outs": o} for k, o in
              [(3, 12)] * 10 + [(9, 18)] * 10]  # first 10 weak, last 10 strong
    d = mlb.distributions(starts)
    assert d["starts_counted"] == 20 and d["sample_quality"] == "ok"
    assert d["k_dist"]["8"] == 0.5 and d["k_dist_last10"]["8"] == 1.0
    assert d["k_mean"]["season"] == 6.0 and d["k_mean"]["last5"] == 9.0
    assert "short_start_flag" not in d


def test_mlb_short_start_flag():
    d = mlb.distributions([{"k": 2, "outs": 6}] * 5)
    assert "short_start_flag" in d and d["sample_quality"] == "small"
    assert mlb.distributions([])["sample_quality"] == "none"
