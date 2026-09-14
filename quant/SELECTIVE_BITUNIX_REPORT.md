# SELECTIVE_BITUNIX_REPORT.md — Selectivity, Leverage & the +100%/day Question

Project: TBT-Engine `quant/`, second research round.  Question posed:

> "Can a highly selective automated crypto futures strategy achieve
> approximately +100% daily ROE on Bitunix with positive expectancy,
> without lookahead, execution artifacts, or lottery-like variance?"

**Executive verdict: NOT SUPPORTED.**

The selectivity layer was built and tested exactly as specified
(SIGNAL → FILTER → SCORE → SELECT → EXECUTE, event-level feature vectors,
rule-based and learned scores, quantile slices down to 0.5%, leverage
grids 1–50x, allocation grids 10–100%, frequency caps 1–911 trades/day,
10,000-path Monte Carlo, discovery → OOS → holdout discipline, and
1-minute intrabar execution validation).  Result: the underlying gross
edge (+2.0 bps/trade average, best measurable cells +3–4 bps) is smaller
than even the most optimistic execution cost (3.6 bps round trip with
maker + rebates).  **Every one of 288 tested (leverage, allocation,
frequency) operating points has negative median daily ROE.**  The only
+100% days observed (21% of days at 911 trades/day × 20x) come from
variance with negative expectancy and end in ruin — precisely the
lottery the mission instructs us to reject.

---

## 1. What was built this round

| piece | file |
|---|---|
| S6 selective fade (event feature vectors, dynamic δ, confirmation gates, score expressions) | `quant/strategies/s6_selective_fade.py` |
| Engine: signal_t/exit_t in trade records, honest fill-bar rule (TP deferred, SL allowed on the fill bar at 1m resolution) | `quant/engine/backtest.py` |
| Event-level analysis (selectivity curves, learned logistic score, daily-ROE distributions, Monte Carlo) | `quant/tools/event_analysis.py` |
| Operating-point grid (leverage × allocation × frequency) + cost tiers | `quant/tools/op_point.py` |
| Bitunix live execution snapshot (spreads, book depth, fees) | `quant/tools/bitunix_snapshot.py` |
| This report | `quant/SELECTIVE_BITUNIX_REPORT.md` |

## 2. Audit of the inherited Q-FADE (constraint respected)

Reproduced the S5/Q-FADE baseline at 1m resolution with the standardised
honest fill-bar rule: 553,375 trades, WR 44.0%, net **−4.46 bps/trade**,
PF 0.873 (gross +2.02, costs 6.47).  The previously invalidated 5m
same-bar artifact was NOT resurrected; every new test runs on 1m
intrabar fills/exits with the same adversarial rules (TP on the fill bar
deferred, worst-case same-bar ordering, limit fills only on
trade-through).

## 3. Bitunix execution environment (measured live 2026-09-11, snapshot)

| | majors (BTC/ETH/SOL/XRP) | mid alts | illiquid alts |
|---|---|---|---|
| top-of-book spread | 0.01–1.0 bps | 1–6 bps | 9–25 bps |
| book depth (top-5, USD) | $0.7M–$8.2M | $80k–$700k | $4k–$110k |
| fees | maker 2.0 / taker 6.0 bps | same | same |

Cost tiers used everywhere below:
* **optimistic**: 3.6 bps/trade (maker 1.5+1.5, tight-stop slippage 1)
* **base**: 7.7 bps/trade (entry maker 2, TP maker 2, SL taker 6 + 2 slip)
* **pessimistic**: 13.9 bps/trade (SL taker 6 + 12 bps half-spread on
  illiquid alts)

Historical Bitunix book/trade data does not exist publicly — the snapshot
is live-only.  This is an **assumption boundary**, not a historical fact;
it is labeled as such everywhere.

## 4. The selectivity experiment (the core question)

Event definition (S6 base): at each 1m close, |z15m| ≥ 1.5 vol-normalized
15m move; fade direction; passive limit δ=50bp; TP +80/SL −60/time 60m.
Every event carries a causal feature vector (price, volatility, volume,
cross-sectional extremity, derivatives, BTC regime; documented in
`s6_selective_fade.py`).

### Selectivity curve (discovery window 2025-01..08, events sliced by score)

| Selection | Trades/day | WR | Net bps | PF |
|---|---:|---:|---:|---:|
| ALL | 911.7 | 44.0% | −4.46 | 0.873 |
| top 50% (z_time) | 378.9 | 44.0% | −4.22 | 0.881 |
| top 25% | 186.0 | 43.2% | −5.17 | 0.859 |
| top 10% | 94.9 | 41.9% | −6.75 | 0.822 |
| top 5% | 60.8 | 40.7% | −8.35 | 0.785 |
| top 1% | 20.7 | 38.9% | −10.87 | 0.730 |
| top 0.5% | 12.7 | 40.3% | −8.96 | 0.773 |
| top 0.5% (learned logit) | — | 37.2% | −13.26 | 0.680 |

**Selectivity does NOT increase edge — it decreases it.**  The most
extreme events are the ones that CONTINUE (regime breaks), not revert.
The logistic model (fit on discovery only, 24 features, 2.43M events)
learns the same truth: its top slices are the worst.

### Best single-feature cells (discovery, decile edges)

| feature | lo-decile net | hi-decile net | edge |
|---|---:|---:|---:|
| rvol_accel | −2.38 | −5.83 | −3.44 |
| body_frac | −3.41 | −6.58 | −3.17 |
| hi_dist60 | −3.26 | −6.23 | −2.96 |
| wick_up / wick_dn | — | — | +2.06 / +2.08 |
| rv60 | −6.00 | −4.05 | +1.95 |

Best measurable cell anywhere: **−2.4 bps net** (low volume-acceleration
fades).  Exhaustion wicks help (+2 bps) but the confirmation-gate Sim run
(confirm=wick, discovery) still lost: PF 0.841, WR 43.0%.  Break-even
requires 47.9% WR at 80/60 with the base cost split (win +76 bp, loss
−70 bp); the best cells reach ~45%.

### Two tempting artifacts, both rejected by the discipline

* "First trade of the UTC day": +0.18 bps (PF 1.005) on discovery — but
  240/241 of them occur at hour 0 (daily-open session effect) and it
  decays to −12.1/−11.1 bps OOS/holdout.
* "Most-extreme-event-per-day by z_time": median +3.8%/day at 5x on
  discovery, +2.3% OOS, **−3.3% holdout** — monotone regime decay,
  rejected.

## 5. Leverage, allocation, frequency — the full surface

288 operating points (12 leverages × 4 allocations × 6 frequency caps).
**Every point has negative median daily ROE.**  Highlights:

### Leverage curve — ALL trades (alloc 0.25, no cap)

| lev | median daily ROE | mean | P(+100% day) | P(≤−20%) | geo growth |
|---:|---:|---:|---:|---:|---:|
| 1 | −6.0% | −7.3% | 0.0% | 20.8% | −100% |
| 5 | −28.0% | −2.3% | 8.2% | 55.4% | −100% |
| 10 | −50.7% | +127% | 16.5% | 63.4% | −100% |
| 20 | −80.4% | +8,832% | 20.9% | 68.4% | −100% |
| 50 | −99.7% | +1.9×10⁸% | 19.1% | 77.3% | −100% |

(The positive means are the convexity lottery — a few monster days
dragging the average up while the median day loses 80–100%.)

### Leverage curve — top-1-trade/day (alloc 0.25)

| lev | median daily ROE | P(+100%) | geo growth |
|---:|---:|---:|---:|
| 1 | −0.16% | 0% | −10.2% |
| 5 | −0.82% | 0% | −42.7% |
| 20 | −3.30% | 0% | −91.7% |
| 50 | −8.25% | 0% | −99.95% |

### Frequency policy (top-K/day, ALL window, L20/a.25)

| cap | trades/day | median | mean | P(+100%) | P(≤−20%) | P(ruin) |
|---:|---:|---:|---:|---:|---:|---:|
| none | 911.7 | −80.4% | +8,832% | 20.9% | 68.4% | 0.16% |
| 25 | 25 | −5.8% | +0.3% | 1.0% | 23.7% | 0% |
| 10 | 10 | −5.1% | −1.1% | 0% | 6.4% | 0% |
| 5 | 5 | −2.6% | −0.7% | 0% | 0% | 0% |
| 1 | 1 | −3.3% | −0.4% | 0% | 0% | 0% |

**No frequency produces a positive median.**  The best achievable median
is ≈ −0.03%/day (L1, alloc 0.10, 10 trades/day) — flat-minus-costs.

## 6. Monte Carlo (10,000 paths × 30 days)

| config | P(hit +100% ≥ once in 30d) | P(ruin) | P(dd>50%) | median geo |
|---|---:|---:|---:|---:|
| ALL 911/d, L20, a.25 | 99.9% | 4.8% | 99.8% | −100% |
| top-5/d, L20 | 0.0% | 0.0% | 0.0% | −27.7% |
| top-1/d, L20 | 0.0% | 0.0% | 0.0% | −11.7% |

The 99.9% "hit +100%" figure belongs to a strategy whose median path is
ruin.  That is the variance trap, quantified.

## 7. Execution sensitivity (top-1/day, L20, a.25)

| cost tier | cost/trade | median daily ROE | mean |
|---|---:|---:|---:|
| optimistic | 3.6 bps | −3.08% | −0.21% |
| base | 7.7 bps | −3.30% | −0.35% |
| pessimistic | 13.9 bps | −3.59% | −0.72% |

Even with hypothetical maker+rebate costs (3.6 bps), the net edge stays
negative (−1.6 bps/trade on ALL trades): the gross edge (+2.0 bps) is
below the cheapest possible execution.

## 8. OOS and walk-forward

Discipline: discovery 2025-01..08 (all score/selection decisions were
made there), OOS 2025-09..2026-01, untouched holdout 2026-02..09.  The
rule set was frozen before the master run (thresholds from the first
round's discovery; no re-fitting on later data).

Per-quarter walk-forward (fixed rule set, every window independent):

| window | trades | WR | net bps | PF |
|---|---:|---:|---:|---:|
| 2025Q1 | 105,222 | 43.9% | −4.26 | 0.881 |
| 2025Q2 | 85,026 | 43.1% | −5.57 | 0.844 |
| 2025Q3 | 70,537 | 45.0% | −3.33 | 0.901 |
| 2025Q4 | 90,347 | 43.6% | −4.89 | 0.863 |
| 2026Q1 | 82,018 | 45.2% | −3.00 | 0.912 |
| 2026Q2 | 77,840 | 43.6% | −5.19 | 0.852 |
| 2026Q3 | 42,385 | 43.9% | −5.17 | 0.849 |

Stable, uniform, negative — the same cost-dominated structure in every
window and every regime.  No lookahead; no same-bar assumptions (1m
intrabar standard, fill-bar TP deferred); no parameter fitted on OOS
data.

## 9. Best strategy (the honest maximum the data supports)

Exact rules (Q-FADE v2, S6 base) — documented because it is the closest
configuration to profitability; it does NOT meet the success bar:

* **signal**: at each 1m close, z15m = r15m/(σ1m·√15); |z| ≥ 1.5
* **filter/score**: none of the 24 features or their learned combination
  improves the edge (all slices negative) — the honest maximum uses a
  MILD filter only: exclude top-decile rvol_accel and top-decile
  extremity (keeps the −2.4 bps cell) and cap 1–10 trades/day
* **entry**: fade direction; passive maker limit δ=50 bps beyond the
  close, 30-minute life, fill on trade-through only
* **exit**: TP +80 bps (maker), SL −60 bps (taker), time stop 60 min;
  no TP credited on the fill bar
* **leverage**: 1–2x is the only survivable range (anything above is a
  faster bleed); **allocation**: ≤10% of equity as margin;
  **max simultaneous positions**: 1
* **expected trades/day**: 1–10

## 10. Best operating point

**1–2 trades/day, 1–2x leverage, 10% margin** →
net **−2.4 bps/trade** (best cell), median daily ROE **≈ −0.03% to
−0.16%**, P(+100% day) = **0.0%**, P(ruin) ≈ 0%.  This is the honest
maximum: essentially flat, slightly negative, nowhere near the target.

## 11. Selectivity curve / leverage curve / frequency curve

Full tables: sections 4–5 above; complete 288-point grid in
`quant/data/research/op_grid.csv`; event-level curves reproducible via
`quant/tools/event_analysis.py --report a01ab33dd7a7`.

## 12. Failure modes (how this dies live)

1. **Costs**: the edge is ~2 bps gross; any spread/slippage deterioration
   (illiquid alts 9–25 bps spreads) turns −2.4 into −12 bps/trade.
2. **Queue risk**: fills assumed whenever price trades through the limit;
   deep-book queue delays reduce fill rate without improving selection.
3. **Funding**: negligible at <1h holds (measured −0.5 total over the
   sample) but sign risk in extreme-funding regimes.
4. **Regime drift**: the 2026 sample already shows the best cells
   decaying; a trending regime (like 2025Q1→2026Q3's drift in the
   extremes) erodes the fade further.
5. **Liquidation mechanics**: at ≥15x the daily drawdown distribution
   crosses −100% (P(ruin) > 0 at 15x+); Bitunix ADL/mark-price
   mechanics make this worse than modeled.

## 13. Final recommendation

**4. REJECT** — as a +100%-daily-ROE system.  The signal family is real
but sub-cost; selectivity, leverage, allocation, frequency, execution
mode, and score engineering have all been explored without finding a
positive-EV operating point.  (The same code remains a valid research
harness if execution costs ever drop below ~2 bps round trip, or if
historical Bitunix book/trade data becomes available for a
venue-specific microstructure edge.)

## 14. The final question

> "If I give this system real capital on Bitunix, what is the most
> defensible estimate of the probability that it can achieve +100% ROE
> in a day while maintaining positive long-run expectancy?"

**Probability: ≈ 0%.**  Specifically:
* 0.00% of days reached +100% for EVERY configuration with non-negative
  gross expectancy at any leverage; there is no configuration with
  positive long-run expectancy (best: −1.6 bps/trade net even at
  optimistic 3.6 bps costs).
* The only +100% days (up to 20.9% of days) occur at high frequency ×
  high leverage where expectancy is firmly negative (−4.5 bps/trade) and
  median geometric growth is −100% (ruin).

**Confidence level: high** for the Binance-tape conclusion — 553k trades,
88 symbols, 24–36 months, 7 walk-forward windows, OOS/holdout splits,
1m intrabar validation, 288-point operating grid, 10k-path Monte Carlo.

**Assumptions**: Bitunix maker/taker fees 2/6 bps (venue-documented);
limit fills on trade-through without queue delay; spreads per the live
snapshot (0.01–25 bps); Binance futures tape as the historical proxy for
Bitunix price paths.

**Biggest uncertainty**: the Binance-tape proxy.  The venue check (round
1) found Bitunix's own tape shows a +3.6% fade win-rate premium vs
Binance — directionally promising but still below break-even, and only
30 days of data exist.  **What would make the answer knowable:**
(1) ≥ 6 months of historical Bitunix 1m klines + trade-level data (the
API only exposes recent bars — needs to be collected going forward);
(2) logged fill/queue statistics for passive orders at 30–70 bps depth
on the actual venue; (3) a fee tier with ≤ 1.5 bps maker.  With (1)+(2)
a definitive venue-specific answer becomes computable with this exact
harness; without them, the defensible answer remains: **no.**
