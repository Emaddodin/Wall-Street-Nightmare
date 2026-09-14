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
    var byId = {};
    vals.forEach(function(v){ byId[v.id] = v.value; });
    info.forEach(function(i){
      var n = (i.name || '') + '';
      if (!/higher|phase|agree|no more|gate/i.test(n)) return;
      var v = byId[i.id];
      out.push(n + ' = ' + String(v).slice(0, 20));
    });
  });
  return JSON.stringify(out);
}catch(e){ return 'ERR ' + String(e).slice(0,80) }})()
"""

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
        rows = json.loads(got)
        if not rows:
            print("    none of those inputs found by name")
        for x in rows:
            print(f"    {x}")
    except Exception as e:
        print(f"  window {i}: {type(e).__name__}: {str(e)[:60]}")
