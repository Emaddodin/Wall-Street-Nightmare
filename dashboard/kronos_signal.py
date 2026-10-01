"""Kronos forecast for Gold Desk: a pretrained candlestick model (github.com/shiyu-coder/Kronos, MIT)
reads the last few hundred bars of the entry timeframe (M5 by default) and samples the next ones. Gold Desk
turns the average of those samples into a plain UP / DOWN / FLAT call, draws the forecast path on the chart
and hands it to the Boom / Crash check (boom.py).

It runs in its own thread, so a forecast never delays a BUY / SELL click.
"""
from __future__ import annotations

import os
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


def atr(rows: list, n: int = 14) -> float:
    trs = [max(r[2], p[4]) - min(r[3], p[4]) for p, r in zip(rows[-n - 1:-1], rows[-n:])]
    return sum(trs) / len(trs) if trs else 0.0


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

    def _future(self, t_last: int, step: int):
        pd = self.pd
        return pd.Series(pd.to_datetime([t_last + step * (i + 1) for i in range(self.horizon)], unit="s"))

    def _call(self, rows: list, path_close: list, step: int) -> dict:
        last = rows[-1][4]
        a = atr(rows)
        target = path_close[-1]
        move = target - last
        d = 1 if move > self.min_atr * a else (-1 if move < -self.min_atr * a else 0)
        return {"t": rows[-1][0], "last": last, "target": round(target, 2), "move": round(move, 2),
                "atr": round(a, 2), "dir": d, "call": {1: "UP", -1: "DOWN", 0: "FLAT"}[d],
                "horizon": self.horizon, "minutes": self.horizon * step // 60,
                "path": [{"time": rows[-1][0] + step * (i + 1), "value": round(v, 2)} for i, v in enumerate(path_close)]}

    def forecast(self, rows: list, step: int = 60) -> dict:
        rows = rows[-self.lookback:]
        df, ts = self._frame(rows)
        out = self.pred.predict(df=df, x_timestamp=ts, y_timestamp=self._future(rows[-1][0], step),
                                pred_len=self.horizon, T=1.0, top_p=0.9, sample_count=self.samples, verbose=False)
        return self._call(rows, [float(x) for x in out["close"].values], step)

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

    def __init__(self, hub, **kw):
        self.hub, self.kw = hub, kw
        self.model: Kronos | None = None
        self.latest: dict | None = None
        self.error: str | None = None
        self.status = "loading model"
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        try:
            self.model = Kronos(**self.kw)
        except Exception as e:
            self.error, self.status = f"Kronos did not load: {e}", "off"
            traceback.print_exc()
            return
        self.status = "ready"
        done_t = 0
        while True:
            try:
                eng = self.hub.engine
                last_closed = eng.m1.t[-1] if eng and len(eng.m1) else 0
                if last_closed and last_closed != done_t:
                    with self.hub.lock:
                        b = self.hub.src.rates(self.hub.tf, self.model.lookback + 1)
                    rows = [[b.t[i], b.o[i], b.h[i], b.l[i], b.c[i], b.v[i]] for i in range(len(b) - 1)]  # closed bars only
                    if len(rows) >= 100:
                        self.status = "forecasting"
                        t0 = time.time()
                        res = self.model.forecast(rows, step=self.hub.sec)
                        res["seconds"] = round(time.time() - t0, 1)
                        res["model"] = f"Kronos-{self.model.size}"
                        res["tf"] = self.hub.tf
                        self.latest, self.error = res, None
                        self.hub.on_kronos(res)
                    done_t = last_closed
                    self.status = "ready"
            except Exception as e:
                self.error, self.status = f"Kronos: {e}", "ready"
                traceback.print_exc()
                time.sleep(10)
            time.sleep(1)

    def state(self) -> dict:
        return {**(self.latest or {}), "status": self.status, "error": self.error,
                "backtest": backtest_summary(self.hub.tf)}


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
