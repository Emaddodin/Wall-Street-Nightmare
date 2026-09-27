"""
scalper/brain/jeff_client.py
============================
Thin client for Jeff 1 (GestaltLabs/Jeff-1, LoRA on Qwen3-4B-Instruct-2507), the typed-decision
model that replaces Laya as the System 1 setup grader.

Jeff needs Python >=3.12 / torch >=2.11 / peft, so it runs as its own local server
(`uv run python -m scripts.jev_clf_server` in https://github.com/Gestalt-Lab/jeff, default
127.0.0.1:8079) and this client talks to it over HTTP. It exposes `predict(state, questions)`
with the same shape the oracle used with Laya: `{"answers": {qid: {...}}}`.

Every failure (server down, timeout, bad payload) raises, so the oracle falls back to its
calibrated rule engine and never blocks or vetoes on a model outage.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import Any, Dict

logger = logging.getLogger("jeff_client")

JEFF_URL = os.getenv("JEFF_URL", "http://127.0.0.1:8079").rstrip("/")
JEFF_TIMEOUT_S = float(os.getenv("JEFF_TIMEOUT_S", "1.5"))


class JeffClient:
    def __init__(self, url: str = JEFF_URL, timeout_s: float = JEFF_TIMEOUT_S):
        self.url = url.rstrip("/")
        self.timeout_s = timeout_s
        self.model_id = "unknown"

    def health(self) -> bool:
        with urllib.request.urlopen(f"{self.url}/health", timeout=3.0) as r:
            data = json.loads(r.read().decode("utf-8"))
        self.model_id = str(data.get("model", "unknown"))
        return bool(data.get("ok"))

    def predict(self, state: Any, questions: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        body = json.dumps({"state": state, "questions": questions}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.url}/v1/systemone",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
            data = json.loads(r.read().decode("utf-8"))
        answers = {qid: data[qid] for qid in questions if qid in data}
        if len(answers) != len(questions):
            raise ValueError("Jeff response missing answers")
        return {"answers": answers, "latency_ms": data.get("latency_ms")}
