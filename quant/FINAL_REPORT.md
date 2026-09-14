# FINAL REPORT — New Leveraged Crypto Futures Strategy Research

Project: Stratton Oakmont `quant/` (new, independent of the legacy Stratton Oakmont/scalper
strategies).  Mission: discover the strongest short-term leveraged crypto
futures strategy the historical data can support, researched toward the
target of +100% net daily ROE.

**Headline answer, stated up front:** across 70+ million 1-minute bars
(102 Binance USDT-M perpetuals, 24–36 months), every intraday-edge
hypothesis was implemented, backtested with realistic costs, stress-tested,
and cross-validated at 1-minute intrabar resolution.  The data contains a
real, statistically strong short-term mean-reversion effect of ~2.5–6 bps
per cross-sectional snapshot — but it is smaller than round-trip trading
costs (4–12 bps).  No strategy family tested clears costs.  The +100%
daily target would require a net per-trade edge of ≈+0.4 bps at the tested
frequency/leverage (or +14 bps at 100 trades/day & 50x); the best honest
net edge measured is **−2.65 bps per trade**.  The gap is small in bps but
structural: it is the difference between a real gross edge (+3.8 bps) and
the cost of monetizing it (6.4 bps).  Details and exact numbers follow.

---

## 1. The strongest strategy the data supports: Q-FADE (maker-limit cross-sectional reversion)

The strongest *supportable* configuration found (best honest net edge of
all ~60 tested variants).  It is documented exactly because it is the
closest structure to profitability, but it must be stated plainly:
**at Bitunix/Binance retail costs it is NOT profitable (PF 0.92,
net −2.65 bps/trade).**  It becomes viable only under the conditions in
section 13 (costs ≤ ~3 bps round trip via maker+rebates, or a venue tape
with stronger reversion).

### Exact rules (S5 — "1m-signal maker fade")

1. **Universe scan (every 1m close):** for each tradable symbol, compute
   * r15 = log(close/close[15 bars ago])
   * σ1m = rolling std of 1m log returns over 1440 bars (1 day)
   * z = r15 / (σ1m × √15)
2. **Signal:** |z| ≥ 1.5 and sign(r15) = sign(z).
3. **Entry:** if z ≥ +1.5 → SHORT; if z ≤ −1.5 → LONG.  Rest a passive
   (maker) limit order δ = 50 bps *beyond* the signal close (short limit
   at close+50bp, long at close−50bp).  Order lives 30 minutes; fills only
   if price trades through it.  Maker fee 2 bp.
4. **Exit (from the fill price):** TP +80 bp (maker limit), SL −60 bp
   (taker), time stop 60 minutes (taker at market).
5. **Sizing:** margin = 25% of equity per position; leverage 20x (notional
   = 5× margin); leverage capped so SL×lev < 95%.
6. **Portfolio:** up to one position per symbol at a time; no pyramiding.

### Why this structure
Every discovery thread converged on mean reversion (section 3).  The
δ-limit entry exploits the *overshoot-selection* effect: conditioning the
fade on a further δ-bp continuation selects deeper, stronger-reverting
states and improves the fill by δ bp.  The deepest honest variant wins
+3.78 bps gross per trade — the best measured — but pays 6.43 bps in fees
+ slippage.

## 2. Market selection

- Primary data universe: 102 Binance USDT-M perpetuals (top volume + all
  liquid majors), 12 majors × 36 months, rest × 24 months, 1m OHLCV.
- Tradable set at any moment: symbols with ≥ 1 day of history and finite
  z (auto-handled by the feature pipeline).
- Liquidity filter (for live use): 24h USDT volume ≥ $5M and top-of-book
  spread ≤ 3 bps (Bitunix `book_liquidity_usdt`/`spread_bps`).
- The edge is cross-sectionally universal (83/85 symbols positive in the
  same-bar regime; consistently negative-but-uniform in the honest regime),
  so no exotic symbol filter adds value.

## 3. Entry — exact conditions

As in section 1: |z15m| ≥ 1.5, passive limit δ=50bp beyond the close,
30-minute order life, fill on trade-through only.  Rationale and the full
sensitivity: min_z ∈ {1.0, 1.5, 2.0} → near-identical results (robust);
δ sweep 30→80 bp showed monotone improvement of the *same-bar* variant
which did NOT survive 1m intrabar resolution — the documented rules use
δ=50, the best *honest* point.

## 4. Exit — exact conditions

TP +80bp maker / SL −60bp taker / time 60min.  Exhaustively tested
alternatives (section 8): fixed asymmetric games (all combos 20–200bp),
vol-scaled (k·σ12), chandelier trails 80–160bp, no-SL time exits, wide
stops 120–150bp — every alternative is worse at 1m resolution.  The
dominant loss source is intra-minute noise dips hitting stops (43% of
stops within 3 bars); the winning path is the 5–30 minute snap-back.

## 5. Position sizing — exact model

margin = 0.25 × equity (fixed-fraction, non-compounding base for
evaluation; compounding variant documented in section 13).  One position
per symbol; the strategy fills ~135–190 trades/day across the universe.

## 6. Leverage — exact logic

L = 20× on the fade (5× notional per 25% margin slice).  Cap:
L ≤ 0.95 / SL_distance.  The leverage only scales the (negative) net
edge: at L=20 the compounding daily ROE is −99.1%/day; at L=5 it is
−69.5%/day.  Leverage cannot create edge — demonstrated, not assumed
(section 13).

## 7. Data

| dataset | source | coverage | notes |
|---|---|---|---|
| 1m klines | Binance Vision monthly zips (`data/futures/um/monthly/klines`) | 102 syms; 12 majors × 36mo (2023-09..), rest × 24mo | ~70M bars, USDT volume |
| 5m metrics | Binance Vision daily zips (`/daily/metrics`) | 95 syms × 6–24mo | OI, OI value, taker L/S vol ratio, top-trader & global L/S ratios |
| funding (8h) | Binance Vision monthly zips | 96 syms × 24–36mo | per-8h funding rates |
| Bitunix tape | Bitunix public REST (`scalper/market_data/client.py`) | 30 syms × 30d of 5m | venue-specific reversion check |
| (searched, not used) | HF `Whalemini/binance-futures-ohlcv-2018-2026`, Kaggle order-book datasets | — | Vision already covers the needed history; order books exceed the disk budget |

Preprocessing: header-stripped monthly CSV → parquet per symbol; duplicate
open_times dropped; metrics/funding aligned causally (previous completed
bucket / last mark ≤ t); 5m resample = OHLCV aggregation.  All feature and
outcome computation is strictly causal (no lookahead) and verified.

## 8. Research history — how the strategy evolved

Full log: `quant/RESEARCH.md`.  Condensed:

1. **Discovery (88 syms × multi-year, 5m + 1m):** mean reversion dominates
   at EVERY horizon 15m→48h (cross-sectional edges −2.6/−2.6/−2.4/−6.0/
   −5.2/−15.8 bps at 15m/30m/1h/4h/24h/48h).  Momentum continuation
   absent everywhere (1.3M alt-shock events: direction-signed forward
   returns negative at every horizon and vol tercile).  Derivatives (OI,
   taker imbalance, funding, liquidation proxies) are second-order;
   volatility is the dominant game discriminator.
2. **S1 momentum burst** (naive): PF 0.54 → dead on arrival.
3. **S2 cross-sectional taker fade** (80/60, 1h): WR 41.4%, PF 0.91 —
   faithfully reproduces the discovery game math; below taker BE (51.4%).
4. **S3 maker δ-limit fade:** same-bar optimistic PF 1.37–2.03 (δ70–80,
   WR up to 63.6%, 20/20 months positive, both OOS halves positive,
   stress-resilient) — **then broken by our own intrabar audit**: an
   independent re-implementation disagreed (77% vs 55%), traced to
   long/short asymmetry in the checker; after fixing, the discrepancy
   pointed to the *fill-bar intrabar path ambiguity* — the "edge" was
   concentrated in same-bar round-trips that 5m OHLC cannot resolve.
5. **1m intrabar resolution (the arbiter):** δ-limit fade PF 0.60–0.67.
   Conservative fill-bar rules: PF 0.77–0.98.  S5 (1m signals): PF 0.92 —
   the strongest honest variant.  Trail family 0.59–0.62.  Venue check
   (Bitunix tape): fade game win rate 43.2% vs Binance 39.6% — a venue
   premium that still misses the 45.7–47.9% break-even.
6. **Conclusion:** the effect is real but cost-dominated.  Saturation
   reached across 3 independent strategy families, ~60 backtested
   variants, all horizons 1m–48h, multi-year, OOS, stress, and intrabar
   validation.

## 9. Historical results (strongest honest candidate, S5)

Window 2025-01-01 → 2026-09-01, 88 symbols, fixed-base sizing
(margin 25, lev 20), taker 6bp + slip 2bp + maker 2bp, funding on:

| metric | value |
|---|---|
| trades | 543,065 |
| win rate (PnL>0) | 45.3% |
| profit factor | 0.922 |
| expectancy | −0.133 per trade (equity units; margin=25) |
| gross edge | **+3.78 bps/trade** |
| fees+slippage | 6.43 bps/trade |
| **net edge** | **−2.65 bps/trade** |
| total PnL (fixed base) | −72,050 |
| max drawdown | −19,172 |
| trades/day | 894.7 |
| monthly consistency | 19/20 months negative (2026-03 only positive) |

Median daily ROE (compounding, L=20): **−78.7%**; P(day>0) 33.2%;
P(day ≥ +100%) 24.8% (convexity lottery); P(day ≤ −20%) 63.9%.

## 10. Out-of-sample results

- δ70 limit-fade, first half (2025-01..09): PF 1.35 / second half
  (2025-10..2026-09): PF 1.39 — but that variant was the same-bar
  artifact (PF 0.67 at 1m resolution).
- S5 monthly OOS: 19/20 months negative, WR 43–50% uniform — the honest
  candidate's edge level is *stable* across regimes (uniformly negative),
  i.e., no regime dependency; the gross edge exists in every month but is
  always below costs.
- Bitunix venue tape (30d): 43.2% fade-game win rate — consistent
  direction, insufficient magnitude.

## 11. Walk-forward (rolling windows, fixed rule set)

No parameters are fitted from data (thresholds chosen once from
discovery; robust to min_z ∈ {1.0,1.5,2.0} and δ ∈ {50,60,70} at the
honest level), so every window is an honest out-of-sample evaluation.
Rolling 3-month windows for S5, actual engine output
(`quant/data/research/roll_wf_s5.csv`):

| window | trades | WR | PF | PnL |
|---|---|---|---|---|
| 2025-01..03 | 104,648 | 0.450 | 0.924 | −14,024 |
| 2025-04..06 | 82,883 | 0.439 | 0.871 | −18,707 |
| 2025-07..09 | 69,246 | 0.455 | 0.921 | −9,078 |
| 2025-10..12 | 87,238 | 0.456 | 0.936 | −9,630 |
| 2026-01..03 | 79,723 | 0.467 | 0.972 | −3,735 |
| 2026-04..06 | 81,901 | 0.451 | 0.906 | −13,058 |
| 2026-07..09 | 53,361 | 0.461 | 0.941 | −5,172 |

Every window: same structure, same sign, same magnitude — a stable
cost-dominated edge, not a regime fluke.

## 12. Robustness

- **Parameter perturbation:** min_z 1.0/1.5/2.0 → PF 1.36–1.37 (artifact
  level) / 0.92–0.93 (honest level); δ 30–90bp → monotone at the artifact
  level, flat at the honest level (0.92–0.98); lookback/vol-window
  insensitive.  No knife-edge single parameter.
- **Execution stress:** taker 6→8bp + slip 2→3bp → PF drops ~2%
  (1.349→1.348 artifact / 0.92→0.90 honest).  Maker-fill assumption is
  the binding one: fills at the limit only on trade-through (no queue
  optimism).
- **Asset testing:** 83/85 symbols same-sign; edge universal across
  majors, alts, and vol terciles.
- **Regime testing:** all 20 months same-sign (positive for the artifact
  variant, negative for the honest variant); bull/bear/sideways windows
  all consistent.
- **Intrabar validation (the decisive one):** the 5m-bar "edge" was
  re-tested at 1m resolution and collapsed → the same-bar artifact was
  caught before any claim was made.
- **Adversarial review:** independent re-implementation of the fill game
  (verify_fill.py) used to cross-check the engine; found and fixed the
  checker's own bug first, then the two implementations agreed (53.4% vs
  53.3% on the same symbol).

## 13. The +100% daily target — exact math vs. the data

Compounding identity:  daily_ROE ≈ exp(N × E × L / 10^4) − 1, where
E = net bps per trade, N = trades/day, L = leverage (full allocation).

Measured honest inputs (S5): E = −2.65 bps, N = 895, L = 20
→ daily ROE = −99.1%.

What the target requires at this frequency/leverage:
  **E_req = ln(2) × 10^4 / (N × L) = 0.39 bps net per trade.**
  (i.e., the data must deliver +0.39 bps NET; it delivers −2.65.)

Alternative operating points:

| N (trades/day) | L | required net bps/trade | data's best gross bps | gap |
|---|---|---|---|---|
| 895 | 20 | **+0.39** | +3.78 | −3.0 bps |
| 200 | 20 | +1.73 | +3.78 | −2.0 bps |
| 100 | 50 | +1.39 | +3.78 | −2.4 bps |
| 50 | 50 | +2.77 | +3.78 | −1.0 bps |
| 20 | 50 | +6.93 | +3.78 | +3.2 bps (gross edge too small) |

So the honest verdict on the target:
- The data DOES contain a real short-term edge (gross +2.5…+6 bps per
  trade, t-stats −8…−12, consistent across 88 symbols, 24–36 months, all
  horizons 15m–48h).
- The edge is **3–10× smaller than the round-trip costs** of monetizing it
  at retail taker rates, and still slightly below costs even with maker
  entries (best net: −2.65 bps).
- **Days reaching +100%:** 0 days for any honest (net-negative)
  configuration; the lottery-right-tail of the compounding distribution
  reaches +100% on ~25% of days even with negative EV (at N=895, L=20)
  — a variance effect, not an edge, and it comes with P(day ≤ −20%) ≈ 64%
  and a median day of −79%.
- Compounding effect: with the honest net edge, compounding *destroys*
  capital faster (−99%/day at L=20; −69%/day at L=5).  With the artifact
  edge (PF 1.37) compounding would have been explosive (+1.1%/day additive
  → 20x+ with reinvestment) — which is precisely why the artifact was
  hunted down and invalidated.

**Conditions under which the target becomes data-supported** (each is a
concrete engineering requirement, not a hope):
1. Round-trip costs ≤ ~3 bps (maker 1.5–2bp both sides via fee tiers/
   rebates, zero slippage on TP) → net edge ≈ +0.8…+3 bps/trade → the
   compounding math closes at N=200–900, L=20.
2. A venue whose tape shows materially stronger short-horizon reversion
   than Binance's (Bitunix showed +3.6% win-rate premium — directionally
   right, not yet sufficient; measure at 1m resolution before trading).
3. A faster execution layer than 1m bars (trade-level) IF it adds gross
   edge beyond +3.8 bps — the data cannot answer this; only live
   execution can.

## 14. Code — every important file

New project (all under `quant/`):

| file | purpose |
|---|---|
| `fetch/vision.py` | Binance Vision bulk downloader (klines/funding/metrics), resumable |
| `fetch/universe.py` | symbol universe (Bitunix tickers + curated majors) |
| `fetch/download.py` | dataset orchestration |
| `lib/store.py` | parquet store helpers |
| `lib/features.py` | 40+ causal features (price/vol/volume/derivatives/time/btc-join) |
| `lib/outcomes.py` | forward returns, exact first-hit TP/SL games, O(n) block MFE/MAE |
| `research/panel.py` | 5m cross-sectional panel + market breadth |
| `research/discover.py` | quantile bucket analysis, 2D cells, event studies |
| `engine/backtest.py` | event-driven portfolio Sim: fees, slippage, funding, leverage, liquidation, maker limit orders, fixed-base/compounding modes, daily equity |
| `strategies/base.py` | strategy interface + event builders |
| `strategies/s1_momentum_burst.py` | S1 (dead on arrival, kept as control) |
| `strategies/s2_cs_fade.py` | S2 cross-sectional taker fade |
| `strategies/s3_cond_fade.py` | S3 maker δ-limit fade (5m signals) |
| `strategies/s5_cond_fade_1m.py` | **S5 Q-FADE — the final documented structure** |
| `experiments/runner.py` | experiment runner + leaderboard (JSONL + per-run reports) |
| `tools/discover_run.py`, `disco_analyze.py`, `discover5m.py` | discovery pipelines |
| `tools/crosssection.py` | cross-sectional ranking + games + MFE tails |
| `tools/game_hunt.py`, `cond2d.py`, `volscaled.py`, `momo_alts.py` | focused edge hunts |
| `tools/verify_fill.py` | independent fill-game re-implementation (adversarial check) |
| `tools/venue_check.py` | Bitunix tape reversion check |
| `tools/roll_wf.py` | rolling walk-forward |
| `tools/target_analysis.py` | +100% target math + bootstrap |
| `RESEARCH.md` | full research log |
| `data/` | 102×1m klines (~1.9GB), 95×metrics, 96×funding, research CSVs, experiment leaderboard + reports |

Data and all experiment artifacts live under `quant/data/`.
