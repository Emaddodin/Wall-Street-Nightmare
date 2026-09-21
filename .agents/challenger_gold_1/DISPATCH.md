# Dispatch for challenger_gold_1

Role: Empirical Adversarial Challenger 1 (Invariants & Compounding Stress Verifier)
Authoritative Specification: /Users/mac/Desktop/TBT-Engine/.agents/ORIGINAL_REQUEST.md
Project Specification: /Users/mac/Desktop/TBT-Engine/.agents/orchestrator_2/PROJECT.md
Working Directory: /Users/mac/Desktop/TBT-Engine/.agents/challenger_gold_1

Task:
1. Write and execute adversarial stress tests targeting:
   - Margin ceiling invariant (<= 20% equity) across boundary equities ($1, $65, $100, $1,000, $10,000, $1,000,000) and volatile gold price shocks ($1,500 to $4,000/oz).
   - Dollar risk per trade invariant (< 0.86% and strictly < 1.0% equity).
   - 5% daily max drawdown killswitch boundary conditions (e.g. exactly at 4.99% vs 5.01%, midnight UTC rollover resets).
   - Monte Carlo simulation numerical stability under edge conditions.
2. Deliver empirical results and verdict: APPROVE or REJECT in handoff.md.

## 2026-09-17T19:43:16Z

You are challenger_gold_1.
Your working directory is /Users/mac/Desktop/TBT-Engine/.agents/challenger_gold_1.
Your parent is orchestrator_2 (convId: d8cde56b-142d-4ad0-b360-6f180e2c8eaa).

MANDATORY: Read the authoritative specification at /Users/mac/Desktop/TBT-Engine/.agents/ORIGINAL_REQUEST.md.
Also read:
- /Users/mac/Desktop/TBT-Engine/.agents/orchestrator_2/PROJECT.md
- /Users/mac/Desktop/TBT-Engine/.agents/challenger_gold_1/DISPATCH.md

Task:
Empirically and adversarially stress-test:
1. Margin ceiling invariant (<= 20% equity) across boundary equities ($1, $65, $100, $1,000, $10,000, $1,000,000) and volatile gold price shocks ($1,500 to $4,000/oz).
2. Dollar risk per trade invariant (< 0.86% and strictly < 1.0% equity).
3. 5% daily max drawdown killswitch boundary conditions (exact intraday threshold, UTC midnight rollover).
4. Monte Carlo scaling engine numerical stability under extreme parameters.

Write an empirical stress test script in your directory, execute it, and record your findings and binary verdict (APPROVE or REJECT) in handoff.md. Send completion message to parent.
