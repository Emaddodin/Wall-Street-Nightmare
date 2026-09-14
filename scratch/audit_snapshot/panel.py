#!/usr/bin/env python3
"""
A small control panel for the bot, meant for a phone.

Deliberately cannot enable live trading. Everything else it does is reversible
-- stopping the bot, swapping coins, moving the target or the stop -- and none
of it moves money on its own. Turning real orders on stays on the SSH command
line, where it takes a typed sentence, because a button on a web page reachable
from anywhere is the wrong place for that switch.

Access is a long random token in the URL. That is thin protection on plain
HTTP: anyone who sees the link can drive the bot. Treat the link like a key.
"""
from __future__ import annotations
import base64
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import socket
import ssl
import logging
from collections import defaultdict

# The panel had no logger at all, so anything it wanted to say about itself had
# nowhere to go -- and the push code added here referred to one that did not
# exist, which would have raised on the first phone that subscribed. journalctl
# already carries this service's stdout, so it goes there.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("panel")
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
_BOT = str(__import__("pathlib").Path(__file__).resolve().parent)

BOT = _BOT

# .env was being loaded from inside a function, so anything that ran before
# that function was ever called saw an empty environment -- which is why the
# notification endpoint refused the book's own messages as unauthenticated,
# silently, from both ends. Environment belongs at import.
try:
    from dotenv import load_dotenv as _load_env
    _load_env(f"{_BOT}/.env")
except Exception:
    pass
UNIT = "/etc/systemd/system/tbt-paper.service"
PY = "/home/tbt/venv/bin/python"
# The token lives beside the engine unless a writable home says
# otherwise. The literal /root path meant this module could not
# even be imported anywhere but the server, as root.
TOKEN_FILE = os.path.join(
    os.path.expanduser("~") if os.access(os.path.expanduser("~"), os.W_OK)
    else _BOT, ".tbt_panel_token")
# Let's Encrypt, renewed by certbot; the self-signed pair stays as a
# fallback so the panel still starts if a renewal ever fails.
CERT = "/etc/letsencrypt/live/62.60.198.135.nip.io/fullchain.pem"
KEY = "/etc/letsencrypt/live/62.60.198.135.nip.io/privkey.pem"
if not os.path.exists(CERT):
    CERT, KEY = "/root/.tbt_panel_cert.pem", "/root/.tbt_panel_key.pem"
PORT = int(os.getenv("PANEL_PORT", "443"))
# Wrong guesses per address before it is refused for a while. The token is 24
# random bytes, so this is belt and braces -- but an open port on the internet
# gets knocked on constantly and there is no reason to answer.
MAX_TRIES, LOCK_SECS = 8, 900
_tries = defaultdict(list)
_sessions: dict[str, float] = {}
SESSION_HOURS = 2
sys.path.insert(0, BOT)

_cache: dict = {"t": 0, "coins": []}
SCAN_OUT = _BOT + "/data/scan.txt"
AUTO_FILE = _BOT + "/data/autopilot.json"
AUTO_EVERY = 3600

# The chart window the scout owns.
#
# It changes coin every few seconds by design, so it can never be "on" any
# particular one. Autopilot compared the live symbols of BOTH windows against
# the two it wanted, found a difference every single time -- because window 1
# was showing whatever the scout was visiting that second -- and switched. A
# switch restarts the book.
#
# So the book was restarted every hour, for a goal that could not be reached.
# With --fill-bars 8 on a 15m chart an order rests for two hours, which means
# no resting order ever survived long enough to fill. In the whole life of
# this book, not one has.
SCOUT_WINDOW = 1
_scan = {"running": False, "started": 0.0}


# Autopilot no longer chooses the chart coins, and its default is off.
#
# perch.py owns data/chart_coins.json now: every two minutes it parks the
# book's window on the ripest high-ATR coin the scout has found. Autopilot
# wrote the same file once an hour from data/eligible.json -- the old
# scanner's ranking, not the ATR watchlist -- and enforced its choice with
# `tbtctl coins`, which RESTARTS the book.
#
# So the two fought over one file, and the loser's move cost a restart. On
# 2026-09-05 at 23:08:52 autopilot wrote COLLECTUSDT and restarted the book
# and the guard; sixty-two seconds later perch wrote NOMUSDT over it. That is
# the same hourly restart that once meant no resting order in the whole life
# of this book ever survived long enough to fill.
#
# The default is off rather than on so that a missing or unreadable
# autopilot.json cannot quietly hand the charts back to the loser.
def auto_state():
    st = {"on": False, "last_run": 0, "last_switch": 0, "note": ""}
    try:
        st.update(json.load(open(AUTO_FILE)))
    except Exception:
        pass
    return st


def auto_save(d):
    json.dump(d, open(AUTO_FILE, "w"))


def perch_owns_charts() -> bool:
    """Is the eagle running? Then nothing else may choose the chart coins."""
    return sh("systemctl", "is-active", "tbt-perch.timer") == "active"


def autopilot():
    """Rescan on the hour, then take the top two if it is safe to move."""
    while True:
        st = auto_state()
        due = st.get("last_run", 0) + AUTO_EVERY
        if time.time() < due or _scan["running"]:
            time.sleep(20)
            continue
        try:
            _scan.update(running=True, started=time.time())
            out = ctl("find", timeout=1500)
        except Exception as e:
            # The failure used to be swallowed here: `last_run` was stamped
            # anyway and the old text stayed on screen, so a scan that never
            # ran looked exactly like one that had just finished.
            out = f"scan FAILED: {e}"
        finally:
            _scan["running"] = False
            try:
                with open(SCAN_OUT, "w") as fh:
                    fh.write(f"@{int(time.time())}\n{out}")
            except Exception:
                pass
        st = auto_state()
        st["last_run"] = int(time.time())
        st["note"] = ""
        try:
            best = [r["sym"] for r in json.load(open(_BOT + "/data/eligible.json"))][:2]
        except Exception:
            best = []
        if perch_owns_charts():
            # Not even when the toggle says on: see auto_state() above. The
            # eagle picks the coin, and it does it without restarting anything.
            st["note"] = "perch owns the charts -- auto switching stands down"
        elif not st.get("on", False):
            st["note"] = "auto switching is off"
        elif len(best) < 2:
            st["note"] = "not enough eligible coins to choose from"
        else:
            try:
                book = json.load(open(f"{_BOT}/data/paper.json"))
                open_now = [t for t in book["trades"] if not t.get("closed")]
            except Exception:
                open_now = []
            # Only the windows that stand still. Comparing the scout's is
            # what made this switch every hour forever.
            cur = held_coins()
            want = best[:max(1, len(cur))]
            if open_now:
                st["note"] = (f"holding {open_now[0]['sym']}, "
                              f"will move to {' + '.join(best)} once it closes")
            elif sorted(cur) == sorted(want):
                st["note"] = ("already on " + " + ".join(want)
                              if want else "nothing to hold")
            else:
                set_wanted(want)
                r = ctl("coins", *want, timeout=600)
                st["last_switch"] = int(time.time())
                st["note"] = (f"moved to {' + '.join(want)}"
                              if "Now on" in r else f"switch failed: {r[-90:]}")
                _cache["t"] = 0
        auto_save(st)




def scan_worker():
    _scan.update(running=True, started=time.time())
    try:
        out = ctl("find", timeout=1500)
    except Exception as e:
        out = f"scan FAILED: {e}"
    finally:
        _scan["running"] = False
        try:
            with open(SCAN_OUT, "w") as fh:
                fh.write(f"@{int(time.time())}\n{out}")
        except Exception:
            pass


def scan_status():
    txt = ""
    try:
        txt = open(SCAN_OUT).read()[-2500:]
    except OSError:
        pass
    # The server runs on UTC. Writing its own wall clock into the text made a
    # scan that had just finished read as four hours old on a phone in Tehran.
    # Send the instant, and let the browser say it in the reader's own time.
    at = 0
    if txt.startswith("@"):
        head, _, rest = txt.partition("\n")
        try:
            at = int(head[1:])
            txt = rest
        except ValueError:
            pass
    return {"running": _scan["running"],
            "since": int(time.time() - _scan["started"]) if _scan["running"] else 0,
            "at": at, "text": txt}


def token() -> str:
    if os.path.exists(TOKEN_FILE):
        t = open(TOKEN_FILE).read().strip()
        if t:
            return t
    t = secrets.token_urlsafe(24)
    open(TOKEN_FILE, "w").write(t)
    os.chmod(TOKEN_FILE, 0o600)
    return t


TOKEN = token()
CREDS = "/root/.tbt_panel_creds.json"
PASS_FILE = "/root/.tbt_panel_pass.json"


def check_password(plain: str) -> bool:
    try:
        d = json.load(open(PASS_FILE))
    except Exception:
        return False
    # maxmem must be given explicitly: OpenSSL defaults below what these
    # parameters need and raises rather than allocating.
    h = hashlib.scrypt(plain.encode(), salt=bytes.fromhex(d["salt"]),
                       n=2 ** 15, r=8, p=1, dklen=32,
                       maxmem=96 * 1024 * 1024)
    return secrets.compare_digest(h.hex(), d["hash"])


def has_password() -> bool:
    return os.path.exists(PASS_FILE)
RP_ID = os.getenv("PANEL_RPID", "62.60.198.135.nip.io")
ORIGIN = f"https://{RP_ID}"
_challenges: dict[str, float] = {}


def creds() -> list[dict]:
    try:
        return json.load(open(CREDS))
    except Exception:
        return []


def save_creds(c):
    json.dump(c, open(CREDS, "w"))
    os.chmod(CREDS, 0o600)


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def unb64u(t: str) -> bytes:
    return base64.urlsafe_b64decode(t + "=" * (-len(t) % 4))


def new_session() -> str:
    sid = secrets.token_urlsafe(32)
    _sessions[sid] = time.time() + SESSION_HOURS * 3600
    # Keep the table from growing without bound if someone logs in repeatedly.
    for k, exp in list(_sessions.items()):
        if exp < time.time():
            _sessions.pop(k, None)
    return sid


def session_ok(sid: str) -> bool:
    exp = _sessions.get(sid or "")
    return bool(exp and exp > time.time())


def log_fail(ip: str):
    _tries[ip].append(time.time())


def locked(ip: str) -> bool:
    hits = [t for t in _tries[ip] if t > time.time() - LOCK_SECS]
    _tries[ip] = hits
    return len(hits) >= MAX_TRIES


LOGIN = '<!doctype html><html><head><meta charset=utf-8><title>Stratton Oakmont</title><link rel="apple-touch-icon" href="/icon-180.png"><link rel="apple-touch-icon" sizes="180x180" href="/icon-180.png"><link rel="icon" type="image/png" sizes="192x192" href="/icon-192.png"><link rel="manifest" href="/manifest.json"><meta name="theme-color" content="#0A0A0A"><meta name="mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="black"><meta name="apple-mobile-web-app-title" content="Stratton">\n\n<meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover">\n<style>\n*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}\nbody{margin:0;background:#000;color:#F5F5F0;font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",system-ui,sans-serif;\n display:flex;align-items:center;justify-content:center;min-height:100vh;\n min-height:100dvh;padding:28px}\nmain{width:100%;max-width:290px;text-align:center}\n.wordmark{font-size:13px;color:#8A8A8F;letter-spacing:.04em;\n margin:0 0 30px}\n.note{width:118px;height:auto;margin:0 auto 10px;display:block;overflow:visible}\n.flut{transform-origin:14px 28px;animation:wind 5.5s ease-in-out infinite}\n@keyframes wind{\n 0%{transform:rotate(-2.5deg) skewY(1.4deg) translateY(0)}\n 28%{transform:rotate(1.6deg) skewY(-1.8deg) translateY(-2px)}\n 55%{transform:rotate(-1.1deg) skewY(1.9deg) translateY(1px)}\n 78%{transform:rotate(2.1deg) skewY(-1.2deg) translateY(-1px)}\n 100%{transform:rotate(-2.5deg) skewY(1.4deg) translateY(0)}}\n@media(prefers-reduced-motion:reduce){.flut{animation:none}}\n\nbutton{width:100%;padding:16px;border:0;border-radius:11px;\n background:#D4AF37;color:#000;font-family:inherit;font-size:16px;\n font-weight:500;cursor:pointer;transition:background .1s}\nbutton:active{background:#C9A227}\n.lock{width:112px;height:auto;padding:0;background:none;color:#D4AF37;\n margin:6px auto 0;display:block;transition:transform .12s,color .2s}\n.lock svg{width:100%;height:auto;display:block}\n.lock:active{background:none;transform:scale(.94)}\n.lock .shackle{transform-origin:18px 32px;transition:transform .42s\n cubic-bezier(.34,1.56,.64,1)}\n.lock.open{color:#00FF9F}\n.lock.open .shackle{transform:translateX(11px) rotate(11deg)}\n.lock.no{animation:shake .34s}\n@keyframes shake{0%,100%{transform:translateX(0)}\n 22%{transform:translateX(-7px)}62%{transform:translateX(7px)}}\na{display:inline-block;margin-top:22px;color:#555;font-size:14px;\n text-decoration:none}\na:active{color:#A0A0A0}\nform{display:none;margin-top:20px}\nform.on{display:block}\ninput{width:100%;padding:15px;border-radius:11px;border:1px solid #1B1B1E;\n background:#0B0B0C;color:#F2F2EE;font-size:16px;font-family:ui-monospace,SFMono-Regular,"SF Mono",Menlo,monospace;\n outline:0;text-align:center}\ninput:focus{border-color:#D4AF37}\n.hint{color:#4A4A50;font-size:13px;margin-top:20px;display:none}\n.e{color:#C41E3A;font-size:14px;margin-top:14px;min-height:18px}\nbody{cursor:pointer}\n</style></head><body><main>\n<svg class=note viewBox="0 0 120 56" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#E8D48B"/><stop offset=".45" stop-color="#D4AF37"/><stop offset="1" stop-color="#8f7420"/></linearGradient></defs><g class=flut><path d="M4 12c22-7 44 5 66-1s34-6 46-2v33c-12-4-24-4-46 2s-44-6-66 1z" fill="url(#g)"/><path d="M11 18c20-6 40 4 60-1s31-5 42-2v20c-11-3-22-3-42 2s-40-5-60 1z" fill="none" stroke="#0A0A0A" stroke-width="1.1" opacity=".55"/><ellipse cx="60" cy="28" rx="13" ry="11" fill="#0A0A0A" opacity=".14"/><text x="60" y="34" text-anchor="middle" font-family="Georgia,serif" font-size="19" font-weight="700" fill="#0A0A0A" opacity=".8">$</text><text x="20" y="32" font-family="Georgia,serif" font-size="9" font-weight="700" fill="#0A0A0A" opacity=".45">1</text><text x="98" y="32" font-family="Georgia,serif" font-size="9" font-weight="700" fill="#0A0A0A" opacity=".45">1</text></g></svg>\n<div class=wordmark>Stratton Oakmont</div>\n<button id=fid class=lock aria-label="Sign in with Face ID"><svg viewBox="0 0 64 78" xmlns="http://www.w3.org/2000/svg"><path class=shackle d="M18 32V21a14 14 0 0 1 28 0v11" fill="none" stroke="currentColor" stroke-width="7" stroke-linecap="round"/><rect x="6" y="32" width="52" height="42" rx="9" fill="currentColor"/><circle class=keyhole cx="32" cy="49" r="5" fill="#000"/><rect class=keyhole x="30" y="52" width="4" height="11" rx="2" fill="#000"/></svg></button>\n<a href=# id=useTok>Use password</a>\n<form method=POST action=/login id=tokform>\n <input name=token type=password placeholder="password"\n  autocomplete=current-password>\n <button style="margin-top:10px">Enter</button>\n</form>\n<div class=hint id=hint>unlock with Face ID</div>\n<div class=e id=err>__ERR__</div>\n<script>\nconst $=i=>document.getElementById(i);\n$(\'useTok\').onclick=e=>{e.preventDefault();$(\'tokform\').classList.add(\'on\');\n $(\'useTok\').style.display=\'none\';$(\'tokform\').querySelector(\'input\').focus()};\nconst b64u=b=>btoa(String.fromCharCode(...new Uint8Array(b)))\n .replace(/\\+/g,\'-\').replace(/\\//g,\'_\').replace(/=+$/,\'\');\nconst unb=t=>Uint8Array.from(atob(t.replace(/-/g,\'+\').replace(/_/g,\'/\')),\n c=>c.charCodeAt(0));\n(async()=>{\n if(!window.PublicKeyCredential)return;\n // The challenge is only good for five minutes, and iOS restores a standalone\n // page from cache without re-running this script -- so a reopened app was\n // holding a stale one, the first tap failed, and you had to tap again. Keep it\n // fresh instead: on load, on every return to the page, and on a slow timer.\n let o=null;\n const refresh=async()=>{\n  try{o=await (await fetch(\'/webauthn/auth-options\',{cache:\'no-store\'})).json()}\n  catch(e){}};\n await refresh();\n if(!o||o.none){\n  $(\'err\').textContent=\'no device registered yet — sign in with your password, \'\n   +\'then set up face id\';\n  $(\'tokform\').classList.add(\'on\');$(\'useTok\').style.display=\'none\';return}\n const btn=$(\'fid\');btn.style.display=\'block\';\n $(\'hint\').style.display=\'block\';\n const go=async()=>{\n  try{\n   const ch=o.challenge;\n   const req=Object.assign({},o,{challenge:unb(ch),\n    // Naming the one credential, and saying it lives on this device, is what\n    // lets the browser skip its own picker and show Face ID directly.\n    allowCredentials:(o.allowCredentials||[]).map(c=>({type:\'public-key\',\n     id:unb(c.id),transports:[\'internal\']})),\n    userVerification:\'required\'});\n   const c=await navigator.credentials.get({publicKey:req});\n   const r=await fetch(\'/webauthn/auth-verify\',{method:\'POST\',\n    headers:{\'Content-Type\':\'application/json\'},\n    body:JSON.stringify({_challenge:ch,id:c.id,rawId:b64u(c.rawId),type:c.type,\n     response:{clientDataJSON:b64u(c.response.clientDataJSON),\n      authenticatorData:b64u(c.response.authenticatorData),\n      signature:b64u(c.response.signature),\n      userHandle:c.response.userHandle?b64u(c.response.userHandle):null}})});\n   const j=await r.json();\n   if(j.ok){btn.classList.add(\'open\');\n    setTimeout(()=>location.href=\'/\',380);return}\n   btn.classList.add(\'no\');setTimeout(()=>btn.classList.remove(\'no\'),360);\n   $(\'err\').textContent=j.msg||\'not recognised\';\n   refresh();\n  }catch(e){$(\'err\').textContent=\'\';refresh()}};\n btn.onclick=e=>{e.stopPropagation();go()};\n setInterval(refresh,120000);\n // pageshow fires when Safari restores the app from its back-forward cache,\n // which is exactly the moment the old challenge was already dead.\n addEventListener(\'pageshow\',refresh);\n document.addEventListener(\'visibilitychange\',()=>{\n  if(!document.hidden)refresh()});\n // Any tap counts as the gesture Safari insists on, so the whole screen is\n // the way in. The automatic attempt below works where that is permitted.\n let armed=true;\n document.body.addEventListener(\'click\',()=>{if(armed)go()});\n $(\'useTok\').addEventListener(\'click\',e=>{e.stopPropagation();armed=false});\n $(\'tokform\').addEventListener(\'click\',e=>e.stopPropagation());\n setTimeout(go,120);\n})();\n</script></main></body></html>'

def sh(*cmd, timeout=180):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return "timed out"


def ctl(*args, timeout=180):
    """Swapping coins drives the chart and waits for the study to settle, and
    the scan walks 137 symbols -- both need far longer than a default."""
    return sh("/usr/local/bin/tbtctl", *args, timeout=timeout)


def exec_line():
    t = open(UNIT).read()
    m = re.search(r"ExecStart=(.*?)(?=\n[A-Z][a-zA-Z]*=)", t, re.S)
    return m.group(1) if m else ""


def setting(flag, default="-"):
    m = re.search(re.escape(flag) + r"\s+([0-9.]+)", exec_line())
    return m.group(1) if m else default


def coins():
    """Reading the chart takes a couple of seconds, so it is cached briefly --
    the panel refreshes often and the charts do not change on their own."""
    if time.time() - _cache["t"] < 20 and _cache["coins"]:
        return _cache["coins"]
    try:
        from signals.tv_cdp import TradingViewCDP
        out = []
        for i in range(TradingViewCDP.chart_windows()):
            c = TradingViewCDP(target_index=i)
            try:
                s = str(c.evaluate("window.TradingViewApi.activeChart().symbol()"))
                out.append(s.split(":")[-1].replace(".P", ""))
            except Exception:
                pass
            c.close()
        _cache.update(t=time.time(), coins=out)
        return out
    except Exception:
        return _cache["coins"] or ["?"]


# ---------------------------------------------------------------- push
# Notifications used to go to ntfy.sh, which meant a second app, a public
# topic, and a message that looked nothing like the thing that sent it. These
# go to this app instead: the phone subscribes once, Apple or Google carries
# the message, and it arrives with the same name and icon as the panel.
PUSH_KEYS = f"{_BOT}/data/vapid.json"
PUSH_SUBS = f"{_BOT}/data/push_subs.json"


def vapid():
    """The keypair this server signs pushes with, made once and then kept.

    Rotating it silently unsubscribes every phone, so it is written on first
    use and never touched again.
    """
    try:
        return json.load(open(PUSH_KEYS))
    except Exception:
        pass
    from py_vapid import Vapid01
    import base64
    from cryptography.hazmat.primitives import serialization
    v = Vapid01()
    v.generate_keys()
    pub = v.public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint)
    priv = v.private_key.private_numbers().private_value.to_bytes(32, "big")
    b64 = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")
    d = {"public": b64(pub), "private": b64(priv),
         "claims": {"sub": "mailto:tbt@localhost"}}
    os.makedirs(os.path.dirname(PUSH_KEYS), exist_ok=True)
    with open(PUSH_KEYS, "w") as f:
        json.dump(d, f)
    os.chmod(PUSH_KEYS, 0o600)
    return d


def push_subs():
    try:
        return json.load(open(PUSH_SUBS))
    except Exception:
        return []


def save_subs(subs):
    tmp = PUSH_SUBS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(subs, f)
    os.replace(tmp, PUSH_SUBS)


def push_all(title: str, body: str, tag: str = "tbt") -> int:
    """Send one line to every phone that has subscribed. Returns how many got it.

    A subscription that the push service rejects with 404 or 410 is gone for
    good -- the app was deleted or the browser cleared -- so it is dropped
    rather than retried forever.
    """
    from pywebpush import webpush, WebPushException
    k = vapid()
    subs, keep, sent = push_subs(), [], 0
    for sub in subs:
        try:
            webpush(subscription_info=sub,
                    data=json.dumps({"title": title, "body": body, "tag": tag}),
                    vapid_private_key=k["private"],
                    vapid_claims=dict(k["claims"]),
                    timeout=10)
            sent += 1
            keep.append(sub)
        except WebPushException as e:
            code = getattr(getattr(e, "response", None), "status_code", 0)
            if code in (404, 410):
                log.info("push subscription gone (%s) -- dropped", code)
                continue
            keep.append(sub)
        except Exception:
            keep.append(sub)
    if len(keep) != len(subs):
        save_subs(keep)
    return sent


# Which pair the charts are supposed to be showing. The panel chooses it and
# the guard enforces it, and until now there was nowhere for the first to tell
# the second: the guard held whatever pair was on its command line when it
# started, so every automatic switch was quietly undone a minute later and the
# two fought over the windows forever.
WANTED = f"{_BOT}/data/chart_coins.json"


def set_wanted(syms) -> None:
    syms = [s.upper().replace(".P", "") for s in syms if s]
    if not syms:
        return
    tmp = WANTED + ".tmp"
    with open(tmp, "w") as f:
        json.dump(syms, f)
    os.replace(tmp, WANTED)
    log.info("charts should hold %s", " + ".join(syms))


def held_coins():
    """The coins on the windows that stand still -- the scout's excluded.

    `coins()` answers "what is on the charts", which is the right answer for
    the display and the wrong one for deciding whether to move them: the
    scout's window is never on anything for longer than a few seconds.
    """
    out = []
    try:
        from signals.tv_cdp import TradingViewCDP
        for i in range(TradingViewCDP.chart_windows()):
            if i == SCOUT_WINDOW:
                continue
            c = TradingViewCDP(target_index=i)
            try:
                s = str(c.evaluate(
                    "window.TradingViewApi.activeChart().symbol()"))
                out.append(s.split(":")[-1].replace(".P", ""))
            except Exception:
                pass
            c.close()
    except Exception:
        pass
    return out


def council():
    """What the six members are saying right now, per chart window.

    The same numbers the table in the corner of the chart shows, read off the
    same two packed series the book trades from -- so if the phone and the
    chart ever disagree, one of them is stale rather than wrong.
    """
    out = []
    try:
        import sys as _sys
        _sys.path.insert(0, BOT)
        from papertrade import unpack_state, unpack_votes, COUNCIL
        from signals.tv_cdp import TradingViewCDP
    except Exception:
        return out
    for idx in (0, 1):
        try:
            c = TradingViewCDP(target_index=idx)
            st = [x for x in c.studies() if "TBT" in x["name"]]
            if not st:
                continue
            r = c.raw_series(st[0]["id"], limit=2)
            rows = (r or {}).get("rows") or []
            if not rows:
                continue
            at = {q: i + 1 for i, q in enumerate(r["plots"])}
            row = rows[-1]

            def v(name):
                i = at.get(name)
                if i is None or i >= len(row):
                    return None
                x = row[i]
                return None if x is None or x != x else x

            sv = v("STATE")
            if sv is None:
                continue
            d = unpack_state(sv)
            # raw_series does not always carry the resolution -- the chart
            # itself always does, and a card headed "None m" is worse than no
            # card at all.
            try:
                _st = c.state()
                _sym, _res = _st.symbol, _st.resolution
            except Exception:
                _sym, _res = r.get("symbol") or "", r.get("resolution") or "?"
            out.append({
                "sym": (_sym or "").split(":")[-1].replace(".P", ""),
                "res": _res,
                "dir": d["plan_dir"], "votes": d["votes"], "ready": d["ready"],
                "entry": v("PLAN_ENTRY"),
                "members": unpack_votes(v("VOTES_PACKED") or 0),
                "order": list(COUNCIL),
            })
            c.close()
        except Exception:
            continue
    return out


def report():
    """Everything worth knowing about the book, computed here so the page
    stays dumb."""
    try:
        b = json.load(open(f"{_BOT}/data/paper.json"))
    except Exception:
        return {}
    tr = b.get("trades", [])
    closed = [t for t in tr if t.get("closed")]
    closed.sort(key=lambda t: t.get("closed") or 0)
    wins = [t for t in closed if (t.get("pnl") or 0) > 0]
    losses = [t for t in closed if (t.get("pnl") or 0) <= 0]

    # Per coin, so a bad pair shows itself rather than hiding in the average.
    by = {}
    for t in closed:
        d = by.setdefault(t["sym"], {"n": 0, "w": 0, "pnl": 0.0})
        d["n"] += 1
        d["w"] += 1 if (t.get("pnl") or 0) > 0 else 0
        d["pnl"] += t.get("pnl") or 0

    # The longest run of losses is what actually threatens the account.
    run = worst = 0
    for t in closed:
        run = run + 1 if (t.get("pnl") or 0) <= 0 else 0
        worst = max(worst, run)

    ents = [t.get("entry_err_r") or 0 for t in closed if t.get("entry_kind") == "market"]
    lag = [(t["opened"] - t["bar"]) for t in closed
           if t.get("entry_kind") == "market" and t.get("bar")]
    recent = [{
        "sym": t["sym"], "side": t["side"],
        "reason": t.get("reason") or "",
        "pnl": t.get("pnl") or 0,
        "at": t.get("closed") or 0,
        "held": round(((t.get("closed") or 0) - t["opened"]) / 60, 1),
        "live": bool(t.get("live")),
    } for t in closed[-12:][::-1]]

    return {
        "n": len(closed), "wins": len(wins),
        "gross_win": sum(t.get("pnl") or 0 for t in wins),
        "gross_loss": sum(t.get("pnl") or 0 for t in losses),
        "best": max((t.get("pnl") or 0 for t in closed), default=0),
        "worst": min((t.get("pnl") or 0 for t in closed), default=0),
        "streak": worst,
        "by": [{"sym": k, **v} for k, v in
               sorted(by.items(), key=lambda kv: -kv[1]["pnl"])],
        "entry_err": (sum(ents) / len(ents)) if ents else None,
        "entry_lag": (sum(lag) / len(lag)) if lag else None,
        "recent": recent,
        "start": b.get("start", 100.0),
    }


def ranked():
    """Coins the scan measured, best first. Falls back to whatever is on the
    charts so the pickers are never empty on a fresh box."""
    # eligible.json is the vetted list: measured, liquid, and with enough
    # history for the indicator. screen.json is the raw measurement and will
    # happily rank a coin listed this morning at the top.
    out = []
    try:
        out = [{"sym": r["sym"], "fast": r["fast"], "range": r["range"],
                "atr": r.get("atr"), "lev": r["lev"]}
               for r in json.load(open(_BOT + "/data/eligible.json"))]
    except Exception:
        try:
            for r in json.load(open("/tmp/screen.json")):
                if r.get("n", 0) >= 200 and r.get("range", 0) >= 0.8:
                    out.append({"sym": r["sym"], "fast": round(r.get("fast", 0), 1),
                                "range": round(r.get("range", 0), 2),
                                "lev": r.get("lev", 0)})
        except Exception:
            pass
    out.sort(key=lambda x: -x["fast"])
    have = {o["sym"] for o in out}
    for s in coins():
        if s not in have and s != "?":
            out.insert(0, {"sym": s, "fast": 0, "range": 0, "lev": 0})
    return out[:40]


_px = {"t": 0.0, "v": {}}


def prices() -> dict:
    """Last price per symbol, cached for a few seconds.

    The page refreshes every fifteen seconds and there may be two positions
    open, so one call to the exchange covers everything without adding a
    request per position per refresh.
    """
    if time.time() - _px["t"] < 5 and _px["v"]:
        return _px["v"]
    try:
        sys.path.insert(0, BOT)
        from dotenv import load_dotenv
        load_dotenv(f"{_BOT}/.env")
        from exchange.bitunix import BitunixClient
        v = {t["symbol"]: float(t["lastPrice"]) for t in BitunixClient().tickers()}
        _px.update(t=time.time(), v=v)
    except Exception:
        pass
    return _px["v"]


def mark(t: dict, px: float | None) -> dict:
    """A live view of one open position: what it is worth right now, and how
    far it has travelled from the stop toward the target."""
    out = dict(t)
    if not px:
        return out
    long = t["side"] == "BUY"
    move = (px / t["entry"] - 1) * (1 if long else -1)
    fee = t["notional"] * 12.0 / 1e4          # the same round trip the book charges
    out["px"] = px
    out["pct"] = move * 100
    out["pnl"] = t["notional"] * move - fee
    # 0 sits on the stop, 1 on the target, so the bar says where you stand
    lo, hi = t["sl"], t["tp"]
    span = (hi - lo) if long else (lo - hi)
    out["at"] = max(0.0, min(1.0, ((px - lo) / span) if long else ((lo - px) / span)))
    return out


def state():
    running = sh("systemctl", "is-active", "tbt-paper") == "active"
    live = "--live" in exec_line()
    try:
        b = json.load(open(f"{_BOT}/data/paper.json"))
        closed = [t for t in b["trades"] if t.get("closed")]
        won = sum(1 for t in closed if t.get("reason") == "target")
        opens = [t for t in b["trades"] if not t.get("closed")]
        pv = prices() if opens else {}
        opens = [mark(t, pv.get(t["sym"])) for t in opens]
        # In paper the book only counts closed trades, so what is still open has
        # to be added on. On a real account it must not be: the exchange's own
        # equity is available + margin + unrealised, so the open position is
        # already in that number and adding it again would show the profit
        # twice.
        unreal = 0.0 if live else sum(t.get("pnl", 0) or 0 for t in opens)
        book = {"equity": b["equity"], "closed": len(closed), "won": won,
                "open": opens, "live_equity": b["equity"] + unreal}
    except Exception:
        book = {"equity": 0, "closed": 0, "won": 0, "open": []}
    return {
        "running": running, "live": live, "coins": coins(), "book": book,
        # Both are reported: the fixed pair is only a fallback once the ATR
        # multipliers are set, and showing 2.0%/0.52% while the book actually
        # trades 1xATR would be a lie on the one screen that is meant to say
        # what is running.
        "tp": setting("--tp"), "sl": setting("--sl"),
        "tp_atr": setting("--tp-atr", "0"), "sl_atr": setting("--sl-atr", "0"),
        # The dials the book actually reads now. Without these the settings
        # card describes a shape the engine stopped using, and editing it
        # writes flags nothing looks at.
        "tp_r": setting("--tp-r", "0"), "r_adapt": setting("--r-adapt", "0"),
        "r_min": setting("--r-min", "3"), "r_max": setting("--r-max", "50"),
        "risk_pct": setting("--risk-pct", "0"),
        "lev": setting("--lev"),
        "frac": setting("--frac"), "be": setting("--break-even", "0"),
        "chrome": sh("systemctl", "is-active", "tbt-chrome") == "active",
        "guard": sh("systemctl", "is-active", "tbt-guard") == "active",
        "rep": report(),
        "scan": scan_status(),
        "devices": len(creds()),
        "auto": {**auto_state(),
                 "next_in": max(0, int(auto_state().get("last_run", 0)
                                       + AUTO_EVERY - time.time()))},
        "ranked": ranked(),
        "council": council(),
    }


PAGE = '<!doctype html><html><head><meta charset=utf-8><title>Stratton Oakmont</title><link rel="apple-touch-icon" href="/icon-180.png"><link rel="apple-touch-icon" sizes="180x180" href="/icon-180.png"><link rel="icon" type="image/png" sizes="192x192" href="/icon-192.png"><link rel="manifest" href="/manifest.json"><meta name="theme-color" content="#0A0A0A"><meta name="mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="black"><meta name="apple-mobile-web-app-title" content="Stratton">\n<meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover">\n\n\n\n\n<style>\n:root{\n --bg:#000; --surface:#0B0B0C; --card:#0E0E10;\n --gold:#D4AF37; --gold-press:#C9A227; --gold-soft:#E8D48B;\n --win:#00FF9F; --loss:#C41E3A; --warn:#FFB800; --info:#4A9EFF;\n --txt:#F2F2EE; --txt2:#8A8A8F; --off:#4A4A50; --on-gold:#000;\n --line:#1B1B1E;\n --ui:-apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",system-ui,sans-serif;\n --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,monospace;\n}\n*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}\nbody{margin:0;background:var(--bg);color:var(--txt);font-family:var(--ui);\n font-weight:400;font-size:15px;padding:16px 13px 48px;-webkit-font-smoothing:antialiased;\n }\n.brand{display:flex;flex-direction:column;align-items:flex-start;\n gap:6px;margin:2px 0 0}\n.note{width:104px;height:auto;display:block;overflow:visible}\n.flut{transform-origin:14px 28px;animation:wind 5.5s ease-in-out infinite}\n@keyframes wind{\n 0%{transform:rotate(-2.5deg) skewY(1.4deg) translateY(0)}\n 28%{transform:rotate(1.6deg) skewY(-1.8deg) translateY(-2px)}\n 55%{transform:rotate(-1.1deg) skewY(1.9deg) translateY(1px)}\n 78%{transform:rotate(2.1deg) skewY(-1.2deg) translateY(-1px)}\n 100%{transform:rotate(-2.5deg) skewY(1.4deg) translateY(0)}}\n@media(prefers-reduced-motion:reduce){.flut{animation:none}}\n\n.brand h1{font-family:var(--ui);font-weight:600;font-size:15px;margin:0;\n letter-spacing:.02em;color:var(--txt2);line-height:1.2}\n.brand span{font-family:var(--ui);font-weight:700;font-size:9px;\n letter-spacing:.30em;color:var(--off)}\n.rule{height:1px;margin:14px 0 14px;background:linear-gradient(90deg,rgba(212,175,55,.35),var(--line) 40%,transparent)}\n.card{background:var(--card);border:1px solid var(--line);border-radius:10px;\n padding:15px;margin-bottom:10px;position:relative;overflow:hidden}\n.card.key{border:1px solid rgba(212,175,55,.20)}\n.row{display:flex;justify-content:space-between;align-items:center;\n padding:8px 0;border-bottom:1px solid var(--line);gap:12px}\n.row:last-child{border-bottom:0}\n.k{color:var(--txt2);font-size:12px;font-weight:500;white-space:nowrap}\n.v{font-family:var(--mono);font-weight:700;font-size:13px;text-align:right;color:var(--txt)}\n.hero{font-family:var(--ui);font-weight:700;font-size:44px;line-height:1.05;\n letter-spacing:-.03em;color:var(--gold);margin:8px 0 4px;\n font-variant-numeric:tabular-nums}\n.hero.green{color:var(--win)}.hero.red{color:var(--loss)}\n.sub{font-size:12px;color:var(--txt2);font-weight:500}\n.pill{padding:4px 11px;border-radius:4px;font-size:11px;font-weight:600;\n font-family:var(--ui)}\n.on{background:var(--gold);color:var(--on-gold)}\n.offp{background:#2A2A2A;color:var(--txt2)}\n.livep{background:var(--loss);color:#fff}\nbutton{width:100%;padding:15px;border:0;border-radius:6px;font-family:var(--ui);\n font-size:15px;font-weight:500;\n color:var(--on-gold);background:var(--gold);margin-top:12px;cursor:pointer;\n transition:transform .08s,box-shadow .08s,background .08s;\n box-shadow:0 0 0 rgba(212,175,55,0)}\nbutton:hover{background:var(--gold-soft)}\nbutton:active{transform:scale(.985);background:var(--gold-press)}\nbutton.stop{background:var(--loss);color:#fff}\nbutton.stop:hover{background:#d9243f}\nbutton.ghost{background:transparent;color:var(--gold);\n border:1.5px solid var(--gold)}\nbutton.ghost:hover{background:rgba(212,175,55,.09);color:var(--gold-soft)}\ninput{width:100%;padding:14px;border-radius:5px;border:1.5px solid var(--line);\n background:var(--bg);color:var(--txt);font-size:16px;margin-top:8px;\n font-family:var(--mono);font-weight:700;letter-spacing:.04em;outline:0;\n transition:border-color .1s,box-shadow .1s}\nselect{width:100%;padding:14px;border-radius:5px;border:1.5px solid var(--line);background:var(--bg);color:var(--txt);font-size:16px;margin-top:8px;font-family:var(--mono);font-weight:700;outline:0;appearance:none;background-image:linear-gradient(45deg,transparent 50%,var(--gold) 50%),linear-gradient(135deg,var(--gold) 50%,transparent 50%);background-position:calc(100% - 20px) 22px,calc(100% - 14px) 22px;background-size:6px 6px,6px 6px;background-repeat:no-repeat}\nselect:focus{border-color:var(--gold);box-shadow:0 0 0 3px rgba(212,175,55,.16)}\ninput:focus{border-color:var(--gold);box-shadow:0 0 0 3px rgba(212,175,55,.16)}\ninput::placeholder{color:var(--off);font-weight:400}\nlabel{font-size:12px;color:var(--txt2);display:block;margin-top:14px;\n font-weight:500}\n.g4{display:grid;grid-template-columns:1fr 1fr;gap:10px}\n.note{color:var(--off);font-size:11px;margin-top:12px;line-height:1.5;\n font-weight:500}\n.pos{background:var(--bg);border-left:3px solid var(--gold);border-radius:4px;\n padding:12px 13px;margin-top:10px}\n.pos.up{border-left-color:var(--win)}\n.pos.dn{border-left-color:var(--loss)}\n.pos .top{display:flex;justify-content:space-between;align-items:baseline;gap:10px}\n.pos .who{font-family:var(--ui);font-size:12px;font-weight:600;color:var(--txt2);\n letter-spacing:.02em}\n.pos .amt{font-family:var(--ui);font-size:22px;font-weight:700;\n letter-spacing:-.02em;font-variant-numeric:tabular-nums}\n.pos .sub2{display:flex;justify-content:space-between;margin-top:3px;\n font-family:var(--mono);font-size:11px;color:var(--off)}\n.pos .track{position:relative;height:3px;border-radius:2px;margin:11px 0 5px;\n background:linear-gradient(90deg,rgba(196,30,58,.5),var(--line) 26%,\n var(--line) 74%,rgba(0,255,159,.5))}\n.pos .dot{position:absolute;top:50%;width:9px;height:9px;border-radius:50%;\n transform:translate(-50%,-50%);background:var(--gold);\n box-shadow:0 0 0 3px var(--bg);transition:left .5s ease}\n.pos .ends{display:flex;justify-content:space-between;align-items:center;\n font-family:var(--mono);font-size:10px;color:var(--off)}\n.pos .ends span:nth-child(2){color:var(--txt2);font-weight:700}\n.title{font-family:var(--ui);font-weight:500;font-size:13px;\n color:var(--txt2);margin:0 0 12px}\n#msg{background:rgba(212,175,55,.07);border:1px solid rgba(212,175,55,.32);\n border-left:3px solid var(--gold);border-radius:5px;padding:13px;\n margin-bottom:14px;font-size:12px;display:none;white-space:pre-wrap;\n font-family:var(--mono);color:var(--gold-soft);line-height:1.5}\n.busy{opacity:.55;pointer-events:none;transition:opacity .12s}\n.up{color:var(--win)}.dn{color:var(--loss)}\n.pair{display:grid;grid-template-columns:2fr 1fr;gap:10px}\n.devrow{text-align:right;margin-top:10px;font-size:12px}\n.devrow a{color:var(--off);text-decoration:none}\n.devrow a:active{color:var(--gold)}\n.autorow{display:flex;justify-content:space-between;align-items:flex-start;\n gap:10px;margin-top:12px;font-size:12px;color:var(--off);line-height:1.5}\n.autorow a{color:var(--txt2);text-decoration:none;white-space:nowrap}\n.autorow a:active{color:var(--gold)}\n.pair button{margin-top:12px}\npre.scan{background:var(--bg);border:1px solid var(--line);\n border-left:3px solid var(--gold);border-radius:4px;padding:12px;\n margin-top:12px;font-size:10px;line-height:1.45;overflow-x:auto;\n color:var(--txt2);font-family:var(--mono)}\n@keyframes flash{0%{border-color:var(--line)}\n 35%{border-color:var(--win)}\n 100%{border-color:var(--line)}}\n.win-flash{animation:flash .55s ease-out}\n</style></head><body>\n<div class=brand><svg class=note viewBox="0 0 120 56" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#E8D48B"/><stop offset=".45" stop-color="#D4AF37"/><stop offset="1" stop-color="#8f7420"/></linearGradient></defs><g class=flut><path d="M4 12c22-7 44 5 66-1s34-6 46-2v33c-12-4-24-4-46 2s-44-6-66 1z" fill="url(#g)"/><path d="M11 18c20-6 40 4 60-1s31-5 42-2v20c-11-3-22-3-42 2s-40-5-60 1z" fill="none" stroke="#0A0A0A" stroke-width="1.1" opacity=".55"/><ellipse cx="60" cy="28" rx="13" ry="11" fill="#0A0A0A" opacity=".14"/><text x="60" y="34" text-anchor="middle" font-family="Georgia,serif" font-size="19" font-weight="700" fill="#0A0A0A" opacity=".8">$</text><text x="20" y="32" font-family="Georgia,serif" font-size="9" font-weight="700" fill="#0A0A0A" opacity=".45">1</text><text x="98" y="32" font-family="Georgia,serif" font-size="9" font-weight="700" fill="#0A0A0A" opacity=".45">1</text></g></svg><h1>Stratton Oakmont</h1></div>\n<div class=rule></div>\n<div id=msg></div>\n<div id=app></div>\n<script>\nconst T=new URLSearchParams(location.search).get(\'t\')||\'\';\nlet busy=false,lastEq=null;\nfunction msg(s){const m=document.getElementById(\'msg\');\n m.style.display=s?\'block\':\'none\';m.textContent=s||\'\'}\nasync function api(p,body,quiet){\n // A tap that lands while a slow call is in flight used to vanish without a\n // trace -- switching coins takes minutes, and every press during it did\n // nothing at all, with nothing on screen to say why.\n if(busy){if(!quiet)msg(\'still working on the last one\');return}\n busy=true;\n // Only a tap dims the page. The background refresh must be invisible or it\n // reads as the screen blinking every fifteen seconds.\n if(!quiet)document.body.classList.add(\'busy\');\n try{const r=await fetch(p+(p.includes(\'?\')?\'&\':\'?\')+\'t=\'+T,\n   {method:body?\'POST\':\'GET\',body:body?JSON.stringify(body):null});\n  // The session lasts two hours. When it lapses every call comes back 403 with\n  // an empty body, r.json() throws, and the page used to sit there claiming it\n  // had lost the server. Go back to the door instead.\n  if(r.status===403||r.status===401){location.href=\'/\';return}\n  const j=await r.json();if(j.msg&&!quiet)msg(j.msg);if(j.state)draw(j.state);\n  return j}\n catch(e){if(!quiet)msg(\'lost the server for a moment\')}\n finally{busy=false;document.body.classList.remove(\'busy\')}}\nfunction money(n){return (n>=0?\'+\':\'\')+n.toFixed(2)}\nfunction draw(s){\n const set=(id,html)=>{const e=document.getElementById(id);\n  if(e&&e.innerHTML!==html)e.innerHTML=html};\n const val=(id,v)=>{const e=document.getElementById(id);\n  if(e&&document.activeElement!==e&&e.value!==String(v))e.value=v};\n const r=s.rep||{},b=s.book;\n const wr=b.closed?Math.round(b.won/b.closed*100):0;\n const up=lastEq!==null&&b.equity>lastEq;lastEq=b.equity;\n // the open position is worth something right now, so the headline follows it\n const pos=b.open.map(p=>{\n  const has=p.pnl!==undefined&&p.pnl!==null;\n  const up=has&&p.pnl>=0;\n  const money=has?(up?\'+\':\'\')+p.pnl.toFixed(2):\'\';\n  const pct=has?(p.pct>=0?\'+\':\'\')+p.pct.toFixed(2)+\'%\':\'waiting for a price\';\n  const at=has?Math.round((p.at||0)*100):50;\n  return `<div class="pos ${has?(up?\'up\':\'dn\'):\'\'}">\n   <div class=top>\n    <span class=who>${p.sym} &middot; ${p.side===\'BUY\'?\'LONG\':\'SHORT\'}</span>\n    <span class="amt ${has?(up?\'up\':\'dn\'):\'\'}">${has?\'$\'+money:\'&mdash;\'}</span>\n   </div>\n   <div class=sub2><span>${(+p.entry).toPrecision(6)}${p.px?\' &rarr; \'+(+p.px).toPrecision(6):\'\'}</span><span class="${has?(up?\'up\':\'dn\'):\'\'}">${pct}</span></div>\n   <div class=track><div class=dot style="left:${at}%"></div></div>\n   <div class=ends><span>stop</span><span>${(+p.margin).toFixed(2)} of your wallet in</span><span>target</span></div>\n  </div>`}).join(\'\');\n if(!document.getElementById(\'wallet\')){\n  document.getElementById(\'app\').innerHTML=`\n  <div class="card key" id=wallet>\n   <div class=row style="border:0;padding-bottom:0">\n    <span class=sub>wallet</span><span class=pill id=st></span></div>\n   <div class=hero id=eq></div>\n   <div class=sub id=eqsub></div>\n   <div id=posbox></div>\n   <div class=pair>\n    <button id=power></button>\n    <button class=ghost id=lockbtn>lock</button>\n   </div>\n   <div class=devrow><a href=# id=reg></a></div>\n  </div>\n  <div class="card key"><div class=title>the council</div><div id=council></div></div>\n  <div class=card><div class=title>live</div><div id=charts></div><div class=autorow><span id=pushtxt>notifications</span><span><a href=# id=pushbtn>turn on</a> &middot; <a href="/vnc" target=_blank id=vnclink>open the desktop</a></span></div></div>\n  <div class=card>\n   <div class=title>performance</div>\n   <div class=row><span class=k>made / lost</span><span class=v id=ml></span></div>\n   <div class=row><span class=k>best / worst trade</span><span class=v id=bw></span></div>\n   <div class=row><span class=k>longest losing run</span><span class=v id=streak></span></div>\n   <div class=row><span class=k>entry lag / error</span><span class=v id=lag></span></div>\n   <div class=row><span class=k>chrome / guard</span><span class=v id=svc></span></div>\n  </div>\n  <div class=card><div class=title>by coin</div><div id=bycoin></div></div>\n  <div class=card><div class=title>recent trades</div><div id=recent></div></div>\n  <div class="card key">\n   <div class=title>coins</div>\n   <label>chart 1</label><select id=c1></select>\n   <label>chart 2</label><select id=c2></select>\n   <button onclick="c1.dataset.touched=\'\';c2.dataset.touched=\'\';api(\'/api/coins\',{v:c1.value+\' \'+c2.value})">switch coins</button>\n   <div class=autorow>\n    <span id=autotxt></span>\n    <span><a href=# id=autobtn></a> &middot; <a href=# id=rescan>rescan</a></span>\n   </div>\n   <div class=note id=coinnote></div>\n   <pre class=scan id=scanbox style="display:none"></pre>\n  </div>\n  <div class=card>\n   <div class=title>settings</div>\n   <div class=note id=shapenote></div>\n   <div class=g4>\n    <div><label id=tplab>target</label><input id=tp inputmode=decimal></div>\n    <div><label id=sllab>stop</label><input id=sl inputmode=decimal></div>\n    <div><label id=levlab>leverage</label><input id=lev inputmode=decimal></div>\n    <div><label id=fraclab>size</label><input id=frac inputmode=decimal></div>\n   </div>\n   <label>stop to break even after +%</label><input id=be inputmode=decimal>\n   <button id=applybtn>apply</button>\n  </div>\n`;\n }\n const rg=document.getElementById(\'reg\');\n if(rg){const n=s.devices||0;\n  const t=n?`face id on ${n} device${n>1?\'s\':\'\'} · add another`:\'set up face id\';\n  if(rg.textContent!==t)rg.textContent=t;\n  if(!rg.onclick)rg.onclick=e=>{e.preventDefault();addFace()}}\n const a=s.auto||{};\n const ago=t=>{if(!t)return\'never\';const m=Math.round((Date.now()/1000-t)/60);\n  return m<1?\'just now\':m<60?m+\'m ago\':Math.round(m/60)+\'h ago\'};\n const left=n=>{const m=Math.round(n/60);return m<1?\'any moment\':\'in \'+m+\'m\'};\n set(\'autotxt\',`checked ${ago(a.last_run)} &middot; next ${left(a.next_in||0)}`\n  +(a.note?`<br>${a.note}`:\'\'));\n const ab=document.getElementById(\'autobtn\');\n if(ab){const t=a.on===false?\'auto off\':\'auto on\';\n  if(ab.textContent!==t)ab.textContent=t;\n  if(!ab.onclick)ab.onclick=e=>{e.preventDefault();api(\'/api/autotoggle\')}}\n const rs=document.getElementById(\'rescan\');\n if(rs){const t=(s.scan&&s.scan.running)?(\'scanning \'+s.scan.since+\'s\'):\'rescan\';\n  if(rs.textContent!==t)rs.textContent=t;\n  if(!rs.onclick)rs.onclick=e=>{e.preventDefault();api(\'/api/find\')}}\n const lb=document.getElementById(\'lockbtn\');\n if(lb&&!lb.onclick)lb.onclick=async()=>{\n  await fetch(\'/api/lock?t=\'+T);location.href=\'/\'};\n const st=document.getElementById(\'st\');\n st.className=\'pill \'+(s.live?\'livep\':(s.running?\'on\':\'offp\'));\n set(\'st\',s.live?\'live &middot; real money\':(s.running?\'running\':\'stopped\'));\n const eqEl=document.getElementById(\'eq\');\n const liveEq=(b.live_equity!==undefined?b.live_equity:b.equity);\n eqEl.className=\'hero\'+(liveEq>=(r.start||100)?\' green\':\'\');\n set(\'eq\',\'$\'+liveEq.toFixed(2));\n set(\'eqsub\',`from $${(r.start||100).toFixed(2)} &middot; ${b.won}/${b.closed} closed &middot; ${wr}% hit`);\n set(\'posbox\',pos);\n const pw=document.getElementById(\'power\');\n pw.className=s.running?\'stop\':\'\';\n if(pw.textContent!==(s.running?\'stop\':\'start\'))pw.textContent=s.running?\'stop\':\'start\';\n pw.onclick=()=>api(\'/api/\'+(s.running?\'off\':\'on\'));\n set(\'council\',(s.council||[]).map(w=>{const d=w.dir>0?\'LONG\':w.dir<0?\'SHORT\':\'no plan\';const cl=w.dir>0?\'up\':w.dir<0?\'dn\':\'\';const say=v=>v>0?\'<span class=up>long</span>\':v<0?\'<span class=dn>short</span>\':\'<span style=\"color:var(--off)\">&mdash;</span>\';const rows=w.order.map(n=>`<div class=row><span class=k>${n}</span><span class=v>${say(w.members[n])}</span></div>`).join(\'\');return `<div style=\"margin-bottom:14px\"><div class=row style=\"border-bottom:1px solid var(--line)\"><span class=k>${w.sym} &middot; ${w.res}m</span><span class=\"v ${cl}\">${d}${w.dir?\' \'+w.votes+\'/6\':\'\'}</span></div>${rows}<div class=row><span class=k>entry</span><span class=v>${w.entry?(+w.entry).toPrecision(6):\'&mdash;\'}</span></div><div class=row><span class=k>price at it?</span><span class=v>${w.dir?(w.ready?\'<span class=up>YES</span>\':\'waiting\'):\'&mdash;\'}</span></div></div>`}).join(\'\')||\'<div class=note>no chart is publishing a council yet</div>\'); const cn=(s.council||[]).length||(s.coins||[]).length; if(draw.cn!==cn){draw.cn=cn;  document.getElementById(\'charts\').innerHTML=Array.from({length:cn},(_,i)=>   `<img id=shot${i} style=\"width:100%;border:1px solid var(--line);border-radius:6px;margin-bottom:10px;display:block\">`).join(\'\')   ||\'<div class=note>no chart window is open</div>\';} for(let i=0;i<cn;i++){const im=document.getElementById(\'shot\'+i);  if(im)im.src=\'/chart/\'+i+\'.jpg?t=\'+T+\'&r=\'+Date.now();} const rMode=(+s.tp_r>0||+s.r_adapt>0), atrMode2=(+s.sl_atr>0); if(rMode){  set(\'shapenote\',`The target is set by the coin, not here. The stop is `+   `${s.sl_atr} \\u00d7 that coin\\u2019s own 15m ATR and the target is `+   `${s.tp_r} of those stops, moved between ${s.r_min} and ${s.r_max} `+   `by how much of the coin\\u2019s movement is closed rather than merely `+   `reached. Margin follows: a stop costs ${s.risk_pct}% of the wallet `+   `whichever coin it is. The boxes below are the fallbacks used only when `+   `a coin has no ATR to read.`); } else { set(\'shapenote\',\'\'); } const pb=document.getElementById(\'pushbtn\'); if(pb&&!pb.onclick)pb.onclick=async e=>{e.preventDefault();await turnOnPush()}; if(pb){const on=(\'Notification\' in window)&&Notification.permission===\'granted\';  pb.textContent=on?\'on\':\'turn on\';} set(\'ml\',`<span class=up>+${(r.gross_win||0).toFixed(2)}</span> / <span class=dn>${(r.gross_loss||0).toFixed(2)}</span>`);\n set(\'bw\',`<span class=up>+${(r.best||0).toFixed(2)}</span> / <span class=dn>${(r.worst||0).toFixed(2)}</span>`);\n set(\'streak\',String(r.streak||0));\n set(\'lag\',`${r.entry_lag==null?\'&mdash;\':r.entry_lag.toFixed(1)+\'s\'} / ${r.entry_err==null?\'&mdash;\':r.entry_err.toFixed(2)+\'R\'}`);\n set(\'svc\',`${s.chrome?\'<span class=up>LIVE</span>\':\'<span class=dn>DOWN</span>\'} / ${s.guard?\'<span class=up>ON</span>\':\'<span class=dn>OFF</span>\'}`);\n set(\'bycoin\',(r.by||[]).map(c=>`<div class=row><span class=k>${c.sym}</span><span class=v>${c.w}/${c.n} &middot; <span class="${c.pnl>=0?\'up\':\'dn\'}">${money(c.pnl)}</span></span></div>`).join(\'\')||\'<div class=note>nothing closed yet</div>\');\n set(\'recent\',(r.recent||[]).map(t=>`<div class=row><span class=k>${t.sym} ${t.side===\'BUY\'?\'L\':\'S\'}${t.live?\' &middot; REAL\':\'\'}</span><span class=v>${t.reason} &middot; ${t.held}m &middot; <span class="${t.pnl>=0?\'up\':\'dn\'}">${money(t.pnl)}</span></span></div>`).join(\'\')||\'<div class=note>nothing closed yet</div>\');\n fillPickers(s);\n const sb=document.getElementById(\'scanbox\');\n if(sb){let t=(s.scan&&s.scan.text)||\'\';\n  // The server is on UTC. It sends the instant; the clock face is drawn here,\n  // so this line reads in the same timezone as everything else on the page.\n  if(t&&s.scan.at){\n   const d=new Date(s.scan.at*1000);\n   t=\'finished \'+d.toLocaleTimeString([], {hour:\'2-digit\',minute:\'2-digit\'})\n     +\'\\n\\n\'+t}\n  sb.style.display=t?\'block\':\'none\';\n  if(sb.textContent!==t)sb.textContent=t}\n // The target and stop are multiples of each coin\'s own ATR once those are\n // set, so the card has to say which units it is showing and write back to the\n // flag that is actually in force -- otherwise editing it silently changes a\n // number the book stopped reading.\n const atrMode=(+s.tp_atr>0||+s.sl_atr>0);\n set(\'tplab\',atrMode?\'target \\u00d7 ATR\':\'target %\');\n set(\'sllab\',atrMode?\'stop \\u00d7 ATR\':\'stop %\');\n val(\'tp\',atrMode?s.tp_atr:s.tp);val(\'sl\',atrMode?s.sl_atr:s.sl);\n val(\'lev\',s.lev);val(\'frac\',s.frac);val(\'be\',s.be);\n const apb=document.getElementById(\'applybtn\');\n if(apb)apb.onclick=()=>api(\'/api/set\',atrMode\n  ?{\'tp-atr\':tp.value,\'sl-atr\':sl.value,lev:lev.value,frac:frac.value,\n    \'break-even\':be.value}\n  :{tp:tp.value,sl:sl.value,lev:lev.value,frac:frac.value,\n    \'break-even\':be.value});\n if(up){const w=document.getElementById(\'wallet\');\n  w.classList.add(\'win-flash\');setTimeout(()=>w.classList.remove(\'win-flash\'),600)}\n}\nconst b64u=b=>btoa(String.fromCharCode(...new Uint8Array(b)))\n .replace(/\\+/g,\'-\').replace(/\\//g,\'_\').replace(/=+$/,\'\');\nconst unb=t=>Uint8Array.from(atob(t.replace(/-/g,\'+\').replace(/_/g,\'/\')),\n c=>c.charCodeAt(0));\nfunction fillPickers(s){\n const list=s.ranked||[];\n // The numbers belong in the signature, not just the names. A rescan usually\n // returns the same coins with fresh measurements, and keying on names alone\n // meant the figures on screen never changed again.\n const sig=JSON.stringify(list)+\'|\'+s.coins.join(\',\');\n if(fillPickers.sig===sig)return;fillPickers.sig=sig;\n [[\'c1\',0],[\'c2\',1]].forEach(([id,i])=>{\n  const el=document.getElementById(id);if(!el)return;\n  // Rebuilding the options under a choice you already made -- every fifteen\n  // seconds, on a timer -- is what made this feel like it was fighting back.\n  // Once you touch a picker it keeps what you chose until the switch lands.\n  if(!el.onchange)el.onchange=()=>{el.dataset.touched=\'1\'};\n  const want=(el.dataset.touched&&el.value)||s.coins[i]||\'\';\n  el.innerHTML=list.map(o=>`<option value="${o.sym}"${o.sym===want?\' selected\':\'\'}>`+\n   `${o.sym}${o.fast?`  ·  ${o.atr?o.atr+\'% atr  ·  \':\'\'}${o.fast}% fast  ·  ${o.lev}x`:\'\'}</option>`).join(\'\');\n  if(want&&!list.some(o=>o.sym===want))\n   el.insertAdjacentHTML(\'afterbegin\',`<option value="${want}" selected>${want}</option>`);\n });\n const n=document.getElementById(\'coinnote\');\n if(n)n.innerHTML=list.length>1\n  ?`${list.length} coins measured, best first. \\u201catr\\u201d is how far the coin moves in five minutes \\u2014 the target is sized to it, so a quiet coin is a small target, not a missed one. Switching checks the coin can carry the strategy before it touches the charts.`\n  :\'Run the coin scan below to fill this list with measured coins.\';\n}\nasync function turnOnPush(){\n if(!(\'serviceWorker\' in navigator)||!(\'PushManager\' in window)){msg(\'this browser cannot do notifications. On iPhone, add the app to your \'+\'home screen first \\u2014 Safari only allows them there.\');return}\n try{\n  const perm=await Notification.requestPermission();\n  if(perm!==\'granted\'){msg(\'notifications were not allowed\');return}\n  const reg=await navigator.serviceWorker.register(\'/sw.js\',{scope:\'/\'});\n  await navigator.serviceWorker.ready;\n  const k=await (await fetch(\'/api/push/key?t=\'+T)).json();\n  let sub=await reg.pushManager.getSubscription();\n  if(!sub)sub=await reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:unb(k.key)});\n  const r=await fetch(\'/api/push/subscribe?t=\'+T,{method:\'POST\',headers:{\'Content-Type\':\'application/json\'},body:JSON.stringify(sub)});\n  const j=await r.json();msg(j.msg||\'done\');\n }catch(e){msg(\'could not turn on notifications: \'+e)}}\nasync function addFace(){\n if(!window.PublicKeyCredential){msg(\'this browser cannot do Face ID\');return}\n try{\n  const o=await (await fetch(\'/webauthn/register-options?t=\'+T,{method:\'POST\'})).json();\n  if(o.ok===false){msg(o.msg);return}\n  const ch=o.challenge;\n  const req=Object.assign({},o,{challenge:unb(ch),\n   user:Object.assign({},o.user,{id:unb(o.user.id)}),\n   excludeCredentials:(o.excludeCredentials||[]).map(c=>Object.assign({},c,{id:unb(c.id)}))});\n  const c=await navigator.credentials.create({publicKey:req});\n  const r=await fetch(\'/webauthn/register-verify?t=\'+T,{method:\'POST\',\n   headers:{\'Content-Type\':\'application/json\'},\n   body:JSON.stringify({_challenge:ch,id:c.id,rawId:b64u(c.rawId),type:c.type,\n    response:{clientDataJSON:b64u(c.response.clientDataJSON),\n     attestationObject:b64u(c.response.attestationObject)}})});\n  const j=await r.json();msg(j.msg||\'done\');\n }catch(e){msg(\'face id setup cancelled or unsupported\')}}\napi(\'/api/state\',null,true);\nsetInterval(()=>{if(!busy)api(\'/api/state\',null,true)},15000);\n// iOS freezes a backgrounded app\'s timers, so reopening it showed whatever was\n// on screen when you last closed it -- a coin-finder clock stuck hours in the\n// past, for one. Refresh the moment the page is looked at again.\nconst wake=()=>{if(!busy)api(\'/api/state\',null,true)};\naddEventListener(\'pageshow\',wake);\naddEventListener(\'focus\',wake);\ndocument.addEventListener(\'visibilitychange\',()=>{if(!document.hidden)wake()});\n</script></body></html>'

def wa_register_options():
    from webauthn import generate_registration_options, options_to_json
    from webauthn.helpers.structs import (
        AuthenticatorAttachment, AuthenticatorSelectionCriteria,
        ResidentKeyRequirement, UserVerificationRequirement)
    opts = generate_registration_options(
        rp_id=RP_ID, rp_name="TBT",
        user_id=b"tbt-owner", user_name="owner", user_display_name="Owner",
        # No exclude list: re-adding a phone should just work rather than
        # failing with an opaque error because it is already on file.
        authenticator_selection=AuthenticatorSelectionCriteria(
            authenticator_attachment=AuthenticatorAttachment.PLATFORM,
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED),
    )
    _challenges[b64u(opts.challenge)] = time.time() + 300
    return json.loads(options_to_json(opts))


def wa_register_verify(body):
    from webauthn import verify_registration_response
    ch = body.pop("_challenge", "")
    if _challenges.pop(ch, 0) < time.time():
        return {"ok": False, "msg": "that registration attempt expired"}
    r = verify_registration_response(
        credential=body, expected_challenge=unb64u(ch),
        expected_origin=ORIGIN, expected_rp_id=RP_ID)
    c = creds()
    c.append({"id": b64u(r.credential_id),
              "pk": b64u(r.credential_public_key),
              "sign_count": r.sign_count,
              "added": int(time.time())})
    save_creds(c)
    return {"ok": True, "msg": f"this device can now sign in with Face ID "
                               f"({len(c)} registered)"}


def wa_auth_options():
    from webauthn import generate_authentication_options, options_to_json
    from webauthn.helpers.structs import (UserVerificationRequirement,
                                          PublicKeyCredentialDescriptor)
    opts = generate_authentication_options(
        rp_id=RP_ID,
        allow_credentials=[PublicKeyCredentialDescriptor(id=unb64u(c["id"]))
                           for c in creds()],
        user_verification=UserVerificationRequirement.REQUIRED)
    _challenges[b64u(opts.challenge)] = time.time() + 300
    return json.loads(options_to_json(opts))


def wa_auth_verify(body):
    from webauthn import verify_authentication_response
    ch = body.pop("_challenge", "")
    if ch not in _challenges:
        # Challenges live in memory, so a restart between asking and answering
        # loses them. Say that rather than implying the device is wrong.
        raise ValueError("that sign-in attempt expired -- press the button again")
    if _challenges.pop(ch, 0) < time.time():
        raise ValueError("that sign-in attempt timed out -- press the button again")
    want = body.get("id")
    rec = next((c for c in creds() if c["id"] == want), None)
    if not rec:
        raise ValueError(f"this device is not registered "
                         f"({len(creds())} on file). Sign in with the token "
                         f"and add it again.")
    r = verify_authentication_response(
        credential=body, expected_challenge=unb64u(ch),
        expected_origin=ORIGIN, expected_rp_id=RP_ID,
        credential_public_key=unb64u(rec["pk"]),
        credential_current_sign_count=rec.get("sign_count", 0))
    # A counter that goes backwards means the credential was cloned.
    rec["sign_count"] = r.new_sign_count
    save_creds(creds()[:-1] + [rec] if False else
               [rec if c["id"] == rec["id"] else c for c in creds()])
    return new_session()


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _ip(self):
        return self.client_address[0]

    def setup(self):
        # This runs on the connection's own thread, so a handshake that never
        # completes costs one thread for twenty seconds instead of the panel.
        self.request = SSL_CTX.wrap_socket(self.request, server_side=True)
        self.request.settimeout(20)
        super().setup()

    def _cookie(self):
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            k, _, v = part.strip().partition("=")
            if k == "tbt":
                return v
        return ""

    def _auth(self):
        """A session cookie, or the token as a query once so an old bookmark
        still works. The cookie keeps the secret out of the address bar,
        screenshots and browser history."""
        if session_ok(self._cookie()):
            return True
        q = parse_qs(urlparse(self.path).query)
        if secrets.compare_digest((q.get("t") or [""])[0], TOKEN):
            return True
        self._deny()
        return False

    def _deny(self, code=403):
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _sec(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")

    def _json(self, obj):
        d = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(d)))
        self.end_headers()
        self.wfile.write(d)

    def log_message(self, *a):
        pass

    def _send_html(self, body: str, cookie: str | None = None):
        d = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(d)))
        if cookie:
            self.send_header("Set-Cookie",
                             f"tbt={cookie}; HttpOnly; Secure; SameSite=Strict; Path=/")
        self._sec()
        self.end_headers()
        self.wfile.write(d)

    def do_POST_login(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode()
        got = parse_qs(raw).get("token", [""])[0]
        if locked(self._ip()):
            self._send_html(LOGIN.replace("__ERR__", "too many tries, wait 15 min"))
            return
        # Either the password or the token. Both are checked in constant time
        # so a wrong one cannot be told from a nearly-right one by timing.
        ok = check_password(got) if has_password() else False
        ok = secrets.compare_digest(got, TOKEN) or ok
        if ok:
            _tries.pop(self._ip(), None)
            self._send_html(PAGE, cookie=new_session())
        else:
            _tries[self._ip()].append(time.time())
            self._send_html(LOGIN.replace("__ERR__", "wrong password"))

    def _static(self, path, ctype):
        try:
            d = open(path, "rb").read()
        except OSError:
            self._deny(404); return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(d)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(d)

    def do_GET(self):
        p = urlparse(self.path).path
        if p.startswith("/icon-") and p.endswith(".png") and "/" not in p[1:]:
            self._static(f"{BOT}{p}", "image/png"); return
        # The indicator source, as plain text. Copying it between a Mac and a
        # VNC session never worked -- two clipboards with a wire between them
        # -- so it is served here instead. Opened in the same browser that
        # has TradingView, select-all and copy stay on one side of that wire
        # and simply work.
        if p == "/pine":
            if not self._auth():
                return
            try:
                body = open(f"{_BOT}/TBT_Sniper.pine", "rb").read()
            except Exception:
                self._deny(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._sec()
            self.end_headers()
            self.wfile.write(body)
            return
        # The real chart window, as an image. Refreshed by the page rather
        # than streamed: a JPEG every few seconds is a few KB and survives a
        # phone locking itself, where a socket does not.
        if p.startswith("/chart/") and p.endswith(".jpg"):
            if not self._auth():
                return
            try:
                idx = int(p[len("/chart/"):-len(".jpg")])
                if idx not in (0, 1):
                    raise ValueError(idx)
                import sys as _sys
                _sys.path.insert(0, BOT)
                from signals.tv_cdp import TradingViewCDP
                c = TradingViewCDP(target_index=idx)
                body = c.screenshot()
                c.close()
            except Exception:
                self._deny(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self._sec()
            self.end_headers()
            self.wfile.write(body)
            return
        # The service worker. Served from the root so its scope covers the
        # whole app -- a worker under a subdirectory can only wake for pages
        # beneath it, which would silently mean no notifications at all.
        if p == "/sw.js":
            body = (
                "self.addEventListener('push', function(e){\n"
                "  var d = {};\n"
                "  try { d = e.data ? e.data.json() : {}; } catch (err) {}\n"
                "  e.waitUntil(self.registration.showNotification(\n"
                "    d.title || 'Stratton Oakmont',\n"
                "    {body: d.body || '', tag: d.tag || 'tbt',\n"
                "     icon: '/icon-192.png', badge: '/icon-192.png',\n"
                "     renotify: true}));\n"
                "});\n"
                "self.addEventListener('notificationclick', function(e){\n"
                "  e.notification.close();\n"
                "  e.waitUntil(clients.matchAll({type:'window',\n"
                "    includeUncontrolled:true}).then(function(ws){\n"
                "      for (var i=0;i<ws.length;i++)\n"
                "        if ('focus' in ws[i]) return ws[i].focus();\n"
                "      if (clients.openWindow) return clients.openWindow('/');\n"
                "    }));\n"
                "});\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Service-Worker-Allowed", "/")
            self.end_headers()
            self.wfile.write(body)
            return
        if p == "/api/push/key":
            if not self._auth():
                return
            self._json({"key": vapid()["public"]})
            return
        # The live desktop. x11vnc listens on loopback only, websockify
        # bridges it to a WebSocket, and noVNC's own page is served from here
        # so the whole thing sits behind this app's login rather than being
        # exposed on a port of its own.
        # The WebSocket the noVNC page opens. The panel is a plain HTTP
        # server and cannot speak WebSocket, but it does not need to: once the
        # request is handed to websockify on loopback, everything after that is
        # opaque bytes in both directions. So the request is replayed verbatim
        # and the two sockets are pumped against each other.
        #
        # Doing it this way keeps the desktop behind this app's TLS and its
        # login. websockify itself listens on loopback only and is not
        # reachable from outside.
        if p == "/vncws":
            if not self._auth():
                return
            up = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                up.settimeout(10)
                up.connect(("127.0.0.1", 6080))
                head = [f"GET /?{urlparse(self.path).query} HTTP/1.1"]
                for k, v in self.headers.items():
                    if k.lower() in ("host",):
                        continue
                    head.append(f"{k}: {v}")
                head.append("Host: 127.0.0.1:6080")
                up.sendall(("\r\n".join(head) + "\r\n\r\n").encode())
                down = self.connection
                up.settimeout(None)
                down.settimeout(None)
                import select
                socks = [up, down]
                while True:
                    r, _w, x = select.select(socks, [], socks, 300)
                    if x or not r:
                        break
                    for src in r:
                        dst = down if src is up else up
                        try:
                            buf = src.recv(65536)
                        except OSError:
                            buf = b""
                        if not buf:
                            raise ConnectionError("closed")
                        dst.sendall(buf)
            except Exception:
                pass
            finally:
                try:
                    up.close()
                except Exception:
                    pass
                self.close_connection = True
            return
        if p == "/vnc" or p.startswith("/novnc/"):
            if not self._auth():
                return
            if p == "/vnc":
                body = (b"<!doctype html><meta name=viewport "
                        b"content='width=device-width,initial-scale=1'>"
                        b"<style>html,body{margin:0;height:100%;"
                        b"background:#000}iframe{border:0;width:100%;"
                        b"height:100%}</style>"
                        b"<iframe src='/novnc/vnc_lite.html"
                        b"?path=vncws&resize=scale&reconnect=1'></iframe>")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self._sec()
                self.end_headers()
                self.wfile.write(body)
                return
            rel = p[len("/novnc/"):]
            # Everything under /usr/share/novnc and nothing above it. Without
            # this a crafted path would read any file the service can open.
            root = os.path.realpath("/usr/share/novnc")
            full = os.path.realpath(os.path.join(root, rel))
            if not full.startswith(root + os.sep) or not os.path.isfile(full):
                self._deny(404)
                return
            kind = ("application/javascript" if full.endswith(".js")
                    else "text/css" if full.endswith(".css")
                    else "text/html; charset=utf-8" if full.endswith(".html")
                    else "image/png" if full.endswith(".png")
                    else "application/octet-stream")
            self._static(full, kind)
            return
        if p == "/manifest.json":
            d = json.dumps({
                "name": "Stratton Oakmont", "short_name": "Stratton",
                "start_url": "https://62.60.198.135.nip.io/",
                "scope": "https://62.60.198.135.nip.io/",
                "id": "https://62.60.198.135.nip.io/",
                "display": "standalone",
                "background_color": "#0A0A0A", "theme_color": "#0A0A0A",
                "icons": [
                    {"src": "https://62.60.198.135.nip.io/icon-192.png", "sizes": "192x192",
                     "type": "image/png"},
                    {"src": "https://62.60.198.135.nip.io/icon-512.png", "sizes": "512x512",
                     "type": "image/png"},
                    {"src": "https://62.60.198.135.nip.io/icon-512-maskable.png", "sizes": "512x512",
                     "type": "image/png", "purpose": "maskable"},
                ]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/manifest+json")
            self.send_header("Content-Length", str(len(d)))
            self.end_headers(); self.wfile.write(d); return
        if p == "/webauthn/auth-options":
            # Open by design: it hands out a challenge, and only a device
            # holding a registered private key can answer one.
            if not creds():
                self._json({"none": True}); return
            self._json(wa_auth_options()); return
        if p == "/" or p == "/index.html":
            if session_ok(self._cookie()):
                self._send_html(PAGE); return
            q = parse_qs(urlparse(self.path).query)
            if secrets.compare_digest((q.get("t") or [""])[0], TOKEN):
                # Arrived with the token in the link: hand out a cookie and
                # bounce to a clean URL so the secret stops travelling.
                self.send_response(303)
                self.send_header("Location", "/")
                self.send_header("Set-Cookie",
                                 f"tbt={new_session()}; HttpOnly; Secure; "
                                 f"SameSite=Strict; Path=/")
                self.send_header("Content-Length", "0")
                self._sec()
                self.end_headers()
                return
            if locked(self._ip()):
                self._deny(429); return
            self._send_html(LOGIN.replace("__ERR__", "")); return
        if False:
            pass
            d = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(d)))
            self.end_headers()
            self.wfile.write(d)
            return
        if not p.startswith("/api/"):
            self.send_response(404); self.end_headers(); return
        if not self._auth():
            return
        cmd = p[5:]
        if cmd == "state":
            self._json({"state": state()})
        elif cmd == "on":
            ctl("on"); _cache["t"] = 0
            self._json({"msg": "started", "state": state()})
        elif cmd == "off":
            ctl("off")
            self._json({"msg": "stopped. nothing will be traded.",
                        "state": state()})
        elif cmd == "autotoggle":
            st = auto_state()
            st["on"] = not st.get("on", False)
            auto_save(st)
            self._json({"msg": "auto switching "
                               + ("on" if st["on"] else "off"),
                        "state": state()})
        elif cmd == "lock":
            sid = self._cookie()
            _sessions.pop(sid, None)
            d = json.dumps({"msg": "locked"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(d)))
            self.send_header("Set-Cookie",
                             "tbt=; HttpOnly; Secure; SameSite=Strict; Path=/; "
                             "Max-Age=0")
            self._sec(); self.end_headers(); self.wfile.write(d); return
        elif cmd == "find":
            if _scan["running"]:
                self._json({"msg": "a scan is already running",
                            "state": state()})
            else:
                threading.Thread(target=scan_worker, daemon=True).start()
                time.sleep(0.4)
                self._json({"msg": "scan started -- it walks every liquid coin "
                                   "and takes a few minutes. The result appears "
                                   "here on its own.",
                            "state": state()})
        else:
            self._json({"msg": f"unknown: {cmd}"})

    def _push_routes(self, p) -> bool:
        """The two POSTs push needs. True when one of them handled the request."""
        if p == "/api/push/subscribe":
            if not self._auth():
                return True
            try:
                n = int(self.headers.get("Content-Length") or 0)
                sub = json.loads(self.rfile.read(n) or b"{}")
                if not sub.get("endpoint"):
                    raise ValueError("no endpoint")
            except Exception as e:
                self._json({"ok": False, "msg": f"bad subscription: {e}"})
                return True
            subs = [x for x in push_subs()
                    if x.get("endpoint") != sub["endpoint"]]
            subs.append(sub)
            save_subs(subs)
            log.info("push subscription stored (%d device(s))", len(subs))
            self._json({"ok": True, "msg": f"notifications on "
                                           f"({len(subs)} device"
                                           f"{'s' if len(subs) > 1 else ''})"})
            return True
        # The book calls this when a position opens or closes. It never comes
        # from a browser, so it is authenticated by a secret rather than by a
        # session, and it is refused from anywhere but this machine.
        if p == "/api/notify":
            peer = self.client_address[0] if self.client_address else ""
            secret = os.getenv("PANEL_NOTIFY_SECRET", "")
            given = self.headers.get("X-TBT-Secret", "")
            if peer not in ("127.0.0.1", "::1") or not secret or \
                    not secrets.compare_digest(given, secret):
                self._deny(403)
                return True
            try:
                n = int(self.headers.get("Content-Length") or 0)
                d = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                self._json({"ok": False})
                return True
            sent = push_all(str(d.get("title", ""))[:120],
                            str(d.get("body", ""))[:400],
                            str(d.get("tag", "tbt"))[:40])
            # A notification that reached nobody and one that reached the phone
            # used to look identical from both ends. Saying which is the whole
            # difference between a working channel and a silent one.
            n = len(push_subs())
            if sent:
                log.info("notified %d device(s): %s", sent,
                         str(d.get("title", ""))[:60])
            elif n == 0:
                log.warning("nothing sent -- no phone has subscribed yet: %s",
                            str(d.get("title", ""))[:60])
            else:
                log.warning("nothing sent -- all %d subscription(s) refused it",
                            n)
            self._json({"ok": True, "sent": sent, "devices": n})
            return True
        return False

    def do_POST(self):
        if self._push_routes(urlparse(self.path).path):
            return
        p = urlparse(self.path).path
        if p == "/login":
            self.do_POST_login(); return
        if p == "/webauthn/auth-verify":
            n = int(self.headers.get("Content-Length") or 0)
            try:
                sid = wa_auth_verify(json.loads(self.rfile.read(n) or b"{}"))
            except Exception as e:
                log_fail(self._ip())
                print(f"face id failed: {type(e).__name__}: {e}", flush=True)
                self._json({"ok": False, "msg": str(e)[:180]}); return
            if not sid:
                log_fail(self._ip())
                self._json({"ok": False, "msg": "not recognised"}); return
            d = json.dumps({"ok": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(d)))
            self.send_header("Set-Cookie",
                             f"tbt={sid}; HttpOnly; Secure; SameSite=Strict; Path=/")
            self._sec(); self.end_headers(); self.wfile.write(d); return
        if p in ("/webauthn/register-options", "/webauthn/register-verify"):
            # Registering a new device needs an existing session: Face ID is
            # added from inside, never as a way in from outside.
            if not self._auth():
                return
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            try:
                out = (wa_register_options() if p.endswith("options")
                       else wa_register_verify(body))
            except Exception as e:
                print(f"face id setup failed: {type(e).__name__}: {e}", flush=True)
                out = {"ok": False, "msg": str(e)[:180]}
            self._json(out); return
        if not self._auth():
            return
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        cmd = p[5:]
        if cmd == "coins":
            syms = [s.upper() for s in str(body.get("v", "")).split() if s.strip()]
            if not 1 <= len(syms) <= 2:
                self._json({"msg": "give one or two symbols"}); return
            # Recorded before the switch is attempted, so the guard stops
            # defending the old pair even if the switch takes a while or has
            # to be retried.
            set_wanted(syms)
            out = ctl("coins", *syms, timeout=600)
            _cache["t"] = 0
            self._json({"msg": out[-1400:], "state": state()})
        elif cmd == "set":
            args = [f"{k}={v}" for k, v in body.items() if str(v).strip()]
            out = ctl("set", *args, timeout=300)
            self._json({"msg": out[-1000:], "state": state()})
        else:
            self._json({"msg": f"unknown: {cmd}"})


SSL_CTX: ssl.SSLContext | None = None


class TLSServer(ThreadingHTTPServer):
    """Do the TLS handshake in the worker thread, never in the accept loop.

    Wrapping the *listening* socket means accept() itself performs the
    handshake, so a single client that opens a connection and then says
    nothing holds the accept loop forever and the whole panel goes dark.
    Port 443 faces the internet and scanners do exactly that -- it is how the
    app went down. Accepting plain and wrapping per connection keeps one rude
    client to itself, and the timeouts stop it lingering even there.
    """

    daemon_threads = True
    request_queue_size = 64

    def get_request(self):
        sock, addr = self.socket.accept()
        sock.settimeout(20)
        return sock, addr

    def handle_error(self, request, client_address):
        # A failed handshake is not an error worth a traceback in the log --
        # it is the background noise of having a port open.
        exc = sys.exc_info()[1]
        if isinstance(exc, (ssl.SSLError, socket.timeout, ConnectionError,
                            OSError)):
            return
        super().handle_error(request, client_address)



# Started here rather than beside autopilot's own definition: the first thing
# it does is call coins(), and that is defined further down this file. A thread
# launched mid-module can outrun the rest of the import.
threading.Thread(target=autopilot, daemon=True).start()

if __name__ == "__main__":
    SSL_CTX = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    SSL_CTX.load_cert_chain(CERT, KEY)
    SSL_CTX.minimum_version = ssl.TLSVersion.TLSv1_2
    srv = TLSServer(("0.0.0.0", PORT), H)
    print(f"panel on https://:{PORT}  token {TOKEN}", flush=True)
    srv.serve_forever()
