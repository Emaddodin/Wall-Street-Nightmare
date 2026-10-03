"""The chart's brain: reads every closed candle with every tool Gold Desk has and says what to do, in words.

At each closed M1 / M5 candle (playbook.Playbook.update calls `read_candle` and `decide`):

  read      what this candle did, in ICT terms and as a candlestick: swept a level (which one), CISD, MSS / BOS /
            CHoCH, left an FVG with displacement, inverted an FVG, broke an order block into a breaker, touched a
            higher-timeframe key level, and its candlestick pattern (engulfing, hammer, morning star ...).
  decide    one answer for right now, in this order:
              MARKET CLOSED / WAIT (news)
              BUY NOW / SELL NOW    a model's entry candle just closed (playbook trigger), graded A or A+, nothing
                                    required failing, Kronos' next 30 minutes not against it
              DON'T                 a model triggered but something says no (grade, Kronos, a required check)
              GET READY             a setup is armed and price is at its zone: the next close decides
              WAIT FOR ...          a setup is armed further away, or a sweep happened and the shift is missing
                                    (wait for a CISD beyond <level>), or nothing is set up (where to look, when)
  line      the one forecast line for the chart, 30 minutes ahead from the live price: the plan of the best ICT
            setup (pull back to its zone, then away toward its liquidity) blended with Kronos' calibrated
            30-minute path, each by its conviction. When they point opposite ways the line goes flat and says so.
  talk      the four phases of the notes in sentences, the candle's read and the answer.

Descriptive and untested: the playbook's track record (playbook_backtest.py and the live record) says how each
model did. Stops and targets stay in the setup for the record; the page leads with the read and the action.
"""
from __future__ import annotations

import math

import candles as cdl
from ictlib import is_disp

LINE_MIN = 30


def _px(x) -> str:
    return f"{x:.2f}" if isinstance(x, (int, float)) else "-"


def _side(d: int) -> str:
    return "BUY" if d == 1 else "SELL"


# ====================================================================== one candle
def read_candle(ctx, tf: str) -> dict | None:
    """What the last closed candle of tf did."""
    T = ctx.tapes.get(tf)
    if not T or T.n < 5:
        return None
    b, j = T.b, T.n - 1
    ev, lean = [], 0.0

    def add(text, d=0, w=1.0, kind=""):
        nonlocal lean
        ev.append({"text": text, "dir": d, "kind": kind})
        lean += d * w

    for w in T.sweeps:
        if w["i"] == j:
            add(f"swept {w['kind']} {_px(w['level'])} and closed back ({'sell-side' if w['dir'] == 1 else 'buy-side'} "
                f"liquidity taken)", w["dir"], 1.5, "sweep")
    for c in T.cisd:
        if c["i"] == j:
            add(f"CISD {'up' if c['dir'] == 1 else 'down'}: closed beyond {_px(c['ref'])}, the open of the run into "
                f"the {'low' if c['dir'] == 1 else 'high'}", c["dir"], 1.5, "cisd")
    for br in T.breaks:
        if br["i"] == j or br.get("known") == j:
            add(f"{br['kind']} {'up' if br['dir'] == 1 else 'down'} through {_px(br['level'])}"
                + (" with displacement" if br["disp"] else ""), br["dir"], 2.0 if br["kind"] == "MSS" else 1.0, "structure")
    for g in T.fvgs:
        if g["born"] == j:
            add(f"left a {'bullish' if g['dir'] == 1 else 'bearish'} FVG {_px(g['bottom'])}-{_px(g['top'])} "
                f"(CE {_px(g['ce'])})" + (" from a displacement candle" if g.get("disp") else ""), g["dir"], 0.7, "fvg")
    for x in T.ifvgs:
        if x["born"] == j:
            add(f"closed through an FVG: {_px(x['bottom'])}-{_px(x['top'])} is now an IFVG "
                f"{'support' if x['dir'] == 1 else 'resistance'}", x["dir"], 1.5, "ifvg")
    for z in T.obs:
        if z["kind"] == "BB" and z["born"] == j:
            add(f"broke an order block: {_px(z['bottom'])}-{_px(z['top'])} is now a "
                f"{'bullish' if z['dir'] == 1 else 'bearish'} breaker", z["dir"], 1.0, "breaker")
    if is_disp(b, j, T.atr):
        d = 1 if b.c[j] > b.o[j] else -1
        add(f"displacement candle ({'up' if d == 1 else 'down'}, body {abs(b.c[j] - b.o[j]):.2f})", d, 0.7, "disp")
    lo_key = ctx.key_level_at(tf, b.l[j])
    hi_key = ctx.key_level_at(tf, b.h[j])
    if lo_key and b.c[j] > b.l[j] + 0.5 * (b.h[j] - b.l[j]):
        add(f"wicked into {lo_key} and closed away from it (rejection)", 1, 1.0, "key")
    elif hi_key and b.c[j] < b.h[j] - 0.5 * (b.h[j] - b.l[j]):
        add(f"wicked into {hi_key} and closed away from it (rejection)", -1, 1.0, "key")
    pats = cdl.at(b, j)
    pat = next((p for p in pats if p["bias"]), pats[0] if pats else None)
    if pat:
        add(f"{pat['name']}: {pat['meaning']}", pat["bias"], 0.6 if pat["bias"] else 0.0, "pattern")
    rng = b.h[j] - b.l[j]
    tone = "bullish" if lean >= 1 else ("bearish" if lean <= -1 else "neutral")
    if not ev:
        body = b.c[j] - b.o[j]
        ev.append({"text": f"{'up' if body > 0 else 'down' if body < 0 else 'flat'} candle, range {rng:.2f}, nothing new in ICT terms",
                   "dir": 0, "kind": "quiet"})
    return {"tf": tf, "time": b.t[j], "close_time": b.t[j] + b.sec, "o": b.o[j], "h": b.h[j], "l": b.l[j],
            "c": b.c[j], "events": ev, "lean": round(lean, 2), "tone": tone,
            "pattern": pat["name"] if pat and pat["bias"] else None, "pattern_bias": pat["bias"] if pat else 0}


# ====================================================================== the answer for right now
def decide(ctx, setups: list, ranking: list, reads: dict, market_open: bool = True) -> dict:
    """One action for right now and why, from the setups (with their candle-close triggers), the reads, the
    higher timeframes, the clock, SMT and Kronos."""
    rank = {r["model"]: k for k, r in enumerate(ranking)}
    order = lambda s: (rank.get(s["model"], 99), -s["score"])
    kv_text = lambda d: ctx.kronos_vote(d)[1]
    if not market_open:
        base = _wait_plan(ctx, setups, ranking)
        return dict(base, action="MARKET CLOSED", dir=0,
                    text="Gold is closed. When it opens: " + base["text"])
    if ctx.news:
        return {"action": "WAIT", "dir": 0, "level": None, "model": None,
                "text": "High-impact USD news within 15 minutes: no entries until it's out and a fresh setup forms.",
                "why": [], "checks": []}
    trig = [s for s in setups if s.get("trigger") and s["trigger"]["fresh"]]
    go = sorted([s for s in trig if s["entry_now"]["verdict"] == "ENTER"], key=order)
    if go:
        s = go[0]
        tr = s["trigger"]
        why = [f"{s['name']} on {s['tf']} ({s['grade']})"] + s["why"] + [tr["text"], kv_text(s["dir"])]
        others = [f"{x['name']} {x['tf']}" for x in go[1:3]]
        if others:
            why.append("also confirming: " + ", ".join(others))
        return {"action": f"{_side(s['dir'])} NOW", "dir": s["dir"], "level": tr["close"], "model": s["name"],
                "tf": tr["tf"], "grade": s["grade"], "setup_id": s["id"],
                "text": (f"{_side(s['dir'])} on this close ({tr['tf']} {_px(tr['close'])}): {s['name']}. "
                         f"{tr['text'][0].upper() + tr['text'][1:]}. {kv_text(s['dir'])}. "
                         f"Wrong if price {'trades below' if s['dir'] == 1 else 'trades above'} {_px(s['sl'])} "
                         f"(beyond the sweep)."),
                "why": why, "checks": s["entry_now"]["checks"]}
    no = sorted(trig, key=order)
    if no:
        s = no[0]
        return {"action": f"DON'T {_side(s['dir'])}", "dir": 0, "level": s["trigger"]["close"], "model": s["name"],
                "tf": s["trigger"]["tf"], "grade": s["grade"],
                "text": (f"{s['name']} on {s['tf']} just triggered a {_side(s['dir']).lower()}, but skip it: "
                         + "; ".join(s["entry_now"]["missing"]) + "."),
                "why": s["why"], "checks": s["entry_now"]["checks"]}
    armed = sorted([s for s in setups if s["status"] == "armed" and s["grade"] in ("A+", "A", "B")], key=order)
    for s in armed:
        T = ctx.tapes[s["tf"]]
        a = T.atr[-1] or 1.0
        z = s["zone"]
        dist = (T.b.c[-1] - z["top"]) if s["dir"] == 1 else (z["bottom"] - T.b.c[-1])
        if dist <= 0.5 * a:
            mid = (z["top"] + z["bottom"]) / 2
            return {"action": f"GET READY: {_side(s['dir'])}", "dir": s["dir"], "level": mid, "model": s["name"],
                    "tf": s["tf"], "grade": s["grade"],
                    "text": (f"Price is at the {s['name']} zone {_px(z['bottom'])}-{_px(z['top'])} on {s['tf']}. "
                             f"{_side(s['dir'])} when a {s['tf']} candle (or an M1 CISD inside it) closes back "
                             f"{'up above' if s['dir'] == 1 else 'down below'} {_px(mid)}. {kv_text(s['dir'])}."),
                    "why": s["why"], "checks": [{"label": c["label"], "ok": c["ok"], "key": c["key"]} for c in s["checks"]]}
    if armed:
        s = armed[0]
        z = s["zone"]
        return {"action": f"WAIT FOR {_side(s['dir'])}", "dir": s["dir"], "level": (z["top"] + z["bottom"]) / 2,
                "model": s["name"], "tf": s["tf"], "grade": s["grade"],
                "text": (f"{s['name']} on {s['tf']} is set up ({s['grade']}): wait for price to come back to "
                         f"{_px(z['bottom'])}-{_px(z['top'])}, then {s['dir'] == 1 and 'buy' or 'sell'} on a candle "
                         f"that closes back {'up' if s['dir'] == 1 else 'down'}. Don't chase it from here."),
                "why": s["why"], "checks": [{"label": c["label"], "ok": c["ok"], "key": c["key"]} for c in s["checks"]]}
    return _wait_plan(ctx, setups, ranking)


def _wait_plan(ctx, setups: list, ranking: list) -> dict:
    """No setup armed: what is half-built, where to look and when."""
    for tf in ("M1", "M5"):
        T = ctx.tapes.get(tf)
        if not T or not T.sweeps:
            continue
        w = T.sweeps[-1]
        age = T.n - 1 - w["i"]
        if age <= (8 if tf == "M1" else 4):
            d = w["dir"]
            shifted = any(c["dir"] == d and c["i"] >= w["i"] for c in T.cisd) or \
                any(x["dir"] == d and x["i"] >= w["i"] for x in T.breaks)
            if not shifted:
                ref = T.cisd_ref.get(d)
                agree = ctx.bias * d > 0.15
                return {"action": f"WATCH: {_side(d)}", "dir": d, "level": ref, "model": None, "tf": tf,
                        "text": (f"{tf} just swept {w['kind']} {_px(w['level'])} ({age} candle{'s' if age != 1 else ''} ago). "
                                 + (f"Wait for a CISD: a {tf} candle closing {'above' if d == 1 else 'below'} {_px(ref)}"
                                    if ref is not None else "Wait for a CISD / MSS")
                                 + f", then {'buy' if d == 1 else 'sell'} the retrace into the gap it leaves. "
                                 + ("Higher timeframes agree." if agree else "Higher timeframes don't agree: take it only with an A grade.")),
                        "why": [], "checks": []}
    b = ctx.bias
    d = 1 if b > 0.15 else (-1 if b < -0.15 else 0)
    H = ctx.tapes.get("H1") or ctx.tapes.get("M15")
    px = ctx.price
    nxt = (ctx.clock.get("next") or [None])[0]
    when = (f"in the {nxt['name']} at {nxt['start']} New York (in {nxt['in_min']} min)" if nxt else "in the next killzone")
    if ctx.clock.get("killzone") and not ctx.clock.get("lunch"):
        when = f"now, in the {ctx.clock['killzone']} killzone"
    if d and H:
        pool = H.erl(-d, px)
        lvl = pool[0]["price"] if pool else None
        text = (f"No setup yet. The higher timeframes lean {'up' if d == 1 else 'down'} ({ctx.bias_why}), so look for "
                f"{'buys' if d == 1 else 'sells'}: a sweep of {'sell' if d == 1 else 'buy'}-side liquidity"
                + (f" ({H.name} {'low' if d == 1 else 'high'} {_px(lvl)})" if lvl else "")
                + f" {when}, then a CISD / MSS on M1 or M5. Don't {'sell' if d == 1 else 'buy'} against it.")
        return {"action": "WAIT", "dir": 0, "level": lvl, "model": None, "text": text, "why": [], "checks": []}
    best = ranking[0]["name"] if ranking else "the playbook"
    return {"action": "WAIT", "dir": 0, "level": None, "model": None,
            "text": (f"No setup and no clear higher-timeframe bias ({ctx.bias_why}). Stand aside; the model that fits "
                     f"this market best is {best}. Look again {when}."), "why": [], "checks": []}


# ====================================================================== the one line
def desk_line(ict: dict, kronos30: dict | None, t: int, price: float, sigma: float | None,
              band30: list | None = None, digits: int = 2, quant: dict | None = None,
              trust: dict | None = None) -> dict | None:
    """30 minutes from the live price: the best ICT plan blended with Kronos' calibrated 30-minute path and the
    quant model's forecast. Each voice weighs by its conviction; a voice that hasn't beaten a coin on held-out
    data (quant) or on its live record (Kronos) is turned down. t: open time of the forming M1 candle."""
    if not ict or not sigma or price is None:
        return None
    sd30 = sigma * math.sqrt(LINE_MIN)
    dec = ict.get("decision") or {}
    plan = None
    for s in ict.get("setups") or []:
        if dec.get("setup_id") and s["id"] == dec["setup_id"]:
            plan = s
    if plan is None and dec.get("model") and dec.get("action", "").startswith(("GET READY", "WAIT FOR")):
        plan = next((s for s in ict.get("setups") or [] if s["name"] == dec["model"] and s["tf"] == dec.get("tf")), None)
    grid = [t + 60 * (m + 1) for m in range(LINE_MIN)]
    # ICT path
    if plan:
        d = plan["dir"]
        z = plan["zone"]
        lvl = (z["top"] + z["bottom"]) / 2
        live = dec.get("action", "").endswith("NOW")
        conv = plan["score"] * (1.0 if live else 0.6)
        tgt = plan["tp1"]
        reach = min(abs(tgt - price), 1.6 * sd30)
        if not live and (price - lvl) * d > 0:                        # first back to the zone, then away
            far = abs(price - lvl) + abs(tgt - lvl) or 1.0
            me = max(3, min(15, round(LINE_MIN * abs(price - lvl) / far)))
            goal = lvl + d * min(abs(tgt - lvl), 1.6 * sd30 * math.sqrt((LINE_MIN - me) / LINE_MIN))
            ict_p = [price + (lvl - price) * (m + 1) / me if m + 1 <= me else
                     lvl + (goal - lvl) * (m + 1 - me) / (LINE_MIN - me) for m in range(LINE_MIN)]
        else:
            goal = price + d * reach
            ict_p = [price + (goal - price) * math.sqrt((m + 1) / LINE_MIN) for m in range(LINE_MIN)]
        ict_dir, ict_name = d, f"{plan['name']} {plan['tf']} {plan['grade']}"
    else:
        b = ict.get("bias") or 0.0
        ict_dir = 1 if b > 0.15 else (-1 if b < -0.15 else 0)
        conv = 0.25 * abs(b)
        ict_p = [price + b * 0.4 * sd30 * (m + 1) / LINE_MIN for m in range(LINE_MIN)]
        ict_name = "higher-timeframe bias"
    # Kronos path, moved onto the live price
    k = kronos30 if kronos30 and kronos30.get("path") and abs((kronos30.get("t") or 0) + 60 - t) <= 600 else None
    if k:
        shift = price - k["last"]
        pts = {p["time"]: p["value"] + shift for p in k["path"]}
        kp, prev = [], price
        for x in grid:
            prev = pts.get(x, prev)
            kp.append(prev)
        cal = (k.get("calibration") or {}).get("M30") or (k.get("calibration") or {}).get("M1x30") or {}
        n, skill = cal.get("n") or 0, cal.get("skill")
        ktrust = 0.7 if n < 30 or skill is None else (0.4 if skill <= 0 else min(1.0, 0.7 + 3 * skill))
        kconv = (k.get("confidence") or 0.0) * ktrust
        kdir = k.get("dir") or 0
    else:
        kp, kconv, kdir = [price] * LINE_MIN, 0.0, 0
    q = quant if quant and quant.get("ok") and quant.get("up_prob") is not None else None
    if q:
        qmove = q.get("move") or 0.0
        qp = [price + qmove * math.sqrt((m + 1) / LINE_MIN) for m in range(LINE_MIN)]
        qconv = (q.get("confidence") or 0.0) * (1.0 if (q.get("skill") or {}).get("beats_coin") else 0.15)
        qdir = q.get("dir") or 0
    else:
        qp, qconv, qdir = [price] * LINE_MIN, 0.0, 0
    tr = trust or {}
    conv *= tr.get("Voice: ICT setup", 1.0)                         # earned in the knowledge mesh (mesh.py)
    kconv *= tr.get("Voice: Kronos 30 min", 1.0)
    qconv *= tr.get("Voice: Quant model", 1.0)
    anchor = 0.35
    tot = conv + kconv + qconv + anchor
    wi, wk, wq = conv / tot, kconv / tot, qconv / tot
    line = [price + wi * (a - price) + wk * (c - price) + wq * (e - price) for a, c, e in zip(ict_p, kp, qp)]
    votes = [x for x in (ict_dir, kdir, qdir) if x]
    if votes and min(votes) != max(votes):
        state = "CONFLICT"
    elif len(votes) >= 2:
        state = "AGREE"
    elif ict_dir:
        state = "ICT ONLY"
    elif votes:
        state = "MODELS ONLY"
    else:
        state = "FLAT"
    # band: Kronos' spread (or the measured 30-minute band) around the line
    band = []
    kb = {b["time"]: b for b in (k.get("band") or [])} if k else {}
    nb = {b["time"]: b for b in (band30 or [])}
    for m, x in enumerate(grid):
        src = kb.get(x) or nb.get(x)
        if src:
            c0 = (src["lo"] + src["hi"]) / 2
            lo, hi, q1, q3 = src["lo"] - c0, src["hi"] - c0, src["p25"] - c0, src["p75"] - c0
        else:
            s = sigma * math.sqrt(m + 1)
            lo, hi, q1, q3 = -1.645 * s, 1.645 * s, -0.674 * s, 0.674 * s
        v = line[m]
        band.append({"time": x, "lo": round(v + lo, digits), "hi": round(v + hi, digits),
                     "p25": round(v + q1, digits), "p75": round(v + q3, digits)})
    act = dec.get("action", "WAIT")
    up = k.get("up_prob") if k else None
    kword = (f"Kronos {'up' if (up or 0.5) >= 0.5 else 'down'} {max(up, 1 - up):.0%}" if up is not None else "Kronos off")
    qup = q.get("up_prob") if q else None
    qword = f"quant {'up' if qup >= 0.5 else 'down'} {max(qup, 1 - qup):.0%}" if qup is not None else "quant off"
    iword = f"ICT {'up' if ict_dir == 1 else 'down' if ict_dir == -1 else 'flat'}"
    label = {"CONFLICT": f"{act} · {iword} vs {kword} vs {qword}: they disagree, no trade",
             "AGREE": f"{act} · {ict_name} + {kword} + {qword}",
             "ICT ONLY": f"{act} · {ict_name} · {kword} · {qword}",
             "MODELS ONLY": f"{act} · no ICT setup · {kword} · {qword}",
             "FLAT": f"{act} · nothing pulls price either way"}[state]
    tip = line[-1]
    return {"t": t, "last": round(price, digits), "minutes": LINE_MIN,
            "path": [{"time": x, "value": round(v, digits)} for x, v in zip(grid, line)], "band": band,
            "target": round(tip, digits), "move": round(tip - price, digits),
            "dir": 1 if tip - price > 0.25 * sd30 else (-1 if tip - price < -0.25 * sd30 else 0),
            "state": state, "label": label, "action": act,
            "weights": {"ict": round(wi, 2), "kronos": round(wk, 2), "quant": round(wq, 2), "anchor": round(anchor / tot, 2)},
            "parts": {"ict": {"dir": ict_dir, "name": ict_name, "conviction": round(conv, 2)},
                      "kronos": {"dir": kdir, "up_prob": up, "conviction": round(kconv, 2)},
                      "quant": {"dir": qdir, "up_prob": qup, "conviction": round(qconv, 2)}},
            "note": "ICT plan + Kronos + quant model, blended by conviction. Scored live in the mesh as 'Desk line'; "
                    "not proven."}


# ====================================================================== the overall analysis
def overall(ict: dict | None, news: dict | None = None, quant: dict | None = None, kronos30: dict | None = None,
            smt: dict | None = None, market_open: bool = True, trust: dict | None = None) -> dict | None:
    """Everything at once: each voice's direction (-1 bearish .. +1 bullish), its weight and its words, the
    weighted verdict, how much the voices agree, what lowers confidence right now, and one paragraph."""
    if not ict:
        return None
    voices = []

    def add(name, v, w, text):
        tm = (trust or {}).get(f"Voice: {name}")                  # earned in the knowledge mesh: 0 .. 2
        if tm is not None:
            w *= tm
            text += f" [mesh trust x{tm:.2f}]"
        if v is None:
            voices.append({"name": name, "value": None, "weight": w, "text": text, "trust": tm})
            return
        voices.append({"name": name, "value": round(max(-1.0, min(1.0, v)), 2), "weight": w, "text": text, "trust": tm})

    dec = ict.get("decision") or {}
    act = dec.get("action", "WAIT")
    b = ict.get("bias") or 0.0
    add("Higher timeframes", b, 3.0, f"{'bullish' if b > 0.15 else 'bearish' if b < -0.15 else 'mixed'} "
                                     f"({ict.get('bias_why', '')})")
    strength = 1.0 if act.endswith("NOW") else (0.6 if act.startswith(("GET READY", "WAIT FOR")) else
                                               (0.4 if act.startswith("WATCH") else 0.0))
    add("ICT setup", (dec.get("dir") or 0) * strength, 3.0,
        f"{act}" + (f" · {dec['model']}" + (f" {dec.get('tf')}" if dec.get("tf") else "") if dec.get("model") else ""))
    tfs = ict.get("timeframes") or {}
    tr = [tfs[k]["trend"] for k in ("D1", "H4", "H1", "M15", "M5", "M1") if k in tfs]
    if tr:
        up, dn = sum(1 for x in tr if x > 0), sum(1 for x in tr if x < 0)
        add("Timeframe alignment", (up - dn) / len(tr), 1.5, f"{up} of {len(tr)} timeframes up, {dn} down")
    reads = ict.get("reads") or {}
    rd = reads.get("M1") or reads.get("M5")
    if rd:
        add("Last candle", rd["lean"] / 3.0, 1.0, f"{rd['tf']}: " + "; ".join(e["text"] for e in rd["events"][:2]))
    h1 = (tfs.get("H1") or {}).get("range")
    if h1 and abs(b) > 0.15:
        good = (b > 0 and h1["zone"] == "discount") or (b < 0 and h1["zone"] == "premium")
        add("Premium / discount", (1 if b > 0 else -1) * (0.6 if good else -0.4), 1.0,
            f"H1 price in {h1['zone']} ({h1['pos']:.0%}): {'good place' if good else 'poor place'} to "
            f"{'buy' if b > 0 else 'sell'}")
    k = kronos30
    if k and k.get("up_prob") is not None:
        cal = (k.get("calibration") or {}).get("M30") or (k.get("calibration") or {}).get("M1x30") or {}
        ktrust = 0.4 if (cal.get("n") or 0) >= 30 and (cal.get("skill") or 0) <= 0 else 1.0
        add("Kronos 30 min", (k["up_prob"] - 0.5) * 2 * ktrust, 2.0,
            f"{k.get('call', '?')}: up {k['up_prob']:.0%}, {k.get('move', 0):+.2f}"
            + (" (hasn't beaten a coin yet: turned down)" if ktrust < 1 else ""))
    else:
        add("Kronos 30 min", None, 2.0, "not running")
    q = quant
    if q and q.get("ok") and q.get("up_prob") is not None:
        beats = (q.get("skill") or {}).get("beats_coin")
        add("Quant model", (q["up_prob"] - 0.5) * 2 * (1.0 if beats else 0.2), 2.0,
            f"{q.get('call', '?')}: up {q['up_prob']:.0%}, {q.get('move', 0):+.2f}"
            + ("" if beats else " (didn't beat a coin out of sample: turned down)"))
    else:
        add("Quant model", None, 2.0, (q or {}).get("status") or "not trained")
    sm = (smt or {}).get("summary") if smt else None
    if sm and sm.get("score") is not None:
        add("SMT (silver)", sm["score"], 1.0, sm.get("note") or "")
    nw = news or {}
    if nw.get("bias") is not None and nw.get("status", "").startswith(("ok", "stale")):
        add("News", nw["bias"], 1.5, nw.get("bias_text") or "")
    else:
        add("News", None, 1.5, nw.get("status") or "news box off")
    ln = ict.get("line") or {}
    live = [v for v in voices if v["value"] is not None]
    tot = sum(v["weight"] for v in live) or 1.0
    score = sum(v["weight"] * v["value"] for v in live) / tot
    sign = 1 if score > 0 else -1
    agree = sum(v["weight"] for v in live if v["value"] * sign > 0.1) / tot
    against = [v["name"] for v in live if v["value"] * sign < -0.1]
    risks = []
    clock = ict.get("clock") or {}
    sess = ict.get("session") or {}
    conf = min(1.0, abs(score) * 1.6) * (0.5 + 0.5 * agree)
    if not market_open:
        risks.append("market closed")
        conf *= 0.5
    if nw.get("wait") or ict.get("news"):
        risks.append(nw.get("wait_text") or "high-impact news window")
        conf *= 0.3
    if sess.get("avoid"):
        risks.append(f"{sess.get('name')}: stand aside")
        conf *= 0.5
    elif not (clock.get("killzone") or clock.get("silver_bullet") or clock.get("macro")):
        risks.append("outside the killzones")
        conf *= 0.8
    if ln.get("state") == "CONFLICT":
        risks.append("ICT, Kronos and the quant model disagree")
        conf *= 0.7
    if (ict.get("regime") or {}).get("kind") == "range":
        risks.append("ranging market: favour reversals at the range edges, take quick targets")
    nxt = nw.get("next_event")
    if nxt and nxt.get("in_min") is not None and 0 <= nxt["in_min"] <= 60:
        risks.append(f"{nxt.get('title')} in {nxt['in_min']} min")
    verdict = "BULLISH" if score >= 0.25 else ("BEARISH" if score <= -0.25 else "MIXED")
    do = act if act.endswith("NOW") or act.startswith(("GET READY", "WAIT FOR", "WATCH", "DON'T", "MARKET")) else \
        ("WAIT" if verdict == "MIXED" else f"WAIT, lean {'buys' if score > 0 else 'sells'}")
    if (nw.get("wait") or ict.get("news")) and market_open:
        do = "WAIT (news)"
    parts = [f"Overall {verdict.lower()} ({score:+.2f}, confidence {conf:.0%})."]
    pro = [v for v in sorted(live, key=lambda v: -abs(v["weight"] * v["value"])) if v["value"] * sign > 0.1][:3]
    if pro and verdict != "MIXED":
        parts.append("For it: " + "; ".join(f"{v['name']} ({v['text']})" for v in pro) + ".")
    if against:
        parts.append("Against it: " + ", ".join(against) + ".")
    parts.append(dec.get("text") or "")
    if risks:
        parts.append("Careful: " + "; ".join(risks) + ".")
    return {"verdict": verdict, "score": round(score, 3), "confidence": round(conf, 2), "agree": round(agree, 2),
            "do": do, "voices": voices, "against": against, "risks": risks, "line_state": ln.get("state"),
            "text": " ".join(p for p in parts if p),
            "note": "A weighted read of every source, for orientation. Not a prediction anyone has proven."}


# ====================================================================== what is forming right now
STAGES = (("watch", 0.25, "liquidity about to be taken"), ("forming", 0.45, "sweep done, waiting for the shift"),
          ("set", 0.70, "set up, waiting for the pullback"), ("ready", 0.85, "price at the zone, the next close decides"),
          ("enter", 1.00, "entry candle closed"))
STAGE_P = {s: p for s, p, _ in STAGES}
STAGE_TXT = {s: t for s, _, t in STAGES}


def _model_names():
    from playbook import MODELS
    return {m: v[0] for m, v in MODELS.items()}


def forming(ctx, setups: list, ranking: list, trust: dict | None = None) -> list:
    """Every model's progress on the live candles, most reliable first.
    Each item: {key, model, name, tf, dir, side, stage, progress, steps[{label, done}], next, level, zone,
    anchor_time, confidence, why[], text}."""
    names = _model_names()
    rk = {r["model"]: r for r in ranking}
    out = {}

    def put(model, tf, d, stage, steps, nxt, level=None, zone=None, anchor=None, score=None, why=None):
        if not d:
            return
        it = {"model": model, "name": names.get(model, model), "tf": tf, "dir": d, "side": _side(d), "stage": stage,
              "progress": STAGE_P[stage], "steps": steps, "next": nxt, "level": level, "zone": zone,
              "anchor_time": anchor, "score": score, "why": why or []}
        k = (model, tf, d)
        if k not in out or out[k]["progress"] < it["progress"]:
            out[k] = it

    # 1) set / ready / enter: the playbook's setups
    for s in setups:
        if s["status"] not in ("armed", "filled") and not (s.get("trigger") and s["trigger"]["fresh"]):
            continue
        tr = s.get("trigger")
        T = ctx.tapes[s["tf"]]
        a = T.atr[-1] or 1.0
        z = s["zone"]
        mid = (z["top"] + z["bottom"]) / 2
        if tr and tr["fresh"] and s["entry_now"]["verdict"] == "ENTER":
            stage, nxt = "enter", f"{tr['text']}: {s['side'].lower()} on this close"
        elif tr and tr["fresh"]:
            stage, nxt = "ready", "triggered but " + "; ".join(s["entry_now"]["missing"])
        elif s["status"] == "filled" or ((T.b.c[-1] - z["top"]) if s["dir"] == 1 else (z["bottom"] - T.b.c[-1])) <= 0.5 * a:
            stage, nxt = "ready", (f"a {s['tf']} close back {'above' if s['dir'] == 1 else 'below'} {mid:.2f} "
                                   f"(or an M1 CISD inside {z['bottom']:.2f}-{z['top']:.2f})")
        else:
            stage, nxt = "set", f"price back into {z['bottom']:.2f}-{z['top']:.2f}, then a close {'up' if s['dir'] == 1 else 'down'}"
        steps = [{"label": c["label"], "done": c["ok"]} for c in s["checks"] if c["key"] in ("key", "sweep", "shift", "time", "bias", "smt", "kronos")]
        steps.append({"label": f"zone {z['bottom']:.2f}-{z['top']:.2f}", "done": True})
        steps.append({"label": "confirmation close", "done": stage == "enter"})
        put(s["model"], s["tf"], s["dir"], stage, steps, nxt, level=mid, zone=z, anchor=s["t"], score=s["score"],
            why=s["why"])

    # 2) forming: a fresh sweep and no shift yet, matched to the models it can become
    sb = ctx.clock.get("silver_bullet")
    utc_now = ctx.utc(ctx.t)
    from ictclock import in_window
    from playbook import MODELS
    for tf, age_max in (("M1", 8), ("M5", 4)):
        T = ctx.tapes.get(tf)
        if not T or not T.sweeps:
            continue
        for w in T.sweeps[-3:]:
            age = T.n - 1 - w["i"]
            if age > age_max:
                continue
            d = w["dir"]
            if any(c["dir"] == d and c["i"] >= w["i"] for c in T.cisd) or any(x["dir"] == d and x["i"] >= w["i"] for x in T.breaks):
                continue                                              # already shifted: the setups cover it
            ref = T.cisd_ref.get(d)
            sw_hi = [s for s in T.swings if s["dir"] == d and s["i"] < w["i"] and (s["price"] - T.b.c[-1]) * d > 0]
            mss_lvl = sw_hi[-1]["price"] if sw_hi else None
            key = ctx.key_level_at(tf, w["ext"])
            ext = not w["kind"].startswith(tf)
            base = [{"label": f"swept {w['kind']} {w['level']:.2f}", "done": True},
                    {"label": f"at a higher-timeframe key level ({key})" if key else "higher-timeframe key level", "done": bool(key)}]
            shift = [{"label": f"CISD: close {'above' if d == 1 else 'below'} {ref:.2f}" if ref is not None else "CISD",
                      "done": False}]
            nxt = (f"a {tf} close {'above' if d == 1 else 'below'} {ref:.2f} (CISD)" if ref is not None else "a CISD") + \
                (f", or {'above' if d == 1 else 'below'} {mss_lvl:.2f} with displacement (MSS)" if mss_lvl else "")
            cands = ["mss_fvg"]
            if sb:
                cands.append("silver_bullet")
            if w["kind"] in ("Asian low", "Asian high") and in_window(utc_now, MODELS["judas"][1]) and ctx.bias * d >= 0:
                cands.append("judas")
            if ext:
                cands += ["turtle_soup", "cisd_fvg"]
            if w["eq"]:
                cands.append("hrlr")
            if ctx.smt_vote(tf, d, T.b.t[w["i"]])[0] == 1:
                cands.append("smt")
            opp_ob = [z for z in T.live_obs(-d, "OB") if (z["bottom"] - T.b.c[-1] if d == 1 else T.b.c[-1] - z["top"]) <= 1.5 * (T.atr[-1] or 1)]
            if opp_ob:
                cands += ["breaker", "unicorn"]
            opp_fvg = [g for g in T.open_fvgs(-d) if abs(g["ce"] - T.b.c[-1]) <= 1.5 * (T.atr[-1] or 1)]
            if opp_fvg:
                cands.append("ifvg")
            for m in cands:
                extra = []
                if m == "breaker" or m == "unicorn":
                    z = opp_ob[-1]
                    extra = [{"label": f"order block {z['bottom']:.2f}-{z['top']:.2f} to break", "done": False}]
                if m == "ifvg":
                    g = opp_fvg[-1]
                    extra = [{"label": f"close {'above' if d == 1 else 'below'} the FVG {(g['top'] if d == 1 else g['bottom']):.2f} "
                                       "(the inversion)", "done": False}]
                if m == "judas":
                    extra = [{"label": "Asian range = accumulation", "done": True},
                             {"label": "London / NY run against the daily bias = manipulation", "done": True},
                             {"label": "distribution after the CISD", "done": False}]
                put(m, tf, d, "forming", base + extra + shift, nxt, level=ref, anchor=T.b.t[w["i"]],
                    why=[f"{tf} swept {w['kind']} {w['level']:.2f} {age} candle{'s' if age != 1 else ''} ago"])

    # 3) watch: liquidity about to be taken (price close to it, not taken yet)
    m5 = ctx.tapes.get("M5")
    if m5 and m5.n:
        a5 = m5.atr[-1] or 1.0
        px = ctx.price
        lv = ctx.lv or {}
        pools = []
        if lv.get("asia"):
            pools += [(lv["asia"]["high"], 1, "Asian high"), (lv["asia"]["low"], -1, "Asian low")]
        for k, d0, nm in (("pdh", 1, "PDH"), ("pdl", -1, "PDL"), ("pwh", 1, "PWH"), ("pwl", -1, "PWL")):
            if lv.get(k):
                pools.append((lv[k]["price"], d0, nm))
        for s in m5.erl(1, px)[:2]:
            pools.append((s["price"], 1, "M5 swing high"))
        for s in m5.erl(-1, px)[:2]:
            pools.append((s["price"], -1, "M5 swing low"))
        for g in lv.get("ndog", []) + lv.get("nwog", []):
            pools.append((g["ce"], 1 if g["ce"] > px else -1, "opening gap 50 %"))
        for lvl, side, nm in pools:
            dist = (lvl - px) * side
            if 0 < dist <= 0.35 * a5:
                d = -side                                              # a raid above sets up a sell, below a buy
                steps = [{"label": f"{nm} {lvl:.2f} is {dist:.2f} away", "done": True},
                         {"label": f"sweep it and close back {'below' if side == 1 else 'above'}", "done": False},
                         {"label": "CISD / MSS", "done": False}, {"label": "entry close", "done": False}]
                nxt = f"a wick through {lvl:.2f} that closes back {'below' if side == 1 else 'above'} it"
                models = ["turtle_soup"]
                if nm.startswith("Asian") and in_window(utc_now, MODELS["judas"][1]):
                    models.append("judas")
                if nm.startswith("opening gap"):
                    models = ["gap"]
                for m in models:
                    put(m, "M5", d, "watch", steps, nxt, level=lvl, anchor=None, why=[f"{nm} {lvl:.2f} within {dist:.2f}"])

    # AMD in London: the Asian range is the accumulation; say what manipulation is expected
    asia = (ctx.lv or {}).get("asia")
    if asia and in_window(utc_now, (("London", 120, 330),)) and abs(ctx.bias) > 0.15:
        d = 1 if ctx.bias > 0 else -1
        lvl = asia["low"] if d == 1 else asia["high"]
        if ("judas", "M5", d) not in out and ("judas", "M1", d) not in out:
            put("judas", "M5", d, "watch",
                [{"label": f"accumulation: Asian range {asia['low']:.2f}-{asia['high']:.2f}", "done": True},
                 {"label": f"manipulation: a run {'below' if d == 1 else 'above'} {lvl:.2f} against the bias", "done": False},
                 {"label": "CISD back inside", "done": False}, {"label": "distribution entry", "done": False}],
                f"London to run {'below the Asian low' if d == 1 else 'above the Asian high'} {lvl:.2f}, then a CISD back",
                level=lvl, why=["AMD: Asia accumulated, London is the manipulation window"])

    # 4) OTE: a leg that shifted and is retracing toward 62 %
    for tf in ("M1", "M5"):
        T = ctx.tapes.get(tf)
        if not T or not T.breaks:
            continue
        br = T.breaks[-1]
        if T.n - 1 - br["i"] > (40 if tf == "M1" else 15) or not br["sweep"]:
            continue
        d = br["dir"]
        rng = range(br["ext_i"], T.n)
        k = max(rng, key=lambda m: T.b.h[m]) if d == 1 else min(rng, key=lambda m: T.b.l[m])
        tip = T.b.h[k] if d == 1 else T.b.l[k]
        span = (tip - br["ext"]) * d
        if span <= 1.5 * (T.atr[-1] or 1):
            continue
        back = (tip - T.b.c[-1]) * d / span
        if 0.3 <= back < 0.62:
            lvl = tip - d * 0.62 * span
            put("ote", tf, d, "forming",
                [{"label": f"{br['kind']} {'up' if d == 1 else 'down'} after a sweep", "done": True},
                 {"label": f"retraced {back:.0%} of the leg", "done": True},
                 {"label": f"reach 62 % ({lvl:.2f})", "done": False}, {"label": "confirmation close", "done": False}],
                f"price into the 62-70 % zone from {lvl:.2f}", level=lvl, anchor=T.b.t[br["i"]],
                why=[f"leg {br['ext']:.2f} -> {tip:.2f}"])

    # 5) Pulse: approaching the higher timeframe's internal liquidity on the way to its external liquidity
    for tf, htf in (("M1", "M15"), ("M5", "H1")):
        H = ctx.tapes.get(htf)
        if not H or not H.n:
            continue
        a = H.atr[-1] or 1.0
        for d in (1, -1):
            gaps = [g for g in H.open_fvgs(d) if 0 < ((ctx.price - g["top"]) if d == 1 else (g["bottom"] - ctx.price)) <= 0.5 * a]
            erl = H.erl(d, ctx.price)
            if gaps and erl:
                g = gaps[-1]
                put("pulse", tf, d, "watch",
                    [{"label": f"{htf} gap {g['bottom']:.2f}-{g['top']:.2f} below the draw" if d == 1 else
                      f"{htf} gap {g['bottom']:.2f}-{g['top']:.2f} above the draw", "done": True},
                     {"label": f"{htf} draw {erl[0]['price']:.2f}", "done": True},
                     {"label": f"tap the gap", "done": False}, {"label": f"{tf} CISD", "done": False}],
                    f"a tap of {g['bottom']:.2f}-{g['top']:.2f}, then a {tf} CISD {'up' if d == 1 else 'down'}",
                    level=g["ce"], zone={"top": g["top"], "bottom": g["bottom"]}, why=[f"Pulse toward {erl[0]['price']:.2f}"])

    # confidence: stage x fit for this market x record x higher-timeframe agreement x Kronos x the setup's grade
    items = []
    for it in out.values():
        r = rk.get(it["model"], {})
        fit = r.get("fit", 0.5)
        edge = r.get("edge", 0.0) or 0.0
        d = it["dir"]
        align = 1.15 if ctx.bias * d > 0.15 else (0.75 if ctx.bias * d < -0.15 else 1.0)
        kv = ctx.kronos_vote(d)[0]
        kf = 1.1 if kv == 1 else (0.8 if kv == -1 else 1.0)
        sc = it["score"] if it["score"] is not None else 0.55
        tm = (trust or {}).get(f"Model: {it['name']}", 1.0)          # earned in the knowledge mesh (mesh.py)
        conf = it["progress"] * (0.35 + 0.65 * fit) * (0.7 + 0.6 * max(-0.5, min(0.5, edge))) * align * kf * (0.5 + 0.5 * sc)
        conf *= 0.5 + 0.5 * tm
        it["trust"] = tm if (trust or {}).get(f"Model: {it['name']}") is not None else None
        it["confidence"] = round(max(0.0, min(1.0, conf)), 3)
        it["key"] = f"{it['model']}:{it['tf']}:{d}:{it['anchor_time'] or round(it['level'] or 0, 1)}"
        done = sum(1 for s in it["steps"] if s["done"])
        it["text"] = (f"{it['name']} {it['side']} {it['stage'].upper()} on {it['tf']} ({done}/{len(it['steps'])}): "
                      f"{STAGE_TXT[it['stage']]}. Next: {it['next']}.")
        items.append(it)
    items.sort(key=lambda x: (-x["confidence"], -x["progress"]))
    return items


class Announcer:
    """Turns forming models into pushes: one message when a model reaches FORMING, READY or ENTER (once per
    model, side and anchor), the most reliable one named first and the others listed; at most one push every
    90 s (an ENTER always goes), 40 a day, nothing while the market is closed or below `min_conf`."""

    PUSH_STAGES = ("forming", "ready", "enter")

    def __init__(self, min_conf: float = 0.18, gap_s: int = 90, per_day: int = 40):
        self.min_conf, self.gap_s, self.per_day = min_conf, gap_s, per_day
        self.done: dict = {}           # key -> highest stage pushed
        self.last = 0
        self.day, self.count = None, 0
        self.log: list = []
        self.cool: dict = {}           # (model, tf, side, stage) -> last push time: the same news isn't repeated
        self.cool_s = 900

    def step(self, items: list, now: int, market_open: bool = True, news: bool = False, overall: dict | None = None) -> list:
        if not market_open or not items:
            return []
        day = now // 86400
        if day != self.day:
            self.day, self.count = day, 0
        fresh = []
        for it in items:
            if it["stage"] not in self.PUSH_STAGES or it["confidence"] < self.min_conf:
                continue
            prev = self.done.get(it["key"])
            ck = (it["model"], it["tf"], it["dir"], it["stage"])
            if now - self.cool.get(ck, -10 ** 9) < self.cool_s:
                continue                                              # said that a moment ago
            if prev is None or STAGE_P[it["stage"]] > STAGE_P[prev]:
                fresh.append(it)
        if not fresh:
            return []
        top = fresh[0]
        urgent = top["stage"] == "enter"
        if (now - self.last < self.gap_s and not urgent) or self.count >= self.per_day:
            return []
        for it in fresh:
            self.done[it["key"]] = it["stage"]
            self.cool[(it["model"], it["tf"], it["dir"], it["stage"])] = now
        if len(self.done) > 2000:
            self.done = dict(list(self.done.items())[-1000:])
        self.last, self.count = now, self.count + 1
        others = [f"{x['name']} {x['side']} ({x['stage']}, {x['confidence']:.0%})" for x in items[:5]
                  if x is not top and x["confidence"] >= self.min_conf][:3]
        word = {"forming": "forming", "ready": "READY", "enter": "ENTER NOW"}[top["stage"]]
        title = f"XAUUSD {top['side']} {word}: {top['name']} {top['tf']}"
        body = [f"{top['text']}", f"Confidence {top['confidence']:.0%} (the most reliable of {len(fresh)} forming now)."]
        if others:
            body.append("Also forming: " + "; ".join(others) + ".")
        if overall:
            body.append(f"Overall: {overall.get('verdict', '').lower()} {overall.get('score', 0):+.2f}.")
        if news:
            body.append("High-impact news close: careful.")
        msg = {"title": title, "body": "\n".join(body), "priority": "urgent" if urgent else ("high" if top["stage"] == "ready" else "default"),
               "tags": "rotating_light" if urgent else ("bell" if top["stage"] == "ready" else "eyes"),
               "item": top, "time": now}
        self.log = (self.log + [{k: msg[k] for k in ("title", "body", "time")}])[-30:]
        return [msg]


# ====================================================================== the knowledge mesh
def mesh_votes(ict: dict | None, overall_: dict | None) -> dict:
    """What the brain says at this candle, as knowledge-mesh sources (mesh.py scores each against the price 10, 30,
    60 and 120 minutes later and earns it trust): every model's forming direction weighted by its progress, the
    best forming model, every voice of the overall analysis, the overall verdict and the decision."""
    g = {}
    if not ict:
        return g
    seen = set()
    for it in ict.get("forming") or []:
        if it["stage"] == "watch" or it["name"] in seen:
            continue
        seen.add(it["name"])
        g[f"Model: {it['name']}"] = round(it["dir"] * it["progress"], 3)
    fm = [x for x in ict.get("forming") or [] if x["stage"] != "watch"]
    if fm:
        g["Best forming model"] = round(fm[0]["dir"] * fm[0]["confidence"], 3)
    dec = ict.get("decision") or {}
    if dec.get("dir"):
        g["Decision"] = dec["dir"]
    for v in (overall_ or {}).get("voices") or []:
        if v.get("value") is not None:
            g[f"Voice: {v['name']}"] = v["value"]
    if overall_ and overall_.get("score") is not None:
        g["Overall"] = overall_["score"]
    return g
