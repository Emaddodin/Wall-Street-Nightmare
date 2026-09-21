## 2026-09-19T08:58:11Z

You are Explorer 2 (Architecture & Purge Explorer).
Your working directory is: /Users/mac/Desktop/TBT-Engine/.agents/explorer_survey_2

MANDATORY INPUT:
Read /Users/mac/Desktop/TBT-Engine/.agents/ORIGINAL_REQUEST.md (specifically the requirements under ## 2026-09-19T08:56:25Z: R1 to R8).

OBJECTIVE:
Investigate codebase structure for R6 repository purge, price action / wick / S/R calculations (R2, R4), and order flow micro-structure (R5):
1. R6 Repository Purge: Survey all files and directories in `/Users/mac/Desktop/TBT-Engine`. Identify all legacy MT5 files, slow synchronous LLM loops, multi-asset routers, old filters, and unused code that should be purged into `_archive/`. List exact file paths to move.
2. R2 Sniper Math: Investigate rolling M5 S/R pivots, M1 candle extreme rejection wick calculation (wick length >= 65% of total high-low range, body closes in direction of `MACRO_STATE.bias`), and final 5-second tick velocity surge calculation (>= 1.5x rolling baseline). Check existing implementations in `scalper/pa/`, `engine/`, `quant/`.
3. R4 Dynamic Exits & Hard Equity Shield: Opposing M5 S/R zone target exit, opposing >= 65% M1 rejection wick reversal exit, and hard -$10.00 equity shield basket close.
4. R5 Order Flow Edge: Top-5 L2 book imbalance ratio calculation (> 3.0 * volatility_regime), trade tape volume delta stall (> 80% opposing ticks in last 20 trades), and sub-5ms memory execution.

SCOPE BOUNDARIES:
- Read-only exploration. DO NOT move or modify any source code files.
- Confine all your metadata and reports to your working directory: /Users/mac/Desktop/TBT-Engine/.agents/explorer_survey_2.

OUTPUT REQUIREMENTS:
Write your comprehensive report to `/Users/mac/Desktop/TBT-Engine/.agents/explorer_survey_2/survey_report.md` and write your handoff to `/Users/mac/Desktop/TBT-Engine/.agents/explorer_survey_2/handoff.md`.
Then send a concise summary message back to orchestrator_3.

COMPLETION CRITERIA:
Exact inventory of files to purge to `_archive/`, mathematical formulas and specifications for R2, R4, R5, and integration architecture for `hyper_predator_bot.py`.
