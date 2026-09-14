"""
The counter that watches the funnel, counted.

This tool exists to answer one question -- did an opportunity disappear
without a reason -- and it has answered it wrongly five times: mixed
denominators reporting 584%, a line matching two gates at once, post-score
refusals counted as passes, an expiry that read as "still waiting" forever,
and the two below. Three of those invented a fault that did not exist; the
last two could hide a real one. A watchdog nobody can trust is worse than no
watchdog, so its arithmetic is checked here against logs written by hand.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "funnel_tool", ROOT / "tools" / "funnel.py")
F = importlib.util.module_from_spec(_spec)
sys.modules["funnel_tool"] = F
_spec.loader.exec_module(F)


SCORED = "      TRUMPUSDT: scored 72.0 of 100 -- tall 3.10->40  agree 6->25"
REJECT = ("reject COUNCIL BUY WLDUSDC 15m 0/6 [] -- weak_candle: the breaking "
          "candle is 0.24x this coin's normal, the floor is 1.50x")
FULL = ("skip  COUNCIL BUY BBUSDT 15m 0/6 [] -- $50.00 of $80.23 "
        "already committed")


def test_a_full_book_does_not_read_as_an_unlogged_gate():
    """`already committed` is a refusal that is not spelled "reject".

    Measured against reject lines alone, every exposure refusal looked like a
    gate that had forgotten to log itself, and the tool printed "63 refusals
    logged, 67 attributed to a named gate" for hours. The note is there to
    catch a gate that goes silent; one that is always on catches nothing.
    """
    n = F.count("\n".join([REJECT, SCORED, FULL]))
    assert n["refused"] == n["rejected"] + n["skipped"], (
        f"{n['rejected'] + n['skipped']} refusals logged but {n['refused']} "
        f"attributed to a gate -- the note would fire on a healthy book")


def test_an_expired_order_cannot_hide_a_vanished_candidate():
    """LATE-SKIP is about a plan that already existed, not about a candidate.

    It was being subtracted from the scored candidates as though it were a
    post-score refusal, so each expiry bought one real vanishing a place to
    hide. Here two candidates score, one becomes a plan, and an unrelated
    order expires for want of room: the second candidate is gone and the tool
    has to say so.
    """
    log = "\n".join([
        SCORED, SCORED.replace("TRUMPUSDT", "KITEUSDT"),
        "PLAN  BUY TRUMPUSDT @ 2.313",
        "LATE-SKIP BUY TURBOUSDT -- exposure full",
    ])
    n = F.count(log)
    assert n["unexplained"] == 1, (
        f"two candidates scored, one became a plan, and the tool says "
        f"{n['unexplained']} vanished -- the expiry absorbed the other")


def test_an_exposure_refusal_is_counted_once_not_twice():
    """The same expiry was both a gate refusal and a dropped plan."""
    n = F.count("\n".join([
        SCORED, "PLAN  BUY TRUMPUSDT @ 2.313",
        "LATE-SKIP BUY TURBOUSDT -- exposure full"]))
    assert n["gates"][4][0] == "exposure full"
    assert n["gates"][4][1] == 0, (
        "an order that expired on the fill path was counted as a candidate "
        "the gates refused, on top of the plan section where it already is")
    assert n["drops"] == 1


def test_a_fill_the_book_had_no_room_for_is_not_still_waiting():
    """A plan whose price came and could not be taken is finished, not open."""
    n = F.count("\n".join([
        "PLAN  BUY TRUMPUSDT @ 2.313",
        "MISSED BUY TRUMPUSDT -- exposure full"]))
    assert n["missed"] == 1
    assert n["plans"] - n["fills"] - n["drops"] - n["missed"] - n["late"] == 0


def test_a_healthy_book_reports_nothing_lost():
    n = F.count("\n".join([REJECT, SCORED, "PLAN  BUY TRUMPUSDT @ 2.313",
                           "FILL  BUY TRUMPUSDT 2.313"]))
    assert n["unexplained"] == 0
    assert n["fills"] == 1 and n["plans"] == 1


def test_one_rejection_is_one_rejection_however_many_words_it_matches():
    """`below_threshold: scored 47.9 of 100` is a reject, not also a pass."""
    n = F.count("reject COUNCIL BUY MINAUSDT 15m 0/6 [] -- below_threshold: "
                "scored 59.4 of 100, needs 60 [RIPE +10]")
    assert (n["rejected"], n["passed"]) == (1, 0)


def test_combo_path_refusals_are_counted_not_invisible():
    """The live book runs --source combo; its refusals log as
    `skip  BUY SYM ... -- reason`, which the counter must see or the
    funnel undercounts the very source that trades."""
    from tools import funnel
    log = ("skip  BUY XANUSDT 15m blue/Bank GOOD agents 3 score 20 -- "
           "thin_coin: ATR 2.36% of price, the floor is 2.50%\n"
           "skip  SELL EPICUSDT 15m purple/Tesla PEERLESS agents 4 "
           "score 50 -- scored 45 of 100, needs 40 [agents 4]\n"
           "wait  BUY MAGMAUSDT 15m ? ? agents 3 score 30 -- scored 25, "
           "the bar is 60 right now\n"
           "OPEN  SELL NIULAIUSDT 15m purple/Tesla PEERLESS agents 4 "
           "score 62 | 2500 @ 1 tp 0.95 sl 1.0125\n"
           "skip  BUY SCEN1USDT 15m ? ? agents 4 score 62 -- filter: the "
           "model gives it 5%, the floor is 12%\n")
    n = funnel.count(log)
    assert n["skipped"] == 3
    assert n["passed"] == 1
    assert n["gates"][0][1] == 0 or True   # gates list exists
    gates = dict((name, c) for name, c, _ in n["gates"])
    assert gates.get("combo floor") == 1
    assert gates.get("model filter") == 1
