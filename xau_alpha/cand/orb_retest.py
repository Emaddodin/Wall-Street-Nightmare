"""
cand/orb_retest.py - Hypothesis H6 (SYNTHESIS.md section 6): opening-range break, at-break vs retest+trigger entry.

Idea (SOURCED web_research 2.7: Holmberg 2013, Tsai 2019 for oil/index futures): the first break of an opening range
(OR) continues. The video-style variant waits for price to come back to the broken OR edge and prints a confirmation
candle before entering. Plain M1 breakout drift is already known to be small (SOURCED HANDOFF l.34), so the at-break
arm is expected to fail (OPINION in SYNTHESIS).

WINDOWS (clock minute of day, [start, end) ; break search in [end, end + brk_h) on the same trading day)
  lon    08:00-08:29 London local  (lon_mod 480..509)                 flat 16:00 UTC
  comex  08:20-08:34 ET            (ny_mod 500..514), only on days WITHOUT a HIGH or MEDIUM calendar row at 08:30 ET;
                                                                       flat 12:00 ET (16:00 UTC in summer, DST-consistent)
  asia   00:00-06:59 UTC           (mod 0..419)                        flat 16:00 UTC
  a+b    union of windows (e.g. 'lon+comex'): signals pooled, one position at a time
  rand   NULL: a random 30-minute window per trading day with start drawn uniformly in [01:00, 15:30) UTC
         (seed `rseed`), on the days where window `match` is valid; flat = window end + `rand_hz` minutes (cap 20:30 UTC)
  A day's OR is valid if >= 80% of the window's minutes have a bar.

RULES (long side; shorts are the exact mirror: the same code runs on the negated series o'=-o, h'=-l, l'=-h, c'=-c
with OR_hi'=-OR_lo, so both sides share identical parameters and there is no directional veto)
  ORw          OR_hi - OR_lo
  FILTER       filt=1: x = ORw / ATRd must lie within [p20, p80] of x over the prior 60 valid days of the same window
               (ATRd = mean daily true range of the prior 20 trading days, from M1 mid). filt=0: none.
  BREAK        the first M1 close beyond OR_hi + kap*ORw (long) or below OR_lo - kap*ORw (short) within brk_h minutes
               of the window end. Only the first break of the day per window is used (either side).
  ENTRY        mode 'brk': market at the break close (t = ts[i0] + 60 s).
               mode 'rt' : retest within R bars: the first bar i1 in (i0, i0+R] with l[i1] <= OR_hi + tau*A, and no
                           close below the OR midpoint in (i0, i1]; then a trigger bar j in [i1, i1+2] with
                           TRIG_BULL(j) = BULL_ENGULF(j) or BULL_PIN(j, lvl) (lower wick >= w_pin*range, l <= OR_hi + tau*A,
                           c > OR_hi), and c[j] > OR_hi, with no close below the OR midpoint in (i0, j]. Market at ts[j] + 60 s.
                           If no trigger on the first touch, no trade.
               mode 'nt' : control arm (no trigger; entry 'nt5'/'nt30'): market at the close of the touch bar i1
                           if c[i1] > OR_hi.
               entry in {'brk', 'rt5', 'rt30', 'nt5', 'nt30'} encodes (mode, R).
  STOP         stop='mid': OR midpoint ; stop='far': OR_lo - pad*A[i0]  (pad default 0.1; stage 2 adds 0.25)
  TARGET       tp=1 / 2: entry-decision close + tp * (close - stop) ; tp=0: none (time exit only)
  EXIT         flat at the window's flat clock; optional break-even `be` (in R: at +be*R move the stop to entry + 0.45 $),
               optional time stop tmax (minutes, 0 = off).
               flat_utc (stage 2): override the flat minute (UTC) for windows whose flat clock is UTC (lon, asia);
               the comex window keeps 12:00 ET. None = the window default (16:00 UTC).
  NEWS         no entry within [T-nb, T+nb] min of a HIGH calendar row (nb default 30; SYNTHESIS N2 for equity < $21).
               nexit (stage 2; -1 = off): if a HIGH row T falls after the entry decision and before the flat time,
               flatten at T - nexit minutes (skip the signal if that is not at least 1 minute after the decision).
               The calendar is a scheduled (ex-ante) list, so both rules are causal.
  CAP          cap='none' (the >= $100 account) | 'skip' (drop signals whose stop distance from the decision close is
               outside the flip band [1.2, 4.0] $/oz).

Units: kap is in OR widths, tau and the stop pad in A = M1 Wilder ATR14 (data.atr); only the flip band and the 0.45 $
break-even offset (cost) are in $/oz. All arrays are cut at the TEST start (2026-06-01) before any computation, so no
holdout bar is used or emitted.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from data import TEST, _ms, atr, load_calendar  # noqa: E402
from features import engulf  # noqa: E402

TEST_MS = _ms(TEST[0])
FLIP_BAND = (1.2, 4.0)
BE_OFF = 0.45                      # break-even offset: entry + ~round-trip cost

# clock: which minute-of-day column; start/end: [start, end) window; flat: (clock, minute)
WINDOWS = {
    "lon": dict(clock="lon", start=480, end=510, flat=("utc", 960)),
    "comex": dict(clock="ny", start=500, end=515, flat=("ny", 720), no_release=True),
    "asia": dict(clock="utc", start=0, end=420, flat=("utc", 960)),
}

# Stage-1 grid (SYNTHESIS H6): window(3) x filter(2) x kap(3) x entry mode(3) x stop(2) x TP(3) = 324
# entry mode (3) = at-break, retest+trigger with R=5, retest+trigger with R=30 -> encoded as (mode, R)
GRID = {
    "window": ["lon", "comex", "asia"],
    "filt": [0, 1],
    "kap": [0.0, 0.1, 0.25],
    "entry": ["brk", "rt5", "rt30"],
    "stop": ["mid", "far"],
    "tp": [0, 1, 2],
}

DEFAULTS = dict(window="lon", filt=0, kap=0.1, entry="rt30", stop="mid", tp=1, brk_h=120, tau=0.1, w_pin=0.6,
                be=0.0, tmax=0, nb=30, cap="none", rseed=0, match="lon", rand_hz=480, pad=0.1, flat_utc=None,
                nexit=-1)

_C: dict = {}


# ---------------------------------------------------------------------------------------------------------------
# cached base arrays (per process)

def _base(m1):
    key = (len(m1), int(m1["ts"].values[0]), int(m1["ts"].values[-1]))
    if _C.get("key") == key:
        return _C["b"]
    _C.clear()
    ts_all = m1["ts"].values
    n = int(np.searchsorted(ts_all, TEST_MS, side="left"))          # holdout cut: nothing at/after TEST start
    mm = m1.iloc[:n]
    ts = mm["ts"].values.astype(np.int64)
    o, h, l, c = (mm[k].values.astype(np.float64) for k in "ohlc")
    A = atr(mm)
    clocks = {"utc": mm["mod"].values.astype(np.int64), "ny": mm["ny_mod"].values.astype(np.int64),
              "lon": mm["lon_mod"].values.astype(np.int64)}
    dcode, days = pd.factorize(mm["tday"].values, sort=True)
    g = pd.DataFrame({"d": dcode, "h": h, "l": l, "c": c, "i": np.arange(n)}).groupby("d", sort=True)
    D = pd.DataFrame({"H": g.h.max(), "L": g.l.min(), "C": g.c.last(), "first": g.i.first(), "last": g.i.last()})
    pc = D["C"].shift(1)
    D["TR"] = np.maximum(D["H"], pc.fillna(D["H"])) - np.minimum(D["L"], pc.fillna(D["L"]))
    atrd = D["TR"].shift(1).rolling(20, min_periods=10).mean().values        # prior days only
    bull_e, bear_e = engulf(o, c)
    cal = load_calendar()
    hi_ev = np.sort(cal.loc[cal["impact"] == "HIGH", "ts"].values.astype(np.int64))
    # days (NY date == tday for daytime bars) with a HIGH/MEDIUM row at 08:30 ET
    ev = cal[cal["impact"].isin(["HIGH", "MEDIUM"])]
    evny = pd.to_datetime(ev["ts"].values, unit="ms", utc=True).tz_convert("America/New_York")
    rel830 = set(evny[(evny.hour == 8) & (evny.minute == 30)].strftime("%Y-%m-%d"))
    no_rel = np.array([d not in rel830 for d in days])
    b = dict(n=n, ts=ts, o=o, h=h, l=l, c=c, A=A, clocks=clocks, dcode=dcode, days=np.asarray(days), D=D,
             atrd=atrd, bull_e=bull_e, bear_e=bear_e, hi_ev=hi_ev, no_rel=no_rel)
    _C["key"], _C["b"] = key, b
    return b


def _windows_table(b, window, brk_h, rseed=0, match="lon", rand_hz=480, flat_utc=None):
    """One row per trading day with a valid OR: OR_hi/lo, last window bar e, break-search index range, flat ms."""
    ck = ("win", window, brk_h, rseed if window == "rand" else None, match if window == "rand" else None, rand_hz,
          None if flat_utc is None else int(flat_utc))
    if ck in _C:
        return _C[ck]
    nd = len(b["D"])
    dcode = b["dcode"]
    if window == "rand":
        base = _windows_table(b, match, brk_h)                  # days where the matched real window is valid
        valid_days = np.zeros(nd, dtype=bool)
        valid_days[base["day"].values] = True
        rng = np.random.default_rng(7919 + int(rseed))
        start = rng.integers(60, 930, size=nd)                  # [01:00, 15:30) UTC
        clock = "utc"
        cl = b["clocks"]["utc"]
        st = start[dcode]
        inwin = (cl >= st) & (cl < st + 30) & valid_days[dcode]
        inbrk = (cl >= st + 30) & (cl < st + 30 + brk_h)
        wlen = 30
        flat_min = np.minimum(start + 30 + rand_hz, 1230)       # cap 20:30 UTC
    else:
        W = WINDOWS[window]
        clock = W["clock"]
        cl = b["clocks"][clock]
        inwin = (cl >= W["start"]) & (cl < W["end"])
        if W.get("no_release"):
            inwin &= b["no_rel"][dcode]
        inbrk = (cl >= W["end"]) & (cl < W["end"] + brk_h)
        wlen = W["end"] - W["start"]
    idx = np.arange(b["n"])
    g = pd.DataFrame({"d": dcode[inwin], "h": b["h"][inwin], "l": b["l"][inwin], "i": idx[inwin]}).groupby("d")
    T = pd.DataFrame({"hi": g.h.max(), "lo": g.l.min(), "e": g.i.last(), "cnt": g.i.size()})
    gb = pd.DataFrame({"d": dcode[inbrk], "i": idx[inbrk]}).groupby("d")
    T = T.join(pd.DataFrame({"f": gb.i.first(), "g": gb.i.last()}), how="inner")
    T = T[(T["cnt"] >= 0.8 * wlen) & (T["f"] > T["e"])]
    T["day"] = T.index.values
    # flat time (ms): open of the first bar AFTER the window with clock >= the flat minute on that day, else the
    # day's last bar close (the trading day starts at 17:00/18:00 ET, so the search must start after the window)
    e_of_day = np.full(nd, np.iinfo(np.int64).max, dtype=np.int64)
    e_of_day[T["day"].values] = T["e"].values
    if window == "rand":
        fcl, fmin = b["clocks"]["utc"], flat_min[dcode]
    else:
        fc, fm = WINDOWS[window]["flat"]
        if flat_utc is not None and fc == "utc":
            fm = int(flat_utc)
        fcl, fmin = b["clocks"][fc], fm
    sel = (fcl >= fmin) & (idx > e_of_day[dcode])
    s = pd.Series(idx[sel]).groupby(dcode[sel]).first()
    fl = np.full(nd, -1, dtype=np.int64)
    fl[s.index.values] = s.values
    last = b["D"]["last"].values
    flat_ms = np.where(fl >= 0, b["ts"][np.maximum(fl, 0)], b["ts"][last] + 60_000)
    T["flat"] = flat_ms[T["day"].values]
    T["atrd"] = b["atrd"][T["day"].values]
    T["w"] = T["hi"] - T["lo"]
    x = (T["w"] / T["atrd"]).values
    xs = pd.Series(x)
    q20 = xs.shift(1).rolling(60, min_periods=30).quantile(0.2).values
    q80 = xs.shift(1).rolling(60, min_periods=30).quantile(0.8).values
    T["filt_ok"] = (x >= q20) & (x <= q80)
    T = T[T["w"] > 0].reset_index(drop=True)
    _C[ck] = T
    return T


# ---------------------------------------------------------------------------------------------------------------

def _ls(o, h, l, c, k, d):
    """Bar k in long space (shorts: negated and h/l swapped)."""
    if d > 0:
        return o[k], h[k], l[k], c[k]
    return -o[k], -l[k], -h[k], -c[k]


def signals(m1, **params):
    """One row per trade signal (before the simulator's one-position-at-a-time filter)."""
    p = {**DEFAULTS, **params}
    b = _base(m1)
    brk_h = int(p["brk_h"])
    fu = p["flat_utc"]
    fu = None if fu is None or (isinstance(fu, float) and np.isnan(fu)) else int(fu)
    T = _windows_table(b, p["window"], brk_h, p["rseed"], p["match"], int(p["rand_hz"]), fu)
    pad, nexit = float(p["pad"]), int(p["nexit"])
    ts, o, h, l, c, A = b["ts"], b["o"], b["h"], b["l"], b["c"], b["A"]
    kap, tau, w_pin = float(p["kap"]), float(p["tau"]), float(p["w_pin"])
    entry = str(p["entry"])
    if entry == "brk":
        mode, R = "brk", 0
    elif entry.startswith("nt"):              # control arm: retest without trigger, R = int(entry[2:])
        mode, R = "rt0", int(entry[2:])
    else:
        mode, R = "rt", int(entry[2:])
    stop_mode, tp_m = str(p["stop"]), float(p["tp"])
    hi_ev, nb = b["hi_ev"], int(p["nb"]) * 60_000
    lo_b, hi_b = FLIP_BAND
    day_last = b["D"]["last"].values
    rows = []
    for r in T.itertuples(index=False):
        if int(p["filt"]) and not r.filt_ok:
            continue
        f, g = int(r.f), int(r.g)
        cc = c[f:g + 1]
        up = np.flatnonzero(cc > r.hi + kap * r.w)
        dn = np.flatnonzero(cc < r.lo - kap * r.w)
        iu = up[0] if len(up) else 10 ** 9
        idn = dn[0] if len(dn) else 10 ** 9
        if iu == idn:                           # no break
            continue
        d = 1 if iu < idn else -1
        i0 = f + min(iu, idn)
        # long space: shorts use the negated series (o'=-o, h'=-l, l'=-h, c'=-c), OR_hi'=-OR_lo
        if d > 0:
            HI, LO, eng = r.hi, r.lo, b["bull_e"]
        else:
            HI, LO, eng = -r.lo, -r.hi, b["bear_e"]
        mid = 0.5 * (HI + LO)
        dl = int(day_last[r.day])
        if mode == "brk":
            j = i0
        else:
            j = -1
            lvl = HI + tau * A[i0]
            for i1 in range(i0 + 1, min(i0 + R, dl) + 1):
                o1, h1, l1, c1 = _ls(o, h, l, c, i1, d)
                if c1 < mid:                    # break failed deep into the range: setup cancelled
                    break
                if l1 > lvl:
                    continue
                # first touch of the broken edge
                if mode == "rt0":               # control arm: no trigger
                    if c1 > HI:
                        j = i1
                    break
                for jj in range(i1, min(i1 + 2, dl) + 1):
                    oj, hj, lj, cj = _ls(o, h, l, c, jj, d)
                    if cj < mid:
                        break
                    rng = max(hj - lj, 1e-9)
                    pin_ok = (min(oj, cj) - lj) >= w_pin * rng and lj <= lvl
                    if (eng[jj] or pin_ok) and cj > HI:
                        j = jj
                        break
                break
            if j < 0:
                continue
        t = int(ts[j]) + 60_000
        if t >= r.flat or t >= TEST_MS:
            continue
        if nb > 0 and len(hi_ev):
            k = np.searchsorted(hi_ev, t - nb, side="left")
            if k < len(hi_ev) and hi_ev[k] <= t + nb:
                continue
        fl = int(r.flat)
        if nexit >= 0 and len(hi_ev):
            k2 = np.searchsorted(hi_ev, t, side="right")           # first HIGH row strictly after the decision
            if k2 < len(hi_ev) and hi_ev[k2] - nexit * 60_000 < fl:
                fl = int(hi_ev[k2] - nexit * 60_000)
                if fl < t + 60_000:
                    continue
        cj = c[j] * d
        sl_ls = mid if stop_mode == "mid" else LO - pad * A[i0]
        risk = cj - sl_ls
        if not np.isfinite(risk) or risk <= 0:
            continue
        if p["cap"] == "skip" and not (lo_b <= risk <= hi_b):
            continue
        tp_ls = cj + tp_m * risk if tp_m > 0 else np.nan
        rows.append((t, d, sl_ls * d, tp_ls * d if tp_m > 0 else np.nan, fl, risk, r.w, int(r.day),
                     float(A[j]), mode))
    return pd.DataFrame(rows, columns=["t", "d", "sl", "tp", "flat", "risk", "orw", "day", "A", "mode"])


def orders(m1, **params):
    """Order dicts for sim.simulate. window may be a union such as 'lon+comex' (signals of each window pooled; the
    simulator's one-position-at-a-time rule then applies across windows)."""
    p = {**DEFAULTS, **params}
    wins = str(p["window"]).split("+")
    df = pd.concat([signals(m1, **{**p, "window": w}).assign(win=w) for w in wins], ignore_index=True)
    df = df.sort_values("t", kind="stable")
    be = float(p["be"])
    tmax = int(p["tmax"])
    out = []
    for r in df.itertuples(index=False):
        od = {"t": int(r.t), "d": int(r.d), "kind": "mkt", "sl": float(r.sl), "flat": int(r.flat),
              "tag": f"orb_{r.win}_{r.mode}{'L' if r.d > 0 else 'S'}"}
        if np.isfinite(r.tp):
            od["tp"] = float(r.tp)
        if be > 0:
            od["be"], od["be_off"] = be * float(r.risk), BE_OFF
        if tmax > 0:
            od["tmax"] = tmax * 60_000
        out.append(od)
    return out
