"""Stratton Oakmont -- the scalper's phone app.

Same structure the operator knows: black/gold phone UI, Face ID
(WebAuthn) + password, served on 443 with the same Let's Encrypt cert and
the same RP/origin -- but it tracks the SCALPER paper book instead of the
TBT engine.  The WebAuthn credential file is migrated from the old panel's
store so the phone's Face ID keeps working without re-registration.
"""
from __future__ import annotations

import base64
import json
import math
import os
import secrets
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.getenv("SCALPER_DATA", str(ROOT / "data")))
STATE = DATA / "state" / "paper.json"
LIVE = DATA / "state" / "live.json"
TRADES = DATA / "logs" / "trades.jsonl"
REJECTS = DATA / "logs" / "rejections.jsonl"
RESEARCH = DATA / "research" / "research.jsonl"
PAUSED = DATA / "state" / "paused"
CREDS = Path(os.getenv("SCALPER_CREDS", str(DATA / "state" / "webauthn_creds.json")))
LEGACY_CREDS = Path("/root/.tbt_panel_creds.json")

TOKEN = os.getenv("SCALPER_APP_TOKEN", "")
RP_ID = os.getenv("SCALPER_RPID", "62.60.198.135.nip.io")
ORIGIN = f"https://{RP_ID}"
SESSIONS: dict[str, float] = {}
SESSIONS_FILE = DATA / "state" / "sessions.json"
CHALLENGES: dict[str, tuple[float, str]] = {}
LOGIN_FAILS: dict[str, list[float]] = {}   # ip -> recent wrong-password times
SESSION_HOURS = 24.0 * 14   # stay signed in for two weeks, across restarts
GOAL_START = float(os.getenv("SCALPER_GOAL_START", "100"))
GOAL_TARGET = float(os.getenv("SCALPER_GOAL_TARGET", "100000"))
GOAL_DAYS = float(os.getenv("SCALPER_GOAL_DAYS", "10"))

if LEGACY_CREDS.exists() and not CREDS.exists():
    try:
        CREDS.parent.mkdir(parents=True, exist_ok=True)
        CREDS.write_text(LEGACY_CREDS.read_text())
        os.chmod(CREDS, 0o600)
    except Exception:
        pass


def _load_creds() -> list[dict]:
    if CREDS.exists():
        try:
            return json.loads(CREDS.read_text())
        except Exception:
            pass
    return []


def _save_creds(c) -> None:
    CREDS.write_text(json.dumps(c))
    os.chmod(CREDS, 0o600)


def _load_sessions() -> None:
    """Sessions survive app restarts: a deploy no longer kicks the phone
    back to the login door."""
    if SESSIONS_FILE.exists():
        try:
            now = time.time()
            for t, exp in json.loads(SESSIONS_FILE.read_text()).items():
                if exp > now:
                    SESSIONS[t] = exp
        except Exception:
            pass


def _save_sessions() -> None:
    try:
        tmp = SESSIONS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(SESSIONS))
        tmp.replace(SESSIONS_FILE)
    except Exception:
        pass


_load_sessions()


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _json_rows(path: Path, n: int) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows[-n:]


def _state_payload() -> dict:
    try:
        st = json.loads(STATE.read_text())
    except Exception:
        st = {}
    equity = float(st.get("equity", GOAL_START)) or GOAL_START
    day_start_eq = float(st.get("day_start_equity", GOAL_START)) or GOAL_START
    try:
        from config.loader import load_config as _lc
        _cfg = _lc(extra_file="config/aggressive.yaml")
        target_pct = float(_cfg.daily.get("target_pct", 1.0)) * 100.0
    except Exception:
        target_pct = 100.0
    now = int(time.time() * 1000)
    ts = int(st.get("ts") or now)
    day_ms = 86_400_000
    start_ms = ts - (ts % day_ms)
    goal_mult = (GOAL_TARGET / GOAL_START) ** (1.0 / GOAL_DAYS)
    day_index = min(int(max(0, (now - start_ms) // day_ms)), int(GOAL_DAYS) - 1)
    curve = [GOAL_START * (goal_mult ** d) for d in range(int(GOAL_DAYS))]
    open_pos = []
    # live prices (written every poll by the paper trader) -> floating pnl
    try:
        live = json.loads(LIVE.read_text()) if LIVE.exists() else {}
    except Exception:
        live = {}
    for p in st.get("positions", []):
        lots = p.get("lots") or []
        if not lots:
            continue
        entry = float(lots[0].get("entry") or 0)
        sl = float(lots[0].get("sl") or 0)
        tp = lots[0].get("tp")
        dirn = p.get("direction", 1)
        model = p.get("meta", {}).get("entry_model", "")
        strat = p.get("meta", {}).get("strategy", "")
        # sum ALL open lots -- margin/pnl must reflect the whole position,
        # not just the first lot
        qty = sum(float(lot.get("qty") or 0)
                  for lot in lots if lot.get("exit_px") is None)
        lv = live.get(p["symbol"], {})
        px = float(lv.get("px")) if lv.get("px") is not None else None
        fp = (qty * (px - entry) * dirn) if px else None
        lev = float(p.get("meta", {}).get("leverage") or 1) or 1
        notional = qty * entry if entry else 0.0
        margin = notional / lev if lev else 0.0
        margin_pct = (notional / lev / equity * 100.0) if equity else 0.0
        # the card's headline % is the trade's own ROI on margin
        # (price move x leverage), the standard exchange number; the
        # account-weighted move is shown alongside as "acct"
        roi = (fp / margin * 100.0) if fp is not None and margin else None
        acct_pct = (fp / equity * 100.0) \
            if fp is not None and equity else None
        at = 0.5
        if entry and (tp or sl):
            lo, hi = (sl, tp) if dirn == 1 else (tp, sl)
            lo = lo or sl
            hi = hi or sl
            if hi != lo:
                ref = px if px else entry
                at = max(0.0, min(1.0, (ref - lo) / (hi - lo)))
        # per-open-lot detail: kind (tp1/tp2/first/runner), its own stop
        # (trailing moves it) and target -- the card shows exactly what the
        # bot is working each leg toward
        lots_open = []
        first_open = None
        for lot in lots:
            if lot.get("exit_px") is not None:
                continue
            d = {"kind": lot.get("kind") or "",
                 "qty": float(lot.get("qty") or 0),
                 "sl": float(lot.get("sl") or 0),
                 "tp": lot.get("tp"),
                 "tp1": lot.get("tp1"),
                 "be": lot.get("be")}
            lots_open.append(d)
            if first_open is None:
                first_open = d
        # ---- the trade card: every level with its PRICE and its DOLLARS --
        meta = p.get("meta", {})
        f1 = float(meta.get("tp1_frac") or 0.4)
        f2 = float(meta.get("tp2_frac") or 0.3)
        f3 = max(0.0, 1.0 - f1 - f2)
        closed_legs = [lg for lg in lots if lg.get("exit_px") is not None]
        total_qty = sum(float(lg.get("qty") or 0) for lg in lots)
        sl_usd = (qty * (sl - entry) * dirn) if qty and entry else 0.0
        realized = float(p.get("realized_pnl") or 0.0)
        held_min = (int((now - int(p.get("opened_ms") or now)) / 60000))
        tiers = []

        def tier_done(reason):
            for lg in closed_legs:
                if lg.get("exit_reason") == reason:
                    return lg
            return None

        for name, share, reason in (("TP1", f1, "tp1"),
                                    ("TP2", f2, "tp2")):
            leg = tier_done(reason)
            if leg is not None:
                tiers.append({"name": name, "share": round(share * 100),
                              "px": leg.get("exit_px"),
                              "usd": round(float(leg.get("pnl") or 0), 2),
                              "done": True})
            else:
                lvl = None
                if name == "TP1":
                    lvl = (first_open or {}).get("tp1")
                else:
                    lvl = (first_open or {}).get("tp")
                if lvl:
                    usd = total_qty * share * (lvl - entry) * dirn
                    tiers.append({"name": name, "share": round(share * 100),
                                  "px": lvl, "usd": round(usd, 2),
                                  "done": False})
        tleg = tier_done("trail")
        if tleg is not None:
            tiers.append({"name": "TRAIL", "share": round(f3 * 100),
                          "px": tleg.get("exit_px"),
                          "usd": round(float(tleg.get("pnl") or 0), 2),
                          "done": True})
        else:
            live_usd = (qty * f3 * (px - entry) * dirn) if px else None
            tiers.append({"name": "TRAIL", "share": round(f3 * 100),
                          "px": None,
                          "usd": round(live_usd, 2) if live_usd is not None
                          else None, "done": False, "live": True})
        open_pos.append({"sym": p["symbol"],
                         "side": "BUY" if dirn == 1 else "SELL",
                         "entry": entry, "sl": sl, "tp": tp,
                         "sl_usd": round(sl_usd, 2),
                         "realized": round(realized, 2),
                         "lev": lev, "held_min": held_min,
                         "tiers": tiers,
                         "model": model, "strategy": strat,
                         "brain_prob": p.get("meta", {}).get("brain_prob"),
                         "be": bool(p.get("be_active")),
                         "trail": bool(p.get("trail_armed")),
                         "bos_pending": bool(p.get("struct_exit_pending")),
                         "lots": lots_open,
                         "at": round(at, 3), "qty": qty,
                         "px": px,
                         "pnl": round(fp, 2) if fp is not None else None,
                         "pct": round(roi, 2) if roi is not None else None,
                         "acct_pct": round(acct_pct, 2)
                         if acct_pct is not None else None,
                         "margin": round(margin_pct, 2),
                         "margin_usd": round(notional / lev, 2),
                         "live_ts": lv.get("ts")})
    float_pnl = sum(float(p["pnl"]) for p in open_pos
                    if p.get("pnl") is not None)
    equity_live = equity + float_pnl
    day_pnl_pct = (equity_live - day_start_eq) / day_start_eq * 100.0
    trades = _json_rows(TRADES, 500)
    # stale-replay artifacts are kept in the log for the record but must
    # never count toward the book's statistics
    trades = [t for t in trades if not t.get("phantom")]
    # a NaN pnl in the payload would make the browser's JSON.parse throw and
    # silently break every dashboard refresh -- sanitize before aggregation
    for t in trades:
        try:
            p = float(t.get("pnl"))
            if not math.isfinite(p):
                t["pnl"] = 0.0
        except (TypeError, ValueError):
            t["pnl"] = 0.0
    closed = [t for t in trades if t.get("pnl") is not None]
    won = sum(1 for t in closed if t["pnl"] > 0)
    gross_win = sum(t["pnl"] for t in closed if t["pnl"] > 0)
    gross_loss = sum(t["pnl"] for t in closed if t["pnl"] <= 0)
    best = max((t["pnl"] for t in closed), default=0.0)
    worst = min((t["pnl"] for t in closed), default=0.0)
    streak = 0
    for t in reversed(closed):
        if t["pnl"] <= 0:
            streak += 1
        else:
            break
    by = {}
    for t in closed:
        k = t.get("strategy") or "?"
        b = by.setdefault(k, {"n": 0, "w": 0, "pnl": 0.0})
        b["n"] += 1
        b["w"] += 1 if t["pnl"] > 0 else 0
        b["pnl"] += t["pnl"]
    by_rows = [{"sym": k, "w": v["w"], "n": v["n"], "pnl": v["pnl"]}
               for k, v in sorted(by.items(), key=lambda kv: -kv[1]["pnl"])]
    recent = []
    for t in reversed(closed[-12:]):
        held = None
        try:
            held = int((t.get("ts_ms", 0) - t.get("opened_ms", 0)) // 60_000)
        except Exception:
            held = None
        recent.append({"sym": t.get("symbol"),
                       "side": "SELL" if t.get("direction") == -1 else "BUY",
                       "lot": t.get("lot", ""),
                       "strat": t.get("strategy", ""),
                       "reason": t.get("exit_reason", ""),
                       "entry": t.get("entry"), "exit": t.get("exit"),
                       "pnl": t.get("pnl", 0.0), "held": held})
    # the lab: the AI inventor's latest report (read-only display)
    lab_rows = []
    lab_state = {}
    try:
        inv = json.loads((DATA / "state" / "inventions.json").read_text())
    except Exception:
        inv = {}
    for r in (inv.get("inventions") or [])[:5]:
        lab_rows.append({"name": r.get("name"),
                         "wr": r.get("wr"), "pf": r.get("pf"),
                         "avg_r": r.get("avg_r"), "n": r.get("n"),
                         "dd": r.get("max_dd_pct"), "score": r.get("score"),
                         "changes": (r.get("changes") or [])[:3],
                         "coins": (r.get("coins") or {}).get("recommendation", "")})
    champ = inv.get("champion") or {}
    lab_state = {"champ_wr": champ.get("wr"), "champ_n": champ.get("n"),
                 "champ_pf": champ.get("pf"),
                 "adopt_gate": bool(inv.get("adopt_gate")),
                 "adopt": bool(inv.get("adopt")),
                 "as_of_ms": inv.get("as_of_ms")}
    # the brain: the learned win-probability filter's training report
    brain_st = {}
    try:
        bm = json.loads((DATA / "state" / "brain_meta.json").read_text())
        brain_st = {"ready": (DATA / "state" / "brain.pkl").exists(),
                    "n_train": bm.get("n_train"), "auc": bm.get("auc"),
                    "base_wr": bm.get("base_wr"), "lifts": bm.get("lifts")}
    except Exception:
        pass
    paused = PAUSED.exists()
    return {
        "ok": True,
        "book": {"equity": equity,
                 "equity_live": round(equity_live, 2),
                 "float_pnl": round(float_pnl, 2),
                 "closed": len(closed), "won": won,
                 "open": open_pos},
        "rep": {"gross_win": gross_win, "gross_loss": gross_loss,
                "best": best, "worst": worst, "streak": streak,
                "by": by_rows, "recent": recent,
                "start": GOAL_START},
        "running": not paused,
        "paused": paused,
        "halted": bool(st.get("halted", False)),
        "halt_reason": st.get("halt_reason", ""),
        "day_pnl_pct": round(day_pnl_pct, 2),
        "target_pct": target_pct,
        "target_hit": day_pnl_pct >= target_pct,
        "trades_today": st.get("trades_today", 0),
        "devices": len(_load_creds()),
        "research_n": (sum(1 for _ in open(RESEARCH)) if RESEARCH.exists() else 0),
        "lab": {"rows": lab_rows, **lab_state},
        "brain": brain_st,
        "goal": {"start": GOAL_START, "target": GOAL_TARGET,
                 "days": int(GOAL_DAYS), "start_ms": start_ms,
                 "day_ms": day_ms, "day_index": day_index, "curve": curve},
        "ts": now,
    }


def _page_html() -> str:
    return """<!doctype html><html><head><meta charset=utf-8>
<title>Stratton Oakmont</title>
<meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name=apple-mobile-web-app-capable content=yes>
<meta name=apple-mobile-web-app-status-bar-style content=black>
<meta name=apple-mobile-web-app-title content=Stratton>
<meta name=theme-color content=#0A0A0A>
<link rel="icon" type="image/png" sizes="192x192" href="/icon-192.png">
<link rel="apple-touch-icon" href="/icon-180.png">
<style>

:root{
 --bg:#000; --surface:#0B0B0C; --card:#0E0E10;
 --gold:#D4AF37; --gold-press:#C9A227; --gold-soft:#E8D48B;
 --win:#00FF9F; --loss:#C41E3A; --warn:#FFB800; --info:#4A9EFF;
 --txt:#F2F2EE; --txt2:#8A8A8F; --off:#4A4A50; --on-gold:#000;
 --line:#1B1B1E;
 --ui:-apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",system-ui,sans-serif;
 --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,monospace;
}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:var(--bg);color:var(--txt);font-family:var(--ui);
 font-weight:400;font-size:15px;padding:16px 13px 48px;-webkit-font-smoothing:antialiased;
 }
.brand{display:flex;flex-direction:row;align-items:center;
 gap:12px;margin:2px 0 0}
.brand img.logo{width:52px;height:52px;border-radius:12px;
 border:1px solid rgba(212,175,55,.28);display:block}
.brand .brandtxt{display:flex;flex-direction:column;gap:2px}

.brand h1{font-family:var(--ui);font-weight:600;font-size:15px;margin:0;
 letter-spacing:.02em;color:var(--txt2);line-height:1.2}
.brand span{font-family:var(--ui);font-weight:700;font-size:9px;
 letter-spacing:.30em;color:var(--off)}
.rule{height:1px;margin:12px 0 14px;background:var(--line)}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
 padding:15px;margin-bottom:10px;position:relative;overflow:hidden}
.card.key{border:1px solid rgba(212,175,55,.20)}
.row{display:flex;justify-content:space-between;align-items:center;
 padding:8px 0;border-bottom:1px solid var(--line);gap:12px}
.row:last-child{border-bottom:0}
.k{color:var(--txt2);font-size:12px;font-weight:500;white-space:nowrap}
.v{font-family:var(--mono);font-weight:700;font-size:13px;text-align:right;color:var(--txt)}
.hero{font-family:var(--ui);font-weight:700;font-size:44px;line-height:1.05;
 letter-spacing:-.03em;color:var(--gold);margin:8px 0 4px;
 font-variant-numeric:tabular-nums}
.hero.green{color:var(--win)}.hero.red{color:var(--loss)}
.sub{font-size:12px;color:var(--txt2);font-weight:500}
.pill{padding:4px 11px;border-radius:4px;font-size:11px;font-weight:600;
 font-family:var(--ui)}
.on{background:var(--gold);color:var(--on-gold)}
.offp{background:#2A2A2A;color:var(--txt2)}
.livep{background:var(--loss);color:#fff}
button{width:100%;padding:15px;border:0;border-radius:6px;font-family:var(--ui);
 font-size:15px;font-weight:500;
 color:var(--on-gold);background:var(--gold);margin-top:12px;cursor:pointer;
 transition:transform .08s,box-shadow .08s,background .08s;
 box-shadow:0 0 0 rgba(212,175,55,0)}
button:hover{background:var(--gold-soft)}
button:active{transform:scale(.985);background:var(--gold-press)}
button.stop{background:var(--loss);color:#fff}
button.stop:hover{background:#d9243f}
button.ghost{background:transparent;color:var(--gold);
 border:1.5px solid var(--gold)}
button.ghost:hover{background:rgba(212,175,55,.09);color:var(--gold-soft)}
input{width:100%;padding:14px;border-radius:5px;border:1.5px solid var(--line);
 background:var(--bg);color:var(--txt);font-size:16px;margin-top:8px;
 font-family:var(--mono);font-weight:700;letter-spacing:.04em;outline:0;
 transition:border-color .1s,box-shadow .1s}
select{width:100%;padding:14px;border-radius:5px;border:1.5px solid var(--line);background:var(--bg);color:var(--txt);font-size:16px;margin-top:8px;font-family:var(--mono);font-weight:700;outline:0;appearance:none;background-image:linear-gradient(45deg,transparent 50%,var(--gold) 50%),linear-gradient(135deg,var(--gold) 50%,transparent 50%);background-position:calc(100% - 20px) 22px,calc(100% - 14px) 22px;background-size:6px 6px,6px 6px;background-repeat:no-repeat}
select:focus{border-color:var(--gold);box-shadow:0 0 0 3px rgba(212,175,55,.16)}
input:focus{border-color:var(--gold);box-shadow:0 0 0 3px rgba(212,175,55,.16)}
input::placeholder{color:var(--off);font-weight:400}
label{font-size:12px;color:var(--txt2);display:block;margin-top:14px;
 font-weight:500}
.g4{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.note{color:var(--off);font-size:11px;margin-top:12px;line-height:1.5;
 font-weight:500}
.pos{background:var(--bg);border:1px solid var(--line);
 border-left:4px solid var(--gold);border-radius:10px;
 padding:14px 15px 12px;margin-top:12px}
.pos.up{border-left-color:var(--win)}
.pos.dn{border-left-color:var(--loss)}
.pos .phead{display:flex;justify-content:space-between;align-items:flex-start;
 gap:10px}
.pos .sym{font-family:var(--ui);font-size:16px;font-weight:700;
 letter-spacing:.02em}
.pos .sym .sd{font-size:11px;font-weight:700;padding:2px 7px;border-radius:4px;
 margin-left:6px}
.pos .sd.buy{background:rgba(0,255,159,.12);color:var(--win)}
.pos .sd.sell{background:rgba(196,30,58,.16);color:var(--loss)}
.pos .tagrow{display:flex;flex-wrap:wrap;gap:5px;margin-top:4px}
.pos .tag{font-size:10px;font-weight:700;letter-spacing:.05em;
 padding:2px 8px;border-radius:4px;background:#222;
 color:var(--txt2);border:1px solid var(--line)}
.pos .tag.on{color:var(--gold);border-color:rgba(212,175,55,.4);
 background:rgba(212,175,55,.08)}
.pos .tag.tr{color:var(--win);border-color:rgba(0,255,159,.35);
 background:rgba(0,255,159,.07)}
.pos .pnl{font-family:var(--ui);font-size:26px;font-weight:800;
 letter-spacing:-.02em;font-variant-numeric:tabular-nums;text-align:right}
.pos .pnlsub{font-family:var(--mono);font-size:11.5px;color:var(--txt2);
 text-align:right;margin-top:2px}
.pos .grid{display:grid;grid-template-columns:1fr 1fr;gap:7px 16px;
 margin-top:11px;padding-top:11px;border-top:1px solid var(--line)}
.pos .cell{display:flex;flex-direction:column;gap:2px;min-width:0}
.pos .cell .lbl{font-size:10px;font-weight:700;letter-spacing:.08em;
 color:var(--txt2)}
.pos .cell .val{font-family:var(--mono);font-size:13px;font-weight:700;
 color:var(--txt);word-break:break-all}
.pos .tier{display:flex;justify-content:space-between;align-items:center;
 gap:10px;padding:8px 0;border-top:1px dashed rgba(255,255,255,.08);
 font-family:var(--mono);font-size:12px}
.pos .tier:first-of-type{border-top:0;padding-top:2px}
.pos .tier .tname{font-weight:800;letter-spacing:.06em;min-width:52px}
.pos .tier .tname.done{color:var(--win)}
.pos .tier .tpx{color:var(--txt2);flex:1;text-align:right}
.pos .tier .tusd{font-weight:700;min-width:64px;text-align:right;
 font-variant-numeric:tabular-nums}
.pos .foot{display:flex;justify-content:space-between;align-items:center;
 margin-top:10px;padding-top:9px;border-top:1px solid var(--line);
 font-size:11px;color:var(--txt2);font-family:var(--ui)}
.pos .foot b{color:var(--txt);font-family:var(--mono)}
.pos .track{position:relative;height:4px;border-radius:2px;margin:12px 2px 5px;
 background:linear-gradient(90deg,rgba(196,30,58,.55),var(--line) 26%,
 var(--line) 74%,rgba(0,255,159,.55))}
.pos .track.sell{background:linear-gradient(90deg,rgba(0,255,159,.55),var(--line) 26%,
 var(--line) 74%,rgba(196,30,58,.55))}
.pos .dot{position:absolute;top:50%;width:9px;height:9px;border-radius:50%;
 transform:translate(-50%,-50%);background:var(--gold);
 box-shadow:0 0 0 3px var(--bg);transition:left .5s ease}
.title{font-family:var(--ui);font-weight:500;font-size:13px;
 color:var(--txt2);margin:0 0 12px}
#msg{background:rgba(212,175,55,.07);border:1px solid rgba(212,175,55,.32);
 border-left:3px solid var(--gold);border-radius:5px;padding:13px;
 margin-bottom:14px;font-size:12px;display:none;white-space:pre-wrap;
 font-family:var(--mono);color:var(--gold-soft);line-height:1.5}
.busy{opacity:.55;pointer-events:none;transition:opacity .12s}
.up{color:var(--win)}.dn{color:var(--loss)}
.pair{display:grid;grid-template-columns:2fr 1fr;gap:10px}
.devrow{text-align:right;margin-top:10px;font-size:12px}
.devrow a{color:var(--off);text-decoration:none}
.devrow a:active{color:var(--gold)}
.autorow{display:flex;justify-content:space-between;align-items:flex-start;
 gap:10px;margin-top:12px;font-size:12px;color:var(--off);line-height:1.5}
.autorow a{color:var(--txt2);text-decoration:none;white-space:nowrap}
.autorow a:active{color:var(--gold)}
.pair button{margin-top:12px}
.twlink{font-family:var(--ui);font-size:11px;font-weight:500;
 color:var(--gold);text-decoration:none;margin-left:8px}
.twlink:active{color:var(--txt)}
pre.scan{background:var(--bg);border:1px solid var(--line);
 border-left:3px solid var(--gold);border-radius:4px;padding:12px;
 margin-top:12px;font-size:10px;line-height:1.45;overflow-x:auto;
 color:var(--txt2);font-family:var(--mono)}
@keyframes flash{0%{border-color:var(--line)}
 35%{border-color:var(--win)}
 100%{border-color:var(--line)}}
.win-flash{animation:flash .55s ease-out}

</style></head><body></style></head><body>
<div class=brand><img class=logo src="/logo.png" alt="Stratton Oakmont"><div class=brandtxt><h1>Stratton Oakmont</h1><span>SCALPER &middot; PAPER DESK</span></div></div>
<div class=rule></div>
<div id=app>loading...</div>
<script>
const T=new URLSearchParams(location.search).get('t')||'';
function money(n){return (n>=0?'+':'')+(+n).toFixed(2)}
async function api(p,body){const r=await fetch(p+(p.includes('?')?'&':'?')+'t='+T,
 {method:body?'POST':'GET',body:body?JSON.stringify(body):null});
 if(r.status===403||r.status===401){location.href='/';return null}
 return r.json()}
function fail(msg){const el=document.getElementById('app');
 if(el)el.innerHTML='<div class=card><div class=title>'+msg+'</div>'
  +'<button onclick=location.reload()>retry</button>'
  +'<div class=note>if this keeps happening, open the app in Safari and clear the page once</div></div>'}
function draw(s){
  const set=(id,html)=>{const e=document.getElementById(id);if(e&&e.innerHTML!==html)e.innerHTML=html};
  const r=s.rep||{},b=s.book;
  const wr=b.closed?Math.round(b.won/b.closed*100):0;
  if(!document.getElementById('wallet')){
    document.getElementById('app').innerHTML=`
    <div class="card key" id=wallet>
     <div class=row style="border:0;padding-bottom:0"><span class=sub>wallet &middot; paper &middot; 24h &middot; v21</span><span class=pill id=st></span></div>
     <div class=hero id=eq></div>
     <div class=sub id=eqsub></div>
     <div id=posbox></div>
     <div class=pair><button id=power></button><button class=ghost id=lockbtn>lock</button></div>
     <div class=devrow><a href=# id=reg></a><span id=up style="display:block;text-align:center;margin-top:6px;color:var(--off);font-size:10px"></span></div>
    </div>
    <div class=card>
     <div class=title>the daily target</div>
     <div class=row><span class=k>day</span><span class=v id=day></span></div>
     <div class=row><span class=k>target</span><span class=v id=tgt></span></div>
    </div>
    <div class=card>
     <div class=title>performance</div>
     <div class=row><span class=k>made / lost</span><span class=v id=ml></span></div>
     <div class=row><span class=k>best / worst trade</span><span class=v id=bw></span></div>
     <div class=row><span class=k>longest losing run</span><span class=v id=streak></span></div>
     <div class=row><span class=k>trades today</span><span class=v id=tt></span></div>
    </div>
    <div class=card><div class=title>by strategy</div><div id=bycoin></div></div>
    <div class=card><div class=title>the lab &middot; ai inventions</div><div id=labbox></div></div>
    <div class=card><div class=title>the brain &middot; ml filter</div><div id=brainsx></div></div>
    <div class=card><div class=title>recent trades</div><div id=recent></div></div>`;
  }
  const st=document.getElementById('st');
  const stCls='pill '+(s.halted?'offp':'on');
  const stTxt=s.halted?('halted &middot; '+(s.halt_reason||'')):(s.running?'running':'paused');
  if(st.className!==stCls)st.className=stCls;
  set('st',stTxt);
  const eqEl=document.getElementById('eq');
  const liveEq=b.equity_live!=null?b.equity_live:(b.equity||0);
  const eqCls='hero'+(liveEq>=(r.start||100)?' green':(liveEq<(r.start||100)?' red':''));
  if(eqEl.className!==eqCls)eqEl.className=eqCls;
  set('eq','$'+liveEq.toFixed(2));
  const floatChip=b.float_pnl?' &middot; <span class="'+(b.float_pnl>=0?'up':'dn')+'">'+(b.float_pnl>=0?'+':'')+b.float_pnl.toFixed(2)+' floating</span>':'';
  set('eqsub',`from $${(r.start||100).toFixed(2)} &middot; ${b.won}/${b.closed} closed &middot; ${wr}% hit${floatChip}`);
  const px6=v=>v!=null?(+v).toPrecision(6):'&mdash;';
  const usd=v=>v==null?'&mdash;':(v>=0?'+':'')+v.toFixed(2);
  const pos=(b.open||[]).map(p=>{
    const has=p.pnl!==undefined&&p.pnl!==null;
    const up=has&&p.pnl>=0;
    const money=has?(up?'+':'')+p.pnl.toFixed(2):'&mdash;';
    const pct=has?(p.pct>=0?'+':'')+p.pct.toFixed(2)+'%':'waiting for a price';
    const acct=has&&p.acct_pct!=null?'acct '+(p.acct_pct>=0?'+':'')+p.acct_pct.toFixed(2)+'%':'';
    const at=has?Math.round(Math.min(100,Math.max(0,(p.at||0)*100))):50;
    const slTxt=px6(p.sl);
    const slUsd=usd(p.sl_usd);
    const tiers=(p.tiers||[]).map(t=>{
      const nm=t.done?`<span class="tname done">${t.name} &#10003;</span>`
                      :`<span class="tname">${t.name} ${t.share}%</span>`;
      const px=t.px!=null?px6(t.px):(t.live?'<span class=up>live</span>':'&mdash;');
      const us=(t.usd!=null?`<span class="tusd ${t.usd>=0?'up':'dn'}">${t.usd>=0?'+':''}${t.usd.toFixed(2)}$</span>`:'');
      return `<div class=tier>${nm}<span class=tpx>${px}</span>${us}</div>`;
    }).join('');
    const tags=[];
    if(p.be)tags.push('<span class="tag on">BE</span>');
    if(p.trail)tags.push('<span class="tag tr">TRAIL</span>');
    if(p.model)tags.push('<span class=tag>'+p.model+'</span>');
    if(p.strategy)tags.push('<span class=tag>'+p.strategy+'</span>');
    const held=p.held_min!=null?(p.held_min>=60?Math.floor(p.held_min/60)+'h '+(p.held_min%60)+'m':p.held_min+'m'):'';
    return `<div class="pos ${has?(up?'up':'dn'):''}">
     <div class=phead>
      <div>
       <span class=sym>${p.sym}<span class="sd ${p.side==='BUY'?'buy':'sell'}">${p.side==='BUY'?'LONG':'SHORT'}</span></span>
       <div class=tagrow>${tags.join('')}<a class=tag href="https://www.tradingview.com/chart/?symbol=BITUNIX%3A${p.sym}">TW</a></div>
      </div>
      <div>
       <div class="pnl ${has?(up?'up':'dn'):''}">${has?'$'+money:'&mdash;'}</div>
       <div class=pnlsub>${pct}${acct?' &middot; '+acct:''}</div>
      </div>
     </div>
     <div class=grid>
      <div class=cell><span class=lbl>entry &middot; ورود</span><span class=val>${px6(p.entry)}</span></div>
      <div class=cell><span class=lbl>live &middot; قیمت</span><span class=val>${p.px!=null?px6(p.px):'&mdash;'}</span></div>
      <div class=cell><span class=lbl>stop &middot; حد ضرر</span><span class=val>${slTxt}</span></div>
      <div class=cell><span class=lbl>stop $ &middot; ضرر در استاپ</span><span class="val ${(p.sl_usd||0)>=0?'up':'dn'}">${slUsd}$</span></div>
      <div class=cell><span class=lbl>locked &middot; قفل‌شده</span><span class="val ${(p.realized||0)>=0?'up':'dn'}">${usd(p.realized)}$</span></div>
      <div class=cell><span class=lbl>lev &middot; اهرم</span><span class=val>${p.lev}x</span></div>
     </div>
     <div>${tiers}</div>
     <div class="${p.side==='SELL'?'track sell':'track'}"><div class=dot style="left:${at}%"></div></div>
     <div class=foot><span>margin <b>$${Math.round(p.margin_usd||0)}</b> &middot; ${(p.margin||0).toFixed(1)}% wallet${held?' &middot; '+held:''}</span><span>updated ${new Date(p.live_ts||Date.now()).toLocaleTimeString()}</span></div>
    </div>`}).join('');
  const posSig=JSON.stringify(b.open||[]);
  if(draw.posSig!==posSig){draw.posSig=posSig;set('posbox',pos)}
  const dp=s.day_pnl_pct||0;
  set('day',`<span class="${dp>=0?'up':'dn'}">${dp>=0?'+':''}${dp.toFixed(2)}%</span>`);
  set('tgt', s.target_pct>0 ? `+${s.target_pct}%` : 'no daily cap &middot; let it run');
  set('ml',`<span class=up>+${(r.gross_win||0).toFixed(2)}</span> / <span class=dn>${(r.gross_loss||0).toFixed(2)}</span>`);
  set('bw',`<span class=up>+${(r.best||0).toFixed(2)}</span> / <span class=dn>${(r.worst||0).toFixed(2)}</span>`);
  set('streak',String(r.streak||0));
  set('tt',String(s.trades_today||0));
  const by=(r.by||[]).map(c=>`<div class=row><span class=k>${c.sym}</span><span class=v>${c.w}/${c.n} &middot; <span class="${c.pnl>=0?'up':'dn'}">${money(c.pnl)}</span></span></div>`).join('')||'<div class=note>nothing closed yet</div>';
  const bySig=JSON.stringify(r.by||[]);
  if(draw.bySig!==bySig){draw.bySig=bySig;set('bycoin',by)}
  const lab=s.lab||{};
  const labTxt=((lab.rows||[]).length
    ?('<div class=row><span class=k>champion</span><span class=v>'+(lab.champ_wr!=null?('wr '+(lab.champ_wr*100).toFixed(0)+'% &middot; pf '+(lab.champ_pf!=null?lab.champ_pf:'&mdash;')+' &middot; '+(lab.champ_n||0)+'t'):'&mdash;')+'</span></div>'
      +(lab.rows||[]).map(r=>{
        const ch=(r.changes||[]).slice(0,2).join(' &middot; ');
        return `<div class=row><span class=k>${r.name||'idea'}</span><span class=v>wr ${r.wr!=null?(r.wr*100).toFixed(0)+'%':'&mdash;'} &middot; pf ${r.pf!=null?r.pf:'&mdash;'} &middot; ${r.n||0}t</span></div>`
         +(ch?`<div class=note style="margin:-2px 0 8px">${ch}</div>`:'')
         +(r.coins?`<div class=note style="margin:-2px 0 8px;color:var(--gold-soft)">${r.coins}</div>`:'');
      }).join(''))
    :'<div class=note>the lab has not run yet &middot; it backtests inventions against the champion and reports what actually wins</div>');
  const gate=lab.as_of_ms?(lab.adopt_gate?'open':'closed'):'';
  const labSig=JSON.stringify(lab||{});
  if(draw.labSig!==labSig){draw.labSig=labSig;set('labbox',labTxt+(gate?`<div class=note>adopt gate ${gate} &middot; an idea goes live only if it clears every guardrail out-of-sample</div>`:''))}
  const br=s.brain||{};
  const lift=br.lifts&&br.lifts['0.40'];
  const brTxt=br.n_train!=null
    ?`<div class=row><span class=k>lessons learned</span><span class=v>${br.n_train} trades</span></div>
      <div class=row><span class=k>time-split exam</span><span class=v>auc ${br.auc!=null?br.auc:'&mdash;'} &middot; base wr ${br.base_wr!=null?(br.base_wr*100).toFixed(0)+'%':'&mdash;'}</span></div>
      <div class=row><span class=k>veto bar 0.40</span><span class=v>${lift?('keeps '+lift.kept_pct+'% at wr '+(lift.wr*100).toFixed(0)+'%'):'&mdash;'}</span></div>
      <div class=note>${br.ready?'the brain is filtering entries by learned win probability':'training &middot; it starts filtering once it has enough lessons'}</div>`
    :'<div class=note>the brain is in training &middot; it will filter entries by learned win probability</div>';
  const brSig=JSON.stringify(br||{});
  if(draw.brSig!==brSig){draw.brSig=brSig;set('brainsx',brTxt)}
  const rec=(r.recent||[]).map(t=>{
    const px=t.entry!=null&&t.exit!=null?(+t.entry).toPrecision(5)+'&rarr;'+(+t.exit).toPrecision(5)+' &middot; ':'';
    return `<div class=row><span class=k>${t.sym} ${t.side==='SELL'?'S':'L'}${t.lot?' '+t.lot:''} &middot; ${t.strat||''}</span><span class=v>${px}${t.reason||''}${t.held!=null?' &middot; '+t.held+'m':''} &middot; <span class="${t.pnl>=0?'up':'dn'}">${money(t.pnl)}</span></span></div>`}).join('')||'<div class=note>nothing closed yet</div>';
  const recSig=JSON.stringify(r.recent||[]);
  if(draw.recSig!==recSig){draw.recSig=recSig;set('recent',rec)}
  set('up','updated '+(s.ts?new Date(s.ts).toLocaleTimeString():'&mdash;'));
  const pw=document.getElementById('power');
  const pwCls=s.running?'stop':'';
  if(pw.className!==pwCls)pw.className=pwCls;
  if(pw.textContent!==(s.running?'pause':'resume'))pw.textContent=s.running?'pause':'resume';
  if(!pw.onclick)pw.onclick=()=>api('/api/power',{}).then(()=>refresh());
  const lb=document.getElementById('lockbtn');
  if(lb&&!lb.onclick)lb.onclick=async()=>{await fetch('/api/lock?t='+T);location.href='/'};
  const rg=document.getElementById('reg');
  if(rg){const n=s.devices||0;const t=n?`face id on ${n} device${n>1?'s':''} &middot; add another`:'set up face id';
    if(rg.textContent!==t)rg.textContent=t;
    if(!rg.onclick)rg.onclick=e=>{e.preventDefault();addFace()}}
}

const b64u=b=>btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\\+/g,'-').replace(/\\//g,'_').replace(/=+$/,'');
const unb=t=>Uint8Array.from(atob(t.replace(/-/g,'+').replace(/_/g,'/')),c=>c.charCodeAt(0));
async function addFace(){
 if(!window.PublicKeyCredential){alert('this browser cannot do Face ID');return}
 try{
  const o=await (await fetch('/webauthn/register-options?t='+T,{method:'POST'})).json();
  if(o.ok===false){alert(o.msg);return}
  const ch=o.challenge;
  const req=Object.assign({},o,{challenge:unb(ch),
   user:Object.assign({},o.user,{id:unb(o.user.id)}),
   excludeCredentials:(o.excludeCredentials||[]).map(c=>Object.assign({},c,{id:unb(c.id)}))});
  const c=await navigator.credentials.create({publicKey:req});
  const r=await fetch('/webauthn/register-verify?t='+T,{method:'POST',
   headers:{'Content-Type':'application/json'},
   body:JSON.stringify({_challenge:ch,id:c.id,rawId:b64u(c.rawId),type:c.type,
    response:{clientDataJSON:b64u(c.response.clientDataJSON),
     attestationObject:b64u(c.response.attestationObject)}})});
  const j=await r.json();alert(j.msg||'done');
 }catch(e){alert('face id setup cancelled or unsupported')}}
function refresh(){api('/api/state?n='+Date.now()).then(j=>{
  if(!j){return}
  if(!j.ok){fail('session expired');return}
  try{draw(j)}catch(e){fail('draw error: '+e.message)}
}).catch(e=>{fail('lost the server: '+e.message)})}
refresh();
setInterval(refresh,3000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh()});
window.addEventListener('focus',refresh);
addEventListener('pageshow',refresh);
</script></body></html>"""


def _login_html(err: str = "") -> str:
    return """<!doctype html><html><head><meta charset=utf-8>
<title>Stratton Oakmont</title>
<meta name=viewport content="width=device-width,initial-scale=1">
<meta name=apple-mobile-web-app-capable content=yes>
<meta name=theme-color content=#0A0A0A>
<link rel="icon" type="image/png" sizes="192x192" href="/icon-192.png">
<link rel="apple-touch-icon" href="/icon-180.png">
<style>
*{box-sizing:border-box}body{margin:0;background:#000;color:#F5F5F0;
 font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text",system-ui,sans-serif;
 display:flex;align-items:center;justify-content:center;min-height:100dvh;padding:28px}
main{width:100%;max-width:290px;text-align:center}
.logo{width:72px;height:72px;border-radius:18px;border:1px solid rgba(212,175,55,.35);
 margin:0 auto 12px;display:block}
.wordmark{font-size:13px;color:#8A8A8F;letter-spacing:.04em;margin:0 0 26px}
button{width:100%;padding:16px;border:0;border-radius:11px;background:#D4AF37;
 color:#000;font-size:16px;font-weight:500;cursor:pointer}
button.fid{display:none;background:none;color:#D4AF37;width:auto;margin:0 auto 4px}
button.fid.on{display:block}
.fid svg{width:52px;height:auto;display:block;margin:0 auto}
form{margin-top:18px}
input{width:100%;padding:15px;border-radius:11px;border:1px solid #1B1B1E;
 background:#0B0B0C;color:#F2F2EE;font-size:16px;text-align:center;outline:0}
.hint{color:#4A4A50;font-size:13px;margin-top:16px}
.e{color:#C41E3A;font-size:14px;margin-top:14px;min-height:18px}
</style></head><body><main>
<img class=logo src="/logo.png" alt="Stratton Oakmont">
<div class=wordmark>Stratton Oakmont</div>
<button id=fid class=fid aria-label="Sign in with Face ID"><svg viewBox="0 0 64 78" xmlns="http://www.w3.org/2000/svg"><path class=shackle d="M18 32V21a14 14 0 0 1 28 0v11" fill="none" stroke="currentColor" stroke-width="7" stroke-linecap="round"/><rect x="6" y="32" width="52" height="42" rx="9" fill="currentColor"/><circle class=keyhole cx="32" cy="49" r="5" fill="#000"/><rect class=keyhole x="30" y="52" width="4" height="11" rx="2" fill="#000"/></svg></button>
<form method=POST action=/login>
 <input name=token id=token type=password placeholder="password" autocomplete=current-password autofocus>
 <button style="margin-top:10px" type=submit>Enter</button>
</form>
<div class=hint id=hint></div>
<div class=e id=err>__ERR__</div>
<script>
const $=i=>document.getElementById(i);
const b64u=b=>btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\\+/g,'-').replace(/\\//g,'_').replace(/=+$/,'');
const unb=t=>Uint8Array.from(atob(t.replace(/-/g,'+').replace(/_/g,'/')),c=>c.charCodeAt(0));
(async()=>{
 if(!window.PublicKeyCredential){$('hint').textContent='sign in with your password';$('token').focus();return}
 let o=null;
 const refresh=async()=>{try{o=await (await fetch('/webauthn/auth-options',{cache:'no-store'})).json()}catch(e){}};
 await refresh();
 if(!o||o.none){$('hint').textContent='sign in with your password';$('token').focus();return}
 $('fid').classList.add('on');
 $('hint').textContent='tap the lock, or type your password';
 const go=async()=>{
  try{
   const ch=o.challenge;
   const req=Object.assign({},o,{challenge:unb(ch),
    allowCredentials:(o.allowCredentials||[]).map(c=>({type:'public-key',id:unb(c.id),transports:['internal']})),
    userVerification:'required'});
   const c=await navigator.credentials.get({publicKey:req});
   const r=await fetch('/webauthn/auth-verify',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({_challenge:ch,id:c.id,rawId:b64u(c.rawId),type:c.type,
     response:{clientDataJSON:b64u(c.response.clientDataJSON),
      authenticatorData:b64u(c.response.authenticatorData),
      signature:b64u(c.response.signature),
      userHandle:c.response.userHandle?b64u(c.response.userHandle):null}})});
   const j=await r.json();
   if(j.ok){location.href='/?t='+j.t;return}
   $('err').textContent=j.msg||'not recognised';refresh();
  }catch(e){$('err').textContent='';refresh()}};
 $('fid').onclick=e=>{e.preventDefault();go()};
 addEventListener('pageshow',refresh);
})();
</script></main></body></html>""".replace("__ERR__", err)


# A stalled client must never be able to freeze the app.  The listening
# socket used to be wrapped in TLS, which performs the handshake inside the
# single-threaded accept loop: one phone that opened a connection and never
# sent its ClientHello (iOS does this when the network flaps) blocked every
# other request until the watchdog restarted the service.  The handshake now
# happens per accepted connection, with a deadline, so a dead peer can only
# ever occupy its own thread.
HANDSHAKE_TIMEOUT_S = 5
REQUEST_TIMEOUT_S = 30


class SecureServer(ThreadingHTTPServer):
    """Threaded HTTP(S) server whose TLS handshake happens INSIDE the
    per-connection worker thread.

    The listening socket is deliberately never wrapped: accept() stays
    instant, so a client that connects and never sends its ClientHello
    (iOS opens speculative connections when the network flaps) occupies
    only its own thread instead of stalling every other request.  Each
    connection also carries a deadline, so no socket can wait forever.
    """

    daemon_threads = True
    request_queue_size = 64
    ssl_context: "ssl.SSLContext | None" = None

    def get_request(self):
        sock, addr = self.socket.accept()      # instant -- no TLS here
        return sock, addr

    def process_request(self, request, client_address):
        threading.Thread(target=self._serve_connection,
                         args=(request, client_address),
                         daemon=True).start()

    def _serve_connection(self, sock, addr):
        if self.ssl_context is not None:
            sock.settimeout(HANDSHAKE_TIMEOUT_S)
            try:
                sock = self.ssl_context.wrap_socket(sock, server_side=True)
            except (ssl.SSLError, OSError):
                self.shutdown_request(sock)
                return
            sock.settimeout(REQUEST_TIMEOUT_S)
        try:
            self.finish_request(sock, addr)
        except Exception:                      # noqa: BLE001
            self.handle_error(sock, addr)
        finally:
            self.shutdown_request(sock)


class Handler(BaseHTTPRequestHandler):
    server_version = "scalper-app"
    protocol_version = "HTTP/1.1"
    timeout = REQUEST_TIMEOUT_S          # never wait forever on a socket

    def setup(self):
        super().setup()
        # a stalled TLS handshake / slow client must not hold a thread
        # forever and starve the accept queue (that is what froze the app
        # for the phone)
        self.connection.settimeout(60)
        # every response ends with a close: HTTP/1.1 keep-alive responses
        # without Content-Length (e.g. the /login 302) made clients wait
        # for EOF while the server waited for the next request -- the
        # login "hang". Always close instead.
        self.close_connection = True

    def _finish_headers(self, status: int, ctype: str, body: bytes,
                        extra: list[tuple[str, str]] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        for k, v in extra or []:
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass

    # ------------------------------------------------------------- auth
    def _authed(self) -> bool:
        q = parse_qs(urlparse(self.path).query)
        t = (q.get("t") or [""])[0]
        if not t:
            cookie = self.headers.get("Cookie", "")
            for part in cookie.split(";"):
                part = part.strip()
                if part.startswith("scalper_s="):
                    t = part.split("=", 1)[1]
        if t and SESSIONS.get(t, 0) > time.time():
            return True
        return False

    def _json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj, default=float).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control",
                         "no-store, no-cache, must-revalidate")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _new_session(self) -> str:
        t = secrets.token_urlsafe(24)
        SESSIONS[t] = time.time() + SESSION_HOURS * 3600
        _save_sessions()
        return t

    def _send_html(self, body: bytes, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ------------------------------------------------------------- routes
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/sw.js":
            # kill-switch service worker: wipes the OLD app's cached pages
            # and unregisters itself so the new UI always loads fresh
            body = (b"self.addEventListener('install',e=>{self.skipWaiting()});"
                    b"self.addEventListener('activate',e=>{e.waitUntil((async()=>{"
                    b"const keys=await caches.keys();"
                    b"await Promise.all(keys.map(k=>caches.delete(k)));"
                    b"const cs=await clients.matchAll();"
                    b"cs.forEach(c=>c.navigate(c.url));"
                    b"self.registration.unregister();"
                    b"})())});")
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.send_header("Service-Worker-Allowed", "/")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/manifest.json":
            body = json.dumps({
                "name": "Stratton Oakmont", "short_name": "Stratton",
                "start_url": "/", "display": "standalone",
                "background_color": "#000000", "theme_color": "#000000",
                "icons": [{"src": "/icon-192.png", "sizes": "192x192",
                           "type": "image/png"},
                          {"src": "/icon-512.png", "sizes": "512x512",
                           "type": "image/png"}],
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/manifest+json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path in ("/logo.png", "/icon-512.png", "/icon-192.png",
                    "/icon-180.png", "/icon-1024.png"):
            f = ROOT / path.lstrip("/")
            if f.exists():
                body = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Cache-Control", "public, max-age=86400")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_error(404)
            return
        if path in ("/", "/login"):
            if self._authed():
                return self._send_html(_page_html().encode())
            return self._send_html(_login_html().encode())
        if path == "/api/state":
            if not self._authed():
                return self._json({"ok": False}, 403)
            return self._json(_state_payload())
        if path == "/api/lock":
            q = parse_qs(urlparse(self.path).query)
            SESSIONS.pop((q.get("t") or [""])[0], None)
            _save_sessions()
            return self._json({"ok": True})
        if path == "/webauthn/auth-options":
            try:
                from webauthn import generate_authentication_options, options_to_json
                from webauthn.helpers.structs import (PublicKeyCredentialDescriptor,
                                                      UserVerificationRequirement)
                creds = _load_creds()
                if not creds:
                    return self._json({"none": True})
                ch = secrets.token_urlsafe(32)
                CHALLENGES[ch] = (time.time() + 300, "auth")
                opts = generate_authentication_options(
                    rp_id=RP_ID, challenge=ch.encode(),
                    allow_credentials=[
                        PublicKeyCredentialDescriptor(id=unb64u(c["id"]))
                        for c in creds],
                    user_verification=UserVerificationRequirement.REQUIRED)
                return self._json(json.loads(options_to_json(opts)))
            except Exception:
                return self._json({"none": True})
        self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode()
        if path == "/login":
            # rate-limit password guessing: 8 failures / 10 min per IP
            ip = self.client_address[0]
            now = time.time()
            fails = LOGIN_FAILS.setdefault(ip, [])
            fails[:] = [t for t in fails if now - t < 600]
            if len(fails) >= 8:
                return self._json({"ok": False, "msg": "too many attempts"},
                                  429)
            tok = (parse_qs(raw).get("token") or [""])[0]
            if TOKEN and secrets.compare_digest(tok, TOKEN):
                LOGIN_FAILS.pop(ip, None)
                t = self._new_session()
                self.send_response(302)
                self.send_header("Set-Cookie",
                                 f"scalper_s={t}; Path=/; HttpOnly; Max-Age={int(SESSION_HOURS*3600)}")
                self.send_header("Location", f"/?t={t}")
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()
                return
            fails.append(now)
            # logged so fail2ban can ban the source (and so an audit can
            # see guessing): "login failed from <ip>"
            print(f"login failed from {ip} ({len(fails)}/8 in 10 min)",
                  flush=True)
            return self._send_html(_login_html("wrong password").encode(), 401)
        if path == "/api/power":
            if not self._authed():
                return self._json({"ok": False}, 403)
            if PAUSED.exists():
                PAUSED.unlink()
            else:
                PAUSED.write_text(str(int(time.time())))
            return self._json({"ok": True, "paused": PAUSED.exists()})
        if path == "/webauthn/register-options":
            if not self._authed():
                return self._json({"ok": False, "msg": "sign in first"}, 403)
            try:
                from webauthn import generate_registration_options, options_to_json
                from webauthn.helpers.structs import (AuthenticatorSelectionCriteria,
                                                      UserVerificationRequirement)
                creds = _load_creds()
                ch = secrets.token_urlsafe(32)
                CHALLENGES[ch] = (time.time() + 300, "register")
                opts = generate_registration_options(
                    rp_id=RP_ID, rp_name="Stratton",
                    user_id="scalper-operator".encode(),
                    user_name="operator",
                    exclude_credentials=[
                        {"id": c["id"], "type": "public-key"} for c in creds],
                    authenticator_selection=AuthenticatorSelectionCriteria(
                        user_verification=UserVerificationRequirement.REQUIRED))
                return self._json(json.loads(options_to_json(opts)))
            except Exception as e:
                return self._json({"ok": False, "msg": str(e)[:120]})
        if path == "/webauthn/register-verify":
            if not self._authed():
                return self._json({"ok": False, "msg": "sign in first"}, 403)
            try:
                from webauthn import verify_registration_response
                body = json.loads(raw)
                ch = body.get("_challenge")
                rec = CHALLENGES.get(ch)
                if not rec or rec[1] != "register" or rec[0] < time.time():
                    return self._json({"ok": False, "msg": "challenge expired"})
                vr = None
                for origin in (ORIGIN, "https://62.60.198.135",
                               "https://62.60.198.135:443"):
                    try:
                        vr = verify_registration_response(
                            credential=body, expected_challenge=unb64u(ch),
                            expected_origin=origin, expected_rp_id=RP_ID)
                        break
                    except Exception:
                        continue
                if vr is None:
                    return self._json({"ok": False, "msg": "face id origin mismatch"})
                creds = _load_creds()
                creds.append({"id": b64u(vr.credential_id),
                              "pk": b64u(vr.credential_public_key),
                              "sign_count": vr.sign_count})
                _save_creds(creds)
                CHALLENGES.pop(ch, None)
                return self._json({"ok": True, "msg": "face id registered"})
            except Exception as e:
                return self._json({"ok": False, "msg": str(e)[:160]})
        if path == "/webauthn/auth-verify":
            try:
                from webauthn import verify_authentication_response
                body = json.loads(raw)
                ch = body.get("_challenge")
                rec = CHALLENGES.get(ch)
                if not rec or rec[1] != "auth" or rec[0] < time.time():
                    return self._json({"ok": False, "msg": "challenge expired"})
                match = next((c for c in _load_creds() if c["id"] == body.get("rawId")
                              or c["id"] == body.get("id")), None)
                if match is None:
                    return self._json({"ok": False, "msg": "device not recognised"})
                # the phone may reach the app via the bare IP or the nip.io
                # hostname; accept either origin for the same rpId
                va = None
                for origin in (ORIGIN, "https://62.60.198.135",
                               "https://62.60.198.135:443"):
                    try:
                        va = verify_authentication_response(
                            credential=body, expected_challenge=unb64u(ch),
                            expected_origin=origin, expected_rp_id=RP_ID,
                            credential_public_key=unb64u(match["pk"]),
                            credential_current_sign_count=match.get("sign_count", 0))
                        break
                    except Exception:
                        continue
                if va is None:
                    return self._json({"ok": False, "msg": "face id origin mismatch"})
                creds = _load_creds()
                for c in creds:
                    if c["id"] == match["id"]:
                        c["sign_count"] = va.new_sign_count
                _save_creds(creds)
                CHALLENGES.pop(ch, None)
                t = self._new_session()
                body = json.dumps({"ok": True, "t": t}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Set-Cookie",
                                 f"scalper_s={t}; Path=/; HttpOnly; Max-Age={int(SESSION_HOURS*3600)}")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            except Exception as e:
                return self._json({"ok": False, "msg": str(e)[:160]})
        self.send_error(404)


def main() -> None:
    port = int(os.getenv("SCALPER_APP_PORT", "443"))
    host = os.getenv("SCALPER_APP_HOST", "0.0.0.0")
    cert = os.getenv("SCALPER_APP_CERT")
    key = os.getenv("SCALPER_APP_KEY")
    if not TOKEN:
        print("set SCALPER_APP_TOKEN in .env first")
        raise SystemExit(1)
    ctx = None
    if cert and key and os.path.exists(cert):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(cert, key)
    httpd = SecureServer((host, port), Handler)
    httpd.ssl_context = ctx                 # None -> plain HTTP
    if ctx is not None:
        print(f"scalper app on https://{host}:{port} "
              f"(handshake timeout {HANDSHAKE_TIMEOUT_S}s, "
              f"request timeout {REQUEST_TIMEOUT_S}s)")
    else:
        print(f"scalper app on http://{host}:{port} (no TLS)")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
