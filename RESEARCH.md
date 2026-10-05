# Research layer

`research/` turns the raw pipeline files into an analyst workstation. Its job is to answer, for any game:

1. What does the market say now, and what was it when Boolin first saw it?
2. How did it move?
3. What does Boolin's baseline model think, and where does it disagree?
4. What changed (injuries, starters, weather, prices, the model) and when?
5. What evidence supports or contradicts the thesis? What is still unknown?
6. How fresh is each input, and is it confirmed, reported, projected or derived?
7. Which past situations are comparable (as historical context)?
8. What would change the conclusion?
9. What happened, and was the analysis any good?

It does **not** produce picks. Conclusions are research statuses: `lean`, `watch`, `review required`, `pass`,
`insufficient information`. A **validation gate** keeps strong model/market disagreement from becoming a `lean` until
the league's model has validated holdout evidence (see "Model validation" below).

## Flow

```
data/latest/*.json (raw, written by pipeline/)          read-only here
        │
        ▼  python -m research.build   (runs in the workflow after every pull; ~1 s)
data/research/markets/<game date>.json     append-only market observations per game
data/research/timeline/<game date>.json    append-only change events per game
data/research/latest/cards.json            one research card per game (current build)
data/research/latest/board.json            daily research board
data/research/latest/manifest.json         research build health + input freshness
data/research/archive/<date>.json.gz       last pregame card per game (frozen at start, with the close)
data/research/results/<date>.json          final scores (ESPN scoreboard)
data/research/grades/<date>.json           postgame grades
data/research/notebooks/<game_key>.json    analyst notebooks (written by people, never by builds)
data/reference/nfl_games_history.json.gz   nflverse closing lines + results since 2012 (comparables)
        │
        ▼  python -m research.render
data/research/latest/index.html            the Research Desk view (not committed; regenerate any time)
```

**Where to look:** the Sharp Board's **Research** tab. The board page can't read GitHub, so the daily slate task
copies the research into the board's database (`python -m research.export`, see TASK.md "Research tab"):
collection `research`, doc `<date>` (board + manifest + keys) and `<date>~c1..n` (cards, packed under the 256 KiB
document cap). Every play and Top 10 game links to its card. `index.html` remains for local viewing.

`python -m research.build --backfill` replays `data/days/*` oldest first to seed market history and the timeline.

## Provenance (every value)

`{"value", "kind", "source", "as_of", "note"}` with `kind` one of:

| kind | meaning | examples |
|---|---|---|
| `confirmed` | official | posted MLB lineup card, final result |
| `reported` | from a feed, not an official confirmation | odds, weekly NFL injury report, ESPN injury list, MLB weather |
| `projected` | expected, not confirmed | MLB probable, NHL goalie with most starts, nflverse QB |
| `derived` | calculated by Boolin | model, no-vig probability, regressed stats |
| `unknown` | not in the data (`reason` says why) | TV, park factor, NFL wind before game day |

Missing values are never filled: they're `unknown` with a reason. Projected values are never shown as confirmed.

## Game research card (`research/1/card`)

| field | contents |
|---|---|
| `key` | `YYYY-MM-DD-LEAGUE-AWAY-HOME` (ET date) |
| `status` | `scheduled` / `started` / `final` |
| `start_et`, `start_label` | ET, DST-aware |
| `game` | teams (abbr + name), venue, context (series, division game, week), TV (`unknown` until a source has it) |
| `market` | per market `open` / `current` / `close` with timestamps, `movement` (line, no-vig prob, FanDuel price + implied change), `series` (every observation), `legend` |
| `research` | team stats per side with sample sizes and `sample_quality` |
| `availability` | `starters` (provenance kind), `lineups`, `injuries` (status, `status_norm`, practice, source, `key_player`) |
| `environment` | roof/outdoors, wind, temperature, condition, park factor |
| `model` | `home_win_p`, `proj_margin_home`, `proj_total`, additive `contributions`, `confidence`, `sample_warnings`, `assumptions`, `version` |
| `comparison` | market vs Boolin rows (prob, spread, total) with difference, label, direction; `overall`; caveat |
| `flags` | `{id, category, severity, title, why}`; categories market / injury / data / performance / environment |
| `recent_changes`, `changes_since_last_pull` | timeline events (last 24 h / this pull) |
| `freshness` | per component: `as_of`, `age_min`, `label`, `status`, `source`, `kind` |
| `summary` | `why_it_matters`, `supporting`, `contradicting`, `unknowns`, `conclusion {status, detail, gate, trigger, would_change_if, analyst_decision}` |
| `validation` | the league scorecard status the gate used: `calibration_status`, `validation_status`, `recommendation`, `lean_allowed` |
| `scenarios` | baseline + one-input-changed reruns; unsupported scenarios listed with the reason |
| `comparables` | `HISTORICAL CONTEXT`: situations, n, cover/over rates, warnings under 30 games |
| `notebook`, `postgame` | the analyst notebook and, after the game, result + grades |

### Market prices are kept apart

- `fanduel`: the actionable price (Kaire's book). `draftkings`: a specific book, context.
- `best` / `best_book`: best across tracked books. Context only; not necessarily obtainable.
- `fair_*`: no-vig probability (Pinnacle, else consensus). An estimate, not a price.
- **Open** = first time Boolin saw the market (the true opener isn't on the free feed).
  **Close** = last observation before start; pulls are periodic, so it's labeled "last before start".
- History is append-only: a repeat only bumps `last_seen`; replays of older pulls and anything at/after start are ignored.

### CLV

`markets.clv(position, close, market)` → `line_clv_pts` (points gained vs the close, oriented to the side),
`price_clv` (decimal-price ratio, only when the line is the same), `prob_clv` (close no-vig prob minus the price paid).

## Change events

`{t, game_key, type, field, old, new, source, cause, [direction], [detail]}` with types
`spread, moneyline, total, price, injury_status, injury_report, starter, lineup, weather, model, source_status`.

`cause` is `"Change detected; cause not confirmed."` for everything except model moves. Model moves list the model
components that moved (exact: the models are additive) plus the data updates in the same pull. When no component
moved enough, the text says the stored data doesn't identify a single confirmed cause.

A new weekly NFL injury report is one `injury_report` event (not 30 player diffs), and a source starting to cover a
game (e.g. the ESPN list once the game is on today's slate) is one event, not "added" per player.

## Boolin baseline models (NFL `baseline-0.2`, NHL/MLB `baseline-0.1 (uncalibrated)`)

| league | inputs | notes |
|---|---|---|
| NFL (0.2) | team EPA/play off/def (regressed n/(n+4)), rest, wind if outdoors | coefficients fit on the 2012-2021 replay (`python -m research.fit_nfl` → `validation/nfl_fit.json`): HFA 1.92, offense 58.15 and defense 32.45 pts per EPA/play, rest 0.072 pts/day (cap 0.72), margin SD 13.63; wind rule not fitted |
| NHL | 5v5 xGF/xGA per game (this season regressed toward last, k=20 GP), projected goalie GSAx/gp (k=30), back-to-back | multiplicative goals model, sequential decomposition |
| MLB | lineup OPS vs the starter's hand (k=600 PA), starter K-BB% + regressed ERA (half each), starter's average outs, bullpen load, postseason environment | Pythagorean 1.83 |
| CFB, NBA, WNBA | — | `available: false` with the reason |

Totals are shown but don't drive the overall disagreement label until they're calibrated. Run lines and puck
lines (fixed ±1.5) aren't compared to the projected margin.

## Research flags (thresholds in `research/flags.py`)

Market: spread move ≥1.5 (NFL), total move, no-vig move ≥3 pp, significant/extreme model disagreement, odds older
than 3 h, no FanDuel price. Injury: key-position downgrades (alert), other downgrades (watch), IL/long-term (info),
unconfirmed starters (watch within 3 h of start, else info), unresolved questionable players. Data: source
partial/error, stale source (>18 h), missing market/lineup/weather. Performance: small samples, recent-form vs
season divergence. NHL goalie sample; MLB bullpen 120+ pitches in 3 days, possible opener, small starter sample.

## Board

Today's games ranked by research importance: flag severity, changes since the last pull, changes in the last 24 h,
and model/market disagreement. `attention` (score ≥ 3 or any alert) vs `stable`; `upcoming` lists later games already
being tracked.

## Notebook

```
python -m research.notebook set 2026-10-05-NFL-ATL-NO --thesis "Saints run game vs depleted LBs" \
    --side home --market spread --support "..." --contra "..." --unknown "Fant status" \
    --decision monitor --trigger "only at NO -1.5 or better"
python -m research.notebook position 2026-10-05-NFL-ATL-NO --market spread --side home --line -1.5 --price -110
python -m research.notebook review 2026-10-05-NFL-ATL-NO --text "..." --correct partly
```

## Postgame grading

Once a game has been over for 3.5 h the build fetches the final score and grades the last pregame card:
model (Brier vs the market's closing no-vig Brier), market read (CLV if there's a position, else the line move
toward the thesis), thesis (won/lost at the closing number), weather (NFL wind, when both readings exist),
availability (not gradable yet). Overall = average of the gradable parts, as a letter.

## Model validation (`python -m research.validate`)

Measures the existing models before anyone tunes them. Runs offline; the workflow runs it before every research build.

```
python -m research.validate                         # canonical: data/research/validation/
python -m research.validate --league NFL            # filtered runs go to data/research/validation/filtered/ (not committed)
python -m research.validate --since 2026-01-01 --until 2026-10-05
```

Sources, never pooled:

| source | what | market benchmark |
|---|---|---|
| `live_archive` | the last pregame research card per game (`archive/`) + ESPN final (`results/`) | pregame no-vig price at the moment the model was built |
| `nfl_replay` | `data/reference/nfl_replay_inputs.json.gz` (`python -m pipeline.nfl_replay`, CI): the live NFL model's exact inputs for every game since 2012, from play-by-play in **earlier weeks only** | the **closing** line (harder than the price at model time) |

Point in time: a row is kept only if the model build and every market observation predate the start (replay: inputs
only from weeks before the game; wind/temp excluded because nflverse records game-time weather). Everything else is
in `excluded.jsonl` with the reason. Splits are chronological: live development = games on or before 2026-10-05
(when baseline-0.1 parameters were last changed), holdout = after; replay development = seasons before 2022,
holdout = 2022+.

Outputs (each carries `model_version`, `validation_version`, `data_cutoff`, `calibration_status`; tables are per
league → source → development / holdout / all):
`games.jsonl` (one chronological row per game, with provenance back to the archived card or replay row),
`summary.json`, `calibration.json` (Brier, log loss, accuracy, calibration buckets 50-55 … 80%+ folded to the
favourite, calibration error, favourite overconfidence), `market_comparison.json` (Boolin vs market on the same games,
paired t, disagreement buckets 0-2.5 … 20+ pp: does the side Boolin prefers win more often than the market said?),
`spread_validation.json`, `total_validation.json`, `confidence_validation.json`, `sample_validation.json`
(early-season groups + a k_games diagnostic on development games only), `disagreement_validation.json` (extreme
audit: ≥10 pp, NFL spread ≥5, CFB ≥7, with five candidate explanations scored), `failure_modes.json`,
`model_scorecard.json`.

### NFL fit (`python -m research.fit_nfl`)

Fits the NFL coefficients on development seasons only (least squares on margin and total; SD of the residuals
converts margin to win probability; k_games picked by leave-one-season-out log loss but kept at 4 unless another
value is better by > 0.001). The holdout is scored once afterwards against baseline-0.1 and the closing market and
written to `validation/nfl_fit.json`. The numbers are copied into `research/models.py` by hand (a test checks they
match). It isn't run by the workflow: refitting is a deliberate change, not a side effect of a data pull.

Result (2022+ holdout, n=1,122): Brier 0.2290 → 0.2267, log loss 0.6504 → 0.6445, calibration error 0.040 → 0.033;
the improvement over 0.1 is not statistically significant (t≈−1.4), and the closing market (Brier 0.2101) is still
clearly better (t≈5.3). Calibration was not the main problem: the inputs carry less information than the market.

Scorecard recommendation (on the primary holdout): `NOT READY` (holdout < 150 games: "we don't know yet", or no skill),
`CALIBRATION NEEDED` (calibration off and worse than the market), `PROMISING BUT UNCALIBRATED` (calibration off, not
detectably worse than the market), `CALIBRATED / RESEARCH READY` (holdout ≥ 300, calibration error ≤ 0.025,
favourite overconfidence within ±0.02), `OUTPERFORMING MARKET IN VALIDATED SAMPLE` (calibrated and better than the
market with t ≤ −2). `lean_allowed` additionally requires that the market is not significantly better in the
holdout. Validation never changes a model parameter; rows from an older model version are reported in their own
`<source> @<version>` slice, never mixed with the current version.

### The gate (`research/summary.py`)

Each card carries `validation` (the league's scorecard status). `summary.conclusion.gate` records whether the gate
changed the status, why, and the ungated status.

| situation | status |
|---|---|
| aligned | `pass` |
| mild disagreement (any) or low confidence | `watch` |
| significant disagreement, model not validated | `watch` at most, never `lean` |
| extreme disagreement, model not validated | `review required` |
| significant/extreme + small sample, or + unresolved key player | `review required` (even for a validated model) |
| significant/extreme, validated model, clean inputs | `lean` possible |

`lean` needs a scorecard recommendation of `CALIBRATED / RESEARCH READY` or better. The board puts `review required`
games in "Needs attention" with the reasons; the Research tab shows Model / Calibration / Validation per league and,
on each card, model confidence, sample quality, calibration status and validation status.

## Known limitations

- Models are uncalibrated baselines; disagreement is a research prompt, not an edge. Validation measures them; it
  doesn't fix them.
- NHL and MLB have no historical replay: their validation is the live archive only, which starts 2026-10-01.
- MLB bullpen workload counts only games whose starter MLB credits (`gamesStarted`); otherwise it is `unknown` and
  the model treats the bullpen as league average.
- Opening line = first seen by Boolin; close = last pull before start (pulls are 1–3 a day).
- No confirmed goalie/lineup/inactive feed: those stay projected until a re-check confirms them.
- Comparables for NHL/MLB come from Boolin's own graded archive and start empty.
- Player-level availability isn't in any model, so "QB sits" style scenarios are listed as unsupported.
- TV and park factors aren't in the data.
