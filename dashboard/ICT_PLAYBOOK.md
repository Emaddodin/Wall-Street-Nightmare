# Gold Desk ICT playbook

Gold Desk is an analysis-only gold chart on your MT5's candles. It reads every timeframe the ICT way, reads every closed candle, and picks the model that fits the live market. It then tells you in words what to do now: "BUY on this close because…", "get ready", "wait for…", or "don't". It draws one forecast line from the ICT plan, Kronos and the quant model together. You place the trade yourself in MT5.

Everything here comes from the `goldictcontents` folder:

- *ICT Concepts for Gold Trading* (the long write-up)
- the @ICT_Success slides: Premium / Discount, OTE, Pulse Model, HRLR / LRLR, IFVG, Breaker Block, Unicorn, Top 5 Models, AMD, MSS + FVG
- the video (an NQ trader's week, read from its frames and an offline transcript; see "The video's entry" below)
- your notes to add XAU / XAG SMT and every candlestick pattern

The slides give definitions and drawings, but rarely numbers. Where a rule had to be chosen, this file says so (**chosen**).

## The video's entry

The video is 81 seconds of an NQ trader explaining his trades. The charts show NQ with the NY AM high / low, the London high and the NY lunch range marked. His long was taken around 09:45 New York after price dipped below the NY AM low, with the exit at the NY AM high.

The offline transcript is rough, but the rule he repeats ("every single one of my trades…") is clear:

1. **Rejection** from a higher-timeframe level: a wick into the level, then a close away from it.
2. The rejection leaves a **big fair value gap** (displacement).
3. An **inversion fair value gap** on the lowest timeframe (he uses 15 seconds). He enters on the close of the candle that inverts it.
4. The higher-timeframe order flow stays intact, and price runs with almost no drawdown.

In Gold Desk this is the **IFVG** model's candle-close trigger on M1 (your lowest timeframe):
- a sweep or rejection at a key level
- a candle closing through the opposite FVG
- the entry on that close

The candle read also names "wicked into <HTF key level> and closed away from it (rejection)" whenever it happens.

## Candle-close entries

The notes enter on a close, not on a touch, so every model's setup has a **trigger**: the candle that confirms it.

- **Zone models** (FVG, breaker, Unicorn overlap, OTE, BPR, order block, gaps): a candle trades into the zone and closes back out in the trade's direction, past the zone's middle. That's the rejection close.
- **CISD models** (Pulse, NDOG / NWOG): the CISD candle's close.
- **IFVG**: the inversion candle's close (the video's entry).
- **M5 setups**: also an M1 CISD made inside the M5 zone (timeframe alignment, the Pulse model's step 2).

A candlestick pattern on the trigger candle is named, and counts as extra confirmation. Then the full entry checklist is checked, and the answer is one of:

- **ENTER**: A or A+ grade, nothing required failing, and Kronos not against it.
- **SKIP**: it triggered, but something says no. The page shows what.
- **LATE**: an earlier candle.

## Candlestick patterns

`candles.py` has the 33 patterns of the Chart Guys cheat sheet, ported from the repo's MotiveWave study with the same rules, plus pin bars and inside / outside bars:

- **Single**: hammer, inverted hammer, dragonfly / gravestone / long-legged doji, doji, spinning top, marubozu, shooting star, hanging man
- **Double**: engulfing, harami, piercing line, dark cloud cover, tweezer top / bottom, kicker
- **Triple**: morning / evening (doji) star, abandoned baby, three white soldiers / black crows, three inside / outside up / down

Gold barely gaps, so gap patterns rarely print on M1 / M5. A pattern is only a confirmation of an ICT entry, never a signal on its own.

## The brain: what to do right now

`brain.py` reads each closed M1 and M5 candle:
- what it swept
- CISD, MSS / BOS / CHoCH
- FVG left, IFVG inverted, breaker made
- a higher-timeframe key level it rejected
- its pattern

It then gives one answer, in this order:

| Answer | When |
|---|---|
| **MARKET CLOSED / WAIT (news)** | gold is closed, or a high-impact USD event is within 15 minutes before / 10 after |
| **BUY NOW / SELL NOW** | a model's trigger candle just closed with the verdict ENTER (the model and the pattern are named) |
| **DON'T BUY / DON'T SELL** | a model triggered but the checklist says no |
| **GET READY** | an armed setup's zone is where price is; the next close decides |
| **WAIT FOR BUY / SELL** | a setup is armed further away: wait for the pullback, don't chase |
| **WATCH** | a sweep happened and the shift is missing: wait for a CISD beyond <level> |
| **WAIT** | nothing set up: where and when to look, with the higher-timeframe bias |

**The one line** is 30 minutes ahead from the live price. It blends:
- the best ICT plan (pull back to its zone, then away toward its liquidity)
- Kronos' calibrated 30 minutes
- the quant model's forecast

Each is weighted by its conviction. A voice that hasn't beaten a coin is turned down. When they disagree, the line goes flat and its label says so. It is scored live in the mesh as "Desk line".

**The overall analysis** weighs every voice (higher timeframes, the ICT setup, timeframe alignment, the last candle, premium / discount, Kronos, the quant model, SMT, news) into a verdict with a confidence. It lists what is against it and what lowers confidence now: news, the session, outside the killzones, a ranging market, voices disagreeing.

## The New York day map

`ictclock.DAY_MAP` splits the New York day into 16 segments. Each segment has the notes' purpose and the models that fit it:

- **Asia**: accumulation
- **London open**: the Judas swing
- **London Silver Bullet**
- **London expansion**
- **London-NY transition**
- **NY open**: the NY Judas, 08:50 and 09:50 macros
- **NY AM Silver Bullet**: gold's best window
- **NY late morning**
- **NY lunch**: avoid
- **NY PM open**
- **NY PM Silver Bullet**
- **NY close**
- **After hours**: avoid
- **Daily break**: avoid
- **Globex open**: NDOG / NWOG

The selector prefers the models that fit the current segment. Once a model has 15+ setups in that segment, it uses the model's record there. `playbook_backtest.py` prints every model's record by segment, and the page's day map shows the measured best and worst models per segment.

## Where it lives

| File | What |
|---|---|
| `ictlib.py` | The ICT events of one timeframe (a "tape"): swings, sweeps, displacement, FVG / IFVG / BPR, OB / breaker, CISD, MSS / BOS / CHoCH, dealing range, OTE |
| `ictclock.py` | New York time: killzones, Silver Bullet, macros, AMD phase; PDH / PDL, PWH / PWL, Asian range, NDOG / NWOG, midnight open |
| `smt.py`, `silver.py` | XAU vs XAG (and DXY) SMT divergence on M1 / M5 / M15 / H1; silver candles from your broker, else Yahoo (delayed) |
| `playbook.py` | The models, the checklist and grade, the "best model now" selector, each timeframe's own read, the talk |
| `brain.py` | The read of every closed candle, what to do now, the one line, the overall analysis |
| `candles.py` | 33 candlestick patterns plus pin / inside / outside bars |
| `kronos_signal.py`, `kronos_calib.py` | Kronos' 30-minute forecast from M1 and M5, blended and calibrated live |
| `kronos_finetune.py` | Fine-tunes Kronos on your gold M1 / M5 history |
| `playbook_backtest.py` | Each model's record on a year of gold, used as the selector's prior |

## The concepts and how they are read

All of these use closed candles only, so nothing repaints.

- **Swing**: a 3-candle pivot, known 3 candles later.
- **Liquidity sweep / Turtle Soup**: a wick through an untaken level, with the body closing back inside, on that candle or the next.
  - Levels: swings, PDH / PDL, PWH / PWL, the Asian range, NDOG / NWOG edges, the higher timeframe's swings, and $10 round numbers (Turtle Soup).
  - Wicks through a level are sweeps, never structure.
- **Displacement**: a candle whose body is ≥ 70 % of its range (from the notes) and whose range is ≥ 1 ATR (**chosen**).
- **FVG (BISI / SIBI)**: candle 1's wick and candle 3's wick don't overlap.
  - The gap must be at least 0.1 ATR and $0.15 (**chosen**: smaller is spread noise).
  - **CE** is its 50 %. A body close beyond the CE degrades the gap.
- **IFVG**: an FVG that a candle body closes through. It flips sides; a close back through it ends it.
- **BPR**: a bullish and a bearish FVG born within 12 candles of each other whose prices overlap (**chosen**: 12). The overlap is the zone, and the newer gap sets its side.
- **Order block**: the last opposite-colour candle at a swing.
  - **MT** is 50 % of its body. A body close beyond the MT means it has been abandoned.
  - A body close beyond its far extreme makes it a **Breaker** the other way. The Breaker is invalid on a body close fully beyond it.
- **Unicorn**: a breaker that overlaps an FVG from the same shift. The entry is the overlap.
- **CISD**: the run of same-colour candles that delivered price into the newest 20-candle extreme has an opening price. A body close beyond that open within 12 candles flips delivery (**chosen**: 20 / 12). It is the earliest signal and has more false positives.
- **MSS**: a body close beyond the last opposite swing, needing all three of:
  - a sweep at the leg's extreme
  - displacement
  - an FVG in the leg

  A close beyond a swing without those is **BOS** (with the trend) or **CHoCH** (against it).
- **Dealing range, premium / discount**: the last swing high and low. Above 50 % is premium and below it is discount. Buy only from discount and sell only from premium, with the higher-timeframe order flow.
- **OTE**: 0.62 / 0.70 / 0.79 of the leg.
  - Golden zone 0.62-0.70.
  - The stop is at the leg's start (level 1).
  - Targets are the leg's end, then the −0.5 extension (read off the slides' drawings).
  - A PD array inside OTE is the preferred entry.
- **IRL / ERL**:
  - IRL is internal liquidity: open FVGs, IFVGs and BPRs.
  - ERL is external liquidity: untaken swings, BSL / SSL.
  - Every timeframe says where it sits in the cycle. After an ERL raid, expect a move into IRL. After IRL is rebalanced, the draw is to ERL.
- **HRLR / LRLR**:
  - HRLR is relative equal lows (highs) swept at a key level, with a shift.
  - LRLR is the run of lower highs (higher lows) on the far side, which become the targets.
- **SMT, XAU vs XAG**:
  - Bullish: gold makes a lower low while silver holds a higher low, or silver sweeps while gold holds.
  - Bearish is the mirror.
  - Read on M1, M5, M15 and H1, from confirmed swings plus a "forming" check on the newest candles.
  - Correlation is shown, and SMT is ignored when it breaks (< 0.3).
  - DXY works the same way, inverted.
- **Time (New York)**:
  - **Killzones**: London 02:00-05:00, NY AM 08:30-11:00, NY PM 13:30-16:00, NY lunch 12:00-13:00 (avoid).
  - **Asian range**: 20:00-02:00.
  - **Silver Bullet**: 03-04, 10-11 and 14-15.
  - **Macros**: 02:33, 04:03, 08:50, 09:50, 10:50, 11:50 and 13:10.
  - **AMD**: Asia accumulates, London manipulates (the Judas swing), London-NY distributes.
- **NDOG / NWOG**: the 17:00 close to the 18:00 open (daily), and Friday to Sunday (weekly). Their edges and 50 % act as support / resistance.

## The models

There are 16 models, each searched on M1 and M5. The higher timeframe used is M15 for M1 entries and H1 for M5 entries.

| Model | Setup | Entry | Stop / target |
|---|---|---|---|
| Silver Bullet | inside a Silver Bullet hour: sweep, then a shift with displacement and an FVG | FVG CE | beyond the sweep / opposing liquidity, then the HTF draw |
| Judas Swing / AMD | London (or NY open) runs the Asian range against the HTF bias, then CISD / MSS back | FVG CE (or CISD level) | beyond the sweep / the other side of the Asian range |
| Turtle Soup | sweep of PDH / PDL, Asian H / L, an HTF swing, NDOG / NWOG or a $10 level, then CISD | FVG CE or CISD level | beyond the wick / the far end of the HTF dealing range |
| MSS + FVG | sweep at an HTF key level, then MSS | FVG CE | beyond the sweep / opposing liquidity |
| Unicorn | MSS that turns an OB into a breaker with an FVG inside | middle of the overlap | beyond the sweep; invalid when a body closes through the breaker MT and the FVG CE |
| Breaker Block | sweep, then the opposite OB fails | breaker edge | beyond the sweep |
| IFVG flip | sweep, then a body close through the opposite FVG | IFVG edge | beyond the sweep / BSL or SSL |
| OTE | a leg that shifted structure after a sweep | 0.62, or a PD array inside OTE | leg start / leg end, then −0.5 |
| Pulse IRL → ERL | price taps an HTF gap on its way to HTF ERL; LTF CISD (15m → 1m, as in the slides) | close of the CISD candle | beyond the low / HTF ERL |
| Pulse ERL → IRL | HTF ERL raided, then LTF CISD | close of the CISD candle | beyond the raid / HTF IRL 50 % |
| HRLR → LRLR | relative equal lows (highs) swept with a shift | FVG CE | beyond the sweep / the LRLR swings |
| Balanced Price Range | BPR with the HTF bias | BPR CE | beyond the range / liquidity |
| Order Block (MT) | continuation OB of the last BOS with the HTF bias | OB edge; out on a body close beyond its MT | beyond the OB |
| NDOG / NWOG | price reaches an opening gap, with the bias, then CISD | close of the CISD candle | beyond the gap / ERL |
| SMT reversal | sweep with XAU / XAG SMT, then CISD | FVG CE or CISD level | beyond the sweep |
| CISD + FVG (classic) | the old BOOM / CRASH: killzone raid of an M15 swing / Asia / PDH / PDL, then CISD and FVG | FVG CE | beyond the raid |

**Orders and targets**
- **Buy limits** sit one spread ($0.22) above the level, so they fill when the chart's bid touches it. That's the notes' spread buffer for longs.
- **Stops** go beyond the sweep, by $0.30 or 0.1 ATR, whichever is larger. The notes say 2-5 pips (**chosen**).
- **TP1** is the first opposing liquidity at least 1.5 R away, else 2 R.
- **TP2** is the higher timeframe's draw on liquidity, else 3 R.
- **Limit timing**: a limit waits 30 M1 or 12 M5 candles. A filled setup ends at TP1, the stop, or after 2 h (M1) or 6 h (M5) (**chosen**).

## Checklist and grade

Every setup is scored on the notes' four phases.

| Phase | Check | Weight |
|---|---|---|
| 1 Context | D1 / H4 / H1 bias agrees | 3 |
| 1 Context | at an HTF key level / PD array | 2 |
| 1 Context | entry in discount (buy) / premium (sell) of the HTF range | 2 |
| 2 Time | killzone, Silver Bullet or macro; NY lunch counts against | 2 |
| 3 Validation | sweep | 2 |
| 3 Validation | MSS with displacement (a bare CISD earns half) | 2 |
| 3 Validation | SMT with silver | 1.5 |
| 3 Validation | Kronos' next 30 minutes | 2 |
| 4 Entry | reward / risk ≥ 2 to the draw | 1 |
| News | a high-impact USD event within 15 minutes blocks the setup | — |

- An unknown check counts half.
- Grade by score: **A+** ≥ 85 %, **A** ≥ 70 %, **B** ≥ 55 %, **C** below that.
- Continuation models require the HTF bias to agree.

## Best model now

Each model gets a score:

```
0.40 × fit + 0.25 × record + 0.35 × setup on the board
```

- **Fit**: its time window × whether a trend or range market suits it.
  - Regime: M15 efficiency ratio ≥ 0.30, with M15 and H1 structure agreeing, means trend.
  - Silver Bullet and Judas score 0 outside their windows.
- **Record**: the shrunk mean R from `playbook_backtest.py` plus every setup it armed live, scored in `~/.golddesk/playbook_track.json`. "Shrunk" means (sum of R) / (n + 30), so a few lucky trades can't crown a model.
- **Setup on the board**: the grade score of the setup it has armed (or filled) right now.

The page shows the whole ranking with each model's record and what it is waiting for.

## Kronos, 30 minutes ahead, tuned for M1 and M5

**The live forecast**
- After every closed M1 candle, Kronos forecasts the next 30 M1 candles (16 sample paths).
- After every closed M5 candle, it forecasts the next 6 (12 paths).
- The two are blended into one 30-minute path, band, up-probability and expected move (`state.kronos.m30`).
- The blend weights follow each source's live skill, with a prior of 1.5 : 1 for M1.

**Live calibration** (`kronos_calib.py`) learns from every forecast once its 30 minutes are over:
- Platt scaling for the up-probability.
- A shrink factor and a bias for the move.
- A Brier skill score and hit rate against the coin-flip range.

Calibration does not create an edge. It shrinks Kronos to what it actually achieved on your gold feed, and the page shows those numbers.

Start it from a measured prior instead of nothing:

```
python3 kronos_backtest.py --csv data/dukascopy_xauusd_m1.csv.gz --tf M1 --horizon 30 --calib-out
python3 kronos_backtest.py --csv data/dukascopy_xauusd_m1.csv.gz --tf M5 --horizon 6 --calib-out
```

**Real fine-tuning** (CPU works, but slowly; a GPU or Apple MPS is better):

```
python3 kronos_finetune.py --csv data/dukascopy_xauusd_m1.csv.gz --tf M1 --size small --epochs 2
python3 kronos_finetune.py --csv data/dukascopy_xauusd_m1.csv.gz --tf M5 --size small --epochs 2
```

- The model is saved only when its validation loss beats the pretrained one.
- Results go to `~/.golddesk/kronos_ft_M1` and `~/.golddesk/kronos_ft_M5`, and Gold Desk uses them automatically for the 30-minute forecast.
- Check it afterwards with `kronos_backtest.py` on the validation months.

## Measuring the models

```
python3 fetch_history.py dukascopy 2025-10-01 2026-09-30            # gold M1
python3 fetch_history.py dukascopy 2025-10-01 2026-09-30 XAGUSD     # silver, for SMT
python3 playbook_backtest.py data/dukascopy_xauusd_m1.csv.gz --silver data/dukascopy_xagusd_m1.csv.gz --split 2026-06-01
```

- It prints each model's record: by model, by timeframe, by grade and by killzone, before and after the split, each with a 95 % range.
- It writes `~/.golddesk/playbook_stats.json`, which the selector reads.
- A year takes roughly half an hour.

None of the models is proven until that table says so. Treat a model whose 95 % range sits above zero after the split as the only kind worth trusting.
