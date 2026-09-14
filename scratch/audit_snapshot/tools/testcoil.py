"""Does a coil actually precede a move? Direction-agnostic, on real candles."""
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
from papertrade import coiling

cli = BitunixClient(os.getenv("BITUNIX_API_KEY"), os.getenv("BITUNIX_API_SECRET"))
syms = [r["sym"] for r in json.load(open("data/boom.json"))][:20]
AHEAD, WANT = 32, 10.0     # eight hours to travel ten percent, either way


def one(sym):
    try:
        h = cli.history(sym, interval="15m", bars=900)
    except Exception:
        return []
    ohlc, vol = {}, {}
    for t, r in h.iterrows():
        ts = int(pd.Timestamp(t).timestamp())
        ohlc[ts] = (float(r.open), float(r.high), float(r.low), float(r.close))
        vol[ts] = float(r.volume)
    keys = sorted(ohlc)
    out = []
    for i in range(90, len(keys) - AHEAD):
        g = coiling(ohlc, keys[i], vol)
        if not g:
            continue
        c = ohlc[keys[i]][3]
        moved = 0.0
        for k in keys[i + 1:i + 1 + AHEAD]:
            _o, hh, ll, _c = ohlc[k]
            moved = max(moved, (hh / c - 1) * 100, (1 - ll / c) * 100)
        out.append((g["pressure"], moved >= WANT, moved))
    return out


rows = []
with ThreadPoolExecutor(max_workers=8) as ex:
    for r in ex.map(one, syms):
        rows += r

n = len(rows)
base = sum(1 for r in rows if r[1]) / n * 100
print(f"  {n} readings across {len(syms)} coins")
print(f"  a bar picked at random moves {WANT:.0f}% within eight hours "
      f"{base:.1f}% of the time\n")
print(f"  {'pressure':<14}{'readings':>10}{'then moved 10%':>17}{'lift':>8}")
for lo, hi in ((0, 20), (20, 35), (35, 50), (50, 65), (65, 101)):
    sel = [r for r in rows if lo <= r[0] < hi]
    if len(sel) < 20:
        continue
    hit = sum(1 for r in sel if r[1]) / len(sel) * 100
    print(f"  {f'{lo}-{hi if hi<=100 else 100}':<14}{len(sel):>10}"
          f"{hit:>16.1f}%{hit/base:>7.2f}x")
