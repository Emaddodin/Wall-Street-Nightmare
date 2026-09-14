"""
Deterministic synthetic markets.

Every generator here produces candles that the engine's OWN shape functions
recognise -- nothing declares "a trade should happen" by hand. A test asks for
a band-and-break and then asserts that `breakout()` returns one; if the shape
logic changes so that it no longer does, the test fails loudly instead of
quietly testing nothing.

All series are keyed by epoch seconds on a fixed grid, exactly as the chart
hands them over, so bar boundaries and ages are real.
"""
from __future__ import annotations

BAR = 900          # 15 minutes, the timeframe the live services run on
T0 = 1788400000    # a fixed Wednesday; nothing here depends on the wall clock


def _typ_range(bars: dict) -> float:
    """The coin's own ordinary candle, as the shape functions measure it."""
    return sum((b[1] - b[2]) / b[3] for b in bars.values()) / max(len(bars), 1)


def quiet_band(n=45, base=100.0, band=0.006, t0=T0, step=BAR):
    """A cluster of small candles inside a narrow band.

    The top edge is tagged exactly every fourth bar so the level has real
    touches rather than a single high, which is what `breakout` demands
    before it will call something a level.
    """
    bars = {}
    top, bot = base * (1 + band), base * (1 - band)
    for i in range(n):
        t = t0 + i * step
        if i % 4 == 0:
            h, l = top, base * (1 - band * 0.5)
        elif i % 4 == 2:
            h, l = base * (1 + band * 0.4), bot
        else:
            h, l = base * (1 + band * 0.5), base * (1 - band * 0.4)
        mid, half = (h + l) / 2, (h - l) / 2
        bars[t] = (mid - half * 0.1, h, l, mid + half * 0.1)
    return bars


def band_then_break(n=45, base=100.0, band=0.006, thrust=3.5, up=True,
                    t0=T0, step=BAR):
    """The first shape: a quiet band, then one candle straight out of it.

    `thrust` is the breaking candle's range as a multiple of the band's
    ordinary candle -- the single measure the confidence function weighs
    heaviest. Returns (bars, signal_bar_time).
    """
    bars = quiet_band(n, base, band, t0, step)
    typ = _typ_range(bars)
    top = max(b[1] for b in bars.values())
    bot = min(b[2] for b in bars.values())
    t = t0 + n * step
    rng = typ * thrust * (top if up else bot)
    if up:
        o = top * 1.0005
        c = o + rng * 0.9
        h, l = c + rng * 0.05, o - rng * 0.05
    else:
        o = bot * 0.9995
        c = o - rng * 0.9
        h, l = o + rng * 0.05, c - rng * 0.05
    bars[t] = (o, h, l, c)
    return bars, t


def staircase_trend(n=40, base=100.0, step_pct=0.004, steps=3, wick=0.012,
                    up=False, t0=T0, step=BAR):
    """The second shape: a trend remaking its own level several candles running.

    Each step carries a real shadow on the far side. That is not decoration:
    a perfectly clean monotone staircase is REFUSED by `trend_ride` -- its
    close sits at the very bottom of the run, so `stretch` reaches 1.0 and the
    shape is correctly judged "already too far from the level". A trend the
    engine will actually join is one that keeps being pushed back toward its
    own level, which is what the shadow builds.

    Returns (bars, signal_bar_time).
    """
    bars = {}
    px = base
    # A flat prologue so there is enough history for the look-back window.
    for i in range(n - steps):
        t = t0 + i * step
        bars[t] = (px * 0.9995, px * 1.0015, px * 0.9985, px * 1.0005)
    for j in range(steps):
        t = t0 + (n - steps + j) * step
        nxt = px * (1 + step_pct) if up else px * (1 - step_pct)
        o, c = px, nxt
        if up:
            h, l = c * (1 + wick), o * 0.9996
        else:
            h, l = o * 1.0004, c * (1 - wick)
        bars[t] = (o, h, l, c)
        px = nxt
    return bars, t0 + (n - 1) * step


def walk(start: float, moves, t0=T0, step=BAR):
    """A price path from explicit per-bar percentage moves.

    Used for exit tests: the entry is already decided, and what matters is
    what price does afterwards -- reach the target, reach the stop, gap
    through both, or go nowhere.
    """
    bars, px, t = {}, start, t0
    for m in moves:
        o = px
        c = px * (1 + m / 100.0)
        h, l = max(o, c) * 1.0005, min(o, c) * 0.9995
        bars[t] = (o, h, l, c)
        px, t = c, t + step
    return bars


def spike(start=100.0, n=30, at=20, size=12.0, t0=T0, step=BAR):
    """A flat market with one violent candle -- the gap case."""
    moves = [0.02] * n
    moves[at] = size
    return walk(start, moves, t0, step)
