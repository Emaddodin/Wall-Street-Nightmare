# Dispatch for worker_gold_m3

Milestone: M3 Walk-Forward Backtesting & Monte Carlo Scaling Harness ($65->$10k)
Authoritative Specification: /Users/mac/Desktop/TBT-Engine/.agents/ORIGINAL_REQUEST.md
Project Specification: /Users/mac/Desktop/TBT-Engine/.agents/orchestrator_2/PROJECT.md
Explorer Survey: /Users/mac/Desktop/TBT-Engine/.agents/explorer_gold_survey_3/handoff.md
Working Directory: /Users/mac/Desktop/TBT-Engine/.agents/worker_gold_m3
File Ownership (Exclusive): engine/monte_carlo_scaling.py, tests/test_scaling_simulation.py

Task:
1. Implement `engine/monte_carlo_scaling.py`:
   - Initial equity $65.00 scaling toward $10,000.
   - 100x leverage on Hyperliquid GOLD perps ($P \approx \$2,500/\text{oz}$).
   - Initial margin strictly capped <= 20% equity ($13.00 max on $65).
   - Dynamic compounding formula: basket size $S(E) = \max(0.45, \text{round\_down}(0.18 \times E \times 100 / P, 2))$, 3 slices with 50ms jitter delay model, risk strictly < 0.86% equity.
   - Hyperliquid fees: 3.5 bps taker fee on entry/market close, -0.2 bps maker fee, 0.5 to 1.5 pip slippage, and 1-hour funding rates.
   - 5% max daily drawdown killswitch: tracks UTC daily peak equity, halts trading for day on breach.
   - Monte Carlo engine: run N permutations (default 1,000) over historical/synthetic trade series, calculating probability of reaching $10,000, median time to target, max drawdown distribution, and Sharpe ratio.
   - CLI execution support: `python3 engine/monte_carlo_scaling.py --simulations 1000 --trades 500`.
2. Implement comprehensive unit and integration tests in `tests/test_scaling_simulation.py`:
   - Test margin invariant <= 20% across equity ranges ($65, $100, $1,000, $10,000).
   - Test dollar risk per trade (< 1.0% equity).
   - Test fee and slippage modeling.
   - Test 5% daily drawdown killswitch triggering and day reset.
   - Test Monte Carlo simulation run and summary statistics.
3. Run verification:
   - `pytest tests/test_scaling_simulation.py -v`
   - `pytest tests/test_gold_relapse_scalper.py -v`
   - `python3 engine/monte_carlo_scaling.py --simulations 100 --trades 100`
4. Document changes in `changes.md` and write a structured 5-component `handoff.md`.

## 2026-09-17T19:29:33Z

You are worker_gold_m3.
Your working directory is /Users/mac/Desktop/TBT-Engine/.agents/worker_gold_m3.
Your parent is orchestrator_2 (convId: d8cde56b-142d-4ad0-b360-6f180e2c8eaa).

Task:
1. Implement `engine/monte_carlo_scaling.py`:
   - Starting equity $65.00, target $10,000.
   - Leverage: 100x (Hyperliquid GOLD perpetual maximum).
   - Margin ceiling: initial margin strictly <= 20% equity ($13.00 max on $65).
   - Dynamic compounding formula: basket size S(E) = max(0.45, round_down(0.18 * E * 100 / P, 2)), 3 slices with 50ms jitter delay model, risk strictly < 0.86% equity.
   - Friction modeling: Hyperliquid fees (3.5 bps taker, -0.2 bps maker), 0.5-1.5 pip slippage, and 1h funding rates.
   - 5% daily max drawdown killswitch: track UTC daily peak equity, halt trading for remainder of day if daily drawdown >= 5%.
   - Monte Carlo simulation: N permutations (default 1,000) over historical/synthetic trade series, evaluating probability of reaching $10,000, median time, max drawdown distribution, and Sharpe ratio.
   - CLI execution support: `python3 engine/monte_carlo_scaling.py --simulations 1000 --trades 500`.
2. Implement comprehensive unit and integration tests in `tests/test_scaling_simulation.py`:
   - Test margin invariant <= 20% across equity ranges ($65, $100, $1,000, $10,000).
   - Test dollar risk per trade (< 1.0% equity).
   - Test fee and slippage modeling.
   - Test 5% daily drawdown killswitch triggering and day reset.
   - Test Monte Carlo simulation run and summary statistics.
3. Run verification:
   - `pytest tests/test_scaling_simulation.py -v`
   - `pytest tests/test_gold_relapse_scalper.py -v`
   - `python3 engine/monte_carlo_scaling.py --simulations 100 --trades 100`
4. Document changes in `changes.md` and write a structured 5-component `handoff.md` in /Users/mac/Desktop/TBT-Engine/.agents/worker_gold_m3/.
When done, send a completion message to parent.
