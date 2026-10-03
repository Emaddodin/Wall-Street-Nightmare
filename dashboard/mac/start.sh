#!/bin/bash
# What the "Gold Desk" icon on the Desktop runs: update, stop any older copy, start on your MT5, open the page.
# Gold Desk is analysis-only: its charts are your MT5's candles (every timeframe), nothing is sent to MT5,
# you trade in the MT5 app on your phone. Nothing is opened to the internet.
main() {
  local APP="$HOME/GoldDesk" CONF="$HOME/.golddesk" BRANCH=claude/eager-dirac-gtv0q1
  KRONOS=small; PORT=8765; MT5=auto
  [ -f "$CONF/config.sh" ] && . "$CONF/config.sh"

  echo "Gold Desk: checking for updates..."
  if git -C "$APP" fetch -q --depth 1 origin "$BRANCH" 2>/dev/null; then
    if [ "$(git -C "$APP" rev-parse HEAD)" != "$(git -C "$APP" rev-parse FETCH_HEAD)" ]; then
      git -C "$APP" reset -q --hard FETCH_HEAD
      chmod +x "$APP/dashboard/mac/"*.sh
      echo "Updated to the latest version."
      exec "$APP/dashboard/mac/start.sh"
    fi
    echo "Up to date."
  else
    echo "Couldn't check for updates (offline?). Starting the version you have."
  fi

  local pids
  pids=$(lsof -ti "tcp:$PORT" 2>/dev/null)
  if [ -n "$pids" ]; then
    echo "Stopping the Gold Desk that was already running."
    kill $pids 2>/dev/null; sleep 1
  fi

  cd "$APP/dashboard" || exit 1
  # Your MT5 is the only live source: every chart and timeframe on the page is MT5's own candles.
  if [ "$MT5" != off ] && mt5_ready; then
    mt5_mode
  fi
  echo
  echo "Gold Desk reads its charts only from your MT5, and MT5 isn't ready yet (see above)."
  echo "Open MT5, put GoldDeskBridge on the XAUUSD chart, then double-click Gold Desk again."
  return 1
}
# Open a page in Safari (Emad's browser), else the default one.
show() {
  open -a Safari "$1" 2>/dev/null || open "$1" 2>/dev/null || xdg-open "$1" 2>/dev/null
}
# MT5 on this Mac: keep GoldDeskBridge in it and, once it runs on a chart, take prices and orders from MT5.
mt5_ready() {
  local said
  said=$(python3 mt5bridge.py --install 2>/dev/null) || return 1           # no MT5 on this Mac
  echo "$said" > "$CONF/mt5_bridge.txt"                                     # where it went, compiled or why not
  python3 mt5bridge.py --check >/dev/null 2>&1
  case $? in
    0) return 0 ;;
    1) echo "Opening MT5 for the Gold Desk bridge..."                     # it ran before: MT5 brings it back
       local app
       app=$(ls -d /Applications/*.app "$HOME"/Applications/*.app 2>/dev/null | grep -iE 'metatrader|mt5|litefinance' | head -1)
       [ -n "$app" ] && open -g -a "$app"
       local i
       for i in $(seq 1 60); do
         python3 mt5bridge.py --check >/dev/null 2>&1 && return 0
         sleep 1
       done
       echo "MT5 didn't start the Gold Desk bridge within a minute (is GoldDeskBridge still on a chart?)."
       return 1 ;;
  esac
  echo
  echo "One time only, to connect Gold Desk to your MT5:"
  if echo "$said" | grep -q ": compiled\|already compiled"; then
    echo "  1. In MT5, find GoldDeskBridge under Expert Advisors in the Navigator (right-click there, Refresh if it's missing)."
  else
    echo "  1. In MT5 press F4 (MetaEditor), open Experts > GoldDeskBridge, press Compile, close MetaEditor."
  fi
  echo "  2. Drag GoldDeskBridge onto your XAUUSD chart, tick \"Allow Algo Trading\", press OK."
  echo "  3. Make sure the Algo Trading button at the top of MT5 is on, then double-click Gold Desk again."
  echo
  return 1
}
# Gold Desk on MT5's prices: analysis only, nothing is sent to MT5.
mt5_mode() {
  local args=(--mt5-bridge --entry-tf M5 --port "$PORT" --no-alerts)
  if [ "$KRONOS" != off ] && python3 -c "import torch" 2>/dev/null; then
    args+=(--kronos "$KRONOS")
  fi
  echo
  echo "Gold Desk is opening in Safari, connected to your MT5. Keep this window open while you trade."
  ( for _ in $(seq 1 120); do
      curl -fs -m 2 -o /dev/null "http://127.0.0.1:$PORT/api/state" && { show "http://127.0.0.1:$PORT"; exit; }
      sleep 1
    done ) &
  python3 server.py "${args[@]}" --no-browser && exit 0
  echo "Gold Desk on MT5 stopped."
  return 1
}
main "$@"
exit
