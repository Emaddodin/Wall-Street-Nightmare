# Gold Desk

One page on your own PC for gold scalping: your broker's live M1 chart with the signals drawn on it, the H1 / M15 / M5 bias, the session clock, lot size from your balance, Buy / Sell / Close buttons, alerts, and a backtest tab on your MT5 history.

Every price comes from the MetaTrader 5 terminal running on the same PC, so the chart matches your broker exactly. Orders go out through that terminal, and only when you click.

## Start it

You need Windows, MetaTrader 5 open and logged in, and Python 3.9 or newer from python.org (tick "Add python.exe to PATH" when installing).

1. In MT5, turn on the **Algo Trading** button in the toolbar. Without it, MT5 refuses orders from the page and the page tells you so.
2. Double-click `start_dashboard.bat`. The first run installs the `MetaTrader5` Python package.
3. Your browser opens `http://127.0.0.1:8765`. Keep the black window open while you trade; closing it stops the page.

To look around first without MT5, double-click `start_demo.bat`: synthetic prices, paper fills.

Options (add them after `start_dashboard.bat` in a terminal, or after `server.py`):

| Option | Use |
|---|---|
| `--symbol XAUUSDm` | Pick the symbol if auto-detect chooses the wrong one |
| `--max-lots 0.5` | Largest order the page will send (default 1.0) |
| `--terminal "C:\...\terminal64.exe"` | Pick one MT5 install when you have several |
| `--utc-offset 3` | Your broker's server clock if session times look wrong |

## On a Mac: LiteFinance

No MT5 needed. Gold Desk opens the LiteFinance web terminal in its own Chromium window and works through it: candles come from the same history feed the LiteFinance chart uses, bid and ask are read from its order ticket, and Buy / Sell fill that ticket (side, lots, stop loss, take profit) and press its button.

```
python3 -m pip install playwright && python3 -m playwright install chromium
python3 server.py --litefinance-login        # once: log in in the window that opens; it saves by itself
python3 server.py --litefinance --account demo
```

- `--account demo` or `--account real` sets the badge. Without it the badge says "Demo or real? Check".
- `--lf-dry-run` fills the LiteFinance ticket but never presses its button. Use it to watch what an order would do.
- `--lf-headless` hides the LiteFinance window. Leave it visible for now: open positions, closing and moving stops are still done in that window.
- Your login is saved in `~/.golddesk/lf_session.json`, readable only by your Mac user. Gold Desk never sees your password.

## Using the page

- **Header**: account badge (red **Real money**, green **Demo account**), bid, ask, spread, balance, server with ping, and the UTC session clock.
- **Chart**: M1 / M5 / M15 / H1 tabs. Arrows print only after an M1 candle closes and never move. Exit circles show each past signal's result in R. Shaded boxes are live M5 fair value gaps (dotted) and order blocks (dashed). The active signal's entry, SL, TP1 and TP2 and your open positions are drawn as lines.
- **Order ticket**: SELL and BUY buttons show the price you'll get. Under each button is the exact plan: stop, target, lots and dollar risk. With a signal on the chart it uses the signal's levels; without one it uses a 1.5 ATR stop. Lots come from your risk % and your broker's tick value. You can switch to fixed lots or manual levels.
- **Confirm step**: on by default. Untick "Ask me to confirm each order" for one-click trading.
- **Positions**: every open gold position on the account, with **BE** (stop to entry), **½** (close half) and **Close**. **Close all** is next to the ticket.
- **Alerts**: press "Turn on sound + popups" once. A **Radar** alert fires while the candle is still open, when all three higher timeframes agree and price touches a zone. An **Execute** alert fires on the candle close with the full levels.
- **Backtest tab**: pick how many days, risk %, start balance and mode, then run. It replays your terminal's M1 history through the same engine and shows trades, win rate, profit factor, max drawdown, net P/L and R, the equity curve, and every trade. If it returns fewer bars than you asked for, raise *Max bars in chart* in MT5 under Tools > Options > Charts.

## Speed

A click reaches MT5 in a few milliseconds, because the page and the terminal are on the same PC. After that, the time to a fill is your terminal's connection to the broker's server, the same as clicking inside MT5. The ticket shows both numbers after each order, and the header shows the terminal's ping. A VPS near your broker's server is the only way to cut that part.

## Safety

- The server only listens on `127.0.0.1`, so nothing else on your network can reach it. Orders also need a secret key that only the page has, so other websites open in your browser can't send them.
- Nothing trades on its own. Signals and alerts never place orders.
- Orders above the lot cap, stops on the wrong side of the price, and lots below your broker's minimum are refused before they reach MT5.
- Try it on a demo account first. The badge in the header always shows which kind of account is connected.

## Files

| File | What it is |
|---|---|
| `server.py` | Local web server and the MT5 connection |
| `engine.py` | Signal rules and backtest (same rules as `mt5/XAU_M1_Scalper.mq5`) |
| `static/` | The page, plus TradingView Lightweight Charts 4.2.3 (Apache 2.0) |
| `start_dashboard.bat`, `start_demo.bat` | Double-click starters |
