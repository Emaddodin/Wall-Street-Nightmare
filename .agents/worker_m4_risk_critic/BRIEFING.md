# BRIEFING — 2026-09-17T00:24:00Z

## Mission
Implement Milestone 4 (R4 & R5): Asynchronous Local LLM Strategic Critic, Capital Allocation & Risk Engine, and CDP Target Pinning.

## 🔒 My Identity
- Archetype: worker
- Roles: implementer, qa, specialist
- Working directory: /Users/mac/Desktop/TBT-Engine/.agents/worker_m4_risk_critic
- Original parent: 68106074-9b56-4a6f-a92f-8aef4206f858
- Milestone: Milestone 4: R4 & R5

## 🔒 Key Constraints
- DO NOT CHEAT: genuine implementations only, no hardcoded test results, no dummy/facade implementations.
- Exclusive file ownership:
  * quant/hft/risk/engine.py
  * quant/hft/risk/__init__.py
  * quant/hft/critic/__init__.py
  * quant/hft/critic/llama_client.py
  * quant/hft/critic/trade_critic.py
  * signals/tv_cdp.py
  * deploy/stratton-llm-critic.service
  * deploy/install_llama.sh
- Liquidation distance >= 9.5% with MMR=0.5% strictly enforced. Clamp leverage <= 10x in isolated margin; if up to 15x requested, require additional committed margin buffer to guarantee effective liquidation distance >= 9.5%. Reject any order with liquidation distance < 9.5%.
- 25% margin sizing per trade: allocated margin = 0.25 * equity (75% unencumbered liquid cash buffer).
- Hard SL gate: deterministic rejection if hard_sl is None, <= 0, or beyond liquidation price.
- Dynamic Kelly multiplier wired into sizing from LLMCriticClient.get_current_risk_multiplier() (sub-1us O(1) memory read, failsafe to 1.0 or conservative fallback).
- Tab target ID pinning in `signals/tv_cdp.py`: eliminate target swapping between chart windows across tab reordering.
- Systemd service `deploy/stratton-llm-critic.service` with `-t 2 -ngl 0 --mlock --ctx-size 2048`, `LimitMEMLOCK=infinity`, `MemoryMax=2000M`, `Restart=always`.
- Script `deploy/install_llama.sh` downloading prebuilt Linux binary and sub-3B Q4_K_M GGUF (`Qwen2.5-1.5B-Instruct-Q4_K_M.gguf`).
- Unit tests in `tests/test_risk_critic.py` verifying all invariants, CDP pinning, and async critic. Zero regressions across suite.

## Current Parent
- Conversation ID: 68106074-9b56-4a6f-a92f-8aef4206f858
- Updated: 2026-09-17T00:24:00Z

## Task Summary
- **What to build**: Production-grade Risk Engine (R5), Local LLM Critic & Client (R4), CDP Target Pinning, Deployment scripts, and comprehensive unit tests.
- **Success criteria**: All risk invariants strictly enforced, CDP pinning verified against tab reordering, LLM critic async loop & O(1) sync risk multiplier working with fallback, test suite passing.
- **Interface contracts**: PROJECT.md, ORIGINAL_REQUEST.md, survey_r3_r5.md, survey_r4_r6.md
- **Code layout**: quant/hft/risk, quant/hft/critic, signals, deploy, tests

## Change Tracker
- **Files modified**:
  * `signals/tv_cdp.py`: Added explicit tab target ID pinning, `pinned_target_id` caching, `pin_target()`, `list_targets()`, and `target_id` forwarding in `CDPSignalSource`.
  * `quant/hft/risk/engine.py`: Built `RiskEngine` enforcing leverage ceiling (<=10x isolated, <=15x buffered), >=9.5% liquidation distance, 25% margin sizing, 75% unencumbered liquid cash buffer, deterministic hard SL gate, and dynamic Kelly multiplier wiring.
  * `quant/hft/risk/__init__.py`: Exported `RiskEngine` and `OrderParameters`.
  * `quant/hft/critic/trade_critic.py`: Implemented `TradeCritic` with rolling 20-trade summary JSON formatting, prompt synthesis, and JSON response parsing/clamping.
  * `quant/hft/critic/llama_client.py`: Implemented `LLMCriticClient` with background async worker loop, synchronous O(1) memory read (<1 µs), fail-safe fallback (1.0), and health checks.
  * `quant/hft/critic/__init__.py`: Exported `LLMCriticClient` and `TradeCritic`.
  * `deploy/stratton-llm-critic.service`: Created systemd unit with `-t 2 -ngl 0 --mlock --ctx-size 2048`, `LimitMEMLOCK=infinity`, `MemoryMax=2000M`, `Restart=always`.
  * `deploy/install_llama.sh`: Created automated VPS download and setup script for llama-server Linux binary and Qwen2.5-1.5B GGUF.
  * `tests/test_risk_critic.py`: Implemented 17 comprehensive unit tests.
- **Build status**: PASS (17/17 tests in test_risk_critic.py; 39/39 in combined M3/M4/filter suite; 50/50 in alpha/data layer).
- **Pending issues**: None.

## Quality Status
- **Build/test result**: 100% tests passing (zero failures, zero warnings).
- **Lint status**: 0 violations (all modules compile cleanly).
- **Tests added/modified**: `tests/test_risk_critic.py` (17 tests covering all R4 & R5 invariants).

## Loaded Skills
- None.

## Key Decisions Made
- `get_current_risk_multiplier()` operates as an immutable float attribute read, guaranteeing atomic sub-microsecond latency (< 0.08 µs benchmarked).
- `TradeCritic` clamps `risk_multiplier` to `[0.0, 1.5]` and `confidence_floor_adj` to `[-0.05, 0.10]` with markdown fence stripping and JSON robustness.
- Sizing strictly enforces `allocated_margin <= 0.25 * equity`, preserving the 75% unencumbered liquid cash buffer even under high conviction.
- `TradingViewCDP` locks `pinned_target_id` upon initial connection and verifies target existence on reconnects, completely eliminating window swapping during tab reordering.

## Artifact Index
- .agents/worker_m4_risk_critic/DISPATCH.md
- .agents/worker_m4_risk_critic/BRIEFING.md
- .agents/worker_m4_risk_critic/progress.md
- .agents/worker_m4_risk_critic/handoff.md
