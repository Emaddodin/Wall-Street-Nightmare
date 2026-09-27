"""
xau_alpha/news/guard_audit.py
Executes the PRODUCTION guard code (scalper/brain/*) unmodified, with the network blocked or a fake clock, to
measure what each guard actually decides. Nothing in the repo is modified; outputs go to xau_alpha/news/out/.

Parts
  A  PoliticianBrain offline: state and BUY/SELL verdicts when every feed fails (seed headlines only).
  B  Headline keyword classifier on hand-written probe headlines (substring-matching artefacts).
  C  MacroWatchdog: event-ordering and timezone behaviour of register_scheduled_event / is_entry_allowed.
  D  TradeJournalRAG veto/boost table for Runner-like candidates (direction x UTC hour x regime).
  E  Full oracle (LayaOracle rule fallback, the path used whenever the Laya/Jeff model is not loaded) for
     Runner-like candidates, calendar freeze forced CLEAR, under the offline-seed and a neutral macro state.
  F  Parity: vectorised freeze mask (replay.py) vs production check_calendar_freeze under a fake clock.

Run:  python3 xau_alpha/news/guard_audit.py          (needs no network; ~1 min, single process)
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

os.environ["JEFF_SKIP_HEAVY_WEIGHTS"] = "1"      # never try to load Laya/Jeff in this audit


def _blocked(*a, **k):
    raise urllib.error.URLError("network blocked by xau_alpha guard_audit")


urllib.request.urlopen = _blocked                   # every production fetch now fails, as on an offline VPS

import scalper.brain.politician_brain as pb          # noqa: E402
import scalper.brain.macro_watchdog as mw            # noqa: E402
from scalper.brain.trade_journal_rag import TradeJournalRAG   # noqa: E402

res = {}


def assessment_dict(a):
    return {"permitted": a.is_permitted, "shield": a.shield_status, "bias": a.macro_bias.value,
            "regime": a.regime.value, "heat": a.geopolitical_heat_index,
            "size_mult": a.alpha_boost_multiplier, "tp_mult": a.tp_expansion_multiplier}


# ---------------------------------------------------------------- A: offline PoliticianBrain
b = pb.PoliticianBrain(update_interval_sec=3600)
freeze_clear = (False, "CLEAR", False)
b.check_calendar_freeze = lambda window_minutes=15: freeze_clear     # isolate the headline logic
a0 = {"t": "t<2s (before first poll)", "state": [x.value if hasattr(x, "value") else x for x in b.get_current_macro_state()],
      "BUY": assessment_dict(b.evaluate_entry_macro_fit("BUY")), "SELL": assessment_dict(b.evaluate_entry_macro_fit("SELL"))}
time.sleep(4.0)                                       # first background cycle: all fetches fail, recalculates from seed
a1 = {"t": "after first poll (all feeds failed)",
      "state": [x.value if hasattr(x, "value") else x for x in b.get_current_macro_state()],
      "n_headlines": len(b._cached_headlines), "headlines": [h.title for h in b._cached_headlines],
      "BUY": assessment_dict(b.evaluate_entry_macro_fit("BUY")), "SELL": assessment_dict(b.evaluate_entry_macro_fit("SELL"))}
b._running = False
res["A_offline_politician"] = [a0, a1]

# ---------------------------------------------------------------- B: keyword classifier probes
probes = [
    "US and China agree trade deal; tariffs lifted on $300bn of goods",
    "Trump taps Kevin Warsh as next Fed chair",
    "Software stocks rally as Treasury yields rise",
    "Powell warns inflation remains sticky, signals no rush to cut",
    "Gold hits record high as dollar slides",
    "Gold slumps as ceasefire holds in Middle East",
    "Fed cuts rates by 25bp, signals more easing",
    "Hot CPI: inflation surges to 3.4%, rate hike bets jump",
    "Award-winning analysts see gold flat this week",
    "Central bank gold buying slows in August",
]
rows = []
for h in probes:
    s, reg, heat = b._classify_headline(h)
    rows.append({"headline": h, "sentiment": round(s, 3), "regime": reg, "heat": heat,
                 "politician_status": b.assess_headline_heuristic(h)["status"],
                 "watchdog_status": mw.MacroWatchdog().assess_headline_heuristic(h)["status"]})
res["B_classifier_probes"] = rows

# ---------------------------------------------------------------- C: MacroWatchdog behaviour
w = mw.MacroWatchdog()
now = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=2)
w.register_scheduled_event("Initial Jobless Claims", now, impact="MEDIUM")
w.register_scheduled_event("CPI m/m", now, impact="HIGH")
c1 = w.is_entry_allowed()
w2 = mw.MacroWatchdog()
w2.register_scheduled_event("CPI m/m", now, impact="HIGH")
c2 = w2.is_entry_allowed()
# timezone: an aware New-York datetime is silently re-labelled as UTC
from zoneinfo import ZoneInfo  # noqa: E402
ny = datetime(2026, 9, 11, 8, 30, tzinfo=ZoneInfo("America/New_York"))
w3 = mw.MacroWatchdog()
w3.register_scheduled_event("CPI m/m", ny, impact="HIGH")
stored = datetime.fromtimestamp(w3._events[0].scheduled_epoch, tz=timezone.utc).isoformat()
# a HALT headline never expires
w4 = mw.MacroWatchdog()
w4.assess_headline_heuristic("Fed turns dovish")
c4 = w4.is_entry_allowed()
res["C_macro_watchdog"] = {
    "same_minute_MEDIUM_then_HIGH_is_entry_allowed": c1,
    "HIGH_only_is_entry_allowed": c2,
    "aware_NY_0830_stored_as_utc": stored, "true_utc": ny.astimezone(timezone.utc).isoformat(),
    "after_one_dovish_headline_is_entry_allowed_forever": c4,
}

# ---------------------------------------------------------------- D: trade-journal RAG veto table
rag = TradeJournalRAG()
regimes = [r.value for r in pb.PoliticalRegime]
tab = []
for d in ("BUY", "SELL"):
    for reg in regimes:
        for hr in range(24):
            e = rag.query_historical_twins({"direction": d, "setup_type": "BREAKOUT_RETEST", "hour_utc": hr,
                                            "wick_ratio": 0.6, "atr_entry": 1.80, "politician_regime": reg}, top_k=15)
            tab.append({"dir": d, "regime": reg, "hour": hr, "allowed": e.is_allowed, "rec": e.recommendation,
                        "twin_wr": e.win_rate_pct, "trap": e.trap_risk_pct})
res["D_rag_journal"] = {"journal": str(rag.journal_path), "n_trades": len(rag.trades)}
res["D_rag_table"] = tab

# ---------------------------------------------------------------- E: full oracle rule fallback for Runner inputs
import scalper.brain.laya_oracle as lo   # noqa: E402

orc = lo.LayaOracle()
time.sleep(0.5)
orc.politician = b                                   # offline brain from part A (freeze forced CLEAR)
orc.politician.check_calendar_freeze = lambda window_minutes=15: freeze_clear


def oracle_table(tag):
    out = []
    for d in ("BUY", "SELL"):
        for hr in range(24):
            dec = orc.evaluate_setup_sync({"direction": d, "entry_price": 4000.0, "sl_price": 3996.0 if d == "BUY" else 4004.0,
                                           "wick_ratio": 0.6, "setup_type": "BREAKOUT_RETEST", "trend_aligned": True,
                                           "hour_utc": hr})
            out.append({"state": tag, "dir": d, "hour": hr, "valid": dec.is_valid, "grade": dec.setup_grade,
                        "trap": round(dec.trap_probability, 3), "conf": round(dec.confluence_score, 2),
                        "size_mult": dec.compounding_multiplier, "tp_mult": dec.tp_expansion_multiplier,
                        "veto_source": dec.matched_ict_concepts[0] if not dec.is_valid else ""})
    return out


e_rows = oracle_table("offline_seed")
with b._lock:
    b._current_bias, b._geopolitical_heat_index = pb.MacroBias.NEUTRAL, 40.0
    b._current_regime = pb.PoliticalRegime.NEUTRAL_CHOP
e_rows += oracle_table("neutral")
with b._lock:
    b._current_bias, b._geopolitical_heat_index = pb.MacroBias.STRONG_BEAR, 70.0
e_rows += oracle_table("strong_bear")
res["E_oracle_fallback"] = e_rows

# ---------------------------------------------------------------- F: parity of the vectorised replay mask
try:
    import replay  # noqa: E402
    res["F_parity"] = replay.parity_check(n_random=4000)
except Exception as ex:   # calendar not built yet
    res["F_parity"] = {"error": repr(ex)}

(OUT / "guard_audit.json").write_text(json.dumps(res, indent=1, default=str))

# ---------------------------------------------------------------- summary
print("A offline:", json.dumps(res["A_offline_politician"][1]["state"]), "BUY", res["A_offline_politician"][1]["BUY"],
      "SELL", res["A_offline_politician"][1]["SELL"])
print("A t0:", res["A_offline_politician"][0]["BUY"], res["A_offline_politician"][0]["SELL"])
for r in rows:
    print("B", r)
print("C", res["C_macro_watchdog"])
import collections  # noqa: E402
cnt = collections.Counter((r["dir"], r["allowed"], r["rec"]) for r in tab)
print("D", res["D_rag_journal"], dict(cnt))
vet = [(r["dir"], r["regime"], r["hour"], r["rec"], r["twin_wr"]) for r in tab if not r["allowed"]]
print("D vetoes:", vet[:60])
for tag in ("offline_seed", "neutral", "strong_bear"):
    sub = [r for r in e_rows if r["state"] == tag]
    for d in ("BUY", "SELL"):
        s2 = [r for r in sub if r["dir"] == d]
        print("E", tag, d, "valid_hours", sum(r["valid"] for r in s2), "/24",
              "vetoes", sorted(set((r["veto_source"]) for r in s2 if not r["valid"])),
              "size_mults", sorted(set(r["size_mult"] for r in s2)), "tp", sorted(set(r["tp_mult"] for r in s2)))
print("F", res["F_parity"])
sys.stdout.flush()
os._exit(0)     # production threads (pollers, executors) are daemon/non-daemon; exit hard
