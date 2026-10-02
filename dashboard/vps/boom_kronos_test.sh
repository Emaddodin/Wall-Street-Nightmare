#!/bin/sh
# Once, for the "Rebuild BOOM/CRASH" thread: does a Kronos vote improve the smart money BOOM / CRASH setups?
# Downloads a year of Dukascopy gold (not LiteFinance: it rate-limits this VPS), finds the setups, has Kronos
# forecast exactly those candles, and backtests with and without its vote. Resumable; changes no service.
#   boom_kronos_test.sh PYTHON KRONOS_REPO SIZE      (run from dashboard/, by setup.sh with Nice=19)
# Results: ~/.golddesk/kronos_research/boom_kronos_*.txt, readable at /api/research/<file>.
set -e
PY=$1 KREPO=$2 SIZE=${3:-small}
OUT=$HOME/.golddesk/kronos_research
mkdir -p "$OUT"
D=$OUT/boom_data
mkdir -p "$D"
CSV=data/dukascopy_xauusd_m1.csv.gz
echo "downloading Dukascopy gold" > "$OUT/boom_kronos_progress.txt"
[ -s "$CSV" ] || "$PY" fetch_history.py dukascopy 2025-10-01 2026-10-01
for name in default loose; do
  set --
  [ "$name" = loose ] && set -- --set sweep_pivot=3 --set choch_within=24
  echo "finding $name setups" > "$OUT/boom_kronos_progress.txt"
  "$PY" boom_backtest.py "$CSV" --split 2026-06-01 --dump "$D/setups_$name.txt" --out "$D/trades_$name.csv" "$@" \
    > "$OUT/boom_kronos_${name}_without.txt"
  echo "Kronos on the $name setups" > "$OUT/boom_kronos_progress.txt"
  "$PY" boom_kronos_votes.py "$CSV" "$D/setups_$name.txt" "$D/votes_$name.csv" --repo "$KREPO" --size "$SIZE"
  "$PY" boom_backtest.py "$CSV" --split 2026-06-01 --kronos "$D/votes_$name.csv" --out "$D/trades_${name}_k.csv" "$@" \
    > "$OUT/boom_kronos_${name}_with.txt"
done
echo "All BOOM/CRASH Kronos tests done" > "$OUT/boom_kronos_progress.txt"
