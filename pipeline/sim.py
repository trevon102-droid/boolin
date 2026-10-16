"""Market-anchored game-script and player-projection simulator for the Top 10 cards (GameSim v1.1).

    python -m pipeline.sim SPEC.json [SPEC2.json ...] --out sims.json [--seed 7] [--anchor 0.5]

Each SPEC is one game (schemas in TASK.md, "Game sims"). The engine plays the game out in runs of 100
simulated games, up to 100 runs (10,000 games), and stops once every tracked average has moved by less
than 1% (or 0.05 absolute) for 3 runs in a row AND the game total's Monte Carlo standard error is under
1% of its mean. The output says how many runs it took.

What the output is, and is not
------------------------------
* It is a **market-anchored simulation**. The raw simulation's team scoring is pulled `anchor` of the way
  (default 50%) toward the market-implied baseline by scaling each team's scoring *efficiency* (yards per
  play, hit/HR rates, shooting %). Volume and usage (plays, pass rate, target/carry/shot shares, lineup
  order, TOI, PP1) are never touched, and player props are never back-solved to a sportsbook line. The raw
  simulation, the market-implied baseline and the anchored result are all in `anchor_audit`.
* `home_win`, `home_cover`, `over` etc. are **simulation frequencies**: the share of simulated games where
  it happened. They are not calibrated or validated probabilities, they are never copied into `modelP`,
  and a simulation/market difference is never an edge or a bet by itself.
* **Convergence is not accuracy.** `uncertainty.simulation` is Monte Carlo error only. Model and input
  uncertainty (tiny samples, projected lineups/goalies, missing fields, the simplifications below) is not
  measured by the simulation; `data_quality` lists what was missing or estimated.
* Player distributions (`players`) come straight from the simulated games. One distribution per player
  per stat feeds every line and ladder rung for that stat.

Sports (simplifications are listed in TASK.md):
  NFL / CFB  drive sim with halves and a game clock driven by pace; neutral pass rate plus score/time
             state; pressure -> sacks and completion %; blitz -> sacks + quick targets to RB/TE; man
             coverage -> small WR1 tilt (capped); wind/rain; QB rates blended with team rates by sample;
             red-zone compression; 4th-down, end-of-half FG, kneel-down and 2-point chart logic;
             NFL overtime (both teams possess, 10-min period, ties possible in the regular season) and
             college overtime (from the 25, 2-point try required from the 2nd OT, alternating 2-point
             plays from the 3rd OT; never ends tied).
  MLB        plate-appearance sim: log5 batter vs pitcher for K/BB/HR/hits, conservative league-average
             platoon factors from bats/throws, times-through-the-order, starter leash (opener/bulk aware),
             reliever list with availability or a generic bullpen (taxed by recent workload), simple base
             running, runs vs earned runs, automatic runner in regular-season extras (none in the
             postseason), never ends tied.
  NHL        even-strength shots and goals from shot shares (TOI as a small modifier), power plays from
             regressed PP%/PK% with PP1 players taking most PP shots, goals and points, goalie SV%
             regressed to last season and league, projected-goalie start probability, empty-net goals,
             3v3 OT and shootout (approximations; the shootout winner gets +1 goal, as books grade).
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from array import array
from collections import defaultdict

BATCH = 100
MAX_BATCHES = 100
MIN_BATCHES = 5
TOL = 0.01
ABS_TOL = 0.05
STREAK = 3
ANCHOR_W = 0.5
PILOT = 800
ENGINE = "boolin-sim v1.1"
LABEL = "Market-anchored simulation"
FREQ_NOTE = "Simulation frequencies (share of simulated games), not calibrated or validated probabilities."
DIFF_NOTE = "Not validated edge. A large difference is a reason to research the game, never a bet by itself."
SUPPORTED = ("NFL", "CFB", "MLB", "NHL")
QUANTS = (("p10", 0.10), ("p25", 0.25), ("p50", 0.50), ("p75", 0.75), ("p90", 0.90))


# --------------------------------------------------------------------------- helpers
def num(x, default=None):
    try:
        if x is None or x == "":
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


def clamp(x, lo, hi):
    return min(max(x, lo), hi)


def gamma_yards(rng, mean, shape=1.2, offset=2.0):
    """Skewed yardage: gamma shifted down by `offset` so losses happen and the tail is long."""
    m = max(mean + offset, 0.5)
    return int(round(rng.gammavariate(shape, m / shape) - offset))


def poisson(rng, lam):
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))
    l, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= l:
            return k
        k += 1


def log5(b, p, lg):
    b, p, lg = clamp(b, 1e-4, 0.999), clamp(p, 1e-4, 0.999), clamp(lg, 1e-4, 0.999)
    return (b * p / lg) / (b * p / lg + (1 - b) * (1 - p) / (1 - lg))


def pick(rng, pairs):
    r, acc = rng.random(), 0.0
    for n, w in pairs:
        acc += w
        if r <= acc:
            return n
    return None  # unlisted player


def imp_prob(o):
    o = num(o)
    if o is None or -100 < o < 100:
        return None
    return 100 / (o + 100) if o > 0 else -o / (-o + 100)


def no_vig(a, b):
    pa, pb = imp_prob(a), imp_prob(b)
    if pa is None or pb is None:
        return None
    return pa / (pa + pb)


def quant(sorted_vals, p):
    """Nearest-rank quantile of an already-sorted list (always an observed value, so P10<=...<=P90)."""
    n = len(sorted_vals)
    if not n:
        return None
    i = clamp(int(math.ceil(p * n)) - 1, 0, n - 1)
    return sorted_vals[i]


def dist_summary(vals):
    s = sorted(vals)
    n = len(s)
    out = {"mean": round(sum(s) / n, 3) if n else None, "median": quant(s, 0.5)}
    for k, p in QUANTS:
        out[k] = quant(s, p)
    return out


def line_probs(vals, line):
    """P(over/under/push) of one line from the simulated distribution (frequencies, not validated)."""
    n = len(vals)
    if not n:
        return None
    over = sum(1 for v in vals if v > line)
    under = sum(1 for v in vals if v < line)
    push = n - over - under
    return {"line": line, "over": round(over / n, 4), "under": round(under / n, 4), "push": round(push / n, 4)}


def prop_se(p, n):
    return math.sqrt(max(p * (1 - p), 0.0) / n) if n else None


# --------------------------------------------------------------------------- market
def market_baseline(sport, market):
    """Market-implied (away, home) scoring and how it was derived, or None when no total is posted."""
    market = market or {}
    total = num(market.get("total"))
    if total is None:
        return None
    sh = num(market.get("spread_home"))
    if sh is not None and sport in ("NFL", "CFB"):
        return {"away": (total + sh) / 2.0, "home": (total - sh) / 2.0, "total": total,
                "basis": f"total {total} and home spread {sh:+g}"}
    ph = no_vig(market.get("home_ml"), market.get("away_ml"))
    if ph is None and imp_prob(market.get("home_ml")) is not None:
        ph = imp_prob(market.get("home_ml"))
    if ph is not None:
        ex = {"MLB": 1.83, "NHL": 2.0, "NFL": 2.37, "CFB": 2.5}[sport]
        r = (ph / (1 - ph)) ** (1 / ex)  # Pythagorean share: ph = h^ex / (h^ex + a^ex), h + a = total
        h = total * r / (1 + r)
        return {"away": total - h, "home": h, "total": total,
                "basis": f"total {total} split by no-vig moneyline (home {ph:.1%}, Pythagorean exponent {ex})"}
    return {"away": None, "home": None, "total": total, "basis": f"total {total} only (split kept from the raw simulation)"}


def market_home_win(market):
    return no_vig((market or {}).get("home_ml"), (market or {}).get("away_ml"))


# --------------------------------------------------------------------------- convergence
class Tracker:
    """Running sums for convergence plus every per-game value of the keys we need distributions for."""

    GAME_KEYS = ("total", "margin", "away_pts", "home_pts")

    def __init__(self, dist_keys=()):
        self.sum = defaultdict(float)
        self.sq = defaultdict(float)
        self.n = 0
        self.samples = {k: array("d") for k in self.GAME_KEYS}
        self.dist = {k: array("d") for k in dist_keys}

    def add(self, flat: dict):
        self.n += 1
        for k, v in flat.items():
            self.sum[k] += v
            self.sq[k] += v * v
        for k, arr in self.samples.items():
            arr.append(flat.get(k, 0.0))
        for k, arr in self.dist.items():
            arr.append(flat.get(k, 0.0))

    def means(self):
        return {k: v / self.n for k, v in self.sum.items()}

    def mean(self, k):
        return self.sum.get(k, 0.0) / self.n if self.n else 0.0

    def se(self, k):
        if self.n < 2:
            return float("inf")
        m = self.sum.get(k, 0.0) / self.n
        var = max(self.sq.get(k, 0.0) / self.n - m * m, 0.0)
        return math.sqrt(var / self.n)


def run_until_stable(sim_one, rng, dist_keys=(), batch=BATCH, max_batches=MAX_BATCHES, min_batches=MIN_BATCHES):
    t = Tracker(dist_keys)
    prev, streak, batches, converged = None, 0, 0, False
    max_move = None
    for b in range(1, max_batches + 1):
        for _ in range(batch):
            t.add(sim_one(rng))
        batches = b
        cur = t.means()
        if prev is not None:
            moves = []
            for k, v in cur.items():
                d = abs(v - prev.get(k, 0.0))
                moves.append(0.0 if d <= ABS_TOL else d / max(abs(v), 1e-9))
            max_move = max(moves) if moves else 0.0
            streak = streak + 1 if max_move < TOL else 0
        prev = cur
        se_ok = t.se("total") <= TOL * max(abs(cur.get("total", 0)), 1e-9)
        if b >= min_batches and streak >= STREAK and se_ok:
            converged = True
            break
    runs = {
        "batches": batches, "batch_size": batch, "sims": batches * batch, "max_batches": max_batches,
        "converged": converged, "last_batch_max_move": round(max_move, 4) if max_move is not None else None,
        "total_se": round(t.se("total"), 3),
        "rule": f"Runs of {batch} games until every average moves <{int(TOL*100)}% (or <{ABS_TOL}) for {STREAK} straight runs "
                f"and the total's SE <{int(TOL*100)}% of the mean; cap {max_batches} runs. Stable means converged, not accurate.",
    }
    return t, runs


def calibrate(sim_factory, sport, market, rng, base_eff=(1.0, 1.0), w=ANCHOR_W, pilot=PILOT):
    """Per-team efficiency multipliers so each team's mean score lands `w` of the way from the raw
    simulation to the market-implied baseline. Returns (eff, raw (away, home), target or None, baseline).
    Every pilot run reuses one seed (common random numbers) so the search is not chasing noise."""
    base = market_baseline(sport, market)
    eff = list(base_eff)
    pilot_seed = rng.randrange(1 << 30)

    def pilot_means(e):
        f = sim_factory(tuple(e))
        rng_p = random.Random(pilot_seed)
        a = h = 0.0
        for _ in range(pilot):
            r = f(rng_p)
            a += r["away_pts"]
            h += r["home_pts"]
        return a / pilot, h / pilot

    raw = pilot_means(eff)
    if base is None or w <= 0:
        return tuple(eff), raw, None, base
    if base["away"] is None:  # total only: keep the raw split
        rt = max(raw[0] + raw[1], 1e-9)
        mk = (base["total"] * raw[0] / rt, base["total"] * raw[1] / rt)
    else:
        mk = (base["away"], base["home"])
    target = (w * mk[0] + (1 - w) * raw[0], w * mk[1] + (1 - w) * raw[1])
    cur = raw
    for _ in range(5):
        for i in (0, 1):
            if cur[i] > 0.05:
                eff[i] *= (target[i] / cur[i]) ** (1.0 if sport in ("NFL", "CFB") else 0.9)
            eff[i] = clamp(eff[i], 0.5, 1.8)
        cur = pilot_means(eff)
        if all(abs(cur[i] - target[i]) / max(target[i], 0.5) < 0.02 for i in (0, 1)):
            break
    return tuple(eff), raw, target, base


# --------------------------------------------------------------------------- data quality
LEVELS = {"info": 0, "partial": 1, "degraded": 2}


class Quality:
    def __init__(self):
        self.flags = []

    def add(self, level, field, note):
        self.flags.append({"level": level, "field": field, "note": note})

    def status(self):
        worst = max((LEVELS[f["level"]] for f in self.flags), default=0)
        return {0: "clean", 1: "partial", 2: "degraded"}[worst]

    def out(self, unknown=False):
        st = "unknown" if unknown else self.status()
        return {"status": st, "flags": self.flags,
                "note": "Missing or estimated inputs reduce confidence in the simulation; fallbacks are listed, never silent."}


def _miss(team, keys):
    return [k for k in keys if num(team.get(k)) is None]


def assess_quality(spec):
    sport = spec["sport"]
    q = Quality()
    market = spec.get("market") or {}
    if num(market.get("total")) is None:
        q.add("partial", "market.total", "No market total: no market anchor, raw simulation only.")
    elif sport in ("NFL", "CFB") and num(market.get("spread_home")) is None and imp_prob(market.get("home_ml")) is None:
        q.add("partial", "market.spread_home", "No spread or moneyline: anchored on the total only; the raw side split is kept.")
    elif sport in ("MLB", "NHL") and imp_prob(market.get("home_ml")) is None:
        q.add("partial", "market.home_ml", "No moneyline: anchored on the total only; the raw side split is kept.")
    env = spec.get("env") or {}
    unknown = False
    for side in ("away", "home"):
        t = spec[side]
        ab = t.get("abbr") or side
        if sport in ("NFL", "CFB"):
            core = _miss(t, ("plays_pg", "pass_rate", "ypc", "ypa", "comp_pct", "sack_rate", "int_rate"))
            if len(core) >= 6:
                unknown = True
            if core:
                q.add("partial", f"{ab}.offense", f"Missing {', '.join(core)}: league averages used.")
            d = t.get("def") or {}
            dm = _miss(d, ("ypc_allowed", "ypa_allowed", "comp_pct_allowed", "sack_rate", "int_rate"))
            if dm:
                q.add("partial", f"{ab}.def", f"Missing {', '.join(dm)}: league averages used.")
            for k in ("blitz_pct", "man_pct", "pressure_pct"):
                if num(d.get(k)) is None:
                    q.add("info", f"{ab}.def.{k}", f"No {k.replace('_pct', '')} rate: no adjustment applied.")
            ps = t.get("players") or []
            if not ps:
                q.add("degraded", f"{ab}.players", "No players: no usage, no player distributions.")
            elif not any(p.get("pos") == "QB" for p in ps):
                q.add("degraded", f"{ab}.QB", "No QB listed: team passing rates only.")
            else:
                qb = next(p for p in ps if p.get("pos") == "QB")
                if num(qb.get("att"), 0) < 100:
                    q.add("partial", f"{ab}.QB", f"{qb.get('name')}: {int(num(qb.get('att'), 0))} attempts, so his rates are blended mostly toward team rates.")
            if ps and all(num(p.get("targets")) is None for p in ps if p.get("pos") != "QB"):
                q.add("partial", f"{ab}.targets", "No targets: target shares estimated from receptions / team completion %.")
            if ps and all(p.get("snap_pct") in (None, []) for p in ps):
                q.add("info", f"{ab}.snap_pct", "No snap %: shown as n/a; usage comes from targets/carries.")
        elif sport == "MLB":
            st = t.get("starter") or {}
            if not st:
                q.add("degraded", f"{ab}.starter", "No starter: generic bullpen rates for every batter.")
            else:
                sm = _miss(st, ("k_pct", "bb_pct", "hr_per_bf", "h_per_bf"))
                if sm:
                    q.add("partial", f"{ab}.starter", f"{st.get('name')}: missing {', '.join(sm)}; league rates used.")
                if num(st.get("bf_per_start")) is None:
                    q.add("partial", f"{ab}.starter.bf_per_start", "No expected leash: 20 batters faced assumed.")
                if not st.get("throws"):
                    q.add("info", f"{ab}.starter.throws", "No throwing hand: no platoon adjustment vs the starter.")
            bp = t.get("bullpen") or {}
            if not bp:
                q.add("degraded", f"{ab}.bullpen", "No bullpen rates: league rates used for every reliever.")
            if num(bp.get("reliever_pitches_last3")) is None and not bp.get("arms"):
                q.add("degraded", f"{ab}.bullpen.workload", "No bullpen workload/availability: every reliever treated as available.")
            lu = t.get("lineup") or []
            if len(lu) < 9:
                q.add("degraded", f"{ab}.lineup", f"Lineup has {len(lu)} hitters: missing spots use league-average hitters.")
            if (t.get("lineup_status") or "projected") != "confirmed":
                q.add("partial", f"{ab}.lineup_status", "Lineup projected, not confirmed.")
            if lu and all(not b.get("bats") for b in lu):
                q.add("info", f"{ab}.bats", "No batting hands: no platoon adjustment.")
        elif sport == "NHL":
            g = t.get("goalie") or {}
            if not g or num(g.get("sv_pct")) is None:
                q.add("partial", f"{ab}.goalie", "No goalie SV%: league .900 used.")
            st = str(g.get("status") or "unknown").lower()
            if not st.startswith("confirm"):
                if isinstance(g.get("alt"), dict):
                    q.add("partial", f"{ab}.goalie.status", f"{g.get('name') or 'Goalie'} {st}: starts {goalie_start_prob(g):.0%} of simulated games, {g['alt'].get('name')} the rest.")
                else:
                    q.add("partial", f"{ab}.goalie.status", f"{g.get('name') or 'Goalie'} {st}: no alternate listed, so he starts every simulated game.")
            if g and num(g.get("sv_pct")) is not None and num(g.get("shots_against")) is None and num(g.get("gs")) is None:
                q.add("partial", f"{ab}.goalie.shots_against", f"No shots against for {g.get('name')}: a {DEFAULT_GOALIE_SA}-shot sample assumed for the SV% regression.")
            if not t.get("skaters"):
                q.add("degraded", f"{ab}.skaters", "No skaters: no player distributions.")
            for k in ("shots_pg", "shots_against_pg", "pp_pct", "pk_pct"):
                if num(t.get(k)) is None:
                    q.add("partial", f"{ab}.{k}", f"Missing {k}: league average used.")
            if num(t.get("pp_opps_pg")) is None:
                q.add("info", f"{ab}.pp_opps_pg", "No PP opportunities per game: 3.0 assumed.")
            if t.get("skaters") and not any(s.get("pp1") for s in t["skaters"]):
                q.add("partial", f"{ab}.pp1", "No PP1 flags: power-play shots/goals spread by shot rate.")
    if sport in ("NFL", "CFB", "MLB") and not env:
        q.add("info", "env", "No weather/park data: no weather adjustment.")
    elif sport in ("NFL", "CFB") and num(env.get("wind_mph")) is None and num(env.get("gust_mph")) is None:
        q.add("info", "env.wind", "No wind data: no wind adjustment.")
    elif sport == "MLB" and num(env.get("temp_f")) is None and not env.get("temp_f_range"):
        q.add("info", "env.temp_f", "No temperature: no temperature HR adjustment.")
    return q, unknown


# --------------------------------------------------------------------------- football
LEAGUE = {
    "CFB": dict(ypc=5.0, ypa=7.3, comp=0.62, sack=0.06, int=0.025, fum=0.008, fg_max=52, punt_net=39, start=26,
                xp=0.985, two_pt=0.45, rz_td=0.62, finish=1.12),
    "NFL": dict(ypc=4.3, ypa=6.9, comp=0.65, sack=0.067, int=0.022, fum=0.007, fg_max=56, punt_net=42, start=30,
                xp=0.95, two_pt=0.48, rz_td=0.56, finish=1.07),
}
STICKS_SHARE = 0.35  # share of 3rd/4th-and-3+ completions that are sticks routes
KNEEL_SECS = 100      # leading team with the ball inside this much game time kneels it out
QB_PRIOR_ATT = 150    # QB's own rates get att/(att+150) weight vs team rates
LG_PRESSURE = 0.32
GO_FOR_TWO = {-2, -5, -9, -12, 1, 5, 12}  # margin after the TD, before the try (late game)


def football_profile(off, dfn, sport, env):
    lg = LEAGUE[sport]
    d = dfn.get("def") or {}
    ypa_t, comp_t, int_t = num(off.get("ypa"), lg["ypa"]), num(off.get("comp_pct"), lg["comp"]), num(off.get("int_rate"), lg["int"])
    qb = next((p for p in (off.get("players") or []) if p.get("pos") == "QB"), None)
    qb_w = 0.0
    if qb and num(qb.get("att"), 0) > 0:  # QB change: blend the starter's own rates by his sample
        att = num(qb.get("att"))
        qb_w = att / (att + QB_PRIOR_ATT)
        if num(qb.get("pass_yds")) is not None:
            ypa_t = (1 - qb_w) * ypa_t + qb_w * num(qb.get("pass_yds")) / att
        if num(qb.get("comp")) is not None:
            comp_t = (1 - qb_w) * comp_t + qb_w * num(qb.get("comp")) / att
        if num(qb.get("int")) is not None:
            int_t = (1 - qb_w) * int_t + qb_w * (num(qb.get("int")) + lg["int"] * 30) / (att + 30)
    ypc = num(off.get("ypc"), lg["ypc"]) * num(d.get("ypc_allowed"), lg["ypc"]) / lg["ypc"]
    ypa = ypa_t * num(d.get("ypa_allowed"), lg["ypa"]) / lg["ypa"]
    comp = log5(comp_t, num(d.get("comp_pct_allowed"), lg["comp"]), lg["comp"])
    sack = log5(num(off.get("sack_rate"), lg["sack"]), num(d.get("sack_rate"), lg["sack"]), lg["sack"])
    intr = log5(int_t, num(d.get("int_rate"), lg["int"]), lg["int"])
    pressure = num(d.get("pressure_pct"))
    blitz = num(d.get("blitz_pct"))
    if pressure is not None:  # pressure -> more sacks and a few more incompletions
        sack *= clamp(pressure / LG_PRESSURE, 0.8, 1.25)
        comp -= clamp((pressure - LG_PRESSURE) * 0.10, -0.02, 0.02)
    if blitz is not None:
        sack *= clamp(1 + (blitz - 0.25) * 0.4, 0.94, 1.06)
    pass_rate = num(off.get("pass_rate"), 0.55)
    plays = num(off.get("plays_pg"), 70.0 if sport == "CFB" else 63.0)
    pa = num(d.get("plays_pg_allowed"))
    if pa:
        plays = (plays + pa) / 2.0
    wind = max(num(env.get("wind_mph"), 0) or 0, (num(env.get("gust_mph"), 0) or 0) * 0.6)
    fg_max, fg_wind_pen = lg["fg_max"], 0.0
    if wind > 12:
        fg_wind_pen = min((wind - 12) * 0.0008, 0.008)
        k = min((wind - 12) * 0.012, 0.15)
        ypa *= 1 - k
        comp -= k * 0.4
        pass_rate -= min((wind - 12) * 0.004, 0.05)
        fg_max -= int(min((wind - 12) * 0.5, 8))
    if env.get("precip"):
        ypa *= 0.97
        comp -= 0.01
    rz = num(off.get("rz_td_pct"))
    rz_sq = 0.90 * (clamp((rz / lg["rz_td"]) ** 0.5, 0.9, 1.1) if rz else 1.0)  # red zone compresses gains
    return dict(ypc=ypc, ypa=ypa, comp=max(comp, 0.35), sack=sack, int=intr, blitz=blitz, man=None if num(d.get("man_pct")) is None else num(d.get("man_pct")),
                pressure=pressure, pass_rate=clamp(pass_rate, 0.3, 0.75), plays=plays, fg_max=fg_max, fg_wind_pen=fg_wind_pen,
                start=lg["start"], fum=lg["fum"], punt_net=lg["punt_net"], rz_squeeze=rz_sq, qb_weight=round(qb_w, 3))


def football_usage(team, prof):
    ps = team.get("players") or []
    qb = next((p for p in ps if p.get("pos") == "QB"), None)
    car = {p["name"]: num(p.get("carries"), 0) / max(num(p.get("gp"), 1), 1) for p in ps if num(p.get("carries"), 0) > 0}
    tot_c = sum(car.values()) or 1
    rush = [(n, c / tot_c) for n, c in car.items()]
    tg = {}
    for p in ps:
        if p.get("pos") == "QB":
            continue
        t = num(p.get("targets"))
        if t is None:
            t = num(p.get("rec"), 0) / max(num(team.get("comp_pct"), 0) or 0.65, 0.3)
        tg[p["name"]] = t / max(num(p.get("gp"), 1), 1)
    blitz, man = prof.get("blitz"), prof.get("man")
    pos = {p["name"]: p.get("pos") for p in ps}
    if blitz is not None:  # blitz -> quick throws to RB/TE (capped)
        for n in tg:
            if pos.get(n) in ("RB", "TE"):
                tg[n] *= 1 + clamp((blitz - 0.25) * 0.8, -0.1, 0.1)
    if man is not None and tg:  # man-heavy -> WR1 (weakly supported: capped at +/-5%)
        wr = [n for n in tg if pos.get(n) == "WR"]
        if wr:
            w1 = max(wr, key=lambda n: tg[n])
            tg[w1] *= 1 + clamp((man - 0.30) * 0.3, -0.05, 0.05)
    tot_t = sum(tg.values())
    other = 0.12  # targets to players not listed
    targets = [(n, (1 - other) * t / tot_t) for n, t in tg.items()] if tot_t else []
    ypt = {}
    for p in ps:
        if p.get("pos") == "QB":
            continue
        rec, yds = num(p.get("rec"), 0), num(p.get("rec_yds"), 0)
        if rec >= 3:
            ypt[p["name"]] = yds / rec
    team_ypt = statistics.mean(ypt.values()) if ypt else None
    ypr_mult = {n: clamp(v / team_ypt, 0.5, 1.8) for n, v in ypt.items()} if team_ypt else {}
    ypc_mult = {}
    tcar = sum(num(p.get("carries"), 0) for p in ps)
    tyds = sum(num(p.get("rush_yds"), 0) for p in ps)
    if tcar and tyds > 0:
        base = tyds / tcar
        for p in ps:
            c = num(p.get("carries"), 0)
            if c >= 8:
                ypc_mult[p["name"]] = clamp((num(p.get("rush_yds"), 0) / c) / base, 0.4, 1.8)
    return dict(qb=qb["name"] if qb else "QB", rush=rush, targets=targets, ypr_mult=ypr_mult, ypc_mult=ypc_mult)


def fg_make_prob(sport, dist, wind_pen):
    p = 0.99 - max(dist - 25, 0) * 0.011 if sport == "NFL" else 0.97 - max(dist - 20, 0) * 0.014
    return clamp(p - wind_pen * max(dist - 30, 0), 0.2, 0.99)


def football_factory(spec):
    sport = spec["sport"]
    lg = LEAGUE[sport]
    env = spec.get("env") or {}
    postseason = bool(spec.get("postseason"))
    A, H = spec["away"], spec["home"]
    profs = (football_profile(A, H, sport, env), football_profile(H, A, sport, env))
    uses = (football_usage(A, profs[0]), football_usage(H, profs[1]))
    home_edge = 0.0 if env.get("neutral") else 0.02
    secs = (1800.0 / profs[0]["plays"], 1800.0 / profs[1]["plays"])

    def factory(eff):
        effs = (eff[0] * (1 - home_edge), eff[1] * (1 + home_edge))

        def one(rng):
            box = defaultdict(float)
            pts = [0, 0]
            plays = [0, 0]
            ot_info = {"ot": 0, "ot_periods": 0, "tie": 0, "safety": 0}

            def margin(s):
                return pts[s] - pts[1 - s]

            def pass_rate(s, ctx, down, togo):
                pr = profs[s]["pass_rate"]
                m = margin(s)
                clk = ctx["clock"]
                if ctx["phase"] == "reg":
                    rem = clk[0] + (1800 if ctx["half"] == 1 else 0)
                    frac = 1 - rem / 3600.0
                    pr += -clamp(m / 14.0, -1.5, 1.5) * 0.10 * (0.3 + 0.7 * frac)
                    if ctx["half"] == 2 and clk[0] <= 300:
                        pr += 0.15 if m < 0 else (-0.15 if m > 0 else 0)
                if down == 3 and togo >= 6:
                    pr += 0.25
                elif down >= 3 and togo <= 2:
                    pr -= 0.15
                return clamp(pr, 0.15, 0.95)

            def play(s, yard, down, togo, prate):
                pr, us = profs[s], uses[s]
                # league finishing factor (small): league-average inputs reproduce league scoring
                e = effs[s] * lg["finish"]
                pfx = f"{s}|"
                plays[s] += 1
                box[pfx + "team_plays"] += 1
                rz = yard >= 80
                sq = pr["rz_squeeze"] if rz else 1.0
                if rng.random() < prate:
                    box[pfx + "team_dropbacks"] += 1
                    if rng.random() < pr["sack"]:
                        box[pfx + "team_sacks"] += 1
                        return -int(round(rng.uniform(4, 10))), None, False, False
                    qb = us["qb"]
                    box[pfx + qb + "|att"] += 1
                    tgt = pick(rng, us["targets"])
                    if tgt:
                        box[pfx + tgt + "|tgt"] += 1
                    if rng.random() < pr["int"]:
                        box[pfx + qb + "|int"] += 1
                        return 0, "int", False, False
                    # late-down and long: deeper throws, lower completion % (a tilt, not a forced conversion)
                    long = max(togo - 5, 0) if down >= 3 else 0
                    comp_p = pr["comp"] / (1 - pr["int"]) - min(long * 0.008, 0.08) - (0.03 if rz else 0.0)  # completion % per attempt ~ comp
                    if rng.random() < comp_p:
                        ypc = pr["ypa"] * e / pr["comp"] * (us["ypr_mult"].get(tgt, 1.0) if tgt else 1.0)
                        if down >= 3 and togo >= 3 and rng.random() < STICKS_SHARE:
                            # some late-down completions are designed to reach the sticks: short of the
                            # natural mean when togo is short, never a forced long gain
                            gain = togo + gamma_yards(rng, 2.0 * e * sq, shape=1.2, offset=0.0)
                        else:
                            ypc *= (1 + min(long * 0.03, 0.25)) * sq
                            gain = gamma_yards(rng, ypc, shape=1.6, offset=3.0)
                        gain = min(max(gain, -3), 100 - yard)
                        box[pfx + qb + "|cmp"] += 1
                        box[pfx + qb + "|pyds"] += gain
                        td = yard + gain >= 100
                        if tgt:
                            box[pfx + tgt + "|rec"] += 1
                            box[pfx + tgt + "|ryds"] += gain
                            if td:
                                box[pfx + tgt + "|rtd"] += 1
                        if td:
                            box[pfx + qb + "|ptd"] += 1
                        return gain, None, td, False
                    return 0, None, False, True
                r = pick(rng, us["rush"]) or us["qb"]
                m = pr["ypc"] * e * us["ypc_mult"].get(r, 1.0) * sq
                if togo <= 2:  # short yardage: tighter, fewer losses (power runs / sneaks), same mean
                    gain = min(gamma_yards(rng, m, shape=2.4, offset=1.0), 100 - yard)
                else:
                    gain = min(gamma_yards(rng, m), 100 - yard)
                box[pfx + r + "|car"] += 1
                box[pfx + r + "|ruyds"] += gain
                if yard + gain >= 100:
                    box[pfx + r + "|rutd"] += 1
                    return gain, None, True, False
                if rng.random() < pr["fum"]:
                    return gain, "fum", False, False
                return gain, None, False, False

            def tick(s, ctx, incomplete):
                clk = ctx["clock"]
                if clk is None:
                    return
                t = secs[s] * rng.uniform(0.75, 1.25) * (0.6 if incomplete else 1.09)
                if ctx["phase"] == "reg" and ctx["half"] == 2 and clk[0] <= 300:
                    m = margin(s)
                    t *= 1.35 if m > 0 else (0.65 if m < 0 else 1.0)
                elif ctx["phase"] == "nfl_ot":
                    t *= 0.65  # both teams in hurry-up / clock-stopping mode in OT
                clk[0] -= t

            def try_after(s, ctx):
                """PAT after a TD (CFB OT rules and the late-game 2-point chart)."""
                two = False
                if ctx["phase"] == "cfb_ot" and ctx["ot_n"] >= 2:
                    two = True
                elif ctx["phase"] == "reg" and ctx["half"] == 2 and ctx["clock"][0] <= 900 and margin(s) in GO_FOR_TWO:
                    two = True
                elif ctx["phase"] == "nfl_ot":
                    two = False
                if two:
                    p2 = clamp(lg["two_pt"] * effs[s] ** 0.5, 0.3, 0.65)
                    if rng.random() < p2:
                        pts[s] += 2
                elif rng.random() < lg["xp"]:
                    pts[s] += 1

            def fourth(s, yard, togo, ctx):
                dist = 100 - yard + 17
                fg_ok = dist <= profs[s]["fg_max"]
                m = margin(s)
                phase = ctx["phase"]
                if phase in ("cfb_ot", "nfl_ot") and ctx.get("need_td"):
                    return "go"
                if phase == "cfb_ot":
                    return "fg" if fg_ok else "go"
                late = ctx["half"] == 2 and ctx["clock"] is not None and ctx["clock"][0] <= 420
                if late and m < -3 and yard >= 30:
                    return "go"
                if fg_ok:  # modern 4th-down aggressiveness inside the opponent's 40
                    if togo <= 1 and yard >= 60 and rng.random() < (0.6 if sport == "CFB" else 0.55):
                        return "go"
                    if togo <= 3 and yard >= 60 and rng.random() < (0.3 if sport == "CFB" else 0.25):
                        return "go"
                    return "fg"
                if togo <= 1 and yard >= 35 and rng.random() < (0.7 if sport == "CFB" else 0.6):
                    return "go"
                if togo <= 3 and yard >= 55 and rng.random() < 0.5:
                    return "go"
                if late and m < 0 and yard >= 20:
                    return "go"
                return "punt"

            def possession(s, yard, ctx):
                """One possession. Returns (result, opponent's start yard or None)."""
                down, togo = 1, min(10, 100 - yard)
                pfx = f"{s}|"
                box[pfx + "team_drives"] += 1
                rz_trip = False
                clk = ctx["clock"]
                while True:
                    if clk is not None and clk[0] <= 0:
                        return "clock", yard
                    if yard >= 80 and not rz_trip:
                        rz_trip = True
                        box[pfx + "team_rz_trips"] += 1
                    m = margin(s)
                    dist = 100 - yard + 17
                    if clk is not None and ctx["phase"] == "reg" and ctx["half"] == 2 and m > 0 and clk[0] <= KNEEL_SECS:
                        clk[0] = 0  # victory formation
                        return "kneel", None
                    if clk is not None and ctx["phase"] == "reg" and clk[0] <= secs[s] * 1.3 and dist <= profs[s]["fg_max"] and (ctx["half"] == 1 or m >= -3):
                        return kick(s, yard, ctx)  # end-of-half / last-play field goal
                    third = down == 3
                    gain, to, td, inc = play(s, yard, down, togo, pass_rate(s, ctx, down, togo))
                    box[pfx + "team_yds"] += gain
                    if third:
                        box[pfx + "team_3d_att"] += 1
                        if td or (not to and gain >= togo):
                            box[pfx + "team_3d_conv"] += 1
                    tick(s, ctx, inc)
                    if td:
                        pts[s] += 6
                        if rz_trip or yard >= 80:
                            box[pfx + "team_rz_td"] += 1
                        try_after(s, ctx)
                        return "td", None
                    if to:
                        air = 10 if to == "int" else 0
                        return "turnover", clamp(100 - (yard + max(gain, 0) + air), 1, 99)
                    yard = max(yard + gain, 1)
                    togo -= gain
                    if togo <= 0:
                        down, togo = 1, min(10, 100 - yard)
                        continue
                    down += 1
                    if down > 4:
                        return "downs", clamp(100 - yard, 1, 99)
                    if down == 4:
                        dec = fourth(s, yard, togo, ctx)
                        if dec == "go":
                            continue
                        if dec == "fg":
                            return kick(s, yard, ctx)
                        net = rng.gauss(profs[s]["punt_net"], 6)
                        land = yard + net
                        if clk is not None:
                            clk[0] -= 6
                        return "punt", (20 if land >= 100 else clamp(int(round(100 - land)), 2, 95))

            def kick(s, yard, ctx):
                dist = 100 - yard + 17
                if ctx["clock"] is not None:
                    ctx["clock"][0] -= 5
                if rng.random() < fg_make_prob(sport, dist, profs[s]["fg_wind_pen"]):
                    pts[s] += 3
                    box[f"{s}|team_fg"] += 1
                    return "fg", None
                if sport == "NFL":
                    return "fg_miss", max(100 - (yard - 7), 20)
                return "fg_miss", max(100 - yard, 20)

            def kickoff_start(s):
                return int(clamp(rng.gauss(profs[s]["start"], 4), 10, 45))

            # ---- regulation: two halves; the team that receives first kicks off the second half
            first = 0 if rng.random() < 0.5 else 1
            for half in (1, 2):
                clk = [1800.0]
                ctx = {"phase": "reg", "half": half, "clock": clk}
                s = first if half == 1 else 1 - first
                yard = kickoff_start(s)
                while clk[0] > 0:
                    res, nxt = possession(s, yard, ctx)
                    if clk[0] <= 0 or res in ("clock", "kneel"):
                        break
                    s = 1 - s
                    yard = nxt if nxt is not None else kickoff_start(s)

            # ---- overtime
            if pts[0] == pts[1]:
                ot_info["ot"] = 1
                if sport == "CFB":
                    cfb_overtime(rng, pts, possession, ot_info, effs, lg)
                else:
                    nfl_overtime(rng, pts, possession, kickoff_start, ot_info, postseason)
            out = {"away_pts": pts[0], "home_pts": pts[1], "total": pts[0] + pts[1], "margin": pts[1] - pts[0],
                   "home_win": 1.0 if pts[1] > pts[0] else 0.0, "away_win": 1.0 if pts[0] > pts[1] else 0.0,
                   "tie": 1.0 if pts[0] == pts[1] else 0.0, "ot": float(ot_info["ot"]), "ot_periods": float(ot_info["ot_periods"]),
                   "safety_resolved": float(ot_info["safety"])}
            out.update(box)
            return out
        return one
    return factory


def cfb_overtime(rng, pts, possession, ot_info, effs, lg, max_periods=12):
    """College OT (2021+ rules): OT1-2 each team gets a possession from the opponent's 25 (2-point try
    required after a TD from OT2); from OT3 the teams alternate single 2-point plays. Never ends tied:
    after `max_periods` (practically unreachable) a coin decides and `safety` is flagged."""
    n = 0
    while pts[0] == pts[1]:
        n += 1
        ot_info["ot_periods"] = n
        if n > max_periods:
            pts[0 if rng.random() < 0.5 else 1] += 2
            ot_info["safety"] = 1
            break
        first = 0 if rng.random() < 0.5 else 1
        if n <= 2:
            for i, s in enumerate((first, 1 - first)):
                need_td = i == 1 and pts[1 - s] - pts[s] > 3
                possession(s, 75, {"phase": "cfb_ot", "half": "ot", "clock": None, "ot_n": n, "need_td": need_td})
                if i == 1 and pts[s] != pts[1 - s]:
                    break
        else:
            for s in (first, 1 - first):
                p2 = clamp(lg["two_pt"] * effs[s] ** 0.5, 0.3, 0.65)
                if rng.random() < p2:
                    pts[s] += 2


def nfl_overtime(rng, pts, possession, kickoff_start, ot_info, postseason):
    """NFL OT (2025 rules): both teams get a possession; then sudden death. 10-minute period in the
    regular season (a tie is possible and reported); 15-minute periods and no ties in the postseason."""
    periods = 0
    s = 0 if rng.random() < 0.5 else 1
    poss = 0
    yard = kickoff_start(s)
    while pts[0] == pts[1]:
        periods += 1
        ot_info["ot_periods"] = periods
        clk = [900.0 if postseason else 600.0]
        ctx = {"phase": "nfl_ot", "half": "ot", "clock": clk}
        while clk[0] > 0:
            poss += 1
            ctx["need_td"] = poss == 2 and pts[1 - s] - pts[s] > 3
            res, nxt = possession(s, yard, ctx)
            if poss >= 2 and pts[0] != pts[1]:
                return
            if res == "clock":  # period over mid-drive: same team keeps the ball next period (new set of downs)
                poss -= 1
                yard = nxt if nxt is not None else yard
                break
            s = 1 - s
            yard = nxt if nxt is not None else kickoff_start(s)
        if not postseason:
            return  # regular-season tie
        if periods >= 6:
            pts[0 if rng.random() < 0.5 else 1] += 3
            ot_info["safety"] = 1
            return


FB_STATS = (("att", "pass_att"), ("cmp", "comp"), ("pyds", "pass_yds"), ("ptd", "pass_td"), ("int", "int"),
            ("car", "carries"), ("ruyds", "rush_yds"), ("rutd", "rush_td"), ("tgt", "targets"), ("rec", "rec"),
            ("ryds", "rec_yds"), ("rtd", "rec_td"))
FB_POS_STATS = {"QB": ("pass_att", "comp", "pass_yds", "pass_td", "int", "carries", "rush_yds", "rush_td"),
                "RB": ("carries", "rush_yds", "rush_td", "targets", "rec", "rec_yds", "rec_td", "td"),
                "WR": ("targets", "rec", "rec_yds", "rec_td", "carries", "rush_yds", "td"),
                "TE": ("targets", "rec", "rec_yds", "rec_td", "td")}


def football_tables(spec, t, sims_n):
    m = t.means()
    A, H = spec["away"], spec["home"]
    env = spec.get("env") or {}
    pa, ph = football_profile(A, H, spec["sport"], env), football_profile(H, A, spec["sport"], env)
    g = lambda k: m.get(k, 0.0)

    def pct(x):
        return None if x is None else f"{round(x*100)}%"

    def rz(s):
        tr = g(f"{s}|team_rz_trips")
        return pct(g(f"{s}|team_rz_td") / tr) if tr > 0.05 else "—"

    team_rows = [
        {"label": "Predicted plays", "a": round(g("0|team_plays"), 1), "b": round(g("1|team_plays"), 1)},
        {"label": "Pass rate (dropbacks/plays)", "a": pct(g("0|team_dropbacks") / max(g("0|team_plays"), 1)), "b": pct(g("1|team_dropbacks") / max(g("1|team_plays"), 1))},
        {"label": "Total yards", "a": round(g("0|team_yds")), "b": round(g("1|team_yds"))},
        {"label": "Sacks taken", "a": round(g("0|team_sacks"), 1), "b": round(g("1|team_sacks"), 1)},
        {"label": "Red-zone trips / TD%", "a": f"{g('0|team_rz_trips'):.1f} / {rz(0)}", "b": f"{g('1|team_rz_trips'):.1f} / {rz(1)}"},
        {"label": "Drives / FGs", "a": f"{g('0|team_drives'):.1f} / {g('0|team_fg'):.1f}", "b": f"{g('1|team_drives'):.1f} / {g('1|team_fg'):.1f}"},
    ]
    dA, dH = (A.get("def") or {}), (H.get("def") or {})

    def cov(d):
        return f"{pct(num(d.get('man_pct')))} / {pct(num(d.get('zone_pct')))}" if num(d.get("man_pct")) is not None else "n/a"

    input_rows = [
        {"label": "Pace (plays/g, off)", "a": A.get("plays_pg"), "b": H.get("plays_pg")},
        {"label": "Neutral pass rate", "a": pct(num(A.get("pass_rate"))), "b": pct(num(H.get("pass_rate")))},
        {"label": "Run yds/carry vs this D", "a": round(pa["ypc"], 2), "b": round(ph["ypc"], 2)},
        {"label": "Pass yds/att vs this D", "a": round(pa["ypa"], 2), "b": round(ph["ypa"], 2)},
        {"label": "Sack rate vs this D", "a": pct(pa["sack"]), "b": pct(ph["sack"])},
        {"label": "Pressure rate faced", "a": pct(num(dH.get("pressure_pct"))) or "n/a", "b": pct(num(dA.get("pressure_pct"))) or "n/a"},
        {"label": "Blitz rate faced", "a": pct(num(dH.get("blitz_pct"))) or "n/a", "b": pct(num(dA.get("blitz_pct"))) or "n/a"},
        {"label": "Coverage faced (man / zone)", "a": cov(dH), "b": cov(dA)},
        {"label": "QB rate weight vs team", "a": pct(pa["qb_weight"]), "b": pct(ph["qb_weight"])},
    ]
    return team_rows, input_rows


# --------------------------------------------------------------------------- MLB
MLB_LG = dict(k=0.222, bb=0.085, hr=0.030, nonhr_h=0.19)
# Conservative, fully regressed league-average platoon factors (hits, HR, BB+HBP, K) by (bats, throws).
PLATOON = {("R", "R"): (0.975, 0.97, 0.96, 1.04), ("R", "L"): (1.03, 1.04, 1.05, 0.95),
           ("L", "L"): (0.95, 0.92, 0.93, 1.09), ("L", "R"): (1.02, 1.03, 1.03, 0.97)}
SWITCH = (1.01, 1.01, 1.01, 0.99)
TTO3 = (1.04, 1.06, 1.0, 0.96)   # third time through the order: gradual penalty
MLB_FINISH = 1.03               # league finishing factor: league-average inputs reproduce ~4.4 runs/team
ERROR_RATE = 0.020               # share of balls in play that become reached-on-error
PULL_RUNS = 5                    # a starter allowing this many runs is pulled
TAXED_PITCHES = 120              # bullpen pitches over the last 3 days that count as taxed


def batter_rates(b):
    pa = max(num(b.get("pa"), 0), 1)
    h = num(b.get("h"))
    if h is None:
        ab = num(b.get("ab"), pa * 0.89)
        h = num(b.get("avg"), 0.245) * ab
    hr = num(b.get("hr"), 0)
    d2, d3 = num(b.get("doubles"), 0), num(b.get("triples"), 0)
    bb = num(b.get("bb_pct"), 0.085) + num(b.get("hbp"), 0) / pa
    k = num(b.get("k_pct"), 0.22)
    nonhr = max(h - hr, 0)
    w = pa / (pa + 200)  # regress toward league with 200 PA
    return dict(k=w * k + (1 - w) * MLB_LG["k"], bb=w * bb + (1 - w) * MLB_LG["bb"],
                hr=w * hr / pa + (1 - w) * MLB_LG["hr"], nonhr=w * nonhr / pa + (1 - w) * MLB_LG["nonhr_h"],
                s2=(d2 / nonhr) if nonhr else 0.2, s3=(d3 / nonhr) if nonhr else 0.02,
                bats=(b.get("bats") or "").upper()[:1] or None)


def pitcher_rates(p, taxed=False):
    p = p or {}
    r = dict(k=num(p.get("k_pct"), MLB_LG["k"]), bb=num(p.get("bb_pct"), MLB_LG["bb"]) + num(p.get("hbp_pct"), 0.008),
             hr=num(p.get("hr_per_bf"), MLB_LG["hr"]), nonhr=max(num(p.get("h_per_bf"), 0.22) - num(p.get("hr_per_bf"), 0.03), 0.1),
             throws=(p.get("throws") or "").upper()[:1] or None)
    if taxed:  # tired bullpen: a little more contact and walks
        r.update(k=r["k"] * 0.98, bb=r["bb"] * 1.03, nonhr=r["nonhr"] * 1.03, hr=r["hr"] * 1.03)
    return r


def mlb_env_hr(env):
    f = num(env.get("park_hr_factor"), 1.0) or 1.0
    t = num(env.get("temp_f"))
    if t is None and env.get("temp_f_range"):
        t = statistics.mean(env["temp_f_range"])
    if t is not None:
        f *= 1 + (t - 70) * 0.003
    wind = num(env.get("wind_mph")) or (num(env.get("wind_gust_mph"), 0) or 0) * 0.5
    d = (env.get("wind_dir") or "").lower()
    if wind and d in ("in", "out"):
        f *= 1 + (0.01 if d == "out" else -0.01) * min(wind, 25)
    return f


PLATOON_MIX = {"R": 0.70, "L": 0.75}  # share of a hitter's PA vs RHP; his season line already reflects this mix


def platoon(bats, throws):
    """Platoon factors centred on the hitter's usual mix, so a season line is not double-counted."""
    if not bats or not throws:
        return (1.0, 1.0, 1.0, 1.0)
    if bats == "S":
        return SWITCH
    f = PLATOON.get((bats, throws))
    if f is None:
        return (1.0, 1.0, 1.0, 1.0)
    w = PLATOON_MIX.get(bats, 0.7)
    vs_r, vs_l = PLATOON[(bats, "R")], PLATOON[(bats, "L")]
    return tuple(f[i] / (w * vs_r[i] + (1 - w) * vs_l[i]) for i in range(4))


def is_postseason(spec):
    if spec.get("postseason") is not None:
        return bool(spec.get("postseason"))
    gt = str(spec.get("game_type") or "")
    return gt in ("F", "D", "L", "W") or any(x in str(spec.get("game") or "") for x in ("ALCS", "NLCS", "ALDS", "NLDS", "World Series", "Wild Card"))


def mlb_staff(team):
    """Pitchers in the order they appear: starter (or opener), optional bulk arm, listed relievers by
    availability, then the generic bullpen for whatever is left."""
    st = team.get("starter") or {}
    bp = team.get("bullpen") or {}
    work = num(bp.get("reliever_pitches_last3"))
    taxed = work is not None and work >= TAXED_PITCHES
    staff = []
    if st:
        bf = num(st.get("bf_per_start"), 20.0)
        opener = str(st.get("role") or "").lower() == "opener" or bf <= 6
        staff.append(dict(key=f"P:{st.get('name') or 'SP'}", name=st.get("name") or "SP", rates=pitcher_rates(st), bf=bf,
                          sd=0.5 if opener else 3.0, min_bf=3 if opener else 6, starter=True))
    bulk = team.get("bulk") or st.get("bulk")
    if isinstance(bulk, dict):
        staff.append(dict(key=f"P:{bulk.get('name') or 'bulk'}", name=bulk.get("name") or "bulk", rates=pitcher_rates(bulk),
                          bf=num(bulk.get("bf_per_start"), 12.0), sd=2.0, min_bf=4, starter=False))
    for arm in bp.get("arms") or []:
        av = str(arm.get("avail") or "full").lower()
        if av == "out":
            continue
        cap = num(arm.get("bf_cap"), 5.0)
        if av == "limited":
            cap = min(cap, 3.0)
        staff.append(dict(key=f"P:{arm.get('name') or 'RP'}", name=arm.get("name") or "RP", rates=pitcher_rates(arm, taxed), bf=cap,
                          sd=0.0, min_bf=1, starter=False))
    staff.append(dict(key="P:bullpen", name="bullpen", rates=pitcher_rates(bp, taxed), bf=1e9, sd=0.0, min_bf=1, starter=False))
    return staff, taxed, work


def mlb_factory(spec):
    env = spec.get("env") or {}
    hrf = mlb_env_hr(env)
    pf = num(env.get("park_factor"), 1.0) or 1.0
    post = is_postseason(spec)
    teams = [spec["away"], spec["home"]]
    lineups = []
    for t in teams:
        lu = [(b["name"], batter_rates(b)) for b in (t.get("lineup") or [])]
        while len(lu) < 9:
            lu.append((f"{t.get('abbr', 'TM')} lineup spot {len(lu)+1}", batter_rates({})))
        lineups.append(lu[:9])
    staffs = [mlb_staff(t)[0] for t in teams]

    def ev_probs(b, p, eff, tto):
        fh, fhr, fbb, fk = platoon(b["bats"], p["throws"])
        if tto >= 3:
            fh, fhr, fbb, fk = fh * TTO3[0], fhr * TTO3[1], fbb * TTO3[2], fk * TTO3[3]
        k = log5(b["k"], p["k"], MLB_LG["k"]) * fk
        bb = log5(b["bb"], p["bb"], MLB_LG["bb"]) * fbb
        hr = log5(b["hr"], p["hr"], MLB_LG["hr"]) * hrf * eff * fhr
        h = log5(b["nonhr"], p["nonhr"], MLB_LG["nonhr_h"]) * pf * eff * fh
        tot = k + bb + hr + h
        if tot > 0.95:
            s = 0.95 / tot
            k, bb, hr, h = k * s, bb * s, hr * s, h * s
        return k, bb, hr, h

    def factory(eff):
        def one(rng):
            box = defaultdict(float)
            runs = [0, 0]
            idx = [0, 0]
            cur = [0, 0]  # index into each fielding team's staff
            stint_bf = [0, 0]
            stint_r = [0, 0]
            limits = []
            for st in staffs:
                limits.append([max(p["min_bf"], int(round(rng.gauss(p["bf"], p["sd"])))) if p["sd"] else p["bf"] for p in st])
            faced = [defaultdict(int), defaultdict(int)]  # batters faced per pitcher (times through order)
            inning = 1
            extras = 0
            safety = 0
            while True:
                for half in (0, 1):
                    if inning >= 9 and half == 1 and runs[1] > runs[0]:
                        break
                    bat, fld = half, 1 - half
                    outs = 0
                    bases = [None, None, None]  # (runner, responsible pitcher key, earned)
                    def change(fld):
                        st = staffs[fld]
                        while cur[fld] < len(st) - 1 and (stint_bf[fld] >= limits[fld][cur[fld]] or (st[cur[fld]]["starter"] and stint_r[fld] >= PULL_RUNS)):
                            cur[fld] += 1
                            stint_bf[fld] = 0
                            stint_r[fld] = 0
                        return st[cur[fld]]
                    if inning >= 10 and not post:  # automatic runner (regular season only); his run is unearned
                        bases[1] = (lineups[bat][(idx[bat] - 1) % 9][0], change(fld)["key"], False)
                    while outs < 3:
                        p = change(fld)
                        pk = f"{fld}|{p['key']}|"
                        name, br = lineups[bat][idx[bat] % 9]
                        idx[bat] += 1
                        faced[fld][p["key"]] += 1
                        tto = (faced[fld][p["key"]] - 1) // 9 + 1
                        stint_bf[fld] += 1
                        box[pk + "bf"] += 1
                        k, bb, hr, h = ev_probs(br, p["rates"], eff[bat], tto if p["starter"] else 1)
                        r = rng.random()
                        bk = f"{bat}|{name}|"
                        box[bk + "pa"] += 1
                        scored, rbi_ok, is_hr = [], True, False
                        me = (name, p["key"], True)
                        if r < k:
                            outs += 1
                            box[bk + "k"] += 1
                            box[pk + "k"] += 1
                            box[pk + "outs"] += 1
                        elif r < k + bb:
                            box[bk + "bb"] += 1
                            box[pk + "bb"] += 1
                            if bases[0]:
                                if bases[1]:
                                    if bases[2]:
                                        scored.append(bases[2])
                                    bases[2] = bases[1]
                                bases[1] = bases[0]
                            bases[0] = me
                        elif r < k + bb + hr:
                            box[bk + "h"] += 1
                            box[bk + "hr"] += 1
                            box[bk + "tb"] += 4
                            box[pk + "h"] += 1
                            scored += [x for x in (bases[2], bases[1], bases[0]) if x] + [me]
                            bases = [None, None, None]
                            is_hr = True
                        elif r < k + bb + hr + h:
                            box[bk + "h"] += 1
                            box[pk + "h"] += 1
                            x = rng.random()
                            if x < br["s3"]:
                                box[bk + "tb"] += 3
                                scored += [y for y in (bases[2], bases[1], bases[0]) if y]
                                bases = [None, None, me]
                            elif x < br["s3"] + br["s2"]:
                                box[bk + "tb"] += 2
                                scored += [y for y in (bases[2], bases[1]) if y]
                                if bases[0] and rng.random() < 0.45:
                                    scored.append(bases[0])
                                    bases = [None, me, None]
                                else:
                                    bases = [None, me, bases[0]]
                            else:
                                box[bk + "tb"] += 1
                                if bases[2]:
                                    scored.append(bases[2])
                                n3 = None
                                if bases[1]:
                                    if rng.random() < 0.62:
                                        scored.append(bases[1])
                                    else:
                                        n3 = bases[1]
                                n2 = bases[0]
                                if n2 and n3 is None and rng.random() < 0.28:  # first to third
                                    n3, n2 = n2, None
                                bases = [me, n2, n3]
                        elif rng.random() < ERROR_RATE:  # reached on error: no RBI, his run is unearned
                            rbi_ok = False
                            if bases[2]:
                                scored.append(bases[2])
                            bases = [(name, p["key"], False), bases[0], bases[1]]
                        else:  # ball in play out
                            outs += 1
                            box[pk + "outs"] += 1
                            if outs < 3 and bases[1] and not bases[2] and rng.random() < 0.25:
                                bases[2], bases[1] = bases[1], None
                            if outs < 3 and bases[0] and rng.random() < 0.12:  # double play
                                outs += 1
                                box[pk + "outs"] += 1
                                bases[0] = None
                                rbi_ok = False
                            if outs < 3 and bases[2] and rng.random() < 0.40:
                                scored.append(bases[2])
                                bases[2] = None
                        if scored and inning >= 9 and half == 1 and not is_hr:
                            need = runs[0] + 1 - runs[1]  # walk-off: only the winning run counts (lead runner first)
                            if 0 < need < len(scored):
                                scored = scored[:need]
                        for (rname, resp, earned) in scored:
                            box[f"{bat}|{rname}|r"] += 1
                            box[f"{fld}|{resp}|r"] += 1
                            if earned:
                                box[f"{fld}|{resp}|er"] += 1
                            if resp == p["key"]:
                                stint_r[fld] += 1
                        if scored:
                            if rbi_ok:
                                box[bk + "rbi"] += len(scored)
                            runs[bat] += len(scored)
                        if inning >= 9 and half == 1 and runs[1] > runs[0]:
                            break
                if inning >= 9 and runs[0] != runs[1]:
                    break
                inning += 1
                if inning > 9:
                    extras = 1
                if inning > 40:  # practically unreachable; never report a tie
                    runs[0 if rng.random() < 0.5 else 1] += 1
                    safety = 1
                    break
            out = {"away_pts": runs[0], "home_pts": runs[1], "total": runs[0] + runs[1], "margin": runs[1] - runs[0],
                   "home_win": 1.0 if runs[1] > runs[0] else 0.0, "away_win": 1.0 if runs[0] > runs[1] else 0.0, "tie": 0.0,
                   "ot": float(extras), "innings": float(inning), "safety_resolved": float(safety)}
            out.update(box)
            return out
        return one
    return factory


MLB_STATS = ("pa", "h", "tb", "hr", "r", "rbi", "bb", "k")
MLB_PSTATS = ("bf", "outs", "k", "h", "bb", "r", "er")


def mlb_tables(spec, t):
    g = lambda k: t.mean(k)
    rows = []
    for s, key in ((0, "away"), (1, "home")):
        opp = 1 - s
        st = (spec[["away", "home"][opp]].get("starter") or {}).get("name") or "SP"
        pk = f"{opp}|P:{st}|"
        rows.append((st, g(pk + "outs"), g(pk + "k"), g(pk + "h"), g(pk + "r"), g(pk + "er"), g(pk + "bf")))
    sa, sh = rows[1], rows[0]
    trows = [
        {"label": "Starter", "a": sa[0], "b": sh[0]},
        {"label": "Starter BF / outs (sim)", "a": f"{sa[6]:.1f} / {sa[1]:.1f}", "b": f"{sh[6]:.1f} / {sh[1]:.1f}"},
        {"label": "Starter K (sim)", "a": round(sa[2], 2), "b": round(sh[2], 2)},
        {"label": "Starter H / R / ER (sim)", "a": f"{sa[3]:.1f} / {sa[4]:.1f} / {sa[5]:.1f}", "b": f"{sh[3]:.1f} / {sh[4]:.1f} / {sh[5]:.1f}"},
        {"label": "Bullpen batters faced (sim)", "a": round(sum(t.mean(k) for k in t.sum if k.startswith("0|P:") and k.endswith("|bf")) - sa[6], 1),
         "b": round(sum(t.mean(k) for k in t.sum if k.startswith("1|P:") and k.endswith("|bf")) - sh[6], 1)},
    ]
    env = spec.get("env") or {}
    A, H = spec["away"], spec["home"]
    _, ta, wa = mlb_staff(A)
    _, th, wh = mlb_staff(H)

    def sp(t_, k):
        return (t_.get("starter") or {}).get(k)

    inputs = [
        {"label": "HR environment factor", "a": round(mlb_env_hr(env), 3), "b": round(mlb_env_hr(env), 3)},
        {"label": "Starter throws / K% / BB%", "a": f"{sp(A,'throws') or '?'} / {sp(A,'k_pct')} / {sp(A,'bb_pct')}", "b": f"{sp(H,'throws') or '?'} / {sp(H,'k_pct')} / {sp(H,'bb_pct')}"},
        {"label": "Expected starter BF", "a": sp(A, "bf_per_start"), "b": sp(H, "bf_per_start")},
        {"label": "Bullpen K% / pitches last 3 days", "a": f"{(A.get('bullpen') or {}).get('k_pct')} / {wa if wa is not None else 'n/a'}{' (taxed)' if ta else ''}",
         "b": f"{(H.get('bullpen') or {}).get('k_pct')} / {wh if wh is not None else 'n/a'}{' (taxed)' if th else ''}"},
        {"label": "Extra innings", "a": "postseason: no automatic runner" if is_postseason(spec) else "automatic runner from the 10th", "b": ""},
    ]
    return trows, inputs


# --------------------------------------------------------------------------- NHL
NHL_LG = dict(sv=0.900, sh=0.095, pp=0.21, pk=0.79, pp_opps=3.0, pp_shots=1.4, es_scale=0.92, ot_goal=0.62)
DEFAULT_GOALIE_SA = 300
EN_LEAD1, EN_LEAD2, EN_TRAIL1, EN_TRAIL2 = 0.28, 0.18, 0.10, 0.06


def toi_min(s):
    if isinstance(s, (int, float)):
        return float(s)
    try:
        mm, ss = str(s).split(":")
        return int(mm) + int(ss) / 60
    except Exception:
        return None


def goalie_sv(gl):
    """Save % regressed to last season (800 shots) and the league (.900, 400 shots; 1,200 with no last season)."""
    gl = gl or {}
    sv = num(gl.get("sv_pct"))
    sa = num(gl.get("shots_against"), (num(gl.get("gs"), 0) or 0) * 28) if sv is not None else 0.0
    if sv is not None and not sa:
        sa = DEFAULT_GOALIE_SA  # sample unknown: a moderate one (flagged in data quality)
    sv = sv if sv is not None else NHL_LG["sv"]
    last = num(gl.get("sv_pct_last_season"))
    w_last = 800.0 if last is not None else 0.0
    w_lg = 400.0 if last is not None else 1200.0
    return (sv * sa + (last or 0) * w_last + NHL_LG["sv"] * w_lg) / (sa + w_last + w_lg)


def goalie_start_prob(gl):
    gl = gl or {}
    if num(gl.get("start_prob")) is not None:
        return clamp(num(gl.get("start_prob")), 0.0, 1.0)
    st = str(gl.get("status") or "").lower()
    if st.startswith("confirm"):
        return 1.0
    if st.startswith("project") or "likely" in st:
        return 0.8
    return 0.65


def nhl_skaters(team):
    raw = team.get("skaters") or []
    tois = {"F": [], "D": []}
    for p in raw:
        tm = toi_min(p.get("toi_pg"))
        if tm:
            tois["D" if p.get("pos") == "D" else "F"].append(tm)
    mean_toi = {k: (statistics.mean(v) if v else None) for k, v in tois.items()}
    out = []
    for p in raw:
        gp = max(num(p.get("gp"), 1), 1)
        ls = p.get("last_season") or {}
        lgp = num(ls.get("gp"), 0) or 0
        w = 20.0 if lgp else 0.0
        sog_pg = (num(p.get("sog"), 0) + (num(ls.get("sog"), 0) / lgp * w if lgp else 0)) / (gp + w)
        a_pg = (num(p.get("assists"), 0) + (num(ls.get("assists"), 0) / lgp * w if lgp else 0)) / (gp + w)
        gs = num(p.get("goals"), 0) + num(ls.get("goals"), 0)
        ss = num(p.get("sog"), 0) + num(ls.get("sog"), 0)
        sh = (gs + NHL_LG["sh"] * 100) / (ss + 100)
        grp = "D" if p.get("pos") == "D" else "F"
        tm = toi_min(p.get("toi_pg"))
        toi_mod = clamp((tm / mean_toi[grp]) ** 0.25, 0.9, 1.1) if tm and mean_toi[grp] else 1.0  # small role modifier
        pp1 = bool(p.get("pp1"))
        es_sog = max(sog_pg - (min(0.6, 0.35 * sog_pg) if pp1 else 0.0), 0.15) * toi_mod  # strip PP shots from the ES rate
        out.append(dict(name=p["name"], pos=p.get("pos"), sog_pg=max(sog_pg, 0.2), es_sog=es_sog, a_pg=max(a_pg, 0.05), sh=sh,
                        toi=p.get("toi_pg"), toi_mod=round(toi_mod, 3), pp1=pp1))
    return out


def nhl_factory(spec):
    A, H = spec["away"], spec["home"]
    sk = [nhl_skaters(A), nhl_skaters(H)]
    goalies = []
    for t in (A, H):  # (main, start prob, alt) per team, SV% regressed
        g = t.get("goalie") or {}
        alt = g.get("alt") if isinstance(g.get("alt"), dict) else None
        goalies.append(dict(main=g.get("name") or "Goalie", sv=goalie_sv(g), p=goalie_start_prob(g) if alt else 1.0,
                            alt=(alt.get("name") if alt else None), alt_sv=goalie_sv(alt) if alt else None))
    shots = [(num(A.get("shots_pg"), 29) + num(H.get("shots_against_pg"), 29)) / 2 * 0.99,
             (num(H.get("shots_pg"), 29) + num(A.get("shots_against_pg"), 29)) / 2 * 1.01]

    def pp_rate(t, key, lg):
        v = num(t.get(key))
        n = max(num(t.get("gp"), 0) or 0, 0) * (num(t.get("pp_opps_pg"), NHL_LG["pp_opps"]) or NHL_LG["pp_opps"])
        return (v * n + lg * 40) / (n + 40) if v is not None else lg

    pp = [pp_rate(A, "pp_pct", NHL_LG["pp"]), pp_rate(H, "pp_pct", NHL_LG["pp"])]
    pk = [pp_rate(A, "pk_pct", NHL_LG["pk"]), pp_rate(H, "pk_pct", NHL_LG["pk"])]
    opps = [(num(A.get("pp_opps_pg"), NHL_LG["pp_opps"]) + num(H.get("times_shorthanded_pg"), NHL_LG["pp_opps"])) / 2,
            (num(H.get("pp_opps_pg"), NHL_LG["pp_opps"]) + num(A.get("times_shorthanded_pg"), NHL_LG["pp_opps"])) / 2]
    p_pp = [log5(pp[0], 1 - pk[1], NHL_LG["pp"]), log5(pp[1], 1 - pk[0], NHL_LG["pp"])]
    meta = dict(sk=sk, goalies=goalies, shots=shots, pp=pp, pk=pk, opps=opps, p_pp=p_pp)

    def factory(eff):
        def one(rng):
            box = defaultdict(float)
            goals = [0, 0]
            sv_now = []  # SV% each team's shooters face, after picking tonight's goalie
            for t in (1, 0):
                gd = goalies[t]
                use_alt = gd["alt"] is not None and rng.random() > gd["p"]
                box[f"{t}|G:{gd['alt'] if use_alt else gd['main']}|gp"] += 1
                sv_now.append((gd["alt_sv"] if use_alt else gd["sv"], gd["alt"] if use_alt else gd["main"]))
            # sv_now[0] = goalie faced by away shooters (home goalie), sv_now[1] = by home shooters
            gfac = [((1 - sv_now[s][0]) / (1 - NHL_LG["sv"])) for s in (0, 1)]

            def credit_assists(s, scorer, pp_goal):
                na = 2 if rng.random() < 0.6 else (1 if rng.random() < 0.8 else 0)
                mates = [(j, x["a_pg"] * ((1.0 if x["pp1"] else 0.3) if pp_goal else 1.0)) for j, x in enumerate(sk[s]) if j != scorer]
                used = set()
                for _ in range(na):
                    pool = [(j, w) for j, w in mates if j not in used]
                    tot = sum(w for _, w in pool)
                    j = pick(rng, [(j, w / tot) for j, w in pool]) if tot else None
                    if j is None:
                        break
                    used.add(j)
                    box[f"{s}|{sk[s][j]['name']}|a"] += 1

            def shooter(s, pp_now):
                ws = [(i, (x["sog_pg"] * (1.0 if x["pp1"] else 0.25)) if pp_now else x["es_sog"]) for i, x in enumerate(sk[s])]
                tot = sum(w for _, w in ws)
                share = 0.9 if pp_now else min(tot / max(shots[s], 1), 0.92)
                return pick(rng, [(i, w / tot * share) for i, w in ws]) if tot else None

            def goal(s, i, pp_goal):
                goals[s] += 1
                box[f"{s}|team_{'pp' if pp_goal else 'es'}g"] += 1
                if i is not None:
                    box[f"{s}|{sk[s][i]['name']}|g"] += 1
                credit_assists(s, i, pp_goal)

            def shot(s, i, opp_goalie):
                box[f"{s}|team_sog"] += 1
                box[f"{1-s}|G:{opp_goalie}|sa"] += 1
                if i is not None:
                    box[f"{s}|{sk[s][i]['name']}|sog"] += 1

            for s in (0, 1):
                gname = sv_now[s][1]
                noise = rng.gammavariate(25, 1 / 25)
                # power plays: regressed PP% vs regressed PK%, goalie-adjusted (square root: conservative)
                n_pp = poisson(rng, opps[s])
                pp_p = clamp(p_pp[s] * gfac[s] ** 0.5 * eff[s], 0.03, 0.45)
                for _ in range(n_pp):
                    box[f"{s}|team_ppo"] += 1
                    scored = rng.random() < pp_p
                    n_sh = max(poisson(rng, NHL_LG["pp_shots"]), 1 if scored else 0)
                    shooters = [shooter(s, True) for _ in range(n_sh)]
                    for k, i in enumerate(shooters):
                        shot(s, i, gname)
                        if scored and k == len(shooters) - 1:
                            goal(s, i, True)
                        else:
                            box[f"{1-s}|G:{gname}|sv"] += 1
                # even strength
                n_es = poisson(rng, max(shots[s] - NHL_LG["pp_shots"] * opps[s], 10) * noise)
                for _ in range(n_es):
                    i = shooter(s, False)
                    shp = (sk[s][i]["sh"] if i is not None else 0.075) * NHL_LG["es_scale"] * gfac[s] * eff[s]
                    shot(s, i, gname)
                    if rng.random() < shp:
                        goal(s, i, False)
                    else:
                        box[f"{1-s}|G:{gname}|sv"] += 1
            # empty-net / late extra-attacker approximation
            d = goals[1] - goals[0]
            if abs(d) in (1, 2):
                lead = 1 if d > 0 else 0
                r = rng.random()
                p_lead, p_trail = (EN_LEAD1, EN_TRAIL1) if abs(d) == 1 else (EN_LEAD2, EN_TRAIL2)
                if r < p_lead:  # empty net: a shot and a goal, no goalie in net
                    i = shooter(lead, False)
                    box[f"{lead}|team_sog"] += 1
                    if i is not None:
                        box[f"{lead}|{sk[lead][i]['name']}|sog"] += 1
                    goal(lead, i, False)
                    box[f"{lead}|team_eng"] += 1
                elif r < p_lead + p_trail:
                    i = shooter(1 - lead, False)
                    shot(1 - lead, i, sv_now[1 - lead][1])
                    goal(1 - lead, i, False)
            ot = so = 0
            if goals[0] == goals[1]:
                ot = 1
                rate = [shots[s] * NHL_LG["sh"] * gfac[s] * eff[s] for s in (0, 1)]
                if rng.random() < NHL_LG["ot_goal"]:  # 3v3 OT goal (approximation)
                    w = 1 if rng.random() < rate[1] / (rate[0] + rate[1]) else 0
                    i = shooter(w, False)
                    shot(w, i, sv_now[w][1])
                    goal(w, i, False)
                else:  # shootout (approximation): winner gets +1 goal in the final, as books grade; no player credit
                    so = 1
                    ph = clamp(0.5 + (sv_now[0][0] - sv_now[1][0]) * 3, 0.4, 0.6)
                    goals[1 if rng.random() < ph else 0] += 1
            out = {"away_pts": goals[0], "home_pts": goals[1], "total": goals[0] + goals[1], "margin": goals[1] - goals[0],
                   "home_win": 1.0 if goals[1] > goals[0] else 0.0, "away_win": 1.0 if goals[0] > goals[1] else 0.0, "tie": 0.0,
                   "ot": float(ot), "shootout": float(so)}
            out.update(box)
            for s in (0, 1):
                for x in sk[s]:
                    k = f"{s}|{x['name']}|"
                    if k + "g" in box or k + "a" in box:
                        out[k + "pts"] = box.get(k + "g", 0) + box.get(k + "a", 0)
            return out
        return one
    return factory, meta


NHL_STATS = ("sog", "g", "a", "pts")


def nhl_tables(spec, t, meta):
    g = lambda k: t.mean(k)
    A, H = spec["away"], spec["home"]
    gl = meta["goalies"]

    def gline(team_idx):
        d = gl[team_idx]
        main = f"{d['main']} ({g(f'{team_idx}|G:' + d['main'] + '|gp'):.0%} of games)"
        return main + (f", {d['alt']} otherwise" if d["alt"] else "")

    trows = [
        {"label": "Shots on goal (sim)", "a": round(g("0|team_sog"), 1), "b": round(g("1|team_sog"), 1)},
        {"label": "PP chances / PP goals (sim)", "a": f"{g('0|team_ppo'):.2f} / {g('0|team_ppg'):.2f}", "b": f"{g('1|team_ppo'):.2f} / {g('1|team_ppg'):.2f}"},
        {"label": "Even-strength goals (sim)", "a": round(g("0|team_esg"), 2), "b": round(g("1|team_esg"), 2)},
        {"label": "Goalie in net (sim)", "a": gline(0), "b": gline(1)},
    ]
    inputs = [
        {"label": "Shot volume input", "a": round(meta["shots"][0], 1), "b": round(meta["shots"][1], 1)},
        {"label": "Opp goalie SV% (regressed)", "a": f"{gl[1]['sv']:.3f}", "b": f"{gl[0]['sv']:.3f}"},
        {"label": "PP% / PK% (regressed)", "a": f"{meta['pp'][0]:.3f} / {meta['pk'][0]:.3f}", "b": f"{meta['pp'][1]:.3f} / {meta['pk'][1]:.3f}"},
        {"label": "PP goal chance per opportunity", "a": f"{meta['p_pp'][0]:.1%}", "b": f"{meta['p_pp'][1]:.1%}"},
        {"label": "PP chances per game input", "a": round(meta["opps"][0], 2), "b": round(meta["opps"][1], 2)},
    ]
    return trows, inputs


# --------------------------------------------------------------------------- player distributions
def player_keys(spec):
    """(side, name, pos, kind, {public stat: internal key or tuple of keys to sum}) for every tracked player."""
    sport = spec["sport"]
    out = []
    for s, side in ((0, "away"), (1, "home")):
        t = spec[side]
        if sport in ("NFL", "CFB"):
            for p in t.get("players") or []:
                st = {pub: f"{s}|{p['name']}|{k}" for k, pub in FB_STATS}
                st["td"] = (f"{s}|{p['name']}|rutd", f"{s}|{p['name']}|rtd")
                want = FB_POS_STATS.get(p.get("pos"), FB_POS_STATS["WR"])
                out.append((side, p["name"], p.get("pos"), "player", p, {k: v for k, v in st.items() if k in want}))
        elif sport == "MLB":
            for b in t.get("lineup") or []:
                out.append((side, b["name"], b.get("pos"), "batter", b, {k: f"{s}|{b['name']}|{k}" for k in MLB_STATS}))
            opp = 1 - s
            stn = (t.get("starter") or {}).get("name")
            if stn:
                out.append((side, stn, "SP", "pitcher", t.get("starter"), {k: f"{s}|P:{stn}|{k}" for k in MLB_PSTATS}))
        elif sport == "NHL":
            for x in t.get("skaters") or []:
                out.append((side, x["name"], x.get("pos"), "skater", x, {k: f"{s}|{x['name']}|{k}" for k in NHL_STATS}))
            g = t.get("goalie") or {}
            if g.get("name"):
                out.append((side, g["name"], "G", "goalie", g, {"saves": f"{s}|G:{g['name']}|sv", "_gp": f"{s}|G:{g['name']}|gp"}))  # goalie's own team
    return out


def dist_keys_for(plist):
    keys = []
    for *_, stats in plist:
        for v in stats.values():
            keys.extend(v if isinstance(v, tuple) else (v,))
    return list(dict.fromkeys(keys))


def spec_lines(spec):
    """Prop lines supplied in the spec: player['lines'] = {stat: line | [lines]} and/or
    spec['props'] = [{player, stat, line | lines, odds?}]."""
    lines = defaultdict(list)
    for side in ("away", "home"):
        t = spec[side]
        for p in (t.get("players") or []) + (t.get("lineup") or []) + (t.get("skaters") or []) + [t.get("starter") or {}, t.get("goalie") or {}]:
            for stat, v in (p.get("lines") or {}).items():
                for ln in (v if isinstance(v, list) else [v]):
                    if num(ln) is not None:
                        lines[(p.get("name"), stat)].append({"line": num(ln)})
    for pr in spec.get("props") or []:
        for ln in (pr.get("lines") or [pr.get("line")]):
            if num(ln) is not None:
                lines[(pr.get("player"), pr.get("stat"))].append({"line": num(ln), "odds": pr.get("odds"), "book": pr.get("book")})
    return lines


def player_distributions(spec, t):
    plist = player_keys(spec)
    lines = spec_lines(spec)
    out = {"away": [], "home": []}
    for side, name, pos, kind, src, stats in plist:
        dists = {}
        started = list(t.dist[stats["_gp"]]) if "_gp" in stats else None
        for pub, key in stats.items():
            if pub.startswith("_"):
                continue
            if isinstance(key, tuple):
                arrs = [t.dist[k] for k in key]
                vals = [sum(x) for x in zip(*arrs)]
            else:
                vals = list(t.dist[key])
            if started is not None:  # goalie props are void if he doesn't start: only games he started
                vals = [v for v, g in zip(vals, started) if g >= 1]
            if not vals:
                continue
            d = dist_summary(vals)
            if d["mean"] is not None and (d["mean"] >= 0.02 or (name, pub) in lines):
                dists[pub] = d
                for ln in lines.get((name, pub), []):
                    lp = line_probs(vals, ln["line"])
                    lp.update({k: v for k, v in ln.items() if k not in ("line",) and v is not None})
                    dists[pub].setdefault("lines", []).append(lp)
        if dists:
            row = {"name": name, "pos": pos, "kind": kind, "stats": dists}
            if started is not None:
                row["start_freq"] = round(sum(1 for g in started if g >= 1) / max(len(started), 1), 3)
            out[side].append(row)
    return out


def _fmt(x, d=0):
    return "—" if x is None else (f"{x:.{d}f}" if isinstance(x, float) and d else f"{x:g}" if isinstance(x, (int, float)) else str(x))


def box_rows(spec, players, t):
    """Top 5 per side for the board, backward compatible with v1 rows (cells/line), plus ranges and lines."""
    sport = spec["sport"]
    box = {}
    for side, s in (("away", 0), ("home", 1)):
        team = spec[side]
        rows = []
        for p in players[side]:
            st, n = p["stats"], p["name"]
            m = lambda k: (st.get(k) or {}).get("mean") or 0.0
            rng80 = lambda k: f"{_fmt(st[k]['p10'])}–{_fmt(st[k]['p90'])}" if k in st else "—"
            props = [dict(stat=k, **ln) for k, d in st.items() for ln in d.get("lines", [])]
            if sport in ("NFL", "CFB"):
                if p["kind"] != "player":
                    continue
                src = next((x for x in team.get("players") or [] if x["name"] == n), {})
                snap = src.get("snap_pct")
                if isinstance(snap, list):
                    snap = statistics.mean([x for x in snap if x is not None] or [0])
                plays = max(t.mean(f"{s}|team_plays"), 1)
                drop = max(t.mean(f"{s}|team_dropbacks"), 1)
                usage = []
                if m("carries") >= 1:
                    usage.append(f"{round(m('carries')/plays*100)}% of plays rush")
                if m("targets") >= 0.5:
                    usage.append(f"{round(m('targets')/drop*100)}% tgt")
                if p["pos"] == "QB":
                    line = f"{m('comp'):.1f}/{m('pass_att'):.1f}, {m('pass_yds'):.0f} yds, {m('pass_td'):.1f} TD, {m('int'):.1f} INT"
                    if m("carries") >= 1:
                        line += f"; {m('carries'):.1f} car {m('rush_yds'):.0f} yds"
                    rank, rg = 1e6, f"Pass yds 80%: {rng80('pass_yds')} · median {_fmt(st.get('pass_yds', {}).get('median'))}"
                else:
                    parts = []
                    if m("carries") >= 0.5:
                        parts.append(f"{m('carries'):.1f} car {m('rush_yds'):.0f} yds")
                    if m("targets") >= 0.3:
                        parts.append(f"{m('rec'):.1f}/{m('targets'):.1f} rec {m('rec_yds'):.0f} yds")
                    parts.append(f"{m('td'):.2f} TD")
                    line = ", ".join(parts)
                    rank = m("rush_yds") + m("rec_yds")
                    main = "rush_yds" if m("rush_yds") > m("rec_yds") else "rec_yds"
                    any_td = next((ln["over"] for ln in st.get("td", {}).get("lines", []) if ln["line"] == 0.5), None)
                    rg = f"{'Rush' if main == 'rush_yds' else 'Rec'} yds 80%: {rng80(main)} · median {_fmt(st.get(main, {}).get('median'))}"
                    if any_td is None and "td" in st:
                        any_td = sum(1 for v in zip(*(t.dist[k] for k in (f'{s}|{n}|rutd', f'{s}|{n}|rtd'))) if sum(v) >= 1) / t.n
                    if any_td is not None:
                        rg += f" · 1+ TD {any_td:.0%} (sim freq)"
                row = {"name": n, "pos": p["pos"], "snap": f"{round(num(snap)*100)}%" if num(snap) is not None else "—",
                       "usage": ", ".join(usage) or "—", "line": line, "range": rg, "props": props, "_rank": rank,
                       "td": round(m("pass_td") if p["pos"] == "QB" else m("td"), 2)}
                cols = ["Player", "Pos", "Snap %", "Usage (sim)", "Projected line"]
            elif sport == "MLB":
                if p["kind"] != "batter":
                    continue
                h1 = sum(1 for v in t.dist[f"{s}|{n}|h"] if v >= 1) / t.n
                row = {"name": n, "pos": p["pos"], "pa": round(m("pa"), 2), "h": round(m("h"), 2), "tb": round(m("tb"), 2), "hr": round(m("hr"), 3),
                       "r": round(m("r"), 2), "rbi": round(m("rbi"), 2),
                       "line": f"{m('h'):.2f} H, {m('tb'):.2f} TB, {m('r'):.2f} R, {m('rbi'):.2f} RBI, {m('hr'):.2f} HR",
                       "range": f"1+ H {h1:.0%} · TB 80%: {rng80('tb')} · HR {sum(1 for v in t.dist[f'{s}|{n}|hr'] if v >= 1)/t.n:.0%} (sim freq)",
                       "props": props, "_rank": m("h") + m("r") + m("rbi")}
                cols = ["Player", "Pos", "PA", "Projected line"]
            else:
                if p["kind"] != "skater":
                    continue
                src = next((x for x in team.get("skaters") or [] if x["name"] == n), {})
                p1 = sum(1 for v in t.dist[f"{s}|{n}|pts"] if v >= 1) / t.n
                g1 = sum(1 for v in t.dist[f"{s}|{n}|g"] if v >= 1) / t.n
                row = {"name": n, "pos": p["pos"], "toi": src.get("toi_pg"), "pp1": bool(src.get("pp1")),
                       "sog": round(m("sog"), 2), "g": round(m("g"), 3), "a": round(m("a"), 3), "pts": round(m("pts"), 3),
                       "line": f"{m('sog'):.2f} SOG, {m('g'):.2f} G, {m('a'):.2f} A, {m('pts'):.2f} PTS",
                       "range": f"SOG 80%: {rng80('sog')} · 1+ pt {p1:.0%} · goal {g1:.0%} (sim freq)", "props": props,
                       "_rank": m("pts") + m("sog") * 0.05}
                cols = ["Player", "Pos", "TOI", "PP1", "Projected line"]
            rows.append(row)
        rows.sort(key=lambda r: -r["_rank"])
        for r in rows:
            r.pop("_rank", None)
        cols = {"MLB": ["Player", "Pos", "PA", "Projected line"], "NHL": ["Player", "Pos", "TOI", "PP1", "Projected line"]}.get(sport, ["Player", "Pos", "Snap %", "Usage (sim)", "Projected line"])
        keys = {"Player": "name", "Pos": "pos", "Snap %": "snap", "Usage (sim)": "usage", "Projected line": "line", "PA": "pa", "TOI": "toi", "PP1": "pp1"}
        for r in rows[:5]:
            r["cells"] = [("PP1" if r.get("pp1") else "") if c == "PP1" else r.get(keys[c], "") for c in cols]
        box[side] = {"cols": cols, "rows": rows[:5]}
    return box


# --------------------------------------------------------------------------- driver
def validate_spec(spec):
    if not isinstance(spec, dict):
        raise ValueError("malformed spec: expected a JSON object")
    sport = spec.get("sport")
    if sport not in SUPPORTED:
        raise ValueError(f"malformed spec: sport {sport!r} has no engine (supported: {', '.join(SUPPORTED)})")
    for side in ("away", "home"):
        if not isinstance(spec.get(side), dict):
            raise ValueError(f"malformed spec: missing '{side}' team block")
    if sport == "MLB":
        for side in ("away", "home"):
            if not isinstance(spec[side].get("lineup", []), list):
                raise ValueError(f"malformed spec: {side}.lineup must be a list")


def _ci(mean, se):
    return [round(mean - 1.96 * se, 3), round(mean + 1.96 * se, 3)]


def simulate(spec, seed=7, w=ANCHOR_W, batch=BATCH, max_batches=MAX_BATCHES, min_batches=MIN_BATCHES, pilot=PILOT):
    validate_spec(spec)
    rng = random.Random(seed)
    sport = spec["sport"]
    quality, unknown = assess_quality(spec)
    w_in = num(w, ANCHOR_W)
    w = clamp(w_in, 0.0, 1.0)
    if w != w_in:
        quality.add("info", "anchor", f"Anchor weight {w_in} clamped to {w}.")
    meta = None
    if sport in ("NFL", "CFB"):
        factory = football_factory(spec)
    elif sport == "MLB":
        factory = mlb_factory(spec)
    else:
        factory, meta = nhl_factory(spec)
    market = spec.get("market") or {}
    base_eff = (MLB_FINISH,) * 2 if sport == "MLB" else (1.0, 1.0)  # football's finishing factor is applied inside the drive sim
    eff, raw, target, baseline = calibrate(factory, sport, market, rng, base_eff=base_eff, w=w, pilot=pilot)
    plist = player_keys(spec)
    t, runs = run_until_stable(factory(eff), rng, dist_keys=dist_keys_for(plist), batch=batch, max_batches=max_batches, min_batches=min_batches)
    n = t.n
    tot = sorted(t.samples["total"])
    mar = list(t.samples["margin"])
    mean_a, mean_h, mean_t, mean_m = t.mean("away_pts"), t.mean("home_pts"), t.mean("total"), t.mean("margin")
    hw, aw, tie = t.mean("home_win"), t.mean("away_win"), t.mean("tie")
    score = {
        "label": LABEL, "frequency_note": FREQ_NOTE,
        "away": round(mean_a, 2), "home": round(mean_h, 2), "total": round(mean_t, 2), "margin": round(mean_m, 2),
        "total_median": quant(tot, 0.5), "total_p10_p90": [quant(tot, 0.1), quant(tot, 0.9)],
        "home_win": round(hw, 3), "away_win": round(aw, 3), "tie_rate": round(tie, 4), "ot_rate": round(t.mean("ot"), 3),
        "raw": {"away": round(raw[0], 2), "home": round(raw[1], 2)},
        "anchor": {"weight": w, "market_implied": [round(baseline["away"], 2), round(baseline["home"], 2)] if baseline and baseline["away"] is not None else None,
                   "efficiency": [round(x, 3) for x in eff]},
    }
    if sport == "NHL":
        score["shootout_rate"] = round(t.mean("shootout"), 3)
    sim_unc = {"label": "Simulation uncertainty",
               "note": "Monte Carlo error from a finite number of simulated games. It shrinks with more runs and says nothing about whether the inputs or the model are right.",
               "n": n,
               "total": {"mean": round(mean_t, 3), "se": round(t.se("total"), 3), "ci95": _ci(mean_t, t.se("total"))},
               "margin": {"mean": round(mean_m, 3), "se": round(t.se("margin"), 3), "ci95": _ci(mean_m, t.se("margin"))},
               "home_win": {"freq": round(hw, 4), "se": round(prop_se(hw, n), 4), "ci95": _ci(hw, prop_se(hw, n))}}
    mt = num(market.get("total"))
    if mt is not None:
        lp = line_probs(tot, mt)
        score.update(over=lp["over"], under=lp["under"], push=lp["push"])
        sim_unc["over"] = {"freq": lp["over"], "se": round(prop_se(lp["over"], n), 4), "ci95": _ci(lp["over"], prop_se(lp["over"], n))}
    sh = num(market.get("spread_home"))
    if sh is not None:
        cov = sum(1 for x in mar if x + sh > 0) / n
        push = sum(1 for x in mar if x + sh == 0) / n
        score.update(home_cover=round(cov, 3), cover_push=round(push, 3))
        sim_unc["home_cover"] = {"freq": round(cov, 4), "se": round(prop_se(cov, n), 4), "ci95": _ci(cov, prop_se(cov, n))}
    q = quality.out(unknown)
    model_unc = {"label": "Model / data uncertainty", "status": q["status"],
                 "note": "Not measured by the simulation: small samples, projected lineups/goalies, missing inputs and the engine's simplifications. "
                         "Boolin's research/model layer stays the authority for research conclusions.",
                 "items": [f"{f['field']}: {f['note']}" for f in q["flags"] if f["level"] != "info"][:8]}
    # RAW -> MARKET IMPLIED -> ANCHORED
    mk_a = baseline["away"] if baseline and baseline["away"] is not None else None
    mk_h = baseline["home"] if baseline and baseline["home"] is not None else None
    audit = {"label": LABEL, "weight": w,
             "raw": {"away": round(raw[0], 2), "home": round(raw[1], 2), "total": round(raw[0] + raw[1], 2), "label": "Raw simulation"},
             "market_implied": ({"away": None if mk_a is None else round(mk_a, 2), "home": None if mk_h is None else round(mk_h, 2),
                                 "total": baseline["total"], "basis": baseline["basis"], "label": "Market-implied baseline"} if baseline else None),
             "target": {"away": round(target[0], 2), "home": round(target[1], 2)} if target else None,
             "anchored": {"away": round(mean_a, 2), "home": round(mean_h, 2), "total": round(mean_t, 2), "label": "Anchored simulation (final mean)"},
             "shift": {"away": round(mean_a - raw[0], 2), "home": round(mean_h - raw[1], 2), "total": round(mean_t - raw[0] - raw[1], 2)},
             "target_miss": {"away": round(mean_a - target[0], 2), "home": round(mean_h - target[1], 2)} if target else None,
             "efficiency": [round(x, 3) for x in eff],
             "method": ("Scoring efficiency (yards per play, hit/HR rates or shooting %) is scaled per team so the mean lands weight × (market − raw) from the raw simulation. "
                        "Volume and usage (plays, pass rate, target/carry/shot shares, lineup, TOI, PP1) are unchanged; player props are never back-solved to a line.")
             if baseline and w > 0 else ("No market total supplied: raw simulation only, no anchor." if not baseline else "Anchor weight 0: raw simulation only.")}
    # SIMULATION / MARKET DIFFERENCE
    drow = []
    mhw = market_home_win(market)
    cols = [spec["away"].get("abbr") or "Away", spec["home"].get("abbr") or "Home"]
    drow.append({"label": f"{cols[1]} win", "sim": round(hw, 3), "market": None if mhw is None else round(mhw, 3),
                 "diff": None if mhw is None else round(hw - mhw, 3), "unit": "freq vs no-vig",
                 "note": None if mhw is not None else "no moneyline supplied"})
    if mt is not None:
        po = no_vig(market.get("over_price"), market.get("under_price"))
        drow.append({"label": "Projected total", "sim": round(mean_t, 2), "market": mt, "diff": round(mean_t - mt, 2), "unit": "points"})
        drow.append({"label": f"Over {mt:g}", "sim": score["over"], "market": None if po is None else round(po, 3),
                     "diff": None if po is None else round(score["over"] - po, 3), "unit": "freq vs no-vig",
                     "note": None if po is not None else "no over/under prices supplied"})
    if sh is not None:
        drow.append({"label": f"{cols[1]} margin", "sim": round(mean_m, 2), "market": -sh, "diff": round(mean_m + sh, 2), "unit": "points"})
        drow.append({"label": f"{cols[1]} {sh:+g} covers", "sim": score["home_cover"], "market": None, "diff": None, "unit": "freq",
                     "note": "market cover price not supplied" if no_vig(market.get("home_spread_price"), market.get("away_spread_price")) is None else None})
        pc = no_vig(market.get("home_spread_price"), market.get("away_spread_price"))
        if pc is not None:
            drow[-1].update(market=round(pc, 3), diff=round(score["home_cover"] - pc, 3), note=None)
    diag = {"label": "Simulation / market difference", "note": DIFF_NOTE, "cols": cols, "rows": drow}
    if sport in ("NFL", "CFB"):
        team_rows, inputs = football_tables(spec, t, n)
    elif sport == "MLB":
        team_rows, inputs = mlb_tables(spec, t)
    else:
        team_rows, inputs = nhl_tables(spec, t, meta)
    players = player_distributions(spec, t)
    box = box_rows(spec, players, t)
    badges = ["STABLE" if runs["converged"] else "NOT STABLE"]
    if q["status"] in ("degraded", "unknown"):
        badges.append("DEGRADED INPUTS")
    elif q["status"] == "partial":
        badges.append("PARTIAL INPUTS")
    notes = spec.get("notes")
    return {
        "engine": ENGINE, "label": LABEL, "sport": sport, "game": spec.get("game"), "seed": seed,
        "status": badges, "runs": runs, "score": score, "anchor_audit": audit,
        "uncertainty": {"simulation": sim_unc, "model_data": model_unc},
        "market_diagnostic": diag, "data_quality": q,
        "team": {"cols": cols, "rows": team_rows},
        "inputs": {"cols": cols, "rows": inputs},
        "box": box, "players": players,
        "notes": (notes or [])[:6] if isinstance(notes, list) else ([str(notes)] if notes else []),
        "caveat": ("Market-anchored simulation: averages and frequencies over the simulated games, not a prediction of one game and not a validated "
                   "probability. Stable means the numbers stopped moving, not that they are accurate. Never a bet by itself; never copied into modelP."),
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("specs", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--anchor", type=float, default=ANCHOR_W)
    a = ap.parse_args(argv)
    out = {}
    for p in a.specs:
        spec = json.load(open(p))
        r = simulate(spec, seed=a.seed, w=a.anchor)
        out[spec["game"]] = r
        sc, ru = r["score"], r["runs"]
        print(f"{spec['game']}: {sc['away']}-{sc['home']} (total {sc['total']}), {ru['batches']} runs / {ru['sims']} sims, "
              f"{' · '.join(r['status'])}, inputs {r['data_quality']['status']}", file=sys.stderr)
    json.dump(out, open(a.out, "w"), indent=1)


if __name__ == "__main__":
    main()
