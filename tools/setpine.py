"""Sync the live charts' TBT study inputs with the 1h-scaled build.

The 4h-era constants fall into two groups. The INPUTS can be set live,
on every chart window, right now -- this tool does that and saves the
layout. The HARDCODED ones (divergence horizons, the phase HMA length,
trend EMAs, ADX, volume/zone windows, max_bars_back) cannot be changed
without the new .pine -- they are script text, and the study is a
private TradingView script: paste pine/TBT_Sniper.pine into it and
save. This tool reads the inputs back afterwards, so the answer is
always the chart's own word, not a wish.
"""
_BOT = str(__import__("pathlib").Path(__file__).resolve().parent.parent)
import json
import sys
import time
sys.path.insert(0, _BOT)
from signals.tv_cdp import TradingViewCDP

# Display name (regex) -> value, in the order of the scaled build.
WANT = [
    (r"^Higher timeframe$", "60"),
    (r"^Max bars since pivot$", 320),
    (r"^Combo gap: max candles between the three$", 140),
    (r"^Bars between combos$", 20),
    (r"^Event memory window \(candles\)$", 40),
    (r"^Fast length$", 84),
    (r"^Slow length$", 220),
    (r"^Impulse: within how many candles$", 12),
]

_WANT_JS = json.dumps([[a, str(b)] for a, b in WANT])

SET = r"""
(function(){
try{
  var ch = window.TradingViewApi.activeChart();
  var WANT = __WANT__;
  var out = [];
  ch.getAllStudies().forEach(function(s){
    if (s.name.indexOf('TBT') < 0) return;
    var st = ch.getStudyById(s.id);
    var info = st.getInputsInfo ? st.getInputsInfo() : [];
    var patch = [];
    WANT.forEach(function(w){
      info.forEach(function(i){
        if (new RegExp(w[0], 'i').test((i.name||'')+''))
          patch.push({id: i.id, value: String(w[1])});
      });
    });
    var ok = [];
    patch.forEach(function(p){
      try { st.setInputValues([p]); ok.push(p.id + '=' + p.value); }
      catch(e){ ok.push(p.id + ' ERR ' + String(e).slice(0,40)); }
    });
    out.push(ok);
  });
  return JSON.stringify(out);
}catch(e){ return 'ERR ' + String(e).slice(0,70) }})()
""".replace("__WANT__", _WANT_JS)

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
    var row = [];
    info.forEach(function(i){
      row.push(i.name + ' = ' + byId[i.id]);
    });
    out.push(row);
  });
  return JSON.stringify(out);
}catch(e){ return 'ERR ' + String(e).slice(0,70) }})()
"""

SAVE = ("(function(){try{window.TradingViewApi.saveChart(function(){});"
        "return 'save asked';}catch(e){return 'ERR '+String(e).slice(0,50)}})()")

n = TradingViewCDP.chart_windows()
print(f"  {n} chart window(s)")
for i in range(n):
    try:
        c = TradingViewCDP(target_index=i)
        st = c.state()
        print(f"\n  window {i}: {st.symbol.split(':')[-1]}")
        print(f"    inputs: {c.evaluate(READ)}")
        print(f"    set:    {c.evaluate(SET)}")
        time.sleep(4)
        print(f"    now:    {c.evaluate(READ)}")
        print(f"    {c.evaluate(SAVE)}")
        time.sleep(3)
        c.close()
    except Exception as e:
        print(f"  window {i}: {type(e).__name__}: {str(e)[:70]}")
