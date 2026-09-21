import json
import pandas as pd
from backtester import VectorizedBacktester, HyperPredatorParams

with open("data/candles/gold_m1_2026-09-18.json") as f:
    candles = json.load(f)
df = pd.DataFrame(candles)
if "timestamp" not in df.columns: df["timestamp"] = df["time"]
if "tick_velocity" not in df.columns: df["tick_velocity"] = 1.6
if "l2_imbalance" not in df.columns: df["l2_imbalance"] = 1.0
if "tape_delta" not in df.columns: df["tape_delta"] = 0.5
if "volatility_regime" not in df.columns: df["volatility_regime"] = 1.0

# 15-minute Trend Alignment (M15 EMA or slope)
df["ema15"] = df["close"].ewm(span=15).mean()
df["macro_bias"] = df.apply(lambda r: "BULLISH" if r["close"] >= r["ema15"] else "BEARISH", axis=1)

bt = VectorizedBacktester(initial_equity=65.0, leverage=100.0)
res = bt.run_backtest(params=HyperPredatorParams(wick_pct=0.65, tick_velocity_mult=1.5), source=df)
print(f"M15 TREND ALIGNED -> Trades: {res.total_trades} | Wins: {res.winning_trades} | Win Rate: {res.win_rate*100:.1f}% | Net PnL: ${res.total_pnl:+.2f} | Final Eq: ${res.final_equity:.2f} | PF: {res.profit_factor:.2f}")
for t in res.trades[:8]:
    print(t)
