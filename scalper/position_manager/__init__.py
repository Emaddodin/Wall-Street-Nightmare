"""Position manager: lots, take-profit models, trailing, structure exits.

Take-profit models (spec section 11):

  A -- one lot, fixed 2R target.
  B -- 50% at 1R, 25% at 2R, 25% runner that trails behind confirmed 1m
       swings after TP1 (stop to breakeven first).
  C -- one lot, exits on the opposite 1m structure break; no profit target.

Trailing (spec section 12): after TP1 the stop moves to breakeven; the
runner trails behind confirmed 1m swing lows (longs) / highs (shorts),
never behind every candle.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from execution import exit_price, pnl_usd
from structure import LONG, SHORT

TP1, TP2, RUNNER, FULL, STRUCT = "tp1", "tp2", "runner", "full", "struct"
MIN_MS = 60_000


@dataclass
class Lot:
    qty: float
    kind: str                  # tp1 | tp2 | runner | full | struct
    sl: float
    tp: float | None           # None = no target (runner/struct)
    entry: float
    entry_fee: float = 0.0
    exit_px: float | None = None
    exit_reason: str = ""
    exit_ms: int = 0
    pnl: float = 0.0
    tp_r: float = 0.0
    risk_dist: float = 0.0        # initial |entry - sl| (R unit, frozen)
    mae_r: float = 0.0            # worst adverse excursion in R
    mfe_r: float = 0.0            # best favorable excursion in R

    @property
    def open(self) -> bool:
        return self.exit_px is None


@dataclass
class Position:
    symbol: str
    direction: int
    lots: list[Lot] = field(default_factory=list)
    opened_ms: int = 0
    entry_ref: float = 0.0
    be_active: bool = False      # stop moved to breakeven (after TP1)
    trail_armed: bool = False    # runner may trail (after TP1)
    struct_exit_pending: bool = False   # opposite BOS seen; close at next open
    pos_id: int = 0
    realized_pnl: float = 0.0           # sum of closed lots (for position-level streaks)
    meta: dict = field(default_factory=dict)

    @property
    def open(self) -> bool:
        return any(l.open for l in self.lots)

    def remaining_qty(self) -> float:
        return sum(l.qty for l in self.lots if l.open)

    def unrealized(self, px: float) -> float:
        return sum((px - l.entry) * l.qty * self.direction - l.entry_fee
                   for l in self.lots if l.open)


def build_position(sym: str, direction: int, fill: float, qty: float,
                   sl: float, cfg, signal_meta: dict, fee_paid: float,
                   at_ms: int, atr15: float,
                   tp_first: float | None = None) -> Position:
    """Split the position into lots according to tp.model."""
    pos = Position(symbol=sym, direction=direction, opened_ms=at_ms,
                   entry_ref=fill, meta=signal_meta)
    risk = abs(fill - sl)
    if risk <= 0:
        return pos
    model = cfg.tp["model"]
    if model == "A":
        r = cfg.tp["fixed_r"]
        pos.lots.append(Lot(qty=qty, kind=FULL, sl=sl,
                            tp=fill + direction * r * risk,
                            entry=fill, entry_fee=fee_paid, tp_r=r))
    elif model == "B":
        p = cfg.tp["partial"]
        lots = [(TP1, p["tp1_frac"], p["tp1_r"], True),
                (TP2, p["tp2_frac"], p["tp2_r"], False),
                (RUNNER, p["runner_frac"], None, False)]
        for kind, frac, r, _ in lots:
            tp = fill + direction * r * risk if r else None
            pos.lots.append(Lot(qty=qty * frac, kind=kind, sl=sl, tp=tp,
                                entry=fill, tp_r=r or 0.0))
    elif model == "D":
        # VP-scalper take-profit: 50% at the nearest opposite-side 15m
        # swing (when one exists beyond entry), 50% runner (trail + BOS).
        # NO first target (e.g. breakout entries): the whole position is a
        # trailing runner that exits on the opposite BOS -- never a lot
        # with no exit path.
        p = cfg.tp["structure"]
        if tp_first is None:
            pos.lots.append(Lot(qty=qty, kind=RUNNER, sl=sl, tp=None,
                                entry=fill, tp_r=0.0))
            pos.trail_armed = True       # trail from entry, BOS exit ready
        else:
            pos.lots.append(Lot(qty=qty * p["first_frac"], kind="first", sl=sl,
                                tp=tp_first, entry=fill, tp_r=0.0))
            pos.lots.append(Lot(qty=qty * p["runner_frac"], kind=RUNNER, sl=sl,
                                tp=None, entry=fill, tp_r=0.0))
    elif model == "C":
        pos.lots.append(Lot(qty=qty, kind=STRUCT, sl=sl, tp=None, entry=fill))
    else:
        raise ValueError(f"tp.model {model!r}")
    for _l in pos.lots:
        _l.risk_dist = risk
    return pos


def step_position(pos: Position, cfg, bar_high: float, bar_low: float,
                  bar_close_ms: int, last_swing_low: float | None,
                  last_swing_high: float | None, atr1m: float,
                  opp_bos: bool, fee_bps: float, slippage_bps: float,
                  tf_minutes: int = 1) -> list[dict]:
    """Apply one closed 1m bar to the position.  Returns closed-lot records.

    Order inside a bar (deterministic):
      1. intrabar stops and targets (conservative: SL wins when both touch)
      2. structure exits on an opposite BOS (exit at next bar open -- the
         caller executes that fill at the following bar's open)
      3. breakeven + trailing updates for the bars that follow
    """
    cfg_t = cfg.trailing
    closed: list[dict] = []

    # 0 -- MAE/MFE (R&D): excursion in units of the ORIGINAL stop distance
    for lot in pos.lots:
        if not lot.open or lot.risk_dist <= 0:
            continue
        if pos.direction == LONG:
            mfe = (bar_high - lot.entry) / lot.risk_dist
            mae = (bar_low - lot.entry) / lot.risk_dist
        else:
            mfe = (lot.entry - bar_low) / lot.risk_dist
            mae = (lot.entry - bar_high) / lot.risk_dist
        lot.mfe_r = max(lot.mfe_r, mfe)
        lot.mae_r = min(lot.mae_r, mae)

    # 0.5 -- breakeven guard (R&D 2026-09-11): the MAE/MFE audit showed
    # stopped losers were +0.95R in profit first; moving the stop to entry
    # once a trade shows be_at_r profit converts those into ~0R exits
    be_at = float(cfg_t.get("breakeven_at_r", 0.0))
    if be_at > 0:
        for lot in pos.lots:
            if not lot.open or lot.risk_dist <= 0:
                continue
            if pos.direction == LONG and bar_high - lot.entry >= be_at * lot.risk_dist:
                lot.sl = max(lot.sl, lot.entry)
            elif pos.direction == SHORT and lot.entry - bar_low >= be_at * lot.risk_dist:
                lot.sl = min(lot.sl, lot.entry)

    # 1 -- stops / targets
    for lot in list(pos.lots):
        if not lot.open:
            continue
        hit_sl = bar_low <= lot.sl if pos.direction == LONG else bar_high >= lot.sl
        hit_tp = (lot.tp is not None and
                  (bar_high >= lot.tp if pos.direction == LONG
                   else bar_low <= lot.tp))
        if hit_sl and hit_tp and cfg.execution["intrabar_sl_first"]:
            hit_tp = False
        if hit_sl:
            px = exit_price(lot.sl, pos.direction, slippage_bps,
                            cfg.execution["exit_slippage_adverse"])
            _close_lot(lot, pos, px, "STOP", bar_close_ms, fee_bps, closed)
        elif hit_tp:
            px = exit_price(lot.tp, pos.direction, slippage_bps,
                            cfg.execution["exit_slippage_adverse"])
            _close_lot(lot, pos, px, f"TP-{lot.kind}", bar_close_ms, fee_bps, closed)
            if lot.kind in (TP1, "first") and cfg_t["breakeven_after_tp1"]:
                pos.be_active = True
                for other in pos.lots:
                    if other.open:
                        other.sl = other.entry
                pos.trail_armed = cfg_t["only_after_tp1"] or True

    # 2 -- structure exit: model C lots AND, when configured, the model-B
    # runner ("bos") ride until an opposite BOS; detected at this bar's
    # close, FILLED at the next bar's open (same policy as entries)
    runner_src = ("structure" if cfg.tp["model"] == "D" else "partial")
    runner_bos = (cfg.tp.get(runner_src) or {}).get("runner_exit", "trail") == "bos"
    if opp_bos and any(l.open and (l.kind == STRUCT
                                   or (l.kind == RUNNER and runner_bos))
                       for l in pos.lots):
        pos.struct_exit_pending = True

    # 3 -- trailing for the runner (confirmed swing based, not every candle)
    if pos.trail_armed and cfg_t["trail_tf"] == "1m":
        for lot in pos.lots:
            if not lot.open or lot.kind != RUNNER:
                continue
            off = cfg_t["trail_offset_atr_mult"] * atr1m
            if pos.direction == LONG and last_swing_low is not None:
                trail = last_swing_low - off
                if not np.isnan(trail):
                    lot.sl = max(lot.sl, trail)
            elif pos.direction == SHORT and last_swing_high is not None:
                trail = last_swing_high + off
                if not np.isnan(trail):
                    lot.sl = min(lot.sl, trail)

    # 4 -- runner time-stop (R&D 2026-09-11): the pool showed runner legs
    # lose -3.46R on average riding into reversals; capping the ride at N
    # bars closes the loss tail (exit at this bar's close, adverse slip).
    runner_src2 = ("structure" if cfg.tp["model"] == "D" else "partial")
    timeout = (cfg.tp.get(runner_src2) or {}).get("runner_timeout_bars", 0)
    if timeout and timeout > 0:
        held = (bar_close_ms - pos.opened_ms) // MIN_MS
        for lot in list(pos.lots):
            if not lot.open or lot.kind != RUNNER:
                continue
            if held >= timeout:
                px = exit_price(bar_high if pos.direction == LONG else bar_low,
                                pos.direction, slippage_bps, True)
                _close_lot(lot, pos, px, "RUNNER-TIME", bar_close_ms,
                           fee_bps, closed)
    return closed


def execute_struct_exit(pos: Position, px: float, ms: int, fee_bps: float,
                        slippage_bps: float) -> list[dict]:
    """Close STRUCT lots (and BOS-exit runner lots) at the next bar's open
    after an opposite BOS."""
    closed: list[dict] = []
    if not pos.struct_exit_pending:
        return closed
    fill = exit_price(px, pos.direction, slippage_bps,
                      True)  # adverse slippage like every other fill
    for lot in list(pos.lots):
        if lot.open and lot.kind in (STRUCT, RUNNER):
            _close_lot(lot, pos, fill, "STRUCT-BOS", ms, fee_bps, closed)
    pos.struct_exit_pending = False
    return closed


def _close_lot(lot: Lot, pos: Position, px: float, reason: str,
               ms: int, fee_bps: float, out: list[dict]) -> None:
    exit_fee = lot.qty * px * fee_bps / 1e4
    lot.exit_px = px
    lot.exit_reason = reason
    lot.exit_ms = ms
    lot.pnl = pnl_usd(pos.direction, lot.qty, lot.entry, px,
                      lot.entry_fee, exit_fee)
    out.append({"lot": lot.kind, "qty": lot.qty, "entry": lot.entry,
                "exit": px, "reason": reason, "exit_ms": ms,
                "pnl": lot.pnl, "exit_fee": exit_fee,
                "sl": lot.sl, "tp": lot.tp,
                "mae_r": lot.mae_r, "mfe_r": lot.mfe_r})
