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

## Game sims (GameSim v1.2, `pipeline/sim.py`)

A **market-anchored simulation** of each Top 10 game (NFL, CFB, MLB, NHL): the range of plausible game scripts and
player outcomes. It is a research/scenario tool, not a predictive betting model, and is separate from the betting
probability layer:

- The raw simulation is blended 50% toward a market baseline by scaling scoring efficiency only; volume, usage and
  player shares never change and props are never back-solved. Football total + spread is a *market-implied
  baseline*; MLB/NHL use a *market-derived scoring baseline* (Pythagorean split of the no-vig moneyline, or a
  single-side implied price when only one side is posted, or total-only with the raw split retained). The card
  shows raw → baseline → anchored.
- Win/over/cover and prop over/under/push percentages are **simulation frequencies**, not calibrated or validated
  probabilities. They never create or upgrade a BET, never change a tier, stake or exposure, and are never copied
  into `modelP`.
- Runs of 100 games repeat until the simulated outputs converge (averages, total and margin SE, and the win /
  over / cover frequencies that exist for the market; cap 100 runs). **Stable means Monte Carlo convergence only**:
  the 95% Monte Carlo simulation interval is shown apart from model/data uncertainty.
- Player distributions (mean, median, P10–P90, over/under/push per line) come from the same simulated games, so every
  line and ladder rung for a stat comes from one distribution; the board keeps them in a collapsed *Distribution* view.
- CFB is a simplified college-football scenario model on the shared drive engine; MLB extras use the automatic runner
  only in the regular season; NHL OT is a 3v3 OT / shootout approximation.
- Missing inputs are listed in `data_quality` (`clean` / `partial` / `degraded` / `unknown`) and badged on the card.
- The simulation/market difference is labelled *Not validated edge*. Boolin's research layer stays the authority.

Specs, steps and simplifications: TASK.md, "Game sims". Tests: `tests/test_sim.py`.

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
