"""SMT divergence between gold and silver (or, inverted, the dollar index), the ICT way.

Gold and silver move together, so a swing high or low should be made by both. When they disagree at a swing,
one of them is a liquidity grab:

  Bullish SMT  gold sweeps its prior swing low (a lower low) while silver's corresponding low holds (a higher
               low): silver is the relative-strength leader and gold's break is the trap. The mirror, silver
               makes the lower low while gold holds its low, is bullish for gold too (gold held = strength).
  Bearish SMT  gold makes a higher high while silver makes a lower high (or silver the higher high while gold
               makes a lower high).
  Inverse      against the dollar index (inverse=True): gold's lows go with the dollar's highs. Gold makes a
               lower low while the dollar fails to make the corresponding higher high -> bullish for gold; the
               dollar makes a higher high while gold holds its low -> bullish too; and the mirrors -> bearish.

It is confluence at a liquidity sweep (best at a higher-timeframe key level) and a reversal confirmation on
M1 / M5 / M15, not a signal on its own. It means nothing while the two markets stop moving together, so the
reading carries their correlation.

How it is read, with no repaint:
  - swings are `pivot`-candle pivots on CLOSED candles (a low lower than the `pivot` candles before it and
    not above the `pivot` candles after it), known `pivot` candles after the swing;
  - for two consecutive swing lows (or highs) of one market, the other market's extreme is taken in a window
    of +-`pivot` candles around each swing time, all closed by the time the second swing is known;
  - an event is final once printed: events from fewer candles are exactly the events from more candles that
    were known by then (tests/test_smt.py checks it).
`live_smt` is the separate intrabar check an M1 scalper needs at the moment of the sweep: gold trading beyond
its last unswept swing in the last few candles (the forming one included) while the other market holds.

Both series must be on the same (gold) candle clock; `align` keeps the candle times both have.
"""
from __future__ import annotations

import bisect
import math
import threading
import time

from engine import Bars

TFS = ("M1", "M5", "M15", "H1")
WEIGHT = {"M1": 1.0, "M5": 2.0, "M15": 3.0, "H1": 4.0}
MIN_CORR = 0.3


# ------------------------------------------------------------------ alignment
def _get(xs: list, i: int, default: float = 0.0) -> float:
    return xs[i] if i < len(xs) else default


def align(gold: Bars, other: Bars) -> tuple:
    """(gold_aligned, other_aligned): the candles whose open time both series have, oldest first."""
    idx = {t: j for j, t in enumerate(other.t)}
    g, o = Bars(gold.sec), Bars(gold.sec)
    for i, t in enumerate(gold.t):
        j = idx.get(t)
        if j is None:
            continue
        g.append(t, gold.o[i], gold.h[i], gold.l[i], gold.c[i], _get(gold.v, i), _get(gold.spread, i))
        o.append(t, other.o[j], other.h[j], other.l[j], other.c[j], _get(other.v, j), _get(other.spread, j))
    return g, o


def _aligned(gold: Bars, other: Bars) -> tuple:
    return (gold, other) if gold.t == other.t else align(gold, other)


# ------------------------------------------------------------------ swings
def _pivot_lows(x: list, n: int, p: int) -> list:
    """Indexes i of pivot lows of x among the first n values: below the p before, not above the p after."""
    return [i for i in range(p, n - p) if x[i] < min(x[i - p:i]) and x[i] <= min(x[i + 1:i + p + 1])]


def _sides(gold: Bars, other: Bars, inverse: bool) -> dict:
    """Per side, (gold array, other array, sign): arrays where LOWER means "beyond" (a lower low / a higher
    high, negated); sign turns an array value back into a price. Inverse: gold's lows pair with the
    other's highs."""
    if inverse:
        return {"low": ([x for x in gold.l], [-x for x in other.h], (1, -1)),
                "high": ([-x for x in gold.h], [x for x in other.l], (-1, 1))}
    return {"low": ([x for x in gold.l], [x for x in other.l], (1, 1)),
            "high": ([-x for x in gold.h], [-x for x in other.h], (-1, -1))}


def _broke(new: float, old: float, tol: float) -> bool:
    """`new` went beyond `old` (lower, in these arrays) by more than tol (a fraction of the price)."""
    return new < old - tol * abs(old)


def _win(x: list, i: int, p: int, n: int) -> float:
    return min(x[max(0, i - p):min(n, i + p + 1)])


def _kind(side: str) -> tuple:
    return (1, "bullish") if side == "low" else (-1, "bearish")


def _closed(n: int, last_forming: bool) -> int:
    return n - 1 if last_forming and n else n


# ------------------------------------------------------------------ confirmed SMT
def divergences(gold: Bars, other: Bars, pivot: int = 3, lookback: int | None = 120, inverse: bool = False,
                tol: float = 0.0, last_forming: bool = True, name: str = "silver") -> list:
    """Confirmed SMT events, oldest first, from closed candles only.

    gold / other: candles on the gold candle clock (aligned here when their times differ). last_forming: the
    last candle is still forming and is ignored. lookback: only swings inside the last `lookback` closed
    candles (None: all). tol: how far beyond a swing (fraction of price, e.g. 0.0001) counts as a break;
    0 = any amount. Each event:
      {"dir": +1 bullish / -1 bearish, "kind": "bullish"|"bearish", "side": "low"|"high",
       "time": open time of the candle whose close made it known (second swing + pivot),
       "a_time", "b_time": the two swing times, "gold": [price_a, price_b], "other": [price_a, price_b],
       "leader": the market that held (relative strength), "swept": gold broke its earlier swing (gold is
       the trap side), "by": "gold"|"other" (whose swings), "name": other's name, "inverse": bool}
    """
    g, o = _aligned(gold, other)
    n = _closed(len(g), last_forming)
    p = max(1, int(pivot))
    if n < 2 * p + 2:
        return []
    start = 0 if lookback is None else max(0, n - int(lookback))
    out = []
    for side, (ga, oa, (gs, os_)) in _sides(g, o, inverse).items():
        d, kind = _kind(side)
        for by, x, y in (("gold", ga, oa), ("other", oa, ga)):
            sw = [i for i in _pivot_lows(x, n, p) if i >= start]
            for a, b in zip(sw, sw[1:]):
                ya, yb = _win(y, a, p, n), _win(y, b, p, n)
                if not _broke(x[b], x[a], tol) or _broke(yb, ya, tol):
                    continue
                if by == "gold":
                    gp, op = [x[a] * gs, x[b] * gs], [ya * os_, yb * os_]
                else:
                    gp, op = [ya * gs, yb * gs], [x[a] * os_, x[b] * os_]
                out.append({"dir": d, "kind": kind, "side": side, "time": g.t[b + p], "a_time": g.t[a],
                            "b_time": g.t[b], "gold": gp, "other": op,
                            "leader": name if by == "gold" else "gold", "swept": by == "gold", "by": by,
                            "name": name, "inverse": bool(inverse)})
    out.sort(key=lambda e: (e["time"], e["b_time"], -e["dir"], e["by"]))
    return out


# ------------------------------------------------------------------ forming (intrabar) SMT
def live_smt(gold: Bars, other: Bars, pivot: int = 3, k: int = 5, lookback: int | None = 120,
             inverse: bool = False, tol: float = 0.0, last_forming: bool = True, name: str = "silver") -> dict | None:
    """SMT happening now: in the last k candles (the forming one included) one market trades beyond its most
    recent swing that was still untaken before them, while the other market's corresponding extreme (its
    extreme within +-pivot candles of that swing) has held ever since. The newest such break, else None.
    Same fields as a confirmed event plus "state": "forming"; "time" is the latest candle, "b_time" the candle
    that went beyond, and the prices are [swing level, extreme so far]."""
    g, o = _aligned(gold, other)
    n = len(g)
    nc = _closed(n, last_forming)
    p, k = max(1, int(pivot)), max(1, int(k))
    if nc < 2 * p + 2 or n < 2:
        return None
    ws = max(0, n - k)
    start = 0 if lookback is None else max(0, nc - int(lookback))
    best = None
    for side, (ga, oa, (gs, os_)) in _sides(g, o, inverse).items():
        d, kind = _kind(side)
        for by, x, y in (("gold", ga, oa), ("other", oa, ga)):
            for a in reversed(_pivot_lows(x, nc, p)):
                if a < start:
                    break
                s = max(ws, a + p + 1)                         # breaks only after the swing was known
                if s >= n:
                    continue
                if a + 1 < s and _broke(min(x[a + 1:s]), x[a], tol):
                    continue                                   # taken before the window: not this sweep
                j = min(range(s, n), key=lambda i: x[i])
                if not _broke(x[j], x[a], tol):
                    continue
                ya = _win(y, a, p, nc)
                ynow = min(y[a + 1:n])
                if _broke(ynow, ya, tol):
                    continue                                   # both took it; an older swing may still diverge
                yw = min(y[s:n])
                xs, ys = (gs, os_) if by == "gold" else (os_, gs)
                xp, yp = [x[a] * xs, x[j] * xs], [ya * ys, yw * ys]
                e = {"dir": d, "kind": kind, "side": side, "state": "forming", "time": g.t[n - 1],
                     "a_time": g.t[a], "b_time": g.t[j], "gold": xp if by == "gold" else yp,
                     "other": yp if by == "gold" else xp, "leader": name if by == "gold" else "gold",
                     "swept": by == "gold", "by": by, "name": name, "inverse": bool(inverse)}
                if best is None or (j, by == "gold") > best[0]:
                    best = ((j, by == "gold"), e)
                break                                          # the newest untaken swing only
    return best[1] if best else None


# ------------------------------------------------------------------ correlation
def correlation(gold: Bars, other: Bars, n: int = 120) -> float | None:
    """Pearson correlation of 1-candle log returns of the closes over the last n aligned candles, or None
    with fewer than 20 returns or a flat series."""
    g, o = _aligned(gold, other)
    gc, oc = g.c[-(n + 1):], o.c[-(n + 1):]
    rg, ro = [], []
    for i in range(1, len(gc)):
        if gc[i - 1] > 0 and gc[i] > 0 and oc[i - 1] > 0 and oc[i] > 0:
            rg.append(math.log(gc[i] / gc[i - 1]))
            ro.append(math.log(oc[i] / oc[i - 1]))
    m = len(rg)
    if m < 20:
        return None
    mg, mo = sum(rg) / m, sum(ro) / m
    sg = sum((a - mg) ** 2 for a in rg)
    so = sum((b - mo) ** 2 for b in ro)
    if sg <= 0 or so <= 0:
        return None
    return round(sum((a - mg) * (b - mo) for a, b in zip(rg, ro)) / math.sqrt(sg * so), 3)


# ------------------------------------------------------------------ words
def _px(v: float, gold: bool) -> str:
    return f"{v:.2f}" if gold else (f"{v:.3f}" if abs(v) < 1000 else f"{v:.2f}")


def describe(tf: str, e: dict) -> str:
    """One plain-English line for an event (confirmed or forming)."""
    name, inv, low, now = e.get("name", "silver"), e.get("inverse"), e["side"] == "low", e.get("state") == "forming"
    other = "the dollar" if inv and name.lower() in ("dxy", "dollar", "usd", "usdx") else name
    head = f"{tf} {'forming ' if now else ''}{e['kind']} SMT{' vs ' + name if inv else ''}: "
    if e["swept"]:                                   # gold went beyond its swing, the other market didn't
        g = f"gold {'is sweeping' if now else 'swept'} {_px(e['gold'][0], True)} ({'lower low' if low else 'higher high'})"
        if inv:
            o = f"{other} {'has not made' if now else 'failed to make'} the matching {'higher high' if low else 'lower low'}"
        elif low:
            o = f"{other} {'holds' if now else 'held'} its low (higher low)"
        else:
            o = f"{other} {'stays below its high' if now else 'failed to make a higher high'} (lower high)"
        return f"{head}{g} while {o} \u2014 {other} leads"
    if inv:
        o = f"{other} {'is making' if now else 'made'} a {'higher high' if low else 'lower low'}"
    else:
        o = f"{other} {'is sweeping' if now else 'swept'} its {'low (lower low)' if low else 'high (higher high)'}"
    if now:                                          # gold's swing level it is holding
        lvl = _px(e["gold"][0], True)
        g = f"gold holds above {lvl} (higher low)" if low else f"gold stays below {lvl} (lower high)"
    else:                                            # gold's own second swing
        lvl = _px(e["gold"][1], True)
        g = f"gold held a higher low at {lvl}" if low else f"gold made a lower high at {lvl}"
    return f"{head}{o} while {g} \u2014 gold leads"


# ------------------------------------------------------------------ reader for the page
class SMTReader:
    """Per-timeframe SMT reading for the page. update(tf, gold_bars, other_bars) after each poll; summary()
    across timeframes. Bars as the sources give them: oldest first, the last one still forming."""

    def __init__(self, name: str = "silver", inverse: bool = False, pivot: int = 3, lookback: int = 120,
                 recent: int = 15, k: int = 5, tol: float = 0.0, min_bars: int = 40, corr_n: int = 120,
                 min_corr: float = MIN_CORR):
        self.name, self.inverse = name, inverse
        self.pivot, self.lookback, self.recent, self.k, self.tol = pivot, lookback, recent, k, tol
        self.min_bars, self.corr_n, self.min_corr = min_bars, corr_n, min_corr
        self.readings: dict = {}
        self._lock = threading.Lock()

    def _empty(self, tf: str, note: str, n: int = 0) -> dict:
        return {"tf": tf, "name": self.name, "inverse": self.inverse, "state": 0, "latest": None, "forming": None,
                "events": [], "corr": None, "corr_ok": False, "ok": False, "bars": n, "lag": None,
                "note": note, "at": int(time.time())}

    def update(self, tf: str, gold_bars: Bars | None, other_bars: Bars | None, last_forming: bool = True) -> dict:
        try:
            r = self._read(tf, gold_bars, other_bars, last_forming)
        except Exception as ex:                                # a reading never breaks the page
            r = self._empty(tf, f"{tf}: SMT not read ({type(ex).__name__}: {str(ex)[:80]})")
        with self._lock:
            self.readings[tf] = r
        return r

    def _read(self, tf: str, gb: Bars | None, ob: Bars | None, last_forming: bool) -> dict:
        nm = self.name
        if gb is None or not len(gb):
            return self._empty(tf, f"{tf}: no gold candles")
        if ob is None or not len(ob):
            return self._empty(tf, f"{tf}: no {nm} candles")
        keep = self.lookback + 4 * self.pivot + self.k + 2
        span = max(keep, self.corr_n + 1)
        gt = gb.slice_from(max(0, len(gb) - span))
        g, o = align(gt, ob)
        n = len(g)
        if not n:
            return self._empty(tf, f"{tf}: {nm} candles don't line up with gold's (different clocks?)")
        # the last aligned candle counts as forming even when gold has newer ones: a lagging feed's newest
        # candle (Yahoo built up into M5) may still be filling, and reading it as closed could repaint
        forming = last_forming
        lag = sum(1 for t in gt.t if t > g.t[-1])                  # gold candles newer than the other's last
        cover = n / max(1, sum(1 for t in gt.t if t >= g.t[0]))
        corr = correlation(g, o, self.corr_n)
        corr_ok = corr is not None and corr >= self.min_corr
        ok = n >= self.min_bars and cover >= 0.6
        base = {"tf": tf, "name": nm, "inverse": self.inverse, "corr": corr, "corr_ok": corr_ok, "ok": ok,
                "bars": n, "lag": lag, "at": int(time.time())}
        if not ok:
            return {**self._empty(tf, f"{tf}: not enough {nm} candles lined up with gold ({n}, {cover:.0%} "
                                      f"of gold's)", n), **base}
        gs, os_ = g.slice_from(max(0, n - keep)), o.slice_from(max(0, n - keep))
        kw = dict(pivot=self.pivot, lookback=self.lookback, inverse=self.inverse, tol=self.tol,
                  last_forming=forming, name=nm)
        evs = divergences(gs, os_, **kw)
        live = live_smt(gs, os_, k=self.k, **kw)
        nc = _closed(len(gs), forming)
        closed_t = gs.t[:nc]
        for e in evs:
            e["age"] = nc - 1 - bisect.bisect_left(closed_t, e["time"])       # closed candles since known
            e["intact"] = self._intact(gs, e)
            e["note"] = describe(tf, e)
        if live:
            live["note"] = describe(tf, live)
        recent = [e for e in evs if e["age"] < self.recent and e["intact"]]
        latest = recent[-1] if recent else None
        state = latest["dir"] if latest else 0
        if latest:
            note = latest["note"]
        elif live:
            note = live["note"]
        else:
            note = f"{tf}: no SMT in the last {self.recent} candles ({nm} {'mirrors' if self.inverse else 'confirms'} gold)"
        if live and latest and live["note"] != note:
            note += f"; now {live['kind']} forming"
        if lag and lag > 2:
            note += f" [{nm} {lag} candles behind gold]"
        if corr is not None and not corr_ok:
            note += f" [correlation broken: {corr:+.2f}, SMT unreliable]"
        return {**base, "state": state, "latest": latest, "forming": live, "events": evs[-6:], "note": note}

    @staticmethod
    def _intact(g: Bars, e: dict) -> bool:
        """Gold hasn't traded beyond the event's second-swing extreme since (forming candle included)."""
        i = bisect.bisect_right(g.t, e["b_time"])
        level = e["gold"][1]
        if e["side"] == "low":
            return not g.l[i:] or min(g.l[i:]) >= level
        return not g.h[i:] or max(g.h[i:]) <= level

    def summary(self) -> dict:
        """Across timeframes: a weighted vote of the usable readings (enough data, correlation intact),
        H1 counting most. {"name", "inverse", "state": +1/-1/0, "score": -1..+1, "tfs": {tf: {state, ok,
        corr, corr_ok, forming}}, "forming": [events with "tf"], "note"}."""
        with self._lock:
            rs = dict(self.readings)
        tfs, num, den, forming, parts, broken = {}, 0.0, 0.0, [], [], []
        for tf in sorted(rs, key=lambda x: WEIGHT.get(x, 0)):
            r = rs[tf]
            tfs[tf] = {"state": r["state"], "ok": r["ok"], "corr": r["corr"], "corr_ok": r["corr_ok"],
                       "forming": r["forming"]["dir"] if r.get("forming") else 0}
            if r.get("forming"):
                forming.append({**r["forming"], "tf": tf})
            if not r["ok"]:
                continue
            if r["corr"] is not None and not r["corr_ok"]:
                broken.append(f"{tf} {r['corr']:+.2f}")
                continue
            w = WEIGHT.get(tf, 1.0)
            den += w
            num += w * r["state"]
            if r["state"]:
                parts.append(f"{tf} {'bullish' if r['state'] > 0 else 'bearish'}")
        score = round(num / den, 3) if den else 0.0
        state = 1 if score >= 0.25 else (-1 if score <= -0.25 else 0)
        label = f"{self.name.capitalize()} SMT" + (" (inverse)" if self.inverse else "")
        if not den:
            note = f"{label}: not enough data" if not broken else f"{label}: correlation broken ({', '.join(broken)})"
        elif parts:
            word = {1: "bullish", -1: "bearish", 0: "mixed"}[state]
            note = f"{label}: {word} ({', '.join(parts)})"
        else:
            note = f"{label}: none recently"
        if forming:
            note += "; forming: " + ", ".join(f"{f['tf']} {f['kind']}" for f in forming)
        if broken and den:
            note += f"; correlation broken on {', '.join(broken)}"
        return {"name": self.name, "inverse": self.inverse, "state": state, "score": score, "tfs": tfs,
                "forming": forming, "note": note}
