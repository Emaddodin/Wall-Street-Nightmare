# X2: higher-timeframe trend following, both directions (`cand/htf_breakout.py`, `cand/trend_pullback.py`)

Date: 2026-09-30. New hypothesis X2, a main-strategy candidate for equity of $100 or more (not the $13 flip).
This file covers both modules. `htf_breakout` is the better one and is the family's returned result.
`trend_pullback` is described in section 8.

Labels:
- **MEASURED**: computed here on TRAIN/VALID only.
- **SOURCED**: taken from a cited file.
- **OPINION**: my judgement.

Holdout: TEST (from 2026-06-01) was never simulated.
- `cand/htf_base.py` truncates the M1 frame at 2026-06-01 before computing any feature.
- Every order carries `flat <= 2026-05-31 23:59 UTC`.
- `sweep.run_orders` also drops t ≥ TEST.
- `final=True` was never used. The video trades dated 2026-09-21/22 were not read.

---

## 0. Verdict

**htf_breakout: formally "near". OPINION: not a tradeable edge.**

The mechanical rule gives "near" for the family's top TRAIN config with n ≥ 150: H1, N = 55, sl = 1.5·ATR_H1, 2R target, multi-day hold. Its lf_base PF is 1.54 on TRAIN and 1.07 on VALID, with n = 164 and 70. Everything else argues against it:

- **The TRAIN edge is long exposure in the 2025 bull market (MEASURED).**
  - Longs: PF 2.28, +9.8 $/oz per trade. Shorts: PF 0.66, −5.7 $/oz.
  - A permutation null that keeps the long/short mix and shuffles which trade is long gives **p = 0.25** on TRAIN (0.12 for the eod finalist).
  - The p = 0.02 against random 50/50 direction only reflects that the rule is long-biased (73% longs) in a rising market.
- **The VALID result cannot be told apart from noise (MEASURED).**
  - Random-direction null: p = 0.27. Permutation null: p = 0.41. R t-stat = 0.74.
  - Dropping the best VALID month gives PF 0.84.
  - VALID PF of the top-10 TRAIN configs (lf_base): median about 0.99, with 4 of 10 at or above 1.05.
  - My pre-declared primary finalist is the intraday variant (hold = eod, which carries no swap). It **fails**: VALID PF 0.99 and avg −0.19 $/oz.
- **What looks like real trend-following (MEASURED, weak):**
  - In VALID the shorts made money: PF 1.27, +9.0 $/oz, n = 30, almost all in the March 2026 crash (+11R).
  - Every top-10 TRAIN config has a positive VALID average in R: +0.06 to +0.38R per trade in the table, and R-space PF about 1.2 for both finalists. The negative dollar averages come from a few large-stop trades in the Jan-Feb 2026 volatility spike.
  - With fixed-fractional sizing, R is what counts. Still, no VALID R t-stat exceeds 0.8.
- **None of it is flip-eligible.** Stops are $10-77/oz (median $16 on TRAIN, $36 on VALID), so 0% of trades fall in [1.2, 4.0].

**trend_pullback: FAIL.**
- MEASURED: TRAIN has a small gross edge that beats both nulls (mid PF 1.24; p = 0.02 random and 0.02 permutation). Long and short are both positive at mid, so it is not beta.
- Cost eats it: lf_base PF 1.08 and lf_harsh PF 0.81 on TRAIN.
- VALID loses even gross: mid PF 0.81, lf_base 0.75.

Configs tried:
- htf_breakout: **256 unique** (272 evaluations over 2 stages).
- trend_pullback: **216 unique** (224 evaluations).
- Total: 472.
- VALID was looked at for 16 distinct htf_breakout configs (2 finalists, their neighbours, the top-10 table) and 10 trend_pullback configs. Details are in section 3.

---

## 1. htf_breakout rules

Code: `cand/htf_breakout.py`. Shared causal blocks are in `cand/htf_base.py`. Longs and shorts are exact mirrors with identical parameters and no directional veto.

| item | rule |
|---|---|
| bars | `tf`-minute mid bars (60 = H1, 240 = H4), UTC-aligned, from `data.resample_causal`. HTF bar k is known at the close of its last M1 bar `ki[k]`. The decision time is t = ts[ki[k]] + 60 s. |
| signal | Long if C[k] > max(H[k−N..k−1]); short if C[k] < min(L[k−N..k−1]). This is a Donchian close-breakout. `fresh=0` accepts every close beyond the channel; `fresh=1` accepts only the first. |
| gates | No entries 20:30-23:30 UTC, on Friday after 19:00 UTC, at the Sunday reopen, or within [−5, +15] min of a HIGH calendar row (the SYNTHESIS N2 window for equity ≥ $21). With `hold=eod`, also no entries from 15:00 to 17:00 ET. |
| entry | Market order at t. One position at a time (simulator). |
| initial stop | sl × ATR14(HTF) at the signal bar, measured from the fill. |
| exit `chX` | Chandelier trail: X × ATR14(HTF) behind the best exit-side price, active from entry, never looser than the initial stop. |
| exit `don` | Turtle exit: flatten at the close of the first later HTF bar that closes beyond the opposite max(5, N/2)-bar channel. |
| exit `rX` | Fixed target at X × the initial stop distance. |
| hold | `eod`: also flat at 16:45 ET of the entry's trading day (intraday, never crosses the swap rollover). `multi`: also flat after 10 calendar days. |

- **Units:** N is in HTF bars; sl and X are ATR(HTF) multiples. There are no dollar thresholds.
- **Swap:** not in `sim`. It is priced separately in section 5.

---

## 2. Grid stages (TRAIN only, lf_base, ranked by TRAIN t)

| stage | configs | content | file |
|---|---|---|---|
| smoke | 3 | 3 grid configs, TRAIN only (timing and sanity) | none |
| 1 | 128 | tf {60, 240} × N {10, 20, 34, 55} × sl {1.5, 3.0} × ex {ch2, ch3.5, don, r2} × hold {eod, multi} | `results/htf_breakout_s1_train.csv` |
| 2 | 144 | Around the stage-1 top 5 (all H1, N 34/55, ch2/r2): tf {60} × N {34, 45, 55} × sl {1.0, 1.5, 3.0} × ex {ch1.5, ch2, ch2.5, r2} × hold {eod, multi} × fresh {0, 1} | `results/htf_breakout_s2_train.csv` |
| merged | **256 unique** | Both stages de-duplicated and sorted by TRAIN t | `results/htf_breakout_train.csv` |
| VALID | 10 configs × 2 costs | The top 10 by TRAIN t with TRAIN n ≥ 150, at lf_base and lf_harsh | `results/htf_breakout_valid.csv` |

- The stage drivers are `cand/x2_stages.py` and `cand/x2_valid_top.py`.
- `sweep.py` was used with `--jobs 2` and `--top 0`, so no VALID look happened during the search.

### Search landscape, all 256 unique configs (MEASURED, TRAIN)

- **255 of the 256 unique configs have TRAIN PF > 1 at lf_base.** This is the signature of a directional market, not of a fragile edge.
- Medians of PF / t / n by timeframe and hold:

  | tf | hold | median PF | median t | median n |
  |---|---|---|---|---|
  | H1 | eod | 1.44 | 1.96 | 184 |
  | H1 | multi | 1.58 | 2.08 | 157 |
  | H4 | eod | 1.35 | 1.25 | 100 |
  | H4 | multi | 1.40 | 1.11 | 48 |

- No H4 config reaches TRAIN n ≥ 150 (max n = 143). The best H4 TRAIN t is 2.11, at n = 39.
- The TRAIN plateau is flat: H1 with N 34-55 gives t 2.5-3.0 whatever the exit.

### Long / short split on TRAIN (MEASURED, `cand/x2_trainsplit.py`, run before any VALID look)

| config (H1) | long n, PF, avg $ | short n, PF, avg $ | beta-adj. long / short avg $ (mid) |
|---|---|---|---|
| N55 sl1.5 r2 multi | 119, 2.28, +9.81 | 45, 0.66, −5.72 | +7.97 / −1.88 |
| N55 sl1.5 ch2 eod | 108, 2.31, +6.76 | 45, 0.93, −0.65 | +5.78 / +0.69 |
| N55 sl3.0 ch2 eod | 105, 2.41, +7.20 | 44, 0.94, −0.54 | +6.22 / +1.10 |
| N34 sl1.5 ch2 eod | 127, 2.23, +6.20 | 69, 0.94, −0.54 | +5.23 / +1.15 |
| N10 sl3.0 don eod | 186, 1.93, +5.94 | 137, 0.81, −2.08 | +4.26 / +0.08 |
| H4 N20 sl3.0 ch2 eod | 77, 2.03, +7.77 | 30, 0.52, −4.56 | +5.85 / −3.25 |

"Beta-adjusted" means the trade's mid P&L minus d × (TRAIN-average drift of 0.00324 $/min) × holding minutes. Longs stay strongly positive after this adjustment. OPINION: that does not rule out beta. Donchian highs select the steepest legs of the bull market, where the local drift is far above the yearly average. The permutation null (section 4) is the right control for that, and it is not significant.

---

## 3. Finalists and selection

- **Primary, pre-declared before any VALID look:**
  - Rule: the best TRAIN t among `hold=eod` configs with n ≥ 150. The reason is that swap is not in the simulator and the search brief is intraday.
  - Result: `{"tf":60,"N":55,"sl":1.5,"ex":"ch2","hold":"eod","fresh":0}` (TRAIN t 2.74).
- **Secondary:**
  - Rule: the family's best TRAIN t with n ≥ 150, which is the harness's own ranking rule.
  - Result: `{"tf":60,"N":55,"sl":1.5,"ex":"r2","hold":"multi","fresh":0}` (TRAIN t 2.82).
- Both were evaluated once on VALID (`cand/x2_eval.py`). The returned "best" is the secondary, because it is the harness-rule pick and the better of the two on VALID.
- **Disclosure:** returning the better of two VALID looks is a mild selection on VALID. The primary fails.

### 3a. Returned config: H1, N 55, sl 1.5·ATR_H1, TP 2R, multi-day (max 10 days), fresh = 0

MEASURED. Units are $/oz per trade (1 oz = 0.01 lot).

| split | cost | n | WR | PF | avg $ | avg R | sum R | maxDD R | avg risk $ |
|---|---|---|---|---|---|---|---|---|---|
| TRAIN | lf_base | 164 | 0.445 | **1.542** | **+5.55** | +0.331 | 54.2 | 9.2 | 18.0 |
| TRAIN | lf_harsh | 165 | 0.424 | 1.379 | +4.11 | +0.255 | 42.0 | 9.5 | 18.0 |
| TRAIN | mid | 163 | 0.460 | 1.625 | +6.25 | +0.383 | 62.4 | 9.0 | 18.1 |
| TRAIN | duka_raw | 164 | 0.433 | 1.469 | +4.88 | +0.300 | 49.2 | 9.0 | 18.0 |
| VALID | lf_base | 70 | 0.386 | **1.073** | **+2.22** | +0.145 | 10.1 | 8.0 | 44.3 |
| VALID | lf_harsh | 70 | 0.386 | **1.063** | +1.93 | +0.137 | 9.6 | 8.1 | 44.3 |
| VALID | mid | 70 | 0.386 | 1.079 | +2.39 | +0.150 | 10.5 | 8.0 | 44.3 |
| VALID | duka_raw | 70 | 0.386 | 1.075 | +2.26 | +0.146 | 10.2 | 8.0 | 44.3 |

Long / short at lf_base:

| split | side | n | PF | avg $ | avg R | sum R |
|---|---|---|---|---|---|---|
| TRAIN | long | 119 | 2.280 | +9.81 | +0.556 | +66.2 |
| TRAIN | short | 45 | 0.664 | −5.72 | −0.266 | −12.0 |
| VALID | long | 40 | 0.897 | −2.86 | +0.077 | +3.1 |
| VALID | short | 30 | 1.266 | +8.99 | +0.236 | +7.1 |

Other diagnostics:
- **Exits:** TRAIN 91 SL / 73 TP; VALID 43 SL / 27 TP.
- **Holding time:** median 7.0 h (p10 0.9 h, p90 38 h on TRAIN; 56 h on VALID).
- **Swap nights:** about 0.6-0.7 per trade.
- **Trade t-stat (R):** TRAIN 2.82, VALID 0.74.
- **TRAIN halves:** PF 1.44 / 1.63.
- **Drop the best month:** TRAIN PF 1.34, VALID PF 0.84.
- **Neighbours:** 8 single-parameter changes (N 45/34, sl 1.0/3.0, ex ch2/ch2.5, hold eod, fresh 1). Their VALID PFs at lf_base are 0.98, 0.96, 0.97, 1.38, 1.20, 1.15, 0.98 and 1.30, with a **median of 1.06**.
- **Flip-eligible (stop 1.2-4.0 $/oz):** n = 0 on TRAIN and VALID (share 0.0). The 10th percentile stop is $10.3 on TRAIN and $26 on VALID.

### 3b. Primary (pre-declared) config: H1, N 55, sl 1.5, chandelier 2·ATR_H1, intraday (flat 16:45 ET)

| split | cost | n | PF | avg $ | avg R | long PF (n) | short PF (n) |
|---|---|---|---|---|---|---|---|
| TRAIN | lf_base | 153 | 1.735 | +4.58 | +0.266 | 2.31 (108) | 0.93 (45) |
| TRAIN | lf_harsh | 156 | 1.511 | +3.37 | +0.177 | 1.95 | 0.85 |
| TRAIN | mid | 153 | 1.829 | +5.01 | +0.294 | 2.45 | 0.97 |
| VALID | lf_base | 68 | **0.990** | **−0.19** | +0.094 | 0.78 (43) | 1.25 (25) |
| VALID | lf_harsh | 68 | 0.922 | −1.57 | +0.051 | 0.70 | 1.20 |
| VALID | mid | 68 | 1.014 | +0.26 | +0.106 | 0.80 | 1.28 |

Other diagnostics:
- **TRAIN t / VALID t:** 2.74 / 0.64.
- **TRAIN halves:** 1.89 / 1.63.
- **Drop the best month:** TRAIN 1.57, VALID 0.81.
- **R-space PF:** TRAIN 1.77, VALID 1.23.
- **Neighbours:** 9 single-parameter changes (N 45/34, sl 1.0/3.0, ex ch1.5/ch2.5/r2, hold multi, fresh 1). Their VALID PFs are 0.91, 0.76, 0.84, 1.12, 0.81, 1.01, 0.98, 1.20 and 1.25, with a **median of 0.975**.
- **Flip-eligible:** 0 trades.
- **Verdict:** fail.

### 3c. Top-10 TRAIN configs (n ≥ 150) on VALID at lf_base (MEASURED, `results/htf_breakout_valid.csv`)

| TRAIN rank | config | TRAIN PF | VALID n | VALID PF | VALID avg $ | VALID avg R | VALID long / short PF |
|---|---|---|---|---|---|---|---|
| 1 | N55 sl1.5 r2 multi | 1.54 | 70 | 1.073 | +2.22 | +0.145 | 0.90 / 1.27 |
| 2 | N55 sl1.5 ch2 eod | 1.74 | 68 | 0.990 | −0.19 | +0.094 | 0.78 / 1.25 |
| 3 | N55 sl1.0 ch2 eod | 1.65 | 72 | 0.836 | −2.94 | +0.101 | 0.80 / 0.88 |
| 4 | N45 sl1.5 ch2 eod | 1.69 | 72 | 0.910 | −1.84 | +0.061 | 0.72 / 1.17 |
| 5 | N45 sl1.5 r2 multi | 1.47 | 75 | 0.979 | −0.67 | +0.108 | 0.83 / 1.15 |
| 6 | N55 sl1.5 r2 eod | 1.50 | 74 | 0.975 | −0.56 | +0.077 | 0.73 / 1.26 |
| 7 | N45 sl1.0 ch2 multi f1 | 1.81 | 59 | 1.218 | +4.11 | +0.379 | 1.46 / 1.01 |
| 8 | N45 sl1.0 ch2 multi | 1.81 | 67 | 0.988 | −0.24 | +0.213 | 1.09 / 0.89 |
| 9 | N55 sl1.0 ch2 eod f1 | 1.64 | 64 | 1.056 | +0.89 | +0.239 | 1.12 / 1.01 |
| 10 | N45 sl3.0 ch2 eod | 1.71 | 69 | 1.095 | +1.76 | +0.056 | 0.79 / 1.58 |

- **Median VALID PF: about 0.99.** Every config has a positive average R.
- In 7 of 10 configs VALID longs have PF < 1, and in 8 of 10 VALID shorts have PF > 1.
- **What happened in VALID:** gold spiked from 4329 to 5593 (2026-01-29), then crashed to about 4100 and ended May at 4540 (MEASURED from `load_m1`). The long breakouts lost in the whipsaw; the short breakouts caught the crash.

---

## 4. Null tests (MEASURED, lf_base, 50 seeds, `cand/x2_eval.py`)

Two nulls use the same entry times and the same exit geometry; only the direction changes.
- **random**: `nulltest` style, each direction drawn at p = 0.5.
- **perm**: the strategy's directions shuffled across its trades, which keeps the long/short count.

In a trending sample, "random" is beaten by any long-biased rule. "perm" controls for that. In each cell, p = (#null avg ≥ real avg + 1) / 51.

| config | split | real avg $ | random: null mean (sd), p | perm: null mean (sd), p |
|---|---|---|---|---|
| returned (r2 multi) | TRAIN | +5.55 | +0.46 (1.76), **p = 0.020** | +4.06 (1.78), **p = 0.255** |
| returned (r2 multi) | VALID | +2.22 | −2.25 (7.15), **p = 0.275** | −2.02 (9.13), **p = 0.412** |
| primary (ch2 eod) | TRAIN | +4.58 | +0.37 (1.57), p = 0.020 | +3.08 (1.06), p = 0.118 |
| primary (ch2 eod) | VALID | −0.19 | +1.77 (4.32), p = 0.725 | −0.77 (4.57), p = 0.490 |

- **Mirror** (every direction reversed) at lf_base:
  - returned config: TRAIN PF 0.71 and VALID PF 0.77;
  - primary: 0.65 and 0.80.
- The mirror loses, as it should for a long-biased rule in a bull market, so it does not discriminate.
- **Conclusion:** the pass bar needs VALID p ≤ 0.05, and it is **not met**. The TRAIN edge is not significant once the long share is held fixed.

---

## 5. Swap (the multi-day variant)

- SOURCED `recon/web_research.md` l.69: LiteFinance XAUUSD swap is long −89.136 and short +3.45 points per lot per night. It is charged at 00:00 server time, triple on Wednesday. That is −0.89 $/oz per night for longs.
- SOURCED `recon/broker_costs.md` l.181: the live account is reportedly swap-free. The swap-free terms penalise holding over the triple-swap night and holding for more than 5 days (web_research l.98).
- MEASURED (`cand/x2_swap.py`) for the returned config with swap charged:

| split | avg swap nights | avg swap $ | PF (lf_base + swap) | avg $ | R-space PF |
|---|---|---|---|---|---|
| TRAIN | 0.66 | −0.36 | 1.500 | +5.19 | 1.54 |
| VALID | 0.60 | −0.32 | 1.062 | +1.90 | 1.20 |

- The eod variant crosses the rollover in about 1% of trades, so swap there is negligible.

---

## 6. Monthly R table: returned config, lf_base, TRAIN and VALID (MEASURED, `results/htf_breakout_multi_monthly.csv`)

| month | n | long | short | sum R | avg $ | PF | sum R long | sum R short |
|---|---|---|---|---|---|---|---|---|
| 2025-01 | 5 | 4 | 1 | +0.94 | −0.24 | 0.96 | +1.95 | −1.01 |
| 2025-02 | 14 | 9 | 5 | +0.86 | +0.55 | 1.07 | +2.91 | −2.05 |
| 2025-03 | 15 | 13 | 2 | +11.88 | +8.25 | 2.71 | +13.90 | −2.02 |
| 2025-04 | 16 | 12 | 4 | +7.93 | +10.99 | 1.88 | +8.94 | −1.02 |
| 2025-05 | 13 | 7 | 6 | −1.07 | −2.51 | 0.83 | +1.96 | −3.04 |
| 2025-06 | 9 | 5 | 4 | +2.94 | +6.22 | 1.66 | +0.97 | +1.98 |
| 2025-07 | 15 | 7 | 8 | +3.23 | +1.00 | 1.13 | +4.94 | −1.71 |
| 2025-08 | 16 | 13 | 3 | −4.17 | −2.59 | 0.71 | −4.14 | −0.03 |
| 2025-09 | 17 | 16 | 1 | +12.90 | +11.24 | 2.75 | +13.91 | −1.01 |
| 2025-10 | 15 | 11 | 4 | +14.95 | +26.42 | 3.69 | +12.96 | +1.99 |
| 2025-11 | 15 | 11 | 4 | −0.08 | −2.03 | 0.88 | +3.94 | −4.02 |
| 2025-12 | 14 | 11 | 3 | +3.93 | +3.57 | 1.23 | +3.95 | −0.01 |
| **2026-01** | 17 | 15 | 2 | +9.04 | +26.45 | 2.39 | +8.96 | +0.08 |
| **2026-02** | 12 | 9 | 3 | −2.92 | −34.72 | 0.35 | +0.09 | −3.01 |
| **2026-03** | 19 | 6 | 13 | +7.96 | +15.93 | 1.52 | −3.02 | +10.98 |
| **2026-04** | 9 | 4 | 5 | −4.93 | −22.13 | 0.35 | −2.91 | −2.02 |
| **2026-05** | 13 | 6 | 7 | +1.00 | +1.44 | 1.07 | −0.04 | +1.04 |

- **TRAIN:** 9 of 12 months positive in R. The short leg is positive in only 2 of 12.
- **VALID:** 3 of 5 months positive. The result depends on two months: 2026-01 (the long spike) and 2026-03 (the short crash).
- The primary's table is in `results/htf_breakout_best_monthly.csv`.

---

## 7. Pass-bar checklist for the returned config (lf_base unless stated)

| criterion | value | ok? |
|---|---|---|
| n TRAIN ≥ 150, VALID ≥ 60 | 164 / 70 | yes |
| TRAIN PF ≥ 1.15 and avg ≥ +0.15 | 1.542 / +5.55 | yes |
| VALID PF ≥ 1.15 and avg ≥ +0.15 | **1.073** / +2.22 | **no** (PF) |
| lf_harsh VALID PF ≥ 1.0 | 1.063 | yes |
| both TRAIN halves PF > 1 | 1.44 / 1.63 | yes |
| beats random direction on VALID at p ≤ 0.05 | **p = 0.27** (perm 0.41) | **no** |
| median VALID PF of neighbours ≥ 1.05 | 1.06 (8 neighbours); top-10 plateau about 0.99 | borderline |
| drop best month PF ≥ 1.05 | TRAIN 1.34, **VALID 0.84** | **no** |
| "near" rule (VALID PF ≥ 1.05, TRAIN PF ≥ 1.10, n ok) | 1.073 / 1.542 | **near** |

**OPINION: treat it as "near" only in the mechanical sense.**
- It is a long-beta trend follower with a VALID result inside the random-direction null.
- It is not tradeable as it stands and cannot be used for the flip.
- If it is taken further: measure it in R under fixed-fractional sizing, with swap charged, and on a longer history that includes a gold bear or range phase. The TEST split alone (4 months) cannot separate trend skill from beta.

---

## 8. trend_pullback (X2b): rules, search, result

Code: `cand/trend_pullback.py`. Longs are described; shorts are the exact mirror.

| item | rule |
|---|---|
| trend | `ema`: last complete H1 bar with C > EMA50 > EMA200 (H1 closes). `don4`: H4 Donchian-20 state, i.e. the side of the last H4 close beyond the prior 20-bar channel. |
| zone | `ema`: M1 low ≤ EMA20 of the last complete M5 bar + 0.1·A5. `fvg`: M1 low enters the most recent live bullish M5 FVG (gap ≥ 0.1·A5, alive 24 M5 bars, killed by an M5 close below it). |
| arm | The first touch while the trend is +1, with no touch in the previous `gap` M1 bars (a fresh pullback). |
| trigger | Within [i, i+W] M1 bars, with the trend still +1 and the gates passing. `eng`: bullish engulfing. `swing`: close above the highest high of the previous 3 bars (a minor swing break). |
| stop | min(L[i−3..j]) − σ·A, widened to at least `smin`·A. This is behind the pullback swing. |
| exit | `rX`: target X·R. `trX`: trail X·A5 once the trade is +1R. Also a time stop of 240 min and flat at 16:45 ET. |
| session | `day`: entries 07:00-16:29 London. `all`: any time the gates allow except 15:30-17:00 ET. |
| gates | The same as htf_breakout. |

**Search (TRAIN, lf_base):**
- Stage 1: trend (2) × zone (2) × trig (2) × W {10, 30} × ex {r2, r3, tr} × sess (2) = 96 configs, with σ = 0.3, smin = 1 and gap = 15.
  - Only 2 of 96 have PF > 1. The best TRAIN t is −0.29.
  - 4 of the top 5 are zone = fvg, trig = eng, sess = day.
- Stage 2: trend (2) × ex {tr2, tr4, r3, r5} × σ {0.3, 1.0} × smin {1, 2} × gap {15, 60} × W {10, 30} = 128 configs, with fvg / eng / day fixed.
  - 31 of 128 have PF > 1. The best TRAIN t is 0.95. Only 2 configs reach TRAIN PF ≥ 1.10.
- Total: **216 unique** configs.
- Files: `results/trend_pullback_s1_train.csv`, `_s2_train.csv`, `_train.csv` (merged) and `_valid.csv` (top 10).

**Best by TRAIN t:** `{"trend":"ema","zone":"fvg","trig":"eng","sess":"day","ex":"tr4","sigma":1.0,"smin":1.0,"gap":15,"W":30}`. MEASURED:

| split | cost | n | PF | avg $ | avg R | long PF (n) | short PF (n) | flip-eligible n, PF (lf_base) |
|---|---|---|---|---|---|---|---|---|
| TRAIN | lf_base | 606 | 1.081 | +0.27 | +0.092 | 1.10 (470) | 1.04 (136) | 292, 1.19 |
| TRAIN | lf_harsh | 617 | 0.806 | −0.75 | −0.155 | 0.83 | 0.75 | 258, 0.80 |
| TRAIN | mid | 600 | 1.240 | +0.72 | +0.239 | 1.26 | 1.19 | 299, 1.40 |
| VALID | lf_base | 255 | **0.752** | −1.80 | +0.002 | 0.84 (134) | 0.68 (121) | 28, 1.48 |
| VALID | lf_harsh | 256 | 0.650 | −2.75 | −0.118 | 0.69 | 0.62 | 17, 0.32 |
| VALID | mid | 254 | 0.814 | −1.29 | +0.078 | 0.90 | 0.75 | 32, 2.34 |

Other diagnostics:
- **Flip-eligible share:** 48% on TRAIN but only 11% on VALID, because stops doubled with volatility (median risk $4.1 on TRAIN, $7.9 on VALID).
- **Nulls (lf_base, 50 seeds):** TRAIN random p = 0.020 and perm p = 0.020, so the TRAIN gross edge is real in-sample and not beta. VALID random p = 0.57 and perm p = 0.45.
- **TRAIN halves:** 1.01 / 1.15.
- **Top 10 by TRAIN t on VALID (lf_base):** PF 0.75-1.03 (median about 0.86).
- **Verdict: FAIL** (TRAIN PF < 1.10, VALID PF 0.75).

The tiny VALID flip-eligible subset (n = 28) is noise (lf_harsh PF 0.32 at n = 17) and must not be mined.

---

## 9. Reproduction

All runs are single-process or `--jobs 2`, TRAIN+VALID only.

```
cd xau_alpha
python3 lib/sweep.py htf_breakout --jobs 2 --top 0          # stage 1 -> copy to results/htf_breakout_s1_train.csv
python3 lib/sweep.py trend_pullback --jobs 2 --top 0        # stage 1 -> results/trend_pullback_s1_train.csv
python3 cand/x2_stages.py hb_s2 ; python3 cand/x2_stages.py tp_s2
python3 cand/x2_trainsplit.py htf_breakout '[{...}]'        # TRAIN-only long/short + beta
python3 cand/x2_eval.py htf_breakout '{"tf":60,"N":55,"sl":1.5,"ex":"r2","hold":"multi","fresh":0}' --seeds 50 --tag multi
python3 cand/x2_eval.py htf_breakout '{"tf":60,"N":55,"sl":1.5,"ex":"ch2","hold":"eod","fresh":0}' --seeds 50 --tag best --neighbors '[...]'
python3 cand/x2_eval.py trend_pullback '{"trend":"ema","zone":"fvg","trig":"eng","sess":"day","ex":"tr4","sigma":1.0,"smin":1.0,"gap":15,"W":30}' --seeds 50 --tag best
python3 cand/x2_valid_top.py htf_breakout tf,N,sl,ex,hold,fresh --defaults '{"fresh":0}'
python3 cand/x2_valid_top.py trend_pullback trend,zone,trig,W,ex,sess,sigma,smin,gap --defaults '{"sigma":0.3,"smin":1.0,"gap":15}'
python3 cand/x2_swap.py htf_breakout '{"tf":60,"N":55,"sl":1.5,"ex":"r2","hold":"multi","fresh":0}'
```

**Results files** (all small; no trade lists):
- `results/htf_breakout_{s1,s2}_train.csv`, `_train.csv`, `_valid.csv`;
- `_best_eval.json`, `_multi_eval.json`, `_multi_nb_eval.json`;
- `_best_monthly.csv`, `_multi_monthly.csv`;
- the same set for `trend_pullback`.

---

## 10. Engine notes (no engine file was changed)

- **`nulltest.random_direction` draws each direction at p = 0.5.** For any trend or directional family tested on a trending sample, this null is anti-conservative: a long-biased rule beats it through beta alone. Here it gives p = 0.02 on TRAIN, but only 0.12-0.25 once the long/short mix is fixed.
  - Suggestion (OPINION): add a permutation null next to it. `cand/x2_eval.null_p(perm=True)` is a 10-line implementation on top of `_to_relative` / `_apply_dir`.
- **`sim._manage` exit reason labels.** When a trailing stop sits between the initial stop and entry + `be_off`, the exit is labelled `"be"` even though no break-even rule exists. The P&L is unaffected; it is cosmetic.
- **`sim.simulate` does not model swap.** For multi-day families, use `cand/x2_swap.py`, or add a per-rollover charge to the `Cost` model.
- **`sweep.evaluate` / `stats` PF is in $/oz.** When volatility doubles between TRAIN and VALID, it is dominated by the high-volatility trades. For a main strategy with fractional-risk sizing, R-space PF (reported here) is the relevant figure. OPINION: consider adding it to `stats`.

---

## 11. Bottom line (OPINION)

- H1/H4 Donchian trend following on gold made money in 2025 mainly by being long in a +59% year.
- In the Jan-May 2026 spike and crash it was roughly break-even in dollars and slightly positive in R. Its shorts caught the March crash, which is the only real evidence of symmetric trend-following value.
- It does not beat random or permuted direction on VALID.
- Pullback entries with small M1 stops have a small TRAIN gross edge that costs remove, and they fail VALID outright.
- Neither module is a flip candidate. htf_breakout's stops are 3-20x the flip cap.
