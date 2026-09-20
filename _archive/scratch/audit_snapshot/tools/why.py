"""Which test is killing every setup."""
_BOT = str(__import__("pathlib").Path(__file__).resolve().parent.parent)
import os
import sys
import json
sys.path.insert(0, _BOT); os.chdir(_BOT)
from dotenv import load_dotenv
load_dotenv(_BOT + "/.env")
from exchange.bitunix import BitunixClient
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
import pandas as pd

cli = BitunixClient(os.getenv("BITUNIX_API_KEY"), os.getenv("BITUNIX_API_SECRET"))
syms = [r["sym"] for r in json.load(open("data/boom.json"))][:12]
ROOM = 1.5


def why_break(ohlc, t, look=60, touch_tol=0.25, min_touches=2,
              min_body=0.45, buffer_mult=1.6, max_stop=ROOM):
    keys = [k for k in sorted(ohlc) if k <= t]
    if len(keys) < look + 4:
        return "not enough history"
    o, h, l, c = ohlc[keys[-1]]
    if not c:
        return "no close"
    rng = max(h, o, c) - min(l, o, c)
    if rng <= 0:
        return "flat candle"
    body = abs(c - o) / rng
    if body < min_body:
        return "candle not decisive"
    ks = keys[-(look + 1):-1]
    typ = sum((ohlc[k][1] - ohlc[k][2]) / ohlc[k][3]
              for k in ks if ohlc[k][3]) / max(len(ks), 1) * 100
    if typ <= 0:
        return "no typical range"
    tol = typ * touch_tol / 100.0
    up = c > o
    level = max(ohlc[k][1] for k in ks) if up else min(ohlc[k][2] for k in ks)
    if (c <= level) if up else (c >= level):
        return "did not close through the level"
    touches, wicks = 0, []
    for k in ks:
        ko, kh, kl, kc = ohlc[k]
        if abs((kh if up else kl) - level) > level * tol:
            continue
        touches += 1
        w = ((kh - max(ko, kc)) if up else (min(ko, kc) - kl)) / level
        wicks.append(abs(w))
    if touches < min_touches:
        return f"level touched only {touches}x"
    wicks.sort()
    noise = wicks[len(wicks) // 2] if wicks else tol
    stop_pct = (noise * buffer_mult + tol) * 100
    if stop_pct > max_stop:
        return f"stop would be {stop_pct:.2f}%, past the line"
    if stop_pct <= 0:
        return "stop is zero"
    return "TAKEN"


def one(sym):
    try:
        h = cli.history(sym, interval="15m", bars=400)
    except Exception:
        return Counter()
    ohlc = {}
    for t, row in h.iterrows():
        ts = int(pd.Timestamp(t).timestamp())
        ohlc[ts] = (float(row.open), float(row.high), float(row.low),
                    float(row.close))
    keys = sorted(ohlc)
    c = Counter()
    for i in range(70, len(keys)):
        win = {k: ohlc[k] for k in keys[:i + 1]}
        c[why_break(win, keys[i])] += 1
    return c


total = Counter()
with ThreadPoolExecutor(max_workers=8) as ex:
    for r in ex.map(one, syms):
        total += r

n = sum(total.values())
print(f"  {len(syms)} coins, {n} bars examined\n")
print(f"  {'what stopped it':<40}{'bars':>8}{'share':>8}")
for k, v in total.most_common():
    print(f"  {k:<40}{v:>8}{100*v/n:>7.1f}%")
