"""
xau_alpha/recon/broker_costs_measure.py
Measure LiteFinance XAUUSD execution costs from the owner's own logs/screenshots and compare
every LiteFinance quote we have to Dukascopy ticks at the same UTC time.

Read-only on everything outside xau_alpha/. Single process. Output: prints tables consumed by broker_costs.md
and writes xau_alpha/recon/broker_costs_tables.json.

Time conventions:
  * logs/ghost_grid.log timestamps are the Mac's local time, Asia/Tehran = UTC+03:30 (checked with `date`).
  * LiteFinance web-terminal screenshots show a clock labelled "(UTC+3)".
  * Dukascopy tick ms are UTC.
"""
import glob
import json
import lzma
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data/candles/duka_raw"
M1DIR = ROOT / "data/candles/duka"
OUT = Path(__file__).resolve().parent / "broker_costs_tables.json"
REC = np.dtype([("ms", ">u4"), ("ask", ">u4"), ("bid", ">u4"), ("av", ">f4"), ("bv", ">f4")])
TEH = timezone(timedelta(hours=3, minutes=30))
UTC3 = timezone(timedelta(hours=3))


def t_local(s):  # ghost_grid.log "2026-09-28 20:59:00,265"
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S,%f").replace(tzinfo=TEH).astimezone(timezone.utc)


def t_lf(s):  # LiteFinance screenshot clock, UTC+3
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC3).astimezone(timezone.utc)


_cache = {}


def ticks_for(dt):
    """All Dukascopy ticks of the UTC hour containing dt, as DataFrame(ts ms, bid, ask)."""
    key = dt.strftime("%Y-%m-%d_%H")
    if key in _cache:
        return _cache[key]
    p = RAW / f"{key}.bi5"
    if not p.exists() or p.stat().st_size == 0:
        _cache[key] = None
        return None
    a = np.frombuffer(lzma.decompress(p.read_bytes()), dtype=REC)
    h0 = int(datetime.strptime(key, "%Y-%m-%d_%H").replace(tzinfo=timezone.utc).timestamp() * 1000)
    df = pd.DataFrame({"ts": h0 + a["ms"].astype(np.int64), "bid": a["bid"] / 1000.0, "ask": a["ask"] / 1000.0})
    _cache[key] = df
    return df


def ticks_window(dt, before_s=180, after_s=180):
    parts = []
    for d in {(dt + timedelta(seconds=o)).replace(minute=0, second=0, microsecond=0)
              for o in (-before_s, 0, after_s)}:
        t = ticks_for(d)
        if t is not None:
            parts.append(t)
    if not parts:
        return None
    df = pd.concat(parts).drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    t0 = int(dt.timestamp() * 1000)
    return df[(df.ts >= t0 - before_s * 1000) & (df.ts <= t0 + after_s * 1000)].reset_index(drop=True)


def duka_at(df, dt):
    """Last Dukascopy tick at or before dt (the quote that was live at dt)."""
    t0 = int(dt.timestamp() * 1000)
    sub = df[df.ts <= t0]
    if len(sub) == 0:
        return None
    r = sub.iloc[-1]
    return {"bid": r.bid, "ask": r.ask, "mid": (r.bid + r.ask) / 2, "spr": r.ask - r.bid, "age_ms": int(t0 - r.ts)}


# ---------------------------------------------------------------------------------------------
# 1. LiteFinance quote observations (hand-transcribed; source given per row)
# ---------------------------------------------------------------------------------------------
# bid/ask known exactly (screenshots, hft.json) or reconstructed from the logged disaster SL:
# ghost_engine (HEAD) sets SL = signal ask + 2.50 (SELL) or signal bid - 2.50 (BUY), and logs mid = round((b+a)/2, 2).
Q = [
    # (utc datetime, bid, ask, account, source)
    (t_lf("2026-09-24 23:48:38"), 4271.54, 4271.66, "REAL", "data/demo_switch_verification.png (clock 23:48:38 UTC+3)"),
    (t_lf("2026-09-24 23:55:29"), 4274.44, 4274.56, "DEMO", "data/demo_ready.png (clock 23:55:29 UTC+3)"),
    (t_lf("2026-09-25 02:25:07"), 4272.93, 4273.40, "DEMO", "data/demo_trade_open.png (clock 02:25:07 UTC+3)"),
    (t_lf("2026-09-25 02:25:31"), 4272.78, 4273.25, "DEMO", "data/demo_trade_closed.png (clock 02:25:31 UTC+3)"),
    (t_local("2026-09-28 20:59:00,265"), 2 * 4134.96 - (4137.57 - 2.50), 4137.57 - 2.50, "DEMO",
     "ghost_grid.log SELL signal mid 4134.96, SL 4137.57 = ask+2.50"),
    (t_local("2026-09-28 21:02:01,268"), 4136.57 + 2.50, 2 * 4139.18 - (4136.57 + 2.50), "DEMO",
     "ghost_grid.log BUY signal mid 4139.18, SL 4136.57 = bid-2.50"),
    (t_local("2026-09-29 13:29:00,047"), 4143.27 + 2.50, 2 * 4145.88 - (4143.27 + 2.50), "DEMO",
     "ghost_grid.log BUY signal mid 4145.88, SL 4143.27 (orders failed)"),
    (t_local("2026-09-29 14:26:00,522"), 4149.23 + 2.50, 2 * 4151.84 - (4149.23 + 2.50), "DEMO",
     "ghost_grid.log BUY signal mid 4151.84, SL 4149.23 (orders failed)"),
    (t_local("2026-09-29 14:31:00,051"), 4151.71 + 2.50, 2 * 4154.32 - (4151.71 + 2.50), "DEMO",
     "ghost_grid.log BUY signal mid 4154.32, SL 4151.71 (orders failed)"),
    (datetime.fromtimestamp(1790680325.962859, timezone.utc), 4154.72, 4154.94, "DEMO", "data/state/hft.json"),
]
# mid-only observations (LiteFinance DOM mid at log time)
QM = [
    (t_local("2026-09-28 20:18:01,696"), 4129.71, "warmup seed"),
    (t_local("2026-09-28 20:20:36,741"), 4130.52, "warmup seed"),
    (t_local("2026-09-28 20:35:10,287"), 4127.74, "warmup seed"),
    (t_local("2026-09-28 20:42:12,695"), 4132.75, "warmup seed"),
    (t_local("2026-09-28 20:50:18,059"), 4134.26, "warmup seed"),
    (t_local("2026-09-28 20:53:42,270"), 4135.99, "warmup seed"),
]


def lag_scan(df, dt, lf_mid, lags=range(-120, 121)):
    """Offset LF_mid - duka_mid(dt+lag) for each lag (s). Returns best lag and offset curve."""
    res = []
    for L in lags:
        d = duka_at(df, dt + timedelta(seconds=L))
        if d:
            res.append((L, lf_mid - d["mid"]))
    arr = np.array(res)
    return arr


def section_quotes():
    rows = []
    for dt, b, a, acct, src in Q:
        mid = (b + a) / 2
        df = ticks_window(dt)
        r = {"utc": dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3], "acct": acct, "lf_bid": round(b, 2), "lf_ask": round(a, 2),
             "lf_spr": round(a - b, 3), "src": src}
        if df is not None and len(df):
            d = duka_at(df, dt)
            r.update({"dk_bid": d["bid"], "dk_ask": d["ask"], "dk_spr": round(d["spr"], 3), "dk_age_ms": d["age_ms"],
                      "off_bid": round(b - d["bid"], 3), "off_ask": round(a - d["ask"], 3), "off_mid": round(mid - d["mid"], 3)})
            # Dukascopy spread context: time-weighted-ish (tick mean) over +-5 min
            w = ticks_window(dt, 300, 300)
            r["dk_spr_pm5min_mean"] = round(float((w.ask - w.bid).mean()), 3)
            curve = lag_scan(df, dt, mid)
            i = int(np.argmin(np.abs(curve[:, 1])))
            r["best_lag_s"] = int(curve[i, 0])
            r["best_lag_off"] = round(float(curve[i, 1]), 3)
            r["lags_within_0.05"] = int((np.abs(curve[:, 1]) <= 0.05).sum())
            # mid range over +-120 s gives a sense of how ambiguous lag matching is
            r["dk_mid_range_pm120s"] = round(float(((w.bid + w.ask) / 2).max() - ((w.bid + w.ask) / 2).min()), 2)
        rows.append(r)
    for dt, m, src in QM:
        df = ticks_window(dt)
        r = {"utc": dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3], "acct": "DEMO", "lf_mid": m, "src": "ghost_grid.log " + src}
        if df is not None and len(df):
            d = duka_at(df, dt)
            r.update({"dk_mid": round(d["mid"], 3), "dk_spr": round(d["spr"], 3), "off_mid": round(m - d["mid"], 3)})
            curve = lag_scan(df, dt, m)
            i = int(np.argmin(np.abs(curve[:, 1])))
            r["best_lag_s"] = int(curve[i, 0])
            r["best_lag_off"] = round(float(curve[i, 1]), 3)
        rows.append(r)
    return rows


# ---------------------------------------------------------------------------------------------
# 2. Aggregate lag estimate: pool all mid observations with Duka coverage, find the common lag
#    minimising the dispersion (std) of LF_mid - duka_mid(t+lag); mean offset at that lag = price offset.
# ---------------------------------------------------------------------------------------------
def section_common_lag(include_warmup=True):
    obs = [(dt, (b + a) / 2) for dt, b, a, *_ in Q]
    if include_warmup:
        obs += [(dt, m) for dt, m, _ in QM]
    out = []
    for L in range(-60, 61):
        offs = []
        for dt, m in obs:
            df = ticks_window(dt)
            if df is None or not len(df):
                continue
            d = duka_at(df, dt + timedelta(seconds=L))
            if d:
                offs.append(m - d["mid"])
        offs = np.array(offs)
        out.append((L, len(offs), float(np.mean(offs)), float(np.std(offs)), float(np.median(np.abs(offs - np.median(offs))))))
    arr = np.array(out)
    best = arr[np.argmin(arr[:, 3])]
    at0 = arr[arr[:, 0] == 0][0]
    return {"n_obs": int(at0[1]), "lag0_mean_off": round(at0[2], 3), "lag0_std_off": round(at0[3], 3),
            "best_lag_s": int(best[0]), "best_mean_off": round(best[2], 3), "best_std_off": round(best[3], 3),
            "curve": [[int(r[0]), round(r[2], 3), round(r[3], 3)] for r in arr[::5]]}


# ---------------------------------------------------------------------------------------------
# 3. Reconstruct the two executed DEMO baskets of 2026-09-28 against Dukascopy
# ---------------------------------------------------------------------------------------------
BASKETS = [
    {"name": "B1 SELL 3x0.01", "dir": -1, "signal": t_local("2026-09-28 20:59:00,265"),
     "sig_bid": 2 * 4134.96 - 4135.07, "sig_ask": 4135.07,
     "orders": [("20:59:00,287", "20:59:01,331"), ("20:59:01,539", "20:59:02,657"), ("20:59:02,980", "20:59:03,830")],
     "stop_log": "20:59:28,149", "stop_pnl": -4.11, "flat_done": "20:59:30,726", "bal_after": 26.40, "bal_before": 31.54},
    {"name": "B2 BUY 3x0.01", "dir": +1, "signal": t_local("2026-09-28 21:02:01,268"),
     "sig_bid": 4139.07, "sig_ask": 2 * 4139.18 - 4139.07,
     "orders": [("21:02:01,300", "21:02:02,925"), ("21:02:03,217", "21:02:05,104"), ("21:02:05,301", "21:02:06,351")],
     "stop_log": "21:02:34,122", "stop_pnl": -4.26, "flat_done": "21:02:36,706", "bal_after": 22.11, "bal_before": 26.40},
]


def section_baskets(offset_mid):
    res = []
    for B in BASKETS:
        day = "2026-09-28 "
        df = ticks_window(B["signal"], 60, 120)
        df = df.assign(mid=(df.bid + df.ask) / 2)
        sig = duka_at(df, B["signal"])
        r = {"name": B["name"], "signal_utc": B["signal"].strftime("%H:%M:%S.%f")[:-3],
             "lf_sig_bid": round(B["sig_bid"], 2), "lf_sig_ask": round(B["sig_ask"], 2),
             "dk_sig_bid": sig["bid"], "dk_sig_ask": sig["ask"], "dk_sig_spr": round(sig["spr"], 3)}
        # entry: fill assumed somewhere in [dispatch+0.45 s, confirm]; use Duka mid at confirm and mid-window
        fills_conf, fills_mid = [], []
        latencies = []
        for s0, s1 in B["orders"]:
            t0, t1 = t_local(day + s0), t_local(day + s1)
            latencies.append((t1 - t0).total_seconds())
            d1 = duka_at(df, t1)
            dm = duka_at(df, t0 + (t1 - t0) / 2)
            fills_conf.append(d1)
            fills_mid.append(dm)
        side = "ask" if B["dir"] > 0 else "bid"
        exit_side = "bid" if B["dir"] > 0 else "ask"
        # LF-equivalent prices = Duka mid + LF offset +- LF half-spread (0.11)
        hs = 0.11
        def lf_px(d, s):
            return d["mid"] + offset_mid + (hs if s == "ask" else -hs)
        entry_conf = np.mean([lf_px(d, side) for d in fills_conf])
        entry_mid = np.mean([lf_px(d, side) for d in fills_mid])
        t_stop = t_local(day + B["stop_log"])
        t_done = t_local(day + B["flat_done"])
        ex_stop = lf_px(duka_at(df, t_stop), exit_side)
        ex_done = lf_px(duka_at(df, t_done), exit_side)
        sig_entry = B["sig_ask"] if B["dir"] > 0 else B["sig_bid"]
        q = 3 * 0.01 * 100  # oz
        # Range of zero-commission model PnL when each fill can be anywhere inside its known window:
        # entry in [dispatch, confirm], exit in [stop trigger log, flatten done]  (LF-equivalent prices).
        def px_range(t0, t1, s):
            a0, a1 = int(t0.timestamp() * 1000), int(t1.timestamp() * 1000)
            prev = duka_at(df, t0)
            sub = df[(df.ts >= a0) & (df.ts <= a1)]
            mids = np.r_[prev["mid"], sub.mid.values]
            px = mids + offset_mid + (hs if s == "ask" else -hs)
            return float(px.min()), float(px.max())
        e_lo = e_hi = 0.0
        for s0, s1 in B["orders"]:
            lo, hi = px_range(t_local(day + s0), t_local(day + s1), side)
            e_lo += lo
            e_hi += hi
        x_lo, x_hi = px_range(t_stop, t_done, exit_side)
        oz1 = 0.01 * 100
        if B["dir"] > 0:  # long: pnl = exit - entry
            best, worst = (x_hi * 3 - e_lo) * oz1, (x_lo * 3 - e_hi) * oz1
        else:
            best, worst = (e_hi - x_lo * 3) * oz1, (e_lo - x_hi * 3) * oz1
        r["model_pnl_range_zero_commission"] = [round(worst, 2), round(best, 2)]
        r.update({
            "order_latency_s": [round(x, 3) for x in latencies],
            "entry_slip_vs_signal_$oz(confirm-time est)": round(B["dir"] * (entry_conf - sig_entry), 3),
            "entry_slip_vs_signal_$oz(mid-window est)": round(B["dir"] * (entry_mid - sig_entry), 3),
            "exit_px_at_stop_trigger(est)": round(ex_stop, 2),
            "exit_px_at_flatten_done(est)": round(ex_done, 2),
            "exit_slip_trigger_to_done_$oz": round(B["dir"] * (ex_done - ex_stop), 3),
            "internal_pnl_at_trigger": B["stop_pnl"],
            "realized_pnl_from_balance": round(B["bal_after"] - B["bal_before"], 2),
            "model_pnl_confirmfill_donefill": round(B["dir"] * (ex_done - entry_conf) * q, 2),
            "model_pnl_signalfill_triggerfill": round(B["dir"] * (ex_stop - sig_entry) * q, 2),
        })
        # Dukascopy path summary (mid) from signal to flatten done, 1-second resolution
        path = []
        t = B["signal"]
        while t <= t_done + timedelta(seconds=1):
            d = duka_at(df, t)
            path.append(round(d["mid"], 2))
            t += timedelta(seconds=2)
        r["dk_mid_path_2s"] = path
        res.append(r)
    return res


# ---------------------------------------------------------------------------------------------
# 4. Dukascopy spread by UTC hour from data/candles/duka/*.csv (per-minute mean spread)
# ---------------------------------------------------------------------------------------------
def section_duka_spread():
    files = sorted(glob.glob(str(M1DIR / "xau_m1_*.csv")))
    parts = []
    for f in files:
        d = pd.read_csv(f, usecols=["time", "spread", "volume"])
        parts.append(d)
    d = pd.concat(parts, ignore_index=True)
    dt = pd.to_datetime(d.time, unit="ms", utc=True)
    d["hour"] = dt.dt.hour
    d["dow"] = dt.dt.dayofweek
    d["month"] = dt.dt.strftime("%Y-%m")
    d["recent"] = dt >= pd.Timestamp("2026-06-01", tz="UTC")
    g = d.groupby("hour").spread
    by_hour = pd.DataFrame({"n_min": g.size(), "mean": g.mean(), "p10": g.quantile(0.1), "median": g.median(),
                            "p90": g.quantile(0.9), "p99": g.quantile(0.99)}).round(3)
    gr = d[d.recent].groupby("hour").spread
    by_hour_recent = pd.DataFrame({"n_min": gr.size(), "mean": gr.mean(), "median": gr.median(),
                                   "p90": gr.quantile(0.9), "p99": gr.quantile(0.99)}).round(3)
    by_month = d.groupby("month").spread.agg(["size", "mean", "median"]).round(3)
    overall = d.spread.describe(percentiles=[0.1, 0.5, 0.9, 0.99]).round(3)
    # spread relative to price level (bps) per month, since gold roughly doubled in the sample
    return by_hour, by_hour_recent, by_month, overall, (d.time.min(), d.time.max(), len(files))


def section_d1_bar():
    """LiteFinance D1 bar 2026-09-24 (bid chart, shown in screenshots) vs Dukascopy bid OHLC for 00:00-20:55 UTC."""
    parts = []
    for h in range(0, 21):
        t = ticks_for(datetime(2026, 9, 24, h, tzinfo=timezone.utc))
        if t is not None:
            parts.append(t)
    t = pd.concat(parts)
    t = t[t.ts <= int(t_lf("2026-09-24 23:55:29").timestamp() * 1000)]
    return {"lf_bid_O": 4290.24, "lf_bid_H": 4303.58, "lf_bid_L": 4244.14, "lf_bid_C@20:55:29": 4274.44,
            "dk_bid_O": float(t.bid.iloc[0]), "dk_bid_H": float(t.bid.max()), "dk_bid_L": float(t.bid.min()),
            "dk_bid_C@20:55:29": float(t.bid.iloc[-1]),
            "dk_first_tick_utc": str(pd.to_datetime(t.ts.iloc[0], unit="ms", utc=True))}


def section_pentest():
    """data/preflight_pentest_report.json: mtime 2026-09-21 22:50:07 local = 19:20:07 UTC (end of test).
    latest_mid 4345.33 was read ~10-40 s before; scan Dukascopy 19:18:30-19:20:10 for when mid ~= 4345.33."""
    end = datetime(2026, 9, 21, 19, 20, 7, tzinfo=timezone.utc)
    df = ticks_window(end, 240, 5)
    if df is None:
        return None
    df = df.assign(mid=(df.bid + df.ask) / 2, spr=df.ask - df.bid)
    return {"dk_mid_min": round(float(df.mid.min()), 3), "dk_mid_max": round(float(df.mid.max()), 3),
            "dk_spr_mean": round(float(df.spr.mean()), 3), "dk_spr_median": round(float(df.spr.median()), 3),
            "lf_latest_mid": 4345.33, "lf_floating_after_buy_0.01": -0.22,
            "bal_rt_buy": round(294.53 - 294.80, 2), "bal_rt_sell": round(294.40 - 294.53, 2)}


def section_fine_lag():
    """For steady-state quotes with Duka coverage: set of lags (s, -30..+30) where |LF_mid - duka_mid| <= 0.05."""
    out = []
    for dt, b, a, acct, src in Q:
        df = ticks_window(dt, 60, 60)
        if df is None or not len(df):
            continue
        m = (b + a) / 2
        ok = []
        for L in np.arange(-30, 30.5, 0.5):
            d = duka_at(df, dt + timedelta(seconds=float(L)))
            if d and abs(m - d["mid"]) <= 0.05:
                ok.append(float(L))
        out.append({"utc": dt.strftime("%m-%d %H:%M:%S"), "lags_ok": ok})
    return out


def _second_grid(path):
    """1-second forward-filled mid and spread for one Dukascopy hour file."""
    raw = open(path, "rb").read()
    if not raw:
        return None
    a = np.frombuffer(lzma.decompress(raw), dtype=REC)
    if len(a) < 500:
        return None
    sec = (a["ms"] // 1000).astype(np.int64)
    mid = (a["bid"].astype(np.float64) + a["ask"].astype(np.float64)) / 2000.0
    spr = (a["ask"].astype(np.float64) - a["bid"].astype(np.float64)) / 1000.0
    g = np.full(3600, np.nan)
    s = np.full(3600, np.nan)
    g[sec] = mid           # last tick in each second wins (records are time-ordered)
    s[sec] = spr
    g = pd.Series(g).ffill().values
    s = pd.Series(s).ffill().values
    return g, s


def section_latency_slippage(start="2026-06-01", lags=(1, 2, 3, 5)):
    """
    Price change over an execution delay L (s), from Dukascopy 1-second mids, recent months.
    unconditional: |mid(t+L) - mid(t)|;
    adverse-conditional ('stop-like'): t = seconds where mid moved >= $0.50 in the last 5 s; measure the move over
    the next L s in the SAME direction (positive = continued against a trader stopped out in that direction).
    """
    files = sorted(glob.glob(str(RAW / "*.bi5")))
    files = [f for f in files if os.path.basename(f)[:10] >= start]
    acc = {L: {"abs": [], "cont": []} for L in lags}
    hours = {L: [] for L in lags}
    for f in files:
        r = _second_grid(f)
        if r is None:
            continue
        g, _ = r
        h = int(os.path.basename(f)[11:13])
        mom = np.full(3600, np.nan)
        mom[5:] = g[5:] - g[:-5]
        for L in lags:
            fwd = np.full(3600, np.nan)
            fwd[:-L] = g[L:] - g[:-L]
            v = np.abs(fwd[5:-L])
            v = v[~np.isnan(v)]
            acc[L]["abs"].append(v[::5])  # thin to every 5th second to cap memory
            hours[L].append((h, float(np.mean(v)) if len(v) else np.nan))
            m = (np.abs(mom) >= 0.5) & ~np.isnan(fwd)
            if m.any():
                acc[L]["cont"].append(np.sign(mom[m]) * fwd[m])
    res = {}
    for L in lags:
        a = np.concatenate(acc[L]["abs"])
        c = np.concatenate(acc[L]["cont"]) if acc[L]["cont"] else np.array([np.nan])
        hh = pd.DataFrame(hours[L], columns=["h", "m"]).groupby("h").m.mean().round(3)
        res[L] = {"abs_mean": round(float(a.mean()), 3), "abs_p50": round(float(np.median(a)), 3),
                  "abs_p90": round(float(np.quantile(a, 0.9)), 3), "abs_p99": round(float(np.quantile(a, 0.99)), 3),
                  "cont_n": int(len(c)), "cont_mean": round(float(np.nanmean(c)), 3),
                  "cont_p50": round(float(np.nanmedian(c)), 3), "cont_p90": round(float(np.nanquantile(c, 0.9)), 3),
                  "cont_p99": round(float(np.nanquantile(c, 0.99)), 3),
                  "abs_mean_by_hour": hh.to_dict()}
    return res, len(files)


def section_news_spread():
    """Dukascopy per-minute mean and max spread around HIGH-impact scheduled US releases vs same-hour baseline."""
    cal = pd.read_csv(ROOT / "xau_alpha/data/econ_calendar.csv")
    cal = cal[cal.impact == "HIGH"].copy()
    cal["t"] = pd.to_datetime(cal.ts_utc, utc=True).astype("int64") // 10**6
    m1 = pd.read_parquet(ROOT / "xau_alpha/data/m1_ba.parquet", columns=["ts", "spr", "spr_max", "bh", "bl"])
    m1 = m1.set_index("ts")
    rows = []
    for t in cal.t.values:
        for k in range(-5, 16):
            ts = int(t + k * 60000)
            if ts in m1.index:
                r = m1.loc[ts]
                rows.append((k, r.spr, r.spr_max, r.bh - r.bl))
    ev = pd.DataFrame(rows, columns=["k", "spr", "spr_max", "rng"])
    tab = ev.groupby("k").agg(n=("spr", "size"), spr_mean=("spr", "mean"), spr_max_med=("spr_max", "median"),
                              spr_max_p90=("spr_max", lambda x: x.quantile(0.9)), rng_med=("rng", "median")).round(3)
    # baseline: same hours, all minutes
    hrs = pd.to_datetime(cal.t, unit="ms", utc=True).dt.hour.unique()
    h = pd.to_datetime(m1.index.values, unit="ms", utc=True).hour
    base = m1[np.isin(h, hrs)]
    bl = {"spr_mean": round(float(base.spr.mean()), 3), "spr_max_med": round(float(base.spr_max.median()), 3),
          "rng_med": round(float((base.bh - base.bl).median()), 3), "hours": sorted(int(x) for x in hrs)}
    return tab, bl, int(len(cal))


def main():
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    quotes = section_quotes()
    print("== QUOTES")
    for r in quotes:
        print(json.dumps(r))
    cl_all = section_common_lag(True)
    print("== COMMON LAG (all incl. warmup seeds)", json.dumps({k: v for k, v in cl_all.items() if k != "curve"}))
    cl = section_common_lag(False)
    print("== COMMON LAG (steady-state quotes only)", json.dumps({k: v for k, v in cl.items() if k != "curve"}))
    print("   curve (lag, mean_off, std_off):", cl["curve"])
    fl = section_fine_lag()
    print("== FINE LAG", json.dumps(fl))
    # steady-state lag-0 mean offset for the basket reconstruction (log clock = Mac clock)
    baskets = section_baskets(cl["lag0_mean_off"])
    print("== BASKETS")
    for b in baskets:
        print(json.dumps(b))
    d1 = section_d1_bar()
    print("== D1 BAR", json.dumps(d1))
    pt = section_pentest()
    print("== PENTEST", json.dumps(pt))
    bh, bhr, bm, ov, span = section_duka_spread()
    print("== DUKA SPREAD span", pd.to_datetime(span[0], unit="ms"), pd.to_datetime(span[1], unit="ms"), "files", span[2])
    print(ov)
    print(bh)
    print("recent (>=2026-06-01)")
    print(bhr)
    print(bm)
    ns, nb, nev = section_news_spread()
    print("== NEWS SPREAD (HIGH events: %d) baseline same hours:" % nev, nb)
    print(ns)
    ls, nfiles = section_latency_slippage()
    print("== LATENCY SLIPPAGE (Dukascopy 1s mids, hour files: %d)" % nfiles)
    for L, v in ls.items():
        print(L, json.dumps({k: x for k, x in v.items() if k != "abs_mean_by_hour"}))
    print("abs move over 2s by hour:", ls[2]["abs_mean_by_hour"])
    OUT.write_text(json.dumps({
        "news_spread": ns.reset_index().to_dict(orient="records"), "news_baseline": nb,
        "latency_slippage": {str(k): v for k, v in ls.items()}, "fine_lag": fl, "common_lag_all": cl_all,
        "quotes": quotes, "common_lag": cl, "baskets": baskets, "d1": d1, "pentest": pt,
        "duka_by_hour": bh.reset_index().to_dict(orient="records"),
        "duka_by_hour_recent": bhr.reset_index().to_dict(orient="records"),
        "duka_by_month": bm.reset_index().to_dict(orient="records"),
    }, indent=1, default=float))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
