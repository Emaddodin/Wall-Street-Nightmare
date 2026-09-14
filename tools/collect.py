#!/usr/bin/env python3
"""
Walk one chart window through the watchlist and keep every bar the council
published: the six votes, the plan, the entry level, and the candle.

This is the only honest dataset for judging the council. The Python port of the
strategy reproduces about a tenth of it, so anything measured there is void --
these numbers come off the indicator itself, on the timeframe the book trades.

It matters which higher timeframe the indicator is set to when this runs: the
HTF vote is one of the six, and a dataset collected at 30 minutes cannot be
used to judge a council running at one hour. The setting is recorded in the
file so a stale dataset can be spotted rather than trusted.
"""
_BOT = str(__import__("pathlib").Path(__file__).resolve().parent.parent)
import json
import os
import sys
import time
sys.path.insert(0, _BOT)
os.chdir(_BOT)
from signals.tv_cdp import TradingViewCDP           # noqa: E402
from papertrade import unpack_state, unpack_votes   # noqa: E402

WINDOW = int(sys.argv[1]) if len(sys.argv) > 1 else 0
OUT = _BOT + "/data/council_history.jsonl"
BARS_JS = """
(function(){try{var d=window.TradingViewApi.activeChart().getSeries().data();
var rows=[];d.each(function(i,v){var a=v&&v.value?v.value:v;
if(a&&a.length>=5)rows.push([a[0],a[1],a[2],a[3],a[4]]);return false;});
return JSON.stringify(rows);}catch(e){return "[]";}})()
"""
HTF_JS = """
(function(){try{var ch=window.TradingViewApi.activeChart();var out='?';
ch.getAllStudies().forEach(function(s){if(s.name.indexOf('TBT')<0)return;
var st=ch.getStudyById(s.id);var info=st.getInputsInfo?st.getInputsInfo():[];
var vals=st.getInputValues?st.getInputValues():[];var by={};
vals.forEach(function(v){by[v.id]=v.value;});
info.forEach(function(i){if(/^Higher timeframe$/i.test((i.name||'')+''))
out=String(by[i.id]);});});return out;}catch(e){return '?';}})()
"""

coins = json.load(open(_BOT + "/data/universe.json"))
done = set()
try:
    done = {json.loads(l)["sym"] for l in open(OUT)}
except Exception:
    pass
coins = [c for c in coins if c not in done]
print(f"  {len(done)} already in the file, {len(coins)} to add", flush=True)

c = TradingViewCDP(target_index=WINDOW)
start = c.state()
htf = c.evaluate(HTF_JS)
print(f"  window {WINDOW} is on {start.symbol} @ {start.resolution}m, "
      f"higher timeframe {htf} -- it will be put back", flush=True)

out = open(OUT, "a")
kept = bars = 0
for n, sym in enumerate(coins, 1):
    try:
        c.set_symbol(f"BITUNIX:{sym}.P")
        ok = False
        for _ in range(24):
            time.sleep(0.5)
            if c.state().symbol.endswith(f"{sym}.P"):
                st = [x for x in c.studies() if "TBT" in x["name"]]
                if st:
                    r = c.raw_series(st[0]["id"], limit=1000)
                    if r and len(r.get("rows") or []) > 200:
                        ok = True
                        break
        if not ok:
            print(f"  {n:>3}/{len(coins)} {sym:<14} no series", flush=True)
            continue
        at = {p: i + 1 for i, p in enumerate(r["plots"])}
        ohlc = {}
        try:
            for b in json.loads(c.evaluate(BARS_JS)):
                ohlc[int(b[0])] = [float(b[1]), float(b[2]), float(b[3]),
                                   float(b[4])]
        except Exception:
            pass

        def v(name, row):
            i = at.get(name)
            if i is None or i >= len(row):
                return None
            x = row[i]
            return None if x is None or x != x else x

        rows = []
        for row in r["rows"]:
            stv = v("STATE", row)
            if stv is None:
                continue
            t = int(row[0])
            o = ohlc.get(t)
            if not o:
                continue
            d = unpack_state(stv)
            d["members"] = unpack_votes(v("VOTES_PACKED", row) or 0)
            d["t"] = t
            d["ohlc"] = o
            d["entry"] = v("PLAN_ENTRY", row)
            d["fib"] = v("FIB_ENTRY", row)
            # The indicator's own running totals, which is what makes a
            # forecast possible at all: SNIP_*_VOTE climbs toward the
            # threshold bar by bar, so a coin one short of it is one module
            # from printing. Without these in the record there is no way to
            # check whether that forecast is worth anything.
            d["snipB"] = v("SNIP_BUY_VOTE", row)
            d["snipS"] = v("SNIP_SELL_VOTE", row)
            d["htf"] = v("HTF_BIAS", row)
            d["atLow"] = v("AT_LOW", row)
            d["atHigh"] = v("AT_HIGH", row)
            d["comboB"] = v("TBT_BUY_SPAN", row)
            d["comboS"] = v("TBT_SELL_SPAN", row)
            d["snipBull"] = v("SNIPER BUY", row)
            d["snipBear"] = v("SNIPER SELL", row)
            rows.append(d)
        if len(rows) < 200:
            print(f"  {n:>3}/{len(coins)} {sym:<14} only {len(rows)} bars",
                  flush=True)
            continue
        out.write(json.dumps({"sym": sym, "res": r.get("resolution"),
                              "htf": htf, "rows": rows}) + "\n")
        out.flush()
        kept += 1
        bars += len(rows)
        plans = sum(1 for x in rows if x["plan_dir"])
        print(f"  {n:>3}/{len(coins)} {sym:<14} {len(rows):>4} bars, "
              f"{plans:>3} carrying a plan", flush=True)
    except Exception as e:
        print(f"  {n:>3}/{len(coins)} {sym:<14} {type(e).__name__}: {e}",
              flush=True)

out.close()
try:
    c.set_symbol(start.symbol)
    time.sleep(2)
    print(f"\n  window {WINDOW} back on {c.state().symbol}", flush=True)
except Exception as e:
    print(f"\n  COULD NOT RESTORE window {WINDOW}: {e}", flush=True)
print(f"  {kept} coins, {bars} bars at htf {htf} -> {OUT}", flush=True)
