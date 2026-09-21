"""
scalper/tests/test_laya_system.py
=================================
Automated verification tests for:
- ICT Knowledge RAG Indexer & Retriever
- Macro & News Watchdog
- Laya Non-Autoregressive System 1 Decision Oracle
"""

import unittest
from datetime import datetime, timezone, timedelta
from scalper.brain.ict_rag import get_ict_rag
from scalper.brain.macro_watchdog import get_macro_watchdog
from scalper.brain.laya_oracle import get_laya_oracle


class TestLayaSystem(unittest.TestCase):

    def setUp(self):
        self.rag = get_ict_rag()
        self.watchdog = get_macro_watchdog()
        self.oracle = get_laya_oracle()

    def test_01_ict_rag_indexing(self):
        self.assertGreater(len(self.rag.concepts), 50, "ICT concepts library should have >50 indexed concepts")
        res = self.rag.retrieve_context({"direction": "BUY", "wick_ratio": 0.55, "session": "London Open"})
        self.assertTrue(len(res["top_concept_titles"]) > 0, "Should retrieve matching ICT concepts")
        self.assertIn("rules_summary", res)

    def test_02_macro_watchdog(self):
        allowed, reason = self.watchdog.is_entry_allowed()
        self.assertIsInstance(allowed, bool)

        # Register event in 3 minutes -> should freeze entries
        now = datetime.now(timezone.utc)
        self.watchdog.register_scheduled_event("US CPI", now + timedelta(minutes=3), impact="HIGH")
        allowed_freeze, freeze_reason = self.watchdog.is_entry_allowed()
        self.assertFalse(allowed_freeze, "Should freeze entries within 5m of High Impact CPI")
        self.assertIn("FREEZE", freeze_reason)

    def test_03_laya_oracle_decision(self):
        market_state = {
            "direction": "BUY",
            "entry_price": 4362.50,
            "sl_price": 4358.00,
            "wick_ratio": 0.52,
            "session": "London Open Judas Swing",
            "trend_aligned": True,
        }
        dec = self.oracle.evaluate_setup_sync(market_state)
        self.assertIsNotNone(dec)
        self.assertIn(dec.setup_grade, ["A_plus_prime", "high_probability", "marginal", "toxic_trap"])
        self.assertGreaterEqual(dec.confluence_score, 0.0)
        self.assertLessEqual(dec.confluence_score, 10.0)
        self.assertGreaterEqual(dec.compounding_multiplier, 0.0)

    def test_04_momentum_exhaustion(self):
        # In profit ($40) with rapid surge in 3 bars -> should show high exhaustion
        exhaustion = self.oracle.evaluate_momentum_exhaustion(pnl=40.0, current_px=4368.0, entry_px=4360.0, bar_count=3)
        self.assertGreaterEqual(exhaustion, 0.60, "High profit surge in few bars should indicate exhaustion")


if __name__ == "__main__":
    unittest.main()
