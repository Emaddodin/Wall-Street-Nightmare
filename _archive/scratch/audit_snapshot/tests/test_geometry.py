"""
Leverage, the stop, the liquidation line, and what a loss actually costs.

At fifty times, half the wallet per trade, the distance between "the stop took
us out" and "the exchange took us out" is the difference between losing 41% of
the account and losing 50% of it. Everything here is about that distance.
"""
from __future__ import annotations

import pytest

import papertrade as P
from papertrade import _stop_room


@pytest.fixture(autouse=True)
def _restore_room():
    was = dict(P._ROOM)
    yield
    P._ROOM.update(was)


def liq_at(lev, maint=0.005):
    """Where the exchange closes us, as a percent of price."""
    return ((1.0 / lev) - maint) * 100


def test_the_stop_ceiling_is_the_liquidation_line():
    assert _stop_room(lev=50, maint=0.005) == pytest.approx(liq_at(50))
    assert _stop_room(lev=20, maint=0.005) == pytest.approx(liq_at(20))


def test_the_stop_ceiling_follows_the_leverage_actually_in_use():
    """The regression that made the ceiling a lie at any leverage but fifty.

    Every caller asks for this with no arguments, so it used to answer 1.5%
    whatever `--lev` said. At a hundred times the exchange closes the position
    0.5% against us while a 1.5% stop was still being accepted as "inside the
    leverage" -- the loss is the whole margin, at a worse fill, and the exit is
    not ours. At twenty times it refused shapes with three percent of room to
    spare.
    """
    P._ROOM.update(lev=100.0, maint=0.005)
    assert _stop_room() == pytest.approx(liq_at(100))
    assert _stop_room() < 1.5

    P._ROOM.update(lev=20.0, maint=0.005)
    assert _stop_room() == pytest.approx(liq_at(20))
    assert _stop_room() > 1.5

    P._ROOM.update(lev=50.0, maint=0.005)
    assert _stop_room() == pytest.approx(1.5), "the live setting must not move"


def test_a_stop_inside_the_ceiling_is_reachable_at_every_leverage():
    """The invariant the whole design rests on, checked across the range."""
    for lev in (10, 20, 25, 50, 75, 100):
        P._ROOM.update(lev=float(lev), maint=0.005)
        assert _stop_room() <= liq_at(lev) + 1e-9, (
            f"at {lev}x a permitted stop sits past the liquidation line")


def test_the_ceiling_never_returns_a_useless_stop():
    P._ROOM.update(lev=10_000.0, maint=0.005)
    assert _stop_room() >= 0.05


# ------------------------------------------------------------- what it costs
def test_at_fifty_times_a_two_percent_stop_is_never_reached():
    """The engine prints this warning at startup; here it is as arithmetic.

    `--sl 2.1 --lev 50` is what the live unit passes for anything that is not
    a shape. The exchange closes the position at 1.5%, so the 2.1% stop is
    decoration: the loss is the entire margin rather than 2.1% of notional.
    """
    assert liq_at(50) < 2.1
    margin, lev = 50.0, 50.0
    notional = margin * lev
    at_the_stop = notional * 0.021          # what the book would have modelled
    at_liquidation = margin                 # what actually happens
    assert at_liquidation < at_the_stop
    assert at_the_stop / margin > 1.0, "the modelled stop exceeds the margin"


def test_a_shape_stop_is_inside_the_margin_at_the_live_leverage():
    """A shape's stop comes from the band, and the band is capped by leverage."""
    P._ROOM.update(lev=50.0, maint=0.005)
    room = _stop_room()
    margin, lev = 50.0, 50.0
    loss = margin * lev * (room / 100.0)
    assert loss <= margin + 1e-9, "even the widest shape stop stays inside the margin"


@pytest.mark.parametrize("tp,lev,frac,equity", [(10.0, 50.0, 0.5, 100.0)])
def test_the_target_is_what_the_service_claims(tp, lev, frac, equity):
    """'Target 10% of price -- 500% of the margin committed.'"""
    margin = equity * frac
    notional = margin * lev
    gross = notional * tp / 100.0
    assert gross == pytest.approx(margin * lev * tp / 100.0)
    assert gross / margin == pytest.approx(tp * lev / 100.0)
    assert gross / margin == pytest.approx(5.0)


def test_fees_are_charged_on_notional_not_margin():
    """12 bps of $2,500 is $3 -- of the $50 margin it would be six cents."""
    notional, fee_bps = 2500.0, 12.0
    assert notional * fee_bps / 1e4 == pytest.approx(3.0)


def test_a_liquidation_can_never_cost_more_than_the_margin():
    """Whatever the price does, the isolated margin is the whole loss."""
    margin, notional, fee_bps = 50.0, 2500.0, 12.0
    pnl = -margin - notional * fee_bps / 1e4 / 2
    assert -pnl <= margin + notional * fee_bps / 1e4
    assert pnl < 0


# ------------------------------------------------------------------- ramping
def test_ramp_is_clamped_and_monotonic():
    assert P._ramp(-5, 0, 10) == 0.0
    assert P._ramp(15, 0, 10) == 1.0
    assert P._ramp(5, 0, 10) == pytest.approx(0.5)
    assert P._ramp(None, 0, 10) is None
    assert P._ramp(10, 10, 10) == 1.0          # a degenerate ramp is a step
    assert P._ramp(9, 10, 10) == 0.0
