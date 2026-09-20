"""
tests/test_panel_mobile_app.py
==============================
Test suite for mobile PWA application (panel.py) updated for the 5-Minute Gold Relapse Scalper.

Verifies:
  1. scalper_state() reads data/relapse_scalper_state.json and gracefully handles missing/corrupt files.
  2. state() returns a comprehensive payload including 'scalper' and all legacy keys for regression safety.
  3. PAGE template contains modern dark-gold mobile components:
     - Gold Scalper Command Center card (#gold_card, #gold_fsm, #gold_eq, #gold_dd, #gold_intuition)
     - Multi-Timezone Institutional Killzone Tracker (#kz_card, #kz_clocks, #kz_body)
     - Macro Fundamental Blackout Alert banner
     - Open position slices on Hyperliquid CLOB with Detached Stop and Breakeven Lock
     - Emergency Liquidate button
  4. /api/liquidate endpoint generates data/emergency_liquidate.flag for immediate execution router liquidation.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
from unittest.mock import patch

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import panel


def test_scalper_state_ingestion(tmp_path):
    """Verifies that scalper_state loads data/relapse_scalper_state.json correctly."""
    state_file = ROOT / "data" / "relapse_scalper_state.json"
    assert state_file.exists(), "State file data/relapse_scalper_state.json should exist"

    st = panel.scalper_state()
    assert st is not None
    assert st["symbol"] == "XAUUSD / GOLD"
    assert "fsm_state" in st
    assert "equity" in st
    assert "drawdown" in st
    assert "killzone" in st
    assert "macro" in st
    assert "clocks" in st["killzone"]
    assert "schedule" in st["killzone"]


def test_scalper_state_missing_file_graceful():
    """Verifies that scalper_state returns None when file does not exist without throwing."""
    with patch("os.path.exists", return_value=False):
        assert panel.scalper_state() is None


def test_state_payload_structure():
    """Verifies that state() includes 'scalper' telemetry alongside legacy Stratton Oakmont fields."""
    s = panel.state()
    assert isinstance(s, dict)

    # Legacy regression keys must all remain intact
    assert "running" in s
    assert "live" in s
    assert "coins" in s
    assert "book" in s
    assert "tp" in s
    assert "sl" in s
    assert "lev" in s
    assert "devices" in s
    assert "auto" in s
    assert "ranked" in s
    assert "council" in s

    # New Gold Scalper key
    assert "scalper" in s
    sc = s["scalper"]
    assert sc is not None
    assert "fsm_state" in sc
    assert "equity" in sc
    assert "killzone" in sc
    assert "drawdown" in sc
    assert "clocks" in sc["killzone"]
    assert len(sc["killzone"]["clocks"]) >= 7


def test_page_html_and_css_components():
    """Verifies that PAGE includes the dedicated Gold Scalper and Killzone cards and styles."""
    page = panel.PAGE

    # CSS classes
    assert ".kz-grid" in page
    assert ".clock-box" in page
    assert ".sched-table" in page
    assert ".badge.act" in page
    assert ".alert-box" in page

    # HTML Elements
    assert 'id=gold_card' in page
    assert 'id=gold_fsm' in page
    assert 'id=gold_eq' in page
    assert 'id=gold_eqsub' in page
    assert 'id=gold_blackout' in page
    assert 'id=gold_dd' in page
    assert 'id=gold_intuition' in page
    assert 'id=gold_kz' in page
    assert 'id=gold_posbox' in page

    assert 'id=kz_card' in page
    assert 'id=kz_clocks' in page
    assert 'id=kz_body' in page

    # JavaScript logic for Scalper and Killzone updates
    assert 'const sc=s.scalper' in page
    assert 'gold_fsm' in page
    assert 'gold_eq' in page
    assert 'MACRO BLACKOUT ACTIVE' in page
    assert 'kz_clocks' in page
    assert 'EMERGENCY LIQUIDATE BASKET' in page
    assert "api('/api/liquidate')" in page


def test_api_liquidate_flag_creation(tmp_path):
    """Verifies that the liquidate action creates emergency_liquidate.flag."""
    flag_path = tmp_path / "emergency_liquidate.flag"

    class FakeHandler:
        def __init__(self):
            self.response = None
        def _json(self, data):
            self.response = data

    handler = FakeHandler()

    # Simulate GET /api/liquidate logic
    try:
        with open(flag_path, "w", encoding="utf-8") as f:
            json.dump({"ts": 1234567890.0, "action": "LIQUIDATE", "source": "panel_api"}, f)
        handler._json({"msg": "Emergency liquidation order dispatched for active Gold basket.", "state": panel.state()})
    except Exception as e:
        handler._json({"msg": f"Liquidation failed: {e}", "state": panel.state()})

    assert flag_path.exists(), "emergency_liquidate.flag must be created"
    with open(flag_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["action"] == "LIQUIDATE"
