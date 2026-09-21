# AGENT BRIEFING & SYSTEM STATE DOSSIER

> **CRITICAL DIRECTIVE FOR ANY AI AGENT OR QUANT ENGINEER ENTERING THIS CODEBASE:**  
> Read this single file before touching code, running commands, or diagnosing issues. It contains the exact live state, architecture layout, operational invariants, and empirical baselines of the Stratton Oakmont engine.

---

## 1. Executive Context & Mission

- **Repository**: `Emaddodin/Wall-Street-Nightmare`
- **Active Git Branch**: Strictly `production` (All work must remain on `production`; never branch or detach).
- **Local Root**: `/Users/mac/Desktop/TBT-Engine`
- **Live VPS Root**: `root@82.115.21.155:/root/ict_sniper` (SSH key pre-authenticated).
- **Core Mission**: High-Frequency XAUUSD (Gold) automated scalper executing micro-structure price action with sub-second execution, non-autoregressive neural validation (Laya System 1), macroeconomic regime classification (Politician Brain), and an autonomous local LLM self-healing doctor.

---

## 2. Live VPS Runtime State & Active Services

The VPS (`82.115.21.155`) runs 3 interdependent systemd services:

| Service Name | Command / Script | Role & Health |
| :--- | :--- | :--- |
| **`stratton-xau-live.service`** | `/root/ict_sniper/venv/bin/python -u run_xau_broker_live.py` | **Live Trading Scalper Engine**. Connects via headless Chromium Playwright gateway to LiteFinance MT5. |
| **`stratton-auto-repair.service`** | `/root/ict_sniper/venv/bin/python -u scalper/sentinel/llm_doctor.py` | **Autonomous LLM Self-Healing Doctor**. 24/7 watchdog that monitors quote stalls, process crashes, and CDP drops, triaging root cause with local LLM. |
| **`stratton-llm-critic.service`** | `llama-server -m models/Qwen2.5-1.5B-Instruct-Q4_K_M.gguf --port 8080` | **Local LLM Engine** (~50ms latency, runs offline on port `8080`). |

### Web Terminal Access:
- **Direct HTTP (Clean, zero SSL warnings)**: `http://82.115.21.155:8088/`
- **Secure HTTPS**: `https://82.115.21.155:8443/`

---

## 3. Current Broker Account Status

### A. Demo Account (CURRENTLY ACTIVE IN PRODUCTION):
- **Account Number**: `MT5-DEMO-ECN-91456523`
- **Current Balance**: **\$309.76 USD** (1:1000 Leverage)
- **Status**: Active live trading. Full order routing enabled.
- **Session File**: `/root/lf_session.json` on VPS.

### B. Real Account (PREPARED & VERIFIED):
- **Account Number**: `MT5-ECN-7535889`
- **Server**: `LiteFinance-MT5-Live` (Swap-Free Islamic Account)
- **Verification Status**: 100% Verified (Identity, Phone, Email, Address approved).
- **Behavior Note**: When balance is \$0.00, broker displays *"Your account is in read-only mode"*. As soon as the user deposits (e.g. \$60), Full Market Trading is instantly unlocked.
- **Mid-Day Switch Procedure**: Documented in [`NOTES.md`](NOTES.md#2-broker-accounts--switching-procedure) (takes $<30$ seconds via headless session switch).

---

## 4. Architectural Map: Where Everything Lives

```
/Users/mac/Desktop/TBT-Engine/
├── run_xau_broker_live.py         # Production execution loop (Apex Trinity + Trailing Ratchet)
├── bark_integration.py            # Native iOS push notification integration
├── README.md                      # Public documentation & architecture overview
├── NOTES.md                       # Operational manual, session schedule & risk parameters
├── AGENT_BRIEFING.md              # THIS FILE: Single orientation dossier for AI agents
├── engine/
│   └── litefinance_gateway.py    # Playwright headless browser gateway (DOM mutation observer, <5ms order click)
├── scalper/
│   ├── app/
│   │   ├── app.py                 # Live web dashboard backend & API (/api/hft)
│   │   └── static/                # PWA icons and web assets
│   ├── brain/
│   │   ├── laya_oracle.py         # Laya System 1 non-autoregressive ModernBERT decision model
│   │   ├── politician_brain.py    # Unified Politician Sentinel (Sword/Shield + 6 RSS feeds on 90s cycle)
│   │   ├── ict_rag.py             # 288-concept institutional ICT knowledge index
│   │   └── regime_prior_engine.py # Empirical 473-day macro regime priors
│   ├── sentinel/
│   │   └── llm_doctor.py          # Autonomous LLM Self-Healing Doctor (triage & auto-remediation)
│   ├── pa/
│   │   ├── candles.py             # Candle geometry, wick-to-range ratios
│   │   ├── levels.py              # Dynamic Support/Resistance breakout mapping
│   │   └── ict.py                 # Fair Value Gap (FVG) and Order Block detection
│   └── strategies/
│       └── apex_trinity.py        # 5m Breakout Retest + Silver Bullet + London Turtle Soup
└── data/
    ├── regime_trade_journal_full.csv     # 6,613-trade chronological backtest journal
    ├── regime_analytics_summary_full.json# 473-day mathematical analytics summary
    └── state/
        ├── hft.json               # Real-time state consumed by dashboard
        └── doctor_telemetry.json  # Live Doctor health telemetry
```

---

## 5. Risk Rules & Strategy Invariants (NEVER BREAK THESE)

1. **Hard Stop Loss Ceiling**:
   - For balances $\le \$200$, the hard software stop loss is strictly **-\$15.00** per trade.
   - Never allow any trade to run unbounded without a stop.
2. **Apex Sovereign Trailing Ratchet**:
   - **Ratchet 1 (Breakeven Lock)**: When trade hits $+1.5\text{ ATR}$, move SL to entry $+0.20\text{ pts}$ (risk-free).
   - **Ratchet 2 (Profit Lock)**: When trade hits $+2.5\text{ ATR}$, ratchet SL to $+1.5\text{ ATR}$.
   - **Spike Harvest**: Automates rapid profit extraction at $+\$50\text{ to }+\$100$.
3. **Macro Sentinel Shields**:
   - **Calendar Freeze**: Complete entry freeze $\pm 8\text{ minutes}$ around high-impact USD economic events (CPI, NFP, FOMC).
   - **Toxic Rollover Veto**: Complete entry freeze during **23:00 – 24:00 UTC** (spread widening dead-zone).
   - **Counter-Trend Shock Veto**: Blocks buys if breaking news classifies as `STRONG_BEAR`.
4. **The Sovereign Titan Boost ("The Sword")**:
   - When technical setup is $A^+$ and macro bias is `STRONG_BULL` (Trump tariffs, safe-haven demand), lot sizing scales by **$1.65\text{x}$** and TP target expands to **$+5.0\text{ to }+8.0\text{ ATR}$**.

---

## 6. The 473-Day Empirical Backtest: Crucial Nuance on Day 1 Sizing

Audited from **January 21, 2025 (Trump Day 1) to September 20, 2026** starting from **\$60.00**:
- Total Wealth Realized: **\$22,171,872.31**
- Banked Withdrawals: **\$15,555,395.41**
- Profit Factor: **5.42**
- Win Rate: **79.0%** (Breakout Retest WR: **83.7%**)

### ⚠️ Critical Sizing Reality:
- **Day 1 with \$60**: Starts with $0.05\text{ lots}$ (\$5 margin). Takes ~13 trades $\rightarrow$ nets **+\$112 to +\$250** $\rightarrow$ ends Day 1 around **\$172 to \$340**.
- **Day 2**: Sizing expands to 0.20–0.40 lots $\rightarrow$ ends around **\$2,450**.
- **Day 3**: Crosses the **\$3,000+** Sovereign Tier.
- **Day 4**: Sizing expands to 1.50–2.50 lots $\rightarrow$ **+\$20,430 single-day profit**.
- **Rule for Agents**: Never promise \$10,000 on Day 1 starting from \$60! Margin physics require building the equity cushion to \$3,000 before high-lot compounding can unleash \$10k+ days without blowup risk.

---

## 7. Command & Deployment Cheat-Sheet

```bash
# 1. Check all live services on VPS
ssh root@82.115.21.155 "systemctl status stratton-xau-live.service stratton-auto-repair.service stratton-llm-critic.service --no-pager"

# 2. Check live dashboard API state
curl -s http://82.115.21.155:8088/api/hft | python3 -m json.tool

# 3. Deploy a modified python file to VPS and restart live engine
scp scalper/brain/politician_brain.py root@82.115.21.155:/root/ict_sniper/scalper/brain/
ssh root@82.115.21.155 "systemctl restart stratton-xau-live.service"

# 4. Tail real-time execution logs
ssh root@82.115.21.155 "journalctl -u stratton-xau-live.service -n 50 -f"

# 5. Tail Doctor self-healing logs
ssh root@82.115.21.155 "journalctl -u stratton-auto-repair.service -n 50 -f"

# 6. Emergency Instant Flatten (Close all positions via command file)
ssh root@82.115.21.155 "echo '{\"action\":\"FLATTEN\"}' > /root/ict_sniper/data/command.json"
```

---

*Keep this briefing file updated whenever you modify core parameters, deploy new services, or alter strategy geometry.*
