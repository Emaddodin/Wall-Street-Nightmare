"""Smart Money / ICT read of the chart's candles: state.smc, drawn by the page's SMC/ICT layer.

Closed candles only, so nothing moves once drawn. All times are on the candle clock.

  swings     HH / HL / LH / LL: pivots with 3 candles on each side, named against the previous high (low).
  structure  BOS / CHoCH: a close through the last swing high (low). CHoCH when it turns the trend, BOS when it
             continues it. The line runs from the swing to the candle that closed through it.
  ob         Order block: the lowest candle (highest for a sell) between the broken swing and the break, the
             last down candle before the move up. A close through it ends it and makes it a breaker block (BB)
             the other way.
  fvg        Fair value gap: candle 1's high below candle 3's low (bullish) or above it (bearish), at least
             0.1 ATR. The open part shrinks as price trades into it. A filled gap ends; one that price then
             closes through becomes an inversion gap (IFVG) the other way.
  liquidity  EQH / EQL: swing highs (lows) within 0.1 ATR of each other, not traded through between them.
             BSL / SSL: the nearest untaken swing highs above price and lows below. Swept once price trades
             through; swept ones stay a while, marked.
  pd         Dealing range: the last swing high and low, stretched by any newer extreme. Above its 50% line is
             premium, below it discount.
  ote        Optimal trade entry: 62-79% back into the leg that made the last BOS / CHoCH, until price closes
             beyond the leg's start.
  killzones  Asia 20:00-00:00, London 02:00-05:00, NY AM 07:00-10:00, NY Lunch 12:00-13:00 and NY PM
             13:30-16:00 New York time, today and yesterday, each with its high and low.
  levels     PDH / PDL: the previous New York trading day (17:00 to 17:00). PWH / PWL: the previous week.
             NMO: the New York midnight open.
"""
from __future__ import annotations

from engine import ny7_offset

PIVOT = 3              # candles on each side of a swing point
FVG_ATR = 0.1          # smallest gap worth drawing
EQ_ATR = 0.1           # highs (lows) this close count as equal
OTE = (0.62, 0.79)
RECENT = 30            # ended items stay on the chart, faded, for this many candles
KEEP = 3               # newest items kept per side and kind
KILLZONES = (("Asia", -240, 0), ("London", 120, 300), ("NY AM", 420, 600), ("NY Lunch", 720, 780),
             ("NY PM", 810, 960))          # minutes from New York midnight (Asia starts the evening before)
DAY = 86400


def _ny(utc: int) -> int:
    """UTC -> New York local clock (New York is server time minus 7 hours on NY+7 brokers)."""
    return utc + ny7_offset(utc) - 7 * 3600


def _atr(b, n: int = 14) -> list:
    out = []
    for i in range(len(b)):
        tr = b.h[i] - b.l[i] if i == 0 else max(b.h[i] - b.l[i], abs(b.h[i] - b.c[i - 1]), abs(b.l[i] - b.c[i - 1]))
        out.append(tr if i == 0 else (out[-1] * (n - 1) + tr) / n)
    return out


def _swings(b, n: int) -> list:
    """Pivot highs (+1) and lows (-1) among the first n candles, each known PIVOT candles after it."""
    out = []
    for i in range(PIVOT, n - PIVOT):
        if b.h[i] > max(b.h[i - PIVOT:i]) and b.h[i] >= max(b.h[i + 1:i + PIVOT + 1]):
            out.append((i, b.h[i], 1))
        if b.l[i] < min(b.l[i - PIVOT:i]) and b.l[i] <= min(b.l[i + 1:i + PIVOT + 1]):
            out.append((i, b.l[i], -1))
    return out


def _newest(items: list, keep: int = KEEP) -> list:
    """The newest `keep` per side."""
    out = []
    for d in (1, -1):
        out += [x for x in items if x["dir"] == d][-keep:]
    return out


def analyze(b, h1=None, off=lambda t: 0) -> dict | None:
    """b: the chart's candles, last one still forming. h1: hourly candles for the day and week levels.
    off(t): candle clock minus UTC, in seconds."""
    n = len(b) - 1                                   # closed candles
    if n < 4 * PIVOT + 3:
        return None
    t, o, h, l, c = b.t, b.o, b.h, b.l, b.c
    atr = _atr(b)
    sw = _swings(b, n)
    known = {}                                       # candle index -> swings known once it has closed
    for s in sw:
        known.setdefault(s[0] + PIVOT, []).append(s)

    last = {1: None, -1: None}                       # newest known swing high / low and whether it's broken
    trend, structure, obs, gaps, legs = 0, [], [], [], []
    live_obs, live_gaps = [], []                     # the ones still being watched
    for j in range(n):
        keep = []
        for z in live_obs:                           # order blocks and breakers end on a close through them
            if (c[j] < z["bottom"]) if z["dir"] == 1 else (c[j] > z["top"]):
                z["to_time"] = t[j]
                if z["kind"] == "OB":
                    bb = {"dir": -z["dir"], "kind": "BB", "top": z["top"], "bottom": z["bottom"],
                          "from_time": t[j], "to_time": None}
                    obs.append(bb)
                    keep.append(bb)
            else:
                keep.append(z)
        live_obs, keep = keep, []
        for g in live_gaps:                          # gaps shrink as price trades in and end when filled
            d = g["dir"]
            if g["kind"] == "IFVG":
                if (c[j] > g["top"]) if d == -1 else (c[j] < g["bottom"]):
                    g["to_time"] = t[j]
                    continue
            elif g["to_time"] is None:
                if d == 1:
                    g["top"] = min(g["top"], l[j])
                    filled = l[j] <= g["bottom"]
                else:
                    g["bottom"] = max(g["bottom"], h[j])
                    filled = h[j] >= g["top"]
                if filled:
                    g["to_time"], g["top"], g["bottom"], g["filled"] = t[j], g["hi"], g["lo"], j
            if g["kind"] == "FVG" and g["to_time"] is not None:
                if (c[j] < g["lo"]) if d == 1 else (c[j] > g["hi"]):    # closed through it: inversion gap
                    ifvg = {"dir": -d, "kind": "IFVG", "top": g["hi"], "bottom": g["lo"], "from_time": t[j],
                            "to_time": None}
                    gaps.append(ifvg)
                    keep.append(ifvg)
                    continue
                if j - g["filled"] > RECENT:
                    continue
            keep.append(g)
        live_gaps = keep
        if j >= 2:                                   # a new gap from candles j-2, j-1, j
            for d, lo, hi in ((1, h[j - 2], l[j]), (-1, h[j], l[j - 2])):
                if hi - lo >= FVG_ATR * atr[j]:
                    g = {"dir": d, "kind": "FVG", "top": hi, "bottom": lo, "hi": hi, "lo": lo,
                         "from_time": t[j - 1], "to_time": None}
                    gaps.append(g)
                    live_gaps.append(g)
        for d in (1, -1):                            # structure: a close through the last swing
            s = last[d]
            if s and not s[2] and (c[j] - s[1]) * d > 0:
                last[d] = (s[0], s[1], True)
                structure.append({"kind": "CHoCH" if trend == -d else "BOS", "dir": d, "price": s[1],
                                  "from_time": t[s[0]], "time": t[j]})
                trend = d
                rng = range(s[0], j)
                k = min(rng, key=lambda x: l[x]) if d == 1 else max(rng, key=lambda x: h[x])
                ob = {"dir": d, "kind": "OB", "top": h[k], "bottom": l[k], "from_time": t[k], "to_time": None}
                obs.append(ob)
                live_obs.append(ob)
                legs.append((d, k, j))
        for s in known.get(j, []):
            last[s[2]] = (s[0], s[1], False)

    px, end = c[n - 1], n - RECENT
    fresh = lambda x: x["to_time"] is None or x["to_time"] >= t[max(0, end)]
    clean = lambda x: {k: v for k, v in x.items() if k not in ("hi", "lo", "filled")}

    # swings, named against the previous one of the same kind
    names, prev = [], {1: None, -1: None}
    for i, p, d in sw:
        if prev[d] is not None:
            names.append({"time": t[i], "price": p,
                          "kind": ("HH" if p > prev[d] else "LH") if d == 1 else ("HL" if p > prev[d] else "LL")})
        prev[d] = p

    # liquidity: equal highs / lows, then the nearest untaken swing levels
    def taken(i, p, d):
        for m in range(i + 1, n):
            if (h[m] - p if d == 1 else p - l[m]) > 0:
                return t[m]
        return None
    pools, used = [], set()
    for d in (1, -1):
        pts = [s for s in sw if s[2] == d][-40:]
        for a in range(len(pts)):
            for z in range(a + 1, len(pts)):
                (ia, pa, _), (iz, pz, _) = pts[a], pts[z]
                lvl = max(pa, pz) if d == 1 else min(pa, pz)
                between = h[ia + 1:iz] if d == 1 else l[ia + 1:iz]
                if abs(pa - pz) > EQ_ATR * atr[iz] or (between and (max(between) > lvl if d == 1 else min(between) < lvl)):
                    continue
                if any(q["dir"] == d and abs(q["price"] - lvl) <= EQ_ATR * atr[iz] for q in pools):
                    continue
                sweep = taken(iz, lvl, d)
                pools.append({"dir": d, "kind": "EQH" if d == 1 else "EQL", "price": lvl, "from_time": t[ia],
                              "to_time": sweep, "swept": sweep is not None})
                used.update((ia, iz))
    for d in (1, -1):
        side = [s for s in sw if s[2] == d and s[0] not in used]
        untaken = [s for s in side if taken(s[0], s[1], d) is None and (s[1] - px) * d > 0]
        untaken.sort(key=lambda s: abs(s[1] - px))
        for i, p, _ in untaken[:2]:
            pools.append({"dir": d, "kind": "BSL" if d == 1 else "SSL", "price": p, "from_time": t[i],
                          "to_time": None, "swept": False})
        for i, p, _ in side[-6:]:                    # levels taken lately: where the sweeps happened
            sweep = taken(i, p, d)
            if sweep is not None and sweep >= t[max(0, end)]:
                pools.append({"dir": d, "kind": "BSL" if d == 1 else "SSL", "price": p, "from_time": t[i],
                              "to_time": sweep, "swept": True})
    live = sorted((q for q in pools if not q["swept"]), key=lambda q: abs(q["price"] - px))
    liquidity = live[:6] + [q for q in pools if q["swept"] and fresh(q)][-4:]     # nearest pools, latest sweeps

    # premium / discount from the last swing high and low
    pd = None
    hi = next((s for s in reversed(sw) if s[2] == 1), None)
    lo = next((s for s in reversed(sw) if s[2] == -1), None)
    if hi and lo:
        top, bot = max(hi[1], max(h[hi[0]:n])), min(lo[1], min(l[lo[0]:n]))
        pd = {"high": top, "low": bot, "eq": (top + bot) / 2, "from_time": t[min(hi[0], lo[0])],
              "zone": "premium" if px > (top + bot) / 2 else "discount"}

    # OTE in the leg that made the last break
    ote = []
    if legs:
        d, k, j = legs[-1]
        start = l[k] if d == 1 else h[k]
        x = max(range(j, n), key=lambda m: h[m]) if d == 1 else min(range(j, n), key=lambda m: l[m])
        tip = h[x] if d == 1 else l[x]
        span = (tip - start) * d
        if span >= atr[n - 1] and all((c[m] - start) * d > 0 for m in range(j, n)):
            a, z = tip - d * OTE[0] * span, tip - d * OTE[1] * span
            ote.append({"dir": d, "top": max(a, z), "bottom": min(a, z), "from_time": t[x]})

    # killzones today and yesterday (New York time), each with its range so far
    utc_last = t[n - 1] - off(t[n - 1])
    ny_mid = _ny(utc_last) - _ny(utc_last) % DAY
    to_utc = lambda ny: ny - (ny7_offset(ny + 5 * 3600) - 7 * 3600)
    to_candle = lambda u: u + off(u + off(u))
    killzones = []
    for day in (ny_mid - DAY, ny_mid):
        for name, a, z in KILLZONES:
            s0, s1 = to_candle(to_utc(day + a * 60)), to_candle(to_utc(day + z * 60))
            if s0 > t[n - 1]:
                continue
            inside = [m for m in range(n) if s0 <= t[m] < s1]
            if inside:
                killzones.append({"name": name, "start": s0, "end": s1,
                                  "high": max(h[m] for m in inside), "low": min(l[m] for m in inside)})

    # day and week levels from hourly candles (or the chart's own when that's all there is)
    levels = []
    hb = h1 if h1 is not None and len(h1) else b
    tday = lambda x: (_ny(x - off(x)) - 17 * 3600) // DAY                  # trading day: from 17:00 New York
    week = lambda d: (d + 4) // 7                                          # trading week: from Sunday 17:00
    td = [tday(x) for x in hb.t]
    today = tday(t[-1])
    prev_day = max((d for d in td if d < today), default=None)
    prev_week = max((week(d) for d in td if week(d) < week(today)), default=None)
    day_start = next((x for x, d in zip(hb.t, td) if d == today), t[-1])
    week_start = next((x for x, d in zip(hb.t, td) if week(d) == week(today)), t[-1])
    for want, name, of, ref in ((prev_day, "PD", lambda d: d, day_start), (prev_week, "PW", week, week_start)):
        ms = [m for m, d in enumerate(td) if want is not None and of(d) == want]
        if ms:
            levels += [{"price": max(hb.h[m] for m in ms), "label": name + "H", "time": ref},
                       {"price": min(hb.l[m] for m in ms), "label": name + "L", "time": ref}]
    mid = to_candle(to_utc(ny_mid))
    first = next((m for m in range(n) if t[m] >= mid), None)
    if first is not None and t[first] < mid + 3600:
        levels.append({"price": o[first], "label": "NMO", "time": t[first]})

    bos = structure[-1] if structure else None
    summary = " · ".join(x for x in (
        f"Structure {'bullish' if trend == 1 else 'bearish'} ({bos['kind']} {'up' if bos['dir'] == 1 else 'down'})"
        if bos else None,
        f"price in {pd['zone']}" if pd else None,
        next((f"{k['name']} killzone" for k in killzones if k["start"] <= t[n - 1] < k["end"]), None)) if x)
    return {
        "bias": trend, "summary": summary,
        "swings": names[-10:],
        "structure": structure[-6:],
        "ob": [clean(z) for z in _newest([z for z in obs if fresh(z)])],
        "fvg": [clean(g) for g in _newest([g for g in gaps if fresh(g)], 4)],
        "liquidity": liquidity,
        "pd": pd, "ote": ote, "killzones": killzones, "levels": levels,
    }
