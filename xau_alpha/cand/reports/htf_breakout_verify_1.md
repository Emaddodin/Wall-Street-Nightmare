# htf_breakout: verifier 1 (statistical robustness)

Date: 2026-09-30. Adversarial check of the finalist in `cand/htf_breakout.py` (report `cand/reports/htf_breakout.md`).

**Finalist:** `{"tf":60,"N":55,"sl":1.5,"ex":"r2","hold":"multi","fresh":0}`. H1 Donchian-55 close breakout, both directions, stop 1.5·ATR14(H1), target 2R, flat after 10 days.

**Lens:** statistical robustness on TRAIN and VALID. I did not repeat the causality audit, which is in `htf_breakout_verify_0.md`.

**Script:** `cand/htf_breakout_verify1.py`, modes `core`, `nulls`, `timing`, `nbrs`, `swap`, `lomo`, `mt`. The outputs are small JSON/CSV files in `cand/results/htf_breakout_verify1_*`, with no trade lists.

**Labels:** MEASURED = computed here on TRAIN/VALID; SOURCED = read from the cited file; OPINION = my judgement.

**Holdout:**
- No order with t ≥ 2026-06-01 was generated or simulated. `htf_base` cuts M1 at the TEST start, and every order is asserted to have t < TEST and `flat` ≤ 2026-05-31 23:59.
- Every simulation in the script runs on 10-s broker arrays that are **physically truncated** at 2026-06-01 (`sim.simulate(arr=...)`).
- The one exception is the unmodified library call `nulltest.random_direction`, which gets VALID-only orders that exit before the cut. It matches my truncated-array replay on 200 of 200 seeds.
- `final=True` was never used. No `lib/*.py` file was changed.

**Configs simulated by this verifier:** the finalist (8 cost/delay variants and 7 swap variants) plus 54 neighbour configs (`nbrs`). The nulls add 20,000 replay draws per null plus 200 library seeds.

---

## 0. Verdict: REFUTED (severity: fatal for the "edge" claim)

The VALID result cannot be told apart from direction or timing nulls. The TRAIN result is long beta, and the TRAIN selection does not survive a multiple-testing haircut. Cost stress is **not** where it fails: VALID PF stays at 1.05-1.09 under every stress. The problem is that 1.07 was never distinguishable from chance in the first place.

| test (all MEASURED, lf_base, VALID unless stated) | result | pass bar |
|---|---|---|
| Library `nulltest.random_direction`, 200 seeds | **p = 0.31** on avg $/oz and PF (p = 0.045 on avg R, but 0.070 at 20k draws) | p ≤ 0.05: **fail** |
| Random direction, 20k replay draws | p = 0.285 ($), 0.070 (R) | fail |
| Order-level permutation (keeps the long/short count) | p = 0.288 ($), 0.080 (R) | fail |
| Trade-set permutation (same 70 trades, directions shuffled) | p = 0.314 ($), 0.071 (R) | fail |
| Exposure null (random gated H1 closes, same long share) | p = 0.44 ($), 0.20 (R) | fail |
| Forward-jitter null (same direction, entry moved randomly ≤ 24 h later) | p = **0.89** ($), 0.83 (R): the real entry time is *worse* than a random later one | fail |
| TRAIN: always-long on the strategy's own timestamps | PF **1.72**, +7.00 $/oz, beating the strategy's 1.54 / +5.55 | TRAIN edge = beta |
| TRAIN trade-set permutation | p = 0.17 ($), 0.19 (R) | TRAIN has no direction skill |
| Neighbours: 8 on-grid / 12 fine / 44-point cube, median VALID PF | 1.065 / 1.056 / **1.018** | borderline (≥ 1.05 on 2 of 3) |
| Drop the best VALID month | PF **0.836**; avg R 0.021 (R permutation p 0.35) | fail |
| Stress: lf_harsh / +0.30 per side / +5 s (fills +10 s) / lf_base + swap / all combined + swap | VALID PF 1.063 / 1.063 / 1.097 / 1.062 / 1.075 | pass |
| Multiple testing on TRAIN t = 2.82 | Bonferroni over 256 configs p = 0.69, over 472 p = 1.0; BHY p = 0.34; deflated Sharpe 0.82 | not significant |
| VALID t (R) | 0.74; one-sided p 0.23, or 0.41 after the 2-look Šidák correction | not significant |

---

## 1. Reproduction (MEASURED, `core`)

- `sweep.evaluate` and my truncated-array run give identical numbers:
  - TRAIN n 164, PF 1.542, +5.55 $/oz, t 2.82.
  - VALID n 70, PF 1.073, +2.22 $/oz, avg R +0.145, t 0.74, R-space PF 1.216.
- These match the hunt report (SOURCED `htf_breakout.md` §3a).
- The one-position-at-a-time replay (`Outcomes.replay`) reproduces `sim.simulate` exactly in three cases: the real directions, all-long, and all-short (`replay_parity_*` = true). So every replay null below runs the same engine path as the strategy.

---

## 2. Null tests (MEASURED, `nulls`, `timing`; lf_base)

Each p-value is (#null ≥ real + 1)/(draws + 1), one-sided. The nulls differ in what they hold fixed:

- **random:** the nulltest design. Same order times and exit geometry; each direction is ±1 with p = ½.
- **order perm:** the strategy's order directions shuffled across its orders, which keeps the long/short count.
- **trade-set perm:** the 70 realised VALID trades fixed; which of them are long is shuffled; each trade is scored on its own.
- **exposure:** the same number of orders placed at uniformly random gated H1 closes (7,127 candidates with the same warm-up, gates and ATR stop/target), with the strategy's directions permuted among them. This tests timing skill beyond the long share.
- **forward jitter:** each order moved to a random gated H1 close in (t, t+24h] with the same direction. This tests whether the breakout moment itself matters.

| null | split | draws | real avg $ | null mean $ (5-95%) | p ($) | real avg R | null mean R | p (R) |
|---|---|---|---|---|---|---|---|---|
| library random_direction | VALID | 200 | +2.22 | −2.26 | **0.309** | +0.145 | n/a | 0.045 |
| random | VALID | 20,000 | +2.22 | −2.15 (−14.5, +10.2) | **0.285** | +0.145 | −0.071 | 0.070 |
| order perm | VALID | 20,000 | +2.22 | −2.15 | 0.288 | +0.145 | −0.053 | 0.080 |
| trade-set perm | VALID | 100,000 | +2.22 | −1.76 (−14.9, +11.3) | 0.314 | +0.145 | −0.071 | 0.071 |
| exposure | VALID | 5,000 | +2.22 | +1.13 | 0.438 | +0.145 | +0.010 | 0.202 |
| forward jitter 24 h | VALID | 5,000 | +2.22 | **+8.10** | **0.894** | +0.145 | +0.242 | 0.831 |
| random | TRAIN | 20,000 | +5.55 | +0.88 | 0.006 | +0.331 | +0.067 | 0.004 |
| order perm | TRAIN | 20,000 | +5.55 | +4.05 | **0.181** | +0.331 | +0.267 | 0.220 |
| trade-set perm | TRAIN | 100,000 | +5.55 | +3.94 | **0.169** | +0.331 | +0.261 | 0.191 |
| exposure | TRAIN | 5,000 | +5.55 | +1.56 | 0.012 | +0.331 | +0.102 | 0.004 |
| forward jitter 24 h | TRAIN | 5,000 | +5.55 | +3.26 | 0.044 | +0.331 | +0.188 | 0.013 |

**Mirror** (library `nulltest.mirror`, every direction reversed): TRAIN PF 0.709 (n 226), VALID PF 0.772 (n 86). The mirror loses, as any long-biased rule does in a bull year, so it does not tell trend skill from beta.

Reading (OPINION, based on the MEASURED rows):

- **VALID fails every null in the pass-bar metric ($/oz): p = 0.29-0.44.**
- In R the best p is 0.07-0.08. The library's 200-seed p = 0.045 on avg R is a sampling fluctuation:
  - Those 200 seeds are the first 200 of the 20k random-direction draws (identical RNG, 0 mismatches), and at 20k draws p is 0.070.
  - The 200-seed standard error at p ≈ 0.07 is about 0.018.
  - R is also not the pass-bar metric, and the finalist was the better of 2 VALID looks. With Šidák over 2 looks, R p ≈ 0.135.
- **The breakout moment carries no VALID information.** Entering the same direction at a random H1 close up to 24 h later does better in 89% of draws (null mean PF 1.29 against the real 1.07). This agrees with verifier 0's per-signal PF of 1.27 (SOURCED `htf_breakout_verify_0.md` delaydiag): the one-at-a-time sequence happened to pick a weaker subset.
- **TRAIN "timing skill" is directional momentum in a bull market, not symmetric trend following.** TRAIN beats the exposure null (p 0.01), which holds the long share fixed. But given the timestamps, the direction choice is worthless or worse: trade-set p 0.17, and always-long on the same timestamps beats the strategy (§3).

---

## 3. Beta decomposition: long vs short against naive benchmarks (MEASURED, `nulls` / `timing`, lf_base)

The same trade timestamps and exit geometry (each trade evaluated on its own):

| split | row | n | PF | avg $ | avg R |
|---|---|---|---|---|---|
| TRAIN | **strategy** | 164 | 1.542 | +5.55 | +0.331 |
| TRAIN | **always long, same timestamps** | 164 | **1.719** | **+7.00** | **+0.457** |
| TRAIN | always short, same timestamps | 164 | 0.691 | −4.17 | −0.256 |
| TRAIN | long signals taken long | 119 | 2.280 | +9.81 | +0.556 |
| TRAIN | short signals taken short | 45 | 0.664 | −5.72 | −0.266 |
| TRAIN | short signals taken **long** | 45 | 0.973 | −0.41 | **+0.193** |
| TRAIN | always long, same order set (one at a time) | 180 | 1.601 | +6.22 | +0.408 |
| TRAIN | every gated H1 close, long / short | 4,967 | 1.197 / 0.819 | +2.25 / −2.32 | +0.161 / −0.173 |
| VALID | **strategy** | 70 | 1.073 | +2.22 | +0.145 |
| VALID | always long, same timestamps | 70 | 0.931 | −2.18 | −0.047 |
| VALID | always short, same timestamps | 70 | 0.960 | −1.23 | −0.102 |
| VALID | long signals taken long | 40 | 0.897 | −2.86 | +0.077 |
| VALID | short signals taken short | 30 | 1.266 | +8.99 | +0.236 |
| VALID | always long / always short, same order set | 74 / 82 | 0.904 / 0.903 | −3.12 / −2.90 | −0.017 / −0.145 |
| VALID | every gated H1 close, long / short | 2,160 | 0.937 / **1.210** | −1.85 / **+5.54** | −0.093 / +0.174 |

- **TRAIN:** the short leg subtracts value. A long-only rule on the same timestamps would have scored higher: PF 1.72 against 1.54, and +0.46R against +0.33R. The TRAIN ranking (t 2.82) therefore measures 2025's long drift at breakout times, not two-sided trend following. It is not a two-sided edge.
- **VALID:** VALID's drift at this exit geometry was down. A random short at any gated H1 close made +5.54 $/oz, and a random long lost −1.85. The strategy's VALID shorts (+8.99 $) beat a random-time short by about +3.5 $/oz, or +0.06R. Its longs beat a random-time long by about −1.0 $, or +0.17R.
- These are small differences on n = 30-40, and they sit inside the permutation nulls of §2 (OPINION).

---

## 4. Parameter neighbourhood (MEASURED, `nbrs`, lf_base; pre-registered lists)

- **G8:** the 8 nearest on-grid single-parameter changes, the same set as the hunt report: N 45/34, sl 1.0/3.0, ex ch2/ch2.5, hold eod, fresh 1.
- **F12:** 12 finer single changes: N 45/50/60/65, sl 1.25/1.75, ex r1.5/r1.75/r2.25/r2.5, fresh 1, maxd 5.
- **CUBE:** N {45, 50, 55, 60, 65} × sl {1.25, 1.5, 1.75} × ex {r1.5, r2, r2.5}, 44 configs excluding the finalist.
- N 60/65 and sl 1.25/1.75 are off the original grid. N = 55 was the **edge** of the searched grid.

| set | k | median VALID PF | min-max | share ≥ 1.05 | share > 1 | median VALID avg R | median VALID t | finalist's rank |
|---|---|---|---|---|---|---|---|---|
| G8 | 8 | **1.065** | 0.955-1.382 | 0.50 | 0.50 | +0.140 | 0.72 | 5 of 9 |
| F12 | 12 | 1.056 | 0.978-1.304 | 0.50 | 0.75 | +0.117 | 0.62 | 5 of 13 |
| CUBE | 44 | **1.018** | 0.841-1.256 | 0.41 | 0.59 | +0.097 | 0.56 | 13 of 45 |

G8 detail, VALID PF (n): N45 0.979 (75), N34 0.955 (86), sl1.0 0.967 (87), sl3.0 1.382 (**41**), ch2 1.200 (57), ch2.5 1.150 (53), eod 0.975 (74), fresh1 1.304 (59). This reproduces the hunt report's 8 values (SOURCED §3a, median 1.06).

- The G8 median clears 1.05 only because of 4 configs with VALID n < 60: sl3.0, ch2, ch2.5 and fresh1.
- The 44-point cube's median is 1.018. **No neighbour reaches VALID t 1.4** (max 1.36; cube max 1.08).
- Every neighbour has a positive VALID avg R. OPINION: that is expected from heavily overlapping trade sets in a sample where shorts caught the March crash. It is one piece of evidence, not 44.

---

## 5. Monthly R table and drop-best-month (MEASURED, `core`; `results/htf_breakout_verify1_monthly.csv`)

| month | split | n | L/S | sum R | sum $ | PF ($) | PF (R) | sum R long | sum R short |
|---|---|---|---|---|---|---|---|---|---|
| 2025-01 | T | 5 | 4/1 | +0.94 | −1.2 | 0.96 | 1.31 | +1.95 | −1.01 |
| 2025-02 | T | 14 | 9/5 | +0.86 | +7.7 | 1.07 | 1.09 | +2.91 | −2.05 |
| 2025-03 | T | 15 | 13/2 | +11.88 | +123.7 | 2.71 | 2.96 | +13.90 | −2.02 |
| 2025-04 | T | 16 | 12/4 | +7.93 | +175.8 | 1.88 | 1.98 | +8.94 | −1.02 |
| 2025-05 | T | 13 | 7/6 | −1.07 | −32.7 | 0.83 | 0.88 | +1.96 | −3.04 |
| 2025-06 | T | 9 | 5/4 | +2.94 | +56.0 | 1.66 | 1.58 | +0.97 | +1.98 |
| 2025-07 | T | 15 | 7/8 | +3.23 | +14.9 | 1.13 | 1.36 | +4.94 | −1.71 |
| 2025-08 | T | 16 | 13/3 | −4.17 | −41.5 | 0.71 | 0.66 | −4.14 | −0.03 |
| 2025-09 | T | 17 | 16/1 | +12.90 | +191.0 | 2.75 | 2.82 | +13.91 | −1.01 |
| 2025-10 | T | 15 | 11/4 | +14.95 | +396.4 | 3.69 | 3.97 | +12.96 | +1.99 |
| 2025-11 | T | 15 | 11/4 | −0.08 | −30.4 | 0.88 | 0.99 | +3.94 | −4.02 |
| 2025-12 | T | 14 | 11/3 | +3.93 | +50.0 | 1.23 | 1.49 | +3.95 | −0.01 |
| 2026-01 | V | 17 | 15/2 | **+9.04** | +449.6 | 2.39 | 2.01 | +8.96 | +0.08 |
| 2026-02 | V | 12 | 9/3 | −2.92 | −416.6 | 0.35 | 0.71 | +0.09 | −3.01 |
| 2026-03 | V | 19 | 6/13 | **+7.96** | +302.7 | 1.52 | 1.79 | −3.02 | +10.98 |
| 2026-04 | V | 9 | 4/5 | −4.93 | −199.2 | 0.35 | 0.45 | −2.91 | −2.02 |
| 2026-05 | V | 13 | 6/7 | +1.00 | +18.8 | 1.07 | 1.11 | −0.04 | +1.04 |

**TRAIN:**
- The TRAIN short leg is positive in only 2 of 12 months, and it sums to −12.0R.
- The long leg makes +66.2R.

**Dropping months:**

| split | drop best month ($) | drop best 2 | leave-one-month-out PF range | drop best R month: avg R / R-space PF |
|---|---|---|---|---|
| TRAIN | 1.335 (2025-10) | 1.227 | 1.335-1.659 | +0.264 / 1.452 |
| VALID | **0.836** (2026-01) | **0.507** | 0.836-1.387 | **+0.021** / 1.029 |

**VALID leave-one-month-out trade-set permutation p** (`lomo`, 50k draws):

| month dropped | n | avg $ | avg R | p ($) | p (R) |
|---|---|---|---|---|---|
| none | 70 | +2.22 | +0.145 | 0.313 | 0.071 |
| **2026-01** | 53 | −5.55 | +0.021 | 0.690 | **0.352** |
| 2026-02 | 58 | +9.86 | +0.225 | 0.043 | 0.032 |
| **2026-03** | 51 | −2.89 | +0.043 | 0.704 | **0.370** |
| 2026-04 | 61 | +5.81 | +0.247 | 0.265 | 0.055 |
| 2026-05 | 57 | +2.40 | +0.161 | 0.268 | 0.033 |

Whatever R-space signal VALID has comes from two regime months: the January 2026 long spike and the March 2026 short crash. Removing either one leaves nothing (R p ≈ 0.35-0.37).

---

## 6. Cost and latency stress (MEASURED, `core`, `swap`)

- +0.30 $/oz per side is added to `slip_entry` and `slip_exit`. `sim` does not charge slippage on TP fills, so the "every exit" rows also subtract 0.30 from each TP trade after the fact.
- **+5 s entry delay:** `sim` fills on the first 10-s bar at or after t, so every delayed order filled exactly **10 s** late (min = max = 10 s). That is harsher than the 5 s asked for.
- **Swap:** SOURCED `recon/web_research.md` l.69 and `cand/x2_swap.py`: long −0.891, short +0.0345 $/oz per server-midnight rollover (Europe/Athens), triple on Wednesday.

| variant | TRAIN n / PF / avg $ | TRAIN halves PF | VALID n / PF / avg $ / avg R | VALID long / short PF |
|---|---|---|---|---|
| lf_base | 164 / 1.542 / +5.55 | 1.44 / 1.63 | 70 / **1.073** / +2.22 / +0.145 | 0.90 / 1.27 |
| lf_harsh | 165 / 1.379 / +4.11 | 1.21 / 1.53 | 70 / 1.063 / +1.93 / +0.137 | 0.89 / 1.26 |
| mid (gross) | 163 / 1.625 / +6.25 | 1.53 / 1.70 | 70 / 1.079 / +2.39 / +0.150 | 0.90 / 1.27 |
| lf_base +0.30/side (entry + SL/time exits) | 164 / 1.433 / +4.61 | 1.35 / 1.50 | 70 / 1.066 / +2.03 / +0.140 | 0.89 / 1.26 |
| lf_base +0.30/side (every exit incl. TP) | 164 / 1.420 / +4.48 | 1.34 / 1.49 | 70 / 1.063 / +1.91 / +0.136 | 0.89 / 1.26 |
| lf_base, entry +5 s (fills +10 s) | 169 / 1.366 / +3.92 | 1.20 / 1.51 | 69 / 1.097 / +2.92 / +0.164 | 0.90 / 1.32 |
| lf_harsh, entry +5 s | 166 / 1.366 / +3.98 | 1.20 / 1.52 | 69 / 1.087 / +2.62 / +0.155 | 0.89 / 1.31 |
| lf_base +0.30 every exit, +5 s | 166 / 1.358 / +3.89 | 1.19 / 1.51 | 69 / 1.086 / +2.60 / +0.155 | 0.89 / 1.31 |
| **lf_base + swap** | 164 / 1.500 / +5.19 | 1.39 / 1.60 | 70 / **1.062** / +1.90 / +0.135 | 0.88 / 1.27 |
| lf_harsh + swap | 165 / 1.344 | 1.17 / 1.49 | 70 / 1.052 / +1.61 / +0.126 | 0.87 / 1.26 |
| lf_base +0.30 every exit + swap | 164 / 1.384 | 1.29 / 1.46 | 70 / 1.052 / +1.59 / +0.126 | 0.87 / 1.26 |
| lf_base +0.30 every exit, +5 s, + swap | 166 / 1.323 | 1.15 / 1.48 | 69 / 1.075 / +2.28 / +0.145 | 0.87 / 1.31 |
| lf_harsh, +5 s, + swap | 166 / 1.331 | 1.16 / 1.49 | 69 / 1.075 / +2.30 / +0.145 | 0.87 / 1.31 |

**Swap detail:**
- Average swap nights: 0.66 per trade on TRAIN, 0.60 on VALID.
- Average swap cost: −0.36 $/oz on TRAIN, −0.32 on VALID.
- For a swap-free account: 7-8% of trades are held over the triple-swap (Wednesday) rollover and 0-0.6% are held more than 5 days. Both breach the swap-free terms (SOURCED web_research §1.2).

**Reading (OPINION):**
- Costs are small next to the $18-44 stops, so every stress leaves VALID PF at 1.05-1.10.
- The stress test passes, but it proves nothing: the lf_base VALID figure it preserves is itself inside the null band (random-direction 5-95% PF band 0.59-1.38).
- TRAIN is more fragile to the one-bar delay (1.54 → 1.37). Verifier 0 explains this as a reshuffle of the one-at-a-time sequence (SOURCED verify_0 §5).

---

## 7. Multiple-testing haircut (MEASURED, `mt`)

**TRAIN** (t of R per trade = 2.82, n 164, one-sided p = 0.0027):

| correction | trials | adjusted p / value |
|---|---|---|
| Bonferroni | 256 unique htf_breakout configs | **0.69** (the 5% threshold is t ≈ 3.55) |
| Bonferroni | 472 in the X2 family (htf_breakout + trend_pullback) | **1.0** (threshold t ≈ 3.70) |
| Šidák | 256 | 0.50 |
| Benjamini-Hochberg-Yekutieli | 256 (the finalist ranks 3rd by p) | 0.34 |
| Deflated Sharpe (Bailey & López de Prado) | 256; trial SR sd 0.053, expected max null SR 0.150 against the finalist's 0.220 | **DSR = 0.82** (< 0.95) |

- Project-wide, about 3,483 TRAIN configs are logged across 14 hypothesis files (approximate count from `results/*_train.csv`). The expected maximum |z| of that many independent null trials is about 3.4.
- **Caveat on DSR (OPINION):** 255 of the 256 configs have TRAIN PF > 1 (SOURCED hunt report §2). The trial distribution sits on the shared 2025 long drift (mean trial SR 0.148). The DSR's null of SR = 0 is therefore too lenient: it grants the beta as if it were skill. The permutation nulls in §2 are the right control, and they give TRAIN p 0.17-0.18.

**VALID:**
- Selection: the finalist is the better of 2 pre-declared VALID looks, and 16 distinct configs were looked at on VALID (SOURCED hunt report §0).
- VALID t = 0.74 gives one-sided p 0.23. After Šidák over 2 looks it is 0.41; over 16 looks, 0.98.
- The smallest $-metric null p (0.285) becomes 0.49 after 2 looks.

---

## 8. Bootstrap and power (MEASURED, `core`, 20k resamples of trades)

| split | PF 5 / 50 / 95% | avg R 5 / 50 / 95% | P(PF > 1) | P(PF ≥ 1.15) | PF without the top 1 / 3 / 5 trades |
|---|---|---|---|---|---|
| TRAIN | 1.16 / 1.55 / 2.05 | +0.15 / +0.33 / +0.52 | 0.99 | 0.96 | 1.49 / 1.40 / 1.33 |
| VALID | 0.65 / 1.07 / 1.70 | −0.17 / +0.14 / +0.47 | **0.60** | 0.40 | **0.94** / 0.79 / 0.67 |

- **Concentration:** VALID's single best trade (+$273/oz) is 1.76× the whole VALID net. Without it, PF is 0.94. Verifier 0 found that one VALID target filled +$92 beyond its level on a weekend gap (SOURCED verify_0 §0).
- **Power:**
  - If the TRAIN mean R (+0.33) were true out of sample, VALID's n = 70 would give an expected t of only 1.69.
  - At the observed VALID mean and sd of R, about 510 trades would be needed for t = 2. At this trade rate that is roughly 2.5 years of VALID-like data.
- OPINION: VALID is under-powered to confirm even the TRAIN effect size. That does not help the candidate: the burden of proof is on the edge. The TEST split (about 4 months, roughly 55 trades) cannot settle it either.

---

## 9. Pass-bar items under this lens (SYNTHESIS §5)

| criterion | measured | ok? |
|---|---|---|
| Beats random direction (≥ 200 seeds) at p ≤ 0.05, VALID | $: 0.31 (200 seeds), 0.285 (20k); R: 0.045 (200) / 0.070 (20k) | **no** |
| Median VALID PF of the 8 nearest grid points ≥ 1.05 | 1.065 (G8); 1.056 (F12); 1.018 (44-point cube) | borderline; fails on the cube |
| Dropping the best month leaves PF ≥ 1.05 | VALID 0.836 | **no** |
| lf_harsh VALID PF ≥ 1.0 | 1.063 (1.052 with swap) | yes |
| VALID PF ≥ 1.15 at lf_base | 1.073 | **no** (SOURCED: already failed in the hunt report) |
| Both TRAIN halves PF > 1 | 1.44 / 1.63 | yes |

---

## 10. Conclusion (OPINION)

- **Refuted. Severity: fatal for any claim of an edge.**
- VALID PF 1.07 is one ordinary draw from a null with the same timestamps and exit geometry:
  - $-metric p is 0.29-0.44 under every null;
  - the breakout moment itself is worse than a random later entry (p 0.89);
  - the R-space hint (p ≈ 0.07) depends entirely on January and March 2026.
- TRAIN's t = 2.82 is 2025 long beta. The short half destroys value, always-long on the same timestamps does better, and the direction-permutation p is 0.17.
- Multiple testing over 256-472 configs takes the TRAIN t to insignificance.
- Cost stress, swap and latency are not the problem: the VALID number barely moves under stress. There is simply no distinguishable edge for costs to erode.
- **Do not spend the TEST look on this finalist.**
- If the family is revisited: pre-register an R-metric, test two-sided trend following on a multi-year history that contains a gold bear or range regime, and use the permutation and exposure nulls here as the gate. The p = ½ random-direction null is too lenient for directional families.

---

## 11. Reproduction

```
cd xau_alpha        # interpreter with numpy/scipy: /usr/local/bin/python3
python3 cand/htf_breakout_verify1.py core     # ~1.5 min: repro, stress, monthly, drop-month, bootstrap
python3 cand/htf_breakout_verify1.py nulls    # ~7 min: library 200 seeds + 20k replay nulls, trade-set perm, mirror
python3 cand/htf_breakout_verify1.py timing   # ~3 min: exposure and forward-jitter nulls
python3 cand/htf_breakout_verify1.py nbrs     # ~2.5 min: G8 / F12 / CUBE neighbourhoods (55 unique configs)
python3 cand/htf_breakout_verify1.py swap     # ~1 min: stress variants with swap
python3 cand/htf_breakout_verify1.py lomo     # ~0.5 min: VALID leave-one-month-out permutation p
python3 cand/htf_breakout_verify1.py mt       # instant: multiple-testing haircut (needs core + nulls)
```

Outputs are in `cand/results/htf_breakout_verify1_{core,nulls,timing,nbrs,swap,lomo_perm,mt}.json` and `htf_breakout_verify1_monthly.csv`. All are below 40 KB, and no trade lists are stored.
