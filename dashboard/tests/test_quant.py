"""The 30-minute quant model (quant_features.py, quant_train.py, quant.py) on synthetic candles: no network, no
Dukascopy. A planted signal must be found and beat the coin; pure noise must not; features must not look ahead
and must be the same live (the page's candle windows, a server clock) as in training; the JSON trees must give
the numpy margins; blending must follow measured skill."""
import json
import math
import random
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

np = pytest.importorskip("numpy")

import quant  # noqa: E402
import quant_features as qf  # noqa: E402
import quant_train as qt  # noqa: E402
from engine import Bars  # noqa: E402

T0 = 1_767_571_200                       # 2026-01-05 00:00 UTC, a Monday
SCHEMA = {"ok", "status", "t", "horizon", "up_prob", "move", "sd", "dir", "call", "confidence", "skill", "top",
          "model", "kronos_used", "sessions"}
SKILL = {"brier_skill", "accuracy", "acc_low95", "n_test", "beats_coin", "test_period", "trained_period"}


def _open(t):
    d, m = (t // 86400 + 3) % 7, t % 86400 // 60
    if d == 5 or (d == 6 and m < 23 * 60) or (d == 4 and m >= 22 * 60):
        return False
    return not (22 * 60 <= m < 23 * 60)                      # the daily break


def make(days=30, seed=1, kappa=0.0, cross=False, t0=T0):
    """Gold-like M1 candles (UTC). kappa > 0 plants momentum: the next minutes drift with the sign of the last
    60 minutes' move. cross: also silver (follows gold) and a dollar index (moves against it)."""
    rnd = random.Random(seed)
    g, s, x = Bars(60), Bars(60), Bars(60)
    p, q, z = 2600.0, 30.0, 100.0
    hist = []
    for k in range(days * 1440):
        t = t0 + 60 * k
        if not _open(t):
            continue
        m = t % 86400 // 60
        vol = 0.35 * (1.6 if 720 <= m < 960 else (1.2 if 420 <= m < 720 else 0.8))
        drift = kappa * vol * (1 if hist[-1] > hist[-61] else -1) if kappa and len(hist) > 60 else 0.0
        o = p
        p = p + drift + vol * rnd.gauss(0, 1)
        g.append(t, round(o, 2), round(max(o, p) + abs(rnd.gauss(0, vol * .4)), 2),
                 round(min(o, p) - abs(rnd.gauss(0, vol * .4)), 2), round(p, 2), 1)
        hist.append(p)
        if cross:
            r = (p - o) / o
            qo, zo = q, z
            q *= 1 + 0.8 * r + 0.0003 * rnd.gauss(0, 1)
            z *= 1 - 0.3 * r + 0.00005 * rnd.gauss(0, 1)
            s.append(t, qo, max(qo, q) * 1.00005, min(qo, q) * 0.99995, q)
            x.append(t, zo, max(zo, z) * 1.00001, min(zo, z) * 0.99999, z)
    return (g, s, x) if cross else g


def live_bars(g, i, full=None):
    """What the page passes at the close of M1 candle i: closed candles, M1 2500, M5 600, M15 400, H1 800."""
    full = full or {tf: qf.aggregate(g, qf.TF_SEC[tf]) for tf in ("M5", "M15", "H1")}
    q = g.t[i] + 60
    out = {"M1": qf.sub(g, i + 1 - 2500, i + 1)}
    for tf, n in (("M5", 600), ("M15", 400), ("H1", 800)):
        b = full[tf]
        z = qf.bisect_right([t + b.sec for t in b.t], q)
        out[tf] = qf.sub(b, max(0, z - n), z)
    return out


def shifted(b, sec):
    """The same candles on a clock `sec` ahead of UTC (a broker's server time)."""
    out = Bars(b.sec)
    for i in range(len(b)):
        out.append(b.t[i] + sec, b.o[i], b.h[i], b.l[i], b.c[i], b.v[i])
    return out


@pytest.fixture(scope="module")
def planted(tmp_path_factory):
    g = make(days=30, seed=11, kappa=0.08)
    ds = qt.build_dataset(g, step=5, log=None)
    split = int(np.quantile(ds["t"], 0.75)) // 86400 * 86400
    model, met, ex = qt.train(ds, split, folds=4, grid=qt.GRID[1:2], max_trees=150, log=None)
    path = tmp_path_factory.mktemp("q") / "quant_model.json"
    qt.write_model(model, str(path))
    return {"g": g, "ds": ds, "model": model, "met": met, "ex": ex, "path": path}


@pytest.fixture(scope="module")
def noise():
    g = make(days=30, seed=12, kappa=0.0)
    ds = qt.build_dataset(g, step=5, log=None)
    split = int(np.quantile(ds["t"], 0.75)) // 86400 * 86400
    model, met, _ = qt.train(ds, split, folds=4, grid=qt.GRID[1:2], max_trees=150, log=None)
    return model, met


# ------------------------------------------------------------------ features
def test_features_complete_and_finite():
    g, s, x = make(days=4, seed=3, cross=True)
    fb = qf.FeatureBuilder(g, silver=s, dxy=x)
    f = fb.features(len(g) - 1)
    assert list(f) == list(qf.FEATURES)
    assert all(isinstance(v, float) and math.isfinite(v) for v in f.values())
    assert fb.features(10) is None                                    # too little history
    assert f["kr_up"] == 0.0 and f["kr_move"] == 0.0                   # Kronos placeholders without a forecast
    f2 = fb.features(len(g) - 1, {"up_prob": 0.7, "move": 1.0})
    assert f2["kr_up"] == pytest.approx(0.2) and f2["kr_move"] > 0


def test_no_lookahead():
    """Features at candle i are the same whether or not later candles exist (all inputs, silver and dollar)."""
    g, s, x = make(days=8, seed=5, cross=True)
    full = qf.FeatureBuilder(g, silver=s, dxy=x)
    for cut in (6000, 8333):
        short = qf.FeatureBuilder(qf.sub(g, 0, cut), silver=qf.sub(s, 0, cut), dxy=qf.sub(x, 0, cut))
        for i in list(range(cut - 400, cut, 37)) + [cut - 1]:
            assert full.vector(i) == short.vector(i), i


def test_live_window_and_server_clock_match_training():
    """The page's windows (2500 M1, 600 M5, 400 M15, 800 H1 candles) on a UTC+3 candle clock give the training
    features (the recursive ATR / RSI / EMA forget their start to ~1e-12)."""
    g, s, _ = make(days=10, seed=7, cross=True)
    full = qf.FeatureBuilder(g, silver=s)
    agg = {tf: qf.aggregate(g, qf.TF_SEC[tf]) for tf in ("M5", "M15", "H1")}
    off = 3 * 3600
    worst = 0.0
    for i in range(5000, len(g), 211):
        bars = live_bars(g, i, agg)
        srv = {tf: shifted(b, off) for tf, b in bars.items()}
        m1, tfs = qf.live_frames(srv)
        lo = i + 1 - 2500
        live = qf.FeatureBuilder(m1, tfs, silver=shifted(qf.sub(s, lo, i + 1), off), utc=lambda t: t - off)
        a, b = full.vector(i), live.vector(len(m1) - 1)
        worst = max(worst, max(abs(u - v) for u, v in zip(a, b)))
    assert worst < 1e-6


def test_lean_smt_matches_reader():
    import smt
    g = make(days=3, seed=9)
    rnd = random.Random(4)
    s, q = Bars(60), 30.0
    for i in range(len(g)):
        qo = q
        q *= 1 + 0.7 * (g.c[i] - g.o[i]) / g.o[i] + 0.00012 * rnd.gauss(0, 1)
        s.append(g.t[i], qo, max(qo, q) * 1.00003, min(qo, q) * 0.99997, q)
    rd, nz = smt.SMTReader(), 0
    for i in range(200, len(g), 17):
        gw = qf.sub(g, i - 138, i + 1)
        sw = qf.sub(s, i - 150, i + 1)
        r = rd.update("M1", gw, sw, last_forming=False)
        ref = r["state"] if r["ok"] and r["corr_ok"] else 0
        st, cr = qf.smt_state(gw, sw)
        assert (st, cr) == (ref, r["corr"])
        nz += ref != 0
    assert nz > 0


# ------------------------------------------------------------------ training
def test_planted_signal_beats_coin(planted):
    met, model = planted["met"], planted["model"]
    assert model["beats_coin"] is True
    assert met["brier_skill"] > 0.01 and met["acc_low95"] > 0.5 and met["auc"] > 0.55
    assert any(r["feature"] in ("ret_60", "ret_30", "pos60", "pos240", "er120", "z60") for r in met["importance"][:3])
    assert model["features"] == list(qf.FEATURES) and model["horizon"] == 30
    for k in ("version", "features", "trees", "platt", "bins", "trained_period", "test_period", "metrics",
              "beats_coin", "note"):
        assert k in model
    assert set(model["trees"]) == {"cls", "reg"} and isinstance(model["beats_coin"], bool)


def test_noise_has_no_edge(noise):
    model, met = noise
    assert model["beats_coin"] is False
    assert abs(met["brier_skill"]) < 0.01


def test_purging_keeps_labels_out_of_the_test_period(planted):
    ds = planted["ds"]
    t = ds["t"]
    folds = qt.purged_folds(t, 4, ds["horizon_s"], 1440 * 60)
    for tr, va in folds:
        v0, v1 = t[va].min(), t[va].max() + ds["horizon_s"]
        tt = t[tr]
        assert not np.any((tt + ds["horizon_s"] >= v0) & (tt <= v1 + 1440 * 60))


def test_json_roundtrip_matches_numpy(planted):
    """Booster (pure Python, from the JSON file) == the numpy model on raw and on binned inputs."""
    ex, ds, model = planted["ex"], planted["ds"], planted["model"]
    te = np.where(ex["test_mask"])[0][:300]
    X = ds["X"][te]
    loaded = json.loads(Path(planted["path"]).read_text())
    for kind in ("cls", "reg"):
        b = quant.Booster(loaded["trees"][kind])
        py = np.array([b.margin(list(r)) for r in X])
        raw = ex[kind].margin_raw(X, ex["edges"])
        binned = ex[kind].margin(qt.apply_bins(X, ex["edges"]))
        assert np.max(np.abs(py - raw)) < 1e-9 and np.max(np.abs(py - binned)) < 1e-9
        bias, con = b.contributions(list(X[0]))
        assert bias + sum(con.values()) == pytest.approx(b.margin(list(X[0])), abs=1e-9)


# ------------------------------------------------------------------ live
def test_predict_schema_matches_numpy_and_is_fast(planted, tmp_path):
    g, model, ex = planted["g"], planted["model"], planted["ex"]
    qm = quant.QuantModel(planted["path"], sessions_path=tmp_path / "none.json")
    agg = {tf: qf.aggregate(g, qf.TF_SEC[tf]) for tf in ("M5", "M15", "H1")}
    i = len(g) - 200
    bars = live_bars(g, i, agg)
    t0 = time.perf_counter()
    r = qm.predict(bars, lambda t: t)
    cold = time.perf_counter() - t0
    t0 = time.perf_counter()
    r2 = qm.predict(bars, lambda t: t)
    warm = time.perf_counter() - t0
    assert r == r2 and warm < 0.005 and cold < 0.25
    assert set(r) == SCHEMA and set(r["skill"]) == SKILL
    assert r["ok"] and r["model"] == "quant-gbm v1" and r["horizon"] == 30 and r["t"] == g.t[i]
    assert 0.01 <= r["up_prob"] <= 0.99 and r["sd"] > 0 and r["kronos_used"] is False
    assert r["call"] == {1: "UP", -1: "DOWN", 0: "FLAT"}[r["dir"]]
    assert (r["dir"] != 0) == (abs(r["up_prob"] - 0.5) >= 0.04)
    assert len(r["top"]) == 5 and all(set(x) == {"feature", "value", "push"} for x in r["top"])
    assert r["skill"]["beats_coin"] is True
    # the same number numpy gives for the training-side features at that candle
    fb = qf.FeatureBuilder(g)
    X = np.array([fb.vector(i)])
    p = float(qt.calibrated(ex["cls"].margin_raw(X, ex["edges"]), ex["platt"])[0])
    assert r["up_prob"] == pytest.approx(p, abs=1e-4)
    mv = float(ex["reg"].margin_raw(X, ex["edges"])[0]) * fb.unit(i)
    assert r["move"] == pytest.approx(mv, abs=1e-3)
    # each new closed candle is computed again; a cached one is not
    r3 = qm.predict(live_bars(g, i + 1, agg), lambda t: t)
    assert r3["t"] == g.t[i + 1]


def test_missing_model_says_how_to_train(tmp_path):
    g = make(days=3, seed=2)
    qm = quant.QuantModel(tmp_path / "nope.json", sessions_path=tmp_path / "nope2.json")
    r = qm.predict({"M1": g}, lambda t: t)
    assert set(r) == SCHEMA
    assert r["ok"] is False and "quant_train.py" in r["status"] and r["call"] == "FLAT" and r["up_prob"] == 0.5
    assert all(e["call"] == "WAIT" and "quant_train.py --sessions" in e["status"] for e in r["sessions"].values())


def test_blend_follows_measured_skill():
    q_good = {"ok": True, "up_prob": 0.6, "move": 1.0, "skill": {"brier_skill": 0.02, "beats_coin": True}}
    q_bad = {"ok": True, "up_prob": 0.6, "move": 1.0, "skill": {"brier_skill": 0.01, "beats_coin": False}}
    k_good = {"up_prob": 0.4, "move": -1.0, "calibration": {"blend_share": 1.0, "M30": {"skill": 0.03}}}
    k_bad = {"up_prob": 0.4, "move": -1.0, "calibration": {"blend_share": 0.5, "M30": {"skill": 0.0},
                                                           "M1x30": {"skill": 0.0}}, "weights": {"M1": 1.0}}
    b = quant.blend_with_kronos(q_good, k_bad)
    assert b["weights"]["quant"] > 0.95 and b["up_prob"] > 0.59 and b["agree"] is False
    b = quant.blend_with_kronos(q_bad, k_good)
    assert b["weights"]["kronos"] > 0.95 and b["up_prob"] < 0.41
    b = quant.blend_with_kronos(q_bad, k_bad)
    assert b["weights"]["quant"] == pytest.approx(0.5) and "coin" in b["text"]
    b = quant.blend_with_kronos(q_good, {"up_prob": 0.7, "move": 2.0, "skill": 0.02})
    assert b["agree"] is True and set(b) == {"up_prob", "move", "weights", "agree", "text"}
    b = quant.blend_with_kronos({"ok": False}, None)
    assert b["up_prob"] == 0.5 and b["weights"] == {"quant": 0.0, "kronos": 0.0}
    b = quant.blend_with_kronos({"ok": False}, k_good)
    assert b["weights"]["kronos"] == 1.0 and b["up_prob"] == 0.4
