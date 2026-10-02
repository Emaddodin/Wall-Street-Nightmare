"""Gold Desk trend model: one up/down probability from every timeframe, ICT / SMC concepts and Kronos.

Each feature is read on candles that had closed by the time asked about, so nothing repaints:

  structure   M1, M5, M15, H1, H4 and D1: +1 after a close above the last swing high (3-candle pivot), -1 after a
              close below the last swing low (BOS / CHoCH / MSS state of that timeframe)
  momentum    each of those timeframes: the last 3 candles' move in ATRs
  regime      H1 and H4: price above EMA 50 above EMA 200 (+1), the mirror (-1)
  premium     where price sits in the H1 dealing range (the last 48 hours' high and low): -1 bottom, +1 top;
              and against the previous New York day's high and low (PDH / PDL)
  turtle      M15 turtle soup: in the last 4 candles a candle traded through a resting swing low and closed back
              above it (+1) or the mirror at a high (-1)
  cisd        M5 change in state of delivery: a body close above the open of the bearish run that made the
              lowest low of the last 20 candles (+1), or the mirror (-1), within the last 6 candles
  judas       London / New York: the Asian range (20:00-02:00 New York) low was swept and price is back above it
              (+1), or the high swept and price back below (-1)
  draw        draw on liquidity: which untaken M15 swing pool is nearer, above (+) or below (-)
  fvg         nearest unfilled M15 fair value gap within 3 ATR: bullish below price (+1) or bearish above (-1)

model_weights.json holds a logistic regression fitted on Dukascopy gold (ict_train.py says on which months
and how it did on the months after). Kronos's up-probability, when it runs, is added on the log-odds scale
with weight KRONOS_W, which was set by hand: there was no year of Kronos forecasts to fit it on.
"""
from __future__ import annotations

import json
import math
from bisect import bisect_right
from pathlib import Path

from engine import Bars, ema
from orchestra import Structure

TFS = (("M1", 60), ("M5", 300), ("M15", 900), ("H1", 3600), ("H4", 14400), ("D1", 86400))
WEIGHTS = Path(__file__).resolve().parent / "model_weights.json"
KRONOS_W = 0.5
DAY = 86400


def ny_clock(utc: int) -> int:
    """UTC -> New York local clock seconds."""
    from smc import _ny
    return _ny(utc)


def _atr(b: Bars, n: int = 14) -> list:
    out = []
    for i in range(len(b)):
        tr = b.h[i] - b.l[i] if i == 0 else max(b.h[i] - b.l[i], abs(b.h[i] - b.c[i - 1]), abs(b.l[i] - b.c[i - 1]))
        out.append(tr if i == 0 else (out[-1] * (n - 1) + tr) / n)
    return out


def _clip(x: float, k: float = 3.0) -> float:
    return max(-k, min(k, x))


def daily(h1: Bars, utc=lambda t: t) -> Bars:
    """D1 candles of the New York trading day (17:00 to 17:00) built from H1 candles."""
    out, keys = Bars(86400), []
    for i in range(len(h1)):
        key = (ny_clock(utc(h1.t[i])) - 17 * 3600) // DAY
        if keys and keys[-1] == key:
            out.h[-1], out.l[-1], out.c[-1] = max(out.h[-1], h1.h[i]), min(out.l[-1], h1.l[i]), h1.c[i]
        else:
            keys.append(key)
            out.append(h1.t[i], h1.o[i], h1.h[i], h1.l[i], h1.c[i])
    return out


class Frame:
    """Per-candle series of one timeframe, aligned by close time."""

    def __init__(self, b: Bars):
        self.b = b
        self.close_t = [t + b.sec for t in b.t]
        self.atr = _atr(b)
        self.st = Structure(b).state
        c = b.c
        self.mom = [0.0] * len(b)
        for j in range(3, len(b)):
            self.mom[j] = _clip((c[j] - c[j - 3]) / self.atr[j]) if self.atr[j] > 0 else 0.0

    def at(self, t: int) -> int:
        """Index of the last candle closed by t."""
        return bisect_right(self.close_t, t) - 1


def _regime(b: Bars) -> list:
    f, s = ema(b.c, 50), ema(b.c, 200)
    return [1 if c > ff > ss else (-1 if c < ff < ss else 0) for c, ff, ss in zip(b.c, f, s)]


def _turtle(b: Bars, atr: list, pivot: int = 3, keep: int = 4) -> list:
    """+1 / -1 while a turtle-soup sweep of a swing low / high happened within the last `keep` candles."""
    n, out = len(b), [0] * len(b)
    lows, highs, last = [], [], {1: -99, -1: -99}
    for j in range(n):
        k = j - pivot
        if k - pivot >= 0:
            if b.l[k] == min(b.l[k - pivot:k + pivot + 1]):
                lows.append(b.l[k])
            if b.h[k] == max(b.h[k - pivot:k + pivot + 1]):
                highs.append(b.h[k])
        swept = [x for x in lows if b.l[j] < x < b.c[j]]
        if swept:
            last[1] = j
        lows = [x for x in lows if b.l[j] >= x][-10:]       # taken levels are gone
        swept = [x for x in highs if b.c[j] < x < b.h[j]]
        if swept:
            last[-1] = j
        highs = [x for x in highs if b.h[j] <= x][-10:]
        a, z = j - last[1] < keep, j - last[-1] < keep
        out[j] = (1 if a else 0) - (1 if z else 0) if a != z else 0
    return out


def _cisd(b: Bars, look: int = 20, keep: int = 6) -> list:
    """Change in state of delivery: body close through the open of the run that made the extreme."""
    n, out = len(b), [0] * len(b)
    ref = {1: None, -1: None}         # (open of the run's first candle, candle index the run ended)
    last = {1: -99, -1: -99}
    run_start = {1: None, -1: None}   # start index of the current down run (for 1) / up run (for -1)
    for j in range(n):
        down, up = b.c[j] < b.o[j], b.c[j] > b.o[j]
        for d, same in ((1, down), (-1, up)):
            if same:
                if run_start[d] is None:
                    run_start[d] = j
            elif run_start[d] is not None:
                s = run_start[d]
                ext = min(b.l[s:j]) if d == 1 else max(b.h[s:j])
                lo = max(0, j - look)
                if (d == 1 and ext <= min(b.l[lo:j])) or (d == -1 and ext >= max(b.h[lo:j])):
                    ref[d] = (b.o[s], j)
                run_start[d] = None
        for d in (1, -1):
            r = ref[d]
            if r and j - r[1] <= 10 and (b.c[j] - r[0]) * d > 0 and (b.c[j] - b.o[j]) * d > 0:
                last[d], ref[d] = j, None
        a, z = j - last[1] < keep, j - last[-1] < keep
        out[j] = (1 if a else 0) - (1 if z else 0) if a != z else 0
    return out


def _pools(b: Bars, pivot: int = 3) -> tuple:
    """Per candle: nearest untaken swing high above and swing low below the close (None when none)."""
    n = len(b)
    up, dn = [None] * n, [None] * n
    highs, lows = [], []
    for j in range(n):
        k = j - pivot
        if k - pivot >= 0:
            if b.h[k] == max(b.h[k - pivot:k + pivot + 1]):
                highs.append(b.h[k])
            if b.l[k] == min(b.l[k - pivot:k + pivot + 1]):
                lows.append(b.l[k])
        highs = [x for x in highs if x > b.h[j]][-30:]
        lows = [x for x in lows if x < b.l[j]][-30:]
        up[j] = min(highs) if highs else None
        dn[j] = max(lows) if lows else None
    return up, dn


def _fvgs(b: Bars, atr: list, min_atr: float = 0.1) -> list:
    """Per candle: the unfilled gaps as (dir, top, bottom), newest last (at most 12)."""
    n, out, live = len(b), [None] * len(b), []
    for j in range(n):
        live = [g for g in live if not ((g[0] == 1 and b.l[j] <= g[2]) or (g[0] == -1 and b.h[j] >= g[1]))]
        if j >= 2:
            if b.l[j] - b.h[j - 2] >= min_atr * atr[j]:
                live.append((1, b.l[j], b.h[j - 2]))
            if b.l[j - 2] - b.h[j] >= min_atr * atr[j]:
                live.append((-1, b.l[j - 2], b.h[j]))
        live = live[-12:]
        out[j] = tuple(live)
    return out


class MarketRead:
    """All timeframes at once. bars: {"M1": Bars, ...} (any of TFS; missing ones are skipped).
    utc(t): candle clock -> UTC seconds (identity for UTC data)."""

    def __init__(self, bars: dict, utc=lambda t: t):
        self.utc = utc
        self.f = {name: Frame(b) for name, b in bars.items() if b is not None and len(b) > 20}
        self.reg = {n: _regime(self.f[n].b) for n in ("H1", "H4") if n in self.f}
        m15 = self.f.get("M15")
        if m15:
            self.turtle = _turtle(m15.b, m15.atr)
            self.pools = _pools(m15.b)
            self.gaps = _fvgs(m15.b, m15.atr)
        m5 = self.f.get("M5")
        if m5:
            self.cisd = _cisd(m5.b)
        self._asia = {}

    def _asia_range(self, t: int):
        """The Asian range (20:00-02:00 New York) that ended before t, as (high, low), from M1 candles."""
        m1 = self.f.get("M1")
        if not m1:
            return None
        ny = ny_clock(self.utc(t))
        day = (ny + 4 * 3600) // DAY                     # the session that starts 20:00 the evening before
        if (ny + 4 * 3600) % DAY < 6 * 3600:
            return None                                  # still inside the Asian session
        if day not in self._asia:
            start_ny = day * DAY - 4 * 3600
            # candle times for that New York window: find by scanning around t (UTC-based search is cheap)
            lo_i, hi_i = m1.at(t - 30 * 3600), m1.at(t)
            hs, ls = [], []
            for i in range(max(0, lo_i), hi_i + 1):
                x = ny_clock(self.utc(m1.b.t[i]))
                if start_ny <= x < start_ny + 6 * 3600:
                    hs.append(m1.b.h[i])
                    ls.append(m1.b.l[i])
            self._asia[day] = (max(hs), min(ls)) if hs else None
            if len(self._asia) > 400:
                self._asia.pop(next(iter(self._asia)))
        return self._asia[day]

    def features(self, t: int, price: float) -> dict | None:
        """Features at time t (only candles closed by t) for the current price."""
        x = {}
        for name, _ in TFS:
            fr = self.f.get(name)
            if not fr:
                x[f"st_{name}"] = x[f"mom_{name}"] = 0.0
                continue
            j = fr.at(t)
            if j < 3:
                return None
            x[f"st_{name}"] = float(fr.st[j])
            x[f"mom_{name}"] = fr.mom[j]
        for name in ("H1", "H4"):
            fr = self.f.get(name)
            x[f"reg_{name}"] = float(self.reg[name][fr.at(t)]) if fr and fr.at(t) >= 0 else 0.0
        h1 = self.f.get("H1")
        j = h1.at(t) if h1 else -1
        if j >= 48:
            hi, lo = max(h1.b.h[j - 47:j + 1]), min(h1.b.l[j - 47:j + 1])
            x["pd_H1"] = _clip((price - (hi + lo) / 2) / ((hi - lo) / 2), 1.5) if hi > lo else 0.0
        else:
            x["pd_H1"] = 0.0
        d1 = self.f.get("D1")
        j = d1.at(t) if d1 else -1
        if j >= 0 and d1.b.h[j] > d1.b.l[j]:
            x["pd_D1"] = _clip((price - (d1.b.h[j] + d1.b.l[j]) / 2) / ((d1.b.h[j] - d1.b.l[j]) / 2), 2.0)
        else:
            x["pd_D1"] = 0.0
        m15 = self.f.get("M15")
        j = m15.at(t) if m15 else -1
        x["turtle_M15"] = float(self.turtle[j]) if j >= 0 else 0.0
        if j >= 0:
            a = m15.atr[j] or 1.0
            up, dn = self.pools[0][j], self.pools[1][j]
            du = (up - price) / a if up is not None else 10.0
            dd = (price - dn) / a if dn is not None else 10.0
            x["draw"] = _clip((dd - du) / max(dd + du, 1e-9), 1.0)
            g = None
            for gap in reversed(self.gaps[j]):
                d, top, bot = gap
                if d == 1 and bot <= price and price - top <= 3 * a:
                    g = 1.0
                    break
                if d == -1 and top >= price and bot - price <= 3 * a:
                    g = -1.0
                    break
            x["fvg_M15"] = g or 0.0
        else:
            x["draw"] = x["fvg_M15"] = 0.0
        m5 = self.f.get("M5")
        j = m5.at(t) if m5 else -1
        x["cisd_M5"] = float(self.cisd[j]) if j >= 0 else 0.0
        asia = self._asia_range(t)
        m1 = self.f.get("M1")
        x["judas"] = 0.0
        if asia and m1:
            hi, lo = asia
            i = m1.at(t)
            k = max(0, i - 240)                          # the last 4 hours
            if min(m1.b.l[k:i + 1]) < lo < price:
                x["judas"] += 1.0
            if max(m1.b.h[k:i + 1]) > hi > price:
                x["judas"] -= 1.0
        return x


class Model:
    """Logistic regression over MarketRead.features, plus the Kronos vote."""

    def __init__(self, path: Path = WEIGHTS):
        try:
            d = json.loads(Path(path).read_text())
        except (OSError, ValueError):
            d = {}
        self.w, self.b0 = d.get("weights", {}), d.get("bias", 0.0)
        self.mean, self.std = d.get("mean", {}), d.get("std", {})
        self.horizon = d.get("horizon_min", 60)
        self.move_atr = d.get("move_atr", 1.0)        # average |move| over the horizon, in M1 ATRs ... per unit edge
        self.meta = {k: d.get(k) for k in ("trained", "tested", "accuracy_oos", "baseline_oos", "samples")}

    @property
    def ready(self) -> bool:
        return bool(self.w)

    def contributions(self, x: dict) -> dict:
        return {k: w * (x.get(k, 0.0) - self.mean.get(k, 0.0)) / (self.std.get(k) or 1.0) for k, w in self.w.items()}

    def logit(self, x: dict) -> float:
        return self.b0 + sum(self.contributions(x).values())

    def prob(self, x: dict, kronos_up: float | None = None) -> float:
        z = self.logit(x)
        if kronos_up is not None:
            p = min(0.97, max(0.03, kronos_up))
            z += KRONOS_W * math.log(p / (1 - p))
        return 1 / (1 + math.exp(-max(-30.0, min(30.0, z))))


# ------------------------------------------------------------------ consensus (what the concepts say together)
GROUPS = (   # name, weight, features averaged
    ("Higher timeframes", 3, ("st_D1", "st_H4", "reg_H4", "st_H1", "reg_H1")),
    ("Intraday structure", 2, ("st_M15", "st_M5")),
    ("ICT order flow", 2, ("turtle_M15", "cisd_M5", "judas", "fvg_M15")),
)
KRONOS_GROUP_W = 2


def consensus(x: dict, kronos_up: float | None = None) -> dict:
    """A plain vote, -1 (all bearish) .. +1 (all bullish): higher-timeframe structure and EMA regime, intraday
    structure, ICT order-flow events and Kronos. Descriptive: it was not found to predict the next 30-120
    minutes (ict_train.py / consensus check), so it says what the chart shows, not what comes next."""
    parts, tot, wsum = [], 0.0, 0.0
    for name, w, keys in GROUPS:
        v = sum(x.get(k, 0.0) for k in keys) / len(keys)
        parts.append({"name": name, "score": round(v, 2), "weight": w,
                      "items": {k: x.get(k, 0.0) for k in keys}})
        tot += w * v
        wsum += w
    if kronos_up is not None:
        v = max(-1.0, min(1.0, (kronos_up - 0.5) * 2.5))
        parts.append({"name": "Kronos", "score": round(v, 2), "weight": KRONOS_GROUP_W, "items": {"up_prob": kronos_up}})
        tot += KRONOS_GROUP_W * v
        wsum += KRONOS_GROUP_W
    s = tot / wsum
    return {"score": round(s, 3), "bias": 1 if s >= 0.35 else (-1 if s <= -0.35 else 0), "parts": parts}
