"""MR P FX scalp strategy + exit controller correctness (offline)."""
from ghost_grid.exit_controller import GridExitController, GridExitConfig
from ghost_grid.mrp_break_retest import MRPBreakRetestStrategy


def pos(vol, pl, t=1000.0):
    return {"volume": vol, "unrealized_pl": pl, "entry_time": t}


def test_target_is_lot_aware():
    for lots in (0.03, 0.30):
        c = GridExitController(GridExitConfig())
        pl = 0.85 * lots * 100  # +0.85 pt net
        d = c.evaluate_grid_tick(0, [pos(lots, pl)], 1010.0)
        assert d.action == "CLOSE_ALL" and "Target" in d.reason


def test_small_dollar_move_at_big_lots_does_not_exit():
    c = GridExitController(GridExitConfig())
    d = c.evaluate_grid_tick(0, [pos(0.25, 1.30)], 1010.0)  # $1.30 on 0.25 lots = 0.05pt
    assert d.action == "HOLD"


def test_hard_stop_and_stagnation_clock_from_entry():
    c = GridExitController(GridExitConfig())
    assert c.evaluate_grid_tick(0, [pos(0.03, -3.99)], 1005.0).action == "CLOSE_ALL"  # -1.33pt
    c.reset()
    assert c.evaluate_grid_tick(0, [pos(0.03, 0.0)], 1050.0).action == "CLOSE_ALL"   # 50s flat


def _candles(n_minutes, base=4000.0):
    out = []
    t0 = 1_800_000_000
    t0 -= t0 % 300
    for i in range(n_minutes):
        px = base + i * 0.02
        out.append({"minute_ts": t0 + i * 60, "open_time": (t0 + i * 60) * 1000,
                    "open": px, "high": px + 0.15, "low": px - 0.15, "close": px + 0.05, "volume": 10.0})
    return out


def test_flat_market_gives_no_signal():
    s = MRPBreakRetestStrategy()
    assert s.evaluate(_candles(120)) is None


def test_breakout_bar_cannot_be_its_own_retest():
    s = MRPBreakRetestStrategy()
    c = _candles(90)
    # blow through the range in the last 1m bars of a 5m window: still no signal until the bar closes
    for k in range(-4, 0):
        c[k]["high"] += 5.0
        c[k]["close"] += 5.0
    assert s.evaluate(c) is None
