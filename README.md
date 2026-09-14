# TBT-Engine

**Start here: [HANDOFF.md](HANDOFF.md)** — what the system does, why every
number is the number it is, every bug and trap already paid for, and what is
still open. Read that first; this file is the inventory of the folder.

The whole trading stack, in one place. Gathered 2026-09-04 with every trading
service stopped.

This folder is a **byte-exact mirror** of `/home/tbt/bot` on the server: 122
files, every name matching, every key file verified identical. Edit here and
the server does not change; edit there and this copy goes stale.

---

## Layout

Everything now lives under one root, on both machines.

| | |
|---|---|
| `*.py` | the engine itself |
| `data/` | live state — watchlist, measurements, the paper book, archives |
| `signals/` | the Chrome DevTools layer that reads the chart |
| `exchange/` | the Bitunix side |
| `tools/` | 22 inspection scripts, moved in from `/home/tbt/tools` |
| `pine/` | the indicator — Mac and server copies verified identical |
| `services/` | reference copies of the live systemd units |
| `scratch/` | loose scripts that used to sit in `/home/tbt` |
| `old/` | stale unit copies, old logs, dead-code tarballs |
| `logs/` | runtime logs |

The root stayed named `bot` on the server on purpose: every systemd unit points
at `~/bot` and seven source files write that path literally. Renaming it would
mean editing all of them for nothing but cosmetics, so everything else moved in
instead. `/home/tbt/tools` is left behind as a symlink, so anything reaching
for the old path still lands correctly.

### Contains secrets

`.env` and `.env.bak.*` are here because you asked for everything. They hold
the ntfy topic and the exchange keys. I copied them without opening or printing
them. Your Desktop is local-only, not synced to iCloud, and readable only by
your account — but this folder is now sensitive: do not put it in a shared
drive, a repo, or a zip you send anywhere.

---

## The chain, in order

1. **`boom2.py`** — the scanner. Walks every listed symbol, measures how far
   each travels and how often it makes one of the two shapes, writes the ranked
   `data/watchlist.json` and `data/watch_measures.json`. Runs on
   `tbt-boom.timer`.
2. **`scout.py`** — walks the top 150 through chart window 1, reads the shape
   off the candles, writes `data/scout.json`.
3. **`papertrade.py`** — the book. Holds chart window 0 still, scores the
   scout's plans, and either trades one or writes down exactly why it refused.
4. **`guard.py`** — keeps the stack alive and the two windows assigned.
5. **`panel.py`** — the phone app, live P&L, VNC relay.

## The two shapes, in either direction

- a candle bursting out of a level that had been holding
- a trend remaking its own level several candles running

Nothing else is traded.

## Settings that matter

50x leverage. Target 10% of price — 500% of the margin committed. Half the
wallet per trade. Stop taken from the level itself, never a chosen percentage.
Nothing under 70% sure. Indicator higher timeframe 1h.

---

## The flow dataset

`data/dataset/` holds a machine-readable record of every decision this engine
can make — coin finding, the eagle's pick, entries, refusals, resting orders
and exits — produced by running the real book over the real recorded signals
and outcomes. Regenerate with `python3 dataset/make_dataset.py`; the branch
matrix lives in `dataset/scenarios.py`; `tests/test_dataset.py` proves every
declared branch is actually produced and that scores and PnL match the book's
own arithmetic. Details in HANDOFF.md, section 15.

The book also journals its own decisions live, one JSONL line per event, to
`data/book_events.jsonl` (`journal.py`) — the same events the funnel counts.
`tools/riskanalysis.py` turns the recorded trades into the arithmetic of one
wallet: equity path, drawdown, stop streaks, expectancy.

---

## State when this was taken

Paper book: **$100.00, zero trades.** Nothing has ever passed the 70 floor.

Stopped: `tbt-paper`, `tbt-scout`, `tbt-guard`, `tbt-recorder`,
`tbt-boom.timer`. Still up so the charts and app stay reachable:
`tbt-chrome`, `tbt-panel`, `tbt-vnc`, `tbt-vncws`.

Verified after the move: all 41 source files parse, all six engine modules
import, every systemd unit resolves to a file that exists, the tools run from
their new home, and `/home/tbt` is clear of loose Python.

### Open bug

**Chart window 0 — the book's window — is dead.** Its TradingView feed stopped
at 20:15 UTC on 2026-09-03 and never reconnected. The page still answered
JavaScript, but every candle it handed back was frozen at that moment; pointing
it at BTC returned the same stale candle, which proves the fault is the tab and
not the coin. It has since stopped answering CDP at all. Window 1 is healthy
and returns candles minutes old.

Because the book reads window 0, it spent roughly fourteen hours reading a
stopped chart. That is every `stale bar -- not traded` line in its log, and the
reason the book never produced a signal of its own — every plan it scored came
from the scout. A reload was started and did not finish; nothing has touched
the tab since.

### Fixed the same night, and live in this code

- **The two chart tabs swapped identity underneath the services.** Both carry
  the same URL and were told apart only by position in Chrome's target list —
  which Chrome orders by whichever tab was last brought forward. The screenshot
  path must call `Page.bringToFront`, so every picture reshuffled them: the
  book began reading the tab the scout was walking, and the scout drove the tab
  the book meant to hold still. Windows are now pinned to the target id, fixed
  for the life of a tab. (`signals/tv_cdp.py`)
- **The book died on the scout's forecast rows.** The scout writes four kinds
  of row; only `break` and `council` are orders. `ripe` and `coil` are
  intelligence, carrying no entry and no stop, and the book indexed a key they
  do not have — 69 crashed polls in forty minutes, each one before it reached
  the shapes further down the file. (`papertrade.py`, `scout_plans`)
- **The ranking rewarded shapes on coins that never travel.** The target is
  10%, but shape production was scored on its own, so symbols measured never to
  cover 10% ranked inside the walked list — tokenised equities making three or
  four shapes a day and reaching the target on none of them. Shape credit is
  now tied to measured reach. (`boom2.py`, `_rank`)

---

## Two landmines found while gathering this

**`old/tbt-paper.service.STALE-DO-NOT-INSTALL`** is an old copy that differs
from the live unit in exactly the two places that were bugs: it runs
`--min-confidence 0`, so the book would trade everything with no quality floor,
and `--interval 0.05`, which is the twenty-polls-a-second setting that had the
box at load 3.6. `old/tbt-scout.service.STALE-DO-NOT-INSTALL` still walks only
40 coins with the old dwell. Both were sitting loose in the home directory
where they could be installed by accident. The live units in
`/etc/systemd/system/` are the truth, and `services/` holds honest copies.

**`scratch/`** holds five scripts that were sitting directly in `/home/tbt`,
which is on the Python path for anything started from there. That position has
broken this system three times by shadowing a real module — `nt.py` took out
`pathlib`, and `inspect.py` and `platform.py` did the same before it. None of
these five collide with a standard module name, so nothing was broken, but they
are off the path now.

---

## Research data (6.2 GB, not in git)

The full research datasets (`quant/data/` ~5.2 GB, `data/` ~95 MB,
`scalper/data/` ~1 GB) exceed GitHub's limits, so they ship as a compressed
archive hosted on the project's own VPS:

```
scp tbt:/root/ict_sniper/data_archive/tbt_data.tar.zst .
shasum -a 256 -c tbt_data.sha256        # verify integrity
tar --use-compress-program=unzstd -xf tbt_data.tar.zst
```

- Archive format: single `tar.zst` (zstd level 3), one file, no split
- Checksum: `tbt_data.sha256` next to the archive
- Regenerate the archive on the box: `tar -cf - quant/data data scalper/data | zstd -3 -T0 -o tbt_data.tar.zst`
- Alternative for GitHub: attach the archive (split into <2 GB parts) to a
  Release of this repository -- ask for a fine-grained token if you want that.
