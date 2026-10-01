# HANDOFF (branch for-dear-opus-5.5)

Running log of important findings. Newest first.

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
- 2026-09-30 17:50, with the owner's approval: deleted the 946 SYNTHETIC `data/candles/gold_m1_*.csv/json` files
  (949 MB, the fake data behind the $22M/$12M results; not tracked in git), 4 installer DMGs in ~/Downloads and the
  old Claude Code 2.1.280 folder. Real data in `data/candles/{duka,duka_raw,duka_bt,duka_bt_jun,real*}/` is intact.
  Old backtests in `tests/*.py` that default to `data/candles` must now be run with `--data-dir data/candles/duka_bt`.
  The retry workflow (ML meta-filter + htf/ORB verifiers) was stopped to stop swap growth; resume it with
  `Workflow({scriptPath: <session>/workflows/scripts/xau-retry-wf_263ac5ae-240.js, resumeFromRunId: "wf_263ac5ae-240"})`.

### Edge hunt results so far (xau_alpha/cand/reports/*.md; TRAIN->VALID, lf_base cost)
- FAIL: H1 sweep_reclaim (valid PF 0.70), H2 zone_retest = owner setup A (0.67; confirmation candle adds nothing,
  random-direction p 0.84), H3 failed_breakout = setup C (0.87), H10 range_fade, H5 comex_momentum (no gross edge),
  H7 news_second_leg (1.12 on n=14, p 0.42), X1 exhaustion_fade (0.63), H11 silver_bullet (0.86), H9 round_reject.
- ORB (H6): the hunt agent was killed 3x by infrastructure (session usage limit twice, then DNS/API outage), not a
  strategy error; its stage-1 work survived (`cand/orb_retest.py`, `results/orb_retest_*.csv`). Stage 1 is the best
  lead: London OR 08:00-08:29 local, enter at the first break, stop beyond the far side: TRAIN PF 1.39 (n 232),
  VALID 1.14 (n 101, harsh 1.03); time exit 16:00 UTC: TRAIN 1.32, VALID 1.52 (harsh 1.41). Wide stops (main
  strategy, not the $13 flip). Relaunched as workflow `xau-orb-finish` (stage 2 + two adversarial verifiers).

### Edge hunt results (continued)
- ORB finished (`cand/reports/orb_retest.md`): verdict "near" mechanically (TRAIN PF 1.44, VALID 1.20, harsh 1.11),
  but NOT an edge: VALID R expectancy -0.076 R/trade, random-direction p 0.15, drop-best-month PF 0.96; the winning
  side follows each split's drift (TRAIN longs +24 R, VALID longs -11.9 R / shorts +4.3 R); break direction matches
  the move to the flat time 52% / 51% (coin flip). Median stops 8-15 $/oz (not flip-eligible).
- X2 htf_breakout "near" mechanically (H1 Donchian 55, 1.5 ATR stop, 2R: TRAIN PF 1.54 t 2.82, VALID 1.07), NOT an
  edge: 255/256 configs profit in TRAIN from long exposure in the bull year (longs PF 2.28, shorts 0.66); VALID
  random-direction p 0.27, drop-best-month 0.84. trend_pullback fails. X1 exhaustion_fade, H11 silver_bullet and
  H9 round_reject fail (placebo hours/levels beat the real ones).
- **Engine look-ahead bug fixed:** `features.window_range` exposed the whole trading day's window range at bars
  with mod >= end, so with the NY-17:00 key the 21:00-23:59 UTC bars saw the upcoming Asia/London range. Now a
  running max/min exposed only after the window occurred (`xau_alpha/tests/test_features_causal.py`). H1 used it
  but only in London/NY windows (not affected); silver_bullet masked it.
- ml_meta (X3) and all verifiers were killed by the session usage limit; the Mac also went to sleep mid-run.

### ORB verification (2026-09-30, `xau_alpha/cand/reports/orb_retest_verify.md`): REFUTED, fatal
- Causality clean: independent rebuild matched all 333 orders; 6 truncation cuts gave 0 differences.
- All claimed numbers reproduce (TRAIN PF 1.44 n 232; VALID PF 1.20 n 101 in $/oz), but VALID is -7.6 R
  (-0.076 R/trade, PF in R 0.85). Random direction p 0.20 ($/oz) / 0.74 (R); mix-keeping permutation p 0.19 / 0.73;
  mirror +4.1 R (beats the rule); always-short at the same times +14.5 R. All 12 neighbours lose in R. The $/oz profit
  is 25 widest-stop trades (+362) vs the other 76 (-195). Best-of-171 TRAIN t 1.83 -> corrected p >= 0.16.
  Conclusion: long beta in 2025 + short beta / one volatile month in VALID; the rule's direction adds nothing.

### HTF breakout verification (`xau_alpha/cand/reports/htf_breakout_verify_1.md`): REFUTED, fatal
- VALID random direction p 0.31 ($/oz), R p 0.070 at 20k draws; direction shuffle p 0.31/0.07; same direction at a
  random later time beats the real entry 89% of draws. TRAIN: always-long at its timestamps beats it (PF 1.72 vs
  1.54). Drop best month VALID PF 0.84 (0.51 without best two). Bonferroni p 0.69-1.0, deflated Sharpe 0.82.
  Causality audit clean (verify_0). Long beta, not an edge.

### ML meta-filter X3 (`xau_alpha/cand/reports/ml_meta.md`): FAIL
- HistGBM on causal M5 features, trained pre-2025-10, threshold on Q4-2025; VALID AUC 0.514 (direction 0.518).
  Finalist a=1.5 T=60: VALID n 699 lf_base PF 0.88, harsh 0.59, mid 1.12; random direction p 0.17; every VALID month
  negative. The model predicts volatility (bracket-resolves AUC 0.75), not direction.
- **Edge hunt complete: 0 of 12 families passes. No candidate earned the single TEST (holdout) look.**

### Why ghost-grid-demo made 0 trades on 2026-09-30 (VPS logs/ghost_grid.log)
- 7 signals fired; all 7 were vetoed on the entry path: Jeff/Laya (graded "A_plus_prime" but vetoed at 1.5-5% conf),
  Trade Journal RAG "93.3% trap rate" (twins from the SYNTHETIC BUY-biased journal -> kills SELLs), regime prior
  "wick < 0.45" (synthetic rule), and one legitimate Core PCE news freeze. Not a market decision: a veto-stack bug.
- New bot rule: only the calendar blackout and the spread guard may block entries; Laya/Jeff/RAG/priors shadow-only;
  every blocked signal is logged with its reason; alert when no trade for too long.

### DEPLOYED 2026-09-30 19:31 UTC: `xau-alpha-demo.service` on the VPS (DEMO only)
- Strategy `xau_alpha/cand/shift_stack.py` = Mr P Fx "buyer's/seller's shift" (video mKP7TowaO5M): M5 down-leg ->
  break of last swing high -> 3 bullish M5 momentum -> pullback -> 48 h major-rejection level -> M1 engulfing entry;
  stack: on each confirmed M1 higher low trail ALL tickets' stop under it and add 2x the last lot (capped by free margin
  and basket loss at stop <= 12.5% equity). 24 h scanning except 20:45-22:15 UTC; news +-30 min (FF feed + CSV).
- Honest backtest (real bid/ask, $13, 1:500, lf_base): single ticket TRAIN PF 1.16 / VALID 0.87; the stack from $13
  RUINED in both TRAIN (peak $29) and VALID (peak $27): margin blocks doubling at $13 and the tight trail is noise-hit.
  Deployed on DEMO by owner's order to get live numbers.
- Service: `run_live.py --module shift_stack --mode DEMO --mirror 13 --handoff 500 --demote 300`; replaces
  ghost-grid-demo (stopped + disabled; it made 0 trades because of the synthetic veto stack). Log
  `/root/ict_sniper/logs/xau_alpha.log`, trades `data/state/xau_alpha_trades.jsonl`, quotes `data/state/quotes/`.
- Anti-stall: warm-up 4200 bars from Binance XAUUSDT perp (basis-shifted); FF calendar every 6 h; stale calendar
  blocks only US release windows; mirror bust resets to $13 and is counted; orphan positions flattened at startup.
- DEMO self-test passed: BUY 0.01 filled in 876 ms with SL, margin $8.31, flatten in 579 ms.

### shift_stack failure analysis and upgrade (2026-09-30 ~20:00 UTC)
- Path study (single ticket, no target, lf_base): P(MFE >= 1/2/3/5/8 R) = 45/34/27/19/11% TRAIN, 48/35/30/18/9% VALID
  vs random walk 50/33/25/17/11%: the entry is ~a coin flip (small excess at 3-5R), stable across splits; losers stop
  in ~10-14 min. Long/short edge flips by regime.
- Weekly-flip objective (`cand/shift_weekly.py`: each ISO week starts at $13, lock at $450): old 2x stack 0/50 weeks
  reach $100, max peak $33.9, bust 36%. Root cause: adds capped at 2x last lot and 12.5% basket-loss cap -> the stack
  never compounds. 72 exit/add variants (pivot size, add gating, BE) -> no week >= $100.
- Fix: "risk-free max-margin" adds (add_mode=riskfree): on each confirmed M1 higher low (pivot k=5) trail all tickets
  to low - 1.0 ATR, add the largest lot that free margin allows AND keeps the basket P&L at the stop >= -1x first
  risk. TRAIN: 1/50 weeks >= $100 (peak $101.6), mean week-end $13.16, bust 44%. VALID: 1/19 weeks peak $139.7,
  mean week-end $18.47 (vs $11.88 old), bust 47%. No week reaches $450 in either split.
- Math ceiling (fair game with ruin at the 0.01-lot margin $8.3): P($13 -> $450) ~ (13-8.3)/(450-8.3) = 1.1% per
  attempt without an edge, independent of exits/sizing; ~3% even with unlimited leverage. Starting at $50 -> ~9%.
- Live bot upgraded to riskfree stack + LOCK at $450 mirror equity (no new trades after), redeployed 20:01 UTC.

### Cross-asset / order-flow tests (2026-09-30 evening; data: Binance XAUUSDT+XAGUSDT perp 1m from 2025-12/2026-01, EURUSDT spot 1m)
- Dukascopy datafeed unreachable from Mac and VPS today (000/429). Data in `xau_alpha/data/xa/` (78 MB zips),
  loader `xau_alpha/lib/xasset.py`. Perp tests use Jan-Mar 2026 = discovery, Apr-May = confirm, Jun-Sep untouched.
- 1-minute lead-lag: silver/EUR/perp -> spot gold correlations +-0.01..0.03 with sign flips: none. Gold LEADS
  Binance EURUSDT (+0.05; thin market). Weekend: spot reopens at the perp price (corr 0.97), no continuation.
  Silver-shock: n 9/4, noise. HOLDOUT SLIP: the weekend table printed Jun-Sep rows before filtering (nothing tuned).
- **Order-flow imbalance (`cand/ofi_flow.py`)**: perp taker-buy imbalance over 15 min, z vs prior 1440 min, |z|>3 ->
  trade spot in the flow's direction, stop 3 ATR, 15-min time exit. lf_base: disc n115 PF 1.13 (+0.42 $/oz), conf
  n81 PF 1.15 (+0.25); gross PF 1.31/1.77; random-direction p 0.02 (first candidate ever to beat it); both sides
  positive; survives +5 s delay (PF 1.15). Fragile: +0.2 $/oz slippage/side -> PF 0.95/0.92; lf_harsh loses;
  months Jan -100, Feb +85, Mar +63, Apr +17, May +3. With a $4 stop cap (flip form): conf PF 1.03 (+0.05).
- DEPLOYED 20:19 UTC: `--module multi` (= shift_stack + ofi_flow capped $4, first signal wins, one position),
  `Environment=XAU_ALPHA_LIVE=1` makes ofi_flow fetch the last 1500 closed perp minutes from fapi (cached 20 s).

### OFI out-of-sample FAIL -> removed from live (2026-09-30 ~20:45 UTC)
- Web research round 2: `xau_alpha/recon/web_strategies_2.md` (short-term trend dead on gold post-2009; vol targeting
  no Sharpe for commodities; calendar effects unfit for $13; sizing near the margin floor: moderate beats max bets).
- Pre-registered test: unchanged OFI rule on 2025 with Binance PAXGUSDT perp flow (2025-03-27..12-31, n 148):
  mid PF 0.79, lf_base 0.53, random-direction p 0.69, every quarter negative. Caveat: PAXG perp != XAUUSDT perp.
  Per the pre-set rule the OFI leg is dropped; live bot back to `--module shift_stack` (ntfy alerts kept).

### HOLDOUT (Jun 1 - Sep 28 2026) used once, 2026-09-30 ~21:10 UTC, frozen rules
- OFI (XAUUSDT perp flow, same contract as discovery): n 139, mid PF 1.10, lf_base PF 0.80 (-0.35 $/oz), harsh 0.45,
  random-direction p 0.16, 3 of 4 months negative -> dead (already removed from live).
- shift_stack single ticket: n 96, mid PF 1.28, lf_base PF 1.14 (+0.45 $/oz, +0.04 R). Across TRAIN/VALID/TEST
  lf_base PF 1.16 / 0.87 / 1.14: thin, not significant, but the only setup not negative out of sample.
- shift_stack weekly flips from $13 (risk-free stack): 18 weeks, 0 reach $100, max peak $37.5, 61% bust, mean final
  $10.63. The $13 -> $450 goal is not supported by any tested strategy.

### Owner order 2026-09-30 21:15 UTC: "trade risky" (DEMO only)
- live/trader.py defaults: flip_max_losses_day 2 -> 10, flip_stop_max 4.0 -> 6.0, rf_budget 1.0 -> 3.0 (stack adds may
  risk up to 3x the first ticket). Guards kept: news/spread, $450 lock, mirror bust reset. Real account untouched.
- Then "risky just like the videos": stack_mode="double" (each add = 2x last lot, capped only by free margin at
  1:500, no basket-risk check), piv_k 2 (add on every M1 higher low), trail_buf 0.3 (tight). Backtest of this
  doubling variant from $13: busts 36-37% of weeks, no week >= $100 (see shift_stack analysis above).

### 2026-10-01: no trades overnight -> root cause + change (12:23 UTC)
- Bot healthy all night (0 errors) but the 5m shift setup never completed between 09-30 19:31 and 10-01 12:12 UTC
  (gold trended 4140 -> 4214). Funnel (all data, 5m): break 3105 -> momentum 2061 -> pullback+level 1913 -> tap
  1217 -> ~430 signals; the M1 engulfing within 15 min is the biggest filter.
- Timeframe comparison (lf_base PF 2025 / Jan-May26 / Jun-Sep26, trades/day): 5m 1.21/1.05/1.06 (~1/day);
  3m 0.68/0.98/1.10 (~1.6); 2m 0.86/1.52/0.56 (~2.4); 1m 0.85/0.84/0.69 (~4/day). Looser 5m thresholds: up to 1.8/day,
  PF 0.57-0.86 on 2026.
- Owner wants routine scalp trades: deployed `--module shift_mtf` (5m 'shift5' priority + 1m 'shift1' scalp, tagged
  for separate live P&L). Would have fired 10-01 02:58 and 04:39 UTC (shift1 SELL). Push alert after 6 h with no
  signal. Risk settings unchanged (video doubling stack).

### 2026-10-01: P&L reporting bug (live) fixed
- First live demo trade: shift1 BUY 0.01 @4171.60 13:27 UTC, SL 4169.71, broker SL hit after 46 s; real loss -$2.02
  (demo balance 22.02 -> 20.00; mirror 13.00 -> 10.98). The alert said +$1.05: `_close` used equity-after minus the
  last equity reading, which already contained the open loss. Now realized P&L = balance after close - balance at open
  (mirror-adjusted), plus a quote-based cross-check `pnl_quote`; losses_today uses the realized value.
  Regression test `xau_alpha/tests/test_live_pnl.py`.
### Video forensics (2026-10-01)
- Gold Mastery Fx (2P0aCxxFYCc): 7 trades located on Dukascopy (phone clock = UTC+3): T1 SELL 3386.04 06-17 ~08:30,
  T2 SELL 3396.6 06-18 13:30, T3 SELL 3374.0 06-19 ~04:26, T4 SELL 3354.5 06-20 09:53, T5 BUY 3353.1 06-23 ~07:15,
  T6 BUY 3363.8 06-23 ~09:35, T7 BUY 3360 06-23 ~12:40 (all 2025). Stops $2.5-6, exits +$6-11/oz, holds 30 min-2 h,
  full-margin compounding at ~1:3000 (Exness). `cand/zr.py` (zone rejection, mode trend_or_strong) detects all 7
  (6 within $3; T2 confirmation entry $3.7 later than his spike fill). Systematically: 7-20 trades/day, lf_base PF
  0.69-0.92 in every period/config. `cand/gmfx.py` (strict break-retest) catches 2/7, PF 0.86/1.09/0.66.
- Mr P Fx (mKP7TowaO5M): live trade 2026-09-25, BUY at the 05:10-05:14 UTC low (~4257-4259; his chart froze at 4258.901
  = 05:11 close), stacked 0.01..0.32 into a +$50 run to ~4310 by 10:30 UTC -> $1,032. Our 1m detector bought the first
  engulfing at 05:05 @4261.83 and was stopped by the 05:10 dip (-$3.70); re-entry (`reentry=True`) fired 05:30 and the
  tight trail was hit at 05:34. Re-entry overall: 5m PF 1.05/1.15/1.03, 1m 0.86/0.81/0.78.
- Context filters on 5m shift signals (H1/H4 trend, session, volatility, zone confluence): every one flips sign between
  periods; none adopted.

### XAUBot AI port (github aditisstillalive/xau-ai-trading-bot), 2026-10-01
- Audit: no malicious code; upstream ships pickled models (arbitrary code on load) -> never loaded. Windows/MT5-only.
  Source copy without pickles: `xau_alpha/xaubot/upstream/`; spec `xau_alpha/xaubot/PORT_SPEC.md`.
- Spec findings: ML blocks nothing since v0.2.4 (only scales confidence/lot), several gates unreachable, look-ahead in
  order blocks (back-written up to 9 bars, top feature), H1 join (label horizon), run-length features and full-history
  HMM decoding; live model probably constant 0.5; its own live days were -$97.78 and +$5.78.
- Leak-free rewrite `xau_alpha/xaubot/brain.py` (V1 features scale-free, Model D label, upstream XGBoost params, 3-state
  HMM fitted on 2025 and decoded on trailing windows). Trained on 2025: val AUC 0.514, 2026 AUC 0.521 / 0.508 (upstream
  claimed 0.73 with leaks). Max confidence ~0.556. As a gate on 5m shift signals: ML agreement lowers PF (Jun-Sep 0.56);
  skip-high-regime is inconsistent (1.01 / 1.44). Not adopted; live bot unchanged.

### 5-step SMC engine `xau_alpha/cand/smc5.py` (owner spec, 2026-10-01)
- H1+M15 trend aligned (EMA50 or structure) -> M15 order block of the BOS impulse as POI -> mitigation -> entry-TF
  liquidity sweep of the last confirmed swing inside the zone -> stop under the protected low, target nearest M15 swing.
  Truncation-invariant (no look-ahead). lf_base PF 2025/JanMay26/JunSep26: ema-5m-rr1 0.92/1.12/0.95 (1.2/day),
  structure-5m-rr2.5 0.74/1.14/1.12, ema-1m-rr1.5 0.78/1.03/0.80; gross PF >1 in all periods for several configs but
  costs eat the 1m variants (stops $1.6-3.4). Win rate 12-27%. Not better than shift5 (1.21/1.05/1.06).
- Added to the demo book (tag smc5, ema/5m/rr1.0) in `cand/shift_mtf.py` with priority shift5 > smc5 > shift1.
- Live demo tally so far (mirror $13): 10-01 13:27 shift1 BUY -2.02; 10-01 14:54 shift5 BUY -2.82 (trail stop after
  4.6 min) -> mirror $8.16; next entry will trigger a counted mirror bust/reset (0.01 lot needs ~$8.4 margin).

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
