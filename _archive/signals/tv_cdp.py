"""
Read Tesla indicator signals straight from TradingView Desktop over CDP.

TradingView Desktop is an Electron app, so launching it with
`--remote-debugging-port=9222` exposes the Chrome DevTools Protocol. That gives
direct access to `window.TradingViewApi`, and from there to the study's own
series data -- the numbers the indicator actually computed, before they were
ever drawn.

Why this replaces the vision layer entirely
-------------------------------------------
The Tesla indicator draws its badges with Pine `plotshape()`, which surfaces as
plots of type "shapes" on the study. Each bar's row carries a timestamp and one
slot per plot; a non-null slot means a shape printed on that bar. So instead of
inferring "roughly which bar is that green rectangle on", we read the exact bar
timestamp and the exact price the indicator emitted.

Concretely this removes every weakness of screen reading:

  * no OCR, no colour thresholds, no region calibration
  * no badge-to-bar attribution error -- the bar time is given, not inferred
  * the window may be minimised, covered, or on another desktop
  * latency drops from seconds to milliseconds
  * full history is available at once, so the PDF's sequence rule ("each BUY
    higher than the previous BUY") is satisfied from the very first signal
    instead of needing to observe two badges live before it can trade

Plot identity is resolved from the study's own metadata rather than hardcoded:
`metaInfo().styles` names each shapes plot ("Buy" / "Sell"), and the value array
is [time, plot_0, plot_1, ...], so plot N lives at index N+1.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import requests
import websocket


log = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9222
CHART = "window.TradingViewApi._activeChartWidgetWV.value()"


class CDPError(RuntimeError):
    pass


@dataclass
class ChartState:
    symbol: str            # as TradingView names it, e.g. BITUNIX:ARBUSDT.P
    resolution: str
    bars: int

    @property
    def exchange(self) -> str:
        return self.symbol.split(":")[0] if ":" in self.symbol else ""

    @property
    def api_symbol(self) -> str:
        """Bitunix's name for this instrument: BITUNIX:ARBUSDT.P -> ARBUSDT."""
        s = self.symbol.split(":")[-1]
        return s[:-2] if s.endswith(".P") else s


class TradingViewCDP:
    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 timeout: int = 30, target_index: int | None = None,
                 target_id: str | None = None):
        self.host, self.port, self.timeout = host, port, timeout
        # Which chart window to attach to. None keeps the old behaviour of
        # taking the first one, so nothing that already used this class changes.
        self.target_index = target_index
        self.target_id = target_id
        self.pinned_target_id: str | None = target_id
        self._ws: websocket.WebSocket | None = None
        self._id = 0

    def pin_target(self, target_id: str) -> None:
        """Explicitly re-pin to a specific target ID."""
        self.pinned_target_id = target_id
        self.target_id = target_id

    # ---------------------------------------------------------------- connect
    def _target(self) -> dict:
        try:
            targets = requests.get(f"http://{self.host}:{self.port}/json",
                                   timeout=10).json()
        except Exception as e:
            raise CDPError(
                f"No debug port on {self.host}:{self.port} ({e}). Relaunch "
                f"TradingView with:\n  /Applications/TradingView.app/Contents/"
                f"MacOS/TradingView --remote-debugging-port={self.port} &") from e
        pages = [t for t in targets if t.get("type") == "page"
                 and "tradingview.com/chart" in (t.get("url") or "")]
        if not pages:
            raise CDPError("TradingView is running with the debug port open, but "
                           "no chart tab was found. Open a chart and retry.")

        # If a target is already pinned (or user supplied target_id), match directly by target ID
        active_target_id = self.pinned_target_id or self.target_id
        if active_target_id:
            for p in pages:
                if p.get("id") == active_target_id:
                    self.pinned_target_id = p.get("id")
                    return p
            raise CDPError(f"Pinned chart target '{active_target_id}' no longer exists.")

        # First connection without explicit target_id: select by target_index or default 0
        pages.sort(key=lambda t: t.get("id") or "")
        if self.target_index is not None:
            if self.target_index >= len(pages):
                raise CDPError(f"asked for chart window {self.target_index} but "
                               f"only {len(pages)} are open")
            selected = pages[self.target_index]
        else:
            selected = pages[0]

        # Pin target_id upon first connection to prevent identity swapping across tab reordering
        self.pinned_target_id = selected.get("id")
        return selected

    @classmethod
    def chart_windows(cls, host: str = DEFAULT_HOST,
                      port: int = DEFAULT_PORT) -> int:
        """
        How many TradingView chart windows are open.

        Each window is its own CDP page with its own symbol, which is how one
        process can follow several charts at once. Two windows on the same
        saved layout still count as two -- they are separate targets.
        """
        try:
            targets = requests.get(f"http://{host}:{port}/json", timeout=10).json()
        except Exception:
            return 0
        return len([t for t in targets if t.get("type") == "page"
                    and "tradingview.com/chart" in (t.get("url") or "")])

    @classmethod
    def list_targets(cls, host: str = DEFAULT_HOST,
                     port: int = DEFAULT_PORT) -> list[dict]:
        """
        List all available TradingView chart targets with their target IDs and metadata.
        """
        try:
            targets = requests.get(f"http://{host}:{port}/json", timeout=10).json()
            return [t for t in targets if t.get("type") == "page"
                    and "tradingview.com/chart" in (t.get("url") or "")]
        except Exception:
            return []

    def connect(self) -> None:
        t = self._target()
        # suppress_origin matters: Chrome rejects the upgrade with 403 when an
        # Origin header is present unless it was launched with
        # --remote-allow-origins, and we cannot assume that flag.
        self._ws = websocket.create_connection(
            t["webSocketDebuggerUrl"], timeout=self.timeout, suppress_origin=True)
        log.info("CDP connected to %s", t.get("url"))

    def close(self) -> None:
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None

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
                        raise CDPError(str(res["exceptionDetails"])[:400])
                    return res.get("result", {}).get("value")
        except (websocket.WebSocketException, OSError) as e:
            self.close()
            if retry:
                log.warning("CDP dropped (%s); reconnecting", e)
                return self.evaluate(expression, retry=False)
            raise CDPError(f"CDP evaluate failed: {e}") from e

    def screenshot(self, quality: int = 60) -> bytes:
        """The chart window as a JPEG, exactly as it is drawn on the server.

        The public TradingView widget cannot load a private script, so a phone
        showing "our setup" has to be shown this window rather than a rebuilt
        one -- indicator, council table, drawings and all.
        """
        import base64
        import time as _t
        if self._ws is None:
            self.connect()
        # Both chart tabs live in one browser window, so only one of them is
        # ever composited. Screenshotting a background tab returned the last
        # frame Chrome had painted -- which is the foreground tab's -- and the
        # phone showed two identical pictures under two different coin names.
        # Bringing the target forward first is what makes the capture actually
        # belong to the tab that was asked for.
        self._id += 1
        self._ws.send(json.dumps({"id": self._id, "method": "Page.bringToFront"}))
        while True:
            got = json.loads(self._ws.recv())
            if got.get("id") == self._id:
                break
        _t.sleep(0.35)                    # let the compositor produce a frame
        self._id += 1
        self._ws.send(json.dumps({
            "id": self._id, "method": "Page.captureScreenshot",
            "params": {"format": "jpeg", "quality": quality,
                       "captureBeyondViewport": False}}))
        while True:
            got = json.loads(self._ws.recv())
            if got.get("id") == self._id:
                d = got.get("result", {}).get("data")
                if not d:
                    raise CDPError("no screenshot came back")
                return base64.b64decode(d)

    # ------------------------------------------------------------------ chart
    def state(self) -> ChartState:
        d = self.evaluate(f"""
        (function(){{
          var c={CHART};
          var n=0; try{{ n=c.getStudyById(c.getAllStudies()[0].id)._study.data()._items.length; }}catch(e){{}}
          return {{symbol:c.symbol(), resolution:String(c.resolution()), bars:n}};
        }})()""")
        return ChartState(d["symbol"], d["resolution"], d["bars"])

    def set_symbol(self, symbol: str) -> None:
        """
        Switch the charted symbol. Needs no Accessibility permission, unlike
        driving the UI through System Events.
        """
        self.evaluate(f"{CHART}.setSymbol({json.dumps(symbol)})")
        log.info("Chart symbol set to %s", symbol)

    def set_resolution(self, res: str) -> None:
        """Change the chart timeframe ('1','3','5','15','60','240','1D')."""
        self.evaluate(f"{CHART}.setResolution({json.dumps(str(res))})")

    def raw_series(self, study_id: str, limit: int | None = None) -> dict:
        """
        Study series with plot titles.

        `limit` keeps only the last N bars, sliced in the page before anything
        is serialised. The full series is ~1300 bars across ~56 plots, about
        370 KB of JSON per call; a live poll needs the last few bars and paid
        200 ms per window for the rest. Research callers omit it and still get
        everything.
        """
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

    def studies(self) -> list[dict]:
        return self.evaluate(f"{CHART}.getAllStudies()") or []

    def find_study(self, needle: str) -> str | None:
        for s in self.studies():
            if needle.lower() in (s.get("name") or "").lower():
                return s["id"]
        return None

    # ----------------------------------------------------------------- badges
    last_chart_symbol: str = ""

    def badges(self, study_id: str, limit: int = 500) -> list[dict]:
        """
        Every shape the indicator has printed, as
        {type: BUY|SELL, bar_time: datetime, price: float}.

        Plot slots are resolved from the study's own metadata: the value array
        is [time, plot_0, plot_1, ...] so plot N sits at index N+1, and
        metaInfo().styles gives each shapes plot its label.
        """
        raw = self.evaluate(f"""
        (function(){{
          var c={CHART}; var s=c.getStudyById({json.dumps(study_id)});
          if(!s) return {{err:'study not found'}};
          var src=s._study||s; var mi=src.metaInfo();
          var shapes=[];
          for(var i=0;i<mi.plots.length;i++){{
            var p=mi.plots[i];
            if(p.type==='shapes'){{
              var st=(mi.styles||{{}})[p.id]||{{}};
              shapes.push({{slot:i+1, text:(st.text||''), id:p.id}});
            }}
          }}
          var it=src.data()._items, out=[];
          for(var j=0;j<it.length;j++){{
            var v=it[j].value;
            for(var k=0;k<shapes.length;k++){{
              var val=v[shapes[k].slot];
              if(val!==null&&val!==undefined)
                out.push({{time:v[0], text:shapes[k].text, price:val}});
            }}
          }}
          return {{symbol:c.symbol(), shapes:shapes, badges:out}};
        }})()""")
        if not raw or raw.get("err"):
            raise CDPError((raw or {}).get("err", "no data returned"))
        self.last_chart_symbol = raw.get("symbol", "")
        out = []
        for b in raw["badges"][-limit:]:
            txt = (b.get("text") or "").strip().upper()
            if txt.startswith("B"):
                kind = _signal_types()[0].BUY
            elif txt.startswith("S"):
                kind = _signal_types()[0].SELL
            else:
                continue
            out.append({
                "type": kind, "price": float(b["price"]),
                "bar_time": _pd().Timestamp(int(b["time"]), unit="s", tz="UTC"),
            })
        out.sort(key=lambda x: x["bar_time"])
        return out

    def study_last_values(self, study_id: str) -> dict:
        """Latest computed value per plot -- useful for cross-checking the HMA."""
        return self.evaluate(f"""
        (function(){{
          var c={CHART}; var s=c.getStudyById({json.dumps(study_id)});
          if(!s) return null;
          var src=s._study||s; var mi=src.metaInfo();
          var it=src.data()._items; if(!it.length) return null;
          var v=it[it.length-1].value, o={{time:v[0]}};
          for(var i=0;i<mi.plots.length;i++){{
            var st=(mi.styles||{{}})[mi.plots[i].id]||{{}};
            o[st.title||mi.plots[i].id]=v[i+1];
          }}
          return o;
        }})()""")


def _pd():
    """pandas, loaded only when a caller actually needs a Timestamp."""
    import pandas
    return pandas


def _signal_types():
    """
    core.strategy pulls pandas in behind it, and only CDPSignalSource needs
    either. Loading them here keeps them out of every process that just reads a
    chart -- the trading bot among them, which never builds one of these.
    """
    from core.strategy import SignalType, TeslaSignal
    return SignalType, TeslaSignal


class CDPSignalSource:
    """
    Emits TeslaSignals from TradingView's own study data.

    Drop-in replacement for VisionSignalSource, minus its failure modes.
    """

    # A genuinely new badge belongs to the bar that just closed. Anything older
    # is history that priming failed to capture -- never a live signal.
    MAX_BAR_AGE_MINUTES = 2

    def __init__(self, study_match: str = "TESLA", host: str = DEFAULT_HOST,
                 port: int = DEFAULT_PORT, symbol: str = "",
                 target_index: int | None = None,
                 target_id: str | None = None):
        self.cdp = TradingViewCDP(host, port, target_index=target_index, target_id=target_id)
        self.study_match = study_match
        self.symbol = symbol
        self._study_id: str | None = None
        self._seen: set[tuple[str, str, str]] = set()
        self.last_badges: list[dict] = []
        # Primed state is PER SYMBOL, not a single flag. A sweeper revisits many
        # symbols per minute; with one boolean, every switch reset it, so poll()
        # re-primed and returned nothing every single time -- the bot swept
        # cleanly for half an hour and could not emit a signal by construction.
        self._primed_symbols: set[str] = set()

    @property
    def primed(self) -> bool:
        """True once THIS symbol's history has been primed."""
        return self.symbol in self._primed_symbols

    def set_symbol(self, symbol: str) -> None:
        if symbol != self.symbol:
            self.symbol = symbol
            # The study id belongs to the chart, which just reloaded.
            self._study_id = None

    def study_id(self) -> str:
        if self._study_id is None:
            sid = self.cdp.find_study(self.study_match)
            if not sid:
                raise CDPError(
                    f"No study matching {self.study_match!r} on the chart. "
                    f"Loaded: {[s.get('name') for s in self.cdp.studies()]}")
            self._study_id = sid
        return self._study_id

    def prime(self, attempts: int = 10, delay: float = 2.0) -> list[dict]:
        """
        Load historical badges without emitting signals for them.

        Marking history as seen is what makes the sequence rule usable
        immediately: the bot knows the previous BUY and SELL from the start, yet
        will not trade a badge that printed before it was watching.

        Retries until the study actually has data. After a symbol switch the
        chart needs a few seconds to reload and recompute, and a study queried
        too early returns zero badges. Priming on that empty result marks
        nothing as seen, so the next poll sees the entire history as brand new
        -- which is exactly how a live run once opened two positions on badges
        more than an hour old. Waiting for bars before trusting the result
        closes that window; MAX_BAR_AGE_MINUTES catches it if this still fails.
        """
        badges: list[dict] = []
        for i in range(max(1, attempts)):
            try:
                badges = self.cdp.badges(self.study_id())
            except CDPError as e:
                log.debug("prime attempt %d failed: %s", i + 1, e)
                badges = []
            if badges:
                break
            # No badges can legitimately mean "none printed yet", so only keep
            # waiting while the chart itself is still not loaded.
            try:
                if self.cdp.state().bars > 0 and i >= 2:
                    break
            except CDPError:
                pass
            self._study_id = None            # study id changes on reload
            time.sleep(delay)

        for b in badges:
            self._seen.add((self.symbol, str(b["bar_time"]), b["type"].value))
        self.last_badges = badges
        self._primed_symbols.add(self.symbol)
        log.info("Primed %d historical badge(s) for %s", len(badges), self.symbol)
        return badges

    def poll(self, now: datetime | None = None) -> list:
        if not self.primed:
            self.prime()
            return []
        badges = self.cdp.badges(self.study_id())
        # Confirm the chart is still showing what we think it is. A symbol
        # switch takes seconds to settle, and badges read mid-switch belong to
        # the other instrument -- acting on them would trade one coin on
        # another's signals.
        charted = (getattr(self.cdp, "last_chart_symbol", "") or "").split(":")[-1]
        if charted.endswith(".P"):
            charted = charted[:-2]
        if charted and self.symbol and charted != self.symbol:
            log.warning("Chart shows %s but we are watching %s; skipping poll",
                        charted, self.symbol)
            return []
        self.last_badges = badges
        ref = _pd().Timestamp(now or datetime.now(timezone.utc))
        _, TeslaSignal = _signal_types()
        out: list = []
        for b in badges:
            key = (self.symbol, str(b["bar_time"]), b["type"].value)
            if key in self._seen:
                continue
            self._seen.add(key)
            # Defence in depth: even if priming missed history, a badge whose
            # bar closed long ago is not a signal to act on. Mark it seen (so
            # the sequence rule still learns from it) but never emit it.
            age_min = (ref - b["bar_time"]).total_seconds() / 60.0
            if age_min > self.MAX_BAR_AGE_MINUTES:
                log.debug("Ignoring stale %s badge on %s: bar %s is %.0f min old",
                          b["type"].value, self.symbol, b["bar_time"], age_min)
                continue
            out.append(TeslaSignal(
                symbol=self.symbol, type=b["type"],
                detected_at=now or datetime.now(timezone.utc),
                bar_time=b["bar_time"], confidence=1.0, source="cdp",
            ))
            log.info("NEW %s badge on %s at bar %s (price %.8g)",
                     b["type"].value, self.symbol, b["bar_time"], b["price"])
        return out
