"""Reload both chart windows and confirm they picked up the saved setting."""
_BOT = str(__import__("pathlib").Path(__file__).resolve().parent.parent)
import json
import sys
import time
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
    var byId = {};
    vals.forEach(function(v){ byId[v.id] = v.value; });
    info.forEach(function(i){
      var n = (i.name || '') + '';
      if (!/higher|agree|no more|gate/i.test(n)) return;
      out.push(n + ' = ' + String(byId[i.id]).slice(0, 12));
    });
  });
  return JSON.stringify(out);
}catch(e){ return 'ERR ' + String(e).slice(0,60) }})()
"""

for i in (0, 1):
    try:
        c = TradingViewCDP(target_index=i)
        try:
            c.evaluate("location.reload()")
        except Exception:
            pass                       # the page navigates away mid-call
        c.close()
        print(f"  window {i}: reload sent")
    except Exception as e:
        print(f"  window {i}: {type(e).__name__}")

print("  waiting for TradingView to come back...")
for attempt in range(40):
    time.sleep(6)
    ok = 0
    for i in (0, 1):
        try:
            c = TradingViewCDP(target_index=i)
            st = [x for x in c.studies() if "TBT" in x["name"]]
            c.close()
            if st:
                ok += 1
        except Exception:
            pass
    if ok == 2:
        break
print(f"  both windows are back after {(attempt+1)*6}s\n")

for i in (0, 1):
    try:
        c = TradingViewCDP(target_index=i)
        st = c.state()
        got = c.evaluate(JS)
        c.close()
        print(f"  window {i}: {st.symbol.split(':')[-1]} @ {st.resolution}m")
        if str(got).startswith("ERR"):
            print(f"    {got}")
            continue
        for x in json.loads(got):
            print(f"    {x}")
    except Exception as e:
        print(f"  window {i}: {type(e).__name__}: {str(e)[:60]}")
