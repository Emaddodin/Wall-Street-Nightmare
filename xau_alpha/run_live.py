"""
xau_alpha/run_live.py
Run a frozen xau_alpha candidate against LiteFinance through the existing Playwright gateway.

    python3 xau_alpha/run_live.py --module <cand_module> --params '<json>'            # DEMO (default)
    python3 xau_alpha/run_live.py --module ... --params ... --mode REAL --allow-real  # REAL: explicit opt-in

DEMO is the default. REAL needs both --mode REAL and --allow-real. Nothing here switches the broker account mode.
Laya runs in shadow mode (logged only) when XAU_ALPHA_SHADOW_ORACLE=1.
Logs: data/state/xau_alpha_trades.jsonl (orders, fills, skips, shadow verdicts), data/state/quotes/*.csv (1 Hz
bid/ask), data/state/hft.json (dashboard telemetry).
"""
import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "xau_alpha" / "live"))

from trader import AlphaTrader, TraderConfig  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("XauAlphaRunner")


async def main(a):
    from engine.litefinance_gateway import LiteFinanceGateway
    oracle = None
    if os.getenv("XAU_ALPHA_SHADOW_ORACLE") == "1":
        try:
            from scalper.brain.laya_oracle import get_laya_oracle
            oracle = get_laya_oracle()
        except Exception as e:
            log.warning("shadow oracle unavailable: %s", e)
    cfg = TraderConfig(module=a.module, params=json.loads(a.params), mode=a.mode, allow_real=a.allow_real,
                       handoff_equity=a.handoff, demote_equity=a.demote)
    gw = LiteFinanceGateway()
    if not await gw.initialize():
        log.error("gateway failed to initialize")
        return 2
    mode = await gw.get_account_mode()
    if mode != a.mode.upper():
        log.error("broker account mode is %s but --mode is %s: refusing to run", mode, a.mode)
        await gw.close()
        return 3
    tr = AlphaTrader(gw, cfg, oracle=oracle)
    await tr.refresh_equity()
    log.info("xau_alpha live: module=%s mode=%s equity=%.2f phase=%s bars=%d",
             a.module, a.mode, tr.equity, tr.phase, len(tr.bars))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(s, stop.set)
    last_eq = last_tel = 0.0
    last_q = None
    while not stop.is_set():
        q = await gw.wait_for_quote(timeout=1.0)
        now = time.time()
        if q is not None and (last_q is None or q.timestamp != last_q.timestamp):
            last_q = q
            await tr.on_quote(q.bid, q.ask, now)
        elif last_q is not None:
            await tr.on_timer(last_q.bid, last_q.ask, now)
        if now - last_eq > 30:
            last_eq = now
            try:
                await tr.refresh_equity()
            except Exception as e:
                log.warning("equity refresh failed: %s", e)
        if now - last_tel > 5:
            last_tel = now
            p = REPO / "data/state/hft.json"
            try:
                tmp = p.with_suffix(".tmp")
                tmp.write_text(json.dumps({**tr.telemetry(), "updated_at": now,
                                           "bid": last_q.bid if last_q else None,
                                           "ask": last_q.ask if last_q else None}, indent=2))
                tmp.replace(p)
            except Exception:
                pass
    log.info("stopping: flattening any open position")
    if tr.pos is not None:
        await gw.flatten_all_positions()
    tr.bars.save()
    await gw.close()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--module", required=True)
    ap.add_argument("--params", default="{}")
    ap.add_argument("--mode", default="DEMO", choices=["DEMO", "REAL"])
    ap.add_argument("--allow-real", action="store_true")
    ap.add_argument("--handoff", type=float, default=100.0)
    ap.add_argument("--demote", type=float, default=60.0)
    a = ap.parse_args()
    if a.mode == "REAL" and not a.allow_real:
        sys.exit("REAL mode requires --allow-real")
    sys.exit(asyncio.run(main(a)))
