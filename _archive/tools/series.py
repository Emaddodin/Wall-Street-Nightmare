"""Every series the study publishes, with its value on the last closed bar."""
_BOT = str(__import__("pathlib").Path(__file__).resolve().parent.parent)
import sys
sys.path.insert(0, _BOT)
from signals.tv_cdp import TradingViewCDP

c = TradingViewCDP(target_index=0)
st = [x for x in c.studies() if "TBT" in x["name"]][0]
r = c.raw_series(st["id"], limit=6)
sym = c.state()
c.close()
plots = r["plots"]
rows = r["rows"]
row = rows[-2] if len(rows) > 1 else rows[-1]
print(f"  {sym.symbol.split(':')[-1]} @ {sym.resolution}m -- "
      f"{len(plots)} series published\n")
for i, name in enumerate(plots):
    v = row[i + 1] if i + 1 < len(row) else None
    if v is None or v != v:
        v = "-"
    elif isinstance(v, float):
        v = f"{v:.6g}"
    print(f"    {name:<26} {v}")
