# Progress Log - worker_gold_m2

Last visited: 2026-09-17T19:21:15Z

## Current Status
- Milestone M2 completed:
  1. Updated `deploy/install_llama.sh` to download `qwen2.5-coder-1.5b-instruct-q4_k_m.gguf` from `https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF/resolve/main/qwen2.5-coder-1.5b-instruct-q4_k_m.gguf`.
  2. Updated `deploy/stratton-llm-critic.service` to use `qwen2.5-coder-1.5b-instruct-q4_k_m.gguf` and enforce `MemoryMax=1800M`, `-t 2`, `-ngl 0`, `--mlock`, `--ctx-size 2048`.
  3. Updated `run_relapse_scalper.py` to add `--calendar-url` argument in `argparse`, log it in start telemetry, and initialize `EconomicCalendarFilter(calendar_api_url=args.calendar_url)`.
  4. Verified dry run: `python3 run_relapse_scalper.py --paper --dry-run --initial-equity 65.0` passed with nominal diagnostic status (exit code 0).
  5. Verified unit tests: `pytest tests/test_gold_relapse_scalper.py -v` (10/10 passed in 1.78s).
  6. Prepared `changes.md` and `handoff.md`.
