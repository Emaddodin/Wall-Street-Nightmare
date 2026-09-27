"""
xau_alpha/lib/ref_runner.py
Reference port of the repo's Runner flip strategy (ghost_grid/runner_strategy.py + runner_exit.py) onto the
new engine, used to cross-check the simulator against scripts/runner_flip.py and as a baseline candidate.
"""
import numpy as np

from data import load_m1, resample_causal
from features import rolling_max_prev, rolling_min_prev


def htf_known_index(known: np.ndarray, n_htf: int) -> np.ndarray:
    """M1 index at whose close each HTF bar becomes known (len(known) if never)."""
    return np.searchsorted(known, np.arange(n_htf), side="left")


def runner_orders(lookback=48, atr_min=3.5, stop=4.0, trail=2.0, be_off=0.3, tmax_min=90, hours=None,
                  m1=None):
    m1 = load_m1() if m1 is None else m1
    htf, known = resample_causal(m1, 5)
    ki = htf_known_index(known, len(htf))
    rng = (m1["h"] - m1["l"]).values
    atr14 = np.convolve(rng, np.ones(14) / 14, mode="full")[:len(rng)]
    hh = rolling_max_prev(htf["h"].values, lookback)
    ll = rolling_min_prev(htf["l"].values, lookback)
    c = htf["c"].values
    ts = m1["ts"].values
    hr = m1["hour"].values
    orders = []
    for k in range(len(htf)):
        i = ki[k]
        if i >= len(m1):
            break
        if hours and not (hours[0] <= hr[i] < hours[1]):
            continue
        if atr14[i] < atr_min:
            continue
        d = 1 if c[k] > hh[k] else (-1 if c[k] < ll[k] else 0)
        if d == 0:
            continue
        orders.append({"t": int(ts[i]) + 60_000, "d": d, "kind": "mkt", "sl_dist": stop,
                       "be": stop, "be_off": be_off, "trail": trail, "trail_act": stop,
                       "tmax": tmax_min * 60_000, "tag": "runner"})
    return orders
