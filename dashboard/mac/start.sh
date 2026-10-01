#!/bin/bash
# What the "Gold Desk" icon on the Desktop runs: update, stop any older copy, start, open the page.
# With a VPS (VPS=stratton in ~/.golddesk/config.sh, the default) the broker page runs there 24/7;
# this updates it, then opens it here through your SSH key. Nothing is opened to the internet.
main() {
  local APP="$HOME/GoldDesk" CONF="$HOME/.golddesk" BRANCH=claude/project-thread-ild2ro
  ACCOUNT=demo; KRONOS=small; PORT=8765; VPS=stratton; VPS_KRONOS=small
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

  if [ -n "$VPS" ] && ssh -o BatchMode=yes -o ConnectTimeout=8 "$VPS" true 2>/dev/null; then
    vps_mode
    echo "Your VPS didn't answer. Starting Gold Desk on this Mac instead."
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
  echo "Gold Desk is opening in your browser. Keep this window open while you trade; closing it stops Gold Desk."
  exec python3 server.py "${args[@]}"
}
# Broker page and dashboard on the VPS 24/7; this Mac only shows the page.
vps_mode() {
  echo "Updating Gold Desk on your VPS..."
  ssh -o BatchMode=yes "$VPS" 'mkdir -p /root/.golddesk && chmod 700 /root/.golddesk && test -s /root/.golddesk/lf_session.json' 2>/dev/null \
    || scp -q -o BatchMode=yes "$CONF/lf_session.json" "$VPS:/root/.golddesk/lf_session.json"
  if ! ssh -o BatchMode=yes "$VPS" "bash -s -- $BRANCH $ACCOUNT $VPS_KRONOS" < "$APP/dashboard/vps/setup.sh"; then
    return 1
  fi
  local url="http://127.0.0.1:$PORT" opened=0
  echo
  echo "Gold Desk runs on your VPS around the clock. Keep this window open to see it here; closing it doesn't stop the VPS."
  while true; do
    ssh -N -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
        -o LogLevel=QUIET -L "$PORT:127.0.0.1:8765" "$VPS" &       # quiet: no "open failed" lines while it starts
    local tunnel=$! i
    for i in $(seq 1 900); do                       # after a restart the VPS page needs a moment; the first start minutes
      curl -fs -m 2 -o /dev/null "$url/api/state" && break
      kill -0 $tunnel 2>/dev/null || break
      [ $((i % 30)) = 0 ] && echo "Gold Desk is still starting on the VPS (the very first start takes a few minutes)..."
      [ "$i" = 180 ] && ssh -o BatchMode=yes "$VPS" 'journalctl -u golddesk -n 8 --no-pager -o cat' 2>/dev/null
      sleep 1
    done
    if [ $opened = 0 ] && curl -fs -m 2 -o /dev/null "$url/api/state"; then
      open "$url" 2>/dev/null || xdg-open "$url" 2>/dev/null
      opened=1
    fi
    wait $tunnel
    echo "Connection to the VPS dropped. Reconnecting..."
    sleep 3
  done
}

main "$@"
exit
