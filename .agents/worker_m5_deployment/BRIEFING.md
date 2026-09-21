# BRIEFING — 2026-09-17T00:26:00Z

## Mission
Deploy and verify Wall-Street-Nightmare Architecture B (Milestone 5: R6) on VPS stratton (82.115.21.155), including codebase sync, llama-server sub-3B setup, stratton-llm-critic.service, <= 2.5GB RAM verification, tbt-hl-hft.service verification, and quant/hft/guard.py monitoring.

## 🔒 My Identity
- Archetype: worker
- Roles: implementer, qa, specialist
- Working directory: /Users/mac/Desktop/TBT-Engine/.agents/worker_m5_deployment
- Original parent: 68106074-9b56-4a6f-a92f-8aef4206f858
- Milestone: Milestone 5: R6 (Production Deployment)

## 🔒 Key Constraints
- Target VPS: 82.115.21.155 (SSH alias stratton, user root, directory /root/ict_sniper/)
- DO NOT CHEAT. Genuine implementations only. No hardcoded results, no facade implementations.
- Sub-3B Q4_K_M model with llama-server prebuilt Linux x64 binary.
- Total VPS system RAM consumption with llama-server running strictly <= 2.5 GB.
- Real Hyperliquid L2 WebSocket stream connectivity and live tick generation in /root/ict_sniper/data/state/hft.json.
- Guard daemon (quant/hft/guard.py) monitors stratton-llm-critic.service and the 2.5 GB memory limit.

## Current Parent
- Conversation ID: 68106074-9b56-4a6f-a92f-8aef4206f858
- Updated: not yet

## Task Summary
- **What to build**: Production deployment on VPS stratton: code sync, llama-server installation and service activation, memory validation (<= 2.5GB), HFT engine service validation, guard monitoring.
- **Success criteria**:
  1. Synchronized codebase to /root/ict_sniper on VPS.
  2. llama-server installed and serving sub-3B model on port 8080 via systemd service stratton-llm-critic.service.
  3. curl health check and completion/critique query succeed.
  4. Total system RAM usage <= 2.5 GB confirmed by free -m and process inspection.
  5. tbt-hl-hft.service active, connected to Hyperliquid L2 WS, updating hft.json.
  6. quant/hft/guard.py monitors stratton-llm-critic.service and the 2.5 GB memory threshold.
  7. Full handoff.md report with exact commands, outputs, and telemetry.
- **Interface contracts**: PROJECT.md, ORIGINAL_REQUEST.md
- **Code layout**: /root/ict_sniper on VPS

## Change Tracker
- **Files modified**: none yet
- **Build status**: pending
- **Pending issues**: none

## Quality Status
- **Build/test result**: pending
- **Lint status**: pending
- **Tests added/modified**: pending

## Loaded Skills
- None specified in dispatch

## Key Decisions Made
- [TBD]

## Artifact Index
- /Users/mac/Desktop/TBT-Engine/.agents/worker_m5_deployment/handoff.md — Final handoff report
