## 2026-09-17T00:17:48Z
You are the Risk and Critic Worker for Wall-Street-Nightmare Architecture B (Milestone 4: R4 & R5).
Working directory: /Users/mac/Desktop/TBT-Engine/.agents/worker_m4_risk_critic
Authoritative User Request: Read /Users/mac/Desktop/TBT-Engine/ORIGINAL_REQUEST.md before starting work.
Project Plan: Read /Users/mac/Desktop/TBT-Engine/PROJECT.md and survey findings in /Users/mac/Desktop/TBT-Engine/.agents/explorer_survey_2/survey_r3_r5.md and /Users/mac/Desktop/TBT-Engine/.agents/explorer_survey_3/survey_r4_r6.md.

DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Exclusive file ownership:
- quant/hft/risk/engine.py
- quant/hft/risk/__init__.py
- quant/hft/critic/__init__.py
- quant/hft/critic/llama_client.py
- quant/hft/critic/trade_critic.py
- signals/tv_cdp.py
- deploy/stratton-llm-critic.service
- deploy/install_llama.sh

Your mission:
Implement R4 (Asynchronous Local LLM Strategic Critic) and R5 (Capital Allocation, Risk Engine & CDP Target Pinning):
1. `quant/hft/risk/engine.py`:
   - Enforce 10x-15x leverage ceiling: In isolated margin mode, clamp leverage strictly to <= 10x ($1/10 - 0.005 = 0.095 = 9.5\%$ liquidation distance with MMR=0.5%). If leverage up to 15x is requested, require additional committed margin buffer to mathematically guarantee effective liquidation distance >= 9.5%. Reject any order with liquidation distance < 9.5%.
   - Enforce 25% margin sizing per trade: allocated margin = 0.25 * equity (guaranteeing 75% unencumbered liquid cash buffer).
   - Enforce deterministic hard stop-losses: reject any candidate order where `hard_sl is None`, `hard_sl <= 0`, or where hard stop loss is beyond the liquidation price.
   - Wire dynamic Kelly multiplier from `LLMCriticClient.get_current_risk_multiplier()` into sizing.
2. `signals/tv_cdp.py`:
   - Implement explicit tab target ID pinning:
     * Add `target_id: str | None = None` parameter to `TradingViewCDP.__init__` and `CDPSignalSource.__init__`.
     * In `_target()`, cache `self.pinned_target_id = page["id"]` upon first connection.
     * On subsequent calls/reconnects, match against `self.pinned_target_id` (or user-supplied `target_id`) to eliminate target swapping between chart windows across tab reordering.
3. `quant/hft/critic/llama_client.py`:
   - Build isolated client for `llama-server` (default `http://127.0.0.1:8080`).
   - Implement asynchronous worker loop decoupled from the critical sub-50ms execution path.
   - Synchronous $\mathcal{O}(1)$ memory read method: `get_current_risk_multiplier() -> float` (returns cached atomic float in `[0.0, 1.5]`, execution time < 1 µs).
   - Fail-safe fallback: returns 1.0 (or conservative 0.5) if server is unreachable or health check fails.
4. `quant/hft/critic/trade_critic.py`:
   - Formats rolling 20-trade summaries into JSON schema (setup type, PnL, duration, slippage, regime).
   - Dispatches prompt to `llama-server` requesting critique and outputting strict JSON with `risk_multiplier` (0.0 to 1.5), `confidence_floor_adj`, and `critique_rationale`.
5. `deploy/stratton-llm-critic.service` and `deploy/install_llama.sh`:
   - Create systemd service unit running `llama-server` on VPS with strict parameters: `-t 2`, `-ngl 0`, `--mlock`, `--ctx-size 2048`, on port 8080, with `LimitMEMLOCK=infinity` (CRITICAL to avoid ENOMEM on `--mlock`), `MemoryMax=2000M`, and `Restart=always`.
   - Script `install_llama.sh` to download prebuilt `llama-server` Linux binary and sub-3B Q4_K_M GGUF model (`Qwen2.5-1.5B-Instruct-Q4_K_M.gguf`).
6. Write unit tests in `tests/test_risk_critic.py` verifying all risk engine invariants (leverage ceiling, liquidation distance >= 9.5%, 25% margin sizing, hard SL gate), CDP target ID pinning across simulated tab reordering, and async critic client behavior.
7. Verify all tests pass with zero regressions.
8. Write handoff report to handoff.md in your working directory and message parent when complete.
