"""Run a historical backtest and print + persist the full report.

Usage:
    python tools/backtest.py --days 130 --profile aggressive
    python tools/backtest.py --start 2026-06-01 --end 2026-09-01
    python tools/backtest.py --days 130 --tp A --compare-models
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
from logger import EventLog                                 # noqa: E402
from market_data.store import CandleStore                   # noqa: E402
from metrics import breakdowns, rejection_summary, report_text, summarize  # noqa: E402

DAY_MS = 86_400_000


def _ms(s: str) -> int:
    import datetime as dt
    return int(dt.datetime.fromisoformat(s + "T00:00:00+00:00").timestamp() * 1000)


def run_once(cfg, store, start_ms, end_ms, label: str = "", out_dir: Path | None = None):
    frames = {s: store.load(s, "1m") for s in store.symbols()}
    eng = BacktestEngine(cfg)
    t0 = time.time()
    sds = eng.prepare(frames, start_ms, end_ms)
    res = eng.run(sds, start_ms, end_ms, starting_equity=cfg.paper["starting_equity"])
    summary = summarize(res.trades, res.equity_curve, cfg.paper["starting_equity"])
    bd = breakdowns(res.trades)
    rej = rejection_summary(res.rejections)
    meta = {"tp_model": cfg.tp["model"],
            "start_ms": start_ms, "end_ms": end_ms, "label": label,
            "prep_run_seconds": round(time.time() - t0, 1)}
    if out_dir is not None:
        log = EventLog(out_dir)
        for t in res.trades:
            log.trade(t)
        for r in res.rejections:
            log.rejection(r)
        log.run(summary, cfg.raw(), meta)
        log.flush()
        report = {"summary": summary, "breakdowns": bd, "rejections": rej,
                  "meta": meta, "universes": {str(k): v for k, v in res.universes.items()}}
        (out_dir / f"report{label}.json").write_text(json.dumps(report, indent=1))
    return summary, bd, rej, meta


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--start", default=None, help="YYYY-MM-DD (overrides --days)")
    ap.add_argument("--end", default=None, help="YYYY-MM-DD")
    ap.add_argument("--profile", default=None, help="e.g. aggressive -> config/aggressive.yaml")
    ap.add_argument("--overrides", default="", help="key=value,key2=value2 config overrides")
    ap.add_argument("--tp", default=None, choices=["A", "B", "C"])
    ap.add_argument("--compare-models", action="store_true")
    ap.add_argument("--out", default="data/reports")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    profile = Path(f"config/{a.profile}.yaml") if a.profile else None
    cfg = load_config(extra_file=profile)

    store = CandleStore("data/candles")
    syms = store.symbols()
    if not syms:
        print("no candle data -- run tools/download.py first"); sys.exit(1)
    end_ms = int(max(store.load(s, "1m")["open_time"].max() for s in syms))
    if a.end:
        end_ms = _ms(a.end)
    if a.start:
        start_ms = _ms(a.start)
    else:
        days = a.days or 130
        start_ms = end_ms - days * DAY_MS

    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if a.compare_models:
        for model in ("A", "B", "C"):
            c2 = load_config(extra_file=profile,
                             overrides={**({"tp.model": model} if model != cfg.tp["model"] else {})})
            s, bd, rej, meta = run_once(c2, store, start_ms, end_ms,
                                        label=f"-tp{model}", out_dir=out_dir)
            print(f"--- model {model} ---")
            print(report_text(s, bd, rej, meta))
        return

    if a.tp:
        cfg = load_config(extra_file=profile, overrides={"tp.model": a.tp})
    s, bd, rej, meta = run_once(cfg, store, start_ms, end_ms, out_dir=out_dir)
    if not a.quiet:
        print(report_text(s, bd, rej, meta))


if __name__ == "__main__":
    main()
