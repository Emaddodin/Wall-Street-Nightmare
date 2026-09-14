# ICT Sniper -- new server (82.115.21.155)

Old server 62.60.198.135:2222 is DEAD (unreachable, 2026-09-13). `ssh stratton`
now points here.

## Layout
    /root/ict_sniper/live_hyperliquid.py   the bot (phase-7 spec)
    /root/ict_sniper/.env                  NTFY_TOPIC, SCALPER_*, HL_ADDRESS
    /root/ict_sniper/venv                  python 3.12 + hyperliquid sdk
    /root/ict_sniper/scalper/app/app.py    the phone app
    /root/ict_sniper/data/state/paper.json app state the bot publishes
    /root/ict_sniper/data/state/live.json  live prices for open positions
    /root/ict_sniper/data/logs/hl_trades.jsonl  every leg the bot closes
    /root/ict_sniper/tls/                  self-signed cert (CN=82.115.21.155.nip.io)
    /root/ict_sniper/bot_execution.log     rotating bot log

## Services
    systemctl status stratton-oakmont-hl-sniper    # the paper book
    systemctl status stratton-oakmont-hl-app       # phone app on :8443
    journalctl -u stratton-oakmont-hl-sniper -f

## App
    https://82.115.21.155:8443   (self-signed cert -> accept the warning)
    password = SCALPER_APP_TOKEN in /root/ict_sniper/.env

## Notes
    * Paper mode: nothing is signed; fills are simulated from closed 5m
      candles with the same rules as the backtest.
    * Face ID needs a trusted cert + a domain whose RP_ID matches; the
      self-signed IP cert is password-login only.
    * VIP cost model in the paper PnL: maker in 0, maker out 1.5,
      taker 3.0, stop slip 1.5 bps.

## Safety -- the bot cannot touch real money
Four independent locks, all verified on 2026-09-13:
  1. code: cfg.live is False unless --live AND --secret-key AND
     ICT_LIVE_ACK=I_UNDERSTAND_REAL_MONEY are ALL present.
  2. service: the systemd unit sets Environment=ICT_PAPER_ONLY=1, and
     /root/ict_sniper/PAPER_ONLY exists -> live refused with exit 3.
  3. no key: .env holds no exchange secret (0 matches for
     SECRET/PRIVATE/PASSWORD); the bot reads only NTFY_TOPIC,
     NTFY_TOPIC_SHARED, SCALPER_DATA, HL_ADDRESS from .env and logs
     every other key it ignores.
  4. runtime: paper mode raises if it ever holds an Exchange client, and
     _make_place_fn() refuses to build an order placer outside live mode.
     Unit test: 0 Exchange constructions and 0 order() calls in paper.
Fills in paper mode are simulated from closed 5m candles with the exact
backtest rules; nothing is ever signed or sent.

## Monitoring -- guard + health checker (same pattern as the old server)
    stratton-oakmont-hl-watchdog.timer   every 5 min  -> hl_watchdog.py   (repairs + ntfy)
    stratton-oakmont-hl-health.timer     every 10 min -> hl_healthcheck.py --notify
Watchdog checks: both services up, book state finite, PAPER LOCK intact,
no signing key in the bot env, no --live flags in the unit, book freshness
(10 min) with ONE self-heal restart per hour, journal errors, app HTTP on
8443 (self-heals the app), disk/RAM/bot-RSS, live-price freshness with an
open position, Hyperliquid API reachable, fail2ban up.
Alarms are deduplicated: the same fault re-alarms every 15 minutes.
Healthcheck prints ok/FAULT lines, exits 1 on any fault, and notifies on
change (at most every 30 min while a fault persists, plus one recovery
message). Manual runs:
    cd /root/ict_sniper && set -a && . ./.env && set +a
    ./venv/bin/python hl_healthcheck.py          # report + exit code
    ./venv/bin/python hl_watchdog.py --test      # one test alarm

## Security
    fail2ban 1.0.2: jails sshd + stratton-app (app logins: 8/10min -> 2h ban)
    sshd: key-only (passwordauthentication no), root via key
