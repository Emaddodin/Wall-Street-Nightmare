"""
xau_alpha/news/live_snapshot.py
One live poll of the production PoliticianBrain (same 6 RSS feeds + FairEconomy call production makes every 90 s)
to record today's headline state and how often its substring keyword matching fires on non-words.
Point-in-time only: this is exactly the information that cannot be replayed historically.
Output: news/out/live_snapshot.json
"""
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
import scalper.brain.politician_brain as pb  # noqa: E402

b = pb.PoliticianBrain(update_interval_sec=3600)
t0 = time.time()
while time.time() - t0 < 90 and len(b._cached_headlines) <= 2:
    time.sleep(1)
time.sleep(2)
b._running = False
hl = list(b._cached_headlines)
kw = list(b.BULLISH_KEYWORDS) + list(b.BEARISH_KEYWORDS) + list(b.HIGH_HEAT_KEYWORDS)
sub_only = {}
for h in hl:
    low = h.title.lower()
    for k in kw:
        if k in low and not re.search(r"\b" + re.escape(k) + r"\b", low):
            sub_only.setdefault(k, []).append(h.title[:90])
reg, bias, heat, top = b.get_current_macro_state()
fz = b.check_calendar_freeze(window_minutes=15)
res = {
    "utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()), "poll_seconds": round(time.time() - t0, 1),
    "n_headlines_cached": len(hl), "n_calendar_events_usd": len(b._cached_calendar_events),
    "state": {"regime": reg.value, "bias": bias.value, "heat": heat, "top_headline": top},
    "BUY": b.evaluate_entry_macro_fit("BUY").__dict__, "SELL": b.evaluate_entry_macro_fit("SELL").__dict__,
    "calendar_freeze_now": fz,
    "top15_used_for_state": [{"t": h.title[:100], "sent": round(h.sentiment_score, 2), "reg": h.regime_tag,
                              "heat": h.heat_contribution} for h in hl[:15]],
    "substring_only_keyword_hits": {k: len(v) for k, v in sub_only.items()},
    "substring_only_examples": {k: v[:2] for k, v in sub_only.items()},
}
for k in ("BUY", "SELL"):
    res[k] = {kk: (vv.value if hasattr(vv, "value") else vv) for kk, vv in res[k].items()
              if kk in ("is_permitted", "shield_status", "alpha_boost_multiplier", "tp_expansion_multiplier", "macro_bias")}
(HERE / "out/live_snapshot.json").write_text(json.dumps(res, indent=1, default=str))
print(json.dumps({k: v for k, v in res.items() if k not in ("top15_used_for_state", "substring_only_examples")},
                 indent=1, default=str))
sys.stdout.flush()
os._exit(0)
