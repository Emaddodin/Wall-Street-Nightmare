#!/bin/bash
# Runs on the VPS (as root), sent there by the Gold Desk icon on the Mac:
#   ssh stratton 'bash -s -- BRANCH ACCOUNT KRONOS' < dashboard/vps/setup.sh
# Keeps /opt/golddesk on the latest code and the golddesk service running 24/7.
# The page listens on 127.0.0.1 only; the Mac reaches it through its SSH key. Deletes nothing.
main() {
  set -e
  local BRANCH=${1:-claude/project-thread-ild2ro} ACCOUNT=${2:-demo} KRONOS=${3:-small}
  local REPO=https://github.com/Emaddodin/Wall-Street-Nightmare.git APP=/opt/golddesk CONF=/root/.golddesk
  local changed=0

  if [ -d "$APP/.git" ]; then
    git -C "$APP" fetch -q --depth 1 origin "$BRANCH"
    if [ "$(git -C "$APP" rev-parse HEAD)" != "$(git -C "$APP" rev-parse FETCH_HEAD)" ]; then
      git -C "$APP" reset -q --hard FETCH_HEAD; changed=1
    fi
  else
    echo "First run: installing Gold Desk on the VPS. This takes a few minutes, once."
    git clone -q --depth 1 --branch "$BRANCH" --filter=blob:none --sparse "$REPO" "$APP"
    git -C "$APP" sparse-checkout set dashboard
    changed=1
  fi

  pine_feed || echo "The TradingView Kronos feed didn't install this time; Gold Desk carries on."

  if [ ! -s "$CONF/lf_session.json" ]; then
    echo "No LiteFinance login in $CONF yet, so the trading page wasn't started."; exit 2
  fi

  # Python: reuse the Kronos environment already on the VPS (it has CPU PyTorch), else make one
  local PY
  if [ -x /root/kronos/.venv/bin/python ]; then
    PY=/root/kronos/.venv/bin/python
  else
    [ -x "$APP/venv/bin/python" ] || { python3 -m venv "$APP/venv"; "$APP/venv/bin/pip" install -q torch --index-url https://download.pytorch.org/whl/cpu; }
    PY="$APP/venv/bin/python"
  fi
  if ! "$PY" -c "import playwright, pandas, einops, huggingface_hub, safetensors" 2>/dev/null; then
    "$PY" -m pip install -q --timeout 30 --retries 3 playwright pandas "einops==0.8.1" huggingface_hub safetensors tqdm
    changed=1
  fi
  if [ ! -f "$CONF/.chromium_ok" ]; then
    "$PY" -m playwright install --with-deps chromium && touch "$CONF/.chromium_ok"
  fi

  # Kronos code: the copy already on the VPS, else a fresh clone
  local KREPO
  KREPO=$(dirname "$(dirname "$(find /root/kronos -maxdepth 3 -path '*/model/kronos.py' 2>/dev/null | head -1)")" 2>/dev/null || true)
  if [ -z "$KREPO" ] || [ "$KREPO" = "." ]; then
    [ -d "$APP/Kronos/.git" ] || git clone -q --depth 1 https://github.com/shiyu-coder/Kronos.git "$APP/Kronos"
    KREPO="$APP/Kronos"
  fi

  local kronos_args=""
  [ "$KRONOS" != off ] && kronos_args="--kronos $KRONOS --kronos-repo $KREPO"
  local unit=/etc/systemd/system/golddesk.service
  local want
  want=$(cat <<UNIT
[Unit]
Description=Gold Desk (LiteFinance broker page + dashboard on 127.0.0.1:8765)
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$APP/dashboard
Environment=HOME=/root PYTHONUNBUFFERED=1
ExecStart=$PY server.py --litefinance --lf-headless --account $ACCOUNT --entry-tf M5 $kronos_args --no-browser --port 8765
Restart=always
RestartSec=5
MemoryMax=1800M

[Install]
WantedBy=multi-user.target
UNIT
)
  if [ "$(cat "$unit" 2>/dev/null)" != "$want" ]; then
    printf '%s\n' "$want" > "$unit"
    systemctl daemon-reload
    systemctl enable -q golddesk
    changed=1
  fi

  if [ "$changed" = 1 ] || ! systemctl is-active -q golddesk; then
    systemctl restart golddesk
    echo "Gold Desk on the VPS (re)started on the latest version."
  else
    echo "Gold Desk on the VPS is running and up to date."
  fi

  # Once: do Kronos and the Boom / Crash calls beat a coin flip on the last 30 days of M5 gold?
  # Low priority, one core at most; the result shows on the page.
  local BT="$CONF/kronos_backtest_M5.txt"
  if [ "$KRONOS" != off ] && [ ! -s "$BT" ] && ! systemctl is-active -q golddesk-backtest; then
    systemd-run -q --unit golddesk-backtest --collect -p Nice=19 -p CPUQuota=100% -p MemoryMax=1200M \
      -p WorkingDirectory="$APP/dashboard" --setenv=HOME=/root \
      /bin/sh -c "exec $PY kronos_backtest.py --tf M5 --litefinance-days 30 --size $KRONOS --repo $KREPO \
                  --out $CONF/kronos_backtest_M5_trades.csv > $BT 2>&1" \
      && echo "Testing Kronos and Boom/Crash on the last 30 days of gold in the background (results show on the page)."
  fi
}
# Kronos paste lines for the TradingView indicator on port 8791, locked with the Kronos chart's key.
# Emad asked for this in writing on 2026-10-01. Read-only: it serves forecasts, nothing else.
pine_feed() {
  local KEY=/root/kronos/chart/token.txt DST=/root/kronos/pine unit=/etc/systemd/system/kronos-pine-feed.service
  if [ ! -s "$KEY" ]; then
    echo "No Kronos chart key on the VPS, so the TradingView feed was skipped."; return 0
  fi
  local new=0
  mkdir -p "$DST"
  cmp -s "$APP/dashboard/pine/pine_feed.py" "$DST/pine_feed.py" || { cp "$APP/dashboard/pine/pine_feed.py" "$DST/"; new=1; }
  if ! cmp -s "$APP/dashboard/pine/kronos-pine-feed.service" "$unit"; then
    cp "$APP/dashboard/pine/kronos-pine-feed.service" "$unit"
    systemctl daemon-reload
    systemctl enable -q kronos-pine-feed
    new=1
  fi
  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw allow 8791/tcp >/dev/null
  fi
  if [ "$new" = 1 ] || ! systemctl is-active -q kronos-pine-feed; then
    systemctl restart kronos-pine-feed
  fi
  echo "TradingView Kronos lines: your chart link with 8790 changed to 8791."
}
main "$@"
exit
