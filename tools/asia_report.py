#!/usr/bin/env python3
"""Session-bucket breakdown for the v4 macro detector (read-only).

Measures expectancy per UTC window over the liquid universe to answer:
is the Asian session (00:00-06:00 UTC, currently "dead") worth trading?

    python3 tools/asia_report.py [days] [coins]
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import live_hyperliquid as m          # noqa: E402
from live_hyperliquid import Config   # noqa: E402

# reuse the audited simulate()/htf_bull() from macro_validation.py
_mv = Path(ROOT) / "tools" / "macro_validation.py"
_spec = importlib.util.spec_from_file_location("macro_validation", _mv)
_mvmod = importlib.util.module_from_spec(_spec)
sys.modules["macro_validation"] = _mvmod
_spec.loader.exec_module(_mvmod)
simulate = _mvmod.simulate
htf_bull = _mvmod.htf_bull
TF_MS = _mvmod.TF_MS

BUCKETS = [
    ("Asia        00-06", 0, 6),
    ("London      06-12", 6, 12),
    ("New York    12-20", 12, 20),
    ("Late/Eve    20-24", 20, 24),
]


async def main(days: int, want: int) -> int:
    info = m.Info(m.hl_constants.MAINNET_API_URL, skip_ws=True)
    loop = asyncio.get_running_loop()
    meta, ctxs = await loop.run_in_executor(None, info.meta_and_asset_ctxs)
    rows = meta["universe"]
    cands = []
    for i, u in enumerate(rows):
        name = u.get("name", "")
        if not name or i >= len(ctxs):
            continue
        try:
            vol = float(ctxs[i].get("dayNtlVlm") or 0)
        except (TypeError, ValueError):
            continue
        if vol >= 1e6:
            cands.append((name, vol))
    cands.sort(key=lambda x: -x[1])
    cands = cands[:want]
    now = int(time.time() * 1000)
    start = now - days * 24 * 3600 * 1000
    sem = asyncio.Semaphore(4)

    async def fetch(coin):
        async with sem:
            return await loop.run_in_executor(
                None, info.candles_snapshot, coin, "15m", start, now)

    got = await asyncio.gather(*[fetch(c) for c, _ in cands],
                               return_exceptions=True)
    data = []
    for (coin, _), bars in zip(cands, got):
        if isinstance(bars, BaseException) or not bars:
            continue
        bars = [b for b in bars if int(b["T"]) <= now]
        if len(bars) >= 200:
            data.append((coin, bars))
    print(f"data: {len(data)} coins x {days} days of 15m candles\n")

    cfg = Config()  # LIVE defaults (momentum, leg>=4, 0.6 stop, TP1 2.5)

    buckets = {name: {"rs": [], "setups": 0} for name, _, _ in BUCKETS}

    for coin, bars in data:
        seen = set()
        day_sig: dict[int, int] = {}
        last_t = 0
        for i in range(120, len(bars) - 1):
            bull = htf_bull(bars, i, cfg)
            if bull is None:
                continue
            st = m.analyze_macro(bars[:i + 1], cfg)
            if not st:
                continue
            if (st["side"] > 0) != bull:
                continue
            key = (st["side"], st["sweep_bar_t"], round(st["entry"], 8))
            if key in seen:
                continue
            dk = int(bars[i]["t"]) // 86_400_000
            if day_sig.get(dk, 0) >= cfg.macro_max_signals_day:
                continue
            if last_t and bars[i]["t"] - last_t < cfg.macro_cooldown_bars * TF_MS:
                continue
            seen.add(key)
            day_sig[dk] = day_sig.get(dk, 0) + 1
            last_t = bars[i]["t"]
            res = simulate(bars, i, st, cfg)
            if not res.get("filled"):
                continue
            hour = time.gmtime(bars[i]["t"] / 1000).tm_hour
            for name, lo, hi in BUCKETS:
                if lo <= hour < hi:
                    buckets[name]["rs"].append(res["r"])
                    buckets[name]["setups"] += 1
                    break

    print("=== expectancy by UTC session (LIVE config, HTF-aligned) ===")
    for name, lo, hi in BUCKETS:
        b = buckets[name]
        rs = b["rs"]
        n = len(rs)
        exp = sum(rs) / n if n else 0.0
        win = sum(1 for r in rs if r > 0) / n * 100 if n else 0.0
        print(f"{name}  setups {b['setups']:4d} filled {n:4d} | "
              f"exp {exp:+.3f}R | win {win:3.0f}% | total {sum(rs):+7.1f}R")
    return 0


if __name__ == "__main__":
    d = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    c = int(sys.argv[2]) if len(sys.argv) > 2 else 12
    raise SystemExit(asyncio.run(main(d, c)))
