#!/bin/sh
# Once, for the "Rebuild BOOM/CRASH" thread: do silver SMT, the dollar index, US bonds and the S&P help call gold?
# Downloads a year of free Dukascopy M1 for each (not LiteFinance: it rate-limits this VPS) and scores the mesh's
# cross-market votes after 2026-06-01. Resumable; changes no service.
#   mesh_nodes_test.sh PYTHON      (run from dashboard/, by setup.sh with Nice=19)
# Result: ~/.golddesk/kronos_research/mesh_nodes_cross.txt, readable at /api/research/mesh_nodes_cross.txt.
set -e
PY=$1
OUT=$HOME/.golddesk/kronos_research
P=$OUT/mesh_nodes_progress.txt
mkdir -p "$OUT"
GOLD=data/dukascopy_xauusd_m1.csv.gz
echo "downloading gold" > "$P"
[ -s "$GOLD" ] || "$PY" fetch_history.py dukascopy 2025-10-01 2026-10-01
FILES=""
for S in XAGUSD DOLLARIDXUSD USTBONDTRUSD USA500IDXUSD; do
  F=data/dukascopy_$(echo $S | tr 'A-Z' 'a-z')_m1.csv.gz
  echo "downloading $S" > "$P"
  [ -s "$F" ] || "$PY" fetch_history.py dukascopy 2025-10-01 2026-10-01 $S || true
  [ -s "$F" ] && FILES="$FILES $F"
done
echo "scoring" > "$P"
{ echo "Files:"; for F in $FILES; do echo "  $F $(du -k "$F" | cut -f1) KB"; done
  "$PY" nodes.py cross "$GOLD" $FILES; } > "$OUT/mesh_nodes_cross.txt" 2>&1
echo "All mesh node tests done" > "$P"
