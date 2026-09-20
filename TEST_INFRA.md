# Architecture B Test Infrastructure Specification (TEST_INFRA.md)
**System**: Wall-Street-Nightmare Autonomous HFT Scalping Engine  
**Target Evolution**: Architecture B (Microstructural Orderflow + ML Filter + Temporal Memory + Risk Engine + LLM Critic)  
**Document Classification**: Engineering Quality & Verification Standard  
**Author**: E2E Test Architecture Writer  
**Status**: Authoritative Standard  

---

## 1. Executive Summary & Testing Philosophy

Architecture B transforms the Wall-Street-Nightmare scalping engine from a legacy fixed-time heuristic strategy into an asynchronous, sub-10ms event-driven trading system. In this regime, execution bugs, mathematical specification errors, or subtle timing race conditions directly result in capital loss. 

To guarantee mathematical correctness, operational safety, and system resilience under extreme market microstructure conditions (e.g., flash crashes, liquidation cascades, orderbook voids), this test infrastructure standard establishes a **4-Tier Testing Architecture** founded on four formal testing methodologies:
1. **Category-Partition Method (CPM)**: Systematic partitioning of input domains and microstructural states into discrete, disjoint equivalence classes.
2. **Boundary Value Analysis (BVA)**: Strict stress-testing at and around domain extremes (e.g., exact $9.5\%$ liquidation distance, $0.35\%$ stop-loss boundaries, $0$-sized or empty depth levels, and numerical $\pm \infty$ / $\text{NaN}$ boundaries).
3. **Pairwise (Combinatorial) Testing**: Comprehensive interaction testing across multidimensional state parameters (e.g., OFI state $\times$ VWAP band $\times$ Hawkes excitation ratio $\times$ spread regime).
4. **Real-World Workload Modeling**: Full end-to-end event injection synthesizing real L2 WebSocket market bursts, trade fills, liquidation cascades, and memory retrieval.

### Core Testing Mandates
- **Zero-Tolerance for Facade Tests**: Every test must exercise genuine mathematical models, actual state transitions, and real data structures. Mocking is restricted strictly to external network barriers (e.g., remote Chrome DevTools websocket endpoints or external `llama-server` HTTP daemon).
- **Progressive Testability**: Tests are designed around formal interface contracts defined in `PROJECT.md`. When subcomponents are in development, test suites support modular verification with deterministic isolation.
- **Deterministic Reproducibility**: Random tests must be explicitly seeded; stochastic properties (such as Hawkes process realization or z-score distributions) are evaluated against formal statistical bounds ($\mu \pm 3\sigma$).
- **Sub-10ms Latency Enforcement**: ML filter and temporal memory inference latency is subject to hard automated performance gates ($P_{99} < 10.0\text{ ms}$, $P_{50} < 1.0\text{ ms}$) across 1,000 continuous benchmark cycles.

---

## 2. 4-Tier Test Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│               TIER 4: REAL-WORLD APPLICATION SCENARIOS                 │
│  - Full Pipeline Orderbook & Trade Burst Replay                        │
│  - Multi-Minute Flash Crash & Cascade Liquidation Simulation           │
│  - Dedicated 1,000-Iteration ML Filter Latency Benchmark (P99 < 10ms)  │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│               TIER 3: CROSS-FEATURE INTERACTIONS                       │
│  - Multi-Component Coupling (OFI + VWAP, Hawkes + Breakout)            │
│  - CVD Divergence with Temporal Memory MAE Veto Enforcement            │
│  - Risk Engine Dynamic Kelly Modulation via LLM Multiplier Cache       │
│  - L2 Depth Imbalance Scalp Invalidation upon Cluster Evaporation     │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│               TIER 2: BOUNDARY & CORNER CASES                          │
│  - Zero, Negative, and Infinite Inputs (NaN/Inf, 0 Vol, 0 Ticks)      │
│  - Exact 9.5% Liquidation Distance & Leverage Ceilings (10x / 15x)     │
│  - Out-of-Order & Duplicate Millisecond Timestamps                     │
│  - Single-Sided LOB, Cross-Book, Inverted Spread Handling              │
│  - Exact Ring Buffer Wrap-Around (300 ticks, 100 bars)                 │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│               TIER 1: FEATURE COVERAGE (UNIT & CONTRACT)               │
│  - ≥5 Independent Test Cases per Architecture B Feature (15 Features) │
│  - Range, Volume, Tick Bar Builders (Zero Lookahead Invariant)         │
│  - Cont-Kukanov-Stoikov 5-Level OFI with Rolling Z-Score               │
│  - CVD Tracker & 10-Bar Divergence Sweep Scanner                       │
│  - OI Contraction Monitor (>2.5% drop in <300 ticks)                   │
│  - Hawkes Exponential Process & Excitation Ratio vs Rolling Median     │
│  - Mark/Mid and Perp/Spot Basis Spread Estimators                      │
│  - L2 Depth Imbalance (Top 1% Clusters, >5x Ratio)                     │
│  - 5 Deterministic Alpha Setups & AlphaEngine                          │
│  - Polymorphic FilterModel (Legacy .npz, CatBoost, LightGBM)           │
│  - 256-Dim Temporal Memory (Dual Backend: Cosine & Qdrant)             │
│  - Expected MAE Evaluator & Hard SL Veto Gate                          │
│  - RiskEngine Leverage Ceiling, 25% Margin Sizing, Hard SL Gate        │
│  - TradingView CDP Explicit Tab Target ID Pinning                      │
│  - Combinatorial Purged K-Fold Cross-Validation (CPCV)                 │
│  - Asynchronous LLM Critic Client & Dynamic Multiplier Cache           │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Test Coverage Methodologies

### 3.1 Category-Partition Method (CPM)
Inputs to every component are decomposed into functional categories, each subdivided into mutually exclusive choices:

| Component | Category | Equivalence Partitions / Choices |
|---|---|---|
| **Bar Builders** | Price / Volume Step | (a) Within threshold, (b) Exact threshold, (c) Overshoot threshold, (d) Flat line |
| **5-Level OFI** | Bid/Ask Price Shifts | (a) Price unchanged (size delta), (b) Price higher (aggressive), (c) Price lower (retreat) |
| **CVD Divergence** | Swing Geometry | (a) Bullish sweep (Price LL, CVD HL), (b) Bearish sweep (Price HH, CVD LH), (c) Parallel |
| **OI Monitor** | Tick Window Drop | (a) Drop $\le 2.5\%$, (b) Drop $> 2.5\%$ in $<300$ ticks, (c) Drop $> 2.5\%$ in $>300$ ticks |
| **Hawkes Process** | Arrival Density | (a) Stationary baseline ($\lambda \approx \mu$), (b) Medium excitation ($1 < \text{ER} \le 3$), (c) Surge ($\text{ER} > 3$) |
| **Alpha Setups** | Entry Trigger | (a) Clear trigger conditions satisfied, (b) Partial match (1 condition missing), (c) Invalidation |
| **Risk Engine** | Requested Leverage | (a) Conservative ($5\text{x}$), (b) Boundary ceiling ($10\text{x}$), (c) Buffered ($15\text{x}$), (d) Excessive ($20\text{x}$) |
| **CPCV** | Event Intervals | (a) Disjoint intervals, (b) Overlapping evaluation span, (c) Embargo buffer boundary |

### 3.2 Boundary Value Analysis (BVA)
BVA tests the behavior of algorithms at exact mathematical boundaries:
- **Liquidation Distance**: Tested at $D_{\text{liq}} = 9.499\%$ (REJECT), $9.500\%$ (ACCEPT/BOUNDARY), $9.501\%$ (ACCEPT).
- **OFI VWAP Band**: Tested at $z_{\text{VWAP}} = 2.499\sigma$ (NO SIGNAL), $2.500\sigma$ (TRIGGER BOUNDARY), $2.501\sigma$ (TRIGGER).
- **OFI Rolling Z-Score**: Tested at $z_{\text{OFI}} = +0.799\sigma$ (NO SIGNAL), $+0.800\sigma$ (TRIGGER BOUNDARY), $+0.801\sigma$ (TRIGGER).
- **OI Contraction**: Tested at $\Delta OI = -2.49\%$ (NO TRIGGER), $-2.50\%$ (TRIGGER BOUNDARY), $-2.51\%$ (TRIGGER).
- **Hawkes Excitation Ratio**: Tested at $\text{ER} = 2.99$ (NO BREAKOUT), $3.00$ (BOUNDARY), $3.01$ (BREAKOUT CONFIRMED).
- **L2 Book Imbalance Ratio**: Tested at $\text{DIR} = 4.99$ (NO WALL), $5.00$ (BOUNDARY), $5.01$ (INSTITUTIONAL WALL).
- **Hard Stop-Loss Sizing**: Tested at exactly $0.3500\%$ offset from entry price.

### 3.3 Pairwise & Combinatorial Testing
High-order feature combinations can produce emergent edge-case failures. Pairwise testing evaluates:
- **OFI $\times$ Hawkes**: Negative OFI with high Hawkes excitation vs. Positive OFI with quiet arrival.
- **CVD Divergence $\times$ Expected MAE Veto**: Bullish CVD divergence triggering while historical MAE in temporal memory exceeds stop-loss.
- **Risk Engine $\times$ LLM Multiplier**: High conviction setup ($1.0\text{x}$) coupled with LLM defensive multiplier ($0.25\text{x}$) under high volatility regime.
- **Book Imbalance $\times$ Spread Distortion**: Massive bid wall ($>5\text{x}$) accompanied by anomalous Perp-Spot spread ($>50\text{ bps}$).

### 3.4 Real-World Workload Simulation
Simulates microstructural streaming environments:
- Replay of continuous L2 orderbook updates with high tick density (up to 5,000 updates/sec).
- Taker trade bursts with clustered arrival timestamps (power-law inter-arrival times).
- Cascading liquidation sequence: rapid price drop, volume surge with $90\%$ taker sells, open interest collapse $>3\%$.

---

## 4. Feature Coverage Inventory & Traceability Matrix

Every feature defined in `PROJECT.md` is mapped to its dedicated test suite in `tests/test_architecture_b.py`:

| # | Feature Name | Target Source Module | Required Test Cases (T1 + T2) | Key Verified Properties |
|---|---|---|:---:|---|
| **1** | Event-Driven Bar Builders | `quant/hft/data_layer/bars.py` | $\ge 10$ | Constant $\Delta P$ range bars, constant volume threshold, exact tick count, zero lookahead bias. |
| **2** | 5-Level Normalized OFI | `quant/hft/data_layer/ofi.py` | $\ge 10$ | Cont-Kukanov-Stoikov price shift logic across levels 0..4, rolling mean/variance, z-score stationarity. |
| **3** | CVD Tracking & Divergence | `quant/hft/data_layer/cvd.py` | $\ge 10$ | Trade-level volume delta accumulation, 10-bar bullish/bearish divergence scanning, relative volume filter. |
| **4** | OI Contraction Monitor | `quant/hft/data_layer/oi_monitor.py` | $\ge 10$ | FIFO 300-tick ring buffer, $>2.5\%$ OI drop calculation, mark price drop $>1.5\%$, taker sell ratio $>85\%$. |
| **5** | Hawkes Intensity & ER | `quant/hft/data_layer/hawkes.py` | $\ge 10$ | Exponential kernel decay, recursive updates, rolling median intensity, excitation ratio ($\text{ER} > 3.0$). |
| **6** | Basis Spread Estimators | `quant/hft/data_layer/basis.py` | $\ge 10$ | Mark vs. Mid spread (bps), Perp vs. Spot basis spread (bps), numerical stability with zero mid. |
| **7** | L2 Depth Imbalance | `quant/hft/data_layer/depth_imbalance.py` | $\ge 10$ | 99th percentile volume cluster identification, proximity constraint ($0.1\%$), $>5\text{x}$ imbalance ratio. |
| **8** | 5 Alpha Setups & Engine | `quant/hft/alpha/` | $\ge 15$ | Setups 1 to 5 entry triggers, deterministic hard SL calculation, invalidation triggers, alpha coordinator. |
| **9** | Polymorphic Filter Model | `filter_model.py` | $\ge 10$ | Backward compatibility with legacy `.npz`, CatBoost/LightGBM loading, median imputation, sub-10ms inference. |
| **10**| 256-Dim Temporal Memory | `quant/hft/memory/temporal_memory.py` | $\ge 10$ | Dual backend (NumPy cosine fallback & Qdrant), top-$k$ similarity search, metric normalization. |
| **11**| Expected MAE Evaluator | `quant/hft/memory/mae_evaluator.py` | $\ge 10$ | Top-$k$ historical trade retrieval, 85th percentile adverse excursion, veto decision when $\text{MAE} \ge \text{SL}$. |
| **12**| Capital Allocation Engine | `quant/hft/risk/engine.py` | $\ge 10$ | Leverage ceiling ($\le 10\text{x}$ isolated), liquidation distance $\ge 9.5\%$, 25% margin sizing, mandatory SL. |
| **13**| CDP Target Pinning | `signals/tv_cdp.py` | $\ge 10$ | Explicit `pinned_target_id` preservation across tab reordering and socket reconnections, `list_targets()`. |
| **14**| CPCV Module | `quant/hft/cpcv.py` | $\ge 10$ | Combinatorial grouping $\binom{N}{k}$, label overlap purging, post-test embargo window, zero data leakage. |
| **15**| LLM Critic Client | `quant/hft/critic/` | $\ge 10$ | Asynchronous non-blocking critique, $\mathcal{O}(1)$ cached risk multiplier read ($<1\ \mu\text{s}$), JSON parsing, fail-safe. |

---

## 5. Acceptance Thresholds & Formal Invariants

Any build or release candidate must satisfy $100\%$ of the following non-negotiable acceptance thresholds:

### 5.1 Mathematical & Risk Invariants
1. **Liquidation Distance Invariant**:
   $$\forall \text{ order approved by RiskEngine}: \quad D_{\text{liq}} = \frac{1}{L} - \text{MMR} \ge 0.0950 \quad (9.50\%)$$
2. **Capital Sizing Invariant**:
   $$\forall \text{ trade allocation}: \quad M_{\text{allocated}} \le 0.25 \times \text{Equity}, \quad \text{Buffer}_{\text{cash}} \ge 0.75 \times \text{Equity}$$
3. **Hard Stop-Loss Mandatory Invariant**:
   $$\forall \text{ emitted order}: \quad P_{\text{SL}} \text{ is not None} \quad \mathbf{and} \quad |P_{\text{entry}} - P_{\text{SL}}| < |P_{\text{entry}} - P_{\text{liq}}|$$
4. **CPCV Zero-Leakage Invariant**:
   $$\forall \text{ split } s \in \binom{N}{k}: \quad \text{TrainIndices}_s \cap \text{TestIndices}_s = \emptyset \quad \mathbf{and} \quad \text{OverlapPurged} = 100\%$$
5. **CDP Target Pinning Invariant**:
   $$\forall \text{ reconnect}: \quad \text{ConnectedTargetID} == \text{PinnedTargetID}$$

### 5.2 Latency & Performance Gates
1. **ML Filter Single-Call Latency**:
   - $P_{50} \le 1.0\text{ ms}$
   - $P_{99} < 10.0\text{ ms}$ (Target: $< 1.0\text{ ms}$)
   - Max latency $< 20.0\text{ ms}$ across 1,000 continuous evaluations.
2. **LLM Critic Multiplier Query**:
   - Execution time of `get_current_risk_multiplier()` $\le 10\ \mu\text{s}$ (atomic memory read).

### 5.3 Backward Compatibility Invariant
- Legacy unit test suite (`tests/test_filter.py`) must pass with **$6/6$ ($100\%$) passing** and zero regressions.

---

## 6. Test Suite Execution & Diagnostics

### Standard Test Execution Commands
```bash
# 1. Execute full Architecture B test suite (Tiers 1-4 + Latency Benchmark)
pytest -v tests/test_architecture_b.py

# 2. Execute legacy regression suite
pytest -v tests/test_filter.py

# 3. Execute combined test verification
pytest -v tests/test_architecture_b.py tests/test_filter.py
```

### Diagnostic Logging & Telemetry
In `tests/test_architecture_b.py`, each test run logs timing metrics, memory allocations, and boundary state evaluations to facilitate rapid fault isolation during continuous integration.
