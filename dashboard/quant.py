"""Gold Desk's quant model, live: the next 30 minutes of gold from the trees quant_train.py fitted, plus the
London / overlap / New York session forecasts (quant_sessions.py, after Solomon Eshun's Quantitative-XAUUSD-
Strategy, MIT). Pure Python (no numpy), cached per closed M1 candle.

    from quant import QuantModel, blend_with_kronos
    qm = QuantModel()                                   # ~/.golddesk/quant_model.json (+ quant_sessions.json)
    q = qm.predict(bars, utc, silver=silver_bars, dxy=None, kronos30=kronos_m30)
    both = blend_with_kronos(q, kronos_m30)

bars: closed candles by timeframe as the playbook gets them ({"M1": Bars, "M5", "M15", "H1", ...}; M1 at least
~1500, the page keeps 2500). utc(t): candle clock -> UTC. silver / dxy: {"M1": Bars[, "M5", "H1"]} or an M1
Bars, closed, on gold's candle clock. kronos30: Kronos' blended 30-minute forecast (kronos_signal.blend30).

predict() returns
  {"ok", "status", "t" (candle-clock open time of the last closed M1), "horizon": 30,
   "up_prob" (Platt-calibrated chance gold is higher in 30 minutes), "move" ($, expected 30-minute move:
   predicted move in units x M1 ATR(14) x sqrt(30)), "sd" ($, typical 30-minute move: the training months'
   RMS move in units x that unit), "dir" +1 / -1 / 0, "call" "UP" / "DOWN" / "FLAT" (FLAT unless
   |up_prob - 0.5| >= 0.04), "confidence" 0..1 (2 |up_prob - 0.5|, quartered when the model didn't beat the
   coin on its held-out months), "skill": {"brier_skill", "accuracy", "acc_low95", "n_test", "beats_coin",
   "test_period", "trained_period"}, "top": the 5 inputs that moved this forecast most [{"feature", "value",
   "push" (log-odds, + toward UP)}] (tree path contributions), "model": "quant-gbm v1", "kronos_used" (Kronos'
   inputs reached the trees: only when the model was trained with them, which quant_train.py can't do yet),
   "sessions": {"london" | "overlap" | "ny": {...}} for each session not finished yet (see _session_entry)}
"""
from __future__ import annotations

import copy
import json
import math
import threading
import time
from pathlib import Path

import quant_features as qf
import quant_sessions as qs

MODEL_FILE = Path.home() / ".golddesk" / "quant_model.json"
SESSIONS_FILE = Path.home() / ".golddesk" / "quant_sessions.json"
MODEL_NAME = "quant-gbm v1"
EDGE = 0.04
P_LO, P_HI = 0.01, 0.99
MIN_LIVE_M1 = 1500
RELOAD_S = 30.0
TRAIN_HINT = ("not trained: run  python3 quant_train.py data/dukascopy_xauusd_m1.csv.gz --split 2026-06-01  "
              "(or  python3 quant_train.py --mt5) to make ~/.golddesk/quant_model.json")
SESSIONS_HINT = ("not trained: run  python3 quant_train.py --sessions data/dukascopy_xauusd_m1.csv.gz  "
                 "(or --sessions --mt5) to make ~/.golddesk/quant_sessions.json")
SKILL_KEYS = ("brier_skill", "accuracy", "acc_low95", "n_test", "beats_coin", "test_period", "trained_period")


def sigmoid(z: float) -> float:
    z = max(-30.0, min(30.0, z))
    return 1.0 / (1.0 + math.exp(-z))


class Booster:
    """Trees exported by quant_train.GBM.export: complete heaps, node k -> 2k+1 when x[f] <= t, else 2k+2;
    f = -1 is a leaf; every node carries its value, so a path's value changes are its features' pushes."""

    def __init__(self, d: dict):
        self.base = float(d.get("base", 0.0))
        self.trees = [(t["f"], t["t"], t["v"]) for t in d.get("trees", [])]
        self.used = {f for fs, _, _ in self.trees for f in fs if f >= 0}

    def margin(self, x) -> float:
        s = self.base
        for f, t, v in self.trees:
            k = 0
            fk = f[0]
            while fk >= 0:
                k = 2 * k + 1 if x[fk] <= t[k] else 2 * k + 2
                fk = f[k]
            s += v[k]
        return s

    def contributions(self, x) -> tuple:
        """(bias, {feature index: push}); bias + sum(pushes) == margin(x)."""
        bias, out = self.base, {}
        for f, t, v in self.trees:
            k = 0
            bias += v[0]
            fk = f[0]
            while fk >= 0:
                nk = 2 * k + 1 if x[fk] <= t[k] else 2 * k + 2
                out[fk] = out.get(fk, 0.0) + v[nk] - v[k]
                k, fk = nk, f[nk]
        return bias, out


def _call(p: float | None) -> tuple:
    if p is None:
        return 0, "WAIT"
    d = 1 if p - 0.5 >= EDGE else (-1 if 0.5 - p >= EDGE else 0)
    return d, {1: "UP", -1: "DOWN", 0: "FLAT"}[d]


def _skill(m: dict | None) -> dict:
    if not m:
        return {k: None for k in SKILL_KEYS}
    met = m.get("metrics") or {}
    return {"brier_skill": met.get("brier_skill"), "accuracy": met.get("accuracy"), "acc_low95": met.get("acc_low95"),
            "n_test": met.get("n_test", met.get("n")), "beats_coin": bool(m.get("beats_coin")),
            "test_period": m.get("test_period"), "trained_period": m.get("trained_period")}


def _read(path: Path) -> tuple:
    """(dict or None, mtime or None, error or None)."""
    try:
        st = path.stat()
    except OSError:
        return None, None, None
    try:
        return json.loads(path.read_text()), st.st_mtime, None
    except (OSError, ValueError) as e:
        return None, st.st_mtime, f"{path.name} can't be read ({type(e).__name__}): retrain"


class QuantModel:
    """The trained models and their live forecasts. Thread-safe; reloads the files when they change."""

    def __init__(self, path: str | Path = MODEL_FILE, sessions_path: str | Path | None = SESSIONS_FILE):
        self.path = Path(path).expanduser()
        self.sessions_path = Path(sessions_path).expanduser() if sessions_path else None
        self.lock = threading.RLock()
        self.m = None
        self.sm = None
        self._mt = self._smt = None
        self._checked = 0.0
        self._tapes: dict = {}
        self._stapes: dict = {}
        self._key = None
        self._out = None
        self._sess_cache: dict = {}
        self.status = TRAIN_HINT
        self.sessions_status = SESSIONS_HINT
        self.load()

    # -- files -----------------------------------------------------------------------------------------------
    def load(self) -> None:
        with self.lock:
            d, mt, err = _read(self.path)
            self._mt, self.m, self.status = mt, None, err or TRAIN_HINT
            if d is not None:
                try:
                    unknown = [f for f in d["features"] if f not in qf.FEATURES]
                    if unknown:
                        raise ValueError(f"inputs this version doesn't compute ({', '.join(unknown[:3])})")
                    self.cls, self.reg = Booster(d["cls"]), Booster(d["reg"])
                    self.feats = list(d["features"])
                    self.platt = (float(d["platt"]["a"]), float(d["platt"]["b"]))
                    kix = {self.feats.index(k) for k in qf.KRONOS_FEATURES if k in self.feats}
                    self.kronos_in_trees = bool(kix & (self.cls.used | self.reg.used))
                    self.m, self.status = d, "ok"
                except (KeyError, TypeError, ValueError) as e:
                    self.status = f"{self.path.name} is from another version ({e}): retrain with quant_train.py"
            self.sm, self.sessions_status = None, SESSIONS_HINT
            if self.sessions_path is not None:
                d, mt, err = _read(self.sessions_path)
                self._smt = mt
                if err:
                    self.sessions_status = err
                if d is not None:
                    try:
                        sm = {}
                        for key, e in (d.get("sessions") or {}).items():
                            hyp = e["hypothesis"]
                            if list(e["features"]) != list(qs.FEATURES[hyp]):
                                raise ValueError(f"{key} inputs differ from this version's")
                            sm[key] = {"m": e, "cls": Booster(e["cls"]), "reg": Booster(e["reg"]),
                                       "platt": (float(e["platt"]["a"]), float(e["platt"]["b"]))}
                        self.sm, self.sessions_status = sm, "ok"
                    except (KeyError, TypeError, ValueError) as e:
                        self.sessions_status = (f"{self.sessions_path.name} is from another version ({e}): retrain "
                                                "with quant_train.py --sessions")
            self._key = self._out = None
            self._sess_cache = {}
            self._checked = time.time()

    def _maybe_reload(self) -> None:
        if time.time() - self._checked < RELOAD_S:
            return
        self._checked = time.time()

        def mtime(p):
            try:
                return p.stat().st_mtime
            except (OSError, AttributeError):
                return None
        if mtime(self.path) != self._mt or (self.sessions_path and mtime(self.sessions_path) != self._smt):
            self.load()

    # -- the 30-minute forecast ------------------------------------------------------------------------------
    def _blank(self, t, status: str) -> dict:
        return {"ok": False, "status": status, "t": t, "horizon": qf.HORIZON, "up_prob": 0.5, "move": 0.0, "sd": 0.0,
                "dir": 0, "call": "FLAT", "confidence": 0.0, "skill": _skill(self.m), "top": [], "model": MODEL_NAME,
                "kronos_used": False}

    @staticmethod
    def _other(x, t_last: int):
        """Silver / dollar candles as given, minus any newer than gold's last closed candle (still forming)."""
        if x is None:
            return None
        if isinstance(x, qf.Bars):
            x = {"M1": x}
        out = {}
        for tf, b in x.items():
            if b is None or not len(b):
                continue
            sec = qf.TF_SEC.get(tf, b.sec)
            z = qf.bisect_right(b.t, t_last + 60 - sec)                  # closed by gold's last close
            out[tf] = b if z == len(b) else qf.sub(b, 0, z)
        return out if out.get("M1") is not None and len(out["M1"]) else None

    def predict(self, bars: dict, utc, silver=None, dxy=None, kronos30: dict | None = None) -> dict:
        with self.lock:
            self._maybe_reload()
            m1 = (bars or {}).get("M1")
            t_last = int(m1.t[-1]) if m1 is not None and len(m1) else None
            kk = None if not kronos30 else (kronos30.get("t"), kronos30.get("up_prob"), kronos30.get("move"))
            sk = lambda x: None if x is None else tuple((tf, len(b), b.t[-1] if len(b) else None)
                                                        for tf, b in (x.items() if isinstance(x, dict) else [("M1", x)])
                                                        if b is not None)
            key = (t_last, len(m1) if m1 is not None else 0, m1.c[-1] if t_last is not None else None, sk(silver),
                   sk(dxy), kk, id(self.m), id(self.sm))
            if key == self._key and self._out is not None:
                return copy.deepcopy(self._out)
            out = self._predict30(bars, utc, silver, dxy, kronos30, m1, t_last)
            try:
                out["sessions"] = self._sessions(bars, utc, silver, t_last)
            except Exception as e:                                       # a session read never breaks the page
                out["sessions"] = {"error": f"{type(e).__name__}: {e}"}
            self._key, self._out = key, out
            return copy.deepcopy(out)

    def _predict30(self, bars, utc, silver, dxy, kronos30, m1, t_last) -> dict:
        if self.m is None:
            return self._blank(t_last, self.status)
        if m1 is None or len(m1) < MIN_LIVE_M1:
            return self._blank(t_last, f"not enough M1 history ({0 if m1 is None else len(m1)} closed candles, needs "
                                       f"{MIN_LIVE_M1})")
        try:
            m1w, tfs = qf.live_frames(bars)
            sv, dx = self._other(silver, t_last), self._other(dxy, t_last)
            fb = qf.FeatureBuilder(m1w, tfs, silver=sv, dxy=dx, utc=utc, tape_cache=self._tapes)
            i = len(m1w) - 1
            x = fb.features(i, kronos30)
            self._tapes = {k: v for k, v in self._tapes.items() if k in fb.used_tapes}
        except Exception as e:                                           # bad candles never break the page
            return self._blank(t_last, f"features failed ({type(e).__name__}: {e})")
        if x is None:
            return self._blank(t_last, "not enough M1 history")
        vec = [x[f] for f in self.feats]
        a, b = self.platt
        p = min(P_HI, max(P_LO, sigmoid(a * self.cls.margin(vec) + b)))
        unit = fb.unit(i)
        move = self.reg.margin(vec) * unit
        sd = float(self.m.get("y_rms") or 0.0) * unit
        d, call = _call(p)
        _, con = self.cls.contributions(vec)
        top = sorted(con.items(), key=lambda kv: -abs(kv[1]))[:5]
        skill = _skill(self.m)
        beats = skill["beats_coin"]
        notes = []
        inp = self.m.get("inputs") or {}
        if inp.get("silver") and sv is None:
            notes.append("silver missing (the model was trained with it: its inputs read 0)")
        if inp.get("dxy") and dx is None:
            notes.append("dollar index missing (trained with it: its inputs read 0)")
        head = (f"beats a coin on {skill['test_period']} (Brier skill {skill['brier_skill']:+.4f}, right "
                f"{skill['accuracy']:.1%})" if beats else
                f"no measured edge on its held-out months ({skill['test_period']}): treat as a coin")
        return {"ok": True, "status": "; ".join([head] + notes), "t": t_last, "horizon": qf.HORIZON,
                "up_prob": round(p, 4), "move": round(move, 3), "sd": round(sd, 3), "dir": d, "call": call,
                "confidence": round(min(1.0, 2 * abs(p - 0.5)) * (1.0 if beats else 0.25), 3), "skill": skill,
                "top": [{"feature": self.feats[k], "value": round(vec[k], 4), "push": round(c * a, 4)} for k, c in top],
                "model": MODEL_NAME,
                "kronos_used": bool(self.kronos_in_trees and kronos30 and kronos30.get("up_prob") is not None)}

    # -- sessions ----------------------------------------------------------------------------------------------
    def _sessions(self, bars, utc, silver, t_last) -> dict:
        if t_last is None:
            return {}
        utc = utc or (lambda t: t)
        now = utc(t_last) + 60
        out, sb = {}, None
        for key, (name, hyp, w, _, waits) in qs.SESSIONS.items():
            day = qs.instance(key, now)
            start, end = qs.window(day, w)
            known = start
            e = {"session": name, "hypothesis": hyp, "known_at": known, "starts": start, "ends": end,
                 "up_prob": None, "move_pct": None, "dir": 0, "call": "WAIT", "skill": None, "status": ""}
            ent = (self.sm or {}).get(key)
            if ent is None:
                e["skill"] = {k: None for k in ("accuracy", "acc_low95", "brier_skill", "n_test", "beats_coin", "test_period")}
                e["status"] = self.sessions_status if self.sessions_status != "ok" else f"no {name} model in the file"
                out[key] = e
                continue
            s = _skill(ent["m"])
            e["skill"] = {k: s[k] for k in ("accuracy", "acc_low95", "brier_skill", "n_test", "beats_coin", "test_period")}
            if now < known:
                mins = (known - now) // 60
                e["status"] = f"waits for {waits} (in {mins // 60} h {mins % 60:02d} min)"
                out[key] = e
                continue
            ck = (key, day)
            hit = self._sess_cache.get(ck)
            if hit is None:
                if sb is None:
                    h1 = qs.live_h1(bars, utc)
                    sb = qs.SessionBuilder(h1, qs.live_silver_h1(silver, t_last), utc=utc, tape_cache=self._stapes)
                v = sb.vector(day, hyp)
                if v is None:
                    hit = {"status": "not enough H1 history to read the sessions before it"}
                else:
                    a, b = ent["platt"]
                    p = min(P_HI, max(P_LO, sigmoid(a * ent["cls"].margin(v) + b)))
                    hit = {"up_prob": round(p, 4), "move_pct": round(ent["reg"].margin(v), 4)}
                self._sess_cache = {k: x for k, x in self._sess_cache.items() if k[1] >= day - 3}
                self._sess_cache[ck] = hit
            e.update({k: v for k, v in hit.items() if k != "status"})
            if "up_prob" in hit:
                e["dir"], e["call"] = _call(hit["up_prob"])
                edge = "beats a coin on its held-out days" if s["beats_coin"] else "no measured edge: treat as a coin"
                run = (f"running until {qs.hhmm(end)} London" if now < end else "finished")
                e["status"] = f"forecast made at {qs.hhmm(known)} London; {run}; {edge}"
            else:
                e["status"] = hit["status"]
            out[key] = e
        if sb is not None:
            self._stapes = {k: v for k, v in self._stapes.items() if k in sb.used_tapes}
        return out


# ------------------------------------------------------------------ quant + Kronos
FLOOR = 1e-3


def _kronos_skill(k: dict) -> float:
    """Kronos' measured skill (kronos_calib: Brier skill against a coin, >= 0, shrunk while samples are few)."""
    if isinstance(k.get("skill"), (int, float)):
        return max(0.0, float(k["skill"]))
    cal = k.get("calibration") or {}
    if isinstance(cal.get("skill"), (int, float)):
        return max(0.0, float(cal["skill"]))
    m30 = float((cal.get("M30") or {}).get("skill") or 0.0)
    w = k.get("weights") or {}
    src = sum(float(v) * float((cal.get(f"{s}x30") or {}).get("skill") or 0.0) for s, v in w.items())
    share = cal.get("blend_share")
    return max(0.0, m30 if share is None else share * m30 + (1 - share) * src)


def blend_with_kronos(q: dict | None, k: dict | None) -> dict:
    """One 30-minute forecast from the quant model's (predict()) and Kronos' (kronos_signal.blend30: up_prob,
    move, calibration skill). Weights follow measured skill: the quant model's held-out Brier skill when it
    beat the coin (else 0), Kronos' live calibrated skill; each at least 0.001, so a source that hasn't beaten
    a coin gets ~0 weight next to one that has, and two unskilled sources are simply averaged.
    -> {"up_prob", "move", "weights": {"quant", "kronos"}, "agree", "text"}"""
    has_q = bool(q and q.get("ok"))
    has_k = bool(k and k.get("up_prob") is not None)
    if not has_q and not has_k:
        return {"up_prob": 0.5, "move": 0.0, "weights": {"quant": 0.0, "kronos": 0.0}, "agree": False,
                "text": "No 30-minute forecast: neither the quant model nor Kronos has one now."}
    sq = 0.0
    if has_q:
        s = q.get("skill") or {}
        sq = max(0.0, float(s.get("brier_skill") or 0.0)) if s.get("beats_coin") else 0.0
    sk = _kronos_skill(k) if has_k else 0.0
    wq = max(FLOOR, sq) if has_q else 0.0
    wk = max(FLOOR, sk) if has_k else 0.0
    tot = wq + wk
    wq, wk = wq / tot, wk / tot
    pq = float(q["up_prob"]) if has_q else 0.5
    pk = float(k["up_prob"]) if has_k else 0.5
    mq = float(q.get("move") or 0.0) if has_q else 0.0
    mk = float(k.get("move") or 0.0) if has_k else 0.0
    p = wq * pq + wk * pk
    mv = wq * mq + wk * mk
    agree = has_q and has_k and (pq - 0.5) * (pk - 0.5) > 0
    word = lambda x: f"{x:.0%} up"
    if has_q and has_k:
        rel = "agree" if agree else "disagree"
        head = f"Quant {word(pq)} ({mq:+.2f}), Kronos {word(pk)} ({mk:+.2f}): they {rel}."
    elif has_q:
        head = f"Quant {word(pq)} ({mq:+.2f}); no Kronos forecast now."
    else:
        head = f"Kronos {word(pk)} ({mk:+.2f}); the quant model has no forecast now."
    if sq <= 0 and sk <= 0:
        tail = " Neither has beaten a coin out of sample, so the blend is a coin flip in practice."
    else:
        tail = f" Weighted by measured skill (quant {wq:.2f}, Kronos {wk:.2f}): {word(p)}, {mv:+.2f} in 30 minutes."
    return {"up_prob": round(p, 4), "move": round(mv, 3), "weights": {"quant": round(wq, 4), "kronos": round(wk, 4)},
            "agree": bool(agree), "text": head + tail}
