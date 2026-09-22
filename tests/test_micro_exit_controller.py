"""
tests/test_micro_exit_controller.py
===================================
Automated verification of the Adaptive Dynamic Micro-Exit Controller ("بازی با پوزیشن"),
including exact tick-by-tick simulation of the scalp.mp4 footage.
"""

import pytest
import time
from scalper.strategies.micro_exit_controller import (
    MicroExitController,
    MicroExitConfig,
    get_default_config,
    ExitDecision,
)


def test_scalp_mp4_forensic_replay():
    """
    Forensic tick replay of scalp.mp4:
    - Initial balance: ~$98.86
    - Stack entry: 1.40 lots (14 x 0.10) BUY at 3030.50
    - Price trajectory over 25 seconds:
      3030.50 -> 3030.70 -> 3030.95 -> 3031.20 -> 3031.46 -> 3031.44 (stall)
    - Verifies fast BE lock, momentum tracking, sweet-spot stall trigger, and profit capture.
    """
    controller = MicroExitController(get_default_config("XAUUSD"))
    t0 = 1000.0
    
    # Arm stack at entry
    controller.arm_position(
        entry_price=3030.50,
        direction="BUY",
        total_volume=1.40,
        sl_price=3028.50,
        open_time=t0,
    )
    assert not controller.be_locked

    # t = 4s: Small uptick to 3030.70 (+0.20 pts, +$28.00)
    dec = controller.evaluate_tick(3030.70, floating_pnl=28.0, current_time=t0 + 4.0)
    assert not dec.should_exit
    assert not controller.be_locked
    assert dec.current_gain == pytest.approx(0.20, abs=1e-3)

    # t = 8s: Move to 3030.90 (+0.40 pts, +$56.00) -> Fast Breakeven MUST trigger
    dec = controller.evaluate_tick(3030.90, floating_pnl=56.0, current_time=t0 + 8.0)
    assert not dec.should_exit
    assert controller.be_locked
    assert controller.sl_price >= 3030.50  # Risk-free cushion locked

    # t = 14s: Move into harvest zone at 3031.25 (+0.75 pts, +$105.00)
    dec = controller.evaluate_tick(3031.25, floating_pnl=105.0, current_time=t0 + 14.0)
    assert not dec.should_exit  # Moving actively higher, riding impulse

    # t = 20s: Surge to impulse peak 3031.46 (+0.96 pts, +$134.40)
    dec = controller.evaluate_tick(3031.46, floating_pnl=134.40, current_time=t0 + 20.0)
    assert not dec.should_exit
    assert controller.peak_price == 3031.46

    # t = 21s: Tick stall 1 (3031.45)
    dec = controller.evaluate_tick(3031.45, floating_pnl=133.0, current_time=t0 + 21.0)
    assert not dec.should_exit
    assert controller.consecutive_stalls == 1

    # t = 22s: Tick stall 2 (3031.44) -> Sweet-spot harvest on stall MUST trigger!
    dec = controller.evaluate_tick(3031.44, floating_pnl=131.60, current_time=t0 + 22.0)
    assert dec.should_exit
    assert dec.metric_label == "SWEET_SPOT_STALL"
    assert "Sweet-Spot Harvest on Stall" in dec.reason
    assert dec.current_gain == pytest.approx(0.94, abs=1e-2)
    assert dec.time_in_trade_sec == pytest.approx(22.0, abs=1e-2)


def test_peak_watermark_protection():
    """
    Tests that a floating profit that hits +$80 and starts collapsing
    is harvested by the watermark trailing rule (>18% pullback from peak).
    """
    controller = MicroExitController(get_default_config("XAUUSD"))
    t0 = 2000.0
    controller.arm_position(entry_price=3030.00, direction="BUY", total_volume=1.0, open_time=t0)

    # Surge to +$80 peak at 3030.80
    controller.evaluate_tick(3030.80, floating_pnl=80.0, current_time=t0 + 10.0)
    assert controller.peak_pnl == 80.0

    # Retrace to +$64 (pullback of $16 = 20% > 18%)
    dec = controller.evaluate_tick(3030.64, floating_pnl=64.0, current_time=t0 + 12.0)
    assert dec.should_exit
    assert dec.metric_label == "PEAK_WATERMARK"
    assert "Peak Watermark Harvest" in dec.reason
    assert dec.floating_pnl == 64.0


def test_scalper_time_decay():
    """
    Tests that if a scalp stalls for >30 seconds without reaching targets,
    it exits on time decay to free margin and prevent choppy reversal bleed.
    """
    controller = MicroExitController(get_default_config("XAUUSD"))
    t0 = 3000.0
    controller.arm_position(entry_price=3030.00, direction="BUY", total_volume=0.80, open_time=t0)

    # Trade floats between -0.10 and +0.15 for 32 seconds
    controller.evaluate_tick(3030.10, floating_pnl=8.0, current_time=t0 + 15.0)
    dec = controller.evaluate_tick(3030.12, floating_pnl=9.6, current_time=t0 + 31.0)
    
    assert dec.should_exit
    assert dec.metric_label == "TIME_DECAY_PROFIT"
    assert "Time-Decay Exit" in dec.reason


def test_hard_risk_stop():
    """
    Tests that floating loss exceeding -$15 triggers an immediate EMERGENCY exit.
    """
    controller = MicroExitController(get_default_config("XAUUSD"))
    t0 = 4000.0
    controller.arm_position(entry_price=3030.00, direction="BUY", total_volume=0.80, open_time=t0)

    dec = controller.evaluate_tick(3028.00, floating_pnl=-16.00, current_time=t0 + 5.0)
    assert dec.should_exit
    assert dec.urgency == "EMERGENCY"
    assert dec.metric_label == "HARD_STOP"


def test_eurusd_pip_calibration():
    """
    Verifies EURUSD calibration (pip scale = 0.0001, target = 7 pips, BE = 2 pips).
    """
    controller = MicroExitController(get_default_config("EURUSD"))
    t0 = 5000.0
    controller.arm_position(entry_price=1.08500, direction="BUY", total_volume=1.0, open_time=t0)

    # 1.08522 (+2.2 pips) -> Fast BE must trigger
    dec = controller.evaluate_tick(1.08522, floating_pnl=22.0, current_time=t0 + 10.0)
    assert not dec.should_exit
    assert controller.be_locked
    assert dec.current_gain == pytest.approx(2.2, abs=1e-2)

    # 1.08575 (+7.5 pips) -> Sweet spot zone
    controller.evaluate_tick(1.08575, floating_pnl=75.0, current_time=t0 + 20.0)
    # 2 stalls
    controller.evaluate_tick(1.08573, floating_pnl=73.0, current_time=t0 + 21.0)
    dec = controller.evaluate_tick(1.08572, floating_pnl=72.0, current_time=t0 + 22.0)
    assert dec.should_exit
    assert dec.metric_label == "SWEET_SPOT_STALL"
    assert "pips" in dec.reason
