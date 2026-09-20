"""
Regression tests for the audit findings fixed 2026-09-06.

Each test pins the CORRECTED behaviour, so the bugs cannot come back
quietly: the trend level joins at the newest candle, the scout's council
lean is read from the shape's own bar, the scout's measurements survive
into the book, the liquidation fee is the same round trip as every other
close, the finder survives a fully blind market, and the caches keep their
last good state on a mid-write read failure.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

import papertrade as P  # noqa: E402
import scout as S  # noqa: E402
import atrscan as A  # noqa: E402
import market as M  # noqa: E402
from fakes import pack_votes  # noqa: E402


def test_trend_ride_level_is_the_newest_candle():
    """The docstring: the newest lower high / higher low is the level."""
    bars, _t = M.staircase_trend(n=40, base=100.0, steps=4, up=False)
    g = P.trend_ride(bars, max(bars), max_stop=1.0)
    assert g and g["side"] == "SELL"
    newest = bars[max(bars)]
    assert g["level"] == newest[1], "level must be the newest lower high"
    bars2, _t2 = M.staircase_trend(n=40, base=100.0, steps=4, up=True)
    g2 = P.trend_ride(bars2, max(bars2), max_stop=1.0)
    assert g2 and g2["side"] == "BUY"
    assert g2["level"] == bars2[max(bars2)][2], \
        "level must be the newest higher low"


def test_scout_break_lean_is_read_from_the_shapes_own_bar():
    """The council lean belongs to the bar the shape was found on."""
    bars, t = M.band_then_break(n=45, base=1.0, t0=1788400000, up=True)
    c = bars[t][3]
    bars[t + M.BAR] = (c, c * 1.0002, c * 0.9998, c)   # the forming bar

    class Chart:
        def evaluate(self, expression, retry=True):
            return json.dumps([[k, *v] for k, v in sorted(bars.items())])

        def studies(self):
            return [{"id": "st1", "name": "TBT Sniper"}]

        def raw_series(self, sid, limit=None):
            plots = ["VOTES_PACKED"]
            rows = []
            for k in sorted(bars):
                if k == t:
                    pv = pack_votes(Bank=1, Team45=1, Tesla=1, Sniper=1,
                                    HTF=1, MA=1)      # 6 with the break
                else:
                    pv = pack_votes(Bank=-1, Team45=-1, Tesla=-1, Sniper=-1,
                                    HTF=-1, MA=-1)    # 6 against, later
                rows.append([k, pv])
            if limit:
                rows = rows[-limit:]
            return {"plots": plots, "rows": rows}

    with mock.patch.object(P, "coin_reach", lambda s: (30.0, 0.3)):
        got = S.read_break(Chart(), sym="TESTUSDT")
    assert got is not None
    assert got["agree"] == 6, got
    assert got["against"] == 0, got


def test_scout_plans_carries_the_scouts_measurements(tmp_path):
    row = {"kind": "council", "sym": "XUSDT", "at": 1788450000,
           "t": 1788449100, "dir": 1, "entry": 1.0, "votes": 4,
           "ready": True, "members": {"Bank": 1},
           "leg": 1.2, "expansion": 2.0, "body": 0.6, "wick": 25.0,
           "run": 4}
    scoutf = tmp_path / "scout.json"
    scoutf.write_text(json.dumps([row]))
    with mock.patch.object(P, "SCOUT", scoutf), \
            mock.patch("time.time", lambda: 1788450060):
        plans = P.scout_plans(set(), set())
    assert len(plans) == 1
    p = plans[0]
    for k in ("leg", "expansion", "body", "wick", "run"):
        assert p[k] == row[k], f"{k} was dropped: {p[k]!r}"


def test_liquidation_pays_the_full_round_trip_fee():
    from dataset.make_dataset import run_scenario
    from dataset import scenarios
    sc = [s for s in scenarios.SCENARIOS
          if s["name"] == "exit_liquidated"][0]
    book, kw, sig, clock, st = run_scenario(sc)
    tr = [t for t in book.state.get("trades") or [] if t.get("closed")]
    assert len(tr) == 1
    t = tr[0]
    assert t["reason"] == "liquidated"
    expect = -t["margin"] - t["notional"] * 12 / 1e4
    assert abs(t["pnl"] - expect) < 1e-9, (t["pnl"], expect)


def test_the_finder_survives_a_fully_blind_market(tmp_path, monkeypatch):
    class Cli:
        def tickers(self):
            return [{"symbol": "XUSDT", "quoteVol": "2e6"}]

        def klines(self, sym, res, limit):
            raise RuntimeError("the venue went away")

    monkeypatch.setattr(A, "BitunixClient", lambda: Cli())
    monkeypatch.setattr(A, "WATCH", tmp_path / "watchlist.json")
    monkeypatch.setattr(A, "MEAS", tmp_path / "atr_measures.json")
    monkeypatch.setattr(A, "WATCH_HIST", tmp_path / "watch_history.jsonl")

    class Args:
        top, min_atr, min_vol, bars, res, workers = 60, 2.5, 1e6, 14, "15m", 2

    assert A._scan(Args()) == 0          # must not raise
    assert json.loads((tmp_path / "watchlist.json").read_text()) == []


def test_ripe_cache_keeps_its_last_good_state(tmp_path):
    scoutf = tmp_path / "scout.json"
    good = [{"kind": "ripe", "sym": "XUSDT", "at": 1788450000,
             "short": 1, "ripe": 90.0}]
    scoutf.write_text(json.dumps(good))
    P._RIPE.update({"t": 0.0, "by": {}})
    with mock.patch.object(P, "SCOUT", scoutf), \
            mock.patch("time.time", lambda: 1788450060):
        got = P.ripe_now()
    assert "XUSDT" in got
    # a truncated mid-write read must not zero the map
    scoutf.write_text('{"kind": "ripe", "sy')
    P._RIPE["t"] = 0.0
    with mock.patch.object(P, "SCOUT", scoutf), \
            mock.patch("time.time", lambda: 1788450061):
        got = P.ripe_now()
    assert "XUSDT" in got, "a bad read replaced the last good ripeness"


def test_atr_cache_keeps_its_last_good_state(tmp_path):
    measf = tmp_path / "atr_measures.json"
    measf.write_text(json.dumps(
        {"XUSDT": {"atr": 3.5, "trend": -1.0,
                   "vol20": 1.0, "mom6h": 2.0, "mom1h": 0.5,
                   "volx": 1.0}}))
    P._ATR.update({"t": 0.0, "by": {}, "trend": {}, "shape": {},
                   "stamp": 0})
    with mock.patch.object(P, "ATR_M", measf):
        assert P.coin_atr("XUSDT") == 3.5
    measf.write_text('{"XUSDT": {"at')
    P._ATR.update({"t": 0.0, "stamp": 0})
    with mock.patch.object(P, "ATR_M", measf):
        assert P.coin_atr("XUSDT") == 3.5, \
            "a bad read replaced the last good measurement"
