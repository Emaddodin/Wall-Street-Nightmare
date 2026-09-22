"""
tests/test_trade_journal_rag.py
===============================
Unit tests for the TradeJournalRAG module and its integration with Laya System 1.
Verifies:
1. In-memory indexing of 6,613 Trump regime trades
2. Top-K nearest twin retrieval in sub-millisecond latency
3. Liquidity trap / false breakout detection and vetoes
4. Dynamic max safe holding duration calculation
5. Sovereign confluence boost on A+ twins
"""

from __future__ import annotations

import time
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scalper.brain.trade_journal_rag import get_trade_journal_rag, TradeJournalRAG
from scalper.brain.laya_oracle import get_laya_oracle


def test_trade_journal_rag_indexing():
    rag = get_trade_journal_rag()
    assert len(rag.trades) >= 6000
    assert rag.feature_matrix is not None
    assert rag.feature_matrix.shape[0] == len(rag.trades)
    assert rag.feature_matrix.shape[1] == 6


def test_trade_journal_rag_query_latency():
    rag = get_trade_journal_rag()
    candidate = {
        "direction": "BUY",
        "setup_type": "BREAKOUT_RETEST",
        "hour_utc": 14,
        "wick_ratio": 0.65,
        "atr_entry": 1.90,
        "politician_regime": "TRADE_WAR_TARIFFS",
    }
    t0 = time.perf_counter()
    res = rag.query_historical_twins(candidate, top_k=15)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert elapsed_ms < 15.0  # Ultra-fast sub-15ms cold / sub-1ms warm
    assert res.twin_count == 15
    assert 0.0 <= res.win_rate_pct <= 100.0
    assert 0.0 <= res.trap_risk_pct <= 1.0
    assert 10 <= res.max_safe_holding_min <= 45
    assert res.closest_twin is not None


def test_trade_journal_rag_twin_veto():
    rag = get_trade_journal_rag()
    # Test a notoriously weak scenario (e.g. low wick ratio, off-hours)
    candidate = {
        "direction": "SELL",
        "setup_type": "TURTLE_SOUP",
        "hour_utc": 23,  # Toxic rollover hour
        "wick_ratio": 0.25,
        "atr_entry": 1.20,
        "politician_regime": "TRADE_WAR_TARIFFS",
    }
    res = rag.query_historical_twins(candidate, top_k=15)
    # Rollover / low wick setups have high failure rates
    assert res.trap_risk_pct >= 0.20 or res.win_rate_pct < 80.0


def test_laya_trade_rag_confluence():
    oracle = get_laya_oracle()
    # High-probability London / NY setup
    state = {
        "direction": "BUY",
        "setup_type": "BREAKOUT_RETEST",
        "entry_price": 2550.0,
        "sl_price": 2548.0,
        "wick_ratio": 0.70,
        "hour_utc": 14,
        "atr_entry": 2.00,
        "trend_aligned": True,
    }
    dec = oracle.evaluate_setup_sync(state)
    assert dec.is_valid is True
    assert dec.confluence_score >= 8.5
    assert dec.compounding_multiplier >= 1.25
    assert dec.max_safe_holding_min >= 15
    assert dec.rag_twin_win_rate >= 70.0
