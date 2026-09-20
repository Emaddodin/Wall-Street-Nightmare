import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(Path(__file__).resolve().parent)):
    if p not in sys.path:
        sys.path.insert(0, p)


@pytest.fixture(autouse=True)
def _no_real_credentials(monkeypatch):
    """No test may hold a key that could move real money.

    `papertrade` calls load_dotenv() at import, so merely importing it puts
    BITUNIX_API_KEY and BITUNIX_API_SECRET into this process's environment --
    and BitunixClient() falls back to them whenever it is constructed without
    explicit arguments. A test that reached the network with those loaded
    would be signing real requests against the real account. Two layers stop
    that: the credentials are removed, and the socket is closed below.
    """
    for k in ("BITUNIX_API_KEY", "BITUNIX_API_SECRET", "NTFY_TOPIC",
              "NTFY_TOPIC_SHARED", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
              "PANEL_NOTIFY_SECRET", "ALERT_PROXY"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Nothing in this suite may reach the outside world.

    A test that quietly falls through to the real exchange is worse than no
    test: it passes for the wrong reason, it is slow, it is non-deterministic,
    and on the private endpoints it would be trading. One of these tests did
    exactly that -- an unknown symbol triggered a live instrument-list fetch --
    which is why this is enforced rather than trusted.
    """
    def refuse(*a, **kw):
        raise RuntimeError(
            "a test tried to open a network connection; every external call "
            "must be faked")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
