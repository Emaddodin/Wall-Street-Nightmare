"""Run the scout's own path on the live chart and say exactly what happened."""
_BOT = str(__import__("pathlib").Path(__file__).resolve().parent.parent)
import json
import sys
import time
sys.path.insert(0, _BOT)
from signals.tv_cdp import TradingViewCDP
import papertrade as pt

c = TradingViewCDP(target_index=1)
raw = c.evaluate(pt.BARS_JS)
st = c.state()
c.close()
bars = json.loads(raw)
ohlc = {int(b[0]): (float(b[1]), float(b[2]), float(b[3]), float(b[4]))
        for b in bars}
keys = sorted(ohlc)
print(f"  {st.symbol.split(':')[-1]} @ {st.resolution}m -- {len(keys)} bars "
      f"from the chart")
now = time.time()
print(f"  last bar opened {(now - keys[-1])/60:.1f} min ago, "
      f"the one before {(now - keys[-2])/60:.1f} min ago")
print(f"  -> the chart {'DOES' if (now - keys[-1]) < 15*60 else 'does NOT'} "
      f"include the forming bar\n")

room = pt._stop_room()
print(f"  the stop line is {room:.2f}%\n")

# what the scout tests: the last closed bar
t = keys[-2]
for name, fn in (("breakout", pt.breakout), ("trend_ride", pt.trend_ride)):
    got = fn(ohlc, t, max_stop=room)
    print(f"  {name} on the last closed bar: "
          f"{'FOUND ' + got['side'] if got else 'nothing'}")

# and how often it would have fired over this chart's own history
hits = 0
for i in range(70, len(keys) - 1):
    win = {k: ohlc[k] for k in keys[:i + 1]}
    if (pt.breakout(win, keys[i], max_stop=room)
            or pt.trend_ride(win, keys[i], max_stop=room)):
        hits += 1
span = (keys[-1] - keys[70]) / 3600
print(f"\n  over this chart's own {len(keys)-70} bars ({span:.1f} hours): "
      f"{hits} setups")
if hits:
    print(f"  which is one every {span/hits:.1f} hours on this coin alone")
