"""Robustness battery (spec section 22).

    python tools/robustness.py --profile aggressive [--fast]

  * parameter perturbation  +/-10% / +/-20%, one parameter at a time
  * slippage stress 1x/2x/3x, fee stress 1x/1.5x/2x
  * Monte Carlo on the baseline trade sequence (order shuffle + execution
    noise): probability of ruin, expected drawdown, 5th percentile equity

Everything is seeded and reproducible; results go to
data/reports/robustness.json.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.loader import load_config                      # noqa: E402
from engine import BacktestEngine                           # noqa: E402
from market_data.store import CandleStore                   # noqa: E402
from metrics import summarize                               # noqa: E402
from robustness import monte_carlo, perturb, stress_slippage_fees  # noqa: E402

DAY_MS = 86_400_000


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=None)
    ap.add_argument("--days", type=int, default=130)
    ap.add_argument("--fast", action="store_true",
                    help="perturbation deltas 10% only, one sign")
    a = ap.parse_args()

    profile = Path(f"config/{a.profile}.yaml") if a.profile else None
    store = CandleStore("data/candles")
    frames = {s: store.load(s, "1m") for s in store.symbols()}
    end_ms = int(max(frames[s]["open_time"].max() for s in frames))
    start_ms = end_ms - a.days * DAY_MS

    def make(overrides=None):
        return load_config(extra_file=profile, overrides=overrides)

    t0 = time.time()
    # baseline
    eng = BacktestEngine(make())
    sds = eng.prepare(frames, start_ms, end_ms)
    base = eng.run(sds, start_ms, end_ms, starting_equity=100.0)
    base_sum = summarize(base.trades, base.equity_curve, 100.0)
    print(f"baseline: n={base_sum['n_trades']} net={base_sum['net_pnl']:+.2f} "
          f"PF={base_sum['profit_factor']:.2f} DD={base_sum['max_drawdown_pct']:.1f}% "
          f"({time.time()-t0:.0f}s)")

    # perturbation
    deltas = (0.1,) if a.fast else (0.1, 0.2)
    rows = perturb(make, store, start_ms, end_ms, deltas=deltas)
    collapsed = {}
    for r in rows:
        key = f"{r['path']}{r['delta']:+.0%}"
        collapsed.setdefault(key, []).append({
            "value": r["value"], "n": r["n_trades"], "net": r["net_pnl"],
            "pf": r["profit_factor"], "dd": r["max_dd_pct"], "avg_r": r["avg_r"]})
    worst = sorted(rows, key=lambda r: r["profit_factor"])[:5]
    print("perturbation runs:", len(rows))
    for r in worst:
        print(f"  worst: {r['path']} {r['delta']:+.0%} -> PF={r['profit_factor']:.2f} "
              f"net={r['net_pnl']:+.2f} n={r['n_trades']}")

    # stress
    stress = stress_slippage_fees(make, store, start_ms, end_ms)
    for k, v in stress.items():
        print(f"  {k}: n={v['n_trades']} net={v['net_pnl']:+.2f} "
              f"PF={v['profit_factor']:.2f} DD={v['max_dd_pct']:.1f}%")

    # monte carlo on the baseline trade sequence
    mc = monte_carlo(base.trades, 100.0, n_sims=1000, seed=42)
    print("monte carlo:", json.dumps(mc, indent=1)[:600])

    out = {"baseline": base_sum, "perturbation": collapsed,
           "perturbation_worst": worst, "stress": stress, "monte_carlo": mc,
           "seconds": round(time.time() - t0, 1)}
    Path("data/reports/robustness.json").write_text(json.dumps(out, indent=1,
                                                               default=float))
    print("-> data/reports/robustness.json")


if __name__ == "__main__":
    main()
