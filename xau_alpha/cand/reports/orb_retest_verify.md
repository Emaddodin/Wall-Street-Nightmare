# H6 ORB finalist: adversarial verification (verifier #2)

Date: 2026-09-30. Target: `cand/orb_retest.py`, the frozen finalist from `cand/reports/orb_retest.md`:

`{"window":"lon","filt":0,"kap":0.0,"entry":"brk","stop":"far","pad":0.25,"tp":1.0,"flat_utc":780,"be":0.0,"tmax":0,"brk_h":120,"nb":30,"nexit":-1}`

Script: `cand/orb_retest_verify2.py` (one process, about 13 minutes on a machine with load average about 200).
Results: `cand/results/orb_retest_verify2.json`.

Labels:
- **MEASURED** means computed here on TRAIN (2025-01-21..2025-12-31) and VALID (2026-01..05).
- **SOURCED** means taken from a cited file.
- **OPINION** means my judgement.

TEST discipline: TEST (>= 2026-06-01) was never simulated. Every order list was cut and asserted `t < 2026-06-01`,
and `final=True` was never used.

## Verdict

**REFUTED. Severity: FATAL** (OPINION, based on the MEASURED items below).

- The code is causal. There is no look-ahead.
- The result is not an edge.
  - **Nulls.** On VALID the rule cannot be told apart from random direction: p 0.20 on $/oz and p 0.74 on R.
  - **R.** It loses in R: −7.6 R over 101 trades.
  - **Mirror.** The mirror rule (every direction reversed) makes more R than the rule itself: +4.1 R against −7.6 R.
  - **Naive short.** A naive always-short at the same timestamps beats it on both measures: PF 1.45 and +14.5 R.
  - **Neighbours.** All 12 nearest neighbours lose in R on VALID.
  - **TRAIN.** Its t of 1.83 was the best of about 171 distinct rankable trade sets out of 450 tries. Under the null,
    the best of that many would reach a t of about 2.7.

## A. Causality (MEASURED): clean

1. **Code review** of `orb_retest.py` and the paths it uses in `data`, `features` and `sim`.
   - **Opening range.** The OR high/low is a groupby over the 08:00-08:29 London bars. It is used only on
     break-search bars, which satisfy `lon_mod >= 510` and `f > e`.
   - **ATRd.** It is a day-level aggregate, but it goes through `.shift(1).rolling(20)`, so it uses prior days only.
   - **Filter percentiles.** They use `shift(1).rolling(60)` (prior days only), and they are off in the finalist
     (`filt=0`).
   - **Leaks.** There is no `shift(-k)` and no `bfill`.
   - **Stop and target.** Both come from the decision-bar close `c[j]` and `A[i0]` with `i0 = j`, which is causal
     Wilder ATR.
   - **Order timing.** Each order is placed at `t = ts[j] + 60 s`.
   - **Minor exit-clock detail.** The flat time is "the first bar with UTC minute >= 780". It uses the bar's
     existence, not its price, so it has no information value. It shows up only as the expected flat mismatch in the
     truncation test.
2. **Independent re-implementation from raw M1.** I rebuilt the rule with my own pandas Wilder ATR, OR, break, stop,
   target, flat and news gate, without using the module's code.
   - Result: **333/333 orders identical** (t, d, sl, tp, flat). There are 0 missing and 0 extra orders.
   - Decision bars lie at London 08:30-10:28 (`lon_mod` 510-628). The earliest decision is 60 s after the OR's last
     bar.
3. **Truncation invariance.** I compared `orders(m1[ts < T])` with `orders(m1)` at 6 cuts (3 in TRAIN, 3 in VALID).
   - The cuts:
     - 2025-04-01 08:15 London, inside the OR window;
     - 2025-06-03 08:31 London, one bar into the break search;
     - 2025-09-01 at signal + 6 min;
     - 2026-02-02 11:30 London, during a hold;
     - 2026-04-01 12:45 London, just before the flat;
     - 2026-05-01 at signal + exactly 5 min.
   - On direction, stop and target: **0 mismatches**, 0 missing and 0 extra orders. This holds both for orders with
     t ≤ T − 5 min (46 to 314 orders per cut) and for every truncated order with t ≤ T.
   - The only differences are `flat` on the cut day itself (4 cuts, 1 order each). There the true flat, 13:00 UTC,
     lies after T and the truncated day ends at T. This is expected and not a leak: flat mismatches with the full
     flat before the cut = 0.

## B. Reproduction (MEASURED, `sweep.evaluate`)

| | n | PF $/oz | avg $/oz | sum R | avg R | t(R) | PF in R |
|---|---|---|---|---|---|---|---|
| TRAIN lf_base | 232 | 1.441 | +1.53 | +25.7 | +0.111 | 1.83 | 1.28 |
| TRAIN lf_harsh | 232 | 1.242 | +0.92 | +8.2 | +0.036 | 0.60 | 1.09 |
| TRAIN mid | 232 | 1.588 | +1.92 | +36.2 | +0.156 | 2.54 | 1.41 |
| VALID lf_base | 101 | **1.200** | +1.66 | **−7.6** | **−0.076** | −0.79 | **0.85** |
| VALID lf_harsh | 101 | 1.109 | +0.95 | −12.0 | −0.118 | −1.26 | 0.77 |
| VALID mid | 101 | 1.240 | +1.95 | −5.9 | −0.058 | −0.61 | 0.88 |

- **All claimed numbers reproduce exactly.**
- VALID t on $/oz is only 0.74.
- The median stop is 8.35 $/oz on TRAIN and 15.35 on VALID.
- VALID win rate 0.465 at a 1R target (net of costs).

## C. Statistics on VALID (lf_base, MEASURED)

### Nulls (one-sided p = share of null draws at least as good as the actual result)

| null | draws | p on avg $/oz | p on PF | p on sum R | null PF median / p95 |
|---|---|---|---|---|---|
| `nulltest.random_direction` | 1000 | **0.198** | 0.198 | **0.736** | 0.98 / 1.48 |
| permutation of directions (keeps the 52L/49S mix) | 500 | 0.190 | 0.190 | 0.727 | 0.96 / 1.48 |
| permutation within each calendar month (keeps the monthly mix) | 500 | 0.160 | 0.160 | 0.635 | 0.96 / 1.40 |

- For contrast, TRAIN (in-sample, selected): random-direction p 0.007, global permutation 0.003 ($/oz) / 0.013 (R),
  within-month permutation 0.007 / 0.013.

### Mirror, long vs short, naive sides at the same timestamps and geometry

| VALID | n | PF $/oz | sum R |
|---|---|---|---|
| rule | 101 | 1.20 | −7.6 |
| rule, longs only | 52 | 0.82 | −11.9 |
| rule, shorts only | 49 | 1.92 | +4.3 |
| **mirror (every direction reversed)** | 101 | 0.78 | **+4.1** |
| always-long | 101 | 0.65 | −18.0 |
| **always-short** | 101 | **1.45** | **+14.5** |
| short on the rule's long-break days | 52 | 1.15 | +10.2 |

- **VALID in R.** The reversed rule beats the rule. On upside-break days, fading the break (+10.2 R) beat following it
  (−11.9 R).
- **VALID in $/oz.** The positive PF is short exposure during a down-drifting 08:30-13:00 window, and naive
  always-short does better.
- **TRAIN.** The rule made +25.7 R. Always-long made +14.3 R and always-short −33.5 R. Longs made +24.1 R (t 2.33);
  shorts made +1.6 R (t 0.17). About 94% of TRAIN R came from longs in a year when gold rose 59% (SOURCED
  orb_retest.md §6).

### Neighbourhood: 12 nearest single-parameter moves (lf_base)

| move | TRAIN PF / sum R | VALID PF $/oz | VALID sum R | VALID R long / short |
|---|---|---|---|---|
| kap 0.05 | 1.43 / +20.4 | 1.11 | −11.8 | −13.7 / +1.9 |
| kap 0.1 | 1.43 / +17.7 | 1.13 | −11.2 | −13.1 / +1.9 |
| pad 0.1 | 1.44 / +24.8 | 1.20 | −7.7 | −12.0 / +4.3 |
| pad 0.4 | 1.48 / +27.3 | 1.22 | −7.3 | −12.8 / +5.5 |
| tp 0.75 | 1.49 / +22.9 | 1.05 | −10.6 | −12.4 / +1.8 |
| tp 1.25 | 1.37 / +20.4 | 1.16 | −10.4 | −14.0 / +3.5 |
| flat 12:00 UTC | 1.29 / +17.6 | 1.19 | −7.1 | −10.3 / +3.2 |
| flat 14:00 UTC | 1.40 / +26.7 | 1.19 | −7.4 | −13.0 / +5.6 |
| brk_h 90 | 1.45 / +25.7 | 1.23 | −6.6 | −10.9 / +4.3 |
| brk_h 150 | 1.42 / +24.7 | 1.20 | −7.6 | −11.9 / +4.3 |
| OR 08:00-08:14 | 1.29 / +8.7 | **0.81** | **−26.3** | −16.9 / −9.4 |
| OR 08:00-08:44 | 1.13 / +3.9 | 1.09 | −7.5 | −8.7 / +1.2 |

- **$/oz bar.** The median VALID PF in $/oz is **1.17**, which passes the ≥ 1.05 bar.
- **R.** The median VALID sum R is **−7.65**, and **0 of 12 neighbours are positive in R**.
- **Long side.** Every neighbour loses on VALID longs.
- **Window sensitivity.** Changing the OR length to 15 or 45 minutes cuts TRAIN to t 0.58 / 0.29. The 30-minute
  London OR is a narrow peak.

### Monthly R and drop-best-month

- **TRAIN.** Monthly R, Jan..Dec 2025: +2.5, −0.8, +7.1, −1.6, +8.2, +0.4, +4.3, −5.0, +4.7, **+9.9**, −0.7, −3.4.
  - 7 of 12 months are positive in R and 8 of 12 in $/oz.
  - Without the best month (Oct): PF 1.27, +15.8 R.
- **VALID.** Monthly R, Jan..May 2026: −8.2, −0.8, **+5.9**, +0.6, −5.2.
  - 2 of 5 months are positive in R and 3 of 5 in $/oz.
  - Without the best month (Mar): **PF 0.957, −13.5 R**. This fails the ≥ 1.05 bar.
  - March 2026 has a median stop of 23 $/oz.
- **VALID R by stop quartile:**

  | stop range ($/oz) | R | $/oz |
  |---|---|---|
  | 6.5-12.9 | −5.1 | −50 |
  | 13.1-15.4 | −8.3 | −113 |
  | 15.7-21.5 | −2.3 | −32 |
  | 21.7-93.5 | +8.0 | **+362** |

  The whole VALID $/oz profit comes from the 25 widest-stop trades. The other 76 trades lost 195 $/oz.
- **Bootstrap** (5000 draws, VALID):
  - PF 90% CI [0.80, 1.79].
  - Avg R 90% CI [−0.23, +0.08].
  - P(avg R ≤ 0) = 0.78.

### Cost and latency stress (MEASURED)

| scenario | TRAIN PF / sum R | VALID PF $/oz / sum R |
|---|---|---|
| lf_base | 1.44 / +25.7 | 1.20 / −7.6 |
| entry +5 s (fills one 10-s bar later; the same as +10 s) | 1.40 / +23.9 | 1.21 / −7.1 |
| entry +30 s | 1.40 / +22.7 | 1.15 / −8.7 |
| entry +60 s | 1.41 / +23.7 | 1.14 / −7.2 |
| slip +0.3 $/oz per side (entry and stop/market exits) | 1.29 / +12.8 (t 0.92) | 1.14 / −10.4 |
| slip +0.3 per side and +5 s | 1.26 / +11.1 | 1.15 / −9.9 |
| +0.3 $/oz per side at every fill, including target limits | 1.28 / +11.4 | 1.15 / −10.2 |

- **$/oz.** The VALID PF stays around 1.14-1.21 under stress, because the stops are wide.
- **R.** VALID R is negative in every scenario.
- **TRAIN.** TRAIN t falls to about 0.8-0.9 under +0.3 $/oz per side.
- **Latency.** Timing is not the problem; latency barely matters.

### Multiple testing (MEASURED from `results/orb_retest_train.csv` and `orb_retest_s2_train.csv`)

- **Search size.** 450 configs were tried. 216 had TRAIN n ≥ 60, and 171 of those were distinct trade sets.
- **Cross-config TRAIN t.** Median 0.46, sd 0.93. The maximum, 1.83, is this finalist.
- **Raw p.** One-sided p for t 1.83 is 0.034.

| effective number of tests | Šidák p | E[max t] under an iid null |
|---|---|---|
| 5 | 0.16 | 1.19 |
| 10 | 0.29 | 1.57 |
| 30 | 0.64 | 2.07 |
| 171 | 0.997 | 2.71 |
| 450 | 1.00 | 3.02 |

- The configs are correlated, so the effective N lies between 5 and 171. Even N_eff = 5 leaves p 0.16.
- The TRAIN evidence does not survive the search that produced it.
- Also, stage-1 VALID results for the top 15 configs existed before stage 2 was designed
  (`results/orb_retest_valid.csv`, SOURCED). So VALID is not perfectly clean for the stage-2 choices of pad 0.25 and
  flat 13:00. This is minor.

## Reasons for "refuted" (OPINION, based on the MEASURED numbers above)

1. **Not distinguishable from the nulls on VALID.**
   - Random direction: p 0.20 ($/oz) and 0.74 (R).
   - Mix-preserving permutation: p 0.19 / 0.73.
   - Within-month permutation: p 0.16 / 0.64.
   - None comes near 0.05.
2. **Negative expectancy in R.** VALID is −0.076 R per trade, and all 12 neighbours are negative. A trader sizing by
   risk loses. The positive $/oz rests on 25 very-wide-stop trades and one month (March 2026). Drop that month and
   PF is 0.957.
3. **Direction adds nothing on VALID.** Mirror beats the rule in R, and naive always-short beats it in $/oz and R.
   The TRAIN edge is mostly long exposure in a +59% year, plus a short-side contribution with t 0.17.
4. **The TRAIN significance is a selection artefact.** A TRAIN t of 1.83 is the maximum of about 171 distinct trade
   sets. Šidák p ≥ 0.16 even at N_eff = 5.
5. **It survives the cost and latency stress in $/oz only.** That is not evidence of an edge, because the edge was
   not established before any stress.

Beyond verification (SOURCED `orb_retest.md` §9): 0 of 101 VALID stops fall inside the $13 flip band [1.2, 4.0] $/oz.
The strategy is unusable for the flip phase regardless of the above.
