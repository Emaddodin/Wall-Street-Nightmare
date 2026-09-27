# LiteFinance XAUUSD execution costs: what the owner's logs actually show

Date: 2026-09-30. Scope: read-only audit of `logs/`, `data/`, `data/state/`, the LiteFinance screenshots in `data/`, and `engine/litefinance_gateway.py`. Dukascopy comparison uses `data/candles/duka_raw/*.bi5` and `data/candles/duka/*.csv`.
Script: `xau_alpha/recon/broker_costs_measure.py` (single process, about 50 s). Raw output: `xau_alpha/recon/broker_costs_tables.json`.

Labels: **MEASURED** means computed here from local files. **SOURCED** means quoted from a named file. **OPINION** means judgement, not measurement.

---

## 0. Bottom line

1. **The real execution sample is very small.** MEASURED:
   - 6 filled market orders, all on the DEMO account on one day (2026-09-28, 17:29 and 17:32 UTC), grouped into 2 baskets of 3 x 0.01 lot.
   - 9 order attempts that failed (2026-09-29).
   - 10 LiteFinance bid/ask quotes, taken on 3 days, falling into 4 clusters of UTC time of day.
   - 0 fill prices logged, 0 broker-side stop-loss hits, 0 requotes, 0 broker rejections.
   - Everything else in `logs/` and `data/` with trade-like records is a simulation on synthetic prices (see section 1).

   So the numbers below are indicative, not statistically robust. The recommended model is deliberately conservative wherever the data is thin.
2. **LiteFinance spread is much tighter than Dukascopy's.** MEASURED:
   - 0.22 $/oz in 8 of 10 quotes (09:59 to 17:32 UTC).
   - 0.12 at 20:48 and 20:55 UTC.
   - 0.47 at 23:25 UTC.
   - Dukascopy at the same instants was 0.69 to 0.83. The ratio LiteFinance/Dukascopy ranged from 0.16 to 0.57, so no single multiplier fits.
3. **LiteFinance prices match Dukascopy closely.** MEASURED:
   - Once the quote stream is steady, LiteFinance mid minus Dukascopy mid = **+0.07 ± 0.06 $/oz** (n=6) at lag 0.
   - The quotes line up best at 0 s. Shifting by ±5 s makes the dispersion 4 to 13 times worse.
   - The first quote read after a page load can be stale. One case was 52 s stale during an $18 spike.
4. **Latency is about 1.1 s per order.** MEASURED:
   - Decision to confirmed position: median **1.11 s**, range 0.84 to 1.83 s (n=7). About 0.65 s of that is fixed sleeps inside the gateway.
   - Closing a 3-ticket basket: **2.58 s** (n=2).
   - The news guard (`politician_sentinel`) plus the Laya veto add **7 to 32 ms** between signal and order dispatch (n=5). That is negligible.
5. **Commission: none is visible, and cannot be pinned down.** MEASURED: the realized P/L of both baskets falls inside the zero-commission range reconstructed from Dukascopy. The resolution is too coarse to rule out a few $/lot.
6. **Margin is 1:500 on gold, not 1:1000.** MEASURED:
   - 0.01 lot needs **$8.27 to $8.55**, which is notional/500, on both DEMO and REAL.
   - Consequence: **a $13 account can hold at most one 0.01-lot position.**
   - One round trip at base cost is about 2.8% of $13 equity.
7. **The UI gateway is the biggest execution risk.** MEASURED:
   - On 2026-09-29, all 9 of 9 order attempts (3 signals) failed with `NO_VALID_DIRECTION_ORDER_BUTTON`.
   - 2 of 8 gateway starts failed.

---

## 1. What exists in the logs, and what is real

| Source | Content | Real LiteFinance data? |
|---|---|---|
| `logs/ghost_grid.log` (2026-09-25 to 09-29) | Gateway starts, 5 signals, 6 order confirmations with latency, 2 flattens with balance, 9 failed orders | **Yes** (DEMO). The only execution log |
| `data/state/hft.json` | One bid/ask snapshot, 2026-09-29 11:12:05 UTC | **Yes** |
| `data/demo_system_trade_journal.json` | 1 trade (0.01 lot), `order_latency_ms` 1227, entry 4273.17 (= mid of the screenshot quote) | **Yes**. The entry is the quote mid, not a fill |
| `data/demo_*.png` (4 screenshots, 2026-09-24) | Web-terminal bid/ask, margin per 0.01 lot, balance | **Yes** (3 DEMO, 1 REAL) |
| `data/preflight_pentest_report.json` (2026-09-21 ~19:20 UTC) | BUY/SELL 0.01 round trips with balances; stream tick rate | **Yes**, but an older gateway (a 24 ms "latency" is only the JS click). Account type not recorded |
| `logs/xau_live.log`, `logs/xau_live_demo.log`, `data/paper.json` | "STACKING EXECUTED", latency 1 to 21 ms | **No**. Replay of the synthetic `data/candles/gold_m1_*.json` (prices near 2,500 while real gold was near 4,300) |
| `data/stratton_vault.json` latency_metrics (4.11 ms) | Internal dispatch timer | **No broker round trip** |
| `logs/backtest_live_6mo.log` (18 `ORDER_REJECTED_SL_ENVELOPE`) | Internal risk-rule rejections in a backtest | **No**. These are not broker rejections |
| `data/state/guard_history.json`, `doctor_telemetry.json` | Host health (stale telemetry, disk 92.4%), Qwen2.5-1.5B "doctor" | No execution content |
| `data/*_journal.csv` (apex, r2, r3, junwin, rep60, real_live30, ...) | Backtest journals | **No** |

Time bases (MEASURED):
- `ghost_grid.log` uses the Mac's local clock, Asia/Tehran UTC+03:30. This was checked against `hft.json`: epoch 1790680325.96 is 11:12:05 UTC, and the last log line is 14:41:25 local.
- Screenshots show the LiteFinance clock "(UTC+3)". File modification times are 18 to 58 s later than the in-image clock, so the in-image clock is used. The closed-trade image clock (23:25:31 UTC) matches the journal timestamp (23:25:31.955 UTC) to the second.

## 2. How orders are sent (from `engine/litefinance_gateway.py`)

These are facts read from the code:
- **The gateway does not use MT5.** Headless Chromium (Playwright) drives the LiteFinance **web terminal** at `my.litefinance.org/trading/chart?symbol=XAUUSD`, using a saved cookie session.
- **Quotes** come from a MutationObserver on the `.js_value_price_bid` and `.js_value_price_ask` DOM elements, with a 50 ms polling fallback. The timestamp is the local receive time. Quotes have 2 decimals.
- **Market order steps:**
  1. Click the BUY/SELL tab, then sleep 150 ms.
  2. Fill `#volume_value_1`, then sleep 100 ms.
  3. Open the SL drawer and fill `#stop_loss_price_1`. The SL value is read back, and the order is refused if it does not match. The **SL is attached in the ticket, so it is a server-side stop.** TP is optional and not used by Ghost Grid.
  4. Click the green or red `js_trade_action_open` button, then sleep 200 ms.
  5. Scan for error popups, then poll every 200 ms for up to 3 s until "assets used" or the open-trades row count increases.
- **The logged "Latency" runs from function entry to that confirmation**, so it includes about 0.65 s of fixed sleeps. **No fill price is ever read or logged.**
- **Ghost Grid (HEAD) entry bookkeeping:** `entry_price` = the signal quote's ask (BUY) or bid (SELL), not the fill. The disaster SL = signal bid − 2.50 (BUY) or ask + 2.50 (SELL). This rule lets the signal bid/ask be recovered from the log (MEASURED; section 3).
- **Flatten:** open the portfolio drawer, then click "close all" or click each ticket's close button and confirm, looping until "assets used" is 0. The logged latency is the whole loop.
- **Rejection detection** is a text match on popups ("Not enough funds", "rejected", ...). None was ever logged.

## 3. LiteFinance quotes vs Dukascopy ticks at the same UTC time

The Dukascopy value is the last tick at or before the LiteFinance time. Negative offsets mean LiteFinance is below Dukascopy. All rows are MEASURED except where the source column says otherwise.

| UTC | Acct | LF bid | LF ask | **LF spr** | Duka spr | LF bid − Duka bid | LF ask − Duka ask | LF mid − Duka mid | Source |
|---|---|---|---|---|---|---|---|---|---|
| 09-24 20:48:38 | REAL | 4271.54 | 4271.66 | **0.12** | 0.73 | +0.43 | −0.19 | +0.12 | demo_switch_verification.png |
| 09-24 20:55:29 | DEMO | 4274.44 | 4274.56 | **0.12** | 0.70 | +0.39 | −0.20 | +0.10 | demo_ready.png |
| 09-24 23:25:07 | DEMO | 4272.93 | 4273.40 | **0.47** | 0.83 | +0.32 | −0.05 | +0.14 | demo_trade_open.png |
| 09-24 23:25:31 | DEMO | 4272.78 | 4273.25 | **0.47** | 0.82 | +0.29 | −0.07 | +0.11 | demo_trade_closed.png |
| 09-28 17:29:00.265 | DEMO | 4134.85 | 4135.07 | **0.22** | 0.70 | +0.21 | −0.28 | −0.04 | log: SELL signal mid 4134.96, SL 4137.57 = ask+2.50 |
| 09-28 17:32:01.268 | DEMO | 4139.07 | 4139.29 | **0.22** | 0.69 | +0.24 | −0.24 | 0.00 | log: BUY signal mid 4139.18, SL 4136.57 = bid−2.50 |
| 09-29 09:59:00 | DEMO | 4145.77 | 4145.99 | **0.22** | n/a | | | | log (orders failed) |
| 09-29 10:56:00 | DEMO | 4151.73 | 4151.95 | **0.22** | n/a | | | | log (orders failed) |
| 09-29 11:01:00 | DEMO | 4154.21 | 4154.43 | **0.22** | n/a | | | | log (orders failed) |
| 09-29 11:12:05 | DEMO | 4154.72 | 4154.94 | **0.22** | n/a | | | | data/state/hft.json |
| 09-21 ~19:20 | ? | mid 4345.33 | | (0.22)* | 0.66 | | | inside Duka range 4344.83 to 4346.56 | preflight pentest |

\* The pentest's floating P/L 3 s after a 0.01-lot BUY was −$0.22. That is consistent with a 0.22 spread (1 oz). No Dukascopy files exist yet for 2026-09-29 (the local set ends 2026-09-28 23:00 UTC).

**Spread (MEASURED, n=10 quotes plus 1 inferred):**
- LiteFinance XAUUSD spread took only three values:
  - 0.22 (09:59 to 19:20 UTC, 3 days)
  - 0.12 (20:48 to 20:55 UTC, 1 day, on both REAL and DEMO)
  - 0.47 (23:25 UTC, 1 day, about 25 min after the daily reopen)
- The steady 0.22 looks like a fixed or floored schedule rather than a pass-through of market spread (OPINION).
- LiteFinance quotes sit **inside** Dukascopy's: LF bid is 0.21 to 0.43 above the Duka bid, and LF ask is 0.05 to 0.28 below the Duka ask.
- 0.22 at $4,150 = 0.53 bps.

**Price offset and lag (MEASURED):**
- For the 6 quotes that have Dukascopy coverage, LF mid − Duka mid = **+0.071 mean, 0.064 sd** at lag 0.
- Pooling those 6 and shifting Dukascopy by L seconds, the sd is 0.064 at L=0, 0.28 at L=+5 s, 0.86 at L=−5 s, and 0.25 to 1.0 elsewhere in ±60 s. The best common lag is 0 s.
- Per-quote scans are ambiguous (price revisits the same level). The two 09-28 signal quotes match within 0.05 at lag 0.
- Conclusion: **LiteFinance web quotes track Dukascopy with no measurable offset (under 0.15 $/oz) and a lag of at most a few seconds.** The resolution is limited by n=6.

**Stale first quote after connect (MEASURED):**
- The 6 "Fast Warmup" seeds are the first DOM quote after each page load. They differ from Dukascopy by −0.29, −0.58, −0.65, −1.03, +0.06 and **−8.15**.
- The −8.15 case was at 2026-09-28 17:12:12.7 UTC. Gold spiked from 4126.5 to 4145.1 between 17:11:15 and 17:12:00 (Dukascopy). The LiteFinance value 4132.75 equals the Dukascopy mid at about 17:11:20, so it was **about 52 s stale**.
- OPINION: discard quotes for the first ~10 s after any (re)connect.

**Daily bar check (MEASURED, 2026-09-24, both bid):**
- High: LiteFinance 4303.58 vs Dukascopy 4302.87 (+0.72).
- Low: 4244.14 vs 4243.93 (+0.22).
- Price at 20:55: 4274.44 vs 4274.06 (+0.39).
- These differences are consistent with LF bid ≈ Duka bid + 0.2 to 0.4 (the narrower LF spread). The open differs by 2.05 (4290.24 vs 4288.19). The cause is unknown, possibly a different first tick at the 00:00 UTC boundary.

## 4. Latency, slippage, commission, failures

### 4.1 Latency (MEASURED from `ghost_grid.log` plus the journal)

| Step | n | Values | Summary |
|---|---|---|---|
| Signal log → first order dispatch (news guard + Laya veto + tier resolution) | 5 | 22, 32, 22, 7, 22 ms | ≤ 32 ms |
| Order function entry → confirmed on account (logged "Latency") | 7 | 842, 1022, 1033, 1106, 1227, 1611, 1834 ms | median 1.11 s, mean 1.24 s |
| …of which fixed sleeps in the gateway | | 150 + 100 + 200 + ≥200 ms | ≥ 0.65 s |
| Signal → 1st position confirmed | 2 | 1.07 s, 1.66 s | |
| Signal → 3rd position confirmed (sequential, 0.15 to 0.35 s random tap gap) | 2 | 3.57 s, 5.08 s | about 1.2 to 1.5 s per extra order |
| Basket stop trigger → flatten complete (3 to 4 tickets) | 2 | 2575, 2578 ms | about 0.86 s/ticket |
| Failed order attempt (volume field invisible → fallback → no button) | 9 | 4.86 to 5.09 s each | |
| Gateway start → connected | 6 ok | 20, 23, 27, 27, 29, 37 s | |
| Signal time after M1 close | 5 | +0.047 to +0.522 s | |

The fill instant lies somewhere between the button click (about 0.3 to 0.5 s after function entry) and confirmation. OPINION: a realistic decision-to-fill delay is **0.5 to 2 s**.

### 4.2 Entry and exit slippage: the two filled baskets, reconstructed on Dukascopy (MEASURED, n=2)

LiteFinance-equivalent prices = Dukascopy mid + 0.071 ± 0.11 (half of the 0.22 spread). "Slip" is adverse-positive, in $/oz, versus the signal quote the engine booked.

| | B1 SELL 3×0.01 | B2 BUY 3×0.01 |
|---|---|---|
| Signal quote (LF) bid/ask | 4134.85 / 4135.07 | 4139.07 / 4139.29 |
| Entry slip vs signal quote (fills at the confirm times) | **+0.30** | **−0.05** (favourable) |
| Engine's internal basket P/L at stop trigger | −4.11 | −4.26 |
| Exit drift from stop trigger to flatten done (2.58 s) | **+0.12** adverse | **+0.37** adverse |
| Realized P/L from broker balance | **−5.14**† | **−4.29** (26.40 → 22.11) |
| Zero-commission model, point estimate (confirm-time fills, flatten-done exit) | −5.26 | −4.19 |
| Zero-commission model, full range (fills anywhere in their windows) | −6.49 to −4.08 | −5.15 to −2.52 |

† B1 assumes the pre-trade balance equals `broker_baseline` 31.54 in `ghost_grid_state.json` (SOURCED). Caveat: the flatten reported "Closed 4 tickets" for 3 opened orders.

- Realized − model point = +0.12 and −0.10 → **net ≈ 0**. Execution cost beyond spread plus price drift during latency is not detectable in these two baskets.
- Dukascopy 1-second study (MEASURED, 2026-06-01 to 09-28, 2,115 hour files, empty/weekend hours skipped), price change over a delay L:

  | L | mean abs(Δmid) | p90 | p99 |
  |---|---|---|---|
  | 1 s | 0.12 | 0.31 | 0.81 |
  | 2 s | 0.18 | 0.44 | 1.10 |
  | 3 s | 0.23 | 0.54 | 1.32 |
  | 5 s | 0.31 | 0.71 | 1.67 |

  After a ≥ $0.50 move in the previous 5 s (a stop-like situation), the next L seconds continue by a mean of **−0.004 to −0.008** (1.31M samples). So there is **no systematic adverse continuation at 1 to 5 s. Latency slippage is zero-mean noise** of the size above.
- By hour of day, the mean 2 s move is largest at 13 to 14 UTC (0.30 to 0.32) and smallest at 20 UTC (0.09).
- **Broker-side SL slippage: no data** (no SL was ever hit). **Requotes: 0 seen** (and the gateway has no requote handling).

### 4.3 Commission

- MEASURED: no commission field or line appears in any log or screenshot.
- Two independent checks:
  - Section 4.2: realized P/L minus the zero-commission model is about 0 ± 1 $ per 0.03-lot basket.
  - Pentest (SOURCED `preflight_pentest_report.json`): BUY round trip 0.01 lot changed the balance by −$0.27; SELL round trip by −$0.13. The spread was 0.22. The average of −$0.20 per 0.01 lot is at or below the spread, which is consistent with no commission.
- **Neither check can exclude a commission of a few $/lot** because price drift over the 3 to 5 s holds is ±0.1 to 0.3 $/oz.
- LiteFinance's published fee schedule could not be checked in this session (web tools failed). **Base = $0, Harsh = $7 per lot round turn (OPINION, a hedge).**
- Swap: the account is swap-free per `NOTES.md:36` and `AGENT_BRIEFING.md:45` (SOURCED). This is irrelevant for intraday.

### 4.4 Failures (MEASURED)

| Event | Count |
|---|---|
| Order attempts that confirmed | 6 of 15 (all 6 on 09-28) |
| Order attempts that failed: `#volume_value_1` not visible → `NO_VALID_DIRECTION_ORDER_BUTTON` | **9 of 15, i.e. 3 of 5 signals missed** (all on 09-29, systematic UI break, fail-safe: no position) |
| Broker rejections / requotes / "Not enough funds" | 0 |
| Gateway start failures | 2 of 8 (Playwright browser missing 09-25; `page.goto` 45 s timeout 09-28) |
| Account snapshot = 0.0 immediately after start | 6 of 6 successful starts (parser races the page) |
| Flatten ticket-count mismatch | 1 of 2 ("Closed 4 tickets" for 3 opened) |

### 4.5 Margin / leverage (MEASURED)

| Obs. | Price | Margin per 0.01 lot | Implied leverage |
|---|---|---|---|
| ghost_grid.log 09-28 | ~4135 | 8.27, 8.28 | 1:500 |
| demo_trade_open.png (DEMO) | 4273 | 8.55 | 1:500 |
| demo_switch_verification.png (REAL) | 4271.6 | 8.54 | 1:500 |
| pentest | ~4345 | 8.69 | 1:500 |

`NOTES.md` states 1:1000 (SOURCED), but gold margin is charged at 1:500 on both accounts. **Implication:** at $4,150, one 0.01 lot needs $8.30. **Below about $16.6 equity only one 0.01-lot position fits**, and there is no smaller lot (min 0.01, step 0.01; SOURCED from the DOM in `ghost_grid.log`: `data-min="0.01" data-step="0.01"`). The stop-out level is unknown.

## 5. Dukascopy spread by UTC hour (MEASURED, `data/candles/duka/*.csv`, column `spread`)

The `spread` column is the per-minute mean spread. Coverage: 599,173 minutes, 2025-01-21 to 2026-09-28. Overall: mean 0.671, median 0.621, p10 0.506, p90 0.835, p99 1.59. The last two columns are the recommended model from section 6, evaluated at the recent Dukascopy median.

| UTC h | Duka mean (all) | median | p90 | p99 | median Jun–Sep 26 | p90 Jun–Sep 26 | mean abs 2 s move | **BASE LF spread** | **HARSH spread** |
|---|---|---|---|---|---|---|---|---|---|
| 00 | 0.72 | 0.69 | 0.88 | 1.62 | 0.76 | 0.83 | 0.21 | 0.49 | 0.96 |
| 01 | 0.73 | 0.69 | 0.89 | 1.59 | 0.74 | 0.82 | 0.27 | 0.32 | 0.74 |
| 02 | 0.71 | 0.68 | 0.87 | 1.58 | 0.73 | 0.82 | 0.20 | 0.31 | 0.73 |
| 03 | 0.69 | 0.67 | 0.84 | 1.39 | 0.73 | 0.81 | 0.15 | 0.31 | 0.73 |
| 04 | 0.68 | 0.67 | 0.83 | 1.29 | 0.73 | 0.79 | 0.12 | 0.31 | 0.73 |
| 05 | 0.69 | 0.68 | 0.83 | 1.31 | 0.73 | 0.79 | 0.18 | 0.31 | 0.73 |
| 06 | 0.66 | 0.62 | 0.82 | 1.36 | 0.64 | 0.76 | 0.18 | 0.30 | 0.64 |
| 07 | 0.62 | 0.57 | 0.79 | 1.22 | 0.55 | 0.63 | 0.17 | 0.22 | 0.55 |
| 08 | 0.63 | 0.58 | 0.78 | 1.21 | 0.56 | 0.66 | 0.17 | 0.22 | 0.56 |
| 09 | 0.63 | 0.58 | 0.79 | 1.32 | 0.56 | 0.67 | 0.15 | 0.22 | 0.56 |
| 10 | 0.62 | 0.57 | 0.79 | 1.17 | 0.56 | 0.66 | 0.13 | 0.22 | 0.56 |
| 11 | 0.62 | 0.58 | 0.79 | 1.30 | 0.56 | 0.68 | 0.16 | 0.22 | 0.56 |
| 12 | 0.64 | 0.59 | 0.81 | 1.30 | 0.57 | 0.74 | 0.25 | 0.22 | 0.57 |
| 13 | 0.65 | 0.61 | 0.82 | 1.37 | 0.60 | 0.74 | 0.32 | 0.22 | 0.60 |
| 14 | 0.65 | 0.61 | 0.82 | 1.23 | 0.60 | 0.74 | 0.30 | 0.22 | 0.60 |
| 15 | 0.64 | 0.60 | 0.81 | 1.35 | 0.60 | 0.73 | 0.23 | 0.22 | 0.60 |
| 16 | 0.63 | 0.59 | 0.81 | 1.24 | 0.61 | 0.75 | 0.18 | 0.22 | 0.61 |
| 17 | 0.62 | 0.59 | 0.80 | 1.13 | 0.61 | 0.75 | 0.17 | 0.22 | 0.61 |
| 18 | 0.63 | 0.59 | 0.80 | 1.14 | 0.61 | 0.75 | 0.17 | 0.22 | 0.61 |
| 19 | 0.60 | 0.57 | 0.78 | 1.02 | 0.61 | 0.73 | 0.16 | 0.22 | 0.61 |
| 20 | 0.65 | 0.61 | 0.82 | 1.46 | 0.66 | 0.84 | 0.09 | 0.22 | 0.66 |
| 21 | 0.71 | 0.66 | 0.95 | 1.90 | break (summer) | | | no trading | no trading |
| 22 | 0.86 | 0.75 | 1.21 | 2.80 | 0.79 | 1.14 | 0.14 | 0.50 | 0.99 |
| 23 | 0.91 | 0.75 | 1.32 | 3.10 | 0.77 | 1.48 | 0.13 | 0.50 | 0.97 |

- Daily break: 21:00 to 22:00 UTC in summer and 22:00 to 23:00 UTC in winter. That is why hour 21 has only 6,959 minutes.
- Monthly Dukascopy mean spread ranged from 0.52 (2025-01) to 0.98 (2026-02). Recent months were 0.64 to 0.70.
- **Around HIGH-impact scheduled US releases** (MEASURED; 209 events in `xau_alpha/data/econ_calendar.csv` with M1 coverage; `m1_ba.parquet` spread and max spread per minute):

  | Minute vs release | Duka mean spread | median of per-minute max spread | p90 max spread | median M1 range |
  |---|---|---|---|---|
  | same-hours baseline | 0.63 | 0.72 | n/a | 1.81 |
  | −2 | 0.69 | 0.77 | 1.19 | 1.73 |
  | −1 | 0.89 | 1.67 | 3.17 | 1.74 |
  | **0** | **1.17** | **3.80** | **8.10** | **6.79** |
  | +1 | 0.89 | 1.20 | 2.61 | 4.24 |
  | +3 | 0.79 | 1.00 | 2.16 | 3.53 |
  | +5 | 0.72 | 0.93 | 1.68 | 3.42 |
  | +10 | 0.71 | 0.85 | 1.51 | 3.36 |

  Spread is elevated from −1 to about +5 min. The worst tick in the release minute is a median of 5.3 times baseline. This is Dukascopy. **LiteFinance's news-time spread is unmeasured.** OPINION: that is the main reason to keep the existing news guard blocking entries from −2 to +5 min around HIGH events, rather than trying to price it.

## 6. Recommended cost model for backtests

The model plugs into `xau_alpha/lib/sim.py` `Cost`. There, broker spread = `max(spread_floor, spread_mult × Duka_spread + spread_add) + hour_add[UTC hour]`.

### BASE (calibrated to LiteFinance quotes; OPINION where noted)

```python
H_BASE = [0.25] + [0.08]*6 + [0.0]*15 + [0.25, 0.25]      # 00h +0.25; 01-06h +0.08; 22-23h +0.25
LF_BASE = Cost(spread_mult=0.32, spread_add=0.0, spread_floor=0.22,
               slip_entry=0.05, slip_exit=0.10, com_rt_lot=0.0, hour_add=tuple(H_BASE))
```

- **Spread:**
  - 07 to 20 UTC → 0.22 (MEASURED 0.22 at 09:59 to 19:20; 0.12 at 20:50 is ignored as a favourable outlier).
  - 22 to 00 UTC → about 0.50 (MEASURED 0.47 at 23:25).
  - 01 to 06 UTC → about 0.31 (**unmeasured**; OPINION: scaled by the Dukascopy Asia/London ratio of 1.3).
  - News scaling: 0.32 × Dukascopy (0.32 = 0.22/0.69, the MEASURED ratio at 17:29 to 17:32).
- **Entry slip:** 0.05 $/oz adverse. The MEASURED latency noise is zero-mean with mean abs of 0.12 to 0.18 over 1 to 2 s. Using a fixed adverse 0.05 is mildly conservative.
- **Exit slip:** 0.10 $/oz. This covers about 1 s of zero-mean noise for a single-ticket close. MEASURED 2.58 s multi-ticket flattens drifted 0.12 and 0.37 adverse (n=2).
- **Delay:** decision at M1 close, fill in the first 10 s bar at or after the decision (current sim convention). The real delay is 0.5 to 2 s (MEASURED); this approximation adds no bias because the noise is zero-mean.
- **Round trip in session:** about **0.37 $/oz**, which is $0.37 per 0.01 lot, or **2.8% of $13 equity per trade**.

### HARSH (stress; use for go/no-go)

```python
H_HARSH = [0.20] + [0.0]*21 + [0.20, 0.20]
LF_HARSH = Cost(spread_mult=1.0, spread_add=0.0, spread_floor=0.30,
                slip_entry=0.20, slip_exit=0.40, com_rt_lot=7.0, hour_add=tuple(H_HARSH))
```

- **Spread:** the Dukascopy spread as recorded. That is 0.55 to 0.61 in session (2.5 to 2.8 times the measured LiteFinance spread) and about 0.97 around the reopen. It automatically carries Dukascopy's news-time widening.
- **Slip:** entry 0.20 (about p75 to p90 of a 2 s move). Exit 0.40 (above p90 of a 3 s move; covers a 2.6 s sequential multi-ticket flatten or an SL fill on a fast bar).
- **Commission:** $7/lot round turn (unverified hedge).
- **Round trip in session:** about **1.25 to 1.3 $/oz**, which is **about 10% of $13 per 0.01-lot trade**. The existing `COSTS["harsh"]` in `sim.py` (Dukascopy × 1.2 + 0.30, slips 0.15/0.25) is of similar total size (about 1.4 $/oz). Either is fine as the stress case.

### Other assumptions to use in either model

- Latency: 1.1 s median, 1.9 s worst observed per order.
- Multi-order baskets: +1.2 to 1.5 s per extra order. Flatten: +0.86 s per ticket.
- No partial fills at 0.01 lot (none observed).
- Lot granularity 0.01. Margin 1:500 (at $13 only one 0.01-lot position).
- News window: no entries from −2 to +5 min around HIGH events (OPINION, from the Dukascopy news-spread table).
- Session filter: base and harsh both treat 21:00 to 22:00 UTC (summer) as closed. Entries from 20:45 to 00:30 UTC should be treated as expensive.

## 7. What would make this model solid (not done here)

1. **Log fills.** Read the open-trades row (open price, ticket, commission column if present) after each confirmation, and the close price after each flatten. Today no fill price is recorded, which is the single biggest gap.
2. **Log quotes.** Write the gateway's DOM bid/ask to a CSV once per second for 1 to 2 weeks. That gives LiteFinance spread by hour and at news times with n in the thousands. It can be done in DEMO with no orders.
3. **Fetch Dukascopy hours** 2026-09-29 09:00 to 11:00 UTC (`scripts/fetch_duka_raw.py`). That enables 4 more quote comparisons, including the `hft.json` snapshot. This run did not download data.
4. **Check the LiteFinance fee schedule** for the account type that is actually used (web terminal vs MT5, ECN vs Classic), for commission and stop-out level.
5. **Fix the UI failure mode seen on 09-29** before any REAL run. Every signal that day was missed.

## 8. Reproduce

```
python3 xau_alpha/recon/broker_costs_measure.py      # ~50 s, single process, writes broker_costs_tables.json
```
