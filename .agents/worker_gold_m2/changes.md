# Changes Implemented by worker_gold_m2

## Milestone M2: LLM Coder Alignment & Macro Blackout Configuration

### 1. `deploy/install_llama.sh`
- **Updated model filename and download URL**:
  - `MODEL_FILENAME="qwen2.5-coder-1.5b-instruct-q4_k_m.gguf"`
  - `MODEL_URL="https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF/resolve/main/qwen2.5-coder-1.5b-instruct-q4_k_m.gguf"`
  - Updated script header to reflect Qwen2.5-Coder-1.5B model.

### 2. `deploy/stratton-llm-critic.service`
- **Updated systemd resource limit**:
  - Decreased `MemoryMax` from `2000M` to `1800M` to guarantee safe operation within 2.5 GB host RAM ceiling on a 4GB RAM VPS.
- **Updated model path**:
  - `-m /root/ict_sniper/models/qwen2.5-coder-1.5b-instruct-q4_k_m.gguf`
- **Preserved critical execution parameters**:
  - `-t 2`
  - `-ngl 0`
  - `--mlock`
  - `--ctx-size 2048`
  - `--host 127.0.0.1`
  - `--port 8080`

### 3. `run_relapse_scalper.py`
- **Added `--calendar-url` argument**:
  - Added `parser.add_argument("--calendar-url", type=str, default=None, help="Live economic calendar API endpoint URL")` in `argparse`.
- **Wired into Startup Telemetry**:
  - Added `"calendar_url": args.calendar_url` to `SYSTEM_STARTING` event telemetry.
- **Wired into `EconomicCalendarFilter`**:
  - Initialized `EconomicCalendarFilter(calendar_api_url=args.calendar_url)`.
- **Verified Execution Modes**:
  - Tested `--paper --dry-run` without `--calendar-url` (nominal exit code 0).
  - Tested `--paper --dry-run` with `--calendar-url` (nominal exit code 0).
