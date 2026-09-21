# Technical Architecture & Survey Report: Vectorized Backtester (R7) & Automated Test Suite (R8)

**Author:** Explorer 3 (Backtester & Test Suite Explorer)  
**Target Modules:** `backtester.py` (R7) & `tests/test_hyper_predator.py` (R8)  
**System Envelope:** 2-core / 4GB RAM Linux VPS / Darwin Development Environment  
**Asset Focus:** GOLD / XAUUSD on Hyperliquid CLOB DEX  
**Date:** 2026-09-19  

---

## 1. Executive Summary & Scope Overview

This report provides the architectural specification, mathematical formulations, memory-envelope calculations, and test suite blueprints for:
1. **R7 Decade-Deep Vectorized Backtester (`backtester.py`)**: An ultra-high performance streaming, chunked, and memory-mapped vectorized backtesting engine capable of processing 10 to 14 years of 1-minute (M1) GOLD OHLCV data strictly within a **4GB RAM envelope** (operating under **< 250 MB RSS**), featuring a multi-dimensional parameter sweep engine (optimizing wick %, M5 S/R lookback, L2 imbalance, and tick velocity) and a 500+ run Monte Carlo simulation engine with realistic execution jitter, adverse slippage, and fee friction.
2. **R8 Automated Test Suite (`tests/test_hyper_predator.py`)**: A comprehensive, deterministic `pytest` verification suite covering all requirements from R1 through R7 without relying on external network connectivity (strictly adhering to the project's `_no_network` test harness invariant).

---

## 2. Historical Data Survey & Storage Architecture for GOLD M1 Data

### 2.1 Codebase Survey of Existing Data
- **Current Data Inventory**:
  - `data/`: Contains runtime JSON artifacts (`autopilot.json`, `relapse_scalper_state.json`, `scan.txt`) and historical signals from older iterations (`signals.jsonl`, `outcomes.jsonl`, `tesla.jsonl`, `council_history.jsonl`).
  - `scalper/learn/stolgo/tests/fixtures/`: Synthetic 100-bar and 300-bar CSVs (`synthetic_100bars.csv`, `trend_up_300bars.csv`).
  - `scalper/backtester/`: Directory is currently empty.
  - `quant/engine/backtest.py`: Implements an event-driven `Sim` class that loads in-memory pandas DataFrames for multi-asset portfolio testing.
- **Finding**: No decade-deep historical GOLD M1 dataset is bundled in the Git repository (which is correct to prevent repository bloat). Therefore, `backtester.py` must support:
  1. Standard external file formats (`.parquet`, `.csv`, `.csv.gz`, `.dat`/`.npy`).
  2. A built-in, deterministic, high-speed **Synthetic Decade M1 Generator** (`generate_synthetic_gold_m1()`) to enable zero-dependency out-of-the-box backtesting and immediate CI/CD unit testing.

### 2.2 Decade-Deep Data Volume & Memory Calculations

| Parameter | Trading-Days Only (Forex/Metals) | Calendar Continuous (24/7 Crypto/Perp Proxy) |
| :--- | :--- | :--- |
| **Trading Hours** | 23 hours / day, 5 days / week (~120 hrs/week) | 24 hours / day, 7 days / week (168 hrs/week) |
| **Bars per Year (M1)** | ~374,400 bars | 525,600 bars |
| **10 Years (2014–2024)** | **3,744,000 bars** | **5,256,000 bars** |
| **14 Years (2010–2024)** | **5,241,600 bars** | **7,358,400 bars** |

#### Binary Memory Footprint per Bar
A minimal canonical binary M1 record contains:
- `timestamp`: `int64` (8 bytes)
- `open`: `float32` (4 bytes)
- `high`: `float32` (4 bytes)
- `low`: `float32` (4 bytes)
- `close`: `float32` (4 bytes)
- `volume`: `float32` (4 bytes)
- `tick_velocity`: `float32` (4 bytes, ratio vs rolling baseline)
- `l2_imbalance`: `float32` (4 bytes, Ask/Bid ratio)
- **Total record size**: $8 + (7 \times 4) = \mathbf{36\text{ bytes per bar}}$.

#### Aggregate Memory Footprint
- 10 Years (5.25M bars) $\times 36\text{ bytes} = \mathbf{189.2\text{ MB}}$.
- 14 Years (7.36M bars) $\times 36\text{ bytes} = \mathbf{264.9\text{ MB}}$.

### 2.3 Why Naive Pandas Causes Out-Of-Memory (OOM) on 4GB RAM
Despite the raw data being ~190 MB, naive implementations routinely crash with OOM on a 4GB VPS due to:
1. **`pd.read_csv()` Overhead**: Naive CSV parsing loads text into Python strings, infers `float64` (8 bytes per float instead of 4), creates object headers, and uses 2.5x–4.0x parsing buffer overhead. Reading a 450 MB CSV can spike peak resident memory to **2.2 GB – 3.0 GB**.
2. **Column Proliferation & Copies**: Adding 20 rolling indicators or intermediate columns in `float64` across 5.25M rows requires:
   $$5,256,000 \times 20 \times 8\text{ bytes} = 840\text{ MB}$$
   A single DataFrame operation like `df = df.copy()` or merging pivots results in **> 2.5 GB RSS**.
3. **Multiprocessing Worker Duplication**: A parameter sweep spawning 4 workers using naive `multiprocessing.Pool` duplicates the DataFrame in each child process. $4 \times 1.2\text{ GB} = 4.8\text{ GB}$ -> **Instant kernel `SIGKILL` (OOM)**.
4. **Python Object Churn**: Generating 1,000,000 trade or bar Python dicts/dataclasses creates 56 bytes per object header plus pointer tables, defeating cache locality and triggering garbage collection stalls.

---

## 3. Strict 4GB RAM Architecture: Streaming, Chunking, and Memory Mapping

To guarantee operation strictly within 4GB (targeting **< 250 MB RSS** during live decade sweeps), `backtester.py` must use a hybrid **Chunked Streaming & Memory-Mapped Vectorization** architecture.

```
+-----------------------------------------------------------------------------+
|                            HISTORICAL DATA INGESTION                        |
|  Parquet (PyArrow) / Gzipped CSV / np.memmap binary / Synthetic M1 Generator|
+-----------------------------------------------------------------------------+
                                       |
                                       v
+-----------------------------------------------------------------------------+
|                      CHUNKED STREAMING ITERATOR (100k Bars)                 |
|  Chunk Size: 100,000 bars (~3.6 MB) | Overlap Buffer (Halo): 1,000 bars     |
+-----------------------------------------------------------------------------+
                                       |
                                       v
+-----------------------------------------------------------------------------+
|                    VECTORIZED SIGNAL EVALUATION (NumPy SIMD)                |
|  - M5 S/R Rolling Pivot Masking (Lookback 20-100)                           |
|  - M1 Rejection Wick Ratio Masking (Wick >= 60-75%, Body Direction)         |
|  - Tick Velocity Surge Masking (Velocity >= 1.2x - 2.0x)                    |
|  - L2 Imbalance Wall Exit Masking (Imbalance > 2.0 - 5.0)                   |
+-----------------------------------------------------------------------------+
                                       |
                                       v
+-----------------------------------------------------------------------------+
|                    FAST STATEFUL BASKET SIMULATOR                           |
|  - 5-Slice Order Dispatch with 20ms Jitter Stagger                          |
|  - Detached Stop-Loss ($1.00 away from invalidation wick)                   |
|  - Opposing M5 S/R Target Exit | Opposing Reversal Wick Exit                 |
|  - Hard Equity Shield (-$10.00 Basket Liquidation)                          |
|  - Active Basket State carried across chunk boundaries                      |
+-----------------------------------------------------------------------------+
                                       |
                                       v
+-----------------------------------------------------------------------------+
|                 MONTE CARLO ENGINE (500+ Iterations) & SWEEPER              |
|  - Jitter & Slippage Stochastic Sampling                                    |
|  - 5% Daily Drawdown Killswitch Tracking                                    |
|  - Sharpe, Max DD ($/%), Win Rate, Profit Factor, Total Trades Metrics      |
+-----------------------------------------------------------------------------+
```

### 3.1 Component Specifications

#### A. Chunked Streaming Iterator (`ChunkedBacktestIterator`)
- **Chunk Size**: 100,000 bars (~70 calendar days of M1 data).
- **Chunk Memory Footprint**: ~3.6 MB raw numeric data, < 25 MB working DataFrame memory.
- **Overlap Halo Buffer**: 1,000 bars.
  - *Problem*: Indicators with rolling lookbacks (e.g. 100-period M5 S/R lookback = 500 M1 bars) experience edge distortion at chunk boundaries.
  - *Solution*: Prepend the last 1,000 bars of Chunk $k-1$ to the head of Chunk $k$. Rolling indicators are computed over 101,000 bars, but entry signals and trade executions are strictly evaluated only for indices $[1000..101000)$.
- **State Continuity**: Open positions (the active basket) are held in an `ActiveBasket` dataclass that persists across chunk boundaries.

#### B. Memory-Mapped Array Vectorization (`np.memmap`)
- For ultra-fast single-node optimization sweeps, convert raw data into a fixed-layout binary file (`gold_m1_decade.dat`).
- Load via:
  ```python
  mmap_data = np.memmap(
      filename,
      dtype=[
          ('timestamp', 'i8'),
          ('open', 'f4'),
          ('high', 'f4'),
          ('low', 'f4'),
          ('close', 'f4'),
          ('volume', 'f4'),
          ('tick_velocity', 'f4'),
          ('l2_imbalance', 'f4'),
      ],
      mode='r'
  )
  ```
- **Virtual Memory Paging**: The OS kernel loads pages on demand and evicts clean pages from RAM when physical memory pressure rises. Memory usage remains bounded under **100 MB RSS** even when accessing 14 years of data.

#### C. Shared Memory for Parameter Sweeps
- When running parameter sweeps across multi-core systems, the underlying dataset is mapped into read-only shared memory (`multiprocessing.shared_memory.SharedMemory`) or shared memory-mapped files.
- Workers read from the same memory block without copy-on-write duplication.

---

## 4. Hyper Predator Trading Strategy Vectorization & Simulation Logic

### 4.1 Signal Generation Math (R1 & R2)

1. **M5 Resampling & Dynamic S/R Levels**:
   - Resample M1 bars into M5 bars:
     $$High_{M5, j} = \max_{k \in [5j, 5j+4]} High_{M1, k}, \quad Low_{M5, j} = \min_{k \in [5j, 5j+4]} Low_{M1, k}$$
   - Dynamic Support & Resistance over lookback window $W_{M5} \in [20, 100]$:
     $$SR_{High, j} = \max_{i \in [j - W_{M5}, j-1]} High_{M5, i}, \quad SR_{Low, j} = \min_{i \in [j - W_{M5}, j-1]} Low_{M5, i}$$
   - M1 S/R Proximity Condition:
     - Long setup: $Low_{M1, t} \le SR_{Low, j} + 0.20$ (touching or entering support zone within $0.20).
     - Short setup: $High_{M1, t} \ge SR_{High, j} - 0.20$ (touching or entering resistance zone within $0.20).

2. **M1 Rejection Wick Math**:
   - Total candle range: $R_t = High_t - Low_t$. (Guard $R_t \ge 0.10$).
   - Rejection wick threshold: $T_{wick} \in [0.60, 0.75]$ (default 0.65).
   - **Bullish Rejection**:
     $$W_{lower, t} = \min(Open_t, Close_t) - Low_t$$
     $$\text{Condition:} \quad \frac{W_{lower, t}}{R_t} \ge T_{wick} \quad \text{AND} \quad Close_t \ge Open_t$$
     Invalidation Wick Extreme: $Low_{inval} = Low_t$.
   - **Bearish Rejection**:
     $$W_{upper, t} = High_t - \max(Open_t, Close_t)$$
     $$\text{Condition:} \quad \frac{W_{upper, t}}{R_t} \ge T_{wick} \quad \text{AND} \quad Close_t \le Open_t$$
     Invalidation Wick Extreme: $High_{inval} = High_t$.

3. **Tick Velocity Edge**:
   - Rolling baseline tick velocity over past $N_{vel}=20$ M1 bars:
     $$\bar{V}_t = \frac{1}{N_{vel}} \sum_{k=1}^{N_{vel}} V_{t-k}$$
   - Surge Condition:
     $$V_{\text{final\_5s}, t} \ge \text{velocity\_mult} \times \bar{V}_t, \quad \text{velocity\_mult} \in [1.2, 2.0] \quad (\text{default } 1.5\text{x})$$

4. **Macro State Gate (Core 1)**:
   - Evaluated from periodic 5-minute telemetry:
     - `permit_trade == True`
     - Directional match: `bias == "BULLISH"` for longs, `bias == "BEARISH"` for shorts.
     - `volatility_regime`: dynamic float scaling parameter.

### 4.2 Execution & Position Sizing (R3)
- **Account Base**: $65.00 micro-account.
- **Leverage**: 100x.
- **Margin Utilization Ceiling**: Strictly $\le 20\%$ equity ($13.00 max margin).
- **Basket Sizing**: Total notional size $S = 0.50$ oz (5 slices of $0.10$ oz).
  - Margin required: $\frac{0.50 \times 2500}{100} = \$12.50 \le \$13.00$.
- **5-Slice Layering (`spam_orders`)**:
  - 5 micro-slices dispatched with 20ms jitter stagger ($t = 0\text{ms}, 20\text{ms}, 40\text{ms}, 60\text{ms}, 80\text{ms}$).
- **Detached Stop-Loss**:
  - Dispatched immediately upon fill confirmation with `reduce_only=True`.
  - Placed exactly **$1.00 absolute dollar** beyond the invalidation wick extreme:
    $$\text{Long Stop} = Low_{inval} - 1.00$$
    $$\text{Short Stop} = High_{inval} + 1.00$$

### 4.3 Ruthless Dynamic Exits (R4 & R5)
The active basket is monitored at every sub-bar tick or minute bar close and liquidated immediately upon the first trigger of:
1. **Opposing M5 S/R Target Exit**:
   - Long: $High_t \ge SR_{High}$.
   - Short: $Low_t \le SR_{Low}$.
2. **Opposing Reversal Wick Exit**:
   - Long: An opposing Bearish Rejection Wick ($\ge 65\%$) prints.
   - Short: An opposing Bullish Rejection Wick ($\ge 65\%$) prints.
3. **Hard Equity Shield Liquidation**:
   - If floating unrealized PnL drops to $-\$10.00$ ($\le -10.00$), execute immediate market close on all open slices.
4. **L2 Orderflow Imbalance Exit**:
   - Top-5 book Ask/Bid ratio:
     - Long: $\frac{\text{Ask Volume}}{\text{Bid Volume}} > 3.0 \times \text{volatility\_regime}$ (or sweep threshold $2.0 - 5.0$).
     - Short: $\frac{\text{Bid Volume}}{\text{Ask Volume}} > 3.0 \times \text{volatility\_regime}$.
5. **Trade Tape Volume Delta Stall Exit**:
   - If in a profitable basket and $> 80\%$ of the last 20 trade ticks are aggressive opposing market fills.
6. **Detached Stop-Loss Hit**:
   - Price breaches the resting stop level.

---

## 5. Parameter Sweep Engine Architecture

### 5.1 Sweep Grid Specification

```python
@dataclass(frozen=True)
class ParameterSpace:
    """Hyper Predator parameter optimization space."""
    wick_pcts: Tuple[float, ...] = (0.60, 0.65, 0.70, 0.75)
    m5_lookbacks: Tuple[int, ...] = (20, 40, 60, 80, 100)
    l2_imbalances: Tuple[float, ...] = (2.0, 3.0, 4.0, 5.0)
    tick_velocities: Tuple[float, ...] = (1.2, 1.4, 1.6, 1.8, 2.0)
```
- Total discrete parameter grid combinations: $4 \times 5 \times 4 \times 5 = \mathbf{400\text{ configurations}}$.

### 5.2 Vectorized Sweeper Design Pattern
To sweep 400 parameter sets over decade-deep data within 4GB RAM:
1. **Feature Pre-computation**:
   - Compute the rolling M5 pivots for all 5 lookback values $(20, 40, 60, 80, 100)$ in a single forward pass over the data chunks.
   - Compute candle wick percentages and tick velocities once.
2. **Matrix Condition Masking**:
   - Each parameter set evaluates boolean signal arrays via NumPy bitwise operations (`&`).
3. **Memory Discipline**:
   - Run sequentially or in a 2-worker pool (matching 2-vCPU VPS).
   - Collect only summary metrics per parameter set (`trades_count`, `win_rate`, `profit_factor`, `sharpe`, `max_dd_dollars`, `max_dd_pct`).
   - Peak RAM across the entire 400-parameter sweep: **< 200 MB RSS**.

---

## 6. Monte Carlo Simulation Engine (500+ Runs)

### 6.1 Stochastic Friction & Jitter Modeling

1. **Execution Jitter Delay Model**:
   - 5 slices dispatched at $t_0, t_0 + 20\text{ms}, t_0 + 40\text{ms}, t_0 + 60\text{ms}, t_0 + 80\text{ms}$.
   - Intra-slice price drift modeled via Gaussian noise scaled by ATR:
     $$\Delta P_{\text{jitter}, k} = \sigma_{ATR} \times \sqrt{\frac{20k}{60000}} \times \epsilon_k, \quad \epsilon_k \sim \mathcal{N}(0, 1)$$
2. **Adverse Execution Slippage**:
   - Asymmetric adverse slippage sampled from a Triangular distribution:
     $$\text{Slippage}_{\text{entry}} \sim \text{Triangular}(\text{min}=0.5, \text{mode}=1.0, \text{max}=2.0)\text{ pips} \quad (\$0.05 - \$0.20/\text{oz})$$
   - Stop-market orders under cascade or high velocity:
     $$\text{Slippage}_{\text{stop}} \sim \text{Triangular}(\text{min}=1.0, \text{mode}=1.5, \text{max}=2.5)\text{ pips} \quad (\$0.10 - \$0.25/\text{oz})$$
3. **Exchange Fee Friction**:
   - Hyperliquid Taker Fee: $3.5\text{ bps}$ ($0.00035$ of notional) applied on entry and stop/market exits.
   - Hyperliquid Maker Fee / Rebate: $-0.2\text{ bps}$ ($-0.00002$ of notional) for resting limit fills.
4. **Resampling Methodology**:
   - Stationary block bootstrapping and random trade sequence permutation across $\ge 500$ simulation runs.
   - Enforce the 5% Daily Drawdown Killswitch and Hard Equity Shield on every run.

### 6.2 Metrics Reporting Specification

1. **Sharpe Ratio (Annualized)**:
   $$\text{Sharpe} = \frac{\mu_{\text{daily}} - r_f}{\sigma_{\text{daily}}} \times \sqrt{252}$$
   *(Trade-based fallback: $\frac{\mu_{pnl}}{\sigma_{pnl}} \times \sqrt{N_{\text{trades\_per\_year}}}$).*
2. **Maximum Drawdown ($ and %)**:
   $$\text{MaxDD}_{\$} = \max_t \left( \max_{\tau \le t} Equity_\tau - Equity_t \right)$$
   $$\text{MaxDD}_{\%} = \max_t \left( \frac{\max_{\tau \le t} Equity_\tau - Equity_t}{\max_{\tau \le t} Equity_\tau} \right) \times 100\%$$
3. **Win Rate (%)**:
   $$\text{Win Rate} = \frac{N_{\text{profitable trades}}}{N_{\text{total trades}}} \times 100\%$$
4. **Profit Factor**:
   $$\text{Profit Factor} = \frac{\sum \text{Gross Profits}}{\sum |\text{Gross Losses}|}$$
5. **Total Trades**: Integer count of all completed closed baskets.
6. **Distribution Percentiles**: 5th, 25th, 50th (median), 75th, and 95th percentiles reported across all 500+ runs.

---

## 7. Automated Test Suite Blueprint (`tests/test_hyper_predator.py`)

### 7.1 Existing Test Harness Invariants & Constraints
- **Zero Network Egress**: `tests/conftest.py` installs an `autouse=True` fixture that monkeypatches `socket.socket.connect` to raise `RuntimeError("a test tried to open a network connection...")`.
- **Implication**: All Hyperliquid SDK calls, WebSocket connections, and LLM HTTP endpoints must be fully mocked using in-memory queues, `AsyncMock`, and deterministic mock venues.

### 7.2 Comprehensive Test Enumeration (Covering R1 to R7)

The dedicated test file `tests/test_hyper_predator.py` must contain **8 test classes** and **32 granular test cases**:

```
tests/test_hyper_predator.py
├── TestMacroBrainCore1 (R1)
│   ├── test_macro_state_dataclass_defaults
│   ├── test_llm_json_parsing_valid_bullish
│   ├── test_llm_json_parsing_valid_bearish
│   ├── test_llm_json_parsing_markdown_codeblock_fallback
│   ├── test_llm_json_parsing_malformed_syntax_fallback
│   ├── test_update_macro_edge_timeout_non_blocking
│   └── test_macro_state_thread_safety
├── TestSniperCore2M1Signal (R2)
│   ├── test_rejection_wick_calculation_exact_65pct_bullish
│   ├── test_rejection_wick_calculation_sub_65pct_rejection
│   ├── test_rejection_wick_bearish_alignment
│   ├── test_rejection_wick_zero_range_guard
│   ├── test_m5_support_resistance_rolling_pivot
│   ├── test_tick_velocity_edge_threshold
│   └── test_signal_trigger_full_confluence
├── TestSpamOrdersLayeredExecution (R3)
│   ├── test_spam_orders_5_slices_dispatched
│   ├── test_spam_orders_20ms_jitter_stagger
│   ├── test_detached_stop_placement_distance
│   ├── test_leverage_100x_and_margin_ceiling_20pct
│   └── test_open_ended_entry_no_static_tp
├── TestL2OrderflowExitEngine (R5)
│   ├── test_l2_top5_imbalance_calculation
│   ├── test_l2_imbalance_exit_long_wall
│   ├── test_l2_imbalance_exit_short_wall
│   ├── test_l2_adaptive_regime_threshold
│   └── test_l2_orderflow_exit_latency_sub_5ms
├── TestTradeTapeVolumeDeltaStall (R5)
│   ├── test_volume_delta_stall_long_opposing_ticks
│   ├── test_volume_delta_stall_short_opposing_ticks
│   └── test_volume_delta_stall_normal_flow_holds
├── TestRuthlessExitsAndHardEquityShield (R4)
│   ├── test_hard_equity_shield_liquidation_at_minus_10
│   ├── test_opposing_reversal_wick_exit
│   └── test_opposing_m5_sr_target_exit
├── TestVectorizedBacktester (R7)
│   ├── test_backtester_data_ingestion_formats
│   ├── test_backtester_chunked_streaming_continuity
│   ├── test_backtester_memory_footprint_under_4gb
│   └── test_backtester_signal_generation_consistency
└── TestParameterSweepAndMonteCarlo (R7)
    ├── test_parameter_sweep_interface
    ├── test_monte_carlo_500_runs_execution
    └── test_metrics_calculation_accuracy
```

---

## 8. Detailed Test Specifications

### 8.1 Group 1: Macro Brain Core 1 (R1)
1. **`test_macro_state_dataclass_defaults`**:
   - Asserts `MACRO_STATE` initializes with `permit_trade=False`, `bias="BEARISH"`, `volatility_regime=1.0`, and valid epoch timestamp.
2. **`test_llm_json_parsing_valid_bullish`**:
   - Feeds raw JSON: `{"permit_trade": true, "bias": "BULLISH", "volatility_regime": 1.25}`.
   - Asserts parser extracts `permit_trade=True`, `bias="BULLISH"`, `volatility_regime=1.25`.
3. **`test_llm_json_parsing_valid_bearish`**:
   - Feeds raw JSON: `{"permit_trade": true, "bias": "BEARISH", "volatility_regime": 0.85}`.
   - Asserts parser extracts `permit_trade=True`, `bias="BEARISH"`, `volatility_regime=0.85`.
4. **`test_llm_json_parsing_markdown_codeblock_fallback`**:
   - Feeds response wrapped in markdown code fence: ```` ```json\n{"permit_trade": false, "bias": "BEARISH", "volatility_regime": 1.5}\n``` ````.
   - Asserts parser strips markdown and extracts values correctly.
5. **`test_llm_json_parsing_malformed_syntax_fallback`**:
   - Feeds broken JSON: `{"permit_trade": true, bias: BULLISH`.
   - Asserts parser catches `json.JSONDecodeError`, preserves previous safe state, logs error, and sets `permit_trade=False`.
6. **`test_update_macro_edge_timeout_non_blocking`**:
   - Mocks `aiohttp` or HTTP client with a hanging delay (1000ms).
   - Verifies loop enforces `< 500ms` timeout, does not crash or block execution loop, and falls back to safe state.
7. **`test_macro_state_thread_safety`**:
   - Verifies concurrent reads by execution loop and writes by macro loop maintain state integrity.

### 8.2 Group 2: Sniper Core 2 M1 Signal Math (R2)
8. **`test_rejection_wick_calculation_exact_65pct_bullish`**:
   - Constructs M1 candle: $Open = 2501.00, High = 2502.00, Low = 2492.00, Close = 2501.50$.
   - Range $= 10.00$. Lower wick $= 2501.00 - 2492.00 = 9.00$ ($90\% > 65\%$). Close $> Open$.
   - Asserts bullish rejection wick detected.
9. **`test_rejection_wick_calculation_sub_65pct_rejection`**:
   - Constructs candle where lower wick is $64.5\%$ of range.
   - Asserts signal is strictly rejected.
10. **`test_rejection_wick_bearish_alignment`**:
    - Upper wick $= 70\%$ of range, Close $< Open$.
    - If `MACRO_STATE.bias == "BEARISH"` -> passes.
    - If `MACRO_STATE.bias == "BULLISH"` -> rejected.
11. **`test_rejection_wick_zero_range_guard`**:
    - Candle where $High == Low == Open == Close$. Asserts calculation handles $R=0$ without `ZeroDivisionError`.
12. **`test_m5_support_resistance_rolling_pivot`**:
    - Computes rolling pivot highs/lows over 20 M5 bars. Verifies pivot matches exact maximum/minimum of prior window.
13. **`test_tick_velocity_edge_threshold`**:
    - Final 5s tick velocity $= 1.6\text{x}$ rolling baseline -> permitted.
    - Final 5s tick velocity $= 1.4\text{x}$ rolling baseline -> blocked.
14. **`test_signal_trigger_full_confluence`**:
    - Verifies signal fires if and only if: `permit_trade == True`, S/R zone active, Wick $\ge 65\%$, and Velocity $\ge 1.5\text{x}$.

### 8.3 Group 3: Layered Order Slicing & Execution (R3)
15. **`test_spam_orders_5_slices_dispatched`**:
    - Mocks exchange client and triggers `spam_orders(coin="GOLD", is_buy=True, total_sz=0.50, slices=5)`.
    - Asserts exactly 5 orders dispatched with `sz = 0.10` each.
16. **`test_spam_orders_20ms_jitter_stagger`**:
    - Instruments mock dispatch calls. Verifies time offsets between slices match 20ms jitter stagger ($0\text{ms}, 20\text{ms}, 40\text{ms}, 60\text{ms}, 80\text{ms}$).
17. **`test_detached_stop_placement_distance`**:
    - Invalidation wick extreme for long $= 2492.00$.
    - Asserts detached stop is placed at exactly $2492.00 - 1.00 = 2491.00$ with `reduce_only=True`.
18. **`test_leverage_100x_and_margin_ceiling_20pct`**:
    - Account equity $= \$65.00$. Leverage $= 100\text{x}$.
    - Asserts total margin across all 5 slices $\le \$13.00$ ($20\%$).
19. **`test_open_ended_entry_no_static_tp`**:
    - Verifies no resting take-profit order is placed on exchange CLOB at entry.

### 8.4 Group 4: L2 Orderflow Exit Engine (R5)
20. **`test_l2_top5_imbalance_calculation`**:
    - Provides mock `l2Book` snapshot with known top-5 bid volumes and ask volumes.
    - Asserts imbalance ratio matches $\sum \text{Ask} / \sum \text{Bid}$.
21. **`test_l2_imbalance_exit_long_wall`**:
    - Long position open, `volatility_regime = 1.0`.
    - Asks in top 5 $= 350.0$, Bids in top 5 $= 100.0$ (Ratio $= 3.5 > 3.0$).
    - Asserts `close_all_positions()` fired immediately.
22. **`test_l2_imbalance_exit_short_wall`**:
    - Short position open, `volatility_regime = 1.0`.
    - Bids in top 5 $= 400.0$, Asks in top 5 $= 100.0$ (Ratio $= 4.0 > 3.0$).
    - Asserts immediate liquidation.
23. **`test_l2_adaptive_regime_threshold`**:
    - When `volatility_regime = 1.5`, threshold is $3.0 \times 1.5 = 4.5$. Ratio $3.5$ holds; ratio $4.6$ exits.
24. **`test_l2_orderflow_exit_latency_sub_5ms`**:
    - Benchmarks 1,000 iterations of L2 book evaluation using `time.perf_counter_ns()`.
    - Asserts mean execution latency is strictly $< 5\text{ms}$ (typically $< 0.05\text{ms}$).

### 8.5 Group 5: Trade Tape Volume Delta Stall (R5)
25. **`test_volume_delta_stall_long_opposing_ticks`**:
    - In profitable Long basket. Last 20 trade ticks: 17 sells, 3 buys ($85\% > 80\%$).
    - Asserts immediate market liquidation.
26. **`test_volume_delta_stall_short_opposing_ticks`**:
    - In profitable Short basket. Last 20 trade ticks: 18 buys, 2 sells ($90\% > 80\%$).
    - Asserts immediate market liquidation.
27. **`test_volume_delta_stall_normal_flow_holds`**:
    - Last 20 trade ticks: 10 buys, 10 sells. Position remains open.

### 8.6 Group 6: Ruthless Exits & Hard Equity Shield (R4)
28. **`test_hard_equity_shield_liquidation_at_minus_10`**:
    - Position floating PnL drops to $-\$10.05$.
    - Asserts immediate market close executed with `reduce_only=True`.
29. **`test_opposing_reversal_wick_exit`**:
    - In active Long basket. An opposing Bearish Rejection Wick ($\ge 65\%$) prints on M1.
    - Asserts immediate market close.
30. **`test_opposing_m5_sr_target_exit`**:
    - In Long basket. Price touches opposing M5 Resistance level.
    - Asserts immediate market close.

### 8.7 Group 7: Vectorized Backtester Engine (R7)
31. **`test_backtester_data_ingestion_formats`**:
    - Validates ingestion of CSV, Parquet, and memory-mapped NumPy arrays.
32. **`test_backtester_chunked_streaming_continuity`**:
    - Streams a multi-chunk dataset (100k bars per chunk) with a 1,000-bar overlap halo.
    - Verifies zero indicator distortion and seamless active basket carry-over across chunk transitions.
33. **`test_backtester_memory_footprint_under_4gb`**:
    - Generates 150,000 synthetic M1 bars.
    - Uses `psutil.Process().memory_info().rss` to measure memory growth during backtest.
    - Asserts RSS delta $\le 100\text{ MB}$ (far below the 4GB ceiling).
34. **`test_backtester_signal_generation_consistency`**:
    - Compares vectorized boolean masks against point-in-time candle calculations to confirm zero lookahead bias and identical signal firing.

### 8.8 Group 8: Parameter Sweep & Monte Carlo Engine (R7)
35. **`test_parameter_sweep_interface`**:
    - Runs a discrete sweep over wick % $(0.60..0.75)$, lookback $(20..100)$, imbalance $(2.0..5.0)$, velocity $(1.2..2.0)$.
    - Verifies output ranking dataframe contains all configurations sorted by Sharpe/Profit Factor.
36. **`test_monte_carlo_500_runs_execution`**:
    - Executes 500 Monte Carlo runs with randomized jitter and slippage.
    - Verifies 500 valid equity paths and percentiles are returned.
37. **`test_metrics_calculation_accuracy`**:
    - Feeds known trade series with predetermined PnL.
    - Asserts exact analytical match for: Sharpe Ratio, Max Drawdown ($ and %), Win Rate, Profit Factor, and Total Trades.

---

## 9. Proposed Software Architecture & Interface Contracts

### 9.1 `backtester.py` Interface Contract
```python
class M1ChunkIterator:
    """Streams M1 OHLCV data in chunks with halo overlap for rolling indicators."""
    def __init__(self, source: str | Path, chunk_size: int = 100_000, halo_size: int = 1_000): ...
    def __iter__(self) -> Iterator[pd.DataFrame]: ...

class VectorizedSignalEngine:
    """Vectorized indicator computation and signal mask generation using NumPy."""
    def compute_m5_pivots(self, highs: np.ndarray, lows: np.ndarray, lookback: int) -> Tuple[np.ndarray, np.ndarray]: ...
    def compute_wick_ratios(self, opens: np.ndarray, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray) -> Tuple[np.ndarray, np.ndarray]: ...
    def generate_signal_mask(self, chunk: pd.DataFrame, params: HyperPredatorParams) -> np.ndarray: ...

class HyperPredatorBacktester:
    """Streaming, event-driven, vectorized backtester operating under 250MB RSS."""
    def __init__(self, initial_equity: float = 65.0, leverage: float = 100.0): ...
    def run_backtest(self, source: str | Path, params: HyperPredatorParams) -> BacktestResult: ...
    def parameter_sweep(self, source: str | Path, space: ParameterSpace) -> pd.DataFrame: ...
    def run_monte_carlo(self, base_result: BacktestResult, n_simulations: int = 500) -> MonteCarloResult: ...
```

### 9.2 CLI Specification for `backtester.py`
```bash
# Direct backtest execution on dataset (or auto-synthetic generation)
python3 backtester.py --data data/gold_m1.parquet --starting-equity 65.0

# Parameter sweep across 4 dimensions
python3 backtester.py --sweep --out reports/sweep_results.csv

# 500-run Monte Carlo simulation with jitter & slippage
python3 backtester.py --monte-carlo --runs 500 --out reports/monte_carlo.json
```

---

## 10. Summary & Downstream Implementation Handoff
- **Streaming & Memory Target**: Guaranteed $< 250\text{ MB}$ RSS memory footprint (safe on 4GB VPS).
- **Signal Equivalence**: Vectorized boolean masks replicate exact live trading rules in R1–R5.
- **Verification Guarantee**: 37 test cases in `tests/test_hyper_predator.py` fully decoupled from external networking, ready for immediate validation via `pytest`.
