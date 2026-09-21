# BRIEFING — 2026-09-17T19:19:30Z

## Mission
Execute Milestone M2: Align local llama model to Qwen2.5-Coder-1.5B, enforce systemd critic resource constraints, add calendar URL argument to runtime, and verify clean execution.

## 🔒 My Identity
- Archetype: worker
- Roles: implementer, qa, specialist
- Working directory: /Users/mac/Desktop/TBT-Engine/.agents/worker_gold_m2
- Original parent: d8cde56b-142d-4ad0-b360-6f180e2c8eaa
- Milestone: M2 LLM Coder Alignment & Macro Blackout Configuration

## 🔒 Key Constraints
- Exclusive file ownership: deploy/install_llama.sh, deploy/stratton-llm-critic.service, run_relapse_scalper.py
- Model strictly matching R2 specification: qwen2.5-coder-1.5b-instruct-q4_k_m.gguf
- Enforce MemoryMax=1800M, -t 2, -ngl 0, --mlock, --ctx-size 2048 in deploy/stratton-llm-critic.service
- Add --calendar-url argument to argparse in run_relapse_scalper.py and wire into EconomicCalendarFilter
- No cheating, no facades, genuine implementations only

## Current Parent
- Conversation ID: d8cde56b-142d-4ad0-b360-6f180e2c8eaa
- Updated: not yet

## Task Summary
- **What to build**: Update llama installer script and systemd unit service to Qwen2.5-Coder-1.5B with 1800M MemoryMax, wire --calendar-url CLI argument into runtime and calendar filter.
- **Success criteria**: Dry run (`python3 run_relapse_scalper.py --paper --dry-run --initial-equity 65.0`) exits 0 with nominal status; pytest passes 100%.
- **Interface contracts**: `/Users/mac/Desktop/TBT-Engine/.agents/orchestrator_2/PROJECT.md`
- **Code layout**: `/Users/mac/Desktop/TBT-Engine/.agents/orchestrator_2/PROJECT.md` § Code Layout

## Key Decisions Made
- Use official HuggingFace repository for Qwen2.5-Coder-1.5B-Instruct-GGUF with exact filename `qwen2.5-coder-1.5b-instruct-q4_k_m.gguf`.
- Set `--calendar-url` with default `None`, passed directly to `EconomicCalendarFilter(calendar_api_url=args.calendar_url)`.

## Artifact Index
- `deploy/install_llama.sh` — Prebuilt llama-server installer & model downloader
- `deploy/stratton-llm-critic.service` — Systemd service definition for local llama-server
- `run_relapse_scalper.py` — Production & paper execution runtime CLI

## Change Tracker
- **Files modified**:
  - `deploy/install_llama.sh`: Updated model filename and download URL to `qwen2.5-coder-1.5b-instruct-q4_k_m.gguf`.
  - `deploy/stratton-llm-critic.service`: Updated model path to `qwen2.5-coder-1.5b-instruct-q4_k_m.gguf` and set `MemoryMax=1800M`.
  - `run_relapse_scalper.py`: Added `--calendar-url` to CLI arguments, wired into startup telemetry and `EconomicCalendarFilter`.
- **Build status**: All unit tests passing (10/10 passed), `--dry-run` execution verified nominal.
- **Pending issues**: None

## Quality Status
- **Build/test result**: 10 passed in 1.78s (`pytest tests/test_gold_relapse_scalper.py -v`)
- **Lint status**: Clean
- **Tests added/modified**: Verified CLI runtime execution and full gold relapse scalper test suite

## Loaded Skills
- None specified in dispatch prompt.
