"""MLB from the official MLB Stats API (free, no key).

Per game: probables with season line + last-5 starts, confirmed lineups when posted,
team K% vs the opposing starter's hand, weather, home-plate ump, and bullpen workload
over the last 3 days (who threw a lot / back-to-back).
"""
from __future__ import annotations

import datetime as dt

from . import common as C

API = "https://statsapi.mlb.com/api/v1"


def _outs(ip: str | float | None) -> int | None:
    if ip in (None, ""):
        return None
    whole, _, frac = str(ip).partition(".")
    return int(whole) * 3 + (int(frac) if frac else 0)


def _pct(num, den) -> float | None:
    try:
        return round(float(num) / float(den), 4) if float(den) else None
    except (TypeError, ValueError):
        return None


def pitcher(pid: int, season: int) -> dict:
    p = C.get_json(f"{API}/people/{pid}")["people"][0]
    out = {"id": pid, "name": p.get("fullName"), "throws": (p.get("pitchHand") or {}).get("code")}
    try:
        splits = C.get_json(f"{API}/people/{pid}/stats",
                            {"stats": "season", "group": "pitching", "season": season})["stats"][0]["splits"]
        s = splits[0]["stat"] if splits else {}
        out["season"] = {
            "gs": s.get("gamesStarted"), "ip": s.get("inningsPitched"), "era": s.get("era"),
            "whip": s.get("whip"), "k": s.get("strikeOuts"), "bb": s.get("baseOnBalls"),
            "bf": s.get("battersFaced"), "k_pct": _pct(s.get("strikeOuts"), s.get("battersFaced")),
            "bb_pct": _pct(s.get("baseOnBalls"), s.get("battersFaced")), "k9": s.get("strikeoutsPer9Inn"),
            "hr9": s.get("homeRunsPer9"),
        }
    except Exception as e:  # noqa: BLE001
        out["season_error"] = str(e)
    starts = []
    for game_type in ("R", "F,D,L,W"):
        try:
            logs = C.get_json(f"{API}/people/{pid}/stats", {
                "stats": "gameLog", "group": "pitching", "season": season, "gameType": game_type,
            })["stats"]
            for sp in (logs[0]["splits"] if logs else []):
                st = sp["stat"]
                if st.get("gamesStarted"):
                    starts.append({
                        "date": sp.get("date"), "opp": (sp.get("opponent") or {}).get("name"),
                        "outs": _outs(st.get("inningsPitched")), "k": st.get("strikeOuts"),
                        "bb": st.get("baseOnBalls"), "h": st.get("hits"), "er": st.get("earnedRuns"),
                        "pitches": st.get("numberOfPitches"),
                    })
        except Exception as e:  # noqa: BLE001
            out[f"gamelog_{game_type.replace(',', '')}_error"] = str(e)
            continue
    starts.sort(key=lambda x: x["date"] or "")
    out["last5"] = starts[-5:]
    out.update(distributions(starts))
    return out


def _dist(vals: list[int], points) -> dict:
    return {str(n): round(sum(v >= n for v in vals) / len(vals), 3) for n in points} if vals else {}


def distributions(starts: list[dict]) -> dict:
    """K and outs hit-rate distributions for the season, last 10 and last 5 starts, with means and
    sample sizes, so the reader can regress the short windows toward the season baseline."""
    if not starts:
        return {"starts_counted": 0, "sample_quality": "none"}
    ks = [s["k"] for s in starts if s.get("k") is not None]
    outs = [s["outs"] for s in starts if s.get("outs") is not None]
    out: dict = {
        "starts_counted": len(starts),
        "sample_quality": C.sample_quality(len(starts), tiny=3, small=8),
        "k_dist": _dist(ks, range(3, 11)),            # season (kept for compatibility)
        "outs_dist": _dist(outs, (12, 15, 16, 17, 18, 19, 20)),
        "k_dist_last10": _dist(ks[-10:], range(3, 11)),
        "outs_dist_last10": _dist(outs[-10:], (12, 15, 16, 17, 18, 19, 20)),
        "k_mean": {"season": _mean(ks), "last10": _mean(ks[-10:]), "last5": _mean(ks[-5:])},
        "outs_mean": {"season": _mean(outs), "last10": _mean(outs[-10:]), "last5": _mean(outs[-5:])},
        "dist_note": "Hit rates are empirical shares of starts, not a projection. Regress last10/last5 toward season.",
    }
    # short-start flag: openers / bulk-relief setups are not marked by the MLB API
    recent = [o for o in outs[-5:]]
    if recent and _mean(recent) is not None and _mean(recent) < 10.5:
        out["short_start_flag"] = (f"Averaged {_mean(recent)} outs over the last {len(recent)} starts: "
                                   "possible opener / bulk-relief usage. Verify the pitching plan.")
    return out


def _mean(vals: list) -> float | None:
    return round(sum(vals) / len(vals), 2) if vals else None


def team_k_vs_hand(team_id: int, season: int) -> dict:
    try:
        splits = C.get_json(f"{API}/teams/{team_id}/stats", {
            "stats": "statSplits", "group": "hitting", "season": season, "sitCodes": "vl,vr",
        })["stats"][0]["splits"]
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}
    out = {}
    for sp in splits:
        code = (sp.get("split") or {}).get("code")
        st = sp.get("stat", {})
        out[{"vl": "vs_LHP", "vr": "vs_RHP"}.get(code, code)] = {
            "k_pct": _pct(st.get("strikeOuts"), st.get("plateAppearances")),
            "bb_pct": _pct(st.get("baseOnBalls"), st.get("plateAppearances")),
            "ops": st.get("ops"), "avg": st.get("avg"), "pa": st.get("plateAppearances"),
        }
    return out


def classify_pitchers(team_box: dict) -> dict:
    """Starter vs relievers for one team's boxscore, from MLB's own credited start
    (stats.pitching.gamesStarted), never from list position. If exactly one pitcher is credited
    with the start the split is confirmed; otherwise it is unknown and nobody is guessed."""
    pids = list(team_box.get("pitchers") or [])
    players = team_box.get("players") or {}
    credited = [pid for pid in pids
                if (((players.get(f"ID{pid}") or {}).get("stats") or {}).get("pitching") or {}).get("gamesStarted")]
    if len(credited) == 1:
        return {"status": "confirmed", "starter": credited[0], "relievers": [p for p in pids if p != credited[0]],
                "source": "MLB boxscore gamesStarted"}
    reason = "no pitcher credited with the start" if not credited else f"{len(credited)} pitchers credited with a start"
    return {"status": "unknown", "starter": None, "relievers": [], "unclassified": pids, "reason": reason,
            "source": "MLB boxscore gamesStarted"}


def bullpen(team_id: int, day: dt.date, fetch=None) -> dict:
    """Reliever pitch counts over the 3 days before `day`. Games whose starter can't be identified
    from the boxscore are left out and listed, and `classification` says how complete the picture is:
    confirmed (every game split), partial, unknown (no game split), none (no games)."""
    fetch = fetch or C.get_json
    start = (day - dt.timedelta(days=3)).isoformat()
    end = (day - dt.timedelta(days=1)).isoformat()
    try:
        sched = fetch(f"{API}/schedule", {"sportId": 1, "teamId": team_id, "startDate": start, "endDate": end})
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "classification": "unknown"}
    usage: dict[str, dict] = {}
    considered, classified, unclassified = 0, 0, []
    for d in sched.get("dates", []):
        for g in d.get("games", []):
            if (g.get("status") or {}).get("abstractGameState") != "Final":
                continue
            considered += 1
            box = fetch(f"{API}/game/{g['gamePk']}/boxscore")
            side = "home" if g["teams"]["home"]["team"]["id"] == team_id else "away"
            t = box["teams"][side]
            split = classify_pitchers(t)
            if split["status"] != "confirmed":
                unclassified.append({"game_pk": g["gamePk"], "date": d["date"], "reason": split["reason"]})
                continue
            classified += 1
            for pid in split["relievers"]:
                pl = t["players"].get(f"ID{pid}", {})
                pitches = ((pl.get("stats") or {}).get("pitching") or {}).get("numberOfPitches") or 0
                name = (pl.get("person") or {}).get("fullName", str(pid))
                u = usage.setdefault(name, {"days": [], "pitches": 0})
                u["days"].append(d["date"])
                u["pitches"] += pitches
    yesterday = (day - dt.timedelta(days=1)).isoformat()
    two_ago = (day - dt.timedelta(days=2)).isoformat()
    for u in usage.values():
        u["back_to_back"] = yesterday in u["days"] and two_ago in u["days"]
    total = sum(u["pitches"] for u in usage.values())
    tired = sorted([n for n, u in usage.items() if u["back_to_back"] or u["pitches"] >= 35])
    status = ("none" if not considered else "confirmed" if classified == considered
              else "unknown" if not classified else "partial")
    return {"reliever_pitches_last3": total, "likely_limited": tired, "detail": usage,
            "classification": status, "games_considered": considered, "games_classified": classified,
            "unclassified_games": unclassified,
            "classification_source": "MLB boxscore gamesStarted (not list order)"}


def run(day: dt.date) -> dict:
    season = day.year
    sched = C.get_json(f"{API}/schedule", {
        "sportId": 1, "date": day.isoformat(),
        "hydrate": "probablePitcher,lineups,weather,officials,team,venue,seriesStatus",
    })
    games_raw = [g for d in sched.get("dates", []) for g in d.get("games", [])]
    if not games_raw:
        C.write("mlb", {"date": day.isoformat(), "games": []}, day)
        return {"status": "ok", "games": 0}
    k_cache: dict[int, dict] = {}
    games = []
    for g in games_raw:
        row: dict = {
            "game_pk": g["gamePk"], "start_et": C.to_et(g.get("gameDate")),
            "status": (g.get("status") or {}).get("detailedState"),
            "game_type": g.get("gameType"), "series": g.get("seriesDescription"),
            "series_game": g.get("seriesGameNumber"),
            "series_status": (g.get("seriesStatus") or {}).get("description"),
            "venue": (g.get("venue") or {}).get("name"),
            "weather": g.get("weather") or None,
            "hp_umpire": next((o["official"]["fullName"] for o in g.get("officials", [])
                               if o.get("officialType") == "Home Plate"), None),
        }
        for side in ("away", "home"):
            t = g["teams"][side]
            team = t["team"]
            row[side] = {
                "id": team["id"], "name": team.get("name"), "abbr": team.get("abbreviation"),
                "record": t.get("leagueRecord"),
            }
            pp = t.get("probablePitcher")
            row[side]["probable_status"] = ("announced (MLB probable; openers/bulk relievers are not flagged)"
                                            if pp else "TBD: no probable announced")
            if pp:
                try:
                    row[side]["probable"] = pitcher(pp["id"], season)
                except Exception as e:  # noqa: BLE001
                    row[side]["probable"] = {"id": pp["id"], "name": pp.get("fullName"), "error": str(e)}
            lu = (g.get("lineups") or {}).get(f"{side}Players")
            row[side]["lineup"] = [p.get("fullName") for p in lu] if lu else None
            row[side]["lineup_status"] = "confirmed" if lu else "not posted yet"
            if team["id"] not in k_cache:
                k_cache[team["id"]] = team_k_vs_hand(team["id"], season)
            row[side]["bats"] = k_cache[team["id"]]
            try:
                row[side]["bullpen"] = bullpen(team["id"], day)
            except Exception as e:  # noqa: BLE001
                row[side]["bullpen"] = {"error": str(e)}
        # K% of each lineup vs the opposing starter's hand, for K props
        for side, opp in (("away", "home"), ("home", "away")):
            hand = ((row[opp].get("probable") or {}).get("throws"))
            key = {"L": "vs_LHP", "R": "vs_RHP"}.get(hand or "")
            row[side]["k_pct_vs_opp_starter_hand"] = (row[side]["bats"].get(key) or {}).get("k_pct") if key else None
        games.append(row)
    C.write("mlb", {"date": day.isoformat(), "season": season, "games": games,
                    "note": ("probable_status / lineup_status say what is confirmed. Start times are MLB's "
                             "(trust these over the odds feed's commence time).")}, day)
    return {"status": "ok", "games": len(games), "components": {"schedule": "ok"}}
