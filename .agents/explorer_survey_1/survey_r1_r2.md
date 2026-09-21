# Architecture B Survey: R1 (Data Layer & Feature Engine) & R2 (Deterministic Alpha Setups)

**Author**: Explorer 1 (Orderflow and Alpha Explorer)  
**Date**: 2026-09-16  
**Status**: Comprehensive Survey Complete  
**Scope**: Requirements R1 & R2 from `ORIGINAL_REQUEST.md`

---

## Executive Summary

This investigation surveys the complete codebase of the autonomous trading engine (`/Users/mac/Desktop/TBT-Engine`) to evaluate existing capabilities, identify architectural gaps, and construct the implementation blueprint for:
1. **R1: Continuous Timeframe-Agnostic Data Layer & Feature Engine** (Event-driven bars, 5-level OFI with rolling z-scores, tick-level CVD & divergences, real-time OI contraction monitoring, Hawkes trade intensity with rolling median excitation, Mark vs Mid and Perp vs Spot basis spreads, and L2 1% depth imbalance clustering).
2. **R2: Deterministic Mechanical Alpha Setups** (5 deterministic setups: OFI VWAP Reversion, Liquidation Cascade Absorption, Hawkes Volatility Breakout, CVD Divergence Sweep, and L2 Depth Imbalance Scalp with strict mathematical entry formulas, exact stop-losses, and hard invalidation rules).

### Key Architectural Discovery
- The repository contains an operational HFT skeleton in `quant/hft/` (with initial Hawkes, rudimentary OFI, orderbook reconstruction from Hyperliquid WebSocket, and Avellaneda-Stoikov / Kelly risk models) and a legacy multi-strategy framework in `scalper/` and root (`live_hyperliquid.py`).
- However, **none of the event-driven bar structures (Range, Volume, Tick bars) currently exist**.
- The existing OFI implementation in `quant/hft/alpha/signal_engine.py` is unnormalized (lacks rolling z-score normalization) and uses a heuristic delta formula rather than the Cont-Kukanov-Stoikov Level-2 formulation.
- Cumulative Volume Delta (CVD) exists only as an approximate candle-level heuristic ($\text{sign}(C - O) \times V$) in `scalper/engine.py:657`; trade-by-trade volume delta accumulation and divergence detection are completely missing.
- Open Interest (OI) tracking is currently only loaded post-facto from daily/hourly CSVs; tick-window contraction monitoring ($>2.5\%$ drop in $<300$ ticks) does not exist in any live path.
- Hawkes process exists in `quant/hft/alpha/hawkes.py`, but lacks rolling median tracking and the excitation ratio calculation vs median.
- Basis spreads (Mark vs Mid, Perp vs Spot) and L2 1% depth cluster scanning are entirely unbuilt.
- None of the 5 candidate alpha setups from R2 are implemented.

Below is the exhaustive mapping, mathematical formulation, gap analysis, and proposed implementation plan.

---

## 1. Codebase Inventory & Current Capabilities

| Component / Requirement | Existing File / Class Location | Current Capabilities & Limitations | Gap Status |
|---|---|---|---|
| **L2 Order Book Reconstruction** | `quant/hft/data_feed/orderbook.py`<br>`OrderBook`, `OFISnapshot` | Rebuilds bids/asks from Hyperliquid L2 snapshots via `SortedDict`. Computes spread in bps, microprice, and depth VWAP. | **Partial**. Lacks top 1% volume cluster detection, depth imbalance ratio within 0.1%, and multi-level price-level change tracking. |
| **Hyperliquid WS Feed** | `quant/hft/data_feed/ws_client.py`<br>`HyperliquidFeed` | Subscribes to `l2Book` and `trades` on `wss://api.hyperliquid.xyz/ws`. Dispatches `on_book_update` and `on_trade`. | **Partial**. Does not subscribe to or poll `activeAssetCtx` / `metaAndAssetCtxs` for real-time Open Interest and Oracle (Spot) prices. |
| **Order Flow Imbalance (OFI)** | `quant/hft/alpha/signal_engine.py`<br>`OFIFeatureEngine.compute_ofi_vector()` | Computes 5-level OFI as $(dB_k - dA_k) / (|dB_k| + |dA_k| + \epsilon)$ averaged over static window. | **Major Gap**. No rolling z-score normalization ($z = (OFI - \mu)/\sigma$). Formula does not account for price level changes between LOB ticks. |
| **Hawkes Process** | `quant/hft/alpha/hawkes.py`<br>`HawkesProcess`, `MultiHawkes` | Exponential decay kernel $\lambda(t) = \mu + \alpha R(t)$, recursive update $R(t_n) = e^{-\beta \Delta t}(1 + R_{n-1})$. | **Partial**. Compares intensity against static $\mu$ multiplier; lacks rolling window buffer of intensities and excitation ratio vs rolling median ($\lambda / \lambda_{\text{median}}$). |
| **Bar Aggregation** | `scalper/market_data/store.py`, `live_hyperliquid.py` | Relies strictly on fixed-time candles (1m, 5m, 15m) fetched via REST API or stored in Parquet/CSV. | **Missing**. No Range Bars, Volume Bars, or Tick Bars exist. |
| **Cumulative Volume Delta (CVD)** | `scalper/engine.py:651-665` | Approximate candle delta: $\text{sign}(C - O) \times V$. In `quant/hft/engine.py:412-422`, `_on_trade` drops trade size and does not accumulate delta. | **Missing**. No trade-level taker buy/sell volume accumulation, no running CVD series, no divergence detection algorithm. |
| **Open Interest Monitoring** | `quant/lib/features.py:164-180`, `tools/asia_report.py:44` | Reads static historical `sum_open_interest` for backtests; queries `info.meta_and_asset_ctxs()` once on startup. | **Missing**. No continuous tick-window buffer ($<300$ ticks) tracking percentage OI contraction or liquidation cascade signatures. |
| **Basis Spreads** | `quant/hft/data_feed/orderbook.py:121` | Only top-of-book bid-ask spread `(ask - bid) / mid * 10000` is computed. | **Missing**. Mark vs Mid bps and Perp vs Spot bps are completely uncomputed. |
| **Alpha Setups (R2)** | `quant/strategies/`, `scalper/strategies/` | Legacy strategies: `ict_sniper.py`, `vp.py`, `breakout.py`, `impulse.py`, `pullback.py`, `sweep.py`. | **Missing**. None of the 5 specified Architecture B alpha setups exist. |

---

## 2. Requirement R1: Continuous Timeframe-Agnostic Data Layer & Feature Engine

### 2.1 Event-Driven Bar Structures
To liberate trade execution from arbitrary chronological clock slicing, R1 mandates three event-driven bar generators:

#### 1. Range Bars (Price-driven sampling)
- **Mathematical Specification**:
  A new bar $b_k$ is closed whenever the price range spans a fixed threshold $\Delta P$ (or dynamic tick threshold based on coin tick size):
  $$\text{Range}_k = \text{High}_k - \text{Low}_k \ge \Delta P$$
  - When $\text{Price}_t > \text{Low}_k + \Delta P$: $\text{High}_k = \text{Low}_k + \Delta P$, bar $b_k$ closes with $\text{Close}_k = \text{High}_k$. A new bar $b_{k+1}$ opens with $\text{Open}_{k+1} = \text{Close}_k$.
  - When $\text{Price}_t < \text{High}_k - \Delta P$: $\text{Low}_k = \text{High}_k - \Delta P$, bar $b_k$ closes with $\text{Close}_k = \text{Low}_k$. A new bar $b_{k+1}$ opens with $\text{Open}_{k+1} = \text{Close}_k$.
  - *Invariant*: Every closed Range Bar has identical high-to-low spread $\Delta P$, effectively eliminating volatility clustering in the price dimension.

#### 2. Volume Bars (Activity-driven sampling)
- **Mathematical Specification**:
  Trades are aggregated until cumulative traded volume equals or exceeds target volume $V_{\text{target}}$:
  $$\sum_{i=1}^{N_k} v_i \ge V_{\text{target}}$$
  - Once threshold is breached, bar $b_k$ closes. Bar metrics include: $\text{Open} = p_1$, $\text{High} = \max(p_i)$, $\text{Low} = \min(p_i)$, $\text{Close} = p_{N_k}$, $\text{Volume} = \sum v_i$, $\text{VWAP} = \frac{\sum p_i v_i}{\sum v_i}$, $\text{BuyVolume} = \sum_{side=buy} v_i$, $\text{SellVolume} = \sum_{side=sell} v_i$, $\text{Delta} = \text{BuyVolume} - \text{SellVolume}$.
  - *Invariant*: Each bar represents an equal quantum of market liquidity, accelerating during high-activity institutional rushes and slowing during dead hours.

#### 3. Tick Bars / Charts (Information-driven sampling)
- **Mathematical Specification**:
  A new bar is finalized every $N_{\text{ticks}}$ trade transactions:
  $$\text{Count}(\text{trades}) = N_{\text{ticks}} \quad (\text{e.g. } 50, 100, 300 \text{ ticks})$$
  - Each bar represents a constant number of trade decisions/arrivals, normalizing for discrete market order arrival frequency.

---

### 2.2 Multi-Level Order Flow Imbalance (OFI) with Rolling Z-Score Normalization

#### Standard Cont-Kukanov-Stoikov 5-Level Formulation
Let $(P_{k}^b(t), Q_{k}^b(t))$ and $(P_{k}^a(t), Q_{k}^a(t))$ denote the bid and ask prices and sizes at depth level $k \in \{0, 1, 2, 3, 4\}$ at time $t$.
The order flow contribution at level $k$ from time $t-1$ to $t$ is defined as:

$$\Delta B_k(t) = \begin{cases} 
Q_k^b(t) & \text{if } P_k^b(t) > P_k^b(t-1) \\
Q_k^b(t) - Q_k^b(t-1) & \text{if } P_k^b(t) = P_k^b(t-1) \\
-Q_k^b(t-1) & \text{if } P_k^b(t) < P_k^b(t-1)
\end{cases}$$

$$\Delta A_k(t) = \begin{cases} 
-Q_k^a(t) & \text{if } P_k^a(t) < P_k^a(t-1) \\
Q_k^a(t) - Q_k^a(t-1) & \text{if } P_k^a(t) = P_k^a(t-1) \\
Q_k^a(t-1) & \text{if } P_k^a(t) > P_k^a(t-1)
\end{cases}$$

$$OFI_k(t) = \Delta B_k(t) - \Delta A_k(t)$$

Composite multi-level OFI across all 5 levels:
$$OFI_{\text{multi}}(t) = \sum_{k=0}^{4} w_k OFI_k(t) \quad \text{where } w_k = \frac{1}{k+1} \text{ or } w_k = 0.2$$

#### Rolling Z-Score Normalization
Raw OFI values scale with coin liquidity and volatility. To enable stationary, scale-invariant signal thresholds across coins, maintain a rolling buffer of length $W$ ticks (e.g. $W = 100$ ticks):
$$\mu_{OFI}(t) = \frac{1}{W} \sum_{i=0}^{W-1} OFI_{\text{multi}}(t-i)$$
$$\sigma_{OFI}(t) = \sqrt{\frac{1}{W-1} \sum_{i=0}^{W-1} (OFI_{\text{multi}}(t-i) - \mu_{OFI}(t))^2}$$
$$z_{OFI}(t) = \frac{OFI_{\text{multi}}(t) - \mu_{OFI}(t)}{\sigma_{OFI}(t) + \epsilon}$$

*Signal Properties*:
- $z_{OFI} > +0.8\sigma$ confirms strong net institutional buying pressure.
- $z_{OFI} < -0.8\sigma$ confirms strong net institutional selling pressure.
- Sign reversal: transition from negative $z_{OFI}$ to positive $z_{OFI} > +0.8\sigma$ indicates aggressive buyers absorbing supply.

---

### 2.3 Cumulative Volume Delta (CVD) Tracking & Divergence Detection

#### Tick-Level CVD Accumulation
For every incoming trade $j = (p_j, q_j, \text{side}_j)$:
$$\delta_j = \begin{cases} +q_j & \text{if } \text{side}_j \in \{\text{'buy'}, \text{'b'}\} \text{ (taker buy)} \\ -q_j & \text{if } \text{side}_j \in \{\text{'sell'}, \text{'s'}\} \text{ (taker sell)} \end{cases}$$
$$CVD_t = CVD_{t-1} + \delta_j$$

#### Bar-Level CVD Series & Divergence Logic
On each event-driven bar $b_m$ (Range, Volume, or Tick bar), record:
- $\text{PriceLow}_m = \text{Low}(b_m)$, $\text{PriceHigh}_m = \text{High}(b_m)$
- $CVD_m = CVD(\text{at bar close})$
- $Vol_m = \text{Volume}(b_m)$

Over a lookback window of $K = 10$ bars:
1. **Bullish CVD Divergence (Sweep Absorption)**:
   - Price prints a Lower Low: $\text{PriceLow}_t < \min_{i=1}^{9} \text{PriceLow}_{t-i}$
   - CVD prints a Higher Low: $CVD_t > \min_{i=1}^{9} CVD_{t-i}$
   - Relative Volume Confirmation: $Vol_t > 1.5 \times \frac{1}{10}\sum_{i=1}^{10} Vol_{t-i}$
   - *Interpretation*: Market sellers pushed price to a new low, but net aggressive selling was substantially less than the previous swing low; passive limit bids absorbed the sell pressure, forecasting an imminent sharp upward expansion.
2. **Bearish CVD Divergence**:
   - Price prints a Higher High: $\text{PriceHigh}_t > \max_{i=1}^{9} \text{PriceHigh}_{t-i}$
   - CVD prints a Lower High: $CVD_t < \max_{i=1}^{9} CVD_{t-i}$
   - Relative Volume Confirmation: $Vol_t > 1.5 \times \overline{Vol}_{10}$

---

### 2.4 Open Interest (OI) Contraction Monitoring

In perpetual futures markets, forced liquidations and stop-loss cascades trigger rapid unwinding of open contracts.
- **Data Source**: Hyperliquid API returns live `openInterest` and `markPx` via `metaAndAssetCtxs` / `activeAssetCtx`.
- **Rolling Ring Buffer**: Maintain a rolling FIFO buffer of the last $N_{\text{max}} = 300$ ticks containing tuples:
  $$\tau_i = (t_i, \text{tick\_id}_i, OI_i, \text{Mark}_i, \text{TakerSellVol}_i, \text{TotalVol}_i)$$
- **Contraction Metric**:
  Within the sliding window $\le 300$ ticks:
  $$\Delta OI_{\%} = \frac{OI_{\text{curr}} - \max_{i \in [T-300, T]} OI_i}{\max_{i \in [T-300, T]} OI_i} \times 100\%$$
- **Trigger Rule**: If $\Delta OI_{\%} \le -2.5\%$ within $\le 300$ ticks, signal an **OI Contraction Alert**.
  When combined with Mark drop $>1.5\%$ and taker sell ratio $>85\%$, this forms the prerequisite cascade state for Alpha Setup 2.

---

### 2.5 Hawkes Trade Arrival Intensity & Excitation Ratio vs Median

#### Continuous Intensity with Exponential Decay
From `quant/hft/alpha/hawkes.py`:
$$\lambda(t) = \mu + \alpha R(t)$$
Between events:
$$R(t) = e^{-\beta(t - t_{\text{last}})} R_{\text{last}}$$
At event arrival $t_n$:
$$R(t_n) = e^{-\beta(t_n - t_{n-1})}(1 + R(t_{n-1}))$$

#### Rolling Median Tracking & Excitation Ratio
To evaluate whether arrival intensity is in an anomalous, institutional surge regime:
- Maintain a rolling deque of evaluated intensities $\Lambda = \{\lambda(t_{-w}), \dots, \lambda(t)\}$ over a 300-tick window.
- Calculate rolling median:
  $$\lambda_{\text{median}} = \text{Median}(\Lambda)$$
- Calculate **Excitation Ratio**:
  $$\text{ER}(t) = \frac{\lambda(t)}{\lambda_{\text{median}} + \epsilon}$$
- An excitation ratio $\text{ER}(t) > 3.0$ identifies a statistically significant clustering of orders (>3x typical activity), satisfying the trigger for Alpha Setup 3.

---

### 2.6 Mark vs Mid and Perp vs Spot Basis Spreads

- **Definitions**:
  - $\text{Mid Price} = \frac{\text{BestBid} + \text{BestAsk}}{2}$
  - $\text{Mark Price} = \text{Hyperliquid mark price from asset context}$
  - $\text{Spot Price} = \text{Hyperliquid oracle price / spot index}$
- **Formulas in Basis Points (bps)**:
  $$\text{Spread}_{\text{Mark-Mid}} = \frac{\text{Mark} - \text{Mid}}{\text{Mid}} \times 10{,}000 \text{ bps}$$
  $$\text{Spread}_{\text{Perp-Spot}} = \frac{\text{Mid}_{\text{perp}} - \text{Spot}}{\text{Spot}} \times 10{,}000 \text{ bps}$$
- **Role in Engine**:
  Large deviations in $\text{Spread}_{\text{Mark-Mid}}$ identify liquidation dislocation and toxic orderflow. Extreme $\text{Spread}_{\text{Perp-Spot}}$ identifies funding-rate premium/discount extremes.

---

### 2.7 Level 2 Book Depth Imbalances (Top 1% Volume Clusters)

Hyperliquid L2 feed exposes up to 50 levels of bids and asks.
- **Algorithm**:
  1. Extract all bid levels $\{(p_i^b, q_i^b)\}$ and ask levels $\{(p_j^a, q_j^a)\}$.
  2. Compute 99th percentile (top 1%) volume threshold $Q_{99} = \text{Percentile}(\{q_i^b\} \cup \{q_j^a\}, 99)$.
  3. Identify qualifying cluster price $P_{\text{cluster}}$ where $q \ge Q_{99}$.
  4. Proximity constraint: check if cluster is within $0.1\%$ of current mid price:
     $$\frac{|P_{\text{cluster}} - \text{Mid}|}{\text{Mid}} \le 0.001$$
  5. Within a $0.1\%$ price window centered at $P_{\text{cluster}}$, compute total bid volume $V_{\text{bid\_cluster}}$ and total ask volume $V_{\text{ask\_cluster}}$.
  6. **Depth Imbalance Ratio**:
     $$\text{DIR} = \frac{V_{\text{bid\_cluster}}}{V_{\text{ask\_cluster}} + \epsilon}$$
  7. If $\text{DIR} > 5.0$ (bids $> 5\times$ asks within 0.1% of cluster), an immovable institutional bid wall is detected (Setup 5).

---

## 3. Requirement R2: Deterministic Mechanical Alpha Setups

All 5 setups must operate with zero subjective discretion: strict mathematical boolean conditions for entry, explicit stop loss formulas, and hard invalidation rules.

```
                    ┌──────────────────────────────────────────────┐
                    │               Hyperliquid WS Feed            │
                    │         (L2 Orderbook, Trades, Context)      │
                    └──────────────────────┬───────────────────────┘
                                           │
                    ┌──────────────────────▼───────────────────────┐
                    │       R1: Feature Engine & Data Layer        │
                    │   Range/Vol/Tick Bars | OFI z-score | CVD   │
                    │   OI Contraction | Hawkes 3x | L2 Top 1%    │
                    └──────────────────────┬───────────────────────┘
                                           │
         ┌──────────────────┬──────────────┴─────┬──────────────────┬─────────────────┐
         ▼                  ▼                    ▼                  ▼                 ▼
   ┌───────────┐      ┌───────────┐        ┌───────────┐      ┌───────────┐     ┌───────────┐
   │ Setup 1   │      │ Setup 2   │        │ Setup 3   │      │ Setup 4   │     │ Setup 5   │
   │ OFI VWAP  │      │ Liq Absorp│        │ Hawkes BO │      │ CVD Sweep │     │ L2 Depth  │
   │ Reversion │      │ Cascade   │        │ Breakout  │      │ Divergence│     │ Imbalance │
   └─────┬─────┘      └─────┬─────┘        └─────┬─────┘      └─────┬─────┘     └─────┬─────┘
         │                  │                    │                  │                 │
         └──────────────────┴──────────────┬─────┴──────────────────┴─────────────────┘
                                           ▼
                    ┌──────────────────────────────────────────────┐
                    │          Alpha Engine Dispatcher             │
                    │   Emits AlphaSignal(Entry, Hard SL, Inval)   │
                    └──────────────────────────────────────────────┘
```

### Setup 1: OFI VWAP Reversion
- **Concept**: Extreme statistical stretch beyond the VWAP distribution band exhausted by an aggressive microstructural order flow reversal.
- **Mathematical Formulations**:
  $$\text{VWAP}_t = \frac{\sum_{i=1}^N p_i v_i}{\sum_{i=1}^N v_i}$$
  $$\sigma_{\text{VWAP}, t} = \sqrt{\frac{\sum_{i=1}^N p_i^2 v_i}{\sum_{i=1}^N v_i} - \text{VWAP}_t^2}$$
  - Upper Band: $\text{UB} = \text{VWAP}_t + 2.5 \cdot \sigma_{\text{VWAP}, t}$
  - Lower Band: $\text{LB} = \text{VWAP}_t - 2.5 \cdot \sigma_{\text{VWAP}, t}$
- **Entry Conditions**:
  - **Long Entry**:
    $$\text{Price}_t \le \text{LB} \quad \mathbf{AND} \quad z_{OFI}(t) > +0.8\sigma \quad \mathbf{AND} \quad z_{OFI}(t-1) \le 0$$
  - **Short Entry**:
    $$\text{Price}_t \ge \text{UB} \quad \mathbf{AND} \quad z_{OFI}(t) < -0.8\sigma \quad \mathbf{AND} \quad z_{OFI}(t-1) \ge 0$$
- **Hard Stop-Loss**:
  Strictly **0.35%** from entry price:
  $$\text{SL}_{\text{long}} = \text{Price}_{\text{entry}} \times (1 - 0.0035)$$
  $$\text{SL}_{\text{short}} = \text{Price}_{\text{entry}} \times (1 + 0.0035)$$
- **Take Profit**: $\text{VWAP}_t$ (mean reversion target).
- **Hard Invalidation**:
  - Immediate exit if price penetrates beyond 0.35% adverse excursion.
  - Invalidate if $z_{OFI}$ flips back against the trade before +0.15% profit is achieved.

---

### Setup 2: Liquidation Cascade Absorption
- **Concept**: Panic market liquidation exhaustion where forced closes purge open interest and passive limit buyers absorb the final dump.
- **Entry Conditions (Long Absorption)**:
  Over any sliding window $\le 300$ ticks:
  1. $\Delta OI_{\%} = \frac{OI_t - \max_{\tau \in [t-300, t]} OI_\tau}{\max_{\tau \in [t-300, t]} OI_\tau} < -0.025 \quad (-2.5\% \text{ drop})$
  2. $\Delta \text{Mark}_{\%} = \frac{\text{Mark}_t - \max_{\tau \in [t-300, t]} \text{Mark}_\tau}{\max_{\tau \in [t-300, t]} \text{Mark}_\tau} < -0.015 \quad (-1.5\% \text{ drop})$
  3. $\text{TakerSellFraction} = \frac{\sum_{\tau} \text{TakerSellVol}_\tau}{\sum_\tau \text{TotalVol}_\tau} > 0.85 \quad (>85\% \text{ taker sell})$
  4. Absorption signature: Price stabilization ($\text{Low}_t \ge \text{Low}_{t-1}$) and positive tick delta ($\delta_t > 0$).
- **Hard Stop-Loss**:
  $$\text{SL} = \min_{\tau \in \text{cascade}} \text{Low}_\tau - 1 \text{ tick}$$
- **Hard Invalidation**:
  If price prints a single tick below the cascade extreme low, the cascade has NOT terminated. Hard stop triggers immediately with zero discretion.

---

### Setup 3: Hawkes Volatility Breakout
- **Concept**: Momentum breakout beyond a 100-bar consolidation confirmed by an intense self-exciting order arrival surge (>3x median intensity).
- **Mathematical Formulations**:
  - Lookback: 100 event-driven bars (Range or Volume bars).
  - Resistance: $R_{100} = \max_{i=1}^{100} \text{High}_i$
  - Support: $S_{100} = \min_{i=1}^{100} \text{Low}_i$
  - Hawkes Excitation Ratio: $\text{ER}(t) = \frac{\lambda(t)}{\lambda_{\text{median}}} > 3.0$
- **Entry Conditions**:
  - **Long Entry**: $\text{Price}_t > R_{100} \quad \mathbf{AND} \quad \text{ER}(t) > 3.0$
  - **Short Entry**: $\text{Price}_t < S_{100} \quad \mathbf{AND} \quad \text{ER}(t) > 3.0$
- **Trailing Stop-Loss (Hull Moving Average Ratchet)**:
  $$WMA(x, k) = \frac{\sum_{i=1}^k i \cdot x_i}{\sum_{i=1}^k i}$$
  $$\text{HMA}(9) = WMA\left(2 \cdot WMA\left(P, 4\right) - WMA(P, 9), 3\right)$$
  - For Long: Stop ratchets up strictly behind $\text{HMA}(9)$:
    $$\text{SL}_t = \max(\text{SL}_{t-1}, \text{HMA}_9(t))$$
  - For Short: Stop ratchets down strictly behind $\text{HMA}(9)$:
    $$\text{SL}_t = \min(\text{SL}_{t-1}, \text{HMA}_9(t))$$
- **Hard Invalidation**:
  Price crosses and closes past $\text{HMA}(9)$ triggers immediate exit.

---

### Setup 4: CVD Divergence Sweep
- **Concept**: Liquidity sweep below/above swing levels where price makes a new extreme but Cumulative Volume Delta fails to confirm, indicating aggressive order exhaustion.
- **Entry Conditions (Bullish Sweep)**:
  Over a lookback window of 10 closed bars:
  1. Price prints Lower Low: $\text{Low}_t < \min_{i=1}^{9} \text{Low}_{t-i}$
  2. CVD prints Higher Low: $\text{CVD}_t > \min_{i=1}^{9} \text{CVD}_{t-i}$
  3. Relative Volume: $\text{Vol}_t > 1.5 \times \text{SMA}(\text{Vol}, 10)$
- **Hard Stop-Loss**:
  $$\text{SL}_{\text{long}} = \text{Low}_t - 1 \text{ tick} \quad (\text{strictly below sweep wick})$$
  $$\text{SL}_{\text{short}} = \text{High}_t + 1 \text{ tick} \quad (\text{strictly above sweep wick})$$
- **Hard Invalidation**:
  Any breach of the sweep wick invalidates the setup immediately.

---

### Setup 5: L2 Depth Imbalance Scalp
- **Concept**: Capitalizing on massive top-1% limit order book volume clusters within 0.1% of mid that act as impenetrable institutional walls.
- **Entry Conditions**:
  1. Scan L2 book for top 1% volume concentration ($q \ge Q_{99}$).
  2. Proximity: $|P_{\text{cluster}} - \text{Mid}| / \text{Mid} \le 0.001$ (within 0.1%).
  3. Imbalance Ratio within 0.1% band:
     - **Long**: $V_{\text{bid\_cluster}} > 5.0 \times V_{\text{ask\_cluster}}$
     - **Short**: $V_{\text{ask\_cluster}} > 5.0 \times V_{\text{bid\_cluster}}$
- **Hard Stop-Loss**:
  Strictly behind the cluster:
  $$\text{SL}_{\text{long}} = P_{\text{cluster\_bid}} - 1 \text{ tick}$$
  $$\text{SL}_{\text{short}} = P_{\text{cluster\_ask}} + 1 \text{ tick}$$
- **Hard Invalidation**:
  If the cluster size drops below $2\times$ the opposing side (due to order cancellation or fill), the wall has dissolved; invalidate and exit immediately.

---

## 4. Candidate Architecture & Module Layout

To ensure modularity, zero degradation of legacy tests, and direct compatibility with Architecture B unit tests (`tests/test_architecture_b.py`), the following layout is recommended:

```
quant/hft/
├── data_layer/                    # [NEW] R1 Timeframe-Agnostic Layer
│   ├── __init__.py
│   ├── bars.py                    # RangeBarBuilder, VolumeBarBuilder, TickBarBuilder, Bar
│   ├── ofi.py                     # MultiLevelOFIEngine (5-level + rolling Z-score)
│   ├── cvd.py                     # CVDTracker & DivergenceDetector
│   ├── oi_monitor.py              # OIMonitor (rolling 300-tick contraction)
│   ├── basis.py                   # BasisSpreadEstimator (Mark vs Mid, Perp vs Spot)
│   └── depth_imbalance.py         # L2DepthImbalanceEstimator (top 1% cluster)
│
├── alpha/                         # [EXPANDED] R2 Deterministic Setups
│   ├── __init__.py
│   ├── hawkes.py                  # Enhanced Hawkes with rolling median & excitation ratio
│   ├── signal_engine.py           # Existing CatBoost direction engine
│   ├── setups.py                  # [NEW] 5 Deterministic Alpha Setups
│   │   ├── ofi_vwap_reversion.py
│   │   ├── liq_cascade_absorption.py
│   │   ├── hawkes_vol_breakout.py
│   │   ├── cvd_divergence_sweep.py
│   │   └── l2_depth_imbalance.py
│   └── alpha_engine.py            # [NEW] Coordinator evaluating all 5 setups
│
tests/
├── test_architecture_b.py         # [NEW] Unit tests for R1 and R2 modules
└── test_filter.py                 # [EXISTING] Legacy model tests (must keep 100% pass)
```

### Data Contracts Between Layers

```python
@dataclass(slots=True)
class Bar:
    bar_type: str                  # "range" | "volume" | "tick"
    open_ts: int                   # epoch ms
    close_ts: int                  # epoch ms
    open: float
    high: float
    low: float
    close: float
    volume: float
    buy_volume: float
    sell_volume: float
    delta: float                   # buy_volume - sell_volume
    cvd: float                     # cumulative volume delta at close
    vwap: float
    ticks: int

@dataclass(slots=True)
class AlphaSignal:
    setup_name: str                # e.g. "OFI_VWAP_REVERSION"
    symbol: str
    direction: int                 # +1 (Long) or -1 (Short)
    entry_price: float
    hard_sl: float                 # Exact mathematical stop price
    tp_price: Optional[float]      # Target price (if applicable)
    trailing_rule: Optional[str]   # "HMA_9" or None
    timestamp: float
    invalidation_level: float
    metadata: dict                 # Diagnostical telemetry (z_ofi, cvd, etc.)
```

---

## 5. Verification Strategy & Test Scenarios for `tests/test_architecture_b.py`

| Test Case | Module Under Test | Input Data / Setup | Expected Outcome |
|---|---|---|---|
| `test_range_bar_invariant` | `bars.RangeBarBuilder` | Stream of 1,000 synthetic price steps with high volatility. | Every closed bar has `high - low == threshold` within floating point precision. |
| `test_volume_bar_invariant` | `bars.VolumeBarBuilder` | Stream of trades with varying sizes. | Every closed bar has `volume >= target_volume`. |
| `test_tick_bar_invariant` | `bars.TickBarBuilder` | Stream of discrete trades. | Every closed bar has exactly $N$ ticks. |
| `test_ofi_zscore_properties` | `ofi.MultiLevelOFIEngine` | Stationary random LOB snapshots. | Output $z_{OFI}$ has mean $\approx 0$, std $\approx 1$. Sudden bid rush produces $z_{OFI} > +0.8$. |
| `test_cvd_divergence_sweep` | `cvd.DivergenceDetector` | Synthetic 10-bar price lower low + CVD higher low + rel vol $2.0\times$. | Emits Bullish Sweep signal; flags invalidation if sweep wick is breached. |
| `test_oi_contraction_cascade` | `oi_monitor.OIMonitor` | 200-tick cascade: OI drops 3.0%, Mark drops 2.0%, taker sell = 90%. | Triggers Liquidation Cascade alert; records cascade low. |
| `test_hawkes_3x_median` | `alpha.hawkes.HawkesProcess` | Quiet period (50 ticks) followed by 20 rapid bursts. | `excitation_ratio > 3.0` evaluates True. |
| `test_basis_spreads` | `basis.BasisSpreadEstimator` | Mark=76100, Mid=76000, Spot=75900. | Mark-Mid spread = $+13.16$ bps; Perp-Spot spread = $+13.18$ bps. |
| `test_l2_depth_imbalance` | `depth_imbalance.L2DepthImbalanceEstimator` | LOB with 99th percentile bid block at 0.05% below mid with 6x ask volume. | Detects imbalance ratio $>5.0$; identifies cluster price. |
| `test_setup1_ofi_vwap_reversion` | `setups.ofi_vwap_reversion` | Price at $-2.6\sigma$ VWAP, $z_{OFI}$ flips from $-0.2$ to $+1.1$. | Emits Long signal with hard SL at exactly $Entry \times (1 - 0.0035)$. |
| `test_setup2_liq_absorption` | `setups.liq_cascade_absorption` | Cascade triggers + price tick reversal. | Emits Long signal with SL strictly at cascade extreme low. |
| `test_setup3_hawkes_breakout` | `setups.hawkes_vol_breakout` | Price breaks 100-bar high with Hawkes ER $= 3.4$. | Emits Long breakout; ratchets trailing stop behind HMA(9). |
| `test_setup5_l2_depth_scalp` | `setups.l2_depth_imbalance` | LOB top 1% bid block $>5\times$ asks within 0.1%. | Emits Long scalp with SL strictly below cluster wall. |

---

## 6. Recommendations for Architecture B Evolution

1. **Keep Legacy Compatibility**: Legacy filter tests (`tests/test_filter.py`) passed 100% in our dry-run (`6 passed in 0.40s`). Architecture B modules must be clean additions that do not break legacy `.npz` loaders or existing TradingView CDP structures.
2. **Tab ID Pinning in `signals/tv_cdp.py`**: The current CDP client accepts `target_index: int`, which can swap chart tabs if tabs reorder. Add `target_id: Optional[str]` pinning so the book stays locked to its specific window.
3. **Execution Latency**: Use vectorized/NumPy ring buffers (`collections.deque` and pre-allocated `np.ndarray`) for rolling calculations to ensure that feature calculation and setup evaluation execute in $<1$ ms, well inside the $<10$ ms ML filter budget.

