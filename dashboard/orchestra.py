"""BOOM / CRASH, rebuilt on smart money concepts: one trend layer, one setup layer, one trade plan.

Trend (bias)   The higher timeframes vote, each -1 / 0 / +1, on candles that had closed by then:
                 H4 structure   the last close through an H4 swing (BOS / CHoCH) pointed up or down
                 H4 EMA 50/200  price above both, fast above slow (or the mirror)
                 H1 structure   the same structure read on H1
                 Kronos         up_prob >= 0.6 votes up, <= 0.4 votes down (when Kronos is running)
               BOOM needs a score of +2 or more with H1 structure not bearish; CRASH the mirror.

Setup (M5)     The textbook smart money sequence, closed candles only, each step watched in order:
                 1 sweep        a candle trades through a resting swing low (liquidity: a 5-candle pivot
                                from the last 6 hours, not taken yet) and closes back above it
                 2 CHoCH        within 12 candles a candle closes above the last internal swing high (a
                                2-candle pivot) from before the sweep: the change of character
                 3 POI          the displacement leg from the sweep low to the break leaves a fair value gap;
                                the newest one is the point of interest, else the last down candle before the
                                leg (the order block)
                 4 return       within 24 candles price trades back into the POI, without closing under
                                the sweep low first
                 5 confirm      a candle in or just after the POI prints a bullish pattern: engulfing, hammer
                                (pin bar), or a close back above the POI after trading into it
               CRASH is the mirror: a sweep of a swing high, CHoCH down, return to the bearish FVG / OB.

Plan           Entry at the live price after the confirming candle closes. Stop beyond the sweep extreme
               plus 0.1 ATR (at least 0.5 ATR away; no call if it is more than 4 ATR). Target TP_R times the
               risk. The call ends at the target, the stop, or after MAX_MIN minutes. London and New York hours.

Every number here was chosen on Dukascopy gold Oct 2025 - May 2026 and checked on the months after it
(boom_backtest.py). Nothing here places an order.
"""
from __future__ import annotations

from bisect import bisect_right

from engine import Bars, ema

# ---- setup layer
SWEEP_PIVOT = 5        # liquidity: swing points with this many candles each side
SWEEP_AGE = 72         # ... formed within this many candles (6 hours on M5)
INTERNAL_PIVOT = 2     # internal structure for the CHoCH
CHOCH_WITHIN = 12      # candles from the sweep to the CHoCH
RETURN_WITHIN = 24     # candles from the CHoCH to the return into the POI
FVG_MIN_ATR = 0.1
CONFIRM = "pattern"    # "pattern": wait for a candle pattern in the POI; "touch": enter on the touch
# ---- plan
SL_BUFFER_ATR = 0.1
MIN_SL_ATR = 0.5
MAX_SL_ATR = 4.0
TP_R = 2.0
MAX_MIN = 240          # a call lives at most this long
SESSION_UTC = (7 * 60, 17 * 60)   # London open to the New York afternoon, UTC minutes
# ---- trend layer
NEED_SCORE = 2
KRONOS_UP, KRONOS_DOWN = 0.6, 0.4

NAME = {1: "BOOM", -1: "CRASH"}
SIDE = {1: "BUY", -1: "SELL"}


# ------------------------------------------------------------------ trend layer
class Structure:
    """BOS / CHoCH state of one timeframe: +1 after a close above the last swing high, -1 after a close below the
    last swing low (3-candle pivots, known 3 candles later). Series aligned to the candles."""

    def __init__(self, b: Bars, pivot: int = 3):
        self.close_t = [t + b.sec for t in b.t]
        n, L = len(b), pivot
        self.state = []
        hi = lo = None
        st = 0
        for j in range(n):
            c = j - L
            if c - L >= 0:
                if b.h[c] == max(b.h[c - L:c + L + 1]):
                    hi = b.h[c]
                if b.l[c] == min(b.l[c - L:c + L + 1]):
                    lo = b.l[c]
            if hi is not None and b.c[j] > hi:
                st, hi = 1, None
            elif lo is not None and b.c[j] < lo:
                st, lo = -1, None
            self.state.append(st)

    def at(self, t: int) -> int:
        j = bisect_right(self.close_t, t) - 1
        return self.state[j] if j >= 0 else 0


class EmaRegime:
    def __init__(self, b: Bars, fast: int = 50, slow: int = 200):
        self.close_t = [t + b.sec for t in b.t]
        f, s = ema(b.c, fast), ema(b.c, slow)
        self.state = [1 if c > ff > ss else (-1 if c < ff < ss else 0) for c, ff, ss in zip(b.c, f, s)]

    def at(self, t: int) -> int:
        j = bisect_right(self.close_t, t) - 1
        return self.state[j] if j >= 0 else 0


class Trend:
    """The trend layer. h4 / h1: closed candles of those timeframes (more may be appended by rebuilding)."""

    def __init__(self, h4: Bars, h1: Bars, names: tuple = ("H4", "H1")):
        self.h4s, self.h4e, self.h1s = Structure(h4), EmaRegime(h4), Structure(h1)
        self.names = names

    def votes(self, t: int, up_prob: float | None = None) -> dict:
        """Votes at time t (only candles closed by t count). The structure of the lower one is listed last."""
        hi, lo = self.names
        v = {f"{hi} structure": self.h4s.at(t), f"{hi} EMA 50/200": self.h4e.at(t)}
        if up_prob is not None:
            v["Kronos"] = 1 if up_prob >= KRONOS_UP else (-1 if up_prob <= KRONOS_DOWN else 0)
        v[f"{lo} structure"] = self.h1s.at(t)
        return v

    @staticmethod
    def bias(v: dict, need: int = NEED_SCORE) -> int:
        score, low = sum(v.values()), list(v.values())[-1]
        if score >= need and low != -1:
            return 1
        if score <= -need and low != 1:
            return -1
        return 0

    @staticmethod
    def words(v: dict, d: int) -> list:
        up = {1: "up", -1: "down"}
        return [f"{k} {up[d]}" for k, x in v.items() if x == d]


# ------------------------------------------------------------------ setup layer
def pattern(d: int, o: float, h: float, l: float, c: float, po: float, pc: float) -> str | None:
    """A confirming M5 candle for side d, or None."""
    body, rng = abs(c - o), h - l
    if rng <= 0:
        return None
    if d == 1:
        if c > o and pc < po and c >= po and o <= pc:
            return "bullish engulfing"
        if min(o, c) - l >= 2 * body and min(o, c) - l >= 0.5 * rng and c >= l + 0.6 * rng:
            return "hammer"
    else:
        if c < o and pc > po and c <= po and o >= pc:
            return "bearish engulfing"
        if h - max(o, c) >= 2 * body and h - max(o, c) >= 0.5 * rng and c <= h - 0.6 * rng:
            return "shooting star"
    return None


class Setups:
    """Feeds closed entry candles one at a time and reports a finished setup per side. Mirrors for d = -1 by
    flipping prices, so the logic is written once for the buy side."""

    def __init__(self, sec: int = 300, **kw):
        self.sec = sec
        self.cfg = {"confirm": CONFIRM, "tp_r": TP_R, "sweep_pivot": SWEEP_PIVOT, "choch_within": CHOCH_WITHIN,
                    "return_within": RETURN_WITHIN, **kw}
        self.o, self.h, self.l, self.c, self.t, self.atr = [], [], [], [], [], []
        self.liq = {1: [], -1: []}            # resting liquidity per side: [bar, price] (lows for 1, highs for -1)
        self.inner = {1: [], -1: []}          # internal swing highs for 1 (lows for -1): [bar, price]
        self.state = {1: None, -1: None}

    # prices seen from side d: for d = -1 everything is negated, so "low" means high, etc.
    def _px(self, d: int, j: int):
        if d == 1:
            return self.o[j], self.h[j], self.l[j], self.c[j]
        return -self.o[j], -self.l[j], -self.h[j], -self.c[j]

    def add(self, t, o, h, l, c) -> dict:
        """One closed candle. Returns {d: setup} for the sides whose setup completed on it."""
        for arr, x in ((self.t, t), (self.o, o), (self.h, h), (self.l, l), (self.c, c)):
            arr.append(x)
        j = len(self.c) - 1
        tr = h - l if j == 0 else max(h - l, abs(h - self.c[j - 1]), abs(l - self.c[j - 1]))
        self.atr.append(tr if j == 0 else (self.atr[-1] * 13 + tr) / 14)
        self._pivots(j)
        out = {}
        if j < 30:
            return out
        for d in (1, -1):
            s = self._step(d, j)
            if s:
                out[d] = s
        return out

    def _pivots(self, j: int) -> None:
        P = self.cfg["sweep_pivot"]
        k = j - P
        if k - P >= 0:
            if self.l[k] == min(self.l[k - P:k + P + 1]):
                self.liq[1].append([k, self.l[k]])
            if self.h[k] == max(self.h[k - P:k + P + 1]):
                self.liq[-1].append([k, -self.h[k]])
        I = INTERNAL_PIVOT
        k = j - I
        if k - I >= 0:
            if self.h[k] == max(self.h[k - I:k + I + 1]):
                self.inner[1].append([k, self.h[k]])
            if self.l[k] == min(self.l[k - I:k + I + 1]):
                self.inner[-1].append([k, -self.l[k]])
        for d in (1, -1):
            self.liq[d] = [x for x in self.liq[d] if j - x[0] <= SWEEP_AGE][-12:]
            self.inner[d] = self.inner[d][-12:]

    def _step(self, d: int, j: int) -> dict | None:
        o, h, l, c = self._px(d, j)
        st = self.state[d]
        cfg = self.cfg

        # any stage: a close under the sweep extreme kills the idea (the stop would have been hit)
        if st and c < st["ext"]:
            st = self.state[d] = None

        if st and st["stage"] == "sweep":
            st["ext"] = min(st["ext"], l)
            if c > st["choch"]:
                leg = range(st["bar"], j + 1)
                poi = self._poi(d, st, j)
                if poi:
                    st.update(stage="poi", poi=poi, choch_bar=j, top=max(self._px(d, m)[1] for m in leg))
                else:
                    st = self.state[d] = None
            elif j - st["bar"] > cfg["choch_within"]:
                st = self.state[d] = None
        elif st and st["stage"] == "poi":
            st["top"] = max(st["top"], h)
            p_top, p_bot = st["poi"]
            touched = l <= p_top
            if touched:
                st["touched"] = st.get("touched", j)
            if st.get("touched") is not None:
                po, _, _, pc = self._px(d, j - 1)
                why = None
                if cfg["confirm"] == "touch":
                    why = "touch of the POI"
                else:
                    why = pattern(1, o, h, l, c, po, pc)
                    if why and d == -1:
                        why = {"bullish engulfing": "bearish engulfing", "hammer": "shooting star"}[why]
                    if why is None and l <= p_top and c > p_top and c > o:
                        why = "rejection close"
                    if why and j - st["touched"] > 2:
                        why = None
                if why:
                    self.state[d] = None
                    return self._finish(d, st, j, why)
                if j - st["touched"] > 2:            # traded in, no pattern within 3 candles: give up
                    st = self.state[d] = None
            if st and j - st["choch_bar"] > cfg["return_within"]:
                st = self.state[d] = None

        if self.state[d] is None:                    # a new sweep starts a new idea
            for k, lvl in reversed(self.liq[d]):
                if k >= j - 1:
                    continue
                if l < lvl and c > lvl and all(self._px(d, m)[2] >= lvl for m in range(k + 1, j)):
                    inner = [p for b, p in self.inner[d] if b < j and p > c]
                    if inner:
                        self.state[d] = {"stage": "sweep", "bar": j, "level": lvl, "ext": l, "choch": inner[-1]}
                    break
        return None

    def waiting(self) -> list:
        """Setups past their CHoCH, waiting for price to come back to the POI (for heads-ups and the chart)."""
        out = []
        for d in (1, -1):
            st = self.state[d]
            if st and st["stage"] == "poi":
                out.append({"dir": d, "poi": sorted(round(x * d, 2) for x in st["poi"]),
                            "sweep_ext": round(st["ext"] * d, 2), "choch": round(st["choch"] * d, 2), "since": self.t[st["choch_bar"]],
                            "until": self.t[st["choch_bar"]] + (self.cfg["return_within"] + 1) * self.sec})
        return out

    def _poi(self, d: int, st: dict, j: int):
        """The newest fair value gap in the leg from the sweep to the break, else the last down candle before it."""
        a = self.atr[j]
        for m in range(j, st["bar"] + 1, -1):
            _, h1, _, _ = self._px(d, m - 2)
            _, _, l3, _ = self._px(d, m)
            if l3 - h1 >= FVG_MIN_ATR * a:
                return (l3, h1)
        for m in range(j - 1, st["bar"] - 1, -1):
            o_, h_, l_, c_ = self._px(d, m)
            if c_ < o_:
                return (h_, l_)
        return None

    def _finish(self, d: int, st: dict, j: int, why: str) -> dict:
        a = self.atr[j]
        sgn = d
        ext = st["ext"] * sgn                         # back to real prices
        level = st["level"] * sgn
        poi = sorted(x * sgn for x in st["poi"])
        return {"dir": d, "bar": j, "t": self.t[j], "atr": a, "sweep_level": level, "sweep_ext": ext,
                "choch": st["choch"] * sgn, "poi": poi, "leg_top": st["top"] * sgn, "pattern": why,
                "close": self.c[j]}


def plan(s: dict, entry: float, spread: float, tp_r: float = TP_R) -> dict | None:
    """Stop and target for a finished setup at this entry price (buy at the ask, sell at the bid)."""
    d, a = s["dir"], s["atr"]
    sl = s["sweep_ext"] - d * SL_BUFFER_ATR * a + (spread if d == -1 else 0.0)
    risk = (entry - sl) * d
    if risk < MIN_SL_ATR * a:
        sl, risk = entry - d * MIN_SL_ATR * a, MIN_SL_ATR * a
    if risk <= 0 or risk > MAX_SL_ATR * a:
        return None
    return {"sl": sl, "tp": entry + d * tp_r * risk, "risk": risk}


def in_session(utc: int) -> bool:
    m = utc % 86400 // 60
    return SESSION_UTC[0] <= m < SESSION_UTC[1] and (utc // 86400 + 3) % 7 < 5


def describe(s: dict) -> list:
    d = s["dir"]
    lo, hi = s["poi"]
    return [f"swept {'low' if d == 1 else 'high'} {s['sweep_level']:.2f}",
            f"CHoCH {'up' if d == 1 else 'down'} through {s['choch']:.2f}",
            f"back into {lo:.2f}-{hi:.2f}", s["pattern"]]
