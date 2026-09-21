# Dispatch for worker_gold_m4

## 2026-09-17T19:22:14Z

Milestone: M4 VPS Systemd Suite & Watchdog Hardening
Authoritative Specification: /Users/mac/Desktop/TBT-Engine/.agents/ORIGINAL_REQUEST.md
Project Specification: /Users/mac/Desktop/TBT-Engine/.agents/orchestrator_2/PROJECT.md
Explorer Survey: /Users/mac/Desktop/TBT-Engine/.agents/explorer_gold_survey_3/handoff.md
Working Directory: /Users/mac/Desktop/TBT-Engine/.agents/worker_gold_m4
File Ownership (Exclusive): deploy/relapse-scalper.service, deploy/relapse-watchdog.service, deploy/relapse-watchdog.timer, quant/hft/guard.py, tests/test_hft_guard.py

Task:
1. Provide production-ready systemd unit files in `deploy/`:
   - `deploy/relapse-scalper.service`: systemd unit to run `run_relapse_scalper.py --paper` with `MemoryMax=600M`, `CPUQuota=100%`, `Restart=always`, `RestartSec=5`, `WorkingDirectory=/root/ict_sniper`, `EnvironmentFile=/root/ict_sniper/.env`.
   - `deploy/relapse-watchdog.service`: oneshot service executing `python3 /root/ict_sniper/quant/hft/guard.py --oneshot`.
   - `deploy/relapse-watchdog.timer`: systemd timer triggering `relapse-watchdog.service` every 30 seconds (`OnBootSec=1min`, `OnUnitActiveSec=30s`).
2. Update `quant/hft/guard.py`:
   - Set monitored services to `SCALPER_UNIT = "relapse-scalper.service"` and `CRITIC_UNIT = "stratton-llm-critic.service"`.
   - Ensure memory budget enforcement: `MAX_SYSTEM_RAM_MB = 2560.0` (2.5 GB resident limit on 4GB VPS).
   - Ensure Antigravity JSON structured telemetry output format: `{"timestamp": ..., "agent": "watchdog_guard", "component": ..., "event": ..., "level": ..., "data": ...}`.
   - Support ntfy.sh escalation for critical alerts (e.g. RAM limit breach or critic failure).
3. Verify test coverage:
   - Run `pytest tests/test_hft_guard.py -v`. Ensure all tests pass.
4. Document changes in `changes.md` and write a structured 5-component `handoff.md`.
When done, send a completion message to parent.
