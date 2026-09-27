"""
xau_alpha/lib/nulltest.py
Null benchmarks for a candidate's orders: does the ENTRY signal add value beyond its timing/volatility exposure
and exit logic?

  random_direction(orders, seeds)  -> same times and exit rules, direction drawn at random (null distribution)
  mirror(orders)                   -> every direction reversed

Orders with absolute sl/tp/px are converted to distances around the decision-time mid so they can be mirrored.
"""
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from data import load_m1  # noqa: E402
from sim import COSTS, simulate, stats  # noqa: E402


def _mid_at(t_ms: np.ndarray) -> np.ndarray:
    m1 = load_m1()
    ts = m1["ts"].values
    i = np.clip(np.searchsorted(ts, np.asarray(t_ms) - 60_000, side="right") - 1, 0, len(ts) - 1)
    return m1["c"].values[i]


def _to_relative(orders):
    """Express absolute price levels as distances from the decision-time mid (sign-free)."""
    mids = _mid_at(np.array([o["t"] for o in orders]))
    out = []
    for o, mid in zip(orders, mids):
        q = dict(o)
        d = o["d"]
        if not (o.get("sl") is None or (isinstance(o.get("sl"), float) and math.isnan(o["sl"]))):
            q["_sl_rel"] = (mid - o["sl"]) * d          # >0: stop below for long
            q.pop("sl")
        if not (o.get("tp") is None or (isinstance(o.get("tp"), float) and math.isnan(o["tp"]))):
            q["_tp_rel"] = (o["tp"] - mid) * d
            q.pop("tp")
        if o.get("kind", "mkt") != "mkt":
            q["_px_rel"] = (o["px"] - mid) * d
        q["_mid"] = mid
        out.append(q)
    return out


def _apply_dir(rel_orders, dirs):
    out = []
    for q, d in zip(rel_orders, dirs):
        o = {k: v for k, v in q.items() if not k.startswith("_")}
        o["d"] = int(d)
        mid = q["_mid"]
        if "_sl_rel" in q:
            o["sl"] = mid - d * q["_sl_rel"]
        if "_tp_rel" in q:
            o["tp"] = mid + d * q["_tp_rel"]
        if "_px_rel" in q:
            o["px"] = mid + d * q["_px_rel"]
        out.append(o)
    return out


def mirror(orders):
    rel = _to_relative(orders)
    return _apply_dir(rel, [-o["d"] for o in orders])


def random_direction(orders, seeds=range(20), cost="base", split=None):
    """Returns a DataFrame of stats for each random-direction draw (optionally restricted to one split)."""
    rel = _to_relative(orders)
    rows = []
    for s in seeds:
        rng = np.random.default_rng(1000 + s)
        dirs = rng.choice([-1, 1], size=len(orders))
        tr = simulate(_apply_dir(rel, dirs), COSTS[cost] if isinstance(cost, str) else cost)
        if split is not None and len(tr):
            tr = tr[tr["split"] == split]
        rows.append({"seed": s, **stats(tr)})
    return pd.DataFrame(rows)
