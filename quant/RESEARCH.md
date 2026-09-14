# QE-1 Research Log — New Leveraged Crypto Futures Strategy

New independent quant research project (this directory). Target: strongest
short-term crypto futures strategy the data supports; ultimate objective
+100% net daily ROE via leverage. Old TBT/scalper strategies are NOT
reused, modified, or extended — only shared infrastructure (market-data
clients, downloaders) is reused.

## Infrastructure audit (2026-09-11)

| piece | location | status |
|---|---|---|
| Bitunix futures REST client | scalper/market_data/client.py | works (taker 0.06%, maker 0.02%) |
| Binance Vision bulk download | quant/fetch/vision.py (new) | works |
| Data store (parquet) | quant/data/{klines,metrics,funding} | building |
| Backtester (event-driven, fees/slippage/funding/leverage/liq) | quant/engine/backtest.py | built + smoke-tested |
| Discovery (buckets, 2D, event studies) | quant/research/discover.py | built |
| Cross-sectional panel + breadth | quant/research/panel.py | built |
| Experiment runner + leaderboard | quant/experiments/runner.py | built |
| Walk-forward harness | quant/tools/walkforward.py | built |
| VPS | ssh tbt (2 CPU / 3.8 GB / 24 GB free) | reachable; old paper trader running (untouched) |

## Dataset (Phase 2)

Primary source: Binance Vision USDT-M perpetual futures (free, official):
* 1m klines — 102 symbols; 12 majors x 36 months, rest x 24 months
* 5m metrics (OI, taker L/S vol ratio, top-trader L/S, global L/S) — tier A
  x 24 months, tier B x 6 months
* 8h funding rates — 102 symbols x 24+ months
* Liquidation snapshots: NOT on Vision -> proxy features from OI x taker
  (liq_long/liq_short), web search for archives (Whalemini/binance-futures-
  ohlcv-2018-2026 on HF as extended OHLCV option; Kaggle orderbook datasets
  noted but too heavy for disk budget)

Network notes: fapi.binance.com blocked from this network; data.binance.vision
reachable. Universe ranked from Bitunix tickers (our venue) + curated majors.

## Discovery findings so far (log)

Sample: 42 symbols x 6 months (2026-03..09), 1m bars, klines-only features.

1. **Univariate short-horizon mean reversion dominates.** For fwd_5/fwd_15,
   ALL momentum features have negative bucket edge with 1.0 cross-symbol
   consistency: r5 (-1.1 bps), r60 (-1.1), hi_dist240 (-1.2), trend_dist
   (-1.0). Jumpiness (max_abs_r60) positive (+0.7/+1.6). Volume features
   (rvol_s/vpr) raise game hit rates.
2. **Cross-sectional fade of 15m momentum:** top-4 vs bottom-4 -> -3.4 bps
   over next 15m (t=-12.6 top, +7.9 bottom). Edge plateaus ~2.5 bps at
   30m/60m — a fixed quick snap-back, not a growing trend.
3. **TP/SL games on majors:** base win rates g30x20=37%, g30x30=49%,
   g50x30=30%, g80x50=26%. Feature lifts max +0.10..0.14 win prob
   (range_frac, rv5, trend extremes). Cost-adjusted break-even at 12-16 bps
   round trip is 52-58% for 30-50 bps games -> NOT reachable on majors.
4. **Cross-sectional game win rates** (rank extremes, 5m bars, 15m horizon):
   g50x40 43.8-45.4% (BE 57.8%), g80x60 39.2-41.1% (BE 51.4%), g120x80
   31-34% (BE 46%). Still below cost break-even at taker rates; the fade
   edge (~2.5 bps) is 4-6x smaller than taker round-trip costs.
5. **Volume-surge ranking (rvol5): no cross-sectional edge** (top/bottom
   both ~ -0.1 to -0.2 bps at fwd_15).

## Multi-year, full-universe results (88 symbols, 24-36 months)

6. **Reversion at every horizon tested** (15m..24h): cross-sectional
   r15-fade edge -2.6 bps (15m), -2.6 (30m), -2.4 (60m), -6.0 (4h), -5.2
   (24h). NO momentum continuation in this sample at any horizon.
7. **Explosive alt moves do NOT continue** (1.3M events, 20 alts, 1m):
   direction-signed fwd returns after rvol-accel + big 5m move are
   NEGATIVE at 1/3/5/10/15m for every vol tercile (-0.2..-1.7 bps).
   Move-direction games: 33-41% win. Chasing pumps is dead in this data.
8. **Fixed TP/SL fade economics fail everywhere tested:**
   - g80x60 1h: base 39.6%; best 1D conditioner +4.0% (range_frac bin4
     -> 43.0-43.6%); 2D pairs add nothing over 1D. Maker BE (2+8bp split)
     = 47.9%, taker BE = 51.4%. Gap remains ~5%.
   - Vol-scaled games (TP=k_tp*sigma12, SL=k_sl*sigma12): win rates
     12-34% vs BE 35-49%. Fail.
   - Trailing exits (trail 120bp/SL 100bp/4h): WR 33%, PF 0.28. Fail.
9. **Backtested structures:** taker fade 80/60 1h: WR 41.4%, PF 0.91.
   Trail variant: PF 0.28. Maker d=30: PF 0.91. **Maker d=50: PF 1.002,
   WR 48.8% (95,960 trades)** — the overshoot-selection effect: after an
   extreme move AND a further 50bp continuation (limit fill), the
   conditional snap-back win rate rises and the +50bp fill improvement
   shifts the economics to break-even. Trade-level: gross +7.3bp/trade,
   fees ~7.4bp -> net ~0.
10. **Derivatives features are second-order** in the pooled sample: OI
    change, taker imbalance, funding z, liquidation proxies all have
    |edge| < 0.02 for 1h games. Volatility (rv5/rv60/range_frac) is the
    dominant game discriminator: high vol -> lower win rates.

## Current frontier -> FINAL VERDICT (research saturated)

**S3 conditioned fade + maker limit entries** looked like THE candidate
(same-bar 5m numbers: d80 PF 2.03, all months positive) -- then the
intrabar audit broke it (see FINAL_REPORT.md section 8):
* independent re-implementation check (verify_fill.py) exposed the
  fill-bar intrabar path ambiguity,
* 1m intrabar resolution: PF 0.60-0.67,
* conservative fill-bar rules: PF 0.77-0.98,
* S5 (1m signals, maker d=50): PF 0.92 -- the STRONGEST honest variant:
  gross +3.78 bps/trade, costs 6.43 bps, net -2.65 bps/trade,
  19/20 months negative, 7/7 walk-forward windows PF 0.87-0.97,
* trail family (80-160bp chandeliers, no-SL, wide stops): PF 0.59-0.62,
* Bitunix venue tape: fade win rate 43.2% vs Binance 39.6% -- premium
  exists but stays below the 45.7-47.9% break-even.

Verdict: mean reversion is real (2.5-6 bps per snapshot, t=-8..-12,
universal across 88 symbols x 24-36 months x all horizons 15m-48h) but
monetization costs (4-12 bp round trip) exceed it under every honest
execution model.  Continuation does not exist in this dataset at any
horizon (1m..48h).  The +100%/day target requires net E >= +0.39 bps/
trade at N=895, L=20 (or +1.4 bps at N=100, L=50); best honest net is
-2.65 bps.  Required cost regime: <= ~3 bp round trip (maker + rebates).
Full deliverable: FINAL_REPORT.md.

## Cost model (venue: Bitunix)

taker 6 bps/side (12 round trip) + slippage 1-2 bps/side (base),
stress 8 bps taker + 3 bps slip. Maker 2 bps/side. Funding charged at 8h
marks while in position. Leverage capped so SL x lev < 95% (liquidation
explicitly modeled).

## +100% daily target math

daily_ROE ~ exp(N * E * L / 1e4) - 1 (full compounding, E = net bps/trade,
L = leverage, N = trades/day). Need N*E*L ~ 69,300 bps-days.
Examples: E=2, L=50 -> N=690; E=5, L=40 -> N=345; E=15, L=30 -> N=154.
Achieving the target therefore requires either a large edge (multi-hour alt
momentum, E~10-20 bps) or very high frequency at low cost (maker fades at
portfolio scale). Both paths are being researched.
