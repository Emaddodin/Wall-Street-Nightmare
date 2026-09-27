"""Runner flip strategy + trailing exit (offline)."""
from ghost_grid.runner_exit import RunnerExitController, RunnerExitConfig
from ghost_grid.runner_strategy import RunnerStrategy


def pos(pts, lots=0.01, t=1000.0):
    return [{"volume": lots, "unrealized_pl": pts * lots * 100, "entry_time": t}]


def test_initial_stop_and_trail():
    c = RunnerExitController(RunnerExitConfig(stop_pts=4.0, trail_pts=2.0))
    assert c.evaluate_grid_tick(0, pos(-3.9), 1010).action == "HOLD"
    assert c.evaluate_grid_tick(0, pos(-4.0), 1020).action == "CLOSE_ALL"
    c.reset()
    assert c.evaluate_grid_tick(0, pos(4.5), 1010).action == "HOLD"       # arms, stop -> max(0.3, 2.5)
    assert c.evaluate_grid_tick(0, pos(9.0), 1020).action == "HOLD"       # stop -> 7.0
    assert c.evaluate_grid_tick(0, pos(7.1), 1030).action == "HOLD"
    d = c.evaluate_grid_tick(0, pos(6.9), 1040)
    assert d.action == "CLOSE_ALL" and "Trail" in d.reason


def test_no_profit_cap():
    c = RunnerExitController(RunnerExitConfig(stop_pts=4.0, trail_pts=2.0))
    for i, p in enumerate(range(5, 60, 5)):
        assert c.evaluate_grid_tick(0, pos(p), 1000 + i).action == "HOLD"


def _bars(n, base=4000.0, step=0.0):
    t0 = 1_800_000_000 - (1_800_000_000 % 300)
    out = []
    for i in range(n):
        px = base + i * step
        out.append({"minute_ts": t0 + i * 60, "open_time": (t0 + i * 60) * 1000, "open": px, "high": px + 1.5, "low": px - 1.5, "close": px, "volume": 1.0})
    return out


def test_no_signal_in_range_and_only_on_bar_close():
    s = RunnerStrategy(lookback=12, atr_min=1.0)
    assert s.evaluate(_bars(400)) is None


def test_breakout_fires_once_per_completed_5m_bar():
    s = RunnerStrategy(lookback=12, atr_min=1.0)
    bars = _bars(400)
    while (bars[-1]["minute_ts"] // 60) % 5 != 4:   # end exactly on a completed 5m bar
        bars.pop()
    for b in bars[-5:]:
        b["high"] += 30.0
        b["close"] += 30.0
    sig = s.evaluate(bars)
    assert sig and sig.direction == "BUY"
    assert s.evaluate(bars[:-1]) is None                 # mid-bar: no signal
