"""Point-in-time NFL replay inputs for model validation (nflverse; built in CI).

For every completed game from 2012 on, the exact inputs the live NFL model would have had the
week of that game: team EPA/play (offense and defense) from that season's play-by-play in weeks
*before* the game, the games count behind it, scheduled rest, roof, and the closing market from
nflverse games.csv. Final scores are stored only as outcomes.

    python -m pipeline.nfl_replay            # past seasons kept from the existing file; current season rebuilt
    python -m pipeline.nfl_replay --force    # rebuild every season

What is deliberately NOT stored as a model input:
  - wind / temp: nflverse fills them from the game itself (postgame weather). The live model
    sees no wind for NFL games either, because nflverse leaves it blank until after the game.
  - injuries: historical weekly reports aren't replayed. Starting QBs are stored as known at
    kickoff (game-day inactives), used only to tag QB changes for failure-mode analysis.

Output: data/reference/nfl_replay_inputs.json.gz
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import sys

import pandas as pd

from . import common as C

GAMES = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
PBP = "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{s}.parquet"
PBP_COLS = ["season_type", "week", "posteam", "defteam", "epa", "pass", "rush"]
OUT = C.DATA / "reference" / "nfl_replay_inputs.json.gz"
FROM = 2012
VERSION = "nfl-replay-1"
# Franchise moves: games.csv and play-by-play can spell a team differently across eras.
FRANCHISE = {"OAK": "LV", "SD": "LAC", "STL": "LA", "LAR": "LA"}


def _fr(t):
    return FRANCHISE.get(t, t)


def _num(v, nd=None):
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return None
    return round(float(v), nd) if nd is not None else float(v)


def team_week_table(pbp: pd.DataFrame) -> dict:
    """{team: {week: [off_sum, off_n, def_sum, def_n]}} from one season's play-by-play, using the
    same play filter as pipeline.nfl.team_epa (REG + POST, pass or rush, EPA present)."""
    df = pbp[pbp["season_type"].isin(["REG", "POST"])]
    plays = df[((df["pass"] == 1) | (df["rush"] == 1)) & df["epa"].notna()]
    out: dict = {}
    for (team, wk), g in plays.groupby(["posteam", "week"]):
        out.setdefault(_fr(team), {}).setdefault(int(wk), [0.0, 0, 0.0, 0])
        rec = out[_fr(team)][int(wk)]
        rec[0] += float(g["epa"].sum()); rec[1] += int(len(g))
    for (team, wk), g in plays.groupby(["defteam", "week"]):
        out.setdefault(_fr(team), {}).setdefault(int(wk), [0.0, 0, 0.0, 0])
        rec = out[_fr(team)][int(wk)]
        rec[2] += float(g["epa"].sum()); rec[3] += int(len(g))
    return out


def inputs_before(table: dict, team: str, week: int) -> dict | None:
    """Team EPA through the weeks strictly before `week` (point in time). None = no plays yet."""
    weeks = {w: r for w, r in (table.get(_fr(team)) or {}).items() if w < week}
    off_n = sum(r[1] for r in weeks.values())
    def_n = sum(r[3] for r in weeks.values())
    if not off_n or not def_n:
        return None
    return {"off_epa": round(sum(r[0] for r in weeks.values()) / off_n, 3),
            "def_epa": round(sum(r[2] for r in weeks.values()) / def_n, 3),
            "games": sum(1 for r in weeks.values() if r[1] > 0),
            "weeks_used": sorted(w for w, r in weeks.items() if r[1] > 0), "plays": off_n}


def season_rows(games: pd.DataFrame, pbp: pd.DataFrame, season: int) -> list[dict]:
    g = games[(games["season"] == season) & games["result"].notna()].sort_values(["week", "gameday", "gametime"])
    table = team_week_table(pbp)
    last_qb: dict[str, str] = {}
    rows = []
    for r in g.to_dict("records"):
        wk = int(r["week"])
        home, away = r["home_team"], r["away_team"]
        row = {"season": season, "week": wk, "game_id": r.get("game_id"), "game_type": r.get("game_type"),
               "gameday": str(r["gameday"])[:10], "gametime": r.get("gametime") if isinstance(r.get("gametime"), str) else None,
               "away": away, "home": home, "location": r.get("location") if isinstance(r.get("location"), str) else None,
               "roof": r.get("roof") if isinstance(r.get("roof"), str) else None, "div_game": _num(r.get("div_game")),
               "home_rest": _num(r.get("home_rest")), "away_rest": _num(r.get("away_rest")),
               "inputs": {"home": inputs_before(table, home, wk), "away": inputs_before(table, away, wk)},
               "qb_at_kickoff": {"home": r.get("home_qb_name") if isinstance(r.get("home_qb_name"), str) else None,
                                 "away": r.get("away_qb_name") if isinstance(r.get("away_qb_name"), str) else None},
               "qb_prev_game": {"home": last_qb.get(_fr(home)), "away": last_qb.get(_fr(away))},
               "market_close": {"spread_line": _num(r.get("spread_line")), "total_line": _num(r.get("total_line")),
                                "home_moneyline": _num(r.get("home_moneyline")),
                                "away_moneyline": _num(r.get("away_moneyline"))},
               "outcome": {"home_margin": _num(r.get("result")), "total": _num(r.get("total"))}}
        for side, team in (("home", home), ("away", away)):
            if row["qb_at_kickoff"][side]:
                last_qb[_fr(team)] = row["qb_at_kickoff"][side]
        rows.append(row)
    return rows


def load_existing(path=OUT) -> dict:
    if not path.exists():
        return {}
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def build(force: bool = False, start: int = FROM) -> dict:
    games = pd.read_csv(io.StringIO(C.get(GAMES, timeout=60).text))
    current = int(games.loc[games["result"].notna(), "season"].max())
    old = {} if force else load_existing()
    by_season = {int(k): v for k, v in (old.get("seasons") or {}).items()} if old.get("version") == VERSION else {}
    status = {}
    for s in range(start, current + 1):
        if s in by_season and s != current:
            status[s] = "kept"
            continue
        try:
            pbp = pd.read_parquet(io.BytesIO(C.get(PBP.format(s=s), timeout=180).content), columns=PBP_COLS)
            by_season[s] = season_rows(games, pbp, s)
            status[s] = f"built {len(by_season[s])} games"
        except Exception as e:  # noqa: BLE001  keep what we had for that season
            status[s] = f"error: {type(e).__name__}: {e}"
        print(s, status[s], flush=True)
    payload = {"version": VERSION, "built_at_et": C.now_et().isoformat(timespec="minutes"),
               "source": "nflverse games.csv + play-by-play",
               "note": ("inputs.* use only plays from weeks before the game (point in time). market_close is the "
                        "closing line from games.csv (spread_line > 0 = home favored). outcome is the final score, "
                        "used only to grade. Wind/temp are excluded (postgame weather)."),
               "status": {str(k): v for k, v in status.items()},
               "seasons": {str(k): v for k, v in sorted(by_season.items())}}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(C.finite(payload), separators=(",", ":"), allow_nan=False)
    with gzip.open(OUT, "wt", encoding="utf-8", compresslevel=9) as f:
        f.write(body)
    return {"seasons": len(by_season), "games": sum(len(v) for v in by_season.values()),
            "errors": [k for k, v in status.items() if str(v).startswith("error")]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pipeline.nfl_replay")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--from", dest="start", type=int, default=FROM)
    a = ap.parse_args(argv)
    r = build(a.force, a.start)
    print(json.dumps(r))
    return 1 if r["errors"] and not r["games"] else 0


if __name__ == "__main__":
    sys.exit(main())
