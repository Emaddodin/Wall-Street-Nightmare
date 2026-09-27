# TBT-Engine codebase inventory: strategies, results, building blocks, lessons

Date: 2026-09-30. Branch `for-dear-opus-5.5`. Scope: read-only archaeology. Nothing outside `xau_alpha/` was modified.

Every claim carries a label:
- **MEASURED**: I computed it in this session. The scripts are in the session scratchpad, and the commands are in section 7.
- **SOURCED**: read from a named file.
- **OPINION**: my judgement.

## 0. Key numbers

| Item | Value | Label |
|---|---|---|
| Runner (deployed flip strategy) on PAXG proxy | PF 1.63, halves 1.99/1.49 at "mid" friction | SOURCED `data/runner_robust.csv` row 1 |
| Same Runner config on **Dukascopy XAUUSD** (`duka_bt`, 2025-01-21..2026-09-28, 599,173 bars) at the same "mid" friction | **PF 1.07, halves 1.00/1.08**, avg +0.16 pt/trade, n=1376 | MEASURED (`scripts/runner_realcheck.py`, 53 s) |
| Same, "harsh" friction | PF 0.89 (0.85/0.90) | MEASURED |
| Same, zero friction (gross edge) | PF 1.35 (1.32/1.36), avg +0.73 pt | MEASURED |
| Same, zero friction, pessimistic intrabar ordering | PF 1.16 (1.07/1.18), avg +0.33 pt | MEASURED |
| Same, Dukascopy-level spread 0.62 plus mid slippage | PF 0.88 (0.78/0.90) | MEASURED |
| Dukascopy XAUUSD mean-per-minute spread (all 526 days) | p10 0.51, **median 0.62**, p90 0.84, p99 1.59. By UTC hour the median is 0.57-0.61 from 07:00 to 20:00 and 0.75 at 22:00-23:00 | MEASURED |
| Spread the codebase assumes | 0.15 (MRP video, nearest-SR), 0.18 (Runner, ghost), 0.30 (Apex). Entries blocked above 0.45 (`ghost_engine.py:261`) | SOURCED |
| Actual LiteFinance ECN spread | **Unknown.** No local log records bid/ask | OPINION: this is the single most important missing number |
| Apex (production entry and exit chain) on Dukascopy | PF 0.22-0.70 in all 10 runs; every run goes to the $10 entry floor | SOURCED `data/apex_*`, `data/junwin_*` summaries |

**Verdict (OPINION):** Runner's PF 1.63 on the PAXG proxy does not survive on real XAUUSD. The deployed parameters show a small gross edge on Dukascopy, at most about 0.3-0.7 pt per trade depending on the intrabar fill assumption. A round-trip cost of about 0.4-0.7 pt removes it. Runner is only viable if the measured LiteFinance spread plus slippage stays well below about 0.3 pt, which is unlikely for Playwright web-UI execution at ~1.2 s order latency (SOURCED: `data/demo_system_trade_journal.json`, one sample, `order_latency_ms` 1227).

---

## 1. Strategies and their exact rules

In these rules, "pt" means $1 of XAUUSD price, which is $1 per 0.01 lot.

### 1.1 Apex Trinity: `scalper/strategies/apex_trinity.py` (live engine `run_xau_broker_live.py`)

`evaluate()` (L95) runs on the 1m history. It needs at least 30 bars and waits 180 s between signals. ATR is a 14-bar rolling mean of true range, clamped to [0.80, 8.0] (L82, L125). EMA20 and EMA50 use `ewm(span)` with the default adjust=True. The volume profile covers the last 120 bars (L132). The first setup that fires wins, in this order:

| Order | Setup | Entry rule | SL | TP1 / "spike" |
|---|---|---|---|---|
| 0.1 | `HOLD_LONG_POC_BOUNCE` (L216) | close>EMA20>EMA50; low within POC+0.35·ATR and close ≥ POC−0.15, or \|low−POC\| ≤ 0.35·ATR; lower wick ≥ 45 % of range; close ≥ open | max(min(low−0.20, POC−0.30), px−min(2.80, 1.8·ATR)) | +3.5·ATR / +6.5·ATR |
| 0.2 | `HOLD_LONG_VAH_BREAKOUT` (L247) | px>EMA50 and px≥VAH; low ≤ VAH+0.35·ATR and close ≥ VAH−0.10; lower wick ≥ 45 %; bullish candle | like 0.1, with VAH−0.40 | +4·ATR / +7·ATR |
| 0.3 | `SCALP_SELL_ASIAN_SWEEP` (L277) | at least 60 Asia bars (00-04 UTC today); high ≥ Asia high and high ≥ VAH and close < VAH; upper wick ≥ 50 % | min(high+0.25, px+min(2.5, 1.5·ATR)) | px − clip(\|px−POC\|, 1.5, 2.2) / −2.5·ATR |
| 2 | `SILVER_BULLET_FVG` (L313) | **Disabled** (returns None). Dead code: bullish FVG `low0>high2`, gap ≥ $0.70, trend-aligned, tap within CE+0.3·ATR | low2−0.30, capped at 1.8·ATR/3.0 | ±2.5·ATR / ±5·ATR |
| 3 | `TURTLE_SOUP_SWEEP` (L416), 06-09 UTC only | Asia range 00-04 UTC (≥60 bars); low<Asia low and close>Asia low with sweep wick ≥ 50 % of range gives BUY (mirrored for SELL) | wick ∓0.30, capped at 1.8·ATR/3.0 | ±2.5·ATR / ±5·ATR |
| 1 | `BREAKOUT_RETEST` (L506) | 5m resample **including the forming 5m bar**; prior 6-bar high/low; close > res·1.0003 arms UP for 20 min. 1m retest: close>EMA20>EMA50, low ≤ lvl+0.8·ATR and high ≥ lvl−0.3·ATR, lower wick ≥ 45 %, bullish candle (mirrored for DOWN) | low−0.20, capped at min(3.0, 1.8·ATR) | ±2.5·ATR / ±5·ATR |

Position management:
- Pyramid at +1.5·ATR: add 75 % volume and lock +0.5·ATR (L645).
- 60/40 scale-out at TP1, with a breakeven stop at entry ±0.30 (L677).
- A 0.01 lot cannot be split.

Live-only gates in `run_xau_broker_live.py`:
- Sessions (L364): allowed 00-06, 09-12 and 15-23 UTC. Blocked 06-09, 12-15, 23-24 UTC, Friday after 18:00, and weekends. The justification is win rates from synthetic backtests.
- Entry ATR ≥ `APEX_MIN_ENTRY_ATR` = 1.5 (L79). The comment says "walk-forward validated", but that validation was on PAXG.
- Calendar freeze, Laya/Jeff oracle and lot ladder (L409), all covered in section 6.
- Exit chain: `MicroExitController` plus breakeven/TP1 ratchets (only in memory; the broker SL is not modified, SOURCED HANDOFF), structure invalidation and a Friday flatten.

The backtest variant (`tests/backtest_live_loop_trump_regime.py:647-705`) is **not identical to live**:
- Retest tolerance is lvl+1.20/−0.20 instead of 0.8/0.3·ATR.
- SL is low−0.8·ATR.
- The breakout becomes usable one extra 5m bar later.
- Only `BREAKOUT_RETEST` is simulated. **The three volume-profile setups, which live checks first, were never backtested on any data** (SOURCED: grep finds only unit tests in `tests/test_volume_profile.py`).

### 1.2 MR P FX break and retest: `ghost_grid/mrp_break_retest.py` and `exit_controller.py`

Entry:
- Only **completed** 5m bars count (L60-63). A 6-bar prior high/low is broken by 0.02 % on close, and the same breakout bar never re-arms.
- The retest window runs 5-25 min after the breakout bar's open.
- Trend: close>EMA20>EMA50 (adjust=False).
- Retest: low ≤ lvl+0.60 and high ≥ lvl−0.25, lower wick ≥ 40 % of max(range, 0.12), bullish candle. SELL is mirrored.
- Disaster SL at low−1.50.

Exit (`GridExitController`, L60), in net points after spread:
- TP 0.45.
- Hard stop 1.30.
- Watermark: after a 0.30 peak, exit on a 25 % giveback (floor 0.07).
- Stagnation: 45 s with less than 0.07.
- Maximum hold 90 s.

Sizing: `CompoundingLadder` grids of 3-10 orders at 0.01-0.20 lots. The basket risks at most 15 % of equity. After 3 consecutive losses there is a 30 min cooldown, and the daily loss limit is 30 % (`ghost_engine.py:21-43`).

### 1.3 Runner (current flip strategy): `ghost_grid/runner_strategy.py`, `runner_exit.py`; live parameters in `ghost_engine.py:37-41`

- Evaluated only when the last 1m bar has minute % 5 == 4, meaning the 5m bar has just completed.
- "ATR" is the mean of the last 14 one-minute high−low ranges, not true range. It must be ≥ 3.5.
- BUY if the 5m close is above the highest high of the prior 48 five-minute bars (4 h). SELL is mirrored.
- Stop 4.0 pt.
- Once the peak reaches 4.0 pt, the stop moves to +0.3.
- Then the stop trails 2.0 pt behind the peak and never loosens.
- Time stop 5400 s. There is no profit cap.
- Risk is 12 % of equity (`RUNNER_RISK_PCT`).

The class defaults (lookback 24, ATR 2.5, stop 2.5, trail 3.5) differ from the live environment defaults.

Research copy: `scripts/runner_flip.py`. Its entry is the next 1m open ± half spread plus entry slippage. Its maximum hold is 90 min.

### 1.4 Other strategies

| Strategy | File | Rules (short) | Tested on real data? |
|---|---|---|---|
| EURUSD OTE micro-scalp | `scalper/strategies/eurusd_microscalp.py:49` | Body range of the last 12 five-minute bars ≥ 12 pips. BUY if price is in [low+0.21R, low+0.38R] and the SL (swing low − 2 pips) is 4.5-8.5 pips away; TP at swing high + 0.27R; 3 limit layers. There is no impulse-direction logic (both "OTEs" come from the same box). | No. Not gold. |
| Nearest S/R "video" scalper | source deleted; bytecode only in `ghost_grid/__pycache__/nearest_sr_scalper*.pyc`; harness `tests/tune_nearest_sr.py` | Zones with ≥ 6 touches over 1440 bars; target is the nearest opposing level 8-12 pt away; RR ≥ 3; SL ≤ 3 pt; optional break-flip filter | Yes, on PAXG `data/candles/real/`. The harness comment says **nothing was positive on both splits** (SOURCED `tests/tune_nearest_sr.py:88-91`). |
| HyperPredator | `backtester.py:604-812` | M1 wick ≥ 65 % at M5 S/R (lookback 50), SL $1 beyond the wick, target at the opposite M5 zone, L2/tape exits (data we do not have) | No. Synthetic only. |
| MicroExitController (exit only) | `scalper/strategies/micro_exit_controller.py:190` | Watermark → hard stop → fast breakeven → harvest target → stall → time decay. XAU default target 0.85 pt with breakeven at 0.40, which is **below the 0.62 median Dukascopy spread**. `get_micro_account_config` uses BUY TP 3.5 / 6.5 pt and SELL 1.8 / 2.5 pt. | Only through the Apex backtests. |
| Relapse scalper, HyperPredator bot, hyperliquid bots | source deleted (`__pycache__/run_relapse_scalper*.pyc` etc.) | — | Unknown |

---

## 2. Every backtest result, with data source and verdict

How I identified the data source (MEASURED):
- The default `--data-dir` of the script that wrote the file.
- The file's mtime compared with the creation time of each candle directory.
- The day count against the file count in each directory.

Directory creation times (2026-09-29 unless noted): synthetic `gold_m1_*` on 09-22, `real/` on 09-28 03:58, `real_bt` 20:37, `real_bt2` 20:42, `duka_bt` 23:51, `duka_bt_jun` 23:56.

Price-level check (MEASURED):

| Source | Q1-2026 mean close | Q1-2026 mean 1m range |
|---|---|---|
| Synthetic | $2,650 | 1.33 |
| PAXG | $4,880 | 2.75 |
| Dukascopy | $4,877 | 3.78 |

Synthetic data is off by about $2,200, and PAXG's 1m ranges are about 27 % smaller than real XAUUSD.

### 2a. SYNTHETIC. Untrustworthy: fiction.

`scripts/fetch_real_m1.py` docstring: every day is `generate_synthetic_gold_m1(start_price=2490, seed=date)`. The generator (`backtester.py:277`) also **injects rejection wicks with a forced ≥ 68 % wick and tick-velocity surges on those bars**, which are exactly the features the strategies look for.

| File | Engine | Trades | WR % | PF | Max DD % | Headline |
|---|---|---|---|---|---|---|
| `regime_analytics_summary_full.json` | Apex BR+SB, 473 d | 6613 | 79.0 | 5.42 | 70.2 | $60 → $22.2M. Breakout-retest 83.7 % WR, Silver Bullet 48.8 %. |
| `regime_analytics_summary.json` | same, 170 d | 2823 | 78.7 | 1.96 | 71.9 | $50 → $4.0M. Turtle Soup 12 trades, 25 % WR. |
| `trump_regime_30usd_summary.json` (and identical `baseline_0/`) | anchor | 5853 | 83.8 | 5.48 | 60.3 | $30 → $12.4M |
| `trump_regime_daily_summary.json` | — | — | 56.2 | — | — | $700 → $9.3M |
| `trump_regime_hardened_summary.json` | hardened | 1115 | 52.5 | 2.36 | — | $60 → $31.9k |
| `trump_regime_hyper_scalp_summary.json` | ultra_clean / baseline | 5719 / 13803 | 55.6 / 54.2 | 2.96 / 1.05 | 70.0 / 99.2 | — |
| `trump_regime_gold_optimized_summary.json` | hybrid masterpiece | 4516 | 60.1 | 1.24 | 97.3 | $60 → $59.8k |
| `backtest_60day_daily_stats.json` | Apex / baseline | 1284 / 1295 | 83.0 / 36.3 | 6.34 / 3.05 | 29 / 53 | — |
| `grid_optimization_report.json` | 5 exit variants | 447-495 | 33-38 | 1.14-1.37 | — | — |
| `daily_withdrawal_schedule_50usd.json` | — | — | — | — | — | $50 → $9,849 profit on day 1 |
| `baseline_a_live/serial_summary.json` (09-25, 473 d to 2026-09-20) | live exit chain | 627 | 21.5 | 0.97 | 92.3 | $30 → $9.60, **loses even on synthetic data** |
| PDF "Daily Compounding Ledger" | Apex | — | — | — | — | $60 → $6.6M (SOURCED HANDOFF) |

### 2b. PAXG proxy, `real_bt`. Untrustworthy: data artefact.

Jan-May 2025 PAXG prices are $1-quantized (SOURCED HANDOFF).

| File | Trades | WR % | PF | Result |
|---|---|---|---|---|
| `real_live30_summary.json` | 16 | 0.0 | 0.00 | $30 → $9.79 |
| `rl_serial_300_summary.json` | 69 | 5.8 | 0.03 | $300 → $7.85 |

### 2c. PAXG proxy, `real_bt2` (2025-06-02..2026-09-25). Real prices, wrong instrument.

Fixed friction throughout. Verdict: **indicative only; did not transfer to Dukascopy.**

| File | Engine / variant | Trades | WR % | PF | DD % | Start → end + withdrawn |
|---|---|---|---|---|---|---|
| `r2_live_100` | Apex live, no ATR gate | 969 | 37.3 | 1.15 | 84.7 | $100 → $96 + $307 |
| `r2_live_300`, `r2_serial_300` | same | 953 | 37.0 | 1.11 | 84.5 | $300 → $96 + $490 |
| `r2_serial_30` | same | 218 | 28.9 | 0.80 | 74.4 | $30 → $7.93 |
| `r3_1.5_{30,100,300}` | ATR ≥ 1.5 | 494 / 484 / 426 | 44.9 / 44.2 / 60.8 | 1.31 / 1.28 / **2.81** | 79-84 | The $300 PF is a lot-size-path artefact (same strategy and data) |
| `r3_2.0_{30,100,300}` | ATR ≥ 2.0 | 325 / 317 / 296 | 48.9 / 48.6 / 61.5 | 1.27 / 1.25 / 2.57 | 64-79 | — |
| `rep60_base` / `rep60_atr15` | $60 replicate | 967 / 490 | 37.2 / 44.7 | 1.13 / 1.28 | 80.5 / 78.9 | — |
| `t_scalper_summary.json` | MRP ghost (`backtest_ghost_real.py`) | 21 | 38.1 | 0.60 | 60.1 | $12.47 → $6.19, then margin-locked (0.01 lot needs more than $6.19 at 1:500) |
| `runner_grid.csv` | Runner, 324 configs, fixed 0.18 spread, 0 entry / 0.05 exit slippage | 2.6k-5.3k per config | — | top 1.92; 96 % of configs PF > 1 on both halves | — | Friction far too low |
| `runner_robust.csv` | Runner, 162 configs × mid (0.15/0.25) / harsh (0.30/0.50) | 768 (best) | 43.9 | best 1.63 mid / 1.43 harsh; median 1.16 / 0.95 | — | Best config was deployed |
| HANDOFF flip odds (`runner_flip_odds.py`) | Runner, compounding simulation | — | — | — | — | $12.47 → $100: about 28 % hit / 67 % ruin. **Overlapping 2-day start windows on in-sample trades, so these odds are overstated** (OPINION). |

### 2d. DUKASCOPY XAUUSD (real). Most trustworthy, but friction is optimistic.

Apex runs use a fixed 0.30 spread plus 0.10 exit slippage. The measured Dukascopy median spread is 0.62.

| File | Data | Start | Trades | WR % | PF | DD % | End |
|---|---|---|---|---|---|---|---|
| `apex_12.47_base` / `_atr15` | `duka_bt` 437 d | $12.47 | 6 / 14 | 0 / 28.6 | 0.00 / 0.56 | 21 / 40 | $9.82 / $8.35 |
| `apex_100_base` / `_atr15` | `duka_bt` | $100 | 156 / 120 | 14.1 / 29.2 | 0.22 / 0.56 | 90 / 91 | about $10 |
| `apex_200_base` / `_atr15` | `duka_bt` | $200 | 293 / 129 | 19.1 / 27.9 | 0.31 / 0.59 | 95 / 96 | about $10 |
| `junwin_60_0` / `_1.5` | `duka_bt_jun` 344 d | $60 | 121 / 198 | 20.7 / 35.4 | 0.37 / 0.70 | 84 / 85 | about $9.5 |
| `junwin_100_0` / `_1.5` | `duka_bt_jun` | $100 | 169 / 264 | 19.5 / 35.6 | 0.35 / 0.66 | 91 / 92 | about $8-10 |
| **Runner deployed config, mid friction** (MEASURED) | `duka_bt` | — | 1376 | 44.1 | **1.07** (1.00/1.08) | — | — |
| Runner, harsh friction (MEASURED) | `duka_bt` | — | 1376 | 42.5 | 0.89 | — | — |
| Runner, Jun-2025+ only, mid friction (MEASURED) | `duka_bt_jun` | — | 1296 | 43.7 | 1.07 (0.99/1.10) | — | — |

Other Runner configs on Dukascopy (MEASURED, mid friction → harsh friction):

| Config | Mid PF | Harsh PF |
|---|---|---|
| L24 atr≥3.5 stop 4 trail 2 | 1.02 | 0.85 |
| L12 atr≥2 stop 1 trail 1 (top of the PAXG grid) | 0.68 | 0.41 |
| L48 atr≥5 stop 4 trail 2 | 1.20 (0.88/1.26) | 0.99 |
| L48 atr≥5 stop 6 trail 3 | 1.07 | 0.92 |
| L24 atr≥6 stop 6 trail 3 | 1.13 (1.18/1.13) | 0.98 |

**No configuration exceeds PF 1.0 on both halves under harsh friction.**

---

## 3. Reusable building blocks, with a causality audit

Causality verdicts: "causal" means the value at bar i uses only bars ≤ i. I checked all of these by reading the code. I checked the two bugs marked MEASURED by running the code.

| Block | File:line | Causal? | Notes and bugs |
|---|---|---|---|
| `killzone(t_ms, zones)` | `scalper/pa/ict.py:32` | yes (time only) | Fixed UTC with EDT alignment and **no DST**, so windows are off by 1 h from November to March. Use the America/New_York timezone instead. |
| `asian_range(df)` | `ict.py:53` | yes (running cummax/cummin of completed Asia bars per day) | Default window 00-04 UTC |
| `fvg_state(df, max_age)` | `ict.py:91` | yes (known at the close of bar i; mitigated when the CE is touched) | **BUG (MEASURED): output columns are shifted by one** (L133-137). `bull_ce` holds the gap bottom, `bull_gap_hi` the gap top and `bull_gap_lo` the CE; the bearish columns are shifted the same way. The internal mitigation logic is correct. Tracks only the freshest gap per direction. No displacement check on the middle candle. |
| `displacement(df)` | `ict.py:144` | yes (average body is `shift(1)`) | Body ≥ 1.5 × average of 20; body/range ≥ 0.70; opposing wick ≤ 0.20 |
| `order_blocks(df, swing_lo, swing_hi)` | `ict.py:165` | yes, given causal swings | **BUG (MEASURED): `NameError: h` at L219.** `h` is never defined, so the function crashes as soon as a bearish OB exists. Zone is the body, MT is (O+C)/2, mitigation is an MT touch. |
| `choch_state` | `ict.py:233` | yes, given causal swings | Fires on every close beyond the last swing. It does not track the prior trend, so it cannot tell a BOS from a CHoCH. |
| `equal_highs_lows` | `ict.py:258` | yes, given swings marked at their confirmation bar | Tolerance 0.04 % (about $1.7-2.0 at $4.3-4.9k) |
| `turtle_soup(df, levels, dir)` | `ict.py:321` | only if `levels` is already lagged by the caller | Sweep wick ≥ 0.6 of the bar range |
| Swing detector | **missing as source.** Only bytecode remains: `scalper/indicators/__pycache__/__init__*.pyc` (`swing_points`, `confirmed_swings`, `market_structure`) and `scalper/structure/__pycache__` (`bos_at`, `swing_at_or_before`) | — | `ict.py` expects causal swing series, but no source produces them. **Pitfall:** a centred pivot (`rolling(center=True)`) marked at the pivot bar leaks k bars of the future. A swing must be stamped at pivot+k. |
| `prior_resistance/support`, `range_high/low`, `consolidation`, `breakout_up/down` | `scalper/pa/levels.py:21-62` | yes (`shift(1)` before rolling) | The `consolidation` default of `range_pct=0.12` (12 %) is meaningless on 1m gold and must be retuned. |
| Candle patterns (doji, hammer, engulfing, pin bars, streaks) | `scalper/pa/candles.py:25-93` | yes | Library default thresholds |
| `VolumeProfileEngine.compute_profile` / `compute_from_candles` | `scalper/strategies/volume_profile.py:60,169` | yes, if given closed bars | Spreads each bar's volume evenly across bins from low to high; POC, 70 % VA, HVN/LVN. Volume is tick volume only (Dukascopy volume column is about 0.1). |
| `ApexTrinityStrategy.compute_atr` | `apex_trinity.py:82` | yes | Simple mean of true range, not Wilder's |
| Apex 5m breakout | `apex_trinity.py:518-548` | **repaints live**: uses the forming 5m bar | The backtest delays it instead, so live and backtest differ. |
| MRP completed-5m breakout | `ghost_grid/mrp_break_retest.py:53-91` | yes | Good template for completed-bar resampling. |
| Runner signal | `ghost_grid/runner_strategy.py:36`; research `scripts/runner_flip.py:46` | yes | — |
| `runner_flip.run` fill simulator | `scripts/runner_flip.py:70` | entry at next open: OK | Adverse extreme is checked before favourable (conservative at entry). **Optimistic after arming**: the trailing stop set from a bar's high is not checked against the same bar's low. MEASURED effect: gross average 0.73 → 0.33 pt. Stops fill exactly at the level, with no gap-through. The `duka_raw` tick files can resolve this. |
| Anchor precompute | `tests/run_30usd_trump_regime_backtest.py:62` | 5m breakout: yes, conservative (L103-127, `shift(1)` plus `avail_time`) | **Look-ahead:** the Asian range is computed from the whole day's 00-05 UTC bars (L137-139), which leaks if used before 05:00. EMA and ATR restart every day because each file is processed on its own. |
| Tick-path model | `tests/backtest_live_loop_trump_regime.py:134-222` | yes | 4 ticks per bar (O-L-H-C for up bars, O-H-L-C for down bars), plus interpolated stop ticks |
| `compute_m5_pivots` | `backtester.py:613` | mostly | 5-bar blocks are counted from index 0, not the clock, so they misalign after gaps. `bfill` (L645) leaks at the first bars. |
| Session and time blocks | `ghost_grid/ghost_engine.py:249-291`; `run_xau_broker_live.py:364` | yes | Ghost blocks Friday ≥ 20:30 UTC, Saturday, Sunday before 22:00, the 23:00 UTC rollover, and spreads above 0.45. Apex sessions are justified only by synthetic win rates. |
| Walk-forward harness | `scalper/tools/walkforward.py` | — | **File is corrupted.** The original 5.9 KB of code is interleaved with 5,920 copies of a GRID block (2.04 MB, committed that way). A reconstructed copy is in the session scratchpad. Design: train 60 d / validate 20 d / test 20 d, rolled forward 20 d, 4 folds; selection score is expectancy × min(PF, 20) with n ≥ 10. It depends on deleted modules (`engine`, `config.loader`, `market_data.store`). |
| Dukascopy tick → M1 builder | `scripts/fetch_dukascopy_m1.py`, `fetch_duka_raw.py` | — | Mid-price candles plus a mean-spread column. The bi5 decoder is reusable. |
| Edge miner (idea) | deleted; docstring in `scalper/__pycache__/edge_miner*.pyc` | — | Conditional forward-return mining over 1/3/5 bars (vol_z, body/range, wicks, atr_z, rng_pos). Worth rebuilding causally. |

---

## 4. ICT concepts from the knowledge library, as codeable rules

Source: `scalper/learn/ict-knowledge-library/concepts/*` (226 files, each with a JSON criteria block). ICT times are **America/New_York** and must be converted with DST: UTC = NY + 4 h in EDT, NY + 5 h in EST (SOURCED `04-time-cycles/dst-handling.md`). Parameters marked "calibrate" have no gold-specific value in the library.

`scalper/knowledge/pieces.jsonl` has 301 entries: 299 "untested" and 2 "tested-kept" (ECA cycle filter, CVD filter). Both were tested in the old crypto engine, not on gold (SOURCED).

| Concept | Codeable rule | Parameters | Existing code |
|---|---|---|---|
| Swing high/low | `SH(n): H_n > H_{n±1..k}`, **confirmed and usable at bar n+k** | k = 1 (library), 2-3 for M1 noise (calibrate) | missing |
| BOS / CHoCH / MSS | BOS: close > last confirmed SH and prior trend is bullish. CHoCH: the same break against the prior trend. MSS = CHoCH + displacement + FVG left in the break leg. | — | `choch_state` (no trend state) |
| Liquidity sweep | BSL: `H>lvl and C<lvl and (H−C) > 0.6·range`. SSL is mirrored. | wick ≥ 0.6 | `turtle_soup`, Apex turtle soup (0.5) |
| EQH/EQL | two confirmed swings with \|Δ\| ≤ ε and an opposite swing between them | ε calibrate (e.g. 0.1·ATR_M15) | `equal_highs_lows` |
| FVG / CE | bull: `L_{n+1} > H_{n−1}`, zone [H_{n−1}, L_{n+1}], CE is the midpoint; candle n must be a displacement. Mitigated when the CE is touched. 2025 classes: "immediate" if touched within 3 bars, "delayed" if untouched for 5+. | minimum size ≥ x·ATR (calibrate) | `fvg_state` (fix column bug) |
| Inversion FVG | after a close through the far edge of an FVG, the zone flips polarity; enter on retest with displacement | — | — |
| Displacement strength | 5-factor score (5-15): body/range {0.5, 0.7, 0.85}, opposing wick {0.3, 0.2, 0.1}, body vs average {1, 1.5, 2}, FVG, follow-through | score ≥ 12 = strong | `displacement` (binary) |
| Order block | last opposite-colour candle (consecutive same-colour candles merged) before a displacement that breaks structure; zone = bodies; MT = (O+C)/2; fresh until the MT trades | — | `order_blocks` (fix NameError) |
| Breaker | OB violated by a close with displacement; retest from the other side with rejection | — | — |
| Killzones (NY time) | Asia 20:00-00:00; London 02:00-05:00; NY AM 08:00-11:00; London close 10:00-12:00; NY PM 13:30-16:00. The 2017 set is London 01:00-05:00 and NY 07:00-10:00. | — | `killzone` (UTC, no DST) |
| Silver Bullet | windows 03-04, **10-11 (strongest)** and 14-15 NY. Sweep (wick ≥ 0.6) → displacement → FVG → entry at CE, **preferably in the first 25 min**; SL beyond the sweep; target ≥ −1.5 SD or the higher-timeframe draw on liquidity (DOL) | — | Apex SB (disabled; used 1m, no sweep requirement) |
| Macros | NY 00:50-01:10, 02:50-03:10, 09:50-10:10, 13:50-14:10, 14:50-15:10 | — | — |
| Asian range, sweep, projections | range = max/min over 20:00-00:00 NY. Sweep: wick through the range edge with close back inside and wick ≥ 0.6. Targets: opposite edge ± {0.5, 1, 1.5, 2} × range. | — | `asian_range` (00-04 UTC) |
| Judas swing | the killzone's first 15-60 min sweeps a known pool, then reverses in the same killzone with displacement and an FVG, aligned with higher-timeframe bias | — | — |
| London close reversal | London-open move has reached a higher-timeframe level; reversal between 10:00 and 12:00 NY | — | — |
| NY AM opening range | OR = 08:00-08:30 (or 09:00) NY. After the window: bullish higher-timeframe bias plus an OR-low sweep gives a long on reversal (mirrored for short). | — | — |
| CBDR | 14:00-20:00 NY body range R < "40 pips" (gold equivalent: calibrate); projection = opposite edge ± n·R, n ∈ {1, 2, 3}, fulfilled 10:00-12:00 NY | — | — |
| OTE | clean impulse leg that broke structure; 0.62-0.79 retracement (0.705 sweet spot) of **body-anchored** fib; PD array present; entry in the impulse direction; SL at the leg origin; targets −0.27/−0.62/−1.0 or −0.5…−2 SD; 08:30-11:00 NY | — | eurusd_microscalp (incorrect: no leg direction) |
| Turtle soup | wick through a known level, close back inside within 0-3 bars, displacement in the opposite direction | — | Apex turtle soup |
| ICT 2022 model | HTF bias + killzone + sweep + displacement with FVG + CE entry + SL beyond the sweep + SD/DOL target | — | — |
| Unicorn | breaker + FVG nested inside the breaker + prior sweep + bias | — | — |
| SMT | correlated asset fails to confirm a new extreme. For gold: XAGUSD, or DXY inverted (OPINION); data not yet in the repo. | — | — |
| News blackout | FOMC −30/+60 min; press conference −5/+60; NFP and CPI −15/+30; other tier-1 −5/+15. NFP: skip 08:30-08:35 NY, trade FVG retests until 09:30 at 50-75 % size. CPI: skip until 08:40. FOMC: skip stage 1 (14:00-14:15), trade stage 2 (14:30+) FVG retests. | — | `politician_brain.check_calendar_freeze` (−15/+5 min only) |
| Risk | SL = PD-array invalidation ± buffer; partials at 1-2R then breakeven; risk per trade (library) | — | Apex 60/40 |

All of these are untested on XAUUSD in this repo. The ones that were tested are Silver Bullet and Turtle Soup, on synthetic data only, which gives no evidence either way (OPINION).

---

## 5. Lessons learned: what has already been shown not to work (or proves nothing)

1. **Synthetic-data results are void.** Examples: the $60 → $22M run, the 83.7 % breakout-retest win rate, Silver Bullet at 48.8 %, Turtle Soup at 25 %, and the session win rates (82.7-85.6 %). The generator restarts every day at $2,490 and plants the ≥ 68 % rejection wicks the strategies trade (SOURCED `backtester.py:325-370`, `scripts/fetch_real_m1.py`). The Silver Bullet and Turtle Soup vetoes therefore rest on no real evidence.
2. **The PAXG proxy flatters results.**
   - Apex: PF 1.11-1.31 on PAXG versus **0.22-0.70 on Dukascopy** for the same code.
   - Runner: 1.63 versus 1.07 at the same friction.
   - The comment "walk-forward validated PF 1.3" (`run_xau_broker_live.py:79`) refers to PAXG.
   - PAXG 1m ranges are about 27 % smaller than XAUUSD (MEASURED), and Jan-May 2025 PAXG is $1-quantized.
3. **Apex breakout-retest with the production exit chain loses on real XAUUSD.** This holds at every start balance ($12.47-$200), with or without the ATR ≥ 1.5 gate, and even with optimistic 0.30-spread friction. Structure-invalidation exits make up 51-100 % of trades (SOURCED `data/apex_*`, `data/junwin_*`).
4. **Micro-targets cannot beat the spread.** MRP targets 0.45 pt with a 1.30 pt stop. MicroExitController targets 0.85 pt with breakeven at 0.40 pt. The Dukascopy median spread is 0.62 pt (MEASURED). MRP on PAXG went $12.47 → $6.19 at PF 0.60 (SOURCED `t_scalper_summary.json`).
5. **Runner (volatility breakout plus trailing stop) has a thin gross edge that friction consumes.** Gross PF on Dukascopy is 1.35 with optimistic intrabar fills and 1.16 with pessimistic fills (MEASURED). It is at or below 1.0 once round-trip cost exceeds about 0.4-0.6 pt. The PAXG-optimal small-stop configs (stop 1 pt) collapse to PF 0.41-0.68.
6. **Fixed-dollar thresholds are not stationary.** Gold's mean 1m range went from 0.83 (2025-01-21) to 3.78 (Q1 2026) and 2.25 (2026-09-28), and price from $2,710 to $4,128-4,880 (MEASURED). An ATR ≥ 3.5 gate therefore selects mostly 2026. Use ATR- or price-normalised parameters.
7. **PF measured in dollars on compounding runs is not a signal metric.** `r3_1.5_100` gave PF 1.28 and `r3_1.5_300` gave PF 2.81 with the same strategy and data. Report per-point PF on fixed size.
8. **Backtests that call `politician_brain` or `regime_prior_engine` are not reproducible and are circular.**
   - The news gate reads the live calendar and headlines at run time (`datetime.now()`).
   - The priors and the trade-journal RAG are fitted on the synthetic journal.
   - Evidence (MEASURED): the synthetic regime journal is 6345 BUY versus 268 SELL (the seeded STRONG_BULL state vetoes sells), while the 2026-09-29 runs are about 50/50.
9. **The nearest-S/R "video" strategy was positive on neither split** on PAXG (SOURCED `tests/tune_nearest_sr.py:88-91`). The "flip" slice was only positive in-sample.
10. **The flip odds (28 % reach $100 / 67 % ruin) are optimistic.** They come from PAXG trades, in-sample, with overlapping start windows. The binding constraint is the minimum lot: 0.01 lot × 4 pt = $4, which is 32 % of $12.47 (SOURCED HANDOFF).
11. **Execution is browser automation (Playwright on the LiteFinance web terminal)**, not the MT5 API. The single logged order latency is 1.2 s. OPINION: this rules out sub-minute scalps and adds slippage that is not modelled.
12. **Code-health traps.**
    - `order_blocks` crashes.
    - `fvg_state` columns are mislabelled.
    - `walkforward.py` is corrupted.
    - The swing and structure helpers exist only as `.pyc` files.
    - Many modules (relapse scalper, nearest_sr, edge_miner, slm_intuition) have deleted sources.
    - None of the ICT library code (`scalper/pa/ict.py`) was ever used in a gold backtest.
13. **`_archive/` holds no evidence of a gold edge.** It contains crypto-era ML (`filter_model` CatBoost/LightGBM, a `kronos_brain` hook to the Kronos foundation model whose weights were never installed) and old service files. `.agents/challenger_gold_{1,2}` hold only bytecode of engineering stress tests (margin, daily-drawdown kill-switch, dispatch races), with no empirical results.

---

## 6. News guards and LLM (Laya / Jeff) models

This section answers the user's note.

| Component | File:line | What it does | Evidence of value | Reuse verdict (OPINION) |
|---|---|---|---|---|
| Calendar freeze | `scalper/brain/politician_brain.py:375` | FairEconomy `ff_calendar_thisweek.json`, USD events. Freezes entries from T−15 min (HIGH) or T−5 min (MEDIUM) until T+5 min. A "post-news expansion" window from T+5 to T+45 min gives BUYs **1.5× size and 2× TP** (L632-645, hard-coded "89.5 % WR", no source). If the feed returns nothing, the fallback covers only Thursday claims and first-Friday NFP; **CPI and FOMC are not covered, so the gate fails open.** | None measured | Keep a blackout idea but rebuild it: use the library's windows (section 4), a cached historical calendar so it can be backtested, and fail closed. Drop the post-news BUY boost. |
| Headline "politician" sentiment | `politician_brain.py:121-146, 184-208, 298-373, 601-720` | Google News and forexlive RSS classified by keyword substring match (for example "war" also matches "warning" and "award"). The initial state is seeded with **hard-coded bullish headlines** (TRADE_WAR, STRONG_BULL, heat 62.5-67.5). It vetoes SELLs when bias is STRONG_BULL and heat ≥ 65, vetoes BUYs on STRONG_BEAR, and boosts size up to 1.5×. | None; not backtestable | Do not reuse as a gate. At most, log it as a feature for later offline study. |
| MacroWatchdog | `scalper/brain/macro_watchdog.py:68` | ±5 min freeze around registered events | `register_scheduled_event` is called only in `scalper/tests/test_laya_system.py`, so **in production the event list is empty and it always returns SAFE** | Dead gate |
| Regime prior engine | `scalper/brain/regime_prior_engine.py:69` | Vetoes hour 23 UTC, Turtle Soup, Silver Bullet and wick < 0.45. Grades "A+ prime" hours {1, 4, 5, 8-11, 13, 15-21} with 89.4 % WR and 1.5× size. Gives POC setups "TITAN 89.5 %" and 1.5× size. | All numbers come from the synthetic backtest, and the POC setups were never backtested | Discard the numbers. The hour-23 rollover block is sensible on spread grounds: Dukascopy median spread is 0.75 at 22-23 UTC (MEASURED). |
| Trade-journal RAG | `scalper/brain/trade_journal_rag.py:177` | k-nearest "twins" from `regime_trade_journal_full.csv` (synthetic) → allow, veto, or size multiplier | Circular | Discard |
| ICT RAG | `scalper/brain/ict_rag.py:121` | Retrieves concept text for the LLM prompt | — | Useful only as documentation |
| Laya / Jeff oracle | `scalper/brain/laya_oracle.py:145`, `jeff_client.py` | Pipeline: calendar freeze → regime prior → politician → RAG twins → LLM grade (choice / "noul" / score over a text description of the setup) → size multiplier 1.0-1.75× and TP expansion. Backends: Jeff (`GestaltLabs/Jeff-1`, a LoRA on Qwen3-4B, local server on port 8079, only if `JEFF_URL` is set) → Laya (`convaiinnovations/laya` Router, about 1.3 s per decision on the VPS) → a rule fallback that is itself built on the synthetic priors. Locally, `import laya` fails because of a torch `libiomp5` dylib error (MEASURED). | **No backtest has ever included the LLM.** The models are general text graders, not trained on XAUUSD outcomes, and their outputs are uncalibrated. | Not usable as an edge source until it is evaluated offline on a labelled, causal real-data set with a proper hold-out. Size boosts from it should be removed (OPINION). |
| llm_doctor / slm_intuition | `scalper/sentinel/llm_doctor.py`; `macro/__pycache__/slm_intuition*.pyc` (source deleted) | Qwen2.5-1.5B on llama-server for operations and SRE triage; a grammar-constrained HOLD/EXIT "intuition" exit with a 300 ms timeout; ±15 min news blackout | None | Operations only; not an alpha source |

---

## 7. Reproduction

- `cd /Users/mac/Desktop/TBT-Engine && python3 -m scripts.runner_realcheck`: Runner on Dukascopy `duka_bt`, 6 configs × {mid, harsh}. Took 53 s on 1 core.
- Scratchpad scripts (not in the repo), run with `PYTHONPATH=<repo>`:
  - `runner_jun.py`: `duka_bt_jun`, several friction levels, plus the Dukascopy spread quantiles by hour and quarter.
  - `runner_sp.py`: spread 0 / 0.18 / 0.62.
  - `runner_pess.py`: pessimistic intrabar ordering.
- Bug checks: `scalper.pa.ict.order_blocks` on `duka/xau_m1_2025-06-03.csv` (first 600 bars) raises `NameError: name 'h' is not defined`. `fvg_state` bullish rows satisfy `bull_ce < bull_gap_lo < bull_gap_hi` in 100 % of 132 rows, and `bull_gap_lo` equals the midpoint in 100 %, which confirms the column shift.
