"""Self-teaching layer for the scalper (isolated project).

Every closed trade writes a "lesson" and the bot adjusts itself from its
own mistakes:

  * lesson journal: data/state/lessons.jsonl -- one row per closed trade
    with the failure signature (strategy, model, exit reason, R, hold
    time, knife-catch flag).
  * autopilot: data/state/autopilot.json -- automatic rules derived from
    those lessons.  The current rule: a strategy that lost its last 3
    closed trades (or averages <= -0.5R over its last 5) is paused for a
    cooldown; the bot re-arms it afterwards and says so via ntfy.

Nothing here is magic: every self-taught decision is logged and pushed
to the operator so the bot can never silently rewrite its own rules.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

LESSON_WINDOW_TRADES = 12
PAUSE_AFTER_CONSEC_LOSSES = 3
PAUSE_MINUTES = 120
AVG_R_FAIL = -0.5


def _state_dir(data: Path) -> Path:
    return data / "state"


def lessons_path(data: Path) -> Path:
    return _state_dir(data) / "lessons.jsonl"


def autopilot_path(data: Path) -> Path:
    return _state_dir(data) / "autopilot.json"


def record_lesson(data: Path, rec: dict) -> None:
    """One lesson per closed trade record (trades.jsonl shape)."""
    try:
        p = lessons_path(data)
        p.parent.mkdir(parents=True, exist_ok=True)
        lesson = {
            "ts_ms": rec.get("ts_ms", int(time.time() * 1000)),
            "symbol": rec.get("symbol"),
            "strategy": rec.get("strategy"),
            "model": rec.get("entry_model"),
            "exit_reason": rec.get("exit_reason"),
            "pnl": rec.get("pnl"),
            "pnl_r": rec.get("pnl_r"),
            "knife_catch": bool(rec.get("knife_catch")),
        }
        with p.open("a") as fh:
            fh.write(json.dumps(lesson) + "\n")
    except Exception:
        pass


def load_lessons(data: Path) -> list[dict]:
    p = lessons_path(data)
    if not p.exists():
        return []
    out = []
    try:
        for line in p.read_text().splitlines():
            if line.strip():
                out.append(json.loads(line))
    except Exception:
        pass
    return out


def evaluate(data: Path) -> dict:
    """Decide what the bot pauses based on its own lessons.

    Returns pause rules keyed by "s:<strategy>" and "m:<entry_model>":
    {key: {"paused_until_ms": int, "reason": str}}.  The bot re-evaluates
    every ~30s and re-arms keys once their pause expires.
    """
    lessons = load_lessons(data)[-LESSON_WINDOW_TRADES:]
    per = {}
    for l in lessons:
        for key in (f"s:{l.get('strategy') or '?'}", f"m:{l.get('model') or '?'}"):
            b = per.setdefault(key, {"pnls": [], "rs": []})
            if l.get("pnl") is not None:
                b["pnls"].append(float(l["pnl"]))
            if l.get("pnl_r") is not None:
                b["rs"].append(float(l["pnl_r"]))

    out = {}
    now = time.time()
    for key, b in per.items():
        recent = b["pnls"][-PAUSE_AFTER_CONSEC_LOSSES:]
        consec_losses = len(recent) == PAUSE_AFTER_CONSEC_LOSSES \
            and all(x < 0 for x in recent)
        avg_r = (sum(b["rs"][-5:]) / max(1, len(b["rs"][-5:]))) \
            if b["rs"] else 0.0
        if consec_losses:
            out[key] = {"paused_until_ms": int((now + PAUSE_MINUTES * 60) * 1000),
                        "reason": f"{PAUSE_AFTER_CONSEC_LOSSES} losses in a row"}
        elif b["rs"] and avg_r <= AVG_R_FAIL and len(b["rs"]) >= 5:
            out[key] = {"paused_until_ms": int((now + PAUSE_MINUTES * 60) * 1000),
                        "reason": f"avg {avg_r:.2f}R over last {min(5, len(b['rs']))}"}
    return out


def paused_strategies(data: Path) -> set[str]:
    """Keys currently paused by the bot's own rules (s:* and m:*)."""
    ap = autopilot_path(data)
    if not ap.exists():
        return set()
    try:
        st = json.loads(ap.read_text())
    except Exception:
        return set()
    now_ms = int(time.time() * 1000)
    return {k for k, r in st.items() if r.get("paused_until_ms", 0) > now_ms}


DISCOVERY_WINDOW_SYM_MS = 24 * 3_600_000      # symbol evidence window
DISCOVERY_WINDOW_HOUR_MS = 72 * 3_600_000     # hour evidence window
SYM_PAUSE_MS = 6 * 3_600_000
HOUR_PAUSE_MS = 24 * 3_600_000
HOUR_MS = 3_600_000


def discover(data: Path) -> dict:
    """Self-discovered rules the bot was never explicitly taught.

    Mines the lesson journal for strong, explainable patterns and turns
    them into temporary guardrails, then keeps a discoveries journal of
    everything it found (so the operator can audit the bot's own ideas).

    Rules:
      sym:X   -- >=2 losses, 0 wins, in the last 24h  -> skip X for 6h
      hour:H  -- >=4 trades, 0 wins, >=3 losses in the
                 same UTC hour over the last 72h    -> skip that hour for 24h
    """
    now_ms = int(time.time() * 1000)
    lessons = load_lessons(data)
    out = {}
    sym = {}
    hour = {}
    insights = []
    for l in lessons:
        ts = l.get("ts_ms", 0)
        pnl = float(l.get("pnl") or 0.0)
        if ts > 0:
            h = (ts // HOUR_MS) % 24
            hb = hour.setdefault(h, {"n": 0, "w": 0, "l": 0, "pnl": 0.0})
            hb["n"] += 1
            if pnl > 0:
                hb["w"] += 1
            elif pnl < 0:
                hb["l"] += 1
            hb["pnl"] += pnl
        s_ = l.get("symbol")
        if s_ and now_ms - ts < DISCOVERY_WINDOW_SYM_MS:
            sb = sym.setdefault(s_, {"n": 0, "w": 0, "l": 0, "pnl": 0.0})
            sb["n"] += 1
            if pnl > 0:
                sb["w"] += 1
            elif pnl < 0:
                sb["l"] += 1
            sb["pnl"] += pnl
    for s_, b in sym.items():
        if b["n"] >= 2 and b["w"] == 0 and b["l"] >= 2:
            out[f"sym:{s_}"] = {"paused_until_ms": now_ms + SYM_PAUSE_MS,
                                "reason": f"0 wins, {b['l']} losses in 24h"}
            insights.append(f"symbol {s_} keeps losing -> auto-skip 6h")
    for h, b in hour.items():
        if b["n"] >= 4 and b["w"] == 0 and b["l"] >= 3:
            out[f"hour:{h}"] = {"paused_until_ms": now_ms + HOUR_PAUSE_MS,
                                "reason": f"0 wins in {b['n']} trades at hour {h}"}
            insights.append(f"hour {h} UTC is a loser window -> auto-skip 24h")
    # top positive discovery: the bot's best pattern, logged for the operator
    best = None
    for s_, b in sym.items():
        if b["n"] >= 2 and b["w"] >= b["n"]:
            best = f"symbol {s_} is on fire: {b['w']}/{b['n']} wins (+{b['pnl']:.2f})"
            break
    if best:
        insights.append(best)
    if insights:
        _record_discoveries(data, insights)
    return out


def discoveries_path(data: Path) -> Path:
    return _state_dir(data) / "discoveries.jsonl"


def _record_discoveries(data: Path, insights: list[str]) -> None:
    """Append-only journal of everything the bot taught itself (dedup by
    text -- the same insight is not re-logged while still true)."""
    try:
        p = discoveries_path(data)
        p.parent.mkdir(parents=True, exist_ok=True)
        known = set()
        if p.exists():
            for line in p.read_text().splitlines():
                if line.strip():
                    try:
                        known.add(json.loads(line).get("text"))
                    except Exception:
                        pass
        with p.open("a") as fh:
            for text in insights:
                if text in known:
                    continue
                fh.write(json.dumps({"ts_ms": int(time.time() * 1000),
                                     "text": text}) + "\n")
    except Exception:
        pass


def daily_review(data: Path, lo_ms: int, hi_ms: int) -> dict:
    """Yesterday's lesson summary for the operator."""
    rows = [l for l in load_lessons(data)
            if lo_ms <= l.get("ts_ms", 0) < hi_ms]
    wins = [l for l in rows if (l.get("pnl") or 0) > 0]
    losses = [l for l in rows if (l.get("pnl") or 0) <= 0]
    by = {}
    for l in rows:
        k = l.get("strategy") or "?"
        b = by.setdefault(k, {"n": 0, "pnl": 0.0})
        b["n"] += 1
        b["pnl"] += float(l.get("pnl") or 0.0)
    return {"n": len(rows),
            "wins": len(wins),
            "losses": len(losses),
            "pnl": round(sum(float(l.get("pnl") or 0.0) for l in rows), 2),
            "by": {k: {"n": v["n"], "pnl": round(v["pnl"], 2)}
                   for k, v in sorted(by.items(),
                                      key=lambda kv: -kv[1]["pnl"])}}


def write_autopilot(data: Path, rules: dict) -> None:
    ap = autopilot_path(data)
    ap.parent.mkdir(parents=True, exist_ok=True)
    tmp = ap.with_suffix(".tmp")
    tmp.write_text(json.dumps(rules, indent=1))
    tmp.replace(ap)
