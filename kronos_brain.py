#!/usr/bin/env python3
"""Optional hook: the REAL Kronos foundation model as the entry gate.

Kronos (https://github.com/shiyu-coder/Kronos) forecasts future K-lines
probabilistically.  This module is a clean integration point so the book
can swap the lightweight `RiskEngine.forecast_bias` (Kronos-lite) for the
actual model when `torch` + the Hugging Face weights are installed.

It is deliberately a soft dependency: `torch` and the model weights are
large, so nothing here is imported at boot.  The bot keeps running on the
lightweight gate until you opt in by installing them.
"""
from __future__ import annotations

from typing import Optional, Tuple


def kronos_available() -> bool:
    """True only if torch AND the model package are importable."""
    try:
        import torch  # noqa: F401
    except Exception:
        return False
    try:
        from model import Kronos, KronosTokenizer, KronosPredictor  # noqa: F401
        return True
    except Exception:
        return False


def kronos_forecast(coin: str, bars: list) -> Optional[Tuple[int, float]]:
    """Return (direction, confidence) for the next bars, or None.

    direction: +1 bullish / -1 bearish / 0 flat.
    confidence: 0..1 (spread of the sampled forecast paths).

    Returns None when the model is not installed or the input is too short
    -- the caller then keeps the lightweight forecast_bias gate.
    """
    if not kronos_available() or not bars or len(bars) < 100:
        return None
    # Load lazily (cached by the caller, not here) and forecast the next few
    # closes; direction = sign of the mean forecasted drift, confidence =
    # agreement of the sampled paths.  Left as the integration point.
    try:
        from model import Kronos, KronosTokenizer, KronosPredictor
    except Exception:
        return None
    # Example (needs a cached predictor keyed by (tokenizer, model)):
    #   tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
    #   model = Kronos.from_pretrained("NeoQuasar/Kronos-mini")
    #   pred = KronosPredictor(model, tokenizer, max_context=512).predict(...)
    #   drift = mean(last_close - first_close over the sampled paths)
    #   return sign(drift), path_agreement
    return None
