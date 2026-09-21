## 2026-09-17T19:10:58Z
You are explorer_gold_survey_2.
Your working directory is /Users/mac/Desktop/TBT-Engine/.agents/explorer_gold_survey_2.
Your parent is orchestrator_2 (convId: d8cde56b-142d-4ad0-b360-6f180e2c8eaa).

MANDATORY: Read the authoritative specification at /Users/mac/Desktop/TBT-Engine/.agents/ORIGINAL_REQUEST.md (specifically the section starting at ## 2026-09-17T19:08:47Z).

Your focus: R2. Local llama.cpp Micro-LLM Intuition Exit Integration & R3. Macro Fundamental Calendar Blackout.
Investigate the codebase (e.g. macro/slm_intuition.py, engine/fsm.py, quant/hft/utils/killzone.py, quant/engine/guards.py, tests/test_gold_relapse_scalper.py, etc.).
Determine:
1. Local llama.cpp server integration (http://localhost:8080, Qwen2.5-Coder-1.5B-Instruct-GGUF).
2. Compressed 1-minute candle telemetry feeding (unrealized_r, candle_wick_ratio, volume_stall, dxy_divergence).
3. GBNF grammar / JSON schema constraining output strictly to {"decision": "HOLD"} or {"decision": "EXIT"}.
4. Latency bounds: < 300ms execution timeout with automatic algorithmic fail-safe fallback to prevent blocking event loop.
5. Macro Fundamental Calendar Blackout: background economic calendar monitor polling every 10m, unconditionally halting new trades +/- 15m around High-Impact US news (CPI, NFP, FOMC, PPI, Fed Rate Decisions).
6. Identify current implementation state and test coverage in tests/test_gold_relapse_scalper.py.

Write your findings to /Users/mac/Desktop/TBT-Engine/.agents/explorer_gold_survey_2/survey_r2_r3.md and provide a structured handoff.md. Keep progress.md updated. When finished, send a completion message to parent.
