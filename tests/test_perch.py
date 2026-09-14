"""The eagle: sitting over the coin about to print, not passing over it.

The scout walks the whole watchlist and circles a coin close to firing, but
the book's own window did not follow -- it sat on whatever the panel had
chosen, so the one chart watched continuously was almost never the one about
to print. This names the coin the guard should put in front of the book.

The chain has an order and these tests hold it: the finder keeps the biggest
candles, the scout walks only those, and this picks the one among them closest
to firing. A ripe coin the finder threw away is a print the book cannot trade.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import perch as PE  # noqa: E402


def _write(tmp_path, monkeypatch, rows, watch=None, atr=None, book=None):
    s = tmp_path / "scout.json"
    s.write_text(json.dumps(rows))
    monkeypatch.setattr(PE, "SCOUT", s)
    w = tmp_path / "watchlist.json"
    w.write_text(json.dumps(watch if watch is not None
                            else [r["sym"] for r in rows]))
    monkeypatch.setattr(PE, "WATCH", w)
    a = tmp_path / "atr_measures.json"
    a.write_text(json.dumps(atr or {}))
    monkeypatch.setattr(PE, "ATR_M", a)
    b = tmp_path / "paper.json"
    b.write_text(json.dumps(book or {"trades": []}))
    monkeypatch.setattr(PE, "BOOK", b)
    monkeypatch.setattr(PE, "WANTED", tmp_path / "chart_coins.json")
    return tmp_path / "chart_coins.json"


def r(sym, short, ripe, lean=0, age=0.0):
    return {"sym": sym, "short": short, "ripe": ripe, "lean": lean,
            "at": time.time() - age}


def test_the_coin_one_module_from_a_print_comes_first(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch,
           [r("AAAUSDT", 3, 100), r("BBBUSDT", 1, 80), r("CCCUSDT", 0, 70)])
    assert PE.ripest()[0] == "CCCUSDT"


def test_ripeness_breaks_a_tie_on_modules(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, [r("AAAUSDT", 1, 80), r("BBBUSDT", 1, 100)])
    assert PE.ripest()[0] == "BBBUSDT"


def test_between_two_equally_ripe_coins_the_bigger_candles_win(tmp_path,
                                                               monkeypatch):
    """A print on a coin that cannot travel is a print we cannot trade."""
    _write(tmp_path, monkeypatch,
           [r("AAAUSDT", 1, 100, 5), r("BBBUSDT", 1, 100, 5)],
           atr={"AAAUSDT": {"atr": 2.6}, "BBBUSDT": {"atr": 5.4}})
    assert PE.ripest()[0] == "BBBUSDT"


def test_a_ripe_coin_the_finder_threw_away_is_not_perched_on(tmp_path,
                                                             monkeypatch):
    _write(tmp_path, monkeypatch,
           [r("AAAUSDT", 0, 100), r("BBBUSDT", 2, 90)],
           watch=["BBBUSDT"])
    assert PE.ripest() == ["BBBUSDT"]


def test_a_stale_reading_is_not_a_reason_to_move_the_chart(tmp_path,
                                                           monkeypatch):
    """Ripeness is a count on a bar. An old one describes an old bar."""
    _write(tmp_path, monkeypatch,
           [r("AAAUSDT", 0, 100, age=3600), r("BBBUSDT", 2, 90)])
    assert PE.ripest() == ["BBBUSDT"]


def test_a_coin_the_book_holds_keeps_its_chart(tmp_path, monkeypatch):
    """A scout that wanders off a live trade is how a position goes blind."""
    out = _write(tmp_path, monkeypatch, [r("BBBUSDT", 0, 100)],
                 watch=["AAAUSDT", "BBBUSDT"],
                 book={"trades": [{"sym": "AAAUSDT", "closed": None}]})
    PE.main()
    got = json.loads(out.read_text())
    assert "AAAUSDT" in got, "the held coin lost its chart"
    # This used to assert the ripe coin was added BESIDE the held one. That
    # asked for a second holdable window, and there is no second holdable
    # window: TradingView streams two charts and the scout owns one of them.
    # The guard could never satisfy it, so with a position open it reported
    # "missing from the charts (rebuild did not take)" every two minutes,
    # forever. The held coin takes the one window there is.
    assert got == ["AAAUSDT"], "only the held coin should be asked for"


def test_nothing_ripe_leaves_the_chart_alone(tmp_path, monkeypatch):
    """Never blank the wanted list: the guard would have nothing to hold."""
    out = _write(tmp_path, monkeypatch, [])
    PE.main()
    assert not out.exists()


def test_the_wanted_list_is_written_whole_or_not_at_all(tmp_path,
                                                        monkeypatch):
    out = _write(tmp_path, monkeypatch, [r("AAAUSDT", 0, 100)])
    PE.main()
    assert json.loads(out.read_text()) == ["AAAUSDT"]
    assert not list(tmp_path.glob("*.tmp"))


def test_one_position_at_a_time_is_enforced_by_arithmetic_not_by_hope():
    """Half the wallet with a half-wallet ceiling leaves room for exactly one.

    The operator wants the book in one place at a time, at full size. That is
    not a preference the code can be trusted to remember -- it is --frac and
    --max-exposure agreeing. If either moved on its own the book would quietly
    start holding two.
    """
    sys.path.insert(0, str(ROOT / "tests"))
    from units import flag
    u = ROOT / "services" / "tbt-paper.service"
    frac = float(flag(u, "--frac"))
    cap = float(flag(u, "--max-exposure") or 1.0)
    assert int(cap / frac) == 1, (
        f"--frac {frac} under a --max-exposure of {cap} leaves room for "
        f"{int(cap / frac)} positions at once, not one")


def test_nothing_caps_how_often_the_book_may_be_right():
    """A daily cap limits good trades as readily as bad ones.

    What limits trading is judgement -- the entry score -- not a counter.
    """
    sys.path.insert(0, str(ROOT / "tests"))
    from units import flag
    u = ROOT / "services" / "tbt-paper.service"
    assert int(flag(u, "--max-per-day") or 0) == 0, (
        "a daily cap is back; it stops the book being right as readily as "
        "being wrong")
    assert float(flag(u, "--min-entry") or 0) > 0, (
        "the cap is gone and nothing replaced it -- with no entry score the "
        "book takes whatever prints")


def test_one_stop_cannot_halve_the_book():
    """At --frac f and --lev L a stop of s% costs f * s * L of the account."""
    sys.path.insert(0, str(ROOT / "tests"))
    from units import flag
    u = ROOT / "services" / "tbt-paper.service"
    cost = (float(flag(u, "--frac")) * float(flag(u, "--sl")) / 100
            * float(flag(u, "--lev")) * 100)
    assert cost <= 30.0, (
        f"one stop costs {cost:.0f}% of the account -- three in a row and "
        f"there is not much book left to trade with")


def test_the_eagle_prefers_the_expanding_coin_at_equal_ripeness(tmp_path,
                                                               monkeypatch):
    """The measured rule: an expansion of 2x carries a 36.6% chance of a
    5% move in two hours against 4.2% when quiet -- the eagle sits there
    first when two coins are equally close to printing."""
    now = time.time()
    rows = [
        {"kind": "ripe", "sym": "QUIETUSDT", "at": now,
         "short": 1, "ripe": 80.0, "lean": 3, "exp": 1.2},
        {"kind": "ripe", "sym": "LOUDUSDT", "at": now,
         "short": 1, "ripe": 80.0, "lean": 3, "exp": 2.4},
    ]
    _write(tmp_path, monkeypatch, rows,
           atr={"QUIETUSDT": {"atr": 5.0},
                "LOUDUSDT": {"atr": 5.0}})
    want = PE.ripest()
    assert want[0] == "LOUDUSDT", want
    # and a missing expansion (an older row) never beats a measured one:
    # with both unexpanded, the riper coin wins
    rows[1]["exp"] = None
    rows[0]["ripe"] = 90.0
    _write(tmp_path, monkeypatch, rows,
           atr={"QUIETUSDT": {"atr": 5.0}, "LOUDUSDT": {"atr": 5.0}})
    want = PE.ripest()
    assert want[0] == "QUIETUSDT", want


def test_closeness_still_outranks_expansion(tmp_path, monkeypatch):
    """One module short beats two modules short, expanding or not."""
    now = time.time()
    rows = [
        {"kind": "ripe", "sym": "FARUSDT", "at": now,
         "short": 1, "ripe": 70.0, "lean": 1, "exp": 3.0},
        {"kind": "ripe", "sym": "NEARUSDT", "at": now,
         "short": 2, "ripe": 99.0, "lean": 5, "exp": 1.0},
    ]
    _write(tmp_path, monkeypatch, rows,
           atr={"FARUSDT": {"atr": 5.0}, "NEARUSDT": {"atr": 5.0}})
    assert PE.ripest()[0] == "FARUSDT"


def test_the_tide_outranks_the_shape_when_weights_are_equal(tmp_path,
                                                           monkeypatch):
    """Measured: tide against the print is 0.12 of tide with it; the
    eagle must not sit on a coin whose print would fight the tide."""
    now = time.time()
    rows = [
        {"kind": "ripe", "sym": "AGAINST", "at": now,
         "short": 1, "ripe": 90.0, "lean": 5, "exp": 3.0,
         "side": 1, "tide": -1},
        {"kind": "ripe", "sym": "WITHTIDE", "at": now,
         "short": 1, "ripe": 90.0, "lean": 5, "exp": 2.0,
         "side": 1, "tide": 1},
    ]
    _write(tmp_path, monkeypatch, rows,
           atr={"AGAINST": {"atr": 6.0}, "WITHTIDE": {"atr": 6.0}})
    assert PE.ripest()[0] == "WITHTIDE"


def test_nothing_ripe_sits_on_the_expanding_coin(tmp_path, monkeypatch):
    """The fallback: no print in sight, so the eagle sits where the move
    is brewing -- the most-expanding coin the scout walked."""
    now = time.time()
    rows = [
        {"kind": "state", "sym": "QUIETUSDT", "at": now, "exp": 0.8},
        {"kind": "state", "sym": "BREWING", "at": now, "exp": 2.6},
        {"kind": "state", "sym": "MIDUSDT", "at": now, "exp": 1.4},
    ]
    _write(tmp_path, monkeypatch, rows,
           atr={"QUIETUSDT": {"atr": 5.0}, "BREWING": {"atr": 4.0},
                "MIDUSDT": {"atr": 9.0}})
    assert PE.ripest()[0] == "BREWING"
