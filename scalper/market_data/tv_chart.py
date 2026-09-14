"""Minimal Chrome DevTools Protocol reader for the TradingView chart tabs.

Written fresh for this project -- shares no code with the production engine,
only the underlying technique (the chart exposes TradingViewApi over CDP, and
the study's own computed series can be read from it).

The TESLA study is the operator's closed-source indicator; its computed
values (Long / Short / X Trend / mid) live on the chart.  Reading them here
is the only way the real TESLA reaches a backtest or paper book without
reimplementing it.

The two chart tabs carry the same URL, so tabs are told apart by target id
(sorted), which Chrome keeps stable for the life of a tab.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

import requests
import websocket

log = logging.getLogger("scalper.tv")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9222
CHART = "window.TradingViewApi._activeChartWidgetWV.value()"
TF_MAP = {"1m": "1", "3m": "3", "15m": "15", "1h": "60"}


class TvError(RuntimeError):
    pass


@dataclass
class TvState:
    symbol: str
    resolution: str
    bars: int

    @property
    def api_symbol(self) -> str:
        """BITUNIX:ARBUSDT.P -> ARBUSDT."""
        s = self.symbol.split(":")[-1]
        return s[:-2] if s.endswith(".P") else s


def chart_pages(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> list[dict]:
    """All TradingView chart tabs, sorted by target id (stable identity)."""
    try:
        targets = requests.get(f"http://{host}:{port}/json", timeout=10).json()
    except Exception as e:
        raise TvError(f"no debug port on {host}:{port}: {e}") from e
    pages = [t for t in targets if t.get("type") == "page"
             and "tradingview.com/chart" in (t.get("url") or "")]
    pages.sort(key=lambda t: t.get("id") or "")
    return pages


class TvChart:
    """One chart window.  Attach, evaluate, read, switch symbols/TFs."""

    def __init__(self, target_index: int = 0, host: str = DEFAULT_HOST,
                 port: int = DEFAULT_PORT, timeout: int = 30):
        self.host, self.port, self.timeout = host, port, timeout
        self.target_index = target_index
        self._ws: websocket.WebSocket | None = None
        self._id = 0

    # ------------------------------------------------------------- connect
    def _target(self) -> dict:
        pages = chart_pages(self.host, self.port)
        if not pages:
            raise TvError("no TradingView chart tab found")
        if self.target_index >= len(pages):
            raise TvError(f"chart window {self.target_index} not found "
                          f"({len(pages)} open)")
        return pages[self.target_index]

    def connect(self) -> None:
        t = self._target()
        self._ws = websocket.create_connection(
            t["webSocketDebuggerUrl"], timeout=self.timeout, suppress_origin=True)
        log.debug("attached to chart %d: %s", self.target_index, t.get("url"))

    def close(self) -> None:
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()

    def evaluate(self, expression: str, retry: bool = True):
        if self._ws is None:
            self.connect()
        self._id += 1
        msg = {"id": self._id, "method": "Runtime.evaluate",
               "params": {"expression": expression, "returnByValue": True,
                          "awaitPromise": True}}
        try:
            self._ws.send(json.dumps(msg))
            while True:
                got = json.loads(self._ws.recv())
                if got.get("id") == self._id:
                    res = got.get("result", {})
                    if "exceptionDetails" in res:
                        raise TvError(str(res["exceptionDetails"])[:400])
                    return res.get("result", {}).get("value")
        except (websocket.WebSocketException, OSError) as e:
            self.close()
            if retry:
                log.warning("CDP dropped (%s); reconnecting", e)
                return self.evaluate(expression, retry=False)
            raise TvError(f"CDP evaluate failed: {e}") from e

    # ---------------------------------------------------------------- chart
    def state(self) -> TvState:
        d = self.evaluate(f"""
        (function(){{
          var c={CHART};
          var n=0; try{{ n=c.getStudyById(c.getAllStudies()[0].id)
                          ._study.data()._items.length; }}catch(e){{}}
          return {{symbol:c.symbol(), resolution:String(c.resolution()), bars:n}};
        }})()""")
        return TvState(d["symbol"], d["resolution"], d["bars"])

    def set_symbol(self, symbol: str) -> None:
        self.evaluate(f"{CHART}.setSymbol({json.dumps(symbol)})")

    def set_resolution(self, tf: str) -> None:
        res = TF_MAP.get(tf, tf)
        self.evaluate(f"{CHART}.setResolution({json.dumps(str(res))})")

    def studies(self) -> list[dict]:
        return self.evaluate(f"{CHART}.getAllStudies()") or []

    def find_study(self, needle: str) -> str | None:
        for s in self.studies():
            if needle.lower() in (s.get("name") or "").lower():
                return s["id"]
        return None

    def study_series(self, study_id: str, limit: int | None = None) -> dict | None:
        """{plots: [names...], rows: [[time, v0, v1, ...], ...]} for a study.
        Plot slots resolved from the study's own metadata."""
        tail = (f"rows=rows.slice(-{int(limit)});" if limit else "")
        return self.evaluate(f"""
        (function(){{
          var c={CHART}; var s=c.getStudyById({json.dumps(study_id)});
          if(!s) return null;
          var src=s._study||s; var mi=src.metaInfo();
          var names=[];
          for(var i=0;i<mi.plots.length;i++){{
            var st=(mi.styles||{{}})[mi.plots[i].id]||{{}};
            names.push(st.title||st.text||mi.plots[i].id);
          }}
          var rows=src.data()._items.map(function(x){{return x.value;}});
          {tail}
          return {{symbol:c.symbol(), res:String(c.resolution()), plots:names,
                   rows: rows}};
        }})()""")

    def candle_series(self, limit: int | None = None) -> list[list] | None:
        """The chart's own candles: rows of [time, open, high, low, close, vol].
        Aligned bar-for-bar with the study rows -- the same bars TESLA was
        computed on, so recorded TESLA never drifts from its candles."""
        tail = (f"rows=rows.slice(-{int(limit)});" if limit else "")
        return self.evaluate(f"""
        (function(){{
          var c={CHART};
          var s=c.getStudyById(c.getAllStudies()[0].id);
          if(!s) return null;
          var src=s._study||s;
          var rows=src.data()._items.map(function(x){{return x.value;}});
          {tail}
          return rows;
        }})()""")

    # -------------------------------------------------------------- helpers
    def wait_ready(self, symbol: str, tf: str, timeout: float = 30.0) -> TvState:
        """After set_symbol/set_resolution: poll until the chart reports the
        asked symbol+resolution and has bars.  Raises on timeout."""
        want = TF_MAP.get(tf, tf)
        end = time.time() + timeout
        st = None
        while time.time() < end:
            try:
                st = self.state()
            except TvError:
                time.sleep(1.0)
                continue
            if st.api_symbol == symbol and str(st.resolution) == want and st.bars > 0:
                return st
            time.sleep(1.0)
        raise TvError(f"chart did not reach {symbol} {tf} in {timeout}s "
                      f"(last: {st})")

    def bars_fresh_seconds(self, tf: str) -> float | None:
        """Age in seconds of the newest bar on this chart (feed-liveness check).
        None when unreadable."""
        step = {"1": 60, "3": 180, "15": 900, "60": 3600}.get(tf, 60)
        try:
            rows = self.candle_series(limit=2)
        except TvError:
            return None
        if not rows:
            return None
        newest = rows[-1][0]
        return max(0.0, time.time() - newest - step)
