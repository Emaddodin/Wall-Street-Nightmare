# Handoff Report: Milestone M2 — LLM Coder Alignment & Macro Blackout Configuration

**Agent**: `worker_gold_m2`  
**Parent**: `orchestrator_2` (convId: `d8cde56b-142d-4ad0-b360-6f180e2c8eaa`)  
**Working Directory**: `/Users/mac/Desktop/TBT-Engine/.agents/worker_gold_m2`  
**Handoff Type**: Hard Handoff (Task Complete)  

---

## 1. Observation

1. **Model Filename and Download Path Mismatch**:
   - In `deploy/install_llama.sh:14-15`:
     ```bash
     MODEL_FILENAME="Qwen2.5-1.5B-Instruct-Q4_K_M.gguf"
     MODEL_URL="https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf"
     ```
     `ORIGINAL_REQUEST.md:71` specifies: `Qwen2.5-Coder-1.5B-Instruct-GGUF` (`qwen2.5-coder-1.5b-instruct-q4_k_m.gguf`).
   - In `deploy/stratton-llm-critic.service:11-19`:
     ```ini
     MemoryMax=2000M
     ExecStart=/root/ict_sniper/llama.cpp/llama-server \
         -m /root/ict_sniper/models/Qwen2.5-1.5B-Instruct-Q4_K_M.gguf \
         --host 127.0.0.1 \
         --port 8080 \
         -t 2 \
         -ngl 0 \
         --mlock \
         --ctx-size 2048
     ```
     Dispatch mandated enforcing `MemoryMax=1800M`, `-t 2`, `-ngl 0`, `--mlock`, `--ctx-size 2048`, and pointing the model path to `qwen2.5-coder-1.5b-instruct-q4_k_m.gguf`.

2. **Runtime CLI Argument Missing**:
   - In `run_relapse_scalper.py:53-62`, `argparse` contained arguments `--paper`, `--dry-run`, `--initial-equity`, `--llm-host`, `--llm-port`, and `--verbose`, but lacked `--calendar-url`.
   - In `run_relapse_scalper.py:94-95`, `calendar_filter = EconomicCalendarFilter()` was instantiated without `calendar_api_url`.

3. **Executed Modifications**:
   - In `deploy/install_llama.sh:14-15`:
     ```bash
     MODEL_FILENAME="qwen2.5-coder-1.5b-instruct-q4_k_m.gguf"
     MODEL_URL="https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF/resolve/main/qwen2.5-coder-1.5b-instruct-q4_k_m.gguf"
     ```
   - In `deploy/stratton-llm-critic.service:11,13`:
     ```ini
     MemoryMax=1800M
     ExecStart=/root/ict_sniper/llama.cpp/llama-server \
         -m /root/ict_sniper/models/qwen2.5-coder-1.5b-instruct-q4_k_m.gguf \
         --host 127.0.0.1 \
         --port 8080 \
         -t 2 \
         -ngl 0 \
         --mlock \
         --ctx-size 2048
     ```
   - In `run_relapse_scalper.py:61,75,96`:
     ```python
     parser.add_argument("--calendar-url", type=str, default=None, help="Live economic calendar API endpoint URL")
     # ...
     emit_telemetry(..., data={..., "calendar_url": args.calendar_url})
     # ...
     calendar_filter = EconomicCalendarFilter(calendar_api_url=args.calendar_url)
     ```

4. **Verification Commands and Output**:
   - `python3 run_relapse_scalper.py --help`:
     Output confirmed `--calendar-url CALENDAR_URL` option.
   - `python3 run_relapse_scalper.py --paper --dry-run --initial-equity 65.0`:
     Exit code: 0. Telemetry logged `DRY_RUN_DIAGNOSTIC_PASSED` with `"status": "NOMINAL"`.
   - `python3 run_relapse_scalper.py --paper --dry-run --initial-equity 65.0 --calendar-url https://example.com/api/calendar`:
     Exit code: 0. Telemetry logged `"calendar_url": "https://example.com/api/calendar"` and `DRY_RUN_DIAGNOSTIC_PASSED`.
   - `pytest tests/test_gold_relapse_scalper.py -v`:
     Output: `10 passed in 1.78s`.

---

## 2. Logic Chain

1. **Model & Service Alignment**:
   - Observation 1 confirmed the previous configuration targeted the non-coder variant `Qwen2.5-1.5B-Instruct-Q4_K_M.gguf` and had `MemoryMax=2000M`.
   - Observation 3 aligned the model URL to the official HuggingFace repository for `Qwen2.5-Coder-1.5B-Instruct-GGUF` and the filename to `qwen2.5-coder-1.5b-instruct-q4_k_m.gguf`.
   - Observation 3 lowered `MemoryMax` to `1800M` in `deploy/stratton-llm-critic.service` while preserving `-t 2`, `-ngl 0`, `--mlock`, and `--ctx-size 2048`, guaranteeing that the critic service operates safely under the 2.5 GB VPS RAM budget.

2. **Runtime Calendar Feed Wiring**:
   - Observation 2 revealed that although `EconomicCalendarFilter` supports `calendar_api_url`, `run_relapse_scalper.py` lacked a CLI argument to specify it.
   - Observation 3 exposed `--calendar-url` via `argparse` and passed `calendar_api_url=args.calendar_url` directly to `EconomicCalendarFilter`.
   - Observation 4 verified that `--dry-run` and `--paper` execute without errors both with and without the `--calendar-url` argument provided.

3. **Regression Safety**:
   - Observation 4 confirmed all 10 tests in `tests/test_gold_relapse_scalper.py` continue to pass cleanly with zero regressions.

---

## 3. Caveats

- **External Calendar Endpoint**: When `--calendar-url` is specified, `EconomicCalendarFilter` will make asynchronous HTTP GET requests to that URL during live runs. In isolated testing / dry-run environments or when `--calendar-url` is omitted, `fetch_latest_events()` returns immediately without network calls.
- **Model Download on Local Machine**: `deploy/install_llama.sh` is designed for installation on the target Ubuntu VPS (`/root/ict_sniper`); it was not executed locally on macOS to avoid downloading the multi-gigabyte GGUF model and Linux binaries to local disk.

---

## 4. Conclusion

Milestone M2 objectives are **completely met and verified**:
1. `deploy/install_llama.sh` and `deploy/stratton-llm-critic.service` now strictly target `qwen2.5-coder-1.5b-instruct-q4_k_m.gguf`.
2. `deploy/stratton-llm-critic.service` enforces `MemoryMax=1800M`, `-t 2`, `-ngl 0`, `--mlock`, `--ctx-size 2048`.
3. `run_relapse_scalper.py` exposes `--calendar-url`, logs it to startup telemetry, and passes it to `EconomicCalendarFilter`.
4. Runtime `--dry-run` diagnostics pass cleanly with nominal status.
5. All 10 test cases in `tests/test_gold_relapse_scalper.py` pass cleanly.

---

## 5. Verification Method

### Test Execution Commands:
```bash
# 1. Verify dry-run runtime with default calendar URL
python3 /Users/mac/Desktop/TBT-Engine/run_relapse_scalper.py --paper --dry-run --initial-equity 65.0

# 2. Verify dry-run runtime with custom calendar URL
python3 /Users/mac/Desktop/TBT-Engine/run_relapse_scalper.py --paper --dry-run --initial-equity 65.0 --calendar-url https://api.example.com/calendar

# 3. Run full test suite
pytest /Users/mac/Desktop/TBT-Engine/tests/test_gold_relapse_scalper.py -v
```

### Inspection:
```bash
# Check service config parameters
grep -E "MemoryMax|qwen2.5-coder|-t 2|-ngl 0|--mlock|--ctx-size 2048" /Users/mac/Desktop/TBT-Engine/deploy/stratton-llm-critic.service

# Check installer model parameters
grep -E "MODEL_FILENAME|MODEL_URL" /Users/mac/Desktop/TBT-Engine/deploy/install_llama.sh
```

### Invalidation Conditions:
- `run_relapse_scalper.py` fails to parse `--calendar-url` or exits with non-zero code on `--dry-run`.
- `deploy/stratton-llm-critic.service` does not specify `MemoryMax=1800M` or `qwen2.5-coder-1.5b-instruct-q4_k_m.gguf`.
- Any test in `tests/test_gold_relapse_scalper.py` fails.
