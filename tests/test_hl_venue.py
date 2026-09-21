"""
tests/test_hl_venue.py
======================
Venue-exactness proofs for hl_venue.py. Every expectation is pinned to a
Hyperliquid source (fee schedule, liquidation docs, meta endpoint).
"""
from __future__ import annotations

import pytest

from hl_venue import (
    HLPaperVenue,
    HLSpecs,
    liq_price,
    px_tick_from_book,
    round_px,
    round_sz,
    taker_fee,
    walk_book,
)


def paxg() -> HLSpecs:
    return HLSpecs(coin="PAXG", sz_decimals=3, max_leverage=10.0,
                   margin_tiers=[(0.0, 10.0), (3_000_000.0, 5.0)], px_tick=0.1)


class TestFees:
    def test_taker_fee_docs_example(self):
        # Docs: $10,000 taker fill costs $4.50 at 0.045%.
        assert taker_fee(10_000.0) == pytest.approx(4.50)

    def test_round_trip_taker(self):
        # Market open + market close: 2 x 4.5 bps of notional.
        n = 4360.35 * 0.5
        assert 2 * taker_fee(n) == pytest.approx(n * 0.0009)


class TestSpecs:
    def test_paxg_mmr_is_5pct(self):
        # Max 10x -> maintenance = half initial at max = 5% (tier 0).
        assert paxg().mmr_for(50_000.0) == pytest.approx(0.05)

    def test_mm_required_tier0(self):
        assert paxg().mm_required(100_000.0) == pytest.approx(5_000.0)

    def test_tick_from_real_book_shape(self):
        lv = [[["4360.3", "0.5"], ["4360.0", "0.1"]], [["4360.4", "0.2"]]]
        assert px_tick_from_book(lv[0] + lv[1]) == pytest.approx(0.1)

    def test_rounding(self):
        assert round_px(4360.37, 0.1) == pytest.approx(4360.4)
        assert round_sz(0.0234567, 0.001) == pytest.approx(0.023)


class TestLiquidation:
    def test_docs_worked_example_shape(self):
        # Guide's simplified 10x long BTC @50000, MMR 0.5% -> 45250.
        # Exact formula sits within 1% of the simplified print.
        s = HLSpecs(coin="BTC", sz_decimals=5, max_leverage=50.0,
                    margin_tiers=[(0.0, 50.0)], px_tick=0.1)
        px = liq_price(50_000.0, 1, 5_000.0, 1.0, s)
        assert px == pytest.approx(45_250.0, rel=0.01)

    def test_liq_invariant_long_and_short(self):
        # At the returned price, remaining margin must equal mm_required.
        s = paxg()
        for side, entry in ((1, 4360.0), (-1, 4360.0)):
            lev, sz, m = 10.0, 0.5, (0.5 * 4360.0) / 10.0
            px = liq_price(entry, side, m, sz, s)
            mm = s.mm_required(sz * px)
            avail = m - mm
            l = 1.0 / s.maint_lev_for(sz * px)
            expect = entry - side * avail / sz / (1 - l * side)
            assert px == pytest.approx(expect, rel=1e-6)

    def test_paxg_10x_long_distance_about_5pct(self):
        # Simplified guide formula prints 5.0%; the docs-exact mark-price
        # formula sits slightly further (~5.5%) because of the (1-l) divisor.
        s = paxg()
        m = (0.5 * 4360.0) / 10.0
        px = liq_price(4360.0, 1, m, 0.5, s)
        d = (4360.0 - px) / 4360.0
        assert 0.052 < d < 0.060


class TestBookWalk:
    def _book(self):
        bids = [(4360.3, 0.5), (4360.0, 1.0)]
        asks = [(4360.4, 0.2), (4360.8, 1.0)]
        return bids, asks

    def test_market_buy_walks_asks(self):
        bids, asks = self._book()
        f = walk_book(True, 0.5, bids, asks)
        # 0.2 @ 4360.4 + 0.3 @ 4360.8
        assert f.complete is True
        assert f.px == pytest.approx((0.2 * 4360.4 + 0.3 * 4360.8) / 0.5)
        assert f.fee == pytest.approx(f.notional * 0.00045)
        assert f.levels_used == 2

    def test_thin_book_partial(self):
        bids, asks = self._book()
        f = walk_book(True, 5.0, bids, asks)
        assert f.complete is False
        assert f.sz == pytest.approx(1.2)

    def test_market_sell_hits_bids(self):
        bids, asks = self._book()
        f = walk_book(False, 0.5, bids, asks)
        assert f.px == pytest.approx(4360.3)
        assert f.complete is True


class TestVenueAccounting:
    def _venue(self) -> HLPaperVenue:
        v = HLPaperVenue(paxg(), 1000.0)
        v.on_book([(4360.3, 5.0)], [(4360.4, 5.0)])
        v.on_mark(4360.35)
        return v

    def test_open_pays_fee_and_locks_margin(self):
        v = self._venue()
        r = v.market_open(1, 0.5, 4360.35, 218.0, 10.0, 4340.0, 1000.0)
        assert r["status"] == "ok"
        assert v.fees_total > 0
        assert v.equity == pytest.approx(1000.0 - r["margin_used"] - v.fees_total)
        assert v.pos.liq_px < 4360.35  # long liq below entry

    def test_rejects_below_10_notional(self):
        v = self._venue()
        sz, _, reason = v.size_for(0.5, 10.0, 4360.35)  # $5 notional
        assert reason == "min_notional_10"

    def test_stop_fires_on_candle_extreme(self):
        v = self._venue()
        v.market_open(1, 0.5, 4360.35, 218.0, 10.0, 4359.0, 1000.0)
        out = v.check_venue_exits(4360.0, 4361.0, 4358.0, 1001.0)
        assert out and out[0]["reason"] == "STOP_MARKET"
        assert v.pos.is_open is False

    def test_liquidation_on_mark(self):
        v = self._venue()
        v.market_open(1, 0.5, 4360.35, 218.0, 10.0, 4000.0, 1000.0)
        liq = v.pos.liq_px
        out = v.check_venue_exits(liq - 1.0, liq + 50, liq - 50, 1001.0)
        assert any(r["reason"] in ("LIQ_PARTIAL_20", "LIQUIDATED") for r in out)

    def test_funding_longs_pay_when_positive(self):
        v = self._venue()
        v.market_open(1, 0.5, 4360.35, 218.0, 10.0, 4000.0, 3599.0)
        before = v.equity
        v.accrue_funding(0.0000125, 3600.0)
        assert v.equity < before
        assert v.funding_total > 0
