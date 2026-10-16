# Sharp Board page (mirror)

`rackz-sharp-board.html` is a copy of the live Rackz Sharp Board page
(https://claude.ai/artifact/DbSgLx1GwBkC15aMK5YsPW) so its math can be audited and diffed.
The live artifact is the source of truth; this copy is updated when the page changes.
It is the page body only (the artifact host adds the doctype/head wrapper), and it reads slates,
bets and settings from the artifact's own database, so it won't load data when opened from disk.

Where the math lives: `sizeIt`, `evalPick`, `sgpEval`, `parlayEval`, `longEval`, `ladEval` near the top of the `<script>`.
Execution (bet permission, line-aware CLV, SGP method labels, market anchor) is the `EXEC` module between
`/*EXEC_JS_BEGIN*/` and `/*EXEC_JS_END*/`, tested in node by `tests/test_board_exec.py`. Research tier and bet
status are separate columns: a play can be A-tier research and `NOT BETTABLE AT CURRENT PRICE` at the same time.
Bet records are additive (schema 2 adds the line fields); older records keep working.
Game sims render on the Top 10 cards via `simCard()` between `/*SIM_JS_BEGIN*/` and `/*SIM_JS_END*/` (tested in node by
`tests/test_sim.py`): market comparison raw → market-implied / market-derived baseline → anchored, simulation/market
difference marked "Not validated edge", simulation uncertainty (95% Monte Carlo simulation interval) vs model/data
uncertainty, top-5 player mean / median / P10–P90 with a collapsed *Distribution* section (P10–P90 and prop line
over/under/push simulation frequencies), and data-quality badges. v1 sim records without the newer fields still render.
