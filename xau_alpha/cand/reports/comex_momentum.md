# H5: intraday momentum into the COMEX settle (`cand/comex_momentum.py`)

Date: 2026-09-30. Family H5 / H5b / H5c from `recon/SYNTHESIS.md` section 6. Companion family H7 is reported in
`cand/reports/news_second_leg.md`.

**Verdict: FAIL, and a clean "no edge".**
- MEASURED: no H5 variant has a positive gross edge (at zero cost) pooled over TRAIN+VALID. Variants a and c are
  flat to negative. Variant b has the *opposite* sign (a late-day reversal; see section 6).
- MEASURED: the best of 306 TRAIN configs has TRAIN t = +0.30 and VALID PF 0.82 at lf_base.

Labels: **MEASURED** means computed here on TRAIN/VALID only. **SOURCED** means taken from a cited file.
**OPINION** means my judgement. The TEST split (>= 2026-06-01) was never simulated: `comex_momentum._base` cuts the
arrays there, and `sweep.run_orders` drops those orders as well.

---

## 1. Hypothesis and rules

**Why (SOURCED `recon/web_research.md` 2.3, SYNTHESIS H5).** Across more than 60 futures, the return from the
prior close to 30 minutes before the close predicts the last 30 minutes (Gao et al. 2018; Baltussen et al. 2021).
For gold, the natural "close" is the COMEX GC settlement window, which ends at 13:30 ET.

**Rules.** Code: `cand/comex_momentum.py`. Longs and shorts are mirrored: d = sign(r), with no directional bias.

| item | rule |
|---|---|
| trading day | `tday` (NY date rolled at 17:00 ET). Its first bar is the 18:00 ET reopen. |
| signal, variant a | r = close at 13:00 ET − open of the trading day |
| signal, variant b (H5b) | r = close at 16:00 ET − open of the trading day |
| signal, variant c (H5c) | r = close at 08:50 ET − open at 08:20 ET |
| filter | \|r\| ≥ θ · ATRd · sqrt(window / 1380). ATRd is the mean daily true range over the prior 20 trading days. |
| entry | Market order at 13:00 ET (a, c) or 16:00 ET (b), in the direction of r. The decision is on the bar close; the order time is t = ts + 60 s. |
| exit | Time exit (`flat`): a/c at 13:25, 13:30, 13:45 or 14:00 ET; b at 16:15, 16:25, 16:30 or 16:45 ET. Never after 16:45 ET. |
| stop | sd = κ × the median \|30-min move\| at the same ET clock over the prior 20 trading days (`med_n`). |
| stop cap | `none`: keep sd. `skip`: skip the day if sd is outside [1.2, 4.0] $/oz. `clamp`: clip sd into [1.2, 4.0]. |
| news | Skip the day if a HIGH row falls in [entry − 30 min, exit]. Skip FOMC-statement days when the exit is after 13:30 ET. |

**Deviations from SYNTHESIS (OPINION).**
- The θ threshold is multiplied by sqrt(window / 1380), so θ means the same thing for the 30-minute c window as for
  the 19-22 h a/b windows. The factor is 0.91 for a, 0.98 for b and 0.15 for c.
- Cap handling has 3 values instead of 2: `none` is added for the account of $100 or more.
- The b-variant exit set is {15, 25, 30, 45} min, which keeps b flat by 16:45 ET.

---

## 2. Grid stages and configs tried (MEASURED)

| stage | configs | content |
|---|---|---|
| raw diagnostic | 0 sim configs | TRAIN only. Mid-price forward return from the entry close, signed by the signal, for every trading day. |
| stage 1 | 216 | variant (3) × θ {0, 0.25, 0.5} × exit (4) × κ {1, 2} × cap {none, skip, clamp} |
| stage 2 | 90 | Top 5 stage-1 parents by TRAIN t (n ≥ 60) × θ {0.6, 1, 1.6}× parent × κ {0.75, 1.5, 3} × med_n {10, 40} |
| VALID check | 10 configs × 3 costs | Top 10 by TRAIN t, at lf_base, lf_harsh and mid |
| diagnostics | 3 | Placebo entry times of 11:00 and 15:00 ET; the post-hoc H5b reversal (mirror) |
| pooled family check | 3 configs × 2 costs | θ = 0, exit 30, κ 2, cap none for each variant. These configs are already in stage 1. |

- **Total:** 306 search configs plus 3 diagnostics = **309**.
- **Pre-sweep VALID look:** one smoke-test `evaluate` before the sweep (a, θ 0.25, exit 30, κ 2, none) printed a
  VALID PF of 0.60. That config was not selected.
- **Files:** `cand/results/comex_momentum_stage1_train.csv` and `_stage2_train.csv`, combined in `_train.csv`;
  `_valid.csv`, `_final.json`, `_monthly.csv`, `_diagnostics.csv` and `_pooled_variants.csv`.
- **Driver:** `cand/h5h7_stages.py` (h5s1, h5s2, valid, final, h5extra).

### Raw TRAIN diagnostic (MEASURED, mid price, no costs, every trading day, n ≈ 227-234)

| variant | fwd 25 min | fwd 30 min | fwd 45 min | fwd 60 min |
|---|---|---|---|---|
| a (open → 13:00, trade 13:00 →) | +0.22 pts, t 0.80 | +0.10, t 0.33 | −0.13, t −0.32 | +0.05, t 0.11 |
| b (open → 16:00, trade 16:00 →) | **−0.50, t −2.24** | −0.37, t −1.47 | −0.40, t −1.40 | n/a |
| c (08:20 → 08:50, trade 13:00 →) | −0.06, t −0.22 | +0.10, t 0.32 | +0.49, t 1.19 | +0.27, t 0.60 |

### Stage 1 summary (MEASURED, TRAIN, lf_base unless marked mid)

| variant | median n | median PF | max PF | max t | median mid PF | max mid PF | mid t range |
|---|---|---|---|---|---|---|---|
| a | 125 | 0.68 | 0.87 | −0.74 | 0.92 | 1.21 | −1.61 .. 0.35 |
| b | 148 | 0.47 | 0.63 | −1.93 | 0.73 | 0.94 | −3.41 .. −0.13 |
| c | 136 | 0.63 | 0.83 | −1.15 | 0.83 | 1.06 | −1.87 .. 0.46 |

- All 216 stage-1 configs have TRAIN t < 0 at lf_base.
- Stage 2 lifted the best TRAIN t to +0.30: variant a, θ 0.30, exit 60 (14:00 ET), κ 0.75, cap skip, med_n 10, n = 62.

### Pooled evidence per variant family (MEASURED, θ = 0 = every day, exit 30 min, κ 2, cap none)

SYNTHESIS asks for t ≥ 2 on TRAIN+VALID pooled within each variant family.

| variant | cost | TRAIN n / PF / t | VALID n / PF / t | pooled t (R) | pooled t (pts) |
|---|---|---|---|---|---|
| a | mid | 234 / 0.90 / −1.03 | 105 / 0.72 / −0.83 | −1.32 | −1.45 |
| a | lf_base | 234 / 0.67 / −3.36 | 105 / 0.65 / −1.30 | −3.48 | −2.74 |
| b | mid | 220 / 0.73 / −1.62 | 99 / 0.60 / −1.79 | **−2.28** | **−2.61** |
| b | lf_base | 220 / 0.49 / −4.42 | 99 / 0.52 / −2.47 | −5.07 | −4.39 |
| c | mid | 234 / 0.79 / −0.75 | 105 / 0.74 / −0.76 | −1.06 | −1.76 |
| c | lf_base | 234 / 0.61 / −2.63 | 105 / 0.68 / −1.12 | −2.79 | −2.84 |

**No family has a positive pooled t, even at zero cost.**

---

## 3. Finalist (chosen by TRAIN t only) and its VALID result (MEASURED)

`{"variant":"a","theta":0.3,"exit":60,"kappa":0.75,"cap":"skip","med_n":10}` (enter at 13:00 ET, exit at 14:00 ET)

| cost | TRAIN n / PF / avg $/oz / avg R / t | VALID n / PF / avg $/oz / avg R / t | pooled PF / t |
|---|---|---|---|
| lf_base | 62 / 1.06 / +0.09 / +0.07 / 0.30 | 18 / 0.82 / −0.43 / −0.25 / −0.50 | 0.99 / 0.01 |
| lf_harsh | 62 / 0.54 / −0.90 / −0.41 / −1.92 | 18 / 0.69 / −0.82 / −0.39 / −0.82 | 0.58 / −2.08 |
| mid (gross) | 62 / 1.38 / +0.51 / +0.26 / 1.03 | 18 / 0.90 / −0.23 / −0.17 / −0.35 | 1.22 / 0.73 |
| duka_raw | 62 / 0.76 / −0.37 | 18 / 0.86 / −0.32 | 0.79 / −0.85 |

**Long / short at lf_base:**
- TRAIN long 33 / PF 1.45 / +0.61; short 29 / 0.71 / −0.49.
- VALID long 10 / 1.82 / +1.59; short 8 / 0.00 / −2.95.
- The rule is symmetric. The long/short gap reflects gold's 2025-26 uptrend and is noise at this n (OPINION).

**Flip-eligible:** with cap = skip, every trade has a stop in [1.2, 4.0], so the flip subset equals the table above.
- VALID n is only 18 because 2026 volatility pushes most stops above $4: VALID has only Jan and May 2026 trades.

**Train halves PF:** 1.50 / 0.71 (FAIL).

**Drop the best month (2025-05):** pooled PF 0.78.

**Random-direction null, 200 seeds, lf_base:**

| split | real PF | null PF, mean ± sd | p |
|---|---|---|---|
| TRAIN | 1.06 | 0.64 ± 0.18 | 0.02 |
| VALID | 0.82 | 1.02 ± 0.55 | 0.58 |

- avg-R p is 0.05 on TRAIN and 0.57 on VALID.
- The TRAIN p is selection-biased: this config is the best of 306. The VALID p is the one that counts.

**Mirror (every direction reversed):** TRAIN PF 0.28, VALID PF 1.09.

**Placebo entry times** (the same rule entering at 11:00 or 15:00 ET instead of 13:00):

| entry | TRAIN n / PF at lf_base (mid) | VALID n / PF at lf_base (mid) |
|---|---|---|
| 11:00 ET | 105 / 0.83 (1.11) | 16 / 0.00 (0.85) |
| 15:00 ET | 104 / 1.05 (1.60) | 22 / 1.14 (1.27) |
| 13:00 ET (real) | 62 / 1.06 (1.38) | 18 / 0.82 (0.90) |

**The settlement time is not special:** the 15:00 ET placebo is at least as good as 13:00.

### Pass bar (SYNTHESIS section 5)

| criterion | value | result |
|---|---|---|
| n ≥ 150 TRAIN, ≥ 60 VALID | 62 / 18 | FAIL |
| lf_base PF ≥ 1.15 on TRAIN and VALID | 1.06 / 0.82 | FAIL |
| lf_base avg ≥ +0.15 on TRAIN and VALID | +0.09 / −0.43 | FAIL |
| lf_harsh VALID PF ≥ 1.0 | 0.69 | FAIL |
| both TRAIN halves PF > 1 | 1.50 / 0.71 | FAIL |
| random direction p ≤ 0.05 on VALID | 0.58 | FAIL |
| pooled t ≥ 2 per variant family | all negative | FAIL |

### Monthly R table, finalist, lf_base, TRAIN + VALID (MEASURED)

| month | n | sum R | avg R | sum $/oz | PF |
|---|---|---|---|---|---|
| 2025-03 | 1 | +7.45 | +7.45 | +9.49 | inf |
| 2025-04 | 6 | −6.43 | −1.07 | −13.55 | 0.00 |
| 2025-05 | 14 | +9.96 | +0.71 | +24.58 | 2.58 |
| 2025-06 | 9 | +1.75 | +0.20 | +2.13 | 1.20 |
| 2025-07 | 13 | −4.62 | −0.36 | −8.86 | 0.52 |
| 2025-08 | 2 | −0.79 | −0.40 | −0.99 | 0.37 |
| 2025-09 | 2 | +4.98 | +2.49 | +6.14 | 3.96 |
| 2025-10 | 6 | −2.62 | −0.44 | −0.47 | 0.96 |
| 2025-11 | 5 | −0.84 | −0.17 | −1.81 | 0.80 |
| 2025-12 | 4 | −4.24 | −1.06 | −10.87 | 0.00 |
| 2026-01 | 6 | +1.35 | +0.23 | +7.19 | 1.54 |
| 2026-05 | 12 | −5.78 | −0.48 | −14.86 | 0.50 |

- Months with no row had no flip-band stop (cap = skip).
- The 2025-03 trade shows +7.45R: the stop was just over $1.2, and the 60-min time exit caught a large move.

---

## 4. VALID check of the top 10 TRAIN configs (MEASURED, `cand/results/comex_momentum_valid.csv`)

| # | config | TRAIN n / PF / t (lf_base) | VALID n / PF / avg $/oz (lf_base) | VALID PF lf_harsh / mid |
|---|---|---|---|---|
| 1 | a, θ 0.30, x60, κ 0.75, skip, m10 | 62 / 1.06 / 0.30 | 18 / 0.82 / −0.43 | 0.69 / 0.90 |
| 2 | a, θ 0.30, x60, κ 0.75, none, m40 | 127 / 0.91 / −0.16 | 63 / 0.80 / −0.69 | 0.68 / 0.87 |
| 3 | a, θ 0.30, x60, κ 0.75, clamp, m10 | 134 / 0.88 / −0.32 | 63 / 0.88 / −0.35 | 0.75 / 0.95 |
| 4 | a, θ 0.15, x60, κ 0.75, clamp, m10 | 172 / 0.87 / −0.41 | 80 / 0.66 / −1.06 | 0.56 / 0.71 |
| 5 | a, θ 0.30, x60, κ 0.75, clamp, m40 | 127 / 0.89 / −0.42 | 63 / 0.95 / −0.13 | 0.82 / 1.05 |
| 6 | a, θ 0.40, x60, κ 0.75, clamp, m10 | 122 / 0.85 / −0.43 | 53 / 0.68 / −0.98 | 0.58 / 0.74 |
| 7 | a, θ 0.15, x60, κ 0.75, clamp, m40 | 164 / 0.87 / −0.52 | 80 / 0.71 / −0.90 | 0.60 / 0.77 |
| 8 | a, θ 0.30, x45, κ 0.75, clamp, m40 | 132 / 0.91 / −0.52 | 65 / 0.94 / −0.18 | 0.79 / 1.04 |
| 9 | a, θ 0.50, x60, κ 0.75, clamp, m10 | 96 / 0.84 / −0.54 | 44 / 0.42 / −1.83 | 0.36 / 0.46 |
| 10 | a, θ 0.50, x60, κ 0.75, none, m40 | 92 / 0.83 / −0.54 | 44 / 0.43 / −2.09 | 0.35 / 0.48 |

**All 10 have VALID PF < 1 at lf_base.**

---

## 5. Why it fails (OPINION)

- **The published effect is on futures closing auctions, not on spot gold in 2025-26.** The COMEX settlement is
  only one venue for gold. Spot XAUUSD trades continuously through it, and the Dukascopy feed shows no drift into
  13:30 ET that the morning return predicts. The 15:00 ET placebo is as good or better, so nothing about the
  settlement window stands out.
- **The cost is large relative to the signal.** A 30-60 min hold earns a gross edge of at most about ±0.2 $/oz, and
  the round trip costs 0.42.

---

## 6. Post-hoc observation: the late-day reversal (H5b mirrored)

This is labelled post-hoc: it was found by looking at TRAIN results and was not a pre-registered hypothesis.

**What was seen.** Variant b (open → 16:00 ET, trade 16:00-16:25 ET) is significantly *negative* as momentum.
- MEASURED raw TRAIN t = −2.24.
- MEASURED pooled mid t = −2.28 (R) and −2.61 (pts).

**One mirror check** (b, θ 0.25, exit 16:25, κ 2, cap none, reversed direction):

| cost | TRAIN n / PF / avg $/oz / t | VALID n / PF / avg $/oz / t |
|---|---|---|
| lf_base | 148 / 1.03 / +0.04 / −0.38 | 72 / 1.19 / +0.48 / 0.52 |
| mid | 148 / 1.60 / +0.53 / 1.85 | 72 / 1.38 / +0.88 / 0.89 |
| lf_harsh | 148 / 0.50 / −0.82 / −4.14 | 72 / 0.76 / −0.81 / −0.60 |

**OPINION:**
- The gross reversal (about +0.5 $/oz per trade) roughly equals the LiteFinance round trip, so after costs it is
  not tradeable.
- It fails the pass bar: TRAIN lf_base PF is 1.03.
- If anyone pursues it, it must be pre-registered as a new hypothesis and tested on fresh data. The last hour
  before the 17:00 ET break has a tight LiteFinance spread (about 0.12, SOURCED web_research), which the cost model
  does not credit. That is the only reason to keep it on a list.

---

## 7. Engine notes (no engine file was modified)

- **R-based t-stat ranking.** `sweep._job` ranks by the t-stat of R. With heterogeneous stop distances, a config can
  rank high with PF < 1 and negative $/oz. H5 was not affected much because its stops come from one scale, but H7
  was (see that report). Suggestion: log the t-stat on pnl ($/oz) next to it. `cand/h5h7_stages.py` does this as
  `tr_tp`.
- **A fixed $4 cap in a high-volatility year.** With the flip band enforced (cap = skip), VALID keeps only 18 of
  about 100 trading days, because most 2026 stops are above $4. This is the constraint SYNTHESIS G13 warns about,
  not a bug.
