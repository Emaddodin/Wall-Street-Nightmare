#!/usr/bin/env python3
"""
backtest_live_engine.py
=======================
6-month backtest of the LIVE gold-scalper configuration on REAL Hyperliquid
PAXG history, driving the ACTUAL engine classes (no re-implementation):

  RelapseFSM + ExecutionRouter + SimulatedBrokerVenue + KillZoneGuard
  + DailyDrawdownGuard + EconomicCalendarFilter

Live settings mirrored exactly (run_relapse_scalper.py defaults):
  equity $65 | lev 100x | margin cap 20% | SL envelope $1.00-$1.50
  3 x 0.01 oz slices | BE lock at +1.5R (entry +/- $0.10) | no static TP
  all 4 killzones | macro blackout via calendar (no URL -> never blackouts,
  same as live) | 5% daily drawdown killswitch.

Documented deviations from live (unavoidable in backtest):
  D1. SLM intuition LLM exit poll (1m) is stubbed to HOLD -- no llama.cpp in
      backtest. Structural exits (SL / BE-stop / opposing 5m pattern) are real.
  D2. No taker fees in the paper venue (same as live paper). A net-of-fees
      sensitivity line (HL PAXG taker 0.045%/side) is computed on top.
  D3. 5m-close resolution for SL checks (the FSM's own resolution).

Data: REAL PAXG 5m candleSnapshot paged from api.hyperliquid.xyz, cached to
data/candles/hl_paxg_5m_6mo.json. NOT synthetic.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from engine.execution_router import ExecutionRouter, RiskInvariants, SimulatedBrokerVenue
from engine.fsm import DailyDrawdownGuard, RelapseFSM
from engine.killzone import KillZoneGuard, XAUUSD_GOLD_KILLZONES
from hl_feed import rest_info
from macro.slm_intuition import EconomicCalendarFilter, IntuitionDecision

CACHE = ROOT / "data" / "candles" / "hl_paxg_5m_6mo.json"
START_EQUITY = 65.0
TAKER_FEE = 0.00045  # HL PAXG-PERP VIP0 taker, per side


def fetch_6mo_5m() -> list:
    if CACHE.exists():
        data = json.loads(CACHE.read_text())
        print(f"loaded {len(data)} cached 5m bars from {CACHE.name}", flush=True)
        return data
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - 183 * 86400 * 1000  # ~6 months + margin
    out, cur, page = [], start_ms, 0
    while cur < end_ms:
        raw = rest_info({"type": "candleSnapshot", "req": {
            "coin": "PAXG", "interval": "5m",
            "startTime": cur, "endTime": end_ms}}, timeout=30.0)
        if not raw:
            break
        out.extend(raw)
        page += 1
        print(f"  page {page}: +{len(raw)} bars (total {len(out)})", flush=True)
        if len(raw) < 5000:
            break
        cur = int(raw[-1]["T"]) + 1
        if len(out) > 120_000:
            break
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(out))
    print(f"cached {len(out)} bars", flush=True)
    return out


class HoldIntuition:
    """LLM stub: never exits. Structural exits remain fully active."""

    async def query_intuition_exit(self, telemetry):
        return IntuitionDecision.HOLD, 0.0, "backtest-stub-HOLD"

    async def close(self):
        return None


async def run() -> dict:
    raw = fetch_6mo_5m()
    df = pd.DataFrame([{
        "open_time": int(c["t"]),
        "open": float(c["o"]), "high": float(c["h"]),
        "low": float(c["l"]), "close": float(c["c"]),
        "volume": float(c["v"]),
    } for c in raw]).sort_values("open_time").reset_index(drop=True)
    first = datetime.fromtimestamp(int(df["open_time"].iloc[0]) / 1000, tz=timezone.utc)
    last = datetime.fromtimestamp(int(df["open_time"].iloc[-1]) / 1000, tz=timezone.utc)
    print(f"history: {len(df)} 5m bars, {first:%Y-%m-%d} -> {last:%Y-%m-%d}", flush=True)

    venue = SimulatedBrokerVenue(initial_equity=START_EQUITY)
    risk = RiskInvariants(coin="GOLD", leverage=100.0, max_margin_pct=0.20,
                          min_sl_delta=1.00, max_sl_delta=1.50,
                          initial_account_equity=START_EQUITY)
    router = ExecutionRouter(venue=venue, risk=risk)
    kz = KillZoneGuard(zones=XAUUSD_GOLD_KILLZONES)
    cal = EconomicCalendarFilter()
    await cal.start()
    dd = DailyDrawdownGuard(max_drawdown_pct=0.05)
    fsm = RelapseFSM(symbol="XAUUSD", execution_router=router,
                     intuition_engine=HoldIntuition(), calendar_filter=cal,
                     drawdown_guard=dd, killzone_guard=kz)

    # --- instrumentation: record entries, exits, SL-envelope rejections ---
    trades, rejections = [], defaultdict(int)
    _fire = router.fire_layered_orders
    _close = router.close_basket

    async def fire_spy(*a, **k):
        b = await _fire(*a, **k)
        if b is None:
            rejections["margin_or_sl"] += 1
        else:
            trades.append({"entry_ts": int(df["open_time"].iloc[cur[0]]) // 1000,
                           "side": b.side.value, "entry": b.entry_price,
                           "sz": b.total_sz, "sl": b.sl_price, "exit": None,
                           "pnl": None, "reason": None})
        return b

    async def close_spy(reason="EXIT_SIGNAL"):
        b = router.active_basket
        meta = (b.side.value, b.entry_price, b.total_sz) if b else None
        out = await _close(reason=reason)
        if out and trades and trades[-1]["exit"] is None:
            trades[-1].update(exit=out.get("exit_price"), pnl=out.get("total_pnl"),
                              reason=out.get("reason"))
        elif out:
            trades.append({"entry_ts": None, "side": meta[0] if meta else "?",
                           "entry": meta[1] if meta else 0, "sz": meta[2] if meta else 0,
                           "sl": 0, "exit": out.get("exit_price"),
                           "pnl": out.get("total_pnl"), "reason": out.get("reason")})
        return out

    router.fire_layered_orders = fire_spy
    router.close_basket = close_spy
    cur = [0]

    peak = START_EQUITY
    max_dd = 0.0
    t0 = time.time()
    n = len(df)
    for i in range(80, n):
        cur[0] = i
        close = float(df["close"].iloc[i])
        venue.set_market_price("XAUUSD", close)
        venue.set_market_price("GOLD", close)
        win = df.iloc[i - 80:i + 1].reset_index(drop=True)
        await fsm.on_5m_bar_update(win)
        eq = await venue.get_equity()
        if router.active_basket:
            fl = eq  # realized; add floating for DD honesty
            _, _ = router.active_basket.calculate_unrealized_pnl(close)
            fl = eq + router.active_basket.calculate_unrealized_pnl(close)[0]
            peak = max(peak, fl)
            max_dd = max(max_dd, (peak - fl) / peak * 100 if peak > 0 else 0)
        else:
            peak = max(peak, eq)
            max_dd = max(max_dd, (peak - eq) / peak * 100 if peak > 0 else 0)
        if (i - 80) % 10000 == 0:
            print(f"  bar {i - 80}/{n - 80} eq=${eq:.2f} trades={len(trades)} "
                  f"elapsed={time.time() - t0:.0f}s", flush=True)

    # flush open basket at final price
    if router.active_basket:
        venue.set_market_price("XAUUSD", float(df["close"].iloc[-1]))
        await router.close_basket(reason="BACKTEST_END_FLUSH")
    final_eq = await venue.get_equity()
    await cal.stop()

    closed = [t for t in trades if t["pnl"] is not None]
    wins = [t for t in closed if t["pnl"] > 0]
    losses = [t for t in closed if t["pnl"] <= 0]
    gross = sum(t["pnl"] for t in closed)
    # fee sensitivity: taker on notional each side
    fees = sum((abs(t["entry"]) + abs(t["exit"] or t["entry"])) * t["sz"] * TAKER_FEE
               for t in closed)
    by_month = defaultdict(list)
    for t in closed:
        if t["entry_ts"]:
            by_month[datetime.fromtimestamp(t["entry_ts"], tz=timezone.utc).strftime("%Y-%m")].append(t)

    res = {
        "bars": n, "first": f"{first:%Y-%m-%d}", "last": f"{last:%Y-%m-%d}",
        "start_equity": START_EQUITY, "final_equity": round(final_eq, 2),
        "net_pnl": round(final_eq - START_EQUITY, 2),
        "trades": len(closed), "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else 0.0,
        "gross_pnl": round(gross, 2),
        "avg_win": round(sum(t["pnl"] for t in wins) / len(wins), 2) if wins else 0.0,
        "avg_loss": round(sum(t["pnl"] for t in losses) / len(losses), 2) if losses else 0.0,
        "est_fees": round(fees, 2), "net_of_fees": round(gross - fees, 2),
        "max_dd_pct": round(max_dd, 2),
        "sl_envelope_rejections": rejections["margin_or_sl"],
        "months": {m: {"trades": len(v),
                       "pnl": round(sum(t["pnl"] for t in v), 2),
                       "wins": len([t for t in v if t["pnl"] > 0])} for m, v in sorted(by_month.items())},
        "elapsed_s": round(time.time() - t0, 1),
    }
    (ROOT / "logs" / "backtest_live_6mo.json").write_text(json.dumps(res, indent=2))
    print("\n" + json.dumps(res, indent=2), flush=True)
    return res


if __name__ == "__main__":
    asyncio.run(run())
