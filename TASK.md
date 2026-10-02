# Slate playbook

Instructions for the daily Claude run that turns this repo's data into a priced card on the
**Rackz Sharp Board** artifact: https://claude.ai/artifact/DbSgLx1GwBkC15aMK5YsPW

**Rev 8: research first.** Kaire picks plays from research and stats, not from a price gate. The job is to
find the best plays on the card from usage, matchups, form and news, explain each one with a real WHY and the
stats behind it, rate it with a conviction **tier (A/B/C)**, and show FanDuel's price. The board sizes plays by
tier (Settings → Sizing mode = Research). The price check (our % vs FanDuel's price) is shown as information,
**never as a reason to pass**.

## 00. Research-first rules (these override anything below that says "pass", "edge bar" or "floor")

- **Every day:** research → Top 10 games → 3–5 props/plays per Top 10 game (main lines only for CFB) → the best
  of them on the board (`picks`) → TD/HR/goal longshots → 2–3 parlays from tiered plays.
- **Every play gets** `why` (2–3 sentences: the role, the matchup, the reason it hits), `inputs` (3–5 stats:
  usage/snap/target or carry share, red-zone/goal-line share, L5/L10 game logs and hit rate on this line,
  opponent allowed to the position, history vs this opponent, news), and `conviction`:
  - **A**: role + matchup + recent usage all point the same way, the line has hit in most recent relevant games
    (e.g. 7+ of last 10), no injury cloud. The plays Kaire should look at first.
  - **B**: two of those three line up, or strong stats with one question mark.
  - **C**: leans, longshots (anytime/2+ TD, HR, goals) and thinner evidence.
- **Streaks, game logs and history vs the opponent are allowed as evidence** (show them in `inputs`), as long as
  the WHY rests on role/usage first. Say the sample size ("4 of last 5", "3 games this season").
- **Price is information.** Still record `odds` (FanDuel), `modelP`, `prior` and `factors` so the board can show the
  price check and we can track results by tier later. Add a short `betTo` like "fine to -140" or "price is juiced;
  still the best play" instead of a pass.
- **`pass: true` only for real reasons:** player out/questionable, lineup or goalie news that breaks the read,
  FanDuel not offering the market, game started. Never for price or thin edge.
- **Exposure:** tier sizes come from the board (A 1u, B 0.5u, C 0.25u by default). Keep the day near the 8u cap
  and ≤3u per game; if over, drop C plays first.
- Sections 3–3c below still describe how to build `modelP` (useful for the price check). Their edge bars and
  floors are **price-mode only** and don't gate plays in research mode.

## 0. Football comes first

**NFL and college football (ranked AND unranked games) are the priority sports.** On any day with
football (CFB Saturdays plus Tue–Fri weeknight games; NFL Thursday, Sunday, Monday, and international/holiday games):

- **Research football first and deepest.** Price every game on the card, including unranked vs unranked.
  That means sides, totals, and team totals where there's a real read, plus player props when you
  can find real prices. Spend most of the research budget here.
- **Same method, same standards.** Football gets more depth, not looser rules. Same sharp-line start,
  same edge bar, same honesty about passes. MLB/NHL/NBA/WNBA still get priced the normal way on
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
- There is no scheduled morning pull: the slate run triggers the `pull-data.yml` workflow itself (see the task prompt).
  If `slate_date` still isn't today (ET), that run failed. Say so in the slate `notes` and
  fall back to web research for everything.
- If a source shows `error` or `skipped`, cover that part with web search and say so in `notes`.

Files in `data/latest/`:

| File | What's in it |
|---|---|
| `odds.json` | Per game: best price + book for every side, **Pinnacle no-vig fair prob**, hold, other lines. Props if pulled. |
| `mlb.json` | Probables (season K%, BB%, last 5 starts, K and outs hit-rate distributions), lineups, team K% vs the starter's hand, bullpen usage last 3 days, weather, HP ump, series status |
| `nhl.json` | Back-to-back flags, standings form, goalie usage + GSAx (this + last season), 5v5 xG% (MoneyPuck) |
| `wnba.json` | ESPN line + win projection, injuries, last-10 logs for each team's leaders, team stats |
| `nba.json` | Same shape as `wnba.json` for today's NBA games (starts Oct 20, 2026; skipped before then) |
| `nfl.json` | This week's games (rest, roof, wind, QBs, nflverse lines), team EPA/success splits + ranks, injury report |
| `cfb.json` | Every FBS game today (AP rank, neutral site, conference game, FPI projection, weather, injuries), AP Top 25, next 3 days' schedule, and team EPA + SP+ if the optional CFBD key is set |
| `injuries.json` | ESPN injury lists for today's NHL and MLB games |

Also read the weekly prep data from the board's database (`ArtifactData` `list` on collection `prep`):
the newest `nfl-…`/`cfb-…` docs (usage, coverage, run D, pace, injuries) and the newest `review-…` doc
(which categories and factors are beating the close). Use them as inputs and say in `notes` if they're missing or more than 8 days old.

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
5. Aim to be calibrated, not bold. (Research mode: this is for `modelP` only; it never turns a play into a pass.)

### 3b. Model lines (our odds first)

For every sized play and every Top 10 prop, build OUR number before comparing it to FanDuel, and write the steps into the pick:
- `prior` + `priorLabel`: the market starting point (Pinnacle/consensus no-vig; FanDuel's own two-way only if nothing else exists; for one-sided markets like HR/TD/goal, a base rate from season frequency with a playoff/opponent haircut).
- `factors`: `[{"f": "what it is", "adj": +2.5}]` in percentage points, each tied to a named data point:
  - **Usage**: snap %, route participation, target share/TPRR, carries, red-zone and end-zone looks, TOI/PP time, lineup spot.
  - **Scheme**: defense's man vs zone rate, single-high rate, blitz/pressure, nickel; the player's YPRR/TPRR vs that coverage; run D (YPC, explosive runs, YAC); pace and plays per game.
  - **Splits**: platoon splits regressed toward league average (~1,000 PA for LHB, ~2,200 for RHB, ~600 switch), times through the order as a gradual penalty, home/road.
  - **Do not use as factors**: batter-vs-pitcher history, hot/cold streaks, 3–5 game logs, WR-vs-CB matchups, goalie back-to-back fatigue. They're noise at the samples we have. Mention them in `why` as color only.
  - **Coverage scheme** (man/zone fit) is weakly supported: cap it at ±1 pt.
  - **Context**: injuries and who absorbs the usage, weather/wind, rest, script (favorite runs, underdog throws), goalie/umpire.
- `modelP` must equal prior + sum of factors. Total moves over ~6 pts from the market need two independent reasons.
- Line moves are information. Record `open`, `move` and `steam`:
  - `"with"`: the market moved toward our side. Re-price from the NEW sharp price; never add the move as a bonus factor (that double counts).
  - `"stale"`: other books moved and FanDuel hasn't. This is the best spot; bet before it adjusts.
  - `"against"`: sharp money went the other way. Re-price from the new sharp price, which lowers our number. Don't just tag it.
  - A longshot that went +700 → +200 is a bet only if our line is shorter than +200. The move tells us the true price is shorter, not that +200 is value.

### 3c. Pricing rules (rev 7)

- **Price timestamps.** Set `priceAt` (ISO time) on every pick, longshot and ladder, and `pricesAt` on the slate. There's no FanDuel feed, so a same-day FanDuel price that's roughly still there is fine to stake; the board only blocks prices older than its `staleMin` setting (24 h). The 10:50 AM and 4:50 PM re-checks catch real moves (news, injuries, steam). Don't pass a good play just because its price is a few hours old.
- **Blend toward the market.** The board shows prior + modelWeight × (modelP − prior). Keep `prior` honest so the blend works.
- **Distributions, not bumps, for player stats.** Project the mean and spread (yards: simulate or use a skewed distribution; counts like K, receptions, SOG: Poisson/binomial/neg-binomial), compare to the median, and price every ladder rung from the same distribution.
- **Longshots from expected counts.** P(at least one) = 1 − e^(−λ). TD λ = team implied TDs × player share. Goal λ = individual xG/60 × expected TOI. HR = 1 − (1 − HR/PA)^expected PA, with park, weather and pitcher HR rate.
- **Floors.** Sides/totals use the min-edge setting, props use the `minEdgeProp` setting (3%), longshots 20% relative. A two-way prop edge over 10% is almost always a data or price error: flag it, don't stake it.
- **One game = one position.** All plays, props, SGPs and longshots on one game share the 3u cap. SGPs are pass by default unless a joint simulation beats FanDuel's slip price.
- **Log every factor.** Keep `factors` on every pick so we can measure which ones help once the bet log has ~300 bets with closing prices.

## 4. Build the slate

**NBA notes.** Use `"sport": "NBA"` and the `NBA …` categories. Key inputs: injury report and rest
(back-to-backs, load management; official reports post by 5 PM local the day before and update through
the afternoon), starting lineups (~30 min before tip), pace and offensive/defensive rating, and minutes
for props. **NBA is off until the regular season (Oct 20, 2026):** the pipeline skips NBA data and odds before then
(`nba` shows `skipped` in the manifest). Don't add preseason NBA games to the slate.

Price every game the user cares about: **NFL and CFB first on football days**, then MLB, NHL,
NBA and WNBA daily. Every play gets a `conviction` tier (A/B/C), a WHY and stats (section 00). Don't fill the
board with price passes; list a play only if the research likes it, and use `pass: true` only for news.

- Use **FanDuel's price** from `odds.json` (the `fanduel` field on each outcome). `best_price`/`best_book` are context only.
- Put the Pinnacle two-way prices in `sharp` when there's a Pinnacle line (`{"odds": side, "other": other side}`).
- Ladders only when you have real alt-line prices for every rung.
- Keep total sized exposure (board + Top 10 props + SGPs + parlays) near the daily cap (8u) and ≤3u per game. If you're over, drop C-tier plays first.
- `why` is two or three plain sentences. `inputs` is 3–5 short stats.

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
`NHL sides`, `NHL player props`, `NBA sides`, `NBA totals`, `NBA player props`, `WNBA sides`, `WNBA totals`, `WNBA player props`,
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
- `props`: 3–5 props per game with **real FanDuel prices** (from `odds.json` props or the web), each with
  `conviction`, `why` and `inputs` (section 00). Rank them best first. CFB: none (main lines only).
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

**FanDuel only.** Kaire bets at FanDuel and nowhere else. Every `odds` on the slate, in `featured` props, SGP legs and parlays is FanDuel's price
(`odds.json` has it per outcome under `fanduel`, with `edge_at_my_book`). Other books and Pinnacle are only for the fair line and line moves.
If FanDuel doesn't offer a market, it can't be a play: list it as a pass with `"betTo": "Not on FanDuel"` only if it's useful context.
SGP `Bet only at` prices refer to FanDuel's SGP slip.

Player props in `odds.json` (with `props_pulled_at_et`, since they can be carried over from an earlier run) only exist when a run pulled them with `props=1` (the slate task's manual run does)
or a manual run did. Otherwise find prop prices on the web and name the book.

### Top 10, longshots and ladders

- `featured` holds the day's **ten** biggest games (football first), same format as above. College player props aren't available to Kaire: CFB is main lines only.
- `longshots`: `[{"id","type":"Anytime TD"|"2+ TDs"|"First TD"|"Home run"|"Anytime goal","sport","game","start","pick","odds","book","conviction","modelP","prior","priorLabel","factors","inputs","why","category"}]`. On NFL days list the best anytime and 2+ TD candidates by usage (red-zone/goal-line share, snaps, TD history, team implied total), each tiered. Leave out `odds` when FanDuel's price isn't found; the board then shows the price to bet at. Stakes are capped by the `longMax` setting.
- `ladders`: `[{"id","sport","game","start","pick","base","why","category","rungs":[{"label":"80+","odds":120,"modelP":0.42}]}]`. Climb from a main Over that has a real read; rungs without a FanDuel price show the price to bet at.

### Cross-game parlays (`parlays`)

Also write 2–3 cross-game parlays for the Parlays tab. Rules:
- **Build from tiered research plays** (A or B, or C longshots in a clearly labeled lottery build). Never add a
  leg just to pump the payout. Give the parlay a `why` that says why these legs.
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

### Spreadsheet copy

After writing the slate, also save it as a workbook and send it:
1. Write the exact slate JSON to `/tmp/slate.json`.
2. `cd /home/claude/boolin && python -m pipeline.slate_xlsx /tmp/slate.json "/tmp/Sharp Board YYYY-MM-DD.xlsx"`
3. Send the .xlsx with SendUserFile (status proactive on scheduled runs). Edge and units in it are live formulas off its Settings sheet.

## 5. Report

Finish with a short message: number of plays by tier, the A-tier plays with FanDuel price and why in one line, the Top 10 games picked, total units
(including SGP stakes), anything that needs a check before start (goalies, lineups), and any data source that failed.
