"""Monte Carlo game simulator for the Top 10 cards.

    python -m pipeline.sim SPEC.json [SPEC2.json ...] --out sims.json [--seed 7]

Each SPEC is one game (see docs at the bottom of TASK.md, "Game sims"). The engine plays the game
out in batches of 100 simulated games, up to 100 batches (10,000 games), and stops once the box
score stops moving: every tracked average changed by less than 1% (or 0.05 in absolute terms) for
3 batches in a row AND the game total's standard error is under 1% of its mean. The output says
how many batches/sims it took.

More runs make the averages *stable*, not more *accurate*: a sim is only as good as its inputs.
Team scoring is blended toward the market (anchor weight 0.5 by default), because the market is
the anchor (TASK.md section 00). The raw (unanchored) score is kept in the output.

Sports:
  NFL / CFB  play-by-play drive sim: predicted plays from pace, pass rate, run/pass efficiency
             (offense x defense / league), sacks and pressure, blitz (sack rate + quick-target tilt
             to RB/TE), coverage (man-heavy tilts targets to the WR1, capped), wind/rain,
             target and carry shares from usage, field goals and punts by field position.
  MLB        plate-appearance sim: log5 batter vs pitcher for K/BB/HR/hits, starter leash then
             bullpen, park and weather HR factors, simple base running.
  NHL        shot sim: team shot volume, shooter share and regressed shooting %, goalie SV%
             (regressed to last season), assists by assist rate, 3v3 OT and shootout.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from collections import defaultdict

BATCH = 100
MAX_BATCHES = 100
MIN_BATCHES = 5
TOL = 0.01
ABS_TOL = 0.05
STREAK = 3
ANCHOR_W = 0.5
ENGINE = "boolin-sim v1"


# --------------------------------------------------------------------------- helpers
def num(x, default=None):
    try:
        if x is None:
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


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
    b, p, lg = min(max(b, 1e-4), 0.999), min(max(p, 1e-4), 0.999), min(max(lg, 1e-4), 0.999)
    x = (b * p / lg) / (b * p / lg + (1 - b) * (1 - p) / (1 - lg))
    return x


def implied_points(sport, market):
    """Market-implied (away, home) points/runs/goals, or None."""
    if not market:
        return None
    total = num(market.get("total"))
    if total is None:
        return None
    sh = num(market.get("spread_home"))
    if sh is not None:  # football: home spread (negative = home favored)
        return (total + sh) / 2.0, (total - sh) / 2.0
    hml, aml = num(market.get("home_ml")), num(market.get("away_ml"))

    def imp(o):
        return 100 / (o + 100) if o > 0 else -o / (-o + 100)

    if hml is None:
        return total / 2.0, total / 2.0
    ph = imp(hml)
    if aml is not None:
        ph = ph / (ph + imp(aml))
    ex = 1.83 if sport == "MLB" else 2.0
    # solve Pythagorean share: ph = h^ex / (h^ex + a^ex) with h + a = total
    r = (ph / (1 - ph)) ** (1 / ex)
    h = total * r / (1 + r)
    return total - h, h


# --------------------------------------------------------------------------- convergence
class Tracker:
    def __init__(self):
        self.sum = defaultdict(float)
        self.sq = defaultdict(float)
        self.n = 0
        self.samples = defaultdict(list)

    def add(self, flat: dict, keep=("total", "margin")):
        self.n += 1
        for k, v in flat.items():
            self.sum[k] += v
            self.sq[k] += v * v
        for k in keep:
            if k in flat:
                self.samples[k].append(flat[k])

    def means(self):
        return {k: v / self.n for k, v in self.sum.items()}

    def se(self, k):
        if self.n < 2:
            return float("inf")
        m = self.sum[k] / self.n
        var = max(self.sq[k] / self.n - m * m, 0.0)
        return math.sqrt(var / self.n)


def run_until_stable(sim_one, rng, batch=BATCH, max_batches=MAX_BATCHES):
    t = Tracker()
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
                pv = prev.get(k, 0.0)
                d = abs(v - pv)
                moves.append(0.0 if d <= ABS_TOL else d / max(abs(v), 1e-9))
            max_move = max(moves) if moves else 0.0
            streak = streak + 1 if max_move < TOL else 0
        prev = cur
        se_ok = t.se("total") <= TOL * max(abs(cur.get("total", 0)), 1e-9)
        if b >= MIN_BATCHES and streak >= STREAK and se_ok:
            converged = True
            break
    runs = {
        "batches": batches, "batch_size": batch, "sims": batches * batch, "max_batches": max_batches,
        "converged": converged, "last_batch_max_move": round(max_move, 4) if max_move is not None else None,
        "total_se": round(t.se("total"), 3),
        "rule": f"Batches of {batch} until every average moves <{int(TOL*100)}% (or <{ABS_TOL}) for {STREAK} straight batches and the total's SE <{int(TOL*100)}% of the mean; cap {max_batches} batches.",
    }
    return t, runs


def calibrate(sim_factory, sport, market, rng, base_eff=(1.0, 1.0), w=ANCHOR_W, pilot=400):
    """Find per-team efficiency multipliers so the mean score sits w of the way from the raw
    model to the market-implied score. Returns (eff, raw_means, target)."""
    imp = implied_points(sport, market)
    eff = list(base_eff)

    def pilot_means(e):
        f = sim_factory(tuple(e))
        a = h = 0.0
        for _ in range(pilot):
            r = f(rng)
            a += r["away_pts"]
            h += r["home_pts"]
        return a / pilot, h / pilot

    raw = pilot_means(eff)
    if imp is None or w <= 0:
        return tuple(eff), raw, None
    target = (w * imp[0] + (1 - w) * raw[0], w * imp[1] + (1 - w) * raw[1])
    cur = raw
    for _ in range(4):
        for i in (0, 1):
            if cur[i] > 0.05:
                eff[i] *= (target[i] / cur[i]) ** (1.0 if sport in ("NFL", "CFB") else 0.9)
            eff[i] = min(max(eff[i], 0.5), 1.8)
        cur = pilot_means(eff)
        if all(abs(cur[i] - target[i]) / max(target[i], 0.5) < 0.02 for i in (0, 1)):
            break
    return tuple(eff), raw, target


# --------------------------------------------------------------------------- football
LEAGUE = {
    "CFB": dict(ypc=5.0, ypa=7.3, comp=0.62, sack=0.06, int=0.025, fum=0.008, fg_max=52, punt_net=39, start=26, finish=1.10),
    "NFL": dict(ypc=4.3, ypa=6.9, comp=0.65, sack=0.067, int=0.022, fum=0.007, fg_max=56, punt_net=42, start=29, finish=1.04),
}


def football_profile(off, dfn, sport, env):
    lg = LEAGUE[sport]
    d = off.get("def_of_opp") or dfn.get("def") or {}
    ypc = num(off.get("ypc"), lg["ypc"]) * num(d.get("ypc_allowed"), lg["ypc"]) / lg["ypc"]
    ypa = num(off.get("ypa"), lg["ypa"]) * num(d.get("ypa_allowed"), lg["ypa"]) / lg["ypa"]
    comp = log5(num(off.get("comp_pct"), lg["comp"]), num(d.get("comp_pct_allowed"), lg["comp"]), lg["comp"])
    sack = log5(num(off.get("sack_rate"), lg["sack"]), num(d.get("sack_rate"), lg["sack"]), lg["sack"])
    intr = log5(num(off.get("int_rate"), lg["int"]), num(d.get("int_rate"), lg["int"]), lg["int"])
    pressure = num(d.get("pressure_pct"))
    if pressure is not None:
        sack *= min(max(pressure / 0.32, 0.8), 1.25)
    blitz = num(d.get("blitz_pct"))
    man = num(d.get("man_pct"))
    pass_rate = num(off.get("pass_rate"), 0.55)
    plays = num(off.get("plays_pg"), 65.0)
    pa = num(d.get("plays_pg_allowed"))
    if pa:
        plays = (plays + pa) / 2.0
    # weather
    wind = max(num(env.get("wind_mph"), 0) or 0, (num(env.get("gust_mph"), 0) or 0) * 0.6)
    fg_max = lg["fg_max"]
    fg_wind_pen = 0.0
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
    return dict(ypc=ypc, ypa=ypa, comp=max(comp, 0.35), sack=sack, int=intr, blitz=blitz, man=man,
                pass_rate=min(max(pass_rate, 0.3), 0.75), plays=plays, fg_max=fg_max, fg_wind_pen=fg_wind_pen,
                start=lg["start"],
                fum=lg["fum"], punt_net=lg["punt_net"])


def football_usage(team, prof):
    ps = team.get("players") or []
    qb = next((p for p in ps if p.get("pos") == "QB"), None)
    # carries (incl. QB designed runs + scrambles)
    car = {p["name"]: num(p.get("carries"), 0) / max(num(p.get("gp"), 1), 1) for p in ps if num(p.get("carries"), 0) > 0}
    tot_c = sum(car.values()) or 1
    rush = [(n, c / tot_c) for n, c in car.items()]
    # targets: use targets if present else receptions
    tg = {}
    for p in ps:
        if p.get("pos") == "QB":
            continue
        t = num(p.get("targets"))
        if t is None:
            t = num(p.get("rec"), 0) / max(num(p.get("comp_pct_team"), 0) or 0.65, 0.3)
        tg[p["name"]] = t / max(num(p.get("gp"), 1), 1)
    # blitz -> quick targets to RB/TE; man coverage -> WR1 (capped, weakly supported)
    blitz, man = prof.get("blitz"), prof.get("man")
    pos = {p["name"]: p.get("pos") for p in ps}
    if blitz is not None:
        for n in tg:
            if pos.get(n) in ("RB", "TE"):
                tg[n] *= 1 + min(max((blitz - 0.25) * 0.8, -0.1), 0.1)
    if man is not None and tg:
        wr = [n for n in tg if pos.get(n) == "WR"]
        if wr:
            w1 = max(wr, key=lambda n: tg[n])
            tg[w1] *= 1 + min(max((man - 0.30) * 0.3, -0.05), 0.05)
    tot_t = sum(tg.values())
    other = 0.12  # targets to players not listed
    targets = [(n, (1 - other) * t / tot_t) for n, t in tg.items()] if tot_t else []
    # per-player yards per target relative to team
    team_ypt = None
    ypt = {}
    for p in ps:
        if p.get("pos") == "QB":
            continue
        rec, yds = num(p.get("rec"), 0), num(p.get("rec_yds"), 0)
        if rec >= 3:
            ypt[p["name"]] = yds / rec
    if ypt:
        team_ypt = statistics.mean(ypt.values())
    ypr_mult = {n: min(max(v / team_ypt, 0.5), 1.8) for n, v in ypt.items()} if team_ypt else {}
    ypc_mult = {}
    tcar = sum(num(p.get("carries"), 0) for p in ps)
    tyds = sum(num(p.get("rush_yds"), 0) for p in ps)
    if tcar:
        base = tyds / tcar if tyds else 1
        for p in ps:
            c = num(p.get("carries"), 0)
            if c >= 8 and base > 0:
                ypc_mult[p["name"]] = min(max((num(p.get("rush_yds"), 0) / c) / base, 0.4), 1.8)
    return dict(qb=qb["name"] if qb else "QB", rush=rush, targets=targets, ypr_mult=ypr_mult, ypc_mult=ypc_mult)


def pick(rng, pairs):
    r, acc = rng.random(), 0.0
    for n, w in pairs:
        acc += w
        if r <= acc:
            return n
    return None  # unlisted player


def football_factory(spec):
    sport = spec["sport"]
    env = spec.get("env") or {}
    A, H = spec["away"], spec["home"]
    pa = football_profile(A, H, sport, env)
    ph = football_profile(H, A, sport, env)
    ua, uh = football_usage(A, pa), football_usage(H, ph)
    home_edge = 0.0 if env.get("neutral") else 0.02

    def factory(eff):
        def one(rng):
            box = defaultdict(float)
            pts = [0, 0]
            plays = [0, 0]
            clock = 3600.0
            side = rng.random() < 0.5
            yard = pa["start"]  # yards from own goal
            profs = (pa, ph)
            uses = (ua, uh)
            secs = [1800.0 / profs[0]["plays"], 1800.0 / profs[1]["plays"]]
            while clock > 0:
                s = 1 if side else 0
                pr, us = profs[s], uses[s]
                e = eff[s] * (1 + (home_edge if s == 1 else -home_edge))
                down, togo, drive_over = 1, 10, False
                while not drive_over and clock > 0:
                    clock -= secs[s] * rng.uniform(0.75, 1.25)
                    plays[s] += 1
                    pfx = f"{s}|"
                    margin = pts[s] - pts[1 - s]
                    prate = pr["pass_rate"] + (0.08 if margin < -10 and clock < 1500 else 0) - (0.08 if margin > 10 and clock < 1500 else 0)
                    if down == 3 and togo >= 6:
                        prate += 0.25
                    gain, turnover = 0, False
                    if rng.random() < prate:
                        box[pfx + "team_dropbacks"] += 1
                        if rng.random() < pr["sack"]:
                            gain = -7
                            box[pfx + "team_sacks"] += 1
                        else:
                            qb = us["qb"]
                            box[pfx + qb + "|att"] += 1
                            tgt = pick(rng, us["targets"])
                            if tgt:
                                box[pfx + tgt + "|tgt"] += 1
                            if rng.random() < pr["int"]:
                                box[pfx + qb + "|int"] += 1
                                turnover = True
                            elif rng.random() < pr["comp"] - (0.04 if down >= 3 and togo >= 7 else 0):
                                ypc = pr["ypa"] * e / pr["comp"] * (us["ypr_mult"].get(tgt, 1.0) if tgt else 1.0)
                                if down >= 3:  # routes run past the sticks on late downs
                                    ypc = max(ypc, togo * 1.15 + 2)
                                gain = max(gamma_yards(rng, ypc, shape=1.6, offset=3.0), -3)
                                gain = min(gain, 100 - yard)
                                box[pfx + qb + "|cmp"] += 1
                                box[pfx + qb + "|pyds"] += gain
                                if tgt:
                                    box[pfx + tgt + "|rec"] += 1
                                    box[pfx + tgt + "|ryds"] += gain
                                if yard + gain >= 100:
                                    box[pfx + qb + "|ptd"] += 1
                                    if tgt:
                                        box[pfx + tgt + "|rtd"] += 1
                    else:
                        r = pick(rng, us["rush"]) or us["qb"]
                        m = pr["ypc"] * e * us["ypc_mult"].get(r, 1.0)
                        gain = min(gamma_yards(rng, m), 100 - yard)
                        box[pfx + r + "|car"] += 1
                        box[pfx + r + "|ruyds"] += gain
                        if yard + gain >= 100:
                            box[pfx + r + "|rutd"] += 1
                        elif rng.random() < pr["fum"]:
                            turnover = True
                    box[pfx + "team_yds"] += gain
                    if turnover:
                        yard = 100 - (yard + max(gain, 0))
                        drive_over = True
                        side = not side
                        break
                    yard += gain
                    if yard >= 100:
                        pts[s] += 7 if rng.random() < 0.97 else 6
                        drive_over, side, yard = True, not side, profs[1 - s]["start"]
                        break
                    togo -= gain
                    if togo <= 0:
                        down, togo = 1, min(10, 100 - yard)
                        continue
                    down += 1
                    if down > 4:  # failed 4th-down try: turnover on downs
                        yard = 100 - yard
                        drive_over, side = True, not side
                        break
                    if down == 4:
                        dist = 100 - yard + 17
                        if dist <= pr["fg_max"]:
                            if sport == "NFL":
                                pmake = 0.99 - max(dist - 25, 0) * 0.011
                            else:
                                pmake = 0.97 - max(dist - 20, 0) * 0.014
                            pmake = min(0.99, max(0.2, pmake - pr["fg_wind_pen"] * max(dist - 30, 0)))
                            clock -= 5
                            if rng.random() < pmake:
                                pts[s] += 3
                                yard = profs[1 - s]["start"]
                            else:
                                yard = max(100 - (yard - 7), 20)
                        elif (togo <= 2 and yard >= 40 and rng.random() < (0.75 if sport == "CFB" else 0.6)) or (togo <= 5 and yard >= 60 and rng.random() < 0.5) or (margin < -8 and clock < 600):
                            continue  # go for it on 4th and short
                        else:
                            yard = min(max(100 - (yard + pr["punt_net"]), 5), 80)
                        drive_over, side = True, not side
                        break
                if clock <= 0:
                    break
            ot = 0
            if pts[0] == pts[1]:
                ot = 1
                ph_ = 0.5 + (eff[1] - eff[0]) * 0.5
                w = 1 if rng.random() < ph_ else 0
                pts[w] += 7 if (sport == "CFB" and rng.random() < 0.5) else 3
            out = {"away_pts": pts[0], "home_pts": pts[1], "total": pts[0] + pts[1], "margin": pts[1] - pts[0],
                   "home_win": 1.0 if pts[1] > pts[0] else 0.0, "ot": ot,
                   "0|team_plays": plays[0], "1|team_plays": plays[1]}
            out.update(box)
            return out
        return one
    return factory


def football_tables(spec, t, raw, target):
    m = t.means()
    A, H = spec["away"], spec["home"]
    pa = football_profile(A, H, spec["sport"], spec.get("env") or {})
    ph = football_profile(H, A, spec["sport"], spec.get("env") or {})

    def g(k):
        return m.get(k, 0.0)

    def pct(x):
        return None if x is None else f"{round(x*100)}%"

    team_rows = [
        {"label": "Predicted plays", "a": round(g("0|team_plays"), 1), "b": round(g("1|team_plays"), 1)},
        {"label": "Pass rate (dropbacks/plays)", "a": pct(g("0|team_dropbacks") / max(g("0|team_plays"), 1)), "b": pct(g("1|team_dropbacks") / max(g("1|team_plays"), 1))},
        {"label": "Total yards", "a": round(g("0|team_yds")), "b": round(g("1|team_yds"))},
        {"label": "Sacks taken", "a": round(g("0|team_sacks"), 1), "b": round(g("1|team_sacks"), 1)},
    ]
    dA, dH = (A.get("def") or {}), (H.get("def") or {})
    input_rows = [
        {"label": "Pace (plays/g, off)", "a": A.get("plays_pg"), "b": H.get("plays_pg")},
        {"label": "Neutral pass rate", "a": pct(num(A.get("pass_rate"))), "b": pct(num(H.get("pass_rate")))},
        {"label": "Run yds/carry vs this D", "a": round(pa["ypc"], 2), "b": round(ph["ypc"], 2)},
        {"label": "Pass yds/att vs this D", "a": round(pa["ypa"], 2), "b": round(ph["ypa"], 2)},
        {"label": "Sack rate vs this D", "a": pct(pa["sack"]), "b": pct(ph["sack"])},
        {"label": "Blitz rate faced", "a": pct(num(dH.get("blitz_pct"))) or "n/a", "b": pct(num(dA.get("blitz_pct"))) or "n/a"},
        {"label": "Coverage faced (man / zone)", "a": f"{pct(num(dH.get('man_pct')))} / {pct(num(dH.get('zone_pct')))}" if dH.get("man_pct") is not None else "n/a",
         "b": f"{pct(num(dA.get('man_pct')))} / {pct(num(dA.get('zone_pct')))}" if dA.get("man_pct") is not None else "n/a"},
    ]
    box = {}
    for s, team in ((0, A), (1, H)):
        rows = []
        plays = max(g(f"{s}|team_plays"), 1)
        drop = max(g(f"{s}|team_dropbacks"), 1)
        for p in team.get("players") or []:
            n = p["name"]
            k = f"{s}|{n}|"
            st = {x: g(k + x) for x in ("att", "cmp", "pyds", "ptd", "int", "car", "ruyds", "rutd", "tgt", "rec", "ryds", "rtd")}
            scrim = st["ruyds"] + st["ryds"]
            snap = p.get("snap_pct")
            if isinstance(snap, list):
                snap = statistics.mean([x for x in snap if x is not None] or [0])
            usage = []
            if st["car"] >= 1:
                usage.append(f"{round(st['car']/plays*100)}% of plays rush")
            if st["tgt"] >= 0.5:
                usage.append(f"{round(st['tgt']/drop*100)}% tgt")
            row = {"name": n, "pos": p.get("pos"), "snap": pct(num(snap)) if snap is not None else "—", "usage": ", ".join(usage) or "—"}
            if p.get("pos") == "QB":
                row["line"] = f"{st['cmp']:.1f}/{st['att']:.1f}, {st['pyds']:.0f} yds, {st['ptd']:.1f} TD, {st['int']:.1f} INT"
                if st["car"] >= 1:
                    row["line"] += f"; {st['car']:.1f} car {st['ruyds']:.0f} yds"
                row["_rank"] = 1e6
            else:
                parts = []
                if st["car"] >= 0.5:
                    parts.append(f"{st['car']:.1f} car {st['ruyds']:.0f} yds")
                if st["tgt"] >= 0.3:
                    parts.append(f"{st['rec']:.1f}/{st['tgt']:.1f} rec {st['ryds']:.0f} yds")
                td = st["rutd"] + st["rtd"]
                parts.append(f"{td:.2f} TD")
                row["line"] = ", ".join(parts)
                row["_rank"] = scrim
            row["td"] = round(st["ptd"] if p.get("pos") == "QB" else st["rutd"] + st["rtd"], 2)
            rows.append(row)
        rows.sort(key=lambda r: -r["_rank"])
        for r in rows:
            r.pop("_rank", None)
        box["away" if s == 0 else "home"] = {"cols": ["Player", "Pos", "Snap %", "Usage (sim)", "Projected line"], "rows": rows[:5]}
    return team_rows, input_rows, box


# --------------------------------------------------------------------------- MLB
MLB_LG = dict(k=0.222, bb=0.085, hr=0.030, nonhr_h=0.19)


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
    # regress toward league with 200 PA
    w = pa / (pa + 200)
    return dict(k=w * k + (1 - w) * MLB_LG["k"], bb=w * bb + (1 - w) * MLB_LG["bb"],
                hr=w * hr / pa + (1 - w) * MLB_LG["hr"], nonhr=w * nonhr / pa + (1 - w) * MLB_LG["nonhr_h"],
                s2=(d2 / nonhr) if nonhr else 0.2, s3=(d3 / nonhr) if nonhr else 0.02)


def pitcher_rates(p):
    return dict(k=num(p.get("k_pct"), MLB_LG["k"]), bb=num(p.get("bb_pct"), MLB_LG["bb"]) + num(p.get("hbp_pct"), 0.008),
                hr=num(p.get("hr_per_bf"), MLB_LG["hr"]), nonhr=max(num(p.get("h_per_bf"), 0.22) - num(p.get("hr_per_bf"), 0.03), 0.1))


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


def mlb_factory(spec):
    env = spec.get("env") or {}
    hrf = mlb_env_hr(env)
    pf = num(env.get("park_factor"), 1.0) or 1.0
    teams = [spec["away"], spec["home"]]
    lineups = [[(b["name"], batter_rates(b)) for b in t["lineup"]] for t in teams]
    starters = [teams[i]["starter"] for i in (0, 1)]
    pens = [pitcher_rates(teams[i]["bullpen"]) for i in (0, 1)]
    srates = [pitcher_rates(s) for s in starters]

    def ev_probs(b, p, eff):
        k = log5(b["k"], p["k"], MLB_LG["k"])
        bb = log5(b["bb"], p["bb"], MLB_LG["bb"])
        hr = log5(b["hr"], p["hr"], MLB_LG["hr"]) * hrf * eff
        h = log5(b["nonhr"], p["nonhr"], MLB_LG["nonhr_h"]) * pf * eff
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
            bf = [0, 0]
            leash = [max(6, int(round(rng.gauss(num(s.get("bf_per_start"), 20), 3)))) for s in starters]
            inning = 1
            while True:
                for half in (0, 1):
                    if inning >= 9 and half == 1 and runs[1] > runs[0]:
                        break
                    bat, fld = half, 1 - half
                    outs, bases = 0, [None, None, None]
                    if inning >= 10:
                        bases[1] = lineups[bat][(idx[bat] - 1) % 9][0]
                    while outs < 3:
                        name, br = lineups[bat][idx[bat] % 9]
                        idx[bat] += 1
                        starter_in = bf[fld] < leash[fld]
                        pr = srates[fld] if starter_in else pens[fld]
                        pk = f"{fld}|P:{starters[fld]['name']}|" if starter_in else f"{fld}|P:bullpen|"
                        bf[fld] += 1
                        k, bb, hr, h = ev_probs(br, pr, eff[bat])
                        r = rng.random()
                        bk = f"{bat}|{name}|"
                        box[bk + "pa"] += 1
                        scored = []
                        if r < k:
                            outs += 1
                            box[bk + "k"] += 1
                            box[pk + "k"] += 1
                            box[pk + "outs"] += 1
                        elif r < k + bb:
                            box[bk + "bb"] += 1
                            if bases[0]:
                                if bases[1]:
                                    if bases[2]:
                                        scored.append(bases[2])
                                    bases[2] = bases[1]
                                bases[1] = bases[0]
                            bases[0] = name
                        elif r < k + bb + hr:
                            box[bk + "h"] += 1
                            box[bk + "hr"] += 1
                            box[bk + "tb"] += 4
                            box[pk + "h"] += 1
                            scored += [x for x in bases if x] + [name]
                            bases = [None, None, None]
                        elif r < k + bb + hr + h:
                            box[bk + "h"] += 1
                            box[pk + "h"] += 1
                            x = rng.random()
                            if x < br["s3"]:
                                box[bk + "tb"] += 3
                                scored += [y for y in bases if y]
                                bases = [None, None, name]
                            elif x < br["s3"] + br["s2"]:
                                box[bk + "tb"] += 2
                                scored += [y for y in bases[1:] if y]
                                if bases[0] and rng.random() < 0.45:
                                    scored.append(bases[0])
                                    bases = [None, name, None]
                                else:
                                    bases = [None, name, bases[0]]
                            else:
                                box[bk + "tb"] += 1
                                if bases[2]:
                                    scored.append(bases[2])
                                n3 = None
                                if bases[1]:
                                    if rng.random() < 0.6:
                                        scored.append(bases[1])
                                    else:
                                        n3 = bases[1]
                                n2 = bases[0]
                                if n2 and n3 is None and rng.random() < 0.28:  # first to third
                                    n3, n2 = n2, None
                                bases = [name, n2, n3]
                        elif rng.random() < 0.015:  # reached on error
                            if bases[2]:
                                scored.append(bases[2])
                            bases = [name, bases[0], bases[1]]
                        else:  # ball in play out
                            outs += 1
                            box[pk + "outs"] += 1
                            if outs < 3 and bases[1] and not bases[2] and rng.random() < 0.25:
                                bases[2], bases[1] = bases[1], None
                            if outs < 3 and bases[0] and rng.random() < 0.12:
                                outs += 1
                                box[pk + "outs"] += 1
                                bases[0] = None
                            if outs < 3 and bases[2] and rng.random() < 0.35:
                                scored.append(bases[2])
                                bases[2] = None
                        for sname in scored:
                            box[f"{bat}|{sname}|r"] += 1
                        if scored:
                            box[bk + "rbi"] += len(scored)
                            box[pk + "er"] += len(scored)
                            runs[bat] += len(scored)
                        if inning >= 9 and half == 1 and runs[1] > runs[0]:
                            break
                if inning >= 9 and runs[0] != runs[1]:
                    break
                inning += 1
                if inning > 15:
                    break
            out = {"away_pts": runs[0], "home_pts": runs[1], "total": runs[0] + runs[1], "margin": runs[1] - runs[0],
                   "home_win": 1.0 if runs[1] > runs[0] else 0.0, "ot": 1.0 if inning > 9 else 0.0}
            out.update(box)
            return out
        return one
    return factory


def mlb_tables(spec, t, raw, target):
    m = t.means()
    g = lambda k: m.get(k, 0.0)
    box = {}
    team_rows = []
    for s, key in ((0, "away"), (1, "home")):
        team = spec[key]
        rows = []
        for b in team["lineup"]:
            k = f"{s}|{b['name']}|"
            h, r, rbi = g(k + "h"), g(k + "r"), g(k + "rbi")
            rows.append({"name": b["name"], "pos": b.get("pos"), "pa": round(g(k + "pa"), 2), "h": round(h, 2), "tb": round(g(k + "tb"), 2),
                         "hr": round(g(k + "hr"), 3), "r": round(r, 2), "rbi": round(rbi, 2), "bb": round(g(k + "bb"), 2), "k": round(g(k + "k"), 2),
                         "line": f"{h:.2f} H, {g(k+'tb'):.2f} TB, {r:.2f} R, {rbi:.2f} RBI, {g(k+'hr'):.2f} HR", "_rank": h + r + rbi})
        rows.sort(key=lambda r: -r["_rank"])
        for r in rows:
            r.pop("_rank")
        opp = 1 - s
        st = spec[["away", "home"][opp]]["starter"]["name"]
        pk = f"{opp}|P:{st}|"
        team_rows.append((st, g(pk + "outs"), g(pk + "k"), g(pk + "h"), g(pk + "er")))
        box[key] = {"cols": ["Player", "Pos", "PA", "Projected line"], "rows": [{"name": r["name"], "pos": r["pos"], "pa": r["pa"], "line": r["line"], **{x: r[x] for x in ("h", "tb", "hr", "r", "rbi")}} for r in rows[:5]]}
    # team_rows here: starters (away starter is pitched by team 0 -> faced by home batting)
    sa, sh = team_rows[1], team_rows[0]  # team_rows[0] = pitcher facing away bats = home starter
    trows = [
        {"label": "Starter", "a": sa[0], "b": sh[0]},
        {"label": "Starter outs (sim)", "a": round(sa[1], 1), "b": round(sh[1], 1)},
        {"label": "Starter K (sim)", "a": round(sa[2], 2), "b": round(sh[2], 2)},
        {"label": "Starter H / R allowed (sim)", "a": f"{sa[3]:.1f} / {sa[4]:.1f}", "b": f"{sh[3]:.1f} / {sh[4]:.1f}"},
    ]
    env = spec.get("env") or {}
    inputs = [
        {"label": "HR environment factor", "a": round(mlb_env_hr(env), 3), "b": round(mlb_env_hr(env), 3)},
        {"label": "Starter K% / BB%", "a": f"{spec['away']['starter'].get('k_pct')} / {spec['away']['starter'].get('bb_pct')}", "b": f"{spec['home']['starter'].get('k_pct')} / {spec['home']['starter'].get('bb_pct')}"},
        {"label": "Expected starter BF", "a": spec["away"]["starter"].get("bf_per_start"), "b": spec["home"]["starter"].get("bf_per_start")},
        {"label": "Bullpen K%", "a": spec["away"]["bullpen"].get("k_pct"), "b": spec["home"]["bullpen"].get("k_pct")},
    ]
    return trows, inputs, box


# --------------------------------------------------------------------------- NHL
def toi_min(s):
    if isinstance(s, (int, float)):
        return float(s)
    try:
        mm, ss = str(s).split(":")
        return int(mm) + int(ss) / 60
    except Exception:
        return 15.0


def nhl_factory(spec):
    A, H = spec["away"], spec["home"]

    def goalie_sv(gl):
        sv = num(gl.get("sv_pct"), 0.900)
        sa = num(gl.get("shots_against"), (num(gl.get("gs"), 3) or 3) * 28)
        last = num(gl.get("sv_pct_last_season"), 0.900)
        return (sv * sa + last * 800 + 0.900 * 400) / (sa + 1200)

    def skaters(team):
        out = []
        for p in team.get("skaters") or []:
            gp = max(num(p.get("gp"), 1), 1)
            ls = p.get("last_season") or {}
            lgp = num(ls.get("gp"), 0) or 0
            w = 20.0 if lgp else 0.0
            sog_pg = (num(p.get("sog"), 0) + (num(ls.get("sog"), 0) / lgp * w if lgp else 0)) / (gp + w)
            a_pg = (num(p.get("assists"), 0) + (num(ls.get("assists"), 0) / lgp * w if lgp else 0)) / (gp + w)
            gs = num(p.get("goals"), 0) + num(ls.get("goals"), 0)
            ss = num(p.get("sog"), 0) + num(ls.get("sog"), 0)
            sh = (gs + 0.095 * 100) / (ss + 100)
            out.append(dict(name=p["name"], pos=p.get("pos"), sog_pg=max(sog_pg, 0.2), a_pg=max(a_pg, 0.05), sh=sh, toi=p.get("toi_pg"), pp1=p.get("pp1")))
        return out

    sk = [skaters(A), skaters(H)]
    sv = [goalie_sv(H["goalie"]), goalie_sv(A["goalie"])]  # sv faced by team i's shooters
    shots = [(num(A.get("shots_pg"), 29) + num(H.get("shots_against_pg"), 29)) / 2 * 0.99,
             (num(H.get("shots_pg"), 29) + num(A.get("shots_against_pg"), 29)) / 2 * 1.01]

    def factory(eff):
        def one(rng):
            box = defaultdict(float)
            goals = [0, 0]
            for s in (0, 1):
                lam = shots[s] * rng.gammavariate(25, 1 / 25)
                n = poisson(rng, lam)
                team_sog = sum(x["sog_pg"] for x in sk[s])
                listed = min(team_sog / max(shots[s], 1), 0.92)
                pairs = [(i, x["sog_pg"] / team_sog * listed) for i, x in enumerate(sk[s])]
                for _ in range(n):
                    i = pick(rng, pairs)
                    shp = (sk[s][i]["sh"] if i is not None else 0.08) * (1 - sv[s]) / 0.095 * eff[s]
                    if i is not None:
                        box[f"{s}|{sk[s][i]['name']}|sog"] += 1
                    box[f"{1-s}|G|sa"] += 1
                    if rng.random() < shp:
                        goals[s] += 1
                        if i is not None:
                            box[f"{s}|{sk[s][i]['name']}|g"] += 1
                        na = 2 if rng.random() < 0.6 else (1 if rng.random() < 0.8 else 0)
                        mates = [(j, x["a_pg"]) for j, x in enumerate(sk[s]) if j != i]
                        tot = sum(w for _, w in mates)
                        used = set()
                        for _ in range(na):
                            pr = [(j, w / tot) for j, w in mates if j not in used]
                            t2 = sum(w for _, w in pr)
                            j = pick(rng, [(j, w / t2) for j, w in pr]) if t2 else None
                            if j is None:
                                break
                            used.add(j)
                            box[f"{s}|{sk[s][j]['name']}|a"] += 1
                    else:
                        box[f"{1-s}|G|sv"] += 1
            ot = 0
            if goals[0] == goals[1]:
                ot = 1
                p_h = 0.5 + (shots[1] * (1 - sv[1]) - shots[0] * (1 - sv[0])) * 0.05
                if rng.random() < 0.6:
                    goals[1 if rng.random() < p_h else 0] += 1
                else:
                    goals[1 if rng.random() < 0.5 else 0] += 1  # shootout: counted as a goal, as books grade
            out = {"away_pts": goals[0], "home_pts": goals[1], "total": goals[0] + goals[1], "margin": goals[1] - goals[0],
                   "home_win": 1.0 if goals[1] > goals[0] else 0.0, "ot": ot}
            out.update(box)
            return out
        return one
    return factory, sk, sv, shots


def nhl_tables(spec, t, sk, sv, shots):
    m = t.means()
    g = lambda k: m.get(k, 0.0)
    box = {}
    for s, key in ((0, "away"), (1, "home")):
        rows = []
        for x in sk[s]:
            k = f"{s}|{x['name']}|"
            sog, gl, a = g(k + "sog"), g(k + "g"), g(k + "a")
            rows.append({"name": x["name"], "pos": x["pos"], "toi": x["toi"], "pp1": bool(x["pp1"]), "sog": round(sog, 2), "g": round(gl, 3), "a": round(a, 3), "pts": round(gl + a, 3),
                         "line": f"{sog:.2f} SOG, {gl:.2f} G, {a:.2f} A, {gl+a:.2f} PTS"})
        rows.sort(key=lambda r: -(r["pts"] + r["sog"] * 0.05))
        box[key] = {"cols": ["Player", "Pos", "TOI", "PP1", "Projected line"], "rows": rows[:5]}
    A, H = spec["away"], spec["home"]
    trows = [
        {"label": "Shots on goal (sim)", "a": round(g("1|G|sa"), 1), "b": round(g("0|G|sa"), 1)},
        {"label": "Goalie (opp) saves (sim)", "a": f"{H['goalie']['name']} {g('1|G|sv'):.1f}", "b": f"{A['goalie']['name']} {g('0|G|sv'):.1f}"},
    ]
    inputs = [
        {"label": "Shot volume input", "a": round(shots[0], 1), "b": round(shots[1], 1)},
        {"label": "Opp goalie SV% (regressed)", "a": f"{sv[0]:.3f}", "b": f"{sv[1]:.3f}"},
        {"label": "PP% / PK%", "a": f"{A.get('pp_pct')} / {A.get('pk_pct')}", "b": f"{H.get('pp_pct')} / {H.get('pk_pct')}"},
    ]
    return trows, inputs, box


# --------------------------------------------------------------------------- driver
def simulate(spec, seed=7, w=ANCHOR_W):
    rng = random.Random(seed)
    sport = spec["sport"]
    if sport in ("NFL", "CFB"):
        factory = football_factory(spec)
    elif sport == "MLB":
        factory = mlb_factory(spec)
    elif sport == "NHL":
        factory, sk, sv, shots = nhl_factory(spec)
    else:
        raise ValueError(f"no engine for {sport}")
    market = spec.get("market") or {}
    base = (1.0, 1.0)
    if sport in LEAGUE:  # league finishing factor: league-average inputs reproduce league scoring
        base = (LEAGUE[sport]["finish"],) * 2
    eff, raw, target = calibrate(factory, sport, market, rng, base_eff=base, w=w)
    t, runs = run_until_stable(factory(eff), rng)
    m = t.means()
    tot = sorted(t.samples["total"])
    mar = t.samples["margin"]
    n = len(tot)
    score = {
        "away": round(m["away_pts"], 2), "home": round(m["home_pts"], 2), "total": round(m["total"], 2),
        "total_median": tot[n // 2], "total_p10_p90": [tot[int(n * 0.1)], tot[int(n * 0.9)]],
        "home_win": round(m["home_win"], 3), "ot_rate": round(m["ot"], 3),
        "raw": {"away": round(raw[0], 2), "home": round(raw[1], 2)},
        "anchor": {"weight": w, "market_implied": [round(x, 2) for x in implied_points(sport, market)] if implied_points(sport, market) else None, "efficiency": [round(x, 3) for x in eff]},
    }
    mt = num(market.get("total"))
    if mt is not None:
        score["over"] = round(sum(1 for x in tot if x > mt) / n, 3)
        score["push"] = round(sum(1 for x in tot if x == mt) / n, 3)
    sh = num(market.get("spread_home"))
    if sh is not None:
        score["home_cover"] = round(sum(1 for x in mar if x + sh > 0) / n, 3)
    if sport in ("NFL", "CFB"):
        team_rows, inputs, box = football_tables(spec, t, raw, target)
    elif sport == "MLB":
        team_rows, inputs, box = mlb_tables(spec, t, raw, target)
    else:
        team_rows, inputs, box = nhl_tables(spec, t, sk, sv, shots)
    cols = [spec["away"].get("abbr"), spec["home"].get("abbr")]
    keys = {"Player": "name", "Pos": "pos", "Snap %": "snap", "Usage (sim)": "usage", "Projected line": "line", "PA": "pa", "TOI": "toi", "PP1": "pp1"}
    for side in box.values():  # display cells aligned to cols, so the board can render any sport the same way
        for r in side["rows"]:
            r["cells"] = [("PP1" if r.get("pp1") else "") if c == "PP1" else r.get(keys[c], "") for c in side["cols"]]
    return {
        "engine": ENGINE, "sport": sport, "game": spec.get("game"), "seed": seed,
        "runs": runs, "score": score,
        "team": {"cols": cols, "rows": team_rows},
        "inputs": {"cols": cols, "rows": inputs},
        "box": box,
        "notes": (spec.get("notes") or [])[:6] if isinstance(spec.get("notes"), list) else [str(spec.get("notes"))],
        "caveat": "Averages over the simulated games, not a prediction of one game. Stable after the stated runs; accuracy depends on the inputs (small 2026 samples, unconfirmed lineups/goalies).",
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
        print(f"{spec['game']}: {r['box'] and ''}{sc['away']}-{sc['home']} (total {sc['total']}), "
              f"{ru['batches']} batches / {ru['sims']} sims, converged={ru['converged']}", file=sys.stderr)
    json.dump(out, open(a.out, "w"), indent=1)


if __name__ == "__main__":
    main()
