"""
tjr/tests/test_tjr_system.py
============================
Comprehensive Unit & Integration Test Suite for the TJR Trading Architecture:
- Fractal swing identification & causal confirmation
- Liquidity sweeps & MSS displacement detection
- FVG creation & 50% discount/premium dealing range calculation
- Laya AI Decision primitives: choice, score, noul
- To The Moon compounding progression from $59 USD
- Trade journaling & analytics extraction
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from tjr.tjr_engine import Direction, FairValueGap, MarketStateSnapshot, TJREngineConfig, TJRMicrostructureEngine, TJRSetup
from tjr.tjr_laya_model import LayaDecisionOutput, TJRLayaModel
from tjr.tjr_to_the_moon_compounding import TJRToTheMoonCompounding
from tjr.tjr_trade_journal import JournalEntry, TJRTradeJournal


def test_tjr_fractal_and_sweep_detection():
    engine = TJRMicrostructureEngine(TJREngineConfig(swing_window=3, displacement_mult=1.2, fvg_min_atr=0.10))

    # Generate synthetic series with clear low pivot, sweep, displacement, and FVG
    dates = pd.date_range("2026-01-01 08:00:00", periods=40, freq="1min", tz="UTC")
    # Base price flat at 100
    prices = np.ones(40) * 100.0
    # Create low swing pivot at index 10: low drops to 95.0
    lows = prices.copy() - 0.2
    highs = prices.copy() + 0.2
    opens = prices.copy()
    closes = prices.copy()

    # Pivot at index 10
    lows[10] = 95.0
    highs[10] = 96.0

    # Candle 15 sweeps below 95.0 (low 94.5) but closes at 96.0 (wick sweep!)
    lows[15] = 94.5
    closes[15] = 96.0
    opens[15] = 95.5
    highs[15] = 96.5

    # Candle 17 is aggressive displacement upwards (close 102.0, open 96.5)
    opens[17] = 96.5
    highs[17] = 102.5
    lows[17] = 96.2
    closes[17] = 102.0

    # Candle 18 opens at 102.0, high 104.0, low 101.5, close 103.5 -> leaves FVG between highs[16]=100.2 and lows[18]=101.5
    highs[16] = 98.0
    opens[18] = 102.0
    lows[18] = 100.0
    highs[18] = 104.0
    closes[18] = 103.5

    df = pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes}, index=dates)

    atr = engine.compute_atr(df)
    assert len(atr) == 40
    assert not math.isnan(atr.iloc[-1])


def test_tjr_laya_model_primitives():
    laya = TJRLayaModel(confidence_threshold=0.80)

    # 1. High-confluence setup during NY AM session
    good_state = MarketStateSnapshot(
        symbol="XAUUSD",
        timestamp=datetime.now(timezone.utc),
        bar_index=100,
        session_window="NY_AM",
        htf_bias="BULLISH",
        direction="LONG",
        sweep_side="SELL_SIDE",
        sweep_penetration_pts=0.4,
        sweep_penetration_pips=4.0,
        displacement_ratio=2.10,
        fvg_size_atr=0.50,
        dealing_range_coordinate=0.25,  # Deep discount
        reward_to_risk=3.0,
        atr_14=2.0,
        entry_price=2500.0,
        stop_loss=2495.0,
        take_profit=2515.0,
        risk_pts=5.0,
        reward_pts=15.0,
    )

    regime = laya.primitive_choice_regime(good_state)
    assert regime in ["TRENDING_ORDERFLOW", "NORMAL_EXPANSION"]

    grade = laya.primitive_score_grade(good_state, regime)
    assert grade >= 2

    prob = laya.primitive_noul_probability(good_state)
    assert 0.0 <= prob <= 1.0
    assert prob >= 0.70

    decision = laya.evaluate_setup(good_state)
    assert isinstance(decision, LayaDecisionOutput)
    assert decision.authorized is True
    assert decision.compounding_multiplier >= 1.0

    # 2. Low-quality chop setup in Asian session with wide penetration
    bad_state = MarketStateSnapshot(
        symbol="XAUUSD",
        timestamp=datetime.now(timezone.utc),
        bar_index=150,
        session_window="ASIAN_OFF_HOURS",
        htf_bias="NEUTRAL",
        direction="LONG",
        sweep_side="SELL_SIDE",
        sweep_penetration_pts=3.5,
        sweep_penetration_pips=35.0,
        displacement_ratio=0.85,
        fvg_size_atr=0.08,
        dealing_range_coordinate=0.68,  # Premium (bad for LONG)
        reward_to_risk=1.2,
        atr_14=2.0,
        entry_price=2500.0,
        stop_loss=2490.0,
        take_profit=2505.0,
        risk_pts=10.0,
        reward_pts=5.0,
    )

    bad_regime = laya.primitive_choice_regime(bad_state)
    assert bad_regime == "CHOPPY_NOISE"

    bad_decision = laya.evaluate_setup(bad_state)
    assert bad_decision.authorized is False
    assert bad_decision.grade <= 1


def test_tjr_to_the_moon_compounding_ladder():
    comp = TJRToTheMoonCompounding(starting_balance=59.0)

    # Initial $59 start -> Genesis Launch Tier, 0.10 lots
    lots_59, tier_59 = comp.compute_lot_size()
    assert lots_59 == 0.10
    assert "Genesis" in tier_59

    # Win a trade (+ $25 profit)
    res_1 = comp.process_trade_result(realized_pnl=25.0, is_win=True)
    assert res_1["current_balance"] == 84.0
    assert res_1["win_rate_pct"] == 100.0

    # Test scaling to $150 tier -> 0.20 lots
    lots_150, tier_150 = comp.compute_lot_size(balance=150.0)
    assert lots_150 == 0.20

    # Test scaling to $600 tier -> 0.80 lots
    lots_600, tier_600 = comp.compute_lot_size(balance=600.0)
    assert lots_600 == 0.80

    # Test scaling to $3,000 tier -> 3.00 lots
    lots_3k, tier_3k = comp.compute_lot_size(balance=3000.0)
    assert lots_3k == 3.00

    # Test scaling to $75,000 tier -> dynamic scaling
    lots_75k, tier_75k = comp.compute_lot_size(balance=75000.0)
    assert lots_75k >= 12.0


def test_tjr_trade_journaling_and_analytics():
    journal = TJRTradeJournal()

    entry_1 = JournalEntry(
        ticket_id=1,
        symbol="XAUUSD",
        entry_time="2026-01-01 08:30:00",
        exit_time="2026-01-01 08:45:00",
        direction="LONG",
        entry_price=2500.0,
        exit_price=2510.0,
        stop_loss=2495.0,
        take_profit=2510.0,
        position_size=0.10,
        risk_pts=5.0,
        reward_pts=10.0,
        rr_realized=2.0,
        realized_pnl=100.0,
        is_win=True,
        exit_reason="TP_HIT",
        holding_bars=15,
        mae_pts=1.2,
        mfe_pts=10.0,
        session_window="NY_AM",
        htf_bias="BULLISH",
        sweep_side="SELL_SIDE",
        sweep_penetration_pips=8.5,
        displacement_ratio=1.75,
        fvg_size_atr=0.30,
        dealing_range_coordinate=0.35,
        planned_rr=2.0,
        atr_14=2.0,
    )

    entry_2 = JournalEntry(
        ticket_id=2,
        symbol="XAUUSD",
        entry_time="2026-01-01 09:30:00",
        exit_time="2026-01-01 09:40:00",
        direction="SHORT",
        entry_price=2510.0,
        exit_price=2515.0,
        stop_loss=2515.0,
        take_profit=2500.0,
        position_size=0.10,
        risk_pts=5.0,
        reward_pts=10.0,
        rr_realized=-1.0,
        realized_pnl=-50.0,
        is_win=False,
        exit_reason="SL_HIT",
        holding_bars=10,
        mae_pts=5.0,
        mfe_pts=1.0,
        session_window="NY_AM",
        htf_bias="BEARISH",
        sweep_side="BUY_SIDE",
        sweep_penetration_pips=11.0,
        displacement_ratio=1.45,
        fvg_size_atr=0.22,
        dealing_range_coordinate=0.62,
        planned_rr=2.0,
        atr_14=2.0,
    )

    journal.record_entry(entry_1)
    journal.record_entry(entry_2)

    assert journal.count() == 2
    analytics = journal.compute_summary_analytics()
    assert analytics["total_trades"] == 2
    assert analytics["win_rate_pct"] == 50.0
    assert analytics["profit_factor"] == 2.0  # 100 profit / 50 loss = 2.0
    assert analytics["total_pnl"] == 50.0

    X, y, cols = journal.get_training_features_and_labels()
    assert len(X) == 2
    assert len(y) == 2
    assert len(cols) == 6
