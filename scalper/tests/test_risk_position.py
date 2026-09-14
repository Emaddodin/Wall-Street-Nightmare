"""Risk sizing, daily guardrails, position/take-profit mechanics."""
from __future__ import annotations

import json
import numpy as np
import pytest

from config.loader import load_config
from execution import entry_price, exit_price, pnl_usd
from position_manager import (FULL, RUNNER, TP1, Lot, Position,
                              build_position, step_position)
from risk_manager import RiskManager
from structure import LONG, SHORT

DAY_MS = 86_400_000
cfg = load_config(extra_file="config/aggressive.yaml")


# ------------------------------------------------------------------ sizing
def test_size_is_risk_over_distance():
    r = RiskManager(cfg, 100.0)
    # 25% risk, 2% stop -> 12.5x implied, under the 50x cap: full risk
    s = r.size(entry=100.0, stop=98.0)
    assert s["qty"] == pytest.approx(12.5)           # 25 / 2
    assert s["notional"] == pytest.approx(1250.0)
    assert s["leverage"] == pytest.approx(12.5)
    # tiny stop -> cap at 50x, risk shrinks (never grows)
    s2 = r.size(entry=100.0, stop=99.9)
    assert s2["leverage"] == pytest.approx(50.0)
    assert s2["notional"] == pytest.approx(5000.0)


def test_leverage_cap_cuts_size_never_grows_it():
    r = RiskManager(cfg, 100.0)
    s = r.size(entry=100.0, stop=99.9)          # tiny stop -> huge implied lev
    assert s["leverage"] == pytest.approx(cfg.risk["leverage_cap"])
    assert s["notional"] == pytest.approx(100.0 * cfg.risk["leverage_cap"])
    # risk shrank, did not grow
    assert s["risk_amount"] < 100.0 * cfg.risk["risk_per_trade"]


# ------------------------------------------------------------------ limits
def test_daily_loss_limit_halts_entries():
    r = RiskManager(cfg, 100.0)
    r.roll_day(DAY_MS)
    r.on_close(-51.0, DAY_MS)                   # -51% > 50% limit
    ok, why = r.can_enter(DAY_MS + 60_000)
    assert not ok and "loss" in why


def test_consecutive_losses_halt_after_3():
    r = RiskManager(cfg, 100.0)
    r.roll_day(DAY_MS)
    for _ in range(3):
        r.on_close(-1.0, DAY_MS)
    ok, why = r.can_enter(DAY_MS + 1)
    assert not ok and "losses" in why


def test_equity_moves_once_per_position_not_per_lot():
    """One stopped model-B position (3 lots) is ONE loss event and ONE
    equity move -- no per-lot double counting."""
    r = RiskManager(cfg, 100.0)
    r.roll_day(DAY_MS)
    pos = _pos("B")
    closed = step_position(pos, cfg, bar_high=102.5, bar_low=98.5,
                           bar_close_ms=1, last_swing_low=99.0,
                           last_swing_high=101.0, atr1m=0.1, opp_bos=False,
                           fee_bps=6.0, slippage_bps=0.0)
    assert len(closed) == 3                     # all three lots stopped
    total = sum(c["pnl"] for c in closed)
    r.on_close(total, DAY_MS)
    assert r.equity == pytest.approx(100.0 + total)
    assert r.consec_losses == 1                 # ONE losing position
    # aggressive profile: one loss starts the 30-min cooldown, no halt
    ok, _ = r.can_enter(DAY_MS + 31 * 60_000)
    assert ok


def test_cooldown_after_loss_then_recover():
    # the cooldown mechanism is a DEFAULT-profile behaviour; the aggressive
    # profile runs cooldown 0 since hunt r3 (the 2-loss halt guards instead)
    from config.loader import load_config
    dcfg = load_config()                      # default.yaml
    r = RiskManager(dcfg, 100.0)
    r.roll_day(DAY_MS)
    r.on_close(-1.0, DAY_MS)                     # default: 1 loss -> cooldown
    ok, _ = r.can_enter(DAY_MS + 10 * 60_000)
    assert not ok                                # inside 30 min cooldown
    ok, _ = r.can_enter(DAY_MS + 31 * 60_000)
    assert ok


def test_day_roll_resets_everything():
    r = RiskManager(cfg, 100.0)
    r.roll_day(DAY_MS)
    r.on_close(-60.0, DAY_MS)
    ok, _ = r.can_enter(DAY_MS + 60_000)
    assert not ok
    r.roll_day(2 * DAY_MS)
    assert r.halted is False
    assert r.trades_today == 0


def test_max_trades_per_day():
    r = RiskManager(cfg, 100.0)
    r.roll_day(DAY_MS)
    for _ in range(cfg.daily["max_trades_per_day"]):
        r.on_fill(DAY_MS, 0.0)
    ok, why = r.can_enter(DAY_MS + 1)
    assert not ok and "max_trades" in why


# ------------------------------------------------------------------ lots
def _pos(model, direction=LONG, fill=100.0, sl=99.0, qty=10.0):
    c = load_config(extra_file="config/aggressive.yaml",
                    overrides={"tp.model": model})
    return build_position("X", direction, fill, qty, sl, c, {}, 0.1, 0, 1.0)


def test_model_a_single_lot_2r():
    pos = _pos("A")
    assert len(pos.lots) == 1
    assert pos.lots[0].kind == FULL
    assert pos.lots[0].tp == pytest.approx(102.0)


def test_model_b_three_lots_sums_to_one():
    pos = _pos("B")
    assert len(pos.lots) == 3
    assert sum(l.qty for l in pos.lots) == pytest.approx(10.0)
    kinds = {l.kind: l.qty for l in pos.lots}
    assert kinds[TP1] == pytest.approx(5.0)
    assert kinds["tp2"] == pytest.approx(2.5)
    assert kinds[RUNNER] == pytest.approx(2.5)
    assert pos.lots[0].tp == pytest.approx(101.0)      # 1R
    assert pos.lots[1].tp == pytest.approx(102.0)      # 2R


def test_model_c_structure_lot():
    pos = _pos("C")
    assert len(pos.lots) == 1
    assert pos.lots[0].kind == "struct"
    assert pos.lots[0].tp is None


def test_tp1_moves_rest_to_breakeven():
    pos = _pos("B")
    closed = step_position(pos, cfg, bar_high=101.5, bar_low=100.0,
                           bar_close_ms=1, last_swing_low=99.0,
                           last_swing_high=101.0, atr1m=0.1, opp_bos=False,
                           fee_bps=6.0, slippage_bps=2.0)
    kinds = [c["lot"] for c in closed]
    assert TP1 in kinds
    for lot in pos.lots:
        if lot.open:
            assert lot.sl == pytest.approx(lot.entry)   # breakeven


def test_stop_wins_when_both_touch_in_one_bar():
    pos = _pos("B")
    # bar range spans stop and target; conservative -> stop first
    closed = step_position(pos, cfg, bar_high=102.5, bar_low=98.5,
                           bar_close_ms=1, last_swing_low=99.0,
                           last_swing_high=101.0, atr1m=0.1, opp_bos=False,
                           fee_bps=6.0, slippage_bps=0.0)
    assert closed[0]["reason"] == "STOP"


def test_runner_trails_swing_low():
    pos = _pos("B")
    pos.trail_armed = True
    for lot in pos.lots:
        if lot.open:
            lot.sl = lot.entry                    # BE already applied
    step_position(pos, cfg, bar_high=103.0, bar_low=102.5, bar_close_ms=1,
                  last_swing_low=102.8, last_swing_high=103.2, atr1m=0.1,
                  opp_bos=False, fee_bps=6.0, slippage_bps=2.0)
    runner = [l for l in pos.lots if l.kind == RUNNER][0]
    assert runner.sl > 100.0                      # trailed above entry


def test_structure_exit_pends_then_fills_next_open():
    pos = _pos("C")
    closed = step_position(pos, cfg, bar_high=100.5, bar_low=99.5,
                           bar_close_ms=1, last_swing_low=99.0,
                           last_swing_high=101.0, atr1m=0.1, opp_bos=True,
                           fee_bps=6.0, slippage_bps=2.0)
    assert closed == []
    assert pos.struct_exit_pending is True
    from position_manager import execute_struct_exit
    closed = execute_struct_exit(pos, 100.6, 2, 6.0, 2.0)
    assert len(closed) == 1
    assert closed[0]["reason"] == "STRUCT-BOS"


# ------------------------------------------------------------------ fills
def test_fee_and_slippage_arithmetic():
    px, fee_frac = entry_price(100.0, LONG, 6.0, 2.0)
    assert px == pytest.approx(100.02)           # 2 bps = 0.02%
    assert fee_frac == pytest.approx(0.0006)
    ex = exit_price(99.0, LONG, 2.0, adverse=True)
    assert ex == pytest.approx(99.0 * 0.9998)    # stop fills worse
    pnl = pnl_usd(LONG, 1.0, 100.0, 102.0, 0.06, 0.0612)
    assert pnl == pytest.approx(2.0 - 0.06 - 0.0612)


def test_daily_target_halt():
    """The +100% daily halt is back ON (operator reinstated 2026-09-09):
    at +100% entries stop until the next day; -51% still halts on loss."""
    c = load_config(extra_file="config/aggressive.yaml")
    assert c.daily["target_pct"] == 1.0
    r = RiskManager(c, 100.0)
    r.roll_day(DAY_MS)
    r.on_close(+101.0, DAY_MS)
    ok, why = r.can_enter(DAY_MS + 60_000)
    assert not ok and "target" in why
    r3 = RiskManager(c, 100.0)
    r3.roll_day(DAY_MS)
    r3.on_close(-51.0, DAY_MS)
    ok, why = r3.can_enter(DAY_MS + 60_000)
    assert not ok and "loss" in why


def test_25pct_risk_50x_cap_sizing():
    """risk 25%, cap 50x: 0.5% stop -> exactly 50x, full risk applies."""
    c = load_config(extra_file="config/aggressive.yaml")
    assert c.risk["risk_per_trade"] == 0.25
    assert c.risk["leverage_cap"] == 50
    r = RiskManager(c, 100.0)
    s = r.size(entry=100.0, stop=99.5)       # 0.5% stop -> 50x implied
    assert s["leverage"] == pytest.approx(50.0)
    assert s["risk_amount"] == pytest.approx(25.0)
    s2 = r.size(entry=100.0, stop=100.0 - 0.4)   # 0.4% stop -> 62.5x, cut to 50x
    assert s2["leverage"] == pytest.approx(50.0)
    assert s2["risk_amount"] == pytest.approx(20.0)


def test_model_d_without_target_is_a_trailing_runner():
    """A model-D position with no first target (breakout entries) must be a
    single runner lot with the trail armed -- never a stuck 'first' lot."""
    c = load_config(extra_file="config/aggressive.yaml")
    pos = build_position("X", LONG, 100.0, 10.0, 98.0, c, {}, 0.1, 0, 1.0,
                         tp_first=None)
    assert len(pos.lots) == 1
    assert pos.lots[0].kind == "runner"
    assert pos.lots[0].tp is None
    assert pos.trail_armed is True
    # with a target, the usual 50/50 split applies
    pos2 = build_position("X", LONG, 100.0, 10.0, 98.0, c, {}, 0.1, 0, 1.0,
                          tp_first=104.0)
    assert len(pos2.lots) == 2
    assert pos2.lots[0].kind == "first" and pos2.lots[0].tp == 104.0
    assert pos2.trail_armed is False


def test_position_roundtrip_keeps_closed_lots_closed():
    """A closed lot must survive the state round-trip as CLOSED."""
    import json as _json
    c = load_config(extra_file="config/aggressive.yaml")
    pos = build_position("X", LONG, 100.0, 10.0, 98.0, c, {}, 0.1, 0, 1.0,
                         tp_first=104.0)
    pos.realized_pnl = 3.33
    closed = step_position(pos, c, bar_high=105.0, bar_low=100.5,
                           bar_close_ms=1, last_swing_low=100.5,
                           last_swing_high=104.0, atr1m=0.1, opp_bos=False,
                           fee_bps=6.0, slippage_bps=0.0)
    assert closed                      # the first lot hit its target
    d = {
        "lots": [{"qty": l.qty, "kind": l.kind, "sl": l.sl, "tp": l.tp,
                  "entry": l.entry, "entry_fee": l.entry_fee, "tp_r": l.tp_r,
                  "exit_px": l.exit_px, "exit_reason": l.exit_reason,
                  "exit_ms": l.exit_ms, "pnl": l.pnl} for l in pos.lots],
        "realized_pnl": pos.realized_pnl,
    }
    # emulate the paper trader's restore
    from position_manager import Lot, Position
    pos2 = Position(symbol="X", direction=LONG, opened_ms=0, pos_id=1,
                    realized_pnl=d["realized_pnl"])
    for lot in d["lots"]:
        pos2.lots.append(Lot(qty=lot["qty"], kind=lot["kind"], sl=lot["sl"],
                             tp=lot["tp"], entry=lot["entry"],
                             entry_fee=lot["entry_fee"], tp_r=lot["tp_r"],
                             exit_px=lot["exit_px"],
                             exit_reason=lot["exit_reason"],
                             exit_ms=lot["exit_ms"], pnl=lot["pnl"]))
    open_lots = [l for l in pos2.lots if l.open]
    assert len(open_lots) == 1
    assert open_lots[0].kind == "runner"
    assert pos2.realized_pnl == pytest.approx(3.33)


def test_stale_signal_guard_blocks_replayed_bars():
    """Signals from bars older than the freshness window must never trade:
    a symbol that re-enters the feed hours later replays old (e.g. PRIME)
    bars, and filling those signals at today's price = phantom trades."""
    from paper_trader import _stale_signal
    from config.loader import load_config
    cfg = load_config(extra_file="config/aggressive.yaml")
    now = 1_789_000_000_000
    # a bar closed 4 minutes ago is fresh
    assert not _stale_signal(now - 4 * 60_000, now, cfg)
    # a bar closed 6 minutes ago is stale (default window 5 min)
    assert _stale_signal(now - 6 * 60_000, now, cfg)
    # hours-old backlog bars are stale
    assert _stale_signal(now - 5 * 3_600_000, now, cfg)
    # the configured window is honored
    assert not _stale_signal(now - 299_000, now, cfg)
    assert _stale_signal(now - 301_000, now, cfg)


def test_metrics_skip_phantom_records():
    """Replay-artifact records stay in the log but never count in metrics."""
    from metrics import summarize, breakdowns
    recs = [
        {"pos_id": 1, "ts_ms": 1, "symbol": "X", "direction": 1, "lot": "full",
         "pnl": 5.0, "pnl_r": 1.0, "strategy": "vp", "entry_model": "fvg",
         "bias": 1, "atr1m": 0.1, "entry": 100.0, "exit": 101.0},
        {"pos_id": 2, "ts_ms": 2, "symbol": "Y", "direction": -1, "lot": "full",
         "pnl": -999.0, "pnl_r": -9.0, "strategy": "breakout",
         "entry_model": "breakout", "bias": -1, "atr1m": 0.1, "entry": 50.0,
         "exit": 60.0, "phantom": True},
    ]
    s = summarize(recs, [(0, 100.0), (1, 105.0)], start_equity=100.0)
    assert s["n_trades"] == 1
    bd = breakdowns(recs)
    by = {r["name"]: r for r in bd["by_strategy"]}
    assert set(by) == {"vp"}
    assert by["vp"]["pnl"] == pytest.approx(5.0)


def test_fill_through_guard_refuses_knife_catches():
    """The bar that tags a limit must not have already traded through the
    stop (AKEUSDT 2026-09-09: filled and stopped in one bar)."""
    from paper_trader import _fill_through
    from structure import LONG, SHORT
    # LONG: bar low below the stop -> refuse
    assert _fill_through(LONG, 0.01540, 0.01560, 0.01541470)
    # LONG: bar low above the stop -> fine
    assert not _fill_through(LONG, 0.01550, 0.01560, 0.01541470)
    # SHORT: bar high above the stop -> refuse
    assert _fill_through(SHORT, 0.01540, 0.01560, 0.01545)
    assert not _fill_through(SHORT, 0.01540, 0.01544, 0.01545)
    # NaN inputs never trigger the guard
    assert not _fill_through(LONG, None, 0.01560, 0.01541470)
    assert not _fill_through(LONG, 0.01540, 0.01560, float("nan"))


def test_lessons_autopilot_pauses_repeated_losers():
    """Three straight losses for one strategy -> the bot pauses it on its
    own; winners keep other strategies armed."""
    import tempfile
    from pathlib import Path
    import lessons as L
    d = Path(tempfile.mkdtemp())
    for i in range(3):
        L.record_lesson(d, {"ts_ms": 1_000 + i, "symbol": "X",
                            "strategy": "breakout", "entry_model": "breakout",
                            "exit_reason": "STOP", "pnl": -10.0,
                            "pnl_r": -1.0})
    L.record_lesson(d, {"ts_ms": 2_000, "symbol": "Y", "strategy": "vp",
                        "entry_model": "fvg", "exit_reason": "TP-first",
                        "pnl": 20.0, "pnl_r": 2.0})
    rules = L.evaluate(d)
    assert "s:breakout" in rules and "m:breakout" in rules
    assert "s:vp" not in rules
    L.write_autopilot(d, rules)
    paused = L.paused_strategies(d)
    assert paused == {"s:breakout", "m:breakout"}
    # a pause in the future keeps it paused; an old pause does not
    st = json.loads((d / "state" / "autopilot.json").read_text())
    st["s:breakout"]["paused_until_ms"] = 1
    st["m:breakout"]["paused_until_ms"] = 1
    L.write_autopilot(d, st)
    assert L.paused_strategies(d) == set()
    # daily review summarises the day
    rev = L.daily_review(d, 0, 9_999_999_999_999)
    assert rev["n"] == 4 and rev["wins"] == 1 and rev["losses"] == 3


def test_discovery_pauses_symbols_and_hours():
    """The bot discovers its own rules: a symbol losing 2+ with 0 wins in
    24h is auto-skipped 6h; an hour with 4+ trades and 0 wins over 72h is
    auto-skipped 24h."""
    import tempfile
    import time
    from pathlib import Path
    import lessons as L
    d = Path(tempfile.mkdtemp())
    now = int(time.time() * 1000)
    for i in range(3):
        L.record_lesson(d, {"ts_ms": now - i * 60_000, "symbol": "LOSR",
                            "strategy": "vp", "entry_model": "fvg",
                            "exit_reason": "STOP", "pnl": -5.0, "pnl_r": -1.0})
    # same losing hour (same ts hour), 4 trades 0 wins
    for i in range(4):
        L.record_lesson(d, {"ts_ms": now - i * 60_000, "symbol": f"L{i}",
                            "strategy": "vp", "entry_model": "fvg",
                            "exit_reason": "STOP", "pnl": -1.0, "pnl_r": -0.5})
    rules = L.discover(d)
    assert "sym:LOSR" in rules
    assert any(k.startswith("hour:") for k in rules)
    assert rules["sym:LOSR"]["paused_until_ms"] > now


def test_advance_bars_never_starves_the_book():
    """The regression that matters: a new 1m bar must ALWAYS produce a
    non-empty evaluation range -- growing or sliding window, first contact
    or not.  (The old index bookkeeping returned an empty range forever
    after the first pass and silently starved the live book.)"""
    import numpy as np
    from paper_trader import advance_bars
    MIN = 60_000
    t0 = 1_789_000_000_000
    base = np.array([t0 + i * MIN for i in range(120)], dtype=np.float64)
    n = len(base)

    # first contact: evaluates from start_i, watermark = close of n-2
    start, last = advance_bars(base, n, 40, None)
    assert start == 40
    assert last == int(base[n - 2] + MIN)

    # growing store (+1 new bar): the new closed bar IS in the range
    grown = np.append(base, base[-1] + MIN)
    start, last2 = advance_bars(grown, n + 1, 40, last)
    assert start <= n - 1
    assert start == n - 1                    # exactly the new closed bar
    assert last2 == int(grown[n - 1] + MIN)

    # no new bar: empty range, no duplicates
    start, last3 = advance_bars(grown, n + 1, 40, last2)
    assert start >= n + 1 - 1 or start == n    # range(start, n) is empty
    assert (n + 1 - 1) - start <= 0

    # sliding window (same length, times shifted +1 bar): new bar at n-2
    slid = base + MIN
    start, last4 = advance_bars(slid, n, 40, int(base[n - 2] + MIN))
    assert start == n - 2
    assert last4 == int(slid[n - 2] + MIN)
