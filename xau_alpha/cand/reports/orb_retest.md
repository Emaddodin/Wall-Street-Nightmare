# H6: opening-range break (and retest) (`cand/orb_retest.py`)

Date: 2026-09-30. Hypothesis H6 from `recon/SYNTHESIS.md` section 6. Stage 1 was run by a previous agent. It was killed
by infrastructure before stage 2. This report adds a causality audit, stage 2, the finalist evaluation and the nulls.

Labels: **MEASURED** means computed here on TRAIN (2025-01-21..2025-12-31) and VALID (2026-01..05). **SOURCED** means
taken from a cited file. **OPINION** means my judgement. TEST (>= 2026-06-01) was never simulated. `orb_retest._base`
cuts every array at 2026-06-01, `sweep.run_orders` drops those orders again, and `final=True` was never used.

## 0. Verdict

**Mechanical verdict: NEAR** (VALID PF 1.20 >= 1.05, TRAIN PF 1.44 >= 1.10). **It does not pass the bar.** It fails the
random-direction null on VALID (p = 0.15) and the VALID drop-best-month check (0.96 < 1.05).

**OPINION: do not trade it, and do not spend a TEST slot on it.** MEASURED reasons:
1. **VALID loses money in R.** The average is −0.076 R per trade (sum −7.6 R, t = −0.79). The +1.66 $/oz VALID average
   comes entirely from the widest-stop quartile (risk > 21.5 $/oz: +362 pts, +8.0 R). The other three quartiles lost
   −15.7 R. Any risk-based sizing, which is what the main strategy would use, loses on VALID.
2. **In each split, the profitable side is the side of the drift.**
   - TRAIN (gold +59%): longs made +24.1 R (PF 1.69, t 2.33); shorts made +1.6 R (PF 1.18, t 0.17).
   - VALID: longs lost −11.9 R (PF 0.82) and lost R in **5 of 5 months**; shorts made +4.3 R (PF 1.92).
   - On VALID long-break days, the *opposite* (short) trade made +10.2 R.
3. **VALID does not beat any null at p <= 0.05.**
   - Random direction: p 0.154 on $/oz, p 0.73 on R.
   - Long-share-preserving permutation: p 0.199 on $/oz, p 0.70 on R.
   - Random 30-minute windows: p 0.079.
4. **It cannot serve the $13 flip phase.** 2.2% of TRAIN and 0% of VALID stops fall in [1.2, 4.0] $/oz. The capped variant
   has n = 7 TRAIN and 0 VALID.
5. **It barely fits the main strategy.**
   - The median stop is 8.35 $/oz on TRAIN and 15.35 on VALID. At the 0.01-lot minimum (1 oz), a 2% risk needs equity
     of about $420 on TRAIN and $770 on VALID (MEASURED arithmetic).
   - At $100 equity, one trade risks 8-15% of the account.

## 1. Why the previous ORB run "errored"

- SOURCED (`HANDOFF.md`, "Edge hunt results"): the hunt agent was killed 3 times by infrastructure: the session usage
  limit twice, then a DNS/API outage. It was not a strategy or engine error. Stage 1 finished, and its CSVs are intact
  and reproducible.
- MEASURED: I re-ran the top 3 stage-1 configs with the (edited) module. I got identical n, PF and t
  (for example n 232, PF 1.391, t 1.76).
- MEASURED, a latent code error left by the kill: the agent's unrun stage-2 driver `cand/orb_retest_stages.py` reads
  `results/orb_retest_s1_train.csv`. The rename step that should create that file never ran, so
  `python3 cand/orb_retest_stages.py stage2` would raise **FileNotFoundError**.
  - Fixed: it now falls back to `orb_retest_train.csv` and writes `*_s2legacy_*`.
  - It is marked SUPERSEDED. Stage 2 was run by the new `cand/orb_retest_stage2.py` instead.
- MEASURED, the environment: the machine was saturated (load average about 199, swap 2.6 GB, 214 MB of free disk).
  One `load_m1()` took 380 s of wall time for 51 s of CPU.
  - All work therefore ran in **one** warm Python process, a scratchpad job runner. It never used more than 1 process
    of the 2 allowed.
  - Stage 2 took 200 s. The finalist evaluation took 270 s.

## 2. Rules and causality audit

Rules are in the module docstring. The London window is 08:00-08:29 London local (DST-correct `lon_mod`).
- **Break:** the first M1 close beyond OR_hi + kap·ORw, or below OR_lo − kap·ORw, within `brk_h` minutes after 08:30.
- **Entry:** at the break close; the order is t = ts + 60 s and fills on the next 10 s ask/bid.
- **Stop:** far side of the OR ± pad·A, or the OR midpoint.
- **Exit:** a target at tp·R, else flat at `flat_utc`.
- **Symmetry:** longs and shorts run through the same code on the negated series.

Causality checks (MEASURED):
- **Truncation invariance.** I re-computed the signals on data cut at 5 points, including 3 mid-session cuts, for 5
  configs (all 3 windows, brk/rt/nt entries, and filt on and off). Every signal decided before a cut was identical:
  **0 failures**.
- **OR timing.** For every day, the last OR-window bar comes strictly before the first break-search bar.
- **Decision bar.** For every lon/brk signal, the decision bar comes after the OR's last bar.
- **ATRd and filter percentiles.** ATRd uses the mean daily TR of the prior 20 trading days (`shift(1)`). The filter's
  p20/p80 of ORw/ATRd come from the prior 60 valid days (`shift(1)`).
- **Calendar gating.** The HIGH/MEDIUM rows are a scheduled, ex-ante list.
- **Minor look-ahead in a loop bound only.** The retest loop uses the index of the trading day's last bar as its upper
  bound. It carries no price information.

Module changes (defaults unchanged; stage 1 reproduces exactly):
- Added `pad` (the far-stop pad in A; default 0.1).
- Added `flat_utc` (overrides the flat time for UTC-flat windows; comex keeps 12:00 ET).
- Added `nexit` (flatten `nexit` minutes before a HIGH release that falls inside the hold; −1 = off).
- Removed the unused `flat_min`.

Finding (MEASURED): the entry blackout `nb` never binds for the London window. The calendar is US-only and its
earliest HIGH row is at 12:30 UTC, while London entries happen at 07:30-10:30 UTC. `nb` 0, 30 and 60 give identical
trades. The meaningful news knob is `nexit`.

## 3. Stage 1 recap (324 configs, previous agent; MEASURED)

- 234 of 324 configs had TRAIN n < 60 and could not be ranked. **This includes every retest and trigger config**:
  rt5/rt30 fire about 45-48 times a year.
- Among the rankable configs, the median PF of lon/brk is 1.00 (median t −0.39). The far-stop, kap-0 corner is the best
  of 36 (t 1.76).
- OPINION: with about 90 rankable configs, a best t of about 1.8 is what selection alone would produce. The corner is a
  lead, not evidence.

## 4. Stage 2 (TRAIN only; 126 configs, 93 distinct outcomes; `results/orb_retest_s2_train.csv`)

Design (`cand/orb_retest_stage2.py`). Ranking is by TRAIN t of R with n >= 60. The finalist rule was fixed before any
stage-2 VALID number existed: the top TRAIN t with n >= 150.

- **Block A (72):** kap {0, 0.1} × stop {far pad 0.1, far pad 0.25, mid} × tp {0, 1, 1.5, 2} × flat {13, 16, 19 UTC}.
- **Block B (33):** the top 3 of A × be {0, 0.5R, 1R} × tmax {0, 60, 120, 240}.
- **Block C (21):** the top 3 of A+B × {nb 0, nb 60, nexit 5, brk_h 60, brk_h 240, lon+comex, filt 1}.

TRAIN t by geometry (Block A, lf_base):

| kap, stop | tp0 13/16/19 | tp1 13/16/19 | tp1.5 13/16/19 | tp2 13/16/19 |
|---|---|---|---|---|
| 0, far 0.10 | 0.33 / 0.89 / 0.86 | 1.75 / 1.76 / 1.77 | 1.48 / 1.32 / 1.32 | 0.88 / 1.22 / 1.23 |
| 0, far 0.25 | 0.27 / 0.91 / 0.92 | **1.83** / 1.77 / 1.78 | 1.47 / 1.32 / 1.32 | 1.02 / 1.36 / 1.37 |
| 0, mid | −2.41 / −1.28 / −1.11 | 0.24 / 0.24 / 0.24 | 0.66 / 0.53 / 0.53 | 0.09 / 0.19 / 0.19 |
| 0.1, far 0.10 | 0.26 / 0.66 / 0.70 | 1.67 / 1.76 / 1.71 | 0.71 / 0.82 / 0.79 | 0.32 / 0.88 / 0.85 |
| 0.1, far 0.25 | 0.21 / 0.65 / 0.74 | 1.30 / 1.35 / 1.31 | 0.62 / 0.80 / 0.78 | 0.36 / 1.03 / 0.99 |
| 0.1, mid | −2.13 / −1.24 / −0.93 | −0.61 / −0.59 / −0.59 | 0.06 / −0.07 / −0.07 | −0.96 / −0.58 / −0.58 |

MEASURED observations:
- **Stop geometry decides the result.** The far stop with a 1R target works; the mid stop does not.
- **Management only hurts.** A time stop tmax of 60 or 120 min drops t to about 0.6. Break-even at 0.5R drops it to
  about 1.05. Break-even at 1R does nothing with tp = 1R, because the target is hit first.
- **Other knobs:**
  - `nexit 5`: t 1.73.
  - brk_h 60/240: t 1.76 / 1.69.
  - filt 1: n 97, t 0.87.
  - lon+comex pooling: t 0.46 (PF 1.12, n 378); the comex signals dilute it.
- **The gain is small.** The best stage-2 t (1.83) is only 0.07 above the stage-1 leader, which is noise.

**Finalist (frozen on TRAIN):**
`{"window":"lon","filt":0,"kap":0.0,"entry":"brk","stop":"far","pad":0.25,"tp":1.0,"flat_utc":780,"be":0.0,"tmax":0,"brk_h":120,"nb":30,"nexit":-1}`.
- The rule's literal pick was a 6-way tie of identical trade sets (nb 0/30/60 and be 0/1R). I froze the canonical
  nb = 30, be = 0, which gives the same trades.
- In plain words: enter at the first M1 close outside the 08:00-08:29 London range within 2 h. The stop is 0.25 ATR
  beyond the far side of the range. The target is 1R. Otherwise flat at 13:00 UTC, before the 12:30/13:30 UTC US data.

## 5. Finalist results (MEASURED; `results/orb_retest_final_eval.json`)

| | n | PF | avg $/oz | avg R | sum R | t (R) | halves PF |
|---|---|---|---|---|---|---|---|
| TRAIN lf_base | 232 | **1.441** | +1.529 | +0.111 | +25.7 | 1.83 | 1.417 / 1.464 |
| TRAIN lf_harsh | 232 | 1.242 | +0.921 | +0.036 | +8.2 | 0.60 | 1.235 / 1.248 |
| TRAIN mid (zero cost) | 232 | 1.588 | +1.922 | +0.156 | +36.2 | 2.54 | 1.517 / 1.656 |
| VALID lf_base | 101 | **1.200** | +1.655 | **−0.076** | −7.6 | −0.79 | 1.244 / 1.154 |
| VALID lf_harsh | 101 | 1.109 | +0.947 | −0.118 | −12.0 | −1.26 | 1.138 / 1.080 |
| VALID mid | 101 | 1.240 | +1.947 | −0.058 | −5.9 | −0.61 | 1.285 / 1.193 |

- Exits: TRAIN 117 target / 84 stop / 31 time; VALID 43 / 49 / 9.
- Trade rate: about 1 trade per trading day.

**Pass bar (SYNTHESIS section 5):**

| criterion | value | ok |
|---|---|---|
| n TRAIN >= 150 / VALID >= 60 | 232 / 101 | yes |
| lf_base PF >= 1.15 and avg >= +0.15 $/oz, TRAIN | 1.441, +1.53 | yes |
| same, VALID | 1.200, +1.66 (but −0.076 R) | yes on $/oz |
| lf_harsh VALID PF >= 1.0 | 1.109 | yes |
| both TRAIN halves PF > 1 | 1.417 / 1.464 | yes |
| beats random direction on VALID, p <= 0.05 (200 seeds) | p = 0.154 ($/oz), 0.73 (R) | **no** |
| neighbour median VALID PF >= 1.05 | 1.14 (10 single-parameter moves) | yes |
| PF without the best month >= 1.05 | TRAIN 1.272, VALID **0.957** | **no** |

## 6. Is the edge just long beta? (MEASURED; `results/orb_retest_final_beta.json`)

Market drift: TRAIN 2710 → 4319 (+59.4%); VALID 4330 → 4540 (+4.9%). On the VALID *signal days*, however, the
08:30-13:00 window drifted down: the same orders forced always-short made +14.5 R, and forced always-long lost −18.0 R.

**Same-day 2×2.** Each cell compares the break side with the opposite side on the same signal days (sum R).

| split, cost | long-break days: long / short | short-break days: short / long |
|---|---|---|
| TRAIN lf_base | **+24.1** / −35.1 (n 130) | **+1.6** / −9.9 (n 102) |
| TRAIN mid | +31.1 / −31.0 | +5.1 / −5.0 |
| VALID lf_base | −11.9 / **+10.2** (n 52) | **+4.3** / −6.0 (n 49) |
| VALID mid | −11.1 / +11.2 | +5.2 / −5.1 |

- **TRAIN.** The break side beat the opposite side on both kinds of day. So in 2025 there was some directional
  information beyond long beta. It was small on the short side: +0.05 R per trade gross, t 0.17 net.
  - Longs vs shorts by half-year: 2025H1 longs PF 1.77 and shorts 1.05; 2025H2 longs 1.64 and shorts 1.31
    (short avg R +0.004).
  - The long-share-preserving permutation null gives p 0.005 on $/oz and 0.015 on R.
- **VALID.** Upside breaks were *wrong* (the short side won on long-break days). The short side won on both kinds of
  day, so VALID's positive $/oz comes from short beta plus a few high-volatility days. There is no directional skill.
- **Hit rate.** The share of trades where the break direction matched the sign of the move from decision to flat time
  is 0.517 on TRAIN and 0.505 on VALID. That is a coin flip.
- **Answer.** The TRAIN edge is mostly long beta (93% of TRAIN R comes from longs) plus a small directional component
  that did not survive into VALID.

## 7. Null tests (lf_base unless noted; MEASURED)

| null | TRAIN | VALID |
|---|---|---|
| random direction (`nulltest.random_direction`, 200 seeds), $/oz | PF median 0.92, p95 1.13; **p 0.005** | PF median 0.97, p95 1.42; **p 0.154** |
| random direction, R | p 0.005 | p 0.73 |
| direction permutation (keeps the long share; 200 draws), $/oz | p 0.005 | p 0.199 |
| direction permutation, R | p 0.015 | p 0.70 |
| random 30-min window, 01:00-15:30 UTC, matched days and horizon (330 min; 100 seeds), lf_base | PF median 0.95; p 0.020 | PF median 0.86; p 0.079 |
| random window, mid | PF median 1.04; p 0.020 | PF median 0.90; p 0.089 |

The VALID null distributions are wide (random-direction PF p95 1.42) because a few 25-90 $/oz-stop days dominate the
points. That is the same fragility the R numbers show.

## 8. Monthly table (lf_base, R per 1 oz; `results/orb_retest_final_monthly.csv`)

| month | split | n | L/S | sum R | R long | R short | avg $/oz | PF | avg risk |
|---|---|---|---|---|---|---|---|---|---|
| 2025-01 | T | 9 | 5/4 | +2.52 | +2.75 | −0.23 | +1.65 | 1.83 | 5.8 |
| 2025-02 | T | 18 | 11/7 | −0.82 | +2.34 | −3.15 | −0.27 | 0.92 | 7.3 |
| 2025-03 | T | 19 | 12/7 | +7.11 | +6.42 | +0.68 | +2.93 | 2.78 | 7.5 |
| 2025-04 | T | 20 | 9/11 | −1.59 | −0.31 | −1.28 | −0.43 | 0.92 | 12.0 |
| 2025-05 | T | 21 | 11/10 | +8.24 | +4.59 | +3.65 | +3.84 | 2.26 | 13.0 |
| 2025-06 | T | 19 | 13/6 | +0.35 | −1.37 | +1.72 | +0.21 | 1.05 | 8.8 |
| 2025-07 | T | 23 | 10/13 | +4.30 | +3.43 | +0.87 | +1.47 | 1.60 | 6.7 |
| 2025-08 | T | 20 | 10/10 | −4.98 | −3.91 | −1.06 | −2.39 | 0.46 | 7.0 |
| 2025-09 | T | 22 | 12/10 | +4.74 | +7.97 | −3.23 | +2.32 | 1.82 | 8.9 |
| 2025-10 | T | 22 | 13/9 | +9.87 | +6.12 | +3.74 | +7.05 | 3.15 | 15.4 |
| 2025-11 | T | 19 | 13/6 | −0.68 | −1.57 | +0.89 | +1.29 | 1.34 | 15.1 |
| 2025-12 | T | 20 | 11/9 | −3.35 | −2.36 | −1.00 | −0.18 | 0.96 | 10.7 |
| 2026-01 | V | 20 | 9/11 | −8.17 | −2.01 | −6.16 | −2.30 | 0.74 | 16.2 |
| 2026-02 | V | 20 | 12/8 | −0.82 | −2.70 | +1.87 | +3.44 | 1.35 | 24.6 |
| 2026-03 | V | 21 | 7/14 | +5.89 | −0.09 | +5.98 | +9.39 | 2.38 | 26.4 |
| 2026-04 | V | 20 | 13/7 | +0.64 | −3.17 | +3.80 | +0.60 | 1.08 | 17.0 |
| 2026-05 | V | 20 | 11/9 | −5.18 | −3.98 | −1.20 | −3.24 | 0.61 | 14.0 |

TRAIN is positive in 7 of 12 months in R. VALID is positive in 2 of 5, and the only large month is March 2026, with an
average stop of 26 $/oz.

## 9. Stop distance and flip eligibility (MEASURED)

- **Risk percentiles** (5/10/25/50/75/90/95, $/oz):
  - TRAIN: 4.5 / 5.3 / 6.6 / 8.4 / 11.6 / 16.6 / 20.8.
  - VALID: 9.3 / 9.8 / 12.9 / 15.4 / 21.5 / 34.9 / 47.6.
  - The VALID median is double TRAIN's: the OR scales with 2026 volatility.
- **Flip band [1.2, 4.0] $/oz:**
  - TRAIN: 5 of 232 trades (2.2%).
  - VALID: 0 of 101.
  - The `cap='skip'` variant has TRAIN n 7 (PF 2.09) and VALID n 0.
  - **H6 has nothing to offer the $13 flip phase.**
- **R by risk quartile on VALID:** −5.1 / −8.3 / −2.3 / **+8.0** R. Only the widest-stop quarter made money.
- **Main strategy** (equity >= $100, about 2% risk). With 1 oz minimum size and a median stop of 8-15 $/oz, 2% risk
  needs roughly $420-770 of equity. Below that, each trade risks 8-15% of equity.

## 10. Robustness: neighbours and entry arms (VALID lf_base; MEASURED)

Single-parameter moves from the finalist (VALID PF):

| move | VALID PF |
|---|---|
| kap 0.1 | 1.13 |
| tp 0 (time exit only) | 1.23 |
| tp 1.5 | 1.16 |
| flat 16:00 UTC | 1.14 |
| be 0.5R | 1.04 |
| tmax 60 | 0.82 |
| brk_h 60 | 1.30 |
| brk_h 240 | 1.14 |
| pad 0.1 | 1.20 |
| mid stop | 0.75 |

The median is **1.14**. These are all $/oz PFs, so they carry the same wide-stop weighting.

Entry arms at the finalist geometry (TRAIN n / PF → VALID n / PF, lf_base):

| arm | TRAIN | VALID |
|---|---|---|
| brk | 232 / 1.44 | 101 / 1.20 |
| rt5 | 45 / 1.92 | 16 / 1.16 |
| rt30 | 48 / 1.83 | 20 / 0.98 |
| nt5 | 102 / 1.55 | 44 / 0.97 |
| nt30 | 113 / 1.53 | 52 / 0.90 |

The video-style retest entry is far too rare to test (about 45 a year). It has no VALID advantage over the at-break
entry.

Context (reference only, not re-selected), the stage-1 time-exit twin {far pad 0.1, tp 0, flat 16:00 UTC}:
- VALID PF 1.52, +8.7 R.
- Built on VALID shorts: +35.0 R, PF 3.11. VALID longs lost −26.3 R (PF 0.50).
- TRAIN R came from longs: +31.0 R, while shorts made −2.2 R.
- It is the same drift story, magnified by the 8-hour hold.

## 11. Configs tried and caveats

- **Selection configs:** 324 (stage 1) + 126 (stage 2) = **450**. Stage 2 had 93 distinct trade sets. This is slightly
  above SYNTHESIS's "about 400".
- **Diagnostics** (no selection): 10 neighbours, 6 arms × 2 costs, 3 context configs, 100 random-window seeds,
  200 random-direction seeds and 200 permutation draws per split.
- **The TRAIN p-values are for the selected config**, not adjusted for selecting 1 of 450 (OPINION: after adjustment
  they are unremarkable).
- **Costs:** lf_base and lf_harsh are models (SOURCED `recon/broker_costs.md`), not measured LiteFinance fills.
- **What would change my mind (OPINION):** a drift-neutral version that works in R on both splits. One example is
  taking only breaks against the prior 20-day trend, tested as a new pre-registered hypothesis rather than a re-mine of
  these 450.

## 12. Files

- Code:
  - `cand/orb_retest.py`: the module, with `pad`, `flat_utc` and `nexit` added.
  - `cand/orb_retest_stage2.py`: the stage-2 driver.
  - `cand/orb_retest_final.py`: the finalist evaluation.
  - `cand/orb_retest_stages.py`: the killed agent's draft; superseded, with its input path fixed.
  - `cand/orb_retest_eval.py`: the killed agent's evaluation script; unused.
- Results (`cand/results/`):
  - Stage 1: `orb_retest_train.csv`, `orb_retest_valid.csv`.
  - Stage 2: `orb_retest_s2_train.csv`, `orb_retest_s2_valid.csv` (top 8, base and harsh),
    `orb_retest_s2_finalist.json`.
  - Finalist: `orb_retest_final_eval.json`, `orb_retest_final_beta.json`, `orb_retest_final_monthly.csv`.
- Reproduce:
  - `python3 cand/orb_retest_stage2.py`
  - `python3 cand/orb_retest_final.py '<finalist json>'`
  - Each run is one process; the data load alone takes 1.5-6 minutes on this machine.
