# boolin

Automated data pulls for the **Rackz Sharp Board** betting desk. Football first (NFL + college football), plus MLB, NHL, NBA, men's college basketball (NCAAB) and WNBA.

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
| NCAAB | ESPN (every Division I game, AP rank, line, win projection, injuries, team stats; main lines only) | none |
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
NCAAB (from Nov 1, 2026) adds ~3 credits per odds run and never pulls props (college player props aren't offered).

## Output

```
data/latest/manifest.json   # health + status per source (ok / partial / error / skipped), components, errors, credits left
data/latest/{odds,nfl,cfb,mlb,nhl,wnba,nba,ncaab,injuries}.json   # plain JSON, each with pulled_at_et
data/days/YYYY-MM-DD/*.json.gz   # gzipped daily snapshots, kept 45 days
```

A source is `partial` when its file was written but a piece failed (for example NFL schedule loaded but EPA
didn't). `build_all` also scans every file for `error` / `*_error` keys and checks the file is today's, so a
failure inside a source can't show up as `ok`. `error` means that source's latest file is from an earlier day.

Daily snapshots are gzipped to keep the git history small (~10x smaller than plain JSON). Files committed
before this change stay in history; shrinking that would need a history rewrite (not done).

## Research desk

`research/` builds the analyst layer on top of the raw data: game research cards (market open/current/close,
Boolin vs market, availability with confirmed/projected labels, freshness), a "what changed" timeline, research
flags, a daily research board, scenarios, historical comparables, analyst notebooks and postgame grades.
The workflow runs `python -m research.build` after every pull. See [`RESEARCH.md`](RESEARCH.md).

Model validation (`python -m research.validate`, also in the workflow) scores the models against results and the
market, point in time, with a chronological holdout, and gates research conclusions on it
(`data/research/validation/`).

## Sharp Board: research vs execution, and CLV

- **Research conviction ≠ bet permission.** Each play shows its research tier (A/B/C, a research size) and,
  separately, an execution status: `BET` (FanDuel's timestamped, non-stale price is at or better than `betTo`,
  line first), `WAIT / RECHECK` (no or stale price, no timestamp, no readable `betTo`), `NOT BETTABLE AT CURRENT
  PRICE` (price or number worse than `betTo`) or `PASS` (news). "Log bet" only appears on `BET`. Logic:
  `EXEC` in `board/rackz-sharp-board.html`, tested by `tests/test_board_exec.py`.
- **CLV counts the line and the price.** Bets store `market_type`, `taken_line` + `taken_price`, and on close
  `close_line` + `close_price`, `line_delta`, `line_changed`, `price_clv`, `line_clv`, `combined_clv`,
  `key_numbers_crossed`. Price CLV is computed only on the same number (or markets without a line); when the line
  moved, line CLV shows the points gained or given up (direction-aware for Over/Under and spreads; 3 and 7 flagged in
  football) and combined CLV is left unavailable, because there is no validated price-per-point conversion. Old
  records (`odds`/`close` only) still read: their price CLV is marked "line not recorded".
- **SGP correlation estimates are not validated.** Each SGP shows the independent and joint probability, the
  uplift, the method (`analyst`, `simulation`, `independent`) and FanDuel's slip price with its timestamp. A
  `validated` label is only honoured once SGP validation exists in the repo (it doesn't yet). Stakes stay capped at 0.5u.
- **The market is the anchor.** The board shows market prior → analyst adjustment → research probability →
  current FanDuel price → bet permission. Probabilities are labelled "Research probability / analyst estimate"
  until a league's validation scorecard allows otherwise; in price mode a research % more than 10 points from the
  market gets no stake (review).

## NBA data-only model (`ingest/` + `scans/`)

A separate, odds-free NBA layer. It reads box scores only, projects tonight's players, and (optionally) scores
the projections against sportsbook lines you log by hand. Everything lives in a local SQLite file
(`data/rackz_edge.db`, gitignored).

```
./run_nightly.sh                  # ingest -> bounce-back scan -> projections -> score logged lines (today)
./run_nightly.sh 2026-11-10       # a specific slate date; output also saved to logs/nightly-DATE.txt

# the pieces
python ingest/nba_games.py 2026-27           # ESPN box scores -> SQLite (incremental; regular season only)
python scans/bounce_back.py                  # last game FGA >= avg+3 and FG% 15 pts off: bounce-back / regression
python scans/projections.py --date DATE      # ranked player-stat projections vs role-adjusted norm
python scans/score_lines.py data/lines/DATE.csv   # model vs real lines (calibrated P(over), edge, EV)
python scans/backtest.py                     # walk-forward accuracy / direction test (2025-26)
python scans/calibration.py [--write]        # is P(over) calibrated? --write refits data/calibration_map.json
python scans/line_proxy.py                   # Over/Under hit rates vs average/median proxy lines
python -m pytest tests/test_bounce_back.py tests/test_calibration.py
```

**Logging lines:** copy `data/lines/2026-11-09.csv` as a template: `date,player,stat,line,over_odds,under_odds,app_last5`
(stat = PTS, REB, AST or 3PM). For X+ ladders (e.g. "3+ threes") use line = X minus 0.5 and leave `under_odds` blank
(ladder book probabilities include the vig). Name files `data/lines/YYYY-MM-DD.csv` and `run_nightly.sh` picks them up.

**What it is and isn't (from the backtests):** it ranks players sensibly and its P(over) is calibrated to within a
few points in the 40-60% range, but it is only ~1% more accurate than a season average, longshot probabilities run
a few points high, and it has not been shown to beat real lines. The teammate-out lift (`--lift`) had no predictive
value and is off by default. Preseason, All-Star and NBA Cup final games are excluded from the data.

## Tests

```
python -m pytest -q tests          # or, without pytest:  python tests/run_offline.py
                                   # (tests/test_board_exec.py runs the board's EXEC module in node)
```
The workflow runs the offline tests before every pull.

## Run locally

```
pip install -r requirements.txt
ODDS_API_KEY=... python -m pipeline.build_all
SOURCES=mlb,nhl RUN_ODDS=0 python -m pipeline.build_all
```
