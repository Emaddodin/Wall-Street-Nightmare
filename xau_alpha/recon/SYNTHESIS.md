# SYNTHESIS: review of the five recon reports, and strategy hypotheses for a $13 LiteFinance XAUUSD scalper

Date: 2026-09-30. Branch `for-dear-opus-5.5`.

**Inputs**
- `recon/video_mindset.md`, `codebase_inventory.md`, `news_llm.md`, `web_research.md`, `broker_costs.md`.
- `data/econ_calendar*.csv`, `news/out/*.json`.
- `HANDOFF.md`.
- The raw logs and data behind the spot checks.

**Labels**
- **MEASURED**: I computed it in this pass.
- **SOURCED**: taken from the cited file or URL. If a report is the source, it is cited as `report §`.
- **OPINION**: my judgement.

**Reproduction.** `python3 xau_alpha/recon/synthesis_checks.py` runs as a single process in about 1 minute, on TRAIN+VALID only; the TEST split is untouched. It runs:
- A. the Runner under 8 cost models;
- B. cost relative to price range, by hour;
- C. a Monte Carlo of the flip with the margin barrier included.

**Files changed.** Nothing outside `xau_alpha/` was changed, except one short dated entry appended to `HANDOFF.md` (§9).

---

## 0. Bottom line

1. **Nothing in the repo has a net edge on real XAUUSD.**
   - MEASURED: the currently deployed Runner, on the 10-second bid/ask simulator, has a gross edge at mid price of +0.35 / +0.21 pt per trade (PF 1.18 / 1.10 on train / valid).
   - The LiteFinance round trip costs about 0.42 pt. At LiteFinance cost (LF_BASE + $5/lot commission) the Runner's PF is **0.91 / 0.88**.
   - All three earlier Runner figures are superseded (see C1): the codebase's 1.07, news_llm's 0.59 / 0.64, and HANDOFF's "gross −0.2 pt".
2. **The 1:500 gold margin, not the signal, is the binding constraint for a $13 flip.**
   - At $4,150 gold, a 0.01 lot (1 oz) needs $8.30 of margin, and no smaller lot exists. After two losing trades the account can no longer open any position.
   - MEASURED (Monte Carlo, start $13, target $100, cost $0.42/oz): P(reaching $100) is
     - 0-2.3% with zero gross edge;
     - 0.7-9.8% with a +0.125R gross edge;
     - 10-37% with a +0.25R gross edge, which no candidate has shown.
   - Starting at $50 instead of $13 lifts the +0.125R case from 4.8% to 37%.
   - The 28% in HANDOFF and the 12-18% in web_research are too high: neither models the margin barrier (C9).
3. **Cost model for all research:**
   - LF_BASE with $5/lot commission, about $0.42/oz round trip from 07 to 20 UTC. Use it for selection.
   - LF_HARSH, about $1.3/oz. Use it for the go/no-go decision.
   - Dukascopy's own spread (median 0.62, MEASURED) is 2.5-2.8x LiteFinance's. A strategy that only works at Dukascopy cost is fine; a strategy that needs cost below 0.42 is dead.
4. **News guards and the Laya/Jeff models (your note):**
   - Keep only a deterministic calendar blackout, which can be replayed against history.
   - For equity below about $21, the blackout must be ±30 min around HIGH events. SOURCED web_research §1.1: LiteFinance may raise gold margin to 1:200 in that window, and then a $13 account cannot open a 0.01 lot at all.
   - Take the headline bias, the trade-journal RAG and the regime priors out of the entry path. Under neutral news they veto SELLs in 15 of 24 hours, and with feeds down they bias bullish.
   - Laya and Jeff run in **shadow mode only** (log their outputs, never veto or size), with a pre-registered forward evaluation (§4).
5. **The owner's method (MR P FX and tzwj) can be coded; its results cannot be reproduced.**
   - The setups (break-and-retest, failed breakout, range fade) are codeable.
   - The creators' P&L came from 2,000-6,400:1 effective leverage on Exness.
   - Keep the structure: zones, retest, confirmation candle, exit at the nearest level, London morning.
   - Replace the sizing with one 0.01 lot and a hard stop of at most $4.
6. **Test first:** H1 (sweep and reclaim of session extremes), H2 (M1 zone break → retest → confirmation), H3 (failed-breakout reversal), plus the H4 cost/volatility gate. All 11 hypotheses are in §6 with rules and parameter grids.
7. **Blocking before any real-money order** (§3):
   - Fill prices are never logged.
   - The web-UI gateway failed 9 of 9 orders on 2026-09-29.
   - Commission, stop-out and news-margin policy are unconfirmed on the live account.
   - Spread has been measured only 10 times.
   - The calendar ends 2026-09-30.

---

## 1. Contradictions between reports

| # | Topic | One report says | Another report says | Resolution |
|---|---|---|---|---|
| C1 | Runner on real XAUUSD | codebase: PF **1.07** (1.00 / 1.08) at "mid" friction (`scripts/runner_flip.py`, fills from M1 bars, full period) | news_llm: PF **0.59 / 0.64** (sim.py `base`). HANDOFF l.31: "gross (mid) expectancy about −0.2 pt/trade" | **MEASURED (checks A), same port, train / valid:**<br>• at mid: PF 1.175 / 1.098, **+0.35 / +0.21 pt**<br>• the codebase's own "mid" friction on 10-s bars: 0.855 / 0.872<br>• LF_BASE: 0.935 / 0.898<br>• LF_BASE + $5/lot: 0.914 / 0.878<br>• sim `zero`: 0.67 / 0.71. This is really the raw Dukascopy spread, **not** zero friction (G17).<br>• sim `base`: 0.59 / 0.64<br>• LF_HARSH: 0.47 / 0.52<br>Why they differ: the 1.07 depends on optimistic fill ordering inside each bar (the codebase itself saw gross PF fall from 1.35 to 1.16 under pessimistic ordering); news_llm used Dukascopy spread plus 0.10; HANDOFF has the wrong sign on the gross edge. **Verdict: Runner loses at every realistic cost.** |
| C2 | Gold leverage and lot cap at $13 | video_mindset: 1:1000, $4.33 per 0.01 lot, "practical cap 0.02 lot", wipe-out after ~$6.5 | web_research (SOURCED LiteFinance instrument page) and broker_costs (MEASURED): **1:500** | 1:500 is right. MEASURED (re-read):<br>• `logs/ghost_grid.log` l.243-247: margin used $8.27 / 16.54 / 24.81 for 1 / 2 / 3 × 0.01 lot at ~4135.<br>• `data/preflight_pentest_report.json`: $8.69 at ~4345.<br>At $13 this means exactly one 0.01 lot, margin level 100% after about $4.4 adverse, and no new trade possible below about $8.3 equity. |
| C3 | LiteFinance spread | codebase §0: "Unknown. No local log records bid/ask" | broker_costs: 0.22 in 8 of 10 quotes. web_research: 0.12 just before the daily break, 0.48-0.51 after the reopen | broker_costs and web_research agree: both see ~0.12 around 20:50-22:00 UTC and ~0.47-0.51 after the reopen. The codebase missed the screenshots and the log. The sample is still only n = 10. |
| C4 | Commission | web_research: ECN **$5/lot round turn, charged at open** (SOURCED LiteFinance ECN page and commission PDF, in force since 2026-05-19) | broker_costs: none visible, so BASE uses $0 | The logs cannot settle it. SOURCED pentest: a BUY round trip cost −$0.27 (= 0.22 spread + 0.05) and a SELL round trip −$0.13; price noise in the holding time is ±0.1-0.3. **Use $5/lot in BASE** until a fill log says otherwise. It costs the Runner about −0.02 PF. |
| C5 | Order latency | web_research: Client Agreement says "usually 3-5 s", 5-15 s otherwise | broker_costs: median 1.11 s (0.84-1.83, n = 7); +1.2-1.5 s per extra ticket | Design with the measured values and stress at 3-5 s. |
| C6 | Quote timing | broker_costs: LiteFinance quotes line up with Dukascopy best at 0 s lag (n = 6) | SOURCED `preflight_pentest_report.json`: `avg_client_lag_ms` 887. broker_costs: the first quote after a (re)connect was up to 52 s stale | Unresolved. Discard quotes for 10 s after any reconnect, and log the server time if the DOM exposes it. |
| C7 | News blackout window | production: [T−15, T+5]. broker_costs: [−2, +5]. web_research H1b: [−2, +10]. The codebase's ICT library: NFP/CPI −15/+30, FOMC −30/+60 | news_llm (MEASURED): the spike is at T..T+4 (range 2.58x, then 1.81x); the range is still 1.42-1.68x at T+5..T+44 | Below about $21 equity, the broker's 1:200 news margin (if applied) forces **[T−30, T+30]**. Above that, sweep the window. Never use [T−15, T+5]: it blocks quiet minutes and reopens in the middle of the spike. |
| C8 | Best trading hours | video_mindset: every entry was 05:31-10:35 UTC | web_research H2: 12-17 UTC (Dukascopy cost/range 0.27 against 0.68) | MEASURED (checks B, LiteFinance cost ÷ median M1 range):<br>• 0.18-0.22 at 13-15 UTC<br>• 0.30-0.35 at 07-11 UTC<br>• 0.40-0.53 at 03-05 UTC<br>• 0.63-0.64 at 22-23 UTC<br>The video's window costs about 1.7x more per unit of movement than 13-15 UTC, but it is not prohibitive. 13-15 UTC overlaps the 08:30-10:00 ET releases, which are blacked out. Make the session a swept parameter. |
| C9 | Flip odds | HANDOFF l.67: 28% hit / 67% ruin (PAXG, 1:1000, in-sample). web_research: 12-18% hit with a 1-oz minimum lot (ruin barrier = one stop) | — | MEASURED (checks C) with the 1:500 margin barrier: 0.7-9.8% at +0.125R gross and 10-37% at +0.25R. Both earlier figures are too optimistic. |
| C10 | When the spread normalises after the reopen | broker_costs: 0.47 at 23:25 UTC, "about 25 min after the daily reopen" | web_research: the reopen is at 22:02 UTC in EU summer | 23:25 UTC is about 83 min after the summer reopen, so the spread stays wide for roughly 1.5 h. BASE's +0.25 at 22-00 UTC covers this. Minor. |
| C11 | Number of HIGH events | news_llm: 220 rows, 196 unique minutes inside the data range, 183 in the window study | broker_costs: 209 with M1 coverage | Different subsets, not a contradiction. Cite the denominator. |
| C12 | Would the guards have blocked T7? | video_mindset: the RAG would veto T7 (SELL, 10:14 UTC) | news_llm: under neutral news, SELL is allowed 00-08 UTC only | Consistent. I re-read `news/out/guard_audit.json` without re-running it: SELL is allowed in hours 0-8 only in the TRADE_WAR table, and in 0 of 24 hours in the offline-seed state. |
| C13 | Broker server clock | video_mindset: "broker time is GMT+0" | web_research: LiteFinance runs GMT+3 in summer, GMT+2 in winter | Both are right. GMT+0 is the creators' Exness server. Never reuse Exness clock times for LiteFinance. |
| C14 | Updating HANDOFF | codebase, news_llm, video_mindset and broker_costs did not write to HANDOFF, as their task instructed | web_research wrote to HANDOFF, against that instruction | Process deviation. HANDOFF now has the LiteFinance conditions but none of the cost model, calendar, video or synthesis findings. I added them (§9). |

---

## 2. Spot checks I ran

| # | Claim | Check | Result |
|---|---|---|---|
| S1 | Runner PF on real data (C1) | `synthesis_checks.py` part A, `lib/ref_runner.py` port, 1,128 orders on train+valid, 10-s bid/ask simulator | Resolved as in C1. The gross edge exists but is smaller than cost. MEASURED |
| S2 | Gold is charged at 1:500 | Re-read the gateway log margin lines and the pentest JSON | Confirmed: 8.27 at ~4135 (= /500), 8.69 at ~4345. MEASURED |
| S3 | Dukascopy spread is median 0.62 | `m1_ba.parquet` `spr` column, 599,173 minutes | p10 0.506, median 0.621, p90 0.835, p99 1.59. Hourly median 0.57-0.61 from 07-20 UTC and 0.75 at 22-23 UTC. Confirmed. MEASURED |
| S4 | The calendar handles daylight saving correctly | Every row converted back to America/New_York | Details below. **No DST error found.** Gaps: US-only, ends 2026-09-30. MEASURED |
| S5 | T6 and T7 prices in the video match Dukascopy | `data/candles/duka/xau_m1_2026-09-22.csv` | Details below. Confirmed. **Both video days are in the TEST split.** MEASURED |
| S6 | Flip odds | Monte Carlo with the margin barrier (checks C) | Contradicts web_research and HANDOFF (C9, §7). MEASURED |
| S7 | Guard SELL vetoes | Re-read `news/out/guard_audit.json` | Consistent with news_llm. SOURCED (not re-executed) |

**S4, calendar details.**
- Every 08:30 ET row sits at 13:30Z under EST and 12:30Z under EDT. Examples:
  - NFP 2025-03-07 at 13:30Z;
  - CPI 2025-03-12 at 12:30Z;
  - NFP 2026-03-06 at 13:30Z;
  - CPI 2026-03-11 at 12:30Z.
- FOMC statements are at 18:00Z in EDT and 19:00Z in EST, on these dates:
  - 2025: Jan 29, Mar 19, May 7, Jun 18, Jul 30, Sep 17, Oct 29, Dec 10;
  - 2026: Jan 28, Mar 18, Apr 29, Jun 17, Jul 29, Sep 16.
- There are no weekend rows, and the last row is 2026-09-30.

**S5, video price details.**
- T6:
  - 09:58: close 4336.34, the first close above 4335.6.
  - 10:01 and 10:02: lows 4332.53 and 4331.86.
- T7:
  - 10:13: low 4328.865, close 4329.445.
  - 10:14: high 4332.015.
- 2026-09-21 is a Monday and 2026-09-22 a Tuesday.

**Not re-checked:** the literature citations (web_research), whisper transcript quality, the Nasdaq cross-check, and the 1-second delay study. They are plausible and internally consistent, but I did not verify them.

---

## 3. Gaps that matter for a real-money $13 scalper

### Blocking: resolve before any REAL order

- **G1. No fill price has ever been logged.**
  - Real slippage is unknown: only 2 baskets could be reconstructed.
  - Read the open price, ticket and commission from the open-trades row after every confirmation, and the close price after every flatten.
- **G2. The execution path is broken.**
  - 2026-09-29: 9 of 9 orders failed with `NO_VALID_DIRECTION_ORDER_BUTTON`.
  - 2 of 8 gateway starts failed.
  - One flatten reported 4 tickets closed when 3 were open.
  - SOURCED broker_costs §4.4. OPINION: browser automation is the biggest execution risk. Consider an MQL5 EA or bridge running in the MT5 terminal on the VPS. The `MetaTrader5` Python package runs only on Windows (OPINION, known constraint); HANDOFF l.59 mentions a leftover Wine process on the VPS.
- **G3. The live account's terms are unconfirmed.** Commission ($5/lot SOURCED against $0 observed), stop-out level (20% for ECN per SOURCED web_research) and the "broker may close below 100% margin level" clause (Client Agreement 7.1).
  - Read `symbol_info("XAUUSD")` and the account info from the live MT5 terminal, read-only.
  - Fields: `trade_contract_size`, `volume_min/step`, `trade_stops_level`, `trade_freeze_level`, `order_calc_margin(0.01)`, `margin_so_so`, and commission on a DEMO fill.
- **G4. The news-margin policy is unknown.**
  - LiteFinance "may" raise gold margin to 0.5% for new orders within ±30 min of important releases; the example given is CPI on 2024-07-11. Which events, and whether it always applies, is unknown.
  - At $13 this blocks every new order. Treat it as a ±30 min blackout on all HIGH rows until confirmed.
- **G5. The calendar stops at 2026-09-30.**
  - Live trading from 2026-10-01 needs forward rows: rebuild weekly with `news/build_calendar.py`.
  - Make the rule fail closed: no entries unless the calendar has rows covering the next 7 days.
  - Coverage is also US-only. No ECB/BoE/China releases or unscheduled headlines are included.
- **G6. The real balance is ambiguous.** HANDOFF says $12.47 (owner-stated); this task says about $13. Read it from the broker before sizing. At $12.47 and 1:500 the margin barrier is closer still.

### Design gaps: they change the results

- **G7. LiteFinance spread: n = 10 quotes.**
  - 01-06 UTC (Asia) is unmeasured; BASE's 0.31 there is a guess.
  - Spread during news is unmeasured.
  - Log DOM bid/ask at 1 Hz on DEMO for 1-2 weeks.
- **G8. Quote staleness and lag** (C6).
- **G9. None of the video's setups has been tested on real data.** Only detector-firing checks are proposed.
  - The 7 tzwj trades fall on 2026-09-21/22, **inside TEST**. Use them only to confirm the detector fires; never read their P&L or tune on them.
  - V1-V4 (2025-03-27) are in TRAIN.
- **G10. Lot size granularity.**
  - Whether LiteFinance Cent offers XAUUSD, and at what contract size, is unclear (web_research §1.2).
  - It is the biggest single lever on ruin. microlot_ruin: with 0.01-oz steps, ruin is about 0% at a +0.10R edge, but reaching $100 then takes about 1,600-2,200 trades.
- **G11. Flip odds in HANDOFF and web_research are overstated** (C9, §7).
- **G12. Holdout hygiene.** HANDOFF l.37-38 already records a minor TEST peek. Log every config tried per hypothesis.
- **G13. Prices are not stationary.**
  - Gold went $2,704 → $5,593 → about $4,130.
  - Mean M1 range went 0.83 → 3.78 → 2.25 (SOURCED codebase §5).
  - Put strategy thresholds in ATR units. Use dollars only for broker constraints (stop cap, cost, margin).
  - A $4 stop cap will skip many trades when volatility is high (2026). Report how many.
- **G14. Time zones and daylight saving.**
  - `econ_calendar.csv` is correct (S4).
  - Remaining traps:
    - `scalper/pa/ict.py` killzones are fixed UTC with no DST (SOURCED codebase §3).
    - The LiteFinance server clock follows EU DST while US sessions follow US DST. They differ during 2026-10-25..11-01 and 2027-03-14..03-28.
    - The video session (05:31-10:35 UTC) was recorded under BST (tzwj) and GMT (v617). Express it in **London local time, about 06:00-11:40**.
  - Use `lon_mod` and `ny_mod` from `data.load_m1()`.
- **G15. Dukascopy volume is tick volume** (~0.1 per minute). The volume-profile setups (Apex POC/VAH) cannot be tested on this data.
- **G16. No XAGUSD or DXY data**, so SMT divergence cannot be tested.

### Hygiene

- **G17. sim.py labels.** `COSTS["zero"]` is actually the raw Dukascopy spread with zero slippage, and should be renamed `duka_raw`. Add `LF_BASE` (with commission) and `LF_HARSH` (§5) to `COSTS`. HANDOFF l.31 ("gross (mid) −0.2 pt") is wrong.
- **G18. Leverage and ruin assumptions.**
  - `lib/account.py`: `Sizing.leverage` defaults to 1000 and must be 500 for gold.
  - `recon/microlot_ruin.py`: the ruin barrier must be max(stop + cost, margin to open one lot).
  - `scripts/runner_flip_odds.py` assumes 1:1000.
- **G19. Broken repo helpers.** Do not reuse `ict.order_blocks` (NameError), `ict.fvg_state` (columns shifted), or `scalper/tools/walkforward.py` (corrupted). Use `xau_alpha/lib/features.py` and `sweep.py` instead (SOURCED codebase §3).

---

## 4. News guards and Laya/Jeff: consolidated specification

This combines news_llm, web_research §2.14, broker_costs §5 and codebase §6.

- **N1. The calendar blackout is the only guard allowed in backtests.**
  - Source: `data/econ_calendar.csv` through `news/replay.py` or `lib/data.news_block_mask`. The replay matches production exactly: 0 mismatches in 24,000 samples (SOURCED news_llm §0.4).
  - Live: use the union of this CSV (rebuilt weekly) and the ForexFactory feed. Fail closed (G5).
- **N2. Blackout windows.**
  - Equity < ~$21: [T−30, T+30] around every HIGH row (G4).
  - Equity ≥ $21: sweep {[−2, +5], [−5, +15], [−15, +30]} and choose by VALID net R.
  - MEDIUM rows (claims, GDP second and third estimates): sweep {off, [−2, +5]}.
- **N3. Live spread guard.**
  - Skip an entry if the quoted spread exceeds max(0.40, 2 × the logged LiteFinance hourly median).
  - Ignore quotes for 10 s after a reconnect.
- **N4. Remove from the entry path:**
  - PoliticianBrain headline bias and its size/TP boosts;
  - the post-news BUY ×1.5 size / ×2 TP window;
  - TradeJournalRAG;
  - RegimePriorEngine grades and size multipliers;
  - MacroWatchdog (inert).

  They are built on synthetic data, bias bullish when feeds fail, veto SELLs in 15 of 24 h under neutral news, and cannot be replayed (SOURCED news_llm §2). Evidence: the post-news drift from T+5 to T+45 has t = 0.33, and on real data they took the Runner only from PF 0.59/0.64 to 0.70/0.68 (SOURCED news_llm §3). No new-entry rule at 22:00-23:59 UTC is still justified by spread, and the session gate already covers it.
- **N5. Laya/Jeff in shadow mode.**
  - **Prompt:** replace the constant fields (wick "0.60", session "London/NY", trend "Yes") with the hypothesis's real features:
    - setup ID and direction;
    - zone touches K, break size ÷ A, retest age in bars, trigger type;
    - stop ÷ A, distance to the nearest opposing level ÷ A;
    - London local minute, volatility percentile, minutes to the next HIGH event;
    - the last 3 headlines with their timestamps.
  - **Timing:** query at the retest touch, before the trigger candle closes, so the ~1.3 s model latency is hidden.
  - **Authority:** no veto, no size multiplier, no TP expansion.
  - **Logging:** the grade, trap probability and confluence score, together with the trade's realized R.
  - **Pre-registered test after ≥ 200 forward signals.** Backtesting 2025-26 with headline inputs is contaminated by the models' training data (SOURCED web_research §2.14). Grant **veto power only**, never size-up power, and only if all three hold:
    - (a) Spearman correlation of trap probability with realized R is below 0 at p < 0.05;
    - (b) PF(allowed) − PF(would-veto) has a 90% block-bootstrap CI above 0;
    - (c) the vetoes remove at most 40% of trades.
  - **Where it runs:** Laya cannot run locally (torch broken; the first load downloads 2.37 GB). It runs on the VPS at ~1.3 s per call, where RAM is tight (SOURCED HANDOFF l.69-70). Jeff is not deployed; leave it until Laya's shadow data exists.
- **N6. Limit.** A guard cannot create an edge. Its value is avoiding spread and slippage spikes and the forced-margin window.

---

## 5. Constraints and evaluation protocol for every hypothesis

**Cost models.** These are the broker_costs models, with the commission from C4 added to BASE.

```python
H_BASE   = [0.25] + [0.08]*6 + [0.0]*15 + [0.25, 0.25]
LF_BASE  = Cost(spread_mult=0.32, spread_floor=0.22, slip_entry=0.05, slip_exit=0.10, com_rt_lot=5.0, hour_add=tuple(H_BASE))
H_HARSH  = [0.20] + [0.0]*21 + [0.20, 0.20]
LF_HARSH = Cost(spread_mult=1.0, spread_floor=0.30, slip_entry=0.20, slip_exit=0.40, com_rt_lot=7.0, hour_add=tuple(H_HARSH))
```

**Account-driven constraints** at $13 equity, $4,150 gold, 1:500 (MEASURED arithmetic):
- **Size.** One position of 0.01 lot = 1 oz.
- **Stop cap.** Margin level reaches 100% after 13 − 0.27 − 8.30 ≈ $4.4 adverse, so use a **stop cap of $4.0** and a **stop floor of $1.2** (about 3x the round-trip cost). Skip a trade outside [1.2, 4.0] rather than resize it, and report how many were skipped.
- **Stop placement.** Put the stop on the broker's server, in the order ticket, as the gateway already does.

**Session gates** (OPINION):
- No entries 20:30-23:30 UTC, after 19:00 UTC on Friday, or at the Sunday open.
- Flat by 16:45 ET.
- Blackout as in N2.
- Mirror every rule for longs and shorts with identical parameters. No directional veto.

**Units and timing.**
- A = Wilder ATR(14) of M1 mid (`data.atr`).
- A5 = the same on causal M5 bars (`data.resample_causal`).
- Session times use `lon_mod` / `ny_mod`.
- A decision on M1 bar i becomes an order with `t = ts[i] + 60_000`; `sim.simulate` fills it on the first 10-s bar at or after t.
- Pivots come from `features.pivots(h, l, k)` and are confirmed k bars after the pivot.

**Protocol.**
- Search on TRAIN (2025-01-21..2025-12-31), with at most about 400 configs per hypothesis, logged.
- Confirm on VALID (2026-01..05).
- Run TEST (2026-06..09-28) once, on at most 2 frozen finalists.
- Report points and R per 1 oz. Never report compounded-dollar PF (SOURCED codebase §5.7).
- Use at most 2 processes (`sweep.py --jobs 2`).

**Pass bar** (OPINION):
- n ≥ 150 on TRAIN and ≥ 60 on VALID.
- At LF_BASE: PF ≥ 1.15 and net average ≥ +0.15 pt on both TRAIN and VALID.
- At LF_HARSH: VALID PF ≥ 1.0.
- Both halves of TRAIN have PF > 1.
- Beats `nulltest.random_direction` (≥ 200 seeds) at p ≤ 0.05.
- Robust to its neighbours: the median VALID PF of the 8 nearest grid points is ≥ 1.05.
- Dropping the best month still leaves PF ≥ 1.05.
- Only then: flip odds from `account.flip_odds` with `Sizing(leverage=500)` on VALID trades.

---

## 6. Hypotheses, ranked (rank and credibility are OPINION)

### Shared definitions

```
BULL_ENGULF(i): c[i]>o[i], c[i-1]<o[i-1], c[i]>=o[i-1], o[i]<=c[i-1], body[i]>body[i-1]
BULL_PIN(i,lvl): (min(o[i],c[i]) - l[i]) >= w_pin*rng[i], l[i] <= lvl, c[i] > lvl        w_pin in {0.5, 0.6}
TRIG_BULL(i,lvl) = BULL_ENGULF(i) or BULL_PIN(i,lvl)          (bearish versions are mirror images)
ZONES(i; L,k,w,K): confirmed pivot prices (highs and lows) with pivot index in (i-L, i]; sort; greedy 1-D
  clustering where each cluster is a maximal run with max-min <= w*A[i]; keep clusters with >= K members;
  zone = [min-0.1A, max+0.1A]. Recomputed at every M1 close (causal).
NEAREST_LEVEL(i, d): the nearest confirmed swing high (d=+1) or swing low (d=-1) beyond the entry, from pivots in the last 240 bars.
```

### H1. Sweep and reclaim of session extremes (rank 1; credibility MEDIUM)

- **Why:**
  - Stop-loss orders cluster just beyond obvious levels, and stop cascades have been documented (Osler 2003, 2005; SOURCED web_research §2.4-2.5).
  - It is the same idea as the creators' "fake out" (T7) and ICT's judas swing and turtle soup.
  - HANDOFF l.37: a first break of the prior-day high/low **continued** +1.24 pt at 15 min in TRAIN (t = 2.4), but the sign flipped in VALID. The fade is the untested complement.
- **Levels:**
  - Asia high/low: `features.window_range(mod, tday, h, l, 0, 420)`, i.e. 00:00-06:59 UTC, known from 07:00.
  - Prior trading-day high/low: `features.prev_day_hl(tday, h, l)`.
  - For the NY window: the London high/low from 07:00-11:59 UTC.
- **Windows:** LON 08:00-11:30 London local; NY 08:30-11:30 ET; or both.
- **Rule (short side; the long side is the mirror image):**
  1. **Sweep:** the first bar i in the window with h[i] ≥ L + δ·A5[i], where level L has not been swept yet today.
  2. **Reclaim:** the first M1 close c[j] ≤ L − ε·A[j] with j − i ≤ N.
  3. **Trigger:** {none, TRIG_BEAR(j, L)}.
  4. **Entry:** market order at the close of bar j.
  5. **Stop:** the highest high since the sweep + σ·A.
  6. **Target:** {midpoint of the reference range, 1.5R, 2.5R}.
  7. **Time stop:** tmax.
- **Grid:**
  - Stage 1: level set {Asia, prior day, both} × δ {0.25, 0.5, 1.0} × N {3, 10, 20} × trigger {none, engulf/pin} × window {LON, NY, both} = 162 configs, with σ = 0.3, TP = 1.5R, tmax = 60.
  - Stage 2 (top 5): ε {0, 0.2} × σ {0.2, 0.5} × TP (3) × tmax {30, 90} = 24 each.
  - About 280 in total.
- **Null tests:**
  - random direction;
  - **placebo levels** L′ = L ± U(0.3, 0.7) × the Asia range width, drawn once per day;
  - the **continuation twin**: enter in the sweep direction at the first close beyond L + δ·A5, with the same stop/target geometry.
- **Kill if:** it misses the pass bar, or the placebo levels land within 1 standard error of the real levels.
- **Expected n:** about 1-3 per day.
- **Reuse:** `features.window_range`, `prev_day_hl`, `running_day_extremes`, `sim`, `nulltest`, `sweep`.

### H2. M1 zone break, then retest, then confirmation candle: the owner's core setup A (rank 2; credibility MEDIUM-LOW)

- **Why:**
  - The owner's main saved method (video_mindset §b). Its state machine is codified there.
  - The documented mechanism is weak. Evidence against:
    - Apex breakout-retest on 5m levels lost on Dukascopy, PF 0.22-0.70 (SOURCED codebase §2d).
    - The ≥6-touch nearest-S/R bot was negative on both PAXG splits (SOURCED codebase §1.4).
    - M1 breakouts drift only ±0.1-0.4 pt (SOURCED HANDOFF l.34).
  - What is new here: M1 multi-touch zones, a confirmation candle, a target at the nearest level, and the London morning session.
- **Rule (long side):**
  1. **Zones:** ZONES(i; L, k, w, K).
  2. **Break:** at bar i0, c[i0] > z_hi + β·A, and the break leg c[i0] − min(l[i0−10..i0]) ≥ λ·A. The zone must not have been broken in the previous 60 bars.
  3. **Retest:** the first bar i1 in (i0, i0+R] with l[i1] ≤ z_hi + τ·A, and no close below z_lo in between.
  4. **Touch:** TOUCH ∈ {1, 2}. For touch 2, price must rise to at least z_hi + 0.5A between the two touches (T1 used the second touch; T6 and T7 used the first).
  5. **Trigger:** TRIG_BULL(j, z_hi) for j ∈ [touch, touch+2]. Control arm: enter at the touch close with no trigger.
  6. **Stop:** min(l[touch..j]) − σ·A.
  7. **Target:** NEAREST_LEVEL, if it is ≥ ρ·(stop distance) away. If none lies within 4× the stop distance, use 2R. Alternative: a fixed {1.0, 1.5, 2.0}R.
  8. **Break-even:** {off, move the stop to entry + 0.45 at +1R}.
  9. **Session:** {06:00-12:00, 06:00-17:00} London local.
- **Grid:**
  - Stage 1, with k = 3, L = 240, σ = 0.3, TP = nearest level (ρ = 1), tmax = 30, video window: w {0.5, 1.0} × K {3, 6} × β {0.1, 0.3} × λ {1.5, 3.0} × R {15, 45, 90} × touch {1, 2} × trigger {on, off} = 192.
  - Stage 2 (top 5): σ {0.2, 0.5} × TP {nearest ρ = 1, nearest ρ = 1.5, 1.5R} × tmax {15, 30, 60} × BE {off, on} = 36 each.
  - Stage 3 (top 3): session × (k, L) ∈ {(2, 120), (3, 240), (2, 240)} = 6 each.
  - About 390 in total.
- **Detector sanity check (not evidence):** it must fire within ±2 bars of V1-V4 (2025-03-27, TRAIN). For T1 and T3-T6 (TEST), check that it fires, without reading P&L.
- **Null tests:**
  - random direction;
  - placebo zones (shift the zone by +1.5·w·A);
  - trigger versus no trigger;
  - touch 1 versus touch 2.
- **Kill if:** it misses the pass bar, or the trigger does not beat the no-trigger arm.

### H3. Failed breakout, then breakdown and retest from the other side: the owner's setup C, T7 (rank 3; credibility MEDIUM-LOW)

- **Why:** trapped breakout buyers set up a stop cascade (Osler 2005 mechanism). T7 was the owner's biggest winner. The standard trap reversal (turtle soup) is the same idea.
- **Rule (short side):**
  1. **Zones:** as in H2, w {0.5, 1.0} × K {3, 6}.
  2. **Fake-out:** within the last N bars, max h > z_hi + φ·A.
  3. **Breakdown:** at bar i0, c[i0] < z_lo − β·A.
  4. **Retest from below:** the first bar i1 ≤ i0 + R with h[i1] ≥ z_lo − τ·A (τ = 0.1).
  5. **Entry mode**, one of:
     - (a) market at the close of i1 if c[i1] < z_lo, as in T7;
     - (b) TRIG_BEAR(j, z_lo) within 2 bars;
     - (c) a sell limit at z_lo − 0.1A placed at the close of i0, expiring after R bars (`kind="limit"`).
  6. **Stop:** max(fake-out high, z_hi) + σ·A.
  7. **Target:** {NEAREST_LEVEL ≥ 1R, 1.5R, 2R}.
  8. **Time stop:** tmax {20, 45} min.
  9. **Session:** 06:00-12:00 London local plus 08:30-11:30 ET.
- **Grid:**
  - Stage 1: w × K (4) × N {10, 20, 30} × φ {0.3, 0.6, 1.0} × β {0, 0.2} × R {5, 15} × entry mode (3) = 432. Cut it to about 216 by fixing w from H2's stage 1.
  - Stage 2 (top 5): σ {0.1, 0.3} × TP (3) × tmax (2) = 12 each.
- **Null tests:**
  - random direction;
  - the **no-fake-out twin** (plain breakdown then retest), which tests whether the failed break adds information.
- **Kill if:** it misses the pass bar, or it cannot beat the no-fake-out twin.
- **Note:** from the fake-out high to entry is often more than $4 when volatility is high, so the stop cap will skip many signals. Report the skip rate.

### H4. Cost and volatility gate: a filter applied to other hypotheses (rank 4; credibility MEDIUM as a filter)

- **Why:**
  - Intraday volatility and spread seasonality are robust (Batten 2017, Iwatsubo 2018; SOURCED web_research §2.2).
  - Volatility clusters (Cai 2001).
  - MEASURED cost/range (checks B): 0.18-0.22 at 13-15 UTC and up to 0.53 at 03-05 UTC.
- **Rule:**
  - COSTR(i) = predicted LiteFinance round trip ÷ the median M1 range of the same UTC hour over the prior 20 trading days (causal). The round trip is 0.42 at 07-20 UTC, 0.50 at 01-06 UTC and 0.67 at 00 and 22-23 UTC. Gate: COSTR ≤ c_max.
  - RVP(i) = the percentile of the 60-bar realized volatility against the same minute of day over the prior 20 days. Gate: q_lo ≤ RVP ≤ q_hi.
- **Grid:** c_max {0.20, 0.30, 0.40, off} × q_lo {0, 0.3} × q_hi {0.95, 1.0} = 16. Apply unchanged to the frozen stage-2 finalists of H1-H3 and H5-H7.
- **Accept if:** VALID net average R improves on at least 3 base hypotheses without cutting n by more than 60%.

### H5. Intraday momentum into the COMEX settle (rank 5; credibility MEDIUM that the effect exists, unknown for spot gold after costs)

- **Why:** across more than 60 futures, commodities included, the return from the prior close to 30 min before the close predicts the last 30 minutes (Baltussen et al. 2021 JFE; Gao et al. 2018; SOURCED web_research §2.3).
  - It does not fit the owner's style, but it is cheap and independent of H1-H3.
  - At about one trade per day, costs are small relative to a 30-minute move.
- **Rule:**
  - Signal: r = mid at 13:00 ET − the open of the trading day (18:00 ET after the daily break).
  - ATRd = the 20-day ATR of daily bars (prior days only).
  - If |r| ≥ θ·ATRd, enter at 13:00 ET in the direction of r.
  - Exit by time at {13:25, 13:30, 13:45, 14:00} ET.
  - Stop: κ × the median absolute 30-minute move over the prior 20 days.
  - If the stop exceeds $4: either skip, or clamp it to $4 (a swept choice).
- **Variants:**
  - H5b: signal from 18:00 ET to 16:00 ET; trade 16:00-16:45 ET.
  - H5c: signal from 08:20 to 08:50 ET; trade 13:00-13:30 ET.
  - Exclude FOMC days for exits after 13:30 ET.
- **Grid:** θ {0, 0.25, 0.5} × exit time (4) × κ {1, 2} × cap handling (2) × variant (3) = 144.
- **Null tests:** the same rule at 11:00 and 15:00 ET; random sign.
- **Power:** about 230 TRAIN and 100 VALID days, so the power is low. Require t ≥ 2 on TRAIN and VALID pooled within each variant family, in addition to the pass bar.

### H6. Opening-range break and retest (rank 6; credibility MEDIUM-LOW)

- **Why:**
  - Opening-range breakouts have evidence on oil and index futures (Holmberg 2013; Tsai 2019; SOURCED web_research §2.7).
  - Adding the video-style retest entry is new.
  - Plain breakout drift is already known to be small (SOURCED HANDOFF l.34), so the at-break arm is expected to fail.
- **Opening-range windows** (local time):
  - (a) 08:00-08:29 London;
  - (b) 08:20-08:34 ET, only on days with no 08:30 ET HIGH or MEDIUM row;
  - (c) 00:00-06:59 UTC (Asia; web_research H8).
- **Filter:** opening-range width ÷ ATRd within [p20, p80] of the prior 60 days; or no filter.
- **Rule:**
  - Break: the first M1 close beyond OR_hi + κ·ORw within 120 minutes of the window's end.
  - Entry: {at the break close, or on a retest: l ≤ OR_hi + 0.1A within R {5, 30} bars, followed by TRIG_BULL}.
  - Stop: {opening-range midpoint, OR_lo − 0.1A}.
  - Target: {1R, 2R, none with a time exit at 16:00 UTC}.
- **Grid:** window (3) × filter (2) × κ {0, 0.1, 0.25} × entry mode (3) × stop (2) × TP (3) = 324.
- **Null test:** the same rule on random 30-minute windows between 01:00 and 16:00 UTC, matched by day.

### H7. Second leg after a release (rank 7; credibility MEDIUM-LOW, and n is small)

- **Why:**
  - News brings volatility, not drift. The post-news drift is t = 0.33 (SOURCED news_llm §3).
  - Volatility stays 1.4-1.7x normal until T+44.
  - The owner's setup A applies to the range the release leaves behind.
- **Events:** HIGH rows of the types with strong price confirmation: CPI, NFP, the FOMC statement, PPI and retail sales (SOURCED news_llm §5.3). That is about 90-100 events in TRAIN+VALID.
- **Rule:**
  1. Wait W {15, 30, 45} minutes after T; W must be ≥ 30 when equity < $21.
  2. The post-release range PR = the high/low over [T, T+W). Require PR ≥ 2× the median same-clock 15-minute range of the prior 20 days.
  3. Signal: the first close beyond the PR edge + 0.1A within 90 minutes.
  4. Entry: {H2-style retest + trigger at the PR edge, or immediate}.
  5. Stop: beyond the retest swing + 0.3A.
  6. Target: {1.5R, 2R}.
  7. Time stop: 45 minutes.
- **Grid:** 3 × 2 × 2 = 12.
- **Null test:** the same clock times on non-event days with the same weekday.
- **Note:** n ≤ ~100, so it cannot meet n ≥ 150 alone. Report it as "promising or not" only.

### H8. Adding to winners on a new engulfing candle: a money-management overlay, not a signal (rank 8)

- **Why:** the video's add rule. Tickets were added in bursts, and T7 added on "a new engulfing" candle. Financed by locked-in profit, this is the one element that can raise flip odds without raising the initial risk (OPINION).
- **Rule:**
  - Applies only when equity ≥ 3 × the margin for one lot (about $25).
  - Base position: a frozen finalist of H1-H3 or H6.
  - Add one 0.01 lot when MFE ≥ a·R **and** a same-direction engulfing candle closes. Before the add, move the stop on every ticket to first entry + 0.45 (break-even plus cost).
  - Allow at most {1, 2} adds.
  - After the first add, close all tickets at the base target, or trail {1.0, 1.5}·A behind the best price.
  - Skip the add if the margin level after it would fall below 300%.
- **Needs code:** multi-ticket positions in `sim` and `account`, which do not exist yet.
- **Metric:** P(hit $100) and P(hit $1,000) from the day-block bootstrap, compared with no adds.
- **Grid:** a {1.0, 1.5} × adds {1, 2} × trail (2) = 8.

### H9. Rejection at the first touch of a round number (rank 9; credibility MEDIUM-LOW)

- **Why:**
  - Gold prices show barrier effects at round numbers (Aggarwal-Lucey 2007).
  - In FX, take-profit orders cluster at round numbers (Osler 2003).
  - SOURCED web_research §2.4. HANDOFF l.36: crossing a $10 level showed "nothing", so only the rejection side is left to test.
- **Levels:** multiples of $10, with $25 and $50 tested separately.
- **Rule (short side, touching from below):**
  - First touch: h[i] ≥ L − 0.1, with the max high over the prior M bars ≤ L − μ·A.
  - Trigger on bars i..i+2: upper wick ≥ 0.6·range and c ≤ L − 0.2A.
  - Entry: short at the trigger close.
  - Stop: the high since the touch + {0.3, 0.6}·A.
  - Target: {1R, 1.5R}.
  - Time stop: tmax {10, 20} min.
- **Grid:** L (3) × M {60, 240} × μ {1, 2} × stop (2) × TP (2) × tmax (2) = 96.
- **Placebo:** L + 3.7 and L + 6.3.

### H10. Fading the edge of a range at a strong zone: setup B with a real stop (rank 10; credibility LOW)

- **Why:**
  - This setup blew attempt 1: T2 had MAE $1.97 against a wipe-out distance of $1.72 (SOURCED video_mindset §c).
  - Anecdote (n = 1, OPINION): with a $4 stop, T2 would have survived and reached its drawn target, which the price hit at about 09:20 UTC.
- **Rule (short side):**
  - Range: over the last P {60, 180} bars, max h − min l ≤ {8, 12}·A.
  - Zones: an upper zone with K ≥ 6 and a lower zone with K ≥ 3.
  - Price reaches the upper zone and TRIG_BEAR fires. Enter short.
  - Stop: z_hi + 0.3A.
  - Target: the **range midpoint**, not the far side.
  - Time stop: 30 minutes.
- **Grid:** P (2) × width (2) × K_upper {6, 8} × TP {midpoint, 1R} = 16.
- **Conflict check:** on the same zones, H2 (continuation) and H10 (fade) cannot both be positive unconditionally. Report both on one shared set of zones.

### H11. ICT Silver Bullet: a fair value gap after a sweep (rank 11; credibility LOW)

- **Why:**
  - The owner's knowledge library is ICT-heavy.
  - No systematic test was found (SOURCED web_research §2.12).
  - Earlier "evidence" was synthetic (SOURCED codebase §5.1). It is cheap to falsify.
- **Windows:** 10:00-11:00 ET (primary); also 03:00-04:00 and 14:00-15:00 ET.
- **Rule:**
  - Precondition: in the prior 60 minutes, a sweep as in H1, or a sweep of equal highs/lows (two confirmed pivots within 0.1·A5).
  - Then a displacement bar opposite the sweep: body ≥ 1.5× the average body of the prior 20 bars, body/range ≥ 0.7, leaving a fair value gap (`features.fvg`) of size ≥ g·A. Never use `ict.fvg_state`.
  - Entry: a limit order at the gap's midpoint, expiring after 25 minutes.
  - Stop: the sweep extreme + 0.2A.
  - Target: {2R, the opposite session extreme}.
- **Grid:** window (3) × g {0.3, 0.5} × target (2) = 12.
- **Placebos:** the other hours; entries at 0.25 and 0.75 of the gap instead of the midpoint.

### Excluded, with reasons

- **Runner and its variants:** they lose at LiteFinance cost (C1).
- **MRP micro-scalps and the 0.45-0.85 pt targets:** below the round-trip cost.
- **London PM fix:** the evidence predates 2015, and in summer 15:00 London coincides with the 10:00 ET releases.
- **Overnight/day bias:** the published evidence conflicts. Use it at most as a bias filter.
- **Volume-profile setups:** there is no real volume data.
- **Any entry generated by an LLM** (N5).

---

## 7. Flip-phase arithmetic and hand-over

All numbers are MEASURED in checks C unless labelled. The table uses a $3 stop.

| Gross edge (R) | Net edge at $3 stop | P(hit $100) from $13 | from $20 | from $30 | from $50 |
|---|---|---|---|---|---|
| 0 (b = 1.5) | −0.14R | 0.3% | — | — | — |
| +0.125R (b = 1.5) | −0.015R | 4.8% | 10.3% | 18.9% | 37.4% |
| +0.25R (b = 1.5) | +0.11R | 24.8% | — | — | — |

- **The $13 account survives at most two losses.**
  - $13 → about $9.6 after one $3 loss.
  - About $6.2 after two, which is below the $8.30 needed to open 0.01 lot.
- **The zero-edge ceiling is about 5%** (OPINION, analytic): (13 − 8.3) ÷ (100 − 8.3) ≈ 5.1% for a fair game with the margin floor. Costs push it lower: the Monte Carlo gives 0-2.3%.
- **Payoff shape under the barrier.**
  - With a real +0.25R gross edge, a higher win rate helps: 37% at b = 1, against 10% at b = 5.
  - With no edge, a larger b is slightly better ("bold play"), but still at most 2.3%.
  - OPINION: if an edge is found, prefer targets of 1-1.5R for the flip, not runners.
- **Starting equity is the dominant lever.** It matters more than any signal found so far.
- **Recommendations** (OPINION):
  1. No REAL trading until a candidate clears the §5 pass bar.
  2. If adding funds is acceptable, $30-50 changes the odds more than any research can. Otherwise, ask LiteFinance about gold on a Cent account (G10).
  3. **Flip phase:** one 0.01 lot, a stop of $1.2-4.0, one position at a time, and stop for the day after 2 losses.
  4. **Hand over** to fractional risk (≤ 2-3% per trade) once equity is ≥ $100.
  5. Allow H8 adds only from locked profit, and only at ≥ $25.

---

## 8. Order of work (OPINION)

1. **No market risk:**
   - Fix `account.py` (leverage 500) and the ruin barrier.
   - Add `LF_BASE` (with commission) and `LF_HARSH` to `sim.COSTS`, and rename `zero` to `duka_raw`.
   - Extend the calendar past 2026-09-30.
2. **Read-only work on the live terminal:** read the MT5 symbol and account spec (G3). Start a 1 Hz quote log on DEMO (G7) and fill logging (G1). Repair or replace the gateway (G2).
3. **Build and sweep** H1, H2 and H3 with the H4 gate as candidates in `xau_alpha/cand/` (TRAIN, then VALID, at most 2 processes). Run H5-H7 in parallel as cheap tests.
4. **Freeze at most 2 finalists**, run TEST once, then compute account-level flip odds with leverage 500 and the margin barrier.
5. **Run Laya in shadow mode on DEMO** alongside step 4 (N5).
6. **Go REAL only after steps 1-4 pass, at 0.01 lot.**

---

## 9. Note appended to HANDOFF.md

Per the standing "document as you go" rule, a short dated entry was appended to `HANDOFF.md` (2026-09-30, "critic synthesis"). It covers:
- the Runner reconciliation and HANDOFF's wrong gross-edge sign;
- the 1:500 margin barrier and the corrected flip odds;
- LF_BASE with $5/lot commission;
- the ±30-minute blackout below $21;
- Laya/Jeff in shadow mode only;
- the blocking items G1-G6.

Everything else is in this file.
