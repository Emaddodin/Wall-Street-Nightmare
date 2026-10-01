# TradingView version (Pine Script v5)

`XAU_M5_Scalper_Kronos.pine` is the Gold Desk scalper (same rules as `../engine.py` and
`mt5/XAU_M1_Scalper.mq5`) plus the Kronos forecast, as one TradingView indicator. Entries are on
M5, gated by H4 regime, H1 structure and M15 zones (every layer one step up from the M1 original;
set the timeframes to 60 / 15 / 5 on a 1-minute chart to get the M1 version back).

1. TradingView, Pine Editor, paste the file, **Add to chart** on a 5-minute XAUUSD chart
   (preferably your broker's own feed).
2. Kronos: run `kronos_pine_line.py` where Kronos is installed, copy the `KRONOS|...` line it
   prints (M5 bars by default), and paste it into the indicator's **Kronos forecasts** box (Settings, Kronos).
   Several lines (newest last) draw several paths; `--history 20` backfills past forecasts so the
   Kronos filter can be judged on past bars.
3. Alerts: create an alert on the indicator with **Any alert() function call** to get the radar
   and execute messages with prices filled in.

Why a paste box: Pine cannot run a neural network or receive data pushed to it. `request.seed`
only ingests data once a day, which is useless for a 15-minute forecast. A forecast only applies
to the minutes after the bar it was made on, so it never looks ahead.

A Dukascopy backtest (Jan 2025 to Sep 2026) of the M1 version of these rules found no edge (about
30% winners, profit factor 0.53 to 0.79). The M5 version is not backtested yet. Treat the signals
as a study.

## Automatic lines from the VPS

`pine_feed.py` runs next to the VPS Kronos chart service (it only reads that service's latest
forecast; it never runs Kronos) and serves the recent forecasts as paste lines at
`http://<vps>:8791/?k=<chart token>` (your chart link with 8790 changed to 8791). Open that link,
copy everything, paste it into the indicator's Kronos box. It finds the chart's forecast JSON by itself
(Gold Desk's own forecast is the fallback) and writes M5 lines (`...|300`). The Gold Desk icon installs
it on the VPS (`vps/setup.sh` copies it to /root/kronos/pine and enables `kronos-pine-feed.service`).
