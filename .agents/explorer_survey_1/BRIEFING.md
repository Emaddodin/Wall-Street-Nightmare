# BRIEFING — 2026-09-19T09:05:00Z

## Mission
Investigate existing Hyperliquid integration, SDK usage, WebSocket feeds (L1, l2Book, trades), execution bridging (spam_orders, detached SL, close_basket), Core 1 background macro edge polling (llama.cpp at http://localhost:8080/completion), and test fixtures for hyper_predator_bot.py (R1, R2, R3, R5).

## 🔒 My Identity
- Archetype: explorer
- Roles: Orderflow and Alpha Explorer (R1 & R2)
- Working directory: /Users/mac/Desktop/TBT-Engine/.agents/explorer_survey_1
- Original parent: 68106074-9b56-4a6f-a92f-8aef4206f858
- Milestone: Architecture B Evolution Survey
- Roles (Updated): Hyperliquid SDK & WS Explorer (R1, R2, R3, R5)
- Current Parent: 39ebbf67-6c24-4133-888f-b0d9bed66dab (orchestrator_3)
- Milestone (Updated): Hyper Predator Bot Execution & WS Survey

## 🔒 Key Constraints
- Read-only investigation — do NOT implement
- Do NOT modify source code files in repository (only write reports/metadata in .agents/explorer_survey_1)
- Authoritative User Request: Read ORIGINAL_REQUEST.md before starting work
- Confine all metadata and reports strictly to /Users/mac/Desktop/TBT-Engine/.agents/explorer_survey_1
- Strict read-only exploration: produce survey_report.md and handoff.md

## Current Parent
- Conversation ID: 39ebbf67-6c24-4133-888f-b0d9bed66dab
- Updated: 2026-09-19T08:58:11Z

## Investigation State
- **Explored paths**:
  - `hyperliquid` installed package (`exchange.py`, `info.py`, `websocket_manager.py`, `types.py`)
  - `engine/execution_router.py` (HyperliquidVenue, SimulatedBrokerVenue, HyperliquidDEXVenue, ExecutionRouter)
  - `live_hyperliquid.py` (WebsocketManager usage, callback queues, live ordering)
  - `macro/slm_intuition.py` (Local llama.cpp `/completion` querying, GBNF grammar, sub-300ms timeout fallback)
  - `tests/test_gold_relapse_scalper.py` (Execution router unit tests, offline mock fixtures)
- **Key findings**:
  - `hyperliquid-python-sdk` is installed; all synchronous network calls must be wrapped via `asyncio.to_thread`.
  - WS stream channels: `bbo` (L1 best bid/ask), `l2Book` (top 5 levels bid/ask imbalance), `trades` (trade tape side "A" taker sell vs "B" taker buy).
  - Sub-5ms order flow exit requirement solved via in-memory `TapeBookMemory` atomic store.
  - `spam_orders` (5 slices, 20ms jitter stagger via `asyncio.gather`), detached stop placed at wick extreme $\pm \$1.00$ with `reduce_only=True`, and `close_basket()` mapped.
  - Core 1 background loop (`update_macro_edge`) mapped with GBNF grammar, sub-500ms timeout fallback, and thread-safe `MACRO_STATE`.
- **Unexplored areas**:
  - R7 deep vectorized backtester implementation (covered by peer explorer).
  - VPS systemd deployment & cleanup scripts (covered by subsequent tasks).

## Key Decisions Made
- Authored comprehensive `survey_report.md` detailing SDK, WS feeds, execution bridging, and macro edge polling.
- Authored 5-component self-contained `handoff.md`.
- Verified existing execution tests with `pytest tests/test_gold_relapse_scalper.py -v` (13 passed).

## Artifact Index
- DISPATCH.md — Dispatch log
- BRIEFING.md — Situational awareness
- progress.md — Liveness heartbeat and task progress
- survey_report.md — Hyperliquid SDK, WS & Execution survey report
- handoff.md — 5-component handoff report
