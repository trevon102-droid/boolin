# Slate playbook

Instructions for the daily Claude run that turns this repo's data into a priced card on the
**Rackz Sharp Board** artifact: https://claude.ai/artifact/DbSgLx1GwBkC15aMK5YsPW

The board does all sizing math itself (edge, fair odds, quarter Kelly, caps). The job here is to
supply honest **prices** and **model probabilities**, plus the reasoning.

## 0. Football comes first

**NFL and college football (ranked AND unranked games) are the priority sports.** On any day with
football (CFB Saturdays plus Tue–Fri weeknight games; NFL Thursday, Sunday, Monday, and international/holiday games):

- **Research football first and deepest.** Price every game on the card, including unranked vs unranked.
  That means sides, totals, and team totals where there's a real read, plus player props when you
  can find real prices. Spend most of the research budget here.
- **Same method, same standards.** Football gets more depth, not looser rules. Same sharp-line start,
  same edge bar, same honesty about passes. MLB/NHL/WNBA still get priced the normal way on
  football days, just with less depth (main markets + the best 1–3 props per game).
- **Football-specific checks:** QB status and backup quality, OL/DL injuries, key numbers (3, 7, 10 in the NFL;
  3, 7, 10, 14, 17 in CFB), wind (15+ mph matters for totals and kicking), travel/rest (short weeks, cross-country,
  bye), neutral sites, look-ahead/letdown spots, and for CFB: tempo, returning production, FCS or
  G5 mismatches, and conference-game familiarity.
- **Exposure:** on football days, football can take most of the 8u daily cap. Keep the ≤3u per game limit.

## 1. Load the data

```
git -C /home/claude/boolin pull || git clone --depth 1 https://github.com/trevon102-droid/boolin /home/claude/boolin
```

Read `data/latest/manifest.json` first.
- If `slate_date` isn't today (ET), the morning pull didn't run. Say so in the slate `notes` and
  fall back to web research for everything.
- If a source shows `error` or `skipped`, cover that part with web search and say so in `notes`.

Files in `data/latest/`:

| File | What's in it |
|---|---|
| `odds.json` | Per game: best price + book for every side, **Pinnacle no-vig fair prob**, hold, other lines. Props if pulled. |
| `mlb.json` | Probables (season K%, BB%, last 5 starts, K and outs hit-rate distributions), lineups, team K% vs the starter's hand, bullpen usage last 3 days, weather, HP ump, series status |
| `nhl.json` | Back-to-back flags, standings form, goalie usage + GSAx (this + last season), 5v5 xG% (MoneyPuck) |
| `wnba.json` | ESPN line + win projection, injuries, last-10 logs for each team's leaders, team stats |
| `nfl.json` | This week's games (rest, roof, wind, QBs, nflverse lines), team EPA/success splits + ranks, injury report |
| `cfb.json` | Every FBS game today (AP rank, neutral site, conference game, FPI projection, weather, injuries), AP Top 25, next 3 days' schedule, and team EPA + SP+ if the optional CFBD key is set |
| `injuries.json` | ESPN injury lists for today's NHL and MLB games |

## 2. Fill the gaps with the web (every run)

Always verify with WebSearch/WebFetch, because the data can be hours old:
- Confirmed starting goalies (Daily Faceoff), confirmed MLB lineups, late scratches, weather changes.
- Player prop lines/prices if `odds.json` has no props. Only use a price you actually found.
- Significant line moves since the pull.

## 3. Set model probabilities

1. **Start from the market.** Pinnacle no-vig `fair_prob` is the prior. With no Pinnacle, use the consensus fair prob.
2. **Adjust only for things the market may not have priced yet** or that the data shows strongly: confirmed
   goalie/lineup news, bullpen fatigue, back-to-backs, weather/wind, ump tendencies, usage changes from injuries.
   Typical adjustments are 1–4 points. Moves bigger than ~5 points off the sharp line need a
   concrete reason in `why`.
3. **Props:** use hit-rate distributions (`k_dist`, `outs_dist`, last-10 logs) blended with the market.
   Regress small samples hard (one-inning NRFI rates, 5-game streaks).
4. **Trends are not inputs.** "Team X is 9-0 in spot Y" can go in `inputs` as color, never as the reason.
5. Aim to be calibrated, not bold. Most plays should land 0–5% edge. If the edge is huge, you're probably wrong.

## 4. Build the slate

Price every game the user cares about: **NFL and CFB first on football days**, then MLB, NHL,
WNBA daily. Include **passes**:
a play the user might like with no edge at the current price goes on the board with a `betTo` number,
so they know what line would make it a bet.

- Use the **best available price** from `odds.json` (`best_price`/`best_book`), not just FanDuel.
- Put the Pinnacle two-way prices in `sharp` when there's a Pinnacle line (`{"odds": side, "other": other side}`).
- Ladders only when you have real alt-line prices for every rung.
- Keep total sized exposure (board + Top 5 props + SGPs + parlays) near the daily cap (8u) and ≤3u per game. If you're over, drop the lowest-edge plays or parlays first.
- `why` is one or two plain sentences. `inputs` is 1–3 short data points.

### Schema (write to collection `slates`, doc id = `YYYY-MM-DD`)

```json
{
  "date": "2026-10-01",
  "title": "MLB · NHL · WNBA",
  "updated": "Thu Oct 1, 9:05 AM ET",
  "notes": "What data was used, what was missing, anything stale.",
  "rules": ["Recheck goalies 60 min before puck drop"],
  "picks": [
    {"id": "m1", "sport": "MLB", "game": "PHI @ ATL", "start": "2:00 PM ET",
     "market": "Pitcher prop", "category": "MLB K props",
     "pick": "Cristopher Sánchez Over 6.5 K", "book": "DraftKings", "odds": -115,
     "modelP": 0.55, "sharp": {"odds": -125, "other": 105},
     "betTo": "-120 or better at 6.5", "why": "…", "inputs": ["…"]},
    {"id": "l1", "sport": "NFL", "game": "PIT @ CLE", "pick": "Warren alt rush yds", "category": "NFL player props",
     "ladder": [{"label": "70+", "odds": -110, "modelP": 0.55}]},
    {"id": "x1", "sport": "NHL", "game": "LA @ COL", "pick": "Avalanche -1.5", "odds": 128, "modelP": 0.41,
     "pass": true, "betTo": "+150 or better", "why": "…"}
  ]
}
```

Category names are what performance is grouped by, so keep them stable:
`MLB sides`, `MLB K props`, `MLB pitcher props`, `MLB first inning`, `MLB batter props`,
`NHL sides`, `NHL player props`, `WNBA sides`, `WNBA totals`, `WNBA player props`,
`NFL sides`, `NFL totals`, `NFL player props`, `NFL TD scorers`,
`CFB sides`, `CFB totals`, `CFB player props`.

Use `"sport": "CFB"` for college football and put the rank in `game` when there is one,
like `"#8 Oregon @ Penn State"`.

### Top 5 games (`featured`)

Every slate also carries a `featured` array: the day's **five biggest games**, broken down in
depth for the board's Top 5 tab.

**Picking the five:** football first. On NFL/CFB days, the top games are the biggest football games
(primetime NFL, ranked-vs-ranked CFB, then ranked teams and big spreads), and other sports only fill
the leftover slots. Otherwise rank by stakes (playoff/elimination > rivalry/primetime > regular) and then by
where the edges are. Only include games that haven't started when the run finishes.

**Per game, write:**
- `lines`: moneyline, spread/run line/puck line, total, each with best prices and the Pinnacle no-vig fair.
- `script`: one paragraph telling how the game is likely to play out and where the edge is (or isn't).
- `matchup`: 6–8 rows of the numbers that decide the game, with `edge: "a"|"b"|null` marking the side it favors.
  MLB: starters, K%/BB%, outs rates, lineup K% and OPS vs hand, bullpen load, ump/weather.
  NFL/CFB: EPA/play off & def (+ ranks), success rate, pass/rush splits, rest, QB, OL/DL injuries, wind.
  WNBA/NBA: records, pace, ratings, top scorers/creators L10, absences. NHL: xG%, goalies + GSAx, rest.
- `injuries`: short strings, including news that changes the read.
- `props`: 2–4 props with **real prices** (from `odds.json` props or the web), sized the same way as board picks.
  Mark props that are also on the board with `"onBoard": true` so exposure isn't double counted.
- `sgps`: 1–2 same-game parlays. Legs should share one game script with real positive correlation. Give each leg
  its price and model %, and set the SGP's `modelP` to your correlated joint probability (always above
  the independent product when the correlation is positive; say by how much in `correlation`).
  The board shows the fair price and a **Bet only at** price, because books reprice SGPs for correlation. Mark extra
  builds `"pass": true` so only the best SGP per game gets a stake. Never build SGPs from negatively correlated legs.

```json
"featured": [
  {"rank": 1, "sport": "NFL", "game": "PIT @ CLE", "start": "8:15 PM ET", "tv": "Prime Video",
   "context": "TNF · AFC North", "headline": "One line on why this game matters",
   "lines": [{"label": "Spread", "value": "PIT -2.5 -112 · CLE +2.5 -108", "fair": "Pinnacle no-vig: PIT 51%"}],
   "script": "How the game plays out and where the edge is.",
   "injuries": ["Dowdle (toe): out"],
   "matchup": {"cols": ["PIT", "CLE"], "rows": [{"label": "Off EPA/play (rank)", "a": "-0.05 (22)", "b": "-0.12 (29)", "edge": "a"}]},
   "props": [{"pick": "Jaylen Warren Over 66.5 rush yds", "market": "Player prop", "category": "NFL player props",
              "odds": -115, "book": "FanDuel", "modelP": 0.56, "betTo": "69.5 at -115", "why": "…", "onBoard": true}],
   "sgps": [{"name": "Grind script", "legs": [{"pick": "Under 38.5", "odds": -118, "modelP": 0.57},
             {"pick": "Warren Over 66.5 rush yds", "odds": -115, "modelP": 0.56}], "modelP": 0.36,
             "script": "…", "correlation": "…"}]}
]
```

**Only use books Kaire can bet.** If a play is only available at a book he doesn't use, mark it `pass` and say so; don't build parlays around it.

Player props in `odds.json` (with `props_pulled_at_et`, since they can be carried over from an earlier run) only exist when the morning run pulled them (repo variable `PROPS_DAILY=1`)
or a manual run did. Otherwise find prop prices on the web and name the book.

### Cross-game parlays (`parlays`)

Also write 2–3 cross-game parlays for the Parlays tab. Rules:
- **Every leg must clear the edge bar on its own** (a sized play on the board or a Top 5 prop). Never add a leg
  just to pump the payout.
- **One leg per game.** Same-game combos are SGPs and belong in `featured`.
- 2–3 legs for the staked parlays. Longer builds (4+ legs) go in as `"pass": true` references.
- Spread risk: staked parlays shouldn't share legs, so one loss doesn't sink them all.
- Leave out `modelP` for independent legs (the board multiplies the legs). Only set it when there's a real
  cross-game correlation, like two teams in the same weather system or a playoff tiebreaker scenario.
- Each leg carries `pick`, `game`, `sport`, `odds`, `modelP`, `start` (so the builder can hide games that already started).

```json
"parlays": [
  {"name": "Fried + Bueckers", "why": "Two cleanest edges, different games.",
   "legs": [{"pick": "Fried Under 4.5 hits allowed", "game": "BOS @ NYY", "sport": "MLB", "odds": -120, "modelP": 0.565, "start": "8:00 PM ET"},
            {"pick": "Bueckers Over 19.5 points", "game": "GSV @ DAL", "sport": "WNBA", "odds": -106, "modelP": 0.54, "start": "9:00 PM ET"}]}
]
```

Parlay stakes are capped at 0.5u each and count toward the 8u daily total (not the per-game cap).

### Writing it

Use the `ArtifactData` tool with the board URL above:
- New day: `set` on `slates` / `<date>` (no `if_version`).
- Doc already exists (a rerun): `get` it first, then `set` with its `version` as `if_version`.
- Don't touch `bets` or `settings`. Those belong to the user.

## 5. Report

Finish with a short message: number of plays, top 3 by edge, the Top 5 games picked, total units
(including SGP stakes), anything that needs a check before start (goalies, lineups), and any data source that failed.
