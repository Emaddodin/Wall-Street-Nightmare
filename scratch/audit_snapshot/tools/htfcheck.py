"""What higher timeframe is the indicator set to, on each chart window?"""
_BOT = str(__import__("pathlib").Path(__file__).resolve().parent.parent)
import sys
sys.path.insert(0, _BOT)
from signals.tv_cdp import TradingViewCDP

JS = r"""
(function(){try{
  var ch = window.TradingViewApi.activeChart();
  var out = [];
  ch.getAllStudies().forEach(function(s){
    if (s.name.indexOf('TBT') < 0 && s.name.indexOf('TESLA') < 0) return;
    var st = ch.getStudyById(s.id);
    var vals = st.getInputValues ? st.getInputValues() : [];
    var hits = [];
    vals.forEach(function(v){
      var n = (v.name || v.id || '') + '';
      if (/htf|higher|timeframe|time frame|resolution|tide/i.test(n))
        hits.push(n + ' = ' + v.value);
    });
    out.push({study: s.name, inputs: hits});
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
        import json
        for s in json.loads(got):
            print(f"    {s['study']}")
            if not s["inputs"]:
                print("      no higher-timeframe input exposed")
            for x in s["inputs"]:
                print(f"      {x}")
    except Exception as e:
        print(f"  window {i}: {type(e).__name__}: {str(e)[:70]}")
