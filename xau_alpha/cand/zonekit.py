"""
cand/zonekit.py - shared, causal building blocks for the zone-based candidates failed_breakout (H3) and
range_fade (H10). Not a candidate itself (no GRID / orders).

Everything is computed on M1 arrays CUT AT THE TEST START (2026-06-01), so no function here can see or emit
a holdout bar. All thresholds downstream are in ATR units (A = data.atr(m1), M1 Wilder ATR14 on mid).

Mirroring: every rule is written once in "long space". For d = +1 the arrays are the mid OHLC; for d = -1 they
are the negated series (o' = -o, h' = -l, l' = -h, c' = -c), so a bullish engulf / pin / breakout there is the
bearish one in price space, zones (lo, hi) map to (-hi, -lo), and swing lows become long-space swing highs.
Longs and shorts therefore use byte-identical rules and parameters.

Zones: features.zone_timeline (SYNTHESIS "ZONES"), flattened to arrays for vectorised lookup:
    chg[j]   bar at whose close zone set j becomes valid
    off[j]   Z[off[j]:off[j+1]] = zones (lo, hi, touches) valid from chg[j] to chg[j+1]-1
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from data import TEST, _ms, atr, news_block_mask  # noqa: E402
import features as F  # noqa: E402

TEST_MS = _ms(TEST[0])
NEAR_WIN = 240          # NEAREST_LEVEL look-back in bars (SYNTHESIS shared definitions)
NEAR_MAX_R = 4.0        # a nearest level further than this many stop distances is ignored -> fallback
NEAR_FALLBACK_R = 2.0

_C: dict = {}


def base(m1):
    """Per-process cache of TEST-cut arrays, plus long-space views for d = +1 / -1."""
    ts_all = m1["ts"].values
    key = (len(m1), int(ts_all[0]), int(ts_all[-1]))
    if _C.get("key") == key:
        return _C["base"]
    _C.clear()
    n = int(np.searchsorted(ts_all, TEST_MS, side="left"))
    df = m1.iloc[:n]
    o, h, l, c = (df[x].values.astype(np.float64) for x in "ohlc")
    A = atr(df)
    b = {"n": n, "ts": ts_all[:n].astype(np.int64), "A": A, "h": h, "l": l, "c": c,
         "lon_mod": df["lon_mod"].values.astype(np.int64), "ny_mod": df["ny_mod"].values.astype(np.int64),
         "dow": df["dow"].values.astype(np.int64), "news": {}, "piv": {}, "sp": {}, "sess": {}}
    for d in (1, -1):
        O, H, Lo, Cl = (o, h, l, c) if d > 0 else (-o, -l, -h, -c)
        eng, _ = F.engulf(O, Cl)
        b["sp"][d] = {"o": O, "h": H, "l": Lo, "c": Cl, "eng": np.nan_to_num(eng).astype(bool)}
    _C["key"] = key
    _C["base"] = b
    return b


def news(b, win):
    """HIGH-impact blackout [T-win, T+win] minutes at the close of each bar (win=0: off)."""
    if win not in b["news"]:
        b["news"][win] = news_block_mask(b["ts"], win, win) if win > 0 else np.zeros(b["n"], dtype=bool)
    return b["news"][win]


def session(b, sess):
    """Entry-session mask on the decision bar.
    'lonny' = 06:00-12:00 London local OR 08:30-11:30 New York (SYNTHESIS H3 session), 'lon', 'ny', 'all'."""
    if sess not in b["sess"]:
        lon = (b["lon_mod"] >= 360) & (b["lon_mod"] < 720)
        ny = (b["ny_mod"] >= 510) & (b["ny_mod"] < 690)
        m = {"lonny": lon | ny, "lon": lon, "ny": ny, "all": np.ones(b["n"], dtype=bool)}[sess]
        b["sess"][sess] = m
    return b["sess"][sess]


def zones(b, L=240, k=3, w=1.0, K=3):
    """features.zone_timeline flattened: (chg, off, Z). Keeps at most 4 timelines per process (memory)."""
    key = ("z", L, k, float(w), K)
    if key not in _C:
        chg, zsets = F.zone_timeline(b["h"], b["l"], b["A"], L=L, k=k, w=w, K=K)
        cnt = np.array([len(z) for z in zsets], dtype=np.int64)
        off = np.r_[0, np.cumsum(cnt)].astype(np.int64)
        Z = np.vstack([z for z in zsets if len(z)]) if cnt.sum() else np.zeros((0, 3))
        zkeys = [x for x in _C if isinstance(x, tuple) and x[0] == "z"]
        if len(zkeys) >= 4:
            _C.pop(zkeys[0])
        _C[key] = (chg, off, Z)
    return _C[key]


def zone_pairs(b, zt, bars):
    """Expand bar indices into (bar, zone) pairs using the zone set known at the close of each bar.
    Returns (bar, zlo, zhi, ztouch) in PRICE space."""
    chg, off, Z = zt
    bars = np.asarray(bars, dtype=np.int64)
    jz = np.searchsorted(chg, bars, side="right") - 1
    ok = jz >= 0
    bars, jz = bars[ok], jz[ok]
    cnt = off[jz + 1] - off[jz]
    tot = int(cnt.sum())
    if tot == 0:
        e = np.zeros(0)
        return np.zeros(0, dtype=np.int64), e, e, e
    rep = np.repeat(bars, cnt)
    first = np.repeat(off[jz], cnt)
    zi = first + (np.arange(tot) - np.repeat(np.cumsum(cnt) - cnt, cnt))
    return rep, Z[zi, 0].copy(), Z[zi, 1].copy(), Z[zi, 2].copy()


def pivots(b, k):
    """All confirmed k-fractal pivots in long space: {d: (idx, value)} of long-space swing HIGHS.
    Same fractal definition as features.pivots / zone_timeline; pivot p is usable from bar p + k."""
    if k not in b["piv"]:
        h, l = b["h"], b["l"]
        n = len(h)
        win = 2 * k + 1
        hmax = pd.Series(h).rolling(win, center=True).max().values
        lmin = pd.Series(l).rolling(win, center=True).min().values
        okm = np.zeros(n, dtype=bool)
        okm[k:n - k] = True
        sh = np.flatnonzero((h == hmax) & okm)
        sl = np.flatnonzero((l == lmin) & okm)
        b["piv"][k] = {1: (sh, h[sh]), -1: (sl, -l[sl])}
    return b["piv"][k]


def target(piv_d, j, k, entry, sd, tp_mode, tp_r):
    """Long-space target. tp_mode 'near': NEAREST_LEVEL = the nearest confirmed swing high (pivots of the last
    NEAR_WIN bars, confirmed by bar j) at least tp_r x sd above entry; if none within NEAR_MAX_R x sd, use
    NEAR_FALLBACK_R x sd. tp_mode 'r': entry + tp_r x sd. Returns (tp, source)."""
    if tp_mode == "near":
        idx, val = piv_d
        a0 = np.searchsorted(idx, j - NEAR_WIN, side="right")
        a1 = np.searchsorted(idx, j - k, side="right")
        v = val[a0:a1]
        v = v[v >= entry + tp_r * sd]
        if len(v) and v.min() <= entry + NEAR_MAX_R * sd:
            return float(v.min()), "lvl"
        return entry + NEAR_FALLBACK_R * sd, "2R"
    return entry + tp_r * sd, "R"


def pin_bull(o, h, l, c, j, lvl, w_pin):
    """BULL_PIN(j, lvl) in long space: lower wick >= w_pin * range, l <= lvl < c."""
    rng = max(h[j] - l[j], 1e-9)
    return (min(o[j], c[j]) - l[j] >= w_pin * rng) and l[j] <= lvl and c[j] > lvl


def dedupe(df):
    """One decision per bar: drop bars where both directions fire (no directional tie-break), keep the first."""
    if not len(df):
        return df
    df = df.sort_values(["t", "d"], kind="stable").reset_index(drop=True)
    both = df.groupby("t")["d"].transform("nunique") > 1
    return df[~both].drop_duplicates("t", keep="first").reset_index(drop=True)
