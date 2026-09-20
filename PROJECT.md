# Project: Wall-Street-Nightmare Architecture B Evolution & VPS Deployment

## Architecture
Architecture B transitions the Wall-Street-Nightmare autonomous scalping engine into an event-driven, microstructural orderflow system coupled with sub-10ms probability filtering, temporal RAG memory, asynchronous local LLM regime criticism, and institutional risk management:

```
Hyperliquid L2 Book & Trade WS Stream
                │
                ▼
┌────────────────────────────────────────────────────────┐
│  Data Layer & Feature Engine (quant/hft/data_layer/)   │
│  - RangeBar / VolumeBar / TickBar Builders             │
│  - 5-Level Cont-Kukanov-Stoikov OFI + Rolling Z-Score  │
│  - Real-Time Trade-Level CVD & 10-Bar Divergence       │
│  - 300-Tick OI Contraction Monitor (>2.5% drop)        │
│  - Hawkes Intensity with Exponential Decay & ER Ratio  │
│  - Basis Spread Estimators (Mark/Mid, Perp/Spot)       │
│  - L2 Depth Imbalance Estimator (Top 1% clusters)      │
└────────────────────────────────────────────────────────┘
                │
                ▼
┌────────────────────────────────────────────────────────┐
│  Deterministic Alpha Setups (quant/hft/alpha/)         │
│  - Setup 1: OFI VWAP Reversion (±2.5σ, SL 0.35%)       │
│  - Setup 2: Liquidation Cascade Absorption (SL low)    │
│  - Setup 3: Hawkes Volatility Breakout (HMA(9) ratchet)│
│  - Setup 4: CVD Divergence Sweep (SL below wick)       │
│  - Setup 5: L2 Depth Imbalance Scalp (SL below cluster)│
└────────────────────────────────────────────────────────┘
                │
                ▼
┌────────────────────────────────────────────────────────┐
│  Execution Filter & Temporal Memory                    │
│  - FilterModel: 100% legacy .npz + LightGBM/CatBoost   │
│  - Latency: Verified Sub-10ms inference benchmark      │
│  - 256-Dim RAG Memory: Top-K cosine similarity search   │
│  - Expected MAE Evaluator & Hard SL Veto Gate          │
│  - CPCV: Combinatorial Purged K-Fold Cross-Validation  │
└────────────────────────────────────────────────────────┘
                │
                ▼
┌────────────────────────────────────────────────────────┐
│  Risk Engine & Target Pinning (quant/hft/risk/)        │
│  - Leverage: Isolated margin clamped to ≤10x (liq≥9.5%)│
│  - Position Sizing: 25% Margin Sizing (75% Cash Buffer)│
│  - Deterministic Hard SL Verification Gate             │
│  - TradingView CDP: Pinned Tab Target ID caching       │
└────────────────────────────────────────────────────────┘
                │
                ▲ (Asynchronous cached risk multiplier read <1 µs)
┌────────────────────────────────────────────────────────┐
│  Asynchronous Local LLM Critic (deploy/ & VPS)         │
│  - Isolated llama-server: Qwen2.5-1.5B Q4_K_M          │
│  - Flags: -t 2, -ngl 0, --mlock, --ctx-size 2048       │
│  - Systemd: stratton-llm-critic (LimitMEMLOCK=infinity)│
│  - Rolling 20-trade JSON critique & risk scaling       │
│  - Strict VPS Memory Constraint: Total RAM ≤ 2.5 GB    │
└────────────────────────────────────────────────────────┘
```

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Event-Driven Bar Builders | RangeBar, VolumeBar, TickBar builders with zero lookahead | M1 | ORIGINAL_REQUEST §R1 |
| 2 | 5-Level Normalized OFI | Multi-level OFI with price change logic & rolling z-score | M1 | ORIGINAL_REQUEST §R1 |
| 3 | CVD Tracking & Divergence | Trade-level volume delta accumulator & 10-bar divergence scanner | M1 | ORIGINAL_REQUEST §R1 |
| 4 | OI Contraction Monitor | FIFO ring buffer tracking >2.5% drop in <300 ticks | M1 | ORIGINAL_REQUEST §R1 |
| 5 | Hawkes Intensity & ER | Hawkes process with exponential kernel & excitation ratio vs median | M1 | ORIGINAL_REQUEST §R1 |
| 6 | Basis Spread Estimators | Real-time Mark vs Mid and Perp vs Spot basis spreads in bps | M1 | ORIGINAL_REQUEST §R1 |
| 7 | L2 Depth Imbalance | Top 1% volume cluster detection & >5x bid/ask imbalance within 0.1% | M1 | ORIGINAL_REQUEST §R1 |
| 8 | OFI VWAP Reversion | Alpha Setup 1: ±2.5σ VWAP band + \|z_OFI\| > 0.8σ, SL 0.35% | M2 | ORIGINAL_REQUEST §R2 |
| 9 | Liquidation Absorption | Alpha Setup 2: OI drop >2.5% in <300 ticks, Mark drop >1.5%, sell >85% | M2 | ORIGINAL_REQUEST §R2 |
| 10 | Hawkes Breakout | Alpha Setup 3: 100-bar breach + intensity >3x median, HMA(9) ratchet | M2 | ORIGINAL_REQUEST §R2 |
| 11 | CVD Divergence Sweep | Alpha Setup 4: Price LL vs CVD HL over 10 bars, rel vol >1.5x | M2 | ORIGINAL_REQUEST §R2 |
| 12 | L2 Depth Scalp | Alpha Setup 5: Top 1% LOB bids >5x asks within 0.1%, SL below cluster | M2 | ORIGINAL_REQUEST §R2 |
| 13 | Polymorphic Filter Model | Sub-10ms FilterModel for legacy .npz, CatBoost, LightGBM | M3 | ORIGINAL_REQUEST §R3 |
| 14 | 256-Dim RAG Memory | Dual backend Qdrant / pure NumPy InMemoryCosineMemory | M3 | ORIGINAL_REQUEST §R3 |
| 15 | Expected MAE Veto | Historical MAE evaluation and hard SL veto gate | M3 | ORIGINAL_REQUEST §R3 |
| 16 | Isolated llama-server | Sub-3B Q4_K_M model with -t 2, -ngl 0, --mlock, --ctx-size 2048 | M4 | ORIGINAL_REQUEST §R4 |
| 17 | Async Critic Decoupling | Background async critique loop, synchronous O(1) cached multiplier read | M4 | ORIGINAL_REQUEST §R4 |
| 18 | Rolling 20-Trade Critic | Prompt generator & parser for rolling 20-trade JSON critique | M4 | ORIGINAL_REQUEST §R4 |
| 19 | Capital Allocation Engine | Leverage ceiling (≤10x isolated, dist≥9.5%), 25% margin, hard SL gate | M4 | ORIGINAL_REQUEST §R5 |
| 20 | CDP Target Pinning | Pinned target ID caching in signals/tv_cdp.py to prevent window swap | M4 | ORIGINAL_REQUEST §R5 |
| 21 | CPCV Module | Combinatorial Purged K-Fold with overlapping label purge & embargo | M3 | ORIGINAL_REQUEST §R5 |
| 22 | VPS Code Deployment | Sync pipeline to /root/ict_sniper on 82.115.21.155 | M5 | ORIGINAL_REQUEST §R6 |
| 23 | LLM Service Configuration | Systemd unit stratton-llm-critic with LimitMEMLOCK=infinity | M5 | ORIGINAL_REQUEST §R6 |
| 24 | VPS Verification & Telemetry| Memory ≤2.5 GB verification, port 8080 health, and live HFT WS stream | M5 | ORIGINAL_REQUEST §R6 |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| E2E | E2E Testing Track | Design 4-tier test infra & test suite in tests/test_architecture_b.py, publish TEST_READY.md | none | IN_PROGRESS |
| M1 | Data Layer & Feature Engine | Event bars (Range, Volume, Tick), 5-level OFI + z-score, CVD, OI contraction, Hawkes ER, basis spreads, L2 depth | none | PLANNED |
| M2 | Deterministic Alpha Setups | 5 candidate alpha setups (setups.py & alpha_engine.py) with entry conditions and invalidations | M1 | PLANNED |
| M3 | ML Filter, RAG Memory & CPCV| Polymorphic filter_model.py, 256-dim temporal memory with cosine fallback, expected MAE veto, CPCV module | none | PLANNED |
| M4 | Risk Engine, CDP Pinning & Critic| Leverage ceiling (≤10x isolated, dist≥9.5%), 25% margin sizing, hard SL gate, CDP tab pinning, async LLM critic client | M2, M3 | PLANNED |
| M5 | Production VPS Deployment | Deploy to 82.115.21.155, install llama-server, systemd service, verify RAM ≤2.5 GB & live WS | M1, M2, M3, M4, TEST_READY | PLANNED |
| FINAL| Final Acceptance & Hardening | Pass 100% E2E test suite (Tiers 1-4) and adversarial coverage hardening (Tier 5) | M5, TEST_READY | PLANNED |

## Interface Contracts

### quant/hft/data_layer ↔ quant/hft/alpha
- `Bar` dataclass: `timestamp: float, open: float, high: float, low: float, close: float, volume: float, tick_count: int`
- `MultiLevelOFIEngine.update(bids: list[tuple[float, float]], asks: list[tuple[float, float]]) -> OFIResult`:
  `OFIResult`: `ofi_raw: np.ndarray, ofi_normalized: float (rolling z-score)`
- `CVDTracker.on_trade(px: float, sz: float, side: str, ts: float) -> float`: updates cumulative volume delta
- `CVDDivergenceDetector.check_divergence(bars: list[Bar], cvd_series: list[float]) -> tuple[bool, str]`: returns `(has_divergence, sweep_type)`
- `OIMonitor.update(oi: float, mark_px: float, taker_sell_vol: float, total_vol: float) -> tuple[bool, float, float, float]`:
  returns `(is_contracting, oi_pct_drop, mark_pct_drop, taker_sell_ratio)`
- `HawkesProcess.update(ts: float, side: str) -> tuple[float, float]`: returns `(current_intensity, excitation_ratio_vs_median)`
- `BasisSpreadEstimator.compute(mark_px: float, mid_px: float, perp_px: float, spot_px: float) -> tuple[float, float]`:
  returns `(mark_mid_bps, perp_spot_bps)`
- `L2DepthImbalanceEstimator.compute(bids: list[tuple[float, float]], asks: list[tuple[float, float]], mid: float) -> tuple[bool, float]`:
  returns `(has_top1pct_imbalance, bid_ask_ratio)`

### quant/hft/alpha ↔ quant/hft/risk & quant/hft/memory
- `CandidateSignal` dataclass:
  `setup_name: str, symbol: str, direction: str, entry_price: float, hard_sl: float, size_multiplier: float, feature_vector: np.ndarray (256-dim), timestamp: float`
- `AlphaEngine.evaluate_tick(state: MarketState) -> list[CandidateSignal]`

### quant/hft/memory ↔ quant/hft/risk
- `TemporalMemoryBackend.query_historical(embedding: np.ndarray, top_k: int = 20) -> list[HistoricalTrade]`
- `HistoricalTrade`: `entry_px: float, exit_px: float, mae_pct: float, pnl_pct: float, outcome: int`
- `ExpectedMAEEvaluator.evaluate(embedding: np.ndarray, proposed_sl_pct: float) -> tuple[bool, float, float]`:
  returns `(is_vetoed, expected_mae_pct, win_rate)`. Veto is `True` if `expected_mae_pct >= proposed_sl_pct`.

### quant/hft/risk ↔ Execution
- `RiskEngine.validate_order(signal: CandidateSignal, equity: float, open_positions: list) -> tuple[bool, OrderParameters, str]`:
  - Enforces `leverage <= 10.0` (guaranteeing liquidation distance $\ge 9.5\%$ with MMR 0.5%).
  - Enforces `margin = 0.25 * equity`.
  - Enforces `signal.hard_sl is not None and signal.hard_sl > 0`.
  - Returns `(approved, OrderParameters, rejection_reason)`.

### quant/hft/critic ↔ quant/hft/risk
- `LLMCriticClient.get_current_risk_multiplier() -> float`:
  Synchronous $\mathcal{O}(1)$ read of atomic cached float in `[0.0, 1.5]`.
- `LLMCriticClient.submit_trade_summary_async(trade_history: list[dict]) -> None`:
  Non-blocking queue submission consumed by background worker.

### signals/tv_cdp.py
- `TradingViewCDP(target_id: str | None = None, ...)`:
  Upon first connection, records `self.pinned_target_id = target["id"]`. On reconnects, matches `pinned_target_id` across tab shifts.

## Code Layout
```
quant/hft/
├── data_layer/
│   ├── __init__.py
│   ├── bars.py               # RangeBarBuilder, VolumeBarBuilder, TickBarBuilder, Bar
│   ├── ofi.py                # MultiLevelOFIEngine (5 levels, CKS math, rolling z-score)
│   ├── cvd.py                # CVDTracker & CVDDivergenceDetector (10-bar sweep)
│   ├── oi_monitor.py         # OIMonitor (300-tick FIFO ring buffer, >2.5% drop)
│   ├── hawkes.py             # HawkesProcess with rolling median & excitation ratio
│   ├── basis.py              # BasisSpreadEstimator (Mark/Mid, Perp/Spot bps)
│   └── depth_imbalance.py    # L2DepthImbalanceEstimator (top 1% cluster, >5x ratio)
├── alpha/
│   ├── __init__.py
│   ├── setups.py             # 5 deterministic candidate alpha setups
│   └── alpha_engine.py       # Tick evaluator generating CandidateSignals
├── memory/
│   ├── __init__.py
│   ├── temporal_memory.py    # TemporalMemoryBackend, InMemoryCosineMemory, QdrantMemoryBackend
│   └── mae_evaluator.py      # ExpectedMAEEvaluator & Hard SL Veto Gate
├── risk/
│   ├── __init__.py
│   ├── engine.py             # Leverage ceiling (≤10x), 25% margin sizing, hard SL gate
│   └── kelly.py              # Fractional Kelly with LLM risk multiplier integration
├── critic/
│   ├── __init__.py
│   ├── llama_client.py       # Async llama.cpp client & cached risk multiplier
│   └── trade_critic.py       # Rolling 20-trade JSON critique generator & prompt manager
├── cpcv.py                   # CombinatorialPurgedKFold with purge & embargoing
├── run_hft.py                # Integration entry point
└── guard.py                  # Telemetry, RAM monitor (≤2.5 GB), service monitor

filter_model.py               # Polymorphic FilterModel (legacy .npz + CatBoost/LightGBM)
signals/tv_cdp.py             # TradingViewCDP with explicit tab target ID pinning
deploy/
├── stratton-llm-critic.service # Systemd service unit (LimitMEMLOCK=infinity)
└── install_llama.sh          # VPS installation script for llama-server and Q4_K_M GGUF
tests/
├── test_filter.py            # Legacy filter unit tests (must pass with zero regressions)
└── test_architecture_b.py    # Comprehensive Architecture B test suite (Tiers 1-4)
```
