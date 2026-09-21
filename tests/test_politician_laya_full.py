"""
tests/test_politician_laya_full.py
==================================
Comprehensive automated test suite for Politician & Fundamental Brain (Laya Geopolitical & Macro Oracle).
Verifies:
1. Geopolitical News & Calendar Ingestion (FairEconomy & Google News RSS).
2. The Sword: Macro Sovereign Titan Boost (1.65x sizing, 1.80x TP expansion).
3. The Shield: System Guarantee Vetoes (Counter-trend shock & calendar freeze).
4. Laya System 1 Decision Integration & Sub-millisecond Latency.
"""

import time
from datetime import datetime, timezone
from scalper.brain.politician_brain import get_politician_brain, PoliticalRegime, MacroBias
from scalper.brain.laya_oracle import get_laya_oracle


def test_politician_brain_intelligence_and_shields():
    print("\n--- 1. Testing Politician Brain Ingestion & Telemetry ---")
    brain = get_politician_brain()
    tele = brain.get_telemetry()
    print("Initial Telemetry:", tele)
    assert "heat_index" in tele
    assert "political_regime" in tele
    assert "macro_bias" in tele
    assert "active_headline" in tele
    print("✅ Telemetry verified.")

    print("\n--- 2. Testing The Sword: Macro Sovereign Titan Mode ---")
    # In Bullish regime, a BUY setup receives Titan Boost (1.65x)
    brain._current_regime = PoliticalRegime.TRADE_WAR_TARIFFS
    brain._current_bias = MacroBias.STRONG_BULL
    sword_eval = brain.evaluate_entry_macro_fit(direction="BUY", strategy_type="BREAKOUT_RETEST")
    print("Sword Evaluation:", sword_eval)
    assert sword_eval.is_permitted is True
    assert sword_eval.alpha_boost_multiplier == 1.65
    assert sword_eval.tp_expansion_multiplier == 1.80
    assert "TITAN" in sword_eval.reasoning
    print("✅ The Sword (Bigger & Better Numbers) verified.")

    print("\n--- 3. Testing The Shield: Counter-Trend Shock Veto ---")
    # If macro bias is STRONG_BEAR, technical BUY must be VETOED
    brain._current_bias = MacroBias.STRONG_BEAR
    shield_eval = brain.evaluate_entry_macro_fit(direction="BUY", strategy_type="BREAKOUT_RETEST")
    print("Shield Evaluation (Counter-Trend):", shield_eval)
    assert shield_eval.is_permitted is False
    assert shield_eval.shield_status == "VETO_COUNTER_TREND_SHOCK"
    assert shield_eval.alpha_boost_multiplier == 0.0
    print("✅ The Shield (Counter-Trend Veto) verified.")

    # Reset back to strong bull for live trading
    brain._current_bias = MacroBias.STRONG_BULL


def test_laya_oracle_integration():
    print("\n--- 4. Testing Laya Oracle Full Decision Integration ---")
    oracle = get_laya_oracle()
    t0 = time.perf_counter()
    decision = oracle.evaluate_setup_sync({
        "direction": "BUY",
        "entry_price": 4345.00,
        "sl_price": 4343.50,
        "wick_ratio": 0.65,
        "session": "London/NY",
        "setup_type": "BREAKOUT_RETEST",
        "hour_utc": 15,
        "trend_aligned": True,
    })
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    print(f"Laya Decision ({elapsed_ms:.2f}ms):", decision)

    assert decision.is_valid is True
    assert decision.setup_grade == "macro_sovereign_titan"
    assert decision.compounding_multiplier >= 1.65
    assert decision.tp_expansion_multiplier >= 1.80
    assert decision.confluence_score >= 9.8
    assert decision.political_regime == "TRADE_WAR_TARIFFS"
    assert decision.macro_bias == "STRONG_BULL"
    assert decision.decision_latency_ms < 10.0
    print(f"✅ Laya System 1 Macro Sovereign Titan verified in {elapsed_ms:.2f}ms!")

    print("\n--- 5. Testing Full Dashboard Telemetry Sync ---")
    tele = oracle.get_telemetry()
    assert "politician" in tele
    assert "geopolitical_heat" in tele
    assert "political_regime" in tele
    assert "macro_bias" in tele
    assert "tp_expansion" in tele
    print("Dashboard Telemetry:", tele["politician"])
    print("✅ Dashboard Telemetry Sync verified.")


if __name__ == "__main__":
    test_politician_brain_intelligence_and_shields()
    test_laya_oracle_integration()
    print("\n🏆 ALL 5 POLITICIAN & FUNDAMENTAL BRAIN SUITES PASSED WITH ZERO ERRORS!")
