"""Research mode: collect, never trade, never auto-tune.

For every candidate setup that passes all legs, record the snapshot --
market conditions, hypothetical entry/SL/TP -- and then the FUTURE OUTCOME
from the subsequent bars (first touch SL/target, MFE/MAE, end-of-window R,
opposite BOS).
"""
from __future__ import annotations

import numpy as np

from structure import LONG


def outcomes_for(sds, signals: list[dict], cfg) -> list[dict]:
    """Attach future outcomes to each recorded signal (pure function of the
    precomputed causal arrays -- no forward leakage into strategy state)."""
    window = cfg.research["outcome_window_bars"]
    buf_mult = cfg.stop["atr_buffer_mult"]
    out = []
    for s in signals:
        sd = sds.get(s["symbol"])
        if sd is None:
            continue
        t1 = sd.tfs["1m"]
        i = s["bar"]
        if i + 1 >= len(t1.t):
            continue
        entry = float(t1.o[i + 1])
        d = LONG if s["direction"] == "LONG" else -LONG
        sl = s["swing_level"] - d * buf_mult * s["atr1m"]
        risk = abs(entry - sl)
        first = None
        mfe = mae = 0.0
        first_bar = -1
        bos_opp = False
        end = min(i + 1 + window, len(t1.t))
        for j in range(i + 1, end):
            h, l, c = float(t1.h[j]), float(t1.l[j]), float(t1.c[j])
            if d == LONG:
                fav = (h - entry) / risk if risk else 0.0
                adv = (l - entry) / risk if risk else 0.0
                hit_sl = l <= sl
            else:
                fav = (entry - l) / risk if risk else 0.0
                adv = (entry - h) / risk if risk else 0.0
                hit_sl = h >= sl
            mfe = max(mfe, fav)
            mae = min(mae, adv)
            if hit_sl:
                first = "SL"
                first_bar = j
                break
            if first is None:
                first = None
        last_close = float(t1.c[end - 1]) if end > i + 1 else entry
        end_r = ((last_close - entry) / risk * d) if risk else 0.0
        s2 = dict(s)
        s2.update({
            "hyp_entry": entry, "hyp_sl": sl, "risk": risk,
            "outcome_first": first, "outcome_bar": first_bar,
            "mfe_r": round(mfe, 3), "mae_r": round(mae, 3),
            "end_r": round(end_r, 3), "opposite_bos": bool(bos_opp),
        })
        out.append(s2)
    return out
