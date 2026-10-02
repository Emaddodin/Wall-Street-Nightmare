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
    get_browser
  fi
  alerts_topic

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
Environment=HOME=/root PYTHONUNBUFFERED=1$( [ -s "$CONF/chrome_path" ] && printf ' GOLDDESK_CHROME=%s' "$(cat "$CONF/chrome_path")")
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
  research "$KREPO"
  boom_kronos "$KREPO"
}
# Once, for the "Rebuild BOOM/CRASH" thread: does a Kronos vote improve the M1 BOOM / CRASH limit orders?
# A year of Dukascopy gold (not LiteFinance, which rate-limits this VPS). Nice 19, one core, resumable, changes
# no service. Results: /api/research/boom_kronos_*.txt (through the Mac's tunnel).
boom_kronos() {
  local P=$CONF/kronos_research/boom_kronos_progress.txt
  [ "$KRONOS" = off ] && return 0
  grep -q "All BOOM/CRASH M1" "$P" 2>/dev/null && return 0
  systemctl is-active -q boom-kronos-test && { echo "BOOM/CRASH Kronos test: $(cat "$P" 2>/dev/null || echo starting)"; return 0; }
  systemd-run -q --unit boom-kronos-test --collect -p Nice=19 -p CPUQuota=100% -p MemoryMax=2000M \
    -p WorkingDirectory="$APP/dashboard" --setenv=HOME=/root \
    /bin/sh vps/boom_kronos_test.sh "$PY" "$1" "$KRONOS" \
    && echo "BOOM/CRASH Kronos test started on the VPS in the background (low priority)." || true
}
# Once, for the "Improve Kronos accuracy" thread: how often is Kronos's direction right, and which settings help?
# Nice 19 with CPU and memory capped, resumable (each icon run restarts it if it stopped), changes no service.
# Progress and results: http://127.0.0.1:8765/api/research and /api/research/<file> (through the Mac's tunnel).
research() {
  local R=$CONF/kronos_research F=$APP/dashboard/kronos_research.py LOG=$CONF/kronos_research.log
  local URL=https://raw.githubusercontent.com/Emaddodin/Wall-Street-Nightmare/5435321a4f07e42197206c0676cde7a954783203/dashboard/kronos_research.py
  [ "$KRONOS" = off ] && return 0
  grep -q "All settings done" "$LOG" 2>/dev/null && return 0
  systemctl is-active -q kronos-research && { echo "Kronos accuracy test: $(cat "$R/progress.txt" 2>/dev/null || echo starting)"; return 0; }
  [ -s "$F" ] || curl -fsSo "$F" "$URL" || { echo "Kronos accuracy test: couldn't download it this time."; return 0; }
  systemd-run -q --unit kronos-research --collect -p Nice=19 -p CPUQuota=200% -p MemoryMax=2500M -p WorkingDirectory=$APP/dashboard --setenv=HOME=/root /bin/sh -c "exec /root/kronos/.venv/bin/python kronos_research.py collect --repo $1 --size base --days 60 --points 240 --threads 2 > $LOG 2>&1" \
    && echo "Kronos accuracy test started on the VPS in the background (low priority)." || true
}
# A browser for the broker page. cdn.playwright.dev is unreachable from this VPS, so in order:
# a Chrome already installed here, Playwright's Microsoft mirror, the normal download, the Chrome .deb
# lying in /root, any chrome binary on disk. Adds, never removes.
get_browser() {
  local found
  found=$(cd "$APP/dashboard" && "$PY" -c 'import litefinance as l; b = l.find_browsers(); print(b[0] if b else "")' 2>/dev/null || true)
  if [ -n "$found" ]; then
    echo "Gold Desk will use the browser already on the VPS: $found"
    printf '%s\n' "$found" > "$CONF/chrome_path"
    touch "$CONF/.chromium_ok"
    return 0
  fi
  echo "Downloading the browser for the broker page (once)..."
  if PLAYWRIGHT_DOWNLOAD_HOST=https://playwright.download.prss.microsoft.com timeout 900 \
       "$PY" -m playwright install chromium chromium-headless-shell \
     || timeout 300 "$PY" -m playwright install chromium chromium-headless-shell; then
    timeout 600 "$PY" -m playwright install-deps chromium >/dev/null 2>&1 || true
    touch "$CONF/.chromium_ok"
    return 0
  fi
  echo "The browser download failed. Looking for a Chrome already on the VPS..."
  if [ -z "$found" ]; then
    local deb
    deb=$(ls -t /root/*chrome*.deb /root/*/*chrome*.deb 2>/dev/null | head -1)
    if [ -n "$deb" ] && DEBIAN_FRONTEND=noninteractive apt-get install -y -q "$deb" >/dev/null 2>&1; then
      echo "Installed Chrome from $deb."
      found=$(command -v google-chrome-stable || command -v google-chrome || true)
    fi
  fi
  if [ -z "$found" ]; then
    found=$(find /root /opt /usr/local -maxdepth 9 -type f -perm -u+x \( -name chrome -o -name headless_shell \
            -o -name chrome-headless-shell -o -name chromium \) 2>/dev/null | head -1)
  fi
  if [ -n "$found" ]; then
    echo "Gold Desk will use the browser at $found."
    printf '%s\n' "$found" > "$CONF/chrome_path"
    touch "$CONF/.chromium_ok"
  else
    echo "No Chrome found on the VPS. Gold Desk's broker page can't start until one is installed."
  fi
}

# "Setup likely soon" pushes (soon.py) go to the ntfy topic your trading bots already use (NTFY_TOPIC in
# /root/ict_sniper/.env, never the shared ones). Only if there is none, Gold Desk makes a private topic of its own.
alerts_topic() {
  if [ ! -s "$CONF/ntfy_topic" ] && ! grep -qE '^NTFY_TOPIC=["'"'"']?[A-Za-z0-9_-]' /root/ict_sniper/.env 2>/dev/null; then
    printf 'golddesk-%s\n' "$(head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n')" > "$CONF/ntfy_topic"
    chmod 600 "$CONF/ntfy_topic"
    changed=1
  fi
  if [ -s "$CONF/ntfy_topic" ]; then
    echo "Phone alerts for gold setups: in the ntfy app, subscribe to $(cat "$CONF/ntfy_topic")"
  else
    echo "Phone alerts for gold setups go to the same ntfy topic as your trading bots."
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
