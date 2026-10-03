"""Tests for the Kronos 30-minute calibration (kronos_calib.py) and blend (kronos_signal.blend30 / KronosWorker),
with synthetic data and a fake model: no torch, no pandas, no network."""
import json
import math
import random
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import kronos_calib as kc  # noqa: E402
import kronos_signal as ks  # noqa: E402


def informative(n, seed=1):
    """A source that knows the outcome: up_prob 0.8 / 0.2 and a move of the right sign."""
    rnd = random.Random(seed)
    out = []
    for _ in range(n):
        up = rnd.random() < 0.5
        real = (1 if up else -1) * (0.5 + rnd.random() * 3)
        out.append((0.8 if up else 0.2, real * 0.5 + rnd.gauss(0, 0.2), real))
    return out


def noise(n, seed=2):
    """A source that knows nothing: random probabilities and moves."""
    rnd = random.Random(seed)
    return [(rnd.random(), rnd.gauss(0, 2), rnd.gauss(0, 2)) for _ in range(n)]


def fill(cal, name, data):
    for u, m, r in data:
        cal.add(name, u, m, r, save=False)


def test_empty_calibrator_is_no_skill():
    c = kc.Calibrator("M1x30")
    assert abs(c.prob(0.95) - 0.5) < 0.1            # with no data it barely trusts the raw number
    assert c.prob(0.5) == pytest.approx(0.5, abs=1e-9)
    assert c.skill == 0.0
    assert c.move(2.0) == pytest.approx(1.0)        # k = 0.5 prior, no bias
    assert kc.P_LO <= c.prob(0.0) <= kc.P_HI and kc.P_LO <= c.prob(1.0) <= kc.P_HI


def test_informative_source_gains_skill_and_sharpens():
    cs = kc.CalibSet(file=None, prior_file=None)
    fill(cs, "M1x30", informative(300))
    s = cs.get("M1x30").summary()
    assert s["skill"] > 0.3 and s["brier_skill"] > 0.3
    assert s["hit_rate"] > 0.95 and s["beats_coin"]
    assert cs.prob("M1x30", 0.8) > 0.8              # calibrated probability moves towards the outcomes
    assert cs.prob("M1x30", 0.2) < 0.2
    assert cs.prob("M1x30", 0.999) <= kc.P_HI
    assert s["k"] > 1.0                             # real move is about 2x the forecast: k grows towards 1.5
    assert cs.move("M1x30", 1.0) > 1.0


def test_random_source_is_pulled_to_coin():
    cs = kc.CalibSet(file=None, prior_file=None)
    fill(cs, "M5x30", noise(400))
    s = cs.get("M5x30").summary()
    assert s["skill"] < 0.03
    assert abs(cs.prob("M5x30", 0.9) - 0.5) < 0.08
    assert abs(cs.prob("M5x30", 0.1) - 0.5) < 0.08
    assert s["k"] < 0.2                             # forecast moves shrunk to almost nothing
    assert not s["beats_coin"]
    assert abs(s["hit_rate"] - 0.5) <= s["coin_band"] + 0.02


def test_rolling_window_keeps_last_samples():
    c = kc.Calibrator("x", max_n=50)
    for u, m, r in noise(120):
        c.add(u, m, r)
    assert len(c.live) == 50


def test_persistence_round_trip(tmp_path):
    f = tmp_path / "calib.json"
    a = kc.CalibSet(file=f, prior_file=None)
    fill(a, "M1x30", informative(120))
    fill(a, "M5x30", noise(80))
    a.save()
    b = kc.CalibSet(file=f, prior_file=None)
    for name in ("M1x30", "M5x30"):
        sa, sb = a.get(name).summary(), b.get(name).summary()
        assert sb["n_live"] == sa["n_live"]
        for k in ("a", "b", "k", "bias", "skill", "brier_skill"):
            assert sb[k] == pytest.approx(sa[k], abs=1e-6), (name, k)
    assert b.prob("M1x30", 0.8) == pytest.approx(a.prob("M1x30", 0.8))


def test_missing_and_corrupt_files(tmp_path):
    kc.CalibSet(file=tmp_path / "nope" / "calib.json", prior_file=tmp_path / "nope.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    c = kc.CalibSet(file=bad, prior_file=bad)
    assert c.get("M1x30").summary()["n"] == 0
    bad.write_text(json.dumps({"sources": {"M1x30": {"samples": [["x", 1, 2, 0.5], [0.6, 1.0, 1.0, 0.5, 7]]}}}))
    c = kc.CalibSet(file=bad, prior_file=None)
    assert c.get("M1x30").summary()["n_live"] == 1   # the broken sample is skipped


def test_offline_prior_seeds_and_fades(tmp_path):
    pf = tmp_path / "prior.json"
    kc.write_prior(pf, "M1x30", [[u, m, r, 2000.0] for u, m, r in informative(400)], {"source": "test"})
    kc.write_prior(pf, "M5x30", [[u, m, r, 2000.0] for u, m, r in noise(100)])
    d = json.loads(pf.read_text())
    assert set(d["sources"]) == {"M1x30", "M5x30"}            # merged, not overwritten
    cs = kc.CalibSet(file=None, prior_file=pf)
    s = cs.get("M1x30").summary()
    assert s["n_prior"] == 400 and s["n_live"] == 0
    assert s["n"] == pytest.approx(kc.PRIOR_CAP, abs=1)      # the prior counts as at most PRIOR_CAP samples
    assert s["skill"] > 0.2 and cs.prob("M1x30", 0.8) > 0.7
    fill(cs, "M1x30", noise(kc.MAX_N, seed=9))               # live data says no skill: the prior fades
    s2 = cs.get("M1x30").summary()
    assert s2["n"] == pytest.approx(kc.MAX_N + kc.PRIOR_CAP * kc.PRIOR_FLOOR, abs=1)
    assert cs.prob("M1x30", 0.8) < 0.65


def test_blend_weights_favour_the_skilled_source():
    cs = kc.CalibSet(file=None, prior_file=None)
    fill(cs, "M1x30", informative(200))
    fill(cs, "M5x30", noise(200))
    w = cs.weights(["M1x30", "M5x30"], {"M1x30": 1.5, "M5x30": 1.0})
    assert w["M1x30"] > 0.85 and sum(w.values()) == pytest.approx(1.0)
    cs2 = kc.CalibSet(file=None, prior_file=None)
    fill(cs2, "M1x30", noise(200))
    fill(cs2, "M5x30", informative(200))
    w2 = cs2.weights(["M1x30", "M5x30"], {"M1x30": 1.5, "M5x30": 1.0})
    assert w2["M5x30"] > 0.8                       # skill beats the M1 prior
    assert kc.blend_weights({"M1": 0.0, "M5": 0.0}, ks.M30_PRIOR) == pytest.approx({"M1": 0.6, "M5": 0.4})
    assert kc.blend_weights({"M1": 0.1, "M5": None}, ks.M30_PRIOR) == {"M1": 1.0}


# --------------------------------------------------------------------------------------------------------------
# blend30: the pure 30-minute blend

def m1_res(t, last, slope, up=0.7):
    rows = [[t - 60 * i, last, last + 1, last - 1, last, 1.0] for i in range(20, -1, -1)]
    paths = [[last + slope * (i + 1) + d for i in range(30)] for d in (-0.5, 0.0, 0.5)]
    r = ks.from_paths(rows, paths, 60)
    r.update(up_prob=up, seconds=2.0, model="fake")
    return r


def m5_res(t5, last, slope, up=0.6):
    rows = [[t5 - 300 * i, last, last + 2, last - 2, last, 1.0] for i in range(20, -1, -1)]
    paths = [[last + slope * (i + 1) + d for i in range(6)] for d in (-1.0, 0.0, 1.0)]
    r = ks.from_paths(rows, paths, 300)
    r.update(up_prob=up, seconds=1.0, model="fake5")
    return r


def test_blend30_schema_and_grid():
    t = 1_700_000_000 - 1_700_000_000 % 300 + 240          # an M1 bar that ends an M5 bar
    m1 = m1_res(t, 2000.0, 0.1)
    m5 = m5_res(t - 240, 2000.0, 1.0)                     # same last close, +$1 per M5 bar
    out = ks.blend30(m1, m5, None, t, 2000.0)
    for k in ("t", "last", "minutes", "path", "band", "up_prob_raw", "up_prob", "move_raw", "move", "dir", "call",
              "confidence", "sources", "weights", "calibration", "model", "seconds"):
        assert k in out, k
    assert out["minutes"] == 30 and len(out["path"]) == 30 and len(out["band"]) == 30
    assert [p["time"] for p in out["path"]] == [t + 60 * (i + 1) for i in range(30)]
    assert out["weights"] == {"M1": 0.6, "M5": 0.4}
    # M1 alone ends +3.0, M5 alone +6.0 over 30 minutes: 0.6 * 3 + 0.4 * 6 = 4.2
    assert out["move_raw"] == pytest.approx(4.2, abs=0.02)
    # halfway: M1 +1.5, M5 interpolated +3.0
    assert out["path"][14]["value"] == pytest.approx(2000 + 0.6 * 1.5 + 0.4 * 3.0, abs=0.02)
    assert out["up_prob_raw"] == pytest.approx(0.6 * 0.7 + 0.4 * 0.6, abs=1e-3)
    assert out["call"] == "UP" and out["dir"] == 1 and out["agree"]
    for b, p in zip(out["band"], out["path"]):
        assert b["lo"] <= b["p25"] <= b["p75"] <= b["hi"] and b["lo"] <= p["value"] <= b["hi"]
    assert set(out["sources"]) == {"M1", "M5"}


def test_blend30_reanchors_an_older_m5_and_drops_a_stale_one():
    t5 = 1_700_000_100 - 1_700_000_100 % 300
    m5 = m5_res(t5, 2000.0, 1.0)
    t = t5 + 240 + 120                                    # two minutes after the M5 bar closed
    m1 = m1_res(t, 2005.0, 0.0)
    out = ks.blend30(m1, m5, None, t, 2005.0)
    assert out["sources"]["M5"]["age_s"] == 120
    # the M5 path is used from now on (its change over the next 30 minutes), not its old price level
    assert out["path"][0]["value"] == pytest.approx(2005.0 + 0.4 * 0.2, abs=0.02)
    late = ks.blend30(m1_res(t5 + 300 * 4, 2005.0, 0.0), m5, None)
    assert set(late["sources"]) == {"M1"} and late["weights"] == {"M1": 1.0}
    assert ks.blend30(None, None) is None


def test_blend30_with_calibration_shrinks_a_no_skill_source():
    cs = kc.CalibSet(file=None, prior_file=None)
    fill(cs, "M1x30", noise(300))
    fill(cs, "M5x30", noise(300, seed=5))
    t = 1_700_000_040
    out = ks.blend30(m1_res(t, 2000.0, 0.3, up=0.95), None, cs, t, 2000.0)
    assert out["up_prob_raw"] == 0.95
    assert abs(out["up_prob"] - 0.5) < 0.1                 # no measured skill: about a coin
    assert abs(out["move"]) < abs(out["move_raw"]) * 0.3
    assert out["call"] == "FLAT"
    assert out["calibration"]["M1x30"]["n"] == 300


# --------------------------------------------------------------------------------------------------------------
# KronosWorker 30-minute path with a fake model and a fake hub

class FakeBars:
    def __init__(self, rows):
        self.t = [r[0] for r in rows]
        self.o = [r[1] for r in rows]
        self.h = [r[2] for r in rows]
        self.l = [r[3] for r in rows]
        self.c = [r[4] for r in rows]
        self.v = [r[5] for r in rows]

    def __len__(self):
        return len(self.t)


class FakeSrc:
    """M1 random walk; `now` is the index of the forming M1 bar."""

    def __init__(self, n=1100, seed=3):
        rnd = random.Random(seed)
        t0, p = 1_700_000_000 - 1_700_000_000 % 300, 2000.0
        self.m1 = []
        for i in range(n):
            o = p
            p += rnd.gauss(0, 0.6)
            self.m1.append([t0 + 60 * i, o, max(o, p) + 0.1, min(o, p) - 0.1, p, 10.0])
        self.now = 600                      # 120 M5 bars: enough for the 100-bar minimum

    def agg(self, rows, sec):
        out = []
        for t, o, h, l, c, v in rows:
            k = t - t % sec
            if out and out[-1][0] == k:
                b = out[-1]
                b[2], b[3], b[4], b[5] = max(b[2], h), min(b[3], l), c, b[5] + v
            else:
                out.append([k, o, h, l, c, v])
        return out

    def rates(self, tf, n):
        rows = self.m1[:self.now + 1]
        if tf == "M5":
            rows = self.agg(rows, 300)
        return FakeBars(rows[-n:])

    def close_at(self, tf, t):
        """Close of the bar opening at t (the future, which the fake model may peek at)."""
        sec = ks.M30[tf][0]
        bars = self.m1 if tf == "M1" else self.agg(self.m1, 300)
        idx = {b[0]: b[4] for b in bars}
        return idx.get(t, bars[-1][4]) if t < bars[-1][0] + sec else bars[-1][4]


class FakeKronos:
    """Stands in for kronos_signal.Kronos: cheats by reading the future of the fake source (so it is perfectly
    informative), plus noise between paths."""
    size, name, ft, lookback, min_atr = "fake", "Kronos-fake", None, 200, 0.5

    def __init__(self, src):
        self.src, self.calls = src, []
        self.rnd = random.Random(7)

    def forecast(self, rows, step=60, paths=0, horizon=0):
        tf = "M1" if step == 60 else "M5"
        self.calls.append((tf, rows[-1][0], paths, horizon))
        fut = [self.src.close_at(tf, rows[-1][0] + step * (i + 1)) for i in range(horizon)]
        ps = [[v + self.rnd.gauss(0, 0.05) for v in fut] for _ in range(max(2, paths))]
        return ks.from_paths(rows[-self.lookback:], ps, step, self.min_atr)


class FakeHub:
    def __init__(self, src):
        self.lock = threading.RLock()
        self.src, self.engine, self.tf, self.sec = src, None, "M5", 300
        self.got = []

    def on_kronos(self, fc):
        pass

    def on_kronos30(self, fc):
        self.got.append(fc)


def test_worker_30min_path_with_fake_model():
    src = FakeSrc()
    hub = FakeHub(src)
    model = FakeKronos(src)
    cs = kc.CalibSet(file=None, prior_file=None)
    w = ks.KronosWorker(hub, model=model, calib=cs, track=ks.Track(file=None), autostart=False, ft_dir=False)
    assert w.step30() is True
    assert w.step30() is False                             # no new closed bar: nothing runs, nothing queues
    assert {c[0] for c in model.calls} == {"M1", "M5"}
    assert ("M1", src.m1[src.now - 1][0], 16, 30) in model.calls
    m5_call = [c for c in model.calls if c[0] == "M5"][0]
    assert m5_call[2:] == (12, 6)
    m30 = w.m30
    assert m30 is hub.got[-1] and m30["t"] == src.m1[src.now - 1][0] and len(m30["path"]) == 30
    st = w.state()
    assert st["m30"] is m30 and "M1x30" in st["track"] and "M5x30" in st["track"] and "M30" in st["track"]
    assert st["m30_models"]["M1"]["model"] == "Kronos-fake" and st["m30_models"]["M1"]["fine_tuned"] is None

    for _ in range(260):                                   # 260 more minutes, one closed M1 bar at a time
        src.now += 1
        w.step30()
    summ = cs.summary()
    assert summ["M1x30"]["n_live"] >= 200                  # every minute's forecast scored after its 30 minutes
    assert summ["M5x30"]["n_live"] >= 40
    assert summ["M30"]["n_live"] >= 200
    assert summ["M1x30"]["skill"] > 0.2                    # the cheating fake is informative ...
    assert summ["M1x30"]["hit_rate"] > 0.9
    m30 = w.m30
    assert m30["calibration"]["M1x30"]["skill"] > 0.2
    tr = w.state()["track"]["M1x30"]
    assert tr["resolved"] >= 200 and tr["direction_pct"] > 90
    assert m30["call"] in ("UP", "DOWN", "FLAT") and 0 <= m30["confidence"] <= 1
    assert math.isclose(sum(m30["weights"].values()), 1.0, abs_tol=0.01)


def test_ft_path_lookup(tmp_path, monkeypatch):
    monkeypatch.delenv("GOLDDESK_KRONOS_FT_M1", raising=False)
    monkeypatch.delenv("GOLDDESK_KRONOS_FT_M5", raising=False)
    monkeypatch.setattr(ks.Path, "home", classmethod(lambda cls: tmp_path))
    assert ks.ft_path("M1") is None
    d = tmp_path / ".golddesk" / "kronos_ft_M1"
    d.mkdir(parents=True)
    (d / "config.json").write_text("{}")
    assert ks.ft_path("M1") is None                        # no weights yet
    (d / "model.safetensors").write_bytes(b"x")
    assert ks.ft_path("M1") == d
    assert ks.ft_path("M1", False) is None
    monkeypatch.setenv("GOLDDESK_KRONOS_FT_M1", "off")
    assert ks.ft_path("M1") is None
    other = tmp_path / "other"
    other.mkdir()
    (other / "config.json").write_text("{}")
    (other / "pytorch_model.bin").write_bytes(b"x")
    assert ks.ft_path("M5", {"M5": str(other)}) == other


# --------------------------------------------------------------------------------------------------------------
# kronos_signal.Kronos glue (fine-tuned variant, batched sample paths) with stub torch / pandas / Kronos modules

def stub_kronos(monkeypatch, tmp_path):
    import types
    repo = tmp_path / "Kronos"
    (repo / "model").mkdir(parents=True)
    (repo / "model" / "kronos.py").write_text("")

    class Loaded:
        def __init__(self, name):
            self.name = name

        @classmethod
        def from_pretrained(cls, name):
            return cls(name)

    class Predictor:
        def __init__(self, model, tokenizer, device=None, max_context=512):
            self.model, self.tok, self.device, self.ctx = model, tokenizer, device or "cpu", max_context

        def predict_batch(self, dfs, xs, ys, pred_len, **kw):
            out = []
            for j, df in enumerate(dfs):
                last = df.rows[-1][3]
                vals = [last + (j % 4 - 1.5) * 0.1 * (i + 1) for i in range(pred_len)]
                out.append({"close": types.SimpleNamespace(values=vals)})
            return out

    mod = types.ModuleType("model")
    mod.Kronos = type("KModel", (Loaded,), {})
    mod.KronosTokenizer = type("KTok", (Loaded,), {})
    mod.KronosPredictor = Predictor
    pd = types.ModuleType("pandas")
    pd.DataFrame = lambda rows, columns=None: types.SimpleNamespace(rows=rows, columns=columns)
    pd.Series = lambda x: list(x)
    pd.to_datetime = lambda x, unit=None: list(x)
    torch = types.ModuleType("torch")
    torch.set_num_threads = lambda n: None
    monkeypatch.setitem(sys.modules, "model", mod)
    monkeypatch.setitem(sys.modules, "pandas", pd)
    monkeypatch.setitem(sys.modules, "torch", torch)
    return repo


def test_kronos_variant_and_paths_batch_with_stubs(monkeypatch, tmp_path):
    repo = stub_kronos(monkeypatch, tmp_path)
    monkeypatch.setattr(ks.Path, "home", classmethod(lambda cls: tmp_path))   # no real ~/.golddesk/kronos_ft_*
    monkeypatch.delenv("GOLDDESK_KRONOS_FT_M1", raising=False)
    monkeypatch.delenv("GOLDDESK_KRONOS_FT_M5", raising=False)
    k = ks.Kronos(repo, "small", lookback=50, horizon=6)
    assert k.name == "Kronos-small" and k.ft is None and k.lookback == 50
    assert k.pred.model.name == "NeoQuasar/Kronos-small"
    ft = tmp_path / "ft"
    ft.mkdir()
    (ft / "config.json").write_text("{}")
    (ft / "model.safetensors").write_bytes(b"x")
    (ft / ks.FT_META).write_text(json.dumps({"size": "small", "tf": "M1", "tokenizer": "tok-ft"}))
    v = k.variant(ft)
    assert v.ft == ft and v.pred.model.name == str(ft) and v.pred.tok.name == "tok-ft"
    assert "fine-tuned" in v.name and k.ft is None and k.pred.model.name == "NeoQuasar/Kronos-small"
    rows = [[1_700_000_000 + 60 * i, 2000.0, 2000.5, 1999.5, 2000.0 + i * 0.01, 1.0] for i in range(80)]
    out = k.forecast_paths_batch([rows, rows[:-5]], step=60, paths=4)
    assert len(out) == 2 and all(o["samples"] == 4 and len(o["path"]) == 6 for o in out)
    assert out[0]["up_prob"] == 0.5 and out[0]["t"] == rows[-1][0] and out[1]["t"] == rows[-6][0]
    one = k.forecast(rows, step=60, paths=4, horizon=30)
    assert len(one["path"]) == 30 and len(one["band"]) == 30 and one["minutes"] == 30
    with pytest.raises(RuntimeError):
        ks.Kronos(repo, "small", ft_dir=tmp_path / "missing")
    w = ks.KronosWorker(FakeHub(FakeSrc()), model=k, ft_dir={"M1": str(ft)}, calib=kc.CalibSet(None, None),
                        track=ks.Track(file=None), autostart=False)
    assert w._model30("M1").ft == ft and w._model30("M5") is k
    st = w.state()["m30_models"]
    assert st["M1"]["fine_tuned"] == str(ft) and st["M5"]["fine_tuned"] is None
