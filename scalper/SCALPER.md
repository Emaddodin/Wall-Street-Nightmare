# SCALPER — Volume Profile + Market Structure edition

The isolated scalper project.  Production bot files were never modified;
its services were STOPPED (not deleted).  Old app infra (chrome/panel/vnc/
wm/xvfb) still runs so the old app opens.

## What is running on the VPS

| unit | what |
|---|---|
| `stratton-oakmont-paper` | the VP scalper paper book on live Bitunix candles -- TESLA IS GONE, pure price action + volume profile, 25% risk, $100 start, daily +100% target |
| `stratton-oakmont-app` | phone app, https://82.115.21.155:8443 (own password, own port) |

The app headline: day PnL vs the +100% target, green/red, "TARGET HIT"
banner when the day is banked, and the compounding goal curve.

## The strategy (the operator's system prompt, implemented)

1. **Bias (15m)**: HH/HL bullish, LH/LL bearish.  When the swing leg is
   oversized (> 8x ATR15) the INTERNAL structure (last 15m ChoCH) dictates
   the intraday bias instead.
2. **Location (volume profile)**: profile over the 15m swing range
   (60 bins, trailing 24h of 15m bars); VAH / POC / VAL = the 70% value
   area.  London-session filter: Asia high/low must be swept first.
3. **Confirmation (1m)**: the bar touches a VP level AND the 1m structure
   shifts (ChoCH = close through the recent 1m swing in the bias
   direction).
4. **Entry models**: fresh 1m order block / unmitigated 1m FVG / the
   micro-POC of the current 1m leg (any one).
5. **Stop**: beyond the recent 1m swing + 0.2 x ATR1m buffer.
6. **Targets (model D)**: 50% at the nearest opposite-side 15m swing,
   50% runner trailing 1m swings, exits on an opposite 1m BOS; BE after
   the first fill.

## The daily-target regime (the operator's KPIs)

* Risk per trade: **25% of equity**.  Leverage cap 50x (the stop always
  exits long before liquidation).
* **Daily target: +100% of day-start equity.  The moment it is reached
  the book HALTS until the next session.**
* -50% daily loss floor, 2 losses in a row halt, 30-min cooldown after any
  loss, max 10 trades/day.
* Session started fresh at $100 on 2026-09-08 13:32 UTC (old state
  archived in data/state/archive/).

## Entry points are model-priced, not chased (2026-09-08)

The entry MODEL now sets the entry POINT.  After the 1m ChoCH confirms at a
VP level, the VP strategy rests a LIMIT at the model's own level instead
of market-chasing the next open:

  * order block -> the block's mean threshold (O+C)/2
  * FVG         -> the gap midpoint (CE)
  * micro-POC   -> the 1m leg's POC

Rules: the limit rests only when the level sits within 0.5% of the
trigger close (farther = market at next open), fills only when the bar
range actually touches it, and expires after 4 1m bars without a fill --
a missed entry is cheaper than a chased one.  Breakout/turtle entries
remain market-at-next-open by design (the break IS the trigger).  Pinned
by tests (test_vp_strategy).

## The strategy farm + the eagle (the computer advantage)

A human runs one playbook; the machine runs a FARM.  The eagle (the
market-wide scan: top-60 candidates by volume/ATR/spread, re-ranked every
5 minutes, every strategy evaluated on every closed 1m bar) feeds three
independent deterministic strategies:

  * `vp`       -- the operator's VP + structure scalper (bias -> VP level
                  -> 1m ChoCH -> OB/FVG/micro-POC).
  * `breakout` -- stolgo consolidation breakout: 12h prior range <= 10%
                  wide, close beyond the range; stop = the far range edge.
  * `turtle`   -- ICT turtle-soup fade: 0.6-wick sweep of an EQH/EQL pool
                  + close back inside; stop beyond the swept extreme.

One book, one risk engine, one daily +100% target: the first strategy to
fire on a bar wins (config order), every trade carries its `strategy`
tag, the app shows it, metrics break down PnL by strategy, and the pacing
is capped at 5 trades/day (the operator's "1-5 correct trades").

130-day backtest of the farm on real Bitunix data (fees+slippage, 25%
risk): 46 positions -- vp n=66 pnl -63.75, breakout n=19 pnl -10.37,
turtle n=6 pnl -0.23 -- recorded honestly in data/reports/.  The farm
still has no positive expectancy on this window; the live session
measures what the real market does with it.

## What was learned from the three libraries (pa/)

* stolgo: prior-window levels, consolidation/breakout, candle patterns.
* ict-knowledge-library: FVG + CE, order blocks + MT, displacement,
  CHoCH/MSS, EQH/EQL pools, turtle soup, killzones, Asian range.
* motivewave: all 33 candlestick patterns (exact ratios).
All reimplemented vectorized + causal in pa/ (numpy fast paths), pinned
by tests.  The 130-day backtest of this exact strategy on real Bitunix
data (fees+slippage included): 62 positions, 123 lots, net -67.5, WR 17%,
PF 0.28 -- recorded honestly in data/reports/; the daily target was not
reached in that window.  The live book measures what happens next.

## Operating

```bash
# VPS
journalctl -u stratton-oakmont-paper -f        # the book
systemctl restart stratton-oakmont-paper       # restart (state survives)
curl -d test -H "Title: scalper" https://ntfy.sh/$TOPIC   # test push

# Mac
cd /Users/mac/Desktop/Stratton Oakmont/scalper
python3 tools/backtest.py --days 130 --profile aggressive
python3 tools/compare.py --days 130
python3 tools/walkforward.py --profile aggressive
python3 -m pytest tests/ -q
```

State: data/state/paper.json.  Logs: data/logs/{trades,rejections,runs,
research,fills}.jsonl.  ntfy pushes on every fill and close.

## Lot-persistence bug fixed (2026-09-08)

Closed lots were serialized to paper.json without their exit fields, so a
restart resurrected them as OPEN lots (a closed runner re-exited, double
PnL) and unbanked `realized_pnl` of partially-closed positions was lost.
Fix: `_pos_to_dict` now persists `exit_px/exit_reason/exit_ms/pnl` per lot
plus `realized_pnl`; `_apply_state` restores both, keeping closed lots
closed. Regression test:
`tests/test_risk_position.py::test_position_roundtrip_keeps_closed_lots_closed`.
The corrupted 2026-09-08 SOPHUSDT state was repaired on the VPS (pre-fix
"first" lot + resurrected runner → one trailing runner, per model-D
breakout semantics); the original is archived in data/state/archive/.

## App fixes (2026-09-08, round 6)

1. **Login hang (root cause).** The `/login` 302 had no Content-Length and no
   `Connection: close`, so with HTTP/1.1 the server kept the socket open
   waiting for a next request while the client waited for EOF — logins
   stalled 15-60s (the "stuck" feeling on the phone). Fix: the handler now
   sets `close_connection = True` for every response and the 302 carries
   `Content-Length: 0` + `Connection: close`. Login now completes in ~16ms.
2. **NaN in the dashboard payload.** Pre-guard-era turtle_soup records with
   NaN qty/pnl were emitted as `NaN` literals, which makes the browser's
   `JSON.parse` throw and silently breaks every dashboard refresh.
   Fixes: `_state_payload` sanitizes every trade pnl to a finite float
   (NaN → 0.0); `metrics.agg` fills NaN pnl with 0.0 and ignores NaN
   `pnl_r`. History repaired in `trades.jsonl` (NaN qty/pnl → 0.0, marked
   `(nan-repaired)`; TESLA-era records backfilled `strategy="tesla"`).
   Original file archived in data/state/archive/.

## Security hardening (2026-09-08, post key-compromise)

- SSH key rotated after an unauthorized login used the old `stratton-vps` key;
  the new `stratton-vps-v2` key is the only authorized key. Old key deleted.
- SSH moved to port **2222** (socket-activated via ssh.socket override;
  22 removed); UFW now allows only 22→(removed) 80/443/2222 — the old
  8443 panel rule was dropped too.
- sshd hardened (99-stratton.conf): MaxAuthTries 4, LoginGraceTime 45,
  MaxStartups 3:50:10, MaxSessions 8, no X11/agent forwarding, local
  TCP forwarding only, AllowUsers root, LogLevel VERBOSE.
- fail2ban active: sshd jail, 3 fails/10 min → 1h ban, port 2222,
  systemd backend watching ssh.service.
- Interactive SSH logins push an ntfy alert (via /root/.ssh/rc, only
  when a tty is allocated — machine sweeps stay silent).
- unattended-upgrades: security updates automatic, no auto-reboot.
- App: /login rate-limited to 8 failed passwords / 10 min per IP (429).

## Stale-replay phantom fills fixed (2026-09-09)

The feed rotates symbols; when a symbol re-entered after hours away, the
backlog loop replayed its old bars (including PRIME bars), emitted signals
on them (the session gate checks the BAR's time, not the fill time), and
filled those signals at TODAY's price.  Result: phantom entries outside
PRIME (SOLUSDT 19:37, ARBUSDT 00:00, SUIUSDT 05:11 UTC) and polluted
ledger.  Fixes:
- `paper.max_signal_age_ms: 300000` — bars older than 5 min update
  indicators but never emit signals (`_stale_signal` guard in paper_trader).
- `paper.max_fill_age_bars: 3` — market fills only within 3 bars of the
  signal bar close (limits already expired).
- Phantom records are tagged `phantom: true` in the logs and excluded from
  app stats and metrics reports (`summarize`/`breakdowns`).
- The ledger was reset to a clean $100 (archives in data/state/archive/).
- Also deployed the previously-undepoloyed fee-viability filter
  (`max_fee_r`, maker-fee handling) — VPS files were behind the Mac.

## Entry filters loosened (2026-09-09, operator decision)

The book went through an entire PRIME day with 0 fills while offline
replays showed setups; the over-tight gates were killing candidates
silently.  Loosened:
- `execution.max_fee_r` 0.20 -> 2.0 (only stops tighter than ~0.08% of
  price are refused now; 96% of replay signals pass)
- `strategy.vp_touch_tol_pct` 5 -> 10 bps, `vp_touch_window_bars` 5 -> 12
- `research.log_reject_min_legs` 4 -> 2 so rejections are visible in the
  log again (we were flying blind)
Session map (PRIME only), 25% risk, +100% daily target unchanged.

## 24h trading (2026-09-09, operator decision)

`strategy.session.trade_windows` changed from `[prime]` to `[any]` -- the
book now evaluates and may enter at every session, all 24 hours.  The
Asia-sweep filter still gates the London open (no entry until the Asia
high/low is swept), the stale-replay guard still protects fills, and the
daily regime (+100% target halt, -50% loss limit, 25% risk) is unchanged.

## Daily target halt OFF (2026-09-09, operator decision)

After TACUSDT took the day +112.98% (one position, both lots in profit),
the operator turned the daily +100% halt OFF: the book keeps trading as
long as the calls are good.  Kept: the -50% daily loss limit, the
consecutive-loss halt/cooldown, and max 5 trades/day (no overtrading).
`daily.target_pct` = 0.0 disables the halt; the app shows
"no daily cap · let it run".

## Fill-through guard (2026-09-09)

AKEUSDT LONG (FVG limit) was tagged by a bar that also crashed through the
stop -- filled and stopped in the same bar, -$57.92 in 3 seconds.  The
fill path now refuses any limit whose tagging bar already traded through
the stop (`_fill_through`: LONG bar low < sl, SHORT bar high > sl) --
knife-catches are not good calls.

## Watchdog alarm bot (2026-09-09)

`watchdog.py` + `stratton-oakmont-watchdog.{service,timer}` run every 5 minutes
(all day, Persistent across reboots) and push an ntfy "scalper-alarm"
(high priority) when anything breaks: services down, non-finite paper
state, engine errors in the journal, app unreachable, disk >=90%, RAM
<200MB, stale candles or stale live-price feed.  Alarms dedupe per check
and repeat every 15 min while the fault persists.  `watchdog.py --test`
sends a test push.

Also on 2026-09-09 (operator): Tehran time (UTC+3:30) is the reporting
timezone; the AKE knife-catch loss was excluded from the ledger
(phantom-tagged), equity restored to $212.98, and the daily +100% target
halt was REINSTATED (day clock restarts at 212.98 -> today's target
$425.96).

## Day boundary = 03:30 Tehran (operator rule, 2026-09-09)

The trading day runs 03:30 Tehran time to the next 03:30 Tehran time
(00:00 UTC, `paper.day_start_hour_utc: 0`).  At rollover the day clock
re-arms: day-start equity = whatever equity the book holds at that moment
(e.g. after the MARSCOIN trade finishes), trades/loss streaks/cooldowns
reset, and the +100% daily target = double that new base.

## Self-teaching layer (2026-09-10)

The bot now learns from every trade it has ever made:
- `lessons.py` keeps a lesson journal (`data/state/lessons.jsonl`) -- one
  row per closed trade with strategy/model/exit/R/hold/knife-catch flag.
  The full history was backfilled (19 lessons at build time).
- Autopilot re-evaluates every ~30s: a strategy OR entry model with 3
  straight losses (or <= -0.5R avg over its last 5) is auto-paused for
  2 hours and re-armed afterwards.  Every pause/resume pushes an ntfy.
- At each day rollover (03:30 Tehran) the bot pushes a "daily lesson"
  ntfy: yesterday's W/L, pnl and per-strategy breakdown.

## ROOT-CAUSE: incremental bar loop was stuck (fixed 2026-09-10)

The paper trader only evaluated bars (a) right after a restart and (b)
when a brand-new symbol first entered the feed.  Cause: the loop wrote
`last_i = n_t - 1` while its range's upper bound was `n_t - 1` exclusive,
so after the first pass the range was ALWAYS empty -- every new bar was
silently skipped.  That is why fills clustered around restarts and the
book then went silent for hours (the "no trades during PRIME" mystery).
Fix: `last_i` now stores the CLOSE TIME of the last processed bar and the
loop uses `np.searchsorted` on close times, which works for both growing
and sliding tail windows.  Verified: rejections.jsonl now advances every
minute (17:09 -> 17:10 -> 17:11 UTC) with zero restarts.

## Never-again guards for the starvation bug (2026-09-10)

1. Bar-advance logic extracted into pure `advance_bars()` with four
   regression tests: first contact, growing store, no-new-bar, sliding
   window.  A new bar ALWAYS yields a non-empty evaluation range.
2. Watchdog now alarms ("engine:stuck") when BOTH rejections.jsonl and
   trades.jsonl are older than 15 minutes while candles are fresh --
   a silent evaluator is now a loud ntfy alarm within minutes.
3. Audited the other stateful loops (position stepping uses the last row
   directly, pending fills expire, feed cache has a fallback) -- no other
   incremental index bookkeeping remains in the live path.
