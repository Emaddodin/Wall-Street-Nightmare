# XAUBot AI — Port Spec of the "brain" (trade permission + sizing)

Source analysed (read-only, nothing executed, no `.pkl` loaded): `xau_alpha/xaubot/upstream/`
(VERSION file `0.2.8`, CHANGELOG up to 2026-02-11). All paths below are relative to `upstream/`.
`file:line` references point at the snapshot as it is on disk.

Scope: data pipeline, features, SMC, ML, HMM regime, entry gates, sizing/risk, reported results, bugs.
Entries/exits are NOT ported, but the SMC signal generator is documented because in the current code it is
the master signal that the gates act on.

---

## 0. TL;DR — what actually decides "trade allowed / how big" in the current `main_live.py`

1. Once per new M15 candle (detected within ~5 s of the bar opening), the bot computes features on the last
   200 M15 bars **including the bar that has just opened** (forming bar, a few seconds old).
2. The **SMC signal** (`src/smc_polars.py:698-918`) is the master. If there is no SMC signal, there is no trade.
3. The ML model is **not a gate** any more (CHANGELOG v0.2.4 "SMC-only"; `main_live.py:1952-2018`). It only
   (a) averages into the confidence when it agrees and (b) caps the lot via `min(final_conf, ml_conf)`.
4. Every SMC signal has confidence >= 0.60, so the `smc_conf < 0.55` gate (`main_live.py:1966`) can never fire.
   The dynamic-confidence threshold is computed but never compared with anything, and `AVOID` cannot happen
   (minimum score is 50). The regime filter can never fire either: SLEEP needs CRISIS, and CRISIS needs 4 HMM
   states while 3 are used.
5. So the gates that actually block trades are: flash-crash (2.5 % in 5 bars), RiskEngine (equity −3 %/day,
   ≥ 3 open positions), the session filter (WIB windows), a night spread cap of 80 points in 15:00–16:59 UTC,
   a 150 s cooldown (in practice a no-op, see §6), the SmartRisk daily (−5 % net realised) and total (10 %) stops,
   and a limit of 2 positions.
6. Lot size is always 0.01. It is 0.02 only when both confidences are ≥ 0.65, the regime is not high-volatility,
   the mode is normal, it is London or Golden, and it is before 22:00 WIB. The SL distance plays no part in
   sizing. There is no MICRO capital mode in the code.
7. The ML training pipeline has severe look-ahead leaks: OB columns, H1 `join_asof` on bar *open* time,
   whole-run counts, and a Viterbi-smoothed HMM. The high backtest win rates (72–82 %) are not reproduced live
   (42.9–56.7 % WR, about flat to negative P&L).

---

## 1. Data pipeline

### 1.1 Source and fetch
| Item | Value | Ref |
|---|---|---|
| Broker / feed | MetaTrader5 `copy_rates_from_pos(symbol, tf, 0, count)`; position 0 = **current forming bar** | `src/mt5_connector.py:427` |
| Columns | `time, open, high, low, close, spread, real_volume, volume` (`tick_volume` renamed to `volume`) | `src/mt5_connector.py:457-477` |
| Time | `pl.from_epoch(time, "s")`, i.e. **broker server time**, naive (not UTC) | `src/mt5_connector.py:471` |
| Symbol | `XAUUSD` | `src/config.py:163` |
| Execution TF | `M15` (capital ≤ $10k). **Switches to `H1` if capital > $10k** (MEDIUM mode) | `src/config.py:166, 228-273` |
| "Trend TF" | `H4` configured but **never used** for decisions (dashboard only) | `src/config.py:167`, `main_live.py:572` |

### 1.2 Bars used live
| Use | TF | Bars | Ref |
|---|---|---|---|
| New-candle detection (every 5 s) | M15 | 2 | `main_live.py:1133-1156` |
| Full analysis (features/SMC/ML/HMM/SMC signal) | M15 | **200** | `main_live.py:1451-1455` |
| Flash-crash check between candles | M15 | 5 | `main_live.py:1204-1218` |
| Fallback position mgmt | M15 | 50 | `main_live.py:1252-1256` |
| H1 bias + H1 V2 features (cached, refreshed every 4 M15 loops) | H1 | 100 | `main_live.py:952-969` |
| M5 | not used live (only `backtests/compare_h1_vs_m5.py`, `simple_h1_vs_m5.py`) | — | — |
| Tick (bid/ask) | current price, spread for night filter | — | `main_live.py:1523-1524, 1737-1739` |

### 1.3 Bars used for training
| Pipeline | M15 bars | H1 bars | Ref |
|---|---|---|---|
| `train_models.py` (V1, 37 features) | 15 000 (log: 2025-06-24 07:00 → 2026-02-11 03:45) | — | `train_models.py:234-239`, `training_output.log:12-13` |
| `src/auto_trainer.py` daily / weekend | 15 000 / 20 000 | `min(bars//4, 2000)` = 2000 | `src/auto_trainer.py:448-454, 520` |
| Model D (`backtests/backtest_36_ml_v2.py`) | 50 000 | 15 000 | `backtests/backtest_36_ml_v2.py:156-157` |
| ML V3 (`backtests/ml_v3/train_ml_v3.py`) | 50 000 (2023-12-27 → 2026-02-09) | 2000 | `backtests/ml_v3/training_log_v3_binary_final.txt` |

### 1.4 Alignment
- **M15 → H1**: `df_m15.join_asof(df_h1, on="time", strategy="backward")` (`backtests/ml_v2/ml_v2_feature_eng.py:123-128`).
  Both `time` columns are bar **open** times, so an M15 bar opening at hh:00 is joined to the H1 bar opening at
  hh:00, which closes at hh+1:00 (see the look-ahead note below).
- There is no explicit resampling. H1 comes from the broker directly.

### 1.5 Things computed on the forming candle (live) — look-ahead and skew flags
| What | Live behaviour | Training/backtest behaviour | Risk |
|---|---|---|---|
| Last M15 row (all features, SMC, ML input, HMM last state, flash-crash) | a bar that opened 0–10 s ago: O≈H≈L≈C | every row is a complete bar | **Train/serve skew.** `price_position`, `wick_ratio`, `body_ratio` become 0/0 → NaN → 0; `returns_1`≈gap; `normalized_range`≈0 |
| SMC `generate_signal` entry price | close of forming bar ≈ current price | close of completed bar i | backtest enters ~1 bar earlier with more information |
| H1 bias + H1 V2 features | last H1 row is the forming H1 bar; cache up to 4 M15 bars stale | H1 bar fully complete (closes up to 45 min **after** the M15 bar) | **Look-ahead in training** (H1 close up to `close[t+3]`) |
| Spread (night filter) | live tick `(ask-bid)/0.01` points | not modelled | not replayable exactly (use the bar `spread` column) |

Port rule: compute on **closed** bars only (row t−1) and decide at the open of bar t.

---

## 2. Feature engineering

### 2.1 Base indicators (`src/feature_eng.py`), applied by `calculate_all` in this order (`:36-61`)
Conventions: polars `ewm_mean(adjust=False)` corresponds to pandas `ewm(..., adjust=False).mean()`.
Polars `rolling_*(w)` uses min_periods=w and std uses ddof=1, as in pandas defaults.

| # | Column | Formula (pandas equivalent) | Ref |
|---|---|---|---|
| 1 | `rsi` | `d=close.diff(); g=where(d>0,d,0); l=where(d<0,-d,0)` (row0 → 0); `ag=g.ewm(alpha=1/14,adjust=False,min_periods=14).mean()`, same for `al`; `rsi = 100 if al==0 else 100-100/(1+ag/al)` | `:63-131` |
| 2 | `atr` | `TR=max(h-l, |h-c.shift()|, |l-c.shift()|)` (row0 = h-l); `ATR=TR.ewm(alpha=1/14,adjust=False,min_periods=14).mean()` (Wilder) | `:133-175` |
| 3 | `atr_percent` | `atr/close*100` | `:178-180` |
| 4-6 | `macd`, `macd_signal`, `macd_histogram` | `ema12-ema26` with `ewm(span,adjust=False)` (min_periods=1); signal = ema9 of macd; hist = macd−signal | `:188-244` |
| 7-11 | `bb_middle`, `bb_upper`, `bb_lower`, `bb_width`, `bb_percent_b` | `m=close.rolling(20).mean(); s=close.rolling(20).std()`; `m±2s`; `width=(up-lo)/m`; `%b=(c-lo)/(up-lo)` | `:246-302` |
| 12-15 | `ema_9`, `ema_21`, `ema_cross_bull`, `ema_cross_bear` | `ewm(span=9/21,adjust=False)`; `above=ema9>ema21`; bull = `above & ~above.shift().fillna(False)`; bear = `~above & above.shift().fillna(False)` | `:304-358` |
| 16-19 | `volume_sma`, `volume_ratio`, `volume_increasing`, `high_volume` | tick volume: `sma20`; `vol/sma20`; `vol>vol.shift()`; `ratio>1.5` | `:375-401` |
| (20-27) | `buy_volume`, `sell_volume`, `ofi_pseudo`, `ofi_trend`, `ofi_std`, `ofi_divergence`, `volume_momentum`, `toxicity` | OFI = `(buy-sell)/(buy+sell+1e-9)`, buy = vol if c>o, sell = vol if c<o; trend/std = rolling 20; div = ofi−trend; mom = `ratio/ratio.shift()-1`; toxicity = `|mom| + 2|div| + |spread/spread.rolling(20).mean()-1|`. **Added for exits ("Phase 4"). Not in Model D (the 76-count only works without them).** | `:403-481` |
| 28-31 | `returns_1`, `returns_5`, `returns_20`, `log_returns` | `c/c.shift(n)-1`; `ln(c/c.shift())` | `:507-515` |
| 32 | `price_position` | `(c-l)/(h-l)` (bar range, despite the comment "day's range") | `:518-523` |
| 33 | `dist_from_sma_20` | `c/c.rolling(20).mean()-1` | `:524-532` |
| 34-36 | `volatility_20`, `normalized_range`, `avg_normalized_range` | `log_returns.rolling(20).std()`; `(h-l)/c`; `((h-l)/c).rolling(14).mean()` | `:535-549` |
| 37-40 | `close_lag_1/2/3/5` | `c.shift(k)` (**raw price levels**) | `:552-557` |
| 41-44 | `higher_high`, `lower_low`, `hh_count_5`, `ll_count_5` | `h>h.shift()`, `l<l.shift()`; rolling sum 5 | `:560-578` |
| 45-48 | `hour`, `weekday`, `london_session`, `ny_session` | from **broker server time**: `hour`; polars ISO weekday 1=Mon..7=Sun; `8<=hour<16`; `13<=hour<21` | `:581-593` |

### 2.2 SMC columns appended by `SMCAnalyzer.calculate_all` (see §3)
`swing_high, swing_low, swing_high_level, swing_low_level, last_swing_high, last_swing_low, is_fvg_bull, is_fvg_bear, fvg_top, fvg_bottom, fvg_mid, fvg_signal, ob, ob_top, ob_bottom, ob_mitigated, bos, choch, market_structure`.

### 2.3 Regime columns (`src/regime_detector.py:426-473`)
`regime` (**raw HMM state index 0/1/2, arbitrary order per fit**), `regime_name` (string), `regime_confidence`
(posterior of the smoothed state).

### 2.4 V2 features (`backtests/ml_v2/ml_v2_feature_eng.py`), applied by `add_all_v2_features` (`:600-644`)
H1 inputs: the H1 df gets `FeatureEngineer.calculate_all(include_ml_features=False)` and `SMCAnalyzer.calculate_all`.
`atr` below is **M15 ATR** unless suffixed `_h1`.

| Column | Formula | Ref | Flag |
|---|---|---|---|
| `h1_ema20` | H1 `close.ewm(span=20,adjust=False)`, joined as-of | `:73-78` | look-ahead (H1 bar incomplete at M15 t) |
| `h1_market_structure` | H1 `market_structure` | `:131-136` | look-ahead |
| `h1_ema20_distance` | `(close - h1_ema20)/atr` | `:139-145` | **look-ahead: contains H1 close = `close[t+3]` for hh:00 bars** |
| `h1_trend_strength` | H1 `bos` of joined bar (−1/0/1), fill 0 (despite its name, not a count) | `:150-156` | look-ahead |
| `h1_swing_proximity` | `min(|lsh_h1-c|, |c-lsl_h1|)/atr` | `:159-176` | ok (swings lag 5 H1 bars) |
| `h1_fvg_active` | 1 if `fvg_bottom_h1 <= c <= fvg_top_h1` (non-null only when the joined H1 bar itself is an FVG bar) | `:179-190` | look-ahead |
| `h1_ob_proximity` | `|(ob_top_h1+ob_bottom_h1)/2 - c|/atr`; NaN (not null) except OB bars → model sees 0 | `:193-209` | **OB leak** |
| `h1_atr_ratio` | `atr_h1/atr`, fill 1.0 | `:212-217` | look-ahead |
| `h1_rsi` | H1 `rsi` | `:220-223` | look-ahead |
| `fvg_gap_size_atr` | `(fvg_top-fvg_bottom)/atr`, fill 0 | `:260-267` | ok |
| `fvg_age_bars` | bars since last `fvg_signal!=0` (row-index diff), fill 999 | `:270-296` | window-dependent (999 in a 200-bar live window) |
| `ob_width_atr` | `(ob_top-ob_bottom)/atr` (NaN→0) | `:299-306` | **OB leak** |
| `ob_distance_atr` | `|(ob_top+ob_bottom)/2 - c|/atr` (NaN→0, fill_null 999 never fires) | `:309-317` | **OB leak** |
| `bos_recency` | bars since last `bos!=0`, fill 999 | `:320-342` | ok |
| `confluence_score` | Σ over {`|ob|>0`, `|fvg_signal|>0`, `|bos|>0`, `|choch|>0`} of `rolling_sum(10, min_periods=1)` | `:346-387` | partial OB leak |
| `swing_distance_atr` | `= h1_swing_proximity` if present (it always is when H1 exists), else `min(|lsh-c|,|c-lsl|)/atr` fill 999 | `:391-408` | duplicate |
| `regime_duration_bars` | `count().over(run_id)`, where `run_id = cumsum(regime != regime.shift())`, i.e. the **full run length including future bars** | `:434-449` | **look-ahead**; and constant 1 live (see §9) |
| `regime_transition_prob` | `1/regime_duration_bars` | `:452-454` | same |
| `volatility_zscore` | `(atr - atr.rolling(50,min_periods=1).mean())/atr.rolling(50,min_periods=1).std()`, fill 0 | `:464-476` | ok |
| `crisis_proximity` | `atr/(2.5*atr.rolling(50,min_periods=1).mean())` | `:481-493` | ok |
| `wick_ratio` | `(|max(o,c)-h| + |l-min(o,c)|)/(h-l)` | `:521-535` | forming-bar skew |
| `body_ratio` | `|c-o|/(h-l)` | `:538-542` | forming-bar skew |
| `gap_from_prev_close` | `(o - c.shift())/atr`, fill 0 | `:552-559` | ok |
| `consecutive_direction` | dir = sign(c−o) ∈ {1,−1,0}; `count().over(run_id)` = **full run length incl. future bars** | `:562-589` | **look-ahead** (value 1 ⇒ next candle flips) |

### 2.5 Which features the models use
**V1 "37 features"** (`src/ml_model.py:489-528`, used by `train_models.py:138-139`), in this order:
`rsi, atr, atr_percent, macd, macd_signal, macd_histogram, bb_percent_b, bb_width, ema_9, ema_21, returns_1, returns_5, returns_20, log_returns, volatility_20, normalized_range, avg_normalized_range, price_position, dist_from_sma_20, higher_high, lower_low, hh_count_5, ll_count_5, volume_ratio, high_volume, swing_high, swing_low, fvg_signal, ob, bos, choch, market_structure, hour, weekday, london_session, ny_session, regime`.
In the V1 training log the top feature by gain is `ob` (214.0, vs 42.1 for the next one, `returns_1`), which is the OB leak
(`training_output.log:55`).

**Model D "76 features"** (the model `main_live.py` says it loads; `main_live.py:136-140, 326-338`). These are
"base" = every df column not in an exclusion set and not starting with `_` (`backtests/backtest_36_ml_v2.py:106-133`)
plus the 23 V2 features. Reported: base 53 + 23 = 76 (`backtests/36_ml_v2_results/ml_v2_summary_20260208_204507.txt`).
The reconstruction below matches the count exactly (inferred; the pickle was not inspected).
- Base 53: the 40 indicator columns of §2.1 rows 1-19 and 28-48 (OFI block excluded), plus
  `swing_high, swing_low, is_fvg_bull, is_fvg_bear, fvg_signal, ob, ob_mitigated, bos, choch, market_structure, regime, regime_confidence, h1_ema20`.
- Excluded: OHLCV, `spread`, `real_volume`, targets, `*_level`, `fvg_top/bottom/mid`, `ob_top/bottom`, `last_swing_*`, `regime_name`.
- V2 23: the list in `ml_v2_feature_eng.py:653-681`.
- It contains **raw price levels**: `ema_9, ema_21, bb_middle/upper/lower, close_lag_*, h1_ema20, atr, macd`. These are non-stationary, and gold went from ~2 600 to ~5 000 over the data.

**Auto-trainer** (`src/auto_trainer.py:540-546`): every numeric/bool column except
`{time, open, high, low, close, volume, target, tick_volume, spread, real_volume, multi_bar_target}`. This
**includes `target_return` (= the label's future return)** and the sparse `*_level/fvg_*` columns (see §9).

**ML V3 alternative** (`backtests/ml_v3/xgboost_model_v3_metadata.json`): 81 columns including `spread` and all
level columns. Trained but not wired by default.

---

## 3. SMC module (`src/smc_polars.py`)

Parameters: `swing_length=5`, `ob_lookback=10` (`src/config.py:41-43`, `main_live.py:108-111`).
`fvg_min_gap_pips=2.0` and `bos_close_break` are **unused**. `calculate_all` order: swings → FVG → OB → BOS/CHoCH
(`:212-226`). `calculate_liquidity_zones` (`:604-696`) is **never called** anywhere.

### 3.1 Swing points (`:315-403`), causal, flagged 5 bars late
```python
L = 5; w = 2*L + 1                       # 11
roll_max = high.rolling(w).max(); roll_min = low.rolling(w).min()
c_hi, c_lo = high.shift(L), low.shift(L)
swing_high = (c_hi == roll_max).astype(int)       # 1 on the CONFIRMATION bar i (pivot at i-5); NaN -> 0
swing_low  = -(c_lo == roll_min).astype(int)      # -1
swing_high_level = c_hi.where(swing_high == 1)    # pivot price
swing_low_level  = c_lo.where(swing_low == -1)
last_swing_high = swing_high_level.ffill(); last_swing_low = swing_low_level.ffill()
```
With ties, every equal high counts as a swing.

### 3.2 FVG (`:228-313`), causal, flagged on the 3rd candle
```python
is_fvg_bull = high.shift(2) < low ; is_fvg_bear = low.shift(2) > high      # rows 0-1: null -> no signal
fvg_top    = low  if bull else (low.shift(2)  if bear else NaN)
fvg_bottom = high.shift(2) if bull else (high if bear else NaN)
fvg_mid = (fvg_top + fvg_bottom)/2 ; fvg_signal = +1 bull / -1 bear / 0
```
There is no minimum gap size.

### 3.3 Order blocks (`:405-513`) — **retroactive write (look-ahead in any full-history use)**
```python
for i in range(10, n):
    if swing_low[i] == -1:                                   # confirmation bar of a swing low
        for j in range(i-1, max(0, i-10), -1):               # j = i-1 .. i-9
            if close[j] < open[j] and close[i] > high[j]:    # most recent bearish candle whose high is below close[i]
                ob[j] = 1; ob_top[j] = high[j]; ob_bottom[j] = low[j]; break
    if swing_high[i] == 1:
        for j in range(i-1, max(0, i-10), -1):
            if close[j] > open[j] and close[i] < low[j]:
                ob[j] = -1; ob_top[j] = high[j]; ob_bottom[j] = low[j]; break
ob_mitigated = (ob != 0) & (low <= ob_top) & (high >= ob_bottom)   # effectively == (ob != 0); ffill is a no-op (0 / NaN, not null)
```
The OB label sits on bar **j** but is only knowable at bar **i** (up to 9 bars later), and it encodes that
price later closed beyond the candle (bullish OB ⇒ price rose afterwards). Port fix: emit OB events at bar i
(store zone j), never back-fill j.

### 3.4 BOS / CHoCH / market structure (`:515-602`), causal state machine
```python
lsh = lsl = nan; trend = 0
for i in range(5, n):
    if swing_high[i] == 1: lsh = swing_high_level[i]
    if swing_low[i] == -1: lsl = swing_low_level[i]
    if not isnan(lsh) and close[i] > lsh:
        if trend == 1: bos[i] = 1
        elif trend == -1: choch[i] = 1
        trend = 1; lsh = nan                    # level consumed
    if not isnan(lsl) and close[i] < lsl:
        if trend == -1: bos[i] = -1
        elif trend == 1: choch[i] = -1
        trend = -1; lsl = nan
    market_structure[i] = trend                 # 0 until the first break
```
This is path-dependent on the window start: live state starts fresh on every 200-bar window, whereas training
runs over 15k–50k bars.

### 3.5 SMC signal + confidence (`:698-918`, `:80-136`): the master entry signal
Window = last 10 rows (live: includes the forming bar).
- `has_bull_break = 1 in bos[-10:] or 1 in choch[-10:]`; `has_bull_fvg = any(is_fvg_bull[-10:])`; `has_bull_ob = 1 in ob[-10:]` (mirror for bear).
- **BUY** if `(market_structure[-1]==1 or has_bull_break) and (has_bull_fvg or has_bull_ob)`; **elif SELL** mirror. BUY has priority.
- Entry = last close. ATR = last `atr`, replaced by 12.0 if None, ≤ 0 or > 5 % of price. SL = `min(last_swing_low (if < entry), entry-1.5*ATR)`. TP = entry + 1.5×risk (fixed RR 1.5; `_calculate_dynamic_rr` `:138-210` is **unused**).
- Confidence = 0.40 + 0.15·[structure == dir] + 0.12·[break] + 0.08·[fvg] + 0.10·[ob] + 0.10·[≥2 same-direction `bos` in last 20 rows], capped at 0.85. The weight `fresh_level=0.05` is unused.
  **Minimum possible = 0.60** (break+fvg) or 0.63 (structure+fvg), so the 0.55 gate downstream never blocks.

### 3.6 Which SMC outputs feed what
- ML features: `swing_high, swing_low, fvg_signal, ob, bos, choch, market_structure` (V1) plus `is_fvg_*`, `ob_mitigated` and the continuous ones (Model D).
- Gates: only via `generate_signal` (existence + confidence) and the pyramid rule (SMC conf ≥ 0.75).
- H1: the same analyzer on H1 gives V2 features. The H1 bias does **not** use SMC.

---

## 4. ML model

### 4.1 V1 — `train_models.py` + `src/ml_model.py`
- **Label**: `target = 1 if close[t+1]/close[t]-1 > 0 else 0` (lookahead 1, threshold 0.0); `target_return` = that return (`src/feature_eng.py:601-638`, `train_models.py:83`).
- **Data**: 15 000 M15 bars; HMM fitted on the same bars first, then `regime` added (`train_models.py:248-252`).
- **Split**: `drop_nulls`, then chronological: train = first `int(0.7·n)`, gap 50 bars, test = rest (`src/ml_model.py:125-154`, `train_models.py:150-157`).
- **XGBoost** (`src/ml_model.py:70-84`): `binary:logistic, eval_metric=auc, max_depth=3, learning_rate=0.05, tree_method=hist, min_child_weight=10, subsample=0.7, colsample_bytree=0.6, reg_alpha=1.0, reg_lambda=5.0, gamma=1.0, max_delta_step=1`; `num_boost_round=50, early_stopping_rounds=5` **on the test set**. No class weights, no calibration. NaN/inf → 0 (`nan_to_num`).
- **Log**: train 10 420 / test 4 417; early-stopped at iteration 6; Train AUC 0.6397 / Test 0.6113 (`training_output.log:49-52`).
- **BUG**: `walk_forward_train` (`src/ml_model.py:429-486`, called `train_models.py:167-174`) calls `self.fit(...)`, which **auto-saves over `models/xgboost_model.pkl`** (`:198-200`). The final saved model is the **last 500-bar fold** (50 rounds, no early stopping, test = 1 row). The log shows 290 "Model saved" lines. Walk-forward avg test AUC 0.5185 (`training_output.log:4144`).
- **BUG**: this V1 pickle has key `model`, but the live loader `TradingModelV2.load` reads `xgb_model` (`backtests/ml_v2/ml_v2_model.py:525`). A V1 file therefore loads as `fitted=True, xgb_model=None`, and `_predict_xgboost` returns **0.5 forever** (HOLD 50 %) (`:419-420`). The changelog's repeated "ML HOLD 50%" (v0.2.3–v0.2.5) is consistent with the ML being dead after `train_models.py` ran on 2026-02-11 (inference, not proven).

### 4.2 V2 "Model D" — what `main_live.py` expects (`backtests/backtest_36_ml_v2.py`, `backtests/ml_v2/*`)
- **Label** `multi_bar_target` (`ml_v2_target.py:36-147`), with k = 3 and th = 0.3·ATR(t):
  `up = max(c[t+1..t+3]) - c[t]`, `dn = c[t] - min(c[t+1..t+3])` (closes only, not highs/lows);
  `1 if up>th and up>dn; 0 if dn>th and dn>up; else null (dropped)`.
- **Data**: 50 000 M15 + 15 000 H1; HMM = the existing `models/hmm_regime.pkl` (`backtest_36_ml_v2.py:73-84`).
- **Split**: `drop_nulls` over features+target; 80 % train, 50-bar gap, 20 % test (`ml_v2_model.py:198-206`, `ml_v2_train.py:246-253`).
- **XGBoost** params = V1 params (`ml_v2_model.py:115-130`); `num_boost_round=100, early_stopping_rounds=10` on the test set.
- **Reported**: Train AUC 0.7385 / Test AUC 0.7339 (`36_ml_v2_results/RESULTS_SUMMARY.md`). Adding H1 alone gave +0.08 AUC (A 0.6253 → B 0.7064), which is the H1 look-ahead signature.
- **Pickled `confidence_threshold`** = constructor default **0.65** (`ml_v2_model.py:62`). `load()` overwrites the 0.60 passed in `main_live.py:137` (`ml_v2_model.py:528`).
- Other configs: Baseline/A/B/C/E (`ml_v2_train.py:260-345`). E (ensemble) = D's AUC.

### 4.3 Auto-trainer (`src/auto_trainer.py`) — runs inside the live bot
- Trigger (`:204-251`): ≥ 20 h since last; 05:00–05:29 WIB, or > 24 h since last, or never trained; plus "AUC < 0.65" (`:316-343`, ≥ 4 h apart). It is checked every 20 candles (`main_live.py:1163-1165`) and runs only if `session_filter.can_trade()` is False and there are no open positions (`main_live.py:2766-2779`).
- Pipeline: features → SMC → **V1 1-bar `target`** (not multi-bar) → HMM refit (auto-saves) → V2 features → `TradingModelV2(threshold 0.60)`, `fit(train_ratio=0.7, num_boost_round=50 (weekend 80), early_stopping_rounds=5)` (`:487-556`).
- **Feature auto-detection includes `target_return` (label leak) and the sparse `swing_*_level`/`fvg_top/bottom/mid` columns.** `drop_nulls` then needs rows where swing-high level, swing-low level and FVG are all non-null, which should leave far fewer than the 100-row minimum. So the XGB retrain **very likely fails** ("Insufficient data") while the **HMM is retrained and saved daily**. The HMM's state indices can permute, so the meaning of the `regime` feature drifts under a fixed XGB (inference from code; not executed).
- Rollback if new test AUC < 0.60 (`main_live.py:2811-2816`); backups keep the last 5.

### 4.4 Live inference and confidence (`ml_v2_model.py:337-415`)
`p_up = booster.predict(last_row)`. `BUY if p_up > thr; SELL if (1-p_up) > thr; else HOLD`.
`confidence = p_up | 1-p_up | max(p_up, 1-p_up)`. No calibration. Features are taken from the last (forming) row.
Missing features: `_get_available_features` filters to existing columns (`main_live.py:898-904`). A mismatch then
raises inside `xgb.DMatrix`/`predict`, which is not caught in `predict`, and becomes a loop error.

### 4.5 Confidence thresholds used live
| Threshold | Value | Effect | Ref |
|---|---|---|---|
| ML signal threshold | pickled value (0.65 Model D / 0.60 auto-trainer & V3-converted) | sets BUY/SELL/HOLD label only | `ml_v2_model.py:401-409` |
| SMC minimum | `smc_conf < 0.55` → no trade | never fires (min 0.60) | `main_live.py:1966` |
| ML agreement | if ML label == SMC dir: `conf = (smc + ml)/2`, else `conf = smc` (ML ignored) | modifier | `main_live.py:1972-1993` |
| London low-vol | session == "London" and `ATR/mean(ATR last 96) < 1.2` → ×0.90 | modifier | `main_live.py:1926-1950` |
| High-vol regime | ×0.90 | modifier | `main_live.py:1999-2000` |
| H1 bias | aligned ×1.05, opposed ×0.90 | modifier | `main_live.py:1687-1714` |
| Lot tier | `min(final_conf, ml_conf) ≥ 0.65` → 0.02; else 0.01 | sizing | `src/smart_risk_manager.py:794-807` |
| Pyramid | SMC ≥ 0.75 and ML label agrees | 2nd entry | `main_live.py:1363-1369` |
| ML reversal exit | ML opposite ≥ 0.65 | exits (`trend_reversal_threshold=0.65`) | `src/smart_risk_manager.py:2253` |

**Dynamic confidence** (`src/dynamic_confidence.py:63-187`, factory `:218-224`):
`score = 50 + session + regime + vol + trend + smc + ml`, clamped to 0..100.
- Session: "overlap"/"golden" +20, "london" +15, "new york"/"ny" +10, "asia"/"tokyo" 0, "closed"/"weekend" −30, else +5.
- Regime: medium +15, low +5, high −5, crisis −25.
- Session volatility: medium +10, low 0, high −5, extreme −10.
- Trend: in {uptrend, downtrend, strong_up, strong_down} +10, in {neutral, ranging, sideways} −5. Live passes the regime name, so 0.
- SMC present +10. ML ≥ 0.70 +5, ≥ 0.60 +2.
- Quality: ≥ 80 EXCELLENT (thr 0.60), ≥ 65 GOOD (0.65), ≥ 50 MODERATE (0.70), ≥ 35 POOR (0.80), else AVOID (0.85).
- Live only uses `quality == AVOID` → block (`main_live.py:1912-1915`). The minimum reachable score is 50, so this **never blocks**, and the threshold is **never compared** with any confidence. `get_entry_decision` (`:189-207`) is unused.

**SELL ≥ 75 % rule** (MONITORING-REPORT-2026-02-10.md §1; CHANGELOG v0.2.4 `:246-265`): "SELL only if ML label == SELL and
ML conf ≥ 0.75; BUY unrestricted". **It is not in the current code.** `main_live.py:1977-1981` says "SELL FILTER REMOVED
(v0.2.5d)". The backtest has a different rule: SELL needs ML == SELL and conf ≥ 0.55 (`backtests/backtest_live_sync.py:581-590`).
If reproduced: `if dir=="SELL" and not (ml_label=="SELL" and ml_conf>=0.75): block`.

### 4.6 V3 (alternative, `backtests/ml_v3/`)
- Triple barrier: +0.5·ATR / −0.5·ATR / 20 bars. **Upper barrier checked first** in each bar (BUY bias). At the time barrier, label = sign of the final return. The log says "Profit barriers hit: 0", a reporting bug.
- Labels 55.1 % BUY. Split per class, last 20 % of each class as test; train shuffled; train undersampled to 17 938 / 17 938.
- Optuna (30 trials) **selects on test accuracy**. Best: depth 4, lr 0.01046, 150 trees, mcw 6, gamma 0.382, subsample 0.822.
- Test accuracy 0.5642. Not loaded by `main_live` unless copied to `models/xgboost_model.pkl` (unknown).

---

## 5. Regime detector (`src/regime_detector.py`, v3)

### 5.1 Inputs: 8 features (`:134-212`)
```python
lr = ln(c/c.shift()); vol20 = lr.rolling(20).std(); vol100 = lr.rolling(100).std()
range_atr_ratio = (h - l)/atr                         # atr = Wilder ATR14 from feature_eng (else SMA14 TR)
trend_strength = |c.rolling(9).mean() - c.rolling(21).mean()| / atr
rsi_c = 100-100/(1+ SMA14(gain)/SMA14(loss)); rsi_deviation = |rsi_c-50|/50     # Cutler RSI, not Wilder
autocorr = (lr*lr.shift()).rolling(20).mean()
vol_regime = (atr - atr.rolling(100).mean())/atr.rolling(100).std()
X = dropna(rows) ; X = nan_to_num(nan=0, posinf=3, neginf=-3) ; StandardScaler (fit at train)
```
Live: 200 bars give ~87 usable rows after warm-up.

### 5.2 Model
- `GaussianHMM(n_components=3, covariance_type="diag", min_covar=1e-2, n_iter=500, tol=1e-4, init_params="smc", params="stmc")`, transmat init diag 0.90 / off-diagonal 0.05 (`:114-132`).
- Seeds 42, 59, 76, 93, 110 (`random_state + 17·k`). Selection score = loglik − 10000·[min state frac < 3 %] − 5000·[any covar > 100] − 3000·[diag_min < 0.5] (or −1000 if < 0.7) + 100·diag_mean − 500·[3 % ≤ min frac < 15 %] (`:245-298`).
- `lookback_periods=500` and `retrain_frequency=20` are stored but unused: fit uses the whole df.
- Mapping (`:374-395`): sort states by `means_[:,1]` (scaled vol20). Lowest → LOW_VOLATILITY, middle → MEDIUM, highest → HIGH. CRISIS exists only with 4 states.
- Last fit log: low 48.8 %, medium 41.5 %, high 9.8 %; avg duration 48.7 bars; diag min 0.951 (`training_output.log:21-36`).

### 5.3 Prediction (`:426-524`)
- `model.predict` = **Viterbi over the whole window** and `predict_proba` = forward-backward posterior. Both use future rows when run on history (**look-ahead in training/backtests**).
- Then smoothing (`:397-424`, `HMM_SMOOTHING_ENABLED` default on): any run shorter than 5 bars is replaced by the preceding regime (≤ 10 passes; the first run is exempt). At the live edge, a new regime is suppressed for its first 4 bars.
- `regime_confidence = posterior[t, smoothed_state]`.
- ATR fallback on the last 200 ATR values, polars quantile (`:526-570`):
  - if HMM = LOW and ATR ≥ P90 → HIGH;
  - elif HMM = LOW and ATR ≥ P75 → MEDIUM;
  - if HMM = HIGH and ATR ≤ P25 → LOW.
- Recommendation: LOW/MEDIUM → TRADE, HIGH → REDUCE, CRISIS → SLEEP.

### 5.4 How the regime is used
| Use | Rule | Ref |
|---|---|---|
| Veto | `recommendation == "SLEEP"` → skip; **unreachable** with 3 states. CRISIS check in combine is also unreachable | `main_live.py:1595-1605, 1917-1920` |
| Confidence | HIGH_VOLATILITY ×0.9 | `main_live.py:1999-2000` |
| Sizing | regime value in {high_volatility, crisis} → lot = 0.01 | `src/smart_risk_manager.py:810-812` |
| H1-bias weights | low/ranging vs high/trending vs medium weight sets (§6) | `main_live.py:1056-1095` |
| Dynamic-confidence score | ±points (§4.5); no effect | — |
| ML feature | raw `regime` int + `regime_confidence` | §2.3 |
| `get_position_multiplier` (1/1/0.5/0) | **unused** | `:584-595` |

---

## 6. Entry gates in `main_live._trading_iteration` (in order)

Clock: WIB = Asia/Jakarta = **UTC+7, no DST**. Sessions are fixed WIB windows, so they are fixed in UTC, and
London/NY DST shifts are **ignored**. ML `hour`/`*_session` features use broker server time instead.

| # | Gate (dashboard name) | Exact condition → action | Status | Replayable? |
|---|---|---|---|---|
| 0 | New-candle trigger | run once when the M15 `time[-1]` increases; checked every 5 s | timing | yes (bar open) |
| 1 | Flash Crash Guard | `|close[-1]/close[-5] - 1|·100 ≥ 2.5` over `df.tail(5)` (incl. forming bar) → **close all positions** and return (`main_live.py:1498-1513`, threshold `src/config.py:194`) | active | yes |
| 2 | Regime Filter | `recommendation == "SLEEP"` | **no-op** | yes |
| 3 | Risk Check (RiskEngine) | circuit breaker active, **or** `(equity - day_start_equity)/day_start_equity·100 ≤ -max_daily_loss` (3 % SMALL / 2 % MEDIUM; day = machine local date, start = first equity seen that day), **or** open positions (magic) ≥ `max_positions` (3 SMALL / 5 MEDIUM) (`src/risk_engine.py:74-166`) | active | needs equity simulation |
| 4 | Session Filter | table below; sets lot multiplier (`src/session_filter.py:224-261`) | active | yes |
| 5 | SMC Signal | §3.5; none → (blocked in 6) | active (master) | yes |
| 6 | Signal Combination | quality AVOID → none (unreachable); CRISIS → none (unreachable); `smc_conf<0.55` → none (unreachable); else build final conf (§4.5) (`main_live.py:1857-2018`) | effectively pass-through | yes |
| 7 | Direction Filter | key `direction_filter` absent in `data/filter_config.json`, so treated as enabled with default `["BUY","SELL"]` (`src/filter_config.py:85-87`) | **no-op** | — |
| 8 | H1 Bias (#31B) | never blocks; conf ×1.05 aligned / ×0.90 opposed | modifier | yes (forming-H1 caveat) |
| 9 | Time Filter (#34A) | `time_blocked = False` hard-coded (`main_live.py:1722`) | **no-op** | — |
| 9b | Night spread | WIB hour ≥ 22 or ≤ 5: `(ask-bid)/0.01 > 80` if session name contains "GOLDEN" else `> 50` → block (`main_live.py:1728-1760`). Hours 0–5 are already session-blocked, so effectively **15:00–16:59 UTC with an 80-point (= $0.80) cap** | active | approx. (bar `spread` column) |
| 10 | Trade Cooldown | `now - last_trade_time < 150 s` (`main_live.py:201, 1763-1779`; config's 300 s is unused) | ~no-op (one iteration per 900 s bar) | yes |
| 11 | Smart Risk Gate | `_update_state()` → `can_trade` False if total loss ≥ 10 % cap, net daily ≤ −5 % cap, or daily net ≥ `DAILY_PROFIT_TARGET` (env, default off) (§7) | active | yes (sim P&L) |
| 12 | Lot calc + multipliers | §7 | sizing | yes |
| 13 | Position Limit | `len(position_guards) ≥ 2` or `!can_trade` → skip (`src/smart_risk_manager.py:674-692`) | active | yes |
| 14 | Execute | broker SL = SMC SL (forced ≥ $1 from price, else 2·$1), TP = SMC TP; retry without SL on retcode 10016 (`main_live.py:2254-2292`) | — | — |

Dead or disabled code relevant to filters:
- Pullback filter `_check_pullback_filter` (`main_live.py:2020-2160`): disabled.
- `filter_config.json` keys `spread_check`, `ml_confidence`, `market_close_guard`: **never read**.
- Session `news_blackout_times` (`src/session_filter.py:129-136`): unused.

**Session table** (`src/session_filter.py:66-126, 142-261`). Matching order: Golden → Tokyo-London → dict order
Sydney, Tokyo, London. Danger zones are checked first.

| Name returned | WIB (inclusive minutes) | UTC | vol label | lot mult | trade? |
|---|---|---|---|---|---|
| Dead Zone (danger) | 00:00–03:59 | 17:00–20:59 | — | 0 | **no** |
| Rollover (danger) | 04:00–05:59 | 21:00–22:59 | — | 0 | **no** |
| Sydney | 06:00–13:00 | 23:00–06:00 | low | 0.5 | yes (aggressive mode special case) |
| Tokyo | 13:01–14:59 | 06:01–07:59 | medium | 0.7 | yes |
| Tokyo-London Overlap | 15:00–16:00 | 08:00–09:00 | high | 0.7 | yes |
| London | 16:01–19:59 | 09:01–12:59 | high | 1.0 | yes |
| London-NY Overlap (GOLDEN) | 20:00–23:59 | 13:00–16:59 | extreme | 1.2 (**not applied**, only mult < 1 is) | yes |
| Weekend | Sat+Sun WIB, whole days | Fri 17:00 → Sun 17:00 (then danger zones until Sun 23:00) | — | 0 | **no** |

So the effective trading week runs **Sun 23:00 UTC → Fri 16:59 UTC**, with a daily break **17:00–22:59 UTC**.
`is_friday_close` (Sat 04:30 WIB) is redundant: Saturday is already "weekend" (`:198-222`).

**H1 bias** (`main_live.py:935-1095`), refreshed when `loop_count - last ≥ 4`. Computed on 100 H1 bars with the
last row the forming H1 bar:
- signals ∈ {−1, 0, +1}:
  - `ema_trend = sign(close - ema21)`
  - `ema_cross = sign(ema9 - ema21)`
  - `rsi = +1 if rsi > 55, −1 if rsi < 45`
  - `macd = sign(hist)`
  - `candles = +1 if bullish (c > o) count of the last 5 H1 bars ≥ 3 else −1` (never 0, because bearish = 5 − bullish)
- Weights by current regime (`_last_regime.value`):
  - low/ranging: ema_trend 0.15, ema_cross 0.15, rsi 0.30, macd 0.25, candles 0.15
  - high/trending: 0.30, 0.25, 0.10, 0.25, 0.10
  - else: 0.25, 0.20, 0.20, 0.20, 0.15
- `score = Σ w·s`. BULLISH if score ≥ 0.3, BEARISH if ≤ −0.3, else NEUTRAL. Strength: ≥ 0.7 strong, ≥ 0.5 moderate.

**Pyramid (secondary entry)** (`main_live.py:1283-1440`), checked every ~5 s between candles. Conditions:
- 30 s since the last pyramid, `can_open_position()` true;
- session name ∈ {"London", "New York", "London-NY Overlap"}. Only "London" ever matches, because the returned names are "London-NY Overlap (GOLDEN)" and NY is never returned;
- position profit ≥ 0.5·(ATR·lot·100);
- guard velocity > 0, cached SMC same direction with conf ≥ 0.75, cached ML label agrees.

It then opens the same lot with the last signal's SL/TP. It **bypasses** the RiskEngine, session/night-spread and
cooldown gates.

**Modules that exist but are not wired into `main_live.py`:**
| Module | Logic | External data | Historical replay |
|---|---|---|---|
| `src/news_agent.py` (disabled: `main_live.py:69, 179-181`; "backtest proved it costs $178") | `_check_known_events` (`:206-248`) on machine time (assumed WIB): first Friday (day ≤ 7) 19–21 WIB = NFP; hard-coded FOMC dates 2025–Jul 2026 at 01–03 WIB; days 10–15, Tue–Thu, 19–21 WIB = "CPI". Any hit → `DANGER_NEWS` → no trade. Buffers 30/60 min declared but unused. Keyword sentiment ±0.3/keyword (`:250-322`) | NewsAPI optional (`:440-478`), never called live | calendar heuristic: yes (pure time rules) |
| `src/m5_confirmation.py` (only in `backtests/compare_h1_vs_m5.py`) | `momentum = 0.35·ema + 0.30·smc_net + 0.15·rsi + 0.10·macd + 0.10·candles`. ema ±1 if `ema9 ≷ ema21` and price beyond ema9; rsi ±0.5 (55/45); macd ±0.5; candles of the last 5 M5 bars: ≥ 4 bull 0.8, 3: 0.4, ≤ 1: −0.8, 2: −0.4. M5 trend BULL > 0.3, BEAR < −0.3. Aligned → conf `min(0.9, m15 + 0.15)`; neutral → keep; opposed → NEUTRAL 0.3 (`:55-245`). **Bug:** reads `bullish_ob/bos_bullish/...` columns that do not exist, so `smc_net` ≡ 0 | none | yes (M5 bars) |
| `src/macro_connector.py` (only tests/scripts) | Yahoo latest quote DXY `DX-Y.NYB`, VIX `^VIX`; FRED latest `DFII10`, `FEDFUNDS` (needs `FRED_API_KEY`); 4 h cache. `score = Σw·s/Σw` with DXY `(115-dxy)/20` w0.35, VIX `(vix-10)/30` w0.25, real yields `(3-ry)/4` w0.30, fed `(6-ff)/6` w0.10, each clipped to 0..1; < 0.3 bearish, > 0.7 bullish (`:227-311`) | **yes (internet)** | only with separately downloaded historical series (code fetches latest values only) |
| `src/kelly_position_scaler.py` | `p' = base_wr·(1 - 0.7·exit_conf)`, `kelly = 0.5·(p'·b - (1-p'))/b`, b = avg_win/avg_loss (defaults 0.55, 8, 4); < 0.25 full exit, < 0.70 partial | none | **exit-only**, not used for entry sizing (`src/smart_risk_manager.py:468-476, 1509-1567`) |

---

## 7. Position sizing and risk

### 7.1 Capital modes (`src/config.py:226-273`). **There is no MICRO or LARGE mode in the code.**
The 4-tier "MICRO < $500 → 2 %" table exists only in upstream `CLAUDE.md`/CHANGELOG; `docs/arsitektur-ai/17-Configuration.md` itself lists 2 modes.
| Mode | Condition (`CAPITAL` env, static) | risk_per_trade | max_daily_loss | leverage | max_positions | max_lot | exec TF |
|---|---|---|---|---|---|---|---|
| SMALL | capital ≤ 10 000 | 1.0 % | 3.0 % | 100 | 3 | 0.05 (`.env.example` `MAX_POSITION_SIZE=0.5` overrides it to 0.5) | M15 |
| MEDIUM | > 10 000 | 0.5 % | 2.0 % | 30 | 5 | 2.0 | **H1** |
`risk_per_trade` is **not used** by the live lot calculation. It is used only by the unused `RiskEngine.calculate_position_size` / `TradingConfig.calculate_position_size` / `validate_order`.

### 7.2 Live lot formula (`src/smart_risk_manager.py:699-822` + `main_live.py:1794-1823`)
Factory (`:2240-2256`): `capital = CAPITAL env` (static; `update_capital` is never called), `max_daily_loss_percent=5.0`,
`max_total_loss_percent=10.0`, `max_loss_per_trade_percent=0.5`, `emergency_sl_percent=2.0`, `base_lot=0.01`,
`max_lot=0.02`, `recovery_lot=0.01`, `trend_reversal_threshold=0.65`, `max_concurrent_positions=2`,
`daily_profit_target=env DAILY_PROFIT_TARGET or 0`.
```python
state = _update_state()                       # priority order:
#  total_loss >= 10%·cap                 -> STOPPED (persistent across days, file data/risk_state.txt)
#  (daily_profit - daily_loss) <= -5%·cap -> STOPPED (today)
#  daily_profit_target>0 and net>=target -> STOPPED
#  total_loss >= 0.8·10%·cap             -> PROTECTED, max_allowed=0.01
#  daily_loss >= 0.8·5%·cap (gross)      -> PROTECTED, max_allowed=0.01
#  consecutive_losses >= 3               -> RECOVERY,  max_allowed=0.01
#  else NORMAL, max_allowed=0.02
if not can_trade: lot = 0
eff = min(final_conf_after_H1_London_HV_mults, ml_conf)        # ml_conf = max(p,1-p): NOT direction-aware
lot = 0.02 if eff >= 0.65 else 0.01                             # (0.55-0.65 -> 0.01; <0.55 -> 0.01)
if regime in ("high_volatility","crisis"): lot = 0.01
lot = round(min(lot, max_allowed), 2)
if session_mult < 1: lot = max(0.01, round(lot*session_mult, 2))    # Sydney .5, Tokyo .7, Tokyo-London .7
if WIB hour >= 22 or <= 5: lot = max(0.01, round(lot*0.5, 2))
```
Result ∈ {0.01, 0.02}. 0.02 is only possible in London (09:01–12:59 UTC) or Golden before 22:00 WIB (13:00–14:59 UTC).
The **SL distance is never used**. With SMC SL ≥ 1.5·ATR(M15) (~$15–30 at gold ≈ $5 000), the dollar risk is about
$15–30 per 0.01 lot (1 lot = 100 oz, so $1 move = $1 per 0.01 lot).

Total/daily accounting (`:2118-2172`):
- loss → `daily_loss += |p|`, `total_loss += |p|`, `consecutive += 1`;
- win → `daily_profit += p`, `total_loss = max(0, total_loss - p)`, `consecutive = 0`.
- New day = machine `date.today()`; the state is reset in memory (`:605-616`), but `total_loss` persists in the file.
- **Only bot-initiated closes are recorded.** Broker SL/TP fills are silently unregistered as "stale" (`main_live.py:2430-2441`), so they never count toward the daily/total limits or the loss streak.

### 7.3 Per-trade loss caps (exit side, for context)
- Software max loss = 0.5 % of capital (`max_loss_usd`).
- "Emergency SL" = 2 % of capital → price distance `usd/(lot·10)·0.01` (`:631-672`). This is **10× too tight** (it assumes $0.10 per 0.01 move per 0.01 lot) and is **only logged**; the broker SL actually sent is the SMC SL.

### 7.4 MICRO account (< $500) behaviour, as coded
- Mode SMALL.
- RiskEngine stop at −3 % equity/day.
- SmartRisk stops at −5 % net realised/day and 10 % cumulative.
- Lot: 0.01 minimum, hard floor after every multiplier, no scaling down. At $100 the software max loss is $0.50 and the SMC SL risk is about $15–30 per trade (15–30 % of equity).
- There is no min-lot rejection when risk exceeds budget.
- **Port: replace with our own risk-based sizing (risk$ / (SL distance·100·lot)) with a skip-if-min-lot-too-risky rule.**

### 7.5 Unit bugs (unused paths, but don't copy them)
- `risk_engine.py:256-259` and `config.py:337-339`: XAU `pip=0.1`, `$1/pip/lot`. Real value is $10 per 0.1 per lot, so lots come out 10× too large (then ×0.5 "half-Kelly").
- `main_live.py:1836`: `risk_amount = lot·SL·10`, 10× too small (logging only).

---

## 8. Reported results

### 8.1 Data used by their backtests
- Real broker history: MT5 `FinexBisnisSolusi-Demo` (account 61045904), M15 (+ H1), 50 000 bars, about 2023-12 → 2026-02.
- Typical evaluation window 2025-08-01 → 2026-02-07.
- No spread, commission or slippage modelled.
- Exits are simulated on M15 OHLC.
- Naive broker time is treated as **UTC** before converting to WIB (`backtest_34_time_filter.py:213-215`, `backtest_live_sync.py:192-194`); trades even appear on "Sat" WIB.
- SMC/features are precomputed on the full df and then sliced (`backtest_34_time_filter.py:862-868`, `backtest_live_sync.py:912-918`), so the **OB look-ahead enters the SMC signal itself**.
- Models were trained on overlapping data (in-sample).

### 8.2 Backtest numbers (from `backtests/*_results/*.log`)
| Run | Trades | WR | Net P&L | PF | MaxDD | Notes |
|---|---|---|---|---|---|---|
| #01 SMC-only | 464 | 47.6 % | +$1 089 | 1.21 | 9.9 % | 2025-08-01 → 2026-02-07 |
| #01 SMC-only "synced" (SmartRisk+PosMgr exits) | 686 | 72.2 % | +$1 450 | 1.42 | 5.4 % | avg win $9.92 / avg loss $18.11; AVOID count 0 |
| #08 stoch_sell | 416 | 76.7 % | — | 1.76 | 2.8 % | |
| #24 final combined B | 739 | 80.4 % | +$2 235 | 1.77 | 3.4 % | Sharpe 2.87 |
| #31B (H1 EMA20 filter) | 625 | 81.8 % | +$2 807 | — | — | base for #34 |
| #34A skip WIB 9 & 21 | 614 | 82.6 % | +$3 162.64 | 2.43 | 2.4 % | Sharpe 4.41 (the time filter is now hard-disabled live) |
| #34 with Model D | 625 | 81.9 % | +$2 869 | 2.22 | 2.5 % | 614 tr / 82.7 % / $3 217 with skip-2h |
| #37 Model D test (107 tr, 2025-09-05 → 2026-02-06) | 107 | 65.4 % | +$408.59 | 2.58 | 0.36 % | Sharpe 5.97 |
| #38 same harness, V1 live 37-feature model | 107 | 43.9 % | +$19.37 | 1.04 | — | Sharpe 0.25 |
| v0.6.0_fixed 90 d | **0** | — | $0 | — | — | log: models missing + `AttributeError: 'SMCAnalyzer' object has no attribute 'analyze'` |
| CHANGELOG v0.1.1 claim "90 d, 338 trades" | 338 | — | +$595.16 (11.9 %) | 1.30 | — | Sharpe 1.29. **No supporting log in repo** |

`backtests/backtest_live_sync.py` claims "100 % identical to main_live.py" but is **not**:
- loads the V1 `TradingModel` (`:175`);
- ML threshold 0.50 (`:568-570`) and strong-disagreement block > 0.65 (`:572-579`);
- SELL filter ML ≥ 0.55 (`:581-590`);
- 2-bar signal persistence keyed on `f"{dir}_{int(entry_price)}"` (`:592-621`);
- pullback filter on;
- cooldown 10 bars;
- different session map, including 00–04 WIB tradable (`:186-209`);
- simple exits with max loss $50.

### 8.3 Live / demo numbers
| Source | Trades | WR | Net | Avg win / loss | Notes |
|---|---|---|---|---|---|
| ANALYSIS-FEB10-TRADES.md (2026-02-10 11:15–23:54) | 42 | 42.9 % | **−$97.78** | $5.04 / $7.85 | worst −$34.70; 19:00–23:59: 11 tr, 27.3 % WR, −$104.16. Most entries are time-stamped 2–5 s after a bar open (22:00:03, 22:15:05, 22:30:02…), consistent with forming-bar entry |
| MONITORING-REPORT-2026-02-10.md (same day, 22:18 WIB) | 60 | 56.7 % | +$5.78 | $5.95 / $7.55 | SELL 19 tr 57.9 % "after" vs 34 tr 41.2 % −$67.05 "before" the SELL ≥ 75 % rule (same-day, uncontrolled). **Contradicts** the Feb-10 analysis |
| CHANGELOG v0.2.2 | — | 76 % | — | avg loss 2× avg win (R:R 0.49) | |
| CHANGELOG v0.2.5 | 2 | 0 % | −$17.52 in 8 min | — | Golden session, SMC 63 % / ML HOLD 50 % |
| CHANGELOG baseline metrics | — | 56–58 % | — | avg win $2.78 | |
Account balance about $5 469 (demo) on 2026-02-11 (`training_output.log:10`).

Changes over time (CHANGELOG):
- v0.2.2 London filter blocks if ML < 0.70.
- v0.2.3 penalty instead of block; 3-tier SMC/ML.
- v0.2.4 SMC-only + SELL ≥ 75 %.
- v0.2.5d SELL filter removed.
- v0.2.5–0.2.8 exit/grace/trajectory tweaks.
- Night lot ×0.5 + spread cap (v6.1).

Conclusion: backtest WRs of 72–82 % are look-ahead-inflated. The live 43–57 % WR with avg loss > avg win is the
realistic picture.

---

## 9. Bugs, look-ahead risks, inconsistencies (ranked by impact on a port)

1. **OB look-ahead** (`smc_polars.py:445-471`): `ob[j]` is set at confirmation bar i ≤ j+9. This leaks into
   `ob, ob_mitigated, ob_width_atr, ob_distance_atr, confluence_score, h1_ob_proximity` and into the SMC signal in
   every precomputed backtest. `ob` is the #1 V1 feature (gain 214 vs 42). Live, the predicted row always has ob = 0.
2. **H1 as-of join on open time** (`ml_v2_feature_eng.py:123-128`): an M15 bar at hh:00 receives the H1 bar that
   closes at hh+1:00, i.e. `close[t+3]`, which equals the 3-bar label horizon. This explains "+0.08 AUC from H1".
   Fix: join on H1 **close** time (H1 bar available from hh+1:00), and use only completed H1 bars live.
3. **Whole-run counts** (`regime_duration_bars`, `regime_transition_prob`, `consecutive_direction`) use
   `count().over(group)`, so the future part of the run leaks. Live, `regime_duration_bars` is always 1 because V2
   features are built **before** `regime` exists (`main_live.py:1472` vs `:1476`).
4. **HMM Viterbi + forward-backward + smoothing over the whole history** gives look-ahead in the `regime` and
   `regime_confidence` training features and in backtests. The live edge lags 4 bars on new regimes. The raw state
   index is used as a feature, and its meaning changes after each (daily) HMM refit.
5. **Forming-bar inference**: live features/SMC/ML are computed on a bar seconds old; training uses complete bars.
6. **Auto-trainer leak/failure**: `target_return` is included as a feature (`auto_trainer.py:541-546`). The sparse
   level columns make `drop_nulls` empty, so XGB retrain very likely fails while the HMM is overwritten.
7. **Model-format mismatch**: a V1 pickle loaded by V2 gives constant p = 0.5. `train_models.py` was run on
   2026-02-11 (V1 format). The ML was likely dead live; "ML HOLD 50 %" appears throughout the CHANGELOG.
8. **`walk_forward_train` overwrites the saved model** with the last 500-bar fold (`ml_model.py:450-457` + `:198-200`).
9. **Early stopping on the test set** (V1/V2) and **Optuna on test accuracy** (V3) make the reported test scores optimistic.
10. **Gates that cannot fire**: SMC < 0.55 (min 0.60), dynamic AVOID (min score 50), regime SLEEP/CRISIS (3
    states), time filter (`False`), direction filter (missing key), cooldown 150 s (< 1 bar), the 50-point night
    spread branch (00–05 WIB already blocked), pyramid sessions "New York"/"London-NY Overlap" (name mismatch).
11. **ML confidence not direction-aware for sizing**: `min(final_conf, max(p,1-p))`, so a strongly opposing ML can raise the lot to 0.02.
12. **Risk accounting misses broker SL/TP fills** (stale-guard cleanup), and SmartRisk capital is the static `CAPITAL` env, not the balance.
13. **Time zones**:
    - ML time features use broker server time.
    - Backtests treat broker time as UTC, while live uses the true WIB clock. Session P&L tables in the backtests are therefore shifted by the broker offset.
    - WIB sessions ignore London/NY DST.
14. **Raw-price features** (`ema_*`, `bb_*`, `close_lag_*`, `h1_ema20`, `atr`, `macd`) are non-stationary over a 2 600 → 5 000 price range.
15. **Doc/code drift**:
    - "37 features" vs 76 vs 81;
    - "11/12/14 filters";
    - MICRO/LARGE modes don't exist;
    - SELL ≥ 75 % documented but removed;
    - `backtest_live_sync.py` not synced;
    - 338-trade claim has no log;
    - the two Feb-10 reports contradict each other.
16. Minor:
    - `m5_confirmation` reads non-existent columns;
    - `macro_connector` component labels are index-based (mislabelled if DXY is missing);
    - XAU contract math 10× off in `risk_engine`, `config` and the emergency SL;
    - `ob_*` NaN (not null) defeats `fill_null(999)`;
    - `price_position`/`wick_ratio`/`body_ratio` produce 0/0 on doji/forming bars;
    - the `fvg_age_bars`/`bos_recency` sentinel 999 depends on window length (200 live vs 15–50k training);
    - the V3 triple barrier checks the upper barrier first (BUY bias), and its log says "profit barriers hit: 0".

---

## 10. Port notes (minimal, for our pandas/numpy engine)

- **Bar discipline**: compute everything on closed bars; decision time = open of bar t, using rows ≤ t−1. H1/H4
  features must use bars whose **close time** ≤ the decision time.
- **Worth porting (causal versions)**:
  - swings (§3.1), FVG (§3.2), BOS/CHoCH/structure (§3.4);
  - OB, re-timed to the confirmation bar;
  - the SMC confidence formula (§3.5) as a permission/confluence score;
  - the WIB session table converted to UTC (§6);
  - the H1 bias score (§6) on completed H1 bars;
  - the HMM regime with a **forward-only filter** (`predict_proba` on a growing window, take the last row) and named labels instead of raw state ids;
  - the ATR-percentile fallback;
  - flash-crash (2.5 %/5 bars);
  - the RiskEngine daily equity stop and the SmartRisk daily/total/consecutive-loss modes (§7.2), with all fills recorded.
- **Not worth porting as-is**: their trained models (leaky features, possibly dead in live), the dynamic-confidence
  manager (no effect), the news/macro/M5 modules (not wired; news is a crude calendar heuristic), and their lot sizing
  (fixed 0.01/0.02, SL-agnostic).
- **If an ML gate is wanted**:
  - retrain on causal features only: §2.1 minus raw price levels; SMC as causal events; H1 joined on close time; regime from a forward filter; no whole-run counts;
  - label = multi-bar (§4.2) or triple-barrier;
  - walk-forward with a purge gap ≥ label horizon;
  - early stopping on a validation block separate from the test block;
  - calibrate probabilities (isotonic/Platt) before using thresholds such as 0.60/0.65/0.75.
