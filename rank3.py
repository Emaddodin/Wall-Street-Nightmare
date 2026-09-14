"""Re-rank with the filter I should have had: a coin needs enough history for
the indicator to compute at all, and a newly listed coin's first day is not
evidence of how it behaves."""
_BOT = str(__import__("pathlib").Path(__file__).resolve().parent)
import sys
import json
import time
import logging
sys.path.insert(0, _BOT); logging.basicConfig(level=logging.ERROR)
from datetime import timezone, timedelta
from dotenv import load_dotenv; load_dotenv(_BOT + "/.env")
from exchange.bitunix import BitunixClient

TEH = timezone(timedelta(hours=3, minutes=30))
NEED_BARS, TP, SL = 400, 2.0, 0.52     # 280 lookback + room to breathe
# A coin whose candles breathe under a percent cannot deliver a two percent
# target inside a candle or two, whatever the indicator says. Above 1% ATR that
# target landed 13% of the time; below 0.6% it landed 0.9%. So quiet coins are
# dropped -- but never to the point of leaving nothing to trade. If the whole
# market goes quiet and the floor would empty the list, the best MIN_COINS by
# volatility are kept anyway and flagged, because a thin list is recoverable and
# an empty one stops the book dead.
MIN_ATR = 1.0
MIN_COINS = 6
rows = [r for r in json.load(open("/tmp/screen.json"))
        if r["n"] >= 200 and r["range"] >= 0.8]
c = BitunixClient()
print(f"  checking age and spread on {len(rows)} candidates\n")
out = []
for r in rows:
    try:
        h = c.history(r["sym"], interval="5m", bars=900)
        r["bars"] = len(h)
        if len(h) < NEED_BARS:
            continue
        # ATR off the bars we already hold, so this costs no extra request
        import pandas as pd
        tr = pd.concat([h.high - h.low,
                        (h.high - h.close.shift()).abs(),
                        (h.low - h.close.shift()).abs()], axis=1).max(axis=1)
        r["atr"] = float((tr.ewm(alpha=1/14, adjust=False).mean()
                          / h.close * 100).iloc[-1])
        if r["atr"] != r["atr"]:
            r["atr"] = 0.0
        r["spread"] = c.spread_bps(r["sym"])
        cost = (r["spread"] + 12.0) / 100
        r["rr"] = (TP - cost) / (SL + cost)
        r["edge"] = r["fast"] / 100 * TP - (1 - r["fast"] / 100) * SL - cost
        out.append(r)
    except Exception:
        pass
    time.sleep(0.05)

# Count the age drops before volatility takes its turn, or the two lines below
# report the same coin twice and the totals stop adding up.
dropped_new = len(rows) - len(out)
loud = [r for r in out if r.get("atr", 0) >= MIN_ATR]
quiet = sorted((r for r in out if r.get("atr", 0) < MIN_ATR),
               key=lambda r: -r.get("atr", 0))
short_by = MIN_COINS - len(loud)
rescued = quiet[:short_by] if short_by > 0 else []
for r in rescued:
    r["thin"] = True
dropped_quiet = len(quiet) - len(rescued)
out = loud + rescued
out.sort(key=lambda r: -r["edge"])
print(f"  {dropped_new} dropped for being too new (under {NEED_BARS} bars)")
print(f"  {dropped_quiet} dropped for being too quiet (ATR under {MIN_ATR}%)")
if rescued:
    print(f"  !! only {len(loud)} coins clear {MIN_ATR}% ATR, so the {len(rescued)} "
          f"loudest of the rest are kept to avoid an empty list:")
    for r in rescued:
        print(f"       {r['sym']} at {r['atr']:.2f}% -- too quiet for a 2% target")
print()
print(f"  {'coin':<12}{'2% fast':>9}{'atr':>7}{'bars':>7}{'range':>8}{'spread':>9}"
      f"{'lev':>5}{'minQty':>9}")
for r in out[:10]:
    mark = " <-- ours" if r["sym"] == "ZORAUSDT" else ""
    print(f"  {r['sym']:<12}{r['fast']:>8.1f}%{r['atr']:>6.2f}%{r['bars']:>7}"
          f"{r['range']:>7.2f}%{r['spread']:>7.2f}bp{r['lev']:>5}"
          f"{r['minq']:>9g}{mark}")
# What the pickers may offer: measured, liquid, and old enough to compute.
import json as _json
_json.dump([{"sym": r["sym"], "fast": round(r["fast"], 1),
             "atr": round(r["atr"], 2), "thin": bool(r.get("thin")),
             "range": round(r["range"], 2), "lev": r["lev"],
             "spread": round(r.get("spread", 0), 2), "bars": r["bars"]}
            for r in out], open(_BOT + "/data/eligible.json", "w"))
print(f"\n  {len(out)} coins are eligible to trade -> data/eligible.json")
newborn = [r["sym"] for r in rows if r.get("bars", 0) < NEED_BARS]
print(f"\n  too new to judge: {newborn}")
