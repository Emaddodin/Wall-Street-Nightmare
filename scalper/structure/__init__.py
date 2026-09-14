"""Market structure helpers: breaks of structure (BOS) and swing bookkeeping.

Operates on the precomputed per-TF arrays of a SymbolData (see engine.py):
`last_sh[j]` / `last_sl[j]` are the most recent CONFIRMED swing high/low
known at bar j (within the configured lookback); a close beyond that level
is a break of structure.
"""
from __future__ import annotations

import numpy as np

LONG = 1
SHORT = -1


def bos_at(tf, j: int, direction: int) -> bool:
    """Did the closed bar j break the most recent confirmed swing in
    `direction`?  Long breaks the last swing HIGH, short the last swing LOW.
    A swing confirmed by bar j itself cannot be broken by bar j (close can
    never exceed its own high), so the check is naturally strict."""
    if j <= 0:
        return False
    if direction == LONG:
        level = tf.last_sh[j]
        return bool(not np.isnan(level) and tf.c[j] > level)
    level = tf.last_sl[j]
    return bool(not np.isnan(level) and tf.c[j] < level)


def bos_level(tf, j: int, direction: int) -> float:
    """The most recent confirmed swing level in `direction` known at bar j
    (NaN when none exists within the lookback)."""
    arr = tf.last_sh if direction == LONG else tf.last_sl
    return float(arr[j])


def higher_low_between(tf, from_bar: int, to_bar: int, above: float,
                       direction: int) -> bool:
    """Did a confirmed swing in `direction` form in (from_bar, to_bar] whose
    price is above `above`?  (Long: a confirmed swing LOW above the swept
    low = the 'higher low' step of the setup sequence.)"""
    arr = tf.swing_lo if direction == LONG else tf.swing_hi
    seg = arr[from_bar + 1: to_bar + 1]
    valid = seg[~np.isnan(seg)]
    if direction == LONG:
        return bool((valid > above).any())
    return bool((valid < above).any())


def opposite_bos(tf, j: int, direction: int) -> bool:
    """Break of structure AGAINST `direction` at bar j (the model-C exit)."""
    return bos_at(tf, j, -direction)


def swing_at_or_before(tf, j: int, direction: int, not_after: int) -> float:
    """Most recent confirmed swing level in `direction` known at bar j but
    confirmed no later than `not_after` -- the 'structure high/low' as it
    stood before the displacement bar.  Falls back to the plain last swing
    when none qualifies."""
    arr = tf.swing_hi if direction == LONG else tf.swing_lo
    if not_after >= j:
        level = arr[j]
    else:
        known = arr[: not_after + 1]
        level = known[~np.isnan(known)][-1] if (~np.isnan(known)).any() else np.nan
    return float(level)
