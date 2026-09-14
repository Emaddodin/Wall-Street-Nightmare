# Stratton Oakmont audit report

Scope: the full trading chain (atrscan → scout → perch → papertrade → guard/panel), the learned
filter path, the dataset package, the exchange/CDP layers, and the tools, as they run on the server
(services/ units included).

Method note: the working tree was being edited **while this audit ran** (papertrade.py changed
mid-read, 4868 → 4880 lines). Every line number and quote below was re-verified against a
read-only snapshot of the tree taken 2026-09-07 00:12 +0330, kept at `scratch/audit_snapshot/`.
Re-check any finding against the live file if edits have continued.

Verdict: real defects found (2 major, 2 conditional/ops major, 5 minor). No money-path sign error
was found in papertrade.py itself; the paper book's PnL/exit/liquidation/funding arithmetic was
checked line by line and is consistent.

---

## 1. [major] `dataset/entries.py:139` — the SELL edge-fill statistic reads the wrong array

```python
133:        shift = ((c - float(bar.get("l") or c)) / c if side == "BUY"
134:                 else (float(bar.get("h") or c) - c) / c) * 100
...
138:        for i in range(min(8, len(adv))):
139:            back = adv[i] if side == "BUY" else fav[i]
```

Proof: fav/adv are side-relative (settled convention, and the code itself documents it in
`recorder.py:194-195`). For a SELL, `shift` is the UP distance from the close to the signal-bar
high `(h-c)/c`; price "returns to the candle extreme" only on an UP move — which is the SELL's
**adverse** (`adv`) array. Line 139 reads `fav` (the DOWN move) for SELL, so every counted "fill"
is a move *away* from the resting level. The printed headline this computes
("price returns to the candle extreme within 8 bars", lines 149-152) is garbage for all SELL rows,
and the paper unit runs `--entry edge`, so this statistic is the very one used to judge that
entry's miss rate.

Fix: `back = adv[i]` unconditionally (for BUY the low is also reached on an adverse/down move).

## 2. [major] `panel.py:186,1287` vs `perch.py:166` — two autonomous writers of `chart_coins.json`; the phone's manual "switch coins" never sticks

```python
panel.py:456: def set_wanted(syms) -> None:            # writes data/chart_coins.json (tmp+replace)
panel.py:1287:            set_wanted(syms)             # manual /api/coins — no perch gate
panel.py:186:                set_wanted(want)          # autopilot — gated by perch_owns_charts() at 126-128
perch.py:166:    write_atomic(WANTED, want)           # stratton-oakmont-perch.timer, every 2 minutes
```

Proof: the panel's autopilot deliberately stands down while perch runs
(`panel.py:126-128`, `perch_owns_charts()`), but the manual switch handler (`panel.py:1280-1290`)
does not. Perch rewrites the file every 2 minutes (`services/stratton-oakmont-perch.timer`,
`OnUnitActiveSec=2min`), so an operator's choice from the phone is silently overwritten within
one perch cycle and the guard (`guard.py:161-179`) then enforces the perch's coin. Additionally
the panel offers two pickers (chart 1 + chart 2) while only one standing window exists — the
guard trims the wanted list to the single non-scout window (`guard.py:290: want = want[:held]`),
so chart 2 can never be enforced on the scout's window.

Fix: give the manual switch its own override file that perch checks before rewriting (or refuse
the switch with a message while perch owns the charts, as the autopilot already does).

## 3. [major, conditional on server state] `atrscan.py:253` vs `boom2.py:464` — two services write `watchlist.json` with different selection criteria

```python
atrscan.py:253:  write_atomic(WATCH, [r["sym"] for r in keep])     # ATR>=2.5 top-60, stratton-oakmont-atr.timer every 10 min
boom2.py:464:    write_atomic(BOT / "data" / "watchlist.json", watch)  # reach/shapes/min-lev 50, stratton-oakmont-boom.timer every 30 min
boom2.py:458:    write_atomic(BOT / "data" / "watch_measures.json", full)
```

Proof: both timers ship in `services/` and both write the file the scout walks
(`scout.py:124-127`) and perch filters by (`perch.py:83-84,104`). The file flip-flops between two
different coin populations every 10-30 minutes; and because `watch_measures.json` (reach/smooth)
only covers boom2's list, whenever atrscan's list is in force the scout walks coins with no
reach/smooth measurements — exactly the state `papertrade.py:1274-1289` warns silently drops the
heaviest judgement term. HANDOFF.md says boom2 is retired, but the unit/timer files still ship —
verify `systemctl is-enabled stratton-oakmont-boom.timer` on the server.

Fix: remove the watchlist write from the retired boom2 (leave boom.json + watch_measures.json),
or disable the stratton-oakmont-boom.timer, so atrscan is the single watchlist owner.

## 4. [major] The live filter artifact (`model.npz`) is never retrained by any scheduled job

```python
services/stratton-oakmont-dataset.service:10: ... && /home/stratton-oakmont/venv/bin/python -u /home/stratton-oakmont/bot/dataset/selector.py
                                   --samples /home/stratton-oakmont/bot/data/dataset/samples/features.npz > .../selector.txt'
dataset/selector.py:251:    if args.save_model:      # the ONLY writer of model.npz
tools/healthcheck.py:249-250: filter_model.model_check(DATA / "dataset" / "samples" / "model.npz")
filter_model.py:71:            return [("fault" if hours > 48 else "ok", ...
```

Proof: the nightly pipeline (stratton-oakmont-dataset.timer, 18:30 UTC) runs selector with `--samples`, which
evaluates AUC and saves nothing; only `selector.py --save-model` writes the artifact that
`stratton-oakmont-paper.service` and `stratton-beast.service` load as a hard startup gate (`--filter-model
data/dataset/samples/model.npz`). The healthcheck comment says its 48h age check exists to verify
"the nightly retrain is still alive" — a retrain that is not scheduled anywhere. The book's
hot-reload (`papertrade.py`, mtime-based, 60 s check) therefore only ever fires after a manual
retrain: the learned filter silently ages, and after 48 h the health alarm fires with no scheduled
remedy.

Fix: append `&& selector.py --save-model data/dataset/samples/model.npz` to the stratton-oakmont-dataset
service (or add a dedicated retrain unit) and keep the artifact-age check.

## 5. [minor] Paper simulator misprices two fills: the edge limit as a taker, the late-market fallback as a maker

```python
papertrade.py:3666:  e = cross(pnd.sym, pnd.want, pnd.side)   # resting limit, but half-spread added
papertrade.py:3779:  e = pnd.want                             # the other limit paths fill at the level
papertrade.py:3834:  e = px                                   # late-market (taker) fills at raw last
papertrade.py:2774:  # A taker crosses the book, so the fill is the far side, not the last print.
```

Proof: a resting limit at `want` can never fill worse than `want` on Bitunix, yet the `--entry
edge` path (the paper unit's entry mode) books every fill at `want` + half the spread, inflating
entry cost on every trade in the paper record; the late-market fallback is a genuine taker order
but books the raw last price with no spread, contradicting the code's own stated rule (line 2774)
and the market path's use of `cross()`/`depth_fill` (`papertrade.py:4601`). Both bias the one
record the paper experiment exists to produce.

Fix: book the edge fill at `pnd.want` (as the `touched` branch does), and price the late-market
fill through `cross(sym, px, side)` or `paper_venue.depth_fill`.

## 6. [minor] `scout.py:496-497` measures wick risk against a hardcoded 1.0% stop

```python
scout.py:496:            "wick": wick_risk(ohlc, "BUY" if d["plan_dir"] == 1 else "SELL",
scout.py:497:                              1.0, t=t),
papertrade.py:4085:      _wr = (s.get("wick") if s.get("scout") ...)   # the book trusts the scout's number
papertrade.py:4086-4087:        else wick_risk(ohlc, s["side"], _csl, ...)   # real stop on the window path
```

Proof: the same council plan measured on the book's own window is scored against the real stop
`_csl`; measured by the scout it is scored against a fixed 1.0% stop, whatever the plan's actual
stop is. The number feeds `--max-wick` (`papertrade.py:4211-4212`) and the confidence wick term.
Dormant under the current flags (max-wick 0, wick weight 0.0) but wrong whenever the flag is used,
and it makes the same plan score differently depending on which process found it.

Fix: drop the scout's copy and compute `wick_risk` in the book for scout rows too (it has `_csl`
at hand), or pass the book's stop to the scout through the existing `_ROOM`/`_SHAPE` pattern.

## 7. [minor] `tools/funnel.py:79-84` — the beast funnel is counted since the *paper* unit's restart

```python
funnel.py:34-37:  ["journalctl", "-u", UNIT, ...]            # journal follows --unit
funnel.py:82:     ["systemctl", "show", "stratton-oakmont-paper", "-p", "ActiveEnterTimestamp", ...]  # hardcoded
```

Proof: `dayreport --unit stratton-beast` (`tools/dayreport.py:244-247`, wired in
`services/stratton-oakmont-report.service`) invokes funnel with `--unit stratton-beast`, which reads the beast's
journal but the paper unit's restart timestamp. The beast funnel then counts lines from before
the beast's last restart (old wordings) or misses fresh ones — the exact mixed-denominator failure
the file's own comments (`funnel.py:185-190`) say it was rebuilt to avoid, and it can light the
"candidates vanished" fault over lines from a replaced version.

Fix: `["systemctl", "show", UNIT, "-p", "ActiveEnterTimestamp", "--value"]`.

## 8. [minor] `papertrade.py:4874-4879` — the beast's lock file is never cleaned up on exit

```python
papertrade.py:2823:  lock = BOOK.with_suffix(".lock")          # beast -> data/beast.lock
papertrade.py:4875:  lk = Path(__file__).resolve().parent / "data" / "paper.lock"   # hardcoded
```

Proof: with `--book` (the beast service), the process takes `beast.lock` but the `finally` block
only ever unlinks `paper.lock`, so every beast exit leaves a stale lock behind (and prints
"book kept in data/paper.json" for every book). The stale-lock takeover at `papertrade.py:2824-2834`
masks it, but it defeats the clean-exit invariant the block exists for and reopens the pid-reuse
misfire window on every restart.

Fix: capture `lock` in an outer scope and unlink that path in `finally`.

## 9. [minor] `papertrade.py:2683-2685` — a venue-cancelled order is cancelled again

```python
2683:            if st["status"] == "cancelled":
2684:                resting.remove(pnd)
2685:                cancel_resting(pnd, "cancelled at the exchange")
```

Proof: `cancel_resting` sets `pnd.settled` and sends `cli.cancel_orders` — a signed write for an
order the venue already cancelled — contradicting the `Pending.settled` contract
(`papertrade.py:106-109`: "a cancel is never sent twice for one order"). Harmless today, but a
redundant destructive request per venue-side cancellation.

Fix: in this branch, book whatever filled and drop the order without re-cancelling (factor the
fill-bookkeeping out of `cancel_resting`).

---

## Examined and found clean (the specific suspicions from the brief)

- **Filter feature trace (item 3) — consistent end to end.** The 13 names and order are identical
  in `dataset/selector.py:40-41`, `filter_model.py:24-25` and the dict built by
  `papertrade.py:1217-1229`; every transform matches (same `float()` casts, `counter` as
  `float(bool(...))`, `side` as ±1, `hour = (t % 86400)/3600` on both sides). Training imputation/
  standardisation (`selector.py:95-127,201-236`: median impute in raw space, then standardise the
  26-column matrix with missingness flags) is byte-for-byte the same arithmetic as
  `FilterModel.score` (`filter_model.py:46-58`). `atr/trend/vol20/mom6h/mom1h/volx` come from the
  same `atrscan` functions in training (`make_dataset.measures_for`) and live
  (`atr_measures.json`). Residual (non-bug) gap: live features are the finder's latest reading,
  training features are computed on candles strictly before signal time — a mild distribution
  shift, not an order/transform mismatch.
- **path_bars orientation** (`dataset/sources.py:297-317`): SELL bars hi=1+adv, lo=1-fav — matches
  the settled convention; `engine.walk_path` checks stop before target in the same bar (spanning
  bar is a stop).
- **Walk-forward leakage** (`dataset/selector.py:271-277`): train = strictly earlier trading days,
  test = the day itself; imputation medians and mu/sd come from the train fold only
  (`selector.py:112-126`). No leakage found.
- **Sampler labels** (`dataset/sampler.py:52-78`): the flip logic commutes correctly — for a
  flipped row the stop fires iff the recorded favourable move ≥1.25%, which is the opposite side's
  adverse move, and target iff the recorded adverse move ≥5%. Spanning bar is a stop. (Flip rows
  only exist with `--boundary > 0`, which defaults to 0.)
- **Journal rotation** is per-book (`--book` derives the journal path, `papertrade.py:2090-2093`)
  and append-only; rotation is a size check + `os.replace`; a crash costs at most one line.
- **Backup** (`tools/backup.py`): tmp + `os.replace`, prune glob cannot match the `.tmp` file,
  archive globs keep reset history.
- **riskanalysis** (`tools/riskanalysis.py:44-56`): compounding path is correct — trades were
  sized at 50% of a $100 wallet, so `ret = pnl/100` and `eq *= 1+ret` is the honest path; max
  drawdown and streak arithmetic are right.
- **Paper book write** is atomic (tmp + replace, `papertrade.py` book section), and two processes
  on the *same* book are refused by a per-book pid lock (stale locks taken over).
- **Two books on one box** (stratton-oakmont-paper + stratton-beast): separate ledgers, locks and journals; the only
  shared read-only files are scout.json/boom.json/watch files, which are written atomically by
  their single owners (modulo finding 3 above).
- **Python compatibility**: every audited file parses under 3.11 (ast check over the whole tree);
  the full pytest suite passes on Python 3.11.0 — **520 passed, 1 warning** in ~81 s.
- **Documented, intentionally left** (from HANDOFF.md, not re-flagged): the daily-loss-limit
  rolling-24 h window vs the pace day boundary, and the retired-boom2 quirks.
