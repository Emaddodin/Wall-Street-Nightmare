#!/bin/bash
# Gold Desk one-time setup for macOS. Safe to run again; it deletes nothing outside ~/GoldDesk.
#   curl -fsSL https://raw.githubusercontent.com/Emaddodin/Wall-Street-Nightmare/refs/heads/claude/project-thread-ild2ro/dashboard/mac/install.sh | bash
main() {
  set -e
  local REPO=https://github.com/Emaddodin/Wall-Street-Nightmare.git
  local BRANCH=claude/project-thread-ild2ro
  local APP="$HOME/GoldDesk" CONF="$HOME/.golddesk"
  say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

  say "1/5  Getting Gold Desk into ~/GoldDesk"
  if ! xcode-select -p >/dev/null 2>&1; then
    xcode-select --install || true
    echo "Click Install in the Apple window that just opened. When it finishes, run this same command again."
    exit 1
  fi
  if [ -d "$APP/.git" ]; then
    git -C "$APP" fetch -q --depth 1 origin "$BRANCH"
    git -C "$APP" reset -q --hard FETCH_HEAD
  else
    git clone -q --depth 1 --branch "$BRANCH" --filter=blob:none --sparse "$REPO" "$APP"
    git -C "$APP" sparse-checkout set dashboard
  fi
  chmod +x "$APP/dashboard/mac/"*.sh

  say "2/5  Installing the LiteFinance browser driver"
  python3 -m pip install -q --user --upgrade playwright
  python3 -m playwright install chromium

  say "3/5  Installing Kronos (forecast model). This one is big, give it a few minutes."
  [ -d "$APP/Kronos/.git" ] || git clone -q --depth 1 https://github.com/shiyu-coder/Kronos.git "$APP/Kronos"
  mkdir -p "$CONF"
  if python3 -m pip install -q --user torch "einops==0.8.1" huggingface_hub safetensors pandas tqdm; then
    kronos=small
  else
    echo "Kronos could not be installed. Gold Desk will run without it."
    kronos=off
  fi
  [ -f "$CONF/config.sh" ] || printf 'ACCOUNT=demo      # demo or real: the badge on the page\nKRONOS=%s       # mini, small, base or off\n' "$kronos" > "$CONF/config.sh"

  say "4/5  LiteFinance login"
  if [ -f "$CONF/lf_session.json" ]; then
    echo "Already saved."
  else
    (cd "$APP/dashboard" && python3 server.py --litefinance-login)
  fi

  say "5/5  Putting \"Gold Desk\" on your Desktop"
  printf '#!/bin/bash\nexec "$HOME/GoldDesk/dashboard/mac/start.sh"\n' > "$HOME/Desktop/Gold Desk.command"
  chmod +x "$HOME/Desktop/Gold Desk.command"
  [ -d "$HOME/Desktop/GoldDesk" ] && echo "Your old ~/Desktop/GoldDesk folder isn't used any more. Delete it whenever you like."

  say "Done. From now on just double-click \"Gold Desk\" on your Desktop. It updates itself each time."
  exec "$APP/dashboard/mac/start.sh"
}
main "$@"
