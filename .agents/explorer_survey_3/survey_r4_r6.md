# Survey Report: Explorer 3 — Local LLM Critic (R4) & VPS Deployment (R6)
**Project**: Architecture B Evolution ("Wall-Street-Nightmare")  
**Target Environment**: Production VPS `82.115.21.155` (`/root/ict_sniper`) & Local Repository (`/Users/mac/Desktop/TBT-Engine`)  
**Date**: 2026-09-16  
**Status**: Investigation Complete  

---

## 1. Executive Summary

This investigation surveys the architecture, existing codebase, hardware resources, network access, and deployment pipeline for:
1. **Requirement R4 — Asynchronous Local LLM Strategic Critic**: An isolated `llama.cpp` / `llama-server` runtime and Python client executing a sub-3B quantized model (`Q4_K_M`) under strict constraints (`-t 2`, `-ngl 0`, `--mlock`, `--ctx-size 2048`), completely decoupled from the sub-50ms execution path, analyzing rolling 20-trade JSON summaries and dynamically outputting risk multipliers.
2. **Requirement R6 — Production VPS Deployment (`82.115.21.155`)**: Deployment of the complete Architecture B engine to `/root/ict_sniper` via SSH, configuring `stratton-llm-critic.service` under `systemd`, verifying Hyperliquid L2 WebSocket streaming and telemetry, and strictly maintaining system-wide resident memory consumption below **2.5 GB**.

### Key Investigation Discoveries:
1. **SSH & VPS Access Verified**: Root SSH access to `82.115.21.155` via alias `stratton` (`~/.ssh/id_rsa`, port 22) is active, stable, and authenticated with zero errors.
2. **Current VPS Resource State**:
   - Host: Ubuntu 24.04 LTS (Kernel 6.8.0-111-generic, x86_64, GLIBC 2.39).
   - CPU: 2 vCPUs (QEMU Virtual CPU @ 2.0 GHz).
   - RAM: 3.8 GiB total (3915 MB). Currently **611 MB used**, **3.3 GB available**, **0 Swap**.
   - Storage: 58 GB disk, 48 GB available (17% used).
   - Port 8080 is completely free and unallocated.
3. **Crucial Kernel Memory Lock Discovery**:
   - System default locked-in-memory space (`MEMLOCK`) on the VPS is capped at **512 MB** (`ulimit -l` = 501232 KB).
   - Starting `llama-server` with `--mlock` on an 800MB–1GB model will **FAIL with `Cannot allocate memory`** unless `LimitMEMLOCK=infinity` is explicitly configured in the systemd service unit.
4. **RAM Budget & Sub-3B Model Sizing**:
   - Base VPS OS + existing running services consume **611 MB**.
   - To strictly meet the **$\le 2.5\text{ GB}$ (2560 MB) total system memory limit**, `llama-server` resident memory (RSS) must **not exceed $\approx 1.8\text{ GB}$**.
   - `Llama-3.2-3B-Instruct-Q4_K_M.gguf` (1.93 GB file size, $\approx 2.1\text{ GB}$ RSS) would push total system memory to **$\approx 2.7\text{ GB}$**, breaching the threshold!
   - `Qwen2.5-1.5B-Instruct-Q4_K_M.gguf` (940 MB file size, $\approx 1.1\text{ GB}$ RSS with `--mlock` and 2048 ctx) results in **$\approx 1.7\text{ GB}$ total system RAM**, safely leaving an **850 MB buffer**.
   - `Llama-3.2-1B-Instruct-Q4_K_M.gguf` (770 MB file size, $\approx 0.9\text{ GB}$ RSS) results in **$\approx 1.5\text{ GB}$ total system RAM**, leaving a **1.0 GB buffer**.
5. **Decoupled Asynchronous Execution**:
   - Sub-3B model inference on 2 CPU cores requires **1.5–4.5 seconds** per critique.
   - The HFT execution path (`_on_book_update` in `quant/hft/engine.py`) operates at sub-millisecond to sub-50ms latency.
   - Decoupling is achieved by running the Critic in an independent background worker (`asyncio.create_task` or daemon thread) that polls `llama-server` over local HTTP asynchronously. The engine reads the cached risk multiplier synchronously in **$\mathcal{O}(1)$ time ($<1\ \mu\text{s}$)** without awaiting LLM generation.
6. **Existing Deployment Scripts**:
   - `tools/sync.sh` contains stale configuration pointing to the defunct server (`tbt:/home/tbt/bot/`). A modernized `tools/sync.sh` targeting `stratton:/root/ict_sniper/` is needed.
   - Existing active systemd units on the VPS: `tbt-hl-hft.service` (paper execution engine), `tbt-hl-app.service` (phone dashboard on `:8443`), and `tbt-hft-guard.timer` (autonomous watchdog).
   - Legacy unit `kronos-forecast.service` is currently stopped/inactive.

---

## 2. Target VPS Environment Profile (`82.115.21.155`)

### 2.1 Hardware and OS Specifications
| Parameter | Specification | Verification Command & Output |
|---|---|---|
| **Host IP** | `82.115.21.155` | `ssh stratton` (port 22) |
| **Hostname** | `srv6585509075` | Linux 6.8.0-111-generic x86_64 |
| **OS Distribution** | Ubuntu 24.04 LTS | Ubuntu GLIBC 2.39-0ubuntu8.9 |
| **vCPU Cores** | 2 cores | `QEMU Virtual CPU version 2.5+ @ 2.0GHz` |
| **Physical RAM** | 3.8 GiB (3,915 MB) | `free -m`: 611 MB used, 3323 MB buff/cache |
| **Swap Space** | 0 B (Disabled) | `free -m`: Swap total 0 MB |
| **Disk Space** | 58 GB total / 48 GB free (17% used) | `df -h /` |
| **Available Ports** | Port 8080 is verified FREE | `ss -tulnp` shows only 22, 53, 8443 |

### 2.2 SSH Access & Authentication Configuration
The local SSH configuration at `~/.ssh/config` defines:
```ssh
Host stratton
    HostName 82.115.21.155
    User root
    Port 22
    IdentityFile ~/.ssh/id_rsa
    IdentitiesOnly yes
    ServerAliveInterval 30
    ServerAliveCountMax 4
```
Direct connection test executed:
```bash
ssh -o BatchMode=yes -o ConnectTimeout=5 stratton "uptime"
# Output: 19:58:43 up 3 days, 4:53, 1 user, load average: 0.00, 0.00, 0.00
```
Authentication operates exclusively via RSA key; `sshd_config` enforces `PasswordAuthentication no`.

### 2.3 Current Memory & Process Breakdown on VPS
Detailed process inspection (`ps aux --sort=-%mem`) reveals:

| Process / Command | PID | RSS (Memory) | % Memory | Role |
|---|---|---|---|---|
| `python -m quant.hft.run_hft ...` | 185050 | 143.9 MB | 3.5% | HFT Quant Engine (Paper Mode) |
| `/usr/lib/systemd/systemd-journald` | 3909 | 117.9 MB | 2.9% | System log daemon |
| `fail2ban-server` | 35896 | 45.9 MB | 1.1% | SSH & App brute-force jail |
| `multipathd` | 9343 | 27.3 MB | 0.6% | Storage daemon |
| `unattended-upgrade-shutdown` | 898 | 23.1 MB | 0.5% | OS updater |
| `python -u app/app.py` | 185046 | 22.7 MB | 0.5% | Web dashboard on port 8443 |
| Systemd & kernel daemons | various | ~230 MB | ~5.8% | Core OS runtime |
| **Total Resident Memory Used** | — | **~611 MB** | **~15.6%** | **Baseline prior to llama-server** |

---

## 3. R4: Asynchronous Local LLM Strategic Critic

### 3.1 Model Selection & Quantization Analysis
Requirement R4 mandates:
> "Isolated `llama.cpp` client and service running a sub-3B quantized model (Q4_K_M) on a constrained 2-core / 4GB RAM VPS with strict parameters (`-t 2`, `-ngl 0`, `--mlock`, `--ctx-size 2048`)."

We evaluated candidate sub-3B GGUF models from Hugging Face for reasoning quality, JSON adherence, disk size, and memory footprint:

| Candidate Model | Parameter Count | Quantization | GGUF File Size | Est. RSS with `--mlock` & 2048 Ctx | Total System Memory (with OS 611MB) | Compliance with $\le 2.5\text{ GB}$ Ceiling |
|---|---|---|---|---|---|---|
| **Qwen2.5-1.5B-Instruct** | **1.54B** | **Q4_K_M** | **940.4 MB** | **$\approx 1,120\text{ MB}$** | **$\approx 1,731\text{ MB}$ (1.7 GB)** | **PASS** (829 MB safety margin) |
| **Llama-3.2-1B-Instruct** | **1.23B** | **Q4_K_M** | **770.3 MB** | **$\approx 920\text{ MB}$** | **$\approx 1,531\text{ MB}$ (1.5 GB)** | **PASS** (1,029 MB safety margin) |
| **SmolLM2-1.7B-Instruct** | **1.71B** | **Q4_K_M** | **1,060 MB** | **$\approx 1,280\text{ MB}$** | **$\approx 1,891\text{ MB}$ (1.9 GB)** | **PASS** (669 MB safety margin) |
| *Llama-3.2-3B-Instruct* | 3.21B | Q4_K_M | 1,925.8 MB | $\approx 2,150\text{ MB}$ | $\approx 2,761\text{ MB}$ (2.76 GB) | **FAIL** (Exceeds 2.5 GB limit by 261 MB!) |

#### Strategic Model Decision:
1. **Primary Recommendation**: `Qwen2.5-1.5B-Instruct-Q4_K_M.gguf`.
   - Outstanding structured JSON generation and mathematical reasoning in sub-3B class.
   - Consumes only 940 MB on disk and $\approx 1.1\text{ GB}$ in memory.
   - Leaves a healthy **829 MB headroom** below the 2.5 GB constraint.
2. **Alternative / Fallback**: `Llama-3.2-1B-Instruct-Q4_K_M.gguf`.
   - Even smaller footprint (770 MB disk, $\approx 920\text{ MB}$ RSS).
   - Ideal if the VPS experiences unexpected OS-level memory spikes.

### 3.2 llama-server Installation & Binary Compatibility
- **Source**: Official release `llama-b11009-bin-ubuntu-x64.tar.gz` from `ggml-org/llama.cpp`.
- **GLIBC Compatibility**: Compiled for Ubuntu x64 with GLIBC $\ge 2.35$; VPS operates on Ubuntu GLIBC 2.39. Binary compatibility is 100%.
- **Contents**: Includes static/dynamic `llama-server` binary and `libllama-server-impl.so`.
- **Target Location**: `/root/ict_sniper/llama.cpp/` or `/usr/local/bin/llama-server`.
- **Direct Download Verification**:
  ```bash
  curl -sL https://github.com/ggml-org/llama.cpp/releases/download/b11009/llama-b11009-bin-ubuntu-x64.tar.gz | tar -tz | grep llama-server
  # Output: llama-b11009/llama-server
  ```

### 3.3 Strict Execution Parameters & System Limits
The server must be launched with:
```bash
/root/ict_sniper/llama.cpp/llama-server \
    -m /root/ict_sniper/models/Qwen2.5-1.5B-Instruct-Q4_K_M.gguf \
    --host 127.0.0.1 \
    --port 8080 \
    -t 2 \
    -ngl 0 \
    --mlock \
    --ctx-size 2048
```
- `-t 2`: Dedicates both physical CPU cores to inference computation.
- `-ngl 0`: Forces 0 GPU offload (pure CPU inference on headless VPS).
- `--mlock`: Locks model memory pages into physical RAM to prevent OS swapping.
- `--ctx-size 2048`: Restricts context buffer to 2048 tokens, bounding the KV cache allocation to $<150\text{ MB}$.
- `--host 127.0.0.1 --port 8080`: Binds exclusively to localhost loopback for internal engine access.

#### Critical Limit Requirement: `LimitMEMLOCK=infinity`
Investigation revealed that Linux defaults to `512 MB` of locked memory for unprivileged/standard systemd services. If `llama-server` attempts to allocate 940 MB with `--mlock` under this limit, `mlock()` fails with `ENOMEM`. The systemd service unit must include:
```ini
LimitMEMLOCK=infinity
```

### 3.4 Decoupled Asynchronous Execution Path
On a 2-core CPU, generating a 200-token critique takes 1.5–4.5 seconds.
In `quant/hft/engine.py`, `_on_book_update` executes on every WebSocket tick (sub-50ms requirement).
Any synchronous call to `llama-server` in the event loop would block order book processing, causing WebSocket buffer overflow and missed fills.

#### Decoupled Architecture:
```
┌────────────────────────────────────────────────────────────────────────┐
│                        Critical HFT Fast Path                          │
│                                                                        │
│  L2 WS Tick ──► OrderBook ──► OFI/Hawkes ──► ML Filter ──► Kelly Sizing │
│                                                                 │      │
│                                                  Reads cached   │      │
│                                                  multiplier     ▼      │
│                                                  in <1µs: [0.85x]     │
│                                                                 │      │
│                                                              Order     │
└────────────────────────────────────────────────────────────────────────┘
                                    ▲
                                    │ Updates thread-safe
                                    │ state in memory
┌────────────────────────────────────────────────────────────────────────┐
│                     Asynchronous LLM Critic Loop                       │
│                                                                        │
│   Trade Closed Event or Periodic Trigger (every 5-15 min)             │
│                              │                                         │
│                              ▼                                         │
│   Build Rolling 20-Trade Summary JSON (from `data/logs/trades.jsonl`)   │
│                              │                                         │
│                              ▼                                         │
│   POST http://127.0.0.1:8080/completion (in asyncio.create_task)      │
│                              │                                         │
│                              ▼  (Awaits 2-4 seconds in background)     │
│   Parse JSON: {"risk_multiplier": 0.85, "regime": "HIGH_VOLATILITY"}   │
│                              │                                         │
│                              ▼                                         │
│   Update Cached Multiplier: `critic.current_risk_multiplier = 0.85`    │
└────────────────────────────────────────────────────────────────────────┘
```

### 3.5 Rolling 20-Trade Critique & Dynamic Risk Multipliers

#### 1. Input Rolling Trade Summary Schema
The critic extracts the last 20 closed trade records from `data/logs/trades.jsonl`:
```json
{
  "window_size": 20,
  "timestamp": 1789589189,
  "metrics": {
    "win_rate": 0.55,
    "profit_factor": 1.42,
    "realized_pnl_usdt": 18.50,
    "consecutive_losses": 2,
    "avg_win_usdt": 3.80,
    "avg_loss_usdt": -2.10,
    "avg_duration_sec": 42.5
  },
  "exit_distribution": {
    "chandelier_ratchet": 8,
    "tp_scale": 3,
    "hard_stop": 7,
    "liquidation_guard": 0
  },
  "current_market_regime": {
    "garch_sigma": 0.002596,
    "ofi_mean": -0.003,
    "hawkes_ratio": 0.642,
    "spread_bps": 0.13
  },
  "recent_trades": [
    {"sym": "BTC", "side": "LONG", "pnl": 3.2, "ret_bps": 12.5, "exit_reason": "chandelier_ratchet", "duration_s": 35},
    {"sym": "BTC", "side": "SHORT", "pnl": -2.1, "ret_bps": -8.2, "exit_reason": "hard_stop", "duration_s": 15}
  ]
}
```

#### 2. LLM Strategic Critic Prompt & Structured Output Schema
The prompt instructs the sub-3B model to act as the Senior Risk Director and output strictly valid JSON:
```
System: You are Stratton Oakmont's Autonomous Strategic Risk Critic.
Analyze the rolling 20-trade execution summary and current microstructure regime.
Evaluate whether current strategy performance indicates regime degradation, adverse selection, or high-conviction alpha.
You must respond ONLY with a JSON object matching this schema:
{
  "regime_assessment": "TRENDING" | "CHOP_HIGH_NOISE" | "VOLATILITY_EXPANSION" | "LIQUIDITY_DROUGHT",
  "performance_verdict": "NOMINAL" | "DEGRADED" | "SUPERIOR",
  "risk_multiplier": <float between 0.00 and 1.50>,
  "confidence_floor_adj": <float between -0.05 and +0.10>,
  "rationale": "<brief 1-sentence analysis>"
}
```

#### 3. Dynamic Scaling Logic in Engine
The execution engine wires `critic.risk_multiplier` into the existing `DayPlanner` and `FractionalKelly` sizing pipeline:
$$\text{Effective Kelly Fraction} = \text{Base Kelly} \times \text{DayPlan Multiplier} \times \text{Critic Multiplier}$$
- If consecutive stop-outs occur in choppy order flow, the LLM outputs `risk_multiplier: 0.50` (or `0.25`), halving exposure to conserve capital.
- If alpha entries achieve high profit factors with tight trailing exits, the LLM outputs `risk_multiplier: 1.15` to capitalize on favorable regime drift.
- If `llama-server` is unreachable or times out, the client automatically defaults to `risk_multiplier: 1.00` (fail-safe neutral) with zero engine disruption.

---

## 4. R6: Production VPS Deployment Details

### 4.1 Remote File Layout at `/root/ict_sniper`
The remote filesystem on `82.115.21.155` is organized as follows:
```
/root/ict_sniper/
├── .env                                 # Environment variables (NTFY_TOPIC, API keys)
├── PAPER_ONLY                           # Hardware safety lock file
├── venv/                                # Python 3.12 virtual environment
├── llama.cpp/                           # llama-server binary & shared libraries
│   ├── llama-server
│   └── libllama-server-impl.so
├── models/                              # Quantized GGUF weights
│   └── Qwen2.5-1.5B-Instruct-Q4_K_M.gguf
├── quant/
│   └── hft/
│       ├── alpha/                       # Hawkes, OFI, SignalEngine
│       ├── critic/                      # LLMCriticClient & trade summarizer (R4)
│       ├── data_feed/                   # Hyperliquid WebSocket client & OrderBook
│       ├── execution/                   # Avellaneda-Stoikov & order splitter
│       ├── exits/                       # Chandelier trailing ratchet
│       ├── models/                      # Direction model & GARCH volatility
│       ├── risk/                        # Fractional Kelly & DayPlanner
│       ├── engine.py                    # Master event loop orchestrator
│       ├── guard.py                     # Health & memory watchdog
│       ├── monitor.py                   # Live telemetry & ntfy.sh alerts
│       └── run_hft.py                   # Service entry point
├── scalper/
│   └── app/                             # Web application (phone dashboard on :8443)
├── data/
│   ├── logs/
│   │   └── trades.jsonl                 # Rolling closed trades record
│   └── state/
│       ├── hft.json                     # Real-time L2 telemetry
│       └── guard_history.json           # Guard watchdog historical status
└── tools/                               # Diagnostic & verification utilities
```

### 4.2 Systemd Unit: `stratton-llm-critic.service`
Specification for `/etc/systemd/system/stratton-llm-critic.service`:
```ini
[Unit]
Description=Stratton Oakmont Local LLM Strategic Critic (llama-server)
After=network.target
Wants=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/ict_sniper
LimitMEMLOCK=infinity
ExecStart=/root/ict_sniper/llama.cpp/llama-server \
    -m /root/ict_sniper/models/Qwen2.5-1.5B-Instruct-Q4_K_M.gguf \
    --host 127.0.0.1 \
    --port 8080 \
    -t 2 \
    -ngl 0 \
    --mlock \
    --ctx-size 2048
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=stratton-llm-critic

[Install]
WantedBy=multi-user.target
```

### 4.3 Systemd Unit: `tbt-hl-hft.service`
Existing unit on VPS (`/etc/systemd/system/tbt-hl-hft.service`), updated to depend on the critic:
```ini
[Unit]
Description=TBT HFT Quant Engine (Paper)
After=network-online.target stratton-llm-critic.service
Wants=network-online.target stratton-llm-critic.service

[Service]
User=root
WorkingDirectory=/root/ict_sniper
EnvironmentFile=/root/ict_sniper/.env
Environment=ICT_PAPER_ONLY=1
ExecStart=/root/ict_sniper/venv/bin/python -u -m quant.hft.run_hft --symbols BTC --balance 65 --model-path quant/hft/models/direction_model.cbm
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### 4.4 Hyperliquid L2 WebSocket & Telemetry Stream
- WebSocket connection to `wss://api.hyperliquid.xyz/ws` is managed by `quant/hft/data_feed/ws_client.py`.
- Verified live on VPS: Process `185050` maintains active subscriptions for `BTC` `l2Book` and `trades`.
- Emits real-time state updates to `/root/ict_sniper/data/state/hft.json` once per second:
  - `mid_price`, `best_bid`, `best_ask`, `spread_bps`
  - `hawkes_buy`, `hawkes_sell`, `hawkes_ratio`
  - `ofi_mean`, `ofi_levels` (5-level array)
  - `garch_sigma`, `dynamic_leverage`, `kelly_fraction`
  - `catboost_confidence`, `catboost_direction`
  - `killzone` status and active trading session quota

### 4.5 Autonomous Guard & RAM Watchdog (`quant/hft/guard.py`)
`tbt-hft-guard.timer` executes `quant/hft/guard.py` every 2 minutes.
Currently, `guard.py` checks service status, book freshness ($<45\text{s}$), venue reachability, and disk usage ($<92\%$).
To strictly satisfy R6 acceptance criteria, `guard.py` will be enhanced to:
1. Ping `http://127.0.0.1:8080/health` to verify `llama-server` is responding.
2. Read `/proc/meminfo` to calculate total system resident memory:
   $$\text{Used Memory} = \text{MemTotal} - \text{MemAvailable}$$
3. Trigger an autonomous alert and self-healing action if `Used Memory` breaches **2.4 GB** (approaching the 2.5 GB ceiling).

---

## 5. Deployment Mechanism & Tooling

### 5.1 Stale Script Audit
Inspection of `tools/sync.sh` revealed:
```bash
# Old target:
"${RSYNC[@]}" ./ tbt:/home/tbt/bot/
```
The server `tbt` (`62.60.198.135:2222`) is dead. The script must be updated to target `stratton:/root/ict_sniper/`.

### 5.2 Synchronizing to Production VPS
The updated synchronization command:
```bash
rsync -avz --delete \
    --exclude '.git' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.env' \
    --exclude '.DS_Store' \
    --exclude 'data/' \
    --exclude 'logs/' \
    --exclude 'venv/' \
    --exclude 'kronos_venv/' \
    --exclude '.agents/' \
    --exclude 'models/*.gguf' \
    ./ stratton:/root/ict_sniper/
```
Data state (`data/`) and virtual environments (`venv/`) are excluded to prevent overwriting live production state.

---

## 6. Architecture B Evolution Roadmap for R4 & R6

### Phase 1: Local LLM Critic Module Implementation
1. Create `quant/hft/critic/__init__.py` and `quant/hft/critic/client.py`:
   - Non-blocking async client querying `http://127.0.0.1:8080/completion`.
   - Timeout handling (default 8.0s), exponential backoff, and fail-safe fallback (`risk_multiplier = 1.0`).
2. Create `quant/hft/critic/summarizer.py`:
   - Reads last 20 records from `trades.jsonl`.
   - Calculates rolling win rate, profit factor, drawdown, and regime context.
   - Formats structured prompt for `llama-server`.
3. Integrate Critic into `quant/hft/engine.py`:
   - Initialize `self.critic = LLMCriticClient()`.
   - Start background task `self.critic.start()`.
   - Modulate Kelly fraction by `self.critic.current_risk_multiplier`.

### Phase 2: Unit Testing Suite
1. Build `tests/test_critic.py`:
   - Mock HTTP server simulating `llama-server` responses.
   - Verify prompt construction from 20-trade JSON summaries.
   - Test JSON parsing, malformed response handling, timeout fallback, and risk multiplier clamping ($0.0 \le m \le 1.5$).
   - Test sub-microsecond latency of synchronous multiplier reads.
2. Verify legacy suite: `pytest tests/test_filter.py` passes with zero regressions.

### Phase 3: VPS Installation & Model Deployment
1. On VPS `82.115.21.155`:
   - Download `llama-b11009-bin-ubuntu-x64.tar.gz` and extract `llama-server` to `/root/ict_sniper/llama.cpp/`.
   - Create `/root/ict_sniper/models/` directory.
   - Download `Qwen2.5-1.5B-Instruct-Q4_K_M.gguf` (940 MB) via `curl -L` from Hugging Face.
2. Install systemd service `/etc/systemd/system/stratton-llm-critic.service` with `LimitMEMLOCK=infinity`.
3. Enable and start service:
   ```bash
   systemctl daemon-reload
   systemctl enable --now stratton-llm-critic.service
   ```
4. Verify `/health` endpoint and measure memory usage:
   ```bash
   curl http://127.0.0.1:8080/health
   free -m
   ```
   Ensure total system resident memory does not exceed **2.5 GB** (projected: $\approx 1.7\text{ GB}$).

### Phase 4: Engine Deployment & Live Verification
1. Sync updated `quant/` codebase to `/root/ict_sniper/quant/`.
2. Update `/root/ict_sniper/quant/hft/guard.py` with critic health check and RAM ceiling guard.
3. Restart `tbt-hl-hft.service` and monitor telemetry:
   ```bash
   systemctl restart tbt-hl-hft.service
   journalctl -u tbt-hl-hft.service -f
   ```
4. Verify live updates in `/root/ict_sniper/data/state/hft.json` and push notifications via ntfy.sh.
