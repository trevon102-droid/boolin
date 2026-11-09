#!/usr/bin/env bash
# Nightly Rackz Edge run (NBA, data-only): ingest -> bounce-back scan -> projections -> score lines (if you logged any).
# Usage: ./run_nightly.sh            (today)
#        ./run_nightly.sh 2026-11-10 (a specific slate date)
# Output is printed and saved to logs/nightly-YYYY-MM-DD.txt (gitignored).
set -uo pipefail
cd "$(dirname "$0")"

DAY="${1:-$(date +%F)}"
YEAR="${DAY%%-*}"; MONTH="${DAY:5:2}"
# NBA season label: Oct-Dec -> YEAR-(YEAR+1); Jan-Sep -> (YEAR-1)-YEAR
if [ "$((10#$MONTH))" -ge 10 ]; then START=$YEAR; else START=$((YEAR - 1)); fi
SEASON="$START-$(printf '%02d' $(((START + 1) % 100)))"

if [ ! -d .venv ]; then
  python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt
fi
# shellcheck disable=SC1091
. .venv/bin/activate

mkdir -p logs
LOG="logs/nightly-$DAY.txt"
{
  echo "=== Rackz Edge nightly $DAY (season $SEASON) ==="

  echo; echo "--- 1/4 ingest box scores ---"
  python ingest/nba_games.py "$SEASON" || { echo "INGEST FAILED, stopping (stale data would mislead the scans)"; exit 1; }

  echo; echo "--- 2/4 bounce-back / regression scan ---"
  python scans/bounce_back.py --season "$SEASON" || echo "(scan failed)"

  echo; echo "--- 3/4 projections for $DAY (last season seeds history) ---"
  python scans/projections.py --date "$DAY" --season "$(printf '%s-%02d' $((START - 1)) $((START % 100))),$SEASON" || echo "(no projections: no upcoming games on $DAY, or scan failed)"

  echo; echo "--- 4/4 score logged lines ---"
  if [ -f "data/lines/$DAY.csv" ]; then
    python scans/score_lines.py "data/lines/$DAY.csv" || echo "(score_lines failed)"
  else
    echo "No data/lines/$DAY.csv. Add tonight's lines (see README) to get edges vs the book."
  fi
} 2>&1 | tee "$LOG"
exit "${PIPESTATUS[0]}"
