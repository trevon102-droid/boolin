"""Research flags: situations worth an analyst's attention, each with the reason it fired."""
from __future__ import annotations

SEVERITY = {"info": 0, "watch": 1, "alert": 2}

# thresholds
SPREAD_MOVE = {"NFL": 1.5, "CFB": 2.5, "NHL": 0.5, "MLB": 0.5}
TOTAL_MOVE = {"NFL": 1.5, "CFB": 2.5, "NHL": 0.5, "MLB": 0.5}
FAIR_MOVE = 0.03          # 3 pp no-vig probability move
ODDS_STALE_MIN = 180      # odds older than 3 h before a game that hasn't started
SOURCE_STALE_MIN = 18 * 60
WIND_LINE = 15            # mph
BULLPEN_HEAVY = 120       # reliever pitches over 3 days


def flag(fid: str, category: str, severity: str, title: str, why: str, **data) -> dict:
    return {"id": fid, "category": category, "severity": severity, "title": title, "why": why,
            **({"data": data} if data else {})}


def build(card: dict) -> list[dict]:
    out: list[dict] = []
    lg = card["league"]
    home, away = card["game"]["home"]["abbr"], card["game"]["away"]["abbr"]
    mk = card.get("market") or {}

    # ---- market
    sp = mk.get("spread") or {}
    mv = sp.get("movement") or {}
    if mv.get("home_line") is not None and abs(mv["home_line"]) >= SPREAD_MOVE.get(lg, 1.5):
        o, c = sp["open"]["home_line"], sp["current"]["home_line"]
        out.append(flag("market.spread_move", "market", "watch", "Major spread movement",
                        f"{home} spread moved {o:+g} to {c:+g} since Boolin first saw it ({sp['observations']} observations).",
                        open=o, current=c))
    tot = mk.get("total") or {}
    tmv = tot.get("movement") or {}
    if tmv.get("line") is not None and abs(tmv["line"]) >= TOTAL_MOVE.get(lg, 1.5):
        out.append(flag("market.total_move", "market", "watch", "Major total movement",
                        f"Total moved {tot['open']['line']:g} to {tot['current']['line']:g}."))
    mlm = (mk.get("moneyline") or {}).get("movement") or {}
    if mlm.get("fair_home") is not None and abs(mlm["fair_home"]) >= FAIR_MOVE:
        out.append(flag("market.price_move", "market", "watch", "Sharp price movement",
                        f"No-vig {home} win probability moved {mlm['fair_home'] * 100:+.1f} pp since first seen."))
    for kind in ("moneyline", "spread", "total"):
        m = mk.get(kind) or {}
        if not m.get("available"):
            out.append(flag(f"data.missing_{kind}", "data", "info", f"No {kind} market",
                            f"No {kind} observation in the odds feed for this game."))
        elif m.get("current") and not _has_book(m["current"], "fanduel"):
            out.append(flag(f"market.no_fanduel_{kind}", "market", "info", f"No FanDuel {kind} at the main line",
                            "FanDuel's price wasn't in the feed at the reference line; the actionable price is unknown."))
    cmp_ = card.get("comparison") or {}
    if cmp_.get("overall") in ("significant disagreement", "extreme disagreement"):
        sev = "alert" if cmp_["overall"] == "extreme disagreement" else "watch"
        rows = "; ".join(f"{r['dimension']}: market {r['market']} vs Boolin {r['boolin']}" for r in cmp_["rows"]
                         if r["label"] in ("significant disagreement", "extreme disagreement"))
        out.append(flag("market.model_disagreement", "market", sev, f"Boolin {cmp_['overall']} with market",
                        f"{rows}. Model confidence: {cmp_.get('model_confidence')}."))
    fr = (card.get("freshness") or {}).get("odds") or {}
    if card.get("status") == "scheduled" and fr.get("age_min") is not None and fr["age_min"] > ODDS_STALE_MIN:
        out.append(flag("data.stale_odds", "data", "watch", "Stale odds",
                        f"Odds are {fr['label']}; prices may have moved since."))

    # ---- data health
    for comp, f in (card.get("freshness") or {}).items():
        if f.get("status") in ("partial", "error"):
            out.append(flag(f"data.source_{comp}", "data", "alert" if f["status"] == "error" else "watch",
                            f"{comp} source {f['status']}",
                            f"The {comp} pull reported '{f['status']}'" + (f": {f['detail']}" if f.get("detail") else ".")))
        elif comp != "odds" and f.get("age_min") is not None and f["age_min"] > SOURCE_STALE_MIN:
            out.append(flag(f"data.stale_{comp}", "data", "watch", f"Stale {comp} data", f"{comp} data is {f['label']}."))

    # ---- availability
    av = card.get("availability") or {}
    for side, st in (av.get("starters") or {}).items():
        team = card["game"][side]["abbr"]
        role = {"NHL": "goalie", "MLB": "pitcher", "NFL": "QB"}.get(lg, "starter")
        if st.get("kind") == "unknown":
            out.append(flag(f"availability.no_starter_{side}", "injury", "alert", f"{team} starting {role} unknown",
                            f"No starting {role} in the data for {team}."))
        elif st.get("kind") != "confirmed" and lg in ("NHL", "MLB"):
            soon = _hours_to_start(card)
            sev = "watch" if soon is not None and soon <= 3 else "info"
            out.append(flag(f"availability.unconfirmed_starter_{side}", "injury", sev,
                            f"{team} starting {role} not confirmed",
                            f"{st.get('value')} is {st.get('kind')} ({st.get('note') or st.get('source')}), not officially confirmed."))
    for side in ("away", "home"):
        lu = (av.get("lineups") or {}).get(side)
        if lg == "MLB" and lu and lu.get("kind") != "confirmed":
            out.append(flag(f"data.no_lineup_{side}", "data", "info", f"{card['game'][side]['abbr']} lineup not posted",
                            "Lineup not posted yet in the MLB feed."))
    for inj in av.get("injuries") or []:
        if inj.get("key_player") and inj.get("status_norm") in ("questionable", "doubtful", "day-to-day"):
            out.append(flag(f"injury.unresolved.{inj['player']}", "injury", "watch",
                            f"{inj['player']} ({inj['team']}) status unresolved",
                            f"Listed {inj['status']} ({inj.get('source')}); final status not confirmed."))
    key_players = {(r["player"], r["team"]) for r in av.get("injuries") or [] if r.get("key_player")}
    seen = set()
    for ch in reversed(card.get("recent_changes") or []):
        if ch["type"] != "injury_status" or ch["field"] in seen:
            continue
        seen.add(ch["field"])  # latest change per player only
        key = any(f"{p} ({t})" == ch["field"] for p, t in key_players)
        added = ch["old"] == "not listed"
        direction = ch.get("direction") or "status change"
        long_term = any(x in str(ch["new"]).lower() for x in ("il", "injured reserve", "suspension"))
        sev = ("info" if long_term else "alert" if key and direction == "downgrade"
               else "watch" if direction == "downgrade" else "info")
        verb = "added to injury report" if added else direction
        out.append(flag(f"injury.{direction}.{ch['field']}", "injury", sev, f"{ch['field']} {verb}",
                        f"{ch['field']}: {ch['old']} → {ch['new']} ({ch['source']})."
                        + (" Key position." if key else ""), player=ch["field"]))

    # ---- performance / samples
    for w in (card.get("model") or {}).get("sample_warnings") or []:
        out.append(flag("performance.small_sample", "performance", "info", "Small sample", w))
    for side in ("away", "home"):
        t = (card.get("research") or {}).get(side) or {}
        rf = t.get("form_divergence")
        if rf:
            out.append(flag(f"performance.form_{side}", "performance", "info",
                            f"{card['game'][side]['abbr']} recent form diverges from season", rf))

    # ---- environment / league specifics
    env = card.get("environment") or {}
    wind = (env.get("wind_mph") or {}).get("value")
    if env.get("outdoors") and wind is not None and wind >= WIND_LINE:
        out.append(flag("environment.wind", "environment", "watch", f"Wind {wind:g} mph",
                        f"Wind at or above {WIND_LINE} mph affects passing and kicking.", wind=wind))
    if env.get("outdoors") and lg in ("NFL", "CFB", "MLB") and (env.get("wind_mph") or {}).get("kind") == "unknown":
        out.append(flag("data.missing_weather", "data", "info", "Weather unknown",
                        "Outdoor game with no weather in the data; check a forecast."))
    for ch in card.get("recent_changes") or []:
        if ch["type"] == "weather":
            out.append(flag(f"environment.change.{ch['field']}", "environment", "watch", f"{ch['field']} changed",
                            f"{ch['old']} → {ch['new']} ({ch['source']})."))
    if lg == "NHL":
        for side in ("away", "home"):
            g = ((card.get("research") or {}).get(side) or {}).get("goalie") or {}
            if g.get("gp_this_season") is not None and g["gp_this_season"] < 5:
                out.append(flag(f"nhl.goalie_sample_{side}", "performance", "info",
                                f"{card['game'][side]['abbr']} goalie sample small",
                                f"{g.get('name')} has {g['gp_this_season']} GP this season; GSAx leans on last season."))
    if lg == "MLB":
        for side in ("away", "home"):
            bp = ((card.get("research") or {}).get(side) or {}).get("bullpen") or {}
            if (bp.get("pitches_last3") or 0) >= BULLPEN_HEAVY:
                out.append(flag(f"mlb.bullpen_{side}", "performance", "watch",
                                f"{card['game'][side]['abbr']} bullpen heavily used",
                                f"Relievers threw {bp['pitches_last3']} pitches over the last 3 days"
                                + (f"; likely limited: {', '.join(bp['limited'])}." if bp.get("limited") else ".")))
            if bp.get("classification") in ("unknown", "partial"):
                out.append(flag(f"data.bullpen_unclassified_{side}", "data", "info",
                                f"{card['game'][side]['abbr']} bullpen workload unknown",
                                "The starter couldn't be identified from MLB's credited start in "
                                f"{len(bp.get('unclassified_games') or []) or 'some'} recent game(s), so reliever "
                                "workload isn't counted and the model treats the bullpen as league average."))
            sp_ = ((card.get("research") or {}).get(side) or {}).get("starter") or {}
            if sp_.get("short_start_flag"):
                out.append(flag(f"mlb.short_start_{side}", "injury", "watch", f"{card['game'][side]['abbr']} possible opener",
                                sp_["short_start_flag"]))
            if sp_.get("starts") is not None and sp_["starts"] < 5:
                out.append(flag(f"mlb.starter_sample_{side}", "performance", "info", "Starter sample small",
                                f"{sp_.get('name')} has {sp_['starts']} starts in the data."))
    out.sort(key=lambda f: -SEVERITY[f["severity"]])
    return out


def _has_book(point: dict, book: str) -> bool:
    for k in ("home", "away", "over", "under"):
        if (point.get(k) or {}).get(book) is not None:
            return True
    return False


def _hours_to_start(card: dict) -> float | None:
    from . import timeutil as T
    st, b = T.parse(card.get("start_et")), T.parse(card.get("built_at"))
    if not st or not b:
        return None
    return (st - b).total_seconds() / 3600
