# H11: ICT Silver Bullet, a fair value gap after a liquidity sweep, entered with a limit order (`cand/silver_bullet.py`)

Date 2026-09-30. TRAIN 2025-01-21..2025-12-31, VALID 2026-01..05. TEST (>= 2026-06-01) was never simulated: `sweep.py` and `cand/rnsb_eval.py` drop every order with t >= 2026-06-01 before simulation, and `final=True` was never used. The video trades of 2026-09-21/22 were not touched.
Labels: **MEASURED** = computed in this run; **SOURCED** = from `recon/SYNTHESIS.md` or a sibling file; **OPINION** = mine.
Companion hypothesis H9 (round-number rejection) is in `cand/reports/round_reject.md`. H11 is the better of the two, and it still fails.

## Verdict: FAIL (clean "no edge": a TRAIN-only effect that reverses on VALID)

- **MEASURED. Configs tried:** 288 in this attempt.
  - Stage 1: 120.
  - Stage 2: 144.
  - Finalist placebos and variants: 24.
  - An earlier attempt ran 96 configs with a cache bug (see §6). Those are superseded and not used.
- **MEASURED. Finalist** (best TRAIN t of stage 2): window 10:00-11:00 plus 14:00-15:00 ET, equal-highs/lows liquidity, gap ≥ 0.5·A, target = the opposite day extreme, stop pad 0.5·A, sweep within 30 bars, tmax 120.
  - TRAIN at `lf_base`: n 118, PF 1.63, +1.49 $/oz, t 1.33.
  - VALID at `lf_base`: n 48, **PF 0.86, −0.97 $/oz**.
  - VALID at `lf_harsh`: PF 0.59.
  - Random-direction null on VALID (50 seeds): **p = 0.92**.
  - The mirror (every direction reversed) has VALID PF 1.99.
- **MEASURED. The ICT-specific ingredients carry no out-of-sample information.**
  - The Silver Bullet hours are not special. On VALID, the real 10:00 and 14:00 hours (PF 1.00 / 0.69) sit below the median of 12 placebo hours (1.23). All three placebo hour-pairs beat the real pair: PF 1.33 / 2.17 / 1.61 against 0.86.
  - The gap midpoint is not special. PF falls steadily from the shallow edge to the deep edge (VALID 0.91 → 0.62). OPINION: this is adverse selection on deeper limit fills.
- **Pass-bar misses:**
  - n (118 < 150 TRAIN, 48 < 60 VALID);
  - VALID PF and average;
  - harsh VALID PF;
  - the null test.
  - "Near" is also missed: VALID PF 0.86 < 1.05.
- **MEASURED. One config out of 30 validated meets the numeric "near" thresholds by chance:** `10,14 / both / δ 0.25`, stage 1, TRAIN-t rank 7.
  - TRAIN: n 181, PF 1.19.
  - VALID: n 71, PF 1.07, but avg R +0.01, harsh PF 0.93, and random-direction p = 0.67.
  - Its mirror does better on VALID (PF 1.35).
  - Picking it would be selecting on VALID. I report it, but it does not change the verdict.
- **MEASURED. Flip phase.** Stops are set by the sweep extreme, so they grow with volatility: average risk is 4.5 $/oz on TRAIN and 12.0 on VALID.
  - Flip-eligible share (stop 1.2-4.0): 52% on TRAIN, 15% on VALID.
  - The flip-eligible TRAIN subset loses: n 62, PF 0.95 at `lf_base` and 0.51 at `lf_harsh`.

## 1. Rules as implemented

Each rule is stated for the long side. The short side is the exact mirror, with identical parameters and no directional veto. A = `data.atr(m1)` (M1 Wilder ATR14). A5 = the same on causal M5 bars (`data.resample_causal(m1, 5)`, value at `known[i]`).

| Step | Rule |
|---|---|
| Liquidity (`liq`) | **sess**: prior trading-day low (`prev_day_hl`, live all day); Asia low 00:00-06:59 UTC (live 07:00-19:59 UTC); London low 07:00-11:59 UTC (live 12:00-19:59 UTC). **eq**: equal lows, meaning two confirmed k = 3 pivot lows more than 3 and at most 240 bars apart, within 0.1·A5, with no lower low between them. The level is the lower of the two. It is live from the confirmation of the second pivot, for 240 bars. **both**: the union. |
| Sweep | The first bar s, after the level is live, with l[s] < level − δ·A5[s]. Each level can be swept only once. |
| Displacement + FVG at bar i | The `features.fvg` bull gap l[i] > h[i−2], with gap ≥ g·A[i]. The middle bar i−1 must be bullish, with body ≥ 1.5 × the mean body of the 20 bars before it, and body/range ≥ 0.7. The latest sell-side sweep must be in [i − look, i − 1], and c[i] must be back above the swept level. |
| Window | Bar i must fall in the New York local hours `win`: '3' = 03:00-03:59, '10', '14', '10,14', or 'sb' = 3 + 10 + 14. Only the first qualifying setup per window per NY day is taken. |
| Entry | A buy limit at gap_hi − e·(gap_hi − gap_lo), with e = 0.5 (the midpoint). It is placed at ts[i] + 60 s and expires after 25 min. |
| Stop / target | Stop = min(l[s..i]) − sb_stop·A[i]. Target: `2R` from the limit price, or `opp` = the running trading-day high at bar i. With `opp`, the setup is skipped if that high is less than 1R away. |
| Exit / gates | Time stop tmax after the fill. Flat by 16:45 ET. No decisions at 20:30-23:30 UTC, after 19:00 UTC on Friday, on Sunday UTC, or at 16:25-18:00 ET. HIGH-event blackout at the decision bar: `news` '30' = [T−30, T+30] (the SYNTHESIS N2 rule below $21); '5_15' = [T−5, T+15]. |

**Spot checks (MEASURED, TRAIN).**
- 2025-01-31, 10:00 ET, long.
  - Bull FVG with l = 2812.425 > h[i−2] = 2811.14.
  - The displacement bar has body 1.31 and body/range 0.75.
  - Limit at the midpoint, 2811.7825.
- 2025-02-05, 10:35 ET (finalist), short.
  - Bear FVG with h = 2874.705 < l[i−2] = 2875.78.
  - The displacement bar has body/range 0.90.
  - Limit at 2875.243, stop 2878.34.
  - The target is the day low 2839.7, 11R away, so this trade effectively has a time exit. Finalist exits on TRAIN: 66 stop, 42 time, 10 target.

## 2. Grid stages and configs tried (MEASURED)

| Stage | Grid | Configs | Files |
|---|---|---|---|
| 1 | SYNTHESIS grid: window {3, 10, 14} plus '10,14' and 'sb' × g {0.3, 0.5} × target {2R, opp}. Crossed with liq {sess, eq, both} × δ {0, 0.25}. Fixed: e 0.5, disp 1.5, br 0.7, sb_stop 0.2, look 60, tmax 60, news '30'. | 120 | `results/silver_bullet_s1_{train,valid}.csv` |
| 2 | 6 bases (the top 5 by TRAIN t, all with win '10' and n 68-100, plus the best with n ≥ 150: '10,14 / eq / δ 0.25') × sb_stop {0.2, 0.5} × tmax {30, 60, 120} × look {30, 60} × news {'30', '5_15'} | 144 | `results/silver_bullet_s2_{train,valid}.csv` (the CLI copies are `silver_bullet_{train,valid}.csv`) |
| Finalist | Placebo hours {1, 2, 4, …, 15}, placebo pairs {8,12; 9,13; 11,15}, entry depth e {0, 0.25, 0.75, 1.0}, liq {both, sess} | 24 | `results/silver_bullet_final.json`, `silver_bullet_monthly.csv` |
| Check | The one numerically "near" config (already in stage 1), with its null test | (1) | `results/silver_bullet_alt_final.json` |

Every stage validated its top 15 by TRAIN t on VALID (base and harsh), as `sweep.py` does. That makes 30 validated configs in total.

**Stage 1, TRAIN (MEASURED).**
- 35 of 120 configs have PF > 1.
- The best t is 1.10 (win 10, n 84).
- 32 configs reach n ≥ 150; all of them use the '10,14' or 'sb' window. Four of these have PF ≥ 1.15, and their t is 0.82-0.92.
- **Medians by window:**
  - 10: PF 0.95;
  - 14: 1.00;
  - 3: 0.72;
  - 10,14: 0.95;
  - sb: 0.87.
- **By liquidity:** session levels alone fire rarely (median n 32, PF 0.60). eq and both have PF 0.96-0.97.
- **By gap size:** g = 0.5 beats 0.3.

**The SYNTHESIS-literal cells** (liq = both, δ = 0), TRAIN `lf_base`, shown as PF (t):

| win | g 0.3 · 2R | g 0.3 · opp | g 0.5 · 2R | g 0.5 · opp |
|---|---|---|---|---|
| 10 (primary) | 0.91 (−0.11) | 1.10 (0.80) | 1.13 (0.96) | 1.20 (1.10) |
| 14 | 0.81 (−1.75) | 0.77 (−1.16) | 1.09 (−0.34) | 1.02 (0.13) |
| 3 | 0.70 (−2.41) | 0.77 (−1.48) | 0.61 (−1.93) | 0.67 (−0.95) |

**Stage 2, TRAIN (MEASURED).**
- 123 of 144 configs have PF > 1. The bases were chosen for this.
- The best t is 1.33 (n 118).
- A shorter sweep look-back (30 bars) gives higher PF (median 1.62 against 1.04) but fewer trades.
- The news window ('30' against '5_15') barely matters.

**The VALID look (MEASURED).**
- Stage-1 top 15 at `lf_base`: median PF 1.05 (range 0.56-1.69), but the median VALID n is only 40.
- Stage-2 top 15: median PF 0.87 (range 0.54-1.30). Only 3 of 15 are ≥ 1.0, and the median VALID n is 48.
- Harsh VALID medians: 0.93 and 0.64.

## 3. Finalist tables (MEASURED)

**Params:** `{"win": "10,14", "g": 0.5, "tp": "opp", "liq": "eq", "delta": 0.25, "sb_stop": 0.5, "tmax": 120, "look": 30, "news": "30"}`. The rest are defaults: e 0.5, disp 1.5, br 0.7, exp 25 min, reclaim on.

| Cost | Split | n | PF | avg $/oz | avg R | WR | t(R) | avg risk |
|---|---|---|---|---|---|---|---|---|
| lf_base | TRAIN | 118 | **1.627** | +1.488 | +0.266 | 0.41 | 1.33 | 4.48 |
| lf_base | VALID | 48 | **0.858** | −0.965 | −0.045 | 0.33 | −0.21 | 12.00 |
| lf_harsh | TRAIN | 112 | 1.296 | +0.812 | −0.081 | 0.35 | −0.43 | 4.63 |
| lf_harsh | VALID | 45 | **0.594** | −3.045 | −0.287 | 0.29 | −1.40 | 11.92 |
| mid (gross) | TRAIN | 119 | 1.817 | +1.813 | +0.389 | 0.44 | 1.95 | 4.46 |
| mid (gross) | VALID | 48 | 0.893 | −0.715 | −0.009 | 0.33 | −0.04 | 12.00 |

n differs between cost models because limit fills depend on the widened bid/ask.

**Other checks at `lf_base`:**
- TRAIN halves: PF 1.89 and 1.45. VALID halves: 1.18 and 0.51.
- Dropping the best month:
  - TRAIN, without 2025-05: PF 1.33;
  - VALID, without 2026-01: PF 0.82.

**Long and short at `lf_base`:**

| Split | Side | n | PF | avg $/oz | avg R | t |
|---|---|---|---|---|---|---|
| TRAIN | long | 49 | 1.684 | +1.405 | +0.324 | 1.29 |
| TRAIN | short | 69 | 1.595 | +1.547 | +0.224 | 0.77 |
| VALID | long | 21 | 1.032 | +0.203 | +0.054 | 0.13 |
| VALID | short | 27 | 0.737 | −1.873 | −0.122 | −0.51 |

**Flip-eligible subset** (stop in [1.2, 4.0] $/oz):

| Cost | TRAIN n (share) / PF / avg $ | VALID n (share) / PF / avg $ |
|---|---|---|
| lf_base | 62 (52%) / 0.945 / −0.09 | 7 (15%) / 1.48 / +0.89 |
| lf_harsh | 57 / 0.51 / −1.00 | 6 / 0.24 / −1.78 |
| mid | 63 / 1.26 / +0.39 | 7 / 1.71 / +1.25 |

**Null tests** (`nulltest`, `lf_base`):
- Random direction, VALID orders only, 50 seeds: **p = 0.92**.
  - Null mean +2.33 $/oz, null 95th percentile +5.88, null PF median 1.37.
  - Actual: −0.97 $/oz, PF 0.86.
- Random direction, TRAIN, 20 seeds (information only): p = 0.048. Null mean −0.09.
- Mirror:
  - TRAIN: PF 0.535, t −2.9;
  - VALID: **PF 1.985**, +4.71 $/oz.
- OPINION: the direction information was real in TRAIN and flipped sign in VALID. That looks like a regime-dependent or overfit effect, not a structural one.

**Placebo hours and entry depths** (the finalist with one field overridden). Each cell is n / PF / avg R.

| Override | base TRAIN | base VALID | mid TRAIN | mid VALID |
|---|---|---|---|---|
| win 1 | 93 / 0.74 / −0.34 | 32 / 0.41 / −0.37 | 98 / 1.01 / −0.07 | 33 / 0.41 / −0.36 |
| win 2 | 70 / 0.64 / −0.34 | 36 / 0.84 / −0.01 | 70 / 0.70 / −0.26 | 38 / 0.94 / +0.09 |
| **win 3 (SB)** | 70 / 0.86 / −0.07 | 36 / 1.17 / +0.06 | 73 / 0.87 / −0.07 | 36 / 1.15 / +0.07 |
| win 4 | 61 / 1.46 / +0.36 | 31 / 1.06 / −0.27 | 64 / 1.47 / +0.35 | 31 / 1.10 / −0.25 |
| win 5 | 83 / 0.75 / −0.24 | 40 / 0.60 / −0.21 | 83 / 0.86 / −0.10 | 40 / 0.62 / −0.18 |
| win 6 | 77 / 0.72 / −0.24 | 36 / 1.39 / −0.05 | 79 / 1.02 / +0.00 | 37 / 1.38 / −0.05 |
| win 7 | 93 / 1.02 / +0.04 | 38 / 1.50 / −0.02 | 97 / 1.26 / +0.24 | 39 / 1.48 / −0.02 |
| win 8 | 69 / 1.00 / −0.01 | 24 / 1.77 / +0.15 | 71 / 1.06 / +0.06 | 25 / 1.99 / +0.60 |
| win 9 | 61 / 0.49 / −0.30 | 29 / 1.89 / +0.36 | 63 / 0.54 / −0.25 | 29 / 1.92 / +0.37 |
| **win 10 (SB)** | 41 / 2.30 / +0.59 | 19 / 1.00 / +0.02 | 42 / 2.42 / +0.68 | 19 / 1.03 / +0.04 |
| win 11 | 63 / 1.07 / −0.06 | 23 / 2.37 / +0.47 | 64 / 1.28 / +0.04 | 24 / 2.33 / +0.43 |
| win 12 | 68 / 0.85 / −0.13 | 27 / 0.84 / +0.22 | 69 / 0.94 / −0.06 | 28 / 0.84 / +0.19 |
| win 13 | 80 / 0.89 / −0.05 | 27 / 3.02 / +0.59 | 82 / 1.21 / +0.20 | 28 / 3.54 / +0.82 |
| **win 14 (SB)** | 77 / 1.02 / +0.09 | 29 / 0.69 / −0.09 | 77 / 1.23 / +0.23 | 29 / 0.73 / −0.04 |
| win 15 | 42 / 0.65 / −0.40 | 18 / 0.95 / +0.20 | 44 / 0.84 / −0.24 | 19 / 1.08 / +0.30 |
| **win 10,14 (finalist)** | 118 / 1.63 / +0.27 | 48 / 0.86 / −0.05 | 119 / 1.82 / +0.39 | 48 / 0.89 / −0.01 |
| win 8,12 | 137 / 0.94 / −0.07 | 51 / 1.33 / +0.19 | 140 / 1.01 / −0.00 | 53 / 1.43 / +0.38 |
| win 9,13 | 141 / 0.66 / −0.16 | 56 / 2.17 / +0.47 | 145 / 0.81 / +0.01 | 57 / 2.31 / +0.59 |
| win 11,15 | 105 / 0.96 / −0.20 | 41 / 1.61 / +0.35 | 108 / 1.17 / −0.07 | 43 / 1.68 / +0.37 |
| e 0.0 (gap edge) | 130 / 1.72 / +0.29 | 56 / 0.91 / +0.17 | 134 / 1.91 / +0.38 | 58 / 0.92 / +0.17 |
| e 0.25 | 124 / 1.62 / +0.28 | 51 / 0.85 / +0.04 | 129 / 1.83 / +0.42 | 52 / 0.89 / +0.07 |
| e 0.75 | 108 / 1.59 / +0.20 | 43 / 0.75 / −0.03 | 115 / 1.91 / +0.45 | 44 / 0.77 / −0.01 |
| e 1.0 (deep edge) | 98 / 1.43 / +0.10 | 42 / 0.62 / −0.06 | 103 / 1.76 / +0.35 | 42 / 0.64 / −0.02 |
| liq both | 126 / 1.49 / +0.25 | 49 / 0.90 / −0.03 | 127 / 1.65 / +0.37 | 49 / 0.94 / +0.01 |
| liq sess | 13 / 0.55 / −0.21 | 4 / 3.27 / +0.06 | 13 / 0.58 / −0.17 | 4 / 3.34 / +0.08 |

**Reading the table (MEASURED; the interpretation is OPINION).**
- On TRAIN, the 10:00 hour is the best of all 15. The window was chosen on TRAIN, so this is expected.
- On VALID the ranking is unrelated to the ICT hours. Placebo hours 8, 9, 11 and 13 all beat hours 10 and 14.
- The finalist's TRAIN PF is 1.63. Its placebo pairs, with similar trade counts (105-141 against 118), have TRAIN PF 0.66-0.96. That gap is the selection effect of choosing the window on TRAIN.

**Monthly table** (TRAIN+VALID, `lf_base`, `results/silver_bullet_monthly.csv`):

| Month | n | sum R | avg R | sum $/oz | PF | avg risk |
|---|---|---|---|---|---|---|
| 2025-02 | 8 | +7.64 | +0.955 | +27.71 | 4.35 | 3.17 |
| 2025-03 | 12 | +1.44 | +0.120 | +2.51 | 1.12 | 2.93 |
| 2025-04 | 9 | −1.73 | −0.193 | −18.76 | 0.52 | 7.77 |
| 2025-05 | 11 | +18.32 | +1.666 | +85.24 | 9.84 | 4.29 |
| 2025-06 | 8 | +2.03 | +0.254 | +4.12 | 1.26 | 3.93 |
| 2025-07 | 11 | +0.74 | +0.068 | −1.37 | 0.92 | 2.39 |
| 2025-08 | 14 | +3.42 | +0.244 | +5.71 | 1.36 | 2.53 |
| 2025-09 | 13 | −9.81 | −0.755 | −28.74 | 0.08 | 3.08 |
| 2025-10 | 11 | +4.97 | +0.452 | +44.63 | 3.12 | 7.23 |
| 2025-11 | 8 | −5.80 | −0.725 | −24.56 | 0.43 | 6.84 |
| 2025-12 | 13 | +10.16 | +0.782 | +79.08 | 2.38 | 6.42 |
| 2026-01 | 8 | +2.84 | +0.356 | +4.56 | 1.10 | 9.24 |
| 2026-02 | 13 | +0.70 | +0.054 | −4.47 | 0.96 | 15.20 |
| 2026-03 | 6 | −2.71 | −0.452 | −17.04 | 0.70 | 22.14 |
| 2026-04 | 12 | +1.00 | +0.083 | +0.38 | 1.01 | 7.33 |
| 2026-05 | 9 | −4.01 | −0.446 | −29.74 | 0.51 | 9.31 |

TRAIN depends on two months: 2025-05 and 2025-12 together contribute +28.5R of the 31.4R total.

## 4. Pass-bar checklist (finalist, MEASURED)

| Criterion (SYNTHESIS §5 / task) | Required | Finalist | OK |
|---|---|---|---|
| n TRAIN / VALID | ≥ 150 / ≥ 60 | 118 / 48 | no |
| lf_base PF, TRAIN / VALID | ≥ 1.15 / ≥ 1.15 | 1.63 / 0.86 | no |
| lf_base avg, TRAIN / VALID | ≥ +0.15 / ≥ +0.15 | +1.49 / −0.97 | no |
| lf_harsh VALID PF | ≥ 1.0 | 0.59 | no |
| TRAIN halves PF > 1 | both | 1.89 / 1.45 | yes |
| Random direction VALID | p ≤ 0.05 | p = 0.92 (50 seeds) | no |
| Placebo hours beaten (SYNTHESIS kill rule) | — | no, VALID below the placebo median | no |
| Near: VALID PF ≥ 1.05 and TRAIN PF ≥ 1.10, n ok | — | 0.86, n too low | no |

## 5. What this means (OPINION)

- The Silver Bullet recipe (sweep, displacement FVG, midpoint limit, fixed NY hours) produced a TRAIN edge only after choosing among 120 + 144 configs. None of the specific ingredients survives VALID:
  - hour: placebo hours are as good or better;
  - depth: the midpoint is not special;
  - direction: the random-direction and mirror nulls do better.
- ICT lore does not explain gold's 2026 behaviour on M1. Stop sizes near 12 $/oz also make it unusable for the $13 flip.
- Do not carry H11 to TEST.
- If anything is kept, only one mechanically generic observation from this family is worth retesting inside another hypothesis: shallow limit entries (e = 0) beat deep ones. It is not an edge on its own.

## 6. Engine and code notes

- **`features.window_range` look-ahead with the NY-17:00 trading-day key (engine bug, MEASURED).**
  - With `day_key = tday`, bars at 21:00/22:00-23:59 UTC already belong to the next trading day and have `mod >= end`. They are therefore marked "after the window" and receive that day's Asia (00:00-06:59 UTC) or London range **before it happens**.
  - That affects 29,014 TRAIN bars for the Asia window.
  - SYNTHESIS H1 recommends exactly this call, and `cand/sweep_reclaim.py` uses it. It leaks only if that module reads the values at 21:00-23:59 UTC; its LON/NY entry windows suggest it does not, but that should be checked.
  - `silver_bullet.py` masks the ranges to 07:00 / 12:00-19:59 UTC.
  - Suggested fix (not applied, since `lib/` is read-only for me): expose the range only at bars whose timestamp is after the window's last bar within the same day key, for example via a per-group cumulative "window finished" flag.
- **Earlier attempt's bug (fixed here, MEASURED).** `_sweeps()` stored the raw equal-level pair and the 4-tuple result under the same cache key `('eq', δ)`. One stage-1 config crashed with "not enough values to unpack", and results depended on config order within a worker.
  - Fixed with distinct keys.
  - Equal pivots must now also be distinct swings (> k bars apart).
  - After the fix, the win '10' rows of stage 1 are identical to the earlier run. Rows that use equal levels in wider windows moved slightly, for example `sb / opp / both / δ 0.25` went from n 286, PF 1.015 to n 283, PF 1.022.
- **`nulltest.random_direction` with limit orders (property, not a bug).** The limit price is mirrored around the decision-time mid. The null order therefore rests on the other side of price and is no longer at a structure level. In VALID's trend this null had a positive mean (+2.33 $/oz), which makes it a demanding benchmark.
- **R versus $ (caution).** `sweep` ranks by t of R, while PF is in $/oz. With stops from 1.5 to 20+ $/oz across regimes, the two can disagree in sign (lf_harsh TRAIN: PF 1.30 with avg R −0.08).
- **HANDOFF.** I did not update `HANDOFF.md`, because the task forbids writing outside `xau_alpha/`. The orchestrator should record the `window_range` finding there.

## 7. Reproduce

```bash
cd /Users/mac/Desktop/TBT-Engine/xau_alpha
python3 lib/sweep.py silver_bullet --jobs 2                                    # stage 1 (module GRID, 120)
python3 lib/sweep.py silver_bullet --jobs 2 --grid "$(python3 -c 'import sys;sys.path[:0]=["lib","cand"];import json,silver_bullet as s;print(json.dumps(s.GRID_S2))')"   # stage 2 (144)
python3 cand/rnsb_eval.py silver_bullet '{"win": "10,14", "g": 0.5, "tp": "opp", "liq": "eq", "delta": 0.25, "sb_stop": 0.5, "tmax": 120, "look": 30, "news": "30"}' --seeds 50 --placebo '[{"win":"1"},{"win":"2"},{"win":"3"},{"win":"4"},{"win":"5"},{"win":"6"},{"win":"7"},{"win":"8"},{"win":"9"},{"win":"10"},{"win":"11"},{"win":"12"},{"win":"13"},{"win":"14"},{"win":"15"},{"win":"8,12"},{"win":"9,13"},{"win":"11,15"},{"e":0.0},{"e":0.25},{"e":0.75},{"e":1.0},{"liq":"both"},{"liq":"sess"}]'
```

Compute used: stage 1 and stage 2 took about 2 min each (2 workers, including the VALID check of the top 15), and the finalist evaluation took 38 s.
