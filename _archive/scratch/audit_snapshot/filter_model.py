#!/usr/bin/env python3
"""
The learned filter: score a candidate with the nightly-trained selector.

`dataset/selector.py --save-model` trains a logistic regression over every
recorded triangle signal (features at signal time -> reached +5% before
-1.25%) and saves the artifact this module loads. The book asks the model
about each candidate and refuses the ones below the floor, with the same
branch name everywhere (journal, funnel, dataset).

The artifact is a self-contained npz: weights, the training scale, the
imputation medians, and the probability at the top-10% cut -- the default
floor for the paper run. A missing or stale artifact is a refusal to
start, never a silent no-op.
"""
from __future__ import annotations

import math
import time
from pathlib import Path

import numpy as np

FEATURES = ["atr", "trend", "vol20", "mom6h", "mom1h", "volx", "agents",
            "tier", "score", "who", "counter", "side", "hour"]


class FilterModel:
    def __init__(self, path):
        d = np.load(Path(path))
        got = [str(f) for f in d["features"]]
        if got != FEATURES:
            raise ValueError(
                f"the model was trained on {got} but this engine reads "
                f"{FEATURES} -- retrain it with dataset/selector.py "
                f"--save-model")
        self.w = d["w"].astype(float)
        self.b = float(d["b"])
        self.mu = d["mu"].astype(float)
        self.sd = d["sd"].astype(float)
        self.med = d["med"].astype(float)
        self.threshold = float(d["threshold"])
        self.trained_at = int(d["trained_at"])
        self.n = int(d["n"])

    def score(self, feats: dict) -> float:
        """P(this candidate reaches +5% before -1.25%), by the model."""
        x = np.zeros(len(FEATURES) * 2)
        for j, f in enumerate(FEATURES):
            v = feats.get(f)
            if v is None or v != v:
                x[len(FEATURES) + j] = 1.0
                x[j] = self.med[j]
            else:
                x[j] = float(v)
        z = (x - self.mu) / self.sd
        logit = float(z @ self.w + self.b)
        return float(1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, logit)))))


def model_check(path) -> list[tuple[str, str]]:
    """(state, message) for the watchdog: a silently aging model is a
    silent no-op, and a missing one is a crash-looping book."""
    path = Path(path)
    if not path.exists():
        return [("fault", f"{path.name} missing -- the book cannot start "
                          f"without it")]
    try:
        trained = int(np.load(path)["trained_at"])
        hours = (time.time() - trained) / 3600.0
        return [("fault" if hours > 48 else "ok",
                 f"{path.name} trained {hours:.0f}h ago (limit 48h)")]
    except Exception as e:
        return [("fault", f"{path.name} will not read: {str(e)[:60]}")]
