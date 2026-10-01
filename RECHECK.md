# Pre-game re-check

Instructions for the scheduled re-check that runs before start times. It does NOT build a new
slate. It re-prices the plays already on today's slate with fresh prices and news, so the board's
stale-price rule (no stake on a price older than 60 minutes) lets good plays through and kills bad ones.

Board: https://claude.ai/artifact/DbSgLx1GwBkC15aMK5YsPW (collection `slates`, doc id = today's date in ET).
Pricing rules: TASK.md sections 3, 3b and 3c. FanDuel prices only.

## Steps

1. `get` today's slate doc. If it doesn't exist, stop quietly (nothing to re-check).
2. Find games on today's slate that haven't started yet. If none, stop quietly.
3. For those games, check with WebSearch/WebFetch:
   - FanDuel's current price for every pick, longshot and ladder rung on the slate (FanDuel team and
     player pages: sportsbook.fanduel.com/teams/<league>/<team>/odds and /teams/<league>/roster/<player>/player-props;
     otherwise pages that label the price as FanDuel's). Never invent a price.
   - The sharp/consensus line for the prior (Pinnacle no-vig if findable; otherwise consensus).
   - News: inactives (NFL ~90 min before kickoff), confirmed MLB lineups and starters, confirmed NHL goalies
     (Daily Faceoff), late scratches, weather changes.
4. For each item checked:
   - Update `odds`, `priceAt` (ISO time now, ET offset), and `prior` if the sharp line moved.
   - If news changes the read, adjust `factors`/`modelP` with a named reason, or set `pass: true` and say why.
   - If FanDuel took the market down, set `pass: true` with `betTo: "Off the board at FanDuel"`.
   - Record moves in `open`/`move`/`steam` (re-price from the new sharp price; never add the move as a bonus).
5. Write back with `ArtifactData` `set` using the doc's `version` as `if_version`. Update `updated` and add one line
   to the top of `notes` saying what was re-checked and when. Never touch `bets` or `settings`.
6. Rebuild the spreadsheet (TASK.md, "Spreadsheet copy") and send it only if a play turned on, turned off,
   or moved past its bet-to price.
7. Notify (PushNotification) only when something changed that Kaire would act on: lead with the play, the new
   FanDuel price and the stake, then anything that went off. If nothing changed, stay silent.
