"""
cand/smc5.py - "5-step" SMC engine (owner spec, 2026-10-01):
  1 trend      : H1 and M15 aligned. mode 'ema': close vs EMA(ema_n) and EMA slope (3 bars) agree on both;
                 mode 'structure': direction of the last M15 and H1 break of a confirmed swing agree.
  2 POI        : M15 order block of the impulse that broke structure: when an M15 close breaks the last confirmed
                 swing high (bullish BOS), the demand zone is the last bearish M15 candle of that up-leg, from its low
                 to max(open, close). Supply mirrored. Zones expire after `zone_age_h` hours.
  3 mitigation : the zone is "in play" from the first bar whose low trades into it (sells: high).
  4 entry      : liquidity sweep inside the zone on the entry timeframe (tf_e minutes): a bar's low takes out the last
                 confirmed entry-TF swing low (pivot ke) and the bar closes back above it -> BUY at that close.
  5 exits      : stop = sweep low ("protected low") - buf*ATR(entry TF); target = nearest confirmed M15 swing high above
                 the entry; trade only if reward >= min_rr * risk and risk <= max_risk_atr * ATR(M15).
A zone is consumed by its first entry, or killed by an M15 close beyond its far side. All inputs are causal
(pivots confirmed k bars late, HTF bars only once complete).
"""
import numpy as np
import pandas as pd

from data import atr, resample_causal
from features import ema

GRID = {}
WARMUP_BARS = 4000


def _confirmed_pivots(h, l, k):
    """Arrays of last confirmed swing high/low value (NaN if none) known at each bar (pivot p known at p+k)."""
    n = len(h)
    hmax = pd.Series(h).rolling(2 * k + 1, center=True).max().values
    lmin = pd.Series(l).rolling(2 * k + 1, center=True).min().values
    sh = pd.Series(np.where(h == hmax, h, np.nan)).shift(k).ffill().values
    sl = pd.Series(np.where(l == lmin, l, np.nan)).shift(k).ffill().values
    return sh, sl


def _trend(df, known_idx, mode, ema_n, k):
    c = df["c"].values
    if mode == "ema":
        e = ema(c, ema_n)
        es = np.r_[np.full(3, np.nan), e[:-3]]
        t = np.where((c > e) & (e > es), 1, np.where((c < e) & (e < es), -1, 0))
    else:
        sh, sl = _confirmed_pivots(df["h"].values, df["l"].values, k)
        brk = np.where(c > sh, 1, np.where(c < sl, -1, 0))
        t = pd.Series(np.where(brk != 0, brk, np.nan)).ffill().fillna(0).values.astype(int)
    return np.where(known_idx >= 0, t[np.maximum(known_idx, 0)], 0)


def signals(m1, trend_mode="ema", ema_n=50, k15=3, tf_e=5, ke=2, buf=0.2, min_rr=1.5, max_risk_atr=2.0,
            zone_age_h=48, hours=(0, 24), both=True):
    H1, k1 = resample_causal(m1, 60)
    M15, kq = resample_causal(m1, 15)
    E, kE = resample_causal(m1, tf_e)
    trend = _trend(H1, k1, trend_mode, ema_n, 3)
    t15 = _trend(M15, kq, trend_mode, ema_n, k15)
    trend = np.where(trend == t15, trend, 0)                       # step 1: aligned (per M1 index)
    A15 = atr(M15).astype(float)
    AE = atr(E).astype(float)
    kiE = np.searchsorted(kE, np.arange(len(E)), side="left")      # M1 index where each E bar is known
    hour = m1["hour"].values
    o15, h15, l15, c15 = (M15[x].values for x in ("o", "h", "l", "c"))
    sh15, sl15 = _confirmed_pivots(h15, l15, k15)
    oE, hE, lE, cE = (E[x].values for x in ("o", "h", "l", "c"))
    shE, slE = _confirmed_pivots(hE, lE, ke)
    out = []
    age_bars = int(zone_age_h * 4)
    # ---- step 2: M15 order-block zones created at each BOS (known at the close of the BOS bar)
    zones = []                                                     # (q_known, d, zlo, zhi)
    for q in range(1, len(M15)):
        if np.isfinite(sh15[q - 1]) and c15[q] > sh15[q - 1] and c15[q - 1] <= sh15[q - 1]:
            for j in range(q - 1, max(q - 20, 0), -1):             # last bearish candle of the up-leg
                if c15[j] < o15[j]:
                    zones.append((q, 1, l15[j], max(o15[j], c15[j])))
                    break
        if both and np.isfinite(sl15[q - 1]) and c15[q] < sl15[q - 1] and c15[q - 1] >= sl15[q - 1]:
            for j in range(q - 1, max(q - 20, 0), -1):
                if c15[j] > o15[j]:
                    zones.append((q, -1, min(o15[j], c15[j]), h15[j]))
                    break
    zq = np.array([z[0] for z in zones], dtype=np.int64)
    active = []                                                    # [q_known, d, zlo, zhi, in_play]
    zi = 0
    for r in range(ke + 2, len(E)):
        i = kiE[r]
        if i >= len(m1):
            break
        q = int(kq[i])                                             # last complete M15 bar at this E close
        while zi < len(zones) and zq[zi] <= q:
            z = zones[zi]
            active.append([z[0], z[1], z[2], z[3], False])
            zi += 1
        if not active:
            continue
        keep = []
        for z in active:
            qk, d, zlo, zhi, play = z
            if q - qk > age_bars:
                continue
            if (d > 0 and c15[q] < zlo) or (d < 0 and c15[q] > zhi):  # zone broken through: dead
                continue
            if not play and ((d > 0 and lE[r] <= zhi) or (d < 0 and hE[r] >= zlo)):
                z[4] = play = True                                 # step 3: mitigation
            fired = False
            if play and trend[i] == d and hours[0] <= hour[i] < hours[1] and np.isfinite(AE[r]):
                liq = slE[r - 1] if d > 0 else shE[r - 1]           # step 4: last confirmed E swing (liquidity)
                if np.isfinite(liq):
                    swept = (lE[r] < liq and cE[r] > liq) if d > 0 else (hE[r] > liq and cE[r] < liq)
                    near = (lE[r] <= zhi + 0.5 * A15[q]) if d > 0 else (hE[r] >= zlo - 0.5 * A15[q])
                    if swept and near:
                        entry = cE[r]
                        stop = (lE[r] - buf * AE[r]) if d > 0 else (hE[r] + buf * AE[r])
                        risk = (entry - stop) * d
                        tgt = sh15[q] if d > 0 else sl15[q]          # step 5: nearest M15 swing beyond entry
                        if np.isfinite(tgt) and risk > 0 and (tgt - entry) * d >= min_rr * risk \
                                and risk <= max_risk_atr * A15[q]:
                            out.append({"i": int(i), "d": int(d), "stop": float(stop), "tp": float(tgt),
                                        "zone": (float(zlo), float(zhi))})
                            fired = True
            if not fired:
                keep.append(z)
        active = keep
    return sorted(out, key=lambda x: x["i"])


def orders(m1, tmax=360, **kw):
    sig = signals(m1, **kw)
    ts = m1["ts"].values
    return [{"t": int(ts[s["i"]]) + 60_000, "d": s["d"], "kind": "mkt", "sl": s["stop"], "tp": s["tp"],
             "tmax": tmax * 60_000, "tag": "smc5"} for s in sig]
