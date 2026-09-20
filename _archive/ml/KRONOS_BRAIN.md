# Kronos → this bot (what was adopted and how)

Source: https://github.com/shiyu-coder/Kronos
"Kronos: A Foundation Model for the Language of Financial Markets" — the
first open-source decoder-only foundation model trained on OHLCV K-lines
from 45+ exchanges. Paper: https://arxiv.org/abs/2508.02739

## What Kronos is

Two stages:

1. **Tokenizer** — quantizes continuous multi-dimensional K-line data
   (OHLCV) into *hierarchical discrete tokens* (the "alphabet" of price
   action).
2. **Autoregressive Transformer** — pre-trained on those tokens to forecast
   the next K-lines *probabilistically* (`T`, `top_p`, `sample_count` give a
   distribution of future paths, not a single point).

Sizes: Kronos-mini 4.1M (context 2048) → small 24.7M → base 102.3M →
large 499.2M.

## The three ideas worth stealing

| Kronos idea | What it maps to here |
|---|---|
| **Probabilistic forward forecast** of OHLCV (not just a classifier) | `RiskEngine.forecast_bias()` — a forward direction read (+/-) with a confidence |
| **Direction + uncertainty** (sample many paths, use the spread) | the `fconf` term: low agreement = chop = don't fight it |
| **Discrete tokenized price "language"** | the bot's existing discrete feature set (`filter_model.FEATURES`) + this gate's EMA-slope / persistence / momentum signals |

## What was added (live now)

`forecast_bias(coin) -> (direction, confidence)` in `live_hyperliquid.py`
— a **Kronos-lite** forward read from the asset's own K-lines, combining
three agreeing signals:

- EMA slope (fast vs slow)
- directional persistence (fraction of last N closes up/down)
- momentum in ATR units

It returns a signed `raw` in [-3,+3]; `direction` = sign(raw), and
`confidence` = |raw|/3. `on_signal` refuses a setup only when the forecast
is **strongly counter** (|raw| >= `fc_counter_floor`, default 2.0 → conf
>= 0.67) to the trade side. Short history → (0, 0.0) → allow (never a
silent no-op).

Config knobs: `forecast_gate_enabled`, `fc_ema_fast`, `fc_ema_slow`,
`fc_persist_bars`, `fc_mom_bars`, `fc_counter_floor`.

## Running the REAL Kronos (optional, off by default)

The full model needs `torch` + a GPU for the large sizes. Kronos-mini
(4.1M params) runs on CPU. The hot path is 30 coins × 15m, so a real
model belongs behind a cache/batch, not inline. Integration point:

```python
from kronos_brain import kronos_forecast   # returns (dir, conf) or None
# None  -> the lightweight forecast_bias above stays in charge
# value -> use it as the entry gate instead
```

To enable it later: `pip install torch`, download
`NeoQuasar/Kronos-Tokenizer-base` + `NeoQuasar/Kronos-mini` from Hugging
Face, and fill in `kronos_brain.py`. Until then the lightweight gate is
the "brain" the book actually runs.
