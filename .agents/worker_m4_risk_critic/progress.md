# Progress — worker_m4_risk_critic

Last visited: 2026-09-17T00:25:00Z

## Status
Task Complete! All components of Milestone 4 (R4 & R5) implemented and verified.

## Completed Work
1. `signals/tv_cdp.py`:
   - Added `target_id: str | None = None` and `target_index: int | None = None` to `TradingViewCDP` and `CDPSignalSource`.
   - Cached `pinned_target_id = page["id"]` upon first connection.
   - Pinned resolution across Chrome `/json` tab reordering and reconnects.
   - Added `pin_target()` and `list_targets()` classmethod.
2. `quant/hft/critic/trade_critic.py`:
   - Formatted rolling 20-trade summaries into strict JSON telemetry (win rate, profit factor, drawdown, duration, slippage, regime).
   - Built prompt requesting structured JSON output.
   - Robust parsing and clamping of `risk_multiplier` to `[0.0, 1.5]` and `confidence_floor_adj` to `[-0.05, 0.10]`.
3. `quant/hft/critic/llama_client.py`:
   - Decoupled asynchronous daemon worker thread with background queue.
   - Synchronous O(1) memory read `get_current_risk_multiplier()` verified < 0.1 µs (< 1 µs requirement).
   - Fail-safe fallback to 1.0 (or configured fallback) upon server unavailability or failure.
   - Health check endpoint `/health`.
4. `quant/hft/risk/engine.py` & `__init__.py`:
   - Built `RiskEngine` enforcing:
     * Leverage ceiling (<=10x isolated; <=15x buffered with committed margin buffer; rejects if liq dist < 9.5%).
     * 25% margin sizing per trade (`allocated_margin = 0.25 * equity`, guaranteeing 75% unencumbered liquid cash buffer).
     * Deterministic hard stop-loss gate (rejects if None, <= 0, wrong side, or beyond liquidation price).
     * Dynamic Kelly multiplier integration from `LLMCriticClient.get_current_risk_multiplier()`.
   - Exported in `quant/hft/risk/__init__.py`.
5. `deploy/stratton-llm-critic.service` & `deploy/install_llama.sh`:
   - Systemd unit with `-t 2 -ngl 0 --mlock --ctx-size 2048`, `LimitMEMLOCK=infinity`, `MemoryMax=2000M`, `Restart=always`.
   - Executable installer downloading Linux x64 `llama-server` and `Qwen2.5-1.5B-Instruct-Q4_K_M.gguf`.
6. Unit Tests in `tests/test_risk_critic.py`:
   - 17 comprehensive unit tests passing with zero regressions across the codebase.
