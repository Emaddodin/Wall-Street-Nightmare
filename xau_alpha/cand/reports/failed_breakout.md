# H3: failed breakout, then breakdown, then retest from the other side (`cand/failed_breakout.py`)

Date 2026-09-30. TRAIN 2025-01-21..2025-12-31, VALID 2026-01..05. TEST (>= 2026-06-01) never simulated: the module cuts every array at 2026-06-01 before computing anything, and the 2026-09-21/22 video trades were not looked at.
Labels: **MEASURED** = computed in this run; **SOURCED** = from `recon/SYNTHESIS.md` or a sibling result file; **OPINION** = mine.
Companion hypothesis H10 (range fade) is in `cand/reports/range_fade.md`. The shared zone code is `cand/zonekit.py`, built on `features.zone_timeline`.

## Verdict: FAIL (no edge that survives VALID or the twin null)

- **MEASURED.** I searched 276 configs by the protocol: 216 in stage 1 and 60 in stage 2. Only 2 stage-1 configs and 18 stage-2 configs reach TRAIN PF ≥ 1.15 at `lf_base` with n ≥ 150. None has a TRAIN t-stat above 1.5 (the best is 1.48).
- **MEASURED.** The finalist is the best TRAIN t in stage 2, chosen by a pre-set rule.
  - TRAIN: PF 1.295, +0.64 $/oz, n 395, but the first half of TRAIN has PF 0.93.
  - VALID: PF 0.874, −0.67 $/oz, n 185, t −1.66.
  - `lf_harsh` VALID PF: 0.745.
  - Gross edge (`mid`) VALID PF: 0.95.
- **MEASURED.** The random-direction null on VALID gives p = 0.745 (50 seeds).
- **MEASURED. It fails the kill test "must beat the no-fake-out twin".**
  - On TRAIN, the fake-out beats its twin in 205 of 216 stage-1 configs on average R.
  - On VALID the ranking reverses. At the finalist's parameters, the twin (plain breakdown then retest) makes VALID PF 1.26 against 0.87 for H3, and 1.41 against 0.95 at `mid`.
  - The failed-break "information" is a regime-dependent sign, the same pattern H1 found for session-level sweeps (SOURCED `cand/reports/sweep_reclaim.md`).
- **MEASURED.** Flip phase: 36% of TRAIN trades have a stop in [1.2, 4.0] $/oz, but only **2.2% of VALID trades (4 trades in 5 months)** do.
  - The median stop is $4.9 on TRAIN and $9.9 on VALID.
  - Even with an edge, this setup would be almost untradeable for the $13 account in the 2026 volatility regime.

## 1. Rules as implemented

The rule is stated for the short side, as in SYNTHESIS. The long side is its exact mirror: `zonekit` runs the same code on the negated series (o' = −o, h' = −l, l' = −h, c' = −c), with identical parameters and no directional veto. **Units:** A = `data.atr(m1)`, the M1 Wilder ATR14 on mid. No dollar thresholds are used; the $1.2-4.0 flip band is applied only in reporting.

| Step | Rule |
|---|---|
| Zones | `features.zone_timeline(h, l, A, L=240, k=3, w, K)`, frozen at the close of bar s = i0 − N − 1. Freezing them **before** the fake-out window stops the fake-out's own pivots from widening the zone. OPINION: this is my causal reading of the spec. |
| Start | c[s] ≤ z_hi: price is inside or below the zone before the window, so this is a failed breakout, not a support that price was already sitting on. OPINION: not in the SYNTHESIS text; added so the H3 arm and the twin differ only in the fake-out. |
| Fake-out | Window = bars i0−N .. i0−1, and f = argmax h over the window. `fake=+1` (H3): h[f] > z_hi + φ·A[i0]. `fake=−1` (twin): h[f] ≤ z_hi + φ·A[i0]. `fake=0`: no condition. |
| Breakdown | c[i0] < z_lo − β·A[i0], and every close c[f..i0−1] ≥ z_lo − β·A[i0], so i0 is the first close below since the extreme. If several zones qualify on one bar, the lowest (the one just crossed) is used. |
| Retest | The first bar i1 in (i0, i0+R] with h[i1] ≥ z_lo − 0.1·A[i1]. |
| Entry | **a:** market at the close of i1 if c[i1] < z_lo (T7). **b:** the first j in [i1, i1+2] with a bear engulf, or an upper wick ≥ 0.6·range with h ≥ z_lo > c; a close above z_hi first aborts. **c:** sell limit at z_lo − 0.1·A placed at the close of i0, expiring after R bars. |
| Stop | max(h[f], z_hi, h[i1..j]) + σ·A, as an absolute price. |
| Target | `near`: the nearest confirmed swing low of the last 240 bars that is ≥ 1R away; if none lies within 4R, 2R. `r`: tp_r × stop distance. |
| Time | tmax minutes. One position at a time (`sim`). |
| Session | Decision bar in 06:00-12:00 London local or 08:30-11:30 ET (`lon_mod` / `ny_mod`). HIGH-event blackout ±30 min (the equity < $21 rule, SYNTHESIS N2). If long and short fire on the same bar, both are dropped. |

**Detector spot check (MEASURED, TRAIN).** Short setup on 2025-03-24:
- Zone [3025.56, 3026.32], known at 12:11 UTC with close 3024.65.
- Fake-out high 3028.685 at 12:26 (above the threshold of 3026.80).
- First close below z_lo at 12:32 (3025.435).
- Retest at 12:33: high 3025.64, close 3025.33, so sell.
- Stop 3028.92, target 2R. All consistent with the rule.

## 2. Grid stages and configs tried (MEASURED)

| Stage | Grid | Configs | Selection |
|---|---|---|---|
| 1 | w = 1.0 (fixed from H2 stage 1, whose top-ranked configs all use w = 1.0; SOURCED `zone_retest_stage1_train.csv`) × K {3, 6} × N {10, 20, 30} × φ {0.3, 0.6, 1.0} × β {0, 0.2} × R {5, 15} × mode {a, b, c}. σ = 0.3, TP = nearest (ρ = 1), tmax = 45 | 216 | TRAIN t-stat of R (`sweep.py`) |
| 2 | Top 5 of stage 1 × σ {0.1, 0.3} × TP {nearest ≥ 1R, 1.5R, 2R} × tmax {20, 45} | 60 | TRAIN t-stat |
| Null | Twin (`fake=−1`) on the whole stage-1 grid (TRAIN only) | 216 | Not used for selection |
| Null | Top 5 of stage 2 × {fake +1, −1, 0}, plus a strict twin φ = 0 for the finalist, at `lf_base` and `mid` | 16 arms | Not used for selection |
| Smoke | Defaults, modes a/b/c × fake ±1, run before the sweep. Their VALID PF was printed (all 0.79-0.93) and not used. | 6 | — |

- **276 selection configs.** Every config is in `cand/results/failed_breakout_s1_train.csv` (216) and `failed_breakout_s2_train.csv` (60). The engine's top-15 VALID check of stage 1 is in `failed_breakout_s1_valid.csv`. `failed_breakout_{train,valid}.csv` is the CLI copy of stage 1.
- **Stage-1 marginals (TRAIN, median over the grid).** Every parameter moves toward more selective settings, and none of it is enough:
  - K = 6 beats K = 3 (PF 0.90 against 0.84);
  - N = 30 beats N = 10 (0.94 against 0.78);
  - φ = 1.0 beats φ = 0.3 (0.90 against 0.84);
  - mode a ≈ mode c > mode b.
  - 25 of 216 configs have TRAIN PF > 1, 8 have t > 0, and 0 have t > 2.
- **Stage 2.** 54 of 60 have TRAIN PF > 1. The best t is 1.48, and only 7 of 60 have PF > 1 in both TRAIN halves. The 2R target and tmax 45 help; σ is flat.

## 3. Finalist

`{"w":1.0,"K":6,"N":30,"phi":1.0,"beta":0.2,"R":5,"mode":"a","sigma":0.1,"tp_mode":"r","tp_r":1.5,"tmax":45}`
(sess `lonny`, news 30, L = 240, k = 3, τ = 0.1)

### 3.1 By cost model (MEASURED; points are $/oz per 1 oz; PF is on points)

| Cost | Split | n | PF | Avg pts | Avg R | WR | Avg risk |
|---|---|---|---|---|---|---|---|
| lf_base | TRAIN | 395 | **1.295** | **+0.644** | +0.075 | 0.519 | 5.75 |
| lf_base | VALID | 185 | **0.874** | **−0.670** | −0.117 | 0.438 | 12.40 |
| lf_harsh | TRAIN | 395 | 0.961 | −0.100 | −0.079 | 0.476 | 6.07 |
| lf_harsh | VALID | 186 | **0.745** | −1.470 | −0.191 | 0.403 | 12.78 |
| mid (gross) | TRAIN | 393 | 1.497 | +0.994 | +0.158 | 0.529 | 5.59 |
| mid (gross) | VALID | 184 | **0.950** | −0.254 | −0.076 | 0.451 | 12.20 |
| duka_raw | TRAIN | 395 | 1.209 | +0.466 | +0.038 | 0.504 | 5.87 |
| duka_raw | VALID | 186 | 0.839 | −0.865 | −0.134 | 0.430 | 12.58 |

- **TRAIN t-stat (R):** 1.48; VALID t −1.66.
- **TRAIN halves PF:** 0.931 / 1.693, which fails "both halves > 1".
- **PF after dropping the best month:** TRAIN 1.18, VALID 0.70.
- **Exit reasons:**
  - TRAIN: stop 140, time 148, target 107.
  - VALID: stop 81, time 72, target 32.
- **Neighbour robustness (engine top-15 VALID check of stage 1, SOURCED `failed_breakout_s1_valid.csv`).** The finalist's stage-1 parent and its neighbours have VALID PF 0.69-0.94 (median 0.87). The stage-2 top 5 have VALID PF 0.85-0.89. There is no robust neighbourhood.
- **Points PF vs R (OPINION).** TRAIN points-PF 1.295 sits next to avg R of only +0.075, because the points PF over-weights high-volatility (wide-stop) trades. The VALID t-stat on R is negative whichever way it is measured.

### 3.2 Long / short (lf_base, MEASURED)

| Side | TRAIN n | TRAIN PF | TRAIN avg pts | VALID n | VALID PF | VALID avg pts |
|---|---|---|---|---|---|---|
| Long | 207 | 1.076 | +0.18 | 110 | 0.835 | −0.92 |
| Short | 188 | 1.581 | +1.16 | 75 | 0.938 | −0.31 |

The TRAIN profit comes mostly from shorts, the fade of failed up-breaks in the 2025 uptrend. Neither side is profitable on VALID.

### 3.3 Flip-eligible subset: stop within [1.2, 4.0] $/oz (MEASURED)

| Cost | TRAIN n | TRAIN PF | TRAIN avg pts | VALID n | VALID PF | VALID avg pts |
|---|---|---|---|---|---|---|
| lf_base | 142 | 1.219 | +0.29 | 4 | 0.00 | −3.66 |
| lf_harsh | 111 | 0.630 | −0.68 | 1 | 0.00 | −3.70 |
| mid | 149 | 1.584 | +0.68 | 5 | 0.00 | −3.44 |

- **Stop quantiles (q10 / q50 / q90):**
  - TRAIN: 2.62 / 4.85 / 9.99 $/oz;
  - VALID: 5.80 / 9.89 / 22.76 $/oz.
- **Flip share:** 35.9% of TRAIN trades and **2.2% of VALID trades**.
- SYNTHESIS predicted that the fake-out-to-entry distance would often exceed $4 in high volatility. In 2026 it almost always does.

### 3.4 Nulls (MEASURED)

**Random direction.** Same times, stops, targets and exits; direction drawn at random; 50 seeds (1000..1049, the same draws as `nulltest.random_direction`); `lf_base`.

| Split | Real avg pts | Null mean ± sd | Null median PF | p (null ≥ real) |
|---|---|---|---|---|
| VALID | −0.670 | −0.081 ± 0.814 | 0.988 | **0.745** |
| TRAIN | +0.644 | −0.197 ± 0.306 | 0.919 | 0.020 (in-sample, after choosing 1 of 276 configs, so not evidence) |

**Mirror** (every direction reversed), `lf_base`: TRAIN PF 0.682, VALID PF 1.060.

**No-fake-out twin.** This is the SYNTHESIS kill test.

- **Grid-wide, TRAIN** (216 paired configs, `cand/results/failed_breakout_twin_train.csv`):
  - H3 beats its twin on average R in 205 of 216 configs (median +0.094 R).
  - On average points it wins only 133 of 216 (median +0.075 pts). Part of the R advantage is cost dilution, because the fake-out makes stops wider.
  - The twin itself never reaches TRAIN PF > 1 (best t −2.45).
- **Finalist parameters, TRAIN vs VALID** (`cand/results/failed_breakout_twin_final.csv`):

| Arm | TRAIN PF (base) | TRAIN avg R | VALID PF (base) | VALID avg R | VALID PF (mid) |
|---|---|---|---|---|---|
| H3, fake-out required | 1.295 | +0.075 | 0.874 | −0.117 | 0.950 |
| Twin, no fake-out (φ = 1.0) | 0.727 | −0.214 | **1.258** | −0.025 | 1.407 |
| Strict twin (price never above z_hi) | 0.688 | −0.235 | 1.099 | −0.083 | 1.241 |
| All breakdowns (no condition) | 0.980 | −0.088 | 0.990 | −0.079 | 1.091 |

- The same reversal holds for all 5 stage-2 top configs: H3 VALID PF 0.85-0.89 against twin VALID PF 1.11-1.29.
- **OPINION.** The fake-out condition picks up a 2025-specific pattern: fading failed up-breaks paid in the 2025 uptrend. It carries no stable information. The twin's VALID points PF of 1.26 comes with an average R of −0.03, so it is high-volatility trades weighing more in points, not an edge. I do not propose it as a candidate: it was never selected, and it fails TRAIN at PF 0.73.

## 4. Monthly table for the finalist (lf_base, TRAIN + VALID; R per trade = pnl / initial risk)

| Month | Split | n | Sum R | Avg pts | PF | Avg risk |
|---|---|---|---|---|---|---|
| 2025-01 | T | 17 | −2.58 | −0.15 | 0.88 | 3.09 |
| 2025-02 | T | 27 | +0.49 | −0.22 | 0.86 | 3.95 |
| 2025-03 | T | 32 | −3.22 | −0.49 | 0.75 | 4.02 |
| 2025-04 | T | 41 | −9.75 | −1.56 | 0.56 | 6.66 |
| 2025-05 | T | 41 | +2.28 | +0.69 | 1.26 | 6.56 |
| 2025-06 | T | 31 | +2.56 | +0.40 | 1.19 | 5.20 |
| 2025-07 | T | 38 | +7.62 | +0.76 | 1.56 | 3.70 |
| 2025-08 | T | 38 | +12.80 | +1.68 | 2.90 | 3.82 |
| 2025-09 | T | 29 | +2.32 | +0.11 | 1.04 | 5.69 |
| 2025-10 | T | 19 | +9.39 | +5.56 | 3.73 | 11.74 |
| 2025-11 | T | 38 | −0.83 | +0.00 | 1.00 | 7.70 |
| 2025-12 | T | 44 | +8.47 | +2.27 | 2.08 | 7.14 |
| 2026-01 | V | 42 | −9.33 | −2.74 | 0.53 | 10.83 |
| 2026-02 | V | 39 | +0.95 | +2.86 | 1.60 | 15.47 |
| 2026-03 | V | 31 | −3.57 | −0.69 | 0.87 | 15.60 |
| 2026-04 | V | 32 | −0.12 | +0.04 | 1.01 | 10.66 |
| 2026-05 | V | 41 | −9.52 | −2.45 | 0.54 | 10.02 |

The TRAIN gains are concentrated in July-August and October-December 2025. The first four months of 2025 lose.

## 5. Pass bar check (SYNTHESIS §5)

| Criterion | Value | Pass? |
|---|---|---|
| n ≥ 150 TRAIN, ≥ 60 VALID | 395 / 185 | yes |
| lf_base PF ≥ 1.15, TRAIN and VALID | 1.295 / **0.874** | **no** |
| lf_base avg ≥ +0.15, TRAIN and VALID | +0.64 / **−0.67** | **no** |
| lf_harsh VALID PF ≥ 1.0 | **0.745** | **no** |
| Both TRAIN halves PF > 1 | **0.93** / 1.69 | **no** |
| Random-direction null p ≤ 0.05 on VALID | **0.745** | **no** |
| Beats the no-fake-out twin (SYNTHESIS H3 kill test) | TRAIN yes, **VALID no** | **no** |
| "Near" (VALID PF ≥ 1.05 and TRAIN PF ≥ 1.10) | 0.874 | no |

**Verdict: FAIL.** A clean "no edge" result for the owner's setup C on M1 multi-touch zones.

## 6. Engine notes (no engine file was modified)

- **No bug found** in `sim.py`, `sweep.py`, `features.py` or `nulltest.py` for this use. Limit entries (mode c) behave as documented, filling at min(px, open) without entry slippage.
- **Caveat (OPINION): `sim.stats` PF is on points, while `sweep.py` ranks by the t-stat of R.** Gold's volatility doubled from 2025 to 2026, so points-PF over-weights wide-stop, high-volatility trades. Examples here:
  - TRAIN PF 1.295 with avg R only +0.075;
  - the twin's VALID PF 1.258 with avg R −0.025.
  - Suggestion: add an R-based PF to `stats`.
- **Performance:** `features.zone_timeline` takes 12-17 s per (w, K), a Python loop over about 165k events, and `zonekit` caches it per process. `nulltest.random_direction` re-simulates both splits for each split call. `fb_rf_stages.diag` uses the same seeds (1000 + s) and shares one simulation per seed.

## 7. Files

- `cand/failed_breakout.py`: GRID + `orders()` + `setups()`.
- `cand/zonekit.py`: shared causal zone, pivot and session helpers.
- `cand/fb_rf_stages.py`: stage 2 and finalist diagnostics.
- `cand/fb_rf_nulls.py`: twin and conflict checks.
- `cand/results/failed_breakout_s1_{train,valid}.csv`, `failed_breakout_s2_train.csv`, `failed_breakout_twin_{train,final}.csv`, `failed_breakout_{train,valid}.csv`.
- Reproduce the finalist: `python3 cand/fb_rf_stages.py diag failed_breakout '<params json>' 50` (about 30 s, one process).
