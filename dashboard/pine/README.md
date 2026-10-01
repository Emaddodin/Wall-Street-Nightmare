# TradingView version (Pine Script v5)

`XAU_M1_Scalper_Kronos.pine` is the Gold Desk scalper (same rules as `../engine.py` and
`mt5/XAU_M1_Scalper.mq5`) plus the Kronos forecast, as one TradingView indicator.

1. TradingView, Pine Editor, paste the file, **Add to chart** on a 1-minute XAUUSD chart
   (preferably your broker's own feed).
2. Kronos: run `kronos_pine_line.py` where Kronos is installed, copy the `KRONOS|...` line it
   prints, and paste it into the indicator's **Kronos forecasts** box (Settings, Kronos).
   Several lines (newest last) draw several paths; `--history 20` backfills past forecasts so the
   Kronos filter can be judged on past bars.
3. Alerts: create an alert on the indicator with **Any alert() function call** to get the radar
   and execute messages with prices filled in.

Why a paste box: Pine cannot run a neural network or receive data pushed to it. `request.seed`
only ingests data once a day, which is useless for a 15-minute forecast. A forecast only applies
to the minutes after the bar it was made on, so it never looks ahead.

A Dukascopy M1 backtest (Jan 2025 to Sep 2026) of these scalper rules found no edge (about 30%
winners, profit factor 0.53 to 0.79). Treat the signals as a study.
