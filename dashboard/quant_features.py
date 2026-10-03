"""Features for the quant model (quant.py, quant_train.py): what a desk's systematic book would read on gold's
closed candles before forecasting the next 30 minutes. Pure Python (no numpy): the live page computes exactly
the same numbers the training script did, with the same code.

Everything is read at the close of M1 candle i (the "query", candle clock t[i] + 60) from candles that had
closed by then, so nothing looks ahead (tests/test_quant.py appends future candles and checks the features
at i don't change). Prices are turned into scale-free numbers (volatility units, ATRs, basis points, 0..1
positions), so a model fitted on gold at 2600 still reads gold at 4000.

  time        sin / cos of the New York minute, weekday, one-hot of the ictclock.DAY_MAP segment
  returns     1, 5, 15, 30, 60, 240-minute returns in units of the last 2 hours' 1-minute volatility x sqrt(h)
  volatility  realised volatility over 5 / 30 / 120 minutes (bp), short / long ratios (5/120, 30/120, 120/1440),
              vol-of-vol (dispersion of the last twelve 5-minute vols), ATR in bp
  range       where price sits in the last 60 / 240 minutes and today's range (0..1), those ranges in ATRs,
              distance to PDH / PDL, the last Asian range high / low and today's open in H1 ATRs
  trend / MR  efficiency ratio over 30 / 120 minutes, RSI(14) on M1 and M5, z-score against the 60-minute
              mean, H1 close against its EMA 50 and EMA 20 - EMA 50, in H1 ATRs
  candles     the last 3 M1 candles: signed body in ATRs, upper / lower wick share of the range; last range
  ICT         from ictlib.Tape on M1, M5 and M15: last sweep / CISD / MSS-BOS-CHoCH direction and age (candles),
              whether that break was an MSS, nearest open FVG above / below (ATRs), premium / discount position
              in the dealing range, structure bias (mean direction of the last 3 breaks)
  cross       silver: 5 / 30 / 60-minute returns in silver's own vol units, XAU - XAG relative strength,
              correlation, SMT state on M1 and M5 (smt.SMTReader, the page's own reading); dollar index:
              returns with the sign flipped (a falling dollar reads as + for gold) and correlation
  Kronos      up_prob - 0.5 and move / unit when a live Kronos 30-minute forecast is given. Training can't replay
              Kronos over a year (minutes per forecast), so it sees the placeholders 0 / 0: the fitted trees never
              split on them and Kronos enters through quant.blend_with_kronos instead.

How the ICT tapes stay identical live and in training: a Tape depends on where its candles start. Each
timeframe's tape covers the candles from a UTC block start, floor((t - W) / B) * B, to the query (W, B in
ICT_WIN), so the live page (2500 M1 / 600 M5 / 400 M15 candles) and the year-long replay read the very same
window. Recursive series (ATR, RSI, EMA) forget their start within a few hundred candles, so the live window
gives the training values to ~1e-12.
"""
from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections import OrderedDict

import ictclock as ck
import smt as smtmod
from engine import Bars, ema, ny7_offset, rsi
from ictlib import Tape, atr_series

DAY = 86400
HORIZON = 30                                   # minutes ahead the model forecasts
RET_H = (1, 5, 15, 30, 60, 240)
TF_SEC = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600}
ICT_TFS = ("M1", "M5", "M15")
ICT_WIN = {"M1": (6 * 3600, 2 * 3600), "M5": (24 * 3600, 8 * 3600), "M15": (48 * 3600, 24 * 3600)}
ICT_KEYS = ("sweep_dir", "sweep_age", "cisd_dir", "cisd_age", "brk_dir", "brk_age", "brk_mss", "fvg_up",
            "fvg_dn", "pd", "bias")
AGE_CAP = 99.0                                 # "no event in the window"
FVG_CAP = 10.0
LVL_CAP = 15.0
RET_CAP = 10.0
SMT_BARS = 139                                 # what smt.SMTReader reads (lookback 120 + pivots + k)
STALE = 300                                    # a cross-asset candle older than this (s) counts as missing
MIN_M1 = 300                                   # fewer closed M1 candles: no features
SESSIONS = tuple(seg[0] for seg in ck.DAY_MAP)
_SEG = tuple((seg[1], seg[2], seg[0]) for seg in ck.DAY_MAP)


def slug(name: str) -> str:
    s = "".join(ch if ch.isalnum() else "_" for ch in name.lower())
    while "__" in s:
        s = s.replace("__", "_")
    return s.strip("_")


SESSION_KEYS = tuple("sess_" + slug(s) for s in SESSIONS)
KRONOS_FEATURES = ("kr_up", "kr_move")
FEATURES = tuple(
    ["tod_sin", "tod_cos", "dow"] + list(SESSION_KEYS)
    + [f"ret_{h}" for h in RET_H]
    + ["rv5_bp", "rv30_bp", "rv120_bp", "vr_5_120", "vr_30_120", "vr_120_1440", "volvol", "atr_bp",
       "pos60", "pos240", "pos_day", "rng60_atr", "rng240_atr",
       "pdh_atr", "pdl_atr", "asia_hi_atr", "asia_lo_atr", "day_open_atr",
       "er30", "er120", "rsi_m1", "rsi_m5", "z60",
       "c0_body", "c0_uw", "c0_lw", "c0_rng", "c1_body", "c1_uw", "c1_lw", "c2_body", "c2_uw", "c2_lw",
       "h1_ema50", "h1_slope"]
    + [f"{tf.lower()}_{k}" for tf in ICT_TFS for k in ICT_KEYS]
    + ["xag_r5", "xag_r30", "xag_r60", "rs30", "rs60", "xag_corr", "smt_m1", "smt_m5",
       "dxy_r5", "dxy_r30", "dxy_r60", "dxy_corr"]
    + list(KRONOS_FEATURES)
)
SILVER_FEATURES = ("xag_r5", "xag_r30", "xag_r60", "rs30", "rs60", "xag_corr", "smt_m1", "smt_m5")
DXY_FEATURES = ("dxy_r5", "dxy_r30", "dxy_r60", "dxy_corr")

# one line each, for the page ("top" contributions)
DESCRIPTIONS = {
    "tod_sin": "time of day (New York)", "tod_cos": "time of day (New York)", "dow": "day of the week",
    **{k: f"in the {s} segment" for k, s in zip(SESSION_KEYS, SESSIONS)},
    **{f"ret_{h}": f"last {h} min return (vol units)" for h in RET_H},
    "rv5_bp": "5-min volatility", "rv30_bp": "30-min volatility", "rv120_bp": "2-hour volatility",
    "vr_5_120": "volatility now vs 2 h", "vr_30_120": "30-min vs 2-hour volatility",
    "vr_120_1440": "2-hour vs 24-hour volatility", "volvol": "how unsteady volatility is", "atr_bp": "M1 ATR",
    "pos60": "position in the last hour's range", "pos240": "position in the last 4 hours' range",
    "pos_day": "position in today's range", "rng60_atr": "last hour's range (ATRs)",
    "rng240_atr": "last 4 hours' range (ATRs)", "pdh_atr": "distance to PDH", "pdl_atr": "distance to PDL",
    "asia_hi_atr": "distance to the Asian high", "asia_lo_atr": "distance to the Asian low",
    "day_open_atr": "distance to today's open", "er30": "30-min trend efficiency", "er120": "2-hour trend efficiency",
    "rsi_m1": "RSI M1", "rsi_m5": "RSI M5", "z60": "stretch from the 60-min mean",
    "c0_body": "last candle's body", "c0_uw": "last candle's upper wick", "c0_lw": "last candle's lower wick",
    "c0_rng": "last candle's range", "c1_body": "candle before's body", "c1_uw": "candle before's upper wick",
    "c1_lw": "candle before's lower wick", "c2_body": "3rd candle back's body", "c2_uw": "3rd candle back's upper wick",
    "c2_lw": "3rd candle back's lower wick", "h1_ema50": "H1 price vs EMA 50", "h1_slope": "H1 EMA 20 vs EMA 50",
    **{f"{tf.lower()}_{k}": f"{tf} {w}" for tf in ICT_TFS for k, w in (
        ("sweep_dir", "last sweep side"), ("sweep_age", "candles since the last sweep"),
        ("cisd_dir", "last CISD side"), ("cisd_age", "candles since the last CISD"),
        ("brk_dir", "last structure break side"), ("brk_age", "candles since the last break"),
        ("brk_mss", "last break was an MSS"), ("fvg_up", "nearest open FVG above (ATRs)"),
        ("fvg_dn", "nearest open FVG below (ATRs)"), ("pd", "premium (+) / discount (-)"),
        ("bias", "structure bias"))},
    "xag_r5": "silver 5-min return", "xag_r30": "silver 30-min return", "xag_r60": "silver 60-min return",
    "rs30": "gold vs silver, 30 min", "rs60": "gold vs silver, 60 min", "xag_corr": "gold-silver correlation",
    "smt_m1": "M1 SMT vs silver", "smt_m5": "M5 SMT vs silver",
    "dxy_r5": "dollar 5-min (inverted)", "dxy_r30": "dollar 30-min (inverted)", "dxy_r60": "dollar 60-min (inverted)",
    "dxy_corr": "gold-dollar correlation", "kr_up": "Kronos up-probability", "kr_move": "Kronos move",
}


# ------------------------------------------------------------------ clock
_OFF: dict = {}


def ny_clock(u: int) -> int:
    """UTC -> New York clock seconds; same as smc._ny, cached per UTC day (the offset only changes at midnight)."""
    d = u // DAY
    off = _OFF.get(d)
    if off is None:
        off = _OFF[d] = ny7_offset(u) - 7 * 3600
    return u + off


def session_name(minute: int) -> str:
    """ictclock.session(utc)["name"] from the New York minute."""
    for a, z, name in _SEG:
        if a <= minute < z:
            return name
    return "Asia"


def _clip(x: float, k: float) -> float:
    return k if x > k else (-k if x < -k else x)


# ------------------------------------------------------------------ candles
def sub(b: Bars, a: int, z: int) -> Bars:
    """Candles a .. z-1 as a new Bars."""
    out = Bars(b.sec)
    out.t, out.o, out.h, out.l, out.c = b.t[a:z], b.o[a:z], b.h[a:z], b.l[a:z], b.c[a:z]
    out.v = b.v[a:z] if len(b.v) == len(b.t) else [0.0] * len(out.t)
    out.spread = b.spread[a:z] if len(b.spread) == len(b.t) else [0.0] * len(out.t)
    return out


def aggregate(b: Bars, sec: int) -> Bars:
    """Higher-timeframe candles from M1 (the last one may still be open; see closed_by)."""
    out = Bars(sec)
    t, o, h, l, c = b.t, b.o, b.h, b.l, b.c
    v = b.v if len(b.v) == len(b.t) else [0.0] * len(b.t)
    ot, oh, ol, oc, ov = out.t, out.h, out.l, out.c, out.v
    for i in range(len(t)):
        k = t[i] - t[i] % sec
        if ot and ot[-1] == k:
            if h[i] > oh[-1]:
                oh[-1] = h[i]
            if l[i] < ol[-1]:
                ol[-1] = l[i]
            oc[-1] = c[i]
            ov[-1] += v[i]
        else:
            out.append(k, o[i], h[i], l[i], c[i], v[i])
    return out


def merge_closed(given: Bars | None, m1: Bars, sec: int) -> Bars:
    """`given` closed candles of a timeframe, extended by the ones built from closed M1 candles that closed since
    (a broker's list can lag the M1 close by a poll). Built candles must start inside the M1 window (complete)."""
    if not len(m1):
        return given if given is not None else Bars(sec)
    last_close = m1.t[-1] + 60
    agg = aggregate(m1, sec)
    first = m1.t[0]
    if given is not None and len(given):
        out = sub(given, 0, len(given))
        after = given.t[-1]
    else:
        out, after = Bars(sec), None
    for k in range(len(agg)):
        tk = agg.t[k]
        if tk < first or tk + sec > last_close or (after is not None and tk <= after):
            continue
        out.append(tk, agg.o[k], agg.h[k], agg.l[k], agg.c[k], agg.v[k])
    return out


def live_frames(bars: dict) -> tuple:
    """(M1, {"M5", "M15", "H1"}) from the page's closed-candle dict."""
    m1 = bars["M1"]
    return m1, {tf: merge_closed(bars.get(tf), m1, TF_SEC[tf]) for tf in ("M5", "M15", "H1")}


def _frames_of(x) -> dict | None:
    if x is None:
        return None
    if isinstance(x, Bars):
        return {"M1": x} if len(x) else None
    if isinstance(x, dict) and x.get("M1") is not None and len(x["M1"]):
        return x
    return None


def _prefix(c: list) -> tuple:
    """Prefix sums of squared and absolute 1-candle changes (change 0 for the first candle)."""
    n = len(c)
    p2, pa = [0.0] * (n + 1), [0.0] * (n + 1)
    s2 = sa = 0.0
    prev = c[0] if n else 0.0
    for k in range(n):
        d = c[k] - prev
        prev = c[k]
        s2 += d * d
        sa += d if d >= 0 else -d
        p2[k + 1] = s2
        pa[k + 1] = sa
    return p2, pa


def _msq(p2: list, i: int, n: int) -> float:
    """Mean squared 1-candle change over the n candles ending at i (fewer near the start)."""
    n = min(n, i)
    if n <= 0:
        return 0.0
    v = (p2[i + 1] - p2[i + 1 - n]) / n
    return v if v > 0 else 0.0


class _Other:
    """Silver or the dollar index: M1 (and M5) closed candles on the gold candle clock."""

    def __init__(self, frames: dict):
        self.m1 = frames["M1"]
        self.t, self.c = self.m1.t, self.m1.c
        self.p2, _ = _prefix(self.c)
        m5 = frames.get("M5")
        self.m5 = merge_closed(m5, self.m1, 300) if m5 is not None else _closed_agg(self.m1, 300)
        self.m5_close = [x + 300 for x in self.m5.t]

    def idx(self, t_gold: int) -> int:
        """The candle at or before gold's candle t_gold (closed with it), -1 when missing or stale."""
        k = bisect_right(self.t, t_gold) - 1
        return k if k >= 0 and self.t[k] >= t_gold - STALE else -1

    def zret(self, k: int, h: int) -> float:
        if k - h < 0:
            return 0.0
        s = math.sqrt(_msq(self.p2, k, 120))
        if s <= 1e-12:
            return 0.0
        return _clip((self.c[k] - self.c[k - h]) / (s * math.sqrt(h)), RET_CAP)


def _closed_agg(m1: Bars, sec: int) -> Bars:
    return merge_closed(None, m1, sec)


# ------------------------------------------------------------------ ICT tape read as of a candle
def _last(xs: list, j: int):
    for x in reversed(xs):
        if x["i"] <= j:
            return x
    return None


def tape_features(tp: Tape, j: int, price: float) -> list:
    """ICT_KEYS values from tape `tp` as known at its candle j (events and states after j are ignored)."""
    if tp is None or j < 0 or j >= tp.n:
        return [0.0, AGE_CAP, 0.0, AGE_CAP, 0.0, AGE_CAP, 0.0, FVG_CAP, FVG_CAP, 0.0, 0.0]
    atr = tp.atr[j] if tp.atr[j] > 0 else 1e-9
    out = []
    for xs in (tp.sweeps, tp.cisd):
        e = _last(xs, j)
        out += [float(e["dir"]), float(min(AGE_CAP, j - e["i"]))] if e else [0.0, AGE_CAP]
    brs = []
    for x in reversed(tp.breaks):
        if x["i"] <= j:
            brs.append(x)
            if len(brs) == 3:
                break
    if brs:
        e = brs[0]
        mss = 1.0 if e["kind"] == "MSS" and e.get("known", e["i"]) <= j else 0.0
        out += [float(e["dir"]), float(min(AGE_CAP, j - e["i"])), mss]
    else:
        out += [0.0, AGE_CAP, 0.0]
    up = dn = FVG_CAP
    for g in tp.fvgs:
        if g["born"] > j or (g["end"] is not None and g["end"] <= j):
            continue
        if g["top"] > price:
            d = (g["bottom"] - price) / atr
            up = min(up, d if d > 0 else 0.0)
        if g["bottom"] < price:
            d = (price - g["top"]) / atr
            dn = min(dn, d if d > 0 else 0.0)
    out += [up, dn]
    hi = lo = None
    for s in reversed(tp.swings):
        if s["known"] > j:
            continue
        if s["dir"] == 1 and hi is None:
            hi = s
        elif s["dir"] == -1 and lo is None:
            lo = s
        if hi is not None and lo is not None:
            break
    pd = 0.0
    if hi is not None and lo is not None:
        b = tp.b
        top = max(hi["price"], max(b.h[hi["i"]:j + 1]))
        bot = min(lo["price"], min(b.l[lo["i"]:j + 1]))
        if top > bot:
            pd = _clip((price - bot) / (top - bot) - 0.5, 1.5)
    out.append(pd)
    out.append(sum(x["dir"] for x in brs) / len(brs) if brs else 0.0)
    return out


# ------------------------------------------------------------------ the builder
class FeatureBuilder:
    """Features at any closed M1 candle of `m1`.

    m1: closed M1 candles (candle clock), oldest first. tfs: {"M5", "M15", "H1": closed Bars}; missing ones are
    built from m1. silver / dxy: {"M1": Bars[, "M5": Bars]} or an M1 Bars, closed candles on the gold candle
    clock (None: their features are 0). utc(t): candle clock -> UTC seconds (default: the candles are UTC).
    tape_cache: a dict the caller keeps between builders to reuse ICT tapes (the live page)."""

    def __init__(self, m1: Bars, tfs: dict | None = None, silver=None, dxy=None, utc=None,
                 tape_cache: dict | None = None):
        self.utc = utc
        self.m1 = m1
        tfs = dict(tfs or {})
        for tf in ("M5", "M15", "H1"):
            if tfs.get(tf) is None:
                tfs[tf] = _closed_agg(m1, TF_SEC[tf])
        self.tf = {"M1": m1, **tfs}
        conv = (lambda xs: list(xs)) if utc is None else (lambda xs: [utc(x) for x in xs])
        self.U = {tf: conv(self.tf[tf].t) for tf in ("M1", "M5", "M15", "H1")}
        self.close = {tf: [x + TF_SEC[tf] for x in self.tf[tf].t] for tf in ("M5", "M15", "H1")}
        self.tape_cache = tape_cache if tape_cache is not None else {}
        self.used_tapes: set = set()
        self._prep_m1()
        self._prep_h1()
        self.m5_rsi = rsi(self.tf["M5"].c, 14)
        fs, fd = _frames_of(silver), _frames_of(dxy)
        self.xag = _Other(fs) if fs else None
        self.dxy = _Other(fd) if fd else None
        self.smt_reader = smtmod.SMTReader(name="silver")

    # -- per-candle series -------------------------------------------------------------------------------------
    def _prep_m1(self) -> None:
        b = self.m1
        n = len(b)
        h, l, o = b.h, b.l, b.o
        self.atr = atr_series(b)
        self.rsi = rsi(b.c, 14)
        self.p2, self.pa = _prefix(b.c)
        dh, dl, dop = [0.0] * n, [0.0] * n, [0.0] * n
        ah, al = [None] * n, [None] * n
        tday = [0] * n
        cur_d = None
        hi = lo = op = 0.0
        cur_a, a_hi, a_lo, done = None, 0.0, 0.0, (None, None)
        U = self.U["M1"]
        for k in range(n):
            ny = ny_clock(U[k])
            m = ny % DAY // 60
            d = (ny - 61200) // DAY
            if d != cur_d:
                cur_d, hi, lo, op = d, h[k], l[k], o[k]
            else:
                if h[k] > hi:
                    hi = h[k]
                if l[k] < lo:
                    lo = l[k]
            tday[k], dh[k], dl[k], dop[k] = d, hi, lo, op
            akey = (ny + 14400) // DAY
            if m >= 1200 or m < 120:
                if akey != cur_a:
                    cur_a, a_hi, a_lo = akey, h[k], l[k]
                else:
                    if h[k] > a_hi:
                        a_hi = h[k]
                    if l[k] < a_lo:
                        a_lo = l[k]
            elif cur_a is not None:
                if akey == cur_a:
                    done = (a_hi, a_lo)
                cur_a = None
            ah[k], al[k] = done
        self.tday, self.day_hi, self.day_lo, self.day_open = tday, dh, dl, dop
        self.asia_hi, self.asia_lo = ah, al

    def _prep_h1(self) -> None:
        b = self.tf["H1"]
        self.h1_atr = atr_series(b)
        self.h1_e20, self.h1_e50 = ema(b.c, 20), ema(b.c, 50)
        keys, hl = [], {}
        for k, u in enumerate(self.U["H1"]):
            d = (ny_clock(u) - 61200) // DAY
            if d in hl:
                x = hl[d]
                if b.h[k] > x[0]:
                    x[0] = b.h[k]
                if b.l[k] < x[1]:
                    x[1] = b.l[k]
            else:
                hl[d] = [b.h[k], b.l[k]]
                keys.append(d)
        self.h1_days, self.h1_hl = keys, hl

    # -- helpers -----------------------------------------------------------------------------------------------
    def last_closed(self, tf: str, q: int) -> int:
        """Index of the last `tf` candle closed by candle-clock time q."""
        return bisect_right(self.close[tf], q) - 1

    def unit(self, i: int) -> float:
        """$ per model move unit at M1 candle i: M1 ATR(14) x sqrt(30)."""
        return max(self.atr[i], 1e-9) * math.sqrt(HORIZON)

    def tape(self, tf: str, j: int) -> tuple:
        """(tape, first candle index) of the tape window that answers candle j of tf."""
        U = self.U[tf]
        W, B = ICT_WIN[tf]
        start = (U[j] - W) // B * B
        s0 = bisect_left(U, start)
        e0 = bisect_left(U, start + W + B) - 1
        key = (tf, U[s0], U[e0], e0 - s0)
        self.used_tapes.add(key)
        tp = self.tape_cache.get(key)
        if tp is None:
            tp = Tape(sub(self.tf[tf], s0, e0 + 1), tf)
            if self.utc is None:                     # training: the previous block is never needed again
                for k in [k for k in self.tape_cache if k[0] == tf]:
                    del self.tape_cache[k]
            self.tape_cache[key] = tp
        return tp, s0

    # -- features ----------------------------------------------------------------------------------------------
    def features(self, i: int, kronos30: dict | None = None) -> OrderedDict | None:
        """FEATURES at the close of M1 candle i, in order. None with fewer than MIN_M1 candles before it."""
        v = self.vector(i, kronos30)
        return None if v is None else OrderedDict(zip(FEATURES, v))

    def vector(self, i: int, kronos30: dict | None = None) -> list | None:
        if i < MIN_M1 or i >= len(self.m1):
            return None
        x = self._raw(i, kronos30)
        return [x[k] for k in FEATURES]

    def _raw(self, i: int, kronos30: dict | None) -> dict:
        b = self.m1
        t, o, h, l, c = b.t, b.o, b.h, b.l, b.c
        ci = c[i]
        q = t[i] + 60
        x = {}
        # time
        ny = ny_clock(self.U["M1"][i] + 60)
        minute = ny % DAY // 60
        ang = 2 * math.pi * minute / 1440.0
        x["tod_sin"], x["tod_cos"] = math.sin(ang), math.cos(ang)
        x["dow"] = float((ny // DAY + 3) % 7)
        sk = "sess_" + slug(session_name(minute))
        for k in SESSION_KEYS:
            x[k] = 1.0 if k == sk else 0.0
        # returns and volatility
        atr = max(self.atr[i], 1e-9)
        p2 = self.p2
        s1 = math.sqrt(_msq(p2, i, 120))
        s1 = s1 if s1 > 1e-12 else atr
        for hz in RET_H:
            x[f"ret_{hz}"] = _clip((ci - c[i - hz]) / (s1 * math.sqrt(hz)), RET_CAP) if i - hz >= 0 else 0.0
        rv = {n: math.sqrt(_msq(p2, i, n)) for n in (5, 30, 120, 1440)}
        x["rv5_bp"], x["rv30_bp"], x["rv120_bp"] = (rv[5] / ci * 1e4, rv[30] / ci * 1e4, rv[120] / ci * 1e4)
        x["vr_5_120"] = rv[5] / rv[120] if rv[120] > 0 else 1.0
        x["vr_30_120"] = rv[30] / rv[120] if rv[120] > 0 else 1.0
        x["vr_120_1440"] = rv[120] / rv[1440] if rv[1440] > 0 else 1.0
        vs = [math.sqrt(_msq(p2, i - 5 * k, 5)) for k in range(12) if i - 5 * k - 5 >= 0]
        if len(vs) >= 2:
            mv = sum(vs) / len(vs)
            x["volvol"] = math.sqrt(sum((v - mv) ** 2 for v in vs) / len(vs)) / mv if mv > 0 else 0.0
        else:
            x["volvol"] = 0.0
        x["atr_bp"] = atr / ci * 1e4
        # ranges
        for n in (60, 240):
            a = max(0, i - n + 1)
            hi, lo = max(h[a:i + 1]), min(l[a:i + 1])
            x[f"pos{n}"] = (ci - lo) / (hi - lo) if hi > lo else 0.5
            x[f"rng{n}_atr"] = (hi - lo) / atr
        dh, dl = self.day_hi[i], self.day_lo[i]
        x["pos_day"] = (ci - dl) / (dh - dl) if dh > dl else 0.5
        jh = self.last_closed("H1", q)
        atr_h = self.h1_atr[jh] if jh >= 0 and self.h1_atr[jh] > 0 else atr * math.sqrt(60)
        lv = lambda p: _clip((ci - p) / atr_h, LVL_CAP) if p is not None else 0.0
        days = self.h1_days
        kd = bisect_left(days, self.tday[i]) - 1
        pd = self.h1_hl[days[kd]] if kd >= 0 else (None, None)
        x["pdh_atr"], x["pdl_atr"] = lv(pd[0]), lv(pd[1])
        x["asia_hi_atr"], x["asia_lo_atr"] = lv(self.asia_hi[i]), lv(self.asia_lo[i])
        x["day_open_atr"] = lv(self.day_open[i])
        # trend / mean reversion
        pa = self.pa
        for n in (30, 120):
            if i - n >= 0:
                den = pa[i + 1] - pa[i + 1 - n]
                x[f"er{n}"] = abs(ci - c[i - n]) / den if den > 0 else 0.0
            else:
                x[f"er{n}"] = 0.0
        x["rsi_m1"] = (self.rsi[i] - 50.0) / 50.0
        j5 = self.last_closed("M5", q)
        x["rsi_m5"] = (self.m5_rsi[j5] - 50.0) / 50.0 if j5 >= 0 else 0.0
        w = c[max(0, i - 59):i + 1]
        mu = sum(w) / len(w)
        sd = math.sqrt(sum((v - mu) ** 2 for v in w) / len(w))
        x["z60"] = _clip((ci - mu) / sd, 5.0) if sd > 1e-12 else 0.0
        # candles
        for k in range(3):
            j = i - k
            rg = h[j] - l[j]
            x[f"c{k}_body"] = _clip((c[j] - o[j]) / atr, 5.0)
            x[f"c{k}_uw"] = (h[j] - max(o[j], c[j])) / rg if rg > 0 else 0.0
            x[f"c{k}_lw"] = (min(o[j], c[j]) - l[j]) / rg if rg > 0 else 0.0
        x["c0_rng"] = _clip((h[i] - l[i]) / atr, 10.0)
        if jh >= 0:
            x["h1_ema50"] = _clip((ci - self.h1_e50[jh]) / atr_h, LVL_CAP)
            x["h1_slope"] = _clip((self.h1_e20[jh] - self.h1_e50[jh]) / atr_h, LVL_CAP)
        else:
            x["h1_ema50"] = x["h1_slope"] = 0.0
        # ICT tapes
        for tf in ICT_TFS:
            j = i if tf == "M1" else self.last_closed(tf, q)
            if j >= 0:
                tp, s0 = self.tape(tf, j)
                vals = tape_features(tp, j - s0, ci)
            else:
                vals = tape_features(None, -1, ci)
            p = tf.lower() + "_"
            for k, v in zip(ICT_KEYS, vals):
                x[p + k] = v
        # cross-asset
        self._cross(x, i, q, j5)
        # Kronos (live only; 0 / 0 in training)
        x["kr_up"] = x["kr_move"] = 0.0
        if kronos30 and kronos30.get("up_prob") is not None:
            x["kr_up"] = float(kronos30["up_prob"]) - 0.5
            mv = kronos30.get("move")
            x["kr_move"] = _clip(float(mv) / self.unit(i), 5.0) if mv is not None else 0.0
        return x

    def _cross(self, x: dict, i: int, q: int, j5: int) -> None:
        for k in SILVER_FEATURES + DXY_FEATURES:
            x[k] = 0.0
        b = self.m1
        t = b.t
        gwin = None
        if self.xag is not None or self.dxy is not None:
            gwin = sub(b, max(0, i - SMT_BARS + 1), i + 1)
        sv = self.xag
        if sv is not None:
            k = sv.idx(t[i])
            if k >= 0:
                for hz in (5, 30, 60):
                    x[f"xag_r{hz}"] = sv.zret(k, hz)
                x["rs30"] = x["ret_30"] - x["xag_r30"]
                x["rs60"] = x["ret_60"] - x["xag_r60"]
                a = bisect_left(sv.t, gwin.t[0])
                r = self.smt_reader.update("M1", gwin, sub(sv.m1, a, k + 1), last_forming=False)
                x["xag_corr"] = float(r.get("corr") or 0.0)
                if r.get("ok") and r.get("corr_ok"):
                    x["smt_m1"] = float(r.get("state") or 0)
                if j5 >= 0:
                    g5 = self.tf["M5"]
                    gw5 = sub(g5, max(0, j5 - SMT_BARS + 1), j5 + 1)
                    k5 = bisect_right(sv.m5_close, q) - 1
                    if k5 >= 0:
                        a5 = bisect_left(sv.m5.t, gw5.t[0])
                        r5 = self.smt_reader.update("M5", gw5, sub(sv.m5, a5, k5 + 1), last_forming=False)
                        if r5.get("ok") and r5.get("corr_ok"):
                            x["smt_m5"] = float(r5.get("state") or 0)
        dx = self.dxy
        if dx is not None:
            k = dx.idx(t[i])
            if k >= 0:
                for hz in (5, 30, 60):
                    x[f"dxy_r{hz}"] = -dx.zret(k, hz)
                a = bisect_left(dx.t, gwin.t[0])
                cr = smtmod.correlation(gwin, sub(dx.m1, a, k + 1), 120)
                x["dxy_corr"] = float(cr or 0.0)
