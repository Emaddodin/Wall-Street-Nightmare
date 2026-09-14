"""Every input the TBT study is carrying, by name, on every window."""
_BOT = str(__import__("pathlib").Path(__file__).resolve().parent.parent)
import json
import sys
sys.path.insert(0, _BOT)
from signals.tv_cdp import TradingViewCDP

JS = r"""
(function(){try{
  var ch = window.TradingViewApi.activeChart();
  var out = [];
  ch.getAllStudies().forEach(function(s){
    if (s.name.indexOf('TBT') < 0) return;
    var st = ch.getStudyById(s.id);
    var info = st.getInputsInfo ? st.getInputsInfo() : [];
    var vals = st.getInputValues ? st.getInputValues() : [];
    var by = {}; vals.forEach(function(v){ by[v.id] = v.value; });
    info.forEach(function(i){
      var n = (i.name || '') + '';
      var v = String(by[i.id]);
      if (v.length > 24) return;          // skip the encrypted blobs
      out.push([n, v]);
    });
  });
  return JSON.stringify(out);
}catch(e){ return 'ERR ' + String(e).slice(0,60) }})()
"""

n = TradingViewCDP.chart_windows()
seen = {}
for i in range(n):
    try:
        c = TradingViewCDP(target_index=i)
        st = c.state()
        got = c.evaluate(JS)
        c.close()
        if str(got).startswith("ERR"):
            print(f"  window {i}: {got}")
            continue
        rows = json.loads(got)
        seen[i] = {k: v for k, v in rows}
        print(f"  window {i}: {st.symbol.split(':')[-1]} @ {st.resolution}m "
              f"-- {len(rows)} inputs")
    except Exception as e:
        print(f"  window {i}: {type(e).__name__}: {str(e)[:50]}")

if not seen:
    raise SystemExit(0)

# every window must agree; a difference between them is the bug that bites
keys = sorted(set().union(*[set(d) for d in seen.values()]))
diff = [k for k in keys
        if len({seen[i].get(k) for i in seen}) > 1]
print(f"\n  {len(keys)} named inputs")
if diff:
    print(f"  THE WINDOWS DISAGREE ON {len(diff)}:")
    for k in diff:
        vals = "  ".join(f"w{i}={seen[i].get(k)}" for i in seen)
        print(f"    {k}: {vals}")
else:
    print("  every window carries the same values")

first = seen[min(seen)]
print("\n  what they are set to:")
for k in keys:
    print(f"    {k:<52} {first.get(k)}")
