#!/bin/bash
# What the "Gold Desk" icon on the Desktop runs: update, stop any older copy, start, open the page.
main() {
  local APP="$HOME/GoldDesk" CONF="$HOME/.golddesk" BRANCH=claude/project-thread-ild2ro
  ACCOUNT=demo; KRONOS=small; PORT=8765
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

  local args=(--litefinance --account "$ACCOUNT" --port "$PORT")
  if [ "$KRONOS" != off ] && python3 -c "import torch" 2>/dev/null; then
    args+=(--kronos "$KRONOS")
    if [ ! -s "$CONF/kronos_backtest.txt" ]; then      # once: does Kronos beat a coin flip on gold?
      echo "Testing Kronos on the last 20 days of gold in the background (results show on the page)."
      (nice -n 15 python3 kronos_backtest.py --litefinance-days 20 --size "$KRONOS" \
         --out "$CONF/kronos_backtest_trades.csv" > "$CONF/kronos_backtest.txt" 2>&1 &)
    fi
  fi

  echo
  echo "Gold Desk is opening in your browser. Keep this window open while you trade; closing it stops Gold Desk."
  exec python3 server.py "${args[@]}"
}
main "$@"
exit
