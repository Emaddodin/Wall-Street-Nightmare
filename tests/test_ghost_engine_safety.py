"""Offline safety tests for GhostEngine (no broker, no network)."""
import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from unittest import mock

import pytest

from ghost_grid.ghost_engine import GhostEngine, MIN_REAL_CANDLES


@dataclass
class Q:
    bid: float
    ask: float
    timestamp: float

    @property
    def mid(self):
        return round((self.bid + self.ask) / 2, 3)


@dataclass
class Acc:
    balance: float = 30.0
    equity: float = 30.0
    assets_used: float = 0.0
    available: float = 30.0
    floating_pnl: float = 0.0


class FakeGateway:
    def __init__(self, flatten_ok=True):
        self.flatten_ok = flatten_ok
        self.flatten_calls = 0
        self.orders = []

    async def get_account_snapshot(self, force_fresh=False):
        return Acc()

    async def flatten_all_positions(self):
        self.flatten_calls += 1
        return {"success": self.flatten_ok, "assets_used": 0.0 if self.flatten_ok else 5.0}

    async def open_market_order(self, **kw):
        self.orders.append(kw)
        return {"success": True}


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("ghost_grid.ghost_engine.push_ntfy", lambda *a, **k: None)
    return GhostEngine(FakeGateway())


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_no_synthetic_warmup(engine):
    q = Q(4000.0, 4000.2, 1_800_000_000.0)
    run(engine._update_candles(q))
    assert len(engine.candles_1m) == 0  # no fake history seeded


def test_basket_stop_scales_with_lots(engine):
    engine.active_positions = [{"volume": 0.01}]
    assert engine._basket_stop_usd() == round(engine.stop_pts * 1.0, 2)
    engine.active_positions = [{"volume": 0.03}]
    assert engine._basket_stop_usd() == round(engine.stop_pts * 3.0, 2)


def test_failed_flatten_keeps_positions_and_halts(engine):
    engine.gateway = FakeGateway(flatten_ok=False)
    engine.active_positions = [{"direction": "BUY", "volume": 0.01, "entry_price": 4000.0}]
    run(engine._flatten_grid(is_win=False, reason="test"))
    assert engine.halted and engine.active_positions
    assert engine.gateway.flatten_calls == 3


def test_saturday_blocks_entries_and_flattens(engine):
    engine.active_positions = [{"direction": "BUY", "volume": 0.01, "entry_price": 4000.0}]
    sat = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)  # Saturday
    with mock.patch("ghost_grid.ghost_engine.datetime") as dt:
        dt.now.return_value = sat
        dt.fromtimestamp.side_effect = datetime.fromtimestamp
        run(engine.tick(Q(4000.0, 4000.2, time.time())))
    assert engine.gateway.flatten_calls >= 1


def test_wide_spread_still_manages_exits(engine):
    engine.active_positions = [{"direction": "BUY", "volume": 0.03, "entry_price": 4010.0}]
    engine.grid_start_time = time.time()
    wed = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
    with mock.patch("ghost_grid.ghost_engine.datetime") as dt:
        dt.now.return_value = wed
        dt.fromtimestamp.side_effect = datetime.fromtimestamp
        run(engine.tick(Q(4000.0, 4001.0, time.time())))  # spread $1.00, deep loss
    assert engine.gateway.flatten_calls >= 1
