"""
cand/ofi_flow.py - order-flow imbalance of the Binance XAUUSDT perp -> trade spot XAUUSD in the flow's direction.
OFI_W = (2*taker_buy - volume)/volume over the last W perp minutes; z-scored vs the prior 1440 minutes (causal).
Entry when |z| crosses above `zt` (first minute of an episode, then a `cool`-minute cooldown), market order at the next
bar, stop `sl_a` x ATR(M1), time exit after `hold` minutes. Data exists from 2025-12-11 (perp listing).
"""
import glob, io, json, os, time, urllib.request, zipfile
import numpy as np, pandas as pd
from data import atr
from xasset import XA

GRID = {"W": [15, 30], "zt": [2.0, 2.5, 3.0], "hold": [15, 30], "sl_a": [3.0, 6.0], "cool": [30]}
_K = None


_LIVE = {"t": 0.0, "df": None}
LIVE_URL = "https://fapi.binance.com/fapi/v1/klines?symbol=XAUUSDT&interval=1m&limit=1500"


def _live_flow():
    """Live mode (XAU_ALPHA_LIVE=1): last 1500 CLOSED perp minutes (volume col 5, taker-buy base col 9), cached 20 s."""
    if _LIVE["df"] is None or time.time() - _LIVE["t"] > 20:
        req = urllib.request.Request(LIVE_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            rows = json.loads(r.read())
        now_min = int(time.time() // 60) * 60_000
        rows = [k for k in rows if int(k[0]) < now_min]
        df = pd.DataFrame({"t": [int(k[0]) for k in rows], "vol": [float(k[5]) for k in rows],
                           "tbuy": [float(k[9]) for k in rows]}).set_index("t")
        _LIVE.update(t=time.time(), df=df)
    return _LIVE["df"]


def _flow():
    global _K
    if os.getenv("XAU_ALPHA_LIVE") == "1":
        return _live_flow()
    if _K is None:
        parts = []
        for fn in sorted(glob.glob(str(XA / "XAUUSDT-1m-*.zip"))):
            with zipfile.ZipFile(fn) as z:
                raw = z.read(z.namelist()[0]).decode()
            df = pd.read_csv(io.StringIO(raw), header=0 if raw.startswith("open_time") else None).iloc[:, [0, 5, 9]]
            df.columns = ["t", "vol", "tbuy"]
            parts.append(df)
        _K = pd.concat(parts).drop_duplicates("t").set_index("t").sort_index()
    return _K


def zscore(m1, W):
    k = _flow().reindex(m1["ts"].values)
    v, b = k["vol"].rolling(W).sum(), k["tbuy"].rolling(W).sum()
    ofi = (2 * b - v) / v
    return ((ofi - ofi.rolling(1440, min_periods=300).mean()) / ofi.rolling(1440, min_periods=300).std()).values


def orders(m1, W=15, zt=2.0, hold=15, sl_a=3.0, cool=30):
    z = zscore(m1, W)
    A = atr(m1)
    ts = m1["ts"].values
    out, last = [], -10**18
    prev = np.r_[np.nan, z[:-1]]
    for i in np.flatnonzero((np.abs(z) > zt) & ~(np.abs(prev) > zt)):
        t = int(ts[i]) + 60_000
        if t - last < cool * 60_000 or not np.isfinite(A[i]):
            continue
        last = t
        out.append({"t": t, "d": int(np.sign(z[i])), "kind": "mkt", "sl_dist": float(sl_a * A[i]),
                    "tmax": hold * 60_000, "tag": "ofi"})
    return out
