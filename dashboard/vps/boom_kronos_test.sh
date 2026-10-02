#!/bin/sh
# Once, for the "Rebuild BOOM/CRASH" thread: does a Kronos vote improve the M1 BOOM / CRASH limit orders?
# Downloads a year of Dukascopy gold (not LiteFinance: it rate-limits this VPS), finds every order the M1 ICT
# entries would place, has Kronos forecast the M5 candle before each, and backtests with and without its vote.
# Resumable; changes no service.
#   boom_kronos_test.sh PYTHON KRONOS_REPO SIZE      (run from dashboard/, by setup.sh with Nice=19)
# Results: ~/.golddesk/kronos_research/boom_kronos_m1_*.txt, readable at /api/research/<file>.
set -e
PY=$1 KREPO=$2 SIZE=${3:-small}
OUT=$HOME/.golddesk/kronos_research
D=$OUT/boom_data
mkdir -p "$D"
P=$OUT/boom_kronos_progress.txt
CSV=data/dukascopy_xauusd_m1.csv.gz
echo "M1: downloading Dukascopy gold" > "$P"
[ -s "$CSV" ] || "$PY" fetch_history.py dukascopy 2025-10-01 2026-10-01
echo "M1: finding the orders" > "$P"
"$PY" ict_backtest.py "$CSV" --split 2026-06-01 --dump "$D/orders_m1.txt" --out "$D/trades_m1.csv" \
  > "$OUT/boom_kronos_m1_without.txt"
echo "M1: Kronos on the orders" > "$P"
"$PY" boom_kronos_votes.py "$CSV" "$D/orders_m1.txt" "$D/votes_m1.csv" --repo "$KREPO" --size "$SIZE"
"$PY" ict_backtest.py "$CSV" --split 2026-06-01 --kronos "$D/votes_m1.csv" --out "$D/trades_m1_k.csv" \
  > "$OUT/boom_kronos_m1_with.txt"
echo "All BOOM/CRASH M1 Kronos tests done" > "$P"
