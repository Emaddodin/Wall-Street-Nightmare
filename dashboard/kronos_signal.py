"""Kronos forecast for Gold Desk: a pretrained candlestick model (github.com/shiyu-coder/Kronos, MIT)
reads the last few hundred bars of the entry timeframe (M5 by default) and samples the next ones. Gold Desk
turns the average of those samples into a plain UP / DOWN / FLAT call, draws the forecast path on the chart
and hands it to the Boom / Crash check (boom.py).

Like the Kronos BTC demo (shiyu-coder.github.io/Kronos-demo) it also draws many separate sample paths and
reports what they say together: the chance price ends higher (upside probability), the chance the coming
bars move more than the recent ones (volatility amplification), and the range the paths cover. Besides the
M5 forecast it makes a 24-hour forecast on H1 bars after every closed hour, as the demo does for BTC, and it
keeps score of how past forecasts turned out.

It runs in its own thread, so a forecast never delays a BUY / SELL click.
"""
from __future__ import annotations

import json
import math
import os
import statistics
import sys
import threading
import time
import traceback
from pathlib import Path

DEFAULT_REPO = Path(__file__).resolve().parent.parent / "Kronos"
MODELS = {   # name -> (model, tokenizer, max context)
    "mini": ("NeoQuasar/Kronos-mini", "NeoQuasar/Kronos-Tokenizer-2k", 2048),
    "small": ("NeoQuasar/Kronos-small", "NeoQuasar/Kronos-Tokenizer-base", 512),
    "base": ("NeoQuasar/Kronos-base", "NeoQuasar/Kronos-Tokenizer-base", 512),
}
HORIZON = {"M1": 15, "M5": 24}   # bars forecast ahead: 15 min on M1, 2 hours on M5
DAY = ("H1", 3600, 24)            # the demo-style forecast: H1 bars, 24 hours ahead
TRACK_FILE = Path.home() / ".golddesk" / "kronos_track.json"


def atr(rows: list, n: int = 14) -> float:
    trs = [max(r[2], p[4]) - min(r[3], p[4]) for p, r in zip(rows[-n - 1:-1], rows[-n:])]
    return sum(trs) / len(trs) if trs else 0.0


def _vol(closes: list) -> float:
    r = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]
    return statistics.pstdev(r) if len(r) > 1 else 0.0


def _q(col: list, f: float) -> float:
    s = sorted(col)
    return s[min(len(s) - 1, max(0, round(f * (len(s) - 1))))]


def distribution(rows: list, paths: list, step: int) -> dict:
    """What many sample paths say together (the Kronos demo's numbers):
    up_prob       share of paths that end above the last close
    vol_amp_prob  share of paths whose bar-to-bar volatility beats that of the last as many real bars
    band          per forecast bar: lowest / highest path and the middle half (p25 / p75)"""
    last, n, h = rows[-1][4], len(paths), len(paths[0])
    finals = [p[-1] for p in paths]
    hist = _vol([r[4] for r in rows[-(h + 1):]])
    t0 = rows[-1][0]
    band = [{"time": t0 + step * (i + 1), "lo": round(min(c), 2), "hi": round(max(c), 2),
             "p25": round(_q(c, 0.25), 2), "p75": round(_q(c, 0.75), 2)} for i, c in enumerate(zip(*paths))]
    return {"samples": n, "up_prob": round(sum(f > last for f in finals) / n, 3),
            "vol_amp_prob": round(sum(_vol([last] + p) > hist for p in paths) / n, 3),
            "band": band, "range": {"lo": round(min(finals), 2), "hi": round(max(finals), 2),
                                    "p25": round(_q(finals, 0.25), 2), "p75": round(_q(finals, 0.75), 2)}}


class Track:
    """Scores past forecasts once their last bar has closed: was the direction right, did price end inside
    the paths' range. Kept in ~/.golddesk/kronos_track.json so restarts don't reset it."""

    def __init__(self, file: Path | None = TRACK_FILE):
        self.file = file
        self.data: dict = {}
        try:
            self.data = json.loads(file.read_text())
        except (OSError, ValueError, AttributeError):
            pass

    def _tf(self, tf: str) -> dict:
        return self.data.setdefault(tf, {"pending": [], "resolved": 0, "right": 0, "inside": 0, "last": None})

    def add(self, tf: str, res: dict) -> None:
        d = self._tf(tf)
        d["pending"] = (d["pending"] + [{k: res.get(k) for k in ("t", "last", "target", "up_prob", "range", "path",
                                                                    "band")}])[-60:]
        self._save()

    def resolve(self, tf: str, rows: list) -> None:
        """rows: closed bars [t, o, h, l, c, ...] of this timeframe."""
        d = self._tf(tf)
        close = {r[0]: r[4] for r in rows}
        keep, changed = [], False
        for f in d["pending"]:
            end = f["path"][-1]["time"] if f.get("path") else None
            if end is None or end not in close:
                if end is not None and end > rows[-1][0]:
                    keep.append(f)                       # not closed yet (a gap in the data drops it)
                continue
            real, guess = close[end] - f["last"], f["target"] - f["last"]
            if real and guess:
                d["resolved"] += 1
                d["right"] += (real > 0) == (guess > 0)
                rg = f.get("range")
                d["inside"] += bool(rg and rg["lo"] <= close[end] <= rg["hi"])
            d["last"] = {**f, "actual": [{"time": p["time"], "value": close[p["time"]]}
                                         for p in f["path"] if p["time"] in close]}
            changed = True
        d["pending"] = keep
        if changed:
            self._save()

    def summary(self, tf: str) -> dict:
        d = self._tf(tf)
        n = d["resolved"]
        return {"resolved": n, "direction_right": d["right"], "inside_range": d["inside"],
                "direction_pct": round(100.0 * d["right"] / n, 1) if n else None,
                "inside_pct": round(100.0 * d["inside"] / n, 1) if n else None,
                "waiting": len(d["pending"]), "last": d["last"]}

    def _save(self) -> None:
        if not self.file:
            return
        try:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            self.file.write_text(json.dumps(self.data))
        except OSError:
            pass


class Kronos:
    """Loads the model once; `forecast` and `forecast_batch` take bars as [t, o, h, l, c, v] (UTC seconds)."""

    def __init__(self, repo: Path | str = DEFAULT_REPO, size: str = "small", lookback: int = 400,
                 horizon: int = 15, samples: int = 5, min_atr: float = 0.5, device: str | None = None):
        repo = Path(repo).expanduser()
        if not (repo / "model" / "kronos.py").exists():
            raise RuntimeError(f"Kronos code not found in {repo}. Run install_kronos.sh first.")
        sys.path.insert(0, str(repo))
        import pandas as pd
        import torch
        from model import Kronos as KModel, KronosPredictor, KronosTokenizer
        self.pd, self.torch = pd, torch
        torch.set_num_threads(max(1, (os.cpu_count() or 2) // 2))   # leave cores for the browser and server
        mname, tname, ctx = MODELS[size]
        self.size = size
        self.lookback = min(lookback, ctx)
        self.horizon, self.samples, self.min_atr = horizon, samples, min_atr
        tok = KronosTokenizer.from_pretrained(tname)
        mdl = KModel.from_pretrained(mname)
        self.pred = KronosPredictor(mdl, tok, device=device, max_context=ctx)
        self.device = self.pred.device

    def _frame(self, rows: list):
        pd = self.pd
        df = pd.DataFrame([r[1:6] for r in rows], columns=["open", "high", "low", "close", "volume"])
        ts = pd.Series(pd.to_datetime([r[0] for r in rows], unit="s"))
        return df, ts

    def _future(self, t_last: int, step: int, horizon: int = 0):
        pd = self.pd
        return pd.Series(pd.to_datetime([t_last + step * (i + 1) for i in range(horizon or self.horizon)], unit="s"))

    def _call(self, rows: list, path_close: list, step: int) -> dict:
        horizon = len(path_close)
        last = rows[-1][4]
        a = atr(rows)
        target = path_close[-1]
        move = target - last
        d = 1 if move > self.min_atr * a else (-1 if move < -self.min_atr * a else 0)
        return {"t": rows[-1][0], "last": last, "target": round(target, 2), "move": round(move, 2),
                "atr": round(a, 2), "dir": d, "call": {1: "UP", -1: "DOWN", 0: "FLAT"}[d],
                "horizon": horizon, "minutes": horizon * step // 60,
                "path": [{"time": rows[-1][0] + step * (i + 1), "value": round(v, 2)} for i, v in enumerate(path_close)]}

    def forecast(self, rows: list, step: int = 60, paths: int = 0, horizon: int = 0) -> dict:
        """The average path. With `paths` >= 2 the sample paths are drawn one by one (in one batch) and
        their spread is added: up_prob, vol_amp_prob, band, range (see `distribution`)."""
        rows = rows[-self.lookback:]
        h = horizon or self.horizon
        df, ts = self._frame(rows)
        fut = self._future(rows[-1][0], step, h)
        if paths < 2:
            out = self.pred.predict(df=df, x_timestamp=ts, y_timestamp=fut, pred_len=h, T=1.0, top_p=0.9,
                                    sample_count=self.samples, verbose=False)
            return self._call(rows, [float(x) for x in out["close"].values], step)
        outs = self.pred.predict_batch([df] * paths, [ts] * paths, [fut] * paths, pred_len=h, T=1.0, top_p=0.9,
                                       sample_count=1, verbose=False)
        ps = [[float(x) for x in o["close"].values] for o in outs]
        res = self._call(rows, [sum(c) / len(c) for c in zip(*ps)], step)
        res.update(distribution(rows, ps, step))
        return res

    def forecast_batch(self, windows: list, step: int = 60) -> list:
        dfs, xs, ys = [], [], []
        for rows in windows:
            df, ts = self._frame(rows[-self.lookback:])
            dfs.append(df); xs.append(ts); ys.append(self._future(rows[-1][0], step))
        outs = self.pred.predict_batch(dfs, xs, ys, pred_len=self.horizon, T=1.0, top_p=0.9,
                                       sample_count=self.samples, verbose=False)
        return [self._call(w[-self.lookback:], [float(x) for x in o["close"].values], step) for w, o in zip(windows, outs)]


class KronosWorker:
    """Loads the model in the background, then re-forecasts after every closed entry bar, off the order path."""

    def __init__(self, hub, paths: int = 10, day_paths: int = 30, **kw):
        self.hub, self.kw = hub, kw
        self.paths, self.day_paths = paths, day_paths
        self.model: Kronos | None = None
        self.latest: dict | None = None
        self.day: dict | None = None
        self.track = Track()
        self.error: str | None = None
        self.status = "loading model"
        threading.Thread(target=self._loop, daemon=True).start()

    def _rows(self, tf: str) -> list:
        with self.hub.lock:
            b = self.hub.src.rates(tf, self.model.lookback + 1)
        return [[b.t[i], b.o[i], b.h[i], b.l[i], b.c[i], b.v[i]] for i in range(len(b) - 1)]  # closed bars only

    def _day(self, done: int) -> int:
        """The 24-hour H1 forecast, once per closed hour (after the M5 one, which Boom / Crash waits on)."""
        tf, sec, h = DAY
        rows = self._rows(tf)
        if len(rows) < 100 or rows[-1][0] == done:
            return done
        self.track.resolve(tf, rows)
        self.status = "forecasting the next 24 h"
        t0 = time.time()
        res = self.model.forecast(rows, step=sec, paths=self.day_paths, horizon=h)
        res.update(seconds=round(time.time() - t0, 1), model=f"Kronos-{self.model.size}", tf=tf)
        self.day = res
        self.track.add(tf, res)
        return rows[-1][0]

    def _loop(self) -> None:
        try:
            self.model = Kronos(**self.kw)
        except Exception as e:
            self.error, self.status = f"Kronos did not load: {e}", "off"
            traceback.print_exc()
            return
        self.status = "ready"
        done_t = done_day = 0
        while True:
            try:
                eng = self.hub.engine
                last_closed = eng.m1.t[-1] if eng and len(eng.m1) else 0
                if last_closed and last_closed != done_t:
                    rows = self._rows(self.hub.tf)
                    if len(rows) >= 100:
                        self.track.resolve(self.hub.tf, rows)
                        self.status = "forecasting"
                        t0 = time.time()
                        res = self.model.forecast(rows, step=self.hub.sec, paths=self.paths)
                        res["seconds"] = round(time.time() - t0, 1)
                        res["model"] = f"Kronos-{self.model.size}"
                        res["tf"] = self.hub.tf
                        self.latest, self.error = res, None
                        self.track.add(self.hub.tf, res)
                        self.hub.on_kronos(res)
                    done_t = last_closed
                    if self.day_paths:
                        done_day = self._day(done_day)
                    self.status = "ready"
            except Exception as e:
                self.error, self.status = f"Kronos: {e}", "ready"
                traceback.print_exc()
                time.sleep(10)
            time.sleep(1)

    def state(self) -> dict:
        return {**(self.latest or {}), "status": self.status, "error": self.error,
                "backtest": backtest_summary(self.hub.tf), "day": self.day,
                "track": {self.hub.tf: self.track.summary(self.hub.tf), DAY[0]: self.track.summary(DAY[0])}}


def backtest_file(tf: str = "M5") -> Path:
    """Where the launchers write the gold backtest for this entry timeframe."""
    return Path.home() / ".golddesk" / ("kronos_backtest.txt" if tf == "M1" else f"kronos_backtest_{tf}.txt")


_bt_cache: dict = {}


def backtest_summary(tf: str = "M5") -> dict | None:
    """The last gold backtest the launcher ran: {status: running|done, lines: [...result lines]}."""
    f = backtest_file(tf)
    try:
        st = f.stat()
    except OSError:
        return None
    if _bt_cache.get("mtime") != (f, st.st_mtime):
        text = f.read_text(errors="replace")
        keep = [ln.strip() for ln in text.splitlines()
                if ln.startswith(("Direction right", "Trades", "Boom/Crash", "Verdict"))]
        prog = text.replace("\r", "\n").strip().splitlines()
        _bt_cache.update(mtime=(f, st.st_mtime), value={
            "status": "done" if any(ln.startswith("Verdict") for ln in keep) else "running",
            "lines": keep, "progress": prog[-1].strip() if prog and not keep else None})
    return _bt_cache["value"]
