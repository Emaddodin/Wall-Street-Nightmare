"""Kronos forecast for Gold Desk: a pretrained candlestick model (github.com/shiyu-coder/Kronos, MIT)
reads the last few hundred bars of the entry timeframe (M5 by default) and samples the next ones. Gold Desk
turns the average of those samples into a plain UP / DOWN / FLAT call, draws the forecast path on the chart
and hands it to the Boom / Crash check (boom.py).

Like the Kronos BTC demo (shiyu-coder.github.io/Kronos-demo) it also draws many separate sample paths and
reports what they say together: the chance price ends higher (upside probability), the chance the coming
bars move more than the recent ones (volatility amplification), and the range the paths cover. Besides the
M5 forecast it makes a 24-hour forecast on H1 bars after every closed hour, as the demo does for BTC, and it
keeps score of how past forecasts turned out.

The 30-minute forecast (`state.kronos.m30`) is for entries on M1 and M5: after every closed M1 bar Kronos
samples the next 30 M1 bars, after every closed M5 bar the next 6 M5 bars, and the two are blended into one
per-minute path for the next 30 minutes. Each source is scored live once its 30 minutes are over, and
kronos_calib.py shrinks its probability and move towards what it actually achieved on gold (towards 50% and
no move while it has not shown skill). The blend weighs the sources by that measured skill, leaning to M1.
Calibration measures Kronos; it does not give it an edge it doesn't have. If kronos_finetune.py has saved a
gold fine-tuned model in ~/.golddesk/kronos_ft_M1 (or _M5), that timeframe's 30-minute forecast uses it.

It runs in its own threads, so a forecast never delays a BUY / SELL click.
"""
from __future__ import annotations

import copy
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
M30 = {"M1": (60, 30), "M5": (300, 6)}   # the 30-minute forecast: timeframe -> (seconds, bars ahead)
M30_PRIOR = {"M1": 1.5, "M5": 1.0}       # blend prior: you enter on M1, so M1 counts a bit more at equal skill
M30_EDGE = 0.04                          # UP / DOWN only when the calibrated chance is at least 54% / at most 46%
M5_MAX_AGE = 600                         # an M5 30-minute forecast older than 10 min is left out of the blend
FT_WEIGHTS = ("model.safetensors", "pytorch_model.bin")
FT_META = "golddesk_ft.json"             # written by kronos_finetune.py next to the saved model


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


def make_call(rows: list, path_close: list, step: int, min_atr: float = 0.5) -> dict:
    """The plain call from an average path: UP / DOWN when it ends more than `min_atr` x ATR(14) away."""
    horizon = len(path_close)
    last = rows[-1][4]
    a = atr(rows)
    target = path_close[-1]
    move = target - last
    d = 1 if move > min_atr * a else (-1 if move < -min_atr * a else 0)
    return {"t": rows[-1][0], "last": last, "target": round(target, 2), "move": round(move, 2),
            "atr": round(a, 2), "dir": d, "call": {1: "UP", -1: "DOWN", 0: "FLAT"}[d],
            "horizon": horizon, "minutes": horizon * step // 60,
            "path": [{"time": rows[-1][0] + step * (i + 1), "value": round(v, 2)} for i, v in enumerate(path_close)]}


def from_paths(rows: list, paths: list, step: int, min_atr: float = 0.5) -> dict:
    """Call from the mean of sample paths plus their spread (`distribution`)."""
    res = make_call(rows, [sum(c) / len(c) for c in zip(*paths)], step, min_atr)
    res.update(distribution(rows, paths, step))
    return res


def is_ft_dir(p) -> bool:
    """A folder holding a model saved by kronos_finetune.py (save_pretrained: config + weights)."""
    try:
        p = Path(p).expanduser()
        return (p / "config.json").is_file() and any((p / w).is_file() for w in FT_WEIGHTS)
    except (TypeError, OSError):
        return False


def ft_path(tf: str, ft_dir=None) -> Path | None:
    """Which fine-tuned model the 30-minute forecast of `tf` uses, if any:
    ft_dir={"M1": path, ...} first, then $GOLDDESK_KRONOS_FT_<TF> ("off" = pretrained), then
    ~/.golddesk/kronos_ft_<TF>. ft_dir=False always means the pretrained model."""
    if ft_dir is False:
        return None
    cand = []
    if isinstance(ft_dir, dict) and ft_dir.get(tf):
        cand.append(ft_dir[tf])
    env = os.environ.get(f"GOLDDESK_KRONOS_FT_{tf}")
    if env:
        if env.strip().lower() in ("off", "0", "none", "pretrained"):
            return None
        cand.append(env)
    cand.append(Path.home() / ".golddesk" / f"kronos_ft_{tf}")
    for c in cand:
        if is_ft_dir(c):
            return Path(c).expanduser()
    return None


def ft_meta(p) -> dict:
    try:
        d = json.loads((Path(p).expanduser() / FT_META).read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


class Track:
    """Scores past forecasts once their last bar has closed: was the direction right, did price end inside
    the paths' range. Kept in ~/.golddesk/kronos_track.json so restarts don't reset it."""

    KEYS = ("t", "last", "target", "up_prob", "range", "path", "band")

    def __init__(self, file: Path | None = TRACK_FILE):
        self.file = file
        self.data: dict = {}
        self.lock = threading.RLock()        # the entry / H1 loop and the 30-minute loop share it
        try:
            self.data = json.loads(file.read_text())
        except (OSError, ValueError, AttributeError):
            pass

    def _tf(self, tf: str) -> dict:
        return self.data.setdefault(tf, {"pending": [], "resolved": 0, "right": 0, "inside": 0, "last": None})

    def add(self, tf: str, res: dict, keys: tuple | None = None) -> None:
        with self.lock:
            d = self._tf(tf)
            d["pending"] = (d["pending"] + [{k: res.get(k) for k in (keys or self.KEYS)}])[-60:]
            self._save()

    def resolve(self, tf: str, rows: list) -> list:
        """rows: closed bars [t, o, h, l, c, ...] of this timeframe. Returns the forecasts resolved now as
        (forecast, real move from its last close), for the calibrator."""
        if not rows:
            return []
        out = []
        with self.lock:
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
                out.append((f, real))
                changed = True
            d["pending"] = keep
            if changed:
                self._save()
        return out

    def summary(self, tf: str) -> dict:
        with self.lock:
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
            with self.lock:
                text = json.dumps(self.data)
            self.file.parent.mkdir(parents=True, exist_ok=True)
            self.file.write_text(text)
        except OSError:
            pass


class Kronos:
    """Loads the model once; `forecast` and `forecast_batch` take bars as [t, o, h, l, c, v] (UTC seconds).
    `ft_dir`: a folder saved by kronos_finetune.py; its weights replace the pretrained ones (same tokenizer)."""

    def __init__(self, repo: Path | str = DEFAULT_REPO, size: str = "small", lookback: int = 400,
                 horizon: int = 15, samples: int = 5, min_atr: float = 0.5, device: str | None = None,
                 ft_dir: Path | str | None = None):
        repo = Path(repo).expanduser()
        if not (repo / "model" / "kronos.py").exists():
            raise RuntimeError(f"Kronos code not found in {repo}. Run install_kronos.sh first.")
        if str(repo) not in sys.path:
            sys.path.insert(0, str(repo))
        import pandas as pd
        import torch
        from model import Kronos as KModel, KronosPredictor, KronosTokenizer
        self.pd, self.torch = pd, torch
        self._KModel, self._KPredictor, self._KTokenizer = KModel, KronosPredictor, KronosTokenizer
        torch.set_num_threads(max(1, (os.cpu_count() or 2) // 2))   # leave cores for the browser and server
        self.size = size
        self.horizon, self.samples, self.min_atr = horizon, samples, min_atr
        self._lookback_req, self._device_req = lookback, device
        self._toks: dict = {}
        self.ft = None
        self.name = f"Kronos-{size}"
        self._load(size, Path(ft_dir).expanduser() if ft_dir and is_ft_dir(ft_dir) else None, device)
        if ft_dir and self.ft is None:
            raise RuntimeError(f"No fine-tuned Kronos model in {ft_dir} (needs config.json and model weights).")

    def _tokenizer(self, name: str):
        if name not in self._toks:
            self._toks[name] = self._KTokenizer.from_pretrained(name)
        return self._toks[name]

    def _load(self, size: str, ft: Path | None, device) -> None:
        meta = ft_meta(ft) if ft else {}
        size = meta.get("size") if meta.get("size") in MODELS else size
        mname, tname, ctx = MODELS[size]
        tname = meta.get("tokenizer") or tname
        tok = self._tokenizer(tname)
        mdl = self._KModel.from_pretrained(str(ft) if ft else mname)
        self.size, self.ctx = size, ctx
        self.lookback = min(self._lookback_req, ctx)
        self.pred = self._KPredictor(mdl, tok, device=device, max_context=ctx)
        self.device = self.pred.device
        self.ft = ft
        self.name = f"Kronos-{size}" + (f" fine-tuned on gold {meta.get('tf', '')}".rstrip() if ft else "")

    def variant(self, ft_dir: Path | str) -> "Kronos":
        """The same setup with the weights of a fine-tuned model (shares the tokenizer and torch)."""
        v = copy.copy(self)
        v._load(self.size, Path(ft_dir).expanduser(), self.device)
        return v

    def _frame(self, rows: list):
        pd = self.pd
        df = pd.DataFrame([r[1:6] for r in rows], columns=["open", "high", "low", "close", "volume"])
        ts = pd.Series(pd.to_datetime([r[0] for r in rows], unit="s"))
        return df, ts

    def _future(self, t_last: int, step: int, horizon: int = 0):
        pd = self.pd
        return pd.Series(pd.to_datetime([t_last + step * (i + 1) for i in range(horizon or self.horizon)], unit="s"))

    def _call(self, rows: list, path_close: list, step: int) -> dict:
        return make_call(rows, path_close, step, self.min_atr)

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
        return from_paths(rows, ps, step, self.min_atr)

    def forecast_batch(self, windows: list, step: int = 60) -> list:
        dfs, xs, ys = [], [], []
        for rows in windows:
            df, ts = self._frame(rows[-self.lookback:])
            dfs.append(df); xs.append(ts); ys.append(self._future(rows[-1][0], step))
        outs = self.pred.predict_batch(dfs, xs, ys, pred_len=self.horizon, T=1.0, top_p=0.9,
                                       sample_count=self.samples, verbose=False)
        return [self._call(w[-self.lookback:], [float(x) for x in o["close"].values], step) for w, o in zip(windows, outs)]

    def forecast_paths_batch(self, windows: list, step: int = 60, paths: int = 16) -> list:
        """Like `forecast(..., paths=paths)` for many windows in one batch (len(windows) * paths series)."""
        dfs, xs, ys = [], [], []
        for rows in windows:
            df, ts = self._frame(rows[-self.lookback:])
            fut = self._future(rows[-1][0], step)
            dfs += [df] * paths; xs += [ts] * paths; ys += [fut] * paths
        outs = self.pred.predict_batch(dfs, xs, ys, pred_len=self.horizon, T=1.0, top_p=0.9,
                                       sample_count=1, verbose=False)
        ps = [[float(x) for x in o["close"].values] for o in outs]
        return [from_paths(w[-self.lookback:], ps[j * paths:(j + 1) * paths], step, self.min_atr)
                for j, w in enumerate(windows)]


# ---------------------------------------------------------------------------------------------------------------
# The 30-minute blend (pure: no model, no torch; KronosWorker feeds it)

def _interp(pts: list, x: float) -> dict:
    """pts: [(instant, {field: value})] sorted by instant; linear in between, flat beyond the ends."""
    if x <= pts[0][0]:
        return pts[0][1]
    if x >= pts[-1][0]:
        return pts[-1][1]
    for (x0, a), (x1, b) in zip(pts, pts[1:]):
        if x0 <= x <= x1:
            f = (x - x0) / float(x1 - x0) if x1 > x0 else 1.0
            return {k: a[k] + f * (b[k] - a[k]) for k in a}
    return pts[-1][1]


def _curve(res: dict, step: int) -> list:
    """A forecast as (instant, price fields): bar time T in a path is the bar *opening* at T, so its close is the
    price at T + step. The first point is the last real close."""
    last = res["last"]
    pts = [(res["t"] + step, {"v": last, "lo": last, "hi": last, "p25": last, "p75": last})]
    band = res.get("band") or []
    for i, p in enumerate(res["path"]):
        b = band[i] if i < len(band) else {}
        v = p["value"]
        pts.append((p["time"] + step, {"v": v, "lo": b.get("lo", v), "hi": b.get("hi", v),
                                       "p25": b.get("p25", v), "p75": b.get("p75", v)}))
    return pts


def _cal_short(c: dict | None) -> dict | None:
    if not c:
        return None
    return {k: c.get(k) for k in ("n", "n_live", "n_prior", "skill", "brier_skill", "hit_rate", "n_dir",
                                  "coin_band", "beats_coin", "a", "b", "k", "bias")}


def blend30(m1: dict | None, m5: dict | None, calib=None, t: int | None = None, last: float | None = None,
            prior: dict | None = None, edge: float = M30_EDGE, minutes: int = 30) -> dict | None:
    """One 30-minute forecast from the M1 one (30 x M1) and the M5 one (6 x M5).

    t / last: the last closed M1 bar (its opening time and close); default: the M1 forecast's.
    Each source's path is re-anchored at that close (its predicted change from now on), the M5 path interpolated
    per minute, and the two averaged with weights ∝ prior x max(0.05, live skill). An M5 forecast older than
    M5_MAX_AGE seconds is left out. `calib` (a kronos_calib.CalibSet, or None for raw numbers) turns the raw
    upside probability and move into calibrated ones: per source, then the blend's own calibrator takes over as
    it gathers samples. Returns None when there is nothing to blend."""
    prior = prior or M30_PRIOR
    srcs: dict = {}
    if m1 and m1.get("path"):
        srcs["M1"] = (m1, 60)
    if m5 and m5.get("path"):
        srcs["M5"] = (m5, 300)
    if not srcs:
        return None
    if t is None:
        t = m1["t"] if "M1" in srcs else m5["t"] + 300 - 60
    if last is None:
        last = m1["last"] if "M1" in srcs else m5["last"]
    now_i = t + 60                                   # the instant of the last close
    if "M1" in srcs and now_i - (m1["t"] + 60) > 120:
        srcs.pop("M1")                               # an M1 forecast from more than two bars ago
    if "M5" in srcs and now_i - (m5["t"] + 300) > M5_MAX_AGE:
        srcs.pop("M5")
    if not srcs:
        return None

    def cal(kind: str, name: str, x):
        if calib is None:
            return (0.5 if x is None else x) if kind == "p" else float(x or 0.0)
        return calib.prob(name, x) if kind == "p" else calib.move(name, x)

    skills = {s: (calib.skill(f"{s}x30") if calib is not None else 0.0) for s in srcs}
    w = {s: v for s, v in _weights(skills, prior).items()}
    grid = [now_i + 60 * (k + 1) for k in range(minutes)]
    offs = {}
    sources = {}
    for s, (res, step) in srcs.items():
        pts = _curve(res, step)
        base = _interp(pts, now_i)["v"]
        offs[s] = [{k: v - base for k, v in _interp(pts, x).items()} for x in grid]
        up_raw = res.get("up_prob")
        mv_raw = res.get("move", res["path"][-1]["value"] - res["last"])
        sources[s] = {"t": res["t"], "last": round(res["last"], 2), "bars": len(res["path"]), "step": step,
                      "up_prob_raw": up_raw, "up_prob": round(cal("p", f"{s}x30", up_raw), 3),
                      "move_raw": round(mv_raw, 2), "move": round(cal("m", f"{s}x30", mv_raw), 2),
                      "samples": res.get("samples"), "atr": res.get("atr"), "seconds": res.get("seconds"),
                      "model": res.get("model"), "fine_tuned": res.get("fine_tuned"),
                      "age_s": now_i - (res["t"] + step), "weight": round(w[s], 3),
                      "skill": round(skills[s], 4)}

    path, band = [], []
    for k, x in enumerate(grid):
        f = {fld: last + sum(w[s] * offs[s][k][fld] for s in srcs) for fld in ("v", "lo", "hi", "p25", "p75")}
        lo, p25, p75, hi = sorted((f["lo"], f["p25"], f["p75"], f["hi"]))
        tm = x - 60                                      # back to the bar-opening convention of the chart
        path.append({"time": tm, "value": round(f["v"], 2)})
        band.append({"time": tm, "lo": round(min(lo, f["v"]), 2), "hi": round(max(hi, f["v"]), 2),
                     "p25": round(p25, 2), "p75": round(p75, 2)})

    ups = [sources[s]["up_prob_raw"] for s in srcs]
    up_raw = (sum(w[s] * (0.5 if sources[s]["up_prob_raw"] is None else sources[s]["up_prob_raw"]) for s in srcs)
              if any(u is not None for u in ups) else None)
    move_raw = path[-1]["value"] - last
    src_p = sum(w[s] * sources[s]["up_prob"] for s in srcs)
    src_m = sum(w[s] * sources[s]["move"] for s in srcs)
    n_blend = calib.n("M30") if calib is not None else 0.0
    alpha = n_blend / (n_blend + 100.0)
    up = alpha * cal("p", "M30", up_raw) + (1 - alpha) * src_p if calib is not None else (
        0.5 if up_raw is None else up_raw)
    move = alpha * cal("m", "M30", move_raw) + (1 - alpha) * src_m if calib is not None else move_raw
    d = 1 if (up >= 0.5 + edge and move > 0) else (-1 if (up <= 0.5 - edge and move < 0) else 0)
    signs = {(sources[s]["move_raw"] > 0) - (sources[s]["move_raw"] < 0) for s in srcs}
    agree = len(srcs) < 2 or len(signs) == 1
    conf = min(1.0, 2 * abs(up - 0.5)) * (1.0 if agree else 0.6)
    secs = [sources[s]["seconds"] for s in srcs if sources[s]["seconds"] is not None]
    summ = calib.summary() if calib is not None else {}
    return {"t": t, "last": round(last, 2), "minutes": minutes, "path": path, "band": band,
            "target": path[-1]["value"], "up_prob_raw": None if up_raw is None else round(up_raw, 3),
            "up_prob": round(up, 3), "move_raw": round(move_raw, 2), "move": round(move, 2),
            "dir": d, "call": {1: "UP", -1: "DOWN", 0: "FLAT"}[d], "confidence": round(conf, 3),
            "agree": agree, "sources": sources, "weights": {s: round(v, 3) for s, v in w.items()},
            "calibration": {"blend_share": round(alpha, 3),
                            **{n: _cal_short(summ.get(n)) for n in ("M1x30", "M5x30", "M30")},
                            "note": "measured on resolved live forecasts; shrinks Kronos to what it achieved, "
                                    "adds no edge"},
            "model": ", ".join(f"{s}: {sources[s]['model']}" for s in srcs),
            "seconds": round(max(secs), 1) if secs else None}


def _weights(skills: dict, prior: dict) -> dict:
    from kronos_calib import blend_weights
    return blend_weights(skills, prior)


class KronosWorker:
    """Loads the model in the background, then re-forecasts after every closed entry bar, off the order path.
    A second thread makes the 30-minute forecast after every closed M1 / M5 bar (`m30`)."""

    def __init__(self, hub, paths: int = 10, day_paths: int = 30, m1_paths: int = 16, m5_paths: int = 12,
                 ft_dir=None, calib=None, m30_prior: dict | None = None, model=None, track: Track | None = None,
                 autostart: bool = True, **kw):
        self.hub, self.kw = hub, kw
        self.paths, self.day_paths = paths, day_paths
        self.m1_paths, self.m5_paths = m1_paths, m5_paths
        self.ft_dir = ft_dir                     # None: auto (env / ~/.golddesk/kronos_ft_<TF>), dict, or False
        self.m30_prior = m30_prior or dict(M30_PRIOR)
        self.model: Kronos | None = model
        self.latest: dict | None = None
        self.day: dict | None = None
        self.m30: dict | None = None
        self.f30: dict = {"M1": None, "M5": None}         # the latest 30-minute forecast of each source
        self.models30: dict = {}
        self.track = track if track is not None else Track()
        self.calib = calib
        self.error: str | None = None
        self.error30: str | None = None
        self.status = "loading model"
        self.status30 = "waiting for the model"
        self._busy_main = threading.Event()               # the entry / H1 forecast runs: the 30-minute one waits
        self._done30 = {"M1": 0, "M5": 0}
        self._m30_tracked = None
        self._min_done = self._try_min = None
        self._last_try = self._first_try = 0.0
        if autostart:
            threading.Thread(target=self._loop, daemon=True).start()

    def _rows(self, tf: str, n: int | None = None) -> list:
        with self.hub.lock:
            b = self.hub.src.rates(tf, (n or self.model.lookback) + 1)
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
        if self.model is None:
            try:
                self.model = Kronos(**self.kw)
            except Exception as e:
                self.error, self.status = f"Kronos did not load: {e}", "off"
                self.status30 = "off"
                traceback.print_exc()
                return
        self.status = "ready"
        if self.m1_paths or self.m5_paths:
            threading.Thread(target=self._loop30, daemon=True).start()
        done_t = done_day = 0
        while True:
            try:
                eng = self.hub.engine
                last_closed = eng.m1.t[-1] if eng and len(eng.m1) else 0
                if last_closed and last_closed != done_t:
                    self._busy_main.set()
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
                self._busy_main.clear()
                self.error, self.status = f"Kronos: {e}", "ready"
                traceback.print_exc()
                time.sleep(10)
            self._busy_main.clear()
            time.sleep(1)

    # -- the 30-minute forecast ------------------------------------------------------------------------------
    def _model30(self, tf: str):
        """The fine-tuned model for this timeframe when kronos_finetune.py saved one, else the pretrained one."""
        if tf not in self.models30:
            m, p = self.model, ft_path(tf, self.ft_dir)
            if p is not None and hasattr(self.model, "variant"):
                try:
                    m = self.model.variant(p)
                    print(f"Kronos 30-minute {tf}: using the fine-tuned model in {p}")
                except Exception as e:
                    self.error30 = f"Fine-tuned Kronos {tf} in {p} did not load ({e}); using the pretrained one"
                    traceback.print_exc()
            self.models30[tf] = m
        return self.models30[tf]

    def _model_name(self, m) -> str:
        return getattr(m, "name", None) or f"Kronos-{getattr(m, 'size', '?')}"

    def _wait_main(self, limit: float = 90.0) -> None:
        t_end = time.time() + limit
        while self._busy_main.is_set() and time.time() < t_end:
            time.sleep(0.2)

    def _forecast30(self, tf: str, rows: list) -> dict:
        sec, h = M30[tf]
        m = self._model30(tf)
        paths = self.m1_paths if tf == "M1" else self.m5_paths
        self.status30 = f"forecasting 30 min on {tf}"
        t0 = time.time()
        res = m.forecast(rows, step=sec, paths=paths, horizon=h)
        res.update(seconds=round(time.time() - t0, 1), model=self._model_name(m), tf=tf, paths=paths,
                   fine_tuned=bool(getattr(m, "ft", None)))
        if tf == "M1" and res["seconds"] > 40 and self.m1_paths > 4:     # must fit in a minute with room to spare
            self.m1_paths = max(4, int(self.m1_paths * 0.75))
        return res

    def step30(self) -> bool:
        """One pass: score what resolved, forecast M1 (new closed M1 bar) and M5 (new closed M5 bar), blend.
        Always the latest bars: a pass that is still running when the next bar closes skips it, nothing queues.
        Returns True when something new was forecast."""
        if self.calib is None:
            from kronos_calib import CalibSet
            self.calib = CalibSet()
        new = False
        if self.m1_paths:
            m = self._model30("M1")
            self._wait_main()                      # the entry / H1 forecast first (Boom / Crash waits on it)
            rows1 = self._rows("M1", m.lookback)
            if len(rows1) >= 100 and rows1[-1][0] != self._done30["M1"]:
                self._learn("M1x30", rows1)
                self._learn("M30", rows1)
                res = self._forecast30("M1", rows1)
                self.f30["M1"] = res
                self.track.add("M1x30", res, keys=("t", "last", "target", "up_prob", "range", "path"))
                self._done30["M1"] = rows1[-1][0]
                self._emit()
                new = True
        if self.m5_paths:
            m = self._model30("M5")
            self._wait_main()
            rows5 = self._rows("M5", m.lookback)
            if len(rows5) >= 100 and rows5[-1][0] != self._done30["M5"]:
                self._learn("M5x30", rows5)
                res = self._forecast30("M5", rows5)
                self.f30["M5"] = res
                self.track.add("M5x30", res, keys=("t", "last", "target", "up_prob", "range", "path"))
                self._done30["M5"] = rows5[-1][0]
                self._emit()
                new = True
        if new and self.m30 and self.m30["t"] != self._m30_tracked:
            self._m30_tracked = self.m30["t"]          # one scored blend per M1 bar (the M5 pass re-blends it)
            self.track.add("M30", {**self.m30, "target": self.m30["path"][-1]["value"],
                                   "up_prob": self.m30["up_prob_raw"]},
                           keys=("t", "last", "target", "up_prob", "path"))
        self.status30 = "ready"
        return new

    def _learn(self, key: str, rows: list) -> None:
        """Forecasts of `key` whose 30 minutes are over go to the calibrator (raw up_prob and move vs real)."""
        learned = False
        for f, real in self.track.resolve(key, rows):
            if f.get("last") is None or f.get("target") is None:
                continue
            self.calib.add(key, f.get("up_prob"), f["target"] - f["last"], real, f.get("t"), save=False)
            learned = True
        if learned:
            self.calib.save()

    def _emit(self) -> None:
        m1, m5 = self.f30["M1"], self.f30["M5"]
        t = last = None
        if m1 is not None:
            t, last = m1["t"], m1["last"]
        elif m5 is not None:
            t, last = m5["t"] + 300 - 60, m5["last"]
        res = blend30(m1, m5, self.calib, t, last, self.m30_prior)
        if res is None:
            return
        self.m30, self.error30 = res, None
        cb = getattr(self.hub, "on_kronos30", None)
        if cb:
            try:
                cb(res)
            except Exception:
                traceback.print_exc()

    def _tick30(self, now: float) -> None:
        """Try once per wall-clock minute (every 3 s for up to 40 s while the new bar hasn't arrived)."""
        minute = int(now) // 60
        if minute == self._min_done or now - self._last_try < 3:
            return
        if self._try_min != minute:
            self._try_min, self._first_try = minute, now
        self._last_try = now
        if self.step30() or now - self._first_try > 40:
            self._min_done = minute

    def _loop30(self) -> None:
        self.status30 = "ready"
        while True:
            try:
                self._tick30(time.time())
            except Exception as e:
                self.error30, self.status30 = f"Kronos 30 min: {e}", "ready"
                traceback.print_exc()
                time.sleep(10)
            time.sleep(1)

    def state(self) -> dict:
        tr = {self.hub.tf: self.track.summary(self.hub.tf), DAY[0]: self.track.summary(DAY[0])}
        if self.m1_paths or self.m5_paths:
            tr.update({k: self.track.summary(k) for k in ("M1x30", "M5x30", "M30")})
        return {**(self.latest or {}), "status": self.status, "error": self.error,
                "backtest": backtest_summary(self.hub.tf), "day": self.day, "m30": self.m30,
                "m30_status": self.status30, "m30_error": self.error30,
                "m30_models": {tf: {"model": self._model_name(m), "fine_tuned": str(m.ft) if getattr(m, "ft", None)
                                    else None} for tf, m in list(self.models30.items())},
                "track": tr}


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
