# Stratton Oakmont — Institutional XAUUSD Scalping Engine

High-frequency, institutional-grade automated trading engine executing real-time Gold (XAUUSD) micro-structure scalping with sub-second execution, non-autoregressive neural validation, and intraday compounding.

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                           LIVE XAUUSD SCALPER ENGINE                             │
│                            (run_xau_broker_live.py)                              │
└────────────────────────┬────────────────────────────────┬────────────────────────┘
                         │                                │
                         ▼                                ▼
            ┌─────────────────────────┐      ┌─────────────────────────┐
            │   Wednesday Geometry    │      │    Laya System 1        │
            │   (5m Break + 1m Wick)  │      │    Decision Oracle      │
            └────────────┬────────────┘      └────────────┬────────────┘
                         │                                │
                         ├────────────────────────────────┤
                         ▼                                ▼
            ┌─────────────────────────┐      ┌─────────────────────────┐
            │  Macro News Watchdog    │      │   Semantic ICT RAG      │
            │  (CPI/FOMC/Headline)    │      │   (288 Concept Library) │
            └─────────────────────────┘      └─────────────────────────┘
                         │                                │
                         └────────────────┬───────────────┘
                                          ▼
                            ┌───────────────────────────┐
                            │   Calibrated Confidence   │
                            │   - Trap Veto (<0.50)     │
                            │   - Standard (0.50 - 0.80)│
                            │   - A+ Boost 1.5x (>0.85) │
                            └─────────────┬─────────────┘
                                          ▼
                            ┌───────────────────────────┐
                            │  LiteFinance MT5 Demo     │
                            │  Atomic Execution (<5ms)  │
                            └───────────────────────────┘
```

1. **Execution Core (`run_xau_broker_live.py`)**:
   - Institutional 5m Breakout -> 1m Retest -> Rejection wick geometry.
   - Dynamic tier progression ($100 -> $300 -> $500 -> $1,000+).
   - Hard -$15 risk ceiling (15% capital preservation stop) & rapid profit spike harvest (+50% per tier).

2. **Laya System 1 Decision Oracle (`scalper/brain/laya_oracle.py`)**:
   - Powered by `convaiinnovations/laya` (non-autoregressive ModernBERT/mmBERT).
   - Evaluates trade setups in a single forward pass (~28ms) with mathematically calibrated probabilities trained via RLCD.
   - **Trap Filter**: Automatically vetoes institutional fakeouts and liquidity traps (`trap_prob > 0.60`).
   - **Compounding Boost**: Scales lot sizes by `1.25x - 1.50x` on A+ confluence setups.
   - **Momentum Exhaustion**: Automatically harvests profit spikes before price retraces.

3. **288-Concept ICT Knowledge Library RAG (`scalper/brain/ict_rag.py`)**:
   - In-memory index of 288 institutional ICT concepts across 33 categories (FVGs, Order Blocks, Liquidity Sweeps, Killzones, Judas Swings, AMD Cycles, Silver Bullet).
   - Matches real-time market geometry to playbook rules in `< 1ms`.

4. **Macro & News Watchdog (`scalper/brain/macro_watchdog.py`)**:
   - Real-time monitoring of US economic calendar (CPI, NFP, FOMC rate decisions) and geopolitical headlines.
   - Automatically halts new entries 5 minutes before and after red-folder news to protect against spread blowouts.

5. **Headless Execution Gateway (`engine/litefinance_gateway.py`)**:
   - Headless Playwright Chrome session running directly on the Linux VPS.
   - Native Chrome V8 `MutationObserver` on DOM bid/ask elements for sub-millisecond quote streaming (<1ms).
   - Atomic order execution (`__executeFastMarketOrder`) with sub-5ms button clicks.

6. **Wall Street Mobile Terminal (`scalper/app/app.py`)**:
   - Live mobile dashboard running on HTTP (`:8088`) and HTTPS (`:8443`).
   - Tehran, New York, London, and UTC real-time clocks with active ICT session detection.
   - Real-time PnL, pip tracks, Laya System 1 telemetry, and one-tap emergency flatten.

7. **Native iOS Push Notifications (`bark_integration.py`)**:
   - Direct APNs push notifications via Bark with Wall Street gold lockscreen icons and custom sounds (`minuet.caf`, `alarm.caf`).

---

## Directory Structure

```
├── run_xau_broker_live.py       # Main production execution engine
├── bark_integration.py          # Native iOS push notification integration
├── requirements.txt             # Production Python dependencies
├── deploy/                      # Systemd production service definitions
│   └── stratton-xau-live.service
├── engine/                      # Execution Gateway layer
│   ├── __init__.py
│   └── litefinance_gateway.py  # Playwright headless LiteFinance MT5 gateway
├── scalper/
│   ├── app/                     # Wall Street mobile HFT dashboard & static icons
│   │   ├── app.py
│   │   └── static/
│   ├── brain/                   # Laya Oracle, ICT RAG, and Macro Watchdog
│   │   ├── __init__.py
│   │   ├── laya_oracle.py
│   │   ├── ict_rag.py
│   │   └── macro_watchdog.py
│   ├── pa/                      # Price action levels and candle math
│   │   ├── __init__.py
│   │   ├── candles.py
│   │   ├── ict.py
│   │   └── levels.py
│   ├── learn/                   # 288-concept institutional ICT knowledge library
│   │   └── ict-knowledge-library/
│   └── tests/                   # Automated unit test suite
│       ├── __init__.py
│       └── test_laya_system.py
└── data/                        # Persistent storage (vault, tiers, HFT state)
    ├── stratton_vault.json
    └── state/
```

---

## Quickstart

### 1. Installation
```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. Run Test Suite
```bash
python -m unittest scalper.tests.test_laya_system
```

### 3. Start Live Trading Engine
```bash
python run_xau_broker_live.py
```

### 4. Access Live Web Terminal
- **Direct HTTP**: `http://<SERVER_IP>:8088/`
- **Secure HTTPS**: `https://<SERVER_IP>:8443/`
