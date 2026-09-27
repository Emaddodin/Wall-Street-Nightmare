"""
cand/runner.py - baseline: the repo's Runner flip strategy (5m Donchian close-breakout + ATR filter, BE + trail).
Template for candidate modules: GRID + orders(m1, **params).
"""
from ref_runner import runner_orders

GRID = {
    "lookback": [12, 24, 48, 96],
    "atr_min": [1.5, 2.5, 3.5, 5.0],
    "stop": [2.0, 3.0, 4.0, 6.0],
    "trail": [1.0, 2.0, 3.0],
    "tmax_min": [90],
}


def orders(m1, lookback=48, atr_min=3.5, stop=4.0, trail=2.0, be_off=0.3, tmax_min=90, hours=None):
    return runner_orders(lookback, atr_min, stop, trail, be_off, tmax_min, hours, m1=m1)
