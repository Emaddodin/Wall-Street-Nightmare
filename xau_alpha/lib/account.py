"""
xau_alpha/lib/account.py
Micro-account simulation on top of a trade table from sim.simulate: discrete lots, margin, compounding, ruin,
and day-block bootstrap Monte Carlo for flip odds.

pnl / risk / mae in the trade table are price units per 1 oz (0.01 lot = 1 oz: $1 per $1 move).
"""
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

OZ_PER_LOT = 100.0


@dataclass
class Sizing:
    risk_frac: float = 0.10        # target risk per trade as a fraction of equity
    max_risk_frac: float = 0.35    # allow rounding up to the min lot only if its risk stays below this
    min_lot: float = 0.01
    lot_step: float = 0.01
    max_lot: float = 50.0
    leverage: float = 500.0        # LiteFinance XAUUSD margin 0.2% (1:500); 1:200 within +-30 min of major news
    margin_use_max: float = 0.95   # broker needs free margin >= required margin; keep a 5% buffer
    stop_out_level: float = 0.2    # margin level (equity/margin) at which the broker liquidates
    ruin_equity: float = 0.0       # below this, count as ruined (set to min-lot margin + a stop)
    daily_loss_stop: float = 1.0   # stop trading for the day after losing this fraction of the day's start equity
    withdraw_above: float = math.inf   # sweep equity above this level into `banked`


def size_lots(eq: float, risk_pts: float, price: float, s: Sizing) -> float:
    if risk_pts <= 0:
        return 0.0
    raw = eq * s.risk_frac / (risk_pts * OZ_PER_LOT)
    lots = math.floor(raw / s.lot_step + 1e-9) * s.lot_step
    if lots < s.min_lot:
        if s.min_lot * risk_pts * OZ_PER_LOT <= eq * s.max_risk_frac:
            lots = s.min_lot
        else:
            return 0.0
    # margin cap
    m_per_lot = OZ_PER_LOT * price / s.leverage
    max_by_margin = math.floor(eq * s.margin_use_max / m_per_lot / s.lot_step + 1e-9) * s.lot_step
    lots = min(lots, max_by_margin, s.max_lot)
    return lots if lots >= s.min_lot - 1e-12 else 0.0


def run(tr: pd.DataFrame, eq0: float, s: Sizing, target: float = math.inf) -> dict:
    """Compound through trades in order. Returns final equity, path, per-day P&L, ruin/target flags."""
    eq = eq0
    banked = 0.0
    path = []
    day_start = {}
    halted_day = None
    reached = None
    ruined = False
    skipped = 0
    for r in tr.itertuples(index=False):
        if r.day not in day_start:
            day_start[r.day] = eq
        if halted_day == r.day:
            continue
        lots = size_lots(eq, r.risk, r.entry, s)
        if lots <= 0:
            skipped += 1
            if eq < s.min_lot * OZ_PER_LOT * r.entry / s.leverage + 0.5:
                ruined = True
                break
            continue
        margin = lots * OZ_PER_LOT * r.entry / s.leverage
        worst = r.mae * OZ_PER_LOT * lots
        if eq + worst < margin * s.stop_out_level:          # broker stop-out before our stop
            pnl = -(eq - margin * s.stop_out_level)
        else:
            pnl = r.pnl * OZ_PER_LOT * lots
        eq += pnl
        if eq > s.withdraw_above:
            banked += eq - s.withdraw_above
            eq = s.withdraw_above
        path.append((r.t_out, r.day, lots, pnl, eq))
        if eq <= max(s.ruin_equity, 0.0):
            ruined = True
            break
        if reached is None and eq + banked >= target:
            reached = r.day
        if eq <= day_start[r.day] * (1 - s.daily_loss_stop):
            halted_day = r.day
    p = pd.DataFrame(path, columns=["t", "day", "lots", "pnl", "eq"])
    daily = p.groupby("day").agg(pnl=("pnl", "sum"), eq=("eq", "last"), n=("pnl", "size")) if len(p) else p
    return {"final": eq, "banked": banked, "ruined": ruined, "reached": reached, "skipped": skipped,
            "path": p, "daily": daily}


def flip_odds(tr: pd.DataFrame, eq0: float, target: float, s: Sizing, days: int = 45, sims: int = 2000,
              seed: int = 7, cal_days: int | None = None) -> dict:
    """
    Day-block bootstrap: draw `days` trading days with replacement from the trade table's days (keeping each
    day's trades together and in order), compound, and report P(hit target), P(ruin), median days to target.
    """
    rng = np.random.default_rng(seed)
    by_day = [g for _, g in tr.groupby("day", sort=True)]
    all_days = tr["day"].unique()
    n_days_total = max(cal_days or len(all_days), len(all_days), 1)   # calendar trading days incl. no-trade days
    # include no-trade days: sample calendar trading days, many of which have no trades
    hit = ruin = 0
    t_hit = []
    finals = []
    for _ in range(sims):
        eq = eq0
        done = False
        for dnum in range(days):
            g = by_day[rng.integers(len(by_day))] if rng.random() < len(by_day) / n_days_total else None
            if g is None:
                continue
            start = eq
            for r in g.itertuples(index=False):
                lots = size_lots(eq, r.risk, r.entry, s)
                if lots <= 0:
                    if eq < s.min_lot * OZ_PER_LOT * r.entry / s.leverage + 0.5:
                        ruin += 1
                        done = True
                    break
                eq += r.pnl * OZ_PER_LOT * lots
                if eq >= target:
                    hit += 1
                    t_hit.append(dnum + 1)
                    done = True
                    break
                if eq <= start * (1 - s.daily_loss_stop):
                    break
            if done:
                break
        finals.append(eq)
    return {"p_target": hit / sims, "p_ruin": ruin / sims,
            "median_days": float(np.median(t_hit)) if t_hit else None,
            "median_final": float(np.median(finals))}
