"""Candlestick patterns on closed candles: the 33 patterns of the Chart Guys cheat sheet, ported from
scalper/learn/motivewave-candlestick-pattern-study (CandlestickPatterns.java, same rules and thresholds), plus pin
bars, inside / outside bars and three-inside / three-outside.

Gold trades almost around the clock, so a candle nearly always opens at the previous close: patterns that need a
gap (abandoned baby, the star patterns' gaps, kickers) rarely print on M1 / M5. The rules are kept as written; the
gap-free kicker (opening beyond the previous open) is the study's own.

Patterns are hints, not signals. The playbook uses a pattern only as the *confirmation candle* of an ICT entry:
a bullish pattern closing at a bullish zone (FVG, breaker, unicorn overlap, OTE, IFVG ...) after the sweep,
which is how the notes and the video enter ("rejection" from the level, then the close).
Priority as in the study: three-candle patterns over two-candle over one-candle.
"""
from __future__ import annotations

from engine import Bars

NAMES = {   # name -> (bias +1 bullish / -1 bearish / 0 neutral, candles, short meaning)
    "Hammer": (1, 1, "long lower wick: buyers rejected the lows"),
    "Inverted Hammer": (1, 1, "long upper wick after a fall: buyers probing"),
    "Dragonfly Doji": (1, 1, "doji with a long lower wick: lows rejected"),
    "Bullish Marubozu": (1, 1, "full bullish body, no wicks: strong buying"),
    "Shooting Star": (-1, 1, "long upper wick: sellers rejected the highs"),
    "Hanging Man": (-1, 1, "long lower wick after a rise: buying weakening"),
    "Gravestone Doji": (-1, 1, "doji with a long upper wick: highs rejected"),
    "Bearish Marubozu": (-1, 1, "full bearish body, no wicks: strong selling"),
    "Doji": (0, 1, "open = close: indecision"),
    "Long-Legged Doji": (0, 1, "long wicks both ways: indecision"),
    "Spinning Top": (0, 1, "small body, wicks both sides: indecision"),
    "Bullish Pin Bar": (1, 1, "lower wick two thirds of the candle: rejection of the lows"),
    "Bearish Pin Bar": (-1, 1, "upper wick two thirds of the candle: rejection of the highs"),
    "Bullish Engulfing": (1, 2, "bullish body swallows the bearish one"),
    "Bullish Harami": (1, 2, "small bullish body inside a bearish one"),
    "Piercing Line": (1, 2, "bullish close above the middle of the bearish candle"),
    "Tweezer Bottom": (1, 2, "two equal lows, opposite colours: support"),
    "Bullish Kicker": (1, 2, "bullish candle opening above the bearish candle's open"),
    "Bearish Engulfing": (-1, 2, "bearish body swallows the bullish one"),
    "Bearish Harami": (-1, 2, "small bearish body inside a bullish one"),
    "Dark Cloud Cover": (-1, 2, "bearish close below the middle of the bullish candle"),
    "Tweezer Top": (-1, 2, "two equal highs, opposite colours: resistance"),
    "Bearish Kicker": (-1, 2, "bearish candle opening below the bullish candle's open"),
    "Inside Bar": (0, 2, "range inside the previous candle: compression"),
    "Outside Bar": (0, 2, "range engulfs the previous candle: expansion"),
    "Morning Star": (1, 3, "bearish, small star, strong bullish: bottom reversal"),
    "Morning Doji Star": (1, 3, "bearish, doji star, bullish: bottom reversal"),
    "Bullish Abandoned Baby": (1, 3, "doji gapped below both neighbours: bottom reversal"),
    "Three White Soldiers": (1, 3, "three rising bullish closes"),
    "Three Inside Up": (1, 3, "bullish harami confirmed by a higher close"),
    "Three Outside Up": (1, 3, "bullish engulfing confirmed by a higher close"),
    "Evening Star": (-1, 3, "bullish, small star, strong bearish: top reversal"),
    "Evening Doji Star": (-1, 3, "bullish, doji star, bearish: top reversal"),
    "Bearish Abandoned Baby": (-1, 3, "doji gapped above both neighbours: top reversal"),
    "Three Black Crows": (-1, 3, "three falling bearish closes"),
    "Three Inside Down": (-1, 3, "bearish harami confirmed by a lower close"),
    "Three Outside Down": (-1, 3, "bearish engulfing confirmed by a lower close"),
}


def _parts(b: Bars, i: int) -> tuple:
    o, h, l, c = b.o[i], b.h[i], b.l[i], b.c[i]
    return o, h, l, c, abs(c - o), h - max(o, c), min(o, c) - l, h - l


def _single(b: Bars, i: int) -> list:
    o, h, l, c, body, up, lo, rng = _parts(b, i)
    out = []
    if rng <= 0:
        return out
    bull, bear = c > o, c < o
    if body / rng < 0.1:
        if lo > rng * 0.6 and up < rng * 0.1:
            out.append("Dragonfly Doji")
        elif up > rng * 0.6 and lo < rng * 0.1:
            out.append("Gravestone Doji")
        elif up > body * 2 and lo > body * 2 and up > 0.3 * rng and lo > 0.3 * rng:
            out.append("Long-Legged Doji")
        else:
            out.append("Doji")
    elif 0.1 < body / rng < 0.3 and up > body and lo > body:
        out.append("Spinning Top")
    if bull and lo > body * 2 and up < body * 0.5:
        out.append("Hammer")
    if bull and up > body * 2 and lo < body * 0.5:
        out.append("Inverted Hammer")
    if bear and up > body * 2 and lo < body * 0.5:
        out.append("Shooting Star")
    if bear and lo > body * 2 and up < body * 0.5:
        out.append("Hanging Man")
    if bull and body / rng > 0.95:
        out.append("Bullish Marubozu")
    if bear and body / rng > 0.95:
        out.append("Bearish Marubozu")
    if lo >= rng * 2 / 3 and min(o, c) >= l + rng * 2 / 3:
        out.append("Bullish Pin Bar")
    if up >= rng * 2 / 3 and max(o, c) <= h - rng * 2 / 3:
        out.append("Bearish Pin Bar")
    return out


def _double(b: Bars, i: int) -> list:
    if i < 1:
        return []
    o0, h0, l0, c0, body0, _, _, rng0 = _parts(b, i - 1)
    o, h, l, c, body, _, _, rng = _parts(b, i)
    bull0, bear0, bull, bear = c0 > o0, c0 < o0, c > o, c < o
    out = []
    if bear0 and bull and o <= c0 and c >= o0:
        out.append("Bullish Engulfing")
    if bull0 and bear and o >= c0 and c <= o0:
        out.append("Bearish Engulfing")
    if bear0 and bull and body < body0 * 0.5 and o > c0 and c < o0:
        out.append("Bullish Harami")
    if bull0 and bear and body < body0 * 0.5 and o < c0 and c > o0:
        out.append("Bearish Harami")
    mid0 = (o0 + c0) / 2
    if bear0 and bull and o < c0 and mid0 < c < o0:
        out.append("Piercing Line")
    if bull0 and bear and o > c0 and o0 < c < mid0:
        out.append("Dark Cloud Cover")
    avg = (rng + rng0) / 2
    if avg > 0 and bull0 != bull and c0 != o0 and c != o:
        if abs(l - l0) / avg < 0.05:
            out.append("Tweezer Bottom")
        if abs(h - h0) / avg < 0.05:
            out.append("Tweezer Top")
    if bear0 and bull and o > o0:
        out.append("Bullish Kicker")
    if bull0 and bear and o < o0:
        out.append("Bearish Kicker")
    if h < h0 and l > l0:
        out.append("Inside Bar")
    if h > h0 and l < l0:
        out.append("Outside Bar")
    return out


def _triple(b: Bars, i: int) -> list:
    if i < 2:
        return []
    a, m, z = i - 2, i - 1, i
    oa, ha, la, ca, ba, _, _, _ = _parts(b, a)
    om, hm, lm, cm, bm, _, _, rm = _parts(b, m)
    oz, hz, lz, cz, bz, _, _, _ = _parts(b, z)
    bull = lambda o, c: c > o
    bear = lambda o, c: c < o
    out = []
    if bear(oa, ca) and bull(oz, cz):
        if bm < ba * 0.3 and max(om, cm) < ca and bz > ba * 0.5:
            out.append("Morning Star")
        if rm > 0 and bm / rm < 0.1 and hm < ca:
            out.append("Morning Doji Star")
        if rm > 0 and bm / rm < 0.1 and hm < la and hm < lz:
            out.append("Bullish Abandoned Baby")
    if bull(oa, ca) and bear(oz, cz):
        if bm < ba * 0.3 and min(om, cm) > ca and bz > ba * 0.5:
            out.append("Evening Star")
        if rm > 0 and bm / rm < 0.1 and lm > ca:
            out.append("Evening Doji Star")
        if rm > 0 and bm / rm < 0.1 and lm > ha and lm > hz:
            out.append("Bearish Abandoned Baby")
    if bull(oa, ca) and bull(om, cm) and bull(oz, cz) and cm > ca and cz > cm:
        out.append("Three White Soldiers")
    if bear(oa, ca) and bear(om, cm) and bear(oz, cz) and cm < ca and cz < cm:
        out.append("Three Black Crows")
    if bear(oa, ca) and "Bullish Harami" in _double(b, m) and bull(oz, cz) and cz > oa:
        out.append("Three Inside Up")                    # closes above the first (bearish) candle's open
    if bull(oa, ca) and "Bearish Harami" in _double(b, m) and bear(oz, cz) and cz < oa:
        out.append("Three Inside Down")
    if "Bullish Engulfing" in _double(b, m) and bull(oz, cz) and cz > cm:
        out.append("Three Outside Up")
    if "Bearish Engulfing" in _double(b, m) and bear(oz, cz) and cz < cm:
        out.append("Three Outside Down")
    return out


def at(b: Bars, i: int) -> list:
    """Patterns completed by candle i, strongest first: [{"name", "bias", "bars", "meaning", "start"}]."""
    out = []
    for names in (_triple(b, i), _double(b, i), _single(b, i)):
        for nm in names:
            bias, bars, meaning = NAMES[nm]
            out.append({"name": nm, "bias": bias, "bars": bars, "meaning": meaning, "start": i - bars + 1})
    return out


def confirms(b: Bars, i: int, d: int) -> dict | None:
    """The strongest pattern at candle i that confirms direction d (+1 buy), or None. A plain close in the trade's
    direction is not a pattern; the caller decides whether that is enough."""
    for p in at(b, i):
        if p["bias"] == d:
            return p
    return None


def recent(b: Bars, last: int = 20) -> list:
    """Directional patterns of the last `last` closed candles, for the chart: [{"time", "name", "bias", "bars"}]."""
    out = []
    for i in range(max(0, len(b) - last), len(b)):
        for p in at(b, i):
            if p["bias"]:
                out.append({"time": b.t[i], "name": p["name"], "bias": p["bias"], "bars": p["bars"]})
                break
    return out
