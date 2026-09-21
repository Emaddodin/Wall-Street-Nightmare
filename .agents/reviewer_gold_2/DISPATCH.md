# Dispatch for reviewer_gold_2

Role: High-Reliability Reviewer 2
Authoritative Specification: /Users/mac/Desktop/TBT-Engine/.agents/ORIGINAL_REQUEST.md
Project Specification: /Users/mac/Desktop/TBT-Engine/.agents/orchestrator_2/PROJECT.md
Working Directory: /Users/mac/Desktop/TBT-Engine/.agents/reviewer_gold_2

Task:
1. Review correctness, completeness, and interface conformance of:
   - `macro/slm_intuition.py` & `engine/fsm.py`: Local llama.cpp Qwen2.5-Coder-1.5B exit intuition, GBNF schema, <300ms timeout with fail-safe, 10m economic calendar polling, ±15m macro blackout on High-Impact US news.
   - `deploy/`: `relapse-scalper.service` (`MemoryMax=600M`), `stratton-llm-critic.service` (`qwen2.5-coder-1.5b-instruct-q4_k_m.gguf`, `MemoryMax=1800M`), `relapse-watchdog.service` and `.timer`.
   - `quant/hft/guard.py`: RAM ceiling <= 2560 MB (/proc/meminfo), Antigravity JSON telemetry format, ntfy.sh escalation.
2. Execute verification:
   - `pytest tests/test_hft_guard.py -v`
   - `python3 run_relapse_scalper.py --paper --dry-run --initial-equity 65.0`
3. Deliver verdict: APPROVE or REQUEST_CHANGES in handoff.md.

## 2026-09-17T19:43:16Z
Review the LLM intuition, macro blackout, and systemd/guard modules:
- macro/slm_intuition.py & engine/fsm.py (llama.cpp Qwen2.5-Coder-1.5B exit intuition, GBNF schema, <300ms timeout with fail-safe, 10m calendar polling, ±15m macro blackout on High-Impact US news).
- deploy/ (relapse-scalper.service, stratton-llm-critic.service with qwen2.5-coder-1.5b-instruct-q4_k_m.gguf & MemoryMax=1800M, relapse-watchdog.service/.timer).
- quant/hft/guard.py (RAM <= 2560 MB limit, Antigravity JSON telemetry, ntfy.sh escalation).
- run_relapse_scalper.py (--calendar-url option, dry-run diagnostics).

Run the tests and diagnostics:
- pytest tests/test_hft_guard.py -v
- python3 run_relapse_scalper.py --paper --dry-run --initial-equity 65.0

Deliver a structured review with your verdict (APPROVE or REQUEST_CHANGES) in handoff.md and send completion message to parent.
