"""Sanity profile comparison for the VP scalper.

  aggressive_mech  -- VP scalper with the session filter off (baseline)
  aggressive       -- the live profile (session filter on)

Both chains: default.yaml -> aggressive.yaml -> overlay.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.loader import load_config                      # noqa: E402
from engine import BacktestEngine                           # noqa: E402
from market_data.store import CandleStore                   # noqa: E402
from metrics import breakdowns, rejection_summary, report_text, summarize  # noqa: E402

DAY_MS = 86_400_000


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=130)
    ap.add_argument("--out", default="data/reports/pa_comparison.json")
    a = ap.parse_args()

    store = CandleStore("data/candles")
    frames = {s: store.load(s, "1m") for s in store.symbols()}
    end_ms = int(max(frames[s]["open_time"].max() for s in frames))
    start_ms = end_ms - a.days * DAY_MS

    profiles = [
        ("aggressive", ["config/aggressive.yaml"]),
        ("aggressive_no_filter", ["config/aggressive.yaml",
                                  "config/aggressive_no_filter.yaml"]),
    ]
    out = {}
    for name, chain in profiles:
        cfg = load_config(extra_files=chain)
        eng = BacktestEngine(cfg)
        sds = eng.prepare(frames, start_ms, end_ms)
        res = eng.run(sds, start_ms, end_ms,
                      starting_equity=cfg.paper["starting_equity"])
        s = summarize(res.trades, res.equity_curve,
                      cfg.paper["starting_equity"])
        bd = breakdowns(res.trades)
        rej = rejection_summary(res.rejections)
        meta = {"session_filter": cfg.strategy["session"]["asia_sweep_filter"],
                "starting_equity": cfg.paper["starting_equity"],
                "risk_per_trade": cfg.risk["risk_per_trade"]}
        out[name] = {"summary": s, "rejections": rej, "meta": meta,
                     "by_coin": bd["by_coin"]}
        print(f"===== {name} =====")
        print(report_text(s, bd, rej, meta))
        print()
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=1, default=float))
    print(f"-> {a.out}")


if __name__ == "__main__":
    main()
