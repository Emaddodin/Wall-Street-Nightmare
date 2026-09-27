import sys
sys.argv = ["x", "data/candles/real_bt2"]
import numpy as np, pandas as pd
import scripts.runner_flip as R
O, H, L, C, T, D = R.load()
idx, ends = R.five_min(O, H, L, C, T)
atr14 = pd.Series(H - L).rolling(14, min_periods=1).mean().to_numpy()

def sim(trades, start_day, bal0, risk, stop, target, ruin, horizon, lev, price=4300.0):
    bal = bal0
    for e, pts, end in trades:
        d = D[e]
        if d < start_day: continue
        if d - start_day > horizon: return "timeout", bal, d - start_day
        lots = max(0.01, int(risk * bal / (stop * 100) / 0.01) * 0.01)
        maxl = 0.9 * bal * lev / (price * 100)
        lots = min(lots, int(maxl / 0.01) * 0.01)
        if lots < 0.01 or bal < ruin: return "ruin", bal, d - start_day
        bal += pts * lots * 100
        if bal >= target: return "hit", bal, d - start_day
        if bal < ruin: return "ruin", bal, d - start_day
    return "timeout", bal, 0


sig = R.signals(H, L, C, T, idx, ends, 48, 3.5, atr14, None)
starts = list(range(0, int(D.max()) - 25, 2))
for tag, (es, xs) in {"mid": (0.15, 0.25), "harsh": (0.30, 0.50)}.items():
    R.ENTRY_SLIP, R.SLIP = es, xs
    tr = R.run(O, H, L, C, T, sig, 4.0, 2.0, 90)
    print(f"\n== L48 atr3.5 stop4 trail2, friction {tag}, leverage 1:1000, target x8 of start")
    for bal0 in (12.47, 25.0, 50.0):
        for risk in (0.05, 0.08, 0.12, 0.20):
            res = [sim(tr, s, bal0, risk, 4.0, bal0 * (100/12.47) if bal0 == 12.47 else max(100.0, bal0 * 4), 4.2, 45, 1000) for s in starts]
            hit = [r for r in res if r[0] == "hit"]; ruin = [r for r in res if r[0] == "ruin"]
            print(f"  start ${bal0:5.2f} risk{int(risk*100):2d}%: P(reach ${max(100.0, bal0*4) if bal0!=12.47 else 100:.0f})={len(hit)/len(res)*100:4.0f}%  P(ruin)={len(ruin)/len(res)*100:4.0f}%  median days={np.median([r[2] for r in hit]) if hit else '-'}")
