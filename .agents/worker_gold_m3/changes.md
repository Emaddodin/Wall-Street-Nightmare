# Changes: Milestone 3 (Walk-Forward Backtesting & Monte Carlo Scaling Harness $65->$10k)

**Agent**: `worker_gold_m3`  
**Parent**: `orchestrator_2` (convId: `d8cde56b-142d-4ad0-b360-6f180e2c8eaa`)  
**Timestamp**: 2026-09-17T19:42:00Z  

---

### 1. New Implementation: `engine/monte_carlo_scaling.py`
Implemented the complete walk-forward backtesting and Monte Carlo account scaling harness for the 5-Minute XAUUSD Relapse Scalper on Hyperliquid DEX:
- **Capital & Sizing Constraints**:
  - Starting equity $65.00 micro-account, target equity $10,000.00, ruin threshold $13.00.
  - 100x leverage on Hyperliquid GOLD perpetuals.
  - Initial margin ceiling: strictly capped at <= 20% equity ($13.00 max on $65.00).
  - Dynamic compounding formula: $S(E) = \max(0.45, \text{round\_down}(0.18 \times E \times 100 / P, 2))$, dynamically partitioning into 3 micro-unit slices with 50ms stagger jitter delay model (0ms, 50ms, 100ms).
  - Dollar risk per trade invariant: strictly < 0.86% equity at standard SL distance and < 1.0% equity across all valid envelope stop losses.
- **Realistic Microstructural Friction Engine**:
  - Hyperliquid CLOB fees: 3.5 bps taker fee (0.00035 * notional) on market entry / close, -0.2 bps maker rebate (-0.00002 * notional) on limit order closes.
  - Execution slippage: uniformly sampled 0.5 to 1.5 pips ($0.05 to $0.15 per oz) adverse slippage on entries and market/stop exits.
  - Funding rate modeling: 1-hour Hyperliquid funding rate (`0.0000125` per hour) calculated over position holding duration.
  - Net PnL accounting: $\text{Gross PnL} - \text{Entry Fee} - \text{Exit Fee} - \text{Funding Fee}$.
- **5% Max Daily Drawdown Killswitch**:
  - UTC daily peak equity tracking via `SimulationDailyDrawdownGuard` and parity with `engine.fsm.DailyDrawdownGuard`.
  - Trips immediately when $\frac{\text{Daily Peak} - \text{Current Equity}}{\text{Daily Peak}} \ge 0.05$, halting all further trades for the remainder of that UTC day.
  - Automatically resets peak equity and un-trips on UTC day rollover (00:00 UTC).
- **Monte Carlo Permutation Engine**:
  - Evaluates $N$ permutations (default 1,000) over historical/synthetic trade series.
  - Calculates probability of reaching $10,000, probability of ruin, median time to target, median trades, max drawdown percentiles (`min`, `p25`, `p50`, `p75`, `p90`, `p95`, `p99`, `max`), and annualized Sharpe ratio.
- **CLI & Structured Telemetry**:
  - Full CLI support: `python3 engine/monte_carlo_scaling.py --simulations 1000 --trades 500`.
  - Flags: `--simulations`, `--trades`, `--initial-equity`, `--target-equity`, `--gold-price`, `--seed`, `--json`.

---

### 2. New Test Suite: `tests/test_scaling_simulation.py`
Constructed a comprehensive 17-test unit and integration suite:
1. `test_round_down_utility`: Validates truncation logic against floating point epsilon anomalies.
2. `test_margin_invariant_across_equity_ranges`: Validates initial margin strictly <= 20% equity at $65, $100, $1,000, $10,000, and continuous sweep across $65 to $15,000.
3. `test_gold_price_sensitivity_on_margin`: Validates margin ceiling holds across wide GOLD prices ($1,800 to $3,500/oz).
4. `test_zero_and_negative_equity_boundary`: Validates edge cases for zero and negative account equity.
5. `test_dollar_risk_per_trade_strictly_bounded`: Validates dollar risk per trade is strictly < 0.86% equity for standard SL and < 1.0% equity for max envelope SL.
6. `test_order_slicing_partition`: Validates exact 3-slice partitioning with 2-decimal precision.
7. `test_entry_slices_jitter_and_delays`: Validates 50ms stagger jitter delays (0ms, 50ms, 100ms) and weighted average entry price.
8. `test_hyperliquid_taker_and_maker_fees`: Validates 3.5 bps taker fee and -0.2 bps maker rebate.
9. `test_slippage_bounds`: Validates execution slippage strictly within [0.5, 1.5] pips ($0.05 to $0.15).
10. `test_1h_funding_rate_payment`: Validates 1h funding fee calculations across holding durations.
11. `test_daily_drawdown_killswitch_trigger_and_day_reset`: Validates 5% daily drawdown killswitch tripping, trade halt, and day reset on UTC day rollover.
12. `test_simulation_daily_drawdown_guard_parity_with_fsm`: Verifies exact parity between `SimulationDailyDrawdownGuard` and `engine.fsm.DailyDrawdownGuard`.
13. `test_monte_carlo_simulation_execution`: Validates Monte Carlo report metrics, monotonic percentiles, Sharpe ratios, and summary text.
14. `test_monte_carlo_custom_trade_series`: Validates bootstrapping/permutations over user-provided historical trade series.
15. `test_target_reaching_and_ruin_stopping`: Validates early stopping upon reaching $10,000 or hitting ruin.
16. `test_cli_execution_and_argument_parsing`: Validates CLI execution via `main()` arguments.
17. `test_cli_subprocess_json_output`: Validates subprocess execution with `--json` flag producing valid parseable JSON.

---

### 3. Test Verification Results
- `pytest tests/test_scaling_simulation.py -v`: 17 passed in 6.60s.
- `pytest tests/test_gold_relapse_scalper.py -v`: 13 passed in 4.08s.
- `python3 engine/monte_carlo_scaling.py --simulations 100 --trades 100`: Executed successfully in 0.468s.
- `ruff check engine/monte_carlo_scaling.py tests/test_scaling_simulation.py`: All checks passed with 0 violations.
