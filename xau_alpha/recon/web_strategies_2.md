# XAUUSD strategy research, round 2: order flow, volatility regime, event/calendar, small-account growth

Written 2026-10-01 on branch `for-dear-opus-5.5`. This is the only repo file created or changed. Scratch scripts and downloads are in
the session scratchpad (`.../scratchpad/web2/`), outside the repo.

Labels: **SOURCED** = from the cited URL or abstract. **MEASURED** = I computed it here; the method is stated. **OPINION** = my judgement.
**(memory)** = a well-known result I did not re-retrieve this session. Treat it as unverified until someone opens the paper.

Credibility scale: **HIGH** = top journal or large multi-asset sample, replicated. **MEDIUM** = peer-reviewed but single study, or a different asset.
**LOW** = working paper, practitioner blog, or small sample. **MARKETING** = vendor, YouTube or auto-generated content with no testable method.

**How the research was done.** The built-in WebSearch and WebFetch failed all session with a model error (same as round 1). I used the
OpenAlex, Semantic Scholar and arXiv APIs, plain HTTPS fetches of open PDFs (Warwick WRAP, arXiv), WordPress APIs (Robot Wealth, Quantocracy),
and the public Binance S3 listing. **Blocked:** SSRN (Cloudflare challenge; I did not try to get around it), Wiley direct PDFs, the Quantpedia API (403),
Alpha Architect (403), and Robot Wealth Pro posts (paywall). Where only a title was reachable, this note says so.

**Holdout disclosure.** The team keeps Jun-Sep 2026 as TEST. One exploratory run in §3.1 printed full-sample and quarterly FX-fix-window
statistics that include Jun-Sep 2026. Nothing was tuned, and that hypothesis is rejected on TRAIN+VALID anyway. Every other measurement here
uses TRAIN (2025-01-21..2025-12-31) and VALID (2026-01-01..2026-05-31) only.

---

## 0. Bottom line

1. **Only the Binance perp order-flow (OFI) signal deserves more work.** There is exactly one paper on this instrument: *Funding Rates and the Conditional Informativeness of Order
   Flow: Evidence from the Binance XAUUSDT Gold Perpetual* (SSRN 6872638, 2026). I could reach its title only, because SSRN is blocked. The title implies the signal's
   information content depends on the funding regime, which is directly testable with public Binance files (§1.1).
2. **The OFI sample can be roughly doubled on an untouched period.** Binance publishes **PAXGUSDT perp 1m klines with taker-buy volume from 2025-03-27** and PAXGUSDT spot from 2020-08.
   Dukascopy gold covers 2025, the TRAIN split, which OFI has never touched. This is the single most informative next test (§7, rank 1). MEASURED from the S3 listing.
3. **XAUUSDT funding was large and variable only in Dec 2025-Mar 2026** (max +0.50% per 4 h, min −0.34%). By Jul-Aug 2026, 65-78% of 4 h prints were exactly 0.
   A funding-conditioned rule therefore has almost no variation after April. Use the continuous **premium index** (1m) instead. MEASURED (§1.1).
4. **Order-flow predictability decays fast everywhere it has been measured.** Flow and price move together (Cont-Kukanov-Stoikov). Lagged cross-asset OFI adds forecast power only at short horizons and decays
   quickly (Cont-Cucuringu-Zhang 2023). For gold, price discovery sits in COMEX futures, not in crypto venues. OPINION: expect the XAUUSDT OFI effect to be small and fragile, which is what the team measured.
   The right tests are replication on independent data and conditioning, not parameter search.
5. **Short-term trend following is structurally dead on small-tick futures such as gold since about 2009.** Kurth, Eisler, Rej and Bouchaud (2026, about 100 futures, 1995-2025) find this. It explains the failed
   Donchian, ORB, trend-pullback and COMEX-momentum tests. Credibility HIGH. Stop testing short-horizon trend variants on gold.
6. **Volatility targeting does not raise Sharpe for commodities.** It only cuts tails (Harvey et al. 2018). In commodity futures it failed out of sample (Kang-Kwon 2020). Use volatility only as a
   **trade/skip filter chosen to raise μ/σ²**, the quantity that decides whether a $13 account reaches $450 (§4).
7. **The published calendar and event anomalies do not fit a $13, 1-oz account.**
   - They are too rare to validate in 20 months: FOMC, month-end and options expiry.
   - They play out over a daily horizon, where one 1-oz position has a standard deviation around $50: day-of-week and seasonal effects.
   - Or they fall inside the broker's 1:200 news-margin window, which a <$21 account cannot trade: pre-announcement drift.
   - The pre-FOMC equity drift disappeared after 2015 (Kurov-Wolfe-Gilbert 2020).
8. **FX-fix W-pattern (Krohn-Mueller-Whelan, JF 2024) tested on our gold data: rejected.** Gold does not mirror the dollar's pre-fix and post-fix reversal (Tokyo reversal −$5.85/day). The
   "Asia reopen" drift is real at mid prices (VALID +$12/day, t 3.5), but it sits in the first 5 minutes, when the spread is $1.0-1.4. At a tradeable entry it is insignificant in both
   splits. MEASURED (§3.1-3.2).
9. **Small-account math.**
   - With no edge, no sizing or payoff shape beats about 1-3% for $13 to $450 (optional stopping).
   - With an edge, the probability of reaching the goal is set by κ = 2μ/σ² per trade, while equity is near the $8.3 margin barrier (Pestien-Sudderth 1985).
   - At the OFI's measured +$0.25-0.42/oz net, with a trade σ of about $4, P($13 to $450) is about 20-33%. At σ ≈ $2.5 it is about 62%.
   - Fixed 1 oz and "1 oz then 10% fractional" give the same P, but fractional scaling gets there 2-3.5x faster. Bold play cuts P by 3-6x.
   - Low-hit-rate convex payoffs are worse near the barrier. MEASURED Monte Carlo (§4.2).

---

## 1. Order flow and microstructure (priority 1)

| # | Item | Claim | Horizon | Effect size | Sample | Source | Cred. |
|---|---|---|---|---|---|---|---|
| 1.1 | XAUUSDT perp funding × flow | Order flow informativeness conditional on funding | intraday (title) | n/a (title only) | Binance XAUUSDT, 2025-26 | [SSRN 6872638](https://doi.org/10.2139/ssrn.6872638) | LOW (unread working paper) |
| 1.2 | OFI → price | Price changes over short intervals are linear in OFI; the relation is **contemporaneous** | seconds-minutes | slope ∝ 1/depth | 50 NYSE stocks | Cont, Kukanov, Stoikov (2014) *JFEc* [doi](https://doi.org/10.1093/jjfinec/nbt003), [arXiv](https://arxiv.org/abs/1011.6402) | HIGH |
| 1.3 | Lagged cross-asset OFI | Multi-level OFI; lagged **cross-asset** OFI improves return forecasts at short horizons and decays rapidly | minutes | small R² gains | Nasdaq-100 stocks | Cont, Cucuringu, Zhang (2023) *QF* [arXiv 2112.13213](https://arxiv.org/abs/2112.13213) | HIGH |
| 1.4 | Order imbalance → returns | Daily returns are not serially dependent, but order imbalances are persistent; sophisticated traders act on imbalances **within the day** | 5-60 min | predictability confined to the first intraday intervals (memory; full text not retrieved) | NYSE | Chordia, Roll, Subrahmanyam (2005) *JFE* [doi](https://doi.org/10.1016/j.jfineco.2004.06.004); Chordia-Subrahmanyam (2004) *JFE* [doi](https://doi.org/10.1016/s0304-405x(03)00175-2) | HIGH |
| 1.5 | FX order flow | Daily interdealer flow explains more than 60% of DEM/USD daily changes (contemporaneous). Customer flow forecasts FX out of sample over 1 day to 1 month (16% of monthly variance) | 1 d-1 m | R² 0.6 (contemp.) | 1996, 2002-05 | Evans-Lyons (2002) *JPE* [doi](https://doi.org/10.1086/324391); Evans-Lyons (2005) *AER* [doi](https://doi.org/10.1257/000282805774669934) | HIGH (data not available to us) |
| 1.6 | Crypto venue flow | "World order flow" has explanatory and **out-of-sample predictive** power for crypto returns, beyond fundamentals | daily | OOS gains, esp. ML | crypto cross-section | Anastasopoulos, Gradojević, Liu, Maynard, Tsiakas (2026) *J. Fin. Markets* [doi](https://doi.org/10.1016/j.finmar.2026.101047) | MEDIUM |
| 1.7 | Crypto perps lead spot | BitMEX perps lead spot BTC prices (1-min data) | minutes | information share | BTC 2016-18 | Alexander, Choi, Park, Sohn (2020) *JFM* [doi](https://doi.org/10.1002/fut.22050); Alexander-Heck (2020) *JFS* [doi](https://doi.org/10.1016/j.jfs.2020.100776) | HIGH for BTC; **not** for gold |
| 1.8 | Gold price discovery | Gold futures lead spot and ETF intraday; leadership varies with liquidity, jumps and attention | intraday | network shares | 2010-2018 | Sehgal, Sobti, Diesting (2021) *JFM* [doi](https://doi.org/10.1002/fut.22208); Hauptfleisch, Putniņš, Lucey (2016) *JFM* | HIGH |
| 1.9 | 15-min reversal, crypto vs metals | Crypto: 90% of 183 Binance pairs show 15-min sign reversal, concentrated **after aggressive-taker moves**, gross ~1.3 bp vs 5 bp cost. **Spot metals: AUC 0.513-0.522 on 24h bars; XAUUSD null (0.503) on US-hours slots** | 15 min | too small after cost | Binance 2021-26, Dukascopy metals | Kitron, Wengrowicz (2026) [arXiv 2608.21888](https://arxiv.org/abs/2608.21888) | MEDIUM (preprint, careful OOS design) |
| 1.10 | Crypto carry / funding | Futures-spot carry sometimes exceeds 40%/yr. It reflects **trend-chasing small investors seeking leverage** plus limited arbitrage capital | days-weeks | n/a | crypto 2018-2024 | Schmeling, Schrimpf, Todorov (2026) *Mgmt Sci* [doi](https://doi.org/10.1287/mnsc.2024.05069) | HIGH |
| 1.11 | Perp-spot deviations | Perp deviations are larger than in FX, **comove across coins** (sentiment) and shrink over time; the implied arbitrage has a high Sharpe | days | n/a | crypto | He, Manela, Ross, von Wachter (2022-) [arXiv 2212.06888](https://arxiv.org/abs/2212.06888); Ackerer, Hugonnier, Jermann (2024) [NBER w32936](https://doi.org/10.3386/w32936) | HIGH (theory + evidence) |
| 1.12 | VPIN (toxicity) | Volume-bucket buy/sell imbalance measures toxicity and flagged stress before the 2010 flash crash | volume time | n/a | E-mini etc. | Easley, López de Prado, O'Hara (2012) *RFS* [doi](https://doi.org/10.1093/rfs/hhs053) | contested → see next row |
| 1.13 | VPIN critique | VPIN's apparent predictive power is mechanical, from trading intensity and volume, and it did not lead the flash crash (memory; abstract not retrieved) | - | - | E-mini | Andersen, Bondarenko (2014) *JFinMkts* [doi](https://doi.org/10.1016/j.finmar.2013.05.005) | HIGH as a caution |
| 1.14 | Trade-sign long memory | Market-order signs are persistent (power-law ACF) in stocks and FX, so predictable flow is already priced; only **unexpected** flow moves price | ticks-hours | ACF ~ τ^−γ | LSE, FX EBS | Lillo-Farmer (2004) (memory); Sato-Kanazawa (2023) *PRL* [doi](https://doi.org/10.1103/physrevlett.131.197401); Gould-Porter-Howison (2016) FX [arXiv 1504.04354](https://arxiv.org/abs/1504.04354) | HIGH |
| 1.15 | Death of short-term trend | Since ~2009, short-term trend P&L collapsed on **small-tick** futures at all signal speeds but survives on large-tick ones. The volatility-normalised tick size is what matters | hours-weeks | P&L collapse | ~100 futures 1995-2025 | Kurth, Eisler, Rej, Bouchaud (2026) [arXiv 2607.01550](https://arxiv.org/abs/2607.01550) | HIGH (CFM authors, large panel) |
| 1.16 | Binance perp LOB features | Order-book and trade features have stable SHAP importance across coins; taker vs maker backtests diverge in a flash crash | 1 s | tradable maker variant | Binance perps 2022-25 | Bieganowski, Ślepaczuk (2026) [arXiv 2602.00776](https://arxiv.org/abs/2602.00776) | MEDIUM |

### 1.1 What is actually available for XAUUSDT and PAXGUSDT (MEASURED from `data.binance.vision` S3 listing and file headers, 2026-10-01)

| Dataset (Binance UM futures unless noted) | From | Granularity | Columns of interest |
|---|---|---|---|
| XAUUSDT klines 1m (already in `data/xa/`) | 2025-12-11 | 1 min | `volume`, `taker_buy_volume` |
| XAUUSDT `fundingRate` (monthly) | 2025-12 | 4 h (8 h on some Jan days) | `last_funding_rate`, `funding_interval_hours` |
| XAUUSDT `premiumIndexKlines/1m` | 2025-12 | 1 min | premium OHLC (perp vs index) |
| XAUUSDT `metrics` (daily) | 2025-12-11 | 5 min (with gaps) | `sum_open_interest` (~177k oz, ~$732M on 2026-09-29), `count_toptrader_long_short_ratio`, `sum_toptrader_long_short_ratio`, `count_long_short_ratio`, `sum_taker_long_short_vol_ratio` |
| XAUUSDT `bookDepth` (daily) | 2025-12-11 | 30 s | cumulative depth at ±0.2%, ±1..5% |
| XAUUSDT `aggTrades` (monthly) | 2025-12 | tick | price, qty, `is_buyer_maker` → exact aggressor side and trade size |
| **PAXGUSDT perp klines 1m** | **2025-03** | 1 min | `taker_buy_volume` |
| PAXGUSDT perp `metrics` | 2025-03-27 | 5 min | OI, taker L/S ratio |
| **PAXGUSDT spot klines 1m** (`data/spot/...`) | **2020-08** | 1 min | taker buy base volume |
| XAUTUSDT spot klines 1m | 2026-03 | 1 min | - |
| XAGUSDT perp klines 1m | 2026-01 | 1 min | `taker_buy_volume` |
| bookTicker (XAUUSDT) | none | - | - |

XAUUSDT funding by month (MEASURED; rate per funding interval):

| Month | n prints | non-zero | min | max | mean |
|---|---|---|---|---|---|
| 2025-12 | 124 | 124 | −0.039% | +0.164% | +0.013% |
| 2026-01 | 186 | 167 | −0.158% | **+0.442%** | +0.023% |
| 2026-02 | 168 | 69 | **−0.335%** | **+0.500%** | +0.007% |
| 2026-03 | 186 | 83 | −0.121% | +0.427% | +0.011% |
| 2026-04 | 180 | 91 | −0.205% | +0.056% | −0.009% |
| 2026-05 | 186 | 92 | 0 | +0.084% | +0.004% |
| 2026-06 | 179 | 102 | 0 | +0.033% | +0.004% |
| 2026-07 | 186 | 65 | 0 | +0.016% | +0.002% |
| 2026-08 | 186 | 41 | 0 | +0.023% | +0.001% |

PAXGUSDT perp funding in the months I sampled: always non-zero, range −0.046% to +0.005% (capped near +0.005%).
OPINION: the Jan-Mar 2026 XAUUSDT perp was a crowded retail-long market (+0.5% per 4 h is roughly 1,000%/yr annualised), which fits Schmeling et al.'s "trend-chasing leverage demand".
The team's OFI monthly P&L (Jan −100, Feb +85, Mar +63, Apr +17, May +3, from `HANDOFF.md`) is too short a series to relate to funding. Test it formally (§7, rank 2).

### 1.2 What this literature implies for the team's OFI signal (OPINION)
- **Mechanism check.** If perp taker flow mainly reacts to past spot moves (crypto trend-chasers, rows 1.10-1.11), the 15-min "prediction" may just be spot momentum under another name.
  The decisive control is to regress r_spot(t+1..t+15) on the OFI z **and** on r_spot(t−15..t) and r_perp(t−15..t), with Newey-West errors (lag 15).
- **Expected-vs-surprise flow.** Order signs have long memory (row 1.14), so a 15-min imbalance is partly forecastable from the previous imbalance.
  Theory and evidence say only the unexpected part carries price information. Build OFI_surprise = OFI_15(t) − E[OFI_15(t) | OFI_15(t−15), OFI_15(t−30), ...] with a causal AR(3) fitted on
  the prior 1440 min, and compare it with raw OFI.
- **Where the reversal lives.** Row 1.9 finds that on the crypto tape the price *reverses* after taker-driven moves. The team finds that *spot gold continues* in the flow's direction.
  These are compatible only if the perp's temporary impact reverts while its information component carries into spot. Measure both legs: after |z|>3 events, report r_perp and r_spot at +1..+30 min.
- **Venue.** Gold price discovery is in COMEX futures (row 1.8). A crypto perp should at best aggregate sentiment or positioning, not lead fundamentals. Expect a small, regime-dependent effect,
  not a stable law.

---

## 2. Volatility regime and volatility-managed strategies (priority 2)

| Item | Claim | Effect | Sample | Source | Cred. |
|---|---|---|---|---|---|
| Volatility-managed portfolios | Scaling exposure by 1/variance raises alpha and Sharpe for equity factors and FX carry | large alphas | US factors, 1926-2015 | Moreira-Muir (2017) *JF* [doi](https://doi.org/10.1111/jofi.12513) | HIGH (in sample) |
| OOS critique | Most volatility-managed strategies do not beat unmanaged ones out of sample in real time | - | 103 strategies | Cederburg, O'Doherty, Wang, Yan (2020) *JFE* [doi](https://doi.org/10.1016/j.jfineco.2020.04.015) (memory) | HIGH |
| **Volatility targeting by asset class** | Sharpe gains **only for risk assets (equity, credit)**, via the leverage effect. For **bonds, FX and commodities the Sharpe impact is negligible**. Volatility targeting still reduces extreme returns in every class | Sharpe Δ≈0 for commodities | multi-asset, decades | Harvey, Hoyle, Korgaonkar, Rattray, Sargaison, van Hemert (2018) *JPM* [doi](https://doi.org/10.3905/jpm.2018.45.1.014) | HIGH |
| Commodity futures | Volatility management works in sample for commodity momentum and market portfolios but **fails out of sample** | OOS ≈ 0 | commodity futures | Kang, Kwon (2020) *JFM* [doi](https://doi.org/10.1002/fut.22175) [pdf](https://strathprints.strath.ac.uk/82764/1/Kang_Kwon_JFM_2020_Volatility_managed_commodity_futures_portfolios.pdf) | MEDIUM-HIGH |
| Commodity momentum (China) | Managing volatility improves commodity momentum | positive | Chinese futures | (2021) *JFM* [doi](https://doi.org/10.1002/fut.22195) | MEDIUM |
| **Liquidity provision is regime-dependent** | Short-term reversal returns (a proxy for liquidity provision) are **highly predictable with the VIX**. Expected returns and conditional Sharpe spike in turmoil | large, time-varying | US equities 1998-2010 | Nagel (2012) *RFS* [doi](https://doi.org/10.1093/rfs/hhs066) | HIGH (equities) |
| Intraday momentum by regime | First-half-hour → last-half-hour predictability is stronger on volatile, high-volume, news days | - | SPY; Chinese commodity futures | Gao, Han, Li, Zhou (2018) *JFE*; Baltussen et al. (2021) *JFE*; RIBAF 2020 [doi](https://doi.org/10.1016/j.ribaf.2020.101278) | HIGH, but **already failed on gold** (COMEX-settle momentum) |
| Time-series momentum | 1-12 month TSMOM in 58 futures including gold, volatility-scaled | Sharpe ~1 diversified | 1985-2009 | Moskowitz, Ooi, Pedersen (2012) *JFE* [doi](https://doi.org/10.1016/j.jfineco.2011.11.003) | HIGH, but monthly holds and ~$50/day σ per oz: **not implementable at $13** |

Codeable consequences (OPINION):
- With a fixed 1-oz lot, "volatility management" can only mean (a) trade or skip, and (b) stop distance ∝ volatility, which changes the dollar σ per trade.
- The goal-reaching criterion (§4) rewards a higher **κ = 2μ/σ²**, not Sharpe. A filter that removes cost-dominated low-volatility states raises μ.
- A filter that removes news and spike states lowers σ².
- Filter definition (causal): RVr(t) = realized variance of M1 mid returns over the last 60 min ÷ median of the same 60-min clock window over the previous 20 trading days.
- Pre-register 3 buckets: RVr < 0.8, 0.8-2.5, > 2.5. Nagel-style prediction for fade or liquidity-provision rules: the edge concentrates in the highest bucket. Round-1 cost/range prediction: the lowest bucket is net-negative for stops under $3.

---

## 3. Event and calendar anomalies (priority 3)

### 3.1 FX-fix W-pattern (Krohn, Mueller, Whelan 2024 *JF*): SOURCED, then MEASURED on gold → **rejected**
- **SOURCED** ([WRAP pdf](https://wrap.warwick.ac.uk/177333/1/WRAP-foreign-exchange-fixings-returns-around-clock-Mueller-2023.pdf), [doi](https://doi.org/10.1111/jofi.13306)):
  - Over G9 currencies 1999-2019, the USD appreciates about 2 bp before the Tokyo fix (9:55 JST) and before the ECB fix (14:15 CET), then reverts after the Tokyo fix and after the London 4 pm fix. t-stats range 4.5-11.7.
  - The reversal portfolios earn 6-16%/yr gross with volatility under 8%. The 2014-2019 Tokyo reversal is "notably smaller".
  - Their windows: pre-T 17:00 ET → Tokyo fix; post-T → 02:00 ET; pre-E 02:00 ET → ECB fix; post-L London fix → 17:00 ET.
  - Related papers: Melvin-Prins (2015) *JFinMkts* [doi](https://doi.org/10.1016/j.finmar.2014.11.001) (month-end equity-hedging flows at the 4 pm fix); Evans (2018) *JBF* [MPRA pdf](https://mpra.ub.uni-muenchen.de/58151/7/MPRA_paper_58151.pdf); Husselmann-Kasikov (2019) *QF* [doi](https://doi.org/10.1080/14697688.2019.1638154).
- **MEASURED.**
  - Data and method: Dukascopy M1 mid, the same windows with DST handled per zone, and the 17:00 ET endpoints moved to 16:59 because of the daily break. 330 weekdays; Mondays drop out because the Sunday close is missing.
  - The gold analogue of "USD up pre-fix" is short gold pre-fix and long gold post-fix.
  - Full sample (includes TEST, see the disclosure at the top): Tokyo reversal **−$5.85/day (t −2.78)**, meaning the opposite sign. Europe reversal **+$0.00/day (t 0.00)**.
  - The only strong window is pre-T: +$4.73/day (t 4.37), gold *rising* before the Tokyo fix.
  - On TRAIN and VALID separately, pre-T was +$2.56 (t 2.95, 64% of all 24 h drift) and +$12.32 (t 3.51). The Europe windows were insignificant in both.
  - Conclusion: gold does not inherit the FX-fix reversal. Drop it.

### 3.2 "Asia reopen" drift: MEASURED, **real at mid but not tradeable**
- Decomposition of the pre-T window by minutes after 18:00 ET, on TRAIN and VALID only, weekdays:

  | Split | Reopen gap (close→open) | First 60 min | 60-120 | 120-180 | 180-240 | 240-360 | 360-480 |
  |---|---|---|---|---|---|---|---|
  | TRAIN (n 245) | +$0.04 (t 0.1) | **+$1.52 (t 2.9)** | +0.13 | +1.06 (t 1.8) | +0.22 | +0.52 | −0.16 |
  | VALID (n 106) | +$1.99 (t 1.1) | **+$5.19 (t 2.2)** | +0.44 | +2.43 | +0.50 | −1.67 | −2.59 |

- The median Dukascopy spread 0/5/10/30/60 min after the reopen is **$1.39 / $1.02 / $0.84 / $0.72 / $0.87**.
- Entering long at the ask 5-15 min after the reopen and exiting at the bid, with an extra $0.15 for commission and slippage, gives:
  - exit at +60 min: TRAIN −$0.40 to −$0.61 (t −1.1 to −1.4); VALID +$1.4 to +$2.4 (t 0.7-1.4)
  - exit at +180 min: TRAIN +$0.67 to +$0.88 (t 0.8-1.1); VALID +$3.2 to +$4.7 (t 0.8-1.2); trade σ $13-41
- Nothing is significant, and TRAIN and VALID disagree at the 60-min exit. The mid-price effect sits in the first 5 minutes at a $1-1.4 spread, plus beta to the 2025-26 bull market (gold ≈ +50%).
- This agrees with Blose-Gondhalekar (2014: COMEX overnight returns positive, day returns negative, effect weakening; round 1 §2.9) and with the team's "no stable hour-of-day drift". Do not pursue.

### 3.3 Pre-announcement drift (not the failed post-news second leg)
- **SOURCED.** Kurov, Sancetta, Strasser, Wolfe (2019) *JFQA* ([pdf](https://www.cambridge.org/core/services/aop-cambridge-core/content/view/E1AE41FB94D4F2CA5134410D5C82A0E2/S0022109018000625a.pdf/div-class-title-price-drift-before-u-s-macroeconomic-news-private-information-about-public-announcements-div.pdf)):
  - In stock-index and Treasury futures, 9 of 20 market-moving US releases show informed trading before the official time.
  - Prices start moving in the "correct" direction about **30 minutes before** the release. The pre-announcement drift is **about 40%** of the total adjustment.
  - Bernile, Hu, Tang (2016) *JFE* [doi](https://doi.org/10.1016/j.jfineco.2015.09.012) find informed trading during FOMC lock-ups.
  - Gold was not in these samples. Gold does react strongly to NFP and CPI (Elder-Miao-Ramchander 2012, round 1 §2.1). Credibility: HIGH for the effect, UNKNOWN for gold.
- **Rule (codeable).**
  - Events: HIGH US releases at 08:30 and 10:00 ET plus FOMC at 14:00 ET, from `data/econ_calendar*.csv`.
  - Signal: z = r_mid(T−30m, T−2m) ÷ σ of the same clock window over the prior 20 non-event days.
  - If |z| > 1, enter at T−1m at the ask or bid in the direction of z. Exit at T+5m (variant: T+15m). Stop at 2× the pre-window range.
  - Placebo: the same clock times on non-release days.
- **Hard constraint.** LiteFinance raises XAUUSD margin to 1:200 for new orders within ±30 min of major news (round 1 §1.1). A 0.01 lot then needs about $21, so this is **unusable below ~$25 equity**.
  Test it now; deploy it only later.

### 3.4 Pre-FOMC drift and the FOMC cycle
- Lucca-Moench (2015) *JF* [doi](https://doi.org/10.1111/jofi.12196): large pre-FOMC excess returns in US and international equities, but **none in Treasuries or money-market futures**.
- Kurov, Wolfe, Gilbert (2020) *FRL* [doi](https://doi.org/10.1016/j.frl.2020.101781): the drift **essentially disappeared after 2015**.
- Cieslak, Morse, Vissing-Jorgensen (2019) *JF* [doi](https://doi.org/10.1111/jofi.12818): the equity premium is earned in even weeks of the FOMC cycle (equities only).
- I found no gold-specific pre-FOMC study (OpenAlex and Semantic Scholar searches). With about 13 FOMC meetings in our data, the test has no power. Credibility for gold: LOW. **Skip.**

### 3.5 Month-end, quarter-end and index flows
- FX month-end hedging flows at the London 4 pm fix: Melvin-Prins 2015, Evans 2018. Robot Wealth reports a turn-of-month **Treasury** trade at Sharpe about 1 since the mid-1990s, with **Sharpe 1.8 after costs in 2025**
  ([post](https://robotwealth.com/much-ado-about-variance/), practitioner, LOW-MEDIUM).
- For gold, one paper tests the turn of the month (TOM) in gold and Bitcoin around COVID (*Managerial Finance* 2024, [doi](https://doi.org/10.1108/mf-02-2024-0088); abstract truncated, results unclear).
- About 20 month-ends in our data are too few. **Skip** unless it can be combined with the FX-fix window. That combination is rejected in §3.1.

### 3.6 LBMA auction, options expiry, seasonality
- **LBMA auction.** Beyond Caminschi-Heaney (2014, telephone-fix era; round 1 §2.6), I found **no post-2015 study** of the electronic auction. Credibility for a 2025-26 edge: LOW.
- **Options expiry and pinning.** Evidence exists for stocks, and a 2026 SSRN note reports a regime shift from pinning to amplification in S&P options
  ([SSRN 6564078](https://doi.org/10.2139/ssrn.6564078)). I found **no study of COMEX gold options pinning**. "Max-pain" gold claims are MARKETING.
- **Day-of-week.** Lucey-Tully (2006) *AFE* [doi](https://doi.org/10.1080/09603100500386586) find a negative Monday effect in COMEX gold and silver 1982-2002, with weak mean effects and strong variance effects.
  Robot Wealth's 2025 "GLD Thursday-Friday weekend trade" and "weekly seasonality in gold" posts are **paywalled; title only, unverified**
  ([1](https://robotwealth.com/rw-pro-webinar-6-mar-2025-golds-weekend-effect-monte-carlo-analysis-of-the-gld-thursday-friday-trade/), [2](https://robotwealth.com/rw-pro-webinar-20-feb-2025-weekly-seasonality-in-gold/)).
  These are daily holds, which a $13 account cannot hold at 1 oz.
- **Autumn effect.** Baur (2013) *RIBAF* [doi](https://doi.org/10.1016/j.ribaf.2012.05.001) found one; Potrykus-Augustynowicz (2024) [doi](https://doi.org/10.2478/ijme-2024-0011) find it **reversed into a winter effect**. Unstable and monthly. Skip.

---

## 4. Small-account growth: reaching $450 from $13 before ruin (priority 4)

### 4.1 Theory (SOURCED unless marked)
- **No edge caps success.** If per-trade expectation is ≤ 0 (a supermartingale), optional stopping bounds P(reach G before ruin) by about (x0 − ruin)/(G − ruin). No sizing rule or payoff shape can beat this.
  - With the $8.3 margin barrier: (13 − 8.3)/(450 − 8.3) ≈ **1.1%**, as in `HANDOFF.md`.
  - The bound is at most 13/450 = 2.9% even with no barrier and unlimited leverage. (Standard martingale argument; OPINION on the arithmetic.)
- **Bold vs timid.** Pestien-Sudderth (1985) *Math. OR* [pdf](https://conservancy.umn.edu/bitstreams/263a4a6f-9134-44b4-8cc9-072f32085815/download): to maximise P(reach goal) when you choose drift μ and variance σ² at each moment, **maximise μ/σ²**.
  Bold play is optimal in subfair games and **timid play in superfair ones**. In superfair games, maximising the drift of log wealth (Kelly) minimises the expected time.
  This matches Dubins-Savage *How to Gamble If You Must* (memory).
- **Kelly minimises the time to a large goal.** Breiman (1961) [doi](https://doi.org/10.21236/ad0402290). MacLean-Thorp-Ziemba (2010) *QF* [doi](https://doi.org/10.1080/14697688.2010.506108) cover the good and bad properties of full and fractional Kelly.
- **With a deadline**, the policy that maximises P(reach goal by T) replicates a digital option: exposure *rises* when behind schedule. Browne (1999) *Adv. Appl. Prob.* [doi](https://doi.org/10.1239/aap/1029955147).
  See also Browne (1997) *Math. OR* on danger and safe regions [doi](https://doi.org/10.1287/moor.22.2.468).
- **Drawdown-constrained Kelly** as a convex program: Busseti, Ryu, Boyd (2016) *J. Investing* [pdf](https://joi.pm-research.com/content/iijinvest/25/3/118.full.pdf).

### 4.2 MEASURED Monte Carlo (scratchpad `sim/goal_sim.py`; 20,000 paths; ≤3,000 trades; ruin when equity < $8.3 × lots needed; cost included in μ)
Trade models:
- "tx" = time-exit P&L per oz ~ N(m, σ) floored at −S (stop), with m calibrated so the net mean is μ.
- "br" = bracket win +bD with probability p, loss −D, minus c = $0.42.

Policies:
- timid = always 1 oz
- frac0.10 = floor(E × 0.10 / stop) oz, at least 1, capped by margin
- hybrid = 1 oz until $60, then 3%
- bold = maximum lots by margin

| Trade model (net of $0.42) | timid P(hit) / median trades | frac0.10 | hybrid | bold |
|---|---|---|---|---|
| tx μ=0, σ=4, S=5 (no edge) | 0.3% / 2368 | 1.4% / 762 | 0.5% | 1.1% / 27 |
| tx μ=+0.25, σ=4, S=5 (≈ OFI confirm) | 20.6% / 1464 | 20.4% / 628 | 20.9% | 3.4% |
| tx μ=+0.42, σ=4, S=5 (≈ OFI discovery) | 32.6% / 944 | 32.8% / 401 | 33.0% | 6.3% |
| tx μ=+0.42, **σ=2.5**, S=3 | **62.0%** / 1009 | 61.7% / **285** | 61.9% | 35.8% |
| tx μ=+0.80, σ=4, S=5 | 53.5% / 526 | 53.3% / 220 | 53.0% | 19.5% |
| br p=.45 b=2 D=$3 (+0.35R gross) | 35.9% / 632 | 33.0% / 188 | 36.0% | 7.2% |
| br p=.40 b=2 D=$3 (+0.20R gross) | 10.9% / 1598 | 6.0% / 265 | 11.2% | 2.2% |
| br p=.20 b=6 D=$2 (convex, +0.40R gross) | 12.4% / 893 | 5.3% / 96 | 11.8% | 2.2% |
| br p=.143 b=6 D=$2 (convex, 0R gross) | 0.0% | 0.1% | 0.0% | 0.2% |

Read-out (OPINION on MEASURED numbers):
1. **Success is decided near the barrier, by μ/σ².** The same μ = +$0.42 goes from 33% to 62% when trade σ drops from $4 to $2.5. Cutting σ (tighter time exits, skipping spike states) matters as much as raising μ.
2. **Fractional scaling after about $50 costs no probability and saves 2-3.5x the trades.** The early phase is 1 oz either way. With about 1.3 OFI trades/day, timid needs 2-3 years; frac0.10 needs 7-12 months.
3. **Bold play only makes sense without an edge**, and then the ceiling is about 1%.
4. **Low-hit-rate convex payoffs (1:6) are worse than 1:2 brackets at similar gross R**, because losing streaks hit the $4.7 buffer first.
5. Any candidate should be judged by κ = 2μ/σ² in $/oz, not by PF alone. Replay its actual trade list through these policies (§7, step 0).

---

## 5. Evaluation hygiene for the OFI signal (SOURCED)
- About 17 strategy families and more than 24 OFI grid configurations have been tried. Deflate the result:
  - Deflated Sharpe Ratio: Bailey & López de Prado (2014) *JPM* [doi](https://doi.org/10.3905/jpm.2014.40.5.094).
  - Probability of Backtest Overfitting via CSCV: Bailey, Borwein, López de Prado, Zhu (2016) *J. Comp. Finance* [pdf](https://escholarship.org/uc/item/4w1110bb).
  - Multiple-testing hurdle t > 3: Harvey, Liu, Zhu (2016) *RFS* (memory); "Lucky factors" (2021) *JFE* [doi](https://doi.org/10.1016/j.jfineco.2021.04.014).
- The random-direction permutation p ≈ 0.02 reported in `HANDOFF.md` is a single-hypothesis p-value. After selection over the OFI grid alone, it is not significant. The PAXG replication (§7, rank 1) is the cleanest fix, because nothing there is tuned.
- Robot Wealth's practitioner framework ([post](https://robotwealth.com/trading-without-edge-thats-expensive-gambling-i-said/)) is useful for triage. Durable retail edges come from risk premia or from **price-insensitive flow**. Ask "who is forced to trade, and why is it not absorbed?"
  For XAUUSDT, the candidate answer is levered crypto retail chasing gold moves (Schmeling et al.). That favours conditioning on crowding (§7, rank 2).

## 6. Low-credibility and marketing flags
- Zenodo, OSF and ResearchGate "funding-rate arbitrage optimisation" studies with scanner or product links ([example](https://doi.org/10.17605/osf.io/e6vwu), [example](https://doi.org/10.5281/zenodo.21699985)): MARKETING. Not used.
- "Who sets the range? Funding mechanics and 4h context" ([arXiv 2601.06084](https://arxiv.org/abs/2601.06084)): narrative, no testable effect size. LOW.
- Binance "top trader long/short ratio" contrarian rules: no peer-reviewed support found. LOW or MARKETING. The data exists in `metrics`, if anyone wants a cheap falsification.
- Gold options "max-pain" and pinning claims, ICT/SMC concepts, LBMA-fix "manipulation scalps": no systematic evidence found (round 1 §2.12). MARKETING until tested.
- Paywalled and unverifiable here: Robot Wealth GLD weekend/weekly seasonality; Concretum "Where to trade intraday trends" and "Can we exploit predictable institutional flows?" ([archive](https://concretumgroup.substack.com/)).

---

## 7. Ranked top 5 to test next (OPINION), with test designs on our data

**Step 0 (applies to every candidate): goal replay.** Take the candidate's net per-trade $/oz list on TRAIN+VALID. Block-bootstrap it (block = 1 week) through the timid, frac0.10 and hybrid policies from §4.2,
with the $8.3/lot barrier (update for the gold price) and a $450 target. Report P(hit), P(ruin) and median calendar days. Go/no-go uses P(hit within 180 days) and κ = 2μ/σ².

**Common protocol.**
- Splits: TRAIN 2025-01-21..2025-12-31, VALID 2026-01-01..2026-05-31, TEST Jun-Sep 2026 untouched until a single final run.
- Costs: LF_BASE (spread $0.22 + $0.05 commission per oz + $0.05/side slippage ≈ $0.42 round trip) and LF_HARSH.
- Latency: entry at the next bar open + 1 s. Standard errors: Newey-West. Controls: random-direction permutation (1,000×).
- Report the Deflated Sharpe with N = all variants ever run for the family.

### Rank 1: replicate the frozen OFI rule on PAXG flow over 2025 (independent venue and period)
- **Evidence.** Cross-venue and cross-asset flow predicts at short horizons (Cont-Cucuringu-Zhang 2023, HIGH). Crypto-venue flow predicts crypto returns (Anastasopoulos et al. 2026, MEDIUM). XAUUSDT-specific support is only the paper title (§1.1).
- **Data.**
  - `data.binance.vision/data/futures/um/monthly/klines/PAXGUSDT/1m/` (2025-03 onward; col 5 `volume`, col 9 `taker_buy_volume`).
  - `data/spot/monthly/klines/PAXGUSDT/1m/` (2020-08 onward; same columns).
  - Spot gold from `data/m1_ba.parquet` and `data/s10_ba.parquet`.
- **Rule.** Exactly `cand/ofi_flow.py` with the deployed parameters and **no re-tuning**:
  - OFI_15 = (2·buy − vol)/vol over 15 perp minutes, z-scored against the prior 1440 min (min_periods 300).
  - Signal: first minute with |z| > 3, then a 30-min cool-down; trade spot in the flow's direction.
  - Stop 3 × ATR(M1); 15-min time exit.
  - Run PAXG-perp (2025-03-27..12-31) and PAXG-spot (2025-01-21..12-31) as separate pre-registered tests.
- **Mechanism controls.**
  - Regress r_spot(t+1..t+15) on z_OFI, r_spot(t−15..t) and r_PAXG(t−15..t).
  - Report the OFI coefficient's NW t-stat, and the result with |z| > 3 events matched on |r_spot(t−15..t)|.
- **Pass.** Net $/oz > 0 at LF_BASE and PF ≥ 1.10 in both halves (Mar-Jul, Aug-Dec). Permutation p < 0.05. OFI coefficient t > 2 after controls.
- **Fail.** Treat the XAUUSDT result as a 5-month artifact and undeploy the OFI leg.

### Rank 2: OFI conditioned on crowding (premium/funding), open-interest change and trade size
- **Evidence.** SSRN 6872638 (title: funding-conditional informativeness). Crypto carry reflects trend-chasing leverage demand (Schmeling-Schrimpf-Todorov 2026, HIGH). Perp deviations comove with sentiment (He et al.).
- **Data (XAUUSDT, VALID only, plus PAXG on TRAIN for replication).**
  - `premiumIndexKlines/1m`, preferred over funding because funding is clamped to 0 most of the time after April.
  - `fundingRate`.
  - `metrics` (5-min `sum_open_interest`; note the gaps).
  - `aggTrades` (monthly; `is_buyer_maker` gives the aggressor; qty gives size).
- **Three pre-registered binary splits, 6 cells in total, no further grid.**
  - (a) *Crowded*: sign(z_OFI) == sign(premium z over 1440 min) vs opposite.
  - (b) *New positioning*: ΔOI over the same 15 min > 0 vs ≤ 0 (≤ 0 suggests short covering or liquidations).
  - (c) *Large-trade share*: share of taker volume from trades at or above the rolling 95th-percentile size, above vs below its rolling median.
- **Output.** Net $/oz, PF, n and κ per cell. Bootstrap CI of the cell difference. DSR with N = 24 prior configs + 6.
- **Prediction (OPINION).** Informative flow is new-position (ΔOI>0), non-crowded, large-trade flow. Crowded and closing flow is noise, or reverses (Kitron-Wengrowicz).
- **Pass.** One pre-registered cell is better than its complement with bootstrap p < 0.05 on XAUUSDT-VALID **and** keeps its sign on PAXG-TRAIN.

### Rank 3: flow decomposition (surprise flow and cross-asset silver flow)
- **Evidence.** Trade-sign long memory: expected flow is priced (row 1.14, HIGH). Lagged cross-asset OFI forecasts (Cont-Cucuringu-Zhang 2023, HIGH).
- **Signals.**
  - (i) OFI_surprise = OFI_15 − AR(3) forecast fitted causally on the prior 1440 minutes' OFI_15 series (15-min steps).
  - (ii) z_OFI of XAGUSDT, and gold+silver combined (dollar-volume weighted).
  - (iii) Trade-sign ACF from XAUUSDT `aggTrades` (lags 1-1000 trades) as a descriptive check of long memory.
- **Test.** Replace z_OFI with each signal inside the frozen rule. Also regress r_gold(t+1..t+h), h ∈ {1, 5, 15} min, on current and lagged OFI_gold and OFI_silver.
  Fit Jan-Mar 2026, test Apr-May 2026 (XAGUSDT starts 2026-01).
- **Pass.** Out-of-sample R² > 0 and net $/oz ≥ the raw-OFI rule in Apr-May, with no degradation on the long and short sides separately.

### Rank 4: volatility-state filter on OFI (raise κ, not Sharpe)
- **Evidence.**
  - HIGH that volatility targeting adds nothing to commodity Sharpe (Harvey et al. 2018; Kang-Kwon 2020).
  - HIGH that liquidity-provision returns are regime-dependent (Nagel 2012).
  - MEASURED in round 1 that the cost/range ratio is about 2.5x worse in dead hours.
- **Filter.** RVr (§2) buckets < 0.8, 0.8-2.5, > 2.5. Also a HIGH-release blackout [T−30, T+30], which the 1:200 margin forces anyway.
- **Test.** Report net $/oz, σ and κ of OFI trades per bucket on PAXG-TRAIN (rank 1). Choose one keep-set from TRAIN only, then verify on XAUUSDT-VALID.
- **Pass.** κ(keep-set) ≥ 1.3 × κ(all) on VALID, with the trade count ≥ 60% of the unfiltered count.

### Rank 5: pre-announcement drift into US releases (Kurov et al. 2019): test now, deploy only above ~$25
- **Evidence.** HIGH in index and Treasury futures (9/20 releases; drift starts about T−30; about 40% of the move). UNKNOWN for gold. It is genuinely different from the failed *post*-release second leg.
- **Data.** `data/econ_calendar.csv` / `econ_calendar_ext.csv` (HIGH US events 08:30, 10:00, 14:00 ET), `data/s10_ba.parquet` for the real release-time spread.
- **Rule.** See §3.3: z = r(T−30m, T−2m)/σ_ref; enter at T−1m if |z| > 1; exit at T+5m (variant T+15m); stop at 2 × the pre-window range.
  Placebo: the same clock times on non-event days. Split the results by release type (NFP, CPI, ISM, FOMC).
- **Pass.** Net ≥ +$0.50/oz at the actual 10-second release spreads, hit rate > 55%, the same sign in TRAIN and VALID, and the placebo ≈ 0.
  If it passes, gate it on equity ≥ $25, because the news-window margin is 1:200.

### Not ranked, and why (do not spend time)
- **Short-horizon trend or momentum variants.** Kurth et al. 2026 (HIGH) and five failed team tests.
- **15-min sign reversal in spot gold.** Kitron-Wengrowicz: null on US-hours slots and AUC ≈ 0.52 on 24 h bars. Even crypto's stronger version is 1.3 bp gross vs 5 bp cost. The team's 5m exhaustion fade also failed.
- **FX-fix reversal and Asia-reopen drift.** MEASURED here, rejected (§3.1-3.2).
- **Pre-FOMC drift, month-end, options expiry, day-of-week and seasonal effects.** Evidence gone or absent for gold, too few events, or daily holds (§3.4-3.6).
- **VPIN as a direction signal.** Contested (Andersen-Bondarenko); use at most as a volatility proxy.

---

## 8. Sources used (all accessed 2026-09-30 / 10-01)
- SSRN 6872638 (title via OpenAlex; PDF blocked): https://doi.org/10.2139/ssrn.6872638
- Cont, Kukanov, Stoikov: https://arxiv.org/abs/1011.6402 · Cont, Cucuringu, Zhang: https://arxiv.org/abs/2112.13213
- Chordia, Roll, Subrahmanyam 2005: https://doi.org/10.1016/j.jfineco.2004.06.004 · Chordia-Subrahmanyam 2004: https://doi.org/10.1016/s0304-405x(03)00175-2
- Evans-Lyons: https://doi.org/10.1086/324391 , https://doi.org/10.1257/000282805774669934
- Anastasopoulos et al. 2026: https://doi.org/10.1016/j.finmar.2026.101047 · Schmeling-Schrimpf-Todorov: https://doi.org/10.1287/mnsc.2024.05069
- He-Manela-Ross-von Wachter: https://arxiv.org/abs/2212.06888 · Ackerer-Hugonnier-Jermann: https://doi.org/10.3386/w32936
- Alexander et al. 2020: https://doi.org/10.1002/fut.22050 · Alexander-Heck: https://doi.org/10.1016/j.jfs.2020.100776
- Sehgal-Sobti-Diesting 2021: https://doi.org/10.1002/fut.22208
- Kitron-Wengrowicz 2026: https://arxiv.org/abs/2608.21888 · Bieganowski-Ślepaczuk 2026: https://arxiv.org/abs/2602.00776
- Easley-López de Prado-O'Hara 2012: https://doi.org/10.1093/rfs/hhs053 · Andersen-Bondarenko: https://doi.org/10.1016/j.finmar.2013.05.005
- Sato-Kanazawa 2023: https://doi.org/10.1103/physrevlett.131.197401 · FX order-flow memory: https://arxiv.org/abs/1504.04354
- Kurth-Eisler-Rej-Bouchaud 2026: https://arxiv.org/abs/2607.01550
- Moreira-Muir: https://doi.org/10.1111/jofi.12513 · Harvey et al. 2018: https://doi.org/10.3905/jpm.2018.45.1.014 · Kang-Kwon: https://doi.org/10.1002/fut.22175 · Nagel: https://doi.org/10.1093/rfs/hhs066 · Moskowitz-Ooi-Pedersen: https://doi.org/10.1016/j.jfineco.2011.11.003
- Krohn-Mueller-Whelan: https://doi.org/10.1111/jofi.13306 (read via https://wrap.warwick.ac.uk/177333/) · Melvin-Prins: https://doi.org/10.1016/j.finmar.2014.11.001 · Evans 2018: https://mpra.ub.uni-muenchen.de/58151/
- Kurov et al. 2019: https://doi.org/10.1017/s0022109018000625 · Bernile-Hu-Tang: https://doi.org/10.1016/j.jfineco.2015.09.012
- Lucca-Moench: https://doi.org/10.1111/jofi.12196 · Kurov-Wolfe-Gilbert: https://doi.org/10.1016/j.frl.2020.101781 · Cieslak et al.: https://doi.org/10.1111/jofi.12818
- Lucey-Tully 2006: https://doi.org/10.1080/09603100500386586 · Baur 2013: https://doi.org/10.1016/j.ribaf.2012.05.001 · Potrykus 2024: https://doi.org/10.2478/ijme-2024-0011
- Pestien-Sudderth 1985: https://doi.org/10.1287/moor.10.4.599 · Browne 1999: https://doi.org/10.1239/aap/1029955147 · Browne 1997: https://doi.org/10.1287/moor.22.2.468 · Breiman: https://doi.org/10.21236/ad0402290 · MacLean-Thorp-Ziemba: https://doi.org/10.1080/14697688.2010.506108 · Busseti-Ryu-Boyd: https://doi.org/10.3905/joi.2016.25.3.118
- Bailey-López de Prado DSR: https://doi.org/10.3905/jpm.2014.40.5.094 · PBO: https://doi.org/10.21314/jcf.2016.322 · Lucky factors: https://doi.org/10.1016/j.jfineco.2021.04.014
- Robot Wealth: https://robotwealth.com/much-ado-about-variance/ , https://robotwealth.com/trading-without-edge-thats-expensive-gambling-i-said/
- Binance public data: https://data.binance.vision/ (S3 listing `s3-ap-northeast-1.amazonaws.com/data.binance.vision?prefix=data/futures/um/...`)
