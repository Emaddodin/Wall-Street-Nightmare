"""
tjr/tjr_trade_journal.py
========================
High-Throughput Trade Journaling & Microstructure Analytics Engine.
Records and tracks millions of simulated and live TJR executions:
- Granular per-trade logging with full causal market state snapshot
- MAE (Maximum Adverse Excursion) and MFE (Maximum Favorable Excursion)
- Stream serialization to high-performance CSV and Parquet/JSON
- Feature extraction pipeline for training the Laya Decision Model
- Statistical aggregations: Win Rate, Profit Factor, Expectancy, Sharpe Ratio
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


@dataclass
class JournalEntry:
    ticket_id: int
    symbol: str
    entry_time: Any
    exit_time: Any
    direction: str  # "LONG" or "SHORT"
    entry_price: float
    exit_price: float
    stop_loss: float
    take_profit: float
    position_size: float
    risk_pts: float
    reward_pts: float
    rr_realized: float
    realized_pnl: float
    is_win: bool
    exit_reason: str  # "TP_HIT", "SL_HIT", "STAGNATION_SCRATCH", "WATERMARK_LOCK"
    holding_bars: int
    mae_pts: float  # Maximum Adverse Excursion
    mfe_pts: float  # Maximum Favorable Excursion
    # Market State Microstructure Features
    session_window: str
    htf_bias: str
    sweep_side: str
    sweep_penetration_pips: float
    displacement_ratio: float
    fvg_size_atr: float
    dealing_range_coordinate: float
    planned_rr: float
    atr_14: float
    # Laya Gating Metadata
    laya_authorized: bool = True
    laya_regime: str = "TRENDING_ORDERFLOW"
    laya_grade: int = 2
    laya_probability: float = 0.85

    def to_flat_dict(self) -> Dict[str, Any]:
        return {
            "ticket_id": self.ticket_id,
            "symbol": self.symbol,
            "entry_time": str(self.entry_time),
            "exit_time": str(self.exit_time),
            "direction": self.direction,
            "entry_price": round(self.entry_price, 2),
            "exit_price": round(self.exit_price, 2),
            "stop_loss": round(self.stop_loss, 2),
            "take_profit": round(self.take_profit, 2),
            "position_size": round(self.position_size, 4),
            "risk_pts": round(self.risk_pts, 2),
            "reward_pts": round(self.reward_pts, 2),
            "rr_realized": round(self.rr_realized, 2),
            "realized_pnl": round(self.realized_pnl, 2),
            "is_win": int(self.is_win),
            "exit_reason": self.exit_reason,
            "holding_bars": self.holding_bars,
            "mae_pts": round(self.mae_pts, 2),
            "mfe_pts": round(self.mfe_pts, 2),
            "session_window": self.session_window,
            "htf_bias": self.htf_bias,
            "sweep_side": self.sweep_side,
            "sweep_penetration_pips": round(self.sweep_penetration_pips, 2),
            "displacement_ratio": round(self.displacement_ratio, 2),
            "fvg_size_atr": round(self.fvg_size_atr, 2),
            "dealing_range_coordinate": round(self.dealing_range_coordinate, 3),
            "planned_rr": round(self.planned_rr, 2),
            "atr_14": round(self.atr_14, 2),
            "laya_authorized": int(self.laya_authorized),
            "laya_regime": self.laya_regime,
            "laya_grade": self.laya_grade,
            "laya_probability": round(self.laya_probability, 3),
        }


class TJRTradeJournal:
    """
    Manages in-memory and disk-persisted journal of TJR setups and executions.
    Provides fast vectorization for training and analytical aggregation.
    """

    def __init__(self, export_dir: Optional[Path] = None):
        self.export_dir = export_dir or Path(__file__).resolve().parent / "journal_data"
        self.export_dir.mkdir(parents=True, exist_ok=True)
        self.entries: List[JournalEntry] = []

    def record_entry(self, entry: JournalEntry) -> None:
        self.entries.append(entry)

    def record_batch(self, batch: List[JournalEntry]) -> None:
        self.entries.extend(batch)

    def clear(self) -> None:
        self.entries.clear()

    def count(self) -> int:
        return len(self.entries)

    def to_dataframe(self) -> pd.DataFrame:
        if not self.entries:
            return pd.DataFrame()
        records = [e.to_flat_dict() for e in self.entries]
        return pd.DataFrame.from_records(records)

    def save_csv(self, filename: str = "tjr_trade_journal.csv") -> Path:
        out_path = self.export_dir / filename
        df = self.to_dataframe()
        df.to_csv(out_path, index=False)
        return out_path

    def compute_summary_analytics(self) -> Dict[str, Any]:
        """
        Calculates institutional trade statistics across all journaled trades.
        """
        if not self.entries:
            return {
                "total_trades": 0,
                "win_rate_pct": 0.0,
                "profit_factor": 0.0,
                "expectancy_pts": 0.0,
                "total_pnl": 0.0,
            }

        df = self.to_dataframe()
        total_trades = len(df)
        wins = df[df["is_win"] == 1]
        losses = df[df["is_win"] == 0]

        win_count = len(wins)
        loss_count = len(losses)
        win_rate = (win_count / total_trades * 100.0) if total_trades > 0 else 0.0

        gross_profit = wins["realized_pnl"].sum()
        gross_loss = abs(losses["realized_pnl"].sum())
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 99.9

        avg_win = wins["realized_pnl"].mean() if win_count > 0 else 0.0
        avg_loss = abs(losses["realized_pnl"].mean()) if loss_count > 0 else 0.0
        expectancy = (win_rate / 100.0 * avg_win) - ((1.0 - win_rate / 100.0) * avg_loss)

        # Sharpe ratio on trade returns
        pnl_series = df["realized_pnl"]
        sharpe = 0.0
        if len(pnl_series) > 1 and pnl_series.std() > 0:
            sharpe = (pnl_series.mean() / pnl_series.std()) * math.sqrt(252)

        # Drawdown computation
        equity_curve = pnl_series.cumsum()
        peak = np.maximum.accumulate(equity_curve)
        drawdown = peak - equity_curve
        max_dd = float(np.max(drawdown)) if len(drawdown) > 0 else 0.0

        # Sub-breakdowns
        session_wr = {}
        for sess, group in df.groupby("session_window"):
            s_win = (group["is_win"] == 1).mean() * 100.0
            session_wr[sess] = {"count": len(group), "win_rate_pct": round(s_win, 1)}

        displacement_wr = {}
        df["disp_bin"] = pd.cut(df["displacement_ratio"], bins=[0.0, 1.2, 1.5, 2.0, 10.0])
        for bin_name, group in df.groupby("disp_bin", observed=False):
            if len(group) > 0:
                displacement_wr[str(bin_name)] = {
                    "count": len(group),
                    "win_rate_pct": round((group["is_win"] == 1).mean() * 100.0, 1),
                }

        return {
            "total_trades": total_trades,
            "win_count": win_count,
            "loss_count": loss_count,
            "win_rate_pct": round(win_rate, 2),
            "profit_factor": round(profit_factor, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "expectancy_per_trade": round(expectancy, 2),
            "total_pnl": round(float(pnl_series.sum()), 2),
            "sharpe_ratio": round(sharpe, 2),
            "max_drawdown": round(max_dd, 2),
            "session_breakdown": session_wr,
            "displacement_breakdown": displacement_wr,
        }

    def get_training_features_and_labels(self) -> Tuple[np.ndarray, np.ndarray, List[str]]:
        """
        Extracts clean normalized numerical features and binary win/loss labels
        for training the Laya AI Decision Model.
        """
        df = self.to_dataframe()
        if df.empty:
            return np.zeros((0, 6)), np.zeros(0), []

        feature_cols = [
            "sweep_penetration_pips",
            "displacement_ratio",
            "fvg_size_atr",
            "dealing_range_coordinate",
            "atr_14",
            "planned_rr",
        ]

        X = df[feature_cols].copy().values.astype(np.float32)
        # Normalize features
        X[:, 0] = np.clip(X[:, 0] / 30.0, 0.0, 2.0)  # penetration
        X[:, 1] = np.clip(X[:, 1] / 3.0, 0.0, 2.0)   # displacement
        X[:, 2] = np.clip(X[:, 2] / 2.0, 0.0, 2.0)   # fvg size
        X[:, 3] = np.clip(X[:, 3], 0.0, 1.0)         # dealing coord
        X[:, 4] = np.clip(X[:, 4] / 6.0, 0.0, 2.0)   # atr
        X[:, 5] = np.clip(X[:, 5] / 4.0, 0.0, 2.0)   # rr

        y = df["is_win"].values.astype(np.int32)
        return X, y, feature_cols
