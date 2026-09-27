# H1: sweep and reclaim of session extremes (`cand/sweep_reclaim.py`)

Date 2026-09-30. TRAIN 2025-01-21..2025-12-31, VALID 2026-01..05. TEST (>= 2026-06-01) never simulated.
Labels: **MEASURED** = computed in this run; **SOURCED** = from `recon/SYNTHESIS.md`; **OPINION** = mine.

## Verdict: FAIL (clean "no edge")

- **MEASURED.** 306 configs were searched (162 in stage 1, 144 in stage 2).
  - Not one has a TRAIN profit factor above 1.0 at `lf_base` once it has at least 60 TRAIN trades.
  - Not one stage-2 config has a TRAIN PF above 1.0 at all. The best is 0.997.
- **MEASURED.** The finalist was chosen by the pre-set rule (best TRAIN t-stat in stage 2):
  - TRAIN PF 0.967, n 76;
  - VALID PF 0.702, n 33;
  - random-direction null p = 0.82 on VALID.
- **MEASURED.** At zero cost (`mid`) the finalist makes TRAIN PF 1.09 and VALID PF 0.75.
- **MEASURED.** An event study that ignores exits finds no drift in the fade direction at 5, 15, 30 or 60 minutes (|t| ≤ 1.1 on TRAIN).
- **MEASURED.** The continuation twin (trading with the sweep) drifts **+0.29..+0.74 A on TRAIN** at 15-30 minutes (t 1.2-2.1). **On VALID the sign flips: −0.5..−1.05 A (t −1.2..−2.8).**
  - This repeats HANDOFF's prior-day-break finding (continuation in TRAIN, sign flip in VALID; SOURCED SYNTHESIS H1 "Why") across all the session levels.
  - The level "sweep" is a regime-dependent coin. It is not a stable fade signal.
- **MEASURED.** The pass bar's n ≥ 150 TRAIN is reachable by only 1 of 162 stage-1 configs. The rule as specified fires 0.7-1.4 times per day at its loosest, against the 1-3 per day SYNTHESIS expected.
- **MEASURED.** Flip phase: in VALID, only 6-8% of trades have a stop within [1.2, 4.0] $/oz, which is 2-6 trades in 5 months. The median stop is $3.8 on TRAIN and $7.8-9.2 on VALID (volatility doubled). The flip constraint alone makes this setup unusable in the 2026 regime.

## 1. Rules as implemented

The long side is the exact mirror of the short side described here, with identical parameters and no directional veto.

**Units.** A = Wilder ATR14 of M1 mid (`data.atr`). A5 = Wilder ATR14 of causal M5 bars (`data.resample_causal`), taken from the last complete M5 bar.

**Levels (causal).**

| Set | Window | Level | Live from |
|---|---|---|---|
| `sess` | LON | Asia range 00:00-06:59 UTC (`window_range(mod, tday, h, l, 0, 420)`) | 07:00 UTC |
| `sess` | NY | London range 07:00-11:59 UTC (`window_range(..., 420, 720)`) | 12:00 UTC |
| `pd` | both | Prior trading day's high/low (`prev_day_hl(tday, h, l)`) | Start of the trading day (NY 17:00 roll) |
| `both` | | `sess` + `pd` | |

**Windows** (the sweep bar must be inside the window):
- LON = 08:00-11:30 London local (`lon_mod`);
- NY = 08:30-11:30 ET (`ny_mod`);
- `both` = either.

**Steps (short side).**
1. **Sweep:** the first bar i with h[i] ≥ L + δ·A5[i] since the level went live.
   - If that first sweep falls before the window, the level is used up for the day. This is my reading of "not swept yet today" (OPINION).
   - Each level trades at most once per day. A `pd` level is shared by both windows, so it is used at most once.
2. **Reclaim:** the first close c[j] ≤ L − ε·A[j] with i ≤ j ≤ i+N, on the same trading day. The reclaim may fall after the window's end.
3. **Trigger:**
   - `trig=0`: none.
   - `trig=1`: TRIG_BEAR(j, L) **on the reclaim bar itself**. That is a bearish engulfing bar, or a bearish pin (upper wick ≥ 0.5 × range, h ≥ L, c < L).
   - If bar j is not a trigger, the level is dropped for the day.
4. **Entry:** market order with t = ts[j] + 60 s.
5. **Stop:** max(h[i..j]) + σ·A[j], as an absolute price.
6. **Target:** `tp='mid'` uses the reference range's midpoint. `tp=k` uses c[j] − k·(stop − c[j]).
7. **Exits:** time stop `tmax`. Also flat at 16:45 ET (never binds).
8. **News:** entry is blocked if the decision time falls within [T−30, T+30] min of a HIGH row (`news='30'`, SYNTHESIS N2 for equity below $21). This applies to every selection run. Sensitivity to the window is in §5.

**Parameters not specified in SYNTHESIS, fixed by me before the sweep (OPINION):**
- ε = 0 in stage 1;
- the trigger must be on the reclaim bar;
- w_pin = 0.5;
- the NY window uses the London range as its session level (not Asia);
- a news blackout on entry only.

## 2. Grid stages and configs tried

| Stage | Grid | Configs |
|---|---|---|
| 1 | levels {sess, pd, both} × δ {0.25, 0.5, 1.0} × N {3, 10, 20} × trig {0, 1} × win {lon, ny, both}; σ 0.3, TP 1.5R, tmax 60, ε 0 | 162 |
| 2 | 6 stage-1 configs × ε {0, 0.2} × σ {0.2, 0.5} × TP {mid, 1.5R, 2.5R} × tmax {30, 90} | 144 |
| **Total searched** | | **306** |

**Other simulations** (null tests and sensitivity on 2 configs; never used for selection):
- 3 costs;
- 2 flip-only runs;
- 3 news windows;
- continuation twin ×2 and mirror ×2;
- 20 placebo seeds × 2 costs;
- 2 × 50 random-direction seeds.

**Disclosure.** Before stage 1, a smoke test simulated one stage-1 config and printed its VALID row: `both/0.5/10/0/both`, VALID PF 1.36, n 48. It played no part in selection.

**Stage-2 selection rule** (set before stage 2 ran; documented in the module):
- The raw top 5 by TRAIN t had n = 2..36 (the harness ranks by t with no n floor), and no config had TRAIN n ≥ 150 with PF > 1.
- So s1 = 0..4 are the top 5 by TRAIN t among configs with TRAIN n ≥ 60, the harness's own `min_n`.
- s1 = 5 is the only config with TRAIN n ≥ 150, added so that at least one family could meet the pass bar's n.

**Stage 1 (MEASURED, `lf_base`, TRAIN):**
- Only 21 of 162 configs reach n ≥ 60, and their best PF is 0.988.
- 33 configs have PF > 1, all with n ≤ 48.
- 92 configs have n < 20.
- The trigger arm hurts: its median n is 5 (32 without it), and its median t is −3.35 (−0.52 without it; configs with n ≥ 20).

Stage-1 TRAIN medians (configs with n ≥ 20):

| | PF | t |
|---|---|---|
| δ 0.25 | 0.63 | −1.78 |
| δ 0.5 | 0.83 | +0.31 |
| δ 1.0 | 0.60 | −1.76 |
| win lon | 0.58 | −2.3 |
| win ny | 0.74 | −0.24 |
| win both | 0.68 | −1.26 |

Why n is so small (MEASURED, over 437 trading days incl. TEST days, no P&L read):
- The London level is already swept between 12:00 UTC and the NY window start on 622 of 872 level-days.
- The Asia level is swept before the LON window on 272 of 872.
- The prior-day level is never reached in the window on about 55-66% of days.
- Many in-window sweeps never reclaim: at δ 0.5, 32-42% within N = 20 bars and 56-69% within N = 3.

Stage-2 TRAIN top 6 (`lf_base`; full table in `results/sweep_reclaim_train.csv`):

| s1 | ε | σ | TP | tmax | n | PF | avg pts | avg R | t | PF h1 / h2 |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 0 | 0.2 | 1.5R | 90 | 76 | 0.967 | −0.08 | +0.12 | 0.90 | 0.85 / 1.11 |
| 0 | 0 | 0.2 | 2.5R | 90 | 76 | 0.997 | −0.01 | +0.15 | 0.86 | 0.86 / 1.16 |
| 1 | 0 | 0.2 | mid | 90 | 62 | 0.905 | −0.28 | +0.18 | 0.81 | 1.18 / 0.69 |
| 1 | 0 | 0.5 | mid | 90 | 62 | 0.837 | −0.52 | +0.12 | 0.62 | 1.14 / 0.62 |
| 1 | 0 | 0.2 | 2.5R | 90 | 62 | 0.866 | −0.41 | +0.10 | 0.53 | 1.15 / 0.63 |
| 0 | 0.2 | 0.2 | 2.5R | 90 | 68 | 0.910 | −0.26 | +0.09 | 0.51 | 0.97 / 0.85 |

Stage 2 overall:
- Best PF 0.997, median PF 0.689.
- Among the 12 configs with n ≥ 150 (s1 = 5), the best PF is 0.742.
- **Why avg R > 0 while pts < 0:** the winners come from the small-risk trades, and PF is computed on points.

## 3. Finalist (best TRAIN t in stage 2)

`{"levels":"sess","delta":0.5,"N":10,"trig":0,"win":"both","eps":0.0,"sigma":0.2,"tp":1.5,"tmax":90}`. News defaults to '30'.

| Cost | Split | n | WR | PF | avg pts | avg R | sum R | avg risk |
|---|---|---|---|---|---|---|---|---|
| lf_base | TRAIN | 76 | 0.50 | 0.967 | −0.08 | +0.121 | +9.2 | 4.37 |
| lf_base | VALID | 33 | 0.39 | **0.702** | −1.54 | −0.060 | −2.0 | 8.47 |
| lf_harsh | TRAIN | 76 | 0.46 | 0.690 | −0.90 | −0.117 | −8.9 | 4.71 |
| lf_harsh | VALID | 33 | 0.39 | 0.614 | −2.16 | −0.143 | −4.7 | 8.85 |
| mid (gross) | TRAIN | 76 | 0.50 | 1.086 | +0.20 | +0.212 | +16.1 | 4.21 |
| mid (gross) | VALID | 33 | 0.39 | 0.752 | −1.23 | −0.014 | −0.5 | 8.30 |

- TRAIN t(R) = +0.90 at base (VALID −0.30).
- TRAIN halves by base PF: 0.851 / 1.107.
- Dropping the best TRAIN month (2025-05) gives PF 0.83.
- Exits, TRAIN: 37 stop / 33 target / 6 time.

**Long vs short** (`lf_base`):

| | TRAIN n | TRAIN PF | TRAIN avg pts | VALID n | VALID PF | VALID avg pts |
|---|---|---|---|---|---|---|
| Long (reclaim of a low) | 32 | 0.868 | −0.36 | 16 | 0.809 | −0.92 |
| Short (reclaim of a high) | 44 | 1.055 | +0.12 | 17 | 0.614 | −2.11 |

**By level** (`lf_base`, TRAIN → VALID PF):

| Level | TRAIN PF (n) | VALID PF (n) |
|---|---|---|
| asiaH-lon | 0.96 (28) | 0.92 (10) |
| asiaL-lon | 0.53 (20) | 1.40 (10) |
| londonH-ny | 1.19 (16) | 0.41 (7) |
| londonL-ny | 1.28 (12) | 0.43 (6) |

No level is consistent from TRAIN to VALID.

**Flip-eligible subset** (stop in [1.2, 4.0], from `sweep.evaluate`):

| | Share of trades | TRAIN (n, PF, avg pts) | VALID (n, PF, avg pts) |
|---|---|---|---|
| lf_base | TRAIN 55%, VALID 6% | 42, 1.285, +0.42 | **2**, 1.29, +0.54 |
| lf_harsh | | 36, 0.677, −0.63 | 2, 1.00, 0.01 |
| mid | | 43, 1.409, +0.57 | 3, 0.69, −0.75 |

Flip-only variant (orders filtered to the cap before simulation):
- `lf_base`: TRAIN 43 trades, PF 1.20; VALID 3 trades, PF 0.61.
- `lf_harsh`: TRAIN PF 0.79.

Stop-distance quantiles (p10 / p25 / p50 / p75 / p90):
- TRAIN: 2.1 / 2.8 / 3.8 / 5.2 / 8.0;
- VALID: 4.6 / 6.4 / 7.8 / 11.5 / 13.7.

**News-window sensitivity** (`lf_base` PF, TRAIN / VALID):

| Window | PF TRAIN / VALID |
|---|---|
| off | 0.817 / 0.580 |
| [−2, +5] | 0.934 / 0.615 |
| [−15, +30] | 0.967 / 0.648 |
| [−30, +30] (selected) | 0.967 / 0.702 |

The blackout helps slightly, but it does not create an edge.

## 4. Null tests (finalist, and the densest n ≥ 150 config)

"Dense" is `{"levels":"both","delta":0.25,"N":20,"trig":0,"win":"both","eps":0,"sigma":0.5,"tp":"mid","tmax":30}`, the best TRAIN t among the n ≥ 150 configs.

| Test | Finalist | Dense |
|---|---|---|
| Real, lf_base TRAIN / VALID PF | 0.967 / 0.702 | 0.742 / 1.321 |
| Random direction, 50 seeds, VALID: p(avg pts ≥ real) | **0.82** (null PF median 0.96) | **0.31** (null PF median 1.11, p95 1.90) |
| Random direction, 50 seeds, TRAIN: p | 0.57 | 0.82 |
| Placebo levels (20 draws), TRAIN: real − placebo avg R, in SE of real | +2.4 SE (real +0.12 vs −0.20; placebo n 28 vs 76) | **+0.6 SE** (within 1 SE, so the kill rule applies) |
| Placebo levels, VALID | **−1.2 SE** (placebo better than real) | +1.8 SE |
| Continuation twin, lf_base TRAIN / VALID PF (n) | 0.885 (141) / 1.108 (48) | 1.109 (178) / 0.485 (64) |
| Continuation twin, mid TRAIN / VALID PF | 1.028 / 1.167 | 1.299 / 0.517 |
| Mirror (same times, reversed), lf_base TRAIN / VALID PF | 1.027 / 1.273 | 1.007 / 0.827 |

**Exit-free event study** (signed mid move from c[j], in A units, all signals, no one-at-a-time filter; MEASURED):

| Arm | TRAIN h5 / h15 / h30 / h60 (t) | VALID h5 / h15 / h30 / h60 (t) |
|---|---|---|
| Fade, finalist | −0.05 / −0.05 / −0.25 / −0.28 (t ≥ −0.63) | +0.16 / +0.07 / −0.32 / −0.48 |
| Continuation, finalist | +0.08 / +0.39 / **+0.74 (t 2.09)** / +0.42 | −0.37 / **−1.05 (t −2.83)** / −0.85 / −0.70 |
| Fade, dense | −0.13 / −0.09 / −0.03 / −0.11 | −0.02 / +0.13 / +0.25 / +0.02 |
| Continuation, dense | +0.11 / +0.29 / +0.36 / −0.10 | −0.12 / −0.53 / **−0.94 (t −2.07)** / −0.88 |

**Reading of the null tests (OPINION):**
- The fade never beats random direction.
- The placebo test is mixed and not matched on n: shifted levels are usually consumed before the window.
- The only nominally significant effects are in the continuation arm, and they have opposite signs in TRAIN and VALID.
- The dense config's VALID PF 1.32 comes from a single month, 2026-05: +142 pts and +22.8R out of a VALID total of +21.0R. Without that month, VALID PF is 0.78. Its VALID first half has PF 0.82, and it is long-only in effect (VALID long PF 1.76, short 0.82).

## 5. Monthly R, finalist, lf_base (TRAIN 2025-01..12, VALID 2026-01..05)

| Month | n | sum R | avg R | sum pts | WR | PF |
|---|---|---|---|---|---|---|
| 2025-01 | 2 | +2.68 | +1.34 | +8.0 | 1.00 | – |
| 2025-02 | 5 | −5.25 | −1.05 | −17.4 | 0.00 | – |
| 2025-03 | 6 | +0.58 | +0.10 | +6.0 | 0.50 | 1.51 |
| 2025-04 | 8 | −5.03 | −0.63 | −34.2 | 0.13 | 0.16 |
| 2025-05 | 10 | +5.53 | +0.55 | +23.2 | 0.70 | 2.68 |
| 2025-06 | 10 | +1.85 | +0.19 | +2.4 | 0.60 | 1.11 |
| 2025-07 | 11 | +7.54 | +0.69 | +22.6 | 0.73 | 3.32 |
| 2025-08 | 6 | +0.79 | +0.13 | +5.4 | 0.50 | 1.66 |
| 2025-09 | 5 | −0.38 | −0.08 | −1.6 | 0.40 | 0.87 |
| 2025-10 | 6 | −1.38 | −0.23 | −20.7 | 0.33 | 0.41 |
| 2025-11 | 2 | +0.34 | +0.17 | −2.6 | 0.50 | 0.59 |
| 2025-12 | 5 | +1.90 | +0.38 | +2.7 | 0.60 | 1.34 |
| 2026-01 | 2 | −2.04 | −1.02 | −17.4 | 0.00 | – |
| 2026-02 | 1 | +1.37 | +1.37 | +4.8 | 1.00 | – |
| 2026-03 | 4 | +1.59 | +0.40 | +6.6 | 0.50 | 1.72 |
| 2026-04 | 10 | −2.99 | −0.30 | −28.4 | 0.30 | 0.58 |
| 2026-05 | 16 | +0.10 | +0.01 | −16.3 | 0.44 | 0.79 |

## 6. Pass-bar checklist (finalist, `lf_base` unless noted)

| Criterion | Required | Result | |
|---|---|---|---|
| n TRAIN / VALID | ≥ 150 / ≥ 60 | 76 / 33 | FAIL |
| PF TRAIN / VALID | ≥ 1.15 | 0.967 / 0.702 | FAIL |
| avg pts TRAIN / VALID | ≥ +0.15 | −0.08 / −1.54 | FAIL |
| VALID PF at lf_harsh | ≥ 1.0 | 0.614 | FAIL |
| Both TRAIN halves PF > 1 | yes | 0.85 / 1.11 | FAIL |
| Random direction p on VALID | ≤ 0.05 | 0.82 | FAIL |
| "Near" (VALID PF ≥ 1.05 and TRAIN PF ≥ 1.10, n ok) | | No config in 306 has TRAIN PF ≥ 1.10 with n ≥ 60 | FAIL |

## 7. Engine observations (no `lib/` file was modified)

1. **`sweep.py` ranks TRAIN results by the t-stat of R with no minimum n.**
   - Configs with n = 2-3 get t = 58-3,507 (near-zero R std) and head the list. The same configs get validated as the "top 15".
   - `train_score(min_n=60)` exists but is not used.
   - Suggest ranking by t with n ≥ `min_n`.
2. **`sweep.py` always writes `<name>_train.csv` / `<name>_valid.csv`**, so a second stage overwrites the first. I renamed stage 1 to `sweep_reclaim_s1_*.csv`. A `--tag` option would help.
3. **`nulltest.random_direction` and `mirror` simulate every order they are given, including TEST-period orders.** The caller must filter `t < TEST_MS` first; `cand/sweep_reclaim_analysis.py` does. This is a holdout hazard for other candidates.
4. **Pass-bar metrics mix units.** PF and avg are on points, while the sweep's t-stat is on R. For fades with variable stops they can disagree in sign (e.g. the finalist's TRAIN avg R is +0.12 while its avg pts is −0.08). Selection by R t-stat can therefore favour configs whose points P&L is negative.
5. **`data.load_m1()` takes about 20 s per process** (the `tday` strftime), so each sweep worker pays it. This is a performance note only.
6. **`features.prev_day_hl` uses the previous `tday` present in the data.** After short holiday sessions the "prior day" range can be tiny. This is minor and unchanged.

## 8. Files

- **Module:** `cand/sweep_reclaim.py`. It holds `GRID` (stage 1), `GRID2` / `STAGE1_TOP` (stage 2), `orders()`, and the null switches `mode='cont'`, `placebo_seed`, `flip`.
- **Analysis:** `cand/sweep_reclaim_analysis.py`, a single process that runs on TRAIN+VALID only.
- **Results:**
  - `cand/results/sweep_reclaim_s1_train.csv` and `sweep_reclaim_s1_valid.csv` (stage 1);
  - `sweep_reclaim_train.csv` and `sweep_reclaim_valid.csv` (stage 2);
  - `sweep_reclaim_analysis_finalist.json` and `sweep_reclaim_analysis_dense.json`.

**Reproduce:**
```
cd xau_alpha
python3 lib/sweep.py sweep_reclaim --jobs 2
python3 lib/sweep.py sweep_reclaim --jobs 2 --grid '{"s1":[0,1,2,3,4,5],"eps":[0.0,0.2],"sigma":[0.2,0.5],"tp":["mid",1.5,2.5],"tmax":[30,90]}'
python3 cand/sweep_reclaim_analysis.py '<params>'
```
The first sweep must be renamed to `_s1` before the second one runs.

**Not done:**
- No TEST run: there is no finalist to run it on.
- No H4 gate: a filter cannot rescue a zero gross edge (OPINION).
- `HANDOFF.md` is not updated, because this task forbids writes outside `xau_alpha/`. The orchestrator should carry this finding into `HANDOFF.md`.
