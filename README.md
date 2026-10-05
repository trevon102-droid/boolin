# boolin

Automated data pulls for the **Rackz Sharp Board** betting desk. Football first (NFL + college football), plus MLB, NHL, NBA and WNBA.

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
| NBA | ESPN public API (same as WNBA): schedule, injuries, projections, team stats, player game logs | none |
| NFL | nflverse: schedule/lines, play-by-play EPA, injury reports | none |
| CFB | ESPN (every FBS game, AP rank, FPI, weather, injuries) + optional CollegeFootballData (team EPA, SP+) | `CFBD_API_KEY` (optional, free) |
| Injuries | ESPN game summaries (NHL, MLB) | none |

## Schedule (ET all year: each time has an EDT and an EST cron, and the workflow keeps the matching one)

- **8:54 AM ET (on demand)**: the daily slate task triggers a manual run with odds + props (workflow_dispatch). There is no scheduled odds pull.
- **10:30 AM ET**: refresh lineups/injuries (no odds), ahead of the 10:50 AM re-check
- **4:30 PM ET**: refresh lineups/injuries (no odds), ahead of the 4:50 PM re-check

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
Player props cost markets × games (about 25 credits a day across today's games). The slate task's manual run
pulls them while more than 100 credits are left (odds + props was 37 credits on Oct 2, 2026).
A push whose commit message contains `[props]` also pulls them once.

## Output

```
data/latest/manifest.json   # health + status per source (ok / partial / error / skipped), components, errors, credits left
data/latest/{odds,nfl,cfb,mlb,nhl,wnba,injuries}.json   # plain JSON, each with pulled_at_et
data/days/YYYY-MM-DD/*.json.gz   # gzipped daily snapshots, kept 45 days
```

A source is `partial` when its file was written but a piece failed (for example NFL schedule loaded but EPA
didn't). `build_all` also scans every file for `error` / `*_error` keys and checks the file is today's, so a
failure inside a source can't show up as `ok`. `error` means that source's latest file is from an earlier day.

Daily snapshots are gzipped to keep the git history small (~10x smaller than plain JSON). Files committed
before this change stay in history; shrinking that would need a history rewrite (not done).

## Tests

```
python -m pytest -q tests          # or, without pytest:  python tests/run_offline.py
```
The workflow runs the offline tests before every pull.

## Run locally

```
pip install -r requirements.txt
ODDS_API_KEY=... python -m pipeline.build_all
SOURCES=mlb,nhl RUN_ODDS=0 python -m pipeline.build_all
```
