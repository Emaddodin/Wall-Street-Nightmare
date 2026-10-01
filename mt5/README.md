# XAU M1 Scalper for MetaTrader 5

Two files that run on your own MT5 terminal, so every candle, level and backtest uses your broker's exact quotes.

| File | Type | What it does |
|---|---|---|
| `XAU_M1_Scalper.mq5` | Indicator | Draws buy/sell arrows, entry, SL, TP1, TP2, M5 zones and session lines on your M1 gold chart, shows the dashboard panel, sizes lots from your balance, and sends Radar and Execute alerts. It never places orders. |
| `XAU_M1_Scalper_Backtest.mq5` | Expert Advisor | Trades the indicator's signals inside the Strategy Tester only, so you get the full MT5 report on real tick history. It refuses to start on a live or demo chart. |

## Install

1. In MT5 open **File > Open Data Folder**, then go to `MQL5`.
2. Copy `XAU_M1_Scalper.mq5` into `MQL5\Indicators\` and `XAU_M1_Scalper_Backtest.mq5` into `MQL5\Experts\`.
3. Open MetaEditor (F4), open each file and press **Compile** (F7). Compile the indicator first.
4. Back in MT5, open your gold symbol on **M1**, then drag **XAU_M1_Scalper** from Navigator > Indicators onto the chart.

If MetaEditor shows any error, paste the full error list in the project thread and it will be fixed.

## Reading the chart

- **Blue up arrow / red down arrow**: a signal, printed only after the M1 candle closed. It never moves or disappears.
- **Lines of the active signal**: entry (blue or red), SL (crimson, thick), TP1 (green dotted), TP2 (green). They extend to the right with price labels until the signal resolves. The SL line moves to breakeven after TP1 and then trails.
- **Past signals**: short segments with the result in R where each one closed.
- **Dotted / dashed boxes**: active M5 fair value gaps / order blocks (blue demand, red supply).
- **Vertical lines**: London open and close (07:00, 10:00 UTC) and NY open and close (12:30, 16:00 UTC).

The panel shows, top to bottom: your broker server and live spread, the session and the Asian range, the H1 regime and shock guard, M15 structure and EMA channel, M5 RSI and zones, the overall bias, then the active signal with **your lot size for your current balance**, then the on-chart backtest of the last 5000 M1 bars.

## How a signal is built

All three higher timeframes must agree, using only bars that had already closed when the M1 candle closed:

1. **H1 regime**: close > EMA 50 > EMA 200 for longs (mirror for shorts). An ATR velocity shock (ATR above 1.8x its 50-bar average, or one H1 bar bigger than 2.5 ATR) blocks trades against the shock for 2 H1 bars.
2. **M15 structure**: last break of a swing was bullish (BOS or CHoCH) and EMA 21 > EMA 55.
3. **M5 momentum**: RSI(14) turning up and still below its upper band (mean + 1.5 standard deviations over 50 bars).
4. **M1 trigger** inside London or NY: an engulfing candle on more than 1.5x average tick volume, a rejection pin through a zone or a swing, an inside-bar breakout, or a liquidity sweep that closes back inside.
5. **Strict mode** (default) also needs an M5 FVG / order block touch in the last 3 bars or a sweep.

Stop: beyond the low (high) of the last 8 M1 bars plus 0.1 ATR. If that is closer than 0.8 ATR it uses 1.5 ATR instead, and the signal is skipped if the stop would be wider than 3 ATR. TP1 = 1.5R (50% off, rest to breakeven), TP2 = 2.5R, runner trails 1 ATR behind the close and exits if M15 structure flips.

Buy prices include the spread, because MT5 charts show the bid and you buy at the ask.

## Lot size

Lots come from your live balance x risk %, divided by the stop distance x your broker's tick value for this symbol, plus commission. If the minimum lot (usually 0.01) already risks more than your risk %, the panel turns yellow and says so. On small accounts a 50% partial is only possible from 0.02 lots up; below that, TP1 just moves the stop to breakeven.

## Alerts

- **Radar** (while the candle is still open): HTF is aligned and price is touching a zone or sweeping a swing. `XAUUSD SCALP RADAR: Setup forming on M1. HTF Confluence verified. Prepare for entry. [BUY]`
- **Execute** (on the candle close): `XAUUSD SCALP EXECUTE: BUY @ 2650.35 | SL: 2647.80 | TP1: 2654.18 | TP2: 2656.73 | Lots: 0.04`

Both pop up in MT5. For phone push, open **Tools > Options > Notifications**, enable push and enter the MetaQuotes ID from your MT5 mobile app (Settings > Messages).

## Backtest on your broker's real history

**Quick view**: the panel's BACKTEST block replays the last 5000 M1 bars on your chart with your broker's per-bar spread, $7/lot commission and 1 point slippage. Raise *M1 bars to analyse* in the inputs for a longer window.

**Full MT5 report** (recommended):

1. Open **View > Strategy Tester** (Ctrl+R).
2. Expert: `XAU_M1_Scalper_Backtest`. Symbol: your gold symbol. Timeframe: **M1**.
3. Modelling: **Every tick based on real ticks**. Pick a date range (start at least two weeks after the first date your broker has, so the H1 EMA 200 is warmed up).
4. Deposit and leverage: match your real account. The tester applies your broker's commission when the server provides it.
5. Press **Start**. The Backtest tab has net profit, profit factor, drawdown, win rate and every trade; tick **Visualize** to watch the arrows and panel bar by bar.

The tester runs the same indicator file, so its signals are identical to what you see live. Small differences from the on-chart block are expected: the tester fills on real ticks, while the on-chart block assumes each bar went open > low > high > close (bullish) or open > high > low > close (bearish).

## Things to know

- Session times assume your broker's server clock. Live, the indicator reads it from your terminal. In the tester it assumes the common New York +7 server clock (GMT+2 in winter, GMT+3 in summer); if your broker differs, set *Broker server clock* to Fixed and enter the offset.
- The per-bar spread MT5 stores is usually the lowest spread seen in that minute, so the on-chart backtest can be a little optimistic. Turn off *Use the spread your broker recorded per bar* to use a fixed $0.25 instead.
- This is a decision aid. Check every level against your own platform before you place an order.
