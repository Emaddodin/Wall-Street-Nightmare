"""
xau_alpha/lib/sim.py
Execution simulator on 10-second bid/ask bars (Dukascopy ticks) with a broker cost model.

Order dict (decided at `t`, the ms timestamp at which the information used is complete, e.g. M1 ts + 60_000):
  t        decision time (ms). Nothing fills before the first 10s bar that opens at or after t.
  d        +1 long / -1 short
  kind     'mkt' | 'stop' | 'limit'   (pending orders need `px` and `exp`)
  px       trigger price for stop/limit (long stop fills when ask >= px, long limit when ask <= px; mirrored for shorts)
  exp      ms after which an unfilled pending order is cancelled
  sl       absolute stop price  OR  sl_dist (distance from the fill price)
  tp       absolute target price OR tp_dist (distance from the fill), NaN/None = no target
  be, be_off       move stop to entry+be_off once favourable excursion >= be (price units)
  trail, trail_act trail the stop `trail` behind the best exit-side price once excursion >= trail_act
  tmax     max holding time (ms); flat: absolute ms at which to flatten
  tag      free text
Conservative conventions: stops trigger on the exit side (long exits on bid), SL has priority over TP in the same bar,
the entry bar can stop out but cannot take profit, trailing/breakeven use only bars before the current one,
gaps fill at the bar open, stop and market exits pay `slip_exit`, market/stop entries pay `slip_entry`.
"""
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from data import load_s10, split_of

LOT_OZ = 100.0


@dataclass(frozen=True)
class Cost:
    spread_add: float = 0.0          # extra broker spread in price units on top of (mult x Dukascopy)
    spread_mult: float = 1.0         # multiplier on the Dukascopy spread (< 1 models a tighter ECN broker)
    spread_floor: float = 0.0        # minimum broker spread
    slip_entry: float = 0.05         # adverse slippage on market/stop entries
    slip_exit: float = 0.05          # adverse slippage on stop/market exits
    com_rt_lot: float = 0.0          # commission, $ per 1.0 lot round turn
    hour_add: tuple = field(default_factory=tuple)   # optional 24 extra-spread values by UTC hour

    @property
    def com_pts(self) -> float:
        return self.com_rt_lot / LOT_OZ


# Named cost models. Refined from xau_alpha/recon/broker_costs.md once measured.
# LiteFinance models from xau_alpha/recon/broker_costs.md + SYNTHESIS.md section 5: ECN spread ~0.22 (0.32 x Dukascopy,
# floor 0.22), wider after the daily reopen (22-00 UTC) and in Asia, $5/lot round-turn commission.
_H_BASE = tuple([0.25] + [0.08] * 6 + [0.0] * 15 + [0.25, 0.25])
_H_HARSH = tuple([0.20] + [0.0] * 21 + [0.20, 0.20])
COSTS = {
    "mid": Cost(spread_mult=0.0, slip_entry=0.0, slip_exit=0.0),                 # true zero cost (mid fills)
    "duka_raw": Cost(0, 1, 0, 0, 0, 0),                                          # raw Dukascopy bid/ask, no slippage
    "lf_base": Cost(spread_mult=0.32, spread_floor=0.22, slip_entry=0.05, slip_exit=0.10, com_rt_lot=5.0,
                    hour_add=_H_BASE),                                           # ~0.42/oz round trip 07-20 UTC
    "lf_harsh": Cost(spread_mult=1.0, spread_floor=0.30, slip_entry=0.20, slip_exit=0.40, com_rt_lot=7.0,
                     hour_add=_H_HARSH),                                         # ~1.3/oz round trip
}
COSTS["base"] = COSTS["lf_base"]     # selection model
COSTS["harsh"] = COSTS["lf_harsh"]   # go/no-go model

_BROKER_CACHE: dict = {}


def broker_arrays(cost: Cost) -> dict:
    key = cost
    if key in _BROKER_CACHE:
        return _BROKER_CACHE[key]
    _BROKER_CACHE.clear()
    s = load_s10()
    ts = s["ts"]
    s_bar = 0.5 * ((s["ao"] - s["bo"]) + (s["ac"] - s["bc"])).astype(np.float64)
    s_new = np.maximum(cost.spread_floor, cost.spread_mult * s_bar + cost.spread_add)
    if cost.hour_add:
        hr = ((ts // 3_600_000) % 24).astype(np.int64)
        s_new = s_new + np.asarray(cost.hour_add, dtype=np.float64)[hr]
    # shift each side by half the spread change (negative = a broker tighter than Dukascopy); applied lazily
    # per slice in _side() to avoid copying the arrays
    w = ((s_new - s_bar) / 2.0).astype(np.float32)
    out = dict(s)
    out["w"] = w
    _BROKER_CACHE[key] = out
    return out


def _side(arr: dict, d: int, a: int, b: int):
    """Entry-side (o,h,l) and exit-side (o,h,l) slices in 'long space' (shorts are negated and h/l swapped).
    Broker prices: ask + w, bid - w, where w is the per-bar half extra spread from the cost model."""
    w = arr["w"][a:b].astype(np.float64) if "w" in arr else 0.0
    ao, ah, al = (arr[c][a:b].astype(np.float64) + w for c in ("ao", "ah", "al"))
    bo, bh, bl = (arr[c][a:b].astype(np.float64) - w for c in ("bo", "bh", "bl"))
    if d > 0:
        return (ao, ah, al), (bo, bh, bl)
    return (-bo, -bl, -bh), (-ao, -al, -ah)


def _nan(x):
    return x is None or (isinstance(x, float) and math.isnan(x))


def _fill_entry(o, arr, k0, cost):
    """Return (k_fill, fill_price_long_space) or (None, None)."""
    d = o["d"]
    kind = o.get("kind", "mkt")
    ts = arr["ts"]
    n = len(ts)
    if k0 >= n:
        return None, None
    if kind == "mkt":
        if ts[k0] - o["t"] > o.get("max_delay", 60_000):     # data gap / market closed: do not fill stale
            return None, None
        (eo, _, _), _ = _side(arr, d, k0, k0 + 1)
        return k0, eo[0] + cost.slip_entry
    px = o["px"] * d
    exp = o["exp"]
    k1 = int(np.searchsorted(ts, exp, side="left"))
    k = k0
    step = 360
    while k < k1:
        b = min(k + step, k1)
        (eo, eh, el), _ = _side(arr, d, k, b)
        if kind == "stop":
            hit = np.flatnonzero(eh >= px)
            if len(hit):
                j = hit[0]
                return k + j, max(px, eo[j]) + cost.slip_entry
        else:
            hit = np.flatnonzero(el <= px)
            if len(hit):
                j = hit[0]
                return k + j, min(px, eo[j])
        k = b
        step = min(step * 4, 50_000)
    return None, None


def _manage(o, arr, kE, entry, cost):
    """Walk the position forward from the entry bar. Returns (k_exit, exit_px_long_space, reason, mfe, mae, sl0)."""
    d = o["d"]
    ts = arr["ts"]
    n = len(ts)
    sl = o.get("sl")
    sl0 = entry - o["sl_dist"] if _nan(sl) else sl * d
    tp = o.get("tp")
    if _nan(tp):
        tpd = o.get("tp_dist")
        tp = math.inf if _nan(tpd) else entry + tpd
    else:
        tp = tp * d
    be = o.get("be", math.nan)
    be_off = o.get("be_off", 0.0)
    trail = o.get("trail", math.nan)
    trail_act = o.get("trail_act", 0.0)
    t_entry = ts[kE]
    t_stop = min(o.get("flat", math.inf) or math.inf, t_entry + (o.get("tmax", math.inf) or math.inf))
    peak = -math.inf        # best exit-side high seen on bars BEFORE the current one
    trough = math.inf
    k = kE
    step = 360
    while k < n:
        b = min(k + step, n)
        _, (xo, xh, xl) = _side(arr, d, k, b)
        # stop level in force at each bar uses highs of previous bars only
        prev_peak = np.maximum.accumulate(np.r_[peak, xh[:-1]])
        stop = np.full(b - k, sl0)
        mfe_prev = prev_peak - entry
        if not _nan(be):
            stop = np.where(mfe_prev >= be, np.maximum(stop, entry + be_off), stop)
        if not _nan(trail):
            stop = np.where(mfe_prev >= trail_act, np.maximum(stop, prev_peak - trail), stop)
        stop = np.maximum.accumulate(stop)
        tsl = ts[k:b]
        time_hit = tsl >= t_stop
        sl_hit = xl <= stop
        tp_hit = xh >= tp
        if k == kE:
            tp_hit[0] = False
        ev = time_hit | sl_hit | tp_hit
        idx = np.flatnonzero(ev)
        if len(idx):
            j = idx[0]
            kk = k + j
            mfe = max(peak, xh[:j + 1].max()) - entry if j >= 0 else peak - entry
            mae = min(trough, xl[:j + 1].min()) - entry
            if time_hit[j]:
                return kk, xo[j] - cost.slip_exit, "time", mfe, mae, sl0
            if sl_hit[j]:
                lvl = stop[j]
                px = min(lvl, xo[j]) - cost.slip_exit
                reason = "sl" if lvl <= sl0 + 1e-9 else ("be" if (_nan(trail) or lvl <= entry + be_off + 1e-9) else "trail")
                return kk, px, reason, mfe, mae, sl0
            return kk, max(tp, xo[j]), "tp", mfe, mae, sl0
        peak = max(peak, xh.max())
        trough = min(trough, xl.min())
        k = b
        step = min(step * 4, 50_000)
    _, (xo, xh, xl) = _side(arr, d, n - 1, n)
    return n - 1, xo[0] - cost.slip_exit, "eod", peak - entry, trough - entry, sl0


def simulate(orders, cost: Cost = COSTS["base"], one_at_a_time: bool = True, arr: dict | None = None) -> pd.DataFrame:
    """Simulate a list of order dicts in time order. Returns one row per filled trade.
    `arr` overrides the broker arrays (tests); otherwise Dukascopy S10 bars widened by `cost`."""
    arr = broker_arrays(cost) if arr is None else arr
    ts = arr["ts"]
    orders = sorted(orders, key=lambda o: o["t"])
    busy_until = -1
    rows = []
    for o in orders:
        if one_at_a_time and o["t"] < busy_until:
            continue
        k0 = int(np.searchsorted(ts, o["t"], side="left"))
        kE, entry = _fill_entry(o, arr, k0, cost)
        if kE is None:
            continue
        kX, exit_px, reason, mfe, mae, sl0 = _manage(o, arr, kE, entry, cost)
        d = o["d"]
        risk = entry - sl0
        pnl = exit_px - entry - cost.com_pts
        rows.append({
            "t_sig": o["t"], "t_in": int(ts[kE]), "t_out": int(ts[kX]) + 10_000, "d": d,
            "entry": entry * d, "exit": exit_px * d, "sl0": sl0 * d, "risk": risk, "pnl": pnl,
            "R": pnl / risk if risk > 0 else np.nan, "mfe": mfe, "mae": mae, "reason": reason,
            "tag": o.get("tag", ""),
        })
        busy_until = int(ts[kX]) + 10_000
    df = pd.DataFrame(rows)
    if len(df):
        df["split"] = split_of(df["t_in"].values)
        df["day"] = pd.to_datetime(df["t_in"], unit="ms", utc=True).dt.tz_convert("America/New_York").add(
            pd.Timedelta(hours=7)).dt.strftime("%Y-%m-%d")
    return df


def stats(tr: pd.DataFrame) -> dict:
    """Point/R statistics for a trade table (pnl in price units per 1 oz)."""
    if tr is None or len(tr) == 0:
        return {"n": 0}
    p = tr["pnl"].values
    R = tr["R"].values
    gw, gl = p[p > 0].sum(), -p[p < 0].sum()
    eq = np.cumsum(R)
    dd = (np.maximum.accumulate(np.r_[0, eq])[1:] - eq).max()
    days = tr["day"].nunique()
    return {
        "n": int(len(tr)), "wr": round(float((p > 0).mean()), 3),
        "pf": round(float(gw / gl), 3) if gl > 0 else float("inf"),
        "avg_pts": round(float(p.mean()), 3), "avg_R": round(float(np.nanmean(R)), 3),
        "sum_R": round(float(np.nansum(R)), 1), "maxdd_R": round(float(dd), 1),
        "avg_risk": round(float(tr["risk"].mean()), 2), "tpd": round(len(tr) / max(days, 1), 2),
    }


def split_stats(tr: pd.DataFrame, include_test: bool = False) -> dict:
    out = {}
    names = ["train", "valid", "test"]
    for s in (0, 1, 2) if include_test else (0, 1):
        out[names[s]] = stats(tr[tr["split"] == s]) if len(tr) else {"n": 0}
    return out
