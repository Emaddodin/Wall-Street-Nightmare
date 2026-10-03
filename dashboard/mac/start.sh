#!/bin/bash
# What the "Gold Desk" icon on the Desktop runs: update, stop any older copy, start, open the page.
# With a VPS (VPS=stratton in ~/.golddesk/config.sh; off by default) the broker page runs there 24/7;
# this updates it, then opens it here through your SSH key. Nothing is opened to the internet.
main() {
  local APP="$HOME/GoldDesk" CONF="$HOME/.golddesk" BRANCH=claude/eager-dirac-gtv0q1
  ACCOUNT=demo; KRONOS=small; PORT=8765; VPS=; VPS_KRONOS=small; MT5=auto     # VPS=stratton to use one
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
  [ -f "$CONF/lf_session.json" ] || python3 server.py --litefinance-login

  if [ "$MT5" != off ] && mt5_ready; then
    mt5_mode
    echo "Starting Gold Desk on the LiteFinance website instead."
  fi

  if [ -n "$VPS" ] && ssh -o BatchMode=yes -o ConnectTimeout=8 "$VPS" true 2>/dev/null; then
    vps_mode
    echo "Starting Gold Desk on this Mac instead, so you can trade now."
  elif [ -n "$VPS" ]; then
    echo "Couldn't reach your VPS ($VPS). Starting Gold Desk on this Mac instead."
  fi

  local args=(--litefinance --account "$ACCOUNT" --entry-tf M5 --port "$PORT")
  if [ "$KRONOS" != off ] && python3 -c "import torch" 2>/dev/null; then
    args+=(--kronos "$KRONOS")
    if [ ! -s "$CONF/kronos_backtest_M5.txt" ]; then   # once: do Kronos and Boom/Crash beat a coin flip on gold?
      echo "Testing Kronos and Boom/Crash on the last 30 days of gold in the background (results show on the page)."
      (nice -n 15 python3 kronos_backtest.py --tf M5 --litefinance-days 30 --size "$KRONOS" \
         --out "$CONF/kronos_backtest_M5_trades.csv" > "$CONF/kronos_backtest_M5.txt" 2>&1 &)
    fi
  fi

  echo
  echo "Gold Desk is opening in Safari. Keep this window open while you trade; closing it stops Gold Desk."
  ( for _ in $(seq 1 120); do
      curl -fs -m 2 -o /dev/null "http://127.0.0.1:$PORT/api/state" && { show "http://127.0.0.1:$PORT"; exit; }
      sleep 1
    done ) &
  exec python3 server.py "${args[@]}" --no-browser
}
# Open a page in Safari (Emad's browser), else the default one.
show() {
  open -a Safari "$1" 2>/dev/null || open "$1" 2>/dev/null || xdg-open "$1" 2>/dev/null
}
# One plain line on what Gold Desk on the VPS is doing or what went wrong there.
vps_says() {
  ssh -o BatchMode=yes -o ConnectTimeout=8 "$VPS" '
    systemctl is-active -q golddesk || { echo "Gold Desk is not running on the VPS."; }
    journalctl -u golddesk --since "-3 min" --no-pager -o cat 2>/dev/null \
      | grep -E "refusing|429|Broker page|Waiting for|No saved|Error:|error:|Dashboard running" | tail -1' 2>/dev/null
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
# Gold Desk here on MT5's prices and account. The VPS keeps running for your phone pushes.
mt5_mode() {
  if [ -n "$VPS" ] && ssh -o BatchMode=yes -o ConnectTimeout=8 "$VPS" true 2>/dev/null; then
    vps_update >/dev/null 2>&1 &                    # keep the VPS current in the background
  fi
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
# Gold Desk on the VPS: latest code, service running.
vps_update() {
  ssh -o BatchMode=yes "$VPS" 'mkdir -p /root/.golddesk && chmod 700 /root/.golddesk && test -s /root/.golddesk/lf_session.json' 2>/dev/null \
    || scp -q -o BatchMode=yes "$CONF/lf_session.json" "$VPS:/root/.golddesk/lf_session.json"
  ssh -o BatchMode=yes "$VPS" "bash -s -- $BRANCH $ACCOUNT $VPS_KRONOS" < "$APP/dashboard/vps/setup.sh"
}
# Broker page and dashboard on the VPS 24/7; this Mac only shows the page.
vps_mode() {
  echo "Updating Gold Desk on your VPS..."
  if ! vps_update; then
    echo "Updating Gold Desk on the VPS didn't finish."
    return 1
  fi
  local url="http://127.0.0.1:$PORT" opened=0 up=0
  echo
  echo "Gold Desk runs on your VPS around the clock. Keep this window open to see it here; closing it doesn't stop the VPS."
  while true; do
    ssh -N -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
        -o LogLevel=QUIET -L "$PORT:127.0.0.1:8765" "$VPS" 2>/dev/null &   # no "channel 2: open failed" lines
    local tunnel=$! i
    for i in $(seq 1 240); do                       # after a restart the VPS page needs up to a minute
      curl -fs -m 2 -o /dev/null "$url/api/state" && { up=1; break; }
      kill -0 $tunnel 2>/dev/null || break
      if [ $((i % 20)) = 0 ]; then
        echo "Waiting for Gold Desk on the VPS... $(vps_says)"
      fi
      sleep 1
    done
    if [ $up = 0 ]; then
      kill $tunnel 2>/dev/null
      echo
      echo "Gold Desk on the VPS didn't open. What the VPS says: $(vps_says)"
      echo "It keeps trying there by itself; the icon goes back to it next time."
      return 1
    fi
    if [ $opened = 0 ]; then
      echo "Gold Desk is open in Safari."
      show "$url"
      opened=1
    fi
    wait $tunnel
    echo "Connection to the VPS dropped. Reconnecting..."
    sleep 3
  done
}

main "$@"
exit
