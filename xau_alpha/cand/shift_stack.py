"""
cand/shift_stack.py - Mr P Fx "buyer's / seller's shift" + doubling stack (video mKP7TowaO5M, 2026-09-27).

SETUP on M5 (long; shorts mirror on the negated series):
  1 direction : a down-leg: the last `look` M5 bars made a new low at least `drop` x A5 below their start
  2 break     : an M5 close above the most recent confirmed swing high (lower-high structure broken)
  3 momentum  : within `mwin` bars of the break, `nbull` consecutive bullish M5 closes AND a leg >= `mom` x A5
  4 pullback  : price then falls back at least `pb` of that momentum leg
  5 level     : the nearest confirmed swing low of the last 48 h (576 M5 bars) that is BELOW the break level and has a
                lower wick >= `wick` of its range ("major rejection"); price must tap level +- tol x A5
TRIGGER on M1: a bullish engulfing closing while the M1 low of the last 3 bars touched the level zone.
ENTRY at the engulfing close; stop = min(low of last 3 M1 bars, level) - buf x A1.
STACK: each time a new M1 swing low (pivot k=2, confirmed) forms ABOVE the previous one (higher low) and price closes
back above it, add a position of 2x the last lot (capped by free margin at 1:500) and move the stop of ALL positions
to that higher low - buf x A1. Basket exits on the stop, on `tgt_R` x initial risk (0 = none), or at flat time.
All decisions are causal (confirmed pivots, completed bars; see tests in reports/shift_stack.md).
"""
import math

import numpy as np
import pandas as pd

from data import atr, resample_causal, news_block_mask
from features import engulf
from sim import COSTS, broker_arrays

LOT_OZ = 100.0
GRID = {}
WARMUP_BARS = 4000   # 48 h of M5 levels + look-back


def _pivots_idx(h, l, k):
    """Confirmed pivot highs/lows: arrays of (pivot_index) and confirm index = p + k."""
    n = len(h)
    hmax = pd.Series(h).rolling(2 * k + 1, center=True).max().values
    lmin = pd.Series(l).rolling(2 * k + 1, center=True).min().values
    ph = np.array([p for p in np.flatnonzero(h == hmax) if k <= p < n - k])
    pl = np.array([p for p in np.flatnonzero(l == lmin) if k <= p < n - k])
    return ph, pl


def signals(m1, look=48, drop=3.0, mwin=12, nbull=3, mom=2.0, pb=0.5, wick=0.4, tol=0.3, buf=0.3,
            news=15, hours=(0, 24), both=True, tf=5, lvl_h=48, funnel=None, reentry=False, reentry_win=20):
    """Return entry signals: list of dicts {i (M1 index of trigger close), d, stop, level}."""
    htf, known = resample_causal(m1, tf)
    lvl_bars = int(lvl_h * 60 / tf)
    pb_bars = int(8 * 60 / tf)
    A5 = atr(htf).astype(float)
    A1 = atr(m1)
    ki = np.searchsorted(known, np.arange(len(htf)), side="left")
    o1, h1, l1, c1 = (m1[c].values for c in "ohlc")
    eb, es = engulf(o1, c1)
    blocked = news_block_mask(m1["ts"].values, news, news) if news else np.zeros(len(m1), bool)
    hour = m1["hour"].values
    out = []
    for d in ((1, -1) if both else (1,)):
        s = 1.0 if d > 0 else -1.0
        # long space: for shorts negate and swap high/low
        H = htf["h"].values * s if d > 0 else -htf["l"].values
        L = htf["l"].values * s if d > 0 else -htf["h"].values
        O = htf["o"].values * s
        C = htf["c"].values * s
        ph, pl = _pivots_idx(H, L, 3)
        rng = H - L
        lw = np.minimum(O, C) - L
        m_h = h1 * s if d > 0 else -l1
        m_l = l1 * s if d > 0 else -h1
        trig = eb if d > 0 else es
        state = None
        last_sig_k = -10_000
        for k in range(look + 5, len(htf)):
            i_known = ki[k]
            if i_known >= len(m1):
                break
            a = A5[k]
            if not np.isfinite(a) or a <= 0:
                continue
            # step 1+2: break of the latest confirmed swing high after a down-leg
            if state is None:
                hs = ph[(ph + 3) <= k]
                if len(hs) < 2:
                    continue
                sh = hs[-1]
                if C[k] > H[sh] and C[k - 1] <= H[sh]:
                    w0 = k - look
                    if H[w0:k].max() - L[w0:k].min() >= drop * a and L[w0:k].argmin() > H[w0:k].argmax():
                        state = {"brk": H[sh], "k_brk": k, "low1": L[w0:k].min()}
                        if funnel is not None: funnel["2_break"] = funnel.get("2_break", 0) + 1
                continue
            # step 3: momentum
            if "mom_hi" not in state:
                if k - state["k_brk"] > mwin:
                    state = None
                    continue
                up = (C[k - nbull + 1:k + 1] > O[k - nbull + 1:k + 1]).all()
                leg = H[k] - L[state["k_brk"] - 3:k + 1].min()
                if up and leg >= mom * a:
                    state["mom_hi"] = H[k]
                    state["mom_lo"] = L[state["k_brk"] - 3:k + 1].min()
                    state["k_mom"] = k
                    if funnel is not None: funnel["3_momentum"] = funnel.get("3_momentum", 0) + 1
                continue
            state["mom_hi"] = max(state["mom_hi"], H[k])
            if k - state["k_mom"] > pb_bars:      # 8 h to complete the pullback
                state = None
                continue
            # step 4: pullback of pb x leg
            if state["mom_hi"] - L[k] < pb * (state["mom_hi"] - state["mom_lo"]):
                continue
            # step 5: major rejection level of the last 48 h below the break level
            if "level" not in state:
                cand = pl[((pl + 3) <= k) & (pl >= k - lvl_bars)]
                cand = [p for p in cand if L[p] < state["brk"] and rng[p] > 0 and lw[p] >= wick * rng[p]]
                if not cand:
                    state = None
                    continue
                state["level"] = max(L[p] for p in cand)     # nearest below the break
                if funnel is not None: funnel["4+5_pullback_level"] = funnel.get("4+5_pullback_level", 0) + 1
            lvl = state["level"]
            if L[k] < lvl - 2 * tol * a:                      # smashed through: invalid
                state = None
                continue
            if L[k] > lvl + tol * a:
                continue
            if funnel is not None: funnel["5_tap"] = funnel.get("5_tap", 0) + 1
            # tap: look for the M1 engulfing trigger in the next 5m bar's minutes (known after this M5 closes)
            i0 = i_known + 1
            for i in range(i0, min(i0 + 15, len(m1))):
                if trig[i] and m_l[max(i - 2, 0):i + 1].min() <= lvl + tol * a and not blocked[i] \
                        and hours[0] <= hour[i] < hours[1] and k > last_sig_k:
                    stop_ls = min(m_l[max(i - 2, 0):i + 1].min(), lvl) - buf * A1[i]
                    out.append({"i": i, "d": d, "stop": stop_ls * s if d > 0 else -stop_ls, "level": lvl * s if d > 0 else -lvl})
                    last_sig_k = k + 12
                    break
            state = None
    out = sorted(out, key=lambda x: x["i"])
    if reentry:
        out = _add_reentries(out, o1, h1, l1, c1, eb, es, A1, blocked, hour, hours, buf, reentry_win)
    return out


def _add_reentries(sig, o1, h1, l1, c1, eb, es, A1, blocked, hour, hours, buf, win):
    """Mr P Fx persistence: if a signal's stop is hit within `win` minutes, re-enter ONCE on the next engulfing candle
    in the same direction within `win` minutes of the stop-out, stop under/over the new extreme (same rule as entry)."""
    extra = []
    n = len(c1)
    for s in sig:
        i, d, st = s["i"], s["d"], s["stop"]
        j_stop = None
        for j in range(i + 1, min(i + 1 + win, n)):
            if (d > 0 and l1[j] <= st) or (d < 0 and h1[j] >= st):
                j_stop = j
                break
        if j_stop is None:
            continue
        trig = eb if d > 0 else es
        for j in range(j_stop + 1, min(j_stop + 1 + win, n)):
            if trig[j] and not blocked[j] and hours[0] <= hour[j] < hours[1]:
                ext = l1[max(j - 3, 0):j + 1].min() if d > 0 else h1[max(j - 3, 0):j + 1].max()
                new_stop = ext - buf * A1[j] if d > 0 else ext + buf * A1[j]
                extra.append({"i": int(j), "d": d, "stop": float(new_stop), "level": s["level"], "reentry": True})
                break
    return sorted(sig + extra, key=lambda x: x["i"])


def backtest(m1, sig, eq0=13.0, cost="lf_base", lev=500.0, max_adds=6, tgt_R=0.0, flat_min=300,
             stop_floor=0.8, stop_cap=4.0, final=False, until_ms=None, piv_k=2, add_after_R=0.0, be_after_R=0.0,
             add_mult=2.0, trail_buf=0.3, lock_eq=0.0, add_mode="mult", rf_budget=0.0):
    """
    Account-level basket simulation on 10-second bid/ask bars. Returns (trades DataFrame, equity path list).
    One basket at a time; lots double on each add, capped by free margin (equity incl. floating P&L).
    """
    arr = broker_arrays(COSTS[cost])
    ts = arr["ts"]
    c = COSTS[cost]
    w = arr["w"]
    A1 = atr(m1)
    mts = m1["ts"].values
    eq = eq0
    busy_until = -1
    rows = []
    for sg in sig:
        i = sg["i"]
        t = int(mts[i]) + 60_000
        if until_ms and t >= until_ms:
            break
        if t < busy_until:
            continue
        d = sg["d"]
        k = int(np.searchsorted(ts, t))
        if k >= len(ts) or ts[k] - t > 60_000:
            continue
        ask = lambda j: float(arr["ao"][j] + w[j])
        bid = lambda j: float(arr["bo"][j] - w[j])
        entry = (ask(k) + c.slip_entry) if d > 0 else (bid(k) - c.slip_entry)
        risk = (entry - sg["stop"]) * d
        if not (stop_floor <= risk <= stop_cap):
            continue
        price = entry
        m_lot = LOT_OZ * price / lev
        if eq < 0.01 * m_lot * 1.02:
            rows.append({"t": t, "d": d, "skip": "margin", "eq": eq})
            continue
        pos = [(0.01, entry)]
        stop = sg["stop"]
        last_lot = 0.01
        adds = 0
        # M1 pivots for higher lows after entry (confirmed k=2)
        j1 = i + 1
        piv_prev = sg["stop"] * d
        t_flat = t + flat_min * 60_000
        reason = None
        kk = k
        n_adds = 0
        exit_px = None
        # walk minute by minute (decisions on M1 closes), exits checked on 10s bars inside each minute
        while j1 < len(m1) - 3:
            m_start = int(mts[j1])
            m_end = m_start + 60_000
            k_a = int(np.searchsorted(ts, max(m_start, t)))
            k_b = int(np.searchsorted(ts, m_end))
            for q in range(max(k_a, kk), k_b):
                xl = (arr["bl"][q] - w[q]) if d > 0 else -(arr["ah"][q] + w[q])
                xo = (arr["bo"][q] - w[q]) if d > 0 else -(arr["ao"][q] + w[q])
                if ts[q] >= t_flat:
                    reason, exit_px = "time", (xo - c.slip_exit) * d
                    break
                if xl <= stop * d:
                    reason, exit_px = "stop", (min(stop * d, xo) - c.slip_exit) * d
                    break
                if tgt_R > 0:
                    xh = (arr["bh"][q] - w[q]) if d > 0 else -(arr["al"][q] + w[q])
                    if xh >= (entry + d * tgt_R * risk) * d:
                        reason, exit_px = "target", entry + d * tgt_R * risk
                        break
            if reason:
                kk = q
                break
            kk = k_b
            # on the close of minute j1: confirmed M1 higher low -> add + trail
            cl_ls = m1["c"].values[j1] * d
            mfeR = (cl_ls - entry * d) / risk
            if be_after_R > 0 and mfeR >= be_after_R:
                lots_open = sum(x[0] for x in pos)
                be = sum(x[0] * x[1] for x in pos) / lots_open + d * (c.com_rt_lot / LOT_OZ + 0.3)
                if (be - stop) * d > 0:
                    stop = be
            p = j1 - piv_k
            if p > i:
                lo_ls = (m1["l"].values * d) if d > 0 else -m1["h"].values
                win = lo_ls[p - piv_k:p + piv_k + 1]
                if lo_ls[p] == win.min() and lo_ls[p] > piv_prev + 0.05:
                    cl = m1["c"].values[j1] * d
                    if cl > lo_ls[p]:
                        new_stop = (lo_ls[p] - trail_buf * A1[j1]) * d
                        if (new_stop - stop) * d > 0:
                            stop = new_stop
                        piv_prev = lo_ls[p]
                        if adds < max_adds and mfeR >= add_after_R:
                            qk = min(k_b, len(ts) - 1)
                            px = (ask(qk) + c.slip_entry) if d > 0 else (bid(qk) - c.slip_entry)
                            lots_open = sum(x[0] for x in pos)
                            floating = sum(x[0] * LOT_OZ * (px - x[1]) * d for x in pos)
                            free = eq + floating - lots_open * m_lot
                            can = math.floor(free / (m_lot * 1.02) / 0.01 + 1e-9) * 0.01
                            if add_mode == "double":
                                lot = round(min(can, round(last_lot * 2, 2)), 2)   # video: 2x, only margin caps
                            elif add_mode == "riskfree":
                                # largest add that keeps the basket P&L at the (trailed) stop >= -rf_budget x first risk $
                                exist = sum(x[0] * LOT_OZ * (stop - x[1]) * d for x in pos)
                                per_lot = LOT_OZ * max((px - stop) * d, 0.05)
                                budget = exist + rf_budget * 0.01 * LOT_OZ * risk
                                allowed = math.floor(max(budget, 0) / per_lot / 0.01 + 1e-9) * 0.01
                                lot = round(min(can, allowed), 2)
                            else:
                                want = round(max(0.01, last_lot * add_mult), 2)
                                lot = round(min(want, can), 2)
                            # the add must not turn the basket into a loss if the new stop is hit
                            if lot >= 0.01:
                                test = pos + [(lot, px)]
                                at_stop = sum(x[0] * LOT_OZ * (stop - x[1]) * d for x in test)
                                if add_mode in ("riskfree", "double") or at_stop > -0.5 * eq * 0.25:
                                    pos = test
                                    last_lot = lot
                                    adds += 1
            j1 += 1
        if exit_px is None:
            break
        pnl = sum(x[0] * LOT_OZ * ((exit_px - x[1]) * d) for x in pos) - sum(x[0] for x in pos) * c.com_rt_lot
        eq += pnl
        rows.append({"t": t, "t_out": int(ts[min(kk, len(ts) - 1)]), "d": d, "entry": entry, "risk": risk,
                     "adds": adds, "lots": round(sum(x[0] for x in pos), 2), "reason": reason,
                     "pnl": round(pnl, 2), "eq": round(eq, 2)})
        busy_until = int(ts[min(kk, len(ts) - 1)]) + 10_000
        if lock_eq and eq >= lock_eq:
            rows.append({"t": busy_until, "skip": "locked", "eq": eq})
            break
        if eq < 0.01 * m_lot * 1.02:
            rows.append({"t": busy_until, "skip": "ruined", "eq": eq})
            break
    return pd.DataFrame(rows)


def orders(m1, **params):
    """Single-entry orders (first ticket only) for sweep/nulltest compatibility; the stack is in backtest()."""
    sig = signals(m1, **{k: v for k, v in params.items() if k in signals.__code__.co_varnames})
    ts = m1["ts"].values
    return [{"t": int(ts[s["i"]]) + 60_000, "d": s["d"], "kind": "mkt", "sl": s["stop"], "tmax": 300 * 60_000,
             "tag": "shift", "stack": True} for s in sig]
