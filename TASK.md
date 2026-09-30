# Slate playbook

Instructions for the daily Claude run that turns this repo's data into a priced card on the
**Rackz Sharp Board** artifact: https://claude.ai/artifact/DbSgLx1GwBkC15aMK5YsPW

The board does all sizing math itself (edge, fair odds, quarter Kelly, caps). The job here is to
supply honest **prices** and **model probabilities**, plus the reasoning.

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

Price every game the user cares about (MLB, NHL, WNBA daily; NFL on game weeks). Include **passes**:
a play the user might like with no edge at the current price goes on the board with a `betTo` number,
so they know what line would make it a bet.

- Use the **best available price** from `odds.json` (`best_price`/`best_book`), not just FanDuel.
- Put the Pinnacle two-way prices in `sharp` when there's a Pinnacle line (`{"odds": side, "other": other side}`).
- Ladders only when you have real alt-line prices for every rung.
- Keep total sized exposure near the daily cap (8u) and ≤3u per game. If you're over, drop the lowest-edge plays.
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
`NFL sides`, `NFL totals`, `NFL player props`, `NFL TD scorers`.

### Writing it

Use the `ArtifactData` tool with the board URL above:
- New day: `set` on `slates` / `<date>` (no `if_version`).
- Doc already exists (a rerun): `get` it first, then `set` with its `version` as `if_version`.
- Don't touch `bets` or `settings`. Those belong to the user.

## 5. Report

Finish with a short message: number of plays, top 3 by edge, total units, anything that needs a
check before start (goalies, lineups), and any data source that failed.
