# Changes: Milestone M4 — VPS Systemd Production Suite & Watchdog Hardening

## Overview
Worker `worker_gold_m4` has implemented the production-ready systemd suite and hardened the watchdog health guard for the 5-Minute XAUUSD Relapse Scalper engine and local LLM critic.

## Files Created / Modified

### 1. `deploy/relapse-scalper.service` (Created)
- **Purpose**: Systemd service unit for the main scalper execution engine.
- **Key Directives**:
  - `WorkingDirectory=/root/ict_sniper`
  - `EnvironmentFile=/root/ict_sniper/.env`
  - `MemoryMax=600M` (cgroup resident memory containment)
  - `CPUQuota=100%` (capped to single vCPU equivalent)
  - `ExecStart=/usr/bin/python3 /root/ict_sniper/run_relapse_scalper.py --paper`
  - `Restart=always`, `RestartSec=5`
  - `StandardOutput=journal`, `StandardError=journal`, `SyslogIdentifier=relapse-scalper`

### 2. `deploy/relapse-watchdog.service` (Created)
- **Purpose**: Systemd oneshot unit executing periodic health and memory auditing.
- **Key Directives**:
  - `Type=oneshot`
  - `WorkingDirectory=/root/ict_sniper`
  - `EnvironmentFile=/root/ict_sniper/.env`
  - `ExecStart=/usr/bin/python3 /root/ict_sniper/quant/hft/guard.py --oneshot`
  - `StandardOutput=journal`, `StandardError=journal`, `SyslogIdentifier=relapse-watchdog`

### 3. `deploy/relapse-watchdog.timer` (Created)
- **Purpose**: Systemd timer triggering the watchdog service at a 30-second cadence.
- **Key Directives**:
  - `OnBootSec=1min` (allows startup stabilization after host reboot)
  - `OnUnitActiveSec=30s` (triggers every 30 seconds thereafter)
  - `Unit=relapse-watchdog.service`
  - `WantedBy=timers.target`

### 4. `quant/hft/guard.py` (Modified)
- **Purpose**: Autonomous Health & Watchdog Guard for the Relapse Scalper & LLM Critic ecosystem.
- **Changes**:
  - Configured monitored services: `SCALPER_UNIT = "relapse-scalper.service"` and `CRITIC_UNIT = "stratton-llm-critic.service"`. Added `HFT_UNIT = SCALPER_UNIT` for backward compatibility.
  - Enforced resident memory ceiling `MAX_SYSTEM_RAM_MB = 2560.0` (2.5 GB resident limit on 4GB VPS host).
  - Implemented Antigravity JSON structured telemetry output schema via `emit_telemetry`:
    `{"timestamp": <ISO8601 UTC>, "agent": "watchdog_guard", "component": <str>, "event": <str>, "level": <str>, "data": <dict>}`.
  - Implemented ntfy.sh critical alert escalation: RAM ceiling breach and LLM critic failures dispatch alerts with `priority="urgent"` and tags `"rotating_light,fire,warning"`.
  - Added CLI argument parser supporting `--oneshot` (default single audit pass), `--no-notify` (suppress alerts), `--loop`, and `--interval`.

### 5. `tests/test_hft_guard.py` (Modified)
- **Purpose**: Unit tests verifying watchdog guard operations and deploy configurations.
- **Coverage (14 tests)**:
  - `test_get_system_ram_used_mb_proc_meminfo`: `/proc/meminfo` memory usage parsing (`MemTotal - MemAvailable`).
  - `test_check_critic_health_success`: HTTP 200 health check validation.
  - `test_check_critic_health_failure`: URLError / network timeout handling.
  - `test_guard_constants_and_monitored_units`: Invariant validation (`SCALPER_UNIT`, `CRITIC_UNIT`, `MAX_SYSTEM_RAM_MB`).
  - `test_guard_detects_critic_service_inactive`: Inactive critic detection and auto-restart.
  - `test_guard_detects_scalper_service_inactive`: Inactive scalper detection and auto-restart.
  - `test_guard_detects_critic_unresponsive_health`: Active service with failing endpoint detection and auto-restart.
  - `test_guard_detects_ram_ceiling_breach`: Memory ceiling (>2560 MB) breach detection.
  - `test_guard_all_systems_nominal`: Returncode 0 and zero notifications when all systems nominal.
  - `test_emit_telemetry_schema_and_agent_name`: Antigravity JSON schema and `watchdog_guard` agent validation.
  - `test_critical_alert_ntfy_escalation`: Escalation to `priority="urgent"` on RAM breach.
  - `test_deploy_systemd_unit_files`: Disk file existence and INI configuration directives validation.
  - `test_stale_telemetry_detection`: Stale feed detection and engine auto-restart.
  - `test_guard_cli_oneshot`: CLI `--oneshot --no-notify` entrypoint verification.

## Test Results
- `pytest tests/test_hft_guard.py -v`: 14 passed in 4.39s (100% pass)
- `pytest tests/test_gold_relapse_scalper.py -v`: 10 passed in 2.61s (100% pass, zero regressions)
- `python3 -m flake8 --max-line-length=120 quant/hft/guard.py tests/test_hft_guard.py`: 0 violations
