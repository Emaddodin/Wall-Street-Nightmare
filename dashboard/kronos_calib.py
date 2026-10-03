"""Live calibration of the Kronos 30-minute forecasts (the "fine-tune it to act best on M1 / M5" part that runs
on any PC, no GPU).

What it does, plainly: every 30-minute forecast is kept until its last bar has closed, then it is compared with
what price really did. From those resolved forecasts each source (M1x30: 30 M1 bars, M5x30: 6 M5 bars, M30: the
blend of both) learns

  - Platt scaling of the upside probability: a logistic regression of "price ended above the last close" on
    logit(up_prob_raw), fitted by a few Newton steps on the last 500 resolved forecasts. An L2 prior pulls it
    towards 'no skill' (always 50%) while data is thin and a little towards the raw value (identity), so a
    handful of lucky calls cannot make it confident. Probabilities are clipped to 3% .. 97%.
  - Move scaling: calibrated move = bias + k * raw move, with k = cov(real, forecast) / var(forecast) clipped to
    0 .. 1.5 and shrunk towards 0.5 (bias towards 0) while there are few samples.
  - Skill: the Brier skill score against a 50% coin, scored prequentially (each forecast with the calibration
    that existed *before* its outcome was known, so the score is out of sample), and the direction hit-rate with
    the 95% range a coin flip would show on that many tries. `skill` (never below 0, shrunk while samples are few)
    sets how much each source counts in the blend.

Calibration measures Kronos on gold and shrinks it towards what it actually achieved. It does not create an edge:
if Kronos is a coin flip on 30-minute gold (what kronos_backtest.py and the mesh have found so far), the
calibrated probability sits near 50% and the calibrated move near 0, which is the honest answer.

Kept in ~/.golddesk/kronos_calib.json. kronos_backtest.py --calib-out writes an offline prior
(~/.golddesk/kronos_calib_prior.json) from a backtest, which seeds the calibrator before live samples arrive and
fades out as they do. Pure Python, no numpy.
"""
from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path

CALIB_FILE = Path.home() / ".golddesk" / "kronos_calib.json"
PRIOR_FILE = Path.home() / ".golddesk" / "kronos_calib_prior.json"
SOURCES = ("M1x30", "M5x30", "M30")
P_LO, P_HI = 0.03, 0.97
MAX_N = 500            # live samples kept per source (rolling)
PRIOR_CAP = 200        # an offline prior counts as at most this many samples ...
PRIOR_FLOOR = 0.2      # ... fading to this share of that once MAX_N live samples are in
LAM_ID = 2.0           # L2 pull of the Platt slope towards 1 (raw = calibrated) ...
LAM_NS = 20.0          # ... and towards 0 (no skill); both pull the intercept to 0
K_PRIOR, K_N0 = 0.5, 30.0      # move shrink factor: prior value and its weight in samples
SKILL_N0 = 50.0                # skill is shrunk by n / (n + SKILL_N0)
REFIT_SEED = 20                # while seeding from a prior, refit every this many samples


def clip_p(p: float) -> float:
    return min(P_HI, max(P_LO, float(p)))


def logit(p: float) -> float:
    p = clip_p(p)
    return math.log(p / (1.0 - p))


def sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def platt_fit(xs: list, ys: list, ws: list, lam_id: float = LAM_ID, lam_ns: float = LAM_NS,
              iters: int = 12) -> tuple:
    """Penalised logistic regression p = sigmoid(a * x + b). Minimises the weighted log-loss plus
    lam_id/2 * ((a - 1)^2 + b^2) + lam_ns/2 * (a^2 + b^2) by Newton's method (2 x 2, strictly convex)."""
    lam = lam_id + lam_ns
    a, b = (lam_id / lam if lam else 1.0), 0.0
    for _ in range(iters):
        ga = lam_id * (a - 1.0) + lam_ns * a
        gb = lam * b
        haa = hbb = lam
        hab = 0.0
        for x, y, w in zip(xs, ys, ws):
            p = sigmoid(a * x + b)
            r, v = w * (p - y), w * p * (1.0 - p)
            ga += r * x
            gb += r
            haa += v * x * x
            hab += v * x
            hbb += v
        det = haa * hbb - hab * hab
        if det <= 1e-12:
            break
        da = (hbb * ga - hab * gb) / det
        db = (haa * gb - hab * ga) / det
        a, b = a - da, b - db
        if abs(da) < 1e-7 and abs(db) < 1e-7:
            break
    return a, b


def coin_band(n: int) -> float:
    """Half-width of the 95% range of a fair coin's hit-rate over n tries."""
    return 1.96 * math.sqrt(0.25 / n) if n else 0.5


class Calibrator:
    """One source. Samples are [up_prob_raw, move_raw, real_move, p_pre, t]; p_pre is the calibrated probability
    this calibrator gave before the outcome was known (for the out-of-sample skill score)."""

    def __init__(self, name: str, max_n: int = MAX_N):
        self.name, self.max_n = name, max_n
        self.live: list = []
        self.prior: list = []
        self.prior_meta: dict | None = None
        self.a, self.b = LAM_ID / (LAM_ID + LAM_NS), 0.0
        self.k, self.bias = K_PRIOR, 0.0
        self.k_raw: float | None = None

    # -- data ---------------------------------------------------------------------------------------------
    def _weighted(self) -> list:
        """(sample, weight): live samples weigh 1; the offline prior counts as at most PRIOR_CAP samples,
        fading to PRIOR_FLOOR of that as live samples arrive."""
        out = [(s, 1.0) for s in self.live]
        if self.prior:
            fade = max(PRIOR_FLOOR, 1.0 - len(self.live) / float(self.max_n))
            w = min(1.0, PRIOR_CAP / float(len(self.prior))) * fade
            out = [(s, w) for s in self.prior] + out
        return out

    def seed(self, samples: list, meta: dict | None = None) -> None:
        """Offline prior from a backtest: [[up_prob_raw, move_raw, real_move, (last)], ...], oldest first.
        Scored prequentially too, refitting every REFIT_SEED samples."""
        self.prior, self.prior_meta = [], meta
        for i, s in enumerate(samples[-self.max_n:]):
            try:
                u, m, r = float(s[0]), float(s[1]), float(s[2])
            except (TypeError, ValueError, IndexError):
                continue
            if i % REFIT_SEED == 0:
                self.fit()
            self.prior.append([u, m, r, self.prob(u), None])
        self.fit()

    def add(self, up_prob_raw: float, move_raw: float, real_move: float, t: int | None = None) -> None:
        u = 0.5 if up_prob_raw is None else float(up_prob_raw)
        p_pre = self.prob(u)
        self.live = (self.live + [[u, float(move_raw or 0.0), float(real_move), p_pre, t]])[-self.max_n:]
        self.fit()

    # -- fit ------------------------------------------------------------------------------------------------
    def fit(self) -> None:
        ws = self._weighted()
        xs, ys, wp = [], [], []
        for s, w in ws:
            if s[2] != 0:
                xs.append(logit(s[0])); ys.append(1.0 if s[2] > 0 else 0.0); wp.append(w)
        self.a, self.b = platt_fit(xs, ys, wp)
        n = sum(w for _, w in ws)
        if n <= 0:
            self.k, self.bias, self.k_raw = K_PRIOR, 0.0, None
            return
        mp = sum(w * s[1] for s, w in ws) / n
        mr = sum(w * s[2] for s, w in ws) / n
        var = sum(w * (s[1] - mp) ** 2 for s, w in ws) / n
        cov = sum(w * (s[1] - mp) * (s[2] - mr) for s, w in ws) / n
        k = min(1.5, max(0.0, cov / var)) if var > 1e-12 else 0.0
        self.k_raw = k
        self.k = (n * k + K_N0 * K_PRIOR) / (n + K_N0)
        self.bias = n / (n + K_N0) * (mr - self.k * mp)

    # -- use ------------------------------------------------------------------------------------------------
    def prob(self, up_prob_raw: float | None) -> float:
        u = 0.5 if up_prob_raw is None else up_prob_raw
        return clip_p(sigmoid(self.a * logit(u) + self.b))

    def move(self, move_raw: float | None) -> float:
        return self.bias + self.k * float(move_raw or 0.0)

    def stats(self) -> dict:
        ws = self._weighted()
        n_w = sum(w for s, w in ws if s[2] != 0)
        bs = sum(w * (s[3] - (1.0 if s[2] > 0 else 0.0)) ** 2 for s, w in ws if s[2] != 0)
        bss = 1.0 - (bs / n_w) / 0.25 if n_w else None
        hits = [((s[1] if s[1] else s[0] - 0.5) > 0) == (s[2] > 0) for s, _ in ws
                if s[2] != 0 and (s[1] or s[0] != 0.5)]
        n_dir = len(hits)
        hit = sum(hits) / n_dir if n_dir else None
        skill = max(0.0, bss) * n_w / (n_w + SKILL_N0) if bss is not None else 0.0
        return {"n": round(n_w, 1), "brier_skill": None if bss is None else round(bss, 4),
                "skill": round(skill, 4), "hit_rate": None if hit is None else round(hit, 4), "n_dir": n_dir,
                "coin_band": round(coin_band(n_dir), 4),
                "beats_coin": bool(hit is not None and n_dir >= 30 and hit > 0.5 + coin_band(n_dir))}

    @property
    def skill(self) -> float:
        return self.stats()["skill"]

    def summary(self) -> dict:
        return {**self.stats(), "n_live": len(self.live), "n_prior": len(self.prior),
                "a": round(self.a, 4), "b": round(self.b, 4), "k": round(self.k, 4), "bias": round(self.bias, 4),
                "k_raw": None if self.k_raw is None else round(self.k_raw, 4),
                "prior": self.prior_meta}


def blend_weights(skills: dict, prior: dict | None = None, floor: float = 0.05) -> dict:
    """weight ∝ prior[source] * max(floor, skill); a source missing from `skills` (or None) gets 0."""
    prior = prior or {}
    raw = {k: prior.get(k, 1.0) * max(floor, s) for k, s in skills.items() if s is not None}
    tot = sum(raw.values())
    return {k: (v / tot if tot else 0.0) for k, v in raw.items()}


class CalibSet:
    """The calibrators of all sources, persisted together. Thread-safe."""

    def __init__(self, file: Path | str | None = CALIB_FILE, prior_file: Path | str | None = PRIOR_FILE,
                 names: tuple = SOURCES, max_n: int = MAX_N):
        self.file = Path(file).expanduser() if file else None
        self.prior_file = Path(prior_file).expanduser() if prior_file else None
        self.max_n = max_n
        self.lock = threading.RLock()
        self.cals: dict = {n: Calibrator(n, max_n) for n in names}
        self._load_prior()
        self._load()

    def _load_prior(self) -> None:
        try:
            d = json.loads(self.prior_file.read_text())
            for name, src in (d.get("sources") or {}).items():
                if isinstance(src, dict) and isinstance(src.get("samples"), list):
                    self.get(name).seed(src["samples"], src.get("meta"))
        except (OSError, ValueError, AttributeError, TypeError):
            pass

    def _load(self) -> None:
        try:
            d = json.loads(self.file.read_text())
            for name, src in (d.get("sources") or {}).items():
                c = self.get(name)
                live = []
                for s in src.get("samples") or []:
                    try:
                        live.append([float(s[0]), float(s[1]), float(s[2]), clip_p(s[3]),
                                     int(s[4]) if len(s) > 4 and s[4] is not None else None])
                    except (TypeError, ValueError, IndexError):
                        continue
                c.live = live[-self.max_n:]
                c.fit()
        except (OSError, ValueError, AttributeError, TypeError):
            pass

    def save(self) -> None:
        if not self.file:
            return
        with self.lock:
            d = {"version": 1, "saved": int(time.time()),
                 "sources": {n: {"samples": c.live} for n, c in self.cals.items() if c.live}}
        try:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.file.with_suffix(".tmp")
            tmp.write_text(json.dumps(d))
            tmp.replace(self.file)
        except OSError:
            pass

    def get(self, name: str) -> Calibrator:
        with self.lock:
            c = self.cals.get(name)
            if c is None:
                c = self.cals[name] = Calibrator(name, self.max_n)
            return c

    def add(self, name: str, up_prob_raw: float, move_raw: float, real_move: float, t: int | None = None,
            save: bool = True) -> None:
        with self.lock:
            self.get(name).add(up_prob_raw, move_raw, real_move, t)
        if save:
            self.save()

    def prob(self, name: str, up_prob_raw: float | None) -> float:
        with self.lock:
            return self.get(name).prob(up_prob_raw)

    def move(self, name: str, move_raw: float | None) -> float:
        with self.lock:
            return self.get(name).move(move_raw)

    def skill(self, name: str) -> float:
        with self.lock:
            return self.get(name).skill

    def n(self, name: str) -> float:
        with self.lock:
            return self.get(name).stats()["n"]

    def weights(self, names, prior: dict | None = None) -> dict:
        return blend_weights({n: self.skill(n) for n in names}, prior)

    def summary(self) -> dict:
        with self.lock:
            return {n: c.summary() for n, c in self.cals.items()}


def write_prior(path: Path | str, name: str, samples: list, meta: dict | None = None) -> Path:
    """Write (or merge into) the offline prior file: samples [[up_prob_raw, move_raw, real_move, last], ...]
    under source `name` (e.g. "M1x30"); other sources already in the file are kept."""
    p = Path(path).expanduser()
    try:
        d = json.loads(p.read_text())
        if not isinstance(d, dict) or not isinstance(d.get("sources"), dict):
            raise ValueError
    except (OSError, ValueError):
        d = {"version": 1, "sources": {}}
    d["sources"][name] = {"samples": [[round(float(x), 6) for x in s[:4]] for s in samples],
                          "meta": {**(meta or {}), "n": len(samples), "written": int(time.time())}}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d))
    return p
