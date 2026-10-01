# Weekly prep and model review

Instructions for the weekly scheduled task (Tuesday). It researches deeply and writes STRUCTURED DATA the
daily slate task reads. No artifact page, no long prose report.

Board: https://claude.ai/artifact/DbSgLx1GwBkC15aMK5YsPW

## Part 1: Football prep (the coming week's NFL and FBS games)

Research with WebSearch/WebFetch (nflverse data in this repo's data/latest is the starting point). For each
NFL team and each FBS team playing a ranked or televised game, collect only numbers with a source:
- Usage (last 3+ games, note the sample): snap %, route participation, target share / targets per route,
  carries and red-zone/goal-line touches for skill players; who absorbs usage from injured starters.
- Defense: man vs zone %, single-high %, blitz and pressure rate, run D (YPC, explosive-run rate, YAC),
  yards allowed by position (WR/TE/RB) where published.
- Offense: pace (plays/game), early-down pass rate, OL injuries.
- CFB: SP+/FPI, returning production, tempo, QB status.
- Injuries to watch and their expected status by game day.

Write it with `ArtifactData` `set` to collection `prep`, doc id `nfl-<season>-wk<week>` (and `cfb-<season>-wk<week>`):
```json
{"week": 5, "updated": "Tue Oct 6, 11 AM ET", "sources": ["url", "..."],
 "teams": {"CLE": {"def": {"man_pct": 0.435, "single_high_pct": 0.757, "pressure_pct": 0.422, "blitz_pct": 0.233,
                            "run_ypc_allowed": 4.5, "explosive_run_pct": 0.16},
                   "off": {"plays_pg": 61.2, "early_down_pass_rate": 0.59},
                   "players": {"Denzel Boston": {"snap_pct": [0.92,0.93,0.89], "targets": [5,5,5], "routes_pct": null, "notes": "..."}},
                   "injuries": ["C Elgton Jenkins (concussion): out W4"]}}}
```
Use `null` for anything not found. Never invent numbers.

## Part 2: Model review

1. Read (read-only) the `bets` collection and the last 7 days of `slates`. Never write to `bets` or `settings`.
2. For settled bets, compute by sport and category: count, units, ROI, average CLV, and % beating the close.
   Mark any group with fewer than 200 bets as "noise".
3. Join bets to their slate picks (`pickId`, `slateId`) and, for each factor name in `factors`, compare average
   CLV for bets that had the factor vs. bets that didn't. Flag factors that look useless or harmful
   (again, low samples are noise).
4. Write the results to collection `prep`, doc id `review-<YYYY-MM-DD>`, as structured fields
   (`by_category`, `by_factor`, `suggestions`). Suggestions are proposals for TASK.md changes; don't edit TASK.md.
5. Notify (PushNotification) with a 2–3 line summary: units and CLV for the week, the strongest and weakest
   category, and any suggested rule change. If there are no settled bets, say so in one line.
