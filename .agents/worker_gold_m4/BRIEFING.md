# BRIEFING — 2026-09-17T19:22:14Z

## Mission
Deliver production-ready systemd unit files, harden quant/hft/guard.py with Antigravity JSON telemetry and ntfy.sh alert escalation for relapse-scalper and stratton-llm-critic services, and verify 100% test pass.

## 🔒 My Identity
- Archetype: worker_gold_m4
- Roles: implementer, qa, specialist
- Working directory: /Users/mac/Desktop/TBT-Engine/.agents/worker_gold_m4
- Original parent: d8cde56b-142d-4ad0-b360-6f180e2c8eaa
- Milestone: M4 VPS Systemd Suite & Watchdog Hardening

## 🔒 Key Constraints
- File Ownership (Exclusive): deploy/relapse-scalper.service, deploy/relapse-watchdog.service, deploy/relapse-watchdog.timer, quant/hft/guard.py, tests/test_hft_guard.py
- Production-ready systemd units matching specs
- Resident RAM ceiling <= 2560.0 MB (2.5 GB)
- Antigravity JSON structured telemetry: {"timestamp": ..., "agent": "watchdog_guard", "component": ..., "event": ..., "level": ..., "data": ...}
- ntfy.sh escalation for critical alerts
- 100% test pass in tests/test_hft_guard.py without regressions

## Current Parent
- Conversation ID: d8cde56b-142d-4ad0-b360-6f180e2c8eaa
- Updated: 2026-09-17T19:22:14Z

## Task Summary
- **What to build**: Production systemd unit files (`relapse-scalper.service`, `relapse-watchdog.service`, `relapse-watchdog.timer`), update `quant/hft/guard.py` for `relapse-scalper.service` and `stratton-llm-critic.service`, structured Antigravity telemetry, ntfy alerting, test suite in `tests/test_hft_guard.py`.
- **Success criteria**: All systemd units created and syntactically correct; guard monitors the correct units; RAM limit 2560 MB enforced; Antigravity JSON emitted; ntfy.sh escalation triggered on critical faults; 100% test pass; handoff report written.
- **Interface contracts**: PROJECT.md & ORIGINAL_REQUEST.md
- **Code layout**: deploy/, quant/hft/guard.py, tests/test_hft_guard.py

## Key Decisions Made
- Use standard systemd unit configuration aligned with existing stratton-llm-critic.service
- Implement `emit_telemetry` helper in `quant/hft/guard.py` producing `{"timestamp": ..., "agent": "watchdog_guard", ...}`
- Support `--oneshot` and `--no-notify` CLI flags in `quant/hft/guard.py`

## Artifact Index
- deploy/relapse-scalper.service — Systemd service running run_relapse_scalper.py --paper
- deploy/relapse-watchdog.service — Oneshot watchdog systemd service running guard.py --oneshot
- deploy/relapse-watchdog.timer — Systemd timer triggering watchdog every 30 seconds
- quant/hft/guard.py — VPS Watchdog & Health Guard
- tests/test_hft_guard.py — Guard unit tests

## Change Tracker
- **Files modified**:
  - `deploy/relapse-scalper.service`: systemd service running run_relapse_scalper.py --paper with MemoryMax=600M
  - `deploy/relapse-watchdog.service`: oneshot service executing guard.py --oneshot
  - `deploy/relapse-watchdog.timer`: systemd timer running every 30s
  - `quant/hft/guard.py`: updated monitored units to relapse-scalper and stratton-llm-critic, Antigravity telemetry, ntfy escalation
  - `tests/test_hft_guard.py`: enhanced test suite with 14 tests
- **Build status**: PASS (14/14 tests passing)
- **Pending issues**: none

## Quality Status
- **Build/test result**: 14 passed in 4.39s
- **Lint status**: clean (0 flake8 violations)
- **Tests added/modified**: 9 new tests added to tests/test_hft_guard.py

## Loaded Skills
- None
