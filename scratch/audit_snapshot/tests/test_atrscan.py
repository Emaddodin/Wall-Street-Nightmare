"""The coin finder: does it pick coins that move, and can it fail safely.

The old scanner ranked on a coin's character over weeks and put the market's
biggest movers outside the list -- NIULAIUSDT, measured at reach 78.8 and
seventy-five minutes to target, dropped for producing none of a shape that
needs a quiet band, on a day it moved forty-four percent. This one ranks on
what the candles are doing now, so the tests are about that and about the two
ways a scanner hurts: writing a list nobody can read, and writing none at all.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import atrscan as A  # noqa: E402


class Row:
    def __init__(self, h, l, c):
        self.high, self.low, self.close = h, l, c


class Frame:
    """The smallest thing that behaves like the klines DataFrame."""

    def __init__(self, rows):
        self._rows = rows

    def __len__(self):
        return len(self._rows)

    def iterrows(self):
        return ((i, r) for i, r in enumerate(self._rows))

    @property
    def iloc(self):
        return self._rows


def flat(n=20, px=100.0):
    return Frame([Row(px, px, px) for _ in range(n)])


def swinging(n=20, px=100.0, pct=5.0):
    half = px * pct / 200
    return Frame([Row(px + half, px - half, px) for _ in range(n)])


def test_a_coin_that_does_not_move_measures_zero():
    assert A.atr_pct(flat(), 14) == pytest.approx(0.0)


def test_a_coin_with_five_percent_candles_measures_five():
    assert A.atr_pct(swinging(pct=5.0), 14) == pytest.approx(5.0, abs=0.01)


def test_atr_is_a_percentage_so_a_cheap_coin_is_not_penalised():
    """A $4 range on a $100 coin and $0.04 on a $1 coin are the same trade.

    Ranking on ATR in currency is ranking on price, which would hand the list
    to whatever happens to be expensive.
    """
    dear = A.atr_pct(swinging(px=1000.0, pct=3.0), 14)
    cheap = A.atr_pct(swinging(px=0.004, pct=3.0), 14)
    assert dear == pytest.approx(cheap, rel=1e-6)


def test_a_gap_between_candles_counts_as_movement():
    """True range, not the candle's own high-low.

    A coin that jumps between bars is moving, and the jump is exactly the part
    a high-low range cannot see.
    """
    rows = [Row(100, 100, 100), Row(110, 110, 110), Row(120, 120, 120)]
    assert A.atr_pct(Frame(rows), 2) > 0


def test_too_few_candles_is_no_measurement_not_a_zero():
    """A coin that could not be read must not rank as a coin that is calm."""
    assert A.atr_pct(flat(n=3), 14) is None
    assert A.atr_pct(None, 14) is None


def test_the_watchlist_is_written_whole_or_not_at_all(tmp_path):
    """The scout reads this on its own schedule.

    A plain write is visible in pieces, and a truncated read leaves the scout
    walking nothing for a sweep with no error anywhere.
    """
    p = tmp_path / "watchlist.json"
    A.write_atomic(p, ["AAAUSDT", "BBBUSDT"])
    assert json.loads(p.read_text()) == ["AAAUSDT", "BBBUSDT"]
    A.write_atomic(p, ["CCCUSDT"])
    assert json.loads(p.read_text()) == ["CCCUSDT"]
    assert not list(tmp_path.glob("*.tmp"))


def test_the_floor_is_where_the_measurement_put_it():
    """2.5% is not a taste. Below it every slice measured negative."""
    import inspect
    src = inspect.getsource(A.main)
    assert '"--min-atr", type=float, default=2.5' in src, (
        "the ATR floor moved away from the value the measurement supports")


def test_volume_is_a_floor_and_never_a_ranking_term():
    """Ranking by volume hands the list back to coins that do not move."""
    import inspect
    src = inspect.getsource(A._scan)
    assert 'rows.sort(key=lambda r: -r["atr"])' in src, (
        "the watchlist is no longer ordered by ATR")
    assert "sort" not in src.split("rows.sort")[0].split("liquid.append")[-1], (
        "volume is being sorted on somewhere before the ATR ordering")


def test_the_health_check_watches_the_scanner_that_is_actually_running():
    """A watchdog pointed at a retired unit reports a fault that is a choice.

    tbt-boom was turned off deliberately -- its ranking asked what a coin had
    done over weeks, and that is how the market's biggest movers ended up
    outside the list. Leaving the health check pointed at it made it shout
    "tbt-boom.timer inactive" every ten minutes about a decision somebody made
    on purpose, which is exactly how an alarm stops being read.
    """
    src = (ROOT / "tools" / "healthcheck.py").read_text()
    import re
    m = re.search(r"^TIMERS = \(([^)]*)\)", src, re.M)
    assert m, "the health check no longer names the timers it watches"
    watched = m.group(1)
    assert "tbt-atr.timer" in watched, "the live coin finder is unwatched"
    assert "tbt-boom.timer" not in watched, (
        "the health check still expects the retired scanner to be running")

    unit = (ROOT / "services" / "tbt-atr.timer").read_text()
    assert "OnUnitActiveSec=" in unit, "the coin finder never runs again"


def test_the_health_check_reports_now_not_history():
    """A fault the guard already repaired must stop being a fault.

    Faults were counted from the book's last restart, so anything that went
    wrong once alarmed for the life of the process. There are two answers and
    this asserts both: the counts that can only come from the log are taken
    over a recent window, and a freeze -- which the guard repairs in about
    three minutes, faster than any window closes -- is asked of the charts
    directly instead.
    """
    src = (ROOT / "tools" / "healthcheck.py").read_text()
    assert "RECENT_MIN" in src, (
        "the health check has no recent window; every fault since the last "
        "restart is still being reported as current")
    i = src.index('("polls failed", "poll failed")')
    after = src[i:i + 900]
    assert "recent.count(needle)" in after, (
        "the log-counted faults are still taken from the whole log")
    assert "earlier, repaired" in after, (
        "a repaired fault should be reported as repaired, not hidden")


def test_the_signal_reader_closes_the_window_it_opened_by_id():
    """A tab nobody is driving stops being fed, and the book calls it frozen.

    The first version closed "window N" by position. Positions move -- the
    scout and the guard open and close tabs while it runs -- so it closed
    somebody else's window or none at all, and left its own behind. Forty
    minutes later the book reported a frozen chart and the operator got an
    alarm about a fault the measuring tool had created.
    """
    src = (ROOT / "tools" / "signalcheck.py").read_text()
    assert "own_id" in src, "the tool does not remember which tab it opened"
    i = src.index("def _shut():")
    body = src[i:i + 1200]
    assert "json/close/\" + own_id" in body, (
        "the tool still closes a window by position rather than by id")
    assert "tg[own]" not in body, "the index-based close is still there"


# ---------------------------------------------- the book's own ATR floor
def test_the_book_reads_each_coins_atr_from_the_finder(tmp_path, monkeypatch):
    import json as _json
    import papertrade as P
    f = tmp_path / "atr_measures.json"
    f.write_text(_json.dumps({"AAAUSDT": {"atr": 5.5, "vol": 1e7},
                              "BBBUSDT": {"atr": 1.2, "vol": 1e7}}))
    monkeypatch.setattr(P, "ATR_M", f)
    P._ATR.update({"t": 0.0, "by": {}, "stamp": 0})
    assert P.coin_atr("AAAUSDT") == pytest.approx(5.5)
    assert P.coin_atr("bbbusdt") == pytest.approx(1.2)


def test_a_coin_the_finder_never_measured_is_not_called_calm(tmp_path,
                                                             monkeypatch):
    """No number is not a small number.

    Refusing on a missing measurement would blind the book on every coin the
    finder has not reached yet, which after a restart is all of them.
    """
    import papertrade as P
    f = tmp_path / "atr_measures.json"
    f.write_text("{}")
    monkeypatch.setattr(P, "ATR_M", f)
    P._ATR.update({"t": 0.0, "by": {}, "stamp": 0})
    assert P.coin_atr("AAAUSDT") is None


def test_the_atr_floor_is_off_unless_it_is_asked_for():
    """It changes what the book takes, so it is the operator's dial."""
    import papertrade as P
    src = Path(P.__file__).read_text()
    i = src.index('"--min-atr"')
    assert "default=0.0" in src[i:i + 200]


def test_the_thin_coin_refusal_names_the_number_it_refused_on():
    """A rejection nobody can check is a rejection nobody can argue with."""
    import papertrade as P
    src = Path(P.__file__).read_text()
    assert "thin_coin: ATR" in src, (
        "the ATR refusal does not say what the coin measured")
    i = src.index("thin_coin: ATR")
    assert "the floor is" in src[i:i + 200], (
        "the refusal does not say what floor it was measured against")


def test_the_live_unit_takes_three_agents_only_with_the_atr_floor_on():
    """Three agents is where the junk lives, and ATR is what removes it.

    Over 163 of the indicator's own signals: at three agents on coins under
    2.5% ATR, 24 signals won nothing at all; above it, 74 signals reached
    +10% a quarter of the time. The indicator's own score and tier do not
    separate them -- inside the three-agent set PEERLESS measured worse than
    GOOD -- so the floor is the only thing standing between the book and
    those 24.
    """
    from units import flag
    u = ROOT / "services" / "tbt-paper.service"
    ag, atr = flag(u, "--min-agents"), flag(u, "--min-atr")
    if ag and int(ag) <= 3:
        assert atr and float(atr) >= 2.5, (
            "the unit takes three-agent signals with no ATR floor, which is "
            "exactly the set that measured zero wins")


def test_the_banner_names_the_floor_that_is_doing_the_work():
    """A banner that lists the agent count and hides the ATR floor is lying.

    At three agents the floor is the whole difference between 74 signals that
    reached the target a quarter of the time and 24 that never reached it at
    all. An operator reading the banner has to see the book that is actually
    running.
    """
    import papertrade as P
    src = Path(P.__file__).read_text()
    i = src.index('f", >= {a.min_agents} agents"')
    assert "a.min_atr" in src[i:i + 800], (
        "the startup banner lists the agent floor but never mentions the ATR "
        "floor beside it")


def test_the_health_check_watches_the_files_that_are_actually_written():
    """A freshness check on a file nothing writes alarms forever.

    boom.json was written by tbt-boom, retired on purpose. Left in the table
    it went stale by definition and pushed "boom.json 94 min old" to the
    operator's phone every ten minutes -- about a file that is supposed to be
    stale.
    """
    src = (ROOT / "tools" / "healthcheck.py").read_text()
    i = src.index('DATA / "scout.json"')
    table = src[i:i + 400]
    assert '"boom.json"' not in table, (
        "the health check still expects the retired scanner's file to be "
        "kept fresh")
    assert '"atr_measures.json"' in table, (
        "the live coin finder's output is not checked for freshness")


def test_a_service_caught_mid_restart_is_not_reported_as_down():
    """systemd says "deactivating" for the second a restart takes.

    The check landed inside one and alarmed the operator about a unit that was
    healthy again before they could read the notification.
    """
    src = (ROOT / "tools" / "healthcheck.py").read_text()
    i = src.index('sh("systemctl", "is-active", s)')
    after = src[i:i + 900]
    assert "deactivating" in after and "activating" in after, (
        "a unit in transition is still reported as a fault")
    assert "time.sleep" in after, (
        "nothing re-checks the unit after the transition")


def test_a_freeze_is_asked_of_the_charts_not_counted_in_the_log():
    """The guard repairs a stopped feed faster than the alarm window closes.

    A freeze is repaired in about three minutes; the log line saying it
    happened sat inside the health check's twenty-minute window for far
    longer, so every freeze pushed an alarm about something already fixed.
    USELESSUSDT did it twice in one morning. Only a chart that is frozen NOW
    is a fault.
    """
    src = (ROOT / "tools" / "healthcheck.py").read_text()
    assert "frozen_now" in src, (
        "the health check does not ask the charts whether one is frozen")
    assert '("chart froze", "FROZEN")' not in src, (
        "the freeze is still being counted out of the log, which alarms for "
        "twenty minutes about a three-minute outage")
    assert "the guard repaired it" in src, (
        "a repaired freeze should be reported as repaired, not hidden")


def test_the_health_check_knows_every_word_the_book_uses_to_evaluate():
    """A check that knows one source's vocabulary goes blind when it changes.

    It counted "reject" and "scored", which the shape source writes. On
    --source combo the book writes skip, drop and OPEN, so it reported
    "nothing evaluated in 46 minutes" with a position opened and a candidate
    refused by name in the very log it was reading.
    """
    src = (ROOT / "tools" / "healthcheck.py").read_text()
    i = src.index("looked = ")
    line = src[i:i + 400]
    for word in ("reject", "skip", "drop", "OPEN"):
        assert word in line, (
            f"the health check does not count {word!r} as the book having "
            f"looked at something")


def test_the_counter_exit_sits_below_the_target():
    """An exit at or above the target would never fire before the target does."""
    from units import flag
    u = ROOT / "services" / "tbt-paper.service"
    ct, tp = flag(u, "--ct-exit"), flag(u, "--tp")
    if not ct or float(ct) == 0:
        return
    assert tp and float(ct) < float(tp), (
        f"--ct-exit {ct}% is not below the {tp}% target, so it can never "
        f"fire first")


# ------------------------------------------------ which way the coin is going
def test_the_finder_measures_where_the_coin_has_come_from():
    import atrscan as A2
    rows = [Row(100, 100, 100)] * 5 + [Row(110, 110, 110)] * 5
    assert A2.trend_pct(Frame(rows), bars=8) == pytest.approx(10.0, abs=0.01)
    flat_rows = [Row(100, 100, 100)] * 12
    assert A2.trend_pct(Frame(flat_rows), bars=8) == pytest.approx(0.0)
    assert A2.trend_pct(Frame(rows[:3]), bars=8) is None


def test_the_book_reads_the_trend_from_the_same_file(tmp_path, monkeypatch):
    import json as _json
    import papertrade as P
    f = tmp_path / "atr_measures.json"
    f.write_text(_json.dumps({"AAAUSDT": {"atr": 5.5, "trend": -7.2},
                              "BBBUSDT": {"atr": 3.0}}))
    monkeypatch.setattr(P, "ATR_M", f)
    P._ATR.update({"t": 0.0, "by": {}, "trend": {}, "stamp": 0})
    assert P.coin_trend("AAAUSDT") == pytest.approx(-7.2)
    assert P.coin_trend("BBBUSDT") is None, (
        "a coin with no trend measured must not read as flat")
    assert P.coin_trend("CCCUSDT") is None


def test_the_wrong_way_refusal_names_the_move_it_refused_on():
    import papertrade as P
    src = Path(P.__file__).read_text()
    assert "against_trend: the coin has moved" in src
    i = src.index("wrong_way = (")
    body = src[i:i + 400]
    assert "abs(_tr) >= 1.0" in body, (
        "a coin that has barely moved is being called a trend, so a signal "
        "on a flat coin can be refused for going against nothing")
    assert "_tr is not None" in body, (
        "a coin the finder never measured is refused on a number nobody has")


def test_the_trend_gate_is_off_unless_it_is_asked_for():
    import papertrade as P
    src = Path(P.__file__).read_text()
    i = src.index('"--with-trend"')
    assert "default=False" in src[i:i + 200]


def test_the_stop_stays_in_proportion_to_the_target():
    """Halving the target without halving the stop halves only the wins.

    When the target went from 10% to 5% and the stop stayed at 2%, every win
    was cut in half while every loss stayed the same size, and the break-even
    win rate went from 16.7% to 28.6% against a measured 31.3%. Measured over
    163 of the indicator's own signals at a 5% target, a 1.00% stop returned
    +5.9% a trade and 1.25% returned +7.3%, while 2.00% returned +3.9%.
    """
    from units import flag
    u = ROOT / "services" / "tbt-paper.service"
    tp, sl = flag(u, "--tp"), flag(u, "--sl")
    assert tp and sl
    rr = float(tp) / float(sl)
    assert rr >= 4.0, (
        f"the target is {tp}% against a {sl}% stop, a reward-to-risk of "
        f"{rr:.1f} -- below four the measured expectancy falls away and the "
        f"break-even win rate climbs past what the signal achieves")


def test_a_unit_flag_is_read_from_the_command_not_from_a_comment():
    """These units explain themselves at length, quoting the flags they set.

    A test matching the whole file found "# --lev 40 and --sl 2.0 go
    together" -- a paragraph about an older setting -- and reported a stop the
    book had not used for hours. It passed for the wrong reason and would have
    gone on passing after the real flag changed.
    """
    from units import exec_start, flag
    u = ROOT / "services" / "tbt-paper.service"
    cmd = exec_start(u)
    assert cmd.startswith("/home/tbt/venv/bin/python"), cmd[:60]
    assert "#" not in cmd, "a comment leaked into the parsed command"
    assert flag(u, "--sl") == "1.25", (
        f"--sl reads as {flag(u, '--sl')}, but the ExecStart says otherwise")


# ------------------------------------------------- the judgement, scored
def test_the_finder_measures_what_the_score_needs():
    import atrscan as A2
    rows = [Row(100 + i, 99 + i, 100 + i) for i in range(30)]
    for i, r in enumerate(rows):
        r.volume = 10.0 if i < 29 else 40.0
    got = A2.shape_of(Frame(rows))
    for k in ("vol20", "mom6h", "mom1h", "volx"):
        assert k in got, f"the finder does not measure {k}"
    assert got["volx"] > 3, "a volume surge is not being seen"
    assert got["mom6h"] > 0


def test_a_signal_with_nothing_in_its_favour_scores_low(monkeypatch):
    import papertrade as P
    monkeypatch.setitem(P._ATR, "shape", {"AAAUSDT": {}})
    monkeypatch.setitem(P._ATR, "by", {"AAAUSDT": 2.6})
    monkeypatch.setitem(P._ATR, "t", 1e18)
    pts, why = P.entry_score("AAAUSDT", "BUY", 3)
    assert pts == 0.0, why


def test_conviction_alone_cannot_reach_the_floor(monkeypatch):
    """Fifty-five needs the coin as well as the indicator's confidence."""
    import papertrade as P
    monkeypatch.setitem(P._ATR, "shape", {"AAAUSDT": {}})
    monkeypatch.setitem(P._ATR, "by", {"AAAUSDT": 2.6})
    monkeypatch.setitem(P._ATR, "t", 1e18)
    pts, _ = P.entry_score("AAAUSDT", "BUY", 6)
    assert pts <= 40.0, (
        "agents alone reaches the floor, so the coin stops mattering")


def test_the_best_case_scores_the_whole_hundred(monkeypatch):
    import papertrade as P
    monkeypatch.setitem(P._ATR, "shape", {"AAAUSDT": {
        "vol20": 0.5, "mom6h": 4.0, "mom1h": 0.1, "volx": 2.0}})
    monkeypatch.setitem(P._ATR, "by", {"AAAUSDT": 5.0})
    monkeypatch.setitem(P._ATR, "t", 1e18)
    pts, why = P.entry_score("AAAUSDT", "BUY", 5)
    assert pts == 100.0, why


def test_a_coin_that_already_jumped_our_way_loses_the_hour_point(monkeypatch):
    """The move we were going to be paid for has already happened."""
    import papertrade as P
    base = {"vol20": 0.5, "mom6h": 4.0, "volx": 2.0}
    monkeypatch.setitem(P._ATR, "by", {"AAAUSDT": 5.0})
    monkeypatch.setitem(P._ATR, "t", 1e18)
    monkeypatch.setitem(P._ATR, "shape", {"AAAUSDT": {**base, "mom1h": 0.1}})
    quiet, _ = P.entry_score("AAAUSDT", "BUY", 5)
    monkeypatch.setitem(P._ATR, "shape", {"AAAUSDT": {**base, "mom1h": 6.0}})
    spent, _ = P.entry_score("AAAUSDT", "BUY", 5)
    assert spent < quiet


def test_the_same_reading_scores_the_other_way_for_a_sell(monkeypatch):
    """Every momentum term is signed by the side the trade points."""
    import papertrade as P
    monkeypatch.setitem(P._ATR, "shape", {"AAAUSDT": {
        "vol20": 0.5, "mom6h": 4.0, "mom1h": 0.1, "volx": 2.0}})
    monkeypatch.setitem(P._ATR, "by", {"AAAUSDT": 5.0})
    monkeypatch.setitem(P._ATR, "t", 1e18)
    buy, _ = P.entry_score("AAAUSDT", "BUY", 5)
    sell, _ = P.entry_score("AAAUSDT", "SELL", 5)
    assert buy != sell, "the score reads the same for a buy and a sell"


def test_the_score_says_why(monkeypatch):
    """A refusal nobody can check is a refusal nobody can argue with."""
    import papertrade as P
    monkeypatch.setitem(P._ATR, "shape", {"AAAUSDT": {"vol20": 0.5}})
    monkeypatch.setitem(P._ATR, "by", {"AAAUSDT": 5.0})
    monkeypatch.setitem(P._ATR, "t", 1e18)
    _, why = P.entry_score("AAAUSDT", "BUY", 4)
    assert "agents 4" in why and "calm" in why and "ATR" in why


def test_the_scale_reaches_the_hundred_it_claims():
    """A floor is only meaningful against a top that can be reached.

    This engine spent weeks taking nothing because a threshold of seventy sat
    above the highest score its scale could produce. The weights here must add
    to exactly what the scale says.
    """
    import inspect
    import re
    import papertrade as P
    src = inspect.getsource(P.entry_score)
    weights = [float(x) for x in re.findall(r"pts \+= (?:min\(([0-9.]+)|([0-9.]+))",
                                            src) for x in (x[0] or x[1],) if x]
    assert sum(weights) == 100.0, (
        f"the terms add to {sum(weights):.0f}, not the hundred the scale and "
        f"every refusal message claim")


def test_the_finder_logs_the_list_it_kept(tmp_path, monkeypatch):
    """Which coins were on the list when a signal fired is the missing
    variable in every offline study; each scan now leaves a line saying so."""
    import pandas as pd

    class Row:
        def __init__(self, h, l, c, o=1.0):
            self.high, self.low, self.close, self.open = h, l, c, o

    class Cli:
        def tickers(self):
            return [{"symbol": "BIGUSDT", "quoteVol": "2e6"},
                    {"symbol": "QUIETUSDT", "quoteVol": "2e6"}]

        def klines(self, sym, res, limit):
            n = 30
            if sym == "BIGUSDT":
                rows = [Row(1.0, 0.95, 0.99) for _ in range(n)]
            else:
                rows = [Row(1.0, 0.999, 1.0) for _ in range(n)]
            return pd.DataFrame([r.__dict__ for r in rows])

    monkeypatch.setattr(A, "BitunixClient", lambda: Cli())
    monkeypatch.setattr(A, "WATCH", tmp_path / "watchlist.json")
    monkeypatch.setattr(A, "MEAS", tmp_path / "atr_measures.json")
    hist = tmp_path / "watch_history.jsonl"
    monkeypatch.setattr(A, "WATCH_HIST", hist)

    class Args:
        top, min_atr, min_vol, bars, res, workers = 60, 2.5, 1e6, 14, "15m", 2

    assert A._scan(Args()) == 0
    lines = hist.read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["syms"] == ["BIGUSDT"]
    assert rec["ts"] > 0


def test_the_health_check_watches_the_models_age(tmp_path):
    """A filter trained days ago is a silent no-op; the watchdog says so.

    The book refuses to start without the artifact, so its absence is a
    crash-looping book, and its age says whether the nightly retrain is
    still alive. Both are checked, with the same 48h limit.
    """
    import numpy as np
    import time as _t
    import filter_model as FM
    model = tmp_path / "dataset" / "samples" / "model.npz"
    model.parent.mkdir(parents=True)

    np.savez_compressed(model, trained_at=int(_t.time() - 3600))
    state, msg = FM.model_check(model)[0]
    assert state == "ok", msg

    np.savez_compressed(model, trained_at=int(_t.time() - 50 * 3600))
    state, msg = FM.model_check(model)[0]
    assert state == "fault", msg

    model.unlink()
    state, msg = FM.model_check(model)[0]
    assert state == "fault" and "missing" in msg

    # and the watchdog actually names it
    src = (ROOT / "tools" / "healthcheck.py").read_text()
    assert "model_check" in src
