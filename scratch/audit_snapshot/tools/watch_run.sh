#!/bin/bash
# Watch the book after the scout-row fix. Exit early on a trade or a break.
cd /home/tbt/bot
START=$(date +%s)
LIMIT=$((55*60))
SHAPES=0
while true; do
  NOW=$(date +%s)
  ELAPSED=$(( NOW - START ))

  # a completed or open trade is the thing we are waiting for
  TRADES=$(/home/tbt/venv/bin/python - <<"PY" 2>/dev/null
import json
try:
    b = json.load(open("data/book.json"))
except Exception:
    print("0 0 100.00"); raise SystemExit
cl = b.get("closed", []) or []
op = b.get("open", []) or []
print(len(cl), len(op), b.get("equity", 100.0))
PY
)
  set -- $TRADES
  CLOSED=${1:-0}; OPEN=${2:-0}; EQ=${3:-100.00}

  # shapes the book actually read since the fix
  S=$(journalctl -u tbt-paper --since "-60 min" --no-pager -o cat 2>/dev/null | grep -cE "level .* entry there|reading [0-9]")
  [ "$S" -gt "$SHAPES" ] && SHAPES=$S

  # anything down?
  DOWN=""
  for u in tbt-paper tbt-scout tbt-guard tbt-chrome tbt-panel; do
    systemctl is-active --quiet $u || DOWN="$DOWN $u"
  done
  ERRS=$(journalctl -u tbt-paper --since "-10 min" --no-pager -o cat 2>/dev/null | grep -icE "traceback|poll failed")

  if [ "$CLOSED" != "0" ] || [ "$OPEN" != "0" ]; then
    echo "=== TRADE ==="
    echo "  open: $OPEN  closed: $CLOSED  equity: \$$EQ"
    journalctl -u tbt-paper --since "-60 min" --no-pager -o cat | grep -iE "OPEN|ENTRY|TARGET|STOP|filled|closed" | tail -20
    exit 0
  fi
  if [ -n "$DOWN" ]; then
    echo "=== SERVICE DOWN:$DOWN ==="
    for u in $DOWN; do journalctl -u $u -n 8 --no-pager -o cat | cut -c1-140; done
    exit 0
  fi
  if [ "$ERRS" -gt 20 ]; then
    echo "=== BOOK ERRORING: $ERRS in 10 min ==="
    journalctl -u tbt-paper --since "-10 min" --no-pager -o cat | grep -A6 -i traceback | head -20
    exit 0
  fi
  if [ "$ELAPSED" -ge "$LIMIT" ]; then
    echo "=== still hunting after $((ELAPSED/60))m ==="
    echo "  equity: \$$EQ   trades: $CLOSED closed / $OPEN open"
    echo "  book errors (10m): $ERRS"
    echo "  --- best readings the book saw ---"
    journalctl -u tbt-paper --since "-60 min" --no-pager -o cat | grep -iE "reading|refused|not sure|sure --" | tail -12 | cut -c1-150
    echo "  --- what the scout is finding ---"
    journalctl -u tbt-scout --since "-60 min" --no-pager -o cat | grep -cE "module\(s\) from" | sed "s/^/    ripe reads: /"
    journalctl -u tbt-scout --since "-60 min" --no-pager -o cat | grep -E "level .*(held|remade)" | tail -6 | cut -c1-150
    exit 0
  fi
  sleep 60
done
