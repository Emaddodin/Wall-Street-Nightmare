"""
cand/x2_swap.py - swap-adjusted and R-space view of a frozen X2 config (TRAIN + VALID only).

    python3 cand/x2_swap.py <module> '<params json>'

Swap (SOURCED recon/web_research.md line 69, LiteFinance instrument page): long -89.136 points, short +3.45 points
per lot per night, charged at 00:00 server time (EET/EEST = Europe/Athens clock), triple on Wednesday. With
1 point = $0.01 and 1 lot = 100 oz this is -0.891 $/oz (long) and +0.0345 $/oz (short) per night.
The live account is reported swap-free (SOURCED recon/broker_costs.md line 181) but its terms penalise holding
over the triple-swap night and > 5 days, so the charged-swap view is the conservative one.
Prints lf_base TRAIN/VALID stats without and with swap, plus R-space PF (sum of positive R / |sum of negative R|).
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "cand"))

from sim import stats  # noqa: E402
from sweep import evaluate  # noqa: E402

SWAP_LONG, SWAP_SHORT = -0.89136, 0.0345


def swap_nights(t_in, t_out):
    """Number of server-midnight rollovers in (t_in, t_out], Wednesday rollovers counted 3x."""
    a = pd.to_datetime(t_in, unit="ms", utc=True).tz_convert("Europe/Athens")
    b = pd.to_datetime(t_out, unit="ms", utc=True).tz_convert("Europe/Athens")
    out = np.zeros(len(a))
    for k, (x, y) in enumerate(zip(a, b)):
        days = pd.date_range(x.normalize() + pd.Timedelta(days=1), y.normalize(), freq="D")
        # the rollover at 00:00 of calendar day D charges the night that started on D-1; Wed night = 00:00 Thursday
        out[k] = sum(3 if d.dayofweek == 3 else 1 for d in days if d.dayofweek not in (5, 6))
    return out


def rpf(R):
    R = np.asarray(R)
    return round(float(R[R > 0].sum() / max(-R[R < 0].sum(), 1e-9)), 3)


def main():
    name, p = sys.argv[1], json.loads(sys.argv[2])
    ev, tr = evaluate(name, p, cost="lf_base", return_trades=True)
    nights = swap_nights(tr["t_in"].values, tr["t_out"].values)
    sw = np.where(tr["d"].values > 0, SWAP_LONG, SWAP_SHORT) * nights
    tr2 = tr.copy()
    tr2["pnl"] = tr["pnl"] + sw
    tr2["R"] = tr2["pnl"] / tr2["risk"]
    out = {"params": p}
    for sp, nm in ((0, "train"), (1, "valid")):
        m = tr["split"].values == sp
        out[nm] = {"no_swap": stats(tr[m]), "with_swap": stats(tr2[m]),
                   "avg_swap_nights": round(float(nights[m].mean()), 2), "avg_swap_pts": round(float(sw[m].mean()), 3),
                   "R_pf_no_swap": rpf(tr["R"].values[m]), "R_pf_with_swap": rpf(tr2["R"].values[m])}
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
