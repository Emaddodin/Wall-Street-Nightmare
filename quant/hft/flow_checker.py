"""
quant/hft/flow_checker.py
=========================
Real-time Trade Flow Diagnostic & Pipeline Auditor.
Monitors the A-to-Z execution funnel to distinguish between:
  1. Low-volatility / sub-threshold market conditions (Normal)
  2. Software bugs / data stalls / sizing rejections (Abnormal)

Runs continuous end-to-end synthetic self-tests and publishes
deep diagnostic telemetry to Ntfy and the HFT Terminal.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class TradeFlowMetrics:
    last_trade_ts: float = field(default_factory=time.time)
    start_ts: float = field(default_factory=time.time)
    ticks_processed: int = 0
    ticks_hawkes_quiet: int = 0
    ticks_low_confidence: int = 0
    ticks_direction_conflict: int = 0
    ticks_spread_wide: int = 0
    ticks_kelly_rejected: int = 0
    pipeline_exceptions: int = 0
    last_exception_msg: str = ""
    max_confidence_seen: float = 0.0
    last_self_test_ts: float = 0.0
    self_test_passed: bool = True
    last_alert_ts: float = 0.0


class TradeFlowAuditor:
    def __init__(self, alert_interval_sec: float = 1800.0) -> None:  # 30 mins
        self.metrics = TradeFlowMetrics()
        self.alert_interval_sec = alert_interval_sec

    def record_tick(self) -> None:
        self.metrics.ticks_processed += 1

    def record_hawkes_quiet(self) -> None:
        self.metrics.ticks_hawkes_quiet += 1

    def record_confidence(self, conf: float) -> None:
        if conf > self.metrics.max_confidence_seen:
            self.metrics.max_confidence_seen = round(conf, 3)
        self.metrics.ticks_low_confidence += 1

    def record_direction_conflict(self) -> None:
        self.metrics.ticks_direction_conflict += 1

    def record_spread_wide(self) -> None:
        self.metrics.ticks_spread_wide += 1

    def record_kelly_rejected(self) -> None:
        self.metrics.ticks_kelly_rejected += 1

    def record_trade_executed(self) -> None:
        self.metrics.last_trade_ts = time.time()
        # Reset window metrics on execution
        self.metrics.max_confidence_seen = 0.0

    def record_exception(self, exc: Exception, stage: str) -> None:
        self.metrics.pipeline_exceptions += 1
        self.metrics.last_exception_msg = f"Stage: {stage} | Error: {exc}"
        logger.error("Trade flow pipeline exception at %s: %s", stage, exc, exc_info=True)

    def run_e2e_self_test(self, engine) -> bool:
        """
        Simulates an idealized synthetic signal through the exact A-to-Z pipeline:
        Feature Eng -> CatBoost Predictor -> Kelly Sizing -> AS Quotes.
        Verifies that if the market triggers, zero exceptions or type errors occur.
        """
        self.metrics.last_self_test_ts = time.time()
        try:
            st = list(engine._states.values())[0]
            
            # Synthetic ideal bullish book
            dummy_bids = [(75000.0 - i * 1.0, 1.5 + i * 0.2) for i in range(5)]
            dummy_asks = [(75001.0 + i * 1.0, 1.0 + i * 0.1) for i in range(5)]
            mid = 75000.5

            # 1. Feature buffer test
            feat = st.signal_engine.feature_eng.get_feature_vector(
                dummy_bids, dummy_asks, hawkes_buy_lam=2.5, hawkes_sell_lam=0.4
            )
            assert len(feat) == 12, "Feature vector must be 12-dimensional"

            # 2. CatBoost predictor test
            p_up, p_down = st.signal_engine.predictor.predict_proba(feat)
            assert not np.isnan(p_up) and not np.isnan(p_down), "CatBoost returned NaN"

            # 3. Kelly Sizing test
            spec = engine._kelly.compute_position(
                symbol="BTC",
                side="long",
                balance_usdt=engine._balance,
                entry_price=mid,
                win_prob=0.65,
                win_loss_ratio=1.5,
                predicted_vol_pct=0.0035,
                atr_price=260.0,
            )
            assert spec is not None, "Kelly failed on valid input"
            assert spec.qty_base > 0, "Kelly qty must be > 0"

            # 4. AS Quote test
            quote = st.as_model.compute_quotes(mid)
            assert quote.spread > 0, "AS spread must be > 0"

            self.metrics.self_test_passed = True
            return True
        except Exception as e:
            self.metrics.self_test_passed = False
            self.record_exception(e, "e2e_self_test")
            return False

    def get_diagnostic_report(self) -> dict:
        now = time.time()
        idle_sec = now - self.metrics.last_trade_ts
        idle_min = int(idle_sec / 60)
        total_ticks = max(1, self.metrics.ticks_processed)

        # Determine diagnosis
        if self.metrics.pipeline_exceptions > 0:
            status = "FAULT_PIPELINE_EXCEPTION"
            diag_text = f"Software error detected: {self.metrics.last_exception_msg}"
        elif not self.metrics.self_test_passed:
            status = "FAULT_SELF_TEST_FAILED"
            diag_text = "E2E self-test failed to validate execution pipeline"
        elif self.metrics.ticks_spread_wide > (total_ticks * 0.8):
            status = "MARKET_SPREAD_TOO_WIDE"
            diag_text = "Exchange bid/ask spread exceeds 8.0 bps limit"
        elif self.metrics.ticks_hawkes_quiet > (total_ticks * 0.7):
            status = "MARKET_CALM_NO_CLUSTERS"
            diag_text = "Low order book activity: Hawkes trade intensity below excitation threshold"
        elif self.metrics.ticks_low_confidence > 0 and self.metrics.max_confidence_seen < 0.60:
            status = "MARKET_SUB_THRESHOLD"
            diag_text = f"CatBoost confidence peak at {self.metrics.max_confidence_seen*100:.1f}% (strictly waiting for >60.0% high-probability setup)"
        else:
            status = "SCANNING_NOMINAL"
            diag_text = "Pipeline fully nominal: scanning for institutional entry conditions"

        return {
            "idle_minutes": idle_min,
            "status": status,
            "diagnosis": diag_text,
            "self_test_passed": self.metrics.self_test_passed,
            "ticks_processed": self.metrics.ticks_processed,
            "max_confidence_seen": self.metrics.max_confidence_seen,
            "pipeline_exceptions": self.metrics.pipeline_exceptions,
            "hawkes_quiet_pct": round((self.metrics.ticks_hawkes_quiet / total_ticks) * 100.0, 1),
            "low_conf_pct": round((self.metrics.ticks_low_confidence / total_ticks) * 100.0, 1),
            "direction_conflicts": self.metrics.ticks_direction_conflict,
            "kelly_rejections": self.metrics.ticks_kelly_rejected,
            "last_check_time": time.strftime("%H:%M:%S", time.gmtime()),
        }

    def should_alert_inactivity(self) -> bool:
        now = time.time()
        if now - self.metrics.last_trade_ts >= self.alert_interval_sec:
            if now - self.metrics.last_alert_ts >= self.alert_interval_sec:
                self.metrics.last_alert_ts = now
                return True
        return False
