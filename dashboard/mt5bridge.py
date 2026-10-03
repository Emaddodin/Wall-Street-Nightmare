"""Your MT5 app as Gold Desk's price and order source on a Mac (or anywhere MT5 runs), through the
GoldDeskBridge EA (mt5/GoldDeskBridge.mq5) on one of its charts.

The EA and Gold Desk talk through files in MT5's shared "Common\\Files\\GoldDesk" folder on this computer:

- state.json   written by the EA whenever gold's price moves (and once a second): price, the last three
               candles of each timeframe, account, open gold trades, contract details;
- in/cmd-*.txt one request from Gold Desk (an order, a close, a stop move, or candle history of gold or,
               for the SMT reading, another symbol such as silver), which the EA
               claims by renaming it, so a request runs once or not at all;
- out/res-*.txt the EA's answer.

Orders are written only when you click on the dashboard. If MT5 doesn't pick a request up within a few
seconds, Gold Desk takes it back, so it can never run late.

    python3 mt5bridge.py --install   # put the EA into every MT5 found on this computer (and try to compile it)
    python3 mt5bridge.py --check     # exit 0: the EA is writing prices now; 1: it ran before; 2: never
"""
from __future__ import annotations

import glob
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from engine import Bars, Spec

HERE = Path(__file__).resolve().parent
EA = HERE / "mt5" / "GoldDeskBridge.mq5"
TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400}
FRESH = 5.0          # the EA writes at least once a second; older than this means MT5 or the EA stopped
TAKE = 3.0           # seconds MT5 has to pick an order up before Gold Desk takes it back
RUN = 30.0           # once MT5 has it: how long the broker may take to answer
HOME = Path.home()
SYMBOL_OK = re.compile(r"^[A-Za-z0-9._#+!$&@-]{1,40}$")    # broker symbol names; never a line break into a request
REUSE = 2.0          # other symbols' candles (silver for the SMT): ask MT5 again at most this often per timeframe
OLD_EA = ("The GoldDeskBridge EA running in MT5 is an older version that only sends gold candles. Run "
          "python3 mt5bridge.py --install (or copy mt5/GoldDeskBridge.mq5 into MT5), compile it and put it back "
          "on the chart to get silver.")

# Wine prefixes MT5 runs in: MetaQuotes' Mac app, CrossOver, PlayOnMac, plain Wine; and Windows itself.
PREFIXES = [str(HOME / "Library/Application Support/*"), str(HOME / "Library/Application Support/CrossOver/Bottles/*"),
            str(HOME / "Library/PlayOnMac/wineprefix/*"), str(HOME / ".wine*"), str(HOME / ".mt5*")]
ROAMING = ["drive_c/users/*/AppData/Roaming", "drive_c/users/*/Application Data"]


def common_dirs() -> list:
    """Every MetaQuotes\\Terminal\\Common\\Files folder on this computer."""
    pats = [os.path.join(p, r, "MetaQuotes/Terminal/Common/Files") for p in PREFIXES for r in ROAMING]
    if os.environ.get("APPDATA"):
        pats.append(os.path.join(os.environ["APPDATA"], "MetaQuotes", "Terminal", "Common", "Files"))
    out = []
    for p in pats:
        out += [x for x in glob.glob(p) if os.path.isdir(x) and x not in out]
    return out


def find_folder() -> Path | None:
    """The GoldDesk folder whose state.json was written last (the EA that is running), if any."""
    env = os.environ.get("GOLDDESK_MT5_FILES")
    cands = [Path(env)] if env else [Path(c) / "GoldDesk" for c in common_dirs()]
    have = [c for c in cands if (c / "state.json").is_file()]
    return max(have, key=lambda c: (c / "state.json").stat().st_mtime) if have else None


def experts_dirs() -> list:
    """MQL5/Experts folders of every MT5 data folder on this computer."""
    pats = []
    for p in PREFIXES:
        pats += [os.path.join(p, "drive_c/Program Files*/*/MQL5/Experts")]
        pats += [os.path.join(p, r, "MetaQuotes/Terminal/*/MQL5/Experts") for r in ROAMING]
    if os.environ.get("APPDATA"):
        pats.append(os.path.join(os.environ["APPDATA"], "MetaQuotes", "Terminal", "*", "MQL5", "Experts"))
    out = []
    for p in pats:
        out += [x for x in glob.glob(p) if os.path.isdir(x) and x not in out]
    return out


def _wine_for(prefix: str) -> str | None:
    """The Wine that comes with the MT5 app using this prefix (Mac), else one on the PATH."""
    for app in glob.glob("/Applications/*.app") + glob.glob(str(HOME / "Applications/*.app")):
        if "metatrader" not in app.lower() and "mt5" not in app.lower() and "litefinance" not in app.lower():
            continue
        for name in ("wine64", "wine", "wineloader"):
            hits = glob.glob(os.path.join(app, "Contents/**/bin", name), recursive=True)
            if hits:
                return hits[0]
    return shutil.which("wine64") or shutil.which("wine")


def compile_ea(experts: str) -> str:
    """Best effort: compile GoldDeskBridge.mq5 with that terminal's own MetaEditor. Returns what happened."""
    mq5, ex5 = os.path.join(experts, EA.name), os.path.join(experts, EA.stem + ".ex5")
    if os.path.isfile(ex5) and os.path.getmtime(ex5) >= os.path.getmtime(mq5):
        return "already compiled"
    data = os.path.dirname(os.path.dirname(experts))                       # .../MQL5/Experts -> the data folder
    prefix = data.split("/drive_c/")[0] if "/drive_c/" in data else None
    editors = glob.glob(os.path.join(data, "metaeditor64.exe")) or (
        glob.glob(os.path.join(prefix, "drive_c/Program Files*/*/metaeditor64.exe")) if prefix else [])
    if not editors:
        return "MetaEditor not found"
    win = lambda p: "C:\\" + p.split("/drive_c/", 1)[1].replace("/", "\\") if prefix else p
    cmd = [editors[0], f"/compile:{win(mq5)}", f"/log:{win(mq5[:-4] + '.log')}"]
    env = dict(os.environ)
    if prefix:
        wine = _wine_for(prefix)
        if not wine:
            return "Wine not found"
        cmd, env["WINEPREFIX"], env["WINEDEBUG"] = [wine] + cmd, prefix, "-all"
    try:
        subprocess.run(cmd, env=env, timeout=60, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as e:
        return f"MetaEditor didn't run ({e})"
    if os.path.isfile(ex5) and os.path.getmtime(ex5) >= os.path.getmtime(mq5):
        return "compiled"
    log = mq5[:-4] + ".log"
    try:
        raw = open(log, "rb").read()
        text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8", "replace")
        tail = [l.strip() for l in text.splitlines() if "error" in l.lower()][-3:]
    except OSError:
        tail = []
    return "not compiled" + (": " + " | ".join(tail) if tail else "")


def install() -> list:
    """Copy the EA into every MT5 found (when it differs) and try to compile it. [(folder, what happened)]"""
    out = []
    src = EA.read_bytes()
    for d in experts_dirs():
        dst = os.path.join(d, EA.name)
        try:
            if not os.path.isfile(dst) or open(dst, "rb").read() != src:
                with open(dst, "wb") as f:
                    f.write(src)
            out.append((d, compile_ea(d)))
        except OSError as e:
            out.append((d, f"couldn't copy ({e})"))
    return out


# ====================================================================== the data source
class MT5BridgeSource:
    """Gold Desk's MT5 source through the GoldDeskBridge EA: the same answers as the Windows MetaTrader5
    package (prices and candles in the broker's server time), so the dashboard treats both alike."""
    kind = "mt5"

    def __init__(self, folder: str | Path | None = None, wait: float = 20.0):
        d = Path(folder) if folder and folder != "auto" else find_folder()
        if d is None:
            raise RuntimeError("No MT5 with the Gold Desk bridge found on this computer. In MT5, put GoldDeskBridge "
                               "on a gold chart and turn on Algo Trading.")
        self.dir = d
        (d / "in").mkdir(parents=True, exist_ok=True)
        (d / "out").mkdir(parents=True, exist_ok=True)
        self.st: dict = {}
        self._mtime = 0.0
        self._sig = None
        self.changed = threading.Condition()          # notified on every new state (live prices wait on it)
        self.cache: dict = {tf: [] for tf in TF_SECONDS}
        self._short: dict = {}                       # timeframe -> (bars MT5 had, when): don't ask again at once
        self.locks = {tf: threading.Lock() for tf in TF_SECONDS}
        self._of: dict = {}                          # (symbol, tf) -> (when, rows, point): other symbols, apart
        self._of_lock = threading.Lock()             # from gold's cache and locks
        self._read()
        threading.Thread(target=self._watch, daemon=True).start()
        end = time.time() + wait
        while not self._fresh() and time.time() < end:
            time.sleep(0.1)
        if not self._fresh() or "spec" not in self.st:
            raise RuntimeError(f"The Gold Desk bridge in MT5 isn't running (no fresh prices in {d}). Open MT5 and "
                               "check GoldDeskBridge is on a chart with Algo Trading on.")
        self.symbol = self.st.get("sym", "XAUUSD")

    # ------------------------------------------------------------ state from the EA
    def _read(self) -> bool:
        f = self.dir / "state.json"
        try:
            s = f.stat()
            sig = (s.st_mtime_ns, s.st_ino, s.st_size)
            if sig == self._sig:
                return False
            d = json.loads(f.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            return False                               # mid-swap or not there yet: next look
        self._sig, self._mtime, self.st = sig, s.st_mtime, d
        return True

    def _watch(self) -> None:
        while True:
            if self._read():
                with self.changed:
                    self.changed.notify_all()
            time.sleep(0.003)

    def _fresh(self) -> bool:
        return bool(self.st) and time.time() - self._mtime < FRESH

    def server_offset(self) -> int | None:
        """Broker server time minus UTC, in seconds, as MT5 itself reports it."""
        off = self.st.get("srv_off")
        return None if off is None else int(round(off / 1800.0) * 1800)

    # ------------------------------------------------------------ requests to the EA
    def _ask(self, fields: dict, take: float = TAKE, run: float = RUN) -> str:
        """Hand MT5 one request; its answer file's text. Raises RuntimeError("...nothing was sent") if MT5 never
        took it."""
        rid = secrets.token_hex(6)
        fields = {"id": rid, **fields}
        if fields.get("op") != "bars":
            fields["expires"] = int(time.time()) + 5          # MT5 skips it after this, should Gold Desk be gone
        cmd, res = self.dir / "in" / f"cmd-{rid}.txt", self.dir / "out" / f"res-{rid}.txt"
        tmp = cmd.with_suffix(".tmp")
        tmp.write_text("".join(f"{k}={v}\n" for k, v in fields.items()), encoding="ascii")
        os.replace(tmp, cmd)
        end, taken = time.time() + take, False
        while True:
            if res.exists():
                try:
                    text = res.read_text(encoding="utf-8", errors="replace")
                    res.unlink()
                    return text
                except OSError:
                    pass                                   # still being swapped in: look again
            if time.time() > end:
                if taken:
                    raise RuntimeError("MT5 took the request but hasn't answered; check MT5 before trying again")
                try:
                    cmd.unlink()                           # never picked up: take it back, so it can't run late
                except FileNotFoundError:
                    taken, end = True, time.time() + run   # MT5 has it: wait for the broker's answer
                    continue
                raise RuntimeError("MT5 didn't pick the request up (is MT5 open, with GoldDeskBridge on a chart "
                                   "and Algo Trading on?); nothing was sent")
            time.sleep(0.001)

    def _trade(self, fields: dict) -> dict:
        if not self._fresh():
            return {"ok": False, "message": self.broker()["message"], "ms": 0}
        t0 = time.perf_counter()
        try:
            r = json.loads(self._ask(fields))
        except RuntimeError as e:
            return {"ok": False, "message": str(e), "ms": round((time.perf_counter() - t0) * 1000, 1)}
        except ValueError:
            return {"ok": False, "message": "MT5's answer couldn't be read; check MT5 before trying again", "ms": 0}
        r["message"] = HINTS.get(r.get("retcode"), "") + (r.get("message") or "")
        r["ms"] = round((time.perf_counter() - t0) * 1000, 1)       # click to answer, as seen from Gold Desk
        r["status"] = ("opened" if fields["op"] == "market" else "done") if r.get("ok") else "refused"
        self._read()
        return r

    # ------------------------------------------------------------ Gold Desk source interface
    @property
    def caps(self) -> dict:
        return {"positions": True, "trading": self.broker()["connected"] and self.account()["trade_allowed"]}

    def broker(self) -> dict:
        st = self.st
        if not self._fresh():
            return {"connected": False, "message": "MT5 isn't sending prices: open MT5 and keep GoldDeskBridge on a "
                                                   "chart (the bridge stops when MT5 closes)"}
        if not st.get("connected"):
            return {"connected": False, "message": "MT5 is not connected to your broker's server"}
        if not st.get("algo"):
            return {"connected": True, "message": "Algo Trading is off in MT5: turn it on (toolbar) to trade from Gold Desk"}
        if not st.get("ea_trade"):
            return {"connected": True, "message": "GoldDeskBridge may not trade: tick 'Allow Algo Trading' in its settings"}
        return {"connected": True, "message": None}

    def tick(self) -> dict | None:
        t = self.st.get("tick")
        return {"bid": t["bid"], "ask": t["ask"], "time": int(t["time"]), "msc": t.get("msc")} if t else None

    def spec(self) -> Spec:
        s = self.st["spec"]
        tv = s.get("tick_value_loss") or s.get("tick_value")
        vpu = tv / s["tick_size"] if tv and s.get("tick_size") else (s.get("contract") or 100.0)
        return Spec(point=s["point"], digits=int(s["digits"]), vpu=vpu, min_lot=s.get("vmin") or 0.01,
                    lot_step=s.get("vstep") or 0.01, max_lot=s.get("vmax") or 100.0)

    def account(self) -> dict:
        a, st = self.st.get("acct") or {}, self.st
        if not a:
            return {"balance": 0.0, "equity": 0.0, "currency": "USD", "server": "not logged in", "mode": "unknown",
                    "trade_allowed": False}
        return {"balance": a.get("balance"), "equity": a.get("equity"), "currency": a.get("currency", "USD"),
                "server": a.get("server"), "company": a.get("company"),
                "mode": {0: "demo", 1: "contest", 2: "real"}.get(a.get("mode"), "unknown"), "login": a.get("login"),
                "margin": a.get("margin"), "free_margin": a.get("free"),
                "trade_allowed": bool(a.get("trade_allowed") and a.get("trade_expert") and st.get("algo")
                                      and st.get("ea_trade") and self._fresh()),
                "ping_ms": round(st["ping_us"] / 1000, 1) if st.get("ping_us") else None,
                "bridge": "MT5 (GoldDeskBridge)"}

    def positions(self) -> list:
        return [{"ticket": p["ticket"], "side": "BUY" if p["type"] == 0 else "SELL", "volume": p["volume"],
                 "open": p["open"], "sl": p["sl"], "tp": p["tp"], "price": p["price"], "profit": p["profit"],
                 "time": p["time"], "magic": p["magic"], "comment": p.get("comment", "")}
                for p in self.st.get("pos") or []]

    def rates(self, tf: str, count: int) -> Bars:
        sec, count = TF_SECONDS[tf], int(count)
        with self.locks[tf]:
            rows = self.cache[tf]
            tail = [list(r) for r in (self.st.get("bars") or {}).get(tf) or []]
            short = self._short.get(tf)
            enough = len(rows) >= count or (short and short[0] <= len(rows) and time.time() - short[1] < 60)
            joined = rows and tail and rows[-1][0] >= tail[0][0]          # the live tail continues the cache
            if not rows or not enough or (tail and not joined):
                rows = self._load(tf, max(count, len(rows), 300))
            if tail:
                rows = [r for r in rows if r[0] < tail[0][0]] + tail
            self.cache[tf] = rows[-max(count, 6000):]
            rows = self.cache[tf]
        pt = self.st["spec"]["point"]
        b = Bars(sec)
        for r in rows[-count:]:
            b.append(int(r[0]), r[1], r[2], r[3], r[4], float(r[5]), float(r[6]) * pt)
        return b

    def _load(self, tf: str, count: int) -> list:
        text = self._ask({"op": "bars", "tf": tf, "count": count}, take=TAKE + 2, run=20.0)
        head, _, body = text.partition("\n")
        info = json.loads(head)
        if not info.get("ok"):
            raise RuntimeError(info.get("message") or f"MT5 gave no {tf} candles")
        rows = []
        for line in body.splitlines():
            p = line.split(",")
            if len(p) == 7:
                rows.append([int(p[0]), float(p[1]), float(p[2]), float(p[3]), float(p[4]), float(p[5]), float(p[6])])
        if len(rows) < count:
            self._short[tf] = (len(rows), time.time())                    # MT5 has no more than this for now
        return rows

    def rates_of(self, symbol: str, tf: str, count: int) -> Bars:
        """Candles of another symbol of this MT5 (silver for the SMT reading), oldest first, the last one
        forming, on the same server clock as gold's. Leaves gold's cache alone. Raises LookupError when MT5
        has no such symbol, NotImplementedError when the EA is too old to send other symbols, RuntimeError
        otherwise (history still loading, MT5 not answering)."""
        sec, count = TF_SECONDS[tf], int(count)
        if not SYMBOL_OK.match(symbol or ""):
            raise LookupError(f"not a symbol name: {symbol!r}")
        with self._of_lock:
            hit = self._of.get((symbol, tf))
            if hit and time.time() - hit[0] < REUSE and len(hit[1]) >= count:
                rows, pt = hit[1], hit[2]
            else:
                rows, pt = self._load_of(symbol, tf, count)
                self._of[(symbol, tf)] = (time.time(), rows, pt)
        b = Bars(sec)
        for r in rows[-count:]:
            b.append(int(r[0]), r[1], r[2], r[3], r[4], float(r[5]), float(r[6]) * pt)
        return b

    def _load_of(self, symbol: str, tf: str, count: int) -> tuple:
        if not self._fresh():
            raise RuntimeError(self.broker()["message"])
        text = self._ask({"op": "bars", "tf": tf, "count": count, "symbol": symbol}, take=TAKE + 2, run=20.0)
        head, _, body = text.partition("\n")
        try:
            info = json.loads(head)
        except ValueError:
            raise RuntimeError(f"MT5's {symbol} answer couldn't be read")
        if not info.get("ok"):
            if info.get("nosym"):
                raise LookupError(info.get("message") or f"MT5 has no {symbol}")
            raise RuntimeError(info.get("message") or f"MT5 gave no {symbol} {tf} candles")
        if info.get("sym") != symbol:                # an older EA ignored "symbol" and sent gold: never use it
            raise NotImplementedError(OLD_EA)
        rows = []
        for line in body.splitlines():
            p = line.split(",")
            if len(p) == 7:
                rows.append([int(p[0]), float(p[1]), float(p[2]), float(p[3]), float(p[4]), float(p[5]), float(p[6])])
        return rows, float(info.get("point") or 0.0)

    # ------------------------------------------------------------ manual trading (only on your clicks)
    def market(self, side: str, lots: float, sl: float, tp: float) -> dict:
        return self._trade({"op": "market", "side": side, "lots": f"{float(lots):.2f}",
                            "sl": float(sl or 0.0), "tp": float(tp or 0.0)})

    def close(self, ticket: int, volume: float | None = None) -> dict:
        return self._trade({"op": "close", "ticket": int(ticket), "volume": float(volume or 0.0)})

    def modify(self, ticket: int, sl: float, tp: float) -> dict:
        return self._trade({"op": "modify", "ticket": int(ticket), "sl": float(sl or 0.0), "tp": float(tp or 0.0)})


# MT5 trade return codes that need a plain word first
HINTS = {10027: "Algo Trading is off in MT5 (toolbar button). ", 10018: "Market closed. ",
         10019: "Not enough money in this MT5 account. ", 10017: "Trading is disabled for this account. ",
         10031: "MT5 has no connection to the broker. ", 10004: "Requote: the price moved. ",
         10006: "The broker rejected it. ", 10016: "Stop loss or take profit too close to the price. "}


def main() -> None:
    if "--install" in sys.argv:
        done = install()
        for d, what in done:
            print(f"Gold Desk bridge EA -> {d}: {what}")
        if not done:
            print("No MT5 found on this computer.")
        raise SystemExit(0 if done else 1)
    if "--check" in sys.argv:
        d = find_folder()
        fresh = d is not None and time.time() - (d / "state.json").stat().st_mtime < FRESH
        print(f"Bridge running: {d}" if fresh else (f"Bridge files at {d}, but MT5 isn't writing them now" if d
                                                     else "No Gold Desk bridge has run in MT5 yet"))
        raise SystemExit(0 if fresh else 1 if d else 2)
    print(__doc__)


if __name__ == "__main__":
    main()
