# H9: rejection at the first touch of a round number ($10 / $25 / $50), with placebo levels (`cand/round_reject.py`)

Date 2026-09-30. TRAIN 2025-01-21..2025-12-31, VALID 2026-01..05. TEST (>= 2026-06-01) was never simulated: `sweep.py`, `cand/rnsb_eval.py` and `cand/rn_levels.py` drop every order with t >= 2026-06-01 before simulation.
Labels: **MEASURED** = computed in this run; **SOURCED** = from `recon/SYNTHESIS.md` or a sibling file; **OPINION** = mine.
Companion hypothesis H11 (Silver Bullet) is in `cand/reports/silver_bullet.md`. H11 is the better of the two, and it also fails.

## Verdict: FAIL (clean "no edge", and no round-number barrier effect even at zero cost)

- **MEASURED. Configs tried:** 422.
  - Stage 1: 192.
  - Stage 2: 96.
  - Level-placebo block: 128 new placebo configs, plus 64 real cells re-run at mid cost.
  - Finalist variants: 6.
- **MEASURED. Stage 1 at `lf_base`:** no config with n ≥ 60 has TRAIN PF > 1. The best TRAIN t is −0.26.
- **MEASURED. Stage 2:** the best TRAIN t is −0.15.
- **MEASURED. Finalist** (best TRAIN t of stage 2): $25 levels, a 60-bar look-back clear by 2·A, a 10-bar approach, wick ≥ 0.5, 07:00 London to 16:25 ET.
  - TRAIN at `lf_base`: n 78, PF 1.07, +0.14 $/oz, avg R −0.02, t −0.15.
  - VALID at `lf_base`: n 62, PF 0.77, −1.34 $/oz.
  - VALID at `lf_harsh`: PF 0.63.
  - Random-direction null on VALID (50 seeds): p = 0.76.
- **MEASURED. The kill rule fires: the placebo levels are as good as or better than the real ones, even at mid (zero cost).**
  - At mid (zero cost), across all 64 cells of the $10 and $25 blocks, the levels offset by +0.63·step beat the real round numbers in every step × split comparison. Pooled mid avg R:
    - $10: real −0.03 / −0.07 against placebo +0.07 / +0.01 (TRAIN / VALID);
    - $25: real −0.01 / −0.08 against +0.01 / +0.02.
  - For the finalist, the placebo at L + 9.25 has TRAIN PF 1.63 against the real level's 1.07.
- **MEASURED. The rejection wick adds nothing.** The no-wick control arm (trig = 0) does better on VALID: PF 1.08 against 0.77, with twice the trades.
- **MEASURED. Flip phase.**
  - Flip-eligible share (stop 1.2-4.0): 55% on TRAIN, 24% on VALID.
  - The flip-eligible subset loses at every cost:
    - `lf_base`: TRAIN PF 0.72, VALID 0.59;
    - `lf_harsh`: 0.38 / 0.43.

## 1. Rules as implemented

Each rule is stated for the short side, touching from below. The long side is the exact mirror, with identical parameters. A = `data.atr(m1)`.

| Step | Rule |
|---|---|
| Levels | L = k·step + off, with step in {10, 25, 50}. The real levels have off = 0. The placebos (`place` 1 / 2) have off = 0.37·step / 0.63·step; for $10 these are SYNTHESIS's L + 3.7 and L + 6.3. |
| First touch | L = the lowest level ≥ max(h over bars [i−M, i−K_app−1]) + μ·A[i], so price stayed at least μ·A below L over the far look-back. Touch: h[i] ≥ L − 0.05·A[i]. **K_app = 0** is the literal SYNTHESIS rule: the touch bar itself must cover the last μ·A. **K_app > 0**: the last K_app bars may approach L but must stay below L − 0.05·A, which makes this the first touch in M bars after a normal approach. |
| Trigger | The first bar j in [i, i+2] with an upper wick ≥ wk·range[j] (wk 0.6 in stage 1) and c[j] ≤ L − dep·A[j] (dep 0.2). The setup is cancelled if a bar in [i, j) closes above L. **trig = 0 is the control arm:** the same close condition without the wick. |
| Entry, stop, target | Market at the close of j (t = ts[j] + 60 s; shorts fill at the next 10-s bid). Stop = max(h[i..j]) + σ·A[j]. Target = c[j] − tp·(stop − c[j]). |
| Exit / gates | tmax minutes. Flat by 16:45 ET. No entries at 20:30-23:30 UTC, after 19:00 UTC on Friday, on Sunday UTC, or at 16:25-18:00 ET. HIGH-event blackout [T−30, T+30] at the decision bar. `sess = active` also requires 07:00 London local ≤ t < 16:25 ET. |

**Spot check (MEASURED, TRAIN, finalist).** 2025-04-04, level 3100 ($25 grid):
- Bars [i−60, i−11] peaked at 3096.08.
- Approach: 3094.1 → 3098.5 over 10:05-10:06 UTC.
- Touch and trigger on the 10:07 UTC bar: h 3100.22, c 3098.35. The upper wick is 76% of the range.
- Short at 10:08 UTC. Stop 3100.76 (h + 0.3·A, so A ≈ 1.8). Target 3094.74 (1.5R).

## 2. Grid stages and configs tried (MEASURED)

| Stage | Grid | Configs | Files |
|---|---|---|---|
| 1 | SYNTHESIS grid: step {10, 25, 50} × M {60, 240} × μ {1, 2} × σ {0.3, 0.6} × TP {1, 1.5}R × tmax {10, 20} = 96, crossed with K_app {0 (literal), 10}. Fixed: wk 0.6, dep 0.2, W 2, sess all. | 192 | `results/round_reject_s1_{train,valid}.csv` |
| 2 | 6 bases (the top 5 by TRAIN t, all $25 with n 69-82, plus the best with n ≥ 150: $10 / M 60 / μ 2 / σ 0.3 / 1.5R / 20) × wk {0.5, 0.6} × dep {0.1, 0.2} × sess {all, active} × K_app {10, 30} | 96 | `results/round_reject_s2_{train,valid}.csv` (the CLI copies are `round_reject_{train,valid}.csv`) |
| Level block | For $10 and $25 at K_app 10: all 32 (M, μ, σ, TP, tmax) cells × place {0, 1, 2} × cost {mid, lf_base}. No selection is done here. | 128 new (+64 real re-runs) | `results/round_reject_levels.csv` |
| Finalist | place 1, place 2, trig 0 (control), step 10, step 50, sess all | 6 | `results/round_reject_final.json`, `round_reject_monthly.csv` |

Stage 1 was first run in an earlier attempt of this task. I re-ran it now with the same module code, and every row reproduced exactly (max |ΔPF| = 0, max |Δn| = 0).

**Stage 1, TRAIN `lf_base` (MEASURED).**
- 6 of 192 configs have PF > 1, and all six have n ≤ 33 (K_app 0).
- None of the 56 configs with n ≥ 60 has PF > 1.
- Best t: −0.26.
- 24 configs reach n ≥ 150; all are $10 with K_app 10, and their best PF is 0.77.
- The literal rule (K_app 0) is rare: a median of 9 TRAIN trades per config (maximum 51) in 11 months.
- **Mean PF by step and K_app:**
  - $10: 0.55 (K 0), 0.64 (K 10);
  - $25: 0.67, 0.71;
  - $50: 0.28, 0.50.
- **VALID, top 15 at `lf_base`:** median PF 0.72 (range 0.52-0.87). Harsh median 0.62.

**Stage 2, TRAIN (MEASURED).**
- 14 of 96 configs have PF > 1, 9 of them among the 84 configs with n ≥ 60.
- Best t: −0.15.
- 22 configs have n ≥ 150; none has PF ≥ 1.15.
- Session `active` beats `all` (median PF 0.86 against 0.79). K_app 10 beats 30 (0.94 against 0.77).
- **VALID, top 15:** median PF 0.80 (range 0.64-1.03). One of 15 is ≥ 1.0. Harsh median 0.66.

## 3. Level-effect test: real round numbers against placebo levels (MEASURED)

`cand/rn_levels.py` runs 32 cells per step (K_app 10). Cells share events, so they are **not** independent. Read the counts as consistency, not as a test.

**Pooled avg R, weighted by trades:**

| Step | Cost | Split | Real (off 0) | Placebo +0.37·step | Placebo +0.63·step |
|---|---|---|---|---|---|
| $10 | mid | TRAIN | −0.025 | −0.115 | **+0.068** |
| $10 | mid | VALID | −0.071 | −0.017 | **+0.008** |
| $10 | lf_base | TRAIN | −0.207 | −0.276 | −0.106 |
| $10 | lf_base | VALID | −0.139 | −0.091 | −0.074 |
| $25 | mid | TRAIN | −0.014 | **+0.050** | +0.011 |
| $25 | mid | VALID | −0.078 | −0.110 | **+0.024** |
| $25 | lf_base | TRAIN | −0.155 | −0.100 | −0.184 |
| $25 | lf_base | VALID | −0.133 | −0.190 | −0.038 |

**Paired, per cell, at mid:** the mean of (real − placebo) avg R, and the number of cells where the real level is better.

| Step | Split | Real − placebo 1 | Real − placebo 2 |
|---|---|---|---|
| $10 | TRAIN | +0.072 (25 of 32) | −0.107 (0 of 32) |
| $10 | VALID | −0.047 (1 of 32) | −0.102 (2 of 32) |
| $25 | TRAIN | −0.059 (4 of 32) | −0.012 (13 of 32) |
| $25 | VALID | +0.041 (24 of 32) | −0.113 (3 of 32) |

**Reading (OPINION).**
- There is no sign that round numbers reject price better than arbitrary price levels on gold M1 in 2025-26.
- Real levels win 2 of 8 comparisons. They lose every comparison against the +0.63 placebo, even before costs.
- The literature effect cited in SYNTHESIS (Aggarwal-Lucey 2007, daily data, SOURCED) does not show up as a tradable intraday first-touch rejection.

## 4. Finalist tables (MEASURED)

**Params:** `{"step": 25, "M": 60, "mu": 2.0, "sigma": 0.3, "tp": 1.5, "tmax": 20, "wk": 0.5, "dep": 0.2, "sess": "active", "K_app": 10}`. The rest are defaults: W 2, tol 0.05, trig 1, news '30'.

| Cost | Split | n | PF | avg $/oz | avg R | WR | t(R) | avg risk |
|---|---|---|---|---|---|---|---|---|
| lf_base | TRAIN | 78 | **1.065** | +0.140 | −0.018 | 0.47 | −0.15 | 4.30 |
| lf_base | VALID | 62 | **0.769** | −1.340 | −0.033 | 0.44 | −0.23 | 10.89 |
| lf_harsh | TRAIN | 78 | 0.744 | −0.669 | −0.283 | 0.44 | −2.40 | 4.68 |
| lf_harsh | VALID | 62 | **0.634** | −2.330 | −0.178 | 0.42 | −1.32 | 11.36 |
| mid (gross) | TRAIN | 78 | 1.246 | +0.483 | +0.142 | 0.50 | 1.06 | 4.12 |
| mid (gross) | VALID | 62 | 0.822 | −0.992 | +0.027 | 0.44 | 0.18 | 10.67 |

**Other checks at `lf_base`:**
- TRAIN halves: PF 0.97 and 1.10. VALID halves: 0.63 and 0.98.
- Dropping the best month:
  - TRAIN, without 2025-12: PF 0.83;
  - VALID, without 2026-04: PF 0.64.
- Exits on TRAIN: 40 stop, 28 target, 10 time.

**Long and short at `lf_base`:**

| Split | Side | n | PF | avg $/oz | avg R | t |
|---|---|---|---|---|---|---|
| TRAIN | long (at a level from above) | 50 | 1.227 | +0.539 | +0.102 | 0.64 |
| TRAIN | short (at a level from below) | 28 | 0.672 | −0.572 | −0.233 | −1.12 |
| VALID | long | 36 | 0.568 | −3.167 | −0.029 | −0.15 |
| VALID | short | 26 | 1.325 | +1.189 | −0.038 | −0.17 |

The sides swap sign between TRAIN and VALID.

**Flip-eligible subset** (stop in [1.2, 4.0] $/oz):

| Cost | TRAIN n (share) / PF / avg $ | VALID n (share) / PF / avg $ |
|---|---|---|
| lf_base | 43 (55%) / 0.722 / −0.42 | 15 (24%) / 0.587 / −0.69 |
| lf_harsh | 43 / 0.379 / −1.24 | 14 / 0.434 / −1.11 |
| mid | 42 / 0.951 / −0.07 | 18 / 1.075 / +0.11 |

**Null tests** (`lf_base`):
- Random direction, VALID orders only, 50 seeds: **p = 0.76**.
  - Null mean −0.35 $/oz, null 95th percentile +2.67, null PF median 0.88.
  - Actual: −1.34 $/oz, PF 0.77.
- Random direction, TRAIN, 20 seeds: p = 0.19.
- Mirror:
  - TRAIN: PF 0.76;
  - VALID: PF 1.08, +0.40 $/oz.

**Finalist variants.** Each cell is n / PF / avg R.

| Override | base TRAIN | base VALID | mid TRAIN | mid VALID |
|---|---|---|---|---|
| none (finalist, real $25) | 78 / 1.07 / −0.02 | 62 / 0.77 / −0.03 | 78 / 1.25 / +0.14 | 62 / 0.82 / +0.03 |
| place 1 (L + 9.25) | 75 / **1.63** / +0.03 | 89 / 0.92 / −0.16 | 75 / 2.07 / +0.25 | 89 / 0.98 / −0.11 |
| place 2 (L + 15.75) | 74 / 1.08 / −0.06 | 77 / 0.70 / −0.08 | 74 / 1.31 / +0.15 | 77 / 0.76 / +0.01 |
| trig 0 (no wick, control) | 157 / 0.97 / −0.15 | 147 / **1.08** / +0.05 | 157 / 1.25 / +0.12 | 147 / 1.17 / +0.12 |
| step 10 | 178 / 0.82 / −0.09 | 183 / 0.88 / −0.06 | 178 / 0.97 / +0.07 | 183 / 0.95 / +0.01 |
| step 50 | 31 / 0.83 / −0.10 | 34 / 0.97 / +0.21 | 31 / 0.95 / +0.03 | 34 / 1.02 / +0.27 |
| sess all | 118 / 1.01 / −0.09 | 116 / 0.87 / −0.08 | 118 / 1.23 / +0.07 | 116 / 0.94 / −0.01 |

**Monthly table** (TRAIN+VALID, `lf_base`, `results/round_reject_monthly.csv`). PF is inf in a month with no losing trade.

| Month | n | sum R | avg R | sum $/oz | PF | avg risk |
|---|---|---|---|---|---|---|
| 2025-01 | 2 | −2.18 | −1.089 | −3.68 | 0.00 | 1.69 |
| 2025-02 | 4 | +4.78 | +1.196 | +11.43 | inf | 2.22 |
| 2025-03 | 2 | −0.64 | −0.320 | −0.51 | 0.71 | 2.21 |
| 2025-04 | 6 | −1.73 | −0.288 | −1.68 | 0.81 | 2.21 |
| 2025-05 | 3 | +1.75 | +0.583 | +6.05 | 2.76 | 3.33 |
| 2025-06 | 7 | −1.87 | −0.268 | −6.58 | 0.45 | 2.56 |
| 2025-07 | 7 | −3.81 | −0.544 | −4.92 | 0.46 | 1.94 |
| 2025-08 | 7 | +1.87 | +0.267 | −0.17 | 0.97 | 2.49 |
| 2025-09 | 2 | +0.28 | +0.142 | +4.98 | 4.57 | 2.89 |
| 2025-10 | 18 | −3.17 | −0.176 | −22.86 | 0.74 | 8.49 |
| 2025-11 | 9 | −3.11 | −0.346 | −9.25 | 0.60 | 4.10 |
| 2025-12 | 11 | +6.40 | +0.581 | +38.14 | 4.22 | 4.64 |
| 2026-01 | 15 | +2.22 | +0.148 | −99.63 | 0.42 | 20.10 |
| 2026-02 | 13 | +1.78 | +0.137 | +35.62 | 2.13 | 8.05 |
| 2026-03 | 12 | −9.86 | −0.822 | −30.95 | 0.66 | 10.98 |
| 2026-04 | 12 | +5.13 | +0.427 | +36.66 | 2.56 | 6.58 |
| 2026-05 | 10 | −1.29 | −0.129 | −24.80 | 0.40 | 5.81 |

In 2026-01 the sum is +2.2R but −99.6 $/oz. The average stop that month was 20 $/oz, against 2-5 in most of 2025, so a few wide-stop losses dominate the dollar sum. The two metrics disagree whenever stop sizes vary a lot.

## 5. Pass-bar checklist (finalist, MEASURED)

| Criterion | Required | Finalist | OK |
|---|---|---|---|
| n TRAIN / VALID | ≥ 150 / ≥ 60 | 78 / 62 | no |
| lf_base PF, TRAIN / VALID | ≥ 1.15 / ≥ 1.15 | 1.07 / 0.77 | no |
| lf_base avg, TRAIN / VALID | ≥ +0.15 / ≥ +0.15 | +0.14 / −1.34 | no |
| lf_harsh VALID PF | ≥ 1.0 | 0.63 | no |
| TRAIN halves PF > 1 | both | 0.97 / 1.10 | no |
| Random direction VALID | p ≤ 0.05 | 0.76 | no |
| Placebo levels worse than the real ones (SYNTHESIS kill rule) | — | placebo better | no |
| Near: VALID PF ≥ 1.05 and TRAIN PF ≥ 1.10, n ok | — | no | no |

## 6. Notes

- **OPINION.** HANDOFF l.36 (SOURCED via SYNTHESIS) found nothing on the crossing side of $10 levels. This run finds nothing on the rejection side either, and the zero-cost placebo comparison rules out a hidden gross effect that costs could be masking. H9 can be closed.
- **Engine.** No engine bug affects this module. It uses only `data.atr`, `features.rolling_max_prev/min_prev` and `data.news_block_mask`.
  - Caution: `sweep` ranks by t of R while PF is in $/oz. Here the finalist's TRAIN PF 1.07 hides an avg R of −0.02.
  - The `features.window_range` look-ahead found for H11 is described in `silver_bullet.md` §6.
- **HANDOFF.** Not updated, because the task forbids writing outside `xau_alpha/`.

## 7. Reproduce

```bash
cd /Users/mac/Desktop/TBT-Engine/xau_alpha
python3 lib/sweep.py round_reject --jobs 2 --grid '{"step": [10, 25, 50], "M": [60, 240], "mu": [1.0, 2.0], "K_app": [0, 10], "sigma": [0.3, 0.6], "tp": [1.0, 1.5], "tmax": [10, 20]}'   # stage 1
python3 lib/sweep.py round_reject --jobs 2 --grid '{"s1": [0,1,2,3,4,5], "wk": [0.5, 0.6], "dep": [0.1, 0.2], "sess": ["all", "active"], "K_app": [10, 30]}'   # stage 2
python3 cand/rn_levels.py                                                            # level-effect block
python3 cand/rnsb_eval.py round_reject '{"step": 25, "M": 60, "mu": 2.0, "sigma": 0.3, "tp": 1.5, "tmax": 20, "wk": 0.5, "dep": 0.2, "sess": "active", "K_app": 10}' --seeds 50 --placebo '[{"place":1},{"place":2},{"trig":0},{"step":10},{"step":50},{"sess":"all"}]'
```

Compute used, all with at most 2 workers: stage 1 about 2 min, stage 2 about 1 min, the level block about 3 min, and the finalist evaluation 20 s.
