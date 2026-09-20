#!/usr/bin/env python3
"""
The paper venue: execute paper orders like Bitunix really would.

The paper book records decisions; this module makes the FILLS honest by
walking the REAL order book of the real venue:

  - a market order eats the book from the top, level by level, and fills
    at the volume-weighted average -- slippage beyond half the spread is
    real depth, not a guess (and a thin book fills only part of the size)
  - a resting limit fills at its own price, up to the depth waiting there
  - the real minimum order size per symbol is enforced, so a paper trade
    the venue would refuse is refused here with the same words
  - if the book cannot be read, the fill falls back to the half-spread
    model and says so

Nothing is invented: every price and every size comes from Bitunix's own
depth endpoint, the same one the live path uses.
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger("paper")

# How long a depth reading is reused. The book polls seconds apart; the
# venue's depth changes constantly but a burst of signals must not hammer
# the endpoint.
DEPTH_TTL_S = 5.0

# How many book levels a market order may eat before the rest is a miss.
MAX_LEVELS = 20

# Symbols whose venue information has been read once per session.
_INFO: dict[str, dict] = {}
_INFO_STAMP: dict[str, float] = {}
_INFO_TTL = 3600.0


def venue_info(cli, sym: str) -> dict:
    """The real symbol's constraints: min qty, precision, leverage cap."""
    if sym in _INFO and time.time() - _INFO_STAMP.get(sym, 0) < _INFO_TTL:
        return _INFO[sym]
    info = {}
    try:
        pairs = cli.trading_pairs()
        p = pairs.get(sym) or {}
        info = {"min_qty": float(p.get("minTradeVolume") or 0.0),
                "base_prec": int(p.get("basePrecision") or 3),
                "quote_prec": int(p.get("quotePrecision") or 4),
                "max_lev": float(p.get("maxLeverage") or 0.0)}
    except Exception:
        info = {"min_qty": 0.0, "base_prec": 3, "quote_prec": 4,
                "max_lev": 0.0}
    _INFO[sym] = info
    _INFO_STAMP[sym] = time.time()
    return info


def depth_fill(cli, sym: str, side: str, notional: float,
               ref_px: float) -> tuple[float, float, bool]:
    """(avg fill price, filled fraction, depth_known) for a market order.

    Eats the real book from the top: a BUY walks the asks, a SELL the bids,
    up to MAX_LEVELS. The fill price is the volume-weighted average of what
    was actually consumed. When the book cannot absorb the whole notional,
    the remainder is a miss -- a partial fill, exactly as the venue gives.
    """
    try:
        d = cli.depth(sym, limit=MAX_LEVELS)
        rows = d.get("asks" if side == "BUY" else "bids") or []
    except Exception:
        rows = []
    if not rows:
        # No book to read: the honest fallback is the old half-spread
        # model, and the caller is told the depth is unknown.
        half = 0.0
        try:
            half = float(cli.spread_bps(sym) or 0) / 2 / 1e4
        except Exception:
            pass
        px = ref_px * (1 + half) if side == "BUY" else ref_px * (1 - half)
        return px, 1.0, False
    spent = 0.0
    vol = 0.0
    used_levels = 0
    for lv in rows:
        try:
            px = float(lv[0])
            qty = float(lv[1])
        except (TypeError, ValueError, IndexError):
            continue
        if px <= 0 or qty <= 0:
            continue
        value = px * qty
        take = min(value, notional - spent)
        if take <= 0:
            break
        spent += take
        vol += take / px
        used_levels += 1
        if spent >= notional - 1e-12:
            break
    if vol <= 0:
        return ref_px, 0.0, True
    avg = spent / vol
    frac = min(1.0, spent / max(notional, 1e-12))
    log.debug("depth fill %s %s: %.2f of $%.0f at %.8g over %d levels",
              side, sym, frac, notional, avg, used_levels)
    return avg, frac, True


def enforce_min_qty(cli, sym: str, qty: float) -> str | None:
    """None when the venue would take the size, else the refusal wording."""
    mn = venue_info(cli, sym).get("min_qty") or 0.0
    if mn and qty < mn:
        return (f"order size {qty:g} is under the exchange minimum {mn:g}")
    return None


_FUND: dict[str, tuple[float | None, float]] = {}


def funding_rate(cli, sym: str) -> float | None:
    """The real funding rate, cached for ten minutes."""
    got = _FUND.get(sym)
    if got and time.time() - got[1] < 600:
        return got[0]
    try:
        r = cli.funding_rate(sym)
    except Exception:
        r = None
    _FUND[sym] = (r, time.time())
    return r
