"""
tests/test_hft_guard.py
=======================
Unit tests for engine.guard monitoring:
- LLM Critic service status and health check (/health endpoint)
- Scalper engine service status and auto-healing
- VPS total system memory ceiling (<= 2.5 GB / 2560 MB)
- Antigravity JSON structured telemetry schema and agent identity
- ntfy.sh alert escalation for critical faults
- Systemd unit configuration files validation
- Stale telemetry detection and CLI flags
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from unittest import mock
import urllib.error

import engine.guard as guard


def test_get_system_ram_used_mb_proc_meminfo(tmp_path: Path):
    """Test accurate computation of system RAM used: MemTotal - MemAvailable."""
    meminfo_content = (
        "MemTotal:        3915000 kB\n"
        "MemFree:          500000 kB\n"
        "MemAvailable:    2500000 kB\n"
        "Buffers:          100000 kB\n"
        "Cached:          1300000 kB\n"
    )
    fake_meminfo = tmp_path / "meminfo"
    fake_meminfo.write_text(meminfo_content)

    with mock.patch("engine.guard.Path") as mock_path:
        mock_path.side_effect = lambda p: fake_meminfo if p == "/proc/meminfo" else Path(p)
        used = guard.get_system_ram_used_mb()

    # (3915000 - 2500000) / 1024 = 1415000 / 1024 = 1381.8359375 MB
    expected = (3915000 - 2500000) / 1024.0
    assert abs(used - expected) < 1e-3


def test_check_critic_health_success():
    """Verify check_critic_health returns True when HTTP 200 is returned."""
    mock_resp = mock.MagicMock()
    mock_resp.status = 200
    mock_resp.getcode.return_value = 200
    mock_resp.__enter__.return_value = mock_resp
    with mock.patch("urllib.request.urlopen", return_value=mock_resp):
        assert guard.check_critic_health() is True


def test_check_critic_health_failure():
    """Verify check_critic_health returns False when HTTP request fails."""
    with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")):
        assert guard.check_critic_health() is False


def test_guard_constants_and_monitored_units():
    """Verify monitored units and memory ceiling match architectural specifications."""
    assert guard.SCALPER_UNIT == "relapse-scalper.service"
    assert guard.CRITIC_UNIT == "stratton-llm-critic.service"
    assert guard.MAX_SYSTEM_RAM_MB == 2560.0


def test_guard_detects_critic_service_inactive():
    """Verify run_guard detects inactive stratton-llm-critic service and triggers restart."""
    with mock.patch("engine.guard.is_service_active") as mock_active, \
         mock.patch("engine.guard.restart_service") as mock_restart, \
         mock.patch("engine.guard.check_venue", return_value=True), \
         mock.patch("engine.guard.get_system_ram_used_mb", return_value=1200.0), \
         mock.patch("engine.guard.push_ntfy"):

        def service_active(unit):
            if unit == guard.CRITIC_UNIT:
                return False
            return True

        mock_active.side_effect = service_active
        mock_restart.return_value = True

        ret = guard.run_guard(notify=False)
        assert ret == 1
        mock_restart.assert_any_call(guard.CRITIC_UNIT)


def test_guard_detects_scalper_service_inactive():
    """Verify run_guard detects inactive relapse-scalper service and triggers restart."""
    with mock.patch("engine.guard.is_service_active") as mock_active, \
         mock.patch("engine.guard.restart_service") as mock_restart, \
         mock.patch("engine.guard.check_critic_health", return_value=True), \
         mock.patch("engine.guard.check_venue", return_value=True), \
         mock.patch("engine.guard.get_system_ram_used_mb", return_value=1200.0), \
         mock.patch("engine.guard.push_ntfy"):

        def service_active(unit):
            if unit == guard.SCALPER_UNIT:
                return False
            return True

        mock_active.side_effect = service_active
        mock_restart.return_value = True

        ret = guard.run_guard(notify=False)
        assert ret == 1
        mock_restart.assert_any_call(guard.SCALPER_UNIT)


def test_guard_detects_critic_unresponsive_health():
    """Verify run_guard restarts critic when service is active but health check fails."""
    with mock.patch("engine.guard.is_service_active", return_value=True), \
         mock.patch("engine.guard.check_critic_health", return_value=False), \
         mock.patch("engine.guard.restart_service") as mock_restart, \
         mock.patch("engine.guard.check_venue", return_value=True), \
         mock.patch("engine.guard.get_system_ram_used_mb", return_value=1200.0), \
         mock.patch("engine.guard.push_ntfy"):

        mock_restart.return_value = True
        ret = guard.run_guard(notify=False)
        assert ret == 1
        mock_restart.assert_any_call(guard.CRITIC_UNIT)


def test_guard_detects_ram_ceiling_breach():
    """Verify run_guard detects RAM usage breaching 2.5 GB ceiling (2560 MB)."""
    with mock.patch("engine.guard.is_service_active", return_value=True), \
         mock.patch("engine.guard.check_critic_health", return_value=True), \
         mock.patch("engine.guard.check_venue", return_value=True), \
         mock.patch("engine.guard.get_system_ram_used_mb", return_value=2700.0), \
         mock.patch("engine.guard.push_ntfy"):

        ret = guard.run_guard(notify=False)
        assert ret == 1


def test_guard_all_systems_nominal():
    """Verify run_guard returns 0 (nominal) when all components are operating within limits."""
    with mock.patch("engine.guard.is_service_active", return_value=True), \
         mock.patch("engine.guard.check_critic_health", return_value=True), \
         mock.patch("engine.guard.check_venue", return_value=True), \
         mock.patch("engine.guard.get_system_ram_used_mb", return_value=1650.0), \
         mock.patch("engine.guard.push_ntfy") as mock_push:

        ret = guard.run_guard(notify=False)
        assert ret == 0
        mock_push.assert_not_called()


def test_emit_telemetry_schema_and_agent_name(capsys):
    """Verify emit_telemetry generates Antigravity-compliant JSON payload."""
    payload = guard.emit_telemetry(
        component="WatchdogTest",
        event="UNIT_TEST_EVENT",
        data={"metric": 42.0, "status": "OK"},
        level="INFO",
    )

    assert "timestamp" in payload
    assert payload["agent"] == "watchdog_guard"
    assert payload["component"] == "WatchdogTest"
    assert payload["event"] == "UNIT_TEST_EVENT"
    assert payload["level"] == "INFO"
    assert payload["data"] == {"metric": 42.0, "status": "OK"}

    # Validate ISO timestamp
    ts = datetime.fromisoformat(payload["timestamp"])
    assert ts.year >= 2026

    # Verify output stream
    captured = capsys.readouterr()
    stdout_line = json.loads(captured.out.strip())
    assert stdout_line == payload


def test_critical_alert_ntfy_escalation():
    """Verify that critical faults trigger ntfy.sh escalation with urgent priority."""
    with mock.patch("engine.guard.is_service_active", return_value=True), \
         mock.patch("engine.guard.check_critic_health", return_value=True), \
         mock.patch("engine.guard.check_venue", return_value=True), \
         mock.patch("engine.guard.get_system_ram_used_mb", return_value=2800.0), \
         mock.patch("engine.guard.push_ntfy") as mock_push:

        ret = guard.run_guard(notify=True)
        assert ret == 1
        mock_push.assert_called_once()
        args, kwargs = mock_push.call_args
        assert kwargs.get("priority") == "urgent"
        assert "fire" in kwargs.get("tags", "") or "rotating_light" in kwargs.get("tags", "")


def test_deploy_systemd_unit_files():
    """Verify that deploy/ systemd units and timer exist and satisfy all invariants."""
    repo_root = Path(__file__).resolve().parents[1]

    scalper_svc = repo_root / "deploy" / "relapse-scalper.service"
    watchdog_svc = repo_root / "deploy" / "relapse-watchdog.service"
    watchdog_tmr = repo_root / "deploy" / "relapse-watchdog.timer"

    assert scalper_svc.exists(), f"Missing {scalper_svc}"
    assert watchdog_svc.exists(), f"Missing {watchdog_svc}"
    assert watchdog_tmr.exists(), f"Missing {watchdog_tmr}"

    # Verify relapse-scalper.service invariants
    scalper_content = scalper_svc.read_text()
    assert "MemoryMax=600M" in scalper_content
    assert "CPUQuota=100%" in scalper_content
    assert "Restart=always" in scalper_content
    assert "RestartSec=5" in scalper_content
    assert "WorkingDirectory=/root/ict_sniper" in scalper_content
    assert "EnvironmentFile=/root/ict_sniper/.env" in scalper_content
    assert "run_relapse_scalper.py --paper" in scalper_content

    # Verify relapse-watchdog.service invariants
    watchdog_svc_content = watchdog_svc.read_text()
    assert "Type=oneshot" in watchdog_svc_content
    assert "engine/guard.py --oneshot" in watchdog_svc_content
    assert "WorkingDirectory=/root/ict_sniper" in watchdog_svc_content
    assert "EnvironmentFile=/root/ict_sniper/.env" in watchdog_svc_content

    # Verify relapse-watchdog.timer invariants
    timer_content = watchdog_tmr.read_text()
    assert "OnBootSec=1min" in timer_content
    assert "OnUnitActiveSec=30s" in timer_content
    assert "Unit=relapse-watchdog.service" in timer_content
    assert "WantedBy=timers.target" in timer_content


def test_stale_telemetry_detection(tmp_path: Path):
    """Verify run_guard detects stale telemetry and restarts the scalper unit."""
    fake_state = tmp_path / "scalper_telemetry.json"
    stale_time = 1000.0  # long ago
    fake_state.write_text(json.dumps({"updated_at": stale_time, "equity": 65.0}))

    with mock.patch("engine.guard.STATE_FILE", fake_state), \
         mock.patch("engine.guard.is_service_active", return_value=True), \
         mock.patch("engine.guard.check_critic_health", return_value=True), \
         mock.patch("engine.guard.check_venue", return_value=True), \
         mock.patch("engine.guard.get_system_ram_used_mb", return_value=1200.0), \
         mock.patch("engine.guard.restart_service") as mock_restart, \
         mock.patch("engine.guard.push_ntfy"):

        mock_restart.return_value = True
        ret = guard.run_guard(notify=False)
        assert ret == 1
        mock_restart.assert_any_call(guard.SCALPER_UNIT)


def test_guard_cli_oneshot(monkeypatch):
    """Verify CLI invocation with --oneshot --no-notify exits cleanly."""
    monkeypatch.setattr(sys, "argv", ["guard.py", "--oneshot", "--no-notify"])
    with mock.patch("engine.guard.run_guard", return_value=0) as mock_run:
        exit_code = guard.main()
        assert exit_code == 0
        mock_run.assert_called_once_with(notify=False)
