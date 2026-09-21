# Handoff Report: Milestone M4 — VPS Systemd Production Suite & Watchdog Hardening

**Agent**: `worker_gold_m4`  
**Parent**: `orchestrator_2` (convId: `d8cde56b-142d-4ad0-b360-6f180e2c8eaa`)  
**Working Directory**: `/Users/mac/Desktop/TBT-Engine/.agents/worker_gold_m4`  
**Handoff Type**: Hard Handoff (Task Complete)  

---

## 1. Observation

1. **Systemd Production Units (`deploy/`)**:
   - `deploy/relapse-scalper.service`:
     - Runs `/usr/bin/python3 /root/ict_sniper/run_relapse_scalper.py --paper`.
     - Invariants: `WorkingDirectory=/root/ict_sniper`, `EnvironmentFile=/root/ict_sniper/.env`, `MemoryMax=600M`, `CPUQuota=100%`, `Restart=always`, `RestartSec=5`.
   - `deploy/relapse-watchdog.service`:
     - Runs `/usr/bin/python3 /root/ict_sniper/quant/hft/guard.py --oneshot`.
     - Invariants: `Type=oneshot`, `WorkingDirectory=/root/ict_sniper`, `EnvironmentFile=/root/ict_sniper/.env`.
   - `deploy/relapse-watchdog.timer`:
     - Invariants: `OnBootSec=1min`, `OnUnitActiveSec=30s`, `Unit=relapse-watchdog.service`, `WantedBy=timers.target`.

2. **Watchdog Guard Implementation (`quant/hft/guard.py`)**:
   - Monitored units configured at lines 30–32:
     ```python
     SCALPER_UNIT = "relapse-scalper.service"
     CRITIC_UNIT = "stratton-llm-critic.service"
     HFT_UNIT = SCALPER_UNIT  # Backward compatibility alias
     ```
   - Memory ceiling enforced at line 34:
     ```python
     MAX_SYSTEM_RAM_MB = 2560.0  # 2.5 GB resident memory ceiling
     ```
   - Antigravity structured JSON telemetry implemented at lines 39–59:
     ```python
     def emit_telemetry(component: str, event: str, data: Dict[str, Any], level: str = "INFO") -> Dict[str, Any]:
         payload = {
             "timestamp": datetime.now(timezone.utc).isoformat(),
             "agent": "watchdog_guard",
             "component": component,
             "event": event,
             "level": level,
             "data": data,
         }
     ```
   - ntfy.sh escalation for critical alerts implemented at lines 282–305:
     ```python
     priority = "urgent" if is_critical_escalation else "high"
     tags = "rotating_light,fire,warning" if is_critical_escalation else "warning"
     ```
   - CLI execution support with `--oneshot`, `--no-notify`, and `--loop` implemented at lines 326–354.

3. **Test Suite Verification (`pytest tests/test_hft_guard.py -v`)**:
   - Command: `pytest tests/test_hft_guard.py -v`
   - Output:
     ```
     tests/test_hft_guard.py::test_get_system_ram_used_mb_proc_meminfo PASSED [  7%]
     tests/test_hft_guard.py::test_check_critic_health_success PASSED         [ 14%]
     tests/test_hft_guard.py::test_check_critic_health_failure PASSED         [ 21%]
     tests/test_hft_guard.py::test_guard_constants_and_monitored_units PASSED [ 28%]
     tests/test_hft_guard.py::test_guard_detects_critic_service_inactive PASSED [ 35%]
     tests/test_hft_guard.py::test_guard_detects_scalper_service_inactive PASSED [ 42%]
     tests/test_hft_guard.py::test_guard_detects_critic_unresponsive_health PASSED [ 50%]
     tests/test_hft_guard.py::test_guard_detects_ram_ceiling_breach PASSED    [ 57%]
     tests/test_hft_guard.py::test_guard_all_systems_nominal PASSED           [ 64%]
     tests/test_hft_guard.py::test_emit_telemetry_schema_and_agent_name PASSED [ 71%]
     tests/test_hft_guard.py::test_critical_alert_ntfy_escalation PASSED      [ 78%]
     tests/test_hft_guard.py::test_deploy_systemd_unit_files PASSED           [ 85%]
     tests/test_hft_guard.py::test_stale_telemetry_detection PASSED           [ 92%]
     tests/test_hft_guard.py::test_guard_cli_oneshot PASSED                   [100%]
     ============================== 14 passed in 4.39s ==============================
     ```

4. **Regression Testing (`pytest tests/test_gold_relapse_scalper.py -v`)**:
   - Command: `pytest tests/test_gold_relapse_scalper.py -v`
   - Output:
     ```
     ============================== 10 passed in 2.61s ==============================
     ```

5. **Linting Check (`flake8 --max-line-length=120 quant/hft/guard.py tests/test_hft_guard.py`)**:
   - Command: `python3 -m flake8 --max-line-length=120 quant/hft/guard.py tests/test_hft_guard.py`
   - Output: Exit code 0, 0 violations.

---

## 2. Logic Chain

1. **Systemd Suite Architecture**:
   - Observation 1 defines the systemd configuration for running the scalper runtime (`relapse-scalper.service`), watchdog checks (`relapse-watchdog.service`), and periodic trigger (`relapse-watchdog.timer`).
   - Cgroup limits (`MemoryMax=600M`, `CPUQuota=100%`) isolate the Python execution process so that even under unexpected memory allocation, it cannot starve the local `llama-server` (`MemoryMax=1800M`) or the OS baseline (~500 MB).
   - Together, 1800 MB + 600 MB = 2400 MB, which strictly respects the physical 4GB host memory and sits safely below the 2,560 MB watchdog ceiling.

2. **Watchdog Monitoring & Escalation**:
   - Observation 2 demonstrates that `quant/hft/guard.py` monitors `relapse-scalper.service` and `stratton-llm-critic.service`.
   - When a service fails or RAM exceeds 2560 MB, `is_critical_escalation` is set to `True`.
   - This sets ntfy priority to `urgent` (bypassing do-not-disturb on mobile endpoints) and emits Antigravity JSON structured telemetry line with `"agent": "watchdog_guard"`.
   - Self-healing automatically restarts the failed service via `systemctl restart`.

3. **Empirical Verification**:
   - Observation 3 confirms all 14 tests in `tests/test_hft_guard.py` pass cleanly.
   - Observation 4 confirms that changes in `quant/hft/guard.py` do not break existing engine tests in `tests/test_gold_relapse_scalper.py`.
   - Observation 5 confirms compliance with codebase style guidelines.

---

## 3. Caveats

1. **Host-Level Cgroup Enforcement**:
   - `MemoryMax=600M` and `CPUQuota=100%` directives require systemd cgroups v2 enabled on the target Linux host (`/sys/fs/cgroup`). On systems where cgroups v2 is unmounted or in containerized mock environments, systemd will log a warning but still run the service.
2. **Local llama-server Endpoint**:
   - `CRITIC_HEALTH_URL` defaults to `http://127.0.0.1:8080/health`. In testing environments without a live `llama-server` process running, unit tests mock `urlopen` to simulate both nominal 200 responses and failure conditions.

---

## 4. Conclusion

Milestone **M4 VPS Systemd Suite & Watchdog Hardening** is complete:
- Production-ready systemd units and timer created in `deploy/`.
- `quant/hft/guard.py` updated with `relapse-scalper.service`, `stratton-llm-critic.service`, 2560 MB RAM ceiling, Antigravity JSON structured telemetry (`agent: "watchdog_guard"`), urgent ntfy escalation, and `--oneshot` CLI flag.
- 100% pass across 14 unit tests in `tests/test_hft_guard.py` and 10 tests in `tests/test_gold_relapse_scalper.py`.

---

## 5. Verification Method

To independently verify this implementation, run:

```bash
# 1. Run all watchdog and systemd unit tests
pytest tests/test_hft_guard.py -v

# 2. Run gold relapse scalper tests to verify zero regressions
pytest tests/test_gold_relapse_scalper.py -v

# 3. Verify lint cleanliness
python3 -m flake8 --max-line-length=120 quant/hft/guard.py tests/test_hft_guard.py

# 4. Verify watchdog oneshot execution
python3 quant/hft/guard.py --oneshot --no-notify
```

**Invalidation Conditions**:
- If any unit test in `tests/test_hft_guard.py` fails.
- If `quant/hft/guard.py` emits telemetry without `"agent": "watchdog_guard"`.
- If `deploy/relapse-scalper.service` lacks `MemoryMax=600M` or `Restart=always`.
