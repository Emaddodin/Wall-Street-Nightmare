# XAUUSD intraday edges and LiteFinance trading conditions: web research

Written 2026-09-30 (UTC 2026-09-29 ~22:00-22:40) on branch `for-dear-opus-5.5`. No production file was modified.
The only new files are this note and `xau_alpha/recon/microlot_ruin.py`.

Labels: **SOURCED** = taken from the cited URL or file. **MEASURED** = I computed it, and the script or data is named.
**OPINION** = my judgement. The web pages I fetched were saved under the session scratchpad. Everything quoted below is
short, and I paraphrased where possible.

**How the research was done (read this before trusting coverage).** The built-in WebSearch and WebFetch tools failed for the whole
session with a model error. DuckDuckGo and Mojeek answered with bot-detection pages, and I stopped using them rather than try to
get around the check. Bing RSS returned unrelated results. What I actually used:
the OpenAlex API (api.openalex.org), the Semantic Scholar API, the arXiv API, and plain HTTPS fetches of known pages (LiteFinance, LBMA, ICE/IBA,
RoboForex, open-access PDFs). Some publisher and SSRN pages returned 403, and QuantifiedStrategies timed out, so the list below misses
paywalled numbers and most practitioner blog backtests. Where a paper's own text was not retrievable, the note says so and uses only its
title and abstract.

---

## 0. Bottom line

1. **At LiteFinance, the minimum XAUUSD lot is 0.01, which is 1 oz: $1 per $1 move.** The instrument margin is **0.2% (1:500)**, not 1:1000.
   Around major news it can be raised to **0.5% (1:200)** for new orders. SOURCED: LiteFinance instrument page and margin-change page (§1).
   MEASURED arithmetic at $4,180 gold: one 0.01 lot needs **$8.36** margin (1:500) or **$20.90** (1:200). A $13 account can hold only one
   0.01 lot, and it **cannot open any position during a raised-margin news window**. After a $4.64 adverse move its margin level
   falls below 100%, and the Client Agreement allows the broker to close positions at that point (clause 7.1).
2. **Costs on ECN.** The raw spread plus **$5 per lot round turn** equals $0.05 per 0.01 lot, charged at open. Two spread snapshots from the LiteFinance
   instrument page: **12 points ($0.12)**, the last quote before the daily break, and **48-51 points ($0.48-0.51)** in the first 30 min after the Asian reopen. A third-party
   site says ECN is "typically 0.10" and standard is 0.50. Classic and Cent add a **14-point ($0.14) markup** and charge no commission. SOURCED §1.
3. **The minimum lot, not the lack of an edge, kills a $13 flip.** MEASURED in `microlot_ruin.py`: with a 1-oz minimum and a $2-4 stop, the forced risk is
   17-36% of equity per trade. That is about 6-11x the Kelly fraction, even for a strategy with a +0.10-0.125R gross edge. Expected log growth is **negative in
   every scenario**, and P(ruin before $100) is 83-100%. With 0.01-oz granularity (a true cent account) and the same edge, ruin is about 0%.
   Reaching $100 then takes a median of about 1,600-2,200 trades.
4. **Macro releases are the best-documented intraday gold effect.** The effect is on volatility, and it is absorbed within about 5 minutes. It is not a tradable drift
   (Elder-Miao-Ramchander 2012; Cai-Cheung-Wong 2001; Christie-David et al. 2000; Roache-Rossi 2010). **Use it as a guard or cost filter, not as a signal.**
5. **Time-of-day seasonality in volatility and liquidity is robust.** It is SOURCED (Batten et al. 2017, Iwatsubo et al. 2018) and **MEASURED** here on real Dukascopy data:
   the median 1-min range is $2.2-2.3 at 13-14 UTC and $0.9 at 04 and 20-21 UTC. The spread-to-range ratio is 0.27 against 0.68.
   A scalp therefore pays about 2.5x more cost per unit of movement in dead hours.
6. **Intraday "momentum into the close" is documented across 60+ futures, including commodities** (Baltussen et al. 2021 JFE). It is the most credible
   *directional* intraday effect that applies to gold. It has not been tested on spot XAUUSD in any source I found.
7. **I found no peer-reviewed or systematic public test of ICT concepts** (silver bullet windows, judas swing, FVG, OTE), positive or negative.
   The closest academic mechanism is order clustering at round numbers and stop cascades just beyond them (Osler 2003, 2005). Treat ICT rules as
   untested hypotheses that are cheap to falsify.
8. **The London PM fix "leak"** (Caminschi & Heaney 2014) was measured in the telephone-fix era. The fix has been an IBA electronic auction since 2015, so the effect
   is probably weaker now. OPINION.
9. **Base rates for individual day traders are very poor.** Of Brazilian futures day traders who persisted at least 300 days, 97% lost money. Fewer than 1% of Taiwanese day traders
   earned predictable positive returns after fees. SOURCED §2.13.
10. **On the "news guards and LLM (Laya) models".** The literature finds that LLM news signals *can* predict returns, but any backtest inside the model's
    training window is contaminated by look-ahead and memorization (Glasserman & Lin 2023; Lopez-Lira et al. 2025). Combined with
    `recon/news_llm.md` (Laya is a general NLU model with no market training), OPINION: the only valid evaluation of Laya or Jeff is forward, on post-cutoff news.

---

## 1. LiteFinance: exact conditions (part B)

### 1.1 XAUUSD specification

| Item | Value | Label / source |
|---|---|---|
| Contract size | 100 oz per 1.00 lot. 0.01 lot = 1 oz | SOURCED: [instrument page](https://my.litefinance.org/trading/info?symbol=XAUUSD), [metals page](https://www.litefinance.org/trading/trading-instruments/metals/) |
| Min volume / step / max | 0.01 / 0.01 / 100 lots | SOURCED: instrument page |
| Digits / point | 2 digits; "price of point" $1.00 per 1 lot. That gives $0.01 per point and $1 per $1 move at 0.01 lot | SOURCED: instrument page; [commission PDF](https://www.litefinance.org/uploads/documents/pdf-litefinance/litefinance-markups-and-commissions-list-en.pdf) (Digits = 2) |
| Instrument leverage / margin | **1:500, margin 0.2%** (shown for the ECN demo group `ECN\demoECN-OR-DEF-USD`) | SOURCED: instrument page. The account leverage of 1:1000 does *not* apply to gold at this broker. Confirm on the live account with MT5 `order_calc_margin` |
| News margin | "no more than 30 minutes before and 30 minutes after" important releases, the company may raise margin for **new** positions. Example listed: XAUUSD **0.50% (1:200)** from 15:15 to 15:35 server time on 11.07.2024 (US CPI day) | SOURCED: [margin-change list](https://www.litefinance.org/markets/list-of-changes-in-margin-requirements/); Client Agreement §6.9 |
| Spread, ECN | Raw floating spread, "from 0.0 points". Snapshots from the instrument page: **12 points** (fetched about 21:55-22:00 UTC 2026-09-29, apparently the last pre-break quote) and **48 / 51 points** (22:30 and 22:32 UTC, Asian reopen). The metals page table showed **194** (ECN) and **688** (Classic) points both times I fetched it. It looks like a stale rollover snapshot, so treat it as the worst case | SOURCED: [ECN page](https://www.litefinance.org/trading/account-types/ecn/), instrument page, metals page |
| Spread, third party | "raw/ECN typically 0.10; standard 0.50" (rank 26 of 29 brokers on the standard account) | SOURCED: [brokeranalysis](https://brokeranalysis.com/broker-review/litefinance/gold-spread/). Unverified methodology |
| Commission, ECN | **$5 per 1.00 lot**, charged "per round lot" at opening on MT4/MT5 (cTrader: half at open, half at close). That is **$0.05 per 0.01 lot** | SOURCED: ECN page; commission PDF (in force since 19 May 2026) |
| Markup, Classic / Cent | **14 points** ($0.14) per round lot on XAUUSD, no commission | SOURCED: commission PDF |
| Swaps | Long **−89.136**, short **+3.45** points; triple on Wednesday; charged at 00:00 server time | SOURCED: instrument and metals pages |
| Trading hours | Mon 01:06-23:59, Tue-Fri 01:02-23:59 **server time**. Server time is **GMT+3 from the last Sunday of March to the last Sunday of October, GMT+2 otherwise** (EU DST rule). That means **22:02-20:59 UTC in summer and 23:02-21:59 UTC in winter**, a daily break of about 63 min | SOURCED: instrument page; [FAQ 4.16](https://www.litefinance.org/support/faq/). OPINION: in the 1-2 weeks a year when EU and US DST differ, check the break against the 17:00 ET COMEX halt |
| Stop / freeze level | ECN: "No Stop & Limit levels". Classic and Cent: not published (the Client Agreement §6.33-6.34 refers to a per-instrument "Stop&Limit" value) | SOURCED: ECN page; [Client Agreement](https://www.litefinance.org/uploads/documents/pdf-litefinance/litefinance-client-agreement-en.pdf). Read `SYMBOL_TRADE_STOPS_LEVEL` and `SYMBOL_TRADE_FREEZE_LEVEL` from the live terminal |
| Execution | Market execution on all accounts. "Usually 3-5 seconds" under normal conditions, "5-15 seconds" otherwise | SOURCED: Client Agreement §6.1, §6.4 |
| Request throttling | Account goes **read-only** above 1,000 requests in 300 s and is **blocked** above 10,000 in 3,600 s | SOURCED: Client Agreement §6.6-6.6.1 |
| Gaps | A stop loss inside a gap fills at the first price after the gap. A take profit inside a gap fills at the TP price. Limit orders fill at the preset price | SOURCED: Client Agreement §6.39 |
| XAUPUSD (perpetual, 24/7) | 100 oz per lot, **2% margin (1:50)**, "Cryptocurrency" category, MT5 only. One oz needs about $84 of margin, so it is not usable at $13 | SOURCED: [LiteFinance news](https://www.litefinance.org/company/detail/news/108143/); commission PDF (ECN $0.01; Classic and Cent 0.014) |

### 1.2 Account types

| | ECN | Classic | Cent |
|---|---|---|---|
| Min deposit | $50 | $50 | $10 |
| Account leverage | 1:1-1:1000 | 1:1-1:1000 | 1:1-1:1000 |
| Spread / cost | raw from 0 + $5/lot RT | "from 1.8 points" (FX); XAUUSD raw + 14 pt markup | "from 3 points" (FX); XAUUSD + 14 pt markup |
| Min lot / step / max | 0.01 / 0.01 / 100 | 0.01 / 0.01 / 100 | 0.01 / 0.01 / 100; "contract size $1000" (FX), balance in USD-¢ |
| Margin call / stop-out | 100% / **20%** | 100% / **20%** | 100% / **50%** |
| Platforms | MT4 / MT5 / cTrader | MT4 / MT5 / LiteFinance | MT4 / MT5 (registration link says `mt4-cent`) |
| Scalping / news / EAs | "Scalping and news trading allowed", "Unlimited duration of transactions" | no restriction found | no restriction found |
| Islamic | yes | yes | yes |
| Sources | [ECN](https://www.litefinance.org/trading/account-types/ecn/) | [Classic](https://www.litefinance.org/trading/account-types/classic/) | [Cent](https://www.litefinance.org/trading/account-types/cent/), [FAQ 4.26](https://www.litefinance.org/support/faq/) |

**Is XAUUSD tradable on Cent? Ambiguous.** The commission PDF has a CENT column for XAUUSD (14-point markup), but the metals page says
"Available in trading accounts: ECN, CLASSIC". Cent is offered to residents of a listed set of countries (including Iran, Indonesia,
Malaysia, Vietnam, Nigeria) "as well as upon request" (SOURCED: Cent page). OPINION: ask support *and* open a Cent demo and read the MT
symbol spec. If Cent gold exists at 1/100 of standard (0.01 lot = 0.01 oz), it changes the micro-account math completely (§3).

**Swap-free.** It is automatic for residents of Afghanistan, Algeria, Bangladesh, Egypt, Indonesia and Iran, and by application for others. It is refused in China, Hong Kong,
Japan, Laos, Mongolia, Myanmar, South Korea, Taiwan, Thailand and Vietnam (SOURCED: [swap-free page](https://www.litefinance.org/trading/account-types/islamic-no-swap/)). The page says there are no extra fees or wider
spreads. Violations include a "prevalence of trades with negative swaps" and carry trades. For non-Islamic swap-free accounts, violations also include "holding positions open overnight before the
triple swap is charged" and "holding positions open for more than 5 days". Penalty: swaps charged retroactively. SOURCED:
[Islamic ToU](https://www.litefinance.org/uploads/documents/pdf-litefinance/litefinance-swap-free-accounts-terms-of-use-en.pdf),
[Swap-free ToU v2](https://www.litefinance.org/uploads/documents/pdf-litefinance/litefinance-swap-free-accounts-terms-of-use-v2-en.pdf).
OPINION: gold longs carry a large negative swap (−89 points), so an intraday system should be flat before 00:00 server time, and especially on Wednesday night.

### 1.3 What this means for a $13 account (MEASURED arithmetic, gold at $4,180)

| Quantity | 1:500 (normal) | 1:200 (news window) |
|---|---|---|
| Margin for 0.01 lot (1 oz) | $8.36 | $20.90 |
| Margin level with $13 equity | 155% | cannot open |
| Adverse move until margin level < 100% (broker "has the right" to close, §7.1) | $4.64 | - |
| Adverse move until stop-out at 20% | about $11.3 | - |
| Max concurrent 0.01 lots | 1 | 0 |
| Round-trip cost at 0.01 lot, ECN (spread $0.12-0.50 + $0.05) | $0.17-0.55 = 1.3-4.2% of equity per trade | - |

### 1.4 Other brokers with finer gold granularity (question: cent or 0.001 lots)

| Broker / account | What I could verify | Label |
|---|---|---|
| LiteFinance CENT | exists; min deposit $10; stop-out 50%; XAUUSD availability ambiguous (above) | SOURCED |
| RoboForex ProCent | XAUUSD card: "Size of 1 lot 100 oz", **min 0.10 lot, step 0.01**, avg spread 18 pips, session 01:05-23:55. On a cent-denominated account that is presumably 0.1 oz of real exposure (**$0.10 per $1 move**). I could not fetch the account page to confirm the cent denomination | SOURCED: [ProCent XAUUSD card](https://roboforex.com/forex-trading/trading/specifications/card/pro-cent/XAUUSD/); the denomination is OPINION |
| RoboForex ECN (for comparison) | 100 oz, min 0.01, avg spread 5 pips, commission "20 / mio" | SOURCED: [ECN card](https://roboforex.com/forex-trading/trading/specifications/card/pro-stan-ecn/XAUUSD/) |
| InstaForex | site lists "Cent accounts" and gold trading; gold cent contract size not found | SOURCED: [conditions](https://www.instaforex.com/trading_conditions); details not verified |
| Exness Standard Cent, XM Micro, FBS Cent, OANDA (unit sizing) | pages returned 403 or 404 to scripted fetches | **NOT VERIFIED.** Check by hand |

---

## 2. Documented intraday edges, as testable hypotheses (part A)

Each entry gives the claim with its source, the claimed effect size, a precise test on our data (`xau_alpha/data/m1_ba.parquet`, TRAIN/VALID only; TEST
is untouched per `lib/data.py`), and a credibility rating (OPINION) for spot XAUUSD in 2025-26. All times are exact clock times, and the lib's
`ny_mod` / `lon_mod` columns already handle DST.

### 2.0 MEASURED context: hour-of-day volatility and cost (Dukascopy bid/ask M1, 2025-01-21..2026-05-31)

Source: `xau_alpha/data/m1_ba.parquet` via `lib/data.load_m1()`, splits TRAIN+VALID. Range = mid high−low. Spread = ask close − bid close.

| UTC hour | median 1m range $ | median spread $ | spread / range |
|---|---|---|---|
| 00 | 1.38 | 0.67 | 0.48 |
| 01 | 1.85 | 0.67 | 0.36 |
| 04 | 0.95 | 0.64 | 0.68 |
| 07 | 1.40 | 0.59 | 0.42 |
| 10 | 1.20 | 0.59 | 0.49 |
| 12 | 1.61 | 0.59 | 0.37 |
| **13** | **2.22** | 0.61 | **0.28** |
| **14** | **2.29** | 0.61 | **0.27** |
| 15 | 1.91 | 0.60 | 0.31 |
| 16 | 1.52 | 0.59 | 0.39 |
| 18 | 1.14 | 0.58 | 0.51 |
| 20 | 0.92 | 0.59 | 0.65 |
| 21 | 0.86 | 0.65 | 0.76 |
| 22-23 | 1.05-1.08 | 0.70-0.71 | 0.66-0.67 |

OPINION: Dukascopy's median spread ($0.56-0.71) is wider than LiteFinance's ECN snapshots ($0.12-0.51) plus the $0.05 commission. If the snapshots are
representative, a Dukascopy-cost backtest is conservative for LiteFinance ECN. This is unverified until a week of live LiteFinance ticks is logged.

### 2.1 Macro releases: volatility yes, drift no [credibility HIGH, as a guard]
- **Claim.** Metal futures absorb US news "swift[ly]". At 08:30 ET, NFP and durable goods have the largest impact, and good economic news pushes gold down. The return impact
  "dissipates very quickly": the adjusted R² peaks at 8:30-8:35, and the response "tends to dissipate in less than an hour".
  NFP explains **35%** of the variance of the 5-min post-release gold return. A 1-σ durable-goods surprise moves gold about **0.06%** in 5 min.
  SOURCED: Elder, Miao, Ramchander (2012) *JBF*, [doi](https://doi.org/10.1016/j.jbankfin.2011.06.007), [pdf](https://mountainscholar.org/bitstream/10217/206884/1/Miao_H_BanFin_2012.pdf) (2002-2008 COMEX 1-min data).
  Cai, Cheung, Wong (2001) *JFM*: employment report, GDP, CPI and personal income have the greatest impact on COMEX gold volatility, and volatility has long memory
  ([doi](https://doi.org/10.1002/1096-9934(200103)21:3<257::aid-fut4>3.0.co;2-w)). Roache & Rossi (2010): gold reacts to US and Euro-area activity and rate
  news in a safe-haven direction ([IMF WP](https://www.elibrary.imf.org/downloadpdf/journals/001/2009/140/001.2009.issue-140-en.xml)).
  Christie-David, Chaudhry, Koch (2000) *JEB* ([doi](https://doi.org/10.1016/s0148-6195(00)00029-1)); Hess, Huang, Niessen (2008) ([doi](https://doi.org/10.1007/s11408-008-0074-x)).
  On our data: 56.6% of HIGH-impact release minutes show a ≥2x 1-min range spike, against 5.6% for a placebo. SOURCED: `recon/news_llm.md` §0.6.
- **H1a (null test, drift).** For HIGH releases at T (from `data/econ_calendar.csv`), regress r(T+5m→T+30m) and r(T+5m→T+60m) on sign(r(T→T+1m)) and on
  r(T→T+5m). Prediction from the literature: slope ≈ 0 for gold. A slope ≠ 0 that survives costs would be a new finding.
- **H1b (guard).** Block new entries in [T−2, T+10] min and compare the net R of an existing strategy with and without the block.
  LiteFinance may enforce 1:200 margin in [T−30, T+30] anyway (§1.1), so a $13 account cannot trade that window at all.
  `news_llm.md` also found the production freeze [T−15, T+5] blocks mostly quiet pre-release minutes and re-opens during the spike.

### 2.2 Time-of-day seasonality in volatility, volume and spread [HIGH]
- **Claim.** Precious-metal volume is n-shaped and peaks around 11:00-17:00 GMT (London and NY overlap). Gold volatility rises from 12:00 to 14:00 GMT. The bid-ask spread is lowest
  while Europe is open and ticks up around the 21:00-22:00 GMT break. SOURCED: Batten, Lucey, McGroarty, Peat (2017) *PLoS ONE*, 5-min data 2000-2015,
  [pdf](https://journals.plos.org/plosone/article/file?id=10.1371/journal.pone.0174232&type=printable). The Tokyo session is dominated by uninformed
  trading and the NY session by informed trading. SOURCED: Iwatsubo, Watkins, Xu (2018) *J. Commodity Markets*, [doi](https://doi.org/10.1016/j.jcomm.2018.05.001).
  New York futures lead price discovery even though they have under 10% of London's volume. SOURCED: Hauptfleisch, Putniņš, Lucey (2016) *JFM*,
  [pdf](https://opus.lib.uts.edu.au/bitstream/10453/41414/4/GoldILS%20JFutMkt%20Forthcoming.pdf).
- **H2.** For any fixed scalp rule, net R per trade in 12:00-16:59 UTC is greater than in all other hours. The MEASURED cost/range ratio above predicts
  the dead hours (03-05, 19-23 UTC) are net-negative for stops under $3.

### 2.3 Intraday momentum into the close [MEDIUM-HIGH for existence, UNKNOWN for spot gold after costs]
- **Claim.** In more than 60 futures across equities, bonds, **commodities** and currencies (1974-2020), the return from the previous close to 30 min before the close positively
  predicts the last-30-min return. The effect is "economically and statistically highly significant" and "reverts over the next days", and is linked to gamma hedging. SOURCED:
  Baltussen, Da, Lammers, Martens (2021) *JFE*, [doi](https://doi.org/10.1016/j.jfineco.2021.04.029) (abstract via Semantic Scholar). SPY: the first
  half-hour return predicts the last half-hour, more strongly on volatile, high-volume and macro-news days. SOURCED: Gao, Han, Li, Zhou (2018) *JFE*, [doi](https://doi.org/10.1016/j.jfineco.2018.05.009).
  In Chinese copper and steel futures the first half-hour predicts the last half-hour, most strongly after high-volume or high-volatility openings. SOURCED: Jin et al. (2019) *JFM*,
  [doi](https://doi.org/10.1002/fut.22084). Li, Sakkas, Urquhart (2022) confirm the effect globally but cover only 16 equity markets ([pdf](https://centaur.reading.ac.uk/95566/1/Accepted-Version.pdf)).
- **H3a.** "Close" = COMEX settlement. OPINION: GC settles about 13:30 ET, which I could not verify because the CME page returned 403. Signal: sign of r(prev 17:00 ET → 13:00 ET). Trade: enter at 13:00 ET in that
  direction and exit at 13:30 ET. **H3b.** Close = daily break: signal r(prev 17:00 ET → 16:30 ET), trade 16:30 → 16:59 ET.
  **H3c.** First-half-hour version: r(08:20→08:50 ET, the old COMEX pit open) predicts r(13:00→13:30 ET). Report t-stats by year and net of $0.20-0.65 costs.

### 2.4 Round numbers and order clustering [MEDIUM]
- **Claim.** Gold prices at round numbers act as barriers, with effects on the conditional mean and variance. SOURCED: Aggarwal & Lucey (2007) *RFE*,
  [doi](https://doi.org/10.1016/j.rfe.2006.04.001). In FX, take-profit orders cluster *at* round numbers, which predicts reversals, and stop-losses cluster *just beyond*
  them, which predicts accelerations after a cross. SOURCED: Osler (2003) *JF*, [doi](https://doi.org/10.1111/1540-6261.00588). Stop-loss cascades are faster and last longer than
  take-profit reactions. SOURCED: Osler (2005) *JIMF*, [pdf](https://www.econstor.eu/bitstream/10419/60715/1/351236996.pdf). A later gold and silver
  update (Lucey & O'Connor 2016 *FRL*, [doi](https://doi.org/10.1016/j.frl.2016.03.009)) exists, but its text returned 403 and its result is not used here.
- **H4a (reversal at first touch).** Levels L = multiples of $10, with $50 and $100 tested separately. Event: the M1 high reaches L from below after the prior 60-min max was ≤ L − $1
  (mirror for touches from above). Measure forward mid returns at 1, 5, 15 and 30 min. Placebo levels: L + $3.7. **H4b (cascade after cross).** Event: close ≥ L + δ,
  δ ∈ {0.3, 0.5, 1.0}. Measure continuation over 5-15 min against the placebo.

### 2.5 Liquidity sweep / "turtle soup" / judas swing of session extremes [MEDIUM-LOW, mechanism SOURCED, rule untested]
- **Mechanism.** Stop cascades beyond obvious levels (Osler 2005). ICT's "judas swing" and Raschke's "turtle soup" are practitioner rules. I found no peer-reviewed test.
  Iwatsubo et al. (Tokyo = uninformed trading) suggest the Asian range is less informative. OPINION.
- **H5.** Reference extremes: the prior NY trading day's high and low (17:00 ET roll), and the Asian range 00:00-06:59 UTC. Sweep: price trades beyond the extreme by
  δ ∈ {$0.5, $1, $2} during 07:00-10:00 UTC or 12:30-15:00 UTC, then an M1 (or M5) close is back inside within N ∈ {5, 15, 30} min. Entry: fade at that close.
  Stop: sweep extreme ± $0.3. Targets: range midpoint and the opposite extreme. Control: the same rule at random times, and the breakout-continuation twin (H8).

### 2.6 London AM/PM fix (LBMA auction 10:30 / 15:00 London) [MEDIUM-LOW today]
- **Claim.** In the telephone-fix era, GC futures and GLD showed elevated volume and volatility right after the PM fixing started, with "statistically significant return advantages in
  the 4 minutes following the start". Opening-minute trades predicted the fix direction ">90%" in some cases, and nothing happened after publication. SOURCED: Caminschi & Heaney
  (2014) *JFM*, [doi](https://doi.org/10.1002/fut.21636). Since 2015 the fix has been an IBA electronic auction held in 30-second rounds until the buy/sell imbalance is within threshold.
  SOURCED: [ICE IBA](https://www.ice.com/iba/lbma-gold-silver-price), [LBMA](https://www.lbma.org.uk/prices-and-data/about-lbma-daily-auction-prices).
  In 2014 the FCA fined Barclays £26m, including for a 2012 manipulation of the fixing. SOURCED: [Wikipedia summary](https://en.wikipedia.org/wiki/Gold_fixing).
- **MEASURED (descriptive).** Median |5-min move| starting at 14:59 London is **$3.08**, against $2.53 at 13:59 London and $1.16 at 11:29. At 10:29 it is $1.34, against $1.33 at 09:29.
  About 350 days each. **This is confounded:** in summer, 15:00 London = 10:00 ET, the time of ISM, JOLTS and confidence releases.
- **H6.** On non-release days only: sign of r(14:59→15:03 London) predicts r(15:03→15:10). Do the same for 10:29→10:33 / 10:33→10:40. Compare against the same statistic at ±1 h.

### 2.7 Opening-range breakout [MEDIUM-LOW for gold]
- **Claim.** On crude-oil futures (1983-2011, daily OHLC, no costs), an ORB rule with a threshold from the open had a success rate of about 0.60-0.71 and a significant positive
  mean return over the full sample. The paper says the 2001-2011 subsample drives the result; the 1983-1992 and 1992-2001 subsamples are weaker and mixed. SOURCED: Holmberg, Lönnbark, Lundström (2013) *FRL*,
  [pdf](http://www.econ.umu.se/DownloadAsset.action?contentId=196616&languageId=3&assetKey=ues845). On index futures (2003-2013), "timely ORB"
  earned more than 8% a year, and 20.3% on TAIEX. SOURCED: Tsai et al. (2019) *IEEE Access*, [doi](https://doi.org/10.1109/access.2019.2899177).
  A gold-futures thesis exists (Sönnert 2015, "ORB and GARCH on gold futures") but sits behind a bot wall. Title only, not used.
- **H7.** Opening ranges: London 07:00-07:29 UTC; US data 08:30-08:44 ET; COMEX 08:20-08:34 ET. Entry: first M1 close beyond range ± 0.1×range. Stop: opposite side
  (variant: midpoint). Exit at 16:00 UTC or at 2R. Filter: range / 20-day ATR in its 20-80th percentile. Report by year, net of costs.

### 2.8 Asian range breakout [LOW, practitioner lore only]
- No academic source found. **H8.** Build the Asian range 00:00-06:59 UTC. Breakout rule: first close beyond it during 07:00-10:00 UTC, stop at the range midpoint, targets 1R and 2R.
  Test it against its own fade twin (H5). OPINION: Iwatsubo et al. would favour the fade.

### 2.9 Overnight vs daytime returns [LOW as a scalp signal]
- **Claim.** COMEX front-month overnight returns were significantly positive and day returns significantly negative, 1985-2012. The asymmetry "has weakened substantially"
  but was still present. SOURCED: Blose & Gondhalekar (2014) *AEL*, [doi](https://doi.org/10.1080/13504851.2014.922661). An earlier working paper by
  Blose (2011) reported the opposite sign ([OpenAlex](https://api.openalex.org/works?filter=title.search:overnight%20and%20weekend%20gold%20returns)). The evidence conflicts.
- **H9.** Mean r(22:05→13:20 UTC) vs r(13:20→20:55 UTC), 2025-26. De-mean by the daily return, because the 2025-26 gold trend dominates. Use it only as a
  directional *bias* filter for H2/H5/H7.

### 2.10 Volatility clustering and regime filters [MEDIUM as a filter, LOW as an edge]
- **Claim.** Gold's high-frequency volatility has long memory once the intraday pattern is filtered out (Cai et al. 2001). Scaling exposure inversely to volatility raised Sharpe
  ratios across factors (Moreira & Muir 2017 *JF*, [pdf](https://onlinelibrary.wiley.com/doi/pdfdirect/10.1111/jofi.12513)). Out-of-sample robustness of that result is disputed
  (Cederburg et al. 2020 *JFE*, [doi](https://doi.org/10.1016/j.jfineco.2020.04.015); abstract not retrieved).
- **H10.** Compute RV over the last 60 min divided by the same-minute-of-day median RV over 20 days. Test whether a scalp's net R is higher in the 30th-80th percentile band and negative in the bottom 30%
  (cost dominates, see §2.0) and the top 5% (news and slippage).

### 2.11 Short-horizon momentum vs reversal (1-60 min) [UNKNOWN]
- Intraday momentum in FX has been attributed to liquidity provision rather than late-informed trading. That is how Shen, Urquhart, Wang (2021)
  ([doi](https://doi.org/10.1111/fire.12290)) summarise Elaut, Frömmel, Lampaert, *J. Financial Markets* ([doi](https://doi.org/10.1016/j.finmar.2016.09.002)).
  Elaut's own abstract was not retrievable. "Does intraday technical trading have predictive power in precious metal markets?"
  (Batten et al. 2017 *JIFMIM*, [doi](https://doi.org/10.1016/j.intfin.2017.06.005)) is directly on point, but its abstract was not retrievable. **Read it before building H11.**
- **H11.** Variance ratios VR(q), q ∈ {5, 15, 30, 60} M1 bars, by session (Asia 00-07, London 07-12, NY 12-17, late 17-21 UTC). VR < 1 suggests fading
  and VR > 1 suggests following. Then test the simple rule: after |r(t−q, t)| > k·σ_q, trade with or against the move for q minutes.

### 2.12 ICT concepts [LOW: no systematic evidence found in either direction]
- Definitions below are from a fan tutorial site, not a primary source. Silver-bullet windows: **03:00-04:00, 10:00-11:00, 14:00-15:00 New York time**
  ([innercircletrader.net](https://innercircletrader.net/tutorials/ict-silver-bullet-strategy/)). OTE: Fibonacci retracement zone **0.62-0.79, "precise" 0.705**
  ([OTE page](https://innercircletrader.net/tutorials/ict-optimal-trade-entry-ote-pattern/)). FVG (common definition, OPINION): a 3-bar pattern where bar 1's high < bar 3's low (bullish).
- Search results. OpenAlex and arXiv returned no peer-reviewed tests for "smart money concepts", "fair value gap", "order block" or "inner circle trader". The only hits were auto-generated
  "E8 Intelligence Research" Zenodo entries with no credible method, for example [this one](https://doi.org/10.5281/zenodo.22006322). OPINION: not evidence.
- **H12a.** FVG on M1 and M5 with gap ≥ $0.5, formed inside the 10:00-11:00 ET window. Limit entry at the gap midpoint for 30 min. Stop beyond bar 1's extreme; TP 2R.
  Placebos: the same rule outside the windows, and random entry times with the same stop and TP. **H12b.** OTE: after an M5 swing ≥ $5 (ZigZag), limit at 0.705 retrace, stop at 1.0,
  target −0.27 extension. Placebo retrace levels are 0.5, 0.6 and 0.8. If 0.705 does not beat the neighbouring levels, the OTE claim fails.
- **H12c (judas).** Covered by H5 with the reference extreme = the 00:00 ET open price and the sweep window = 02:00-05:00 ET.

### 2.13 Base rates and micro-account literature
- 97% of Brazilian equity-futures day traders who persisted for at least 300 days lost money; 0.4% earned more than a bank teller; there was no evidence of learning. SOURCED: Chague, De-Losso,
  Giovannetti (2019), [doi](https://doi.org/10.2139/ssrn.3423101). In Taiwan 1992-2006, fewer than 1% of day traders "predictably and reliably earn positive abnormal returns net of fees".
  SOURCED: Barber, Lee, Liu, Odean (2014) *JFinMkts*, [pdf](https://escholarship.org/content/qt7k75v0qx/qt7k75v0qx.pdf). RoboForex's CFD disclosure says 75.85% of retail accounts lose money. SOURCED: RoboForex card page.
- Kelly: the growth-optimal fraction (Kelly 1956, [doi](https://doi.org/10.1002/j.1538-7305.1956.tb03809.x)). Betting 2x Kelly gives about zero growth, and betting more gives negative growth.
  SOURCED: Ziemba & Ziemba, "Good and bad properties of the Kelly criterion", [doi](https://doi.org/10.1002/9781119206095.ch4). MacLean, Thorp, Ziemba (2010) *QF*
  ([doi](https://doi.org/10.1080/14697688.2010.506108)); Thorp (2006) ([doi](https://doi.org/10.1016/s1872-0978(06)01009-x)).

### 2.14 News and LLM literature (for the repo's news guards and the Laya/Jeff models)
- GPT-4 scores on *post-cutoff* headlines predict stock reactions and the subsequent drift. Strategy returns decline over time. SOURCED: Lopez-Lira & Tang (2023),
  [arXiv 2304.07619](http://arxiv.org/abs/2304.07619v6).
- Backtests inside the training window suffer from look-ahead bias and a "distraction effect". SOURCED: Glasserman & Lin (2023), [arXiv 2309.17322](http://arxiv.org/abs/2309.17322v1).
  LLMs have memorized economic and financial data; instructions to respect a cutoff and masking both fail. SOURCED: Lopez-Lira et al. (2025),
  [arXiv 2504.14765](http://arxiv.org/abs/2504.14765v2).
- Gold-specific: "News sentiment in the gold futures market" (Smales 2014 *JBF*, [doi](https://doi.org/10.1016/j.jbankfin.2014.09.006); full text blocked by a WAF, title
  only). A labelled gold-news-headline dataset exists: Sinha & Khandait (2020), [arXiv 2009.04202](http://arxiv.org/abs/2009.04202v1).
- OPINION, combined with `recon/news_llm.md`: Laya is a general NLU model with no market training, and Jeff is not deployed, so neither has a valid track record.
  A valid test is forward only: log every decision live and compare vetoed against allowed setups after 200 or more events. The deterministic calendar freeze is the only
  guard that can be backtested (and `news_llm.md` has already built a replay for it).

---

## 3. Micro-account math with discrete lots (MEASURED: `xau_alpha/recon/microlot_ruin.py`)

Assumptions: E0 = $13, goal $100, 20,000 paths, up to 4,000 trades. Win pays +b·D − c and loss pays −D − c per oz. "Forced" means 1 minimum step is traded
even when the 2% risk target rounds to 0. Ruin = equity below one stop at the minimum step. ECN cost c = $0.20/oz, Dukascopy-like c = $0.65/oz.

| edge (gross) | stop D | cost | step | risk at 1 step | Kelly f* | log-growth/trade | P(hit $100) | P(ruin) | median trades to $100 |
|---|---|---|---|---|---|---|---|---|---|
| p=.45 b=1.5 (+0.125R) | $2 | 0.20 | **1 oz (0.01 lot)** | 16.9% | 1.6% | −0.018 | 15% | 85% | 94 |
| same | $4 | 0.20 | 1 oz | 32.3% | 4.9% | −0.055 | 18% | 83% | 23 |
| same | $4 | 0.20 | 0.1 oz | 3.2% | 4.9% | +0.0016 | 93% | 5% | 1,656 |
| same | $4 | 0.20 | 0.01 oz | 0.3% (sized at 2%) | 4.9% | +0.0012 | 97% | 0% | 1,575 |
| same | $4 | 0.65 | 1 oz | 35.8% | 0 | −0.120 | 7% | 93% | 16 |
| p=.55 b=1.0 (+0.10R) | $4 | 0.20 | 1 oz | 32.3% | 5.0% | −0.039 | 17% | 83% | 36 |
| same | $4 | 0.20 | 0.01 oz | (2%) | 5.0% | +0.0008 | 87% | 0% | 2,196 |
| same | $2 | 0.20 | 1 oz | 16.9% | **0** (costs eat the edge) | −0.015 | 12% | 89% | 128 |
| same | $2 | 0.20 | 0.1 / 0.01 oz | (2%) | 0 | −0.0002 | 1-3% | 32% / 0.4% | - |

Read-out (OPINION on MEASURED numbers):
- With a 1-oz minimum, a $13 account is **over-betting by about 6-11x Kelly** where an edge survives costs, and infinitely where it does not. Log growth is negative even with a real edge,
  so the 12-18% who reach $100 are lottery winners (median 23-128 trades). Expect to lose the stake about 5 times in 6.
- The single biggest improvement is **lot granularity** (a cent account, or 0.1-oz steps), not a better signal. Next are **wider stops ($4+) with low cost per trade**:
  a $2 stop lets a $0.20 cost eat most of a +0.10R edge.
- A strategy's edge must be quoted **net of LiteFinance's real cost** (see §1.1) in R units. `lib/account.py` currently assumes 1:1000 leverage, but the instrument page shows
  1:500 for XAUUSD. The margin figures there should be halved: margin per 0.01 lot is about $8.4, not $4.2.

---

## 4. Top 10 hypotheses ranked by credibility (OPINION)

| # | Hypothesis | Core test | Evidence strength |
|---|---|---|---|
| 1 | Macro releases are volatility events with no post-5-min drift → guard, not signal (H1) | r(T+5→T+60) vs sign r(T→T+1) = 0; net R with and without the [T−2, T+10] block | 4+ peer-reviewed gold papers |
| 2 | Trade only 12-17 UTC; dead hours are net-negative after costs (H2) | same rule, hour buckets | Batten 2017, Iwatsubo 2018 + MEASURED cost/range |
| 3 | Intraday momentum into the COMEX settle or the daily close (H3) | r(prev 17:00 ET→13:00 ET) → r(13:00→13:30 ET) | Baltussen 2021 (60+ futures incl. commodities), Gao 2018 |
| 4 | Round-number reversal at first touch / cascade after a cross (H4) | $10/$50/$100 levels vs placebo L+3.7 | Aggarwal-Lucey 2007 (gold), Osler 2003/2005 (FX) |
| 5 | Volatility-regime filter (skip the bottom 30% and top 5% of time-of-day-normalized RV) (H10) | net R by RV percentile | Cai 2001; Moreira-Muir 2017 (disputed OOS) |
| 6 | Sweep-and-reclaim of prior-day or Asian extremes (turtle soup / judas) (H5) | δ, N grid vs random-time placebo | mechanism: Osler 2005; rule: practitioner only |
| 7 | PM/AM fix continuation during the auction on non-release days (H6) | r(14:59→15:03) → r(15:03→15:10) London | Caminschi-Heaney 2014 (pre-2015 regime) |
| 8 | ORB at London 07:00 / US 08:30 ET / COMEX 08:20 ET (H7) | first close beyond range ± 0.1R, stop opposite | Holmberg 2013 (oil), Tsai 2019 (indices) |
| 9 | Overnight long bias vs day short bias (H9) | de-meaned session returns 2025-26 | Blose-Gondhalekar 2014 (weakening; conflicting earlier paper) |
| 10 | ICT FVG / OTE / silver-bullet windows (H12) | rule vs placebo levels and windows | none found (untested) |

---

## 5. Open items that need a human or the live terminal
1. On the **live** LiteFinance MT5 account, read `symbol_info("XAUUSD")`: `trade_contract_size`, `volume_min`, `volume_step`, `trade_stops_level`,
   `trade_freeze_level`, `margin_initial` or `order_calc_margin(0.01)`, and the session times. Log bid/ask ticks for 5 or more trading days to replace the spread snapshots above.
   This is a read-only script. It must be written under `xau_alpha/`, not in production code.
2. Ask LiteFinance support whether XAUUSD is tradable on **CENT**, and what the Cent contract size and margin for gold are.
3. Check Exness Standard Cent, XM Micro, FBS Cent and OANDA gold minimum sizes by hand (scripted fetches returned 403 or 404).
4. Retrieve the full texts of Batten et al. 2017 (*JIFMIM*, intraday technical trading in precious metals) and Lucey & O'Connor 2016 before building H4 and H11.
