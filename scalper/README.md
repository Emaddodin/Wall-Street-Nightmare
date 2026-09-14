# Scalper — crypto intraday confluence bot (isolated research/paper project)

A separate, self-contained project. It shares **no code** with the
production Stratton Oakmont engine next door — no imports, no shared files — only the
concept of reading the operator's TESLA indicator off the same TradingView
charts the production bot used (the charts are shared *infrastructure*, the
code is not).

## The setup (user architecture)

```
LONG  =  15m Bullish regime   (EMA50>EMA200, slope>0, price>EMA200, HH/HL)
       + TESLA(15m) Long      (the operator's indicator, read off the charts)
       + 3m Liquidity Sweep   (candle dips below the swing low, closes back above)
       + 1m BOS               (close above the most recent confirmed 1m swing high)
       + Volume confirmation  (bar volume > rolling average)

SHORT = mirror image.
```

All five legs must be true at the same moment; the entry fills at the NEXT
1m bar's open with fees + slippage.  Risk: stop below the swept swing (long)
with an ATR buffer; model B take-profit (50% @1R, 25% @2R, 25% trailing
runner, stop to breakeven after TP1); position size = equity × risk ÷ stop
distance — leverage is an execution ceiling, never a sizing input.

## TESLA sources

| source | where it comes from | use |
|---|---|---|
| `recorded` | harvested from the real charts via CDP (`data/tesla/`) | real backtests |
| `live` | chart walker keeps the last closed bar per symbol | paper trading |
| `proxy`  | labelled 15m MACD stand-in (`config tesla.source: proxy`) | engine validation only |

`tesla.source: auto` = recorded when it exists, else proxy.  Every trade and
rejection record carries the source that produced it.

## Layout

```
config/            ALL strategy parameters (default.yaml + aggressive.yaml)
market_data/       Bitunix public REST client, parquet candle store, CDP chart reader
universe_selector/ volume/liquidity/volatility/trend/spread ranking (top N)
indicators/        EMA, Wilder ATR/ADX, session VWAP, confirmed swings
market_regime/     15m bull/bear/neutral + ADX gate
structure/         BOS (break of structure) helpers
liquidity/         3m sweep detection
setup_detector/    sweep freshness tracking
entry_engine/      1m confluence gate + quality score (spec section 9)
risk_manager/      dynamic sizing + daily loss/streak/cooldown/budget controls
position_manager/  lots, TP models A/B/C, breakeven, swing trailing
execution/         fees + slippage on every fill
engine.py          the shared core: causal per-TF preparation + global loop
backtester/        (engine.run) -- lookahead-free by construction
paper_trader/      the SAME engine on live candles (paper fills)
research/          observe-only candidate + outcome dataset
metrics/ logger/   full metric set + JSONL event logs
robustness/        parameter perturbation, slippage/fee stress, Monte Carlo
app/               standalone HTTPS phone app (own port, own auth)
tools/             download.py, backtest.py, walkforward.py, research.py
tests/             pytest: indicators, no-lookahead, risk, determinism
```

## Commands (all from this directory)

```bash
# historical data (130 days 1m, top-30 by volume)
python3 tools/download.py --days 130 --top 30 --workers 6

# backtest
python3 tools/backtest.py --days 130 --profile aggressive
python3 tools/backtest.py --days 130 --profile aggressive --compare-models

# walk-forward (train 60 / val 20 / test 20, rolled)
python3 tools/walkforward.py --profile aggressive

# research dataset (hypothetical signals + future outcomes)
python3 tools/research.py --days 130 --profile aggressive

# tests
python3 -m pytest tests/ -q

# paper trading (VPS; TESLA live from the charts)
python3 paper_trade.py --profile aggressive

# app
SCALPER_APP_TOKEN=... python3 app/app.py
```

## Honesty notes

* Backtest fills are **never** perfect: every leg pays taker fee + adverse
  slippage; if a bar touches both stop and target, the stop wins.
* No future bars: 3m/15m state consumed at a 1m close is only CLOSED-bar
  state; entries fill at the next open.
* The universe rebalances daily from trailing 24h data only (volume, 1m
  range-median spread proxy, 15m ATR/trend).
* Proxy-TESLA numbers are engine validation, not TESLA numbers.  The real
  judgement comes from recorded-TESLA backtests and live paper.
