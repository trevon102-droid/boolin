# Pre-game re-check

Instructions for the scheduled re-check that runs before start times. It does NOT build a new
slate. It re-prices the plays already on today's slate with fresh prices and news, so the board's
plays reflect current prices and news. Research mode: plays come off only for news (injury, lineup, goalie, market pulled), never for price alone.
(The board's stale-price limit is 24 hours, so same-day prices are never blocked just for age.)

Board: https://claude.ai/artifact/DbSgLx1GwBkC15aMK5YsPW (collection `slates`, doc id = today's date in ET).
Pricing rules: TASK.md sections 3, 3b and 3c. FanDuel prices only.

## Steps

1. `get` today's slate doc. If it doesn't exist, stop quietly (nothing to re-check).
2. Find games on today's slate that haven't started yet. If none, stop quietly.
3. Read `data/latest/manifest.json` (`health`, per-source `status`: ok / partial / error) and use `on_slate_day` events only. Then, for those games, check with WebSearch/WebFetch:
   - FanDuel's current price for every pick, longshot and ladder rung on the slate (FanDuel team and
     player pages: sportsbook.fanduel.com/teams/<league>/<team>/odds and /teams/<league>/roster/<player>/player-props;
     otherwise pages that label the price as FanDuel's). Never invent a price.
   - The sharp/consensus line for the prior (Pinnacle no-vig if findable; otherwise consensus).
   - News: inactives (NFL ~90 min before kickoff), confirmed MLB lineups and starters, confirmed NHL goalies
     (Daily Faceoff), NBA injury reports/rest and starting lineups, late scratches, weather changes.
4. For each item checked:
   - Update `odds`, `line`, `priceAt` (ISO time now, ET offset), and `prior` if the sharp line moved. SGPs: rebuild the
     slip and update `bookPrice` + `bookPriceAt`. Don't touch `betTo` or the tier: the board turns plays BET / NOT
     BETTABLE AT CURRENT PRICE from the fresh price against the unchanged `betTo`.
   - If news changes the read, adjust `conviction`, `factors`/`modelP` and the `why` with a named reason, or set `pass: true` and say why.
   - Research mode: a price move alone never turns a play off. Update `odds` and `priceAt`, and note big moves in `move`.
   - If FanDuel took the market down, set `pass: true` with `betTo: "Off the board at FanDuel"`.
   - Record moves in `open`/`move`/`steam` (re-price from the new sharp price; never add the move as a bonus).
5. Write back with `ArtifactData` `set` using the doc's `version` as `if_version`. Update `updated` and add one line
   to the top of `notes` saying what was re-checked and when. Never touch `bets` or `settings`.
6. Refresh the board's Research tab: trigger nothing new, just `git pull`, then follow TASK.md "Research tab"
   (export with the existing docs' versions, then `batch`).
7. Rebuild the spreadsheet (TASK.md, "Spreadsheet copy") and send it only if a play turned on or off,
   or its tier changed.
8. Notify (PushNotification) only when something changed that Kaire would act on: lead with the play, the new
   FanDuel price and the stake, then anything that went off. If nothing changed, stay silent.
