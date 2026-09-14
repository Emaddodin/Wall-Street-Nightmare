"""THE BRAIN trainer -- one-shot (and re-runnable) lesson builder.

Replays the real engine over the whole candle store so every closed trade
becomes a labeled lesson (features known at entry -> did it win), adds the
live book's actual trades, then trains the win-probability model with a
strict time split and saves it for the live gate:

    data/state/brain.pkl      (the model)
    data/state/brain_meta.json (validation report: base win rate, AUC,
                                 win-rate lift at each veto bar)

Read-only on everything except its own model files.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

log = logging.getLogger("scalper.brain")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")

DATA = ROOT / "data"


def main() -> int:
    from config.loader import load_config
    from market_data.store import CandleStore
    from engine import BacktestEngine
    from brain import WARMUP_MS, train

    cfg = load_config(extra_file="config/aggressive.yaml")
    store = CandleStore(DATA / "candles")
    # the replay must fit in RAM: take the symbols with the longest history
    # (the most lessons), capped -- not the whole 200+ watchlist
    max_symbols = int(os.environ.get("SCALPER_BRAIN_SYMS", "40"))
    frames = {}
    for s in sorted(store.symbols("1m")):
        try:
            df = store.load(s, "1m")
            if len(df) >= 1500:
                frames[s] = df
        except Exception:
            continue
    if len(frames) > max_symbols:
        keep = sorted(frames, key=lambda s: -len(frames[s]))[:max_symbols]
        frames = {s: frames[s] for s in keep}
    # the internet database (Binance Vision): research lessons from symbols
    # the venue store does not cover -- venue truth always wins collisions
    try:
        bstore = CandleStore(DATA / "candles_binance")
        for bs in bstore.symbols("1m"):
            if bs in frames:
                continue
            bdf = bstore.load(bs, "1m")
            if len(bdf) >= 1500:
                frames[bs] = bdf
    except Exception as e:
        log.warning("internet db merge skipped: %s", e)
    if not frames:
        log.error("no candles -- abort")
        return 1
    last = max(int(df["open_time"].max()) for df in frames.values())
    end_ms = last + 60_000
    days = int(os.environ.get("SCALPER_BRAIN_DAYS", "90"))
    start_ms = end_ms - days * 86_400_000
    lo = start_ms - WARMUP_MS

    log.info("replaying engine over %d symbols, %d days",
             len(frames), (end_ms - start_ms) // 86_400_000)
    t0 = time.time()
    eng = BacktestEngine(cfg)
    try:
        sds = eng.prepare(frames, lo, end_ms)
        if not sds:
            log.error("no symbol had enough history")
            return 1
        res = eng.run(sds, lo, end_ms)
    except Exception as e:
        log.error("replay failed: %s", e)
        return 1
    records = [t for t in res.trades
               if not t.get("phantom") and (t.get("ts_ms") or 0) >= start_ms]
    log.info("replay produced %d labeled trades in %.0fs",
             len(records), time.time() - t0)

    # the live book's actual trades are the freshest lessons
    live_path = DATA / "logs" / "trades.jsonl"
    if live_path.exists():
        for line in live_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("phantom"):
                continue
            if not rec.get("pnl_r") and rec.get("pnl") is None:
                continue
            records.append(rec)
    log.info("total lessons: %d (live %d)", len(records),
             sum(1 for _ in open(live_path)) if live_path.exists() else 0)

    # the lab's mined pool: every trade of every idea ever tested
    pool = DATA / "state" / "lab_pool.jsonl"
    if pool.exists():
        added = 0
        for line in pool.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if not r.get("entry") or not r.get("stop"):
                continue
            rec = {"opened_ms": int(r.get("opened_ms") or 0),
                   "ts_ms": int(r.get("ts_ms") or 0),
                   "symbol": r.get("symbol"),
                   "direction": r.get("direction"),
                   "lot": r.get("lot"), "strategy": r.get("strategy"),
                   "entry_model": r.get("entry_model"),
                   "entry": float(r["entry"]), "stop": float(r["stop"]),
                   "tp": r.get("tp"), "exit": r.get("exit"),
                   "pnl": float(r.get("pnl") or 0.0),
                   "pnl_r": float(r.get("pnl_r") or 0.0),
                   "atr1m": float(r.get("atr1m") or 0.0),
                   "leverage": float(r.get("leverage") or 0.0),
                   "exit_reason": r.get("exit_reason")}
            if rec["opened_ms"] > 0 and rec["pnl"] is not None:
                records.append(rec)
                added += 1
        log.info("pool lessons added: %d", added)

    report = train(records, frames, cfg,
                   meta_out=DATA / "state" / "brain_meta.json",
                   log=log.info)
    log.info("report: %s", json.dumps(report, indent=1))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
