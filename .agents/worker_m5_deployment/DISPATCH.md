## 2026-09-17T00:26:00Z
You are the Production Deployment Worker for Wall-Street-Nightmare Architecture B (Milestone 5: R6).
Working directory: /Users/mac/Desktop/TBT-Engine/.agents/worker_m5_deployment
Authoritative User Request: Read /Users/mac/Desktop/TBT-Engine/ORIGINAL_REQUEST.md before starting work.
Project Plan: Read /Users/mac/Desktop/TBT-Engine/PROJECT.md and survey findings in /Users/mac/Desktop/TBT-Engine/.agents/explorer_survey_3/survey_r4_r6.md.

DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

VPS target: 82.115.21.155 (SSH alias `stratton`, user `root`, directory `/root/ict_sniper/`).

Your mission:
1. Synchronize the complete codebase from /Users/mac/Desktop/TBT-Engine to /root/ict_sniper on the VPS using rsync/ssh (updating tools/sync.sh if helpful).
2. Install `llama-server` prebuilt Linux x64 binary into `/root/ict_sniper/llama.cpp/` and download the sub-3B Q4_K_M model (`Qwen2.5-1.5B-Instruct-Q4_K_M.gguf` ~940MB or `Llama-3.2-1B-Instruct-Q4_K_M.gguf` ~770MB) via `deploy/install_llama.sh` or direct SSH commands.
3. Configure and activate `stratton-llm-critic.service` in `/etc/systemd/system/` with `LimitMEMLOCK=infinity`, `-t 2 -ngl 0 --mlock --ctx-size 2048` on port 8080.
4. Verify `llama-server` is active, responding to `curl -s http://127.0.0.1:8080/health`, and responding to a test completion/critique query.
5. Verify VPS system RAM consumption with `llama-server` running: run `free -m` and inspect process RSS to strictly confirm that total system memory usage is safely <= 2.5 GB (acceptance criterion).
6. Verify the HFT execution engine on the VPS: ensure `tbt-hl-hft.service` is active, connected to the Hyperliquid L2 WebSocket stream, updating `/root/ict_sniper/data/state/hft.json` with live ticks and telemetry without crashes.
7. Ensure `quant/hft/guard.py` monitors `stratton-llm-critic.service` and the 2.5 GB memory limit.
8. Document all verification commands, outputs, system specs, and telemetry in handoff.md in your working directory and notify parent upon completion.
