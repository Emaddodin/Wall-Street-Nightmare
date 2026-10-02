"""Timeframe desks: each timeframe reads its own ICT concepts with its own thinking model and makes its own call.

ICT reads the timeframes top-down, and each one has a different job:

  D1   Bias               where the day is heading: structure, premium / discount of the month, daily gaps
  H4   Narrative          the swing in play: structure, EMA regime, H4 gaps, sweeps of H4 swings
  H1   Draw on liquidity  which pool price is going for: nearest untaken highs / lows, H1 structure and gaps
  M15  Setup              the setup forming: turtle soup of M15 swings, M15 gaps, change in state of delivery
  M5   Confirmation       the shift that confirms it: CISD, M5 structure and gaps
  M1   Trigger            the entry candle: CISD, M1 sweep, gap and push

Every desk scores its own concepts with its own weights (-1 bearish .. +1 bullish), says which concepts are active
and why, how far ahead it looks (a few candles of its own timeframe), where it leans by then, and the levels to
draw on its chart: nearest liquidity above and below, the open gap nearest price, and the range's equilibrium.
It also says whether it agrees with the desk above it, ICT's rule for taking a lower timeframe's signal.

The weights are the doctrine written down, not fitted. Each desk is scored live in the knowledge mesh (mesh.py) as
"Desk M5" and so on, and mesh_report.py --history checks them on a year of gold. Nothing here is proven.
"""
from __future__ import annotations

from ictmodel import _cisd, _clip, _fvgs, _pools, _regime, _turtle

ORDER = ("D1", "H4", "H1", "M15", "M5", "M1")
DESKS = {   # role, look-ahead (minutes), range candles for premium / discount, weights by concept
    "D1": ("Bias", 5 * 1440, 20, {"structure": 3, "momentum": 1, "discount": 1, "draw": 1, "gap": 1}),
    "H4": ("Narrative", 2 * 1440, 30, {"structure": 3, "regime": 2, "gap": 1, "sweep": 1, "draw": 1}),
    "H1": ("Draw on liquidity", 12 * 60, 48, {"structure": 2, "regime": 1, "draw": 2, "discount": 1, "gap": 1,
                                              "sweep": 1}),
    "M15": ("Setup", 3 * 60, 32, {"sweep": 2, "gap": 2, "structure": 1, "draw": 1, "cisd": 1}),
    "M5": ("Confirmation", 60, 36, {"cisd": 2, "structure": 2, "gap": 1, "momentum": 1}),
    "M1": ("Trigger", 15, 30, {"cisd": 2, "sweep": 1, "structure": 1, "gap": 1, "momentum": 1}),
}
LEAN_ATR = 2.0                  # a full-strength desk leans this many of its own ATRs by its look-ahead
WORDS = {
    "structure": ("structure bullish (higher highs / lows)", "structure bearish (lower highs / lows)"),
    "regime": ("above the 50 / 200 EMAs", "below the 50 / 200 EMAs"),
    "momentum": ("pushing up", "pushing down"),
    "sweep": ("turtle soup: swept a low and closed back", "turtle soup: swept a high and closed back"),
    "cisd": ("CISD up: closed through the down run's open", "CISD down: closed through the up run's open"),
    "gap": ("holding above a bullish fair value gap", "holding below a bearish fair value gap"),
    "discount": ("in discount (lower half of the range)", "in premium (upper half of the range)"),
    "draw": ("liquidity above is closer: drawn up", "liquidity below is closer: drawn down"),
}


def _series(read, name: str) -> dict:
    """Concept series of one timeframe, computed once per MarketRead."""
    cache = read.__dict__.setdefault("_desk_series", {})
    if name not in cache:
        fr = read.f[name]
        cache[name] = {"turtle": _turtle(fr.b, fr.atr), "cisd": _cisd(fr.b), "pools": _pools(fr.b),
                       "gaps": _fvgs(fr.b, fr.atr), "regime": _regime(fr.b) if len(fr.b) >= 60 else None}
    return cache[name]


def concepts(read, name: str, t: int, price: float) -> tuple | None:
    """(values -1..+1 by concept, levels) of timeframe `name` at time t, only candles closed by t."""
    fr = read.f.get(name)
    if not fr:
        return None
    j = fr.at(t)
    if j < 5:
        return None
    s = _series(read, name)
    a = fr.atr[j] or 1.0
    b = fr.b
    v = {"structure": float(fr.st[j]), "momentum": _clip(fr.mom[j] / 2, 1.0),
         "regime": float(s["regime"][j]) if s["regime"] else 0.0,
         "sweep": float(s["turtle"][j]), "cisd": float(s["cisd"][j])}
    n = DESKS[name][2]
    lo_i = max(0, j - n + 1)
    hi, lo = max(b.h[lo_i:j + 1]), min(b.l[lo_i:j + 1])
    eq = (hi + lo) / 2
    v["discount"] = _clip(-(price - eq) / ((hi - lo) / 2), 1.0) if hi > lo else 0.0
    up, dn = s["pools"][0][j], s["pools"][1][j]
    du = (up - price) / a if up is not None else 10.0
    dd = (price - dn) / a if dn is not None else 10.0
    v["draw"] = _clip((dd - du) / max(dd + du, 1e-9), 1.0)
    gap, g = None, 0.0
    for d, top, bot in reversed(s["gaps"][j]):
        if d == 1 and bot <= price and price - top <= 2 * a:
            gap, g = (d, top, bot), 1.0
            break
        if d == -1 and top >= price and bot - price <= 2 * a:
            gap, g = (d, top, bot), -1.0
            break
    v["gap"] = g
    levels = {"liquidity_above": up, "liquidity_below": dn, "range_high": hi, "range_low": lo, "equilibrium": eq,
              "gap": {"dir": gap[0], "top": gap[1], "bottom": gap[2], "ce": (gap[1] + gap[2]) / 2} if gap else None,
              "atr": a}
    return v, levels


def desk(read, name: str, t: int, price: float) -> dict | None:
    got = concepts(read, name, t, price)
    if not got:
        return None
    v, levels = got
    role, ahead, _, w = DESKS[name]
    score = sum(w[k] * v.get(k, 0.0) for k in w) / sum(w.values())
    why = []
    for k in sorted(w, key=lambda k: -w[k] * abs(v.get(k, 0.0))):
        x = v.get(k, 0.0)
        if abs(x) >= 0.25:
            why.append({"concept": k, "weight": w[k], "value": round(x, 2), "dir": 1 if x > 0 else -1,
                        "text": WORDS[k][0 if x > 0 else 1]})
    bias = 1 if score >= 0.3 else (-1 if score <= -0.3 else 0)
    target = price + score * LEAN_ATR * levels["atr"]
    return {"tf": name, "role": role, "score": round(score, 3), "bias": bias,
            "label": {1: "bullish", -1: "bearish", 0: "mixed"}[bias], "concepts": {k: round(v.get(k, 0.0), 2) for k in w},
            "weights": w, "why": why, "minutes": ahead, "t": t, "price": price, "target": round(target, 2),
            "path": [{"time": t, "value": round(price, 2)}, {"time": t + ahead * 60, "value": round(target, 2)}],
            "levels": {k: (round(x, 2) if isinstance(x, float) else x) for k, x in levels.items()},
            "proven": False}


def desks(read, t: int, price: float) -> dict:
    """Every timeframe's desk, top-down; each says whether it sides with the desk above it."""
    out, above = {}, None
    for name in ORDER:
        d = desk(read, name, t, price)
        if not d:
            continue
        if above:
            d["above"] = above["tf"]
            d["with_above"] = (d["bias"] == above["bias"]) if d["bias"] and above["bias"] else None
        out[name] = d
        above = d
    return out
