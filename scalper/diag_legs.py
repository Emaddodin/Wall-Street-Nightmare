import datetime
import glob, os, time
import pandas as pd
from engine import prepare_symbol
from config.loader import load_config
from strategies import collect_signals
from entry_engine import EntryEngine

cfg = load_config(extra_file="config/aggressive.yaml")
day_start = (int(time.time()*1000) // 86_400_000) * 86_400_000
prime_lo = day_start + int(9.5*3_600_000)
prime_hi = day_start + int(14.5*3_600_000)

ee = EntryEngine(cfg)
files = sorted(glob.glob("data/candles/*_1m.parquet"), key=os.path.getmtime, reverse=True)[:15]
legs = {}
n_bars = n_sig = n_rej = 0
for f in files:
    sym = os.path.basename(f).replace("_1m.parquet", "")
    try:
        df = pd.read_parquet(f)
        if "open_time" not in df.columns:
            continue
        m = (df["open_time"] >= prime_lo) & (df["open_time"] <= prime_hi)
        if m.sum() < 100:
            continue
        sd = prepare_symbol(sym, df, cfg)
        t1 = sd.tfs["1m"]
        tf15 = sd.tfs["15m"]
        for i in range(len(t1.t)):
            if not (prime_lo <= t1.t[i] <= prime_hi):
                continue
            j15 = sd.i15[i]
            if j15 < 5:
                continue
            t_close = int(t1.t[i] + 60_000)
            n_bars += 1
            sigs, rej = ee.on_bar(sd, i, tf15, j15, t_close, {})
            if sigs:
                n_sig += 1
            if rej:
                n_rej += 1
                for r in rej:
                    k = (r.get("strategy", "?"), r.get("leg", "?"))
                    legs[k] = legs.get(k, 0) + 1
    except Exception as e:
        print(sym, "ERR", e)
print("bars evaluated:", n_bars)
print("bars with signals:", n_sig)
print("bars with rejections:", n_rej)
for k, v in sorted(legs.items(), key=lambda kv: -kv[1])[:12]:
    print("  ", k, v)
