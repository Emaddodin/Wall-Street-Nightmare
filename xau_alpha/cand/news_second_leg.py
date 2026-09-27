"""
cand/news_second_leg.py - Hypothesis H7 (SYNTHESIS.md section 6): the second leg after a HIGH-impact US release.

Idea: news brings volatility, not drift (post-news drift T+5..T+45 has t = 0.33, SOURCED news_llm section 3), and range
stays 1.4-1.7x normal until ~T+44. Wait W minutes for the release spike to print a post-release range (PR), then trade
the first break of that range, either immediately or on the owner's setup-A style retest of the broken PR edge with a
confirmation candle.

Events (data/econ_calendar.csv, HIGH rows; times are exact UTC minutes, DST-checked in SYNTHESIS S4):
  ev='core' : CPI, NFP (Employment Situation), FOMC statement, PPI, Retail Sales   (the price-confirmed types,
              news_llm 5.3)
  ev='all'  : every HIGH row except the FOMC press conference (folded into the statement 30 min earlier)
  Same-minute rows are merged. placebo=k shifts every event k weeks (same weekday, same ET wall clock) and keeps a
  shifted time only if no calendar row (HIGH or MEDIUM) lies within +-180 minutes: the "non-event day" null.

Rules (written in long space; shorts use the negated series, so long and short rules are byte-identical):
  PR        high/low of the M1 bars in [T, T+W).
  FILTER    PR width >= f x median width of the same ET-clock [T, T+W) window over the prior 20 NY weekdays (f=0: off).
            (SYNTHESIS says "2x the median same-clock 15-minute range"; this compares like with like for W = 30/45,
            OPINION.)
  BREAK     the first M1 close in [T+W, T+W+brk) beyond PR_hi + 0.1A (long) or PR_lo - 0.1A (short); one signal per
            event, direction = side of the first break.
  ENTRY     mode 'imm': market at the close of the break bar i0; stop = min(l[i0-2..i0]) - sigma*A.
            mode 'lim' : buy limit at PR_hi + 0.1A placed at the close of i0, alive R bars; stop =
                        min(l[i0-2..i0], PR_hi) - sigma*A. (Added: the pullback arm without a candle trigger.)
            mode 'touch': retest = first bar i1 in (i0, i0+R] with l[i1] <= PR_hi + 0.2A; market at its close if
                        c[i1] >= PR_hi - 0.1A; stop = l[i1] - sigma*A. (Added: the H2 "no trigger" control arm.)
            mode 'rt' : retest = first bar i1 in (i0, i0+R] with l[i1] <= PR_hi + 0.2A (edge zone PR_hi +- 0.1A,
                        touch tolerance 0.1A); a close below PR_hi - 0.1A before the trigger aborts. Trigger =
                        TRIG_BULL(j, PR_hi + 0.1A) for j in [i1, i1+2] (bull engulf, or lower wick >= 0.6 range with
                        l[j] <= lvl < c[j]). Market at the close of j; stop = min(l[i1..j]) - sigma*A.
  TARGET    tp_r x stop distance from the decision close (absolute price).
  TIME      tmax minutes (default 45).
  NEWS      no entry inside [E - blk, E + blk] of any HIGH row E (data.news_block_mask); blk = 30 is the <$21 margin
            rule (then W >= 30 is required so the release itself does not block every entry).

Units: A = data.atr(m1) (M1 Wilder ATR14, mid). All thresholds are in A or relative to the same-clock median; no
dollar thresholds. Arrays are cut at the TEST start (2026-06-01).
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from data import TEST, _ms, atr, load_calendar, news_block_mask  # noqa: E402
import features as F  # noqa: E402

TEST_MS = _ms(TEST[0])
CORE = ("CPI", "NFP", "FOMC", "PPI", "RETAIL")

# Stage-1 grid (SYNTHESIS H7: W(3) x entry(2) x TP(2) = 12, extended to entry(4)) x event set (2) x filter (2) = 96.
GRID = {
    "ev": ["core", "all"],
    "W": [15, 30, 45],
    "mode": ["imm", "lim", "touch", "rt"],
    "tp_r": [1.5, 2.0],
    "f": [0.0, 2.0],
}

DEFAULTS = dict(ev="core", W=30, mode="rt", tp_r=1.5, f=2.0, brk=90, R=30, sigma=0.3, tmax=45, blk=5, w_pin=0.6,
                med_n=20, placebo=0)

_C: dict = {}


def ev_type(e: str) -> str:
    e = e.lower()
    for key, t in (("cpi", "CPI"), ("nonfarm", "NFP"), ("fomc rate decision", "FOMC"), ("fomc press", "FOMCpc"),
                   ("fomc minutes", "FOMCmin"), ("ppi", "PPI"), ("retail sales", "RETAIL"), ("ism", "ISM"),
                   ("jolts", "JOLTS"), ("pce", "PCE"), ("gdp", "GDP"), ("testimony", "TESTIMONY"),
                   ("jackson", "JH")):
        if key in e:
            return t
    return "OTHER"


def _base(m1):
    key = (len(m1), int(m1["ts"].values[0]), int(m1["ts"].values[-1]))
    if _C.get("key") == key:
        return _C["b"]
    _C.clear()
    ts_all = m1["ts"].values
    n = int(np.searchsorted(ts_all, TEST_MS, side="left"))
    ts = ts_all[:n].astype(np.int64)
    o, h, l, c = (m1[k].values[:n].astype(np.float64) for k in "ohlc")
    A = atr(m1)[:n]
    ny = m1["ny_mod"].values[:n].astype(np.int64)
    nyd = pd.to_datetime(ts, unit="ms", utc=True).tz_convert("America/New_York")
    dord = (nyd.normalize().tz_localize(None).values.astype("datetime64[D]").astype(np.int64))
    wd = nyd.dayofweek.values
    key_arr = dord * 1440 + ny
    order = np.argsort(key_arr, kind="stable")
    ks = key_arr[order]
    first = np.r_[True, ks[1:] != ks[:-1]]
    keys_u, idx_u = ks[first], order[first]
    wk_days = np.unique(dord[wd < 5])
    sides = {}
    for d in (1, -1):
        if d > 0:
            oo, hh, ll, cc = o, h, l, c
        else:
            oo, hh, ll, cc = -o, -l, -h, -c
        bull, _ = F.engulf(oo, cc)
        sides[d] = dict(o=oo, h=hh, l=ll, c=cc, eng=bull)
    cal = load_calendar()
    cal["type"] = cal["event"].map(ev_type)
    b = dict(n=n, ts=ts, o=o, h=h, l=l, c=c, A=A, ny=ny, dord=dord, keys_u=keys_u, idx_u=idx_u, wk_days=wk_days,
             sides=sides, cal=cal)
    _C["key"], _C["b"] = key, b
    return b


def _lookup(b, day_ord, minute):
    """M1 index of the bar at (NY date ordinal, ET minute), or -1."""
    k = day_ord * 1440 + minute
    j = np.searchsorted(b["keys_u"], k)
    if j < len(b["keys_u"]) and b["keys_u"][j] == k:
        return int(b["idx_u"][j])
    return -1


def events(m1, ev="core", placebo=0):
    """Event times (ms UTC) for the event set, optionally shifted `placebo` weeks (non-event-day null)."""
    b = _base(m1)
    cal = b["cal"]
    hi = cal[cal["impact"] == "HIGH"]
    if ev == "core":
        hi = hi[hi["type"].isin(CORE)]
    else:
        hi = hi[hi["type"] != "FOMCpc"]
    t = np.unique(hi["ts"].values.astype(np.int64))
    if placebo:
        loc = pd.to_datetime(t, unit="ms", utc=True).tz_convert("America/New_York").tz_localize(None)
        loc = loc + pd.Timedelta(days=7 * int(placebo))
        t = (loc.tz_localize("America/New_York", ambiguous="NaT", nonexistent="NaT").tz_convert("UTC")
             .as_unit("ms").asi8)
        t = t[t > 0]
        allev = np.sort(cal["ts"].values.astype(np.int64))
        j = np.searchsorted(allev, t - 180 * 60_000)
        clash = (j < len(allev)) & (allev[np.minimum(j, len(allev) - 1)] <= t + 180 * 60_000)
        t = np.unique(t[~clash])
    return t[(t >= b["ts"][0]) & (t < TEST_MS)]


def _same_clock_median(b, i_T, W, med_n):
    """Median width of the same ET-clock [T, T+W) window over the prior med_n NY weekdays with data."""
    dT, mT = b["dord"][i_T], b["ny"][i_T]
    wk = b["wk_days"]
    j = np.searchsorted(wk, dT)
    widths = []
    for dd in wk[max(0, j - 3 * med_n):j][::-1]:
        s = _lookup(b, dd, mT)
        e = _lookup(b, dd, mT + W - 1)
        if s < 0 or e < 0 or e < s:
            continue
        widths.append(b["h"][s:e + 1].max() - b["l"][s:e + 1].min())
        if len(widths) >= med_n:
            break
    return float(np.median(widths)) if len(widths) >= med_n // 2 else np.nan


def setups(m1, **params):
    p = {**DEFAULTS, **params}
    b = _base(m1)
    ts, A, n = b["ts"], b["A"], b["n"]
    W, brk, R = int(p["W"]), int(p["brk"]), int(p["R"])
    sigma, f, tp_r, w_pin = float(p["sigma"]), float(p["f"]), float(p["tp_r"]), float(p["w_pin"])
    mode = str(p["mode"])
    blk = int(p["blk"])
    ck = ("block", blk)
    if ck not in _C:
        _C[ck] = news_block_mask(ts, blk, blk) if blk > 0 else np.zeros(n, dtype=bool)
    blocked = _C[ck]
    rows = []
    for T in events(m1, str(p["ev"]), int(p["placebo"])):
        i_T = int(np.searchsorted(ts, T))
        if i_T >= n or ts[i_T] != T:
            continue
        j_W = int(np.searchsorted(ts, T + W * 60_000))
        if j_W - i_T < W - 2 or j_W >= n:
            continue
        pr_hi, pr_lo = b["h"][i_T:j_W].max(), b["l"][i_T:j_W].min()
        mk = ("med", T, W, int(p["med_n"]))
        if mk not in _C:
            _C[mk] = _same_clock_median(b, i_T, W, int(p["med_n"]))
        med = _C[mk]
        if f > 0 and not (np.isfinite(med) and pr_hi - pr_lo >= f * med):
            continue
        j_end = min(int(np.searchsorted(ts, T + (W + brk) * 60_000)), n - 1)
        c = b["c"]
        i0, d = -1, 0
        for i in range(j_W, j_end):
            if c[i] > pr_hi + 0.1 * A[i]:
                i0, d = i, 1
                break
            if c[i] < pr_lo - 0.1 * A[i]:
                i0, d = i, -1
                break
        if i0 < 0:
            continue
        sp = b["sides"][d]
        o_, h_, l_, c_ = sp["o"], sp["h"], sp["l"], sp["c"]
        edge = pr_hi if d > 0 else -pr_lo              # broken PR edge in long space
        if mode == "imm":
            j = i0
            stop = l_[max(i0 - 2, 0):i0 + 1].min() - sigma * A[j]
        elif mode == "lim":
            # pullback limit at the edge-zone top, placed at the close of the break bar, alive for R bars
            j = i0
            px = edge + 0.1 * A[i0]
            stop = min(l_[max(i0 - 2, 0):i0 + 1].min(), edge) - sigma * A[i0]
            sd = px - stop
            if blocked[i0:i0 + R + 1].any() or not sd > 0:
                continue
            t = int(ts[i0]) + 60_000
            if t + R * 60_000 >= TEST_MS:
                continue
            rows.append((t, d, stop * d, (px + tp_r * sd) * d, sd, int(T), int(i0), int(j), (pr_hi - pr_lo), med,
                         "limit", px * d, t + R * 60_000))
            continue
        elif mode == "touch":
            # H2 control arm: enter at the close of the first retest bar if it closed back above the edge zone low
            j = -1
            for i in range(i0 + 1, min(i0 + R, n - 1) + 1):
                if l_[i] <= edge + 0.2 * A[i]:
                    if c_[i] >= edge - 0.1 * A[i]:
                        j = i
                    break
            if j < 0:
                continue
            stop = l_[j] - sigma * A[j]
        else:
            i1 = -1
            for i in range(i0 + 1, min(i0 + R, n - 1) + 1):
                # a close back below the edge implies a touch, so the abort is handled in the trigger loop
                if l_[i] <= edge + 0.2 * A[i]:
                    i1 = i
                    break
            if i1 < 0:
                continue
            j = -1
            for jj in range(i1, min(i1 + 3, n - 1)):
                if c_[jj] < edge - 0.1 * A[jj]:
                    break
                lvl = edge + 0.1 * A[jj]
                rng = max(h_[jj] - l_[jj], 1e-9)
                pin = (min(o_[jj], c_[jj]) - l_[jj]) >= w_pin * rng and l_[jj] <= lvl < c_[jj]
                if sp["eng"][jj] or pin:
                    j = jj
                    break
            if j < 0:
                continue
            stop = l_[i1:j + 1].min() - sigma * A[j]
        if blocked[j]:
            continue
        sd = c_[j] - stop
        if not sd > 0:
            continue
        tp = c_[j] + tp_r * sd
        t = int(ts[j]) + 60_000
        if t >= TEST_MS:
            continue
        rows.append((t, d, stop * d, tp * d, sd, int(T), int(i0), int(j), (pr_hi - pr_lo), med, "mkt", np.nan, 0))
    return pd.DataFrame(rows, columns=["t", "d", "sl", "tp", "sd", "T", "i0", "j", "pr_w", "med_w", "kind", "px",
                                       "exp"])


def orders(m1, **params):
    p = {**DEFAULTS, **params}
    df = setups(m1, **p)
    tag = f"h7{p['ev'][:1]}{p['mode']}{'P' if int(p['placebo']) else ''}"
    out = []
    for r in df.itertuples(index=False):
        o = {"t": int(r.t), "d": int(r.d), "kind": r.kind, "sl": float(r.sl), "tp": float(r.tp),
             "tmax": int(p["tmax"]) * 60_000, "tag": f"{tag}{'L' if r.d > 0 else 'S'}"}
        if r.kind == "limit":
            o["px"], o["exp"] = float(r.px), int(r.exp)
        out.append(o)
    return out
