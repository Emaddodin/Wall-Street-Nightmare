# Dispatch for worker_gold_m2

## 2026-09-17T19:18:27Z

Milestone: M2 LLM Coder Alignment & Macro Blackout Configuration
Authoritative Specification: /Users/mac/Desktop/TBT-Engine/.agents/ORIGINAL_REQUEST.md
Project Specification: /Users/mac/Desktop/TBT-Engine/.agents/orchestrator_2/PROJECT.md
Explorer Survey: /Users/mac/Desktop/TBT-Engine/.agents/explorer_gold_survey_2/handoff.md
Working Directory: /Users/mac/Desktop/TBT-Engine/.agents/worker_gold_m2
File Ownership (Exclusive): deploy/install_llama.sh, deploy/stratton-llm-critic.service, run_relapse_scalper.py

Task:
1. Update `deploy/install_llama.sh` and `deploy/stratton-llm-critic.service`:
   - Point model download and execution to `qwen2.5-coder-1.5b-instruct-q4_k_m.gguf` strictly matching R2 specification.
   - Enforce `MemoryMax=1800M`, `-t 2`, `-ngl 0`, `--mlock`, `--ctx-size 2048`.
2. Update `run_relapse_scalper.py`:
   - Add `--calendar-url` argument in `argparse` to allow supplying live calendar endpoint.
   - Wire `calendar_url` into `EconomicCalendarFilter(calendar_api_url=args.calendar_url)`.
   - Ensure clean `--dry-run` and `--paper` execution.
3. Run verification:
   - `python3 run_relapse_scalper.py --paper --dry-run --initial-equity 65.0`
   - `pytest tests/test_gold_relapse_scalper.py -v`
4. Document changes in `changes.md` and write a structured 5-component `handoff.md`.
