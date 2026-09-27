import sys, itertools
sys.argv = ["x", "data/candles/real_bt2"]
import numpy as np, pandas as pd
import scripts.runner_flip as R
O, H, L, C, T, D = R.load()
idx, ends = R.five_min(O, H, L, C, T)
atr14 = pd.Series(H - L).rolling(14, min_periods=1).mean().to_numpy()
split = len(C) // 2
cfgs = [(12, 2.0, None, 1.0, 1.0, 30), (24, 1.5, None, 1.0, 1.0, 90), (12, 1.5, None, 1.5, 2.0, 90), (24, 2.0, None, 1.5, 2.0, 90)]
for (n, am, hrs, stop, trail, mm) in cfgs:
    sig = R.signals(H, L, C, T, idx, ends, n, am, atr14, hrs)
    for es, xs in ((0.0, 0.05), (0.15, 0.25), (0.30, 0.50), (0.50, 0.80)):
        R.ENTRY_SLIP, R.SLIP = es, xs
        st = R.stats(R.run(O, H, L, C, T, sig, stop, trail, mm), T, split)
        print(f"n={n} atr>={am} stop={stop} trail={trail} | slip in/out {es}/{xs} -> {st}", flush=True)
