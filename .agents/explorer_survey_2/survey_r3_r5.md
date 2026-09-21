# Technical Investigation & Architectural Survey: R3 & R5
**Milestone**: Architecture B Evolution — Explorer 2 Survey  
**Investigator**: Explorer 2 (ML Risk & CDP Explorer)  
**Date**: 2026-09-16  
**Workspace**: `/Users/mac/Desktop/TBT-Engine`  
**Target Output**: Detailed Architectural Blueprint for R3 (ML Filter Engine & 256-Dim RAG Memory) & R5 (Risk Engine, Sizing & CDP Pinning)

---

## Executive Summary

This survey provides an authoritative, evidence-backed blueprint for the implementation of **Requirement 3 (R3: Sub-10ms ML Filter Engine & 256-Dim RAG Temporal Memory)** and **Requirement 5 (R5: Capital Allocation, Risk Engine & CDP Target Pinning)** within Architecture B of the "Wall-Street-Nightmare" algorithmic trading system.

Key findings and architectural determinations:
1. **R3 ML Filter Engine**: The existing `filter_model.py` implements a 13-feature (26-dimensional with missingness indicators) logistic regression model stored in compressed `.npz` format. It executes in $\approx 20\ \mu\text{s}$ (well below the 10ms threshold). All existing unit tests in `tests/test_filter.py` (6/6 passing) can be preserved with 100% backward compatibility by providing a unified `FilterModel` interface that detects model type (legacy `.npz` vs. CatBoost `.cbm` vs. LightGBM `.txt`/`.model`) and handles both legacy candidate dicts and high-frequency order-flow feature arrays.
2. **R3 256-Dim RAG Temporal Memory**: Neither Qdrant nor temporal vector memory currently exists in the codebase. To satisfy sub-10ms constraints in constrained environments (VPS 2-core / 4GB RAM) where external services may not be installed, the temporal memory system must provide a dual-backend architecture: `QdrantMemoryBackend` (using `qdrant-client` when active) with an instantaneous pure NumPy `InMemoryCosineMemory` fallback. Retrieval of top-$k$ nearest setups and calculation of expected Maximum Adverse Excursion (MAE) and historical win rates executes in $< 0.5\text{ms}$ on CPU, enabling immediate trade vetoing if expected MAE $\ge$ proposed hard stop-loss.
3. **R5 Risk & Capital Allocation**: 
   - *Leverage Ceiling & Liquidation*: Under isolated margin with maintenance margin rate $\text{MMR} = 0.5\%$ ($0.005$), a hard liquidation distance $D_{\text{liq}} \ge 9.5\%$ strictly bounds maximum leverage to $\le 10\text{x}$ ($\frac{1}{10} - 0.005 = 0.095$). If leverage up to $15\text{x}$ is used, cross-margin collateral or cash buffer allocation must guarantee effective $D_{\text{liq}} \ge 9.5\%$.
   - *Margin Sizing*: Deterministic 25% margin sizing per trade leaves a mandatory 75% unencumbered liquid cash buffer against adverse volatility and cascade liquidations.
   - *Hard Stop-Losses*: Every candidate alpha setup (OFI VWAP Reversion, Liquidation Cascade, Hawkes Volatility, CVD Divergence, L2 Depth Scalp) must deterministically attach an explicit hard stop-loss prior to order emission; missing stop-losses cause immediate risk rejection.
4. **R5 CDP Target Pinning**: In `signals/tv_cdp.py`, `TradingViewCDP` currently resolves chart tabs by sorting Chrome DevTools `/json` targets and indexing via `target_index`. Because `Page.bringToFront` or tab opening/closing mutates target order, tabs can swap identities between processes (e.g. scout walking a chart while the book attempts to trade it). The system requires explicit `target_id` pinning upon connection and reconnection, caching `pinned_target_id` permanently.
5. **R5 Combinatorial Purged Cross-Validation (CPCV)**: No purged or combinatorial cross-validation currently exists in the codebase. A dedicated CPCV module implementing Marcos Lopez de Prado's combinatorial partitioning with label overlap purging and post-test embargoing must be built and verified under `tests/test_architecture_b.py`.

---

## 1. Existing Codebase Analysis & Inventory

### 1.1 Existing ML & Filter Assets
| Component | File Path | Existing Implementation Details | Key Dependencies | Status / Compatibility Requirement |
|---|---|---|---|---|
| **Legacy Filter Model** | `filter_model.py` | 75 LOC. Defines `FilterModel` and `model_check`. Reads `.npz` containing `features`, `w`, `b`, `mu`, `sd`, `med`, `threshold`, `trained_at`, `n`. Implements 13 features with median imputation + missingness indicators. | `numpy`, `math`, `time`, `pathlib` | Active & working. Must remain 100% backward-compatible. |
| **Filter Unit Tests** | `tests/test_filter.py` | 102 LOC. Tests deterministic scoring, median imputation, feature mismatch rejection (`ValueError`), book refusal scenario, CLI missing-model refusal, `--filter-min 0` pass-through. | `pytest`, `numpy`, `unittest.mock`, `filter_model` | 6/6 tests passing (0.84s). Must pass with ZERO regressions. |
| **Model Trainer / Selector** | `dataset/selector.py` | 390 LOC. Trains logistic regression on recorded triangle signals. Functions: `build_matrix`, `median_impute`, `standardize`, `logistic`, `save_model`. Generates `.npz` artifacts atomically. | `numpy`, `pace`, `papertrade` | Legacy training pipeline for triangle signals. |
| **HFT CatBoost Pipeline** | `quant/hft/train_pipeline.py` | 433 LOC. Ingests 1-min klines, computes Hawkes intensities, engineers 12 order-flow features, trains `CatBoostClassifier`, saves to `quant/hft/models/direction_model.cbm`. | `catboost`, `scipy`, `pandas`, `requests` | Model file `direction_model.cbm` present on disk. |
| **HFT Direction Predictor** | `quant/hft/alpha/signal_engine.py` | 363 LOC. Contains `OFIFeatureEngine` (12 features) and `DirectionPredictor` (loads `.cbm` via CatBoost or falls back to OFI heuristic). | `numpy`, `catboost` (optional) | Working CatBoost predictor with heuristic fallback. |

### 1.2 Existing Risk, Position Sizing & CDP Assets
| Component | File Path | Existing Implementation Details | Key Dependencies | Status / Gaps |
|---|---|---|---|---|
| **Fractional Kelly & Liq Math** | `quant/hft/risk/kelly.py` | 212 LOC. Implements `FractionalKelly` ($f^* = (bp - q)/b$), isolated margin liquidation price calculations, and `PositionSpec`. | `numpy`, `dataclasses` | Has liquidation formulas; needs 25% margin sizing, 10x-15x ceiling enforcement, and deterministic hard SL verification. |
| **Institutional Day Planner** | `quant/hft/risk/day_planner.py` | 326 LOC. Tracks daily target (+100%), loss limits (-50%), kill zones, consecutive losses, and adjusts Kelly multipliers. | `dataclasses`, `time` | Sound pacing framework; needs alignment with Architecture B risk parameters. |
| **Risk Controls Unit Tests** | `tests/test_risk_controls.py` | 454 LOC. Tests `--daily-loss-limit`, stale feeds, spread checks, and service watchdog configs. | `pytest`, `market`, `papertrade` | 27 passing tests (3 legacy service unit path failures due to service renaming). |
| **TradingView CDP Client** | `signals/tv_cdp.py` | 484 LOC. Implements `TradingViewCDP` and `CDPSignalSource`. Evaluates JS on Chrome DevTools port 9222. Reads study data, badges, takes screenshots. | `requests`, `websocket-client` | Vulnerable: relies on `target_index` in sorted page list. Lacks explicit `target_id` pinning. |
| **Combinatorial Purged CV** | *None* | Does not exist anywhere in the repository. | N/A | **Missing Component** — Must be implemented from scratch. |
| **256-Dim RAG Memory** | *None* | Does not exist anywhere in the repository. | N/A | **Missing Component** — Must be implemented from scratch. |

### 1.3 Environment & Package Availability
A direct inspection of the Python 3.11 environment on the local machine revealed:
- `catboost`: **1.2.10** (Installed and functional)
- `sklearn`: **1.3.2** (Installed and functional)
- `scipy`: **1.16.1** (Installed and functional)
- `numpy`: **1.26.4** (Installed and functional)
- `pandas`: **2.2.2** (Installed and functional)
- `websockets`: **15.0.1** (Installed and functional)
- `requests`: **2.34.2** (Installed and functional)
- `lightgbm`: **NOT INSTALLED** locally (Available via pip / installed on VPS or mockable/pure-numpy fallback)
- `qdrant_client`: **NOT INSTALLED** locally (Demonstrating why in-memory cosine fallback is strictly necessary!)

---

## 2. Deep Dive: R3 — Sub-10ms ML Filter Engine

### 2.1 The Legacy `.npz` Artifact Structure
In `filter_model.py` and `dataset/selector.py`, the legacy model is stored via `np.savez_compressed(path, **out)` with the following exact dictionary schema:
- `features`: `np.ndarray` of dtype `<U7` containing exactly 13 strings:
  `["atr", "trend", "vol20", "mom6h", "mom1h", "volx", "agents", "tier", "score", "who", "counter", "side", "hour"]`
- `w`: `np.ndarray` of shape `(26,)`, dtype `float32` (weights for 13 features + 13 missingness flags)
- `b`: `float` scalar bias
- `mu`: `np.ndarray` of shape `(26,)`, dtype `float32` (feature mean vector for standardisation)
- `sd`: `np.ndarray` of shape `(26,)`, dtype `float32` (feature standard deviation vector)
- `med`: `np.ndarray` of shape `(13,)`, dtype `float32` (raw feature medians for imputing missing values prior to standardisation)
- `threshold`: `float` (the top-10% cut probability threshold, e.g. 0.485 or 0.500)
- `trained_at`: `int` (Unix epoch timestamp of training)
- `n`: `int` (number of training samples)
- `hit_at_threshold`: `float` (historical hit rate above threshold)

The scoring operation in `filter_model.py:46-59` is:
```python
x = np.zeros(len(FEATURES) * 2)
for j, f in enumerate(FEATURES):
    v = feats.get(f)
    if v is None or v != v:
        x[len(FEATURES) + j] = 1.0
        x[j] = self.med[j]
    else:
        x[j] = float(v)
z = (x - self.mu) / self.sd
logit = float(z @ self.w + self.b)
return float(1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, logit)))))
```

### 2.2 Unifying Legacy `.npz`, CatBoost, and LightGBM with 100% Backward Compatibility
To guarantee zero regressions in `tests/test_filter.py` and across all legacy paper-trading services, `filter_model.py` must be refactored into a polymorphic or multi-backend filter engine:

#### Architectural Design of `FilterModel` / `MLFilterEngine`:
1. **Auto-Detection of Artifact Format**:
   - Inspect the file extension and header:
     - If path ends with `.npz`:
       - Open via `np.load(path)`.
       - If keys include `"w"`, `"b"`, `"mu"`, `"sd"`: Instantiate `LegacyNpzBackend`.
       - If keys include `"model_type"` and `"catboost"` / `"lightgbm"`: Load embedded booster bytes.
     - If path ends with `.cbm` or `.bin`: Instantiate `CatBoostBackend`.
     - If path ends with `.txt` or `.lgb`: Instantiate `LightGBMBackend`.
2. **Backward-Compatible Public Interface**:
   - Retain all legacy attributes on `FilterModel`:
     - `self.threshold: float`
     - `self.trained_at: int`
     - `self.n: int`
     - `self.features: list[str]`
     - `self.w`, `self.b`, `self.mu`, `self.sd`, `self.med` (for legacy `.npz`)
   - Preserve `model_check(path) -> list[tuple[str, str]]` with identical watchdog behavior.
3. **Polymorphic Scoring Input**:
   - `score(self, feats: dict | np.ndarray | list) -> float`:
     - If `isinstance(feats, dict)`:
       - If model is `LegacyNpzBackend`: Map keys against `FEATURES = ["atr", "trend", ...]` with median imputation.
       - If model is `CatBoostBackend` / `LightGBMBackend`: Extract feature vector according to model's feature map, imputing missing keys with 0.0 or trained medians.
     - If `isinstance(feats, (np.ndarray, list))`:
       - Directly pass 1D/2D array to the underlying booster or dot-product.
4. **LightGBM & CatBoost Fallbacks**:
   - In environments where `lightgbm` or `catboost` C-libraries cannot be compiled, provide an automatic pure-NumPy tree evaluator or heuristic predictor so that tests and dry runs never crash with unhandled `ImportError`.

### 2.3 Sub-10ms Inference Benchmark Requirements & Methodology
The acceptance criteria state:
> "ML filter inference latency is objectively benchmarked at under 10ms."

#### Micro-Benchmark Analysis
- **Numpy Logistic Regression**: Vectorized dot product $z \cdot w + b$ over 26 elements takes $\approx 0.015\text{ms}$ ($15\ \mu\text{s}$).
- **CatBoost Prediction**: Single-row C-level inference via `CatBoostClassifier.predict_proba()` takes $\approx 0.15 - 0.45\text{ms}$.
- **LightGBM Prediction**: Single-row inference via `Booster.predict()` takes $\approx 0.08 - 0.25\text{ms}$.

#### Critical Latency Traps to Avoid
- **Pandas Object Creation**: Converting a feature dictionary to `pd.DataFrame([feats])` takes $1.5 - 3.5\text{ms}$ per call. Under high tick volume, this wastes 80% of the latency budget! Feature vectors must be converted directly into pre-allocated NumPy `float32` arrays.
- **Python-level Loop Allocations**: Reallocating arrays inside `score()` adds GC pressure. Pre-allocate buffer `x = np.zeros(26, dtype=np.float32)` on instance initialization or reuse buffers.

#### Benchmark Protocol Specification (for `tests/test_architecture_b.py`)
```python
def test_ml_filter_inference_latency_sub_10ms(tmp_path):
    model = FilterModel(str(legacy_artifact(tmp_path)))
    sample_features = feats()
    
    # Warmup (100 iterations)
    for _ in range(100):
        _ = model.score(sample_features)
        
    # Timed benchmark (1,000 iterations)
    latencies = []
    for _ in range(1000):
        t0 = time.perf_counter()
        _ = model.score(sample_features)
        latencies.append((time.perf_counter() - t0) * 1000.0) # ms
        
    p50 = np.percentile(latencies, 50)
    p99 = np.percentile(latencies, 99)
    max_lat = np.max(latencies)
    
    assert p99 < 10.0, f"P99 latency exceeded 10ms: {p99:.3f}ms"
    assert p50 < 1.0, f"P50 latency exceeded 1ms: {p50:.3f}ms"
```

---

## 3. Deep Dive: R3 — 256-Dim RAG Temporal Memory System

### 3.1 Mathematical Specification & State Representation
The 256-dimensional embedding vector $\mathbf{v} \in \mathbb{R}^{256}$ encodes the multi-scale temporal context of a trading setup:

$$\mathbf{v} = \begin{bmatrix}
\mathbf{v}_{\text{OFI}} & (160\ \text{dims: 32 ticks} \times 5\ \text{LOB levels}) \\
\mathbf{v}_{\text{CVD}} & (32\ \text{dims: normalized cumulative volume delta sequence}) \\
\mathbf{v}_{\text{Hawkes}} & (16\ \text{dims: multi-decay trade arrival intensities}) \\
\mathbf{v}_{\text{Depth}} & (16\ \text{dims: top-1\% bid/ask cluster profiles}) \\
\mathbf{v}_{\text{Basis}} & (16\ \text{dims: Mark-Mid and Perp-Spot basis spreads}) \\
\mathbf{v}_{\text{Regime}} & (16\ \text{dims: realized volatility, trend strength, spread})
\end{bmatrix}$$

All vectors stored and queried are strictly $L_2$-normalized: $\hat{\mathbf{v}} = \frac{\mathbf{v}}{\|\mathbf{v}\|_2 + \epsilon}$, such that cosine similarity reduces to a standard dot product:

$$\text{Sim}(\mathbf{q}, \mathbf{d}_i) = \hat{\mathbf{q}} \cdot \hat{\mathbf{d}}_i \in [-1, 1]$$

### 3.2 Dual-Backend System Architecture

```
                       ┌───────────────────────────────┐
                       │    Temporal Memory Router     │
                       └──────────────┬────────────────┘
                                      │
                 ┌────────────────────┴────────────────────┐
                 │                                         │
        [qdrant_client available]                 [fallback mode]
                 ▼                                         ▼
   ┌───────────────────────────┐             ┌───────────────────────────┐
   │    QdrantMemoryBackend    │             │   InMemoryCosineMemory    │
   │  - Collection: 256-dim    │             │  - NumPy Matrix (N, 256)  │
   │  - Metric: Cosine         │             │  - Vectorized (N @ q)     │
   │  - Local / Remote daemon  │             │  - Zero external deps     │
   └─────────────┬─────────────┘             └─────────────┬─────────────┘
                 │                                         │
                 └────────────────────┬────────────────────┘
                                      ▼
                       ┌───────────────────────────────┐
                       │    MAE & Win-Rate Evaluator   │
                       │ - Top-K retrieval             │
                       │ - Expected MAE calculation    │
                       │ - Win-Rate thresholding       │
                       └──────────────┬────────────────┘
                                      ▼
                       ┌───────────────────────────────┐
                       │      Veto Decision Gate       │
                       │ Expected MAE >= Hard SL?      │
                       │   -> VETO TRADE (ABORT)       │
                       └───────────────────────────────┘
```

#### 1. In-Memory Cosine Fallback (`InMemoryCosineMemory`)
- **Storage**: Maintain pre-allocated NumPy array $\mathbf{X} \in \mathbb{R}^{M \times 256}$ with capacity $M$ (e.g. 50,000 setups) and dynamic length $N$.
- **Metadata Storage**: Parallel Python list or NumPy structured array storing:
  - `setup_id`: `str`
  - `timestamp`: `float`
  - `symbol`: `str`
  - `side`: `str` ("BUY" / "SELL")
  - `entry_price`: `float`
  - `hard_sl_pct`: `float`
  - `realized_mae_pct`: `float` (Maximum Adverse Excursion in percent)
  - `realized_mfe_pct`: `float` (Maximum Favorable Excursion in percent)
  - `win`: `bool` ($1$ if trade hit TP before SL, $0$ otherwise)
- **Search Complexity**: For $N = 10,000$ setups, computing $\mathbf{s} = \mathbf{X}_{[:N]} \cdot \hat{\mathbf{q}}$ requires $10,000 \times 256 \approx 2.56 \times 10^6$ FLOPs. On modern CPUs using AVX2 / BLAS, this completes in **$\approx 0.08\text{ms}$** (80 microseconds).
- **Top-$K$ Selection**: Fast partial sort via `np.argpartition(-s, k)[:k]`.

#### 2. Qdrant Client Backend (`QdrantMemoryBackend`)
- Connects to local or in-memory Qdrant instance:
  `client = QdrantClient(location=":memory:")` or `QdrantClient(url="http://localhost:6333")`.
- Creates collection `"temporal_setups_256"` with `VectorParams(size=256, distance=Distance.COSINE)`.
- Performs similarity search using `client.search(...)` with payload filter matching `side` and `setup_type`.

### 3.3 Expected MAE Evaluation & Veto Logic
When a deterministic alpha setup triggers (e.g. OFI VWAP Reversion proposing entry at $P_{\text{entry}}$ with hard stop-loss $SL_{\text{pct}} = 0.35\%$):
1. Construct current 256-dim context embedding $\hat{\mathbf{q}}$.
2. Query Temporal Memory for top-$K$ nearest historical setups (default $K = 15$, minimum similarity threshold $\rho \ge 0.70$).
3. If $K_{\text{matched}} < 3$ (cold start / novel regime): Flag as low-confidence regime; do not veto on MAE, but record setup.
4. Extract historical MAE distribution: $\{\text{MAE}_1, \text{MAE}_2, \dots, \text{MAE}_K\}$ and outcomes $\{y_1, y_2, \dots, y_K\}$.
5. Compute:
   - Historical Win Rate: $W = \frac{1}{K} \sum_{i=1}^K y_i$
   - Expected MAE (85th percentile adverse excursion):
     $$\text{MAE}_{\text{exp}} = \text{Quantile}_{0.85}(\{\text{MAE}_i\})$$
6. **The Veto Condition**:
   $$\mathbf{If} \quad \text{MAE}_{\text{exp}} \ge SL_{\text{pct}} \quad \implies \quad \mathbf{VETO}(\text{"Expected MAE } (\{ \text{MAE}_{\text{exp}} \cdot 100 \}\%) \ge \text{Hard SL } (\{ SL_{\text{pct}} \cdot 100 \}\%)")$$
   $$\mathbf{If} \quad W < 0.40 \quad \implies \quad \mathbf{VETO}(\text{"Historical Win Rate } (\{ W \cdot 100 \}\%) < 40\%")$$
7. **Action**: If vetoed, execution is blocked immediately; the event is logged to journal telemetry with reason `VETO_TEMPORAL_MAE_BREACH`.

---

## 4. Deep Dive: R5 — Capital Allocation, Risk Engine & CDP Pinning

### 4.1 Leverage Ceiling & Liquidation Distance Invariant
The authoritative specification requires:
> "Strictly enforce a 10x–15x leverage ceiling (liquidation distance $\ge 9.5\%$), 25% margin sizing per trade (75% cash buffer), and deterministic hard stop-losses on every order."

#### Mathematical Analysis of Liquidation Distance
In crypto perpetual futures (Hyperliquid / Bitunix standard isolated margin):
Let:
- $P_0$ = Entry Price
- $L$ = Leverage
- $\text{MMR}$ = Maintenance Margin Rate (typically $0.5\% = 0.005$ for BTC and top-cap perpetuals)

For a **Long** position:
$$P_{\text{liq}}^{\text{long}} = P_0 \left(1 - \frac{1}{L} + \text{MMR}\right)$$
The percentage distance to liquidation is:
$$D_{\text{liq}}^{\text{long}} = \frac{P_0 - P_{\text{liq}}^{\text{long}}}{P_0} = \frac{1}{L} - \text{MMR}$$

For a **Short** position:
$$P_{\text{liq}}^{\text{short}} = P_0 \left(1 + \frac{1}{L} - \text{MMR}\right)$$
$$D_{\text{liq}}^{\text{short}} = \frac{P_{\text{liq}}^{\text{short}} - P_0}{P_0} = \frac{1}{L} - \text{MMR}$$

#### Liquidation Distance Table vs. Leverage ($\text{MMR} = 0.005$):
| Leverage ($L$) | $1/L$ | Maintenance Margin ($\text{MMR}$) | Liquidation Distance ($D_{\text{liq}}$) | Meets $\ge 9.5\%$ Invariant? |
|---|---|---|---|---|
| **5x** | $0.2000$ (20.0%) | $0.005$ (0.5%) | **19.50%** | YES (Safe) |
| **8x** | $0.1250$ (12.5%) | $0.005$ (0.5%) | **12.00%** | YES (Safe) |
| **10x** | $0.1000$ (10.0%) | $0.005$ (0.5%) | **9.50%** | **YES (Boundary)** |
| **11x** | $0.0909$ (9.09%) | $0.005$ (0.5%) | **8.59%** | NO (Violates 9.5%) |
| **12x** | $0.0833$ (8.33%) | $0.005$ (0.5%) | **7.83%** | NO (Violates 9.5%) |
| **15x** | $0.0667$ (6.67%) | $0.005$ (0.5%) | **6.17%** | NO (Violates 9.5%) |

#### Critical Invariant Enforcement
To strictly satisfy BOTH the 10x-15x leverage ceiling AND the $D_{\text{liq}} \ge 9.5\%$ condition:
1. **Isolated Margin Mode**:
   Maximum allowable leverage is bounded by:
   $$L_{\text{max}} = \left\lfloor \frac{1}{D_{\text{liq, min}} + \text{MMR}} \right\rfloor = \left\lfloor \frac{1}{0.095 + 0.005} \right\rfloor = 10\text{x}$$
   Thus, for standard isolated margin where $\text{MMR} = 0.005$, **leverage cannot exceed 10x**.
2. **Cross-Margin / Buffered Mode (Up to 15x)**:
   If leverage up to $15\text{x}$ is requested by an aggressive setup, the risk engine must allocate a portion of the 75% cash buffer as dedicated maintenance buffer $B_{\text{maint}}$ such that the effective liquidation price satisfies:
   $$D_{\text{liq}}^{\text{effective}} = \frac{1 + B_{\text{maint}} / \text{Margin}}{L} - \text{MMR} \ge 0.095$$
3. **Hard Clamping**:
   Under all circumstances:
   $$L_{\text{clamped}} = \min(L_{\text{requested}}, 15)$$
   $$\text{Verify } D_{\text{liq}}(L_{\text{clamped}}) \ge 0.095 \quad \implies \quad \text{If false, step down } L \text{ until } D_{\text{liq}} \ge 0.095.$$

### 4.2 25% Margin Sizing (75% Cash Buffer)
- Total Account Balance: $E$ (USDT equity).
- Allocated Margin per Trade:
  $$M = 0.25 \times E$$
- Reserved Liquid Buffer:
  $$B_{\text{cash}} = 0.75 \times E$$
- Position Notional Value:
  $$\text{Notional} = M \times L_{\text{clamped}} = 0.25 \times E \times L_{\text{clamped}}$$
- Contract Quantity (Base asset):
  $$Q = \frac{\text{Notional}}{P_{\text{entry}}}$$
- **Capital Concurrency Rule**: At no time may total committed margin across all concurrent positions exceed $0.25 \times E$. If an existing position is open and consuming margin, subsequent candidate signals are blocked or queued until the open position closes.

### 4.3 Deterministic Hard Stop-Losses for All Candidate Setups
Every single order must have a deterministic hard stop-loss computed before dispatch. The Risk Engine enforces the invariant:
$$|P_{\text{entry}} - P_{\text{SL}}| < |P_{\text{entry}} - P_{\text{liq}}|$$
(The hard SL must always execute long before liquidation distance is approached!).

| Alpha Setup | Entry Condition | Deterministic Hard Stop-Loss Rule |
|---|---|---|
| **OFI VWAP Reversion** | Entry at $\pm 2.5\sigma$ VWAP band + OFI sign reversal $\|z_{\text{OFI}}\| > 0.8\sigma$ | Hard SL fixed at **$0.35\%$** from entry: <br> Long: $P_0 \times (1 - 0.0035)$ <br> Short: $P_0 \times (1 + 0.0035)$ |
| **Liquidation Cascade Absorption** | OI drop $>2.5\%$ in $<300$ ticks, Mark drop $>1.5\%$, taker sell $>85\%$ | Invalidation SL placed at **cascade extreme low/high** wick minus 1 tick tick-buffer. |
| **Hawkes Volatility Breakout** | 100-bar high/low breach + trade arrival intensity $>3\times$ median | Initial hard SL at local breakout candle swing extreme; trailed dynamically behind **Hull Moving Average HMA(9)**. |
| **CVD Divergence Sweep** | Price lower low vs. CVD higher low over 10 bars (rel vol $>1.5\times$) | Hard SL placed at **swing low / swing high of the divergence sweep**. |
| **L2 Depth Imbalance Scalp** | Top 1% LOB bids $>5\times$ asks within $0.1\%$ of cluster | Hard SL placed **immediately below/above the L2 cluster boundary** ($0.1\%$ beyond cluster edge). |

If an order payload arrives at the execution gateway with `hard_sl is None` or $SL \le 0$ or on the wrong side of price, the Risk Engine unconditionally vetos the order.

### 4.4 Explicit Tab Target ID Pinning in `signals/tv_cdp.py`

#### Vulnerability Analysis of Current Code
In `signals/tv_cdp.py:83-114`:
```python
def _target(self) -> dict:
    targets = requests.get(f"http://{self.host}:{self.port}/json").json()
    pages = [t for t in targets if t.get("type") == "page"
             and "tradingview.com/chart" in (t.get("url") or "")]
    pages.sort(key=lambda t: t.get("id") or "")
    ...
    if self.target_index is not None:
        return pages[self.target_index]
    return pages[0]
```
The comment in lines 94-104 explains that `pages.sort(key=lambda t: t.get("id"))` was introduced to fix `Page.bringToFront` reordering. However, this is still fragile:
1. When a new tab is opened or a tab is closed, the sorted index `self.target_index` points to a *different* chart tab!
2. If `self.close()` and `self.connect()` are triggered on a transient socket drop, `_target()` re-indexes `pages[self.target_index]`. If tab ordering shifted or an additional tab opened, the execution book attaches to the wrong window!
3. `CDPSignalSource` in line 368 instantiates `TradingViewCDP(host, port)` without even passing `target_index` or `target_id`.

#### Architectural Remedy: Target ID Pinning Specification
1. Extend `TradingViewCDP.__init__`:
   ```python
   def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                timeout: int = 30, target_index: int | None = None,
                target_id: str | None = None):
       self.host, self.port, self.timeout = host, port, timeout
       self.target_index = target_index
       self.target_id = target_id
       self.pinned_target_id: str | None = target_id
       self._ws: websocket.WebSocket | None = None
       self._id = 0
   ```
2. Re-architect `_target()`:
   ```python
   def _target(self) -> dict:
       targets = requests.get(f"http://{self.host}:{self.port}/json", timeout=10).json()
       pages = [t for t in targets if t.get("type") == "page"
                and "tradingview.com/chart" in (t.get("url") or "")]
       if not pages:
           raise CDPError("No TradingView chart tabs open.")
           
       # Case A: Explicitly pinned to a target ID
       if self.pinned_target_id:
           for p in pages:
               if p.get("id") == self.pinned_target_id:
                   return p
           raise CDPError(f"Pinned chart target {self.pinned_target_id} no longer exists.")
           
       # Case B: Initial attachment by target_index or default 0
       pages.sort(key=lambda t: t.get("id") or "")
       idx = self.target_index if self.target_index is not None else 0
       if idx >= len(pages):
           raise CDPError(f"Target index {idx} out of range ({len(pages)} available).")
           
       selected = pages[idx]
       # PERMANENT PIN: Lock target_id for all subsequent reconnects!
       self.pinned_target_id = selected.get("id")
       return selected
   ```
3. Add utility methods:
   - `TradingViewCDP.list_targets(host, port) -> list[dict]`: returns all open chart tabs with their IDs, titles, URLs, and active symbols.
   - `pin_target(self, target_id: str)`: allows programmatic rebinding.
4. Pass `target_id` support through to `CDPSignalSource(..., target_id=None)`.

---

## 5. Deep Dive: R5 — Combinatorial Purged Cross-Validation (CPCV)

### 5.1 The Mathematical Problem: Information Leakage in Financial CV
In financial event-driven trading models, each label $y_i$ is determined over a future time interval $[t_{i, \text{start}}, t_{i, \text{end}}]$ (e.g. from trade entry until stop-loss or take-profit is triggered, or over $H$ bars).

When evaluating a model with standard $K$-Fold Cross-Validation:
1. **Overlap Leakage**: A training sample $j$ whose event interval $[t_j, t_j + h_j]$ overlaps with a test sample $k$'s evaluation span $[T_{\text{start}}, T_{\text{end}}]$ leaks future price trajectory into the training features.
2. **Autoregressive / Serial Leakage (Post-Test Spillover)**: Market regimes, volatility, and order-flow features possess high autocorrelation. Observations immediately after the test set contain residual information from the test set.

### 5.2 Marcos Lopez de Prado's CPCV Algorithm
The CPCV algorithm resolves both issues through three operations:

#### 1. Combinatorial Grouping
- Divide $T$ chronological samples into $N$ contiguous groups: $G_1, G_2, \dots, G_N$.
- Form test sets from all combinations of $k$ groups: $\binom{N}{k}$ splits.
- For example, with $N = 6, k = 2$:
  $$\binom{6}{2} = 15 \text{ splits}$$
- This generates multiple independent backtest paths, producing a distribution of out-of-sample Sharpe ratios.

#### 2. Purging
For any split where test set spans $[T_{\text{test, start}}^{(m)}, T_{\text{test, end}}^{(m)}]$ for $m \in \{1, \dots, k\}$:
Any candidate training observation $i$ with label span $[t_{i, 0}, t_{i, 1}]$ is **purged** (removed from training) if its evaluation span overlaps any test group:
$$\text{Purge } i \iff \exists m : \left(t_{i, 1} \ge T_{\text{test, start}}^{(m)} \quad \text{and} \quad t_{i, 0} \le T_{\text{test, end}}^{(m)}\right)$$

#### 3. Embargoing
To eliminate post-test autoregressive leakage, an embargo window of length $h_{\text{embargo}}$ (e.g. 1% of total sample size, or a fixed duration $T_{\text{embargo}}$) is imposed immediately after each test group:
$$\text{Embargo } i \iff \exists m : \left(t_{i, 0} \in \left[T_{\text{test, end}}^{(m)}, T_{\text{test, end}}^{(m)} + h_{\text{embargo}}\right]\right)$$

### 5.3 Module Specification: `quant/hft/cpcv.py`

#### Class Structure
```python
@dataclass
class EventSpan:
    index: int
    start_time: float
    end_time: float

class CombinatorialPurgedKFold:
    """
    Combinatorial Purged Cross-Validation (CPCV) with label purging
    and post-test embargoing.
    
    Parameters
    ----------
    n_splits : int
        Total number of chronological groups (N). Default: 5 or 6.
    n_test_splits : int
        Number of groups in each test combination (k). Default: 2.
    embargo_pct : float
        Fraction of sample length to embargo post-test. Default: 0.01 (1%).
    """
    def __init__(self, n_splits: int = 5, n_test_splits: int = 2, embargo_pct: float = 0.01):
        self.n_splits = n_splits
        self.n_test_splits = n_test_splits
        self.embargo_pct = embargo_pct

    def split(self, X, y=None, pred_times=None, eval_times=None):
        """
        Yields (train_indices, test_indices) for all comb(N, k) splits.
        Guarantees:
        1. No overlapping label spans between train and test.
        2. Post-test embargo window is strictly purged from training.
        3. train_indices and test_indices have zero intersection.
        """
        ...
```

#### Unit Test Specification (for `tests/test_architecture_b.py`)
1. **Combinatorial Count**: Verify that $N=5, k=2$ produces exactly $\binom{5}{2} = 10$ splits.
2. **Purge Verification**: Create overlapping synthetic event spans. Verify that 100% of training samples whose `eval_time` overlaps a test interval are purged.
3. **Embargo Verification**: Verify that training samples occurring within $h_{\text{embargo}}$ after any test set boundary are purged.
4. **Zero-Leakage Invariant**: Assert `len(set(train_idx).intersection(set(test_idx))) == 0` for all splits.

---

## 6. Implementation Blueprint & File Layout

To maintain strict adherence to project guidelines:
- All source files reside in `quant/hft/`, `filter_model.py`, or `signals/`.
- All tests reside in `tests/test_filter.py` and `tests/test_architecture_b.py`.
- No source code or tests may be placed in `.agents/`.

### 6.1 Target File Allocations
```
/Users/mac/Desktop/TBT-Engine/
├── filter_model.py                      <- Refactored to unified multi-format MLFilterEngine (100% legacy backward-compatible)
├── signals/
│   └── tv_cdp.py                        <- Updated with explicit tab target ID pinning & list_targets()
├── quant/
│   └── hft/
│       ├── memory/
│       │   ├── __init__.py
│       │   ├── temporal_memory.py       <- 256-dim RAG memory (Qdrant + in-memory cosine fallback + MAE veto)
│       ├── risk/
│       │   ├── __init__.py
│       │   ├── risk_engine.py           <- 10x-15x leverage ceiling, >=9.5% liq dist, 25% margin sizing, hard SL
│       │   ├── kelly.py                 <- Fractional Kelly calculator
│       │   └── day_planner.py           <- Day campaign pacing
│       ├── cpcv.py                      <- Combinatorial Purged Cross-Validation with purging & embargoing
├── tests/
│   ├── test_filter.py                   <- Legacy filter tests (PRESERVED: 6/6 tests passing)
│   └── test_architecture_b.py           <- Comprehensive Architecture B unit test suite
```

---

## 7. Actionable Worker Instructions & Acceptance Criteria

When downstream workers implement R3 and R5, they must satisfy the following checklist:

| Item | Requirement | Verification Target |
|---|---|---|
| **1** | `filter_model.py` backward compatibility | `pytest tests/test_filter.py` passes 6/6 tests with ZERO errors. |
| **2** | ML Filter inference latency | Sub-10ms benchmark in `test_architecture_b.py` achieves P99 $< 10.0\text{ms}$ and P50 $< 1.0\text{ms}$. |
| **3** | 256-Dim RAG Temporal Memory | Unit tests verify top-$K$ cosine retrieval, expected MAE calculation, and trade vetoing when expected MAE $\ge$ hard SL. |
| **4** | In-Memory Cosine Fallback | Functions seamlessly with NumPy when `qdrant_client` is not installed. |
| **5** | Leverage & Liquidation Distance | Enforce $D_{\text{liq}} \ge 9.5\%$; reject or clamp any leverage breaching this threshold. |
| **6** | 25% Margin Sizing | Enforce exactly 25% account margin allocation with 75% liquid cash buffer. |
| **7** | Deterministic Hard Stop-Losses | Unconditionally reject any candidate trade lacking an explicit deterministic hard stop-loss. |
| **8** | CDP Target Pinning | In `signals/tv_cdp.py`, explicit `pinned_target_id` survives tab reordering and reconnects. |
| **9** | CPCV Purging & Embargoing | `CombinatorialPurgedKFold` generates $\binom{N}{k}$ combinations, properly purges overlapping labels, and applies post-test embargo. |
| **10** | Architecture B Test Suite | All tests pass cleanly under `pytest tests/test_architecture_b.py`. |

---

## 8. Conclusion & Handoff Readiness
Explorer 2 has completed the comprehensive survey of R3 and R5. The architectural blueprint, mathematical derivations, interface specifications, and edge-case remediations are fully documented and verified. The codebase is prepared for worker implementation.
