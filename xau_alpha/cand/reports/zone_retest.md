# zone_retest: H2, the owner's setup A (M1 zone, break, retest, confirmation candle, nearest-level target)

Date: 2026-09-30. Module `cand/zone_retest.py`. Stage driver `cand/zone_retest_stages.py`. Evaluation `cand/zone_retest_eval.py`.
Results are in `cand/results/zone_retest_*`.

**Verdict: FAIL.** H2 has no net edge on real XAUUSD, and there is no reliable gross edge either.
- MEASURED: 324 configs had at least 60 TRAIN trades (the engine's ranking rule). **None** of them reached PF > 1 at `lf_base` on TRAIN.
- The config picked on TRAIN scored:
  - TRAIN: PF 0.98, −0.02 $/oz per trade, t = −1.14.
  - VALID: PF 0.67, −0.84 $/oz per trade.
  - Random-direction null on VALID: p = 0.84.
- The confirmation candle (trigger) does not beat the no-trigger control arm per $/oz. Touch 2 does not beat touch 1.

Labels: **MEASURED** means I computed it here, **SOURCED** means it comes from a cited file, and **OPINION** is my judgement.

---

## 1. Rules as implemented

The rules are written for longs. Shorts use the exact mirror: the same code runs on the negated series o' = −o, h' = −l, l' = −h, c' = −c, so the parameters are identical and there is no directional veto. All thresholds are in units of A, the M1 Wilder ATR14 (`data.atr`).

| Step | Rule |
|---|---|
| Zones | `features.zone_timeline(h, l, A, L, k, w, K)`, the SYNTHESIS "ZONES" definition. A break at bar i uses the zones known at the close of bar i−1. |
| Break (bar i0) | c[i0] > z_hi + β·A. The break leg c[i0] − min(l[i0−10..i0]) must be ≥ λ·A. The zone must be "fresh": max(c[i0−fresh..i0−1]) ≤ z_hi + β·A, with fresh = 60 in the spec. If several zones break on the same bar, the highest one is used. The zone bounds are frozen at the break. |
| Retest | A touch is a bar in (i0, i0+R] with l ≤ z_hi + τ·A, τ = 0.1. Any M1 close below z_lo, including on the touch and trigger bars, cancels the setup. **touch = 2** needs a high ≥ z_hi + 0.5·A on some bar strictly after touch 1, followed by another touch. This is the state machine in `video_mindset.md` §(b). |
| Trigger | **trig = 1**: the first bar j in [touch, touch+2] with BULL_ENGULF(j) or BULL_PIN(j, z_hi). A pin needs a lower wick ≥ 0.6 × range, l ≤ z_hi and c > z_hi. If no trigger appears, there is no trade. **trig = 0** is the control arm: j = the touch bar. |
| Entry | Market order at ts[j] + 60 s (the sim fills at the next 10-s ask). j must fall in the London-local session. It must also be outside [T−30, T+30] min of any HIGH calendar event, the SYNTHESIS N2 rule for equity below $21. |
| Stop | min(l[touch..j]) − σ·A, as an absolute price. |
| Target | `near`: the nearest confirmed swing high from the last 240 bars that is ≥ ρ × stop distance above c[j]. If that level is more than 4 × the stop distance away, or there is none, use 2R. `r`: a fixed multiple of the stop distance. |
| Management | Optional break-even: at +1R, move the stop to entry + $0.45. Time stop tmax. One position at a time. |
| Placebo | Every zone shifted by ±1.5·w·A before the break test. |

TEST bars (≥ 2026-06-01) are removed inside the module before any computation, so no holdout signal can be generated.

## 2. Grid stages and configs tried (MEASURED)

| Stage | Grid | Configs |
|---|---|---|
| 1 (`python3 lib/sweep.py zone_retest --jobs 2`) | w {0.5, 1} × K {3, 6} × β {0.1, 0.3} × λ {1.5, 3} × R {15, 45, 90} × touch {1, 2} × trig {1, 0}. Fixed: k = 3, L = 240, σ = 0.3, TP near ρ = 1, tmax = 30, session 06:00-12:00 London | 192 |
| 2a (**mistake**, kept and counted) | My first driver ranked parents by raw t, without the engine's n ≥ 60 rule. It picked 5 parents with n = 19-39 and ran × 36 variants. None of these configs can meet the pass bar, and they are excluded from ranking. | 180 |
| 2 | Top 3 stage-1 configs (TRAIN t, n ≥ 60) × σ {0.2, 0.5} × TP {near ρ = 1, near ρ = 1.5, 1.5R} × tmax {15, 30, 60} × BE {off, on} | 108 |
| 3 | Top 2 stage-2 configs × session {06-12, 06-17 London} × (k, L) {(2, 120), (3, 240), (2, 240)} × fresh {60, 15} | 24 |
| Arms at the selected config | trigger toggled, touch toggled, placebo +1.5, placebo −1.5 (each at lf_base and at mid) | 4 |
| **Total** | | **508** |

Budget notes:
- Because of the 2a mistake I cut the stage-2 and stage-3 parents from 5/3 to 3/2, to stay near the ~500-config budget.
- `fresh` = 15 is a variant I added after the detector sanity check (§8). The spec's 60-bar freshness rule is what blocked the V4 break.
- Before the sweep I ran a 4-config smoke test (the defaults, trig = 0, touch = 2, and w = 1 with K = 6). All 4 are in the stage-1 grid. I saw their VALID numbers (PF 0.57-0.65), which is a small protocol leak. It did not influence selection, because every selection used TRAIN only.

Stage-1 overview, TRAIN, lf_base (MEASURED):
- Median PF 0.56 and median net −0.38 $/oz, which is roughly minus the round-trip cost. The gross edge is therefore about zero.
- 2 of 192 configs had PF > 1. Both had n of 19 or 31.
- Best t with n ≥ 60: −1.54.
- Stages 2 and 3 improved the best TRAIN PF only to 0.90 and 0.98.

## 3. Selected config (highest TRAIN t with n ≥ 60 across all stages; selected on TRAIN only)

```json
{"w":1.0,"K":6,"beta":0.3,"lam":3.0,"R":15,"touch":2,"trig":1,"L":120,"k":2,"tau":0.1,"w_pin":0.6,
 "sigma":0.5,"tp_mode":"r","tp_r":1.5,"tmax":30,"be":0,"sess":"0612","news":30,"placebo":0.0,"fresh":15}
```

MEASURED, per 1 oz:

| Cost | Split | n | WR | PF | avg $/oz | avg R | sum R | avg risk $ |
|---|---|---|---|---|---|---|---|---|
| lf_base | TRAIN | 155 | 0.439 | **0.982** | −0.020 | −0.102 | −15.9 | 2.01 |
| lf_base | VALID | 74 | 0.446 | **0.672** | −0.840 | −0.026 | −1.9 | 3.95 |
| lf_harsh | TRAIN | 155 | 0.361 | 0.515 | −0.791 | −0.476 | −73.8 | 2.33 |
| lf_harsh | VALID | 74 | 0.365 | **0.448** | −1.737 | −0.290 | −21.4 | 4.32 |
| mid (gross) | TRAIN | 155 | 0.465 | 1.395 | +0.356 | +0.154 | +23.9 | 1.84 |
| mid (gross) | VALID | 74 | 0.446 | 0.773 | −0.540 | +0.077 | +5.7 | 3.76 |
| duka_raw | TRAIN | 155 | 0.368 | 0.789 | −0.255 | −0.233 | −36.1 | 2.13 |
| duka_raw | VALID | 74 | 0.392 | 0.556 | −1.213 | −0.155 | −11.5 | 4.12 |

Further figures for this config:
- t-stat of R at lf_base: TRAIN −1.14, VALID −0.19.
- TRAIN halves PF at lf_base: 0.855 / 1.092, so one half is below 1.
- Dropping the best month leaves PF 0.875 on TRAIN and 0.576 on VALID.
- Exits at lf_base: TRAIN 85 stop / 62 target / 8 time; VALID 40 / 30 / 4.
- The gross edge on TRAIN (+0.36 $/oz) disappears on VALID (−0.54 $/oz).

Why VALID's avg R is near 0 while its PF is 0.67:
- PF and average are measured in $/oz, and the large-risk trades of 2026 (median risk $3.8) lost the most dollars.
- R normalises the loss per trade by that trade's risk.

### Long / short (lf_base; mid in brackets), MEASURED

| Split | Side | n | PF | avg $/oz |
|---|---|---|---|---|
| TRAIN | long | 72 | 0.919 (1.331) | −0.087 (+0.288) |
| TRAIN | short | 83 | 1.034 (1.448) | +0.038 (+0.416) |
| VALID | long | 30 | 0.798 (0.925) | −0.452 (−0.153) |
| VALID | short | 44 | 0.604 (0.690) | −1.104 (−0.804) |

### Flip-eligible subset (risk in [1.2, 4.0] $/oz), MEASURED

- Share of trades that are flip-eligible: TRAIN 0.774, VALID 0.595. Risk p10 / p50 / p90:
  - TRAIN 1.08 / 1.76 / 3.30;
  - VALID 1.86 / 3.77 / 5.73.
- About 40% of VALID signals exceed the $4 cap, as expected from the 2026 volatility (G13).

| Cost | Split | n | PF | avg $/oz | avg R |
|---|---|---|---|---|---|
| lf_base | TRAIN | 120 | 0.815 | −0.232 | −0.102 |
| lf_base | VALID | 44 | 1.346 | +0.480 | +0.206 |
| lf_harsh | VALID | 35 | 0.972 | −0.044 | −0.054 |
| mid | TRAIN | 100 | 1.089 | +0.104 | +0.055 |
| mid | VALID | 47 | 1.844 | +0.975 | +0.385 |

OPINION: the VALID flip subset is positive on n = 44, but the TRAIN flip subset (n = 120) is negative at lf_base. With that sign flip, 44 trades and 508 configs searched, this is noise, not an edge.

## 4. Null tests (MEASURED, lf_base)

**Random direction** (`nulltest.random_direction`, 50 seeds; the same timing, stop and target geometry):
- VALID: null PF median 0.851, null PF p95 1.281. The actual PF of 0.672 gives **p = 0.84** (on avg $/oz; 0.82 on PF).
- TRAIN: null PF median 0.732, p95 0.964. The actual PF of 0.982 gives p = 0.078.
- This is not significant even on TRAIN, the split it was selected on.

**Mirror** (every direction reversed): PF 0.537 on TRAIN and 1.023 on VALID. The reversed-direction rule does better than the rule on VALID.

**Placebo zones** (every zone shifted by ±1.5·w·A):

| Variant | TRAIN PF | VALID PF |
|---|---|---|
| Real zones | 0.982 | 0.672 |
| Placebo +1.5 | 0.933 | 0.611 |
| Placebo −1.5 | 0.651 | 1.023 |

The real zones do not separate from the placebo zones. The TRAIN advantage over placebo −1.5 reverses on VALID.

## 5. Trigger vs no trigger, and touch 1 vs touch 2 (the kill criteria in SYNTHESIS H2)

**Stage 1, TRAIN, lf_base, paired on the other 6 parameters (96 pairs each), MEASURED:**

| Comparison | Pairs better on avg R | Pairs better on avg $/oz | Median Δ avg $/oz | Pairs better on PF |
|---|---|---|---|---|
| trig = 1 vs trig = 0 | 97.9% | **29.2%** | −0.099 | 64.6% |
| touch = 2 vs touch = 1 | 46.9% | 53.1% | +0.012 | 51.0% |

- The trigger looks better in R only because it widens the stop:
  - The trigger arm enters 0-2 bars after the touch, with its stop below the lowest low from the touch to the entry bar.
  - Its median risk is $1.81, against $1.09 without the trigger, so the fixed $0.42 cost is a smaller fraction of R.
- In $/oz the trigger arm is worse in 71% of pairs.

**At the selected config**, lf_base TRAIN / VALID (mid in brackets):

| Arm | TRAIN PF | VALID PF |
|---|---|---|
| trigger on (selected) | 0.98 (1.40) | 0.67 (0.77) |
| trigger off | 0.55 (1.01) | 0.73 (0.89) |
| touch 2 (selected) | 0.98 | 0.67 |
| touch 1 | 0.79 (1.12) | 0.90 (1.14) |

- On VALID the trigger does not beat the control arm, even gross.
- Touch 2 is not better than touch 1 on VALID.
- Touch 1 has a small gross edge on both splits, +0.12 / +0.26 $/oz. That is below the 0.42 round-trip cost, and it was found after the fact.

**Kill criterion met:** "the trigger does not beat the no-trigger arm", in $/oz and on VALID.

## 6. Monthly R table for the selected config (lf_base, TRAIN + VALID), MEASURED

| Month | Split | n | L/S | sum R | avg $/oz | PF | avg risk |
|---|---|---|---|---|---|---|---|
| 2025-01 | T | 11 | 4/7 | +0.8 | +0.08 | 1.11 | 1.44 |
| 2025-02 | T | 18 | 10/8 | −6.5 | −0.77 | 0.37 | 1.54 |
| 2025-03 | T | 7 | 4/3 | −1.3 | −0.40 | 0.58 | 1.39 |
| 2025-04 | T | 8 | 5/3 | +2.5 | +0.73 | 1.76 | 2.32 |
| 2025-05 | T | 9 | 7/2 | −0.5 | +0.28 | 1.27 | 2.02 |
| 2025-06 | T | 17 | 8/9 | −0.5 | −0.17 | 0.84 | 1.74 |
| 2025-07 | T | 19 | 9/10 | −8.7 | −0.62 | 0.44 | 1.47 |
| 2025-08 | T | 10 | 1/9 | +0.2 | +0.46 | 1.65 | 1.57 |
| 2025-09 | T | 16 | 6/10 | −4.7 | −0.63 | 0.52 | 1.94 |
| 2025-10 | T | 11 | 5/6 | +2.0 | +0.93 | 1.78 | 2.84 |
| 2025-11 | T | 14 | 7/7 | −2.5 | −0.15 | 0.91 | 3.14 |
| 2025-12 | T | 15 | 6/9 | +3.3 | +1.11 | 2.20 | 2.84 |
| 2026-01 | V | 14 | 7/7 | −3.2 | −1.20 | 0.40 | 2.96 |
| 2026-02 | V | 16 | 6/10 | +7.6 | +0.16 | 1.07 | 4.32 |
| 2026-03 | V | 13 | 4/9 | −2.8 | −1.56 | 0.51 | 4.60 |
| 2026-04 | V | 20 | 10/10 | −1.6 | −0.82 | 0.70 | 4.08 |
| 2026-05 | V | 11 | 3/8 | −1.9 | −1.01 | 0.61 | 3.65 |

5 of 12 TRAIN months and 1 of 5 VALID months are positive in R.

## 7. Pass bar (SYNTHESIS §5) for the selected config

| Criterion | Value | Result |
|---|---|---|
| n TRAIN ≥ 150, VALID ≥ 60 | 155 / 74 | ok |
| lf_base PF ≥ 1.15, TRAIN and VALID | 0.982 / 0.672 | **fail** |
| lf_base avg ≥ +0.15 $/oz, TRAIN and VALID | −0.020 / −0.840 | **fail** |
| lf_harsh VALID PF ≥ 1.0 | 0.448 | **fail** |
| Both TRAIN halves PF > 1 | 0.855 / 1.092 | **fail** |
| Random-direction null p ≤ 0.05 on VALID | 0.84 | **fail** |
| "near" (VALID PF ≥ 1.05 and TRAIN PF ≥ 1.10) | 0.672 / 0.982 | no |

## 8. Detector sanity check (TRAIN video trades only, 2025-03-27; not evidence)

I ran 192 stage-1 detector configs with a 00:00-12:00 session and no news filter, and read the signals within ±2 bars of each video entry. No P&L was computed. The 2026-09 trades were not looked at. MEASURED:

| Trade | Time UTC, side | Detector fires within ±2 bars? |
|---|---|---|
| V1 | ~06:00 BUY at ~3028.5 | **No long.** The detector fired **short** in 48 of 192 configs: a break down of the 3030.5-3031.0 zone at 05:57, retest at 05:59. SOURCED `video_trades.csv`: the creator called V1 an "M5 trendline break + retest of a functional level". Price came down into 3028, so there was no upward M1 zone break to retest. |
| V2 | ~06:02 BUY at ~3030.45 | No long. The creator's reason was a "massive rejection" bar that added to V1. The 3030.5-3031.0 zone had closes above it within 60 bars (and within 15), so it was not fresh. |
| V3 | ~06:05 BUY at ~3031.5-3032.1 | No long. These were pyramided adds at worse prices, not a new setup. |
| V4 | 06:50-06:58 BUY at ~3029.6 | **No** at the spec's fresh = 60. The "build-up" zone 3028.29-3028.72 broke at 06:50 (a leg of 4.5·A), and the first touch came at 06:56. The break was rejected because price had closed above that level 23-60 bars earlier. **With fresh = 15, the control arm fires at 06:56** (24 of 192 configs, within 2 bars of 06:58). The trigger arm does not: 06:56-06:58 contain no engulf or ≥ 60% pin. |

Conclusions:
- At the spec settings the detector reproduces 0 of 4 train-dated video entries, and 1 of 4 with fresh = 15 and no trigger.
- OPINION: the creators' M1 entries are not well described by "multi-touch fractal zone → fresh break → retest → candle". V1-V3 are a trendline/rejection idea plus pyramiding. Only V4 is a break-and-retest.
- MEASURED: `fresh` = 15 was then swept in stage 3. It gave the best TRAIN PF of any config (0.98), but VALID was 0.67.

## 9. Engine observations (no lib files changed)

1. `sweep.py` changed while my stage 1 was running: it now ranks by `rank_t` (n ≥ `MIN_N_RANK` = 60). My first stage-2 driver did not mirror this, which caused the 2a mistake above. The driver now uses `sweep.MIN_N_RANK`.
2. `sweep._job` ranks on the t-stat of R, while PF and avg are in $/oz. For setups whose stop width varies by arm, such as trigger vs no trigger, R-based ranking favours wider stops because the fixed cost is a smaller share of R. OPINION: it is worth also reporting a $/oz t-stat. This is not a bug.
3. `features.zone_timeline` and `pivots` mark every bar tied at the window maximum as a pivot, so flat tops add touches to K. This is minor, and arguably desired for equal highs.
4. I found no simulator or holdout bug. Every order was generated with t < 2026-06-01, and the module removes TEST bars before computing anything.

## 10. Bottom line (OPINION)

- H2 in the form SYNTHESIS specifies is dead on this data: all 324 configs with n ≥ 60 lose at the LiteFinance base cost.
- The gross edge is about zero; the selected config's TRAIN gross edge fails on VALID.
- The confirmation candle, the owner's core filter, does not add value per $/oz over entering at the touch. The second touch adds nothing over the first.
- Do not pursue H2 further. Its best by-product (touch 1, k = 2, L = 120, fresh = 15, gross +0.1-0.3 $/oz) is below cost, and it was found after 500 configs.
- **This finding should be appended to `HANDOFF.md` by the orchestrator.** This task forbids writing outside `xau_alpha/`.

## Files
- `cand/zone_retest.py`: the module (GRID, `orders()`, `setups()` diagnostics).
- `cand/zone_retest_stages.py`: the stage 2/3 driver.
- `cand/zone_retest_eval.py`: the final evaluation.
- `cand/results/`:
  - `zone_retest_stage1_{train,valid}.csv`, which are copies of the CLI's `zone_retest_{train,valid}.csv`;
  - `zone_retest_stage2a_lowN_train.csv`, `zone_retest_stage2_train.csv`, `zone_retest_stage3_train.csv`;
  - `zone_retest_final.json`, `zone_retest_monthly.csv`.
