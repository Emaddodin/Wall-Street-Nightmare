"""Copy the Tesla wiring from the fixed chart to every chart window.

The operator pointed the Tesla sources at the Tesla study on one chart
by hand. Every other window must say the same thing or its TBT study
silently votes "the candle closed higher" instead of reading Tesla --
the exact unwired-hook trap the Pine warns about. This reads window 0's
wiring and applies it to every window, then saves each layout.

    python3 tools/wiretesla.py
"""
_BOT = str(__import__("pathlib").Path(__file__).resolve().parent.parent)
import json
import sys
import time
sys.path.insert(0, _BOT)
from signals.tv_cdp import TradingViewCDP

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
      var n = (i.name||'')+'';
      if (n.indexOf('Tesla') >= 0) out.push([n, byId[i.id]]);
    });
  });
  return JSON.stringify(out);
}catch(e){ return 'ERR ' + String(e).slice(0,70) }})()
"""

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
    var ok = [];
    WANT.forEach(function(w){
      info.forEach(function(i){
        if ((i.name||'')+'' === w[0]) {
          try { st.setInputValues([{id: i.id, value: w[1]}]);
                ok.push(w[0].slice(0,14) + '=set'); }
          catch(e){ ok.push(w[0].slice(0,14) + ' ERR ' + String(e).slice(0,30)); }
        }
      });
    });
    out.push(ok);
  });
  return JSON.stringify(out);
}catch(e){ return 'ERR ' + String(e).slice(0,70) }})()
"""

SAVE = ("(function(){try{window.TradingViewApi.saveChart(function(){});"
        "return 'save asked';}catch(e){return 'ERR '+String(e).slice(0,50)}})()")


def read_wiring(idx: int):
    c = TradingViewCDP(target_index=idx, timeout=45)
    try:
        return json.loads(c.evaluate(READ))
    finally:
        c.close()


n = TradingViewCDP.chart_windows()
if n == 0:
    print("no chart windows")
    raise SystemExit(1)
wiring = read_wiring(0)
if not wiring or isinstance(wiring, str):
    print(f"window 0 wiring unreadable: {wiring}")
    raise SystemExit(1)
print(f"window 0 wiring: {wiring}")

wanted = [[name, val] for name, val in wiring]
_set = SET.replace("__WANT__", json.dumps(wanted))
for i in range(n):
    try:
        c = TradingViewCDP(target_index=i, timeout=45)
        st = c.state()
        print(f"\n  window {i}: {st.symbol.split(':')[-1]}")
        print(f"    before: {c.evaluate(READ)}")
        print(f"    {c.evaluate(_set)}")
        time.sleep(3)
        print(f"    after:  {c.evaluate(READ)}")
        print(f"    {c.evaluate(SAVE)}")
        time.sleep(2)
        c.close()
    except Exception as e:
        print(f"  window {i}: {type(e).__name__}: {str(e)[:70]}")
