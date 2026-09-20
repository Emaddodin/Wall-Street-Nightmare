"""Set the higher timeframe on every chart window, and save the layout."""
_BOT = str(__import__("pathlib").Path(__file__).resolve().parent.parent)
import sys
import time
sys.path.insert(0, _BOT)
from signals.tv_cdp import TradingViewCDP

SET = r"""
(function(){try{
  var ch = window.TradingViewApi.activeChart();
  var done = [];
  ch.getAllStudies().forEach(function(s){
    if (s.name.indexOf('TBT') < 0) return;
    var st = ch.getStudyById(s.id);
    var info = st.getInputsInfo ? st.getInputsInfo() : [];
    var target = null;
    info.forEach(function(i){
      if (/^Higher timeframe$/i.test((i.name||'')+'')) target = i.id;
    });
    if (!target) { done.push('input not found'); return; }
    var patch = {}; patch[target] = '60';
    st.setInputValues([{id: target, value: '60'}]);
    done.push('set ' + target);
  });
  return JSON.stringify(done);
}catch(e){ return 'ERR ' + String(e).slice(0,70) }})()
"""

READ = r"""
(function(){try{
  var ch = window.TradingViewApi.activeChart();
  var out = [];
  ch.getAllStudies().forEach(function(s){
    if (s.name.indexOf('TBT') < 0) return;
    var st = ch.getStudyById(s.id);
    var info = st.getInputsInfo ? st.getInputsInfo() : [];
    var vals = st.getInputValues ? st.getInputValues() : [];
    var byId = {}; vals.forEach(function(v){ byId[v.id] = v.value; });
    info.forEach(function(i){
      if (/^Higher timeframe$/i.test((i.name||'')+''))
        out.push('Higher timeframe = ' + byId[i.id]);
    });
  });
  return JSON.stringify(out);
}catch(e){ return 'ERR ' + String(e).slice(0,70) }})()
"""

SAVE = "(function(){try{window.TradingViewApi.saveChart(function(){});" \
       "return 'save asked';}catch(e){return 'ERR '+String(e).slice(0,50)}})()"

n = TradingViewCDP.chart_windows()
print(f"  {n} chart window(s)")
for i in range(n):
    try:
        c = TradingViewCDP(target_index=i)
        st = c.state()
        print(f"\n  window {i}: {st.symbol.split(':')[-1]}")
        print(f"    before: {c.evaluate(READ)}")
        print(f"    {c.evaluate(SET)}")
        time.sleep(4)
        print(f"    after:  {c.evaluate(READ)}")
        print(f"    {c.evaluate(SAVE)}")
        time.sleep(3)
        c.close()
    except Exception as e:
        print(f"  window {i}: {type(e).__name__}: {str(e)[:70]}")
