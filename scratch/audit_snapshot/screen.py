"""Rank coins by the thing that actually matters here: how often a 2% move
finishes inside one or two candles, without giving up 0.52% first.

From every 5m bar's open, race +2% against -0.52% in both directions and see
whether it settles within two bars. A bar where both are reachable counts as
the loss, because there is no way to know the order inside a candle.
"""
_BOT = str(__import__("pathlib").Path(__file__).resolve().parent)
import sys
import time
import json
import logging
import statistics
sys.path.insert(0, _BOT); logging.basicConfig(level=logging.ERROR)
from dotenv import load_dotenv; load_dotenv(_BOT + "/.env")
from exchange.bitunix import BitunixClient

TP, SL, MINVOL = 2.0, 0.52, 1_000_000
c = BitunixClient()
tk = {t["symbol"]: t for t in c.tickers()}
cands = []
for s, t in tk.items():
    try:
        if float(t.get("quoteVol") or 0) >= MINVOL:
            cands.append(s)
    except (TypeError, ValueError):
        pass
print(f"  screening {len(cands)} coins", flush=True)

out = []
for n, sym in enumerate(sorted(cands), 1):
    try:
        k = c.klines(sym, "5m", 200)
        if len(k) < 100:
            continue
        rows = [(r.open, r.high, r.low, r.close) for _, r in k.iterrows()]
        fast = decided = 0
        rng = []
        for i in range(len(rows) - 2):
            o = rows[i][0]
            rng.append((rows[i][1] / rows[i][2] - 1) * 100)
            for sgn in (1, -1):          # a long race and a short race
                tgt = o * (1 + sgn * TP / 100)
                stp = o * (1 - sgn * SL / 100)
                res = None
                for j in (i, i + 1):
                    _, hi, lo, _c = rows[j]
                    a = (hi >= tgt) if sgn > 0 else (lo <= tgt)
                    b = (lo <= stp) if sgn > 0 else (hi >= stp)
                    if a and b:
                        res = 0; break
                    if a:
                        res = 1; break
                    if b:
                        res = 0; break
                if res is not None:
                    decided += 1
                    fast += res
        if not decided:
            continue
        out.append({
            "sym": sym,
            "fast": fast / decided * 100,
            "n": decided,
            "range": statistics.median(rng),
            "vol": float(tk[sym].get("quoteVol") or 0),
            "lev": c.max_leverage(sym),
            "minq": c.min_qty(sym),
            "px": float(tk[sym]["lastPrice"]),
        })
    except Exception:
        pass
    if n % 25 == 0:
        print(f"    {n}/{len(cands)} done", flush=True)
    time.sleep(0.06)

json.dump(out, open("/tmp/screen.json", "w"))
print(f"  measured {len(out)} coins -> /tmp/screen.json")
