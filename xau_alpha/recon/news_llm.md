# News guards and LLM models ("Laya", "Jeff"): audit, historical calendar and a backtestable replay

Written 2026-09-30 for branch `for-dear-opus-5.5`. Every claim has a label:
**MEASURED**: I computed it; the script is named. **SOURCED**: taken from a cited file or URL. **OPINION**: my judgement.
No production file was modified. All new code and data are under `xau_alpha/`.

---

## 0. Bottom line

1. **None of the guards or models has valid evidence of adding edge.** Every edge claim in the repo comes from
   backtests on the synthetic `gold_m1_*.csv` series or from state that was frozen during the backtest. That covers the README "With Politician Brain"
   table ($60 → $22.2M, PF 5.42), the regime-prior win rates (89.4%, 83.7%, ...) and the trade-journal RAG "twins".
   SOURCED: README.md §3; `data/regime_analytics_summary_full.json`. MEASURED: the RAG journal
   `data/regime_trade_journal_full.csv` has entry prices of $2,482–2,911 over 2025-01-21..2026-09-20. Real Dukascopy
   XAUUSD over the same period traded at $2,704–5,593. `politician_regime` is `TRADE_WAR_TARIFFS` on all
   6,613 rows, and every BUY carries boost 1.65 / TP 1.8.
2. **When all feeds fail, the Politician brain defaults to a long bias.** With the network blocked it keeps its two hard-coded
   bullish seed headlines. The result is STRONG_BULL with heat 67.5, which **vetoes every SELL and boosts every BUY ×1.65 in size and ×1.8
   in TP**. The same thing happens for about 2 s after every restart. MEASURED: `news/guard_audit.py` part A.
3. **The trade-journal RAG vetoes all SELLs from 09:00 to 23:59 UTC** under the default rule-fallback oracle, whatever the headline regime.
   All 7 regimes map to the same RAG coordinate. It does this because the synthetic journal's SELL win rate is 10.8%. For a Runner signal, only 9 of 24 UTC hours can take a
   short even with neutral news. MEASURED: guard_audit parts D and E.
4. The **calendar freeze is deterministic and now fully replayable**. The vectorised replay matches the unmodified
   production function in **0 of 24,000** sampled minutes (MEASURED: `news/replay.py::parity_check`). The window it uses,
   [T−15, T+5], mostly blocks near-normal minutes before the release (median 1-minute range 1.12× the quiet baseline). The real spike is at T..T+4, and volatility stays
   elevated after T+5, which is exactly when production *re-opens* and even boosts BUYs.
5. **Laya** (`convaiinnovations/laya`, a 421M ModernBERT typed-decision model) is a general NLU model. Nothing indicates it was
   trained on market data. Its prompt contains no market data beyond two price strings. It **cannot run on this machine**: the torch 2.1.0 install is
   broken, and the first use downloads a 2.37 GB repository. **Jeff** (a Qwen3-4B LoRA) needs an 8.06 GB base model and a separate server,
   and it is not deployed. Neither has any evaluation on trading outcomes.
6. The deliverable calendar is `xau_alpha/data/econ_calendar.csv`: **319 rows** (220 HIGH, 99 MEDIUM), 2025-01-02..2026-09-30.
   The source is official agency schedules. **316 of 319 rows (99.1%)** are confirmed at the same UTC minute by an independent source (Nasdaq).
   **Price verification:** 56.6% of HIGH release minutes show a strict ≥2× one-minute range spike (placebo at T±60 min: 5.6%).
   In 80.1% of HIGH minutes the range at T is larger than at both T−60 and T+60 (about 33% would be expected by chance).

---

## 1. What runs where

| Engine | Guard calls, in order | Source |
|---|---|---|
| **Apex** `run_xau_broker_live.py` (entry block ~l.1174) | `politician.check_calendar_freeze(15)` → spread > $0.45 skip → `is_in_allowed_session(allow_news_expansion=post)` → signal → ATR filter → `await laya_oracle.evaluate_setup()` → micro-capital guard (< $100: veto if `!is_valid` or trap ≥ 0.50 or confluence < 6.5; 0.02 lot if confluence ≥ 9 & trap ≤ 0.15 & bal ≥ $28) → TP expansion `atr·(tp_mult−1)·2.5` | SOURCED (code) |
| **Ghost / Runner** `ghost_grid/ghost_engine.py::_handle_signal` (l.505-533) | `politician_brain.is_entry_allowed()` (= `check_calendar_freeze(5)`) → `await laya_oracle.evaluate_setup({dir, entry, sl, wick, "BREAKOUT_RETEST", trend_aligned=True, hour})` → veto if `!is_valid`. **Exceptions in either are logged and ignored (fail-open).** The Runner signal's `wick_ratio` is the constant 0.6. | SOURCED |
| Inside `LayaOracle.evaluate_setup_sync` | (1) `check_calendar_freeze(15)` else `MacroWatchdog.is_entry_allowed()` → (2) `RegimePriorEngine` → (3) `PoliticianBrain.evaluate_entry_macro_fit` (headline bias) → (3.5) `TradeJournalRAG` → (4) ICT RAG text (model path only) → (5) Jeff/Laya model if loaded, else (6) the rule fallback | SOURCED |

Backend choice happens in `_warmup_model`. `JEFF_SKIP_HEAVY_WEIGHTS=1` selects the rules. If `JEFF_URL` is set, Jeff is used. Otherwise `laya.Router()` is tried and the rules are the fallback. HANDOFF says the VPS
demo now runs the real Laya model at about 1.3 s per decision (SOURCED: HANDOFF.md, not re-measured).
Locally, `from laya import Router` fails, so the rules are used (MEASURED).

**Dead or inert code.** `MacroWatchdog` has no production caller of `register_scheduled_event` or `assess_headline_heuristic`, so it always returns SAFE.
`PoliticianBrain.evaluate_full_sentinel` and `evaluate_regime_fit` are not called in production.
`LayaOracle.evaluate_momentum_exhaustion` is only used in tests. `LLAMA_COMPLETION_URL` in the Apex runner is unused.
`macro/slm_intuition` exists only as a `.pyc` and is never imported. (SOURCED: grep over the repo excluding `_archive`.)

---

## 2. Per-component audit

Legend: **Det.** = deterministic given its inputs. **Replay** = can it be reproduced for past dates.

### 2.1 PoliticianBrain calendar freeze (`check_calendar_freeze`), the only news guard that actually blocks entries
- **Decides:** freeze or allow new entries, and whether the "post-news expansion" window is active.
- **Inputs:** the FairEconomy `ff_calendar_thisweek.json` (USD rows, all impacts, polled every 90 s), plus the wall clock.
- **Rule:** a HIGH event at T freezes now ∈ [T−w, T+5 min], with w = 15 in Apex and inside the oracle and w = 5 in `is_entry_allowed`.
  A MEDIUM event freezes [T−5, T+5]. A HIGH event also sets post = now ∈ (T+5, T+45]. In Apex, post bypasses the session filter, so NY-open trading
  12–15 UTC is allowed right after 08:30 releases. In `evaluate_entry_macro_fit`, **BUY only** gets size ×1.5 and TP ×2.0, and this check comes before the
  counter-trend veto.
- **Offline fallback:** used only when the week's feed has no event at or after now−60 min. It freezes Thursday 08:30 NY claims for [T−10, T+5] and
  "first Friday" (day ≤ 7) 08:30 NY NFP for [T−20, T+5]. It sets post for 40 or 55 min after those.
- **Det.:** yes. **Replay:** yes. The calendar-driven part is replayable with a historical calendar. The FF impact labels are only approximated
  (§5.4). The fallback is exact.
- **Coverage:** MEASURED in `news/out/freeze_coverage.json`. With this repo's calendar, w = 15 freezes 0.83% of M1 bars (83 h over 20 months).
  The offline fallback catches only **41 of 220** HIGH events at their release minute: 0 FOMC, 0 ISM, 0 JOLTS, 15% of CPI and 75% of NFP. Eight of 20 NFP releases
  were not on a first Friday: 2025-01-10, 07-03, 11-20, 12-16, 2026-01-09, 02-11, 05-08, 07-02.
- **Failure mode:** calendar fetch errors are swallowed at debug level and the fallback schedule is used, which is **fail-open for about 80% of HIGH events**.
  Ghost ignores exceptions from the brain (fail-open).

### 2.2 PoliticianBrain headline state (`evaluate_entry_macro_fit`)
- **Decides:** vetoes a SELL when bias = STRONG_BULL and heat ≥ 65. Vetoes a BUY when bias = STRONG_BEAR, unless the post-news window is active.
  Sets size multipliers of 1.65 / 1.35 / 1.5 and TP multipliers of 1.8 / 1.4 / 2.0 for BUYs.
- **Inputs:** 6 RSS feeds (5 Google-News queries plus ForexLive), keeping the first 40 interleaved headlines. Bias and heat come from the first 15.
  - Sentiment is the sum of weighted keyword hits, normalised as `(bull−bear)/3` and clipped to ±1.
  - Heat is 4 + 1.5 per "heat" keyword. The index is 10× the mean, clipped to [20, 100].
  - Bias thresholds on the mean sentiment are ±0.10 (MILD) and ±0.35 (STRONG).
  - The regime is the most frequent tag.
- **Det.:** yes given the headlines. **Replay:** no. The feeds keep no point-in-time archive, and the code stamps every headline with the
  fetch time (`epoch=now`), so it does not even know their age.
- **Keyword matching is substring-based** (MEASURED, guard_audit B):
  - "Trump taps Kevin **War**sh as next Fed chair", "Soft**war**e stocks rally", "Powell **war**ns ..." and "A**war**d-winning ..." are all
    tagged SAFE_HAVEN_ESCALATION with sentiment +0.6.
  - "Fed cuts rates by 25bp" scores 0.0 (no hit on "rate cut").
  - "trade deal; tariffs lifted" is still tagged TRADE_WAR_TARIFFS.
- **Live snapshot:** MEASURED 2026-09-29 21:50 UTC, `news/out/live_snapshot.json`. 40 headlines gave MILD_BULL, heat 44, NEUTRAL_CHOP.
  BUY is allowed at ×1.35 size and ×1.4 TP. SELL is allowed.
- **Failure mode:** **fail-bullish** (§0.2). Any total feed outage, and the first ~2 s after any restart, gives STRONG_BULL. After the first poll heat is 67.5, which vetoes all SELLs.
  **Evidence of edge:** none that is valid (§3).

### 2.3 RegimePriorEngine (`evaluate_regime_fit`)
- **Rule** (all constants):
  - Veto: hour 23 UTC; strategy names containing TURTLE, SILVER or BULLET; wick < 0.45 (NaN counts as 0).
  - "A+ prime" (size ×1.5, confluence 9.8) needs BREAKOUT or RETEST, a prime hour {1,4,5,8–11,13,15–21}, trend alignment and wick ≥ 0.55.
  - "High probability" (×1.25): BREAKOUT/RETEST with wick ≥ 0.45 and trend alignment.
  - POC, VAH/VAL and SCALP_SELL get their own "titan" grades.
- **Det.:** yes. **Replay:** yes.
- The win rates in its notes come from the synthetic backtest (SOURCED: module docstring and summary JSON).
- MEASURED on real data: Runner trades opened at 23 UTC had PF 0.35 (n = 77, train+valid). That is consistent with the hour-23 veto,
  but n is small and this is one strategy. For the Runner, wick is the constant 0.6, so the wick veto never fires.

### 2.4 TradeJournalRAG (`query_historical_twins`)
- **Rule:** weighted-Euclidean 15-NN over (direction, strategy, hour, wick, ATR, regime) on the 6,613-row journal.
  - Veto if the twins' stop rate is ≥ 0.80 or their win rate is ≤ 20%.
  - "Sovereign" (size ≥ 1.4) if win rate ≥ 75% and trap ≤ 0.25.
- **Det.:** yes. **Replay:** yes.
- **Data problem:** the journal is synthetic-period output, all BUY-biased and all TRADE_WAR_TARIFFS (§0.1).
  All seven PoliticalRegime values map to the same coordinate: only TRADE_WAR_TARIFFS is in the RAG's map, and unknown names default to 1.0,
  so the regime input is a no-op.
- **Measured effect** (guard_audit D): **all SELL candidates between 09:00 and 23:59 UTC are vetoed** (105 of 168 SELL cells; twin win rate 0–20%).
  BUYs are never vetoed.
- **Failure mode:** if the CSV is missing the RAG returns a neutral allow (fail-open). If present, it imposes a synthetic directional prior.

### 2.5 LayaOracle rule fallback (runs whenever no model is loaded, including locally)
- **Rule:** `trap = min(prior_trap, 0.15 if wick ≥ 0.5 & trend else 0.45)`, plus 0.30 if not trend-aligned and plus 0.25 if wick < 0.40.
  - `is_valid = trap < 0.60`.
  - Grades: A+ if confluence ≥ 8.5 and trap ≤ 0.20; high if trap < 0.5; marginal if trap < 0.65; otherwise toxic.
  - Size = max(prior multiplier, grade multiplier), then blended with the RAG and politician multipliers.
- **Det.:** yes. **Replay:** yes (`news/replay.py::ProductionGuardReplay`).
- **Runner inputs** (MEASURED, guard_audit E, calendar forced clear):

| headline state | BUY valid hours | SELL valid hours | size mult (valid) |
|---|---|---|---|
| offline seed (STRONG_BULL, heat 67.5) | 23/24 (hour 23 vetoed) | **0/24** | BUY 1.65, TP 1.8 |
| neutral | 23/24 | **9/24** (00–08 UTC) | 1.5 |
| strong bear | **0/24** | 9/24 | 1.5 |

### 2.6 Laya model (`convaiinnovations/laya` through `laya.Router`, pip laya 0.3.4)
- **What it is:** a non-autoregressive typed-decision head on ModernBERT-large, 421M parameters, 512-token context. It answers `choice`, `score`
  and `noul` questions in one forward pass. SOURCED: laya METADATA/README in site-packages and `router.py`.
  The README's benchmarks are general NLU (MASSIVE, XNLI) plus four synthetic business workflows. There is no finance training.
- **Questions sent** (`laya_oracle.py` l.308-334):
  - setup grade: a choice of 4 options;
  - "Is this breakout an institutional liquidity trap?": noul, giving P(true);
  - confluence: a 0–4 score, rescaled to 0–10.
- **Rule:** `is_valid = trap < 0.60 and grade != toxic_trap`. The size multiplier is then overridden upward by the prior, RAG and politician boosts.
- **Prompt content** (MEASURED, `news/laya_replay.py` dry run on 300 Runner signals, 141 reached the model step):
  - Varying fields: direction, entry price, SL price, one of two ICT-text snippets, and the catalyst/headline string.
  - Constant fields for the Runner: wick "0.60", session "London/NY" (Ghost does not pass a session), trend "Yes" (hard-coded).
  - There are **no candles, volatility or time-of-day features**. All 141 states route to the English checkpoint, so there is no model reload thrash.
- **Runs here?** **No.** MEASURED: `import laya` fails because torch 2.1.0 is missing `libiomp5.dylib` and `libshm.dylib`. Weights are not cached.
- **Download size** (MEASURED from the HF API tree):
  - English `model.safetensors`: 842.6 MB (fp16), about 1.7 GB of RAM in fp32 on CPU.
  - `Router()` passes no `subfolder` for the English route, so `snapshot_download` pulls the **whole 2.37 GB repo** (all three checkpoints).
  - I did not download it, per the task rule.
- **Latency:** the README claims 193–464 ms on CPU when preloaded and a 7.4 s median reload (SOURCED). HANDOFF reports about 1.3 s per decision on the 2-vCPU VPS (SOURCED).
- **Det.:** yes for identical input (eval mode, no sampling). **Replay:** yes if the weights are available: `laya_replay.py --backend laya` caches every answer.
  The headline field must be fixed to a scenario.
- **Failure modes:**
  - Any exception falls back to the rules (fail-open to the rules).
  - The first use needs network access and 2.37 GB of disk.
  - The ~1.3 s decision is awaited before the order, which adds entry delay.
- **Evidence of edge:** none. There is no evaluation anywhere in the repo. OPINION: with inputs that carry no market information, any correlation with
  outcomes would have to come from direction or headline wording.

### 2.7 Jeff (`GestaltLabs/Jeff-1`, `scalper/brain/jeff_client.py`)
- **What it is:** a LoRA adapter (47.2 MB) on `Qwen/Qwen3-4B-Instruct-2507` (8.06 GB). MEASURED: HF API tree.
- **Setup:** needs Python ≥ 3.12, torch ≥ 2.11 and its own server (SOURCED: client docstring and HANDOFF). It is only used if `JEFF_URL` is set, with a 1.5 s timeout.
- **Other properties:** same questions and thresholds as Laya. **Not deployed, no evaluation.**
- **Failure:** any error falls back to the rules (fail-open).

### 2.8 ICT RAG (`ict_rag.py`)
Keyword and tag overlap scoring over 456 markdown concept files. It only produces the text snippet placed in the model prompt.
Deterministic. Does not affect the rule fallback.

### 2.9 Sentinels (not entry guards)
- **`llm_doctor.py`**: Qwen2.5-1.5B via `llama-server :8080`, 4 s timeout, 90 tokens, temperature 0.1. Near-deterministic, but a model reply is trusted verbatim.
  - The deterministic reflexes are:
    - spread ≥ 1.5 bps → PAUSE, resume below 0.8 bps;
    - floating P/L ≤ −$25 → EMERGENCY_FLATTEN;
    - position ≥ 20 min with negative P/L → EMERGENCY_FLATTEN;
    - a stalled or dead service → restart.
  - OPINION: the LLM can turn any incident into `RESTART_LIVE_SERVICE` or `EMERGENCY_FLATTEN`; the default when the key is missing is RESTART.
    Commands go through the single-slot `data/command.json`. The default ntfy topic is hard-coded.
  - Only the spread rule is historically replayable, and only with broker spreads.
- **`omni_angle_auditor.py`**: monitoring and notifications only (checks price in $1.8k–5k, spread > $0.50 warning, daily loss $5). It makes no trading decisions.

---

## 3. Evidence of edge: what exists and what I measured

**Repo evidence (all invalid).**
- The README's A/B table and the Trump-regime journals: synthetic candles plus a politician state frozen at backtest run time.
- `tests/*trump_regime*` call `evaluate_entry_macro_fit`, which reads `datetime.now()`. The backtest therefore applies today's calendar and headlines to every historical bar.
- MEASURED consequence: the journal shows boost 1.65 / TP 1.8 on all 6,345 BUYs and a constant regime.
- No live journal records guard decisions with outcomes; `data/live_trade_journal.csv` does not exist locally.

**Measured on real data** (`news/news_window_study.py`, Dukascopy M1 plus the 10-second bid/ask simulator, 'base' costs; only the train and valid splits
were used and the test holdout was not touched):

| window vs T (183 HIGH minutes) | [−30,−16] | [−15,−6] | [−5,−1] | **T** | [+1,+4] | [+5,+14] | [+15,+44] | [+45,+90] |
|---|---|---|---|---|---|---|---|---|
| median 1-min range ÷ quiet baseline | 1.12 | 1.12 | 1.12 | **2.58** | 1.81 | 1.68 | 1.42 | 1.26 |
| mean spread ÷ baseline | 1.09 | 1.09 | 1.18 | **1.88** | 1.36 | 1.18 | 1.13 | 1.10 |

- **Post-news drift from T+5 to T+45** is what the production BUY boost bets on. Mean +$0.39, median +$0.56, **t = 0.33**, 52.2% up (n = 182).
  The first 5-minute move continues on 48.9% of events (t = 0.2). There is no directional edge to justify a BUY-only boost. (MEASURED)
- **The Runner itself** (`lib/ref_runner.py` port) loses on real data in this simulator: PF 0.59 on train (n = 328) and 0.64 on valid (n = 782).
  That is another agent's port and it contradicts the PAXG-based PF 1.63 in HANDOFF. **Cross-check before relying on either figure.**
- Guard effect on the Runner (PF train / valid):

| variant | PF train / valid |
|---|---|
| all signals | 0.587 / 0.641 |
| drop [T−15, T+5] | 0.565 / 0.631 |
| full production guards, neutral state | 0.698 / 0.678 |
| full production guards, offline-seed state | 0.484 / 0.644 |

- The neutral-state gain comes mostly from the RAG SELL veto and the hour-23 veto. It does not rescue a negative strategy.
- Trade level:
  - signals inside the freeze window: PF 1.15 (n = 34);
  - signals inside the post window: PF 0.55 (n = 41);
  - SELLs 09–23 UTC: PF 0.55; SELLs 00–08 UTC: PF 0.82; all BUYs: PF 0.61.
  - The samples are small. OPINION: no guard shows a robust edge here.

---

## 4. Failure-mode summary

| Component | Network / model failure | Exception in the caller | Net |
|---|---|---|---|
| Calendar freeze | falls back to the Thu/first-Fri schedule, which catches 41/220 HIGH events | Ghost logs and continues | **fail-open** |
| Headline state | seed headlines give STRONG_BULL: SELL vetoed, BUY ×1.65 | same | **fail-bullish** |
| MacroWatchdog | n/a (never fed) | n/a | inert; latent bugs: a same-minute MEDIUM event hides a HIGH one (MEASURED), a tz-aware NY datetime is stored as UTC (a 4 h error, MEASURED), and one HALT headline freezes forever |
| RAG | CSV missing → neutral allow | same | fail-open; when present, a synthetic SELL veto |
| Laya / Jeff | falls back to the rules | same | fail-open to the rules; adds 1.3 s of latency when up |
| llm_doctor | falls back to deterministic reflexes | n/a | fail-active (restarts or flattens) |

---

## 5. Historical calendar (`xau_alpha/data/econ_calendar.csv`)

### 5.1 Build
- **Fetch and parse:** `news/fetch_official_schedules.py`.
- **Assemble and convert to UTC:** `news/build_calendar.py`, using `zoneinfo America/New_York`. For example, 08:30 ET becomes 13:30 UTC under EST and 12:30 UTC under EDT.
- **Columns** are the contract of `lib/data.py`: `ts_utc` (ISO, Z), `event`, `impact`, `source`.
  `data/econ_calendar_ext.csv` adds type, local ET time, reference period, the Nasdaq check, and actual/consensus values.

| type | n | impact | ET | official source |
|---|---|---|---|---|
| NFP / Employment Situation | 20 | HIGH | 08:30 | BLS schedule 2025/2026 (bls.gov via web.archive.org, snapshots 2026-08-19 and 08-22) |
| CPI | 20 | HIGH | 08:30 | BLS |
| PPI | 20 | HIGH | 08:30 | BLS |
| JOLTS | 21 | HIGH | 10:00 | BLS |
| Core PCE (Personal Income & Outlays) | 20 | HIGH | 08:30 (10:00 on 2025-04-30, 2025-12-05, 2026-01-22) | BEA `apps.bea.gov/API/signup/release_dates.json` |
| GDP advance (incl. the 2025-12-23 "initial" estimate that replaced the cancelled advance) | 7 | HIGH | 08:30 | BEA JSON + schedule pages |
| GDP second / third / updated | 13 | MEDIUM | 08:30 | BEA |
| Retail Sales | 21 | HIGH | 08:30 | census.gov/retail/release_schedule.html (+ 2025-03-06 archive) |
| ISM Manufacturing / Services PMI | 21 + 21 | HIGH | 10:00 | ismworld.org ROB calendar via web.archive.org (2025-07-08, 2026-08-24) |
| FOMC decision / press conference / minutes | 14 / 14 / 14 | HIGH | 14:00 / 14:30 / 14:00 | federalreserve.gov/monetarypolicy/fomccalendars.htm |
| Fed Chair semiannual testimony | 5 (+2 text-release rows, MEDIUM) | HIGH | 10:00 | federalreserve.gov/json/ne-testimony.json + 2025 testimony page |
| Jackson Hole Chair speech (2025-08-22 Powell, 2026-08-28 Warsh) | 2 | HIGH | 10:00 | federalreserve.gov/json/ne-speeches.json |
| Initial Jobless Claims | 84 | MEDIUM | 08:30 | Nasdaq API rows with an actual value |

- **Shutdown handling (SOURCED):**
  - Oct 2025 NFP was cancelled. The Sep NFP moved to 2025-11-20 and Oct+Nov to 12-16.
  - Sep CPI moved to 10-24, the Oct CPI was cancelled, and Nov CPI came 12-18.
  - The Q3 GDP advance was cancelled and replaced by the 12-23 initial estimate.
  - No claims were published from 2025-10-02 to 11-13. Nasdaq's backlog rows at artificial minutes (03:10, 08:24–08:29) were dropped.
  - The Jan/Feb 2026 BLS shifts (NFP 02-11, CPI 02-13, PPI 01-14 and 01-30) come from the 2026 schedule.
- **Known gaps:** there is no second-day Senate testimony for July 2026 (not listed by the Fed or Nasdaq). The 2026-07-14 10:00 hearing time is
  assumed (the Fed JSON shows the 08:30 text release). Unscheduled shocks such as tariff announcements and Fed-chair news are not included.

### 5.2 Independent cross-check (MEASURED)
- `news/fetch_nasdaq_calendar.py` pulled 639 days from `api.nasdaq.com/api/calendar/economicevents`.
- Two quirks were found and corrected:
  - `date=D` returns day D−1;
  - the `gmt` column is a fixed UTC−4 clock.
- **316 of 319** calendar rows appear in Nasdaq at the same UTC minute. The 3 exceptions are the Fed testimony text-release rows and the Warsh
  2026-07-14 hearing, which Nasdaq does not list.

### 5.3 Price verification (MEASURED, `news/verify_calendar.py`, full calendar in the data range rather than a sample)
- **Rule:** the one-minute mid range at T must be ≥ 2× the quiet same-minute median of the prior 20 weekdays, **and** larger than every minute in T−10..T−1.
- **Result:** HIGH unique minutes **111/196 = 56.6%**. Placebos on the same events: T−60 5.6%, T+60 5.6%, T+7 0.5%.
- The range at T beats both T±60 in **80.1%** of HIGH minutes. By type: CPI 19/19, NFP 18/18, FOMC 13/14, PPI 15/19, Retail 14/20, Jackson Hole 2/2.
- **Weak types:**
  - ISM 30–35% and JOLTS 26%: 10:00 ET coincides with the London PM fix and other releases, so the baseline is noisy.
  - PCE 37% and GDP advance 29%.
  - FOMC press conference 21%: the statement move precedes it, so the pre-10-minute test fails.
  - Chair testimony hearing 0/5: the prepared text had been released at 08:30.
- **DST spot checks:**
  - NFP 2025-03-07 at 13:30Z: ratio 7.2.
  - CPI 2025-03-12 at 12:30Z: 7.4.
  - NFP 2026-03-06 at 13:30Z: 8.0.
  - CPI 2026-03-11 at 12:30Z: 5.1.
- MEDIUM: 47/94 (50%). Claims alone: 21/56 (37.5%).

### 5.4 Limits versus production
Production freezes on FairEconomy's own USD High/Medium list, which is broader: for example ADP, CB Confidence and Fed speakers are Medium there.
FF also labels ISM Manufacturing, JOLTS and Claims as *Medium* (SOURCED: live FF JSON, 2026-09-29..10-02). Replaying with this calendar is therefore a
**lower bound** on production's frozen time.

---

## 6. How to backtest the guards

```python
import sys; sys.path += ["xau_alpha/news", "xau_alpha/lib"]
import replay
cal = replay.load_cal()                                   # data/econ_calendar.csv
m = replay.production_masks(t_ms, cal, window_minutes=15) # {'frozen','post','active_feed'}; exact production rule
fb = replay.fallback_masks(t_ms)                          # offline (no FF feed) rule
g = replay.ProductionGuardReplay(state="neutral", feed="calendar")   # or "offline_seed", "strong_bear"; feed "offline"
g.ghost_verdict(t_ms, "SELL", entry_price=px, sl_price=px + 4)      # unmodified production objects, fake clock
```

- `lib/data.py::news_block_mask` reads the same CSV. The replay parity is 0 mismatches on 24,000 samples.
- For the model path, `laya_replay.py --backend laya|jeff` records and caches the exact prompts. It needs a machine with working torch and the weights.

---

## 7. Recommendations (OPINION)

1. For the flip engine, **remove the headline bias, the RAG veto and the politician boosts from the entry path**. They encode synthetic-data priors and make the system long-only when offline.
   If a guard is wanted, use the replayable calendar mask and **choose its window by backtest**. The measured danger is T..T+4 (range 2.6× and 1.8×, spread 1.9×), not T−15..T−1.
2. Make the calendar local and deterministic (this CSV, extended weekly) instead of a live FF poll with a fail-open fallback.
3. Do not put Laya or Jeff in the order path without a replay evaluation (`laya_replay.py`) on real data. Today they add latency and RAM with no demonstrated benefit.
4. Fix the latent bugs before any reuse:
   - substring keyword matching;
   - `epoch=now` on headlines;
   - MacroWatchdog event ordering and tz relabelling;
   - LLM-doctor actions accepted verbatim.

## 8. Files (all new)
- **Calendar:**
  - `xau_alpha/data/econ_calendar.csv`: 319 rows.
  - `xau_alpha/data/econ_calendar_ext.csv`.
  - `xau_alpha/data/econ_calendar_verify.csv`.
- **Raw sources:**
  - `xau_alpha/data/news_raw/`: Nasdaq JSONL, `official_schedules.json`, cached pages.
- **Code:**
  - `xau_alpha/news/fetch_official_schedules.py`, `fetch_nasdaq_calendar.py`, `build_calendar.py`, `verify_calendar.py`.
  - `xau_alpha/news/replay.py`, `guard_audit.py`, `news_window_study.py`, `laya_replay.py`, `live_snapshot.py`.
- **Outputs:**
  - `xau_alpha/news/out/`: `guard_audit.json`, `verify_summary.json`, `news_window_study.json`, `freeze_coverage.json`, `laya_dry_run.json`, `live_snapshot.json`.
