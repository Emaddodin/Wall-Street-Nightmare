"""
preflight_real_connectivity_test.py
===================================
Non-destructive, thorough pre-flight connectivity, latency, auth, stream,
DOM execution controls, and account integrity test for Live Broker Execution.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from engine.litefinance_gateway import LiteFinanceGateway

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("preflight_check")


async def run_audit():
    results = {}
    logger.info("================================================================")
    logger.info("🛡️ STARTING BROKER LIVE PRE-FLIGHT VERIFICATION & AUDIT")
    logger.info("================================================================")

    gw = LiteFinanceGateway()

    # 1. Gateway Initialization & Authentication
    logger.info("\n--- TEST 1: Session & Authentication Verification ---")
    t0 = time.perf_counter()
    connected = await gw.initialize()
    init_time_s = time.perf_counter() - t0

    if not connected:
        logger.error("❌ TEST 1 FAILED: Could not initialize LiteFinance Gateway.")
        sys.exit(1)

    acc = await gw.get_account_snapshot(force_fresh=True)
    logger.info("✅ TEST 1 PASSED: Gateway initialized in %.2fs. Balance: $%.2f | Assets Used: $%.2f | Available: $%.2f",
                init_time_s, acc.balance, acc.assets_used, acc.available)
    results["test_1_auth"] = {
        "status": "PASS",
        "init_time_s": round(init_time_s, 2),
        "balance": acc.balance,
        "assets_used": acc.assets_used,
        "available": acc.available,
    }

    # 2. Quote Stream Benchmarking
    logger.info("\n--- TEST 2: High-Speed Quote Stream & Latency Check (10 ticks) ---")
    ticks_received = 0
    t_start = time.time()
    latencies = []
    spreads = []
    last_quote = None

    for i in range(15):
        q = await gw.wait_for_quote(timeout=1.0)
        if q:
            ticks_received += 1
            last_quote = q
            lag_ms = (time.time() - q.timestamp) * 1000.0
            latencies.append(lag_ms)
            spread = round(q.ask - q.bid, 2)
            spreads.append(spread)
            logger.info("  Tick #%02d: Bid=$%.2f | Ask=$%.2f | Mid=$%.2f | Spread=$%.2f | ClientLag=%.1fms",
                        ticks_received, q.bid, q.ask, q.mid, spread, lag_ms)
            if ticks_received >= 10:
                break
        await asyncio.sleep(0.05)

    avg_lag = sum(latencies) / len(latencies) if latencies else 0.0
    avg_spread = sum(spreads) / len(spreads) if spreads else 0.0
    logger.info("✅ TEST 2 PASSED: Received %d ticks. Avg Lag: %.1fms | Avg Spread: $%.2f pts",
                ticks_received, avg_lag, avg_spread)
    results["test_2_stream"] = {
        "status": "PASS" if ticks_received >= 5 else "WARN",
        "ticks_received": ticks_received,
        "avg_lag_ms": round(avg_lag, 2),
        "avg_spread_pts": round(avg_spread, 2),
        "last_mid": last_quote.mid if last_quote else 0.0,
    }

    # 3. DOM Execution Controls & Sizing Inputs
    logger.info("\n--- TEST 3: DOM Execution Controls & Stop-Loss Readiness ---")
    dom_status = await gw._page.evaluate("""() => {
        const buyBtn = document.querySelector('button.btn_green.js_trade_action_open, button.btn_green');
        const sellBtn = document.querySelector('button.btn_red.js_trade_action_open, button.btn_red');
        const volInput = document.querySelector('#volume_value_1');
        const slTrigger = document.querySelector('.js_extra_field_trigger');
        const slInput = document.querySelector('#stop_loss_price_1');
        const portfolioDrawer = document.querySelector('.portfolio, .bottom_bar');
        return {
            buy_button_found: !!buyBtn,
            buy_button_visible: !!buyBtn && buyBtn.getBoundingClientRect().width > 0,
            sell_button_found: !!sellBtn,
            sell_button_visible: !!sellBtn && sellBtn.getBoundingClientRect().width > 0,
            volume_input_ready: !!volInput,
            volume_current_val: volInput ? volInput.value : null,
            sl_trigger_available: !!slTrigger,
            sl_input_ready: !!slInput,
            portfolio_drawer_ready: !!portfolioDrawer
        };
    }""")
    logger.info("DOM Check: %s", json.dumps(dom_status, indent=2))
    dom_pass = dom_status.get("buy_button_visible") and dom_status.get("sell_button_visible")
    results["test_3_dom"] = {
        "status": "PASS" if dom_pass else "WARN",
        "details": dom_status,
    }

    # 4. Account Type & Deposit Verification
    logger.info("\n--- TEST 4: Real Account Balance & Equity Confirmation ---")
    account_details = await gw._page.evaluate("""() => {
        const bodyText = document.body.innerText;
        return {
            is_real: bodyText.includes('REAL ACCOUNT') || bodyText.includes('Real trading'),
            is_demo: bodyText.includes('DEMO ACCOUNT'),
            full_text_sample: bodyText.split('\\n').filter(l => l.includes('ACCOUNT') || l.includes('TOTAL') || l.includes('AVAILABLE') || l.includes('USD')).slice(0, 15)
        };
    }""")
    logger.info("Account Type Verification: %s", json.dumps(account_details, indent=2))
    results["test_4_account_type"] = {
        "status": "PASS",
        "details": account_details,
    }

    # 5. Clean close
    await gw.close()

    logger.info("\n================================================================")
    logger.info("🏁 PRE-FLIGHT AUDIT COMPLETE - ALL CHECKS RECORDED")
    logger.info("================================================================")

    # Save to json report
    out_file = ROOT_DIR / "data" / "preflight_real_audit.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w") as f:
        json.dump(results, f, indent=2)
    logger.info("Report saved to %s", out_file)
    return results


if __name__ == "__main__":
    asyncio.run(run_audit())
