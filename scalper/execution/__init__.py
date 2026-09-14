"""Execution model: fees + slippage, applied to every fill.

Backtests must NOT assume perfect fills.  Every leg pays:
  * taker fee (configurable bps of notional, per leg)
  * slippage (configurable bps, adverse direction)

Entry fills at the next bar's open (signal bars are never traded); stop and
target fills also eat adverse slippage so the model is conservative, not
flattering.
"""
from __future__ import annotations

import math

from structure import LONG


def entry_price(ref: float, direction: int, fee_bps: float,
                slippage_bps: float) -> tuple[float, float]:
    """(fill_price, fee_usd_per_unit_notional) for a market entry.

    Long pays the ask side: ref * (1 + slip); short pays ref * (1 - slip).
    The fee is returned per unit of NOTIONAL so the caller multiplies by
    (qty * price)."""
    slip = slippage_bps / 1e4
    price = ref * (1.0 + slip) if direction == LONG else ref * (1.0 - slip)
    fee = fee_bps / 1e4
    return price, fee


def exit_price(level: float, direction: int, slippage_bps: float,
               adverse: bool = True) -> float:
    """Fill price when a stop/target at `level` triggers.

    adverse=True: the fill is worse than the level by slippage (stops gap
    through, targets fill behind) -- the honest, conservative model."""
    if not adverse:
        return level
    slip = slippage_bps / 1e4
    # long exits by selling: fills below the level
    return level * (1.0 - slip) if direction == LONG else level * (1.0 + slip)


def pnl_usd(direction: int, qty: float, entry: float, exit_px: float,
            entry_fee: float, exit_fee: float) -> float:
    """Closed PnL: price move minus both legs' fees."""
    return (exit_px - entry) * qty * direction - entry_fee - exit_fee


def round_trip_cost_r(stop_frac: float, fee_bps: float,
                      slippage_bps: float,
                      entry_fee_bps: float | None = None,
                      entry_slippage: bool = True) -> float:
    """Round-trip fees + slippage expressed in units of R (the risk budget).

    A position that risks `stop_frac` of its notional pays the venue
    2*(fee+slip)/1e4 of that notional to open and close.  Dividing the two
    gives the share of the risk budget the venue takes before the market
    moves at all -- the number that decides whether a setup can ever be
    positive-sum.

    Measured on the 130-day farm backtest: the median stop was 0.152% of
    price, which at 6 bps taker + 2 bps slippage costs 1.05R per round
    trip.  The median trade paid more than its whole risk budget in
    friction, which is why profit factor was 0.11 with a live edge.

    A resting limit fills as MAKER and eats no slippage, so pass
    `entry_fee_bps=maker_fee_bps, entry_slippage=False` for those; the
    defaults price a taker market entry on both legs.
    """
    if not stop_frac or stop_frac <= 0 or not math.isfinite(stop_frac):
        return float("inf")
    entry_fee = fee_bps if entry_fee_bps is None else entry_fee_bps
    slips = 2.0 if entry_slippage else 1.0
    cost = (entry_fee + fee_bps + slips * slippage_bps) / 1e4
    return cost / stop_frac


def min_viable_stop_frac(fee_bps: float, slippage_bps: float,
                         max_fee_r: float) -> float:
    """The tightest stop whose round-trip cost stays within `max_fee_r`.

    Inverse of round_trip_cost_r -- what the stop must be for the venue to
    take no more than `max_fee_r` of the risk budget."""
    if max_fee_r <= 0:
        return float("inf")
    return 2.0 * (fee_bps + slippage_bps) / 1e4 / max_fee_r
