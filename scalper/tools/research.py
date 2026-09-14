"""Research-mode collector (spec section 25): hypothetical signals + outcomes.

    python tools/research.py --days 130 --profile aggressive

Runs the engine in observe mode (it never opens a position), attaches the
future outcome of every candidate setup, and appends the rows to
data/research/research.jsonl.  Nothing in this tool changes strategy
parameters.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.loader import load_config                      # noqa: E402
from engine import BacktestEngine                           # noqa: E402
from market_data.store import CandleStore                   # noqa: E402
from research import outcomes_for                           # noqa: E402

DAY_MS = 86_400_000


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=130)
    ap.add_argument("--profile", default=None)
    ap.add_argument("--out", default="data/research/research.jsonl")
    a = ap.parse_args()

    cfg = load_config(extra_file=Path(f"config/{a.profile}.yaml") if a.profile else None)
    store = CandleStore("data/candles")
    frames = {s: store.load(s, "1m") for s in store.symbols()}
    end_ms = int(max(frames[s]["open_time"].max() for s in frames))
    start_ms = end_ms - a.days * DAY_MS

    eng = BacktestEngine(cfg)
    sds = eng.prepare(frames, start_ms, end_ms)
    res = eng.run(sds, start_ms, end_ms, starting_equity=cfg.paper["starting_equity"],
                  research_mode=True)
    rows = outcomes_for(sds, res.research_signals, cfg)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "a") as fh:
        for r in rows:
            fh.write(json.dumps(r, default=float) + "\n")
    print(f"{len(rows)} candidate setups recorded -> {out}")
    if rows:
        sl = sum(1 for r in rows if r["outcome_first"] == "SL")
        tp = sum(1 for r in rows if r["outcome_first"] == "TP1")
        print(f"first touch: SL {sl} ({sl/len(rows):.0%}) | TP1 {tp} ({tp/len(rows):.0%})"
              f" | none yet {len(rows)-sl-tp}")
        avg_mfe = sum(r["mfe_r"] for r in rows) / len(rows)
        avg_mae = sum(r["mae_r"] for r in rows) / len(rows)
        print(f"avg MFE {avg_mfe:+.2f}R | avg MAE {avg_mae:+.2f}R")


if __name__ == "__main__":
    main()
