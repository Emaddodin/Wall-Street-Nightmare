"""Photograph the best coins on the list, as the chart actually draws them."""
_BOT = str(__import__("pathlib").Path(__file__).resolve().parent.parent)
import json
import sys
import time
sys.path.insert(0, _BOT)
from signals.tv_cdp import TradingViewCDP

m = json.load(open(_BOT + "/data/watch_measures.json"))
w = json.load(open(_BOT + "/data/watchlist.json"))
picks = w[:4]

c = TradingViewCDP(target_index=1)
start = c.state().symbol
print(f"  window 1 was on {start.split(':')[-1]}")
for sym in picks:
    try:
        c.set_symbol(f"BITUNIX:{sym}.P")
        ok = False
        for _ in range(30):
            time.sleep(0.5)
            if c.state().symbol.endswith(f"{sym}.P"):
                ok = True
                break
        if not ok:
            print(f"  {sym}: would not load")
            continue
        time.sleep(7)                      # let the study finish drawing
        png = c.screenshot(quality=80)
        path = f"/home/tbt/shot_{sym}.jpg"
        open(path, "wb").write(png)
        d = m.get(sym, {})
        print(f"  {sym:<13} {len(png)//1024:>4}KB   covers 10% "
              f"{d.get('reach', 0):.1f}%  {d.get('shapes', 0):.2f} shapes/day  "
              f"force {d.get('force', 0):.1f}")
    except Exception as e:
        print(f"  {sym}: {type(e).__name__}")
try:
    c.set_symbol(start)
    time.sleep(2)
except Exception:
    pass
c.close()
print(f"  window 1 back on {start.split(':')[-1]}")
