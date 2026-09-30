# boolin

Automated data pulls for the **Rackz Sharp Board** betting desk. Football first (NFL + college football), plus MLB, NHL and WNBA.

GitHub Actions pulls free data sources on a schedule and commits clean JSON to `data/`.
A daily Claude scheduled task reads it, adds news from the web, prices every play, and writes the
slate to the board. See [`TASK.md`](TASK.md) for that playbook.

## Sources

| Sport | Source | Key needed |
|---|---|---|
| Odds (all) | [The Odds API](https://the-odds-api.com): best price per side, Pinnacle no-vig fair line | `ODDS_API_KEY` |
| MLB | MLB Stats API: probables, game logs, lineups, splits, bullpen usage, weather, umps | none |
| NHL | NHL web API + MoneyPuck CSVs: rest, standings, goalies, 5v5 xG%, GSAx | none |
| WNBA | ESPN public API: schedule, injuries, projections, player game logs | none |
| NFL | nflverse: schedule/lines, play-by-play EPA, injury reports | none |
| CFB | ESPN (every FBS game, AP rank, FPI, weather, injuries) + optional CollegeFootballData (team EPA, SP+) | `CFBD_API_KEY` (optional, free) |
| Injuries | ESPN game summaries (NHL, MLB) | none |

## Schedule (UTC cron; ET shown for daylight time)

- **7:15 AM ET**: everything + odds
- **12:30 PM ET**: refresh lineups/injuries (no odds)
- **5:30 PM ET**: refresh lineups/injuries (no odds)

Any push that changes `pipeline/` also triggers a run. You can run it by hand from
**Actions → Pull sports data → Run workflow** (with options for odds, props, or a specific date).

## Setup

1. **Odds key**: sign up at the-odds-api.com, then in this repo go to
   **Settings → Secrets and variables → Actions → New repository secret**, name it `ODDS_API_KEY`.
   Without it, everything else still runs and odds show as `skipped` in the manifest.
   Optional: add a free key from collegefootballdata.com as `CFBD_API_KEY` for college EPA and SP+ ratings.
2. **Actions write access**: **Settings → Actions → General → Workflow permissions →
   Read and write permissions** (so the job can commit data).

## Credit budget (The Odds API)

The job asks for 10 books at once (counts as 1 region) and 3 markets, so about **3 credits per
in-season sport per odds run**. One odds run a day across 4–5 sports is ~12–15 credits, or ~360–450 a month,
which fits the 500-credit free tier with little room. Upgrade before adding more odds runs or props.
Player props (`PROPS=1`) cost markets × games, so they're off by default. Turn them on once
you're on a paid tier.

## Output

```
data/latest/manifest.json   # what ran, status per source, credits left
data/latest/{odds,nfl,cfb,mlb,nhl,wnba,injuries}.json
data/days/YYYY-MM-DD/...     # daily snapshots, kept 45 days
```

## Run locally

```
pip install -r requirements.txt
ODDS_API_KEY=... python -m pipeline.build_all
SOURCES=mlb,nhl RUN_ODDS=0 python -m pipeline.build_all
```
