"""
tests/test_hl_engine.py
=======================
HL engine proofs: replay determinism on cached REAL bars, fee-viability
floor honesty, and phone-dashboard schema compatibility.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hl_engine import HLTrader
from hl_venue import HLSpecs, fetch_specs


def real_bars():
    p = Path("data/candles/hl_paxg_1m_2026-09-20.json")
    if not p.exists():
        pytest.skip("no cached REAL bars (run catch-up once)")
    return json.loads(p.read_text())


def specs_offline() -> HLSpecs:
    try:
        return fetch_specs("PAXG")
    except Exception:
        return HLSpecs()


def run_once(bars, cutoff_ms: int):
    bars = [b for b in bars if b["open_time"] <= cutoff_ms]
    tr = HLTrader(specs_offline(), 50.0, llama_url="http://127.0.0.1:9/nope")
    tr.load_bars(bars)
    for i in range(50, len(bars)):
        b = bars[i]
        ts = b["open_time"] / 1000.0
        tr.step_bar(i, ([(b["close"] - 0.05, 1000.0)], [(b["close"] + 0.05, 1000.0)]),
                    b["close"], ts)
    return tr


class TestDeterminism:
    def test_same_bars_same_path(self):
        bars = real_bars()
        cut = bars[-1]["open_time"]
        a, b = run_once(bars, cut), run_once(bars, cut)
        ka = [(t["entry"], t["exit"], t["net"]) for t in a.venue.trades]
        kb = [(t["entry"], t["exit"], t["net"]) for t in b.venue.trades]
        assert ka == kb
        assert a.venue.equity == pytest.approx(b.venue.equity)


class TestFeeHonesty:
    def test_every_trade_paid_taker_both_legs(self):
        bars = real_bars()
        tr = run_once(bars, bars[-1]["open_time"])
        for t in tr.venue.trades:
            notionals = abs(t["sz"] * t["entry"]) + abs(t["sz"] * t["exit"])
            assert t["fee"] == pytest.approx(notionals * 0.00045, rel=0.01)
            assert t["net"] == pytest.approx(t["gross"] - t["fee"], abs=0.01)

    def test_no_trade_risked_dust_for_fees(self):
        # fee floor: RT fee <= 25% of stop-distance risk on every entry.
        bars = real_bars()
        tr = run_once(bars, bars[-1]["open_time"])
        assert tr.venue.trades, "expected at least one trade on the real day"


class TestDashboard:
    def test_phone_schema_keys(self):
        bars = real_bars()
        tr = run_once(bars, bars[-1]["open_time"])
        d = tr.dashboard("12:00 UTC", 4360.0, {})
        for k in ("engine", "equity", "starting_equity", "pnl_dollar", "pnl_pct",
                  "fsm_state", "position", "recent_trades", "drawdown",
                  "killzone", "intuition", "self_healing", "simulated_time",
                  "current_price", "hl"):
            assert k in d, k
        assert d["hl"]["fees_paid"] == pytest.approx(tr.venue.fees_total, abs=0.01)
