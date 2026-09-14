"""
The scout, and the file it hands the book.

One window walks a hundred and fifty coins a few seconds each, writes down
what it finds, and moves on. Everything it gets wrong arrives at the book as a
confident number about a coin the chart is no longer showing.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import market as M
import papertrade as P
import scout as S
from fakes import FakeCDP, pack_votes


def window(sym="TESTUSDT", bars=None, votes=None, plots=None):
    FakeCDP.install({0: {"symbol": f"BITUNIX:{sym}.P", "res": "15",
                         "ohlc": bars if bars is not None else {},
                         "votes": votes if votes is not None else 0,
                         "plots": plots or {}}})
    return FakeCDP(target_index=0)


def pack_state(plan_dir=1, ready=1, votes=5, fib_dir=0, fib_hit=0,
               ma_dir=0, ma_over=0):
    return ((plan_dir + 1) + 3 * ready + 6 * votes + 42 * (fib_dir + 1)
            + 126 * fib_hit + 252 * (ma_dir + 1) + 756 * ma_over)


# ------------------------------------------------------------------- the root
def test_the_scout_finds_its_own_root_rather_than_a_written_path():
    """It used to chdir into a literal /home/tbt/bot before anything ran."""
    assert S.BOT == Path(__file__).resolve().parent.parent
    assert S.FOUND == S.BOT / "data" / "scout.json"


# ------------------------------------------------------------------- shapes
@pytest.mark.parametrize("up", [True, False])
def test_the_scout_reads_the_same_shape_the_book_would(up):
    """The scout and the book must agree, or the book refuses what it is sent."""
    bars, sig_t = M.band_then_break(thrust=3.5, up=up)
    c = bars[sig_t][3]
    bars[sig_t + M.BAR] = (c, c * 1.0002, c * 0.9998, c)   # the forming bar
    want = 1 if up else -1
    c_win = window(votes=pack_votes(Bank=want, Team45=want, Tesla=want,
                                    Sniper=want), bars=bars)
    got = S.read_break(c_win, "TESTUSDT")
    assert got is not None
    assert got["side"] == ("BUY" if up else "SELL")
    assert got["kind"] == "break"
    assert got["t"] == sig_t, "the shape must be pinned to its own closed bar"
    assert got["agree"] == 4 and got["against"] == 0
    direct = P.breakout(bars, sig_t, max_stop=P._stop_room())
    assert got["level"] == direct["level"]
    assert got["stop_pct"] == pytest.approx(direct["stop_pct"])


def test_the_scout_never_reads_the_bar_still_forming():
    """A shape is a statement about a finished candle."""
    bars = M.quiet_band(n=45)
    ks = sorted(bars)
    typ = sum((bars[k][1] - bars[k][2]) / bars[k][3] for k in ks) / len(ks)
    top = max(bars[k][1] for k in ks)
    # a perfect break, but it is the bar in progress
    forming = ks[-1] + M.BAR
    rng = typ * 4.0 * top
    bars[forming] = (top * 1.0005, top + rng, top * 0.999, top + rng * 0.9)
    assert S.read_break(window(bars=bars), "TESTUSDT") is None


def test_the_scout_looks_back_further_than_the_newest_closed_bar():
    """It is away visiting other coins; a shape must not go unseen for that."""
    bars, sig_t = M.band_then_break(thrust=3.5, up=True)
    px = bars[sig_t][3]
    for i in range(1, 4):                 # three quiet bars since the break
        bars[sig_t + i * M.BAR] = (px, px * 1.0005, px * 0.9995, px)
    got = S.read_break(window(bars=bars), "TESTUSDT")
    assert got is not None and got["t"] == sig_t


def test_a_shape_older_than_the_lookback_is_let_go():
    bars, sig_t = M.band_then_break(thrust=3.5, up=True)
    px = bars[sig_t][3]
    for i in range(1, S.LOOKBACK + 4):
        bars[sig_t + i * M.BAR] = (px, px * 1.0005, px * 0.9995, px)
    assert S.read_break(window(bars=bars), "TESTUSDT") is None


def test_an_empty_chart_produces_nothing_rather_than_an_error():
    assert S.read_break(window(bars={}), "TESTUSDT") is None
    assert S.read_coil(window(bars={})) is None


# ------------------------------------------------------------------ council
def test_a_council_plan_carries_its_own_measurements():
    """The window has walked on by the time the book reads this."""
    bars = M.quiet_band(n=120)
    c = window(bars=bars, votes=pack_votes(Bank=1, Team45=1),
               plots={"STATE": pack_state(plan_dir=1),
                      "PLAN_ENTRY": 100.0})
    got = S.read_council(c)
    assert got is not None
    assert got["dir"] == 1 and got["entry"] == 100.0
    # without these the book silently skips every candle test it has
    for k in ("expansion", "body", "wick", "run", "leg"):
        assert k in got


def test_a_council_row_with_no_plan_is_not_written_down():
    bars = M.quiet_band(n=120)
    c = window(bars=bars, plots={"STATE": pack_state(plan_dir=0),
                                 "PLAN_ENTRY": 100.0})
    assert S.read_council(c) is None


def test_a_council_plan_with_no_entry_level_is_not_written_down():
    """A plan the book cannot rest an order at is not a plan."""
    bars = M.quiet_band(n=120)
    c = window(bars=bars, plots={"STATE": pack_state(plan_dir=1),
                                 "PLAN_ENTRY": None})
    assert S.read_council(c) is None


def test_the_councils_wick_reading_cannot_see_past_its_own_bar():
    """It is measured on the plan's bar, not on whatever came after it."""
    bars = M.quiet_band(n=120)
    last = max(bars)
    c = window(bars=bars, plots={"STATE": pack_state(plan_dir=1),
                                 "PLAN_ENTRY": 100.0})
    before = S.read_council(c)["wick"]
    px = bars[last][3]
    for i in range(1, 40):                    # a violent hour, afterwards
        bars[last + i * M.BAR] = (px, px * 1.001, px * 0.85, px * 0.88)
    c2 = window(bars=bars, plots={"STATE": (lambda t: pack_state(1)
                                            if t == last else pack_state(0)),
                                  "PLAN_ENTRY": 100.0})
    got = S.read_council(c2)
    # the plan row is now an older bar; its wick reading must be its own
    assert got is None or got["wick"] == pytest.approx(before) or got["t"] != last


# -------------------------------------------------------------------- ripe
def test_a_ripe_reading_needs_the_higher_timeframe_with_it():
    bars = M.quiet_band(n=30)
    packed = pack_votes(Bank=1, Team45=1, Tesla=1, Sniper=1)
    c = window(bars=bars, votes=packed,
               plots={"SNIP_BUY_VOTE": 2, "SNIP_SELL_VOTE": 0, "HTF_BIAS": -1})
    assert S.read_ripe(c) is None            # the tide is against it
    c2 = window(bars=bars, votes=packed,
                plots={"SNIP_BUY_VOTE": 2, "SNIP_SELL_VOTE": 0, "HTF_BIAS": 1})
    got = S.read_ripe(c2)
    assert got is not None and got["kind"] == "ripe"
    assert got["ripe"] >= S.RIPE_MIN
    assert "entry" not in got, "a forecast must never look like an order"


def test_a_coil_is_only_reported_at_the_extreme_of_the_scale():
    """Below the threshold the reading is indistinguishable from noise."""
    assert S.COIL_MIN >= 60.0
    bars = M.quiet_band(n=120)
    got = S.read_coil(window(bars=bars))
    assert got is None or got["pressure"] >= S.COIL_MIN


# ------------------------------------------------------- what the book reads
def test_the_book_reads_only_the_rows_that_are_orders(tmp_path):
    """`ripe` and `coil` carry no entry and no stop; 69 polls died on them."""
    rows = [
        {"kind": "ripe", "sym": "AAAUSDT", "side": 1, "ripe": 90.0,
         "t": M.T0, "at": M.T0},
        {"kind": "coil", "sym": "BBBUSDT", "pressure": 80.0, "t": M.T0,
         "at": M.T0},
        {"kind": "break", "sym": "CCCUSDT", "dir": 1, "entry": 100.0,
         "t": M.T0, "at": M.T0, "votes": 3},
    ]
    f = tmp_path / "scout.json"
    f.write_text(json.dumps(rows))
    import time
    from unittest import mock
    with mock.patch.object(P, "SCOUT", f), \
            mock.patch.object(time, "time", lambda: M.T0 + 60):
        out = P.scout_plans(set(), set())
    assert [x["sym"] for x in out] == ["CCCUSDT"]
    assert out[0]["side"] == "BUY" and out[0]["entry"] == 100.0


def test_a_plan_on_a_coin_a_window_already_holds_is_dropped(tmp_path):
    rows = [{"kind": "break", "sym": "CCCUSDT", "dir": 1, "entry": 100.0,
             "t": M.T0, "at": M.T0}]
    f = tmp_path / "scout.json"
    f.write_text(json.dumps(rows))
    import time
    from unittest import mock
    with mock.patch.object(P, "SCOUT", f), \
            mock.patch.object(time, "time", lambda: M.T0 + 60):
        assert P.scout_plans(set(), {"CCCUSDT"}) == []


def test_a_plan_is_only_ever_handed_over_once(tmp_path):
    rows = [{"kind": "break", "sym": "CCCUSDT", "dir": 1, "entry": 100.0,
             "t": M.T0, "at": M.T0}]
    f = tmp_path / "scout.json"
    f.write_text(json.dumps(rows))
    import time
    from unittest import mock
    seen = set()
    with mock.patch.object(P, "SCOUT", f), \
            mock.patch.object(time, "time", lambda: M.T0 + 60):
        assert len(P.scout_plans(seen, set())) == 1
        assert P.scout_plans(seen, set()) == []


def test_a_plan_past_its_shelf_life_is_dropped(tmp_path):
    rows = [{"kind": "break", "sym": "CCCUSDT", "dir": 1, "entry": 100.0,
             "t": M.T0, "at": M.T0}]
    f = tmp_path / "scout.json"
    f.write_text(json.dumps(rows))
    import time
    from unittest import mock
    with mock.patch.object(P, "SCOUT", f), \
            mock.patch.object(time, "time", lambda: M.T0 + 60 * 60 * 3):
        assert P.scout_plans(set(), set()) == []


def test_the_scouts_file_is_replaced_not_edited():
    """A book reading it mid-write must never see half a document."""
    src = Path(S.__file__).read_text()
    assert "os.replace(tmp, FOUND)" in src


def test_the_scout_closes_its_socket_on_every_path():
    """One connection per coin per sweep, leaked on every failure, all night."""
    src = Path(S.__file__).read_text()
    body = src[src.index("for sym in coins:"):]
    assert "finally:" in body and "c.close()" in body


# ------------------------------------------------- which coins get walked
def test_a_short_watchlist_is_still_the_watchlist(tmp_path, monkeypatch):
    """Eight names is a decision, not a truncated file.

    This used to need ten before it counted -- a guard against reading a file
    mid-write, which outlived the problem once the scanner started writing
    atomically. What it did instead was hide a replacement: the ATR finder
    keeps only coins whose candles are big enough to reach the target, and on
    a calm morning that is eight names. The scout took the short list as a
    broken one and fell back to a ranking written by a scanner that had been
    switched off, walking coins nobody had chosen, with nothing in the log to
    say so.
    """
    watch = tmp_path / "watchlist.json"
    ranked = tmp_path / "boom.json"
    watch.write_text(json.dumps(["AAAUSDT", "BBBUSDT", "CCCUSDT"]))
    ranked.write_text(json.dumps([{"sym": "OLDUSDT"}]))
    monkeypatch.setattr(S, "WATCH", watch)
    monkeypatch.setattr(S, "RANKED", ranked)
    assert S.ranked(40) == ["AAAUSDT", "BBBUSDT", "CCCUSDT"]


def test_an_empty_watchlist_falls_back_and_says_so(tmp_path, monkeypatch,
                                                   caplog):
    """A fallback that fires quietly is worse than no fallback."""
    watch = tmp_path / "watchlist.json"
    ranked = tmp_path / "boom.json"
    watch.write_text("[]")
    ranked.write_text(json.dumps([{"sym": "OLDUSDT"}]))
    monkeypatch.setattr(S, "WATCH", watch)
    monkeypatch.setattr(S, "RANKED", ranked)
    with caplog.at_level("WARNING"):
        got = S.ranked(40)
    assert got == ["OLDUSDT"]
    assert any("watchlist" in r.message for r in caplog.records), (
        "the scout fell back to the old ranking without a word")


def test_a_watchlist_that_will_not_parse_falls_back_and_says_so(
        tmp_path, monkeypatch, caplog):
    watch = tmp_path / "watchlist.json"
    ranked = tmp_path / "boom.json"
    watch.write_text("{not json")
    ranked.write_text(json.dumps([{"sym": "OLDUSDT"}]))
    monkeypatch.setattr(S, "WATCH", watch)
    monkeypatch.setattr(S, "RANKED", ranked)
    with caplog.at_level("WARNING"):
        assert S.ranked(40) == ["OLDUSDT"]
    assert any("will not read" in r.message for r in caplog.records)


# ------------------------------------ the indicator's own signal, walked
def test_the_scout_reads_the_indicators_signal_off_its_own_plots():
    """Nothing is re-derived. Score, tier and agents come off the study."""
    #     t   BUY_SCORE  SELL_SCORE  TIER  SPAN  VOTE  WIRED
    rows = [
        [1000,   0,        0,        0,    0,    0,    0],
        [1900,  55.0,      0,        3,   12,    5,    1],   # last CLOSED bar
        [2800,  90.0,      0,        3,   20,    6,    1],   # forming, ignored
    ]

    class Study:
        def find_study(self, needle):
            return "s1"

        def raw_series(self, sid, limit=None):
            return {"plots": ["TBT_BUY_SCORE", "TBT_SELL_SCORE",
                              "TBT_BUY_TIER", "TBT_BUY_SPAN",
                              "SNIP_BUY_VOTE", "TSL_WIRED"],
                    "rows": rows}

    got = S.read_combo(Study())
    assert got is not None
    assert got["kind"] == "combo" and got["side"] == "BUY"
    assert got["score"] == 55 and got["agents"] == 5, (
        "the forming bar was read instead of the last closed one")
    assert got["t"] == 1900


def test_the_forming_bar_is_never_the_signal():
    """confirmOnly is off by default, so a print can appear and vanish.

    The indicator can light up inside a candle and be dark again by its close.
    Reading the forming bar would trade signals that never existed.
    """
    rows = [[1000, 0, 0, 0, 0, 0, 0], [1900, 0, 0, 0, 0, 0, 0],
            [2800, 99.0, 0, 3, 9, 6, 1]]

    class Study:
        def find_study(self, needle):
            return "s1"

        def raw_series(self, sid, limit=None):
            return {"plots": ["TBT_BUY_SCORE", "TBT_SELL_SCORE",
                              "TBT_BUY_TIER", "TBT_BUY_SPAN",
                              "SNIP_BUY_VOTE", "TSL_WIRED"],
                    "rows": rows}

    assert S.read_combo(Study()) is None


def test_a_chart_with_no_tbt_study_reports_nothing():
    class Bare:
        def find_study(self, needle):
            return None

    assert S.read_combo(Bare()) is None


def test_the_state_row_is_never_mistaken_for_something_to_trade():
    """It exists to be studied, not acted on.

    A state row carries no entry, no agents and no score -- it is what the
    indicator is saying on a bar with no signal. The book's scout reader acts
    on kind "combo" and on rows carrying a direction and an entry; this must
    match neither.
    """
    rows = [[1000, 0, 0], [1900, 5, 1], [2800, 6, 1]]

    class Study:
        def find_study(self, needle):
            return "s1"

        def raw_series(self, sid, limit=None):
            return {"plots": ["STATE", "TSL_WIRED"], "rows": rows}

    got = S.read_state(Study())
    assert got is not None
    assert got["kind"] == "state"
    assert "entry" not in got, "a state row carries an entry the book could rest"
    assert got.get("agents") is None


def test_the_walk_spends_its_eyesight_on_the_expanding_coins():
    coins = ["AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT"]
    exp = {"AAAUSDT": 0.6, "BBBUSDT": 2.5, "CCCUSDT": 1.4, "DDDUSDT": 3.0}
    order = S.walk_order(coins, ["CCCUSDT"], exp)
    assert order[0] == "CCCUSDT"           # perched circles first
    assert order[1] == "DDDUSDT"           # then the most expanding
    assert order[-1] == "AAAUSDT"          # the quiet one goes last


def test_dwell_gives_the_long_look_to_the_expanding_coin():
    assert S.dwell_for(3.0, 3.0) == 4.5
    assert S.dwell_for(1.5, 3.0) == 3.0
    assert S.dwell_for(0.4, 3.0) == 1.5
    assert S.dwell_for(None, 3.0) == 3.0
    assert S.dwell_for(0.1, 0.8) == 1.0    # never less than a second
