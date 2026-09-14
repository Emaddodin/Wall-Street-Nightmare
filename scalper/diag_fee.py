import glob, os, time
import pandas as pd
import numpy as np
from engine import prepare_symbol
from config.loader import load_config
from strategies import collect_signals
from entry_engine import EntryEngine
from execution import entry_price, round_trip_cost_r

cfg = load_config(extra_file="config/aggressive.yaml")
day_start = (int(time.time()*1000) // 86_400_000) * 86_400_000
prime_lo = day_start + int(9.5*3_600_000)
prime_hi = day_start + int(14.5*3_600_000)

ee = EntryEngine(cfg)
files = sorted(glob.glob("data/candles/*_1m.parquet"), key=os.path.getmtime, reverse=True)[:15]
rows = []
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
            sigs, _ = ee.on_bar(sd, i, tf15, j15, t_close, {})
            if not sigs:
                continue
            sig = sigs[0]
            px = float(t1.c[i])          # market fill ~ close (paper uses last kline close)
            d = sig.direction
            fill, _ = entry_price(px, d, cfg.execution["fee_bps"],
                                  cfg.execution["slippage_bps"])
            buf = cfg.stop["atr_buffer_mult"] * sig.atr1m
            sl = sig.swing_level - d * buf
            dist = abs(fill - sl)
            frac = dist / fill if fill else 0.0
            fee_r = round_trip_cost_r(frac, cfg.execution["fee_bps"],
                                      cfg.execution["slippage_bps"])
            rows.append((sym, frac * 100, fee_r, sig.strategy, sig.entry_level))
    except Exception as e:
        print(sym, "ERR", e)

n = len(rows)
print("total signal bars:", n)
fracs = np.array([r[1] for r in rows])
print(f"stop distance as % of price: median {np.median(fracs):.3f}%  p25 {np.percentile(fracs,25):.3f}%  p75 {np.percentile(fracs,75):.3f}%")
for cap, name in [(0.20, "current 0.20"), (1.0, "1.0"), (2.0, "2.0")]:
    ok = sum(1 for r in rows if r[2] <= cap)
    print(f"max_fee_r={name}: {ok}/{n} signals pass ({100*ok/max(n,1):.0f}%)")
by = {}
for r in rows:
    by[r[3]] = by.get(r[3], 0) + 1
print("by strategy:", by)
