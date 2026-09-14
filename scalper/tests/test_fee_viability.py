"""The fee-viability bound.

The 130-day farm backtest died with profit factor 0.11 while every entry
model, both biases and every volatility regime were negative.  The cause was
not the signal: the median stop was 0.152% of price, and at 6 bps taker +
2 bps slippage a round trip on that stop costs 1.05R -- the median trade
handed its entire risk budget to the exchange before the market moved.
These tests pin the arithmetic and the guard that refuses such setups.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from execution import min_viable_stop_frac, round_trip_cost_r  # noqa: E402


def test_cost_is_inverse_in_stop_distance():
    """Halving the stop doubles the share of R the venue takes."""
    wide = round_trip_cost_r(0.010, 6.0, 2.0)
    tight = round_trip_cost_r(0.005, 6.0, 2.0)
    assert tight == pytest.approx(2 * wide)


def test_measured_median_stop_costs_more_than_one_r():
    """The observed median (0.152% stop, 6+2 bps) is negative-sum."""
    assert round_trip_cost_r(0.00152, 6.0, 2.0) == pytest.approx(1.053, abs=1e-3)


def test_zero_and_nonfinite_stops_are_infinitely_expensive():
    for bad in (0.0, -0.001, float("nan"), float("inf")):
        assert round_trip_cost_r(bad, 6.0, 2.0) == float("inf")


def test_min_viable_stop_inverts_the_cost():
    frac = min_viable_stop_frac(6.0, 2.0, 0.20)
    assert frac == pytest.approx(0.008)            # 0.80% of price
    assert round_trip_cost_r(frac, 6.0, 2.0) == pytest.approx(0.20)


def test_maker_fees_move_the_bound():
    """Cutting 6 bps taker to 2 bps maker cuts the required stop 2x."""
    taker = min_viable_stop_frac(6.0, 2.0, 0.20)
    maker = min_viable_stop_frac(2.0, 2.0, 0.20)
    assert maker < taker
    assert maker == pytest.approx(0.004)


def test_guard_rejects_the_backtest_median_stop():
    """The shipped bound (tightened 2026-09-11 to 1.0R): the backtest
    median stop (0.152%) pays 1.05R in friction and is REJECTED now --
    friction must cost less than one whole risk unit."""
    from config.loader import load_config
    cfg = load_config(extra_file=Path(__file__).resolve().parents[1]
                      / "config" / "aggressive.yaml")
    bound = cfg.execution["max_fee_r"]
    fee, slip = cfg.execution["fee_bps"], cfg.execution["slippage_bps"]
    assert round_trip_cost_r(0.0005, fee, slip) > bound
    assert round_trip_cost_r(0.00152, fee, slip) > bound
    assert round_trip_cost_r(0.0100, fee, slip) <= bound


def test_engine_module_resolves_every_name_it_uses():
    """engine.py used SHORT while importing only LONG, so the backtest
    raised NameError on the first short setup that reached the stale-signal
    check -- silently, because no test ever exercised that branch.  Compile
    the module and assert every global it reads is resolvable."""
    import engine

    missing = [n for n in engine.run.__code__.co_names
               if n.isupper() and not hasattr(engine, n)] \
        if hasattr(engine, "run") else []
    assert not missing, f"engine globals not defined: {missing}"

    for name in ("LONG", "SHORT"):
        assert hasattr(engine, name), f"engine.{name} is not imported"


def test_engine_short_direction_constant_is_negative_one():
    import engine
    from structure import LONG as SLONG, SHORT as SSHORT
    assert (engine.LONG, engine.SHORT) == (SLONG, SSHORT) == (1, -1)


def test_maker_entry_is_cheaper_than_taker_entry():
    """A resting limit fills as maker and eats no slippage, so it must cost
    strictly less than the same trade entered at market.  Charging limits
    the taker rate overstated their cost by 4 bps = 0.26R at the measured
    0.152% median stop."""
    taker = round_trip_cost_r(0.00152, 6.0, 2.0)
    maker = round_trip_cost_r(0.00152, 6.0, 2.0,
                              entry_fee_bps=2.0, entry_slippage=False)
    assert maker < taker
    assert taker == pytest.approx(1.053, abs=1e-3)
    assert maker == pytest.approx(0.658, abs=1e-3)


def test_default_arguments_still_price_a_taker_round_trip():
    """The default path must stay 2*(fee+slip)/d exactly."""
    d, fee, slip = 0.004, 6.0, 2.0
    assert round_trip_cost_r(d, fee, slip) == pytest.approx(
        2 * (fee + slip) / 1e4 / d)


def test_maker_entries_halve_the_viable_stop_requirement():
    """Maker entry moves the 0.20R bound from 0.80% down to 0.50%."""
    assert round_trip_cost_r(0.0050, 6.0, 2.0,
                             entry_fee_bps=2.0,
                             entry_slippage=False) == pytest.approx(0.20)


def test_config_exposes_a_maker_rate_below_the_taker_rate():
    from config.loader import load_config
    cfg = load_config()
    assert cfg.execution["maker_fee_bps"] < cfg.execution["fee_bps"]
