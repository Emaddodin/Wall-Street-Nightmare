# H10: fade the edge of a range at a strong zone, with a real stop, targeting the range midpoint (`cand/range_fade.py`)

Date 2026-09-30. TRAIN 2025-01-21..2025-12-31, VALID 2026-01..05. TEST (>= 2026-06-01) was never simulated: the module cuts every array at 2026-06-01 first.
Labels: **MEASURED** = computed in this run; **SOURCED** = from `recon/SYNTHESIS.md` or a sibling result file; **OPINION** = mine.
Companion hypothesis H3 (failed breakout) is in `cand/reports/failed_breakout.md`. The shared zone code is `cand/zonekit.py`, which uses `features.zone_timeline`.

## Verdict: FAIL (clean "no edge"; weaker than H3)

- **MEASURED.** I searched 88 configs: 64 in stage 1 and 24 in stage 2.
  - No config reaches TRAIN PF ≥ 1.15 at `lf_base` with n ≥ 150.
  - Only 8 of 88 have TRAIN PF > 1.
  - Among the ranked configs (n ≥ 60), the best TRAIN t-stat is 0.03.
- **MEASURED.** The finalist is the best TRAIN t with n ≥ 60.
  - TRAIN: n 66, PF 0.88, −0.16 $/oz.
  - VALID: n 23, PF 0.285, −2.94 $/oz, t −3.1.
  - Random-direction null on VALID: p = 0.96.
- **MEASURED.** The setup is rare in the form the spec gives. A strong zone (≥ 6 touches) at a range edge, with a floor zone opposite, fires about 1.3-1.9 times per day when w = 1.0. With w = 0.5 it barely fires, and the P = 180 cells often have 0 trades.
- **MEASURED. H2-vs-H10 conflict check.** On one shared set of zones, both the continuation (H2 via `zone_retest`, and the H10 mirror) and the fade (H10) are unprofitable at `lf_base`. Event drifts flip sign between TRAIN and VALID.
  - There is no conflict to resolve. The zones carry no stable directional information at 5-30 minute horizons.
- **MEASURED.** Flip phase: stops are narrower than in H3. 71% of TRAIN trades and 39% of VALID trades have a stop within [1.2, 4.0] $/oz. The flip-eligible VALID subset still loses: n 9, PF 0.26.

## 1. Rules as implemented

The rule is stated for the short side, as in SYNTHESIS. The long side is the exact mirror: the same code runs on the negated series, with identical parameters and no directional veto. Units: A = `data.atr(m1)`.

| Step | Rule |
|---|---|
| Zones | `features.zone_timeline(h, l, A, L=240, k=3, w, K=3)` known at the close of bar t−1. The touch count is the number of pivots in the cluster. Greedy clustering does not depend on K, so the zones with K ≥ 6 are exactly the K = 6 zone set. |
| Range | Over bars t−P .. t−1: RH = max h, RL = min l, mid = (RH + RL) / 2. Require RH − RL ≤ wid·A[t−1]. |
| Edge (faded zone) | touches ≥ K_edge, z_lo > mid, and RH within [z_lo, z_hi + 0.5A], so the range top is this zone. If several qualify, the lowest is used. |
| Opposite zone | Some zone with touches ≥ 3, z_hi < mid, and RL within [z_lo − 0.5A, z_hi], i.e. a real range floor. OPINION: this is my operationalisation of "a lower zone with K ≥ 3". |
| Touch | h[t] ≥ z_lo and c[t−1] < z_lo: price arrives from inside the range. |
| Trigger | **trig = 1:** the first j in [t, t+2] with a bear engulf, or an upper wick ≥ 0.6·range with h ≥ z_lo > c. **trig = 0 (control arm):** j = t if c[t] ≤ z_hi. A close above z_hi, or a high at or above the stop, on bars t..j aborts the setup. |
| Entry, stop | Market at the close of j. Stop = z_hi + σ·A[j] (σ = 0.3). |
| Target | `mid`: the range midpoint, but only if it is at least 0.5 × the stop distance below the entry. OPINION: the minimum reward is mine; without it some midpoints sit inside the cost. `1R`: 1 × the stop distance. |
| Time and session | tmax 30. Decision bar in 06:00-12:00 London local or 08:30-11:30 ET. HIGH-event blackout ±30 min. One position at a time. |

**Spot check (MEASURED, TRAIN).** Short setup on 2025-09-23:
- Upper zone [3783.33, 3785.43] with 14 touches.
- 60-bar range 3777.59-3785.08, a width of 4.3 A.
- Touch at 12:34 UTC coming from below; trigger at 12:36.
- Stop 3785.93. Target at the midpoint 3781.33, which is 0.61R.

## 2. Grid stages and configs tried (MEASURED)

| Stage | Grid | Configs |
|---|---|---|
| 1 | SYNTHESIS grid P {60, 180} × wid {8, 12} × K_edge {6, 8} × TP {mid, 1R} = 16, extended with zone width w {0.5, 1.0} × trigger {on, off} | 64 |
| 2 | Top 3 of stage 1 (TRAIN t) × session {lonny, all hours} × σ {0.3, 0.6} × tmax {30, 60} | 24 |
| Smoke | 5 configs run before the sweep. Their VALID PF was printed (0.28-0.90) and not used. | 5 |
| Conflict | 3 touch-event sets, 2 H2 (`zone_retest`) zone settings × 2 costs, H10 mirror × 2 costs | — |

Results files:
- `cand/results/range_fade_s1_{train,valid}.csv` (the CLI copies are `range_fade_{train,valid}.csv`);
- `range_fade_s2_train.csv`;
- `range_fade_conflict.json`.

**Stage-1 marginals (TRAIN medians).** Every cell is negative at `lf_base`.
- By zone width: w = 1.0 gives PF 0.70; w = 0.5 gives 0.60 on a median n of 11.
- By range length: P = 60 gives 0.68; P = 180 gives 0.85 on a median n of 15.
- **Trigger against the control arm:** the trigger improves average R in 22 of 32 pairs (median +0.08 R), but average points in only 13 of 32 (median −0.06).
- **Target:** the midpoint is marginally better than 1R.

## 3. Finalist

`{"w":0.5,"P":60,"wid":12,"K_edge":6,"tp":"mid","trig":1,"sess":"lonny","sigma":0.3,"tmax":30}`

### 3.1 Overall

| Cost | Split | n | PF | Avg pts | Avg R | Avg risk |
|---|---|---|---|---|---|---|
| lf_base | TRAIN | 66 | 0.880 | −0.163 | +0.004 | 2.21 |
| lf_base | VALID | 23 | **0.285** | −2.942 | −0.551 | 5.38 |
| lf_harsh | TRAIN | 66 | 0.536 | −0.834 | −0.311 | 2.54 |
| lf_harsh | VALID | 23 | 0.225 | −3.622 | −0.637 | 5.79 |
| mid (gross) | TRAIN | 65 | 1.176 | +0.204 | +0.234 | 2.06 |
| mid (gross) | VALID | 23 | 0.414 | −2.071 | −0.354 | 5.19 |
| duka_raw | TRAIN | 66 | 0.777 | −0.316 | −0.072 | 2.34 |
| duka_raw | VALID | 23 | 0.273 | −3.038 | −0.536 | 5.59 |

- **PF of the TRAIN halves:** 0.76 / 0.97.
- **PF with the best month dropped:** TRAIN 0.71, VALID 0.19.
- **Stop quantiles (q10 / q50 / q90):**
  - TRAIN: 1.00 / 1.96 / 3.60;
  - VALID: 2.39 / 4.22 / 9.33 $/oz.

### 3.2 Long and short (lf_base)

| Side | TRAIN n | TRAIN PF | VALID n | VALID PF |
|---|---|---|---|---|
| Long (fade support) | 30 | 1.096 | 12 | 0.548 |
| Short (fade resistance) | 36 | 0.750 | 11 | 0.147 |

### 3.3 Flip-eligible subset: stop within [1.2, 4.0] $/oz

| Cost | TRAIN n | TRAIN PF | TRAIN avg pts | VALID n | VALID PF | VALID avg pts |
|---|---|---|---|---|---|---|
| lf_base | 47 | 1.426 | +0.47 | 9 | 0.259 | −1.66 |
| lf_harsh | 57 | 0.779 | −0.31 | 7 | 0.097 | −2.67 |
| mid | 44 | 2.112 | +0.97 | 10 | 0.475 | −0.96 |

Flip share: 71.2% of TRAIN trades and **39.1% of VALID trades**.

### 3.4 Nulls

- **Random direction (50 seeds, lf_base):**
  - VALID: real −2.94 against a null of −1.40 ± 0.98, **p = 0.961**.
  - TRAIN: real −0.16 against a null of −0.43 ± 0.33, p = 0.216.
- **Mirror (continuation with the same geometry):**
  - lf_base: TRAIN PF 0.471, VALID PF 1.054.
  - mid: TRAIN PF 0.598, VALID PF 1.268 (n 23).

### 3.5 Monthly results (lf_base)

| Month | n | Sum R | Avg pts | Month | n | Sum R | Avg pts |
|---|---|---|---|---|---|---|---|
| 2025-01 | 3 | −0.23 | +0.16 | 2025-10 | 5 | +3.96 | +0.79 |
| 2025-02 | 5 | −4.01 | −1.15 | 2025-11 | 6 | −4.42 | −2.11 |
| 2025-03 | 3 | +3.06 | +1.83 | 2025-12 | 6 | +1.90 | −0.04 |
| 2025-04 | 4 | +0.77 | +0.67 | 2026-01 (V) | 1 | −1.04 | −3.57 |
| 2025-05 | 3 | −3.17 | −2.78 | 2026-02 (V) | 7 | −5.59 | −4.08 |
| 2025-06 | 6 | −0.79 | −0.79 | 2026-03 (V) | 9 | −5.20 | −3.75 |
| 2025-07 | 11 | −0.63 | +0.11 | 2026-04 (V) | 3 | −3.14 | −3.58 |
| 2025-08 | 10 | +8.25 | +1.43 | 2026-05 (V) | 3 | +2.29 | +2.98 |
| 2025-09 | 4 | −4.41 | −1.80 | | | | |

## 4. H2-vs-H10 conflict check on one shared zone set

Measured with `cand/fb_rf_nulls.py conflict`; the output is in `cand/results/range_fade_conflict.json`.

**(a) Touch-event drift in the fade direction.** Mid price, no cost, in ATR units: d·(c[j+h] − c[j]) / A[j].

| Events | Split | n | +5 min | +15 min | +30 min |
|---|---|---|---|---|---|
| Finalist zones, trigger arm | TRAIN | 80 | +0.15 A (t 0.85) | **+0.98 A (t 3.05)** | +0.82 A (t 1.43) |
| Finalist zones, trigger arm | VALID | 25 | −0.24 A (t −0.65) | **−1.00 A (t −1.76)** | −1.39 A (t −1.84) |
| Finalist zones, control arm | TRAIN | 365 | −0.11 (t −1.34) | +0.40 (t 2.50) | +0.79 (t 2.97) |
| Finalist zones, control arm | VALID | 118 | −0.18 (t −1.08) | −0.24 (t −0.94) | +0.21 (t 0.53) |
| w = 1.0, K_edge = 6, control arm (broad) | TRAIN | 2086 | −0.00 (t −0.11) | −0.09 (t −1.30) | +0.03 (t 0.30) |
| w = 1.0, K_edge = 6, control arm (broad) | VALID | 794 | −0.05 (t −0.84) | +0.01 (t 0.10) | +0.31 (t 2.13) |

**(b) H2 continuation on the same zones.** This uses the sibling module `zone_retest` with its defaults, the zone definition L = 240, k = 3, and the w and K shown. SOURCED module, MEASURED run.

| Zones | Cost | TRAIN n | TRAIN PF | VALID n | VALID PF |
|---|---|---|---|---|---|
| w 0.5, K 6 | lf_base | 56 | 0.373 | 29 | 0.529 |
| w 0.5, K 6 | mid | 56 | 0.673 | 29 | 0.623 |
| w 1.0, K 6 | lf_base | 159 | 0.695 | 63 | 0.565 |
| w 1.0, K 6 | mid | 159 | 1.056 | 63 | 0.661 |

**Reading (OPINION).**
- Neither the fade nor the continuation is positive at cost on the shared zones, so the "both positive" conflict never arises.
- On the small finalist event set, the fade drift is strongly positive in TRAIN and negative in VALID. On the broad event set it is about zero. That is noise, or a regime flip, not a structural edge.
- This is the same TRAIN-to-VALID sign flip seen in H1 (SOURCED `cand/reports/sweep_reclaim.md`) and in H3's twin test.

## 5. Pass bar (SYNTHESIS §5)

| Criterion | Value | Pass? |
|---|---|---|
| n ≥ 150 TRAIN, ≥ 60 VALID | **66 / 23** | no |
| lf_base PF ≥ 1.15, TRAIN and VALID | **0.88 / 0.285** | no |
| lf_base avg ≥ +0.15 | **−0.16 / −2.94** | no |
| lf_harsh VALID PF ≥ 1.0 | **0.225** | no |
| Both TRAIN halves PF > 1 | **0.76 / 0.97** | no |
| Random-direction null p ≤ 0.05, VALID | **0.961** | no |
| "Near" | VALID PF 0.285 | no |

**Verdict: FAIL.**
- The best VALID PF among the engine's top-15 VALID check is 1.133, for w 1.0, P 60, wid 8, K_edge 8, mid, trig 1. But that config has TRAIN PF 0.749 and VALID n 45, so it is not a candidate.
- T2's anecdote (a $4 stop would have survived) does not generalise: on VALID the median stop needed is already $4.2.

## 6. Files

- `cand/range_fade.py`: GRID, `orders()`, `setups()`.
- `cand/zonekit.py`: shared with H3.
- `cand/fb_rf_stages.py`: `rf_stage2`, `diag`.
- `cand/fb_rf_nulls.py`: `conflict`.
- `cand/results/range_fade_*`.
- No engine file was modified. The engine notes are in `failed_breakout.md` §6.
