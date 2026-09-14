"""Read-only: what Higher timeframe value does the TBT study carry on each window?"""
import sys
sys.path.insert(0, "/home/tbt/bot")
from signals.tv_cdp import TradingViewCDP

JS = r"""
(function(){try{var ch=window.TradingViewApi.activeChart();var out=[];
ch.getAllStudies().forEach(function(s){if(s.name.indexOf('TBT')<0)return;
var st=ch.getStudyById(s.id);var info=st.getInputsInfo?st.getInputsInfo():[];
var vals=st.getInputValues?st.getInputValues():[];var by={};
vals.forEach(function(v){by[v.id]=v.value;});
info.forEach(function(i){if(/^Higher timeframe$/i.test((i.name||'')+''))
out.push('HTF = '+by[i.id]);});});
return JSON.stringify(out);}catch(e){return 'ERR '+e}})()
"""

for i in range(TradingViewCDP.chart_windows()):
    c = TradingViewCDP(target_index=i)
    st = c.state()
    print("window %d: %s @ %sm  -> %s" %
          (i, st.symbol.split(":")[-1], st.resolution, c.evaluate(JS)))
    c.close()
