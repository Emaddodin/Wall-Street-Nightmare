"""
cand/shift_mtf.py - Mr P Fx shift on two timeframes in one book (one position at a time, earliest signal wins;
on a tie the 5-minute setup wins). Tags: 'shift5' (tested: PF 1.21/1.05/1.06 by period) and 'shift1' (scalp,
~4/day, backtest PF 0.85/0.84/0.69 - run on DEMO to measure it live).
"""
import shift_stack
import smc5

WARMUP_BARS = 4000
GRID = {}


def orders(m1, **p):
    a = [dict(o, tag="shift5") for o in shift_stack.orders(m1, tf=5)]
    b = [dict(o, tag="shift1") for o in shift_stack.orders(m1, tf=1)]
    c = smc5.orders(m1, trend_mode="ema", tf_e=5, min_rr=1.0)       # 5-step SMC engine (tag smc5), no stacking
    prio = {"shift5": 0, "smc5": 1, "shift1": 2}
    return sorted(a + b + c, key=lambda o: (o["t"], prio.get(o["tag"], 9)))
