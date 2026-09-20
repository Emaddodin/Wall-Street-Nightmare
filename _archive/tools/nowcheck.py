"""Right now, across the whole watchlist: how many shapes are on the table?"""
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
syms = [r["sym"] for r in ranked]
ROOM = pt._stop_room()


def one(sym):
    try:
        h = cli.history(sym, interval="15m", bars=200)
    except Exception:
        return None
    ohlc = {int(pd.Timestamp(t).timestamp()):
            (float(r.open), float(r.high), float(r.low), float(r.close))
            for t, r in h.iterrows()}
    keys = sorted(ohlc)
    out = []
    # the last eight closed bars, which is the window the scout would have
    # seen over the last two hours
    for t in keys[-9:-1]:
        win = {k: ohlc[k] for k in keys if k <= t}
        for fn, shape in ((pt.breakout, "break"), (pt.trend_ride, "trend")):
            g = fn(win, t, max_stop=ROOM)
            if not g:
                continue
            rch, smo = reach.get(sym, (None, None))
            conf, _ = pt.confidence(reach=rch, tall=g.get("thrust", 0) / 2.0,
                                    agree=None, against=None)
            out.append((sym, shape, t, g["side"], g["stop_pct"],
                        g.get("thrust", 0), conf))
            break
    return out


rows = []
with ThreadPoolExecutor(max_workers=10) as ex:
    for r in ex.map(one, syms):
        if r:
            rows += r

print(f"  {len(syms)} coins, their last eight closed bars = the last two hours\n")
if not rows:
    print("  no shape anywhere on any of them")
else:
    print(f"  {len(rows)} shapes\n")
    print(f"  {'coin':<13}{'shape':>7}{'side':>6}{'stop':>7}{'candle':>8}"
          f"{'reading':>9}")
    for sym, shape, t, side, sl, th, conf in sorted(rows, key=lambda r: -r[6]):
        print(f"  {sym:<13}{shape:>7}{side:>6}{sl:>6.2f}%{th:>7.1f}x"
              f"{conf:>8.0f}")
    top = [r for r in rows if r[6] >= 70]
    print(f"\n  {len(top)} of them would be taken at the current floor of 70")
