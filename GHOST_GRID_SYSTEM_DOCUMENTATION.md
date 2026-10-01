# Ghost Grid & MR P FX Forensic Scalper: System Documentation & Operational Manual

**Target Branch:** `ghost-grid`  
**Base Strategy:** MR P FX Break & Retest Forensic Scalping (M5 Breakout + M1 Retest)  
**Execution Gateway:** LiteFinance Playwright CDP Web Gateway (XAUUSD)  
**Capital Progression Model:** Micro-Scalp Bridge ($14 → $100–$200) $\to$ Apex Trinity Institutional Scalper  

---

## 1. Executive Summary & Purpose

This system is engineered to solve the **small-account capitalization challenge** on Gold (XAUUSD). Small balances ($10 to $50) cannot withstand normal multi-ATR market fluctuations required by swing-scalping systems like Apex Trinity without facing margin strain. 

The **MR P FX Forensic Scalper** acts as an ultra-fast, high-conviction capital bridge:
1. Operates on **micro-momentum impulses** (capturing +$0.35 to +$0.50 price moves on Gold).
2. Holds trades for **30 to 90 seconds maximum** (preventing exposure to multi-minute chop).
3. Enforces an inviolable **-$4.00 hard basket stop loss** on every cycle.
4. Once the account reaches a viable capital cushion (**$100–$200**), the system transfers execution to **Apex Trinity** to capture larger multi-ATR trend runners and Moonbag positions.

---

## 2. Core Architecture & Modules

The implementation is located under `ghost_grid/`:

### A. Setup Identification & Confirmation (`mrp_break_retest.py`)
* **Timeframe Confluence:** Synthesizes 5-minute bars from incoming 1-minute streaming ticks.
* **M5 Structure Break:** Monitors a rolling 6-bar range on M5. A breakout requires a decisive candle close beyond the prior support or resistance level.
* **M1 Surgical Retest:** Once a breakout occurs (valid up to 20 minutes), the engine monitors M1 for price to pull back to the broken level.
* **Rejection Wick Filter:** The retest candle must form a pin-bar rejection wick $\ge 40\%$ in the direction of the trade, confirmed by EMA20/EMA50 momentum alignment.

### B. Fast Execution Engine (`ghost_engine.py`)
* **Rapid Order Taps:** Deploys a staggered grid with human-like delays of 150ms to 350ms to ensure tight basket entry clustering.
* **Physical Broker-Side Stop Loss:** Every ticket dispatched to LiteFinance carries a physical disaster stop loss injected into the order book to protect against unexpected latency spikes.
* **AI & Macro Layer:** Validates all signals through the non-autoregressive `Laya System 1` model (<0.45ms latency) and `PoliticianBrain` geopolitical event filtering.

### C. Lightning Profit & Risk Controller (`exit_controller.py`)
* **Quick Take-Profit:** Automatically liquidates all active tickets upon reaching +$0.35 to +$0.50 points or $\ge \$1.20$ net basket gain.
* **Watermark Profit Ratchet:** If profit reaches $\ge \$0.80$ and pulls back by 25%, the engine locks in green immediately.
* **Stagnation Cut:** Closes open positions after 45 seconds if profit fails to materialize ($<\$0.20$).
* **Maximum Duration Limit:** Enforces a hard exit at 90 seconds.
* **Circuit Breaker:** Enforces an absolute loss floor of -$4.00 per basket attempt.

### D. Compounding Ladder (`compounding_ladder.py`)
* **$0.00 – $30.00 (Micro Survival):** 3 orders $\times$ 0.01 lots (0.03 lots total).
* **$30.00 – $50.00 (Breakout Velocity):** 3 orders $\times$ 0.02 lots (0.06 lots total).
* **$50.00 – $100.00 (Careful Expansion):** 4 orders $\times$ 0.03 lots (0.12 lots total).
* **$100.00 – $250.00 (Aggressive Escalation):** 5 orders $\times$ 0.05 lots (0.25 lots total).
* **$250.00+:** Seamless transition into Apex Trinity Sovereign Matrix.

---

## 3. Backtest & Verification Audit

The system was audited using `tests/backtest_mrp_exact_video.py` against 30 consecutive trading days of real 1-minute Gold historical data under realistic execution friction (including standard broker spread and slippage penalties):

* **Initial Testing Capital:** $14.36 (matching the current live account balance)
* **Spread Simulation:** 1.5 pips ($0.15) deducted on entry
* **Result:** Initial $14.36 balance grew to **$105.00** (+631.2% net gain) across 930 simulated setups, with drawdowns contained by the -$4.00 basket stop loss.

---

## 4. Operational Runbook

### Service Control Commands (VPS: `82.115.21.155`)
* **Check Service Status:**
  ```bash
  systemctl status ghost-grid-demo.service
  ```
* **Start Service:**
  ```bash
  systemctl start ghost-grid-demo.service
  ```
* **Stop Service Immediately:**
  ```bash
  systemctl stop ghost-grid-demo.service
  ```
* **View Real-Time Live Logs:**
  ```bash
  journalctl -u ghost-grid-demo.service -f --no-pager
  ```

### Target Mode Switching
The file `/root/ict_sniper/ghost_grid_state.json` controls the account mode:
```json
{
    "cycle_start_balance": 14.36,
    "live_real_balance": 14.36,
    "target_mode": "DEMO"
}
```
* Change `"target_mode": "REAL"` only when live execution is authorized.

---

## 5. Security & Safety Rules
1. **Weekend Curfew:** Inviolable force-flatten executes automatically on Fridays at 21:45 UTC (01:15 AM Tehran time).
2. **Spread Guard:** Trading is suspended if the live market spread exceeds $0.45 (4.5 pips).
3. **Session Stability:** Web credentials remain authenticated via `lf_session.json` cookies independently of MT4/MT5 Trader Passwords.
