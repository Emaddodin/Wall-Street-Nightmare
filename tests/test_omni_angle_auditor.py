"""
tests/test_omni_angle_auditor.py
================================
Unit tests verifying the 6-domain Omni-Angle Autonomous Auditor Suite.
"""

import json
import time
from unittest.mock import MagicMock, patch

import pytest
from scalper.sentinel.omni_angle_auditor import (
    AngleAuditResult,
    OmniAngleAuditor,
    OmniAuditReport,
)


@pytest.fixture
def auditor(tmp_path, monkeypatch):
    monkeypatch.setattr("scalper.sentinel.omni_angle_auditor.REPORTS_DIR", tmp_path / "audit_reports")
    (tmp_path / "audit_reports").mkdir(parents=True, exist_ok=True)
    return OmniAngleAuditor(cycle_interval_sec=1800)


@pytest.fixture
def mock_telemetry():
    return {
        "engine": "XAUUSD Stratton Oakmont Broker LIVE Engine",
        "status": "ACTIVE",
        "bot_running": True,
        "fsm_state": "SCANNING",
        "mode": "BROKER LIVE (LiteFinance DEMO Account)",
        "account_mode": "DEMO",
        "symbol": "XAUUSD",
        "balance": 32.86,
        "equity": 32.86,
        "realized_pnl": 0.0,
        "pnl_pct": 0.0,
        "current_price": 4305.50,
        "mid_price": 4305.50,
        "best_bid": 4305.39,
        "best_ask": 4305.61,
        "spread_bps": 0.51,
        "position": None,
        "laya": {
            "latency_ms": 0.45,
            "last_confidence": 91.5,
            "last_trap_prob": 12.0,
            "confluence_score": 8.8,
            "politician": {
                "heat_index": 44.0,
                "political_regime": "NEUTRAL_CHOP",
                "shield_status": "ACTIVE_PROTECTION",
                "breaking_news": [{"title": "Headline 1"}, {"title": "Headline 2"}],
            },
        },
        "updated_at": time.time(),
    }


def test_angle_1_system_infrastructure_pass(auditor):
    with patch("subprocess.run") as mock_sub:
        # Mock systemctl is-active -> "active"
        mock_sub.side_effect = [
            MagicMock(stdout="active\n"),  # systemctl is-active
            MagicMock(stdout="123 1 chrome --headless --remote-debugging\n"),  # ps -eo
            MagicMock(stdout="root 1 0 0 S ? 0:00 /init\n"),  # ps aux (no zombies)
        ]
        res = auditor.audit_angle_1_system_infrastructure(api_lat_ms=12.5)
        assert res.angle_id == 1
        assert res.passed is True
        assert res.score >= 90.0
        assert res.details["service_status"] == "active"


def test_angle_2_market_feed_valid_quotes(auditor, mock_telemetry):
    res = auditor.audit_angle_2_market_feed(mock_telemetry)
    assert res.angle_id == 2
    assert res.passed is True
    assert res.score == 100.0
    assert res.details["mid_price"] == 4305.50
    assert res.details["spread_usd"] == 0.22


def test_angle_2_market_feed_stalled_quote_fails(auditor, mock_telemetry):
    mock_telemetry["updated_at"] = time.time() - 40.0  # 40s ago (>35s)
    res = auditor.audit_angle_2_market_feed(mock_telemetry)
    assert res.passed is False
    assert any("stalled" in e for e in res.errors)


def test_angle_3_quant_risk_margin_001_clamp(auditor, mock_telemetry):
    res = auditor.audit_angle_3_quant_risk_margin(mock_telemetry)
    assert res.angle_id == 3
    assert res.passed is True
    assert res.details["persisted_real_balance"] == 29.66
    assert res.details["projected_margin_level_pct"] >= 320.0


def test_angle_3_quant_risk_allows_002_titan_with_tight_sl(auditor, mock_telemetry):
    # Option B: Grade A+ Titan 0.02 lots with tight 1.40 pt stop ($2.80 risk) is valid on micro balance
    mock_telemetry["position"] = {
        "volume": 0.02,
        "entry_price": 4305.0,
        "sl_price": 4303.60,  # 1.40 pts (< 1.80 limit)
    }
    res = auditor.audit_angle_3_quant_risk_margin(mock_telemetry)
    assert res.passed is True
    assert res.score == 100.0


def test_angle_3_quant_risk_rejects_002_with_wide_sl(auditor, mock_telemetry):
    # Option B: 0.02 lots with wide stop (2.20 pts > 1.80 pt cap) must be flagged
    mock_telemetry["position"] = {
        "volume": 0.02,
        "entry_price": 4305.0,
        "sl_price": 4302.80,  # 2.20 pts
    }
    res = auditor.audit_angle_3_quant_risk_margin(mock_telemetry)
    assert res.passed is False
    assert any("exceeds A+ micro cap" in e for e in res.errors)


def test_angle_3_quant_risk_detects_volume_clamp_breach(auditor, mock_telemetry):
    # Open position with 0.03+ lots on $29.66 balance -> must trigger critical error
    mock_telemetry["position"] = {
        "volume": 0.03,
        "entry_price": 4305.0,
        "sl_price": 4303.60,
    }
    res = auditor.audit_angle_3_quant_risk_margin(mock_telemetry)
    assert res.passed is False
    assert any("INVIOLABLE CLAMP BREACH" in e for e in res.errors)


def test_angle_4_strategy_ai_pipeline_pass(auditor, mock_telemetry):
    res = auditor.audit_angle_4_strategy_ai_pipeline(mock_telemetry)
    assert res.angle_id == 4
    assert res.passed is True
    assert res.details["laya_latency_ms"] == 0.45
    assert res.details["laya_confidence"] == 91.5
    assert res.details["geopolitical_heat"] == 44.0


def test_angle_5_state_machine_consistency(auditor, mock_telemetry):
    res = auditor.audit_angle_5_state_machine(mock_telemetry)
    assert res.angle_id == 5
    assert res.passed is True
    assert res.details["fsm_state"] == "SCANNING"

    # Test FSM desync: position open but state says SCANNING
    mock_telemetry["position"] = {"floating_pnl": 1.50, "open_time": time.time()}
    res_bad = auditor.audit_angle_5_state_machine(mock_telemetry)
    assert res_bad.passed is False
    assert any("desync" in e for e in res_bad.errors)


def test_angle_6_log_health_clean_logs(auditor):
    with patch("subprocess.run") as mock_sub:
        mock_sub.return_value = MagicMock(
            stdout="2026-09-25 10:56:48 [INFO] Live engine running smoothly\n"
                   "2026-09-25 10:56:49 [INFO] Quote tick processed\n"
        )
        res = auditor.audit_angle_6_log_health()
        assert res.angle_id == 6
        assert res.passed is True
        assert res.score == 100.0


def test_angle_6_log_health_detects_traceback(auditor):
    with patch("subprocess.run") as mock_sub:
        mock_sub.return_value = MagicMock(
            stdout="Traceback (most recent call last):\n  File 'test.py', line 1, in <module>\n"
        )
        res = auditor.audit_angle_6_log_health()
        assert res.passed is False
        assert any("Traceback" in e for e in res.errors)


def test_run_full_omni_audit_report_generation(auditor, mock_telemetry):
    with patch.object(auditor, "query_live_api", return_value=(mock_telemetry, 8.5)), \
         patch.object(auditor, "_dispatch_audit_notification"), \
         patch("subprocess.run") as mock_sub:
        mock_sub.side_effect = [
            MagicMock(stdout="active\n"),
            MagicMock(stdout="123 1 chrome --headless --remote-debugging\n"),
            MagicMock(stdout="root 1 0 0 S ? 0:00 /init\n"),
            MagicMock(stdout="[INFO] Everything clean\n"),
        ]
        report = auditor.run_full_omni_audit()
        assert isinstance(report, OmniAuditReport)
        assert len(report.angles) == 6
        assert report.overall_passed is True
        assert report.overall_score >= 90.0

        # Verify files were generated
        from scalper.sentinel import omni_angle_auditor
        json_file = omni_angle_auditor.REPORTS_DIR / "audit_latest.json"
        md_file = omni_angle_auditor.REPORTS_DIR / "audit_latest.md"
        assert json_file.exists()
        assert md_file.exists()
        assert "Domain Scorecard" in md_file.read_text()
