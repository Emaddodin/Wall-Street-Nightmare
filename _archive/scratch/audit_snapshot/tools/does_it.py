"""Does the reading actually separate the winners from the rest?"""
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
    o = {}
    for t, row in h.iterrows():
        o[int(pd.Timestamp(t).timestamp())] = (
            float(row.open), float(row.high), float(row.low), float(row.close))
    return o


books = {}
with ThreadPoolExecutor(max_workers=8) as ex:
    for sym, o in zip(syms, ex.map(load, syms)):
        if o and len(o) > 200:
            books[sym] = o

rows = []
for sym, ohlc in books.items():
    keys = sorted(ohlc)
    rch, smo = reach.get(sym, (None, None))
    for i in range(LOOK + 6, len(keys) - 1):
        win = {k: ohlc[k] for k in keys[:i + 1]}
        g = pt.breakout(win, keys[i], look=LOOK, max_stop=ROOM)
        if not g:
            continue
        conf, _ = pt.confidence(
            reach=rch, smooth=smo,
            stairs=g["touches"], tall=g["thrust"] / 2.0,
            body=g["body"], agree=None, against=None)
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
        if res is not None:
            rows.append((conf, res, g["stop_pct"]))

rows.sort(key=lambda r: -r[0])
n = len(rows)
print(f"  {n} setups that resolved, across {len(books)} coins\n")
print(f"  {'reading':<16}{'setups':>8}{'reached +10%':>15}{'break-even':>13}")
edges = [(80, 101), (70, 80), (60, 70), (50, 60), (0, 50)]
for lo, hi in edges:
    sel = [r for r in rows if lo <= r[0] < hi]
    if len(sel) < 5:
        continue
    w = sum(r[1] for r in sel)
    sl = sum(r[2] for r in sel) / len(sel)
    be = sl / (TP + sl) * 100
    print(f"  {f'{lo}-{hi if hi<=100 else 100}':<16}{len(sel):>8}"
          f"{100*w/len(sel):>14.1f}%{be:>12.1f}%")
top = [r for r in rows if r[0] >= 70]
if len(top) >= 5:
    w = sum(r[1] for r in top)
    sl = sum(r[2] for r in top) / len(top)
    print(f"\n  everything at 70 or better: {len(top)} setups, "
          f"{100*w/len(top):.1f}% reached the target, break-even "
          f"{sl/(TP+sl)*100:.1f}%")
