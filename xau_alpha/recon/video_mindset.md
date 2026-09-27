# Video recon: the owner's reference method (XAUUSD M1/M5 "flip" scalping)

Date: 2026-09-30. Scope: 3 videos in the repo root plus 65 pre-extracted frames. Nothing outside `xau_alpha/` was modified.

Evidence labels:
- **MEASURED**: I computed it or read it directly off a frame, a transcript or the Dukascopy data.
- **SOURCED**: stated in a video (the transcript line or frame is cited). This is what the creator claims, and I have not verified it.
- **OPINION**: my interpretation.

Artifacts (all MEASURED outputs):
- Transcripts, timestamped (faster-whisper small, int8, CPU): `xau_alpha/video/{scalp,tzwj,v617}.transcript.txt`. Each file has two sections:
  - ORIGINAL: verbatim whisper output.
  - ENGLISH: all audio is English, so no translation was needed. This section has whisper mis-hearings fixed. Fixes tagged `[cap]` were confirmed against burned-in captions; `[ctx]` fixes are inferred from context.
  - Built by `xau_alpha/video/make_transcripts.py` from the raw `*.transcribe.small.txt` files.
- Frames: `xau_alpha/video/frames_scalp/` (36 keyframes + full-res crops), `frames_tzwj/` (177 keyframes + contact sheets), `frames_617/` (151 keyframes + contact sheets + full-res crops). Files are named `t_<MMmSSs>_<sec>.jpg` by video time.
- Trade table: `xau_alpha/recon/video_trades.csv`. Dukascopy cross-check: `xau_alpha/recon/video_trades_verify.txt`, produced by `xau_alpha/video/verify_trades.py`.
- Helper scripts: `xau_alpha/video/transcribe.py`, `extract_keyframes.py`, `verify_trades.py`.

---

## (a) Who and what each video is

| File | Length / format | What it is | Language |
|---|---|---|---|
| `-6170051788186523182.MP4` ("v617") | 13:00, 1280x720, encoder Lavf58.76.100 (MEASURED) | A YouTube video by **"MR P FX" ("I'm Mr. P")**, a talking-head presenter (SOURCED: transcript 12:54, subscribe overlay at 02:27/12:53). He trades gold live on an **Exness Pro** MT5 account `222212819 / Exness-MT5Real30, Hedge`, balance 50.00 USD (MEASURED: frame t_01m20s). The chart date is **27 Mar 2025**, running roughly 06:00-07:20 UTC. The video's claim: "$50 to over $3,000", then a withdrawal of $2,998.50 (MEASURED: history frame t_11m14s). | English (whisper lang=en p=0.91) |
| `TzwJTfYgK5FAujgWfLNa+fO1C_jia-DY.mp4` ("tzwj") | 16:16, 1644x1080, encoder Lavf58.76.100 (MEASURED) | An **iPad MT5 screen recording** with English captions burned in. There is no face on screen. The creator says: "I tried turning $14 into thousands... blew the whole account... started over and hit $5,300+", "the broker which I'm using is XNS [= Exness] unlimited leverage", "7 years plus experience", and "I drop most of these trades on my Telegram community" (SOURCED: caption frames 52/62/63 and transcript 00:00-00:12). Dates are **Mon 21 Sep 2026** (attempt 1) and **Tue 22 Sep 2026** (attempt 2). The iPad status-bar clock reads chart time +1 h, i.e. UTC+1 (MEASURED). | English (whisper auto-detected Yoruba 0.83 on the first 30 s, then forced to en and re-run; the text is English) |
| `scalp.MP4` | 0:37, 1170x2532 HEVC, creation 2026-09-22T13:36:44Z (MEASURED) | The **owner's own phone screen recording** of the YouTube app playing the v617 video, segment 02:52-03:45 (the $98 to $238 trade). The status bar shows "MCI LTE" + VPN + hotspot, and the control center is visible at 0:36. A German-language "Finom" pre-roll ad appears at 0:00 (MEASURED: frames). The transcript overlaps v617 03:10-03:45 nearly word for word (MEASURED). | English |
| `scratch/video_frames/frame_0001..0065.jpg` | 1280x841 | **Frames from the tzwj video**, one every 15 s, with frame *n* at t = (n-0.5) x 15 s. MEASURED: frame_0030 matches t=442.5 s with mean absolute pixel difference 1.37, versus >= 5.8 at +/-7.5 s. Re-checked on frame_0005: MAD 2.31 at t=67.5 s, versus 12.07 at 60 s and 13.18 at 75 s. The burned-in captions of frames 5, 36, 38, 39, 52, 62 and 63 also match the transcript at (n-0.5) x 15 s. | n/a |

Conclusions:
- None of the three videos shows the owner trading. v617 and tzwj are influencer/"teacher" content. MEASURED: both carry the same downloader encoder tag and non-camera file names. scalp.MP4 shows the owner watching and studying one such teacher. OPINION: "MCI" is Iran's Hamrah-e Aval carrier, which, together with the VPN, suggests the owner is in Iran.
- The owner's mindset therefore has to be inferred from what he chose to study and save: **micro-account "flip" videos (a $14 or $50 deposit taken to thousands in one session) on XAUUSD, using broker "unlimited" leverage**.
- OPINION: tzwj and v617 may or may not be the same creator. Both use Exness unlimited leverage, the same vocabulary ("boom", "nearest support/resistance", "optimize capital") and a West-African English accent. I cannot confirm identity.
- Both creators are verified against real prices (MEASURED): every tzwj and v617 entry and exit I could read lies inside, or within the broker spread of, the same UTC minute of Dukascopy XAUUSD mid data (see (e)). So the charts are real 2025-03-27 and 2026-09-21/22 gold, and the broker server time is GMT+0.

---

## (b) Setups as codeable rules

Common context (MEASURED from frames and transcripts):
- Symbol XAUUSD. Platform MT5 mobile (Android phone for v617, iPad for tzwj).
- Chart is plain candlesticks with **no indicators at all**. The only drawings are grey rectangles (zones), red horizontal lines (target and "stop" levels), trendlines on M5 in v617, and red arrows showing the expected move.
- Timeframes: tzwj executes and analyses on **M1** only. v617 says "a standard timeframe of 5 minutes and also a 1-minute timeframe" (SOURCED v617 00:27), with M5 for levels and trendlines and M1 for the entry.
- v617 lists its toolkit as (SOURCED v617 00:18-00:32 and the recap at 11:29-11:57):
  1. standard support/resistance
  2. "standard breakout and return [retest]"
  3. rejection candlesticks
  4. M5 + M1
  5. 1:unlimited leverage
  6. "build-up levels"

Definitions, written as code parameters. The creators give no numbers. The defaults marked (OPINION) are my calibration from the drawn zones, measured on the frames:

```
ZONE: horizontal band [z_lo, z_hi] where >= K_touch M1 swing highs/lows (3-bar fractal) cluster
      in the last L bars. Drawn zones measured on frames are ~$1.0-3.0 tall.
      Defaults (OPINION): L = 120-300 M1 bars, width <= 0.35 * (M1 ATR14 * 15) ~ $2.5,
      K_touch >= 3; "strong" = K_touch >= 6 ("reacted more than six times", SOURCED tzwj 02:56).
BREAK_UP(zone): an M1 close > z_hi. The creators treat a fast impulse ("kept going straight
      up, no reaction", SOURCED tzwj 06:30) as a valid break. Default (OPINION): a close
      beyond by >= $0.3 and the break leg's range >= 1.5 * ATR14(M1).
RETEST_UP: after BREAK_UP, price trades back into [z_lo, z_hi + tol] within R bars without an
      M1 close < z_lo. Default R = 3-90 bars (the tzwj retests came 1-60 min after the break).
REJECTION_BULL at the zone (the "confirmation", SOURCED tzwj 07:34-07:58, v617 11:29):
      a bullish engulfing (close > prev open, open <= prev close, body > prev body),
      OR a pin with lower wick >= 60% of range whose low touched the zone and close is above z_hi.
      "I didn't enter or place any pending trade because I want to enter after confirmation."
BUILD_UP level (v617 06:27-07:05): a small M1 consolidation that forms just beyond the broken
      level. Its break + retest is traded like BREAK/RETEST, as a second, tighter zone.
```

Consolidated entry state machine for Setup A (OPINION: my codification of the SOURCED steps below; parameters are defaults to sweep):

```
state IDLE      -> on BREAK_UP(zone)                         : state BROKEN, t_break = now
state BROKEN    -> if M1 close < z_lo                        : IDLE (break failed; candidate for setup C)
                -> if now - t_break > R_max (90 bars)        : IDLE
                -> on first low <= z_hi + tol                : state TOUCH1   (do NOT enter; tzwj 01:23)
state TOUCH1    -> on price leaving zone by >= 0.5*ATR14      : state AWAY
state AWAY      -> on second low <= z_hi + tol               : state TOUCH2
state TOUCH2    -> on REJECTION_BULL / bullish engulfing bar : ENTER long at bar close
                -> if M1 close < z_lo                        : IDLE
strong-trend variant: allow entry at TOUCH1 if the break leg >= 3*ATR14(M1) (T6)
NOTE (MEASURED): the second-touch wait is stated only for T1. T6 and T7 entered on the FIRST
      touch, 1-4 bars after the break close. Sweep {TOUCH1, TOUCH2} as a parameter; do not hard-code it.
exit: TP = nearest opposing swing/zone level (typically +$1..+$6), close all tickets at once;
      hard SL (our addition, creators have none) = z_lo - buffer; time stop 15-20 min (OPINION);
      session window 05:00-11:00 UTC; stop for the day at a higher-timeframe zone or daily target.
```

**Setup A: Break-and-retest continuation (the main setup; T1, T3, T4, T6, V1, V4 and, per the v617 recap, "the overall foundation for all of our trades today").**
- Long version. The short version is the mirror image.
  1. BREAK_UP of a zone. For shorts the context is "gold dropping, making pullbacks, breaking lows" (tzwj 00:52).
  2. Wait. **Do not enter on the first touch.** "As it came back I didn't enter immediately, I waited for it to drop and then come back again at that level" (tzwj 01:23-01:31).
  3. RETEST into the zone, then REJECTION_BULL on M1. Entry is a market order at the close of the rejection bar.
- Invalidation: an M1 close back through the far side of the zone. OPINION: in practice neither creator exits on this, see stops below.
- Target: **"the nearest resistance level"** (tzwj 08:15-08:28). The target is a red horizontal line at the prior swing or zone. MEASURED target distances: tzwj +$4.2 to +$11 from the average entry; v617 +$0.7 to +$2.
- Exits actually taken (MEASURED) were at +$4.3 to +$6 in tzwj and +$0.7 to +$1.6 in v617, usually **before** the drawn target.

**Setup B: Range-edge fade at a strong level (T2, the trade that blew attempt 1).**
- Context: a range, with the upper zone touched >= 6 times. Earlier, price broke range support downward, which the creator reads as "sellers still in control" (tzwj 02:22-02:50).
- Trigger: price returns to the upper zone AND reacts ("still even react before I now decided to take my trade", tzwj 03:06). Sell.
- Target: the far side of the range, the drawn line 4343.578, $10.9 away.
- The drawn "stop" line was 4356.74, $2.26 above entry. That is beyond the account's wipe-out distance of $1.72, so the account was liquidated first. MEASURED: equity went to 0.00 at about 08:35 UTC. Price then fell to the target around 09:20 UTC.

**Setup C: Failed breakout, then a break below the zone and a retest from below (T7).** Source: tzwj 13:19-14:08 and frame t_13m50s.
- Context: "from the bigger picture the market is a selling market / in a downtrend" (13:21-13:31). The creator says he "zooms out"; no higher-timeframe chart is shown, and the chart stays on M1.
- Sequence, MEASURED on the frame:
  1. The zone at 4331-4334 (the 08:39 swing high) was broken upward. T6 bought that breakout.
  2. Price spiked to about 4337.5, then fell back into the zone ("that was a fake out", 14:04).
  3. It then "broke the support level and went back up there to retest" (13:54-14:00). Dukascopy (MEASURED): the 10:13 bar made a low of 4328.86 and closed at 4329.44, below the zone. The 10:14 bar rallied to 4332.02, back into the zone.
  4. Sell on that retest at 4331.5-4332.1, during the 10:14 bar. This was the first touch, one bar after the break, not a second touch.
- As code: Setup A mirrored (BREAK_DOWN of the zone, RETEST from below, bearish trigger), with an added filter: the zone must have had a failed BREAK_UP within the last N bars (OPINION: N ≈ 30).
- Target: the "nearest support", drawn line 4325.012.
- Adds: "I saw an engulfing pattern and I added another few sells" and "since I found my favourite candlestick pattern" (14:08-14:37). Each new bearish engulfing in favour of the trade is an add trigger. The adds were at about 4329.8, lower than the first entry, which makes this pyramiding.

**Rule stated for strong trends** (tzwj 11:38-11:55, SOURCED): "if the trend is very very strong... wait for the market to pull back to tap in your resistance zone and then you enter buys". This is the same retest entry as Setup A. When the trend is strong the creator skips the second-touch requirement. MEASURED for T6 on Dukascopy:
- The impulse ran 4322.4 -> 4338.2 in 09:50-09:59, about $16.
- The first M1 close clearly above the 08:30-09:00 high (4335.6) was at 09:58.
- The pullback went into the zone at 10:01-10:02, with lows 4332.5 and 4331.9.
- Entry was at 10:02, the first pullback, 4 minutes after the break close.

**Add rule, shared by all setups** (MEASURED from position counts):
- Tickets are opened in bursts of 3-7 at the same price.
- More are added while the position is open:
  - T2 went from 9 tickets at 08:25 UTC to about 14 at 08:30 UTC. Ratio of total P&L to per-ticket P&L: -4.10/-0.46 and then 92.36/6.43.
  - In V3 the adds were at worse prices.
  - In T7 the adds were at better prices.
- There is no fixed rule for when to add. The stated triggers are "optimize my capital" (V2) and "a new engulfing" (T7).

**Setup D: Same-level re-entry after taking profit (T5).**
- "I entered new buys around the same place I closed it the other time" (tzwj caption frame 39). The trigger is a new bullish engulfing at that level (frame 40).
- OPINION: this is setup A applied to the level just reclaimed.

Exit and management rules. These are what is actually done, MEASURED from the P&L sequence:

| Rule | tzwj | v617 |
|---|---|---|
| Broker SL/TP | **None.** The S/L and T/P columns are empty on every position list (frames 4-62). | None visible on any chart or position frame. |
| "Stop loss" | A drawn line. In every case it sits **beyond** the equity wipe-out distance (T2 4356.74 vs wipe 4356.20; T4 4314.92-4315.15 vs wipe 4315.78; the line's label reads 4315.145 at t_08m15s, so the line was moved or read differently on other frames). Effective stop = broker stop-out. | None named. "Any little fluctuation can take us out" (11:57). |
| Take profit | Manual close of all tickets at once ("close all positions, boom") at or before the nearest level. | Manual close at **account-balance milestones**: "double to 100 bucks", "1000", "at least 3k" (02:28, 05:19, 08:36). |
| Hold time | 2-5 min (T1, T3-T6), 12 min (T2, loss), 21 min (T7). | 1-15 min. |
| Adding | Up to about 25 tickets per idea, plus adds in the direction of profit (T7). | 7 to 16+ tickets per idea. Adds at progressively worse prices (V3: 3031.53 -> 3032.13). |
| Session | All entries 05:31-10:35 UTC (late Asia to London morning). | 06:00-07:20 UTC. |
| Trades per session | Attempt 2: 5 trades in 110 min (08:45-10:35 UTC). | 5 trades in about 80 min. |

The v617 creator states the end-of-session rule: stop once price "has hit a maximum resistance level... formations from this zone become risky" (12:04-12:18). OPINION as code: stop trading once price is at a higher-timeframe zone, or once the session target is hit.

---

## (c) Risk and money management mindset

MEASURED lots versus balance. Lots are estimated from P&L per ticket and the total floating P&L. Wipe-out distance = balance / (lots x 100 oz): the adverse move that takes equity to about 0.

| Trade | Balance before | Total lots | Lots per $100 | Wipe-out distance | Result |
|---|---|---|---|---|---|
| T1 | $14.00 | 0.06 | 0.43 | $2.33 | +34.22 |
| T2 | $48.22 | ~0.28 | 0.58 | **$1.72** | **-48.22 (liquidated)** |
| T3 | $14.00 | ~0.08 | 0.57 | $1.75 | +38.29 |
| T4 | $52.29 | ~0.30 | 0.57 | $1.74 | +155.09 |
| T5 | $207.38 | ~1.25 | 0.60 | $1.66 | +523.31 |
| T6 | $730.69 | ~3.0 | 0.41 | $2.44 | +1287.72 |
| T7 | $2018.41 | ~10.4 (margin 3464.48 shown) | 0.52 | $1.94 | +3332.70 |
| V1 | $50 | 0.70 | 1.4 | $0.71 | ~+48 |
| V3 | $238.30 | 5.0 | 2.1 | **$0.48** | +799.20 |
| V4 | $1037.50 | ~8 | 0.77 | $1.30 | +1509.65 |

Key facts (MEASURED):
- **The whole balance is at risk on every trade.** Effective leverage at entry is roughly 1,900-6,400:1, which is only possible with Exness "unlimited" leverage. Profit is fully compounded into the next trade.
- The Dukascopy **median M1 bar range over the last 60 days is $1.69** (p75 $2.49, p90 $3.57). **One ordinary M1 candle against the position equals the wipe-out distance** in tzwj, and exceeds it by 2-3x in v617.
- T4 survived with an MAE of $1.64 against a wipe distance of $1.74, i.e. **$0.10 from liquidation**, on Dukascopy mid. The broker feed can differ by a few tenths.
- T2 MAE was $1.97 against a wipe distance of $1.72, so it was liquidated. That is consistent with the video.
- Money is "money I can afford to lose" (tzwj frame 38). The next burned-in captions (frames t_09m26s and t_09m32s) justify the all-in sizing: "I'm a millionaire in dollars so um ... and that is why I'm risking it all" (SOURCED). The $14 is play money for the creator. That premise does not transfer to an owner who needs the flip to work.
- The creator claims "I already know the lot size which I'm using before I enter it and I already know how much I'm risking" (tzwj 16:03-16:12, SOURCED). He also says he usually does not show SL/TP on the chart. MEASURED: no broker SL is set on any position. The drawn "stop" lines lie beyond the equity wipe-out price, e.g. T4 line 4315.145 (frame t_08m15s) against a wipe price of about 4315.78. So the risk he "knows" is the whole balance.
- After a blow-up the answer is **re-deposit and "try the second time"** (tzwj 05:19, 06:01). This is attempt-based: a series of lottery tickets, with the stake reset to a fixed small deposit.
- There is no averaging-down grid and no martingale in lot size: lots are not increased after losses. **Pyramiding does occur**: tickets are added at different prices in the same direction during the trade (T7 adds lower; V3 adds higher). Sizing grows only through compounding the balance.
- Withdrawal behaviour (v617): withdraw everything above the deposit at the goal ($2,998.50 out, $50 left). "We don't like tempting the broker" (12:24-12:33). tzwj: "don't stay too long in case they'll find you" (caption frame 36).
- v617 also says unlimited leverage "might drop to 1:2000 as capital grows" (05:54-06:04). SOURCED claim; not verified here.

---

## (d) Psychology and values

All OPINION, grounded in the SOURCED quotes cited:
- **Goal = flip, not edge.** Targets are stated in account dollars ("$48 to maybe $500 or $300", "3k and above and we are good"), not in R multiples or win rates.
- **Values fast, small price moves with very large dollar results.** Wins were taken after 1-5 minutes. The lesson drawn from the blow-up was "close quickly at the nearest level, don't be greedy", not "use less size" (tzwj 04:49-05:15, 05:45-05:58). The creator also rationalises the blow-up: "my analysis is still correct, but the liquidation happened".
- **Confirmation-seeking at the level.** He waits for a retest plus a rejection or engulfing candle and does not use pending orders. The second touch was used in T1; T6 and T7 entered on the first touch. He believes a confirmation candle means "we shouldn't expect a drawdown" (tzwj 07:46).
- **Speed and adrenaline.** "Trading extremely fast... thinking very very fast", "storm into sniper entries", "boom" (v617 00:40, 01:05).
- **Broker-adversarial and marketing-driven.**
  - A Telegram community.
  - Giveaways: "fund 3 random traders $3,000 each at 1,000 likes" (v617 12:39).
  - "Trading since 2019 ... teaching this advanced price action since 2022" (tzwj 15:11-15:31).
  - v617 opens with "a $27 account with over $8,000 of withdrawal within 24 hours" and "a $239 account with over $9,000 of withdrawals" (00:00-00:13).
  - "Unedited proof" framing that shows only selected sessions.
  - All of these are SOURCED claims and unverified.
- **What the owner values** (inferred from what he saved): mobile-only execution, tiny deposits ($13-50), and a one-session flip. He recorded the $98->$238 segment specifically, which is the stacking-0.1-lots-with-unlimited-leverage moment ("my capital is still extremely low, let me add more trades").

---

## (e) Trades visible in the videos (for matching against Dukascopy)

Full table: `recon/video_trades.csv`. Dukascopy check: `recon/video_trades_verify.txt`. All times are UTC = broker server time. Prices are the broker's (bid for sells, ask for buys).

| ID | Date | Entry UTC | Side | Entry (seen) | Exit est. | Balance before -> after | Dukascopy entry-minute mid range | MFE / MAE $ |
|---|---|---|---|---|---|---|---|---|
| T1 | 2026-09-21 | 05:31 | SELL 3x0.02 | 4360.81-4361.27 | 05:33 ~4355.4 | 14.00 -> 48.22 | 4360.26-4362.01 | 6.95 / 0.91 |
| T2 | 2026-09-21 | 08:23 | SELL ~14x0.02 | 4354.475 | 08:35 stop-out | 48.22 -> 0.00 | 4353.54-4355.16 | 3.47 / **1.97** |
| T3 | 2026-09-22 | 08:45 | BUY ~4x0.02 | 4316.60-4317.24 | 08:48 ~4321.86 | 14.00 -> 52.29 | 4315.94-4320.92 | 5.56 / 1.16 |
| T4 | 2026-09-22 | 09:42 | BUY ~6x0.05 | 4317.51-4317.55 | 09:47 ~4322.7 | 52.29 -> 207.38 | 4315.88-4317.63 | 5.59 / **1.64** |
| T5 | 2026-09-22 | 09:50-51 | BUY ~25x0.05 | 4322.45-4323.65 | 09:52 ~4327.1 | 207.38 -> 730.69 | 4322.42-4323.76 | 7.77 / 0.66 |
| T6 | 2026-09-22 | 10:02 | BUY ~15x0.2 | 4332.30-4333.13 | 10:03 ~4337.1 | 730.69 -> 2018.41 | 4331.86-4334.75 | 4.90 / 0.74 |
| T7 | 2026-09-22 | 10:14 | SELL ~10x1.0 (+adds) | 4331.49-4332.09 | 10:34-35 ~4328.5 | 2018.41 -> 5351.11 | 4329.56-4332.02 | 3.76 / 0.63 |
| V1 | 2025-03-27 | ~06:00 | BUY 7x0.10 | 3028.49-3028.54 | ~06:01 | 50 -> ~98 | 3028.00-3029.88 | approx. |
| V2 | 2025-03-27 | ~06:02 | BUY ~19x0.10 | ~3030.45 | ~06:03 ~3031.2 | ~98 -> 238.30 | 3029.44-3031.60 | approx. |
| V3 | 2025-03-27 | ~06:04-05 | BUY 10x0.50 | 3031.53-3032.13 | ~06:06 ~3033.4 | 238.30 -> 1037.50 | approx. | approx. |
| V4 | 2025-03-27 | ~06:50-58 | BUY ~16x0.50 | 3029.56-3029.82 | ~07:03 ~3031.0 | 1037.50 -> 2547.15 | 3028.79-3029.80 | approx. |
| V5 | 2025-03-27 | ~07:14-21 | BUY >=13x0.50 | 3031.80-3031.89 | ? | 2547.15 -> 3048.50 | n/a | n/a |
| H1 | 2025-03-27 | ? | BUY >=9x0.50 | 3034.03-3034.08 | **05:24:28** @3035.287 | (history screen) | 05:24 mid 3034.53-3035.60 | see (f) |

Notes:
- T2 peak unrealised P&L seen: **+92.36 USD, equity 140.58**. The ask was 4351.260 at 08:30 UTC (frame t_03m45s), about $3.2 in favour. It was not taken, and 3-5 minutes later the account was at 0.00.
- The v617 entry minutes are estimated from chart axis labels, +/-2 min. MFE/MAE for V-trades is therefore not reliable. The tzwj entry minutes come from ticket timestamps and are reliable to the minute.
- Ticket numbers are in the CSV (for example T1 3256944670/951/5063 and T7 3264715246..540). They are useful if the owner has the same broker history.

---

## (f) Red flags

1. **No stop loss at the broker, and the account balance is the stop** (MEASURED: empty S/L columns; "stop" lines drawn beyond the wipe-out price). Every trade is an all-in bet that an ordinary 1-minute candle ($1.69 median) can end.
2. **Leverage is not reproducible on our target account.** The method depends on Exness "unlimited" leverage (~2,000-6,400:1 effective). On LiteFinance ECN at 1:1000 with $13, assuming margin = notional/1000 and 100 oz per lot:
  - 0.01 lot needs $4.33 margin at $4,330.
  - The theoretical ceiling is 0.03 lot, but $12.99 of margin means about 100% margin level at entry, so the spread alone would trigger margin-call or stop-out behaviour.
  - The practical ceiling is **0.02 lot**, about 150% margin level. That size wipes out after a ~$6.5 adverse move. The 0.06-0.08 lots used on $14 in tzwj are about 2-3x what 1:1000 allows. The LiteFinance stop-out level is not verified here.
3. **Survivorship and selection bias.** tzwj shows one blown attempt and one successful attempt. The successful path needed 5 consecutive wins at a wipe distance of ~$1.7-2.4. Base rate (MEASURED, random entries, last 60 days of Dukascopy M1, mid, no spread): P(+$5 before -$1.75) is 9% within 5 min, 20% within 15 min and 25% within 30 min. P(+$4 before -$2) is 15% / 28% / 32%. **Five in a row at 20-30% per trade is about 0.03-0.24%** unless the setup has a large real edge, which these videos do not demonstrate.
4. **Unresolved timestamp inconsistency in v617** (MEASURED):
   - The history screen shows 0.50-lot buys at 3034.03-3034.08 closed at **05:24:28** at 3035.287. That is consistent with Dukascopy at 05:24 UTC.
   - But the chart axes and prices place the video's "first trade from $50" at the ~3028.0 dip at **~06:00 UTC**, and the later trades up to ~07:20 UTC.
   - None of the trades shown live has entries at 3034.0x, and the Balance rows in the history are blurred.
   - The ~06:00 timing is independently confirmed by the owner's own recording (scalp.MP4 frame `full_12s`). MEASURED:
     - The M1 axis labels read "27 Mar 05:04 / 05:28 / 05:52".
     - The chart shows two peaks near 3035.5 and 3035.2, then a dip to about 3027.8. These match the Dukascopy highs at 05:20 (3035.97) and 05:40 (3035.53) and the 06:00 low (3028.00).
     - v617 frame t_09m38s (axis 05:18 / 06:06 / 06:54, red line at 3027.854) places V5 after about 07:10.
     - The H1 entries (ask 3034.03-3034.08, i.e. mid about 3033.8) fit Dukascopy around 05:10-05:15, before the dip.
   - Either the video is not chronological, or trades happened on the account before the "$50 start". OPINION: treat v617's $50 -> $3,048 narrative as unverified. The individual trade prices are real.
   - Caveat: I assume MT5 mobile History > Positions shows the close time. The 05:24:28 price only fits as a close price.
5. **Marketing incentives**: Telegram signals, giveaways, "unedited proof" framing, "don't tempt the broker". Neither video shows a track record beyond one session.
6. **Pyramiding** into the move (T7, V3) increases exposure while already all-in. There is **no martingale** and **no loss-averaging grid** (MEASURED: lots never increased after a loss). Averaging does appear in V3 in a mild form: adds at higher prices, with the position underwater at 05m10s (equity 190.40 vs balance 238.30).
7. **"Confirmation means no drawdown" belief** (tzwj 07:46). It is contradicted by T4, which went to within $0.10 of liquidation.
8. **News is absent.** "news", "CPI", "NFP", "FOMC", "calendar" and "session" appear in none of the three transcripts (MEASURED: grep). All trades fall in 05:31-10:35 UTC.
   - MEASURED against `xau_alpha/data/econ_calendar.csv` (US-centric, 319 rows): 2026-09-21 and 2026-09-22 have no rows. The nearest release on 2025-03-27 was claims plus GDP at 12:30 UTC, 5 h after V5.
   - So no video trade was news-exposed.
   - OPINION: this came from the chosen window by accident, not from a rule.
9. **The all-in sizing is justified by a wealth claim that does not apply to the owner.** "I'm a millionaire in dollars ... that is why I'm risking it all" (tzwj caption, 09:26-09:32). The creator frames the method as risking play money. The owner's $13 account is the real stake.

---

## (g) What is worth keeping for our engine (OPINION, to be backtested on real Dukascopy data)

- **Keep:**
  - The deterministic structure: M1 zone detection -> break -> **retest** (first or second touch, as a swept parameter) -> rejection/engulfing trigger -> target = nearest opposing level, with a 05:00-11:00 UTC window.
  - Short holds of 1-10 min.
  - A flat and stop rule at session end or at a higher-timeframe zone.
  - All of this is codeable with the parameters in (b).
- **Replace:**
  - The broker stop-out stop becomes a hard SL beyond the zone's far edge + buffer.
  - Size so that SL distance x lots x 100 <= a fixed fraction of equity. For a $13 flip at 1:1000 the practical cap is 0.01-0.02 lots anyway: 0.02 lot means a $6.5 wipe distance.
  - The flip goal must come from compounding a positive-expectancy setup, not from 2,000:1 exposure.
- **Test first** (cheap, on `data/candles/duka` and `duka_raw` ticks):
  - Does the break -> retest -> engulfing sequence beat the random base rates above? Use the same TP/SL grid.
  - Does "second touch" beat "first touch"?
  - The 7 tzwj trades plus V1-V4 are in-sample anecdotes: use them to validate that the detector *fires* on those minutes, not as evidence of edge.
- **News guard and Laya/Jeff LLM** (the user noted both exist: `scalper/brain/macro_watchdog.py`, `scalper/brain/laya_oracle.py`):
  - Neither creator uses news or AI, so the method is compatible with a hard news blackout. That blackout should be fail-closed; HANDOFF.md line 50 notes the current news freeze is fail-open.
  - See `xau_alpha/recon/news_llm.md` (a separate audit) for the facts that matter here (SOURCED from that file):
    - The production freeze window [T-15, T+5] re-opens while volatility is still elevated.
    - With feeds down, the Politician brain defaults to STRONG_BULL, which vetoes SELLs.
    - The RAG layer vetoes all SELLs from 09:00 to 23:59 UTC.
    - Laya and Jeff have no trading evaluation and cannot run on this machine.
  - Consequence for this method (OPINION):
    - T7, a SELL at 10:14 UTC and the trade that took the account from $2,018 to $5,351, would, per that audit, have been vetoed by the RAG layer under the default rule-fallback oracle.
    - Setup C and the short side of A/B should therefore be tested with the production guards off and with only a calendar blackout on.
  - Feed the LLM the codified setup features (zone touches, break size, retest age, trigger candle type, distance to the nearest level) as a veto or ranking layer, not as the source of entries. That keeps the rules backtestable.

---

## Method notes (MEASURED)

- Transcription used faster-whisper small, int8, CPU. The machine was heavily loaded (load average 16-48), so I ran 1 thread per process, at most 2 processes, beam 1, VAD on.
- Timestamps are video time. Whisper mis-hears some words (e.g. "XNS" = Exness, "straight/street" = trade, "cell" = sell, "Zal/ZOW USD" = XAUUSD). No translation was needed because all audio is English, so the English transcript is both the original and the translation.
- Second pass (this revision): re-read the full transcripts and a representative frame subset (all 6 tzwj contact sheets, v617 sheet 0, and full-res frames tzwj t_03m23s, t_03m45s, t_04m24s, t_08m15s, t_13m50s, v617 t_09m38s, t_11m14s, scalp full_12s, scratch frame_0005). Corrections: T2 peak float, T4 stop-line label, Setup C sequence, LiteFinance practical lot ceiling. Additions: burned-in caption quotes, add-rule, state machine, news-calendar check.
- Chart time = UTC was established by matching tzwj ticket times and prices to Dukascopy M1: T4 09:42 buy 4317.507 against the Dukascopy 09:42 range 4315.88-4317.63, and similarly for all tzwj trades.
