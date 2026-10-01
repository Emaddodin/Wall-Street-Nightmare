import sys
sys.argv = ["x", "data/candles/duka_bt"]
import numpy as np, pandas as pd
import scripts.runner_flip as R
O, H, L, C, T, D = R.load()
print("bars", len(C), "days", int(D.max()) + 1, flush=True)
idx, ends = R.five_min(O, H, L, C, T)
atr14 = pd.Series(H - L).rolling(14, min_periods=1).mean().to_numpy()
split = len(C) // 2
cfgs = [(48, 3.5, 4.0, 2.0), (24, 3.5, 4.0, 2.0), (12, 2.0, 1.0, 1.0), (48, 5.0, 4.0, 2.0), (48, 5.0, 6.0, 3.0), (24, 6.0, 6.0, 3.0)]
for (n, am, stop, trail) in cfgs:
    sig = R.signals(H, L, C, T, idx, ends, n, am, atr14, None)
    for tag, (es, xs) in {"mid": (0.15, 0.25), "harsh": (0.30, 0.50)}.items():
        R.ENTRY_SLIP, R.SLIP = es, xs
        st = R.stats(R.run(O, H, L, C, T, sig, stop, trail, 90), T, split)
        print(f"L{n} atr>={am} stop {stop} trail {trail} | {tag}: {st}", flush=True)
