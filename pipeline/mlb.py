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
        except Exception:  # noqa: BLE001
            continue
    starts.sort(key=lambda x: x["date"] or "")
    last = starts[-5:]
    out["last5"] = last
    if starts:
        ks = [s["k"] for s in starts if s["k"] is not None]
        outs = [s["outs"] for s in starts if s["outs"] is not None]
        out["k_dist"] = {str(n): round(sum(k >= n for k in ks) / len(ks), 3) for n in range(3, 11)} if ks else {}
        out["outs_dist"] = {str(n): round(sum(o >= n for o in outs) / len(outs), 3)
                            for n in (12, 15, 16, 17, 18, 19, 20)} if outs else {}
        out["starts_counted"] = len(starts)
    return out


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


def bullpen(team_id: int, day: dt.date) -> dict:
    """Reliever pitch counts over the 3 days before `day`."""
    start = (day - dt.timedelta(days=3)).isoformat()
    end = (day - dt.timedelta(days=1)).isoformat()
    try:
        sched = C.get_json(f"{API}/schedule", {"sportId": 1, "teamId": team_id, "startDate": start, "endDate": end})
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}
    usage: dict[str, dict] = {}
    for d in sched.get("dates", []):
        for g in d.get("games", []):
            if (g.get("status") or {}).get("abstractGameState") != "Final":
                continue
            box = C.get_json(f"{API}/game/{g['gamePk']}/boxscore")
            side = "home" if g["teams"]["home"]["team"]["id"] == team_id else "away"
            t = box["teams"][side]
            for i, pid in enumerate(t.get("pitchers", [])):
                if i == 0:
                    continue  # starter
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
    return {"reliever_pitches_last3": total, "likely_limited": tired, "detail": usage}


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
            if pp:
                try:
                    row[side]["probable"] = pitcher(pp["id"], season)
                except Exception as e:  # noqa: BLE001
                    row[side]["probable"] = {"id": pp["id"], "name": pp.get("fullName"), "error": str(e)}
            lu = (g.get("lineups") or {}).get(f"{side}Players")
            row[side]["lineup"] = [p.get("fullName") for p in lu] if lu else None
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
    C.write("mlb", {"date": day.isoformat(), "season": season, "games": games}, day)
    return {"status": "ok", "games": len(games)}
