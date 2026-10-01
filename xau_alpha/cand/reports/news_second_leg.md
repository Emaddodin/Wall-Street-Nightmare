# H7: second leg after a HIGH-impact US release (`cand/news_second_leg.py`), with a summary of H5

Date: 2026-09-30. Families H7 and H5 from `recon/SYNTHESIS.md` section 6. The full H5 report is
`cand/reports/comex_momentum.md`.

**Verdict: FAIL for both families.**

**H7** is the better of the two, and it is "not promising".
- MEASURED: the finalist, chosen on TRAIN only, has TRAIN PF 1.83 (n = 27) and VALID PF 1.12 (n = 14) at lf_base.
- Its R-based t is +1.11 on TRAIN and −0.65 on VALID; pooled over both it is +0.57.
- It does not beat random direction on VALID (p = 0.42), and it fails lf_harsh (VALID PF 0.89).
- Its flip-eligible VALID subset loses (n = 9, PF 0.31).

**H5** has no edge even at zero cost (section 7).

About 41 trades in 16 months cannot establish an edge either way.

Labels: **MEASURED** means computed here on TRAIN (2025-01-21..12-31) and VALID (2026-01..05) only. **SOURCED**
means taken from a cited file. **OPINION** means my judgement. TEST (>= 2026-06-01) was never simulated: the module
cuts its arrays at the TEST start, and `sweep.run_orders` drops those orders. The video days (2026-09-21/22) were not
touched.

---

## 1. Hypothesis and rules

**Why (SOURCED SYNTHESIS H7, `recon/news_llm.md` section 3).**
- News brings volatility, not drift: the T+5..T+45 drift has t = 0.33.
- The range stays 1.4-1.7x normal until about T+44.
- The owner's setup A (break → retest → confirmation) is applied to the range the release leaves behind.

**Events.** Source: `data/econ_calendar.csv`, HIGH rows. Times are exact UTC minutes and were DST-checked in
SYNTHESIS S4.
- `core` = CPI, NFP, FOMC statement, PPI and Retail Sales. These are the types with strong price confirmation
  (news_llm 5.3). There are 44 TRAIN and 23 VALID unique minutes.
- `all` = every HIGH row except the FOMC press conference, which is folded into the statement. There are 101 TRAIN
  and 45 VALID minutes.

**Rules.** Written in long space; shorts run on the negated series, so both sides use byte-identical rules.

| item | rule |
|---|---|
| post-release range | PR = high/low of the M1 bars in [T, T+W) |
| filter `f` | PR width ≥ f × the median width of the same ET-clock [T, T+W) window over the prior 20 NY weekdays. f = 0 turns it off. |
| break | The first M1 close in [T+W, T+W+brk) beyond PR_hi + 0.1A (long) or PR_lo − 0.1A (short). One signal per event. |
| `imm` | Market order at the break close. Stop = min(l[i0-2..i0]) − σA. |
| `lim` | Buy limit at PR_hi + 0.1A, placed at the break close and alive for R bars. Stop = min(break swing, PR_hi) − σA. |
| `touch` | Retest: the first bar in (i0, i0+R] with l ≤ PR_hi + 0.2A. Market order at its close if c ≥ PR_hi − 0.1A. Stop = l[touch] − σA. This is the H2 "no trigger" arm. |
| `rt` | The same retest, then TRIG_BULL (engulf, or pin with l ≤ PR_hi + 0.1A < c) within 2 bars. A close below PR_hi − 0.1A aborts. Stop = min(l[touch..j]) − σA. |
| target | tp_r × the stop distance, as an absolute price from the decision close |
| time stop | tmax = 45 min |
| blackout | No entry within ±blk min of any HIGH row. blk = 5 by default; blk = 30 is the flip rule for equity below $21. |

**Deviations from SYNTHESIS (OPINION).**
- Two entry arms were added (`lim` and `touch`), so that chasing the break can be compared with waiting for a
  pullback.
- The range filter compares like with like: the same-clock median over W minutes, not over 15 minutes.
- For `imm`, the stop is the break-leg swing, because no retest swing exists.

All thresholds are in A (M1 Wilder ATR14) or relative to the same-clock median. There are no dollar thresholds.

---

## 2. Grid stages and configs tried (MEASURED)

| stage | configs | content |
|---|---|---|
| stage 1 | 96 | ev {core, all} × W {15, 30, 45} × mode {imm, lim, touch, rt} × tp_r {1.5, 2.0} × f {0, 2}. Fixed: σ 0.3, tmax 45, brk 90, R 30, blk 5. |
| stage 2 | 48 | Top 5 stage-1 parents by TRAIN t (n ≥ 20) × σ {0.2, 0.5} × tmax {30, 60} × brk {60, 120} × R {15, 45}. R is not varied for `imm`. |
| VALID check | 10 configs × 3 costs | Top 10 by TRAIN t, at lf_base, lf_harsh and mid |
| diagnostics | 5 | placebo non-event days (4 week-shifts pooled), blk = 30, ev = all, mode = rt, mode = imm |
| neighbour check | 20 configs, already counted | VALID lf_base of the finalist's stage-1/2 neighbours |

- **Total:** 144 search configs plus 5 diagnostics = **149** for H7. Together with H5's 309, **458** in all.
- **Ranking:** by TRAIN t-stat of R at lf_base, with n ≥ 20. The engine's `MIN_N_RANK = 60` would leave every
  `core` config unranked, because the whole universe is 44 TRAIN events.
- **Files:** `cand/results/news_second_leg_stage1_train.csv` and `_stage2_train.csv`, combined in `_train.csv`;
  `_valid.csv`, `_final.json`, `_monthly.csv`, `_diagnostics.csv`, `_by_type.csv` and `_neighbours_valid.csv`.
- **Driver:** `cand/h5h7_stages.py` (h7s1, h7s2, valid, final, h7extra).

### Stage 1 summary (MEASURED, TRAIN, medians over the configs in each cell)

| ev | mode | n | PF lf_base | $/oz lf_base | t (R) | PF mid | $/oz mid |
|---|---|---|---|---|---|---|---|
| all | imm | 57 | 0.84 | −0.56 | 0.18 | 0.92 | −0.26 |
| all | lim | 45 | 0.80 | −0.65 | −0.26 | 0.90 | −0.25 |
| all | touch | 34 | 0.94 | −0.09 | −0.12 | 1.27 | +0.32 |
| all | rt | 8.5 | 2.32 | +1.89 | 1.15 | 2.62 | +2.14 |
| core | imm | 29 | 0.60 | −1.69 | 0.12 | 0.67 | −1.32 |
| core | lim | 22.5 | 0.71 | −1.07 | 0.03 | 0.85 | −0.52 |
| core | touch | 16 | 1.32 | +0.53 | 0.72 | 1.56 | +0.85 |
| core | rt | 5 | 2.54 | +1.84 | 1.98 | 2.83 | +2.08 |

- On TRAIN, chasing the break (`imm`, `lim`) loses even at mid, and the retest arms (`touch`, `rt`) carry the positive
  gross. W = 15 is the worst window: the core-event median PF is 0.43.
- 36.5% of stage-1 configs have TRAIN PF > 1 at lf_base, and 50% at mid.
- **No config reaches TRAIN t ≥ 2 with n ≥ 20.** The best is 1.24.

### Stage 2 parent families (MEASURED, TRAIN, lf_base)

| parent | k | median n | PF min / median | share PF > 1 |
|---|---|---|---|---|
| all, W30, imm, 2R, f2 | 8 | 23.5 | 0.75 / 1.01 | 0.50 |
| all, W30, imm, 1.5R, f2 | 8 | 23.5 | 0.75 / 0.96 | 0.38 |
| core, W45, imm, 1.5R, f0 | 8 | 38 | 0.69 / 0.75 | 0.00 |
| core, W45, imm, 2R, f0 | 8 | 38 | 0.47 / 0.67 | 0.00 |
| **core, W45, touch, 2R, f0** | 16 | 26.5 | **1.27 / 1.45** | **1.00** |

### Finalist selection (TRAIN only)

- The engine's ranking rule, highest TRAIN t, picks rank 0: `all, W30, imm, 2R, f2, σ0.5, tmax60, brk120`, with
  n = 25, PF 1.17 and t 1.24. Its family is fragile on TRAIN (above).
- I chose the finalist with a TRAIN-only neighbour-robustness criterion: the only family whose stage-2 neighbours
  all have TRAIN PF > 1. Within it I kept the parent at its default parameters, which has the family's highest
  TRAIN t.
- **Disclosure:** I had already seen the top-10 VALID table (section 4) when making this choice. The robustness
  argument uses TRAIN numbers only. Rank 0 fails VALID badly (PF 0.42, n = 8), so the choice does not change the
  verdict.

---

## 3. Finalist results (MEASURED)

`{"ev":"core","W":45,"mode":"touch","tp_r":2.0,"f":0.0}` with defaults `brk 90, R 30, sigma 0.3, tmax 45, blk 5`.

| cost | TRAIN n / PF / avg $/oz / avg R / t(R) | VALID n / PF / avg $/oz / avg R / t(R) | pooled PF / avg $/oz / t(R) |
|---|---|---|---|
| lf_base | 27 / **1.83** / +1.23 / +0.32 / 1.11 | 14 / **1.12** / +0.29 / −0.23 / −0.65 | 1.49 / +0.91 / **0.57** |
| lf_harsh | 27 / 1.16 / +0.33 / −0.09 / −0.35 | 14 / **0.89** / −0.35 / −0.36 / −1.03 | 1.04 / +0.10 / −0.87 |
| mid (gross) | 27 / 2.28 / +1.64 / +0.63 / 1.99 | 14 / 1.26 / +0.60 / −0.17 / −0.45 | 1.79 / +1.28 / 1.45 |
| duka_raw | 27 / 1.51 / +0.84 / +0.12 / 0.44 | 14 / 1.08 / +0.21 / −0.22 / −0.64 | 1.32 / +0.62 / 0.01 |

- Average stop: 2.9 $/oz on TRAIN and 4.0 on VALID.
- Exit reasons: TRAIN has 14 SL, 12 TP and 1 time exit; VALID has 10 SL and 4 TP.
- PF in $/oz and the average R disagree on VALID: the winners had wide stops (FOMC) and the losers had tight ones.
- 26 of 27 TRAIN trades and every VALID trade resolved by SL or TP. tmax 30 and 60 give identical VALID results.

**Long / short (lf_base):**

| split | long n / PF / avg $/oz | short n / PF / avg $/oz |
|---|---|---|
| TRAIN | 14 / 1.37 / +0.47 | 13 / 2.21 / +2.04 |
| VALID | 9 / 0.52 / −1.56 | 5 / 3.95 / +3.63 |

**Flip-eligible (stop in [1.2, 4.0] $/oz, lf_base):**

| split | n | PF | avg $/oz | avg R |
|---|---|---|---|---|
| TRAIN | 15 | 2.36 | +1.57 | +0.43 |
| VALID | 9 | **0.31** | −1.77 | −0.74 |

- The flip-eligible share of VALID trades is 9 / 14 = 0.64.

### Null and robustness tests (MEASURED)

**Random direction** (200 seeds, lf_base; the same times, stops and targets):

| split | real PF | null PF, mean ± sd | p (PF) | p (avg R) |
|---|---|---|---|---|
| TRAIN | 1.83 | 1.40 ± 0.57 | 0.23 | 0.14 |
| VALID | 1.12 | 1.08 ± 0.55 | **0.42** | 0.70 |

**Mirror** (every direction reversed): TRAIN PF 0.84, VALID PF 0.92.

**Placebo non-event days** (same weekday and ET clock ±1 and ±2 weeks, no calendar row within ±3 h; pooled over the
4 shifts):

| | TRAIN n / PF / t | VALID n / PF / t |
|---|---|---|
| lf_base | 59 / 0.56 / −1.96 | 37 / 0.83 / 0.05 |
| mid | 59 / 0.75 / −0.19 | 37 / 0.95 / 0.78 |

Real events beat the placebo days on TRAIN (1.83 vs 0.56), and only marginally on VALID (1.12 vs 0.83).

**Other diagnostics:**
- **Flip blackout blk = 30** (the margin rule below $21): TRAIN 23 / PF 1.99; **VALID 12 / PF 0.43**.
- **ev = all** (every HIGH type): TRAIN 52 / PF 1.09 / t −0.32; VALID 23 / PF 2.65 / t 1.03.
- **mode = rt** (confirmation-candle arm): TRAIN 9 / PF 3.86 / t 2.37; VALID 8 / PF 0.72. The n is too small.
- **mode = imm** (chase arm): TRAIN 39 / PF 0.85; VALID 20 / PF 0.56. On TRAIN, the retest is worth more than
  chasing.

**By event type** (pooled TRAIN+VALID, lf_base, sum of R):

| type | n | sum R |
|---|---|---|
| FOMC | 7 | +7.2 |
| NFP | 4 | +7.6 |
| Retail Sales | 12 | +1.7 |
| CPI | 11 | −5.8 |
| PPI | 7 | −5.3 |

OPINION: do not cherry-pick FOMC/NFP from 11 trades.

**Neighbour robustness** (VALID lf_base of 20 stage-1/2 neighbours):
- Median PF 1.27; 19 of 20 are > 1.
- The neighbours share most of the same 13-15 VALID trades, so this is not independent evidence.
- The one-step neighbour `core, W30, touch` has VALID PF 0.13.

**Drop the best month** (2025-03): pooled PF 1.30, n = 37.

### Pass bar (SYNTHESIS section 5)

| criterion | value | result |
|---|---|---|
| n ≥ 150 TRAIN, ≥ 60 VALID | 27 / 14 | **FAIL** (structural: at most about 44/23 core events) |
| lf_base PF ≥ 1.15 on TRAIN and VALID | 1.83 / 1.12 | **FAIL** (VALID) |
| lf_base avg ≥ +0.15 $/oz on TRAIN and VALID | +1.23 / +0.29 | pass |
| lf_harsh VALID PF ≥ 1.0 | 0.89 | **FAIL** |
| both TRAIN halves PF > 1 | 2.02 / 1.70 | pass |
| random direction p ≤ 0.05 on VALID | 0.42 | **FAIL** |
| t ≥ 2 pooled (low-frequency criterion) | 0.57 at lf_base (1.45 at mid) | **FAIL** |
| neighbours median VALID PF ≥ 1.05 | 1.27 (not independent) | pass |
| drop best month PF ≥ 1.05 | 1.30 | pass |

"Near" would need the n requirement to hold, so the verdict is **fail**. In SYNTHESIS terms, H7 is **not promising**:
- no TRAIN config reaches t = 2;
- VALID R-t is negative;
- it cannot beat random direction;
- the flip-compliant version (blk = 30) and the flip-eligible subset both lose on VALID.

### Monthly R table, finalist, lf_base, TRAIN + VALID (MEASURED)

| month | n | sum R | avg R | sum $/oz | PF |
|---|---|---|---|---|---|
| 2025-02 | 3 | +2.50 | +0.83 | +7.59 | 3.84 |
| 2025-03 | 4 | +7.41 | +1.85 | +14.84 | inf |
| 2025-04 | 2 | −2.31 | −1.15 | −2.25 | 0.00 |
| 2025-05 | 3 | −3.14 | −1.05 | −11.20 | 0.00 |
| 2025-06 | 4 | +1.65 | +0.41 | +8.87 | 2.96 |
| 2025-07 | 3 | −0.34 | −0.11 | +6.73 | 3.19 |
| 2025-08 | 2 | +0.37 | +0.18 | +0.45 | 1.35 |
| 2025-09 | 2 | −2.09 | −1.05 | −9.67 | 0.00 |
| 2025-10 | 1 | −1.03 | −1.03 | −5.24 | 0.00 |
| 2025-11 | 1 | +1.93 | +1.93 | +11.42 | inf |
| 2025-12 | 2 | +3.64 | +1.82 | +11.61 | inf |
| 2026-01 | 4 | +1.61 | +0.40 | +11.33 | 2.03 |
| 2026-02 | 3 | −3.12 | −1.04 | −11.65 | 0.00 |
| 2026-03 | 2 | +3.58 | +1.79 | +17.16 | inf |
| 2026-04 | 2 | −2.15 | −1.08 | −4.32 | 0.00 |
| 2026-05 | 3 | −3.18 | −1.06 | −8.44 | 0.00 |

TRAIN sums to +8.6R over 27 trades; VALID sums to −3.3R over 14 trades.

---

## 4. VALID check of the top 10 TRAIN configs (MEASURED, `cand/results/news_second_leg_valid.csv`)

| # | config | TRAIN n / PF / t (lf_base) | VALID n / PF / avg $/oz (lf_base) | VALID PF lf_harsh / mid |
|---|---|---|---|---|
| 0 | all, W30, imm, 2R, f2, σ.5, tmax60, brk120 | 25 / 1.17 / 1.24 | 8 / 0.42 / −9.45 | 0.39 / 0.43 |
| 1 | all, W30, imm, 2R, f2, σ.2, tmax30, brk120 | 25 / 1.08 / 1.23 | 8 / 3.08 / +11.36 | 2.58 / 3.22 |
| 2 | all, W30, imm, 2R, f2, σ.2, tmax60, brk120 | 25 / 1.11 / 1.12 | 8 / 0.67 / −4.86 | 0.63 / 0.69 |
| 3 | **core, W45, touch, 2R, f0 (finalist)** | 27 / 1.83 / 1.11 | 14 / 1.12 / +0.29 | 0.89 / 1.26 |
| 4 | core, W45, touch, 2R, f0, σ.5, tmax60, brk120, R15 | 28 / 1.61 / 1.07 | 13 / 1.55 / +1.51 | 1.28 / 1.71 |
| 5 | all, W30, imm, 1.5R, f2, σ.2, tmax60, brk120 | 25 / 1.13 / 1.04 | 8 / 0.50 / −7.32 | 0.47 / 0.52 |
| 6 | all, W30, imm, 1.5R, f2 | 25 / 0.82 / 1.03 | 8 / 1.32 / +2.93 | 1.21 / 1.38 |
| 7 | core, W45, touch, 2R, f0, σ.5, tmax30, brk120, R15 | 28 / 1.60 / 1.02 | 13 / 1.55 / +1.51 | 1.28 / 1.71 |
| 8 | all, W30, imm, 1.5R, f2, σ.2, tmax30, brk120 | 25 / 0.98 / 0.97 | 8 / 2.63 / +8.90 | 2.19 / 2.76 |
| 9 | core, W45, imm, 2R, f0, σ.2, tmax30, brk120 | 40 / 0.95 / 0.97 | 20 / 1.09 / +0.47 | 0.94 / 1.17 |

The `all, W30, imm, f2` family swings from PF 0.42 to 3.08 on the same 8 VALID events as σ and tmax change. VALID
stops average $5-15 after 2026 releases. OPINION: that swing is noise, not signal.

---

## 5. Interpretation (OPINION)

- **Retest versus chase.** The only consistent qualitative finding on TRAIN is that entering on the retest of the
  broken post-release range beats chasing the break: `touch` and `rt` against `imm` and `lim`, even at mid. With 5-40
  trades per config, this is a direction for H2-style work, not evidence.
- **W = 15 is too early.** Its core-event median PF is 0.43. The release spike (T..T+4, news_llm) and the following
  swings are still running.
- **The flip account cannot trade it.** The ±30-minute margin blackout that applies below $21 turns VALID into PF 0.43.
  Post-release stops in 2026 are often above $4, and the flip-eligible VALID subset loses.
- **What would change my mind:** 2-3 more years of events (for example Dukascopy 2019-2024, re-costed). With about
  60 core events a year, one year of TEST cannot settle it.

---

## 6. Engine notes (no engine file was modified)

- **R-t ranking with heterogeneous stops.** The TRAIN t of R ranks configs whose PF is below 1 and whose $/oz is
  negative. Example: `all, W30, imm, 1.5R, f2` has PF 0.82, −0.67 $/oz and t = +1.03. Small-stop winners dominate R
  while wide-stop losers dominate $/oz. For event strategies, whose stops span $0.6-15, suggestions:
  - rank on a pnl-based t (logged here as `tr_tp`); or
  - cap R at ±3 before computing t.
- **`sweep.MIN_N_RANK = 60`.** This makes every low-frequency config unrankable through the CLI. The driver here uses
  its own threshold (20) and says so.
- **A possible trap, not a bug.** `nulltest.random_direction` rebuilds absolute sl/tp as distances from the mid at
  the decision time. That is correct here: sl and tp are measured from c[j], and the mid at t − 60 s is c[j].

---

## 7. H5 summary (full report: `cand/reports/comex_momentum.md`)

**Rules.** Return from the 18:00 ET open (or 08:20-08:50 ET for H5c) to the entry clock. Enter in that direction at
13:00 ET (a, c) or 16:00 ET (b). Time exit 25-60 min later. The stop is κ × the median same-clock 30-min move, and the
flip-band cap is skip, clamp or none.

**Configs:** 306 search configs plus 3 diagnostics.

**Results (MEASURED):**
- **Raw TRAIN diagnostic**, mid forward return signed by the signal: a +0.10 $/oz at 30 min (t 0.33); b −0.50 at
  25 min (t −2.24); c +0.10 (t 0.32).
- **All 216 stage-1 configs** have TRAIN t < 0 at lf_base.
- **Stage-2 best**, `{"variant":"a","theta":0.3,"exit":60,"kappa":0.75,"cap":"skip","med_n":10}`:
  - TRAIN: n 62, PF 1.06, +0.09 $/oz, t 0.30, halves 1.50 / 0.71.
  - VALID: n 18, PF 0.82, −0.43 $/oz.
  - VALID PF is 0.69 at lf_harsh and 0.90 at mid.
  - Random direction on VALID: p 0.58.
- **Pooled TRAIN+VALID t at zero cost, per family** (θ 0, exit 30): a −1.32, b −2.28, c −1.06. The requirement is
  t ≥ 2, and all three are negative.
- **Placebo entry at 15:00 ET** is as good as the 13:00 settlement entry. Nothing about the settlement stands out.
- **Post-hoc:** H5b has the opposite sign, a late-day reversal into the 17:00 ET break.
  - Gross: about +0.5 $/oz per trade.
  - Mirrored at lf_base: TRAIN PF 1.03 (n 148), VALID PF 1.19 (n 72); lf_harsh VALID 0.76.
  - Cost eats it, it was not pre-registered, and it fails the pass bar.

**Verdict:** FAIL, a clean "no edge".
