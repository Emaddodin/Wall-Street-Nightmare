"""Jeff integration: client contract + oracle fallback when the Jeff server is down."""
import json
from unittest import mock

from scalper.brain.jeff_client import JeffClient
from scalper.brain.laya_oracle import get_jeff_oracle


class _Resp:
    def __init__(self, payload):
        self._p = json.dumps(payload).encode()

    def read(self):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_client_wraps_answers():
    payload = {"model": "jeff", "latency_ms": 5, "q": {"type": "noul", "noul": 0.2}}
    with mock.patch("urllib.request.urlopen", return_value=_Resp(payload)):
        out = JeffClient().predict("state", {"q": {"type": "noul", "instructions": "x"}})
    assert out["answers"]["q"]["noul"] == 0.2


def test_client_raises_on_missing_answer():
    with mock.patch("urllib.request.urlopen", return_value=_Resp({"model": "jeff"})):
        try:
            JeffClient().predict("s", {"q": {"type": "noul", "instructions": "x"}})
        except ValueError:
            return
    raise AssertionError("expected ValueError")


def test_oracle_falls_back_without_server():
    oracle = get_jeff_oracle()
    d = oracle.evaluate_setup_sync({"direction": "BUY", "entry_price": 4000.0, "sl_price": 3998.5, "wick_ratio": 0.5})
    assert d is not None and hasattr(d, "is_valid")
