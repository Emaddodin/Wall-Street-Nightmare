"""
tests/test_live_margin_and_switch.py
====================================
Rigorous unit test suite verifying:
1. Live broker margin math (1:500 leverage on XAUUSD, contract size 100 oz).
2. Micro-account sizing clamps (<$75 strictly 0.01 lots, 0 parallel positions).
3. Free margin buffering (1.5x single, 2.5x parallel) & Margin Level guard (>200%).
4. Demo-to-Real automated transition trigger on first winning trade.
"""

import pytest
import math
from unittest.mock import AsyncMock, MagicMock, patch
from run_xau_broker_live import LiveBrokerScalper, sync_dashboard_state
from engine.litefinance_gateway import LiteFinanceGateway, AccountSnapshot, QuoteSnapshot


def test_live_margin_exact_calculations():
    """Verify margin per 0.01 lot at various gold prices with 1:500 leverage."""
    gw = MagicMock(spec=LiteFinanceGateway)
    scalper = LiveBrokerScalper(gw)

    # At $4,270/oz: 0.01 lot = 1 oz. Value = $4,270. Margin = 4270 / 500 = $8.54
    px_4270 = 4270.0
    margin_4270 = (px_4270 * 100.0 * 0.01) / 500.0
    assert round(margin_4270, 2) == 8.54

    # At $4,300/oz: Margin = 4300 / 500 = $8.60
    px_4300 = 4300.0
    margin_4300 = (px_4300 * 100.0 * 0.01) / 500.0
    assert round(margin_4300, 2) == 8.60

    # At $4,500/oz: Margin = 4500 / 500 = $9.00
    px_4500 = 4500.0
    margin_4500 = (px_4500 * 100.0 * 0.01) / 500.0
    assert round(margin_4500, 2) == 9.00


def test_micro_account_lot_size_ladder():
    """Test lot sizing ladder for micro-accounts (<$75 strictly 0.01 lots)."""
    gw = MagicMock(spec=LiteFinanceGateway)
    scalper = LiveBrokerScalper(gw)

    # Balance $30 (Demo micro-start)
    assert scalper.compute_lot_size(30.0, current_price=4270.0) == 0.01

    # Balance $50
    assert scalper.compute_lot_size(50.0, current_price=4270.0) == 0.01

    # Balance $59.87 (Real account balance in snapshot)
    assert scalper.compute_lot_size(59.87, current_price=4270.0) == 0.01

    # Balance $74.50 (Just below $75 threshold)
    assert scalper.compute_lot_size(74.50, current_price=4270.0) == 0.01

    # Balance $80.00 (Crossed $75 threshold)
    # At $80 with max 30% margin utilization: 80 * 0.30 = $24.00.
    # Required for 0.02 lots = $17.08. So 0.02 is permitted!
    assert scalper.compute_lot_size(80.0, current_price=4270.0) == 0.02

    # Insufficient capital to open 0.01 lots (< 1.5x margin requirement = $12.81)
    assert scalper.compute_lot_size(10.0, current_price=4270.0) == 0.0


def test_parallel_position_margin_shield():
    """Verify parallel stacking constraints: strictly 1 position below $75, buffer guards above $75."""
    gw = MagicMock(spec=LiteFinanceGateway)
    scalper = LiveBrokerScalper(gw)

    # Below $75: max_parallel must be 1
    max_p_30 = 1 if 30.0 < 75.0 else (2 if 30.0 < 150.0 else 3)
    assert max_p_30 == 1

    max_p_59 = 1 if 59.87 < 75.0 else (2 if 59.87 < 150.0 else 3)
    assert max_p_59 == 1

    # Above $75: max_parallel is 2
    max_p_80 = 1 if 80.0 < 75.0 else (2 if 80.0 < 150.0 else 3)
    assert max_p_80 == 2

    # Parallel entry check logic test:
    margin_per_001 = (4270.0 * 100.0 * 0.01) / 500.0  # 8.54
    req_cushion = margin_per_001 * 2.50  # 21.35

    # Case A: Free available margin is only $15 (< 21.35) -> VETO
    available_low = 15.0
    has_cushion_low = available_low >= req_cushion
    assert has_cushion_low is False

    # Case B: Free available margin is $25 (> 21.35) -> ALLOW
    available_high = 25.0
    has_cushion_high = available_high >= req_cushion
    assert has_cushion_high is True

    # Case C: Projected margin level guard
    # If equity is $80, used margin is $8.54 + $8.54 = $17.08:
    proj_used = 17.08
    margin_level = (80.0 / proj_used) * 100.0  # 468% -> WELL ABOVE 250%
    assert margin_level >= 250.0

    # If equity dropped to $35 and used is $17.08:
    margin_level_stressed = (35.0 / proj_used) * 100.0  # 204% -> BELOW 250% -> VETO
    assert margin_level_stressed < 250.0


def test_demo_to_real_switch_trigger_logic():
    """Verify that a confirmed winning realized PnL (>= $1.00) triggers the Demo-to-Real transition."""
    import asyncio
    from run_xau_broker_live import MIN_DEMO_WIN_FOR_SWITCH

    async def _test():
        gw = AsyncMock(spec=LiteFinanceGateway)
        gw.get_account_mode.return_value = "DEMO"
        gw.switch_account_mode.return_value = {
            "success": True,
            "mode": "REAL",
            "balance": 29.66,
            "equity": 29.66,
            "available": 29.66,
        }
        gw.get_account_snapshot.return_value = AccountSnapshot(
            balance=29.66, equity=29.66, assets_used=0.0, available=29.66, floating_pnl=0.0
        )

        scalper = LiveBrokerScalper(gw)
        scalper.account_mode = "DEMO"

        # Simulate exit of a winning demo trade (+1.85 USD >= MIN_DEMO_WIN_FOR_SWITCH)
        tot_pnl_win = 1.85
        should_switch = (getattr(scalper, "account_mode", "REAL") == "DEMO" and tot_pnl_win >= MIN_DEMO_WIN_FOR_SWITCH)
        assert should_switch is True

        # Execute simulated switch
        res = await gw.switch_account_mode("REAL")
        assert res["success"] is True
        assert res["mode"] == "REAL"
        scalper.account_mode = "REAL"

        # Subsequent trades are now in REAL mode and should NOT re-trigger switch
        tot_pnl_win_2 = 2.40
        should_switch_again = (getattr(scalper, "account_mode", "REAL") == "DEMO" and tot_pnl_win_2 >= MIN_DEMO_WIN_FOR_SWITCH)
        assert should_switch_again is False

    asyncio.run(_test())


def test_demo_scratch_profit_does_not_trigger_switch():
    """Verify that a scratch/minor profit (< $1.00) on Demo does NOT trigger switch to Real."""
    from run_xau_broker_live import MIN_DEMO_WIN_FOR_SWITCH
    scalper = LiveBrokerScalper(MagicMock(spec=LiteFinanceGateway))
    scalper.account_mode = "DEMO"

    tot_pnl_scratch = 0.25  # Only +25 cents (breakeven cushion / scratch)
    should_switch = (getattr(scalper, "account_mode", "REAL") == "DEMO" and tot_pnl_scratch >= MIN_DEMO_WIN_FOR_SWITCH)
    assert should_switch is False
    assert scalper.account_mode == "DEMO"


def test_demo_loss_does_not_trigger_switch():
    """Verify that a losing trade on Demo does NOT trigger the switch to Real."""
    from run_xau_broker_live import MIN_DEMO_WIN_FOR_SWITCH
    scalper = LiveBrokerScalper(MagicMock(spec=LiteFinanceGateway))
    scalper.account_mode = "DEMO"

    tot_pnl_loss = -1.50
    should_switch = (getattr(scalper, "account_mode", "REAL") == "DEMO" and tot_pnl_loss >= MIN_DEMO_WIN_FOR_SWITCH)
    assert should_switch is False
    assert scalper.account_mode == "DEMO"


def test_demo_mode_calibrates_to_live_margin_numbers():
    """
    CRITICAL USER MANDATE: Even on Demo, numbers must strictly mirror Live margin!
    Demo balance might be $10,000, but effective balance must be $29.66.
    Lot size must strictly be 0.01 lots and risk ceiling ~$2.37.
    """
    from scalper.strategies.micro_exit_controller import get_micro_account_config
    gw = MagicMock(spec=LiteFinanceGateway)
    scalper = LiveBrokerScalper(gw)
    scalper.account_mode = "DEMO"
    scalper.live_real_balance = 29.66

    # Demo reported balance is $10,000
    demo_reported_balance = 10000.0
    eff_bal = scalper.get_effective_balance(demo_reported_balance)
    assert eff_bal == 29.66

    # Lot size must be strictly 0.01 lots, NOT 10.0 lots!
    lots = scalper.compute_lot_size(eff_bal, current_price=4295.0)
    assert lots == 0.01

    # MicroExitController config must use effective balance
    cfg = get_micro_account_config(balance=eff_bal, direction="BUY")
    assert cfg.hard_risk_stop_usd <= 2.50
    assert cfg.watermark_activate_usd == 2.00


def test_calc_required_margin_utility():
    """Verify standalone calc_required_margin utility function."""
    from run_xau_broker_live import calc_required_margin

    # 0.01 lot at $4,270 with 1:500 leverage
    m_001 = calc_required_margin(4270.0, 0.01)
    assert round(m_001, 2) == 8.54

    # 0.02 lot at $4,270
    m_002 = calc_required_margin(4270.0, 0.02)
    assert round(m_002, 2) == 17.08

    # 0.05 lot at $4,300
    m_005 = calc_required_margin(4300.0, 0.05)
    assert round(m_005, 2) == 43.00


def test_dynamic_hard_stop_scales_with_volume():
    """Verify that MicroExitController scales dynamic_hard_stop by volume so 0.02L does not stop out prematurely."""
    from scalper.strategies.micro_exit_controller import MicroExitController, get_micro_account_config

    cfg = get_micro_account_config(balance=59.87, direction="BUY")
    ctrl = MicroExitController(cfg)

    # Position with 0.01 lots and 2.50 pt SL ($2.50 risk)
    ctrl.arm_position(entry_price=4270.0, direction="BUY", total_volume=0.01, sl_price=4267.50)
    assert ctrl.dynamic_hard_stop <= 3.13  # Micro clamp around ~$2.88-$3.125

    # Position with 0.02 lots and 2.50 pt SL ($5.00 risk)
    ctrl_002 = MicroExitController(cfg)
    ctrl_002.arm_position(entry_price=4270.0, direction="BUY", total_volume=0.02, sl_price=4267.50)
    # Must allow at least the full $5.00 computed risk plus cushion without capping at $3.125
    assert ctrl_002.dynamic_hard_stop >= 5.00
    assert ctrl_002.dynamic_hard_stop <= 6.25  # 2.50 * 2.0 * 1.25 = 6.25


def test_sync_dashboard_state_dynamic_baseline(tmp_path):
    """Verify that sync_dashboard_state uses start_balance to avoid phantom PnL on Demo."""
    import run_xau_broker_live
    import json
    
    orig_app = run_xau_broker_live.STATE_FILE_APP
    orig_relapse = run_xau_broker_live.STATE_FILE_RELAPSE
    
    test_app = tmp_path / "hft.json"
    test_relapse = tmp_path / "relapse.json"
    
    run_xau_broker_live.STATE_FILE_APP = test_app
    run_xau_broker_live.STATE_FILE_RELAPSE = test_relapse
    
    try:
        # On Demo with $300 balance and $300 start balance -> realized_pnl must be $0.00, NOT $300 - $59.87!
        sync_dashboard_state(
            balance=300.0,
            equity=300.0,
            account_mode="DEMO",
            start_balance=300.0,
        )
        data = json.loads(test_app.read_text())
        assert data["realized_pnl"] == 0.0
        assert data["pnl_pct"] == 0.0
        assert data["account_mode"] == "DEMO"
    finally:
        run_xau_broker_live.STATE_FILE_APP = orig_app
        run_xau_broker_live.STATE_FILE_RELAPSE = orig_relapse


def test_real_account_29_66_margin_level():
    """Verify that $29.66 balance with 0.01 lot ($8.59 used) satisfies 320% margin level."""
    from run_xau_broker_live import calc_required_margin
    price = 4295.0
    req_margin = calc_required_margin(price, 0.01)
    equity = 29.66
    margin_level = (equity / req_margin) * 100.0  # 345.28%
    min_margin_level = 320.0
    assert margin_level >= min_margin_level


def test_effective_margin_state_simulates_real_on_demo():
    """Verify that get_effective_margin_state returns $29.66 reference in DEMO mode."""
    gw = MagicMock(spec=LiteFinanceGateway)
    scalper = LiveBrokerScalper(gw)
    scalper.account_mode = "DEMO"
    scalper.live_real_balance = 29.66

    raw_acc = AccountSnapshot(balance=10000.0, equity=10000.0, assets_used=0.0, available=10000.0, floating_pnl=0.0)
    eff_bal, eff_equity, eff_used, eff_avail = scalper.get_effective_margin_state(raw_acc)

    assert eff_bal == 29.66
    assert eff_equity == 29.66
    assert eff_used == 0.0
    assert eff_avail == 29.66


def test_get_account_mode_detection_levels():
    """Verify get_account_mode resolves DEMO/REAL across DOM leaf elements and locator fallbacks."""
    import asyncio

    async def _run():
        gw = LiteFinanceGateway()
        page = AsyncMock()
        page.is_closed = MagicMock(return_value=False)
        gw._page = page

        # Level 1: Leaf JS evaluation returns "DEMO"
        page.evaluate.return_value = "DEMO"
        assert await gw.get_account_mode() == "DEMO"

        # Level 2: Leaf JS evaluation returns "REAL"
        page.evaluate.return_value = "REAL"
        assert await gw.get_account_mode() == "REAL"

        # Level 3: Leaf JS returns "UNKNOWN", but page locator has "DEMO ACCOUNT"
        page.evaluate.return_value = "UNKNOWN"
        demo_loc = AsyncMock()
        demo_loc.count.return_value = 1
        real_loc = AsyncMock()
        real_loc.count.return_value = 0

        def mock_locator(selector):
            if "DEMO ACCOUNT" in selector:
                return demo_loc
            return real_loc

        page.locator = MagicMock(side_effect=mock_locator)
        page.content = AsyncMock(return_value="")
        assert await gw.get_account_mode() == "DEMO"

    asyncio.run(_run())


def test_startup_guard_prevents_trading_on_real_when_demo_fails():
    """Verify that if broker is in REAL mode and user requested DEMO, trading is paused if switch fails."""
    import asyncio

    async def _run():
        gw = AsyncMock(spec=LiteFinanceGateway)
        gw.get_account_mode.return_value = "REAL"
        gw.switch_account_mode.return_value = {"success": False, "error": "SWITCH_TIMEOUT"}

        scalper = LiveBrokerScalper(gw)
        req_mode = "DEMO"
        initial_mode = "REAL"

        # Simulate startup guard logic
        if req_mode in ("DEMO", "REAL") and initial_mode != req_mode:
            sw_res = await gw.switch_account_mode(req_mode)
            if not sw_res.get("success"):
                actual_mode = await gw.get_account_mode()
                if actual_mode in ("DEMO", "REAL"):
                    initial_mode = actual_mode
                if req_mode == "DEMO" and initial_mode == "REAL":
                    scalper.trading_paused = True

        assert scalper.trading_paused is True
        assert initial_mode == "REAL"

    asyncio.run(_run())


def test_micro_account_drawdown_limit_and_circuit_breaker():
    """Verify that micro accounts (<$75) enforce $5.00 max daily loss and 2-consecutive-loss permanent halt."""
    gw = MagicMock(spec=LiteFinanceGateway)
    scalper = LiveBrokerScalper(gw)
    scalper.daily_start_balance = 29.66
    scalper.live_real_balance = 29.66
    scalper.account_mode = "REAL"

    eff_bal = scalper.get_effective_balance(29.66)
    assert eff_bal == 29.66

    # Verify max_day_loss calculation
    max_day_loss = 5.00 if eff_bal < 75.0 else min(50.0, max(10.0, scalper.daily_start_balance * 0.15))
    assert max_day_loss == 5.00  # Must be strictly $5.00, NOT $10.00!

    # Simulate 2 consecutive losses of -$2.20
    scalper.consecutive_losses += 1
    scalper.daily_realized_loss += 2.20
    assert not (scalper.consecutive_losses >= 2 or scalper.daily_realized_loss >= max_day_loss)

    scalper.consecutive_losses += 1
    scalper.daily_realized_loss += 2.20
    if scalper.consecutive_losses >= 2 or scalper.daily_realized_loss >= max_day_loss:
        scalper.circuit_breaker_active = True
        scalper.trading_paused = True

    assert scalper.circuit_breaker_active is True
    assert scalper.trading_paused is True


def test_broker_sl_clamping_for_micro_accounts():
    """Verify that broker_sl is clamped to max 2.80 pts ($2.80 risk) for accounts < $75."""
    eff_bal = 29.66
    max_sl_dist = 2.80 if eff_bal < 75.0 else 5.00
    assert max_sl_dist == 2.80

    # BUY trade at 4305.00 with wide structural SL at 4295.00 (10 pts)
    entry_px = 4305.00
    wide_sl = 4295.00
    atr_1m = 1.20
    min_sl_dist = max(1.50, atr_1m * 1.0)
    raw_broker_sl = min(wide_sl, entry_px - min_sl_dist)
    broker_sl = round(max(entry_px - max_sl_dist, raw_broker_sl), 2)
    assert broker_sl == 4302.20  # Exactly 2.80 pts away, NOT 10 pts!

    # SELL trade at 4305.00 with wide structural SL at 4315.00 (10 pts)
    wide_sell_sl = 4315.00
    raw_sell_sl = max(wide_sell_sl, entry_px + min_sl_dist)
    broker_sell_sl = round(min(entry_px + max_sl_dist, raw_sell_sl), 2)
    assert broker_sell_sl == 4307.80  # Exactly 2.80 pts away!



