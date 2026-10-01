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
import bootstrap  # noqa: E402

FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


def ff_events():
    """ForexFactory this-week calendar -> [(ts_ms, impact, name)] for USD High/Medium rows."""
    import urllib.request
    from datetime import datetime
    req = urllib.request.Request(FF_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        rows = json.loads(r.read())
    out = []
    for e in rows:
        if e.get("country") != "USD" or e.get("impact") not in ("High", "Medium"):
            continue
        try:
            ts = int(datetime.fromisoformat(e["date"]).timestamp() * 1000)
        except Exception:
            continue
        out.append((ts, "HIGH" if e["impact"] == "High" else "MEDIUM", e.get("title", "")))
    return out

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
                       handoff_equity=a.handoff, demote_equity=a.demote, state_dir=str(REPO / "data/state"),
                       mirror_start=a.mirror)
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
    acc = await gw.get_account_snapshot(force_fresh=True)
    if acc and acc.assets_used > 0:
        log.warning("orphan positions at startup (margin used %.2f): flattening", acc.assets_used)
        await gw.flatten_all_positions()
    if a.selftest:
        q = await gw.wait_for_quote(timeout=10.0)
        res = await gw.open_market_order(direction="BUY", volume=0.01, sl_price=round(q.bid - 5.0, 2),
                                         tp_price=None, expected_mode=a.mode)
        log.info("SELFTEST open: %s", res)
        await asyncio.sleep(3)
        res2 = await gw.flatten_all_positions()
        log.info("SELFTEST flatten: %s", res2)
        await gw.close()
        return 0 if (res and res.get("success") and res2 and res2.get("success")) else 4
    await tr.refresh_equity()
    log.info("xau_alpha live: module=%s mode=%s equity=%.2f phase=%s bars=%d",
             a.module, a.mode, tr.equity, tr.phase, len(tr.bars))
    from trader import push
    push("✅ xau_alpha started", f"{a.module} on {a.mode} · mirror ${a.mirror:.0f} · lock ${tr.cfg.lock_equity:.0f} · bars {len(tr.bars)}", "default")
    def refresh_news():
        try:
            ev = ff_events()
            tr.news.add_events(ev, fresh_for_s=8 * 3600)
            log.info("calendar: merged %d ForexFactory USD rows", len(ev))
        except Exception as e:
            log.warning("ForexFactory calendar fetch failed (%s); relying on local CSV (fail-closed if stale)", e)

    refresh_news()
    q0 = await gw.wait_for_quote(timeout=10.0)
    if q0 is not None:
        try:
            n = bootstrap.warm(tr, (q0.bid + q0.ask) / 2, minutes=tr.warmup + 200)
            log.info("warm-up: loaded %d history bars (now %d)", n, len(tr.bars))
        except Exception as e:
            log.warning("warm-up failed: %s (will build bars live)", e)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(s, stop.set)
    last_eq = last_tel = last_news = 0.0
    last_trade_check = time.time()
    start_ts = time.time()
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
        if now - last_news > 6 * 3600:
            last_news = now
            refresh_news()
        if now - last_trade_check > 3600:
            last_trade_check = now
            tel = tr.telemetry()
            log.info("status: %s", json.dumps(tel))
            quiet_h = (now - getattr(tr, "last_signal_ts", start_ts)) / 3600
            if quiet_h >= 6:
                from trader import push
                push("⏳ no setup for %.0f h" % quiet_h, "Bot is alive (bars %d, equity $%.2f) but no setup has formed." % (tel["bars"], tel["equity"]), "default")
            if int(now // 3600) % 4 == 0:
                from trader import push
                push("💓 xau_alpha heartbeat", f"{tel['mode']} {tel['phase']} equity ${tel['equity']:.2f} · bars {tel['bars']} · "
                     f"position {'yes' if tel['position'] else 'none'} · busts {tel['busts']}", "min")
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
    ap.add_argument("--handoff", type=float, default=500.0)
    ap.add_argument("--selftest", action="store_true", help="open+close one 0.01 lot on DEMO and exit")
    ap.add_argument("--mirror", type=float, default=0.0, help="DEMO: size as if equity started here (e.g. 13)")
    ap.add_argument("--demote", type=float, default=60.0)
    a = ap.parse_args()
    if a.selftest and a.mode != "DEMO":
        sys.exit("selftest is DEMO-only")
    if a.mode == "REAL" and not a.allow_real:
        sys.exit("REAL mode requires --allow-real")
    sys.exit(asyncio.run(main(a)))
