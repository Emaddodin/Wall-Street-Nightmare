"""What the same shapes look like if a level has to hold four times.

Reads the coins the scout is actually walking, finds every shape the engine
would find today at min_touches=2, and re-asks the same question at 3, 4 and
5. Nothing here changes anything: it is the same detector, run twice.
"""
import os
import sys
import json
BOT = os.environ.get("TBT_BOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BOT)
import papertrade as P
from exchange.bitunix import BitunixClient

TP, FB, FEE, LEV, THR = 10.0, 8, 12/1e4, 50, 1.5
c = BitunixClient()
coins = json.load(open(os.path.join(BOT, "data", "watchlist.json")))
if isinstance(coins[0], dict): coins = [x.get("symbol") or x.get("sym") for x in coins]

CACHE = {}
for sym in coins[:70]:
    try: df = c.klines(sym, "15m", limit=200)
    except Exception: continue
    if df is None or len(df) < 80: continue
    ts = [int(t.timestamp()) for t in df.index]
    CACHE[sym] = {t: (float(r.open), float(r.high), float(r.low), float(r.close))
                  for t, (_, r) in zip(ts, df.iterrows())}

def sweep(mt):
    found = filled = won = lost = 0
    ret = 0.0
    for sym, ohlc in CACHE.items():
        keys = sorted(ohlc)
        for i in range(40, len(keys) - 2):
            s = P.breakout(ohlc, keys[i], max_stop=P._stop_room(), min_touches=mt)
            if not s or s["thrust"] < THR: continue
            found += 1
            e, st, sd = s["entry"], s["stop"], s["side"]
            tp = e*(1+TP/100) if sd == "BUY" else e*(1-TP/100)
            fj = None
            for j in range(i+1, min(i+1+FB, len(keys))):
                o,h,l,cl = ohlc[keys[j]]
                if (sd == "BUY" and l <= e) or (sd == "SELL" and h >= e): fj = j; break
            if fj is None: continue
            filled += 1
            for j in range(fj, len(keys)):
                o,h,l,cl = ohlc[keys[j]]
                hit_stop = l <= st if sd == "BUY" else h >= st
                hit_tp   = h >= tp if sd == "BUY" else l <= tp
                if hit_stop: lost += 1; ret -= (s["stop_pct"]/100 + FEE)*LEV; break
                if hit_tp:   won += 1;  ret += (TP/100 - FEE)*LEV; break
    return found, filled, won, lost, (ret/filled*100 if filled else 0)

print(f"  {'min_touches':<14}{'شکل پیدا شد':<14}{'پر شد':<10}{'به هدف':<16}"
      f"{'استاپ':<16}{'انتظار هر ترید'}")
for mt in (2, 3, 4, 5):
    f, fl, w, l, ex = sweep(mt)
    print(f"  {mt:<14}{f:<14}{fl:<10}{w:>3} ({100*w/max(fl,1):>4.1f}%)      "
          f"{l:>4} ({100*l/max(fl,1):>4.1f}%)      {ex:>+7.1f}% مارجین")
