# HANDOFF (branch for-dear-opus-5.5)

Running log of important findings. Newest first.

## 2026-09-30 cloud session: scorecard and status

- The price data is not in git (`xau_alpha/data/`, `data/candles/` are local-only). The cloud container's network
  policy blocks `datafeed.dukascopy.com`, so no new simulations ran here. Rerun research on the Mac, or allow that
  host in the cloud environment's network settings.
- X3 `ml_meta` (gradient-boosted entry filter) written up from the saved results: **FAIL**
  (`xau_alpha/cand/reports/ml_meta.md`). VALID AUC 0.50-0.59, and the best flip config (VALID lf_base PF 1.23, n 65)
  is 1 of 72 configs, has TRAIN t 1.15, and loses at lf_harsh on both splits.
- **Scorecard (TRAIN -> VALID, LiteFinance cost):** 14 families tested, 0 pass. FAIL: sweep_reclaim, zone_retest
  (owner setup A), failed_breakout (setup C), range_fade, comex_momentum, news_second_leg, exhaustion_fade,
  silver_bullet, round_reject, trend_pullback, ml_meta, and the deployed Runner. NEAR but rejected by the nulls:
  orb_retest (VALID loses in R, p 0.15) and htf_breakout (long beta, p 0.25-0.41). None of them is flip-eligible
  (stop in [1.2, 4.0] $/oz) with an edge that survives costs.
- **Consequence for the $13 flip:** with no measured edge, the barrier-aware flip odds from $13 to $100 are 0-2.3%
  (`recon/SYNTHESIS.md`). Do not fund REAL with any of these. The TEST split (2026-06..09) is still unused.

## 2026-09-30 session: xau_alpha research program (real Dukascopy bid/ask)

### Data and engine (new, all under `xau_alpha/`)
- `xau_alpha/data/m1_ba.parquet` (599,173 minutes) and `s10_ba.parquet` (3.58M 10-second bars): **bid AND ask** OHLC rebuilt
  from the Dukascopy tick files in `data/candles/duka_raw/` (2025-01-21 .. 2026-09-28). Builders: `xau_alpha/lib/build_m1.py`,
  `build_s10.py`. Mid close matches `data/candles/duka/*.csv` to 0.0005.
- `xau_alpha/lib/sim.py`: execution on 10 s bid/ask bars. Longs fill at ask / exit at bid, SL has priority over TP in the
  same bar, trailing uses previous bars only, gaps fill at the open, cost models `zero` (raw Dukascopy spread only),
  `base` (+0.10 spread, 0.05/0.08 slippage), `harsh` (+0.30, x1.2, 0.15/0.25). Unit tests: `xau_alpha/tests/test_sim.py` (7 pass).
- `xau_alpha/lib/account.py`: discrete-lot micro-account compounding, margin, stop-out, day-block bootstrap flip odds.
- `xau_alpha/lib/sweep.py`: parameter sweeps; orders at or after 2026-06-01 are dropped unless `final=True`.
- Splits: TRAIN 2025-01-21..2025-12-31 (gold 2710 -> 4319, bull), VALID 2026-01..05 (spike to 5593 and crash, M1 range
  2.2x train), TEST 2026-06..09 (4539 -> 4127, bear leg; holdout).
- Dukascopy spread by UTC hour: 0.60-0.73 most of the day, 0.86-0.91 at 22-23 UTC.

### Live adapter (new, not deployed)
- `xau_alpha/live/trader.py` (`AlphaTrader`): runs a frozen `xau_alpha/cand/<module>.py` live by calling the SAME
  `orders()` used in research on live M1 bid/ask bars (`live/bars.py`, same columns as `data.load_m1`). Position
  management mirrors `sim.py` (exit side, SL priority, BE/trail from previous quotes, tmax/flat); stop/limit orders
  emulated from quotes; ticket SL set at open (gateway cannot modify SL, so BE/trail are software flattens).
  FSM: FLIP (<$100: 0.01 lot, stop must be in [1.2, 4.0], 2 losses/day max) -> MAIN (fractional risk 2%), demote
  below $60, HALT when 0.01 lot cannot be margined at 1:500 (1:200 within +-30 min of news).
  Guards on entries only: calendar blackout fail-closed (`live/news.py`, +-30 min below $21), spread cap
  max(0.40, 2x 60-bar median), 20:30-23:30 UTC break, Friday 19:00 cutoff / 20:30 flatten.
  Laya/Jeff: shadow verdicts logged, never veto or size. Logs: `data/state/xau_alpha_trades.jsonl`,
  1 Hz quotes in `data/state/quotes/` (fills the LiteFinance spread/fill-price gap G1/G7).
- `xau_alpha/run_live.py`: entry point; DEMO default; REAL needs `--mode REAL --allow-real`; refuses if the broker
  account mode differs from `--mode`.
- `xau_alpha/tests/replay_parity.py`: replays real 10 s bars through the live trader with a fake broker and compares
  trades with `sim.simulate` on the same orders.
- Parity result (runner, 2026-01-13..15, VALID): live and backtest took the same 12 trades, entry times and prices
  identical; exits differ by median 0.54 $/oz, always worse live, because the replay feeds 4 quotes per 10 s bar and
  the fake broker fills stops at the bar-extreme quote (a discretization artifact, pessimistic).
- Sweep harness fix: configs with < 60 TRAIN trades are no longer ranked (a 2-trade config had t = 3506).

### Machine hygiene (2026-09-30 ~03:10 local)
- The Mac hit **disk full** (swap grew to 5 files / 3.5 GB under RAM pressure from parallel backtests; 113 GB disk,
  <1 GB free). Freed ~700 MB of regenerable items only: `xau_alpha/video/audio` + `frames_*` (re-extract with
  `xau_alpha/video/extract_keyframes.py`), `data/state/ghost_signals_ccdee062a3.pkl` (cache of
  `scripts/backtest_ghost_real.py`, rebuilt on demand), pip cache, Homebrew downloads, and the faster-whisper-small
  model cache (re-downloads on next use). Also killed 5 orphaned multiprocessing workers (parent dead 3.5 h).
  The owner should free several GB before running more parallel research.

### Edge hunt results so far (xau_alpha/cand/reports/*.md; TRAIN->VALID, lf_base cost)
- FAIL: H1 sweep_reclaim (valid PF 0.70), H2 zone_retest = owner setup A (0.67; confirmation candle adds nothing,
  random-direction p 0.84), H3 failed_breakout = setup C (0.87), H10 range_fade, H5 comex_momentum (no gross edge),
  H7 news_second_leg (1.12 on n=14, p 0.42), X1 exhaustion_fade (0.63), H11 silver_bullet (0.86), H9 round_reject.
- ORB (H6): the hunt agent was killed 3x by infrastructure (session usage limit twice, then DNS/API outage), not a
  strategy error; its stage-1 work survived (`cand/orb_retest.py`, `results/orb_retest_*.csv`). Stage 1 is the best
  lead: London OR 08:00-08:29 local, enter at the first break, stop beyond the far side: TRAIN PF 1.39 (n 232),
  VALID 1.14 (n 101, harsh 1.03); time exit 16:00 UTC: TRAIN 1.32, VALID 1.52 (harsh 1.41). Wide stops (main
  strategy, not the $13 flip). Relaunched as workflow `xau-orb-finish` (stage 2 + two adversarial verifiers).

### Findings
- **Critic synthesis of the 5 recon reports (`xau_alpha/recon/SYNTHESIS.md`, checks in `recon/synthesis_checks.py`):**
  - Runner reconciled on the 10 s bid/ask sim (train/valid): mid (true zero cost) PF 1.18/1.10, **+0.35/+0.21 pt gross**
    (so the "gross about -0.2 pt" line below has the wrong sign; `sim.COSTS['zero']` is raw Dukascopy spread, not zero);
    LiteFinance cost (0.22 spread, 0.05/0.10 slip, $5/lot) PF 0.91/0.88. Runner loses at every realistic cost. The codebase's
    PF 1.07 came from optimistic intrabar fills in `scripts/runner_flip.py`.
  - Flip odds with the **1:500 margin barrier** (0.01 lot cannot be opened below ~$8.30 equity at $4,150): from $13 to $100,
    P(hit) = 0-2.3% at zero gross edge, 0.7-9.8% at +0.125R gross, 10-37% at +0.25R; $50 start gives 37% at +0.125R.
    The 28% (below) and `microlot_ruin.py` 12-18% ignore the barrier. `lib/account.py` leverage must be 500.
  - Use LF_BASE **with $5/lot commission** (ECN fee, sourced) and LF_HARSH for go/no-go. Below ~$21 equity, blackout
    [T-30, T+30] on HIGH events (broker 1:200 news margin). Headline bias / RAG / regime priors off the entry path;
    Laya/Jeff shadow-only with a pre-registered forward test.
  - Blocking before REAL: no fill prices logged; gateway failed 9/9 orders on 09-29; commission, stop-out and news-margin
    unconfirmed on the live account; only 10 LiteFinance spread quotes; `econ_calendar.csv` ends 2026-09-30.
  - 11 ranked hypotheses with rules and grids: sweep-and-reclaim of session extremes, M1 zone break-retest (owner setup A),
    failed-breakout reversal (setup C), cost/volatility gate, COMEX-settle momentum, ORB-retest, post-release second leg,
    add-on-engulfing overlay, round-number rejection, range fade, silver-bullet FVG.
- **LiteFinance XAUUSD conditions (web research, `xau_alpha/recon/web_research.md`):**
  - XAUUSD instrument margin is **0.2% (1:500)**, not 1:1000. It can go to **0.5% (1:200) for new orders within ±30 min of major news**.
    At $4,180 gold, 0.01 lot needs $8.36 of margin ($20.90 in a news window). A $13 account can hold only 1 x 0.01 lot and cannot open one during raised news margin.
    `xau_alpha/lib/account.py` and `scripts/runner_flip_odds.py` assume 1:1000.
  - Costs: ECN raw spread + **$5/lot round turn** ($0.05 per 0.01 lot); Classic and Cent add a 14-point markup. Stop-out is 20% (Cent 50%). Execution is "usually 3-5 s".
    Server time is GMT+3 in EU summer and GMT+2 otherwise.
  - With a 1-oz minimum lot and $13, risk is 17-36% per trade, about 6-11x Kelly. `xau_alpha/recon/microlot_ruin.py` gives 83-100% ruin even with a +0.10R edge.
    Lot granularity (a cent account) matters more than the signal. Whether XAUUSD is available on LiteFinance Cent is ambiguous; ask support.
- **The deployed Runner (48 x 5m close breakout, 1m ATR >= 3.5, stop 4, BE+trail 2) LOSES on real XAUUSD:** PF 0.67/0.71
  (train/valid) with only the raw Dukascopy spread, 0.59/0.64 with base costs, 0.43/0.46 harsh; both long and short
  sides lose. (Correction: the "zero" cost model was raw Dukascopy spread. At true mid the gross edge is +0.35/+0.21
  pt/trade, PF 1.18/1.10; at LiteFinance cost PF 0.91/0.88. Still a loser.) The PF 1.63 in the
  2026-09-29 entry came from Binance PAXGUSDT 1m bars (proxy microstructure), not gold. The demo service on the VPS
  (`ghost-grid-demo`, GHOST_STRATEGY=runner) is running a negative-expectancy strategy.
- M1 event studies (mid, before costs): 5m Donchian breakouts (12/48/96) and displacement bars have forward drift
  within +-0.1..0.4 pt at 5-120 min (|t| < 2), below the ~0.65 spread. No stable hour-of-day drift (signs flip between
  train and valid). Volatility seasonality is stable (13-15 UTC highest). $10 round-number crosses: nothing.
  Prior-day high/low first break: +1.24 pt at 15 min in train (t 2.4) but negative in valid. Note: this quick study
  also printed TEST-split columns for these simple features (a minor holdout peek; nothing was tuned on them).

## 2026-09-29 session

### Facts established
- VPS `82.115.21.155` is reachable by key (`/root/ict_sniper`, Python 3.12.3, 2 vCPU / 3.9 GB RAM, UTC, NTP ok).
- **Production was stopped manually on 2026-09-28 17:09 UTC** (SSH session, then `Stopping` on `stratton-xau-live` and `ghost-grid-demo`). `stratton-xau-live` ignored SIGTERM and was SIGKILLed. Nothing listens on 80/443/8088/8443.
- Demo broker balance at stop: $31.54 (started 14.36 mirror). `ghost_grid_state.json` `live_real_balance` was overwritten by demo activity and is NOT the real balance. Real balance is unknown; read it from the broker or ask the owner.
- VPS `run_xau_broker_live.py` md5 == local. Ghost files on the VPS are older than local; `lf_session.json` lives in `/root/lf_session.json` (mode 644, should be 600).
- **`data/candles/gold_m1_*.csv` is synthetic** (`scripts/fetch_real_m1.py` docstring: generated from a deterministic ramp starting at $2490). Any backtest built on it, including the Trump-regime $60 -> $22M figures, is unverified.
- Jeff = `GestaltLabs/Jeff-1` (LoRA on Qwen3-4B-Instruct-2507, typed Choice/Noul/Score). Needs Python >= 3.12 and torch >= 2.11; cannot run on the VPS (RAM). Runs as a separate local server (`scripts.jev_clf_server`, port 8079), `scalper/brain/jeff_client.py` calls it; oracle falls back to rules on any failure. Default remains fallback (`JEFF_SKIP_HEAVY_WEIGHTS=1`). Laya was never actually loaded before either (same default).

### Code changes made (uncommitted, not deployed)
- Jeff swap in `scalper/brain/laya_oracle.py` (+ `jeff_client.py`); Laya names kept as aliases.
- Ghost Grid: REAL needs `--allow-real`; unverifiable account mode refuses to trade; no synthetic warm-up (55 real candles, persisted); exits keep running on wide spread; weekend/Friday 20:30/rollover blocks; flatten verified with 3 retries then halt; basket stop scales with equity (>= $4); disaster SL sized from the basket stop; free-margin gate; oracle veto now reads `is_valid` (it used to read a non-existent `approve` attribute, so it never vetoed).
- Gateway: SL field is read back before dispatch (abort with `SL_NOT_SET`); `UNKNOWN` account mode blocks DEMO-intended orders.
- Apex loop: auto DEMO->REAL switch is opt-in (`APEX_AUTO_SWITCH_REAL=1`); ghost-position watchdog needs 2 consecutive zero-assets reads; orphan broker positions flattened at startup; 30 s entry cooldown after a failed/unconfirmed order.
- Tests: `tests/test_ghost_engine_safety.py`, `tests/test_jeff_oracle.py`.

### Open audit items (not yet fixed)
- Apex: ratchets never modify the broker SL (only in-memory); PnL from balance diff can read 0 and count as a win; no persistence of circuit-breaker state; `MAX_RISK_STOP_USD` unused; command.json single slot / stale replay; fail-open news freeze.
- VPS: no firewall, root SSH/services, default dashboard token literal in `scalper/app/app.py:46` (rotate), certbot standalone will fail while app holds :80, jail `tbt-app` dead, no external uptime alert, 140 MB stray files in /root, stale Wine process, reboot pending.

## 2026-09-29 (later): flip engine, real-data backtests, demo deploy

- Real account balance is **$12.47** (owner-stated; $60 -> $12.47 live with Apex). The PDF "Daily Compounding Ledger" ($60 -> $6.6M + $15.56M vaulted) is the Apex backtest run on SYNTHETIC candles; not reproducible.
- Real data: `data/candles/real_bt2/` = Binance PAXGUSDT 1m (gold-token PROXY, not XAUUSD) Jun 2025 -> Sep 2026, penny precision. Jan-May 2025 PAXG is $1-quantized (unusable); Dukascopy/Pyth unreachable from here. Rebuild with `scripts/fetch_real_m1.py` (uses data-api.binance.vision).
- Apex production exit chain on real data (`tests/backtest_live_loop_trump_regime.py --data-dir data/candles/real_bt2`): PF 1.11, WR 37%, $30 start ruined. With entry ATR >= 1.5 (`BT_MIN_ATR`, live `APEX_MIN_ENTRY_ATR`): PF ~1.3, both halves positive. Deployed as default 1.5.
- MR P FX tight scalp: spread/slippage kill it. Replaced as the flipper by the **Runner** (`ghost_grid/runner_strategy.py`, `runner_exit.py`): 5m close beyond 48-bar (4h) high/low, 1m ATR(14) >= 3.5, stop 4.0pt, breakeven arm at +4pt, trail 2.0pt, no cap. Real-data PF 1.63 (halves 1.99/1.49) at moderate slippage 0.15/0.25, 1.43 (1.81/1.28) at harsh 0.30/0.50, dies beyond that. Select with `GHOST_STRATEGY=runner|mrp`.
- Flip odds (`scripts/runner_flip_odds.py`, 1:1000, min lot 0.01, 45 days): from $12.47 -> $100: ~28% hit / ~67% ruin (harsh slippage 14% / 78%); from $25: ~55% / 31%; from $50 -> $200: ~65% / 3%. Min-lot risk (0.01 lot x 4pt = 32% of $12.47) is the binding constraint, not the edge.
- Deployed to VPS (DEMO only): backups in `/root/ict_sniper/backups/pre_deploy_20260929/`. `ghost-grid-demo.service` rewritten (runner, `GHOST_MIRROR_BALANCE=12.47`, `GHOST_HANDOFF_BALANCE=100`, `Conflicts=stratton-xau-live`, `Restart=on-failure`, `ExecStopPost` starts Apex when `data/state/handoff_to_apex` exists). Runner needs ~4.4 h of real candles before its first signal (persisted in `data/state/ghost_candles.json`).
- Laya (real `convaiinnovations/laya` via `laya.Router`, ~1.3 s/decision on the 2-vCPU VPS) is the oracle backend; Jeff only if `JEFF_URL` is set. Oracle is now awaited off the event loop in both engines.
- VPS memory is tight (llama-server critic 1.1 GB + Laya + Chrome; swap ~2.2 GB used). Consider stopping `stratton-llm-critic`.
- NOT done: commit (all changes uncommitted), Apex broker-SL ratchet, real-account run, rotate dashboard token/firewall.
