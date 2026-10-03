"""Session models (quant_sessions.py, quant_sessions_train.py, QuantModel's "sessions"), after Solomon Eshun's
Quantitative-XAUUSD-Strategy (MIT), on synthetic H1 candles: the London clock with daylight saving, the server-
clock fix, no lookahead and no Asia / London overlap, a planted Asia -> London effect found, noise not, and the
live forecasts equal to the training side's."""
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

np = pytest.importorskip("numpy")

import quant  # noqa: E402
import quant_features as qf  # noqa: E402
import quant_sessions as qs  # noqa: E402
import quant_sessions_train as qst  # noqa: E402
from engine import Bars, ny7_offset  # noqa: E402

T0 = 1_640_995_200          # 2022-01-01 00:00 UTC


def ts(s: str) -> int:
    return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp())


def make_h1(years=3.0, seed=1, kappa=0.0, t0=T0, sigma=0.0025):
    """Gold-like H1 candles in UTC. kappa > 0 plants Hypothesis A: during London (08:00-17:00 London time) price
    drifts the way it moved since the Asian session opened (22:00 London the evening before)."""
    rnd = random.Random(seed)
    b, p, asia = Bars(3600), 1800.0, None
    for k in range(int(years * 365 * 24)):
        t = t0 + 3600 * k
        d, m = (t // 86400 + 3) % 7, t % 86400 // 60
        if d == 5 or (d == 6 and m < 22 * 60) or (d == 4 and m >= 21 * 60):
            continue
        lm, day = (t + qs.london_offset(t)) % 86400 // 60, qs.london_day(t)
        o, drift = p, 0.0
        if lm >= 22 * 60 or lm < 8 * 60:
            key = day + (1 if lm >= 22 * 60 else 0)
            if asia is None or asia[0] != key:
                asia = (key, o)
        elif lm < 17 * 60 and kappa and asia and asia[0] == day:
            drift = kappa * sigma * p * (1 if p > asia[1] else -1)
        p = p + drift + rnd.gauss(0, sigma) * p
        b.append(t, round(o, 2), round(max(o, p) * (1 + abs(rnd.gauss(0, sigma / 2))), 2),
                 round(min(o, p) * (1 - abs(rnd.gauss(0, sigma / 2))), 2), round(p, 2), 100)
    return b


def shifted(b, off):
    out = Bars(b.sec)
    for i in range(len(b)):
        out.append(b.t[i] + off(b.t[i]), b.o[i], b.h[i], b.l[i], b.c[i], b.v[i])
    return out


@pytest.fixture(scope="module")
def planted(tmp_path_factory):
    h1 = make_h1(years=3, seed=4, kappa=0.35)
    ds = qst.build(h1, log=None)
    sb = ds.pop("_builder")
    out = {"version": 1, "model": qst.MODEL_NAME, "sessions": {}}
    res = {}
    for key, hyp in (("london", "A"), ("ny", "C")):
        d = ds[hyp]
        split = int(d["day"][int(len(d["day"]) * 0.8)])
        ent, ex = qst.train_hyp(d, split, folds=3, grid=qst.SGRID[:1], max_trees=150, log=None)
        ent["hypothesis"], ent["session"] = hyp, qs.SESSIONS[key][0]
        out["sessions"][key] = ent
        res[hyp] = (ent, ex)
    path = tmp_path_factory.mktemp("s") / "quant_sessions.json"
    path.write_text(json.dumps(out))
    return {"h1": h1, "ds": ds, "sb": sb, "res": res, "path": path}


# ------------------------------------------------------------------ the clock
def test_london_clock_and_windows():
    assert qs.eu_dst_bounds(2025) == (ts("2025-03-30T01:00"), ts("2025-10-26T01:00"))
    assert qs.eu_dst_bounds(2026) == (ts("2026-03-29T01:00"), ts("2026-10-25T01:00"))
    summer, winter = qs.london_day(ts("2026-07-15T12:00")), qs.london_day(ts("2026-01-15T12:00"))
    assert qs.london_to_utc(summer, 480) == ts("2026-07-15T07:00")       # London opens 07:00 UTC in summer
    assert qs.london_to_utc(winter, 480) == ts("2026-01-15T08:00")
    assert qs.window(summer, qs.ASIA) == (ts("2026-07-14T21:00"), ts("2026-07-15T07:00"))
    assert qs.window(winter, qs.NEWYORK) == (ts("2026-01-15T17:00"), ts("2026-01-15T22:00"))
    assert qs.hhmm(ts("2026-07-15T07:00")) == "08:00"
    fri_night = ts("2026-07-17T21:30")                                   # after Friday's New York session
    for key in qs.SESSIONS:
        d = qs.instance(key, fri_night)
        assert qs.weekday(d) == 0                                         # next one is Monday's
    assert qs.instance("london", ts("2026-07-15T10:00")) == summer        # running: today's


def test_no_lookahead_and_no_overlap(planted):
    """Inputs for day D use only candles closed when the target starts: cutting history there changes nothing,
    and the Asian window ends where London begins (the original's 07:00 UTC summer overlap is gone)."""
    h1, sb = planted["h1"], planted["sb"]
    days = [d for d in sb.days() if sb.vector(d, "A") is not None][300:330]
    for d in days:
        for hyp in ("A", "B", "C"):
            start = qs.window(d, qs.SESSIONS[qs.HYP_KEY[hyp]][2])[0]
            cut = qf.bisect_left(h1.t, start)
            short = qs.SessionBuilder(qf.sub(h1, 0, cut))
            assert short.vector(d, hyp) == sb.vector(d, hyp)
        assert qs.window(d, qs.ASIA)[1] == qs.window(d, qs.LONDON)[0]


def test_server_clock_is_read_as_utc_only_through_utc(planted):
    """MT5 candles on a New York + 7 server clock give the UTC features once utc(t) is applied; reading server
    time as UTC (the original's bug) shifts the sessions and changes them."""
    h1, sb = planted["h1"], planted["sb"]
    srv = shifted(h1, ny7_offset)
    good = qs.SessionBuilder(srv, utc=lambda t: t - ny7_offset(t))
    bad = qs.SessionBuilder(srv)
    days = [d for d in sb.days() if sb.vector(d, "A") is not None][200:260:7]
    same = diff = 0
    for d in days:
        a = sb.vector(d, "A")
        g = good.vector(d, "A")
        assert g == pytest.approx(a, abs=1e-9)
        assert good.target(d, "A") == sb.target(d, "A")
        b = bad.vector(d, "A")
        same += b is not None and b == pytest.approx(a, abs=1e-9)
        diff += b is None or b != pytest.approx(a, abs=1e-9)
    assert diff == len(days) and same == 0


# ------------------------------------------------------------------ training
def test_planted_asia_london_beats_coin_and_noise_does_not(planted):
    ent, _ = planted["res"]["A"]
    met = ent["metrics"]
    assert ent["beats_coin"] is True and met["brier_skill"] > 0.05 and met["acc_low95"] > 0.5
    assert met["importance"][0]["feature"] in ("asia_ret_pct", "asia_pos", "rs_asia")
    ent_c, _ = planted["res"]["C"]                                        # nothing planted for New York
    assert ent_c["beats_coin"] is False
    h1 = make_h1(years=3, seed=5, kappa=0.0)
    d = qst.build(h1, log=None)["A"]
    split = int(d["day"][int(len(d["day"]) * 0.8)])
    ent_n, _ = qst.train_hyp(d, split, folds=3, grid=qst.SGRID[:1], max_trees=150, log=None)
    assert ent_n["beats_coin"] is False and abs(ent_n["metrics"]["brier_skill"]) < 0.05
    for k in ("years", "trade_all", "buy_hold", "auc", "reg_corr"):
        assert k in met


def test_original_inputs_and_reference_loader_degrade_gracefully(planted, tmp_path):
    sb = planted["sb"]
    d = [x for x in sb.days() if sb.original(x, "A") is not None][100]
    o = sb.original(d, "A")
    assert list(o) == list(qs.ORIGINAL)
    assert o["asia_range"] > 0 and 0 < o["rsi_at_asia_close"] < 100     # dollars, unscaled, as the original
    assert set(sb.original(d, "C")) == set(qs.ORIGINAL) | set(qs.ORIGINAL_EXTRA["C"])
    rows = qst.reference_models(str(tmp_path), sb, [d], np.array([1.0]))
    assert len(rows) == 1 and "note" in rows[0]                           # no models there / no xgboost: a note


# ------------------------------------------------------------------ live
def test_live_sessions_match_training_side(planted, tmp_path):
    h1, sb = planted["h1"], planted["sb"]
    qm = quant.QuantModel(tmp_path / "none.json", sessions_path=planted["path"])
    off = 3 * 3600
    srv = lambda b: shifted(b, lambda t: off)
    checked = 0
    for when in ("2024-09-10T06:30", "2024-09-10T09:10", "2024-09-10T18:30", "2024-09-13T22:30"):
        now = ts(when)
        j = qf.bisect_right([t + 3600 for t in h1.t], now) - 1
        m1 = Bars(60)
        m1.append(now - 60, h1.c[j], h1.c[j], h1.c[j], h1.c[j])
        r = qm.predict({"M1": srv(m1), "H1": srv(qf.sub(h1, j + 1 - 800, j + 1))}, lambda t: t - off)
        assert r["ok"] is False and "quant_train.py" in r["status"]        # no 30-minute model here
        assert set(r["sessions"]) == {"london", "overlap", "ny"}
        for key, e in r["sessions"].items():
            assert set(e) == {"session", "hypothesis", "known_at", "starts", "ends", "up_prob", "move_pct", "dir",
                              "call", "skill", "status"}
            assert e["ends"] > now and e["known_at"] == e["starts"]
            if key == "overlap":
                assert "not trained" in e["status"] or "no London / New York overlap model" in e["status"]
                continue
            assert set(e["skill"]) == {"accuracy", "acc_low95", "brier_skill", "n_test", "beats_coin", "test_period"}
            if now < e["known_at"]:
                assert e["call"] == "WAIT" and e["up_prob"] is None and e["status"].startswith("waits for")
            else:
                ent = qm.sm[key]
                v = sb.vector(qs.london_day(e["known_at"]), e["hypothesis"])
                a, b = ent["platt"]
                p = min(0.99, max(0.01, quant.sigmoid(a * ent["cls"].margin(v) + b)))
                assert e["up_prob"] == pytest.approx(p, abs=1e-4) and e["call"] in ("UP", "DOWN", "FLAT")
                checked += 1
    assert checked >= 2
    r = qm.predict({"M1": srv(m1), "H1": srv(qf.sub(h1, j + 1 - 800, j + 1))}, lambda t: t - off)
    assert r["sessions"]["london"]["status"].startswith("waits for the Asian session to end at 08:00 London")
