# htf_breakout: verifier 0 (causality and implementation audit)

Date: 2026-09-30. Adversarial verification of `cand/htf_breakout.py` (with `cand/htf_base.py`) and its report `cand/reports/htf_breakout.md`.

**Finalist:** `{"tf":60,"N":55,"sl":1.5,"ex":"r2","hold":"multi","fresh":0}`. That is an H1 Donchian-55 close breakout, both directions, stop 1.5·ATR14(H1), target 2R, flat after 10 days.

**Lens:** causality and implementation only. The statistical questions (beta, nulls, neighbours) are the hunt agent's and the other verifiers'; I re-ran the pass-bar null only because the bar asks for ≥ 200 seeds.

**Script:** `cand/htf_breakout_verify0.py`, modes `trunc`, `trunc2`, `eval`, `null`, `extra`, `gaps`, `delaydiag`. Outputs are `cand/results/htf_breakout_verify0_<mode>.json` (small, no trade lists).

**Labels:**
- **MEASURED**: computed here, TRAIN/VALID only.
- **SOURCED**: read from the cited file.
- **OPINION**: my judgement.

**Holdout (MEASURED):**
- No order with t ≥ 2026-06-01 was generated or simulated. `htf_base` truncates the M1 frame at the TEST start before any feature is computed.
- All 535 finalist orders have t < TEST and `flat` ≤ 2026-05-31 23:59 UTC. The last simulated exit is 2026-05-29 20:59:50 UTC.
- `final=True` was never used.

**The `features.window_range` fix does not reach this candidate.**
- The module imports only `features.rolling_max_prev` / `rolling_min_prev` and `data.atr` / `resample_causal` / `news_block_mask`.
- The claimed numbers reproduce bit-for-bit with today's `features.py` (section 4).

**Configs:** no parameter search. **1 parameter set simulated**: the finalist.
- Simulation runs of it:
  - 3 costs;
  - 6 execution-delay variants (+10 s, +30 s, +60 s, +300 s, +900 s, and +0 s again);
  - 1 calendar-gate ablation;
  - 1 per-signal diagnostic without the one-at-a-time rule (+0 s and +10 s);
  - 400 null draws.
- Orders only, never simulated:
  - 4 more grid configs for the truncation test. They exercise the `ch`, `don`, `eod`, H4 and `fresh=1` code paths.
  - The injected-leak variant of the finalist.

---

## 0. Verdict

**Not refuted under this lens.** I found no look-ahead and no implementation defect, and every claimed number reproduces exactly. The candidate's own verdict stands: formally "near", **not a pass**, not tradeable as it stands.

| check (all MEASURED) | result |
|---|---|
| Code audit (§1) | No look-ahead pattern. Each order is decided at the close of the last M1 minute of a **completed** H1 bar, so t = bucket end. |
| Truncation invariance: 8 cuts (4 TRAIN, 4 VALID) × 5 configs (§2) | **0 violations**; 12,214 orders compared bit-exactly |
| Targeted truncation: 40 cuts inside finalist signal buckets | **0 violations** on the real module. The same test flags an injected one-bucket leak at **34 of 40** cuts, which is the power check. |
| Future scramble: prices after T mirrored, 3 cuts | 0 changes before T − 5 min; 18-682 order changes after T |
| Independent rebuild: pandas resample, no module code (§3) | 535 of 535 orders reproduce (direction, stop 1.5·ATR_H1, 2R target) from completed buckets. t − bucket end = 0 min for 534 orders and 1 min for 1 (a missing last minute). |
| Signal coverage | 589 module signals = 535 orders + 48 session-gated + 6 news-gated; **0 unexplained** |
| `sweep.evaluate` at lf_base / lf_harsh / mid (§4) | Every claimed n, PF, avg $ and t reproduces **exactly** |
| Naive re-simulation at mid, no `sim.py` code | VALID PF 1.080 against 1.079 (n 70 = 70). TRAIN 1.671 against 1.625 (6 trades differ by path). |
| Order semantics against `sim.py` (§3) | Consistent. All 535 orders fill on the 10-s bar that opens exactly at t. No exit after 2026-05-29. |
| Pass-bar null, 200 seeds (§6) | Random direction p = **0.34**; permutation p = 0.30. **Fails** p ≤ 0.05. |

**Implementation caveats** (MEASURED, §5). They do not change the verdict.

- **One weekend-gap target fill.**
  - VALID PF 1.073 includes one target filled **+$92.14/oz beyond the level**: a long, at the 2026-03-01 Sunday reopen, after a +$97 gap. `sim` fills a gapped target at the open, which is the market convention for a resting limit order. Whether LiteFinance passes on positive gap slippage is unconfirmed (OPINION).
  - With that fill capped at the target, **VALID PF is 1.03**, below the 1.05 "near" line.
  - Four VALID stops gapped the other way (2 weekend reopens, 1 daily break, and one ordinary $0.54 jump between 10-s bars), costing $102.67 beyond their levels. With both kinds of gap removed, VALID PF is 1.082.
  - Every material gap fill (more than $1 beyond the level) is a real market gap at a weekend reopen or the daily break. **None is a hole in the tick data.**
- **Path sensitivity.**
  - Pure execution delays of +10 s to +15 min move VALID PF between 1.07 and 1.24, and TRAIN between 1.37 and 1.75.
  - Simulated per signal (no one-at-a-time rule), a 10-s delay changes almost nothing: TRAIN 1.50 → 1.48, VALID 1.269 → 1.270, with the entry $0.11/oz worse on average.
  - So the swings come from the one-position-at-a-time sequence reshuffling about 10 trades, not from timing information.
  - OPINION: the VALID PF of 1.07 (n 70) sits in a noise band about ±0.15 wide.

---

## 1. Code audit, line by line

- **Holdout cut.** `htf_base.base` cuts the frame at the TEST start before computing anything. `gate` uses only the bar's own UTC minute and weekday plus the calendar mask. `eod_ms` uses the bar's own New York minute and is not used for `hold=multi`.
- **When an H1 bar is known.** `data.resample_causal` builds UTC-aligned buckets. A bucket is complete at the close of its last minute, or one M1 bar later when that minute is missing. `htf_base.htf` sets `ki[k]` to the first M1 index at which bucket k is complete, and the order time is t = ts[ki[k]] + 60 s. So t equals the bucket end, or the bucket end plus 1 minute when the last minute is missing.
- **Signal.** `rolling_max_prev(H, 55)` is `shift(1).rolling(55).max()`: the 55 bars *before* k. The signal is C[k] > that value, and the mirror for shorts. `fresh` would use `up[k-1]`; it is not used by the finalist.
- **Stop distance.** ATR14 on H1 bars is a Wilder EWM over bars ≤ k.
- **`don` exit.** `_next_true_after` looks forward, but only to schedule an exit at the close of the later exit bar m, at ts[ki[m]] + 60 s, which is when a live system would act. It is an exit time, not an entry feature, and the finalist (`r2`) does not use it.
- **Grep of the module** (MEASURED): no `shift(-k)`, `bfill`, centered window, percentile, scaler, fitted model or whole-sample statistic. The only `[-1]` is in the cache key.
- **Calendar gate, the only external input.**
  - Impact is assigned by event type from official release schedules, not by the realised move (SOURCED `news/build_calendar.py` l.99-181). Only MEDIUM claims rows are filtered on "has an actual value", and the gate uses HIGH rows only.
  - Ablation (MEASURED): removing the gate adds 6 orders (541 against 535). TRAIN goes 1.542 → 1.516 (n 164 → 166). VALID is identical: PF 1.073, n 70.
- **Hygiene note** (OPINION: low risk, no effect on any reported number):
  - `htf_base.base` caches on `(len, first ts, last ts)`. A same-length frame with different prices, such as a perturbed copy, silently gets the stale cached features.
  - The pipeline never does this: it uses `load_m1()` or truncated copies.
  - Any future perturbation test must call `htf_base._C.clear()`. The scramble test in section 2 does.

---

## 2. Truncation invariance and leak detection (MEASURED)

**Test.** `orders(m1[m1.ts < T])` against `orders(m1)`, both filtered to t ≤ T − 5 min. Orders are matched on (t, d). Every field (t, d, kind, sl_dist, tp_dist, trail, trail_act, flat, tag) is compared **bit-exactly**; there is no tolerance, because a causal computation on a prefix must give identical floats.

**Cuts.** 4 in TRAIN and 4 in VALID:
- 2025-04-09 13:37:21.5;
- 2025-07-23 08:05;
- 2025-09-17 14:00 (on an H1 boundary);
- 2025-11-14 19:59:59.999;
- 2026-01-29 15:22:10 (the spike);
- 2026-03-04 12:01 (1 minute after an H4 boundary);
- 2026-03-18 18:00:00.001;
- 2026-05-20 10:11:12.

Mid-hour cuts leave a partial H1/H4 bucket at the end of the cut frame.

| config | cuts | orders compared | violations |
|---|---|---|---|
| **finalist** H1 N55 sl1.5 r2 multi | 8 | 2,799 | **0** |
| H1 N55 sl1.5 ch2 eod | 8 | 2,724 | 0 |
| H1 N34 sl3.0 don multi | 8 | 3,448 | 0 (34 `don` exit times fall after the cut, as expected) |
| H4 N20 sl3.0 ch3.5 eod | 8 | 1,304 | 0 |
| H1 N45 sl1.0 ch2 multi fresh=1 | 8 | 1,939 | 0 |
| **total** | 40 | **12,214** | **0** |

**Positive control, random cuts** (`trunc`):
- I injected a leak into `htf_base` inside the verifier process only: every HTF bucket counts as "known" at its **first** minute, so the decision sees the bucket's next 59 minutes.
- The same harness flagged it at only **1 of 30** random cuts.
- The reason: a random cut exposes only the one partial bucket at the cut, and the finalist signals on 7.3% of H1 bars (589 of 8,026 before the TEST cut). So random cuts alone have low power against a short-horizon leak.

**Targeted truncation** (`trunc2`):
- 40 cuts placed 37 min 21 s into H1 buckets that carry a finalist signal: 20 in TRAIN and 20 in VALID, chosen at random with seed 31.

| module | cuts | orders compared | cuts with a violation |
|---|---|---|---|
| real `htf_breakout` | 40 | 13,210 | **0** |
| injected leak (bucket known at its first minute) | 40 | 12,993 | **34** |

- Where a leak would show, the test catches it 85% of the time. The real module passes all 40 cuts.
- The truncation tests use a 5-minute buffer, so they cannot see a leak of under 5 minutes. The rebuild in §3 closes that gap: each order's t is exactly the end of the bucket its signal comes from.

**Future scramble** (finalist, 3 cuts):
- Prices after T were replaced by the mirrored path 2·c_T − price, with high and low swapped. The frame length is unchanged and the cache was cleared.
- Orders before T − 5 min: 0 added, 0 removed, 0 field changes, with 195, 439 and 527 orders compared.
- Orders after T changed: 682, 192 and 18 order times. So the scramble did bite.

---

## 3. Independent rebuild and order semantics (MEASURED)

**Rebuild.** H1 bars were rebuilt from raw M1 mid with `pandas.resample("60min")`, using no `htf_base` or `resample_causal` code. Each module order was mapped to the last bucket that **ended** at or before its t.

| check | result |
|---|---|
| order t − bucket end | 0 min for 534 of 535. 1 min for 1: that bucket's last M1 minute is missing, so the module correctly waits for the next bar. |
| direction confirmed by the rebuilt Donchian-55 close signal of that completed bucket | 535 / 535 |
| sl_dist = 1.5 × rebuilt ATR14(H1) of that bucket (relative error 1e-9) | 535 / 535 |
| tp_dist = 2 × sl_dist | 535 / 535 |

A module that used the forming bucket, or any later bar, would fail the direction and ATR rows: t would precede the end of the bucket it used.

**Signal coverage** (`trunc2`):
- The module's own signal array has 589 H1 closes beyond the Donchian-55 channel:
  - 535 became orders;
  - 48 fell in the session gate (20:30-23:30 UTC, Friday after 19:00 UTC, Sunday before 23:30 UTC);
  - 6 fell in the news gate ([−5, +15] min around HIGH rows);
  - 0 were warm-up, and **0 are unexplained**.
- This matches the rebuild's 589 signal times. The rebuild's 55 signal times with no order at that time are the 54 gated signals plus the 1 order delayed by a minute.
- So no signal is dropped by anything that could see the future.

**Order semantics against `sim.py`** (535 finalist orders):
- **Timing.** Every t is minute-aligned (a bucket end). Every order fills on the 10-s bar that opens exactly at t: the fill delay is 0 s for all 234 trades, so the fill is the first tick at or after the decision.
- **Order fields.** `kind` is `mkt` for all. There is no absolute sl/tp/px and no trail or break-even, so the direction flip in `nulltest` is exact. `sl_dist` is between 6.93 and 182.75, with none ≤ 0 or NaN. `tp_dist` = 2·`sl_dist` for all.
- **Flat time.** `flat` > t, and `flat` = min(t + 10 d, 2026-05-31 23:59) for all.
- **Trade table** (lf_base):
  - `risk` equals the order's `sl_dist`, with a maximum error of 4.5e-13.
  - 0 overlapping trades (one position at a time).
  - Exit reasons: 134 SL and 100 TP. There are **0 time exits**, so the 10-day cap and the holdout flatten never bind.
  - 0 TRAIN trades exit inside VALID.
  - 0 flip-eligible trades (stops of $1.2-4.0).
- **Conventions**, consistent with the module docstring. The stop and target are measured from the fill price, which includes spread and slippage, and they trigger on the exit side. SL has priority in the same bar. The entry bar cannot take profit.

**Naive re-simulation.** A 40-line Python loop over raw S10 mid (entry at the mid open, SL/TP on mid high/low, the same priority and entry-bar rules), sharing no `sim.py` code. It agrees with `sim` at `mid` cost:

| split | naive n, PF, avg $ | sim mid n, PF, avg $ |
|---|---|---|
| TRAIN | 165, 1.671, +6.71 | 163, 1.625, +6.25 |
| VALID | 70, 1.080, +2.41 | 70, 1.079, +2.39 |

- 231 trades match. Of these, 231 of 231 have the same exit reason, the median |Δpnl| is 0.000 and the maximum is 0.57.
- 4 + 2 unmatched TRAIN trades come from path divergence. The two differ only in how the spread is removed at the bar extremes: `sim` uses the bar-average spread, the loop uses (ask + bid)/2 per field.

---

## 4. Re-run of the claimed numbers (MEASURED, `sweep.evaluate`)

| cost | split | claimed n / PF / avg $ | reproduced n / PF / avg $ | t (R) |
|---|---|---|---|---|
| lf_base | TRAIN | 164 / 1.542 / +5.546 | **164 / 1.542 / +5.546** | 2.82 (claimed 2.82) |
| lf_base | VALID | 70 / 1.073 / +2.219 | **70 / 1.073 / +2.219** | 0.74 (claimed 0.74) |
| lf_harsh | TRAIN | 165 / 1.379 / +4.105 | 165 / 1.379 / +4.105 | 2.18 |
| lf_harsh | VALID | 70 / 1.063 / +1.929 | **70 / 1.063 / +1.929** | 0.70 |
| mid | TRAIN | 163 / 1.625 / +6.248 | 163 / 1.625 / +6.248 | 3.25 |
| mid | VALID | 70 / 1.079 / +2.389 | **70 / 1.079 / +2.389** | 0.77 |

Also reproduced exactly:
- TRAIN halves at lf_base: PF 1.437 / 1.630.
- Long/short at lf_base:
  - TRAIN: long 119, PF 2.280; short 45, PF 0.664.
  - VALID: long 40, PF 0.897; short 30, PF 1.266.
- lf_harsh TRAIN halves: 1.213 / 1.525.

---

## 5. Execution sensitivity (MEASURED; a robustness check, not a causality test)

The same 535 orders were executed later, with no change in information, at lf_base.

| entry delay | TRAIN n / PF / avg $ | VALID n / PF / avg $ |
|---|---|---|
| +0 s (as reported) | 164 / 1.542 / +5.55 | 70 / 1.073 / +2.22 |
| +10 s (the next 10-s bar; covers 1-5 s broker latency) | 169 / 1.366 / +3.92 | 69 / 1.097 / +2.92 |
| +30 s | 168 / 1.506 / +5.25 | 69 / 1.147 / +4.36 |
| +60 s | 163 / 1.462 / +4.88 | 68 / 1.138 / +4.14 |
| +300 s | 169 / 1.467 / +4.98 | 69 / 1.122 / +3.64 |
| +900 s | 162 / 1.748 / +7.39 | 72 / 1.242 / +6.74 |

- A timing leak would show as a collapse under a small delay. **There is none.** TRAIN stays at 1.37-1.75 and VALID at 1.07-1.24, and neither moves monotonically with the delay.
- **Why +10 s moves TRAIN from 1.54 to 1.37** (`delaydiag` mode, MEASURED):
  - With every one of the 535 signals simulated as its own trade (no one-at-a-time rule), a 10-s delay makes the entry $0.106/oz worse on average (median $0.02, p10/p90 −1.07/+1.53).
  - Per-signal PF barely moves: TRAIN 1.501 → 1.479 (n 398); VALID 1.269 → 1.270 (n 137).
  - In the one-at-a-time sequence, 225 of 234 trades share their signal. The swing comes from the roughly 10 trades that change.
  - OPINION: the reported VALID PF of 1.07 (n 70) is one draw from a band roughly ±0.15 wide. The "near" label (≥ 1.05) holds for 6 of 6 delays, but it is not precise evidence.
- The per-signal version (overlapping positions) is only a diagnostic. It is **not** a proposed config: it was looked at on VALID.

**Gap fills beyond SL/TP** (`gaps` mode, lf_base, MEASURED). Every fill that landed beyond its level by more than $1:

| split | side, exit | entry | exit bar (UTC) | previous 10-s bar | jump (mid) | beyond level |
|---|---|---|---|---|---|---|
| TRAIN | short, TP | 2025-07-25 11:00 | Sun 2025-07-27 22:00 | Fri 20:59:50 (weekend) | −19.24 | +3.87 |
| VALID | short, SL | 2026-01-16 16:00 | Sun 2026-01-18 23:00 | Fri 21:59:50 (weekend) | +31.35 | −27.51 |
| VALID | **long, TP** | 2026-02-27 14:00 | Sun 2026-03-01 23:00:10 | Fri 21:59:50 (weekend) | +97.23 | **+92.14** |
| VALID | long, SL | 2026-04-17 13:00 | Sun 2026-04-19 22:00 | Fri 20:59:50 (weekend) | −62.36 | −51.50 |
| VALID | short, SL | 2026-05-26 18:00 | Tue 2026-05-26 22:00 | 20:59:50 (daily break) | +31.27 | −23.11 |

All five are real market gaps, at a weekend reopen or across the daily break. None is a hole in the Dukascopy data: there are 0 M1 bars inside four of the gaps and 1 at the reopen minute of the fifth. `sim` fills both sides at the reopen price, which is realistic.

| split | PF as simulated | TP gaps capped at the target | SL gaps at the stop | both removed |
|---|---|---|---|---|
| TRAIN | 1.542 | 1.540 | 1.542 | 1.540 |
| VALID | 1.073 | **1.030** | 1.128 | 1.082 |

- **Gap exposure** (MEASURED): the multi-day hold carries weekend-gap risk in both directions. In VALID, gap effects netted −$10.5/oz: one target +92.14 against four stops −102.67. One of those stops is the $0.54 jump, below this table's $1 threshold.
- The "near" label depends on how the broker fills **one** weekend-gap target: +$92 of the VALID net of +$155/oz.
- If LiteFinance does not pass on positive gap slippage, VALID PF is 1.03, which is a fail. This is unconfirmed: no LiteFinance fill has ever been logged (SOURCED SYNTHESIS G1).

**Other implementation facts** (MEASURED):
- 0 orders were dropped as stale: every order had a 10-s bar exactly at t.
- 0 exits by time.
- Swap is not in `sim`. The hunt agent priced it separately: VALID PF 1.062 with swap (SOURCED report §5).

---

## 6. Pass-bar null (MEASURED, `nulltest.random_direction`, lf_base, VALID)

I ran the library function unchanged: `random_direction(orders, seeds=range(200), cost="base", split=1)`. It simulates all 535 TRAIN+VALID orders with random directions and scores the VALID trades. p = (#null ≥ real + 1) / 201.

| null | real VALID | null mean (sd) of avg $/oz | p on avg $ | p on PF |
|---|---|---|---|---|
| random direction, 200 seeds | avg +2.22, PF 1.073, n 70 | −1.69 (7.63) | **0.343** | **0.348** |
| permutation of the strategy's own directions (keeps its 75% long share), 200 seeds | same | −1.93 (6.82) | **0.303** | **0.303** |

- The pass bar needs p ≤ 0.05. **It fails.**
- This is consistent with the hunt agent's figures: p = 0.27 with 50 seeds, from a different seed set and VALID-only orders (SOURCED report §4).

---

## 7. What this does and does not establish (OPINION)

- **Established.** This is a genuinely causal, correctly simulated H1 trend follower. No leak explains its TRAIN PF of 1.54 or its VALID PF of 1.07. The hunt agent's numbers are exact, and its "near" label is mechanically correct: VALID PF 1.073 ≥ 1.05 and TRAIN PF 1.542 ≥ 1.10.
- **Not established: an edge.** Causality is necessary, not sufficient. The candidate still fails the pass bar:
  - VALID PF is 1.073, against ≥ 1.15 required.
  - Random direction on VALID gives p = 0.34 with 200 seeds (permutation p = 0.30), against ≤ 0.05 required.
  - The "near" margin rests on one weekend-gap target fill; capped at the target, VALID PF is 1.03.
  - VALID PF with the best month dropped is 0.84 (SOURCED report §3a).
  - The TRAIN edge does not beat a permutation null that keeps the long share: p = 0.25 (SOURCED report §4).
- **Not a flip candidate.** Stops run $6.9-183/oz, so 0 trades fall in [1.2, 4.0].
- **Recommendation.** Do not spend one of the two TEST slots on it. If trend-following is pursued for the ≥ $100 phase, the question is beta against skill. It needs a longer history that includes a gold bear or range regime, and it cannot be settled by more checks of causality.

---

## 8. Reproduction

Single process each, TRAIN/VALID only, at most 2 at a time:

```
cd xau_alpha
python3 cand/htf_breakout_verify0.py trunc    # 8 cuts x 5 configs, random-cut positive control, future scramble, rebuild, semantics (~3 min)
python3 cand/htf_breakout_verify0.py trunc2   # 40 targeted cuts in signal buckets: real module vs injected leak; signal coverage
python3 cand/htf_breakout_verify0.py eval     # sweep.evaluate x3 costs vs claims, trade-level, naive mid resim, delays, gate ablation (~2 min)
python3 cand/htf_breakout_verify0.py null     # nulltest.random_direction 200 seeds + 200-seed permutation null, VALID
python3 cand/htf_breakout_verify0.py extra    # +10 s / +30 s delays, stale-order count, gap-fill contribution
python3 cand/htf_breakout_verify0.py gaps     # every SL/TP gap fill: market gap or data hole; PF with gaps removed
python3 cand/htf_breakout_verify0.py delaydiag  # per-signal (no one-at-a-time) +0 s vs +10 s: timing vs path
```

Nothing outside `xau_alpha/` and no `lib/*.py` file was modified. The monkeypatches for the injected leak and the gate ablation live only inside the verifier process.
