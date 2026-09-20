"""How each dial changes how often the shape appears, and how it then does."""
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
syms = [r["sym"] for r in json.load(open("data/boom.json"))][:20]
ROOM = 1.5
TP = 10.0


def load(sym):
    try:
        h = cli.history(sym, interval="15m", bars=600)
    except Exception:
        return None
    ohlc = {}
    for t, row in h.iterrows():
        ts = int(pd.Timestamp(t).timestamp())
        ohlc[ts] = (float(row.open), float(row.high), float(row.low),
                    float(row.close))
    return ohlc


books = {}
with ThreadPoolExecutor(max_workers=8) as ex:
    for sym, o in zip(syms, ex.map(load, syms)):
        if o and len(o) > 200:
            books[sym] = o
bars = sum(len(o) for o in books.values())
days = bars / 96 / max(len(books), 1)
print(f"  {len(books)} coins, {days:.1f} days each\n")
print(f"  {'level over':>11}{'body at least':>15}{'setups':>8}{'a day':>8}"
      f"{'reached +10%':>14}")

for look in (60, 36, 24, 16):
    for min_body in (0.45, 0.35):
        found = won = lost = 0
        for sym, ohlc in books.items():
            keys = sorted(ohlc)
            for i in range(look + 6, len(keys) - 1):
                win = {k: ohlc[k] for k in keys[:i + 1]}
                g = pt.breakout(win, keys[i], look=look, min_body=min_body,
                                max_stop=ROOM)
                if not g:
                    continue
                found += 1
                up = g["side"] == "BUY"
                e, stop = g["entry"], g["stop"]
                tp = e * (1 + TP/100) if up else e * (1 - TP/100)
                filled = False
                for j in range(i + 1, min(i + 9, len(keys))):
                    _o, h, l, _c = ohlc[keys[j]]
                    if (l <= e) if up else (h >= e):
                        filled = True
                        for k in range(j, min(j + 200, len(keys))):
                            _o2, h2, l2, _c2 = ohlc[keys[k]]
                            if (l2 <= stop) if up else (h2 >= stop):
                                lost += 1; break
                            if (h2 >= tp) if up else (l2 <= tp):
                                won += 1; break
                        break
        dec = won + lost
        per = found / days / 1
        wr = f"{100*won/dec:.1f}% of {dec}" if dec >= 5 else "-"
        print(f"  {look:>9} bars{min_body:>15.2f}{found:>8}{per:>8.1f}{wr:>14}")
