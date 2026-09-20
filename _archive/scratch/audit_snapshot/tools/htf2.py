"""Every input the TBT study is actually carrying on each window."""
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
    var vals = st.getInputValues ? st.getInputValues() : [];
    vals.forEach(function(v){
      out.push(((v.name || v.id || '') + '') + ' = ' + v.value);
    });
  });
  return JSON.stringify(out);
}catch(e){ return 'ERR ' + e }})()
"""

for i in (0, 1):
    try:
        c = TradingViewCDP(target_index=i)
        st = c.state()
        got = c.evaluate(JS)
        c.close()
        print(f"\n  window {i}: {st.symbol.split(':')[-1]} @ {st.resolution}m")
        if str(got).startswith("ERR"):
            print(f"    {got}")
            continue
        rows = json.loads(got)
        print(f"    {len(rows)} inputs on the study")
        for x in rows:
            low = x.lower()
            if any(k in low for k in ("higher", "phase", "hma", "gate",
                                      "timeframe", "agree", "no more")):
                print(f"      {x}")
    except Exception as e:
        print(f"  window {i}: {type(e).__name__}: {str(e)[:70]}")
