# Project: 5-Minute XAUUSD Relapse Scalper on Hyperliquid DEX

## Architecture
The 5-Minute XAUUSD Relapse Scalper operates natively on Hyperliquid DEX (replacing MT5) with a microstructural execution bridge, local llama.cpp sub-second intuition exits, macro calendar protection, and an institutional risk framework scaling a $65 micro-account to $10,000 on a 4GB Linux VPS.

```
Hyperliquid WebSocket (L2 Book / Trades) & Economic Calendar Feed
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  Macro Fundamental Calendar Blackout (macro/slm_intuition)  │
│  - 10-minute async polling                                  │
│  - Unconditional ±15m blackout on High-Impact US news       │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  5M Relapse State Machine (engine/fsm.py)                   │
│  - 5m PA / ICT Relapse entry detection                      │
│  - Daily drawdown killswitch (5% daily max DD)              │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  Hyperliquid Asynchronous Execution Router (engine/)        │
│  - 100x leverage on GOLD perps (Hyperliquid maximum)        │
│  - Initial margin ceiling: strictly <= 20% equity ($13 on $65)│
│  - SL envelope: $1.00 - $1.50 delta (0.10-0.15 beyond wick) │
│  - Open-ended entry (take_profit = None)                    │
│  - Asynchronous Order Slicing: 3 slices with 50ms jitter    │
│  - Detached Stop Market: unified order with reduce_only=True │
│  - Breakeven Lock: at +1.5R floating profit -> Entry ±$0.10 │
│  - Dynamic Basket Close: parallel liquidation on EXIT signal│
│  - HyperliquidDEXVenue adapter wrapping hyperliquid-sdk     │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▲ (1-minute tape feedback loop)
┌─────────────────────────────────────────────────────────────┐
│  Local llama.cpp Micro-LLM Intuition Exit Engine            │
│  - Qwen2.5-Coder-1.5B-Instruct-GGUF on http://localhost:8080│
│  - Compressed 1m telemetry: R, wick_ratio, volume_stall, dxy│
│  - GBNF Grammar: {"decision": "HOLD"|"EXIT"}                │
│  - < 300ms execution timeout with algorithmic fail-safe     │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  Production VPS Systemd & Watchdog Hardening (deploy/)      │
│  - relapse-scalper.service & stratton-llm-critic.service    │
│  - relapse-watchdog.service / .timer (RAM <= 2.5 GB check)  │
│  - Antigravity structured JSON telemetry & ntfy.sh alerting │
└─────────────────────────────────────────────────────────────┘
```

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| F1 | HL DEX CLOB Risk Invariants | 100x leverage, <= 20% margin ceiling, $1.00-$1.50 SL delta, open-ended dispatch | M1 | Survey R1 |
| F2 | Asynchronous Order Slicing | `fire_layered_orders` 3 tickets with 50ms stagger jitter via `asyncio.gather` | M1 | Survey R1 |
| F3 | Detached Stop & Breakeven Lock | Unified Stop Market order, breakeven lock at +1.5R (Entry ±$0.10), basket close | M1 | Survey R1 |
| F4 | HyperliquidDEXVenue SDK Adapter | Async adapter wrapping `hyperliquid-python-sdk` with `asyncio.to_thread` | M1 | Survey R1 |
| F5 | Local llama.cpp Exit Engine | Qwen2.5-Coder-1.5B on port 8080, compressed 1m telemetry, GBNF schema, <300ms timeout | M2 | Survey R2 |
| F6 | Algorithmic LLM Fail-Safe | Fallback heuristic on timeout/error (R >= 1.0 + wick >= 0.65 + stall -> EXIT) | M2 | Survey R2 |
| F7 | Macro Calendar Blackout | 10m polling, unconditional ±15m halt on High-Impact US news releases | M2 | Survey R3 |
| F8 | Compounding Scaling Model | $65 to $10,000 scaling formula maintaining <0.86% risk and <20% margin | M3 | Survey R4 |
| F9 | Realistic Friction Modeling | 3.5 bps taker fee, -0.2 bps maker fee, 0.5-1.5 pip slippage, 1h funding | M3 | Survey R4 |
| F10 | 5% Daily Drawdown Killswitch | UTC-anchored daily peak equity tracking with immediate liquidation on breach | M3 | Survey R4 |
| F11 | Monte Carlo Scaling Simulation | Reproducible backtest harness and Monte Carlo permutations | M3 | Survey R4 |
| F12 | VPS Systemd Production Suite | `relapse-scalper.service`, `stratton-llm-critic.service`, `relapse-watchdog` | M4 | Survey R5 |
| F13 | Resident RAM Ceiling <= 2.5 GB | Budgeted 2,020 MB RSS on 4GB host enforced by cgroups and watchdog polling | M4 | Survey R5 |
| F14 | Antigravity JSON Telemetry | Standardized single-line JSON event logging and ntfy.sh alert escalation | M4 | Survey R5 |
| F15 | Comprehensive E2E Verification | 100% test pass across all unit, integration, and stress test suites | M5 | Acceptance Criteria |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Hyperliquid DEX Adapter & Execution Hardening | Implement `HyperliquidDEXVenue` adapter wrapping `hyperliquid-python-sdk` via `asyncio.to_thread`, add Short SL & breakeven tests, ensure testnet/live config | none | DONE |
| M2 | LLM Coder Alignment & Macro Blackout Configuration | Update `deploy/install_llama.sh` and service to `qwen2.5-coder-1.5b-instruct-q4_k_m.gguf`, add `--calendar-url` to `run_relapse_scalper.py` | none | DONE |
| M3 | Walk-Forward Backtesting & Monte Carlo Scaling Harness | Implement `engine/monte_carlo_scaling.py` and `tests/test_scaling_simulation.py` ($65->$10k scaling with fees, slippage, funding, 5% DD killswitch) | M1 | DONE |
| M4 | VPS Systemd Suite & Watchdog Hardening | Create systemd unit files, configure `quant/hft/guard.py` for `relapse-scalper.service` and telemetry sink, verify RAM <= 2.5 GB | M2 | DONE |
| M5 | Full E2E Test Suite Pass & Adversarial Hardening | Verify all tests pass cleanly (`test_gold_relapse_scalper.py`, `test_scaling_simulation.py`, `test_hft_guard.py`), Reviewers APPROVE, Challenger stress test, Auditor CLEAN | M1, M2, M3, M4 | DONE |

## Interface Contracts

### HyperliquidVenue Protocol (`engine/execution_router.py`)
```python
class HyperliquidVenue(Protocol):
    async def get_equity(self) -> float: ...
    async def get_market_price(self, coin: str = "GOLD") -> float: ...
    async def market_open(self, coin: str, is_buy: bool, sz: float, slippage_pct: float = 0.001) -> OrderResult: ...
    async def market_close(self, coin: str, is_buy: bool, sz: float, trigger_px: Optional[float] = None, reduce_only: bool = True) -> OrderResult: ...
    async def cancel(self, coin: str, order_id: str) -> bool: ...
```

### SLMIntuitionEngine (`macro/slm_intuition.py`)
```python
class SLMIntuitionEngine:
    async def query_intuition_exit(self, telemetry: IntuitionTelemetry) -> IntuitionDecision: ...
```

### DailyDrawdownGuard (`engine/fsm.py`)
```python
class DailyDrawdownGuard:
    def update(self, current_equity: float, current_ts: float) -> Tuple[bool, float]: ...
    @property
    def is_tripped(self) -> bool: ...
```

### MonteCarloScalingSimulator (`engine/monte_carlo_scaling.py`)
```python
class MonteCarloScalingSimulator:
    def run_simulation(self, n_simulations: int = 1000, n_trades: int = 500) -> SimulationReport: ...
```

## Code Layout
- `engine/execution_router.py`: Hyperliquid execution bridge, CLOB invariants, order slicing, detached stop, breakeven lock, basket close, `HyperliquidDEXVenue`.
- `engine/fsm.py`: 5-minute Relapse FSM, ICT setup validation, `DailyDrawdownGuard`.
- `engine/monte_carlo_scaling.py`: $65 to $10,000 walk-forward backtest and Monte Carlo scaling simulator with fees/slippage/funding.
- `macro/slm_intuition.py`: `SLMIntuitionEngine`, GBNF grammar, compressed telemetry, `EconomicCalendarFilter`.
- `run_relapse_scalper.py`: Main CLI entrypoint, live/paper event loop, shutdown handler.
- `quant/hft/guard.py`: RAM monitor (/proc/meminfo), systemd health watchdog, Antigravity JSON logger.
- `deploy/`: Systemd unit files (`relapse-scalper.service`, `stratton-llm-critic.service`, `relapse-watchdog.service`, `relapse-watchdog.timer`) and `install_llama.sh`.
- `tests/test_gold_relapse_scalper.py`: Unit and integration tests for CLOB invariants, slicing, stops, macro blackout, intuition, FSM.
- `tests/test_scaling_simulation.py`: Backtesting and Monte Carlo account scaling simulation tests.
- `tests/test_hft_guard.py`: Memory ceiling and watchdog health check tests.
