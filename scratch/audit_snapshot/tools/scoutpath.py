"""Run the scout's own read_break on a coin we know has a shape."""
_BOT = str(__import__("pathlib").Path(__file__).resolve().parent.parent)
import json
import sys
import time
sys.path.insert(0, _BOT)
sys.argv = ["scout"]
import scout
from signals.tv_cdp import TradingViewCDP
import papertrade as pt

SYM = sys.argv[1] if len(sys.argv) > 1 else "VELVETUSDT"
c = TradingViewCDP(target_index=1)
print(f"  putting window 1 on {SYM}")
c.set_symbol(f"BITUNIX:{SYM}.P")
for _ in range(24):
    time.sleep(0.5)
    if c.state().symbol.endswith(f"{SYM}.P"):
        break
time.sleep(6)
st = c.state()
print(f"  window is on {st.symbol} @ {st.resolution}m")

raw = c.evaluate(pt.BARS_JS)
bars = json.loads(raw)
print(f"  BARS_JS returned {len(bars)} candles")
ohlc = {int(b[0]): (float(b[1]), float(b[2]), float(b[3]), float(b[4]))
        for b in bars}
keys = sorted(ohlc)
t = keys[-2] if len(keys) > 1 else keys[-1]
room = pt._stop_room()
print(f"  testing bar {t}, stop line {room:.2f}%")
for nm, fn in (("breakout", pt.breakout), ("trend_ride", pt.trend_ride)):
    g = fn(ohlc, t, max_stop=room)
    print(f"    {nm}: {'FOUND ' + g['side'] if g else 'nothing'}")

print("\n  and the scout's own read_break:")
try:
    got = scout.read_break(c)
    print(f"    {got if got else 'returned None'}")
except Exception as e:
    print(f"    RAISED {type(e).__name__}: {e}")
c.close()
