# Stratton Oakmont — Operator Notes & Operational Manual

This document contains essential operator notes, institutional killzone schedules, risk management parameters, broker account procedures, and emergency operations.

---

## 1. Institutional Session & Killzone Schedule

All market hours are mapped across Tehran Time (IRST UTC+3:30), London (BST UTC+1:00), New York (EDT UTC-4:00), and UTC:

| Session / Killzone | UTC Time | Tehran Time (IRST) | New York (EDT) | Strategic Focus & Strategy Tier |
| :--- | :--- | :--- | :--- | :--- |
| **Asian Range Accumulation** | 00:00 – 06:00 | 03:30 – 09:30 | 20:00 – 02:00 | Asian High/Low mapping. Range boundary baseline. |
| **London Open / Judas Swing** | 06:00 – 09:00 | 09:30 – 12:30 | 02:00 – 05:00 | **Prime Window**: London Turtle Soup & 5m Breakout + Retest. High liquidity injection. |
| **London Silver Bullet** | 07:00 – 08:00 | 10:30 – 11:30 | 03:00 – 04:00 | Fair Value Gap (FVG) Consequent Encroachment (CE) tap. |
| **London Mid-Day Transition** | 09:00 – 11:00 | 12:30 – 14:30 | 05:00 – 07:00 | Liquidity pauses. Pre-NY position positioning. |
| **New York Pre-Market & Open** | 11:00 – 14:00 | 14:30 – 17:30 | 07:00 – 10:00 | High volatility overlap. 5m Breakout + 1m pin rejection. |
| **New York Silver Bullet** | 14:00 – 15:00 | 17:30 – 18:30 | 10:00 – 11:00 | **Highest Expected Return Window** (+$1,973 expectancy). Explosive orderflow expansions. |
| **London Close / NY PM** | 15:00 – 19:00 | 18:30 – 22:30 | 11:00 – 15:00 | Trend continuation or afternoon macro spike harvest. |
| **Asian Pre-Market** | 19:00 – 22:00 | 22:30 – 01:30 | 15:00 – 18:00 | Light volume scalping. |
| **TOXIC ROLLOVER HOUR** ⚠️ | **23:00 – 24:00** | **02:30 – 03:30** | **19:00 – 20:00** | **HARD ENGINE VETO ACTIVE**: Zero entries. Spread explodes by 20–50 pips. |

---

## 2. Broker Accounts & Switching Procedure

The system maintains headless authenticated sessions with LiteFinance MT5.

### Account Registry:
- **Demo Account**: `MT5-DEMO-ECN-91456523`
  - Server: `LiteFinance-MT5-Demo`
  - Active Balance: **$309.76 USD** (1:1000 Leverage)
  - Purpose: Live testing, feed validation, zero-risk trade execution.
- **Real Account**: `MT5-ECN-7535889`
  - Server: `LiteFinance-MT5-Live`
  - Leverage: `1:1000` (Swap-Free Islamic Account)
  - Verification: 100% Verified (Identity, Phone, Email, Proof of Address)
  - Behavior: When balance is $0.00, LiteFinance displays *"Your account is in read-only mode"*. As soon as the first deposit lands (e.g. $60.00), the broker automatically enables Full Market Trading.

### Mid-Day Switch Procedure (Demo $\leftrightarrow$ Real):
To switch accounts without taking the server down:
```bash
# On the VPS (takes <30 seconds):
/root/ict_sniper/venv/bin/python -c "
import asyncio
from playwright.async_api import async_playwright

async def switch_mode():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True, executable_path='/usr/bin/google-chrome', args=['--no-sandbox', '--proxy-server=socks5://127.0.0.1:10808'])
        ctx = await b.new_context(storage_state='/root/lf_session.json')
        page = await ctx.new_page()
        await page.goto('https://my.litefinance.org/trading', wait_until='domcontentloaded')
        await page.wait_for_timeout(3000)
        # Click user header and toggle Real/Demo
        await page.click('.header_user, .user_name')
        await page.wait_for_timeout(1000)
        await page.click('a:has-text(\"ACTIVATE REAL TRADING\"), a:has-text(\"ACTIVATE DEMO TRADING\")')
        await page.wait_for_timeout(4000)
        await ctx.storage_state(path='/root/lf_session.json')
        await b.close()
asyncio.run(switch_mode())
"
systemctl restart stratton-xau-live.service
```

---

## 3. Compounding Ladder & Sizing Tiers

The bot uses the empirical sizing ladder mathematically calibrated across 473 continuous trading days:

| Balance Tier | Base Lot Size | Titan Boost (1.65x) | Hard Stop Loss | Profit Target / Action |
| :--- | :--- | :--- | :--- | :--- |
| **\$50 – \$200** (Day 1) | **0.05 lots** | 0.08 lots | **-\$15.00 cap** | +$35 to +$50 spike harvest |
| **\$200 – \$400** (Day 1–2) | **0.10 lots** | 0.16 lots | **-\$25.00 cap** | +$50 to +$80 spike harvest |
| **\$400 – \$800** (Day 2) | **0.20 lots** | 0.33 lots | **-\$45.00 cap** | +$80 to +$150 spike harvest |
| **\$800 – \$1,500** (Day 2–3) | **0.40 lots** | 0.66 lots | **-\$80.00 cap** | +$150 to +$300 spike harvest |
| **\$1,500 – \$3,000** (Day 3) | **0.80 lots** | 1.32 lots | **-\$150.00 cap** | +$350 to +$700 spike harvest |
| **\$3,000+** (Sovereign Tier) | **`balance / 2000`** | up to 5.0 lots | Dynamic 15% tier stop | **+\$2,000 to +\$10,000+ per day** |

### Trailing Ratchet Mechanics:
1. **Breakeven Cushion (+1.5 ATR)**:
   - When trade moves $+1.5\text{ ATR}$ in favor, software SL is moved to entry $+0.20\text{ pts}$ ($2 pips).
   - The trade is now mathematically risk-free.
2. **Profit Lock (+2.5 ATR)**:
   - When trade hits $+2.5\text{ ATR}$, software SL is ratcheted up to $+1.5\text{ ATR}$.
   - Guarantees retained profit even if price flash-reverses.
3. **Daily Cash-Out Protocol**:
   - At the end of each trading day, the Stratton Vault allocates **30% of daily net profits** to the cash-out queue.
   - 70% remains in the account to compound exponentially.

---

## 4. Macro Sentinel & Live News Wire

### Active Economic Calendar Rules:
- Source: FairEconomy JSON (`ff_calendar_thisweek.json`).
- High-impact USD events monitored: CPI, PPI, NFP (Non-Farm Payrolls), FOMC Rate Decisions, Powell Speeches.
- **Freeze Window**: Exactly $\pm 8\text{ minutes}$ around the event timestamp. All new entries are blocked.

### Live Geopolitical News Taxonomy:
- Refresh Rate: **Every 90 seconds** across 6 specialized RSS streams.
- **Bullish Catalysts**: Tariffs, trade war, retaliatory duties, sanctions, Middle East escalations, Strait of Hormuz tensions, BRICS de-dollarization, central bank gold buying.
- **Bearish Catalysts**: Rate hike fears, hot CPI surprises, dollar surges, ceasefire declarations.
- **The Sovereign Titan Boost**: When technical setup grade is `A_plus_prime` and macro bias is `STRONG_BULL`, the engine triggers `macro_sovereign_titan` (1.65x lot boost + 1.8x TP expansion up to +8.0 ATR).

---

## 5. Autonomous LLM Doctor & Emergency Cheat-Sheet

The Doctor daemon runs as `stratton-auto-repair.service` using `Qwen2.5-1.5B` on `127.0.0.1:8080`.

### Key Commands:
```bash
# Check all 3 core services status
systemctl status stratton-xau-live.service stratton-auto-repair.service stratton-llm-critic.service --no-pager

# Restart trading engine cleanly
systemctl restart stratton-xau-live.service

# Inspect live broker execution logs
journalctl -u stratton-xau-live.service -n 50 -f

# Inspect Doctor diagnostic logs
journalctl -u stratton-auto-repair.service -n 50 -f

# Emergency Instant Flatten (Close all positions)
echo '{"action":"FLATTEN"}' > /root/ict_sniper/data/command.json
```
