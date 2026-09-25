"""
tests/backtest_live_loop_trump_regime.py
=========================================
Live-Loop Fidelity Backtest of the Trump Regime (473 trading days).

WHY THIS EXISTS
---------------
The two pre-existing harnesses do not run production code on the exit side:

  * tests/run_30usd_trump_regime_backtest.py  ->  never imports MicroExitController.
  * tests/backtest_trump_regime_hyper_scalp.py ->  imports it but re-implements the
    exit chain inline instead of calling controller.evaluate_tick().

This harness closes that gap. It reuses the anchor's precompute + signal engine so
ENTRIES stay 1:1 identical, and replaces the exit side with the production chain
copied verbatim from run_xau_broker_live.py (see EXIT CHAIN below). Therefore any
change in the metrics is attributable to exit-side code edits and nothing else.

WHAT IS SIMULATED FAITHFULLY
----------------------------
Entry engine (imported / copied verbatim from the 473-day anchor):
  * 5m breakout + retest + rejection pin bar (Institutional 83.7% WR setup)
  * regime_prior_engine + politician_brain entry gates
  * micro lot sizing ladder + Titan 1.65x boost
  * real friction: $0.30 spread charged on entry AND exit, $0.10 slippage on exit

Exit chain (run_xau_broker_live.py:700-830), evaluated per synthetic tick:
  1. Friday force-flatten                      (:769)
  2. MicroExitController.evaluate_tick()       (:725)  <- the REAL class, real config
  3. Ratchet 1 - Breakeven sync                (:732)
  4. Ratchet 2 - TP1 profit lock @ 1.8 ATR     (:740)
  5. Structure invalidation (1m bar reversal)   (:749)
  6. Software SL check                         (:762)
  7. Macro spike harvest (6.5 / 2.5 ATR)       (:766)
  Account risk state: cooldown / lockout / circuit breaker (:817-831)

Tick model:
  4 base points per 1m bar following the standard OHLC traversal
  (up bar: O->L->H->C, down bar: O->H->L->C), 15s apart.
  PLUS an interpolated tick inserted exactly at every exit-triggering level
  (stop price and structure level) whenever the path crosses it, so fills land
  at the level rather than at the next 15s sample.
  BUY exec price = mid - spread/2 (bid), SELL exec price = mid + spread/2 (ask),
  matching `pos_exec_px` in run_xau_broker_live.py:710.
  Entry price = the chart/mid price, matching production's `sig.entry_price`
  (apex_trinity.py:227) so every threshold compares the same way it does live.
  Round-trip friction therefore = half spread (entry mid vs exit bid/ask) +
  $0.10 slippage charged on the closing fill = $0.25, which is exactly how much
  the live loop itself charges. The reference anchor charges $0.55; that model
  difference is expected and is why the gate compares this harness against its
  own baseline, not against the anchor's headline numbers.
  Profit-side exits fill at the next base sample (slightly optimistic, identical
  for baseline and post-edit, so it does not bias the A/B gate).

MODES
-----
  --mode serial        max_parallel = 1, scan only when flat -> entry parity with
                       the anchor. This is the mandatory A/B gate for exit edits.
  --mode live          production parallel rule:
                         max_parallel = 1 if bal < 75 else (2 if bal < 150 else 3)
                       plus the "all prior positions must be at breakeven" guard.
  --exit-scope flatten current production behaviour: ONE trigger flattens EVERYTHING
                       (run_xau_broker_live.py:807).
  --exit-scope single  close only the triggering ticket (candidate edit B1).

BASELINE PROTOCOL
-----------------
  Baseline A  = this script run on the UNMODIFIED tree (capture BEFORE any edit).
  After edits = same script, same data, same flags.
  Gate: post-edit >= baseline on PF / WR / expectancy / trades / net,
        and max drawdown may only fall.

Outputs (default prefix data/live_loop_):
  <tag>_summary.json   headline metrics
  <tag>_journal.csv    per-trade journal
  <tag>_daily.csv      daily ledger
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scalper.strategies.micro_exit_controller import (  # noqa: E402
    MicroExitController,
    get_micro_account_config,
    get_to_the_moon_config,
)

# --------------------------------------------------------------------------
# Reuse the anchor's precompute / sizing / journal definitions.
# Imported as a normal module (NOT via importlib) so that the ProcessPoolExecutor
# workers in precompute_day_causal can re-import it by qualified name under the
# "spawn" start method.
# --------------------------------------------------------------------------
_TESTS_DIR = ROOT_DIR / "tests"
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))
import run_30usd_trump_regime_backtest as anchor  # noqa: E402

precompute_day_causal = anchor.precompute_day_causal
compute_micro_lot_size = anchor.compute_micro_lot_size
MicroJournalEntry = anchor.MicroJournalEntry

# Friction model (identical inputs to the anchor)
SPREAD = 0.30
SLIPPAGE = 0.10

# Mirrors RAPID_SPIKE_TARGET_USD (run_xau_broker_live.py:78)
RAPID_SPIKE_TARGET_USD = 50.00

EXIT_SCOPE_FLATTEN = "flatten"
EXIT_SCOPE_SINGLE = "single"


def build_exit_config(balance: float, direction: str):
    """run_xau_broker_live.py:1077 - micro config under $100, To-The-Moon above."""
    if balance < 100.0:
        return get_micro_account_config(balance=balance, direction=direction)
    return get_to_the_moon_config("XAUUSD", direction=direction)


def raw_path(op: float, hi: float, lo: float, cl: float, t0: float) -> List[Any]:
    """4 base points per 1m bar, 15s apart, standard OHLC traversal."""
    path = (op, lo, hi, cl) if cl >= op else (op, hi, lo, cl)
    return [(px, t0 + k * 15.0) for k, px in enumerate(path)]


def ticks_with_stop(raw: List[Any], pos: Dict[str, Any]) -> List[Any]:
    """
    Build the executable tick stream for one bar and guarantee that any exit
    level is filled AT its price.

    1. Raw mid prices are converted to the executable side, exactly as production
       does (pos_exec_px = bid for BUY, ask for SELL -> run_xau_broker_live.py:710).
    2. The previous bar's last price is prepended, so the inter-bar gap is also
       scanned for crossings (m1 opens differ from the prior close in this dataset).
    3. An interpolated tick is inserted AT every level that can trigger an exit
       whenever the path crosses it:
           * the position's current stop price -> RISK_STOP / BREAKEVEN_CUSHION / TRAILING_SL
           * the structure level -> prev bar low (BUY) / high (SELL)
           * the dollar hard-risk stop -> HARD_STOP
       Without this a coarse sample lets price trade far past the level, which
       systematically inflates every losing trade. This is the standard backtest
       convention: a resting level fills AT its price when traded through.
       Levels are re-read per segment so breakeven/TP1 ratchets that fire mid-bar
       are honoured immediately.
    """
    direction = pos["direction"]
    half = SPREAD / 2.0
    # mid -> executable side
    if direction == "BUY":
        pts = [(px - half, ts) for px, ts in raw]
    else:
        pts = [(px + half, ts) for px, ts in raw]

    # Carry the last traded price across the bar boundary so gaps are scanned too
    last_px = pos.get("_last_px")
    if last_px is not None and pts:
        if abs(last_px - pts[0][0]) > 1e-12:
            pts = [(last_px, pts[0][1] - 0.001)] + pts

    struct_level = pos.get("_struct_level", 0.0)
    # Structure invalidation uses a STRICT inequality (close < prev_low for BUY),
    # so its tick is nudged a sub-quote beyond the level; production exits on the
    # first 20ms tick that trades past it, so the fill is the level itself.
    # Structure invalidation uses a STRICT inequality (close < prev_low for BUY),
    # and the dollar risk stop is compared on a value rounded to 2dp
    # (round(-3.125, 2) == -3.12, which fails `<= -3.125`). Both ticks are nudged a
    # sub-quote beyond their level so they fire exactly there instead of at the
    # next coarse sample, matching production's 20ms sampling.
    eps = -0.005 if direction == "BUY" else 0.005

    # Dollar-denominated hard risk stop (RULE 0: floating_pnl <= -dynamic_hard_stop)
    risk_level = 0.0
    ctrl = pos.get("micro_exit")
    vol = pos.get("volume") or 0.0
    if ctrl is not None and vol > 0.0:
        stop_usd = float(getattr(ctrl, "dynamic_hard_stop", ctrl.cfg.hard_risk_stop_usd))
        delta = stop_usd / (100.0 * vol)
        risk_level = pos["entry_price"] - delta if direction == "BUY" else pos["entry_price"] + delta

    levels = [
        (pos.get("sl_price", 0.0) or 0.0, 0.0),
        (struct_level or 0.0, eps),
        (risk_level, eps),
    ]

    out: List[Any] = []
    for idx, (px, ts) in enumerate(pts):
        if idx > 0:
            prev_px, prev_ts = pts[idx - 1]
            span = ts - prev_ts
            crossings: List[Any] = []
            for level, offset in levels:
                if not level or level <= 0.0:
                    continue
                if direction == "BUY" and prev_px > level >= px:
                    frac = (prev_px - level) / (prev_px - px) if prev_px != px else 0.0
                    crossings.append((level + offset, prev_ts + span * frac))
                elif direction == "SELL" and prev_px < level <= px:
                    frac = (level - prev_px) / (px - prev_px) if px != prev_px else 0.0
                    crossings.append((level + offset, prev_ts + span * frac))
            crossings.sort(key=lambda kv: kv[1])  # order by time
            out.extend(crossings)
        out.append((px, ts))

    if out:
        pos["_last_px"] = out[-1][0]
    return out


def max_parallel_for(balance: float) -> int:
    """run_xau_broker_live.py:919 - live parallel capacity ladder."""
    if balance < 75.0:
        return 1
    return 2 if balance < 150.0 else 3


def is_friday_flatten(ts: float) -> bool:
    """run_xau_broker_live.py:700 - Friday 20:30 UTC force-flatten."""
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.weekday() == 4 and (dt.hour > 20 or (dt.hour == 20 and dt.minute >= 30))


class RiskState:
    """Cooldown / lockout / circuit breaker (run_xau_broker_live.py:817-831)."""

    def __init__(self) -> None:
        self.cooldown_until: float = 0.0
        self.lockout_until: float = 0.0
        self.consecutive_losses: int = 0
        self.daily_realized_loss: float = 0.0
        self.circuit_breaker_active: bool = False
        self.daily_start_balance: float = 0.0
        self.daily_date: str = ""

    def on_new_day(self, date_str: str, balance: float) -> None:
        if self.daily_date != date_str:
            self.daily_date = date_str
            self.daily_start_balance = balance
            self.daily_realized_loss = 0.0
            self.consecutive_losses = 0
            self.circuit_breaker_active = False

    def on_close(self, tot_pnl: float, balance: float, now: float) -> None:
        if tot_pnl < 0:
            self.consecutive_losses += 1
            self.daily_realized_loss += abs(tot_pnl)
            cd_time = 90.0 if abs(tot_pnl) < 1.0 else 240.0
            self.cooldown_until = now + cd_time
            if self.consecutive_losses >= 2:
                self.lockout_until = now + 1800.0
            max_day_loss = min(50.0, max(10.0, self.daily_start_balance * 0.15))
            if self.daily_realized_loss >= max_day_loss:
                self.circuit_breaker_active = True
        else:
            self.consecutive_losses = 0

    def entries_allowed(self, now: float) -> bool:
        if self.circuit_breaker_active:
            return False
        return now >= self.cooldown_until and now >= self.lockout_until


def make_position(
    *,
    direction: str,
    strategy: str,
    entry_price: float,
    entry_time: float,
    volume: float,
    sl_price: float,
    tp1_price: float,
    atr_1m: float,
    balance: float,
    laya_grade: str,
    compounding_boost: float,
    tp_expansion: float,
    politician_regime: str,
    hour: int,
) -> Dict[str, Any]:
    """Mirrors new_position (run_xau_broker_live.py:1087-1109)."""
    cfg = build_exit_config(balance, direction)
    ctrl = MicroExitController(cfg)
    ctrl.arm_position(
        entry_price=entry_price,
        direction=direction,
        total_volume=volume,
        sl_price=sl_price,
        open_time=entry_time,
    )
    return {
        "direction": direction,
        "strategy": strategy,
        "volume": volume,
        "entry_price": entry_price,
        "entry_time": entry_time,
        "hour": hour,
        "sl_price": sl_price,
        "tp1_price": tp1_price,
        "atr_1m": atr_1m,
        "strategy_type": strategy,
        "open_time": entry_time,
        "floating_pnl": 0.0,
        "peak_pnl": 0.0,
        "be_ratchet_hit": False,
        "tp1_ratchet_hit": False,
        "is_hold_long": direction == "BUY",
        "micro_exit": ctrl,
        "laya_grade": laya_grade,
        "compounding_boost": compounding_boost,
        "tp_expansion": tp_expansion,
        "politician_regime": politician_regime,
        "bars_held": 0,
        "entry_bar_idx": 0,
        "wick_ratio": 0.5,
        # Executable price at entry (bid for BUY, ask for SELL); anchors the
        # cross-bar gap scan in ticks_with_stop().
        "_last_px": entry_price - SPREAD / 2.0 if direction == "BUY" else entry_price + SPREAD / 2.0,
        "_struct_level": 0.0,
    }


def evaluate_position_tick(
    pos: Dict[str, Any],
    tick_price: float,
    tick_time: float,
    candles_prev_close: float,
    candles_prev_low: float,
    candles_prev_high: float,
    last_close: float,
    last_low: float,
    last_high: float,
    balance: float,
    friday: bool,
) -> Optional[Dict[str, Any]]:
    """
    One production tick of exit evaluation.
    Returns exit dict, or None to keep holding.
    Mirrors run_xau_broker_live.py:705-805.
    """
    direction = pos["direction"]
    entry_px = pos["entry_price"]
    atr = pos.get("atr_1m", 1.50)
    vol = pos["volume"]
    pos_exec_px = tick_price
    gain_pts = (pos_exec_px - entry_px) if direction == "BUY" else (entry_px - pos_exec_px)
    time_held_sec = tick_time - pos["open_time"]

    pos_pnl = round(gain_pts * 100.0 * vol, 2)
    pos["floating_pnl"] = pos_pnl
    if pos_pnl > pos.get("peak_pnl", 0.0):
        pos["peak_pnl"] = pos_pnl

    # --- MicroExitController evaluation ---
    controller: Optional[MicroExitController] = pos.get("micro_exit")
    if controller:
        exit_dec = controller.evaluate_tick(
            current_price=pos_exec_px,
            floating_pnl=pos_pnl,
            current_time=tick_time,
        )
        # Ratchet 1: sync breakeven lock (run_xau_broker_live.py:732)
        if controller.be_locked and not pos.get("be_ratchet_hit", False):
            pos["be_ratchet_hit"] = True
            pos["sl_price"] = controller.sl_price
    else:
        exit_dec = None

    # Ratchet 2: TP1 profit lock at +1.8 ATR (run_xau_broker_live.py:740)
    if not pos.get("tp1_ratchet_hit", False) and gain_pts >= 1.8 * atr:
        pos["tp1_ratchet_hit"] = True
        locked_sl = entry_px + (1.0 * atr) if direction == "BUY" else entry_px - (1.0 * atr)
        pos["sl_price"] = locked_sl
        if controller:
            controller.sl_price = locked_sl

    # Structure invalidation (:749) - needs >= 35s in trade and a 1m reversal
    structure_exit = False
    structure_reason = ""
    if time_held_sec >= 35.0:
        if direction == "BUY" and last_close < candles_prev_low and pos_pnl < 0:
            structure_exit = True
            structure_reason = f"Structure Invalidation: 1m bar broke below prior swing low ({last_close:.2f} < {candles_prev_low:.2f})"
        elif direction == "SELL" and last_close > candles_prev_high and pos_pnl < 0:
            structure_exit = True
            structure_reason = f"Structure Invalidation: 1m bar broke above prior swing high ({last_close:.2f} > {candles_prev_high:.2f})"

    # Software stop loss (:762)
    sl_hit = (direction == "BUY" and tick_price <= pos["sl_price"]) or (
        direction == "SELL" and tick_price >= pos["sl_price"]
    )

    # Macro spike harvest (:766)
    spike_thresh = 6.5 * atr if pos.get("is_hold_long", True) else 2.5 * atr
    hit_macro_spike = gain_pts >= spike_thresh or (
        balance >= 100.0 and pos_pnl >= RAPID_SPIKE_TARGET_USD
    )

    # ---- Production priority order (:769-804) ----
    if friday:
        return {"label": "FRIDAY_FLATTEN", "reason": "Friday Weekend Force-Flatten",
                "exec_px": pos_exec_px, "pnl": pos_pnl, "gain_pts": gain_pts}
    if exit_dec is not None and exit_dec.should_exit:
        return {"label": exit_dec.metric_label, "reason": exit_dec.reason,
                "exec_px": pos_exec_px, "pnl": pos_pnl, "gain_pts": gain_pts}
    if structure_exit:
        return {"label": "STRUCTURE_INVALIDATION", "reason": structure_reason,
                "exec_px": pos_exec_px, "pnl": pos_pnl, "gain_pts": gain_pts}
    if sl_hit:
        is_trailing = pos.get("be_ratchet_hit", False)
        return {
            "label": "TRAILING_SL" if is_trailing else "RISK_STOP",
            "reason": "Trailing SL Cushion Hit" if is_trailing else "Risk Stop Hit",
            "exec_px": pos_exec_px, "pnl": pos_pnl, "gain_pts": gain_pts,
        }
    if hit_macro_spike:
        return {"label": "MACRO_SPIKE_HARVEST",
                "reason": f"Macro Spike Harvest (+{gain_pts:.2f} pts)",
                "exec_px": pos_exec_px, "pnl": pos_pnl, "gain_pts": gain_pts}
    return None


def run_live_loop_backtest(
    data_dir: str = "data/candles",
    start_date_str: str = "2025-01-21",
    starting_balance: float = 30.0,
    enable_daily_withdrawal: bool = True,
    buffer_equity: float = 100.0,
    mode: str = "serial",
    exit_scope: str = EXIT_SCOPE_FLATTEN,
    max_days: Optional[int] = None,
) -> Tuple[Dict[str, Any], List[Any], List[Dict[str, Any]]]:
    import concurrent.futures as cf

    candle_files = sorted(Path(data_dir).glob("gold_m1_*.csv"))
    candle_files = [f for f in candle_files if f.stem.replace("gold_m1_", "") >= start_date_str]
    if max_days:
        candle_files = candle_files[:max_days]

    t0 = time.time()
    print(f"⏳ Loading {len(candle_files)} trading days (live-loop fidelity mode={mode}, exit_scope={exit_scope})...")
    with cf.ProcessPoolExecutor() as ex:
        days_data = list(ex.map(precompute_day_causal, candle_files))
    days_data = [d for d in days_data if d is not None]
    print(f"✅ Loaded {len(days_data)} days in {time.time() - t0:.2f}s")

    regime_engine = anchor.get_regime_prior_engine()
    politician_brain = anchor.get_politician_brain()

    balance = starting_balance
    peak_equity = balance
    total_withdrawn_cash = 0.0
    last_sig_time = 0.0

    journal: List[Any] = []
    daily_summaries: List[Dict[str, Any]] = []
    exit_counts: Dict[str, int] = {}
    ticket_counter = 1
    risk = RiskState()

    # Position book: one slot per parallel capacity slot
    active_positions: List[Dict[str, Any]] = []

    for day_idx, d in enumerate(days_data, 1):
        day_start_bal = balance
        trades_before = len(journal)
        date = d["date"]
        risk.on_new_day(date, balance)

        n = d["n"]
        o, h, l, c, t = d["o"], d["h"], d["l"], d["c"], d["t"]
        hour, atr_arr = d["hour"], d["atr"]
        ema20, ema50 = d["ema20"], d["ema50"]
        bo_res, bo_sup, bo_up, bo_down = d["bo_res"], d["bo_sup"], d["bo_up"], d["bo_down"]
        avail_t = d["avail_t"]

        active_bo_type = None
        active_bo_lvl = 0.0
        active_bo_time = 0.0

        def close_position(pos, ex, now, day_ctx):
            nonlocal balance, peak_equity, ticket_counter
            # Realism: slippage charged on the closing fill only
            slip = SLIPPAGE if pos["direction"] == "BUY" else -SLIPPAGE
            fill_px = ex["exec_px"] - slip
            gain_pts = (fill_px - pos["entry_price"]) if pos["direction"] == "BUY" else (
                pos["entry_price"] - fill_px
            )
            pnl = round(gain_pts * 100.0 * pos["volume"], 2)
            balance = round(balance + pnl, 2)
            peak_equity = max(peak_equity, balance)
            dd_pct = (peak_equity - balance) / peak_equity * 100.0 if peak_equity > 0 else 0.0

            exit_counts[ex["label"]] = exit_counts.get(ex["label"], 0) + 1
            journal.append(
                MicroJournalEntry(
                    ticket_id=ticket_counter,
                    date=day_ctx["date"],
                    time_utc=datetime.fromtimestamp(pos["entry_time"], tz=timezone.utc).strftime("%H:%M:%S"),
                    hour=pos["hour"],
                    strategy=pos["strategy"],
                    direction=pos["direction"],
                    entry_price=round(pos["entry_price"], 2),
                    exit_price=round(fill_px, 2),
                    initial_volume=pos["volume"],
                    final_volume=pos["volume"],
                    points_captured=round(gain_pts, 2),
                    realized_pnl=pnl,
                    is_win=pnl > 0,
                    exit_reason=ex["label"],
                    duration_min=max(1, int((now - pos["open_time"]) // 60)),
                    atr_entry=round(pos["atr_1m"], 2),
                    wick_ratio=round(pos.get("wick_ratio", 0.5), 2),
                    pyramid_added=False,
                    scaled_out=False,
                    running_balance=round(balance, 2),
                    peak_balance=round(peak_equity, 2),
                    drawdown_pct=round(dd_pct, 2),
                    laya_grade=pos.get("laya_grade", "macro_sovereign_titan"),
                    compounding_boost=round(pos.get("compounding_boost", 1.0), 2),
                    tp_expansion=round(pos.get("tp_expansion", 1.0), 2),
                    politician_regime=pos.get("politician_regime", "TRADE_WAR_TARIFFS"),
                )
            )
            ticket_counter += 1
            risk.on_close(pnl, balance, now)

        for i in range(2, n):
            curr_px = c[i]
            hi_px, lo_px, op_px = h[i], l[i], o[i]
            curr_t = t[i]
            hr = hour[i]
            curr_atr = atr_arr[i]

            # 5m breakout detection (identical to the anchor)
            if bo_up[i]:
                active_bo_type, active_bo_lvl, active_bo_time = "UP", bo_res[i], avail_t[i]
            elif bo_down[i]:
                active_bo_type, active_bo_lvl, active_bo_time = "DOWN", bo_sup[i], avail_t[i]

            friday = is_friday_flatten(curr_t)

            # ------------------------------------------------------------
            # 1. MANAGE OPEN POSITIONS (production exit chain, per tick)
            # ------------------------------------------------------------
            if active_positions:
                # Production evaluates positions in order and BREAKS on the first
                # trigger (run_xau_broker_live.py:769-804), then flattens everything.
                trigger: Optional[Any] = None
                for pos in active_positions:
                    pos["bars_held"] = i - pos["entry_bar_idx"]
                    # Structure level, expressed on the EXECUTABLE side so the
                    # crossing test below stays in one price space:
                    #   BUY  fires when mid_close < prev_low  <=> exec < prev_low - half
                    #   SELL fires when mid_close > prev_high <=> exec > prev_high + half
                    half_sp = SPREAD / 2.0
                    if pos["direction"] == "BUY":
                        pos["_struct_level"] = l[i - 1] - half_sp
                    else:
                        pos["_struct_level"] = h[i - 1] + half_sp
                    prev_low, prev_high = l[i - 1], h[i - 1]
                    hit = None
                    for tick_px, tick_ts in ticks_with_stop(
                        raw_path(op_px, hi_px, lo_px, curr_px, curr_t), pos
                    ):
                        hit = evaluate_position_tick(
                            pos=pos,
                            tick_price=tick_px,
                            tick_time=tick_ts,
                            candles_prev_close=c[i - 1],
                            candles_prev_low=prev_low,
                            candles_prev_high=prev_high,
                            last_close=tick_px,
                            last_low=l[i],
                            last_high=h[i],
                            balance=balance,
                            friday=friday,
                        )
                        if hit is not None:
                            hit["_tick_time"] = tick_ts
                            break
                    if hit is not None:
                        trigger = (pos, hit)
                        break

                if trigger is not None:
                    trig_pos, trig_ex = trigger
                    close_at = trig_ex["_tick_time"]
                    if trig_pos["direction"] == "BUY":
                        mid = trig_ex["exec_px"] + SPREAD / 2.0
                    else:
                        mid = trig_ex["exec_px"] - SPREAD / 2.0

                    if exit_scope == EXIT_SCOPE_FLATTEN:
                        # Production: ONE trigger flattens EVERYTHING at market
                        # (run_xau_broker_live.py:807). Every ticket is filled at
                        # its own bid/ask at that same instant.
                        closing_pairs: List[Any] = []
                        for p in active_positions:
                            if p is trig_pos:
                                closing_pairs.append((p, trig_ex))
                                continue
                            px = mid - SPREAD / 2.0 if p["direction"] == "BUY" else mid + SPREAD / 2.0
                            gain = (px - p["entry_price"]) if p["direction"] == "BUY" else (
                                p["entry_price"] - px
                            )
                            closing_pairs.append(
                                (
                                    p,
                                    {
                                        "label": "FLATTEN_ALL_COLLATERAL",
                                        "reason": "Closed by parallel flatten (production flatten_all_positions)",
                                        "exec_px": px,
                                        "pnl": round(gain * 100.0 * p["volume"], 2),
                                        "gain_pts": gain,
                                        "_tick_time": close_at,
                                    },
                                )
                            )
                        active_positions.clear()
                        for p, ex in closing_pairs:
                            close_position(p, ex, close_at, {"date": date})
                    else:
                        # Candidate edit B1: close only the triggering ticket
                        active_positions = [p for p in active_positions if p is not trig_pos]
                        close_position(trig_pos, trig_ex, close_at, {"date": date})

                if balance <= 0.0:
                    break

            # ------------------------------------------------------------
            # 2. SCAN FOR ENTRY
            # ------------------------------------------------------------
            scan_ok = (curr_t - last_sig_time) >= 180.0 and balance >= 10.0 and hr != 23
            if scan_ok:
                if mode == "serial":
                    can_enter = len(active_positions) == 0
                else:
                    cap = max_parallel_for(balance)
                    if len(active_positions) < cap:
                        can_enter = (
                            len(active_positions) == 0
                            or all(p.get("be_ratchet_hit", False) for p in active_positions)
                        )
                    else:
                        can_enter = False
                can_enter = can_enter and risk.entries_allowed(curr_t)

                if can_enter:
                    sig_dir = None
                    sig_strat = None
                    wick_r = 0.50

                    if active_bo_type and 0 < (curr_t - active_bo_time) <= 1200.0:
                        lvl = active_bo_lvl
                        rng = max(0.20, hi_px - lo_px)
                        if active_bo_type == "UP":
                            trend_ok = curr_px > ema20[i] > ema50[i]
                            retest_ok = lo_px <= lvl + 1.20 and hi_px >= lvl - 0.20
                            wick = min(op_px, curr_px) - lo_px
                            w = wick / rng
                            if trend_ok and retest_ok and w >= 0.45 and curr_px >= op_px:
                                sig_dir, sig_strat, wick_r = "BUY", "BREAKOUT_RETEST", w
                                active_bo_type = None
                        elif active_bo_type == "DOWN":
                            trend_ok = curr_px < ema20[i] < ema50[i]
                            retest_ok = hi_px >= lvl - 1.20 and lo_px <= lvl + 0.20
                            wick = hi_px - max(op_px, curr_px)
                            w = wick / rng
                            if trend_ok and retest_ok and w >= 0.45 and curr_px <= op_px:
                                sig_dir, sig_strat, wick_r = "SELL", "BREAKOUT_RETEST", w
                                active_bo_type = None

                    if sig_dir:
                        regime_eval = regime_engine.evaluate_regime_fit(
                            strategy=sig_strat, hour_utc=hr, wick_ratio=wick_r, trend_aligned=True
                        )
                        if not regime_eval.is_allowed:
                            sig_dir = None
                        else:
                            pol_eval = politician_brain.evaluate_entry_macro_fit(
                                direction=sig_dir, strategy_type=sig_strat
                            )
                            if not pol_eval.is_permitted:
                                sig_dir = None
                            else:
                                base_lot = compute_micro_lot_size(balance)
                                compounding_mult = max(
                                    1.0, regime_eval.compounding_multiplier, pol_eval.alpha_boost_multiplier
                                )
                                grade = "high_probability"
                                if (
                                    pol_eval.alpha_boost_multiplier >= 1.50
                                    and regime_eval.regime_grade == "A_plus_prime"
                                ):
                                    compounding_mult = 1.65
                                    grade = "macro_sovereign_titan"

                                final_lot = round(base_lot * compounding_mult, 2)
                                final_lot = 0.01 if balance < 60.0 else max(0.01, min(5.0, final_lot))

                                # Production sets sig.entry_price = curr_px (apex_trinity.py:227
                                # etc.) i.e. the CHART price, not the ask. PnL is then measured
                                # against the executable side, exactly as the live loop does.
                                actual_entry = curr_px
                                sl = round(lo_px - (0.8 * curr_atr), 2) if sig_dir == "BUY" else round(hi_px + (0.8 * curr_atr), 2)
                                tp1 = round(curr_px + (2.5 * curr_atr), 2) if sig_dir == "BUY" else round(curr_px - (2.5 * curr_atr), 2)

                                pos = make_position(
                                    direction=sig_dir,
                                    strategy=sig_strat,
                                    entry_price=actual_entry,
                                    entry_time=curr_t,
                                    volume=final_lot,
                                    sl_price=sl,
                                    tp1_price=tp1,
                                    atr_1m=curr_atr,
                                    balance=balance,
                                    laya_grade=grade,
                                    compounding_boost=compounding_mult,
                                    tp_expansion=pol_eval.tp_expansion_multiplier,
                                    politician_regime=(
                                        pol_eval.regime.value
                                        if hasattr(pol_eval.regime, "value")
                                        else str(pol_eval.regime)
                                    ),
                                    hour=hr,
                                )
                                pos["entry_bar_idx"] = i
                                pos["wick_ratio"] = wick_r
                                active_positions.append(pos)
                                last_sig_time = curr_t

        # New day bookkeeping (open positions carry overnight, exactly as the anchor does)
        day_trades = journal[trades_before:]
        day_wins = [x for x in day_trades if x.is_win]
        day_pnl = balance - day_start_bal

        withdrawn_today = 0.0
        if enable_daily_withdrawal and balance > buffer_equity and day_pnl > 0:
            rate = 0.30 if balance < 1000.0 else 0.50 if balance < 5000.0 else 0.70
            withdrawn_today = round(day_pnl * rate, 2)
            total_withdrawn_cash += withdrawn_today
            balance = round(balance - withdrawn_today, 2)

        wr = (len(day_wins) / len(day_trades) * 100.0) if day_trades else 0.0
        daily_summaries.append(
            {
                "day_num": day_idx,
                "date": date,
                "start_balance": round(day_start_bal, 2),
                "end_balance": round(balance, 2),
                "day_pnl": round(day_pnl, 2),
                "withdrawn_today": withdrawn_today,
                "cumulative_withdrawn": round(total_withdrawn_cash, 2),
                "trades_count": len(day_trades),
                "wins": len(day_wins),
                "win_rate_pct": round(wr, 1),
            }
        )

    summary = _build_summary(
        journal=journal,
        daily_summaries=daily_summaries,
        balance=balance,
        total_withdrawn_cash=total_withdrawn_cash,
        peak_equity=peak_equity,
        exit_counts=exit_counts,
        starting_balance=starting_balance,
        days=days_data,
        mode=mode,
        exit_scope=exit_scope,
    )
    return summary, journal, daily_summaries


def _build_summary(
    *,
    journal,
    daily_summaries,
    balance,
    total_withdrawn_cash,
    peak_equity,
    exit_counts,
    starting_balance,
    days,
    mode,
    exit_scope,
) -> Dict[str, Any]:
    total_trades = len(journal)
    wins = [x for x in journal if x.is_win]
    losses = [x for x in journal if not x.is_win]
    overall_wr = (len(wins) / total_trades * 100.0) if total_trades else 0.0
    gross_profit = sum(x.realized_pnl for x in wins)
    gross_loss = abs(sum(x.realized_pnl for x in losses))
    pf = (gross_profit / gross_loss) if gross_loss > 0 else 99.9
    expectancy = (sum(x.realized_pnl for x in journal) / total_trades) if total_trades else 0.0
    max_dd = max((x.drawdown_pct for x in journal), default=0.0)
    profitable_days = sum(1 for x in daily_summaries if x["day_pnl"] > 0)
    losing_days = sum(1 for x in daily_summaries if x["day_pnl"] < 0)

    return {
        "model": "LIVE_LOOP_FIDELITY",
        "mode": mode,
        "exit_scope": exit_scope,
        "date_range": f"{days[0]['date']} to {days[-1]['date']}" if days else "",
        "total_days_audited": len(days),
        "total_trades_logged": total_trades,
        "overall_win_rate_pct": round(overall_wr, 1),
        "profit_factor": round(pf, 2),
        "expectancy_usd": round(expectancy, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "starting_capital": starting_balance,
        "total_cash_withdrawn_usd": round(total_withdrawn_cash, 2),
        "final_retained_equity_usd": round(balance, 2),
        "total_realized_wealth_usd": round(total_withdrawn_cash + balance, 2),
        "wealth_multiplier": round((total_withdrawn_cash + balance) / starting_balance, 2),
        "profitable_days": profitable_days,
        "losing_days": losing_days,
        "daily_win_rate_pct": round(
            (profitable_days / len(daily_summaries) * 100.0) if daily_summaries else 0.0, 1
        ),
        "exit_breakdown": dict(sorted(exit_counts.items(), key=lambda kv: -kv[1])),
    }


def _write_outputs(summary, journal, daily_summaries, out_prefix: Path) -> None:
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    sum_path = out_prefix.with_name(out_prefix.name + "_summary.json")
    sum_path.write_text(json.dumps(summary, indent=2))

    j_path = out_prefix.with_name(out_prefix.name + "_journal.csv")
    with open(j_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[x.name for x in MicroJournalEntry.__dataclass_fields__.values()])
        w.writeheader()
        for row in journal:
            w.writerow(asdict(row))

    d_path = out_prefix.with_name(out_prefix.name + "_daily.csv")
    with open(d_path, "w", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "day_num", "date", "start_balance", "end_balance", "day_pnl",
                "withdrawn_today", "cumulative_withdrawn", "trades_count", "wins", "win_rate_pct",
            ],
        )
        w.writeheader()
        w.writerows(daily_summaries)

    print(json.dumps(summary, indent=2))
    print(f"\n📁 {sum_path}\n📁 {j_path}\n📁 {d_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Live-loop fidelity backtest (Trump regime)")
    ap.add_argument("--mode", choices=["serial", "live"], default="serial")
    ap.add_argument("--exit-scope", choices=[EXIT_SCOPE_FLATTEN, EXIT_SCOPE_SINGLE], default=EXIT_SCOPE_FLATTEN)
    ap.add_argument("--tag", default="live_loop", help="output file prefix tag")
    ap.add_argument("--starting-balance", type=float, default=30.0)
    ap.add_argument("--max-days", type=int, default=None)
    ap.add_argument("--data-dir", default="data/candles")
    args = ap.parse_args()

    summary, journal, daily = run_live_loop_backtest(
        data_dir=args.data_dir,
        starting_balance=args.starting_balance,
        mode=args.mode,
        exit_scope=args.exit_scope,
        max_days=args.max_days,
    )
    _write_outputs(summary, journal, daily, ROOT_DIR / "data" / args.tag)


if __name__ == "__main__":
    main()
