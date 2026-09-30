"""Odds from The Odds API -> best price per side, sharp (Pinnacle) no-vig fair line, hold.

Needs env ODDS_API_KEY. Without it this step is skipped (status 'skipped').
Credit cost per run = markets (3) x 1 bookmaker group per active sport, so ~3 per sport.
Player props are optional (PROPS=1) and cost markets x events, so keep them off on the free tier.
"""
from __future__ import annotations

import datetime as dt
import os
import statistics
from collections import defaultdict

from . import common as C

API = "https://api.the-odds-api.com/v4"
# <=10 books counts as one "region" for credits. Pinnacle is the sharp reference.
BOOKS = os.environ.get(
    "ODDS_BOOKS",
    "pinnacle,fanduel,draftkings,betmgm,williamhill_us,espnbet,fanatics,betrivers,betonlineag,lowvig",
)
SPORTS = {
    "MLB": "baseball_mlb",
    "WNBA": "basketball_wnba",
    "NHL": "icehockey_nhl",
    "NFL": "americanfootball_nfl",
    "CFB": "americanfootball_ncaaf",
}
PROP_MARKETS = {
    "baseball_mlb": "pitcher_strikeouts,pitcher_outs",
    "basketball_wnba": "player_points,player_assists,player_rebounds",
    "icehockey_nhl": "player_shots_on_goal,player_points",
    "americanfootball_nfl": "player_pass_yds,player_rush_yds,player_reception_yds,player_anytime_td",
    "americanfootball_ncaaf": "player_pass_yds,player_rush_yds,player_reception_yds,player_anytime_td",
}
# How far ahead to look: today's slate for daily sports, the whole week for NFL.
WINDOW_HOURS = {"americanfootball_nfl": 24 * 7, "americanfootball_ncaaf": 24 * 3}


def _dec(american: float) -> float:
    a = float(american)
    return 1 + (a / 100 if a > 0 else 100 / -a)


def _summarize(book_prices: dict[str, dict[str, float]], names: list[str]) -> dict:
    """book_prices: {book: {outcome_name: american}} for ONE line. Returns fair + best per outcome."""
    full = {b: p for b, p in book_prices.items() if all(n in p for n in names)}
    ref, fair = None, None
    if "pinnacle" in full:
        ref, fair = "pinnacle", C.devig([full["pinnacle"][n] for n in names])
    elif full:
        per_book = [C.devig([p[n] for n in names]) for p in full.values()]
        per_book = [x for x in per_book if x]
        if per_book:
            ref = f"consensus ({len(per_book)} books)"
            fair = [statistics.fmean(x[i] for x in per_book) for i in range(len(names))]
    outcomes = []
    for i, n in enumerate(names):
        offers = [(b, p[n]) for b, p in book_prices.items() if n in p]
        best = max(offers, key=lambda x: _dec(x[1]), default=(None, None))
        fp = fair[i] if fair else None
        outcomes.append({
            "name": n,
            "fair_prob": round(fp, 4) if fp else None,
            "fair_odds": C.to_american(fp),
            "best_price": best[1],
            "best_book": best[0],
            "pinnacle": book_prices.get("pinnacle", {}).get(n),
            "edge_at_best_vs_fair": round(fp * _dec(best[1]) - 1, 4) if fp and best[1] else None,
            "books": {b: p[n] for b, p in book_prices.items() if n in p},
        })
    hold = None
    if ref == "pinnacle":
        ps = [C.implied(full["pinnacle"][n]) for n in names]
        hold = round(sum(ps) - 1, 4)
    return {"ref": ref, "pinnacle_hold": hold, "outcomes": outcomes}


def _pick_main_line(by_point: dict[float, dict]) -> float | None:
    if not by_point:
        return None
    for pt, books in by_point.items():
        if "pinnacle" in books and len(books["pinnacle"]) == 2:
            return pt
    return max(by_point, key=lambda pt: len(by_point[pt]))


def summarize_event(ev: dict) -> dict:
    home, away = ev["home_team"], ev["away_team"]
    h2h: dict[str, dict[str, float]] = defaultdict(dict)
    spreads: dict[float, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    totals: dict[float, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    for bk in ev.get("bookmakers", []):
        b = bk["key"]
        for m in bk.get("markets", []):
            for o in m.get("outcomes", []):
                if m["key"] == "h2h":
                    h2h[b][o["name"]] = o["price"]
                elif m["key"] == "spreads" and o.get("point") is not None:
                    key = o["point"] if o["name"] == home else -o["point"]  # keyed by home spread
                    spreads[key][b][o["name"]] = o["price"]
                elif m["key"] == "totals" and o.get("point") is not None:
                    totals[o["point"]][b][o["name"]] = o["price"]
    out = {
        "id": ev["id"],
        "commence_time": ev["commence_time"],
        "start_et": C.to_et(ev["commence_time"]),
        "away": away,
        "home": home,
        "books": sorted({bk["key"] for bk in ev.get("bookmakers", [])}),
        "markets": {},
    }
    if h2h:
        names = [away, home]
        if any("Draw" in p for p in h2h.values()):
            names.append("Draw")
        out["markets"]["moneyline"] = _summarize(h2h, names)
    sp = _pick_main_line(spreads)
    if sp is not None:
        s = _summarize(spreads[sp], [away, home])
        s["home_line"] = sp
        s["away_line"] = -sp
        s["other_lines"] = sorted(spreads.keys())
        out["markets"]["spread"] = s
    tp = _pick_main_line(totals)
    if tp is not None:
        t = _summarize(totals[tp], ["Over", "Under"])
        t["line"] = tp
        t["other_lines"] = sorted(totals.keys())
        out["markets"]["total"] = t
    return out


def summarize_props(ev_odds: dict) -> list[dict]:
    """Group player props by (market, player, point) and summarize each Over/Under pair."""
    grouped: dict[tuple, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    for bk in ev_odds.get("bookmakers", []):
        for m in bk.get("markets", []):
            for o in m.get("outcomes", []):
                key = (m["key"], o.get("description"), o.get("point"))
                grouped[key][bk["key"]][o["name"]] = o["price"]
    rows = []
    for (market, player, point), books in grouped.items():
        names = sorted({n for p in books.values() for n in p})
        if names == ["No", "Yes"]:
            names = ["Yes", "No"]
        elif set(names) == {"Over", "Under"}:
            names = ["Over", "Under"]
        s = _summarize(books, names)
        rows.append({"market": market, "player": player, "line": point, **s})
    return sorted(rows, key=lambda r: (r["market"], r["player"] or "", r["line"] or 0))


def run(day: dt.date) -> dict:
    key = os.environ.get("ODDS_API_KEY")
    if not key:
        return {"status": "skipped", "reason": "ODDS_API_KEY secret not set"}
    sports_live = {s["key"] for s in C.get_json(f"{API}/sports", {"apiKey": key}) if s.get("active")}
    result: dict = {"pulled_at_et": C.now_et().isoformat(timespec="minutes"), "books": BOOKS, "sports": {}}
    remaining = None
    for label, sk in SPORTS.items():
        if sk not in sports_live:
            result["sports"][label] = {"status": "off-season"}
            continue
        r = C.get(f"{API}/sports/{sk}/odds", {
            "apiKey": key, "bookmakers": BOOKS, "markets": "h2h,spreads,totals",
            "oddsFormat": "american", "dateFormat": "iso",
        })
        remaining = r.headers.get("x-requests-remaining", remaining)
        horizon = C.now_et() + dt.timedelta(hours=WINDOW_HOURS.get(sk, 36))
        events = []
        for ev in r.json():
            start = dt.datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
            if start.astimezone(C.ET) <= horizon:
                events.append(summarize_event(ev))
        entry: dict = {"status": "ok", "events": events}
        if os.environ.get("PROPS") == "1" and events:
            # Only games on the slate day that haven't started: each event costs markets x credits.
            cap = int(os.environ.get("PROPS_MAX_EVENTS", "6"))
            now_utc = dt.datetime.now(dt.timezone.utc)
            todays = [ev for ev in events
                      if (t := dt.datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))) > now_utc
                      and t.astimezone(C.ET).date() == day]
            props = {}
            for ev in todays[:cap]:
                pr = C.get(f"{API}/sports/{sk}/events/{ev['id']}/odds", {
                    "apiKey": key, "bookmakers": BOOKS, "markets": PROP_MARKETS[sk], "oddsFormat": "american",
                })
                remaining = pr.headers.get("x-requests-remaining", remaining)
                props[ev["id"]] = {"game": f"{ev['away']} @ {ev['home']}", "start_et": ev["start_et"],
                                   "rows": summarize_props(pr.json())}
            entry["props"] = props
        result["sports"][label] = entry
    result["credits_remaining"] = remaining
    C.write("odds", result, day)
    n = sum(len(v.get("events", [])) for v in result["sports"].values())
    return {"status": "ok", "events": n, "credits_remaining": remaining}
