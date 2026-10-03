# Gold Desk

An analysis-only gold chart on your own MT5. Every chart and timeframe (1m to 1D) is your MT5's candles. Gold Desk reads them the ICT way, along with Kronos, a quant model, news and silver. Then it tells you, candle by candle, what to do:

- **BUY / SELL now on this close:** which model triggered, which candle confirmed it, and what agrees.
- **Get ready:** a setup is close to triggering.
- **Wait for…:** what has to happen first.
- **Don't:** a setup triggered, but something says no, and why.

It also pushes to your phone when a model is forming, ready or entering.

You trade yourself, in MT5 on your phone. Gold Desk never sends an order: there are no buy/sell buttons, and the server refuses order requests. It does show your open gold positions read-only, including trades you opened on your phone.

The rules come from your `goldictcontents` folder (the gold ICT write-up, the @ICT_Success slides and the video). See [ICT_PLAYBOOK.md](ICT_PLAYBOOK.md) for every concept and rule and where each comes from.

## Start it (Mac)

Paste this once in Terminal:

```
curl -fsSL https://raw.githubusercontent.com/Emaddodin/Wall-Street-Nightmare/refs/heads/claude/eager-dirac-gtv0q1/dashboard/mac/install.sh | bash
```

It installs Gold Desk in `~/GoldDesk` (with Kronos if PyTorch is available) and puts a **Gold Desk** icon on your Desktop. Double-clicking the icon does the following:

- updates Gold Desk
- copies the **GoldDeskBridge** EA into MT5
- starts Gold Desk on your MT5 and opens the page

The first time only, in MT5:
1. If GoldDeskBridge isn't under Expert Advisors, press F4 (MetaEditor), open Experts > GoldDeskBridge and press Compile. It must be version 1.11, which serves 1D and silver.
2. Drag GoldDeskBridge onto the XAUUSD chart and press OK.

Keep MT5 logged into the same account as your phone, so your phone's trades show on Gold Desk. MT5 is the only live source: if it isn't ready, the icon says what to do instead of falling back to another feed.

Manual start: `python3 server.py --mt5-bridge [--kronos small]`. Try it without MT5: `python3 server.py --demo` (synthetic prices).

## What the page shows

| Part | What it is |
|---|---|
| **What to do now** | The one answer (`brain.decide`):<br>• BUY / SELL NOW, with the trigger candle, the model, the candlestick pattern and the checklist<br>• DON'T, and why<br>• GET READY, WAIT FOR …, WATCH or WAIT<br>• WAIT on a two-way market (a strong buy and a strong sell at once)<br>• how your open position sits with the answer |
| **Chart** | Your MT5 candles. On top of them:<br>• **the one line:** 30 minutes ahead, blending the best ICT setup, Kronos and the quant model. When they disagree it goes flat and grey ("they disagree").<br>• setup zones, with the sweep and the shift (MSS / CISD)<br>• forming models, each with its level and progress<br>• ENTER arrows on the candles that triggered, and small marks on candles that did something in ICT terms<br>• SMC levels: structure, OB / BB, FVG / IFVG, liquidity, premium / discount, OTE, killzones, PDH / PDL, Asian range, NDOG / NWOG<br>• XAU / XAG SMT lines<br>• your MT5 positions |
| **Overall analysis** | Every voice weighed into one verdict, with a confidence and the risks right now:<br>• higher timeframes, the ICT setup, timeframe alignment, the last candle, premium / discount<br>• Kronos, the quant model and its session forecasts<br>• SMT and news |
| **Forming now** | Every model's live stage: watch → forming → set up → ready → enter. Each has its checklist and the exact next condition (e.g. "an M1 close above 2401.20"). The most reliable is named; models on the same sweep merge into one setup ("confirmed by …"). |
| **News** | The next high-impact event with a countdown and what a beat / miss means for gold, the wait window around it, headlines with their gold impact, the news bias and a summary |
| **Candle by candle** | What each closed 1m / 5m candle did (sweep, CISD, MSS, FVG, IFVG, breaker, rejection, pattern) and the answer at that close |
| **Timeframes top-down** | D1 bias, H4 narrative, H1 draw on liquidity, M15 setup, M5 confirmation, M1 trigger: each timeframe's own read |
| **Details** | The 16 models ranked for the live market, the New York day map (which models fit each part of the day, and how each really did there), the knowledge-mesh scoreboard, recent pushes, the glossary |

## Phone pushes

Gold Desk makes a private ntfy topic on first start and shows it in the header. Subscribe to it in the ntfy app.

A push goes out when a model reaches **forming**, **ready** or **enter**. The most reliable model is named first and the others forming are listed. The limits:
- once per setup and stage
- the same model and stage at most every 15 minutes
- at most one push per 90 seconds (entries never wait)
- at most 40 pushes a day
- nothing while the market is closed, and no ENTER push on a two-way market

`--ntfy-topic NAME` uses your own topic; `--no-alerts` turns pushes off.

## How it decides (all in the backend)

- **`ictlib.py`:** each timeframe's ICT events.
- **`ictclock.py`:** New York time windows and the day's levels.
- **`playbook.py`:** the 16 models, each with its candle-close trigger and checklist (A+ / A / B / C), ranked for the live market, plus each model's record.
- **`candles.py`:** 37 candlestick patterns.
- **`brain.py`:** turns all of it into:
  - the read of each candle
  - the answer
  - the forming models and the pushes
  - the one line
  - the overall analysis
- **`smt.py` / `silver.py`:** silver SMT from your MT5.
- **`news.py`:** the news box.
- **`kronos_signal.py`:** Kronos' 30-minute forecast from M1 and M5, calibrated live.
- **`quant.py`:** the quant model and the session models.
- **`mesh.py`:** the knowledge mesh. It records every model, voice and forecast at each candle and scores each one against what price really did 10, 30, 60 and 120 minutes later. Once a source is clearly better or worse than a coin (3 sigma, on 200+ samples), its earned trust raises or lowers its weight in the brain.

**None of it is proven.** The record on the page says how each model really did. The commands below measure it on history.

## Measure and train (on your Mac)

```
python3 fetch_history.py dukascopy 2025-10-01 2026-09-30            # gold M1 history (and ... XAGUSD for silver)
python3 playbook_backtest.py data/dukascopy_xauusd_m1.csv.gz --silver data/dukascopy_xagusd_m1.csv.gz --split 2026-06-01
python3 quant_train.py --mt5                                        # the 30-minute quant model on your MT5's candles
python3 quant_train.py --sessions --mt5 --mt5-bars 50000            # the session models (Asia -> London, -> overlap, -> NY)
python3 kronos_backtest.py --csv data/dukascopy_xauusd_m1.csv.gz --tf M1 --horizon 30 --calib-out   # Kronos' starting calibration
python3 kronos_finetune.py --csv data/dukascopy_xauusd_m1.csv.gz --tf M1 --size small --epochs 2     # fine-tune Kronos (GPU / Apple MPS helps)
```

What each one gives you:
- **`playbook_backtest.py`** prints each model's record by timeframe, grade, killzone and part of the day, for limit entries and for candle-close entries, before and after the split. The live ranking uses it as each model's starting record.
- **`quant_train.py`** prints a "beats a coin: yes / no" verdict on months it never saw. A model that doesn't beat a coin keeps almost no weight.
- **`kronos_finetune.py`** keeps a fine-tuned model only if it beats the pretrained one on held-out months; Gold Desk then uses it automatically.

## Files

| File | What it is |
|---|---|
| `server.py` | Local web server (127.0.0.1 only), MT5 bridge connection, the page's state |
| `mt5bridge.py`, `mt5/GoldDeskBridge.mq5` | Your MT5 as the price, candle (1m-1D), silver and position source; read-only |
| `boom.py` | The reading engine (historical name): runs everything at each closed M1 candle |
| `brain.py` | The answer, the candle reads, forming models and pushes, the one line, the overall analysis, the mesh votes |
| `playbook.py`, `ictlib.py`, `ictclock.py`, `candles.py` | ICT models, events, time and candlestick patterns |
| `smt.py`, `silver.py` | XAU / XAG SMT |
| `news.py` | Calendar and headlines with their gold impact |
| `kronos_signal.py`, `kronos_calib.py`, `kronos_finetune.py`, `kronos_backtest.py` | Kronos forecasts, live calibration, fine-tuning, backtest |
| `quant.py`, `quant_features.py`, `quant_train.py`, `quant_sessions.py`, `quant_sessions_train.py` | The quant model and the session models (ported from soloshun/Quantitative-XAUUSD-Strategy, MIT) |
| `mesh.py`, `mesh_report.py`, `nodes.py` | Knowledge mesh and its scoreboard; clock, calendar, cross-market nodes |
| `playbook_backtest.py`, `fetch_history.py` | Measuring the models on history |
| `smc.py`, `tfdesk.py`, `ictmodel.py`, `nowcast.py`, `engine.py` | The SMC drawing layer, timeframe desks, the trend reading and the older engine the reading still shares |
| `static/` | The page: a thin renderer of the backend's state, with TradingView Lightweight Charts 4.2.3 (Apache 2.0) |
| `ICT_PLAYBOOK.md` | The concepts, the models and the rules |
