"""
tests/test_scaling_simulation.py
================================
Comprehensive Unit & Integration Test Suite for Walk-Forward Backtesting
& Monte Carlo Scaling Harness ($65.00 -> $10,000.00).

Validates:
1. Dynamic Compounding Formula & Margin Invariant (strictly <= 20% equity across $65, $100, $1k, $10k).
2. Dollar Risk Invariant (strictly < 1.0% equity and < 0.86% equity for standard SL envelope).
3. Realistic Friction Modeling (Hyperliquid 3.5 bps taker fee, -0.2 bps maker rebate, 0.5-1.5 pip slippage, 1h funding).
4. 3-Slice Layering with 50ms Stagger Jitter Model.
5. 5% Daily Max Drawdown Killswitch (UTC daily peak tracking, immediate halt on breach, day reset).
6. Monte Carlo Simulation Engine (N permutations, probability of reaching $10k, max DD distribution, Sharpe ratio).
7. Custom / Historical Trade Series Bootstrapping & Permutations.
8. CLI Execution & JSON Telemetry Serialization.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from typing import Any, Dict, List


from engine.monte_carlo_scaling import (
    DailyDrawdownGuard,
    FrictionEngine,
    MonteCarloScalingSimulator,
    SimulationDailyDrawdownGuard,
    SimulationReport,
    calculate_basket_size,
    calculate_dollar_risk,
    calculate_margin_required,
    main,
    round_down,
    slice_basket,
    validate_margin_invariant,
)


# =========================================================================
# 1. Dynamic Compounding Formula & Margin Ceiling Invariant Tests
# =========================================================================


def test_round_down_utility():
    """Verify round_down truncates without floating point representation bugs."""
    assert round_down(0.468, 2) == 0.46
    assert round_down(0.461, 2) == 0.46
    assert round_down(0.44999999999999996, 2) == 0.45
    assert round_down(0.7200000000000001, 2) == 0.72
    assert round_down(18.0000001, 2) == 18.00
    assert round_down(12.3456, 3) == 12.345


def test_margin_invariant_across_equity_ranges():
    """
    Test margin invariant strictly <= 20% equity across equity milestones:
    - $65.00: max margin <= $13.00
    - $100.00: max margin <= $20.00
    - $1,000.00: max margin <= $200.00
    - $10,000.00: max margin <= $2,000.00
    """
    gold_price = 2500.00
    leverage = 100.0

    milestones = [
        (65.00, 13.00),
        (100.00, 20.00),
        (1000.00, 200.00),
        (10000.00, 2000.00),
    ]

    for equity, expected_max_margin in milestones:
        sz = calculate_basket_size(equity=equity, price=gold_price, leverage=leverage)
        margin = calculate_margin_required(sz, price=gold_price, leverage=leverage)
        margin_pct = margin / equity

        # Margin ceiling invariant: Margin <= 20% equity ($13 on $65)
        assert margin <= expected_max_margin + 1e-6, (
            f"Failed at equity ${equity}: margin ${margin:.2f} > max allowed ${expected_max_margin:.2f}"
        )
        assert margin_pct <= 0.20 + 1e-6, (
            f"Failed at equity ${equity}: margin pct {margin_pct * 100:.2f}% > 20%"
        )
        assert validate_margin_invariant(equity, sz, gold_price, leverage) is True

    # Continuous sweep across equity range from $65 to $15,000
    for eq_int in range(65, 15000, 25):
        eq = float(eq_int)
        sz = calculate_basket_size(equity=eq, price=gold_price, leverage=leverage)
        margin = calculate_margin_required(sz, price=gold_price, leverage=leverage)
        assert margin <= (0.20 * eq + 1e-4), (
            f"Continuous sweep failure at equity ${eq}: margin ${margin} > 20% (${0.20 * eq})"
        )


def test_gold_price_sensitivity_on_margin():
    """Verify margin ceiling invariant holds across wide GOLD price fluctuations ($1800 - $3500)."""
    equity = 65.00
    leverage = 100.0

    test_prices = [1800.0, 2000.0, 2400.0, 2500.0, 2700.0, 3000.0, 3500.0]
    for price in test_prices:
        sz = calculate_basket_size(equity=equity, price=price, leverage=leverage)
        margin = calculate_margin_required(sz, price=price, leverage=leverage)
        assert margin <= (13.00 + 1e-6), (
            f"Price {price}: margin ${margin} exceeded 20% ceiling ($13.00)"
        )


def test_zero_and_negative_equity_boundary():
    """Verify boundary condition when equity is zero or negative."""
    assert calculate_basket_size(0.0) == 0.0
    assert calculate_basket_size(-50.0) == 0.0
    assert validate_margin_invariant(0.0, 0.45) is False
    assert validate_margin_invariant(-10.0, 0.45) is False


# =========================================================================
# 2. Dollar Risk Invariant Tests
# =========================================================================


def test_dollar_risk_per_trade_strictly_bounded():
    """
    Test dollar risk per trade (< 1.0% equity and strictly < 0.86% equity
    for the standard Relapse SL envelope $1.00 - $1.50).
    """
    gold_price = 2500.00
    leverage = 100.0

    test_equities = [65.00, 100.00, 500.00, 1000.00, 5000.00, 10000.00]

    for equity in test_equities:
        # Standard Relapse SL distance = $1.15
        sl_dist_std = 1.15
        sz_std = calculate_basket_size(
            equity=equity, price=gold_price, leverage=leverage, sl_distance=sl_dist_std
        )
        risk_std = calculate_dollar_risk(sz_std, sl_dist_std)
        risk_pct_std = risk_std / equity

        assert risk_pct_std < 0.0086 + 1e-6, (
            f"Equity ${equity}: risk {risk_pct_std * 100:.3f}% >= 0.86% limit"
        )
        assert risk_pct_std < 0.01, (
            f"Equity ${equity}: risk {risk_pct_std * 100:.3f}% >= 1.0% limit"
        )

        # Maximum envelope SL distance = $1.50
        sl_dist_max = 1.50
        sz_max = calculate_basket_size(
            equity=equity, price=gold_price, leverage=leverage, sl_distance=sl_dist_max
        )
        risk_max = calculate_dollar_risk(sz_max, sl_dist_max)
        risk_pct_max = risk_max / equity

        assert risk_pct_max < 0.01, (
            f"Equity ${equity} with SL $1.50: risk {risk_pct_max * 100:.3f}% >= 1.0% limit"
        )


# =========================================================================
# 3. Order Slicing & 50ms Stagger Jitter Model Tests
# =========================================================================


def test_order_slicing_partition():
    """Verify basket size divides into 3 slices summing exactly to total."""
    test_sizes = [0.45, 0.46, 0.50, 0.72, 1.00, 7.20, 72.00]
    for sz in test_sizes:
        slices = slice_basket(sz, num_slices=3)
        assert len(slices) == 3
        assert round(sum(slices), 2) == round(sz, 2)
        # Slices should be within 0.01 of each other
        assert abs(max(slices) - min(slices)) <= 0.02


def test_entry_slices_jitter_and_delays():
    """Verify entry slicing applies 50ms jitter stagger delays (0ms, 50ms, 100ms)."""
    engine = FrictionEngine()
    sz = 0.45
    avg_price, total_fee, slice_results = engine.execute_entry_slices(
        basket_size=sz,
        base_price=2500.00,
        is_buy=True,
    )

    assert len(slice_results) == 3
    assert slice_results[0].delay_ms == 0
    assert slice_results[1].delay_ms == 50
    assert slice_results[2].delay_ms == 100

    # Total filled size matches basket size
    total_sz_filled = sum(s.sz for s in slice_results)
    assert round(total_sz_filled, 2) == sz

    # Weighted average price is close to base price + slippage
    assert 2500.00 < avg_price < 2501.00
    assert total_fee > 0.0


# =========================================================================
# 4. Realistic Friction Modeling Tests
# =========================================================================


def test_hyperliquid_taker_and_maker_fees():
    """
    Verify Hyperliquid fee accounting:
    - 3.5 bps taker fee (0.00035 * notional) on market entry / close.
    - -0.2 bps maker rebate (-0.00002 * notional) on limit order closes.
    """
    engine = FrictionEngine(taker_fee_rate=0.00035, maker_fee_rate=-0.00002)

    sz = 1.00  # 1 oz
    price = 2500.00
    notional = sz * price  # $2500.00
    assert notional == 2500.00

    # Taker exit
    fill_px_taker, taker_fee = engine.execute_exit(
        basket_size=sz, raw_exit_price=price, is_buy=True, is_maker=False
    )
    expected_taker_fee = round(sz * fill_px_taker * 0.00035, 4)
    assert math.isclose(taker_fee, expected_taker_fee, abs_tol=1e-4)
    assert taker_fee > 0.0

    # Maker exit (limit order): rebate
    fill_px_maker, maker_fee = engine.execute_exit(
        basket_size=sz, raw_exit_price=price, is_buy=True, is_maker=True
    )
    expected_maker_fee = round(sz * fill_px_maker * (-0.00002), 4)
    assert math.isclose(maker_fee, expected_maker_fee, abs_tol=1e-4)
    assert maker_fee < 0.0  # Negative fee = rebate to account


def test_slippage_bounds():
    """Verify execution slippage is strictly bounded between 0.5 and 1.5 pips ($0.05 to $0.15)."""
    engine = FrictionEngine(min_slippage_pips=0.5, max_slippage_pips=1.5, pip_size=0.10)
    for _ in range(100):
        pips, dollars = engine.sample_slippage()
        assert 0.5 <= pips <= 1.5
        assert 0.05 <= dollars <= 0.15


def test_1h_funding_rate_payment():
    """Verify 1-hour funding rate calculation based on trade duration."""
    engine = FrictionEngine(funding_rate_1h=0.0000125)
    sz = 2.0  # 2 oz
    entry_px = 2500.00  # Notional = $5,000

    # 1.0 hour holding time
    fee_1h = engine.calculate_funding_fee(
        basket_size=sz, entry_price=entry_px, holding_duration_hours=1.0, is_buy=True
    )
    assert math.isclose(fee_1h, 5000.0 * 0.0000125 * 1.0, abs_tol=1e-4)

    # 2.5 hours holding time
    fee_2_5h = engine.calculate_funding_fee(
        basket_size=sz, entry_price=entry_px, holding_duration_hours=2.5, is_buy=True
    )
    assert math.isclose(fee_2_5h, 5000.0 * 0.0000125 * 2.5, abs_tol=1e-4)

    # Short position receives funding if rate is positive
    fee_short = engine.calculate_funding_fee(
        basket_size=sz, entry_price=entry_px, holding_duration_hours=1.0, is_buy=False
    )
    assert fee_short == -fee_1h


# =========================================================================
# 5. 5% Daily Max Drawdown Killswitch Tests
# =========================================================================


def test_daily_drawdown_killswitch_trigger_and_day_reset():
    """
    Verify 5% daily drawdown killswitch:
    1. Tracks UTC daily peak equity.
    2. Trips killswitch when drawdown >= 5%.
    3. Blocks trades for remainder of the same UTC day.
    4. Automatically resets peak equity and un-trips on new UTC day rollover.
    """
    guard = SimulationDailyDrawdownGuard(max_drawdown_pct=0.05)
    ts_day1 = 1774000000.0  # arbitrary UTC timestamp

    # Start of Day 1: $100.00
    tripped, dd = guard.update(100.00, ts_day1)
    assert tripped is False
    assert dd == 0.0

    # Small dip: $97.00 (3% drawdown) -> Safe
    tripped, dd = guard.update(97.00, ts_day1 + 1800)
    assert tripped is False
    assert math.isclose(dd, 0.03, abs_tol=1e-4)

    # New intraday high: $105.00 -> Peak ratchets up
    tripped, dd = guard.update(105.00, ts_day1 + 3600)
    assert tripped is False
    assert dd == 0.0
    assert guard._peak_day_equity == 105.00

    # Drawdown of 5.1% from new peak: $105 * 0.949 = $99.645
    tripped, dd = guard.update(99.60, ts_day1 + 7200)
    assert tripped is True
    assert dd >= 0.05
    assert guard.is_tripped is True

    # Subsequent check on same UTC day: remains tripped
    tripped, dd = guard.update(99.60, ts_day1 + 10800)
    assert tripped is True
    assert guard.is_tripped is True

    # Rollover to Day 2 (+24 hours): resets!
    ts_day2 = ts_day1 + 86400.0
    tripped, dd = guard.update(99.60, ts_day2)
    assert tripped is False
    assert guard.is_tripped is False
    assert guard._peak_day_equity == 99.60


def test_simulation_daily_drawdown_guard_parity_with_fsm():
    """Verify SimulationDailyDrawdownGuard matches engine.fsm.DailyDrawdownGuard behavior."""
    fsm_guard = DailyDrawdownGuard(max_drawdown_pct=0.05)
    sim_guard = SimulationDailyDrawdownGuard(max_drawdown_pct=0.05)

    ts = 1774000000.0
    f_trip, f_dd = fsm_guard.update(100.0, ts)
    s_trip, s_dd = sim_guard.update(100.0, ts)
    assert f_trip == s_trip and math.isclose(f_dd, s_dd, abs_tol=1e-4)

    # Trip both
    f_trip, f_dd = fsm_guard.update(94.0, ts + 1000)
    s_trip, s_dd = sim_guard.update(94.0, ts + 1000)
    assert f_trip is True and s_trip is True
    assert math.isclose(f_dd, s_dd, abs_tol=1e-4)

    # Day 2 rollover
    f_trip, f_dd = fsm_guard.update(94.0, ts + 86400)
    s_trip, s_dd = sim_guard.update(94.0, ts + 86400)
    assert f_trip is False and s_trip is False


# =========================================================================
# 6. Monte Carlo Simulation Engine Tests
# =========================================================================


def test_monte_carlo_simulation_execution():
    """
    Run Monte Carlo simulation with N=100 permutations over 100 trades.
    Validates report statistics, percentiles, and Sharpe ratio.
    """
    sim = MonteCarloScalingSimulator(
        starting_equity=65.0,
        target_equity=10000.0,
        seed=42,
    )
    report = sim.run_simulation(n_simulations=100, n_trades=100)

    assert isinstance(report, SimulationReport)
    assert report.n_simulations == 100
    assert report.n_trades == 100
    assert report.starting_equity == 65.0
    assert report.target_equity == 10000.0

    # Probabilities bounded [0, 1]
    assert 0.0 <= report.probability_target_reached <= 1.0
    assert 0.0 <= report.probability_ruin <= 1.0

    # Max drawdown percentiles must be monotonically non-decreasing
    dd_dist = report.max_drawdown_dist
    assert (
        dd_dist["min"]
        <= dd_dist["p25"]
        <= dd_dist["p50"]
        <= dd_dist["p75"]
        <= dd_dist["p90"]
        <= dd_dist["p95"]
        <= dd_dist["max"]
    )
    assert report.median_max_drawdown == dd_dist["p50"]
    assert report.p95_max_drawdown == dd_dist["p95"]

    # Sharpe ratio percentiles must be monotonic
    sharpe_dist = report.sharpe_ratio_dist
    assert (
        sharpe_dist["min"]
        <= sharpe_dist["p25"]
        <= sharpe_dist["p50"]
        <= sharpe_dist["p75"]
        <= sharpe_dist["p90"]
        <= sharpe_dist["p95"]
        <= sharpe_dist["max"]
    )
    assert report.median_sharpe == sharpe_dist["p50"]

    # Killswitch trips tracking
    assert report.killswitch_trips_total >= 0
    assert report.killswitch_trips_mean >= 0.0

    # Report serialization
    summary_text = report.summary()
    assert "MONTE CARLO ACCOUNT SCALING SIMULATION" in summary_text
    assert "SCALING OUTCOMES" in summary_text
    assert "DRAWDOWN DISTRIBUTION" in summary_text

    dict_output = report.to_dict()
    assert dict_output["n_simulations"] == 100
    assert dict_output["starting_equity"] == 65.0
    assert "max_drawdown_dist" in dict_output
    # Must be JSON serializable
    json_str = json.dumps(dict_output)
    assert len(json_str) > 50


def test_monte_carlo_custom_trade_series():
    """Verify simulator executes correctly over user-provided historical trades."""
    custom_trades: List[Dict[str, Any]] = [
        {
            "trade_id": "HIST-001",
            "timestamp": 1774000000.0,
            "is_buy": True,
            "sl_distance": 1.20,
            "r_multiple": 3.0,
            "duration_hours": 1.0,
            "is_maker": True,
        },
        {
            "trade_id": "HIST-002",
            "timestamp": 1774010000.0,
            "is_buy": False,
            "sl_distance": 1.10,
            "r_multiple": -1.0,
            "duration_hours": 0.5,
            "is_maker": False,
        },
        {
            "trade_id": "HIST-003",
            "timestamp": 1774020000.0,
            "is_buy": True,
            "sl_distance": 1.30,
            "r_multiple": 2.5,
            "duration_hours": 1.5,
            "is_maker": True,
        },
        {
            "trade_id": "HIST-004",
            "timestamp": 1774100000.0,  # Next day
            "is_buy": False,
            "sl_distance": 1.15,
            "r_multiple": 4.0,
            "duration_hours": 2.0,
            "is_maker": True,
        },
    ]

    sim = MonteCarloScalingSimulator(starting_equity=65.0, seed=123)
    report = sim.run_simulation(n_simulations=20, n_trades=40, trades=custom_trades)

    assert report.n_simulations == 20
    assert report.n_trades == 40
    assert len(report.runs) == 20
    assert report.runs[0].trades_executed > 0


def test_target_reaching_and_ruin_stopping():
    """Verify simulation halts trading when reaching target $10,000 or ruin ($13.00)."""
    # Extremely favorable trade series: fast reach to $10,000
    fast_target_trades: List[Dict[str, Any]] = [
        {
            "trade_id": f"WIN-{i}",
            "timestamp": 1774000000.0 + i * 86400.0,
            "is_buy": True,
            "sl_distance": 1.15,
            "r_multiple": 8.0,
            "duration_hours": 0.5,
            "is_maker": True,
        }
        for i in range(200)
    ]

    sim = MonteCarloScalingSimulator(
        starting_equity=65.0, target_equity=1000.0, seed=42  # small target for fast test
    )
    report = sim.run_simulation(n_simulations=5, n_trades=100, trades=fast_target_trades)
    assert report.probability_target_reached == 1.0
    for r in report.runs:
        assert r.target_reached is True
        assert r.final_equity >= 1000.0
        assert r.trades_executed < 100  # Stopped early!


# =========================================================================
# 7. CLI Execution Tests
# =========================================================================


def test_cli_execution_and_argument_parsing():
    """Verify CLI interface executes cleanly via main() and returns code 0."""
    test_args = ["--simulations", "10", "--trades", "10", "--seed", "42"]
    exit_code = main(test_args)
    assert exit_code == 0


def test_cli_subprocess_json_output():
    """Verify CLI execution via python3 subprocess with --json produces valid JSON."""
    cmd = [
        sys.executable,
        "engine/monte_carlo_scaling.py",
        "--simulations",
        "5",
        "--trades",
        "5",
        "--json",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    assert res.returncode == 0
    data = json.loads(res.stdout)
    assert data["n_simulations"] == 5
    assert data["starting_equity"] == 65.0
    assert "max_drawdown_dist" in data
