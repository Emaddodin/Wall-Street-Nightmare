# BRIEFING — 2026-09-17T00:26:00+03:30

## Mission
Lead the end-to-end design, implementation, verification, and deployment of Architecture B for the "Wall-Street-Nightmare" autonomous cryptocurrency scalping engine and deploy to VPS 82.115.21.155.

## 🔒 My Identity
- Archetype: teamwork_preview_orchestrator
- Roles: orchestrator, user_liaison, human_reporter, successor
- Working directory: /Users/mac/Desktop/TBT-Engine/.agents/orchestrator_1
- Original parent: parent
- Original parent conversation ID: 0de1a263-c916-4a19-bf53-4483747be2f8

## 🔒 My Workflow
- **Pattern**: Project
- **Scope document**: /Users/mac/Desktop/TBT-Engine/PROJECT.md
1. **Decompose**: Survey full scope with 3 Explorers, create feature inventory and milestone breakdown, and spawn parallel tracks (Implementation and E2E Testing).
2. **Dispatch & Execute**:
   - **Delegate (sub-orchestrator)**: Decompose Architecture B into modular milestones (R1-R6), spawn sub-orchestrators for milestones or run Explorer -> Worker -> Reviewer -> Challenger -> Auditor loop.
3. **On failure** (in this order):
   - Retry: nudge stuck agent or re-send task
   - Replace: spawn fresh agent with partial progress
   - Skip: proceed without (only if non-critical)
   - Redistribute: split stuck agent's remaining work
   - Redesign: re-partition decomposition
   - Escalate: report to parent (sub-orchestrators only, last resort)
4. **Succession**: At 16 spawns, write handoff.md, kill timers, spawn successor.
- **Work items**:
  1. Survey & Codebase Investigation [done]
  2. Project Decomposition & PROJECT.md [done]
  3. E2E Testing Track Initialization [in-progress]
  4. R1: Data Layer & Feature Engine (M1) [done]
  5. R2: Deterministic Alpha Setups (M2) [done]
  6. R3: Sub-10ms ML Filter Engine & 256-Dim RAG (M3) [done]
  7. R4 & R5: Risk Engine, CDP Pinning & LLM Critic (M4) [done]
  8. R6: Production VPS Deployment & Integration (M5) [in-progress]
  9. Final Acceptance & Coverage Hardening [pending]
- **Current phase**: 2 (Deployment & Final Acceptance)
- **Current focus**: Milestone 5: Production VPS Deployment Worker (82.115.21.155) and E2E Test Suite completion

## 🔒 Key Constraints
- NEVER write, modify, or create source code files directly.
- NEVER run build/test commands yourself — require workers to do so.
- NEVER investigate or explore the problem at the code level — dispatch Explorers for technical investigation.
- Audit is a binary veto: INTEGRITY VIOLATION fails milestone unconditionally.
- Never reuse a subagent after it has delivered its handoff — always spawn fresh.
- ML filter inference latency strictly under 10ms.
- VPS memory <= 2.5 GB with llama-server active.
- 100% backward compatibility for legacy .npz artifacts.

## Current Parent
- Conversation ID: 0de1a263-c916-4a19-bf53-4483747be2f8
- Updated: 2026-09-16T23:25:00+03:30

## Key Decisions Made
- Milestone 1 (R1) completed & verified (25 tests).
- Milestone 2 (R2) completed & verified (25 tests).
- Milestone 3 (R3 & R5) completed & verified (16 component tests, 6 legacy tests, P99 < 1.0ms).
- Milestone 4 (R4 & R5) completed & verified (17 tests, 39 combined tests passing).
- Dispatched Worker M5 for VPS Deployment to 82.115.21.155.

## Team Roster
| Agent | Type | Work Item | Status | Conv ID |
|-------|------|-----------|--------|---------|
| explorer_survey_1 | teamwork_preview_explorer | Survey R1 & R2 | completed | 8a80c6f9-1a89-4d48-84df-7f6c437b14e3 |
| explorer_survey_2 | teamwork_preview_explorer | Survey R3 & R5 | completed | c19ac762-cd75-4ceb-85cf-1414ed57eae7 |
| explorer_survey_3 | teamwork_preview_explorer | Survey R4 & R6 | completed | c10ffcd7-bac3-40b7-a350-90a9d5756874 |
| test_writer_e2e_1 | teamwork_preview_test_writer | E2E Test Infra & 4-Tier Test Suite | failed (EOF) | 33f32438-76b9-4825-bedd-feae07d1f0eb |
| worker_m1_datalayer | teamwork_preview_worker | Milestone 1: Data Layer & Feature Engine | completed | 738f9b95-858d-411e-b2d7-4bcd178c5c2b |
| worker_m3_ml | teamwork_preview_worker | Milestone 3: ML Filter, 256D RAG & CPCV | completed | 70c8a40e-29c5-4fc0-aaed-ebae25d56afb |
| worker_m2_alpha | teamwork_preview_worker | Milestone 2: 5 Alpha Setups & AlphaEngine | completed | 0ecc34cf-f913-42fa-8d0b-4a9100395cbd |
| worker_m4_risk_critic| teamwork_preview_worker | Milestone 4: Risk Engine, CDP Pinning & Critic | completed | ef64c0a8-1563-4db2-a946-c5adddd07cc2 |
| test_writer_e2e_2 | teamwork_preview_test_writer | Replacement E2E Test Suite Writer | in-progress | 30f10bf7-456a-4233-82de-d316e86cfdae |
| worker_m5_deployment | teamwork_preview_worker | Milestone 5: Production VPS Deployment | in-progress | aaef51ed-676d-4ffd-8a91-8073d90eb126 |

## Succession Status
- Succession required: no
- Spawn count: 10 / 16
- Pending subagents: 30f10bf7-456a-4233-82de-d316e86cfdae, aaef51ed-676d-4ffd-8a91-8073d90eb126
- Predecessor: none
- Successor: not yet spawned

## Active Timers
- Heartbeat cron: 68106074-9b56-4a6f-a92f-8aef4206f858/task-22
- Safety timer: handled by heartbeat cron
- On succession: kill all timers before spawning successor
- On context truncation: run `manage_task(Action="list")` — re-create if missing

## Artifact Index
- /Users/mac/Desktop/TBT-Engine/ORIGINAL_REQUEST.md — Authoritative user request
- /Users/mac/Desktop/TBT-Engine/PROJECT.md — Master project architecture and feature inventory
- /Users/mac/Desktop/TBT-Engine/TEST_INFRA.md — E2E Test Infrastructure Specification
- /Users/mac/Desktop/TBT-Engine/.agents/orchestrator_1/DISPATCH.md — Initial dispatch instructions
- /Users/mac/Desktop/TBT-Engine/.agents/orchestrator_1/progress.md — Liveness & execution progress
- /Users/mac/Desktop/TBT-Engine/.agents/worker_m1_datalayer/handoff.md — M1 handoff
- /Users/mac/Desktop/TBT-Engine/.agents/worker_m2_alpha/handoff.md — M2 handoff
- /Users/mac/Desktop/TBT-Engine/.agents/worker_m3_ml/handoff.md — M3 handoff
- /Users/mac/Desktop/TBT-Engine/.agents/worker_m4_risk_critic/handoff.md — M4 handoff
