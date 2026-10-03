"""The chart's brain: candle-close triggers, the decision, the one line and the overall analysis."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain                                  # noqa: E402
import candles as cdl                         # noqa: E402
from engine import Bars                       # noqa: E402
from playbook import Playbook, Track          # noqa: E402
from test_playbook import T0, UTC, bullish_mss  # noqa: E402


def with_rows(b, rows):
    x = Bars(b.sec)
    for i in range(len(b)):
        x.append(b.t[i], b.o[i], b.h[i], b.l[i], b.c[i])
    for o, h, l, c in rows:
        x.append(x.t[-1] + x.sec, o, h, l, c)
    return x


def test_rejection_close_triggers_the_setup():
    m1 = with_rows(bullish_mss(), [(2002.8, 2003.6, 2001.5, 2003.4)])   # dips into the FVG, closes back up
    st = Playbook(Track(None, None)).update({"M1": m1}, UTC, spread=0.2)
    s = next(s for s in st["setups"] if s["model"] == "mss_fvg")
    tr = s["trigger"]
    assert tr and tr["fresh"] and tr["tf"] == "M1" and abs(tr["close"] - 2003.4) < 1e-9
    assert s["entry_now"]["verdict"] in ("ENTER", "SKIP")
    d = st["decision"]
    assert d["action"] in ("BUY NOW", "DON'T BUY")
    assert st["reads"]["M1"]["events"]


def test_no_trigger_on_a_close_against_the_trade():
    m1 = with_rows(bullish_mss(), [(2002.8, 2003.0, 2001.5, 2001.6)])   # into the zone but closes down
    st = Playbook(Track(None, None)).update({"M1": m1}, UTC, spread=0.2)
    s = next(s for s in st["setups"] if s["model"] == "mss_fvg")
    assert s["trigger"] is None
    assert not st["decision"]["action"].endswith("NOW")


class _Ctx:
    """The bits of playbook.Ctx that decide() reads."""
    news, bias, bias_why, price, clock = False, 0.5, "H4 up", 2003.4, {"next": []}
    tapes = {}

    def kronos_vote(self, d):
        return 1, "Kronos 30 min agrees: up 61%"


def test_decide_buy_now_names_the_model_and_candle():
    s = {"id": "u", "model": "unicorn", "name": "Unicorn (Breaker + FVG)", "tf": "M1", "dir": 1, "grade": "A+",
         "score": 0.9, "status": "filled", "why": ["swept Asian low 1998.00"], "sl": 1996.7, "zone": {"top": 2003, "bottom": 2001},
         "trigger": {"tf": "M1", "close": 2003.4, "fresh": True, "text": "M1 candle closed at 2003.40: tapped the zone "
                     "and closed back up as a Bullish Engulfing"},
         "entry_now": {"verdict": "ENTER", "missing": [], "checks": [{"label": "x", "ok": True, "key": "k"}]}}
    d = brain.decide(_Ctx(), [s], [{"model": "unicorn"}], {})
    assert d["action"] == "BUY NOW" and d["model"].startswith("Unicorn") and "Bullish Engulfing" in d["text"]


def test_desk_line_conflict_goes_flat_and_says_so():
    ict = {"bias": 0.0, "decision": {"action": "WAIT"}, "setups": []}
    up = {"t": 1000 - 60, "last": 2000.0, "up_prob": 0.7, "dir": 1, "confidence": 0.4, "move": 2.0,
          "path": [{"time": 1000 + 60 * k, "value": 2000 + 0.1 * k} for k in range(30)]}
    q = {"ok": True, "up_prob": 0.25, "dir": -1, "confidence": 0.5, "move": -2.0, "skill": {"beats_coin": True}}
    ln = brain.desk_line(ict, up, 1000, 2000.0, 0.3, quant=q)
    assert ln["state"] == "CONFLICT" and "disagree" in ln["label"]
    assert abs(ln["move"]) < 1.0
    ln2 = brain.desk_line(ict, up, 1000, 2000.0, 0.3)
    assert ln2["move"] > 0 and len(ln2["path"]) == 30 and len(ln2["band"]) == 30


def test_overall_weighs_every_voice_and_flags_news():
    ict = {"bias": 0.6, "bias_why": "H4 up", "decision": {"action": "BUY NOW", "dir": 1, "model": "Unicorn"},
           "timeframes": {"H1": {"trend": 1, "range": {"zone": "discount", "pos": 0.3}}, "M1": {"trend": 1}},
           "reads": {}, "clock": {"killzone": "London"}, "session": {"name": "London open"}, "line": {"state": "AGREE"}}
    o = brain.overall(ict, {"status": "ok", "bias": 0.4, "bias_text": "dovish Fed", "wait": False},
                      {"ok": True, "up_prob": 0.6, "call": "UP", "move": 1.0, "skill": {"beats_coin": True}},
                      {"up_prob": 0.62, "call": "UP", "move": 1.5})
    assert o["verdict"] == "BULLISH" and o["confidence"] > 0.3 and not o["against"]
    o2 = brain.overall(ict, {"status": "ok", "bias": 0.0, "wait": True, "wait_text": "CPI in 5 min"}, None, None)
    assert o2["do"] == "WAIT (news)" and any("CPI" in r for r in o2["risks"])


def test_candle_patterns_confirm_direction():
    b = Bars(60)
    for i, r in enumerate([(10, 10.1, 9.0, 9.1), (9.0, 10.4, 8.9, 10.3)]):
        b.append(i * 60, *r)
    assert cdl.confirms(b, 1, 1)["name"] == "Bullish Engulfing"
    assert cdl.confirms(b, 1, -1) is None


def _item(model="unicorn", stage="forming", conf=0.5, anchor=1, d=1):
    return {"model": model, "name": model.title(), "tf": "M1", "dir": d, "side": "BUY" if d == 1 else "SELL",
            "stage": stage, "progress": brain.STAGE_P[stage], "confidence": conf, "key": f"{model}:M1:{d}:{anchor}",
            "text": f"{model} {stage}", "steps": [], "next": "x"}


def test_announcer_pushes_best_once_and_upgrades():
    a = brain.Announcer()
    m = a.step([_item(conf=0.6), _item("turtle_soup", conf=0.3)], 10_000)
    assert len(m) == 1 and "Unicorn" in m[0]["title"] and "Turtle_Soup" in m[0]["body"]
    assert a.step([_item(conf=0.6)], 10_200) == []                    # same thing again: silent
    assert a.step([_item(anchor=2, conf=0.6)], 10_300) == []          # a new sweep, same model + stage: cooling down
    up = a.step([_item(stage="ready", conf=0.7)], 10_400)             # the stage moved on: pushed
    assert up and "READY" in up[0]["title"]
    now = a.step([_item(stage="enter", conf=0.8)], 10_410)            # entries never wait for the gap
    assert now and now[0]["priority"] == "urgent"
    assert a.step([_item(conf=0.9)], 20_000, market_open=False) == []
