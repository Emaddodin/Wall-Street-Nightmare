# X1 exhaustion_fade: fading 5m/15m displacement bars (mean reversion after exhaustion)

Date: 2026-09-30. Data: TRAIN 2025-01-21..2025-12-31 and VALID 2026-01..05 only. TEST (>= 2026-06-01) was never simulated. The video trades were not read.

**Verdict: FAIL.** The best config that has enough trades (finalist B) is weakly positive on TRAIN and loses on VALID at every cost model:
- lf_base VALID PF 0.63 and avg -3.6 $/oz;
- lf_harsh VALID PF 0.57;
- mid (zero cost) VALID PF 0.66;
- random-direction null p = 0.96.

No config in 381 searched reached train t >= 2. The continuation twin does no better on TRAIN and better on VALID. Every number below is MEASURED unless it is labelled OPINION or SOURCED.

---

## 1. Hypothesis and how it was tested

**Premise (SOURCED, task text and the HANDOFF quick study).** After a 5m bar whose body is larger than 2.5x the mean 5m range, the mid price moves against the bar over the next 30-60 min.

**Re-measured here.** I used the same definition (body >= 2.5x the mean range of the prior 24 5m bars) and mid price, with the move measured in the bar's direction (negative means reversal):

| Horizon | Split | All events | Excluding ±30 min of HIGH news |
|---|---|---|---|
| 30 min | TRAIN | n 425, **-0.82 $/oz, t -1.79** | n 389, -0.55, t -1.23 |
| 30 min | VALID | n 212, **-1.82, t -1.39** | n 201, -1.52, t -1.11 |
| 60 min | TRAIN | -0.55, t -0.82 | -0.39, t -0.59 |
| 60 min | VALID | -1.60, t -0.86 | -1.69, t -0.87 |

- This reproduces the premise.
- Removing news events weakens it.
- None of these t-values reaches 2. The effect was fragile before any costs were applied.

### Rules (`cand/exhaustion_fade.py`)

Longs and shorts use identical, mirrored rules: a bearish bar runs the same code on the negated series.

| Step | Rule |
|---|---|
| Displacement | A completed tf-minute bar (tf 5 or 15, from `data.resample_causal`) with body >= k·A_tf. A_tf is the Wilder ATR14 of the tf bars as of the **previous** tf bar, so the bar cannot raise its own threshold. |
| News | `news='ex'` (default): skip the event if any minute of the displacement bar, or the entry bar, is within ±30 min of a HIGH calendar row. This matches the rule for equity below $21. `'only'` and `'all'` were tested separately. |
| Confirmation | Checked over at most m M1 bars after the displacement bar; stop checking at a data gap longer than 5 min.<br>• `none`: enter at the displacement close.<br>• `opp`: the first opposite-colour M1 close.<br>• `back25` / `back50`: an M1 close back inside the bar's last 25% / 50% of the body.<br>• `nofail`: no new extreme within m bars; enter at the close of bar m. |
| Entry | Market order at the confirmation close; order `t = ts[j] + 60 s`. |
| Stop | The running extreme (from the displacement bar's first minute to j) + s·A, with A the M1 ATR14. |
| Target | `rXX`: ext − f·(ext − bar's far end), f = 0.25 / 0.382 / 0.5. Skipped if it lies less than 0.3·stop from entry.<br>`RX`: X × the stop distance. |
| Time | Exit after tmax minutes; flat at 16:45 ET. |
| Session | No entries 20:30-23:30 UTC, on Friday from 19:00 UTC, on Sunday, or 15:45-18:00 ET. Optional sessions: `eu_us` (07-20 UTC) and `lonny` (06-12 London or 08:30-11:30 NY). |
| Continuation twin | `side='cont'`: same decision time, stop distance and target distance, mirrored around the decision mid, so it trades **with** the displacement. |

### Deviations from the brief, with reasons

1. **Unit for 15m bars.** On 15m, k is measured in A15 (about 1.7·A5), not A5. This keeps k meaning "a multiple of the bar's own typical range", as in the event study. On 5m it is A5, as specified. (OPINION)
2. **k = 4 was not simulated.** Before any confirmation or session gate there were only 50 (5m) and 21 (15m) ex-news TRAIN displacement bars, so n >= 150 was impossible.
3. **back50 with retracement targets.** Entry already sits at 50% of the body, so a 38-50% retracement target lies at or behind the entry. These combinations produce 0 trades. They were kept in the grid and are counted.

---

## 2. Grid stages and configs tried (all ranked by TRAIN t-stat of R at lf_base, n >= 60)

| Stage | What | Configs |
|---|---|---|
| 1 | tf {5,15} × k {2, 2.5, 3} × conf {none, opp, back25, back50, nofail} × tp {r38, r50, R1, R1.5} × tmax {30, 60}; s = 0.3, m = 5, news ex, session all | 240 |
| 2 | Top 5 distinct stage-1 families × s {0.15, 0.3, 0.6} × m {3, 5, 10} × tmax {30, 60, 120} | 135 |
| 3 | Top 3 stage-2 configs × session {eu_us, lonny} | 6 |
| Finalist variants | Twin, news_only and news_all for A and B (6); new neighbours (2); fade/twin diagnostic with no confirmation (4 new twins) | 12 |
| **Total simulated** | | **393** |

Also run, but not counted as configs: 2 event-study passes (mid-price forward moves).

**Stage 1 (TRAIN, lf_base).**
- Of 240 configs, 110 have n >= 60, and **only 1 has avg R > 0** (t 0.97).
- 0 configs have t >= 2.
- 0 configs meet the TRAIN part of the pass bar (n >= 150, PF >= 1.15, avg >= 0.15).

Median TRAIN t by confirmation type:

| conf | none | opp | back25 | back50 | nofail |
|---|---|---|---|---|---|
| median TRAIN t | -3.9 | -2.6 | -0.9 | -0.9 | -0.7 |

- The fade gets worse the earlier it enters.
- By k and tf, every median t is negative (-0.5 to -2.4).

**Stage 2 top 5 families (by stage-1 TRAIN rank):**
1. 5m/k2/back50/R1.5
2. 15m/k2/back25/R1.5
3. 5m/k2/back50/R1
4. 5m/k2/nofail/R1.5
5. 15m/k2.5/opp/R1.5

- The best stage-2 result is t 1.37, but with n = 92.
- With n >= 150, the best is t 0.80: that is finalist B.
- Every n >= 150 config comes from m = 10.

**Stage 3.** Session filters cut n to about 42, which is below the ranking floor.

---

## 3. Finalists

- **B** (the reported best: the highest TRAIN t among configs with n >= 150): `{"tf":5,"k":2.0,"conf":"back50","tp":"R1.5","tmax":120,"s":0.15,"m":10,"sess":"all","news":"ex"}`
- **A** (the highest TRAIN t overall, but n = 92 fails the bar): the same, with s = 0.3 and m = 5.

### B: split × cost (per 1 oz)

| Cost | Split | n | WR | PF | avg $/oz | avg R | sum R | t(R) | avg risk $ |
|---|---|---|---|---|---|---|---|---|---|
| lf_base | TRAIN | 161 | 0.47 | **1.30** | **+0.96** | +0.071 | +11.5 | 0.80 | 7.4 |
| lf_base | VALID | 85 | 0.38 | **0.63** | **-3.60** | -0.109 | -9.3 | -0.95 | 18.8 |
| lf_harsh | TRAIN | 161 | 0.45 | 1.03 | +0.13 | -0.077 | -12.5 | -0.91 | 7.7 |
| lf_harsh | VALID | 85 | 0.37 | **0.57** | -4.50 | -0.182 | -15.5 | -1.63 | 19.3 |
| mid (gross) | TRAIN | 161 | 0.47 | 1.42 | +1.30 | +0.138 | +22.2 | 1.50 | 7.2 |
| mid (gross) | VALID | 85 | 0.39 | 0.66 | -3.23 | -0.083 | -7.0 | -0.71 | 18.6 |
| duka_raw | TRAIN | 161 | 0.46 | 1.23 | +0.75 | +0.032 | +5.1 | 0.37 | 7.5 |
| duka_raw | VALID | 85 | 0.37 | 0.61 | -3.91 | -0.142 | -12.1 | -1.26 | 19.0 |

- TRAIN halves (lf_base): PF 1.26 and 1.32. Under harsh: 0.92 and 1.12.
- Dropping the best month (2025-12): TRAIN PF 1.17. Dropping VALID's best month: 0.54.
- Exits (TRAIN / VALID): stop 74/43, target 60/22, time 27/20.

### B: long and short, lf_base (a long fades a bearish bar; a short fades a bullish bar)

| Side | TRAIN n | TRAIN PF | TRAIN avg R | VALID n | VALID PF | VALID avg R |
|---|---|---|---|---|---|---|
| Long | 105 | 1.38 | +0.078 | 57 | **0.51** | -0.250 |
| Short | 56 | 1.12 | +0.060 | 28 | 1.18 | +0.178 |

VALID losses come from buying into the Feb-Mar 2026 sell-off (see the monthly table). OPINION: this is regime exposure, not a reversion edge.

### B: flip-eligible subset (stop in $1.2-4.0)

- TRAIN: n 42, PF 1.00, avg R +0.02.
- VALID: **n 0**, because the VALID stop quantiles (p10 / p50 / p90) are $7.7 / $13.9 / $37.8.

The flip-eligible share of VALID trades is **0.0** (TRAIN 0.26). This geometry cannot be traded on the $13 account in 2026 volatility.

### B: nulls

| Null | Result |
|---|---|
| Random direction, 50 seeds, VALID, lf_base | Null mean avg +0.77 $/oz (PF 1.15), p95 +5.3 $/oz. Real -3.60 → **p = 0.96** (same for PF). |
| Mirror, VALID | n 84, PF 1.49, avg +3.64 $/oz |
| Continuation twin, lf_base | TRAIN PF 0.74, avg R -0.13 (fade better); VALID PF **1.49**, avg R +0.07 (twin better) |
| Continuation twin, mid | TRAIN avg R -0.04; VALID +0.10 |
| News-only (HIGH ±30 min), lf_base | TRAIN n 19, PF 2.33; VALID n 4, PF 0.00. Too few trades; not takeable below $21. |
| News included (`news='all'`), lf_base | TRAIN PF 1.37 (n 179); VALID 0.58 (n 89) |

Neighbours (one step on k, s, m and tmax), VALID lf_base PF:

| Change | VALID PF | VALID n |
|---|---|---|
| k 2.5 | 0.41 | 35 |
| s 0.3 | 0.61 | 85 |
| m 5 | 0.78 | 43 |
| tmax 60 | 0.74 | 85 |

Median neighbour VALID PF: **0.67**.

### A (n below the bar; for completeness)

| Cost | TRAIN (n 92) | VALID (n 43) |
|---|---|---|
| lf_base | PF 1.48, avg +1.49, avg R +0.16, t 1.37 | PF **0.73**, avg -2.74, avg R +0.01 |
| lf_harsh | — | PF 0.67 |
| mid | PF 1.63 | PF 0.76 |

- Long / short on VALID: PF 0.43 / 1.91.
- Flip-eligible VALID n: 0.
- Random-direction null: p = 0.71.
- Continuation twin, VALID lf_base: PF 1.27.
- Median neighbour VALID PF: 0.77.

### B: monthly R table (lf_base; `cand/results/exhaustion_fade_finalB_monthly.csv`)

| Month | Split | n | sum R | avg R | avg $/oz | PF |
|---|---|---|---|---|---|---|
| 2025-01 | T | 5 | +4.8 | +0.97 | +3.83 | 9.48 |
| 2025-02 | T | 17 | +2.7 | +0.16 | +0.65 | 1.35 |
| 2025-03 | T | 14 | -2.5 | -0.18 | -0.37 | 0.85 |
| 2025-04 | T | 13 | -0.0 | -0.00 | +0.43 | 1.09 |
| 2025-05 | T | 12 | +3.0 | +0.25 | +1.63 | 1.72 |
| 2025-06 | T | 16 | -2.0 | -0.12 | -0.96 | 0.69 |
| 2025-07 | T | 10 | -3.5 | -0.35 | -2.15 | 0.47 |
| 2025-08 | T | 17 | +1.7 | +0.10 | +0.10 | 1.04 |
| 2025-09 | T | 20 | -8.7 | -0.44 | -3.33 | 0.31 |
| 2025-10 | T | 14 | +2.2 | +0.16 | +6.71 | 2.29 |
| 2025-11 | T | 10 | +4.5 | +0.45 | +3.86 | 2.48 |
| 2025-12 | T | 13 | +9.1 | +0.70 | +5.71 | 3.00 |
| 2026-01 | V | 22 | +0.2 | +0.01 | -0.65 | 0.93 |
| 2026-02 | V | 11 | -2.9 | -0.26 | -10.06 | 0.32 |
| 2026-03 | V | 19 | -4.9 | -0.26 | -9.80 | 0.38 |
| 2026-04 | V | 16 | -0.3 | -0.02 | +1.66 | 1.31 |
| 2026-05 | V | 17 | -1.3 | -0.08 | -1.27 | 0.77 |

TRAIN profit is concentrated in 2025-10..12 (+15.8R of +11.5R total).

---

## 4. Diagnostic: the flip-compatible fade has no gross edge

Only `conf=none` has stops that fit the $4 cap (median stop $1.6-2.5). The table compares it with its twin at k 2.5 and tmax 30, as TRAIN / VALID avg R:

| tf, tp | Fade, mid | Twin, mid | Fade, lf_base | Twin, lf_base |
|---|---|---|---|---|
| 5m, r38 (n 255 / 114) | +0.09 / -0.08 | +0.11 / -0.08 | -0.45 / -0.21 | -0.32 / -0.18 |
| 5m, R1 (n 270 / 129) | +0.02 / -0.07 | -0.03 / -0.06 | -0.41 / -0.21 | -0.39 / -0.20 |
| 15m, r38 (n 116 / 62) | -0.08 / +0.18 | +0.17 / +0.33 | -0.35 / +0.08 | -0.09 / +0.14 |
| 15m, R1 (n 123 / 68) | +0.03 / +0.07 | -0.03 / -0.10 | -0.25 / -0.04 | -0.24 / -0.22 |

- The gross R is about 0 in both directions. The drift seen in the event study sits below the noise a $1-3 stop sees.
- A $0.42 round trip on a $1.6-2.5 stop costs about 0.2-0.3R per trade.

---

## 5. Pass bar (SYNTHESIS §5), finalist B

| Criterion | Value | Pass? |
|---|---|---|
| n TRAIN >= 150 / VALID >= 60 | 161 / 85 | yes |
| lf_base PF >= 1.15 and avg >= +0.15, TRAIN | 1.30 / +0.96 | yes |
| Same, VALID | **0.63 / -3.60** | **no** |
| lf_harsh VALID PF >= 1.0 | 0.57 | **no** |
| Both TRAIN halves PF > 1 | 1.26 / 1.32 | yes |
| Random-direction null p <= 0.05 (VALID) | 0.96 | **no** |
| Median neighbour VALID PF >= 1.05 | 0.67 | **no** |
| Drop best month PF >= 1.05 | TRAIN 1.17, VALID 0.54 | **no** |
| "Near" (VALID PF >= 1.05 and TRAIN >= 1.10) | VALID 0.63 | **no** |

**Verdict: FAIL.** OPINION: kill X1 as a stand-alone entry. The reversal in the event study is small (|t| < 1.8), and partly news-driven. It lives at 30-60 min on moves whose stop, sized to the displacement, is $7-38 in 2026. Wide stops dilute it and tight stops drown it in noise and cost. The TRAIN-positive delayed-confirmation fade reverses on VALID, where buying bearish displacement bars in the Feb-Mar 2026 sell-off lost.

---

## 6. Engine notes (no files in lib/ were changed)

- **PF in points versus R diverge.** `sweep` ranks by R t-stat, but `stats()` PF is in $/oz. With stops of $3-38, PF in points is dominated by high-volatility trades: stage-1 configs show PF(pts) 1.3-1.4 with negative avg R. The pass bar is written in points; judging this family in R gives a more honest verdict. Not a bug, but worth knowing.
- **Process count.** `sweep.py --jobs 2` runs 3 python processes (the parent plus 2 workers); the parent is idle while the workers run.
- **Empty validation file.** No stage-3 config reached n >= 60, so `exhaustion_fade_s3_valid.csv` is empty (1 byte). It was written by my stage runner, not the engine.
- I found no correctness bugs in `sim` / `data` / `features` for this use.

---

## 7. Files

- `cand/exhaustion_fade.py`: the module (GRID = stage 1, DEFAULTS, `setups()`, `orders()`).
- `cand/exhaustion_fade_stages.py`: the stage 2 and 3 runner.
- `cand/exhaustion_fade_eval.py`: finalist evaluation (costs, sides, flip subset, twin, news, null, neighbours, monthly).
- `cand/results/exhaustion_fade_{s1,s2,s3}_{train,valid}.csv` (`exhaustion_fade_{train,valid}.csv` is the same as s1).
- `cand/results/exhaustion_fade_final{A,B}.json` and `_monthly.csv`.

Suggested HANDOFF.md line, not written because it is outside `xau_alpha/`: "X1 exhaustion fade (5/15m displacement ≥ k·ATR, 393 configs): FAIL. Best n ≥ 150 config has TRAIN PF 1.30 but VALID PF 0.63 at lf_base, null p 0.96; the no-confirmation fade has gross R ≈ 0 and loses 0.2-0.45R at cost."
