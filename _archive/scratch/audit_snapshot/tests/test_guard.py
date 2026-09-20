"""
The watchdog, against the failures it exists for.

systemd restarts a process that exits. None of the failures that have actually
cost this system anything look like a process exiting.
"""
from __future__ import annotations

import time
from unittest import mock

import pytest

import guard as G
from fakes import FakeCDP

BAR = 900
NOW = 1788500000.0


def rows_ending(newest_t, n=200, step=BAR):
    return [[newest_t - (n - 1 - i) * step, 1] for i in range(n)]


# --------------------------------------------------------------- staleness
def test_a_live_chart_is_not_called_stale():
    with mock.patch.object(time, "time", lambda: NOW):
        age = G.newest_bar_age(rows_ending(NOW - 60), "15")
    assert age < BAR


def test_a_stopped_feed_is_measured_in_hours():
    """The fourteen-hour failure, as a number.

    The tab still answers every question: study loaded, symbol right,
    timeframe right, two hundred bars. Every candle is simply frozen at the
    moment the feed died -- and nothing in this file used to look at when.
    """
    with mock.patch.object(time, "time", lambda: NOW):
        age = G.newest_bar_age(rows_ending(NOW - 14 * 3600), "15")
    assert age == pytest.approx(14 * 3600)
    assert age > G.STALE_BARS * BAR


def test_a_chart_with_no_rows_reports_nothing_rather_than_zero():
    assert G.newest_bar_age([], "15") is None
    assert G.newest_bar_age(None, "15") is None
    assert G.newest_bar_age([[None, 1]], "15") is None


def test_the_freeze_is_reported_by_the_chart_check():
    """The whole point: a frozen window has to become a problem, out loud."""
    FakeCDP.install({0: {"symbol": "BITUNIX:AAAUSDT.P", "res": "15",
                         "ohlc": {}, "votes": 0}})
    frozen = FakeCDP.windows[0]
    frozen.ohlc = {NOW - 14 * 3600 - i * BAR: (1, 1, 1, 1) for i in range(200)}

    class Series(FakeCDP):
        def raw_series(self, study_id, limit=None):
            ks = sorted(self._src.ohlc)
            return {"symbol": self._src.symbol, "res": "15",
                    "plots": ["TSL_WIRED"],
                    "rows": [[t, 1] for t in ks]}

    with mock.patch("signals.tv_cdp.TradingViewCDP", Series), \
            mock.patch.object(time, "time", lambda: NOW):
        ok, detail, info = G.check_charts(["AAAUSDT"], "15")
    assert not ok
    assert "frozen" in detail, detail
    assert info["stale"] == [0]


def test_a_healthy_chart_passes_the_same_check():
    FakeCDP.install({0: {"symbol": "BITUNIX:AAAUSDT.P", "res": "15",
                         "ohlc": {}, "votes": 0}})
    live = FakeCDP.windows[0]
    live.ohlc = {NOW - 120 - i * BAR: (1, 1, 1, 1) for i in range(200)}

    class Series(FakeCDP):
        def raw_series(self, study_id, limit=None):
            ks = sorted(self._src.ohlc)
            return {"symbol": self._src.symbol, "res": "15",
                    "plots": ["TSL_WIRED"],
                    "rows": [[t, 1] for t in ks]}

    with mock.patch("signals.tv_cdp.TradingViewCDP", Series), \
            mock.patch.object(time, "time", lambda: NOW):
        ok, detail, info = G.check_charts(["AAAUSDT"], "15")
    assert ok, detail
    assert info["stale"] == []


# ----------------------------------------------------------- other failures
def test_two_windows_on_one_coin_are_named_not_inferred():
    """It halves the engine's coverage without producing a single error."""
    FakeCDP.install({
        0: {"symbol": "BITUNIX:AAAUSDT.P", "res": "15", "ohlc": {}, "votes": 0},
        1: {"symbol": "BITUNIX:AAAUSDT.P", "res": "15", "ohlc": {}, "votes": 0},
    })
    for w in FakeCDP.windows.values():
        w.ohlc = {NOW - 120 - i * BAR: (1, 1, 1, 1) for i in range(200)}

    class Series(FakeCDP):
        def raw_series(self, study_id, limit=None):
            ks = sorted(self._src.ohlc)
            return {"symbol": self._src.symbol, "res": "15",
                    "plots": ["TSL_WIRED"], "rows": [[t, 1] for t in ks]}

    with mock.patch("signals.tv_cdp.TradingViewCDP", Series), \
            mock.patch.object(time, "time", lambda: NOW):
        ok, detail, _ = G.check_charts(["AAAUSDT", "BBBUSDT"], "15")
    assert not ok
    assert "both on AAAUSDT" in detail


def test_a_chart_on_the_wrong_timeframe_is_reported():
    FakeCDP.install({0: {"symbol": "BITUNIX:AAAUSDT.P", "res": "5",
                         "ohlc": {}, "votes": 0}})
    FakeCDP.windows[0].ohlc = {NOW - 60 - i * 300: (1, 1, 1, 1)
                               for i in range(200)}

    class Series(FakeCDP):
        def raw_series(self, study_id, limit=None):
            ks = sorted(self._src.ohlc)
            return {"symbol": self._src.symbol, "res": "5",
                    "plots": ["TSL_WIRED"], "rows": [[t, 1] for t in ks]}

    with mock.patch("signals.tv_cdp.TradingViewCDP", Series), \
            mock.patch.object(time, "time", lambda: NOW):
        ok, detail, _ = G.check_charts(["AAAUSDT"], "15")
    assert not ok and "5m not 15m" in detail


def test_the_guard_never_restarts_the_trading_services():
    """Trading is stopped on purpose; a watchdog must not undo that."""
    assert "tbt-paper" not in G.SERVICES
    assert "tbt-scout" not in G.SERVICES
    assert set(G.SERVICES) == {"tbt-xvfb", "tbt-wm", "tbt-chrome"}


def test_a_repair_waits_for_a_second_opinion():
    """Repairs restart things; one bad reading is usually a service coming up."""
    src = __import__("pathlib").Path(G.__file__).read_text()
    assert "first strike, waiting" in src


def test_the_recheck_uses_the_timeframe_that_was_asked_for():
    """It defaulted to 15, so any other setting rechecked against the wrong one."""
    src = __import__("pathlib").Path(G.__file__).read_text()
    assert "check_charts(wanted(a.charts))" not in src


# ------------------------------------------------- the window the scout owns
def _windows(spec):
    """spec: {index: symbol}. All fresh, all on 15m."""
    FakeCDP.install({i: {"symbol": f"BITUNIX:{s}.P", "res": "15",
                         "ohlc": {}, "votes": 0} for i, s in spec.items()})
    for w in FakeCDP.windows.values():
        w.ohlc = {NOW - 120 - i * BAR: (1, 1, 1, 1) for i in range(200)}

    class Series(FakeCDP):
        # A healthy window shows real candles. A test that wants the Heikin
        # Ashi fault says so by overriding this, rather than every other test
        # having to know the style question exists.
        style = "1"

        def raw_series(self, study_id, limit=None):
            ks = sorted(self._src.ohlc)
            return {"symbol": self._src.symbol, "res": "15",
                    "plots": ["TSL_WIRED"], "rows": [[t, 1] for t in ks]}

        def evaluate(self, expression, retry=True):
            if "mainSeries" in expression and "style" in expression:
                return self.style
            return super().evaluate(expression, retry)
    return Series


def test_the_scouts_window_is_not_held_to_a_coin():
    """The alarm that fired every two minutes and could never be fixed.

    The scout changes window 1's coin every few seconds by design. Demanding
    a fixed symbol there is a fault no repair can clear -- and repairing it
    means two services setting the same chart to different coins all night.
    """
    Series = _windows({0: "PONSUSDT", 1: "WHATEVERUSDT"})
    with mock.patch("signals.tv_cdp.TradingViewCDP", Series), \
            mock.patch.object(time, "time", lambda: NOW):
        ok, detail, _ = G.check_charts(["PONSUSDT", "TRIAUSDT"], "15",
                                       scout_window=1)
    assert ok, detail
    assert "TRIAUSDT" not in detail


def test_without_a_scout_both_windows_are_still_held():
    """Turning the flag off must not turn the check off."""
    Series = _windows({0: "PONSUSDT", 1: "WHATEVERUSDT"})
    with mock.patch("signals.tv_cdp.TradingViewCDP", Series), \
            mock.patch.object(time, "time", lambda: NOW):
        ok, detail, _ = G.check_charts(["PONSUSDT", "TRIAUSDT"], "15",
                                       scout_window=-1)
    assert not ok
    assert "TRIAUSDT missing" in detail


def test_the_book_window_is_still_held_to_its_coin():
    """The scout's exemption must not cover the window the book reads."""
    Series = _windows({0: "WRONGUSDT", 1: "WHATEVERUSDT"})
    with mock.patch("signals.tv_cdp.TradingViewCDP", Series), \
            mock.patch.object(time, "time", lambda: NOW):
        ok, detail, _ = G.check_charts(["PONSUSDT"], "15", scout_window=1)
    assert not ok
    assert "PONSUSDT missing" in detail


def test_the_scouts_window_is_still_checked_for_a_stopped_feed():
    """Exempt from which coin, not from being alive."""
    Series = _windows({0: "PONSUSDT", 1: "WHATEVERUSDT"})
    FakeCDP.windows[1].ohlc = {NOW - 14 * 3600 - i * BAR: (1, 1, 1, 1)
                               for i in range(200)}
    with mock.patch("signals.tv_cdp.TradingViewCDP", Series), \
            mock.patch.object(time, "time", lambda: NOW):
        ok, detail, info = G.check_charts(["PONSUSDT"], "15", scout_window=1)
    assert not ok
    assert "frozen" in detail and info["stale"] == [1]


def test_the_guard_never_sets_a_symbol_on_the_scouts_window():
    src = __import__("pathlib").Path(G.__file__).read_text()
    body = src[src.index("def repair_charts"):]
    assert "i != scout_window" in body, (
        "the guard can still drag the scout's window onto a coin")


def test_the_live_unit_tells_the_guard_which_window_the_scout_owns():
    unit = (__import__("pathlib").Path(G.__file__).resolve().parent
            / "services" / "tbt-guard.service").read_text()
    assert "--scout-window 1" in unit
    scout = (__import__("pathlib").Path(G.__file__).resolve().parent
             / "services" / "tbt-scout.service").read_text()
    assert "--window 1" in scout, "the scout moved and the guard was not told"


def test_the_scouts_window_has_to_actually_exist():
    """One window, and the scout walks window 1. There is no window 1.

    `want` is a wish -- the check below trims it to whatever windows are free,
    and that is deliberate. The scout's window is not a wish: nearly every
    candidate the book sees arrives through it. Nothing counted it. On
    2026-09-05 a browser restart came back with a single window, the wanted
    list held one coin, and this check reported all clear while the scout
    swept zero coins and the engine watched one coin instead of 348.
    """
    Series = _windows({0: "PONSUSDT"})
    with mock.patch("signals.tv_cdp.TradingViewCDP", Series), \
            mock.patch.object(time, "time", lambda: NOW):
        ok, detail, _ = G.check_charts(["PONSUSDT"], "15", scout_window=1)
    assert not ok, "the scout's window is missing and the guard said nothing"
    assert "scout walks window 1" in detail, detail


def test_a_wanted_list_longer_than_the_windows_is_still_not_a_fault():
    """The wish is trimmed, not enforced -- that behaviour is deliberate."""
    Series = _windows({0: "PONSUSDT", 1: "WHATEVERUSDT"})
    with mock.patch("signals.tv_cdp.TradingViewCDP", Series), \
            mock.patch.object(time, "time", lambda: NOW):
        ok, detail, _ = G.check_charts(["PONSUSDT", "TRIAUSDT"], "15",
                                       scout_window=1)
    assert ok, detail


def test_the_repair_opens_the_scouts_window_as_well():
    """What the check demands, the repair has to be able to produce."""
    import inspect
    src = inspect.getsource(G.repair_charts)
    assert "scout_window + 1" in src, (
        "repair_charts opens only len(want) windows, so the shortfall the "
        "check now reports would be reported forever and never repaired")


def test_the_health_check_asks_the_browser_not_the_startup_banner():
    """A window count printed once at startup is not a current fact.

    The book prints "following N chart window(s)" one time, as it starts. A
    book that came up while chrome was still starting printed 0 and attached
    seconds later, and the health check went on reporting an outage for the
    life of the process -- on 2026-09-05, twenty minutes after the guard had
    already repaired it.
    """
    from pathlib import Path
    src = (Path(G.__file__).resolve().parent / "tools"
           / "healthcheck.py").read_text()
    assert "following 0 chart window" not in src, (
        "the health check still latches onto a line the book printed once at "
        "startup instead of asking how many windows exist now")
    assert "chart_windows()" in src


# ------------------------------------------- what the account can stream
def test_a_third_chart_window_is_a_fault_not_extra_eyesight():
    """TradingView streams two charts on this account. The third dies quietly.

    A third window opens, loads, shows a price, and then its feed stops --
    with no error anywhere. The book goes on reading a chart whose newest
    candle is an hour old. Every "FROZEN" line on 2026-09-05 was this, not a
    browser fault: USELESSUSDT twice and NOMUSDT once, all while three or four
    windows were open.
    """
    Series = _windows({0: "AAAUSDT", 1: "BBBUSDT", 2: "CCCUSDT"})
    with mock.patch("signals.tv_cdp.TradingViewCDP", Series), \
            mock.patch.object(time, "time", lambda: NOW):
        ok_, detail, _ = G.check_charts(["AAAUSDT"], "15", scout_window=1)
    assert not ok_, "three windows passed as healthy"
    assert "streams 2" in detail, detail


def test_two_windows_are_fine():
    Series = _windows({0: "AAAUSDT", 1: "BBBUSDT"})
    with mock.patch("signals.tv_cdp.TradingViewCDP", Series), \
            mock.patch.object(time, "time", lambda: NOW):
        ok_, detail, _ = G.check_charts(["AAAUSDT"], "15", scout_window=1)
    assert ok_, detail


def test_the_repair_never_opens_more_than_the_account_can_stream():
    """What the check refuses, the repair must not create."""
    import inspect
    src = inspect.getsource(G.repair_charts)
    assert "MAX_WINDOWS" in src, (
        "repair_charts can still open a window past the streaming limit, "
        "which is the fault it would then be asked to repair")
    assert G.MAX_WINDOWS == 2


def test_the_health_check_also_refuses_a_third_window():
    from pathlib import Path
    src = (Path(G.__file__).resolve().parent / "tools"
           / "healthcheck.py").read_text()
    assert "MAX_WINDOWS" in src and "already dead or about to be" in src, (
        "the health check does not notice a window the account cannot feed")


# ------------------------------------------------- real candles, not averages
def test_a_heikin_ashi_window_is_a_fault():
    """An averaged candle is a price nobody ever paid.

    Every reading the indicator takes -- the breaking candle, the body, the
    level, the stop -- is computed from the candle in front of it. On Heikin
    Ashi each of those is an average of the bar before, so the whole chain
    describes a market that did not happen. Both windows were on style 8 on
    2026-09-05 and nothing had noticed, because nothing had ever asked.
    """
    import inspect
    src = inspect.getsource(G.check_charts)
    assert "check_candles" in src, (
        "check_charts never asks what kind of candle it is looking at")
    assert G.REAL_CANDLES == 1
    assert "Heikin Ashi" in src


def test_the_guard_puts_the_candles_back():
    """A saved layout restores its own style, so a reload undoes the fix."""
    import inspect
    src = inspect.getsource(G.repair_charts)
    assert "fix_candles" in src, (
        "the guard reports Heikin Ashi and never corrects it, so the fault "
        "returns on every chart reload and stays until someone notices")


def test_the_style_is_read_from_the_series_not_guessed():
    assert "mainSeries" in G.CHART_STYLE_JS and "style" in G.CHART_STYLE_JS
    assert "setChartType(1)" in G.SET_REAL_JS


def test_a_window_on_heikin_ashi_is_reported(monkeypatch):
    """The fault as the guard would actually meet it."""
    Series = _windows({0: "AAAUSDT", 1: "BBBUSDT"})
    Series.style = "8"
    with mock.patch("signals.tv_cdp.TradingViewCDP", Series), \
            mock.patch.object(time, "time", lambda: NOW):
        ok, detail, _ = G.check_charts(["AAAUSDT"], "15", scout_window=1)
    assert not ok
    assert "Heikin Ashi" in detail, detail
    Series.style = "1"


def test_a_window_that_cannot_answer_is_not_accused_of_heikin_ashi():
    """Silence is not evidence.

    An old build, a page still loading, a study not yet attached -- none of
    them mean the candles are averaged, and inventing a fault out of a
    non-answer is how an alarm stops being believed.
    """
    Series = _windows({0: "AAAUSDT", 1: "BBBUSDT"})
    Series.style = "?"
    with mock.patch("signals.tv_cdp.TradingViewCDP", Series), \
            mock.patch.object(time, "time", lambda: NOW):
        ok, detail, _ = G.check_charts(["AAAUSDT"], "15", scout_window=1)
    assert ok, detail
    Series.style = "1"


def test_the_candles_are_drawn_wide_enough_to_read():
    """A chart nobody can read is a chart nobody checks.

    Left at TradingView's own spacing the window showed a hundred and fifty
    bars at once -- thirty hours of a fifteen minute chart squeezed into a
    line. The operator has to be able to look at the thing the book is
    trading on.
    """
    import inspect
    assert G.BAR_SPACING >= 6
    assert "setBarSpacing" in G.SET_SPACING_JS
    src = inspect.getsource(G.fix_candles)
    assert "SET_SPACING_JS" in src, (
        "the guard puts the candles back but leaves them unreadably narrow")


def test_the_price_axis_fits_the_candles_and_nothing_else():
    """The indicator's packed integers are plots on the same scale.

    VOTES_PACKED and STATE publish values in the hundreds and thousands. With
    scaleSeriesOnly off the axis stretched to hold them, so on a coin trading
    at 0.139 the scale ran to 1,345 and every candle collapsed to a flat line.
    It looked like a zoom problem and no amount of zooming would have fixed it.
    """
    import inspect
    assert "scaleSeriesOnly" in G.SET_SERIES_SCALE_JS
    assert "setValue(true)" in G.SET_SERIES_SCALE_JS
    src = inspect.getsource(G.fix_candles)
    assert "SET_SERIES_SCALE_JS" in src, (
        "the guard restores the candles and the spacing but leaves the axis "
        "stretched by the indicator's own numbers")
