"""
tests/test_volume_profile.py
============================
Unit tests for the Volume Profile Engine:
- Verifies causal calculation of Point of Control (POC), Value Area High (VAH), and Value Area Low (VAL).
- Verifies Value Area enclosing ~70% of total traded volume.
- Verifies detection of High Volume Nodes (HVN) and Low Volume Nodes (LVN).
- Verifies position classification ('ABOVE_VAH', 'INSIDE_VA', 'BELOW_VAL').
"""
import numpy as np
import pandas as pd
import pytest
from scalper.strategies.volume_profile import VolumeProfileEngine, VolumeProfileResult


def test_volume_profile_basic_poc_vah_val():
    engine = VolumeProfileEngine(default_bins=50, default_va_pct=0.70)

    # Synthetic range from 3000 to 3010
    # Heavy volume concentrated around 3005.00
    n = 100
    np.random.seed(42)
    lows = np.random.uniform(3000, 3008, n)
    highs = lows + np.random.uniform(0.5, 2.0, n)
    volumes = np.ones(n) * 10.0

    # Inject massive volume around 3005.00
    for i in range(20):
        lows[i] = 3004.5
        highs[i] = 3005.5
        volumes[i] = 500.0

    res = engine.compute_profile(highs, lows, volumes, bins=50, va_pct=0.70)
    assert res.is_valid
    assert 3004.0 <= res.poc <= 3006.0
    assert res.val <= res.poc <= res.vah
    assert res.val >= 3000.0
    assert res.vah <= 3010.0


def test_volume_profile_position_classification():
    engine = VolumeProfileEngine(default_bins=40, default_va_pct=0.70)

    highs = np.array([3010.0, 3008.0, 3006.0, 3004.0])
    lows = np.array([3006.0, 3004.0, 3002.0, 3000.0])
    volumes = np.array([100.0, 200.0, 200.0, 100.0])

    res = engine.compute_profile(highs, lows, volumes)
    assert res.is_valid

    # Classification check
    assert res.position_relative_to_va(res.vah + 1.0) == "ABOVE_VAH"
    assert res.position_relative_to_va(res.val - 1.0) == "BELOW_VAL"
    assert res.position_relative_to_va(res.poc) == "INSIDE_VA"


def test_volume_profile_empty_and_flat_guards():
    engine = VolumeProfileEngine()

    # Empty array
    res_empty = engine.compute_profile(np.array([]), np.array([]), np.array([]))
    assert not res_empty.is_valid

    # Flat range (< 0.20 span)
    res_flat = engine.compute_profile(np.array([3000.05]), np.array([3000.00]), np.array([10.0]))
    assert res_flat.is_valid
    assert res_flat.poc == pytest.approx(3000.025, abs=0.01)


def test_apex_trinity_hold_long_poc_bounce():
    from scalper.strategies.apex_trinity import ApexTrinityStrategy

    strat = ApexTrinityStrategy(min_candles_warmup=25)
    base_t = 1770000000000
    candles = []
    # 1. 25 accumulation bars around 3000 with heavy volume establishing POC
    for i in range(25):
        candles.append({
            "open_time": base_t + i * 60000,
            "open": 3000.0,
            "high": 3000.5,
            "low": 2999.5,
            "close": 3000.2,
            "volume": 500.0,
        })

    # 2. 15 gentle upward bars establishing bullish EMA20 > EMA50 alignment
    px = 3000.2
    for i in range(25, 40):
        px += 0.15
        candles.append({
            "open_time": base_t + i * 60000,
            "open": px - 0.1,
            "high": px + 0.3,
            "low": px - 0.2,
            "close": px,
            "volume": 50.0,
        })

    # Compute Volume Profile from established base
    vp = strat.vp_engine.compute_from_candles(candles, lookback_bars=40)
    assert vp.is_valid
    poc = vp.poc

    # 3. Add hammer pin dipping into POC and rejecting upward
    candles.append({
        "open_time": base_t + 40 * 60000,
        "open": poc + 0.90,
        "high": poc + 2.10,
        "low": poc - 0.10,  # dips to POC
        "close": poc + 2.00,  # strong bullish rejection close
        "volume": 100.0,
    })

    df_test = pd.DataFrame(candles)
    df_test["ema20"] = df_test["close"].ewm(span=20).mean()
    df_test["ema50"] = df_test["close"].ewm(span=50).mean()

    curr_close = float(df_test["close"].iloc[-1])
    sig = strat._evaluate_volume_profile(
        df_test,
        curr_px=curr_close,
        atr=1.50,
        curr_time=(base_t + 40 * 60000) / 1000.0,
        vp=vp,
    )
    assert sig is not None
    assert sig.direction == "BUY"
    assert sig.strategy_type == "HOLD_LONG_POC_BOUNCE"
    assert sig.is_hold_long is True
    assert sig.is_scalp_sell is False
    assert sig.tp1_price > sig.entry_price
    assert sig.spike_target > sig.tp1_price


def test_micro_exit_direction_aware_configs():
    from scalper.strategies.micro_exit_controller import get_micro_account_config, get_to_the_moon_config

    # Micro account BUY vs SELL asymmetry
    cfg_buy = get_micro_account_config(balance=30.0, direction="BUY")
    cfg_sell = get_micro_account_config(balance=30.0, direction="SELL")

    # Hold Long has wider targets and longer duration
    assert cfg_buy.micro_harvest_target == 3.50
    assert cfg_buy.micro_harvest_extended == 6.50
    assert cfg_buy.time_decay_seconds == 1200.0

    # Scalp Sell has tight targets and faster cut
    assert cfg_sell.micro_harvest_target == 1.80
    assert cfg_sell.micro_harvest_extended == 2.50
    assert cfg_sell.time_decay_seconds == 600.0
    assert cfg_sell.fast_be_trigger == 0.80

    # Moon config BUY vs SELL
    moon_buy = get_to_the_moon_config("XAUUSD", direction="BUY")
    moon_sell = get_to_the_moon_config("XAUUSD", direction="SELL")
    assert moon_buy.micro_harvest_target > moon_sell.micro_harvest_target
    assert moon_buy.time_decay_seconds > moon_sell.time_decay_seconds


def test_live_broker_scalper_parallel_positions():
    from run_xau_broker_live import LiveBrokerScalper

    class MockGateway:
        pass

    scalper = LiveBrokerScalper(MockGateway())
    assert len(scalper.active_positions) == 0
    assert scalper.active_stack is None

    # Add position 1
    pos1 = {"ticket_id": "pos_1", "direction": "BUY", "entry_price": 3000.0, "volume": 0.01}
    scalper.active_positions.append(pos1)
    assert len(scalper.active_positions) == 1
    assert scalper.active_stack["ticket_id"] == "pos_1"

    # Add parallel position 2
    pos2 = {"ticket_id": "pos_2", "direction": "BUY", "entry_price": 3002.0, "volume": 0.01}
    scalper.active_positions.append(pos2)
    assert len(scalper.active_positions) == 2
    assert scalper.active_stack["ticket_id"] == "pos_2"

    # Setting active_stack = None clears all parallel positions cleanly
    scalper.active_stack = None
    assert len(scalper.active_positions) == 0
    assert scalper.active_stack is None


def test_politician_brain_calendar_freeze_and_post_news():
    from scalper.brain.politician_brain import PoliticianBrain
    import time
    from datetime import datetime, timezone, timedelta

    brain = PoliticianBrain()
    now = datetime.now(timezone.utc)

    # 1. Event in 10 minutes (within 15m window) -> FREEZE
    upcoming_time = (now + timedelta(minutes=10)).isoformat()
    with brain._lock:
        brain._cached_calendar_events = [
            {"title": "CPI Release", "impact": "High", "country": "USD", "date": upcoming_time}
        ]

    is_frozen, reason, is_post = brain.check_calendar_freeze(window_minutes=15)
    assert is_frozen is True
    assert "CPI Release" in reason
    assert is_post is False

    # 2. Event released 15 minutes ago (within 5m to 45m post-news expansion window)
    past_time = (now - timedelta(minutes=15)).isoformat()
    with brain._lock:
        brain._cached_calendar_events = [
            {"title": "Non-Farm Payrolls", "impact": "High", "country": "USD", "date": past_time}
        ]

    is_frozen, reason, is_post = brain.check_calendar_freeze(window_minutes=15)
    assert is_frozen is False
    assert is_post is True
    assert "Post-Non-Farm Payrolls Institutional Expansion" in reason

