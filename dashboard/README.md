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

## Kronos forecasts (optional)

[Kronos](https://github.com/shiyu-coder/Kronos) is a pretrained candlestick model (MIT licence). Gold Desk can run it in the background: after each closed 1-minute candle it forecasts the next 15 minutes, draws that path as a yellow dashed line and shows UP / DOWN / FLAT. It never places orders and never slows a click.

```
bash install_kronos.sh                                   # once: Kronos code + PyTorch
python3 kronos_backtest.py --litefinance-days 20         # does it beat a coin flip on gold, after the spread?
python3 server.py --litefinance --account demo --kronos  # show it on the page
```

The backtest prints how often the forecast got the direction right, the coin-flip range for that many tries, and the profit after the 0.22 spread per 0.01 lot. Treat the forecast as a curiosity unless that test says otherwise.

## Using the page

- **Top bar**: DEMO / REAL MONEY badge, a green dot while prices are streaming, balance and equity.
- **Chart**: 1m / 5m / 15m / 1h. The last candle moves with every price. Signal arrows print only after a candle closes.
- **SELL / BUY**: one click sends the order straight away, no confirm box. The box under them says what happened and how long it took.
- **Lots**: type it, use − / +, or tap 0.01 / 0.05 / 0.10 / 0.50 / 1.00.
- **Stop loss / Take profit**: optional prices. Leave empty for none. "Use levels" copies the current signal's levels in.
- **CLOSE ALL** and each trade's **Close** (MT5 and practice mode). On LiteFinance the page lists your open trades as LiteFinance prints them; close them in the LiteFinance window for now.
- The backtest still runs from `/api/backtest?days=30`.

## Speed

A click reaches MT5 in a few milliseconds, because the page and the terminal are on the same PC. After that, the time to a fill is your terminal's connection to the broker's server, the same as clicking inside MT5. The ticket shows both numbers after each order, and the header shows the terminal's ping. A VPS near your broker's server is the only way to cut that part.

## Safety

- The server only listens on `127.0.0.1`, so nothing else on your network can reach it. Orders also need a secret key that only the page has, so other websites open in your browser can't send them.
- Nothing trades on its own. Signals never place orders; only your BUY / SELL / Close clicks do.
- Orders above the lot cap, stops on the wrong side of the price, and lots below your broker's minimum are refused before they reach MT5.
- Try it on a demo account first. The badge in the header always shows which kind of account is connected.

## Files

| File | What it is |
|---|---|
| `server.py` | Local web server and the MT5 connection |
| `engine.py` | Signal rules and backtest (same rules as `mt5/XAU_M1_Scalper.mq5`) |
| `static/` | The page, plus TradingView Lightweight Charts 4.2.3 (Apache 2.0) |
| `start_dashboard.bat`, `start_demo.bat` | Double-click starters |
