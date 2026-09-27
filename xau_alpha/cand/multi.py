"""
cand/multi.py - live multi-strategy book (one position at a time, first signal wins):
  shift : Mr P Fx buyer/seller shift with the risk-free max-margin stack (cand/shift_stack.py)
  ofi   : Binance XAUUSDT perp order-flow imbalance (W15, |z|>3, 15-min hold), stop 3 ATR capped at $4 (cand/ofi_flow.py)
Only data/calendar/spread guards apply; no veto layers.
"""
import shift_stack
import ofi_flow

WARMUP_BARS = 4000
GRID = {}


def orders(m1, **p):
    a = shift_stack.orders(m1)
    try:
        b = [dict(o, sl_dist=min(o["sl_dist"], 4.0)) for o in ofi_flow.orders(m1, W=15, zt=3.0, hold=15, sl_a=3.0)]
    except Exception:
        b = []                       # flow feed down: keep trading the shift setup
    return sorted(a + b, key=lambda o: o["t"])
