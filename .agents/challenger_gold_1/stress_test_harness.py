#!/usr/bin/env python3
"""
stress_test_harness.py
======================
Adversarial & Empirical Stress Test Suite for 5-Minute XAUUSD Relapse Scalper.

Target Invariants & Stress Vectors:
1. Margin Ceiling Invariant (<= 20% equity) across boundary equities
   ($1, $65, $100, $1,000, $10,000, $1,000,000) and volatile gold price shocks ($1,500 to $4,000/oz).
2. Dollar Risk Per Trade Invariant (< 0.86% and strictly < 1.0% equity).
3. 5% Daily Max Drawdown Killswitch Boundary Conditions
   (exact intraday threshold, UTC midnight rollover, FSM basket liquidation).
4. Monte Carlo Scaling Engine Numerical Stability under extreme parameters
   (n_sims=0,1; n_trades=0,1; zero/negative equity; 100% loss/win; flat curves; JSON serialization).

Author: challenger_gold_1 (Empirical Adversarial Challenger)
Date: 2026-09-17
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import random
import sys
import time
from typing import Any, Dict, List, Tuple

# Ensure project root is on sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pandas as pd

from engine.execution_router import (
    ExecutionRouter,
    OrderSide,
    RiskInvariants,
    SimulatedBrokerVenue,
)
from engine.fsm import (
    DAY_MS,
    DailyDrawdownGuard,
    RelapseFSM,
    RelapseState,
    SetupContext,
)
from engine.monte_carlo_scaling import (
    DAY_SECONDS,
    DEFAULT_GOLD_PRICE,
    DEFAULT_LEVERAGE,
    DEFAULT_STARTING_EQUITY,
    DEFAULT_TARGET_EQUITY,
    MAX_MARGIN_PCT,
    MAX_RISK_PCT,
    MIN_BASKET_SIZE,
    FrictionEngine,
    MonteCarloScalingSimulator,
    SimulationDailyDrawdownGuard,
    SimulationReport,
    SyntheticTradeGenerator,
    SyntheticTradeProfile,
    calculate_basket_size,
    calculate_dollar_risk,
    calculate_margin_required,
    round_down,
    slice_basket,
    validate_margin_invariant,
)


class TestResultLogger:
    """Accumulates structured test outcomes, assertions, and anomalies."""

    def __init__(self):
        self.passed_tests = 0
        self.failed_tests = 0
        self.total_assertions = 0
        self.findings: List[Dict[str, Any]] = []

    def assert_true(self, condition: bool, test_name: str, message: str, context: Dict[str, Any] = None):
        self.total_assertions += 1
        if not condition:
            self.failed_tests += 1
            entry = {
                "status": "FAIL",
                "test": test_name,
                "message": message,
                "context": context or {},
            }
            self.findings.append(entry)
            print(f"[FAIL] {test_name}: {message} | ctx={context}")
            raise AssertionError(f"{test_name}: {message} (ctx: {context})")
        else:
            self.passed_tests += 1

    def record_pass(self, test_name: str, details: str = ""):
        print(f"[PASS] {test_name}{' - ' + details if details else ''}")


logger = TestResultLogger()


# =========================================================================
# SUITE 1: Margin Ceiling Invariant (<= 20% equity)
# =========================================================================

def stress_test_margin_ceiling_across_boundaries_and_shocks():
    """
    Stress-tests margin ceiling invariant across:
    - Boundary equities: $0.01, $1.00, $13.00, $65.00, $100.00, $1,000.00, $10,000.00, $1,000,000.00
    - Price shocks: $1,200 (flash crash), $1,500, $1,800, $2,000, $2,500, $3,000, $3,500, $4,000, $5,000/oz
    - Granular equity sweeps
    """
    print("\n--- Running Suite 1: Margin Ceiling Invariant Stress Tests ---")
    equities = [0.01, 1.00, 13.00, 64.99, 65.00, 65.01, 100.00, 1000.00, 10000.00, 1000000.00]
    prices = [1200.0, 1500.0, 1800.0, 2000.0, 2400.0, 2500.0, 3000.0, 3500.0, 4000.0, 5000.0]
    leverages = [100.0, 50.0, 20.0]

    eval_count = 0
    max_observed_margin_pct = 0.0

    for eq in equities:
        for p in prices:
            for lev in leverages:
                sz = calculate_basket_size(equity=eq, price=p, leverage=lev)
                margin = calculate_margin_required(sz, price=p, leverage=lev)
                margin_pct = margin / eq if eq > 0 else 0.0
                if margin_pct > max_observed_margin_pct:
                    max_observed_margin_pct = margin_pct

                eval_count += 1
                ctx = {"equity": eq, "price": p, "leverage": lev, "sz": sz, "margin": margin, "margin_pct": margin_pct}

                # Invariant 1: Margin must never exceed 20% equity (with 1e-6 epsilon)
                logger.assert_true(
                    margin <= (0.20 * eq + 1e-5),
                    "MarginCeilingInvariant",
                    f"Required margin ${margin:.4f} exceeded 20% equity (${0.20 * eq:.4f})",
                    ctx,
                )

                logger.assert_true(
                    margin_pct <= 0.20 + 1e-5,
                    "MarginPctInvariant",
                    f"Margin percentage {margin_pct*100:.3f}% exceeded 20.0%",
                    ctx,
                )

                if sz > 0:
                    is_valid = validate_margin_invariant(eq, sz, price=p, leverage=lev)
                    logger.assert_true(
                        is_valid,
                        "ValidateMarginInvariantHelper",
                        "validate_margin_invariant returned False for sizing generated by calculate_basket_size",
                        ctx,
                    )

    # Granular continuous sweep: $1 to $1,000 in steps of $1
    for eq_int in range(1, 1000, 1):
        eq = float(eq_int)
        for p in [1500.0, 2500.0, 4000.0]:
            sz = calculate_basket_size(equity=eq, price=p, leverage=100.0)
            margin = calculate_margin_required(sz, price=p, leverage=100.0)
            eval_count += 1
            logger.assert_true(
                margin <= (0.20 * eq + 1e-5),
                "ContinuousMarginSweep",
                f"Continuous sweep failed at eq=${eq}, p=${p}: margin=${margin:.4f}",
                {"equity": eq, "price": p, "sz": sz, "margin": margin},
            )

    logger.record_pass(
        "MarginCeilingStress",
        f"Evaluated {eval_count:,} combinations. Max observed margin utilization: {max_observed_margin_pct*100:.3f}% <= 20.000%",
    )


async def stress_test_execution_router_margin_rejection():
    """
    Verifies that ExecutionRouter strictly rejects orders exceeding the 20% margin ceiling
    across all boundary equities on SimulatedBrokerVenue.
    """
    print("\n--- Running Suite 1b: ExecutionRouter Margin Enforcement ---")
    test_equities = [1.0, 65.0, 100.0, 1000.0, 10000.0]
    test_prices = [1500.0, 2500.0, 4000.0]

    for eq in test_equities:
        for price in test_prices:
            venue = SimulatedBrokerVenue(initial_equity=eq)
            venue.set_market_price("GOLD", price)
            router = ExecutionRouter(venue=venue)

            # 1. Compliant size from dynamic formula
            safe_sz = calculate_basket_size(equity=eq, price=price, leverage=100.0)
            is_ok, req_margin, max_margin = await router.validate_margin(safe_sz, price)
            ctx = {"eq": eq, "price": price, "safe_sz": safe_sz, "req_margin": req_margin, "max_margin": max_margin}

            logger.assert_true(
                is_ok,
                "RouterSafeMarginApproval",
                "ExecutionRouter rejected safe basket size",
                ctx,
            )
            logger.assert_true(
                req_margin <= max_margin + 1e-5,
                "RouterReqMarginBoundary",
                "req_margin exceeded max_margin",
                ctx,
            )

            # 2. Artificially enlarged size that breaches 20% ceiling
            # max allowed size = (0.20 * eq * 100) / price
            max_allowed_sz = (0.20 * eq * 100.0) / price
            violating_sz = round(max_allowed_sz + 0.10, 2)
            if violating_sz <= 0:
                violating_sz = 0.50

            violating_ok, viol_margin, _ = await router.validate_margin(violating_sz, price)
            logger.assert_true(
                not violating_ok,
                "RouterViolatingMarginRejection",
                f"ExecutionRouter failed to reject violating size {violating_sz} (margin ${viol_margin:.2f} > max ${max_margin:.2f})",
                {"eq": eq, "price": price, "violating_sz": violating_sz, "viol_margin": viol_margin},
            )

            # Verify fire_layered_orders returns None when margin ceiling breached
            basket = await router.fire_layered_orders(
                symbol="GOLD",
                total_sz=violating_sz,
                invalidation_wick_price=round(price - 1.15, 2),
                is_buy=True,
            )
            logger.assert_true(
                basket is None,
                "RouterFireLayeredOrdersRejection",
                "fire_layered_orders did not return None for margin-breaching order",
                {"eq": eq, "price": price, "violating_sz": violating_sz},
            )

    logger.record_pass("ExecutionRouterMarginRejection", "All margin-violating orders rejected cleanly.")


# =========================================================================
# SUITE 2: Dollar Risk Per Trade Invariant (< 0.86% and strictly < 1.0% equity)
# =========================================================================

def stress_test_dollar_risk_per_trade_invariant():
    """
    Stress-tests dollar risk per trade:
    - Must be strictly < 0.86% equity for standard SL envelope ($1.00 - $1.50, default $1.15)
    - Must be strictly < 1.0% equity under ANY valid SL envelope parameter
    - Evaluated across boundary equities: $1, $65, $100, $1k, $10k, $1M
    - Evaluated across continuous SL distances: $1.00, $1.05, ..., $1.50
    """
    print("\n--- Running Suite 2: Dollar Risk Invariant Stress Tests ---")
    equities = [1.0, 13.0, 65.0, 100.0, 500.0, 1000.0, 10000.0, 1000000.0]
    sl_distances = [round(1.00 + 0.05 * i, 2) for i in range(11)]  # 1.00 to 1.50
    prices = [1500.0, 2500.0, 4000.0]

    eval_count = 0
    max_observed_risk_pct = 0.0

    for eq in equities:
        for sl in sl_distances:
            for p in prices:
                sz = calculate_basket_size(equity=eq, price=p, leverage=100.0, sl_distance=sl)
                dollar_risk = calculate_dollar_risk(sz, sl)
                risk_pct = dollar_risk / eq if eq > 0 else 0.0
                if risk_pct > max_observed_risk_pct:
                    max_observed_risk_pct = risk_pct

                eval_count += 1
                ctx = {"equity": eq, "sl": sl, "price": p, "sz": sz, "dollar_risk": dollar_risk, "risk_pct": risk_pct}

                # Invariant 2a: Risk strictly < 0.86% equity (with 1e-6 epsilon)
                logger.assert_true(
                    risk_pct <= 0.0086 + 1e-6,
                    "RiskUnder086PctInvariant",
                    f"Risk percentage {risk_pct*100:.4f}% exceeded 0.86% limit",
                    ctx,
                )

                # Invariant 2b: Risk strictly < 1.0% equity
                logger.assert_true(
                    risk_pct < 0.0100,
                    "RiskStrictlyUnder100PctInvariant",
                    f"Risk percentage {risk_pct*100:.4f}% exceeded 1.0% limit",
                    ctx,
                )

                # Dollar risk strictly <= 0.0086 * equity
                logger.assert_true(
                    dollar_risk <= (0.0086 * eq + 1e-5),
                    "DollarRiskAmountInvariant",
                    f"Dollar risk ${dollar_risk:.4f} exceeded max dollar risk ${0.0086 * eq:.4f}",
                    ctx,
                )

    logger.record_pass(
        "DollarRiskStress",
        f"Evaluated {eval_count:,} combinations. Max observed dollar risk: {max_observed_risk_pct*100:.4f}% (< 0.8600% and < 1.0000%).",
    )


def stress_test_sl_envelope_boundary_conditions():
    """
    Stress-tests the SL envelope boundaries ($1.00 min to $1.50 max delta, 0.10-0.15 wick buffer)
    in ExecutionRouter for BUY and SELL setups.
    """
    print("\n--- Running Suite 2b: SL Envelope Invalidation & Clamping ---")
    venue = SimulatedBrokerVenue()
    router = ExecutionRouter(venue=venue)
    entry_price = 2500.00

    # BUY setups
    # 1. Wick too far: delta > 1.50 -> must reject
    # Invalidation wick = 2498.00 -> raw SL = 2498.00 - 0.12 = 2497.88 -> delta = 2.12 > 1.50
    ok, sl_px, delta, reason = router.calculate_and_validate_sl(
        is_buy=True, entry_price=entry_price, invalidation_wick_price=2498.00
    )
    logger.assert_true(not ok, "BuyWideSLRejection", f"Expected rejection for delta {delta} > 1.50")
    logger.assert_true("exceeds maximum allowed" in reason, "BuyWideSLReason", "Reason missing max allowed warning")

    # 2. Wick valid: delta = 1.25 -> must accept
    # Invalidation wick = 2498.87 -> raw SL = 2498.75 -> delta = 1.25
    ok, sl_px, delta, reason = router.calculate_and_validate_sl(
        is_buy=True, entry_price=entry_price, invalidation_wick_price=2498.87
    )
    logger.assert_true(ok, "BuyValidSLAcceptance", f"Expected acceptance for delta {delta}")
    logger.assert_true(math.isclose(delta, 1.25, abs_tol=0.01), "BuyValidSLDeltaMatch", "Delta mismatch")

    # 3. Wick too close: delta < 1.00 -> must clamp to minimum $1.00 delta
    # Invalidation wick = 2499.70 -> raw SL = 2499.58 -> delta = 0.42 < 1.00
    ok, sl_px, delta, reason = router.calculate_and_validate_sl(
        is_buy=True, entry_price=entry_price, invalidation_wick_price=2499.70
    )
    logger.assert_true(ok, "BuyTightSLClamping", "Expected clamped acceptance for tight wick")
    logger.assert_true(math.isclose(delta, 1.00, abs_tol=1e-3), "BuyClampedDeltaMin", "Delta not clamped to 1.00")
    logger.assert_true(math.isclose(sl_px, 2499.00, abs_tol=1e-3), "BuyClampedSLPrice", "SL px not 2499.00")

    # SELL / Short setups
    # 4. Short wick too far: delta > 1.50 -> must reject
    ok, sl_px, delta, reason = router.calculate_and_validate_sl(
        is_buy=False, entry_price=entry_price, invalidation_wick_price=2502.00
    )
    logger.assert_true(not ok, "SellWideSLRejection", f"Expected rejection for sell delta {delta} > 1.50")

    # 5. Short wick valid: delta = 1.20 -> must accept
    ok, sl_px, delta, reason = router.calculate_and_validate_sl(
        is_buy=False, entry_price=entry_price, invalidation_wick_price=2501.08
    )
    logger.assert_true(ok, "SellValidSLAcceptance", f"Expected acceptance for sell delta {delta}")

    # 6. Short wick too close: delta < 1.00 -> must clamp to minimum $1.00 delta
    ok, sl_px, delta, reason = router.calculate_and_validate_sl(
        is_buy=False, entry_price=entry_price, invalidation_wick_price=2500.30
    )
    logger.assert_true(ok, "SellTightSLClamping", "Expected clamped acceptance for tight sell wick")
    logger.assert_true(math.isclose(delta, 1.00, abs_tol=1e-3), "SellClampedDeltaMin", "Delta not clamped to 1.00")
    logger.assert_true(math.isclose(sl_px, 2501.00, abs_tol=1e-3), "SellClampedSLPrice", "SL px not 2501.00")

    logger.record_pass("SLEnvelopeBoundaryStress", "Envelope constraints strictly enforce $1.00 min clamp and $1.50 max rejection.")


# =========================================================================
# SUITE 3: 5% Daily Max Drawdown Killswitch Boundary Conditions
# =========================================================================

def stress_test_daily_drawdown_killswitch_boundaries():
    """
    Stress-tests the 5% daily drawdown killswitch:
    1. Exact intraday threshold testing (4.999% vs 5.000% vs 5.001%)
    2. UTC Midnight rollover (23:59:59.999 UTC vs 00:00:00.000 UTC)
    3. Peak ratcheting during intraday profits
    4. Guard parity between engine.fsm.DailyDrawdownGuard and engine.monte_carlo_scaling.SimulationDailyDrawdownGuard
    """
    print("\n--- Running Suite 3: 5% Daily Drawdown Killswitch Boundary Tests ---")

    guards = [
        ("FSM_DailyDrawdownGuard", DailyDrawdownGuard(max_drawdown_pct=0.05)),
        ("Sim_DailyDrawdownGuard", SimulationDailyDrawdownGuard(max_drawdown_pct=0.05)),
    ]

    base_ts = 1774051200.0  # Exactly 2026-03-21 00:00:00 UTC (divisible by 86400)

    for guard_name, guard in guards:
        # 1. Day 1 start with $65.00
        tripped, dd = guard.update(65.00, base_ts)
        logger.assert_true(not tripped, f"{guard_name}_Day1Init", "Guard tripped on init")
        logger.assert_true(dd == 0.0, f"{guard_name}_Day1ZeroDD", "Initial DD not 0")

        # 2. Intraday peak rises to $70.00 at 04:00 UTC
        tripped, dd = guard.update(70.00, base_ts + 14400)
        logger.assert_true(not tripped, f"{guard_name}_PeakRise", "Guard tripped on peak rise")
        logger.assert_true(guard._peak_day_equity == 70.00, f"{guard_name}_PeakRatcheted", "Peak did not ratchet to 70.00")

        # Peak is $70.00. 5.0% DD = $3.50 -> threshold equity = $66.500000
        # Test 4.999% DD: equity = 70.00 - 3.4993 = $66.5007
        tripped, dd = guard.update(66.501, base_ts + 18000)
        logger.assert_true(not tripped, f"{guard_name}_SubThreshold499", f"Guard tripped at DD {dd*100:.4f}% < 5.0%")

        # Test exact 5.000% DD: equity = $66.5000
        tripped, dd = guard.update(66.5000, base_ts + 21600)
        logger.assert_true(tripped, f"{guard_name}_Exact500Threshold", f"Guard did not trip at exact 5.000% DD (dd={dd*100:.4f}%)")
        logger.assert_true(guard.is_tripped, f"{guard_name}_IsTrippedProperty", "is_tripped property False after tripping")

        # Test 5.001% DD: equity = $66.4990
        tripped, dd = guard.update(66.499, base_ts + 25200)
        logger.assert_true(tripped, f"{guard_name}_SuperThreshold501", "Guard not tripped above 5%")

        # Subsequent recovery within SAME UTC day: equity rises back to $68.00 at 22:00 UTC
        # Guard MUST REMAIN TRIPPED for remainder of the UTC day!
        tripped, dd = guard.update(68.00, base_ts + 79200)
        logger.assert_true(tripped, f"{guard_name}_SameDayLockout", "Guard un-tripped before midnight on equity recovery!")

        # 3. UTC Midnight Boundary Precision
        # 23:59:59.999 UTC: still tripped!
        tripped_pre, dd_pre = guard.update(68.00, base_ts + 86399.999)
        logger.assert_true(tripped_pre, f"{guard_name}_PreMidnightLock", "Guard un-tripped at 23:59:59.999 UTC")

        # 00:00:00.000 UTC (+86400.000s): Day rollover! MUST RESET!
        tripped_post, dd_post = guard.update(68.00, base_ts + 86400.000)
        logger.assert_true(not tripped_post, f"{guard_name}_MidnightReset", "Guard did not reset at 00:00:00 UTC rollover")
        logger.assert_true(not guard.is_tripped, f"{guard_name}_MidnightIsTrippedFalse", "is_tripped remains True on new day")
        logger.assert_true(guard._peak_day_equity == 68.00, f"{guard_name}_NewDayPeakReset", "New day peak equity not set to current equity")
        logger.assert_true(dd_post == 0.0, f"{guard_name}_NewDayZeroDD", "New day DD not reset to 0.0")

    logger.record_pass("DailyDrawdownKillswitchStress", "Exact 5.0% threshold and UTC midnight rollover verified across both guard implementations.")


async def stress_test_fsm_killswitch_liquidation():
    """
    Verifies that when the DailyDrawdownGuard trips while the FSM is in IN_TRADE,
    the active position basket is immediately liquidated with reason='MAX_DAILY_DRAWDOWN_KILLSWITCH'
    and state transitions to IDLE.
    """
    print("\n--- Running Suite 3b: FSM In-Trade Killswitch Liquidation ---")
    venue = SimulatedBrokerVenue(initial_equity=65.0)
    venue.set_market_price("GOLD", 2500.00)
    router = ExecutionRouter(venue=venue)
    dd_guard = DailyDrawdownGuard(max_drawdown_pct=0.05)
    fsm = RelapseFSM(execution_router=router, drawdown_guard=dd_guard)

    # Put FSM into IN_TRADE state with active basket
    basket = await router.fire_layered_orders(
        symbol="GOLD",
        total_sz=0.45,
        invalidation_wick_price=2498.85,
        is_buy=True,
    )
    logger.assert_true(basket is not None, "FSMBasketCreated", "Failed to create test basket")
    fsm.state = RelapseState.IN_TRADE
    fsm.active_basket = basket

    # Create 5M bar dataframe at current time
    now_ms = int(time.time() * 1000)
    df_bar = pd.DataFrame([{
        "open_time": now_ms,
        "open": 2500.0,
        "high": 2500.5,
        "low": 2499.5,
        "close": 2500.0,
        "volume": 1000.0,
    }])

    # Establish day baseline peak equity in dd_guard ($65.00)
    dd_guard.update(65.00, (now_ms - 300_000) / 1000.0)

    # Simulate equity drop breaching 5% ($65 * 0.949 = $61.68)
    venue.equity = 61.50

    # Trigger bar update
    new_state = await fsm.on_5m_bar_update(df_5m=pd.concat([df_bar]*35, ignore_index=True))

    logger.assert_true(
        new_state == RelapseState.IDLE,
        "FSMKillswitchTransitionToIdle",
        f"FSM state was {new_state} instead of IDLE upon killswitch trip",
    )
    logger.assert_true(
        router.active_basket_id is None,
        "FSMBasketLiquidatedOnKillswitch",
        "Active basket was not closed by killswitch handler",
    )
    logger.assert_true(
        not basket.is_active,
        "BasketIsActiveFlagFalse",
        "Basket remains active after killswitch liquidation",
    )

    logger.record_pass("FSMKillswitchLiquidation", "In-trade position liquidated immediately on 5% DD killswitch breach.")


# =========================================================================
# SUITE 4: Monte Carlo Scaling Engine Numerical Stability
# =========================================================================

def stress_test_monte_carlo_numerical_stability():
    """
    Adversarially tests MonteCarloScalingSimulator across boundary and pathological inputs:
    - n_simulations = 0, 1, 10
    - n_trades = 0, 1, 10
    - starting_equity = 0.0, -10.0, 0.01, 1.0, 65.0, 10000.0
    - target_equity <= starting_equity ($65 -> $50, $65 -> $65)
    - 100% loss rate (win_rate = 0.0) -> ruin probability 1.0, no NaN/Inf
    - 100% win rate (win_rate = 1.0) -> target reached, no NaN/Inf
    - Flat returns (zero variance) -> Sharpe calculation returns 0.0 without ZeroDivisionError
    - Short runs (< 3 daily returns) -> Sharpe returns 0.0 cleanly
    - Extreme slippages (100 pips) and fees
    - Full JSON telemetry serialization integrity
    """
    print("\n--- Running Suite 4: Monte Carlo Numerical Stability Stress Tests ---")

    # 1. Zero simulations edge case
    sim_zero = MonteCarloScalingSimulator(starting_equity=65.0, target_equity=10000.0, seed=1)
    try:
        rep_zero = sim_zero.run_simulation(n_simulations=0, n_trades=10)
        logger.assert_true(rep_zero.n_simulations == 0, "ZeroSimulationsCount", "n_simulations != 0")
        logger.assert_true(rep_zero.probability_target_reached == 0.0, "ZeroSimTargetProb", "Target prob != 0")
        logger.assert_true(rep_zero.probability_ruin == 0.0, "ZeroSimRuinProb", "Ruin prob != 0")
        logger.assert_true(rep_zero.mean_sharpe == 0.0, "ZeroSimMeanSharpe", "Mean sharpe != 0")
    except ZeroDivisionError as zde:
        logger.total_assertions += 1
        logger.failed_tests += 1
        logger.findings.append({
            "status": "FAIL",
            "test": "ZeroSimulationsDivisionByZero",
            "message": f"ZeroDivisionError in engine/monte_carlo_scaling.py:1044 when n_simulations=0 (killswitch_trips_mean calculation): {zde}",
            "context": {"file": "engine/monte_carlo_scaling.py", "line": 1044, "error": str(zde)},
        })
        print(f"[FAIL] ZeroSimulationsDivisionByZero: ZeroDivisionError at engine/monte_carlo_scaling.py:1044 when n_simulations=0: {zde}")

    # 2. Zero trades edge case
    sim_zero_trades = MonteCarloScalingSimulator(starting_equity=65.0, target_equity=10000.0, seed=2)
    rep_zero_trades = sim_zero_trades.run_simulation(n_simulations=5, n_trades=0)
    logger.assert_true(rep_zero_trades.n_trades == 0, "ZeroTradesCount", "n_trades != 0")
    logger.assert_true(len(rep_zero_trades.runs) == 5, "ZeroTradesRunsCount", "Runs count != 5")
    for r in rep_zero_trades.runs:
        logger.assert_true(r.trades_executed == 0, "ZeroTradesExecuted", "trades_executed != 0")
        logger.assert_true(r.final_equity == 65.0, "ZeroTradesEquityUnchanged", "Equity changed with 0 trades")

    # 3. Target <= Starting Equity edge case (immediate termination)
    sim_target_eq = MonteCarloScalingSimulator(starting_equity=65.0, target_equity=65.0, seed=3)
    rep_target_eq = sim_target_eq.run_simulation(n_simulations=5, n_trades=10)
    logger.assert_true(rep_target_eq.probability_target_reached == 1.0, "TargetEqualsStartingProb", "Prob target != 1.0")

    sim_target_below = MonteCarloScalingSimulator(starting_equity=65.0, target_equity=50.0, seed=4)
    rep_target_below = sim_target_below.run_simulation(n_simulations=5, n_trades=10)
    logger.assert_true(rep_target_below.probability_target_reached == 1.0, "TargetBelowStartingProb", "Prob target != 1.0")

    # 4. Zero / Negative Starting Equity
    sim_zero_eq = MonteCarloScalingSimulator(starting_equity=0.0, target_equity=10000.0, seed=5)
    rep_zero_eq = sim_zero_eq.run_simulation(n_simulations=3, n_trades=5)
    logger.assert_true(rep_zero_eq.probability_ruin == 1.0, "ZeroStartingEquityRuin", "Zero equity not marked ruined")

    sim_neg_eq = MonteCarloScalingSimulator(starting_equity=-50.0, target_equity=10000.0, seed=6)
    rep_neg_eq = sim_neg_eq.run_simulation(n_simulations=3, n_trades=5)
    logger.assert_true(rep_neg_eq.probability_ruin == 1.0, "NegStartingEquityRuin", "Neg equity not marked ruined")

    # 5. Pathological 100% Loss Rate (Ruin Stress)
    profile_all_loss = SyntheticTradeProfile(
        win_rate=0.0,
        breakeven_prob=0.0,
        loss_r=-1.0,
    )
    gen_all_loss = SyntheticTradeGenerator(profile=profile_all_loss, seed=7)
    trades_all_loss = gen_all_loss.generate_trades(100)

    sim_loss = MonteCarloScalingSimulator(starting_equity=65.0, target_equity=10000.0, ruin_equity=13.0, seed=8)
    rep_loss = sim_loss.run_simulation(n_simulations=20, n_trades=100, trades=trades_all_loss)
    logger.assert_true(rep_loss.probability_ruin == 1.0, "AllLossRuinProbability", "Prob ruin != 1.0 for 100% loss rate")
    logger.assert_true(rep_loss.probability_target_reached == 0.0, "AllLossTargetProbability", "Target reached with 100% loss")
    # Verify no NaN or Inf in report metrics
    dict_loss = rep_loss.to_dict()
    json_str_loss = json.dumps(dict_loss)
    logger.assert_true("NaN" not in json_str_loss and "Infinity" not in json_str_loss, "AllLossNoNaNInf", "JSON contained NaN/Inf")

    # 6. Pathological 100% Win Rate (Target Reaching & Compounding Stress)
    profile_all_win = SyntheticTradeProfile(
        win_rate=1.0,
        breakeven_prob=0.0,
        avg_win_r=4.0,
        min_win_r=3.0,
        max_win_r=5.0,
    )
    gen_all_win = SyntheticTradeGenerator(profile=profile_all_win, seed=9)
    trades_all_win = gen_all_win.generate_trades(300)

    sim_win = MonteCarloScalingSimulator(starting_equity=65.0, target_equity=10000.0, seed=10)
    rep_win = sim_win.run_simulation(n_simulations=10, n_trades=300, trades=trades_all_win)
    logger.assert_true(rep_win.probability_target_reached == 1.0, "AllWinTargetProbability", "Target not reached for 100% win rate")
    logger.assert_true(rep_win.probability_ruin == 0.0, "AllWinRuinProbability", "Ruin observed for 100% win rate")
    logger.assert_true(rep_win.median_trades_to_target is not None, "AllWinMedianTradesValid", "Median trades to target is None")

    # 7. Flat Equity / Zero Variance Sharpe Ratio Stress
    # Flat daily equity curve: [100.0, 100.0, 100.0, 100.0, 100.0] -> std = 0.0
    sharpe_flat = sim_win._calculate_sharpe([100.0, 100.0, 100.0, 100.0, 100.0])
    logger.assert_true(sharpe_flat == 0.0, "FlatCurveZeroSharpe", f"Sharpe for flat curve was {sharpe_flat} != 0.0")

    # Short curve (< 3 entries)
    sharpe_short = sim_win._calculate_sharpe([100.0, 101.0])
    logger.assert_true(sharpe_short == 0.0, "ShortCurveZeroSharpe", f"Sharpe for short curve was {sharpe_short} != 0.0")

    # 8. Extreme Friction Stress: Slippage 100 pips ($10.00/oz), High Funding
    friction_extreme = FrictionEngine(
        min_slippage_pips=100.0,
        max_slippage_pips=100.0,
        taker_fee_rate=0.01,  # 1% taker fee
        funding_rate_1h=0.01,  # 1% per hour
    )
    pips, slip_dollars = friction_extreme.sample_slippage()
    logger.assert_true(slip_dollars == 10.0, "ExtremeSlippageDollars", "Slippage calculation failed")

    avg_px, entry_fee, _ = friction_extreme.execute_entry_slices(basket_size=1.0, base_price=2500.0, is_buy=True)
    logger.assert_true(avg_px >= 2510.0, "ExtremeSlippageEntryPx", "Entry price did not reflect extreme slippage")
    logger.assert_true(entry_fee > 0.0, "ExtremeEntryFee", "Fee not positive")

    # 9. Large Permutation Stress (N=1,000 simulations over 500 trades)
    print("Running production-scale simulation (N=1,000 simulations x 500 trades)...")
    start_time = time.time()
    sim_prod = MonteCarloScalingSimulator(starting_equity=65.0, target_equity=10000.0, seed=42)
    rep_prod = sim_prod.run_simulation(n_simulations=1000, n_trades=500)
    elapsed = time.time() - start_time
    print(f"Production-scale simulation completed in {elapsed:.2f}s ({len(rep_prod.runs)} runs)")

    logger.assert_true(rep_prod.n_simulations == 1000, "ProdSimulationsCount", "Simulations count mismatch")
    logger.assert_true(rep_prod.n_trades == 500, "ProdTradesCount", "Trades count mismatch")
    logger.assert_true(elapsed < 20.0, "ProdSimulationLatency", f"Simulation took {elapsed:.2f}s > 20s ceiling")

    # Monotonicity checks for percentiles
    dd = rep_prod.max_drawdown_dist
    logger.assert_true(
        dd["min"] <= dd["p25"] <= dd["p50"] <= dd["p75"] <= dd["p90"] <= dd["p95"] <= dd["p99"] <= dd["max"],
        "DrawdownPercentilesMonotonicity",
        f"Drawdown percentiles not monotonic: {dd}",
    )

    sh = rep_prod.sharpe_ratio_dist
    logger.assert_true(
        sh["min"] <= sh["p25"] <= sh["p50"] <= sh["p75"] <= sh["p90"] <= sh["p95"] <= sh["max"],
        "SharpePercentilesMonotonicity",
        f"Sharpe percentiles not monotonic: {sh}",
    )

    # JSON serializability check
    prod_dict = rep_prod.to_dict()
    prod_json = json.dumps(prod_dict)
    logger.assert_true(len(prod_json) > 100, "ProdJSONValid", "Serialized JSON is empty")
    logger.assert_true("NaN" not in prod_json and "Infinity" not in prod_json, "ProdJSONNoNaNInf", "Production JSON contains NaN/Inf")

    logger.record_pass(
        "MonteCarloNumericalStability",
        f"All pathological scenarios passed. N=1,000 simulations executed in {elapsed:.2f}s without numerical anomalies.",
    )


# =========================================================================
# Main Test Harness Orchestration
# =========================================================================

def run_all_stress_tests():
    print("=" * 80)
    print("STARTING EMPIRICAL ADVERSARIAL STRESS TEST SUITE")
    print("=" * 80)
    t0 = time.time()

    try:
        # Suite 1
        stress_test_margin_ceiling_across_boundaries_and_shocks()
        asyncio.run(stress_test_execution_router_margin_rejection())

        # Suite 2
        stress_test_dollar_risk_per_trade_invariant()
        stress_test_sl_envelope_boundary_conditions()

        # Suite 3
        stress_test_daily_drawdown_killswitch_boundaries()
        asyncio.run(stress_test_fsm_killswitch_liquidation())

        # Suite 4
        stress_test_monte_carlo_numerical_stability()

    except Exception as exc:
        print(f"\n[FATAL] Stress test harness crashed with unhandled exception: {exc}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    t1 = time.time()
    total_time = t1 - t0

    print("\n" + "=" * 80)
    print("STRESS TEST HARNESS EXECUTION SUMMARY")
    print("=" * 80)
    print(f"Total Test Assertions Evaluated: {logger.total_assertions:,}")
    print(f"Passed Assertions:             {logger.passed_tests:,}")
    print(f"Failed Assertions:             {logger.failed_tests:,}")
    print(f"Total Execution Time:          {total_time:.2f}s")
    print("=" * 80)

    if logger.failed_tests > 0:
        print(f"\nBINARY VERDICT: REJECT ({logger.failed_tests} failed assertions)")
        sys.exit(1)
    else:
        print("\nBINARY VERDICT: APPROVE (100% of adversarial stress tests passed)")
        sys.exit(0)


if __name__ == "__main__":
    run_all_stress_tests()
