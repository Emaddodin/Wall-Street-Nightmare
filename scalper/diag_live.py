import datetime, time
import pandas as pd
import numpy as np
from engine import prepare_symbol, session_label
from config.loader import load_config
from entry_engine import EntryEngine
from execution import entry_price, round_trip_cost_r
from risk_manager import RiskManager
from market_data.client import BitunixPublic
from pathlib import Path

cfg = load_config(extra_file="config/aggressive.yaml")
ee = EntryEngine(cfg)
now_ms = int(time.time() * 1000)
print("now:", datetime.datetime.fromtimestamp(now_ms/1000, datetime.timezone.utc).strftime("%H:%M:%S UTC"))

client = BitunixPublic()
feed = ["ETHUSDT", "BTCUSDT", "SOLUSDT", "XRPUSDT"]
risk = RiskManager(cfg, 100.0)
from pathlib import Path
DATA = Path("/home/tbt/scalper/data")
import sys
sys.path.insert(0, "/home/tbt/scalper")
from market_data.store import CandleStore
try:
    store = CandleStore(DATA / "candles")
except Exception:
    store = None

for sym in feed:
    try:
        df = store.load(sym, "1m") if store else pd.read_parquet(DATA / f"candles/{sym}_1m.parquet")
    except Exception as e:
        print(sym, "load ERR", e)
        continue
    if "open_time" not in df.columns or len(df) < 1500:
        print(sym, "insufficient data", len(df))
        continue
    tail = df.tail(int(cfg.paper["rebuild_days"] * 1440))
    sd = prepare_symbol(sym, tail, cfg)
    n_t = len(sd.tfs["1m"].t)
    t1 = sd.tfs["1m"]
    i = n_t - 2                       # last CLOSED bar (same as trader loop)
    t_close = int(t1.t[i] + 60_000)
    j15 = sd.i15[i]
    tf15 = sd.tfs["15m"]
    print(f"\n== {sym} last closed bar {datetime.datetime.fromtimestamp(t_close/1000, datetime.timezone.utc).strftime('%H:%M:%S')} ==")
    print("  stale?", now_ms - t_close > cfg.paper.get("max_signal_age_ms", 5*60_000),
          f"(age {(now_ms-t_close)/1000:.0f}s)")
    print("  j15:", j15, "bias_dir:", int(tf15.bias_dir[j15]) if j15 >= 0 else "n/a",
          "atr_z:", round(float(tf15.atr_z[j15]), 2) if j15 >= 0 and not np.isnan(tf15.atr_z[j15]) else "nan")
    print("  session:", session_label(t_close, cfg))
    if j15 < 5:
        print("  -> skipped (j15<5)")
        continue
    sigs, rej = ee.on_bar(sd, i, tf15, j15, t_close, {})
    print("  signals:", len(sigs), "| rejections:", len(rej))
    for r in rej[:3]:
        print("    rej:", r.get("strategy"), r.get("leg"), "| passed", r.get("passed_legs"), "|", str(r.get("why"))[:70])
    if sigs:
        sig = sigs[0]
        print("  signal:", sig.strategy, "dir", sig.direction, "swing", sig.swing_level,
              "atr1m", sig.atr1m, "entry_level", sig.entry_level)
        px = float(t1.c[i])
        fill, _ = entry_price(px, sig.direction, cfg.execution["fee_bps"],
                              cfg.execution["slippage_bps"])
        buf = cfg.stop["atr_buffer_mult"] * sig.atr1m
        sl = sig.swing_level - sig.direction * buf
        dist = abs(fill - sl)
        fee_r = round_trip_cost_r(dist / fill, cfg.execution["fee_bps"],
                                  cfg.execution["slippage_bps"])
        print(f"  fill {fill:.6g} sl {sl:.6g} dist% {100*dist/fill:.3f}% fee_r {fee_r:.2f} "
              f"(cap {cfg.execution.get('max_fee_r')}) -> {'PASS' if fee_r <= cfg.execution.get('max_fee_r') else 'FEE-BLOCK'}")
        ok, why = risk.can_enter(now_ms)
        print("  risk can_enter:", ok, why)
