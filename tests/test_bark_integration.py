"""
tests/test_bark_integration.py
==============================
Unit tests for Bark push notification integration and fallback logic.
"""

import asyncio
import json
from unittest.mock import MagicMock, patch

from bark_integration import (
    get_bark_keys,
    get_bark_server,
    push_bark,
    push_bark_async,
    send_alert,
    send_alert_async,
)


def test_bark_keys_from_env(monkeypatch):
    monkeypatch.setenv("BARK_KEY", "key1,key2, key3 ")
    keys = get_bark_keys()
    assert keys == ["key1", "key2", "key3"]


def test_bark_server_custom(monkeypatch):
    monkeypatch.setenv("BARK_SERVER", "https://custom.bark.host/")
    assert get_bark_server() == "https://custom.bark.host"


def test_push_bark_no_keys(monkeypatch):
    monkeypatch.delenv("BARK_KEY", raising=False)
    monkeypatch.delenv("BARK_DEVICE_KEY", raising=False)
    with patch("bark_integration._read_env_value", return_value=""):
        res = push_bark("Test Title", "Test Message")
        assert res is False


def test_push_bark_success(monkeypatch):
    monkeypatch.setenv("BARK_KEY", "mock_device_key_123")
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({"code": 200, "message": "success"}).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        res = push_bark("Order Filled", "Bought 0.10 lots XAUUSD", priority="high")
        assert res is True
        assert mock_urlopen.called
        req = mock_urlopen.call_args[0][0]
        data = json.loads(req.data.decode("utf-8"))
        assert data["title"] == "Order Filled"
        assert data["body"] == "Bought 0.10 lots XAUUSD"
        assert data["device_key"] == "mock_device_key_123"
        assert data["group"] == "Stratton Oakmont"
        assert "icon-180.png" in data["icon"]


def test_push_bark_async(monkeypatch):
    monkeypatch.setenv("BARK_KEY", "mock_key_abc")
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({"code": 200, "message": "success"}).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        res = asyncio.run(push_bark_async("Async Title", "Async Body"))
        assert res is True


def test_send_alert_fallback(monkeypatch):
    monkeypatch.delenv("BARK_KEY", raising=False)
    monkeypatch.delenv("BARK_DEVICE_KEY", raising=False)
    with patch("bark_integration._read_env_value", return_value=""):
        with patch("bark_integration._push_ntfy_fallback", return_value=True) as mock_ntfy:
            res = send_alert("Alert Title", "Alert Body", priority="high")
            assert res is True
            assert mock_ntfy.called
