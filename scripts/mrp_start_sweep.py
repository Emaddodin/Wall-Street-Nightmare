"""MR P FX scalper from $12.47 started on many different dates (signals come from the per-day cache)."""
import sys, glob, pickle, json
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
import backtest_ghost_real as B

files = sorted(glob.glob(str(ROOT / "data/candles/duka_bt/gold_m1_*.csv")))
days = [pickle.load(open(ROOT / "data/state/ghost_days" / (Path(f).stem + ".pkl"), "rb")) for f in files]
print("days", len(days))
HORIZON = 60
out = {}
for label, sp, sl in (("spread0.30+slip0.10", 0.30, 0.10), ("spread0.18+slip0.05", 0.18, 0.05)):
    res = []
    for s in range(0, len(days) - HORIZON, 3):
        trades, daily, bal, handed, last_i = B.run_scalper(days[s:s + HORIZON], 12.47, 100.0, sp, sl, 500.0)
        n = len(trades)
        status = "HIT_100" if handed else ("BLOWN(<$9)" if bal < 9.0 else "ALIVE")
        res.append((days[s][0], status, round(bal, 2), n))
    stat = {k: sum(1 for r in res if r[1] == k) for k in ("HIT_100", "BLOWN(<$9)", "ALIVE")}
    tr = np.array([r[3] for r in res]); fin = np.array([r[2] for r in res])
    print(f"\n== {label}: {len(res)} start dates, each simulated for {HORIZON} trading days")
    print(stat, f"| median trades {int(np.median(tr))} | median final balance ${np.median(fin):.2f} | best ${fin.max():.2f} | worst ${fin.min():.2f}")
    hits = [r[0] for r in res if r[1] == "HIT_100"]
    print("start dates that reached $100:", hits[:12])
    out[label] = dict(stat=stat, starts=len(res), median_final=float(np.median(fin)))
json.dump(out, open(ROOT / "data/mrp_start_sweep.json", "w"), indent=1)
