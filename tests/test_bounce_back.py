import sqlite3

from ingest.nba_games import SCHEMA
from scans.bounce_back import classify, scan


def _db(rows):
    con = sqlite3.connect(":memory:")
    con.executescript(SCHEMA)
    con.executemany("INSERT INTO player_games VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return con


def _row(gid, day, pid, name, fgm, fga):
    return (gid, f"2026-11-{day:02d}", "2026-27", "AAA", "BBB", 1, pid, name, 30, 20, fgm, fga, 0, 0, 0, 0, 0, 0, 1)


def _player(pid, name, last_fgm, last_fga, n=6):
    # 6 prior games of 5-10 FG (avg 10 FGA, 50%), then the last game
    rows = [_row(f"{pid}-{i}", i + 1, pid, name, 5, 10) for i in range(n)]
    rows.append(_row(f"{pid}-last", n + 1, pid, name, last_fgm, last_fga))
    return rows


def test_flags_high_volume_cold_game():
    # avg 10 FGA @ 50%. Last: 13 FGA (>= avg+3) at 4/13 = 30.8% (<= 35%) -> bounce back
    assert classify(13, 4, 10.0, 0.50) == "BOUNCE_BACK"


def test_boundaries_are_inclusive():
    # exactly avg+3 FGA, exactly 15 pts below: 20 FGA, avg 17 FGA, avg 50% -> 7/20 = 35%
    assert classify(20, 7, 17.0, 0.50) == "BOUNCE_BACK"


def test_no_flag_when_volume_not_high_enough():
    assert classify(12, 3, 10.0, 0.50) is None  # cold, but only +2 FGA


def test_no_flag_when_not_cold_enough():
    assert classify(14, 6, 10.0, 0.50) is None  # 42.9%, only 7 pts below


def test_hot_game_is_regression_not_bounce_back():
    assert classify(13, 10, 10.0, 0.50) == "REGRESSION"


def test_scan_end_to_end_and_excludes_last_game_from_baseline():
    con = _db(_player("1", "Cold Guy", 4, 13) + _player("2", "Normal Guy", 6, 11))
    res = scan(con, "2026-27")
    assert [(r["player"], r["flag"]) for r in res] == [("Cold Guy", "BOUNCE_BACK")]
    assert res[0]["avg_fga"] == 10.0 and abs(res[0]["avg_pct"] - 0.5) < 1e-9


def test_scan_ignores_players_with_too_few_games():
    con = _db(_player("1", "Rookie", 2, 13, n=3))
    assert scan(con, "2026-27") == []
