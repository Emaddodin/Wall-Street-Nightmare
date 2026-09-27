import sys, itertools
sys.argv = ["x", "data/candles/real_bt2"]
import numpy as np, pandas as pd
import scripts.runner_flip as R
O, H, L, C, T, D = R.load()
idx, ends = R.five_min(O, H, L, C, T)
atr14 = pd.Series(H - L).rolling(14, min_periods=1).mean().to_numpy()
split = len(C) // 2
rows = []
for n, am, hrs in itertools.product((12, 24, 48), (1.5, 2.5, 3.5), (None, (7, 17))):
    sig = R.signals(H, L, C, T, idx, ends, n, am, atr14, hrs)
    for stop, trail in itertools.product((1.5, 2.5, 4.0), (2.0, 3.5, 5.0)):
        out = {}
        for tag, (es, xs) in {"mid": (0.15, 0.25), "harsh": (0.30, 0.50)}.items():
            R.ENTRY_SLIP, R.SLIP = es, xs
            out[tag] = R.stats(R.run(O, H, L, C, T, sig, stop, trail, 90), T, split)
        if out["mid"] and out["harsh"]:
            rows.append(dict(lookback=n, atr_min=am, hours=str(hrs), stop=stop, trail=trail, n=out["mid"]["n"],
                             pf_mid=out["mid"]["pf"], pf1_mid=out["mid"]["pf1"], pf2_mid=out["mid"]["pf2"], avg_mid=out["mid"]["avg"],
                             pf_harsh=out["harsh"]["pf"], pf1_h=out["harsh"]["pf1"], pf2_h=out["harsh"]["pf2"], avg_harsh=out["harsh"]["avg"],
                             wr_mid=out["mid"]["wr"]))
    print("done", n, am, hrs, flush=True)
df = pd.DataFrame(rows)
df["score"] = np.minimum(df.pf1_h, df.pf2_h)
df = df.sort_values("score", ascending=False)
pd.set_option("display.width", 300)
print(df.head(20).to_string(index=False))
df.to_csv("data/runner_robust.csv", index=False)
