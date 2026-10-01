# X3 ml_meta: a machine-learned entry filter (HistGradientBoosting on causal M5 features)

Date: 2026-09-30. Data: TRAIN 2025-01-21..2025-12-31 and VALID 2026-01..05 only. TEST (>= 2026-06-01) was never
built, simulated or looked at: `_base()` truncates M1 at 2026-06-01 before any feature or label is computed. `evaluate(final=True)` was never called.
Every number is MEASURED unless labelled OPINION or SOURCED.

**Verdict: FAIL.** We judge on VALID only, as the brief requires for this ML family. The pre-declared finalist is a=1.5, T=60, q=0.02, chosen by Q4-2025 t-stat.

| VALID (2026-01..05) | n | PF | avg $/oz | WR |
|---|---|---|---|---|
| lf_base | 699 | **0.884** | **-0.257** | 0.466 |
| lf_harsh | 706 | 0.587 | -1.128 | 0.390 |
| mid (zero cost) | 702 | 1.121 | +0.236 | 0.513 |
| duka_raw | 702 | 0.741 | -0.608 | 0.416 |

- Random-direction null (200 seeds, lf_base, VALID orders): p = 0.17 on avg $/oz. At mid (100 seeds), p = 0.11.
- Directional AUC is 0.506 on Q4 and 0.518 on VALID. The model has essentially no directional skill.
- The stacked AUC (0.516 / 0.514) comes almost entirely from predicting whether a bracket resolves at all, which is volatility (resolution AUC 0.75 on Q4, 0.54 on VALID).

---

## 1. What was built (`cand/ml_meta.py`)

The code was written by two earlier agents, both killed by session limits. I reviewed it, verified it and re-ran it. The design is unchanged except for the VALID-only verdict added to `ml_meta_eval.py`.

**Decision points.**
- Every completed M5 bar (`data.resample_causal`). The order goes out at `t = ts[i] + 60 s` of the M1 bar at which the M5 bar becomes known.
- Gates:
  - no entries 20:30-23:30 UTC, on Friday from 19:00 UTC, on Saturday/Sunday UTC, or 16:15-18:00 ET;
  - no entries within ±30 min of a HIGH calendar event;
  - flat at 16:45 ET.

**Features: 39 causal columns plus wA.** Every column is scale-free (A5 units):
- M5 returns over 1/3/6/12/48 bars;
- M5 body and wicks;
- M1 ATR / A5, and A5 / A5_96, A5_288 and its prior-20-day median;
- position in the day-so-far range, the Asia range (00:00-06:59 UTC, fixed `window_range`, keyed by UTC date) and the prior-day range;
- distance to the prior-day high/low;
- distance to the nearest `zone_timeline` zone above and below, its touch count, and an in-zone flag;
- lon_mod / ny_mod sin/cos, day of week;
- minutes to the next and since the last HIGH event;
- sweep flags for the prior-day high/low and the Asia high/low;
- M5 FVG size, FVG count over 3 bars, M5 engulfing;
- M1 spread / its prior-20-day same-UTC-hour median, and spread / A5;
- wA = w / A5.

**Label (MID prices).**
- Long wins if +w is hit before -w within T minutes, and never past 16:45 ET. A bar that touches both counts as a loss. The short label is the mirror.
- w = clip(a·A5, 1.2, 4.0) $/oz ("cap" mode). Every trade is therefore flip-eligible, which follows the brief's preference.
- The cap binds hard in 2026: the median w is 4.0 on Q4 and VALID for a >= 1, and A5 is 5-8 $/oz. wA is a feature for this reason.

**Model.**
- One HistGradientBoostingClassifier (sklearn 1.3.2) with max_iter 300, lr 0.05, 15 leaves, min_leaf 400, l2 1.0 and random_state 0.
- It is fit on stacked long rows (x) and mirrored short rows (m(x)), so long and short rules are exact mirrors and the model cannot learn an unconditional drift.
- **Fit**: decision points whose label window ends by 2025-10-01 (42,900 points).
- **Early stop and Platt calibration**: on Q4 2025 (15,714 points, label window ends by 2026-01-01).
- **Threshold**: the top-q quantile of max(P_long, P_short) over the gated Q4 points.
- **Trade**: the side with the larger P when P >= threshold; market order, SL = TP = w, tmax T.

## 2. Causality checks (`tests/test_ml_meta_causal.py`), all PASS

1. `test_features_truncation_invariant`: features, gates and times at every decision point are identical with and without later data. Three cuts, Jan-May 2025.
2. `test_features_truncation_targeted_times` (new): 10 cuts placed where a look-ahead would show.
   - 03:00 UTC, inside the Asia window.
   - 07:02 UTC.
   - 21:02 and 22:31 UTC, the stretch where the old `window_range` leaked the upcoming Asia range.
   - 10:02 UTC, mid M5 bucket.
3. `test_asia_features_hidden_before_0700_utc` (new): Asia features are NaN at every decision before 07:00 UTC.
4. `test_model_ignores_data_from_2026` (slow, RUN_SLOW=1, run today): P_long and P_short for every pre-2026 decision point are bit-identical when M1 is truncated at 2026-01-01. The best iteration is also identical. So `orders()` trains only on data before 2026-01-01.
5. `tests/test_features_causal.py`, for the fixed `window_range`: PASS.

**window_range fix.** The previous agent's features already used the fixed `window_range` (keyed by UTC date).
- No pre-fix cache survived, so everything was recomputed from scratch.
- All 9 model fits and all 27 stage-1 configs reproduced the previous agent's post-fix numbers exactly.
- The v1 results computed before the fix are kept only as `results/ml_meta_OBSOLETE_v1_*` and are void.

## 3. Model quality (finalist a=1.5, T=60; similar for all 9 brackets)

| | Q4 2025 | VALID |
|---|---|---|
| AUC stacked | 0.516 | 0.514 |
| AUC long / short | 0.516 / 0.515 | 0.524 / 0.506 |
| AUC direction (bars where exactly one side won; P_long - P_short) | **0.506** | **0.518** |
| AUC resolution (any side wins; P_long + P_short) | 0.750 | 0.543 |
| log-loss vs base rate | 0.6919 vs 0.6927 | 0.6926 vs 0.6930 |
| Brier vs base | 0.2494 vs 0.2498 | 0.2497 vs 0.2499 |
| top-2% bars: win rate on model side / R model side / R opposite | 0.603 / +0.21 / -0.22 | **0.502 / +0.00 / -0.10** |

Across all 9 (a, T) brackets:
- VALID direction AUC is 0.501-0.518;
- Q4 direction AUC is 0.491-0.515;
- the best Q4 log-loss improves on the base rate by at most 0.006.

**Calibration (decile of stacked P: mean predicted → realised).**

| Split | Decile 1 | Decile 5 | Decile 10 | Total range |
|---|---|---|---|---|
| Q4 | 0.445 → 0.441 | 0.484 → 0.498 | 0.516 → 0.500 | 0.44-0.52 |
| VALID | 0.457 → 0.457 | 0.487 → 0.485 | 0.519 → 0.507 | 0.46-0.52 |

The calibration is roughly right, but the whole predicted range is only 0.44-0.52. The top decile does not realise above 0.51.

- Most-used split features: wA, ny_s, d_pdh, pos_asia, pos_day, atr_s288. These are volatility, time of day and level geometry.
- Purged 3-fold cross-fit over 2025 (diagnostic only) gives stacked AUC 0.558 / 0.553 / 0.531.

## 4. Grid and configs tried

| Stage | Configs | Status |
|---|---|---|
| v1 (first agent, pre-fix window_range) | 72 | VOID; not used for any decision |
| Stage 1: a {0.75, 1, 1.5} × T {30, 60, 120} × q {0.02, 0.05, 0.10}, cap / mirror / score p | 27 | re-run today, identical |
| Stage 2 arms around the winner | 7 | previous agent, same code (after the fix) |

- The 7 stage-2 arms: q 0.01 and 0.03; uncapped atr; atr + flip filter; separate side models; the 'edge' score at q 0.02 and 0.05.
- **Post-fix distinct configs: 34 (≤ 40).** The eval's neighbours are all among these 34.

**Stage 1.**
- Only 3 of 27 configs have Q4 PF > 1.
- The best Q4 t-stat is 0.98, for the finalist: Q4 n 242, PF 1.134.
- **Every one of the top 5 has VALID lf_base PF below 1**: 0.884, 0.987, 0.918, 0.910, 0.761. Their lf_harsh PFs are 0.53-0.67.

**Stage 2** (VALID lf_base).

| Arm | VALID lf_base |
|---|---|
| edge score | PF 0.91 |
| q 0.01 | PF 0.88 |
| q 0.03 | PF 0.84 |
| separate side models | PF 0.72 |
| uncapped atr stop | PF 1.105, n 177 |

The uncapped atr arm fails for three reasons:
- its average stop is 8.1 $/oz, so it is not flip-eligible;
- its Q4 PF is 0.98, so it would not be selected;
- its lf_harsh PF is 0.89.

## 5. Finalist detail (a=1.5, T=60, q=0.02, cap, mirror; threshold P >= 0.526)

**Long vs short.**

| VALID | Long | Short |
|---|---|---|
| lf_base | n 496, PF 0.839, avg -0.36 $/oz | n 203, PF 0.993, avg -0.02 $/oz |
| lf_harsh | PF 0.59 | PF 0.59 |
| mid | PF 1.05 | PF 1.29 |

Longs make up 72% of fired bars, even though the model is exactly mirror-symmetric.

**Other checks.**
- **Flip-eligible share**: 100% (stops 1.2-4.0 $/oz by construction; the median is 4.0).
- **Nulls on VALID, lf_base**:
  - random direction, 200 seeds: null mean -0.396, 95th percentile -0.17, real -0.257 → **p = 0.174**;
  - mirror: PF 0.739.
- **Q4 null** (100 seeds): p = 0.010. But Q4 was used for early stopping, calibration, threshold and selection, so it is not independent.
- **VALID label check (mid bracket, every fired bar)**: model-side R -0.014 vs opposite -0.062, t = -0.38, win rate 0.492.
- **VALID monthly PF (lf_base)**: Jan 0.98, Feb 0.91, Mar 0.75, Apr 0.93, May 0.83. No month is positive. Halves PF 0.93 / 0.85; drop-best-month PF 0.85.
- **Neighbours**: median VALID PF 0.878.
- **Cross-fit 2025** (the honest analogue of the TRAIN trades), lf_base: n 644, PF 0.90, avg -0.18, halves 1.00 / 0.82. lf_harsh PF 0.55; mid PF 1.09.
- **In-sample Jan-Sep 2025 trades**: PF 1.65, t 5.6. This is overfit and should not be judged.

**Pass-bar checklist (VALID only).**

| Criterion | Result |
|---|---|
| n >= 60 | ✔ 699 |
| lf_base PF >= 1.15 | ✘ 0.884 |
| avg >= +0.15 $/oz | ✘ -0.257 |
| lf_harsh PF >= 1.0 | ✘ 0.587 |
| random-direction p <= 0.05 | ✘ 0.17 |
| near (PF >= 1.05) | ✘ |

## 6. Conclusion (OPINION)

The generic causal feature set does not tell which way XAUUSD will move over the next 30-120 minutes.
- **What the model does learn**: when a bracket will resolve (volatility) and the level geometry.
- **Why the fired bars still look good at zero cost**: they sit in high-volatility states, and those carry a small gross positive drift (VALID mid PF 1.12). Random directions on the same bars get about the same result (mid p = 0.11).
- **Costs**: that drift is smaller than the LiteFinance cost of about 0.42 $/oz round trip on a 4 $/oz bracket.
- **Q4**: the positive Q4 result (PF 1.13) did not survive into VALID, and was selected from 27 configs on the same slice used for calibration.

"No edge" is the result. The rest of the grid should not be tuned further on VALID.

## 7. Reproduction

```
C=<cache dir>; export ML_META_CACHE=$C OMP_NUM_THREADS=1
/usr/local/bin/python3 cand/ml_meta_stages.py prefit      # features + 9 models (~10 min)
/usr/local/bin/python3 cand/ml_meta_stages.py stage1      # results/ml_meta_s1_{train,valid}.csv
/usr/local/bin/python3 cand/ml_meta_eval.py '{"a":1.5,"T":60,"q":0.02}' --seeds 200 --cv   # results/ml_meta_final.json
RUN_SLOW=1 /usr/local/bin/python3 -m pytest -q tests/test_ml_meta_causal.py
```

**Outputs**:
- `results/ml_meta_models.json` (diagnostics for all 9 brackets);
- `ml_meta_s1_*.csv`, `ml_meta_s2_*.csv`;
- `ml_meta_final.json` and `ml_meta_final_monthly.csv`.

No lib file was changed.
