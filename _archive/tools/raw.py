"""Does any single readable property separate the winners? No formula."""
_BOT = str(__import__("pathlib").Path(__file__).resolve().parent.parent)
import os
import sys
import json
sys.path.insert(0, _BOT); os.chdir(_BOT)
from dotenv import load_dotenv
load_dotenv(_BOT + "/.env")
from exchange.bitunix import BitunixClient
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
import papertrade as pt

cli = BitunixClient(os.getenv("BITUNIX_API_KEY"), os.getenv("BITUNIX_API_SECRET"))
ranked = json.load(open("data/boom.json"))
reach = {r["sym"]: (float(r.get("reach") or 0), float(r.get("smooth") or 0))
         for r in ranked}
syms = [r["sym"] for r in ranked][:20]
ROOM, TP, LOOK = 1.5, 10.0, 36


def load(sym):
    try:
        h = cli.history(sym, interval="15m", bars=600)
    except Exception:
        return None
    return {int(pd.Timestamp(t).timestamp()):
            (float(r.open), float(r.high), float(r.low), float(r.close))
            for t, r in h.iterrows()}


books = {}
with ThreadPoolExecutor(max_workers=8) as ex:
    for sym, o in zip(syms, ex.map(load, syms)):
        if o and len(o) > 200:
            books[sym] = o

rows = []
for sym, ohlc in books.items():
    keys = sorted(ohlc)
    rch, smo = reach.get(sym, (0, 0))
    for i in range(LOOK + 6, len(keys) - 1):
        win = {k: ohlc[k] for k in keys[:i + 1]}
        g = pt.breakout(win, keys[i], look=LOOK, max_stop=ROOM)
        if not g:
            continue
        up = g["side"] == "BUY"
        e, stop = g["entry"], g["stop"]
        tp = e * (1 + TP/100) if up else e * (1 - TP/100)
        res = None
        for j in range(i + 1, min(i + 9, len(keys))):
            _o, h, l, _c = ohlc[keys[j]]
            if (l <= e) if up else (h >= e):
                for k in range(j, min(j + 200, len(keys))):
                    _o2, h2, l2, _c2 = ohlc[keys[k]]
                    if (l2 <= stop) if up else (h2 >= stop):
                        res = 0; break
                    if (h2 >= tp) if up else (l2 <= tp):
                        res = 1; break
                break
        if res is None:
            continue
        st = pt.staircase(win, keys[i], g["side"])
        rows.append(dict(won=res, reach=rch, smooth=smo,
                         touches=g["touches"], thrust=g["thrust"],
                         body=g["body"], stop=g["stop_pct"],
                         long=1 if up else 0,
                         run=pt.run_strength(win, keys[i], g["side"]),
                         steps=(st or {}).get("steps", 0)))

n = len(rows); w = sum(r["won"] for r in rows)
print(f"  {n} setups resolved, {w} reached the target ({100*w/n:.1f}%)")
print(f"  break-even is {sum(r['stop'] for r in rows)/n/(TP+sum(r['stop'] for r in rows)/n)*100:.1f}%\n")
print(f"  {'property':<12}{'losers':>10}{'winners':>10}{'gap':>9}"
      f"{'  top third of it':>18}")
for k in ("reach", "smooth", "touches", "thrust", "body", "stop", "run",
          "steps", "long"):
    a = [r[k] for r in rows if not r["won"]]
    b = [r[k] for r in rows if r["won"]]
    ma, mb = sum(a)/len(a), sum(b)/len(b)
    srt = sorted(rows, key=lambda r: -r[k])[:n//3]
    tw = sum(r["won"] for r in srt) / len(srt) * 100
    print(f"  {k:<12}{ma:>10.3f}{mb:>10.3f}{mb-ma:>+9.3f}{tw:>16.1f}%")
