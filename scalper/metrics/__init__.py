"""Metrics (spec section 21): the full trade arithmetic + every breakdown.

Win rate alone is explicitly NOT the yardstick.  Everything here is computed
from the recorded trade list and the marked equity curve.
"""
from __future__ import annotations

import math
from collections import defaultdict

import numpy as np
import pandas as pd

DAY_MS = 86_400_000
HOUR_MS = 3_600_000


def _dd(equity: np.ndarray) -> float:
    if len(equity) == 0:
        return 0.0
    peak = np.maximum.accumulate(equity)
    return float(((peak - equity) / np.where(peak == 0, 1, peak)).max())


def _sharpe(daily_returns: np.ndarray, periods: float = 365.0) -> float:
    if len(daily_returns) < 2 or np.std(daily_returns) == 0:
        return 0.0
    return float(np.mean(daily_returns) / np.std(daily_returns) * math.sqrt(periods))


def _sortino(daily_returns: np.ndarray, periods: float = 365.0) -> float:
    neg = daily_returns[daily_returns < 0]
    if len(neg) < 2 or np.std(neg) == 0:
        return 0.0
    return float(np.mean(daily_returns) / np.std(neg) * math.sqrt(periods))


def summarize(trades: list[dict], equity_curve: list[tuple[int, float]],
              start_equity: float, days: float | None = None) -> dict:
    """The headline metric set from closed trades + the marked equity path."""
    trades = [r for r in trades if not r.get("phantom")]   # replay artifacts
    t = pd.DataFrame(trades) if trades else pd.DataFrame()
    eq = np.array([e for _, e in equity_curve], dtype=float)
    eq = np.where(np.isnan(eq) | (eq < 0), 0.0, eq)   # ruin floor at zero
    n = len(trades)
    out: dict = {
        "n_trades": n,
        "n_positions": int(t["pos_id"].nunique()) if n else 0,
        "net_pnl": float(t["pnl"].sum()) if n else 0.0,
        "final_equity": float(eq[-1]) if len(eq) else float(start_equity),
        "max_drawdown_pct": _dd(eq) * 100.0,
        "ruined": bool(len(eq) and eq[-1] <= 0),
    }
    if n:
        wins = t[t["pnl"] > 0]
        losses = t[t["pnl"] <= 0]
        gross_win = float(wins["pnl"].sum()) if len(wins) else 0.0
        gross_loss = float(losses["pnl"].sum()) if len(losses) else 0.0
        out.update({
            "profit_factor": gross_win / abs(gross_loss) if gross_loss != 0 else float("inf"),
            "win_rate_pct": len(wins) / n * 100.0,
            "avg_win": float(wins["pnl"].mean()) if len(wins) else 0.0,
            "avg_loss": float(losses["pnl"].mean()) if len(losses) else 0.0,
            "expectancy": float(t["pnl"].mean()),
            "avg_r": float(t["pnl_r"].mean()),
            "largest_loss": float(t["pnl"].min()),
            "largest_win": float(t["pnl"].max()),
            "longest_losing_streak": _streak(t["pnl"].to_numpy()),
            "recovery_factor": (out["net_pnl"] / (out["max_drawdown_pct"] * start_equity / 100.0))
            if out["max_drawdown_pct"] > 0 else float("inf"),
        })
    else:
        out.update({"profit_factor": 0.0, "win_rate_pct": 0.0, "avg_win": 0.0,
                    "avg_loss": 0.0, "expectancy": 0.0, "avg_r": 0.0,
                    "largest_loss": 0.0, "largest_win": 0.0,
                    "longest_losing_streak": 0, "recovery_factor": 0.0})
    # daily returns from the equity curve
    if len(eq):
        eq_s = pd.Series(eq, index=pd.to_datetime([ms for ms, _ in equity_curve],
                                                  unit="ms", utc=True))
        eq_s2 = eq_s.replace(0.0, np.nan)
        daily = eq_s2.resample("1D").last().dropna().pct_change().dropna().to_numpy()
        daily = daily[~np.isnan(daily)]
        span_days = (equity_curve[-1][0] - equity_curve[0][0]) / DAY_MS if len(equity_curve) > 1 else 0
        out["avg_trades_per_day"] = n / span_days if span_days > 0 else float(n)
        out["sharpe"] = _sharpe(daily)
        out["sortino"] = _sortino(daily)
    else:
        out.update({"avg_trades_per_day": 0.0, "sharpe": 0.0, "sortino": 0.0})
    return out


def _streak(pnls: np.ndarray) -> int:
    best = cur = 0
    for p in pnls:
        if p <= 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def breakdowns(trades: list[dict]) -> dict:
    """PnL by coin / hour / day / regime / setup type / score / volatility
    regime (spec section 21)."""
    trades = [r for r in trades if not r.get("phantom")]   # replay artifacts
    out: dict[str, list[dict]] = {}
    if not trades:
        for k in ("by_coin", "by_hour", "by_day", "by_regime", "by_lot",
                  "by_score", "by_vol_regime"):
            out[k] = []
        return out
    t = pd.DataFrame(trades)
    t["hour"] = (t["ts_ms"] // HOUR_MS) % 24
    t["day"] = t["ts_ms"] // DAY_MS
    t["model_bucket"] = t["entry_model"].fillna("?")
    t["vol_regime"] = pd.cut(t["atr1m"] / t["entry"] * 100,
                             bins=[0, 0.05, 0.15, 0.3, 0.6, 1e9],
                             labels=["<0.05%", "0.05-0.15%", "0.15-0.3%",
                                     "0.3-0.6%", ">0.6%"])

    def agg(key: str, cols: dict) -> list[dict]:
        rows = []
        for name, grp in t.groupby(key, dropna=False, observed=False):
            avg_r = grp["pnl_r"].dropna().mean()
            rows.append({"name": str(name), "n": int(len(grp)),
                         "won": int((grp["pnl"] > 0).sum()),
                         "pnl": float(grp["pnl"].fillna(0.0).sum()),
                         "avg_r": float(avg_r) if pd.notna(avg_r) else 0.0})
        rows.sort(key=lambda r: -r["pnl"])
        return rows

    out["by_coin"] = agg("symbol", {})
    out["by_hour"] = agg("hour", {})
    out["by_day"] = agg("day", {})
    out["by_bias"] = agg("bias", {})
    out["by_lot"] = agg("lot", {})
    out["by_model"] = agg("model_bucket", {})
    out["by_strategy"] = agg("strategy", {})
    out["by_vol_regime"] = agg("vol_regime", {})
    return out


def rejection_summary(rejections: list[dict]) -> dict:
    """Where candidate setups die: count of each failing leg, and the
    distribution of how many legs were passed before the refusal."""
    legs: dict[str, int] = defaultdict(int)
    passed: dict[int, int] = defaultdict(int)
    for r in rejections:
        passed[r.get("passed_legs", 0)] += 1
        for rej in r.get("rejections") or []:
            legs[rej.get("leg", "?")] += 1
    return {"by_leg": dict(sorted(legs.items(), key=lambda kv: -kv[1])),
            "by_passed_legs": dict(sorted(passed.items())),
            "n_rejections": len(rejections)}


def report_text(summary: dict, bd: dict, rej: dict, cfg_meta: dict) -> str:
    """A compact human-readable report."""
    s = summary
    lines = [
        "=" * 64,
        f"run: vp scalper | tp model {cfg_meta.get('tp_model')}",
        f"trades={s['n_trades']} positions={s['n_positions']} "
        f"net={s['net_pnl']:+.2f} final={s['final_equity']:.2f}",
        f"win rate {s['win_rate_pct']:.1f}% | PF {s['profit_factor']:.2f} | "
        f"expectancy {s['expectancy']:+.2f}/trade | avg R {s['avg_r']:+.2f}",
        f"avg win {s['avg_win']:+.2f} | avg loss {s['avg_loss']:+.2f} | "
        f"worst {s['largest_loss']:+.2f} | best {s['largest_win']:+.2f}",
        f"max DD {s['max_drawdown_pct']:.1f}% | sharpe {s['sharpe']:.2f} | "
        f"sortino {s['sortino']:.2f} | trades/day {s['avg_trades_per_day']:.2f}",
        f"losing streak {s['longest_losing_streak']} | recovery {s['recovery_factor']:.2f}",
        "-" * 64,
        "rejections: " + ", ".join(f"{k}={v}" for k, v in rej["by_leg"].items()),
    ]
    lines.append("by coin:")
    for r in bd["by_coin"][:10]:
        lines.append(f"  {r['name']:12s} n={r['n']:3d} won={r['won']:3d} "
                     f"pnl={r['pnl']:+9.2f} avgR={r['avg_r']:+.2f}")
    lines.append("by hour (UTC):")
    for r in sorted(bd["by_hour"], key=lambda x: x["name"]):
        lines.append(f"  {r['name']:>2s}: n={r['n']:3d} pnl={r['pnl']:+9.2f}")
    lines.append("=" * 64)
    return "\n".join(lines)
