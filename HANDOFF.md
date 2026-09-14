# Stratton Oakmont — what this is and everything it took to get here

Written to be read cold. If you are picking this up with no memory of the
work, read this file top to bottom and you will know what the system does,
why every number is the number it is, which mistakes have already been made,
and what is still open.

Everything here is either measured or observed. Where something is a guess it
says so.

---

## 1. What the system does

It watches the crypto coins whose candles are biggest right now, waits for the
Stratton Oakmont Sniper indicator to print a signal on one of them, judges that signal
against what actually separated winners from losers, and takes **eight trades
a day** — one position at a time, half the wallet, five percent target, 1.25%
stop.

The chain, in order:

```
atrscan.py     every 10 min   the biggest candles in the market -> watchlist.json
scout.py       continuous     walks only those coins, reads the indicator on each
perch.py       every  2 min   parks the book's own chart window on the ripest one
papertrade.py  continuous     scores each signal, spends 8 a day on the best
guard.py       every  2 min   keeps the browser, the charts and the candles honest
```

Nothing in it invents a signal. `--source combo` reads what the indicator
publishes — `STRATTON_BUY_SCORE`, its tier, its span, `SNIP_*_VOTE`, `TSL_WIRED`,
`STATE`, `VOTES_PACKED` — off the study on a live TradingView chart, over the
Chrome DevTools Protocol.

---

## 2. The live configuration, and why

`services/stratton-oakmont-paper.service`:

```
--source combo        the indicator's own signal, not a shape this engine invents
--tp 5                target, five percent of price
--sl 1.25             stop, and it moves with the target -- see below
--frac 0.5            half the wallet per trade
--max-exposure 0.5    with --frac 0.5 this is what makes it ONE position at a time
--per-coin-lev        each coin at the most leverage the exchange allows on it
--lev 40              only the fallback when a coin's cap cannot be read
--min-lev 0           nothing is refused for having a low cap
--per-day 8           a budget, not a cap
--day-start 17.5      the day runs 9pm Tehran to 9pm Tehran (17:30 UTC)
--max-per-day 0       the old blunt cap, switched off
--min-entry 40        the floor under the entry score
--min-agents 3        the indicator's own conviction floor
--min-atr 2.5         no signal on a coin whose candles are too small
--with-trend          no signal against the coin's own last two hours
--ct-exit 3.5         leave a winner when the indicator prints against it
```

### The target and the stop move together

A 5% target with the old 2% stop halved every win and left every loss the same
size; the break-even win rate went from 16.7% to 28.6% against a signal that
achieves about 31%. Measured over 163 of the indicator's own signals at a 5%
target:

| stop | R:R | reached target | per trade |
|------|-----|----------------|-----------|
| 0.80% | 6.2 | 17.2% | +3.3% |
| 1.00% | 5.0 | 20.9% | +5.9% |
| **1.25%** | **4.0** | **24.5%** | **+7.3%** |
| 1.50% | 3.3 | 26.4% | +4.6% |
| 2.00% | 2.5 | 31.3% | +3.9% |

Tighter and the stop catches noise rather than a wrong signal; wider and the
reward stops paying for the losses. A test enforces R:R >= 4.

### Per-coin leverage, capped by the stop

The exchange caps its most volatile coins lowest — MARSCOIN and PONS at 20x,
USELESS and TUT at 25x — and a floor of 40 threw away exactly the coins the
ATR scan likes best. Each coin now trades at its own ceiling.

But leverage is still held to what the stop can survive: the exchange closes a
position at about `(1/leverage - maintenance)` of adverse price, so a 1.25%
stop at 75x — where that line sits at 0.83% — would never be reached. The loss
would be the whole margin at a worse price and the exit would be the
exchange's choice. With a 1.25% stop and 0.5% maintenance the ceiling is
**57.1x**; a coin that allows 75 is traded at 57.

This ceiling applies **only** under `--per-coin-lev`. When the operator names
a leverage it is theirs and is never silently reduced — the first version of
this change resized every trade in configurations nobody had asked to change,
and a test now prevents that.

---

## 3. The entry score

With one position at a time, taking a mediocre signal costs the next good one.
Every term was measured at the signal bar over 163 of the indicator's own
signals, and each is worth what it separated. The weights add to exactly 100.

| term | pts | what it measured |
|------|-----|------------------|
| agents >= 4 | 40 | 35.4% reached target against 19.4% at three agents |
| a calm coin | 25 | calmer half 23.9% and +12.7% a trade; wilder half 10.1% and -20.5% |
| six-hour move with the trade | 15 | +7.4% against -14.4% |
| one-hour move NOT already spent our way | 10 | a coin that just jumped our way has made the move |
| volume above the coin's own normal | 5 | +3.2% against -10.5% |
| ATR at least 4% | 5 | enough travel to reach five percent at all |

Two things worth knowing:

- **Agents alone tops out at 40.** Conviction cannot reach a floor of 40
  without the coin. That is deliberate.
- **The indicator's own tier does not rank.** PEERLESS measured +55.5% and
  GOOD +75.4%. It is not gated on. Neither is the indicator's own confluence
  score.

A reading the finder does not have scores nothing, neither for nor against.
Missing is not bad — refusing on a number nobody has would blind the book on
every coin the scanner has not reached yet.

The scale adding to exactly 100 is not decoration. This engine once spent
weeks taking no trades at all because a floor of 70 sat above the highest
score its scale could produce. A test enforces the sum.

---

## 4. The budget — `pace.py`

**Eight trades a day, spent on the best eight the day offers.**

A cap refuses the ninth signal whatever it is, and the ninth is as likely to be
the best as the first. A fixed floor leaves the budget unspent on a rich day
and spends it before noon on a poor one. Neither is what "take the best eight"
means.

So the book keeps every score it has seen for 24 hours and works out two things
from it — how often signals arrive and how good they usually are. Then:

```
slots left / signals still expected today  =  the fraction it can afford
bar = that percentile of the scores the day has offered
```

Eight slots against eighty expected signals means the top tenth, so the bar is
the ninetieth percentile. Two slots with an hour left and three signals coming
means two in three, and the bar falls to the thirty-third. **A generous day
makes it fussy; a thin one makes it patient.**

Observed live on 2026-09-05/06:

```
19:15  bar 52.7   22.2h left, 6.0 signals/h -> can afford the top 6%
20:04  bar 49.5   21.4h left, 5.3 signals/h -> top 7%
20:31  bar 45.3   21.0h left, 5.4 signals/h -> top 7%
21:25  bar 40.4   20.1h left, 4.6 signals/h -> top 9%
```

Two guards on it:

- **Nothing is spent before the day has been looked at.** Below
  `MIN_SAMPLE = 8` scores there is no distribution to be the best of, and
  taking the first arrival is not choosing. `bar()` returns `None`.
- **...unless the day is nearly over.** Inside `LATE_H = 6` hours it stops
  waiting and uses the floor, because a budget that ends untouched is its own
  kind of failure.

`state["scores"]` persists in `data/paper.json`, so a restart cannot hand the
book a fresh eight or an empty memory of what the day has been offering.
`pace.spent()` counts trades opened since the day boundary.

---

## 5. The chart, and what has to be true about it

The indicator runs on a real TradingView chart in a headless Chrome. Three
properties are enforced by `guard.fix_candles()` every round and by the scout
on every symbol change, because a saved layout restores its own and a reload
undoes all three:

1. **Real candles, style 1.** The chart was on Heikin Ashi (style 8) for the
   engine's entire life. A Heikin Ashi candle is an average of the one before
   it, so every reading — the breaking candle, the body, the level, the stop —
   came from a price that was never traded. Verified after the switch: chart
   open matches exchange open to 0.0000%.
2. **Bar spacing 9.** Left alone, the window showed ~150 bars at once, which
   on a 15m chart is thirty hours squeezed into a line.
3. **`scaleSeriesOnly` true.** This one is the important one. The indicator
   publishes `VOTES_PACKED` and `STATE` as plots, and they are packed integers
   in the hundreds and thousands. With the flag off, the price axis had to
   stretch to hold them: on a coin trading at 0.139 the scale ran to **1,345**
   and every candle collapsed into a flat line. It was not a zoom problem and
   no amount of zooming would have fixed it.

**A window that cannot answer the style question is not accused.** Silence is
not evidence, and inventing a fault from a non-answer is how an alarm stops
being believed.

---

## 6. Traps — every one of these cost hours

**TradingView streams only TWO charts on this account.** A third window opens,
loads, shows a price, and then its feed silently stops. No error anywhere. The
book goes on reading a chart whose newest candle is an hour old. Every FROZEN
line on 2026-09-05 was this. `MAX_WINDOWS = 2` is enforced in
`guard.check_charts`, `guard.repair_charts` and `tools/healthcheck.py`.

**`chart_windows()` index order does NOT match the order of tabs in
`/json`.** Never match a tab to a window by position.

**Close any window you open BY TARGET ID, never by index.** Indexes move as
the scout and guard open and close tabs. Closing by index orphaned a tab,
TradingView stopped feeding it, and forty minutes later the book reported a
frozen chart caused by a measuring tool.

**`data().each(function(i,v){...})` needs `return false` to CONTINUE.**
Returning `true` stops at the FIRST bar, which is ~300 bars and 75 hours old
on a 15m chart and never changes. Reading it that way made every window look
dead and I closed two healthy tabs on that evidence. The engine's own
`BARS_JS` uses `return false`; copy it.

**The right-hand TradingView panel lags a symbol change** and can show the
previous coin's details while the chart has moved on. UI lag only — the engine
reads the study plots, and `scout.belongs_to()` confirms the study has followed
before any number is read.

**`load_dotenv()` at import time puts real API credentials into the
environment.** The test suite has autouse fixtures that strip
`BITUNIX_API_KEY`/`BITUNIX_API_SECRET`/ntfy vars and block `socket.connect`,
because a test once hit the live exchange.

**Unit files quote their own flags in long comments.** A test matching the
whole file with a regex found `# --lev 40 and --sl 2.0 go together` — a
paragraph about an older setting — and reported a stop the book had not used
for hours. `tests/units.py` parses only the ExecStart line; every unit-flag
test uses it.

**Do not edit `papertrade.py` with a scripted `str.replace`.** A single
replace hit three sites at once and broke the file's indentation in a way that
parsed fine locally and failed at runtime. Use targeted edits.

---

## 7. Bugs found and fixed — the ones that explain the design

These are here because each one is a class of mistake that will recur.

**The book had never opened a position in its life.** Three bugs in a chain:
the panel autopilot compared both chart windows (window 1 is the scout's,
changing every few seconds) so it switched charts and restarted the book every
hour; resting orders were never persisted so every restart destroyed them; and
their signal keys stayed in `seen` so they were never re-offered. With
`--fill-bars 8` (two hours) against a 60-minute restart cycle, no order could
survive.

**The panel autopilot was still restarting the book, hourly.** Found
2026-09-05 at 23:28 while asking why the funnel's clock had reset. `perch.py`
owns `data/chart_coins.json` — every two minutes it parks the book's window on
the ripest high-ATR coin. Autopilot wrote the same file once an hour from
`data/eligible.json`, the retired scanner's ranking rather than the ATR
watchlist, and enforced its choice with `strattonctl coins`, **which restarts the
book**. At 23:08:52 it wrote COLLECTUSDT and restarted `stratton-oakmont-paper` and
`stratton-oakmont-guard`; sixty-two seconds later perch wrote NOMUSDT over it. Two owners of
one file, and the loser's move cost a restart. This is the same hourly restart
that once meant no resting order in the whole life of this book ever survived
long enough to fill — the earlier fix stopped it comparing the scout's window,
which was only half the cause. Now `perch_owns_charts()` stands autopilot down
whenever `stratton-oakmont-perch.timer` is active, ahead of the on/off toggle, and every
read of that toggle defaults to **off** so a missing file cannot hand the
charts back. Five tests in `tests/test_panel_autopilot.py`.

**perch asked for more chart windows than exist, and the guard then gave up
forever.** Found 2026-09-06 08:10 with SELL CASHCATUSDT open. Two defects,
stacked:

*perch* built its want list as "every held coin, plus ripe ones up to
`1 + len(held)`". With one position open that asks for two coins — but
TradingView streams only **two** charts and the scout owns one of them, so
exactly **one** window can hold anything. The second coin could never be
placed. Fixed with `HOLDABLE = 1`: a held coin takes the one window there is,
and ripe coins fill it only when nothing is held.

*The guard* made it permanent. Its chart strike counter only ever grew, and
past three it stopped attempting repairs entirely — reporting
`CASHCATUSDT missing from the charts (rebuild did not take)` every two minutes
and never trying again. Fixing perch was not enough on its own: the guard had
already stopped and stayed stopped. `CHART_RETRY_AFTER = 18` now resets the
counter after about half an hour, so it is three attempts, then quiet, then
three more. The original intent — do not reopen tabs every two minutes at a
session that needs a person — is kept; the giving-up is no longer forever.

**A fault that cannot be repaired is worse than no fault at all: it teaches
the operator to ignore the alarm.** An existing test asserted the broken
behaviour ("the ripe coin was not added beside it"); it was asserting a window
that does not exist, and now asserts the constraint instead.

**The healthcheck alarmed at the guard's own memory threshold.** Found
2026-09-06 04:08. The guard owns memory at 250 MB available and deliberately
waits for a SECOND consecutive bad reading before restarting Chrome, because
one dip is usually Chrome allocating and giving it straight back. The
healthcheck faulted on a single sample at the same number — so the phone got
alarmed twice for a condition the guard had decided not to act on, and
availability recovered on its own both times. Same class as the repaired-freeze
alarm. It now alarms only below 150 MB, where the guard's policy has actually
failed, and between 150 and 250 it reports the number and says who owns it.

**`--min-lev` was dead on the traded path.** It existed, was documented, was
set to 40, and was only ever checked on the shape source. On `--source combo`
there was no leverage floor at all, and the book opened USELESSUSDT at 25x
under a `--min-lev` of 40 with nothing said. `--max-entry-r` had exactly the
same defect earlier. **Whenever a flag is added, check it is enforced on the
source that actually trades.**

**The book silently dropped every scout row on `--source combo`** with a bare
`continue` and no log line — the scout marks all its rows `council: True`.
Eleven coins walked every forty seconds, a plan delivered, and nothing between
"scout brought 1 plan" and silence. Invisible to the funnel, because an
unlogged drop cannot be counted.

**The scout never published the indicator's signal**, so on `--source combo`
the book could only see the indicator on the two windows it follows and
evaluated nothing for 57 minutes. `scout.read_combo()` now reads it per coin.
It reads only the last CLOSED bar, because `confirmOnly` defaults false in the
Pine and a signal can appear intrabar and vanish.

**`scout.ranked()` ignored any watchlist under ten names** — an old guard
against reading a half-written file, obsolete since the scanner writes
atomically. The ATR finder keeps only coins that can reach the target and
produced eight, so the scout silently fell back to the ranking of a scanner
that had been switched off, and spent a morning walking coins nobody had
chosen.

**The pacer recorded only ACCEPTED scores** despite a comment saying
otherwise. The sample began at the floor, every percentile sat too high, and
the book grew fussier the worse the day got — the exact opposite of the
design. It also never reached a sample at all.

**`--ct-exit` gated on the wrong thing.** Reading 4USDT bar by bar showed the
`ct` flag is a label on the signal's own type — it appears on the same bar and
points the same way — so the exit only fired on a counter-trend opposing
signal and ignored a plain one, when both say the same thing about the move.

**Six false alarms reached the operator's phone, all of them my own
leftovers**: a repaired freeze alarming for twenty minutes when the guard
fixes it in three; `boom.json` freshness demanded from a scanner retired on
purpose; a service caught mid-restart reported as down; `following 0 chart
window(s)` latched from a line printed once at startup; the evaluation counter
knowing only the shape source's vocabulary; `stratton-oakmont-boom.timer` inactive by
design. Each now has a test. **A status page nobody believes is worse than
none.**

**`tools/funnel.py` miscounted five times** — mixed denominators reporting
584%, a line matching two gates at once, post-score refusals counted as
passes, an expiry reading as "still waiting" forever, and a permanent false
note whenever the book was full. Its counting is now in `count(log)` with
`tests/test_funnel.py` checking the arithmetic against logs written by hand.

---

## 8. What was measured, and what it says

All of it from `data/signals_study.json` — 163 of the indicator's own signals
across the highest-ATR coins over ~50 hours, entry at the next bar's open, and
any bar spanning both target and stop counted as a **stop**.

**The old shape source does not work.** `breakout()` — a quiet band then one
candle out of it — over 41 hours and 70 coins: 2.5% reached +10%, 84.9%
stopped, and no target size rescued it. The reason is structural: it needs a
band that has gone quiet, and a coin in a powerful move never goes quiet.
Sitting on the eleven biggest movers in the market for fifty hours produced
eleven of its signals and zero winners.

**The indicator's own signal does work.** Same measurement, 163 signals at a
5% target: 25.8% reached target overall; ATR >= 2.5% gave 30.5%; agents >= 4
gave 35.4%; both together 37.0%.

**Which council members are worth anything** (target 10%, stop 1%):

| member | agrees with the trade | disagrees |
|--------|----------------------|-----------|
| MA | n=63, 28.6%, +26.0% | too few |
| Sniper | n=135, 23.0%, +10.7% | too few |
| HTF | n=80, 22.5%, +10.6% | n=55, 16.4%, -5.5% |
| Bank | n=118, 22.0%, +9.0% | n=27, 18.5%, -0.4% |
| Tesla | n=134, 21.6%, +7.9% | **n=9, 0.0%, -44.8%** |
| Team45 | n=82, 20.7%, +5.0% | **n=60, 23.3%, +13.1%** |

**Team45 is the one giving junk** — results are better when it disagrees.
**Tesla disagreeing is a real veto** — nine signals, zero winners.

**Trend alignment.** With the coin's own two-hour direction: 20.7% and +5.3%.
Against it: 13.0% and -12.1%. A coin that had just fallen more than five
percent produced thirteen signals and won **none**. Hence `--with-trend`.

**Momentum inverts by horizon.** Six-hour momentum with the trade is good
(+7.4% vs -14.4%); thirty-minute, one-hour and two-hour momentum with the
trade is bad (-14.4% vs +7.4%). Buy the pullback in a trend, not the spike.

**Regime caveat, and it is a real one.** In that 50-hour window 10 of 11 coins
rose, median +36.9%. BUY signals won 36.4% and SELL 9.4%. That split is regime,
not edge, and no long-only filter was added because of it. Every weight above
comes from one bullish window: the directions are reliable, the exact numbers
are not.

---

## 9. Two case studies worth keeping

**SELL 4USDT — the winner.** Opened 10:45 on 2026-09-05 at 0.026448475. Before the signal the coin fell two candles hard (-4.03%, -4.86%)
then recovered over four. The signal printed on a candle that was **UP
+2.95%**, as a SELL, PEERLESS, 3 agents, with `ct=SELL` — the indicator
labelling its own signal counter-trend — while HTF read long and the council
0/6. Then:

```
11:30  +5.34%   45 minutes in -- a five percent target pays here
11:45  +7.35%
12:00  +8.40%   the peak
12:30  +4.04%
13:20  +2.66%   two and a half hours in, still open, waiting for a ten
                percent the move never had in it
```

This trade is why the target is five and not ten. The pattern: a short pump,
a counter-trend short into it, a quick fall.

**BUY TRIAUSDT — the loser.** The strongest signal the book had seen —
PEERLESS, 4 agents, the indicator's own score 77, ATR 2.91%, every gate
passed. It printed after two rising candles (+2.70%, +1.81%) following a
-6.67% crash, and the very next candle fell -2.92%. Stopped twelve minutes
later. It is the mirror of 4USDT: the indicator's shorts into a pump worked,
its longs into a bounce did not. `--with-trend` exists because of this trade.

---

**BUY WOOUSDT — the first trade of the budget era, and a stop.** Opened
2026-09-06 03:45:20 at 0.012195002, 18.7 seconds after the signal bar, entry
error 0.00R. PEERLESS, 4 agents, entry score 50 against a bar of 40. Notional
$2,500 on $50 margin — fifty times, WOO's own exchange cap. It never went
green: −0.53% at one minute, −1.03% at three, stopped at five.

```
PnL = 2500 x -0.0125  -  2500 x 12/1e4  =  -31.25 - 3.00  =  -$34.25
```

Verified exact against the book. Equity $100.00 → $65.75.

The number worth internalising: **a 1.25% stop at fifty times on half the
wallet costs 34% of the wallet.** That is arithmetic, not a fault — the same
sizing pays $122 on a 5% target, an R:R of 3.6 — but it means the eight-trade
budget has real teeth, and two stops in a row take a third of the account.

**SELL USELESSUSDT — the second, and the same ending by a different road.**
Opened 2026-09-06 04:00:03 at 0.242555010, **1.4 seconds** after the signal.
PEERLESS, 4 agents, entry score 58 — the best of that whole night. $821.88
notional on $32.88 margin: **25x, USELESS's own exchange cap**, a quarter the
leverage WOO ran at.

It behaved, and then it did not:

```
02 min  -0.36%   the usual first wobble
19 min  +0.14%   green for the first time
22 min  +0.73%
25 min  +0.81%   the peak -- +$5.67, still 4.22% from target
31 min  -0.31%
32 min  -1.36%   stopped
```

```
PnL = 821.875 x -0.0125  -  821.875 x 12/1e4  =  -10.2734 - 0.9863  =  -$11.2597
```

Book and calculation agree to 5.3e-15. Equity $65.75 → $54.49.

Two things this trade says. First, **the per-coin leverage cap did exactly its
job**: the same 1.25% stop cost $11.26 here against $34.25 on WOO, purely
because the exchange lets USELESS run at 25 and WOO at 50. The sizing rule is
not decoration. Second, the trade got a fifth of the way to target and gave it
all back inside seven minutes — with `--tp 5` and no partial and no trailing
stop below `--step-at`, a move that goes 0.8% for you and then reverses is a
full stop, not a scratch.

**SELL NIULAIUSDT — the first target, and the pattern that pays.** Opened
2026-09-06 05:00:30 at 0.111795, **28.5 seconds** after the signal, 0.00R entry
error. PEERLESS, 4 agents, the indicator's own confluence 60, entry score 45
against a bar of 40. $1,362.26 notional on $27.25 margin: **50x, NIULAI's own
exchange cap**. The print carried **CT SELL** — the indicator labelling its own
signal counter-trend, on a coin already down 10% for the day.

```
08 min  +2.02%   +$25.84
13 min  +3.96%   +$52.29
19-55   drifts back to +0.1%, dips to -0.31%, sits near flat for half an hour
56 min  +2.37%   +$30.60   the second leg starts
57 min  TARGET   +5.00%
```

```
PnL = 1362.2578 x 0.05  -  1362.2578 x 12/1e4  =  68.1129 - 1.6347  =  +$66.478181
```

Book and calculation agree to 7.1e-14. Equity $54.49 → **$120.97**, above the
$100 start after two stops.

The thing to keep: it went to +3.96% at thirteen minutes, then **gave almost
all of it back and spent thirty-seven minutes going nowhere** before the real
move came. A break-even stop or an early trail would have closed it flat around
minute 50 and missed the whole trade. Held 56.8 minutes. Same shape as the
4USDT winner: a counter-trend short into a coin that had already run.

**SELL CASHCATUSDT — the best signal of the run, and a stop.** Opened
2026-09-06 07:46:02 at 0.227779868, 62.7 seconds after the signal, 0.00R entry
error. PEERLESS, **5 agents** — the first five-agent print of the run — entry
score **60**, the highest the book had seen, against a bar of 40. $1,209.68
notional on $60.48 margin: **20x, CASHCAT's own exchange cap**.

It went nowhere for an hour. Best +1.51%, never within 3.5% of target, and
stopped at **65.0 minutes**.

```
PnL = 1209.6849 x -0.0125  -  1209.6849 x 12/1e4  =  -15.1211 - 1.4516  =  -$16.572684
```

Book and calculation agree to 3.6e-15.

The point of keeping this one: **the highest score of the day lost.** Five
agents, the top of the scale, every gate passed — and it still went nowhere.
The entry score ranks signals; it does not predict them. A day of four trades
is far too small to say anything about whether 60 beats 45, and nothing in the
scale should be changed on the strength of one trade in either direction.

## 10. Tools

Everything below is read-only unless it says otherwise.

```
tools/healthcheck.py [--notify]  is anything wrong RIGHT NOW; exit 1 if so
tools/funnel.py [hours]          where candidates went; exit 1 if any vanished
tools/riskanalysis.py            the arithmetic of the recorded trades: equity
                                 path, drawdown, streaks, expectancy, splits
tools/casestudy.py record|report the whole path of a trade, a line a minute
tools/postmortem.py              every gate, and what it refused, on real candles
tools/signalcheck.py             the indicator's own signals, walked and scored
tools/signalslice.py             slices what signalcheck saved, free
tools/whatif.py                  the min_touches sweep
tools/dayreport.py [hours]       the day, pushed to ntfy
tools/backup.py [--keep 7]       nightly tar of the irreplaceable record
tools/twocharts.py               two side-by-side chart windows on the VNC screen
tools/sync.sh [--apply]          the byte-exact mirror, as a command
atrscan.py                       the coin finder (writes watchlist.json)
perch.py                         parks the book's window (writes chart_coins.json)
```

`tools/casestudy.py` is the one to reach for after any trade closes. It
records price, percent from entry, PnL, distance to target and stop, and what
the indicator was saying at that minute — with a 20-minute freshness limit so
a stale reading is never attached to a moment it did not describe.

---

## 11. Operating

```bash
# health
ssh stratton "cd /home/stratton-oakmont/bot && sudo -u stratton /home/stratton-oakmont/venv/bin/python tools/healthcheck.py"

# the whole test suite, on the server
ssh stratton "cd /home/stratton-oakmont/bot && sudo -u stratton /home/stratton-oakmont/venv/bin/python -m pytest tests/ -q"

# deploy a file
scp -q papertrade.py stratton:/tmp/dep/ && ssh stratton "cp /tmp/dep/papertrade.py /home/stratton-oakmont/bot/ && chown stratton:stratton /home/stratton-oakmont/bot/papertrade.py"

# deploy a unit (both copies, then reload)
ssh stratton "cp /tmp/dep/stratton-oakmont-paper.service /home/stratton-oakmont/bot/services/ && cp /tmp/dep/stratton-oakmont-paper.service /etc/systemd/system/ && systemctl daemon-reload"
```

**Resetting the book** stops the service, archives `data/paper.json` and
`data/casestudy.jsonl`, writes a fresh state, and starts again:

```python
{"equity": 100.0, "start": 100.0, "trades": [], "seen": [],
 "resting": [], "scores": [], "known_syms": []}
```

**A restart is safe** — open positions and resting orders persist and are
restored — but never restart mid-position without reason. The autopilot doing
exactly that, hourly, is why the book never traded for weeks.

**`--live` must never appear in a unit.** `tools/healthcheck.py` fails if it
does.

---

## 12. Still open

- **The regime question.** Every weight in the entry score comes from one
  bullish 50-hour window. The directions held up; the numbers need a second
  regime before they mean much.
- **A realised-volatility ceiling.** Realised volatility of 20-bar returns was
  the strongest single separator measured (high vol -20.5%, low vol +12.7%)
  and a hard ceiling was proposed but not applied — the "calm coin" term in
  the score carries it for now.
- ~~**Chrome's memory.**~~ **Done 2026-09-06.** The tree grows about 80 MB an
  hour and was holding 2.45 GB of a 3.9 GB box after sixteen hours. The
  guard's emergency path never fired because memory kept recovering just over
  its line between rounds, so it never got a second consecutive strike.
  `tools/recycle_chrome.py` + `stratton-oakmont-recycle.timer` now bound the growth every
  two hours instead of reacting to it, and they act only when **no position is
  open** and Chrome has been up at least six hours. An unreadable book counts
  as a position open — not knowing is not permission. Five tests in
  `tests/test_recycle.py`.
- **`MIN_SAMPLE` costs the first signals of the day, and they can be the best
  ones.** Observed 2026-09-06: the day's highest score, MAGMAUSDT at **60**,
  arrived at 17:45:22 — fifteen minutes into the day, inside the eight-score
  window where `bar()` returns `None` and refuses to spend. Nothing scored
  above 30 for the following six hours. The guard is right in principle —
  taking the first arrival is not choosing — but it is not free, and no
  measurement yet says whether the first eight signals of a day are worse than
  the rest. Until one does, leave it alone.
- **`--with-trend` uses a two-hour horizon**, while the feature study says
  alignment at one-to-two hours is bad and at six hours good. The gate refuses
  signals pointing against the coin; it does not refuse ones that are already
  extended. It does half its job.

---

## 13. The standing instruction

From the operator, and it governs how to work here:

> No comparisons. No option tables. No A-vs-B. Execute, and report facts.
> On a monitoring pass where nothing changed and there was no work to do, say
> nothing at all.
> Every important thing goes into this file as it happens, and is deployed to
> the server in the same pass. Nothing important lives only in a transcript.

Fix operational faults directly: diagnose, write a test, run the whole suite,
deploy. Do not change a number that is a choice without asking. And **look at
the chart** — a whole day was spent reading values through CDP while the price
scale was stretched to 1,345 and every candle was a flat line, plainly visible
in screenshots that were taken and never read.

---

## 14. Running log

Newest last. Every pass that found something writes a line here; quiet passes
write nothing.

- **2026-09-05 17:30 UTC** — the book was reset to $100.00 and the trading day
  re-anchored to 17:30 UTC (9pm Tehran). Zero trades since.
- **2026-09-05 21:39 UTC** — this document written and deployed. 397 tests
  green both sides.
- **2026-09-05 21:42 UTC** — 4.2h into the day, 17 scores, the bar has come all
  the way down to its floor of 40 and cannot fall further. The strongest thing
  the day has offered scored 60; the median is 15. SELL MAGMAUSDT, which the
  indicator itself called PEERLESS at 52/100 confluence, scored **15** here:
  three agents pay nothing, and only the 4.4% ATR found anything to pay for.
- **2026-09-05 23:28 UTC** — found the panel autopilot still restarting the
  book hourly and fighting perch over `chart_coins.json`; it had restarted
  `stratton-oakmont-paper` and `stratton-oakmont-guard` at 23:08:52. Gated it behind
  `perch_owns_charts()`, defaulted the toggle off, set the live
  `autopilot.json` to off, added `tests/test_panel_autopilot.py`. 402 tests
  green both sides. No position was open, so nothing was lost.
- **2026-09-06 00:25 UTC** — the book's start time held past the autopilot's
  hourly boundary; the fix is holding. Two more refusals: BUY NOMUSDT at
  00:00:00 (PEERLESS, 4 agents, indicator 52) scored 20 — four agents paid 20
  and every coin reading paid nothing; BUY FLOCKUSDT at 00:15 scored 15.
  Recorded a finding in section 12: the day's best score of the night, MAGMA
  at 60, landed inside the MIN_SAMPLE window and could not be spent.
- **2026-09-06 04:08 UTC** — the first two trades of the budget era, both at
  four agents, both PEERLESS, both entered within 19 seconds of the signal.
  **BUY WOOUSDT** at 03:45:20, score 50, 50x (its own cap), stopped in five
  minutes for exactly **-$34.25**; equity $100.00 → $65.75. **SELL
  USELESSUSDT** at 04:00:03, score 58, 25x (its own cap), still open, best
  +0.20%, currently around -0.5%. Spent 2 of 8. Wrote up WOO as a case study
  in section 9. Also this pass: fixed the healthcheck's memory alarm (section
  7) and built the Chrome recycle timer that section 12 had been asking for.
  407 tests green both sides.
- **2026-09-06 04:47 UTC** — **SELL USELESSUSDT stopped** after 31.9 minutes
  for exactly **-$11.26** (821.875 x -0.0125 = -10.2734, minus 821.875 x
  12/1e4 = 0.9863; book and calculation agree to 5.3e-15). Best +0.81% at 25
  minutes, then gave it all back in seven. Equity $65.75 → $54.49, 2 of 8
  spent, both stops. Written up as a case study in section 9. The per-coin
  leverage cap is visible in the two numbers: the same 1.25% stop cost $11.26
  at 25x and $34.25 at 50x.
- **2026-09-06 05:57 UTC** — **SELL NIULAIUSDT reached TARGET**, +$66.478181
  exactly (1362.2578 x 0.05 = 68.1129, minus 1362.2578 x 12/1e4 = 1.6347; book
  and calculation agree to 7.1e-14). Held 56.8 minutes, best +5.33%, worst
  -0.31%. Equity $54.49 → **$120.97**, above the $100 start. 3 of 8 spent:
  two stops and one target, net **+$20.97**, which is the R:R working as
  designed. Written up as a case study in section 9 — including the thirty-seven
  minutes it spent going nowhere after an early +3.96%, which a break-even stop
  would have turned into a scratch.
- **2026-09-06 08:26 UTC** — **SELL CASHCATUSDT opened** at 07:46:04, score
  **60** — the day's highest, and the first FIVE-agent print. Also found and
  fixed the perch/guard chart fault above (section 7): perch was asking for
  two coins when one window can hold one, and the guard had permanently given
  up repairing it. Both fixed, guard restarted, `charts: fixed (2 charts
  healthy)`, window 0 back on the held coin. 413 tests green both sides.
  Separately, the Chrome recycle built earlier did its job unprompted: it
  declined at 04:12 with a position open, then ran at 06:13 with nothing open
  and took memory from 302 MB to **2672 MB**.
- **2026-09-06 09:35 UTC** — **the paper book was stopped on request.**
  `stratton-oakmont-paper`, `stratton-oakmont-case.timer` and `stratton-oakmont-perch.timer` are inactive; the scout,
  guard and Chrome are still running. Final book: **equity $104.40** from a
  $100.00 start, 4 of 8 spent, all four closed — WOO -$34.25 (stop),
  USELESS -$11.26 (stop), NIULAI +$66.478181 (target), CASHCAT -$16.572684
  (stop). One win in four, net **+$4.40**, which is what a 3.6 R:R does at a
  25% hit rate.
- **2026-09-06 08:45-09:35 UTC — SSH to the box was unreachable for fifty
  minutes** and the stop had to wait for it. Worth recording because of what
  it was NOT: the machine never stopped working. It wrote **2,921 journal
  lines** during the window and the scout kept sweeping coins throughout.
  No OOM, no failed unit, no UFW rule on port 22, load average 0.5. TCP
  connected every time and the SSH banner never arrived. The cause is outside
  the box — the network path or the provider's filtering — and nothing on this
  side can say more than that. **A retry loop that stops the book the moment
  the box answers is the right response**; it ran every 25 seconds and stopped
  it 15 tries in. CASHCAT had already stopped out at 08:51, inside the
  blackout, so nothing was lost by the delay.
- **2026-09-06 — the indicator's higher timeframe moved from 4h to 1h, on the
  operator's word that 4h was wrong.** The Stratton Oakmont study's own "Higher timeframe"
  input drives the HTF phase gate, the HTF council vote and the ripeness tide,
  so it is one setting with three faces. Changed everywhere it is used:
  `tools/sethtf.py` (the tool that pushes the value onto every chart window)
  now sets `60`; the Pine default in `pine/Stratton_Oakmont_Sniper.pine` is now `60`, so a
  re-added study or a layout reset lands on 1h instead of 30m; README's
  settings line and `tools/collect.py`'s warning now say one hour. The live
  charts were switched in the same pass: `sethtf.py` was deployed to the box
  and run — window 0 was already at 60 (switched by hand earlier), window 1
  was still on 240 and is now set to 60; both layouts saved, so the change
  survives the scout walking and a guard rebuild. A saved TradingView layout
  overrides the Pine default, which is why the tool run mattered and why a
  fresh chart inherits the layout rather than the Pine. `hunt.py`/
  `hunt_paper.py` were left alone: their 4h resample is the hunt strategy's
  own EMA21 trend, not the Stratton Oakmont indicator's higher timeframe. A trading
  dataset was built from this workflow in the same pass — section 15.

## 15a. The engineering pass — journal, silence, risk arithmetic

All of it approved by the operator; nothing here touches the target, the
stop, the budget or any score weight.

- **`journal.py` — the book now journals its own decisions.** A logging
  handler attached in `papertrade.main()` appends one JSONL line per
  decision to `data/book_events.jsonl`, derived from the book's state path
  (so tests and second books journal beside themselves). The lines are the
  same decision lines the funnel parses; the branches are spelled the same
  as the dataset's. Append-only, rotated at 64 MB, and a journal failure can
  never take a poll down. 21 tests in `tests/test_journal.py`.
- **Three silent refusals now say so out loud.** The wiped-account skip
  (`equity is gone`), on both the council and combo paths, and the fill
  refused because the coin is already held (`fill skipped ... already in`,
  four fill sites). The funnel counts both now, and the "still waiting"
  arithmetic subtracts the new fill-skip line.
- **`tools/riskanalysis.py` — the arithmetic of what already happened.**
  Walks `data/dataset/trades.jsonl` like one compounding wallet: equity
  path, deepest drawdown, stop-streak clustering, expectancy, and the same
  numbers split by side/agents/tier/counter. Its first run says: over the
  full recorded population the live gate chain selected 132 trades, 110
  closed, **8% reached +5% before -1.25%** (bar-level, spanning-bar-is-a-
  stop, entry at the recorded next-bar open, ct-exit off). Only 9 of 110
  ever reached +5% at any bar, so the miss is not a stop-order artifact;
  4 of the 101 stops had been +3.5% or better. This contradicts the
  24.5% the 163-signal study measured — because that study hand-picked the
  highest-ATR coins while the recorder captured everything the windows
  showed. The population is the strategy. Verified independently: a manual
  re-trace of all 110 exits against the raw fav/adv data matched every one.
- **Continuous ground truth.** `stratton-oakmont-recorder` is enabled again on the box
  (it was inactive and the signal files had not grown since 2026-09-04), so
  every new print is collected at the now-correct 1h setting.
  `stratton-oakmont-collect.timer` appends council history every six hours — its unit
  refuses to run while `stratton-oakmont-paper` is active, because the collector walks
  the book's window. `stratton-oakmont-dataset.timer` rebuilds the flow dataset daily at
  18:30 UTC from the growing files.
- **`tools/sync.sh`** — the byte-exact mirror is now a command: dry run by
  default, `--apply` to push, `--with-dataset` to include the regenerated
  `data/dataset/` outputs. `data/` itself is never synced — it is live
  state on both machines.

The scout coverage gap that was proposed (finder keeps 60, scout walks 40)
does not exist: the unit runs `--top 150`, so the scout walks the whole
watchlist. Nothing to change there.

## 15b. The edge sweep — what the recorded market actually pays

Built on the operator's word that the market has an inefficiency on heavy,
high-ATR coins where an orchestra could take eight winning setups a day.
`dataset/sweep.py` answers it on the 10,939 recorded signals, same 5% target
and 1.25% stop, same spanning-bar-is-a-stop convention (462 tests green both
sides). The results, measured 2026-09-06:

- **No population slice breaks even at this geometry.** ATR 0-1%: 1.9% hit
  (n=8016); 1-2%: 10.8%; 2-2.5%: 15.3%; 2.5-4%: 13.7%; 4-5.5%: 14.8%;
  5.5%+: **13.5% (n=52)**. Break-even at 4R is 21.9%; the whole recorded
  market sits below it. Agents, tier and the counter flag do not separate
  at all (4-5% everywhere); measured with-trend helps slightly (6.7%).
- **The live gates select 254 of 10,702** measured candidates: 11.0% hit,
  still below break-even. The strictest slices (gated AND ATR>=5.5, n=10)
  reach 20.0% -- the sample is too small to call an edge.
- **The budget's premise does not hold on this population.** The best 8 of
  each day by the book's own entry score hit 10.0%; everything else the day
  offered hit 11.2%. The score ranks signals; on this record it does not
  rank winners. Supply existed -- 2 of the 5 trading days held 8 winners
  among 40-140 candidates -- but nothing in the recorded features picked
  them out ahead of time.
- **The risk half.** Kelly is zero below a 20% hit rate; at the 163-signal
  study's 24.5% it is 3.3% of the wallet per trade; at 39% it is 21.9%.
  The seeded ruin simulation: an 8% hit rate ruins the account at any real
  risk; 24.5% with 2-5% risk compounds (median x2.4-4.4 in 30 days); 39%
  with 5% risk is the strong edge the design assumed.
- **The one variable that could reconcile this with the 163-signal study**
  is whether a coin was ON the finder's list when its signal fired -- the
  study hand-picked the top movers, the recorder captured everything the
  two windows showed. The finder now appends `data/watch_history.jsonl`
  (one line per scan: timestamp + the kept list), so every future sweep can
  slice on actual list membership at signal time. That, plus the recorder
  collecting at 1h, is the decisive next measurement -- no conclusion
  before it exists. `stratton-oakmont-dataset.timer` now runs the sweep after each
  dataset rebuild and writes `data/dataset/sweep.txt`.

## 15c. The learned selector — the "makes sense" filter, measured

The operator's read was that the edge is the golden triangle itself and the
over-engineered layers around it are noise; the missing piece is the human
filter that knows which triangle prints make sense. `dataset/selector.py`
tests whether that filter can be LEARNED: a logistic regression over the
recorded signal-time features (ATR, trend, vol20, 6h/1h momentum, volume,
agents, tier, the indicator's own score, colour, counter flag, side, hour),
trained leave-one-day-out -- no row is scored by a model that saw its day.
468 tests green both sides; the server's newer signals file reproduces the
same numbers.

- **The filter is learnable, and the hand-built score is not just weak --
  it is anti-ranked.** Walk-forward AUC of the learned model: 0.758. AUC of
  the book's entry score: 0.408, below the coin-flip 0.5. The six hand-
  weighted terms rank losers ahead of winners on this record.
- **The lift.** Unseen days baseline 4.3-4.4%; the model's top 5% hits
  16.7-16.9%, top 10% 14.6%, top 20% 12.3% -- a 3.5x lift, consistent on
  every day with real sample size (15-20.5% top-10% on the big days against
  a 3.5-7% base). Still below the 21.9% break-even at 4R, so no bet is
  justified yet -- but the direction is real and the model has 13 features
  and one week of data.
- **What carries the edge, per the coefficients:** ATR is the dominant
  feature (+0.24 standardised) -- the operator's heavy-coin thesis gets
  direct support. The indicator's own confluence score and tier are
  slightly NEGATIVE (the tighter the convergence, the worse -- the same
  thing the 294-signal tier measurement found). vol20 negative here
  (against the hand score's calm bonus), mom1h positive, later hours worse.
- **What this means for the build.** The "agentic" step the operator wants
  is exactly this, as a service: retrain daily on the growing record, emit
  a learned probability beside every live signal in the journal, paper-test
  it, and let it replace the entry score only when its out-of-sample top
  slice clears break-even. The two instruments it needs are already
  running: the recorder at 1h and `watch_history.jsonl` for list membership
  at signal time.

## 15d. The million-sample market and the transfer test

What the operator asked for from the start: a generator that builds millions
of trade samples so the robot has SEEN everything. `dataset/sampler.py`
builds 10 million of them the honest way -- the features are jittered
around real recorded signals (each synthetic row keeps its anchor's own
categories, missingness pattern and, crucially, its anchor's RECORDED
post-signal path), and the label is computed by the engine's own walk_path.
Nothing about the market's behaviour is invented; only the pairing is
synthetic. `--boundary` adds uniform box-coverage rows with random paths,
marked in the prov column -- measured to HURT the learned selector
(0.708 -> 0.584 AUC at just 10%), so it defaults to 0.

The transfer test (`dataset/selector.py --samples`), walk-forward on the
real unseen days, 472 tests green:

- entry score AUC 0.408 -- the six hand-weighted terms anti-rank winners
- learned model, trained on real history only: AUC 0.758, top-10% 14.6%
  hit against a 4.4% base
- pretrained on the 10M synthetic sample, NEVER seeing a real row:
  AUC 0.708 -- the sample carries most of the real conditional
- pretrained then fine-tuned on real history: 0.624 -- the current
  fine-tuning schedule does not yet beat either; a tuning matter, not a
  dead end

The honest ceiling stands: even the learned top slice (14.6%) is below the
21.9% break-even at this R:R, so no bet is justified yet. The machine is
built and turns nightly: dataset, sweep, 10M sampler and selector all run
on `stratton-oakmont-dataset.timer`; the real record grows at 1h; the day the walk-
forward top slice clears break-even is the day the learned filter replaces
the entry score. Until then it stays advisory.

## 15. The flow dataset

**Every decision this engine can make, one row each, produced by the engine
itself.** Built 2026-09-06, 11k+ rows, generated by running the REAL
`papertrade.main()` loop — the same stand-ins the tests use (`tests/fakes.py`,
`tests/harness.py`) — over the REAL recorded signals and the REAL candles that
followed them. Nothing here re-implements a gate: the log lines ARE the reason
strings, and the book on disk IS the trade record.

```
data/dataset/flow.jsonl      the event stream: universe, scan, scout, perch,
                             signal, rest, exit -- one row per decision
data/dataset/trades.jsonl    every opened trade, with its real post-entry path
data/dataset/manifest.json   coverage per branch + the 4h->1h flip count
dataset/make_dataset.py      the builder (python3 dataset/make_dataset.py)
dataset/scenarios.py         the branch matrix -- one scenario per decision
dataset/engine.py            drives papertrade.main() on fakes
dataset/sources.py           the recorded data, joined
tests/test_dataset.py        18 tests: every declared branch is produced,
                             scores recompute, PnL matches the book's formula
```

Grounding: `signals.jsonl` (10,939 real indicator prints) joined to
`outcomes.jsonl` (real entry + per-bar favourable/adverse moves) and
`tesla.jsonl`; coin measurements from the recorded `council_history.jsonl`
candles through the finder's own `atrscan` functions; leverage caps and reach
from `boom.json`/`watch_measures.json`. A signal with no recorded history gets
a synthetic quiet prefix, marked `synthetic-prefix` — the signal bar and
everything after it are always the recorded real ones.

Conventions, the same the repo's own replay tools use: a bar spanning both
target and stop is a STOP (which came first inside one candle is unknowable,
and resolving it in our favour is how a backtest lies); entry is the recorded
next-bar price with spread 0, so the fill equals what the market printed; an
unmeasured coin feature is missing, never zero.

What it covers: the finder's keep/drop/fallback, the scout's six row kinds
plus the belongs_to refusal, the eagle's pick (real `perch.ripest()`), every
combo gate in the book's order — backlog, catch-up, stale, orange, unwired,
agents, tier, fat body, thin coin, against trend, low leverage, poor score,
all four pace states, no price, zero equity, the loss breaker, exposure,
one-per-symbol, entry modes now/edge/back/smart/extreme/mid with fills,
expiries and the late-market fallback, the fill-site skips, and every exit —
target, stop, flat, counter, liquidated, stalled, timeout, yielded,
live-reconciled.

HTF: every signal row carries the 4h vote as recorded and the 1h vote
recomputed from the same candles (HMA(55) on 1h, offset one bar — the Pine's
own phase); the manifest counts how often the two disagree.

## 15f. The audit pass, the live filter, and the paper restart

A three-agent audit of the whole codebase (2026-09-06) found three major
and a handful of minor bugs; every major one is fixed with a regression
test in `tests/test_audit_fixes.py`, and the whole suite is 485 green both
sides:

- **`trend_ride()` joined a run at the FAR extreme of the leg** -- the
  level it computed for a downtrend was the OLDEST high, not the newest
  lower high its own docstring describes, which also corrupted the
  "already travelled" gate. Fixed: the level is the newest candle's
  extreme. The real-candle frequency guard in `tests/test_replay.py` was
  retuned to the corrected behaviour (929 shapes / 13,500 bars).
- **The scout's break shapes carried the NEWEST bar's council lean** --
  `read_break` read VOTES_PACKED from `rows[-2]` whatever bar the shape
  was found on, so a shape six bars old was scored on a vote that did not
  exist when it formed. Fixed: the lean is read from the shape's own bar
  (the series window reaches back past LOOKBACK).
- **`scout_plans()` stripped every measurement the scout wrote** -- leg,
  expansion, body, wick and run were dropped on the way into the book,
  which made `--tp-leg` unreachable, the candle floors silently no-op,
  and `confidence()` score every scout council plan zero. Fixed: the keys
  are carried.
- Minors fixed too: the liquidation close charged half the round-trip fee
  (now the same full fee as every other close); "too early in the day" was
  journaled as "budget spent" (now its own branch); the recorder crashed
  on a fresh deploy with no signals file; the finder crashed on a fully
  blind market; `ripe_now`/`coin_atr` zeroed their last-good cache on a
  truncated mid-write read (now kept, like `coin_reach`). Left as they
  are, deliberately: boom2 is retired and its two lock/watchlist quirks
  die with it; the `--catch-up` bar-open measurement and the daily-loss
  rolling window are documented behaviours off the live path.

**The learned filter went live on the paper book.** `dataset/selector.py
--save-model` trains on every recorded triangle signal (12,358 rows on the
server, growing with the recorder) and writes
`data/dataset/samples/model.npz`; the book loads it via
`--filter-model ... --filter-min 0.124` (the model's top-10% cut -- a
choice the operator can move) and refuses every candidate the model scores
below the floor, logging `filter: the model gives it X%, the floor is Y%`
-- its own branch in the journal and the funnel. A model that will not
load is a refusal to start, never a silent no-op. The book was restarted
2026-09-06 ~18:13 UTC on its existing state (equity $104.40, no reset),
charts on HTF 1h, recorder and council collector running. The measurement
this run exists to produce: the REAL selection's hit rate under the
learned filter, counted by the journal -- no conclusion before it exists.

## 15g. The fav/adv convention, settled with a decisive test

The third audit claimed the sampler's flip rule inverted ~22% of the 10M
synthetic labels. Investigated rather than trusted: a direct
close-containment test over all 1,284,240 recorded bar comparisons --
reconstructing every close from the recorded cls and checking which
interpretation brackets it -- shows **fav/adv are side-relative** (0
violations; the "absolute" reading violates 511,028 bars). The recorder
builds them that way (`fav = (e - low)/e` for a SELL), the sampler's XOR
flip is correct, and the new `tests/test_semantics.py` pins the convention
so it cannot be flipped back.

What the investigation DID surface is the real inversion, one level up:
`sources.path_bars()` ignored the side and always built BUY-oriented bars,
so every SELL label in the flow dataset, the sweep and the selector was
mirrored (a SELL "stop" fired on a 1.25% DOWN move). Fixed: the bars are
oriented by side, and four regression tests reproduce the old inversion.
The corrected measurements:

- selector walk-forward AUC 0.771 (was 0.758 with the mirrored labels);
  top-10% 14.4% against a 4.1% base -- the learned filter still has the
  rank power, still below the 21.9% break-even.
- sweep slices shift modestly (ATR 2.5-4%: 15.5%; gated + agents>=4:
  16.0%); no slice breaks even, the conclusion stands.
- the paper book was retrained and restarted on the corrected model:
  12,421 rows, threshold 0.123 (its top-10% cut), 18:36 UTC.

Minor fixes from the same audit: htf1h's hourly close was the hour's FIRST
15m bar (now the last, per its own comment); the --samples scoring path
imputed missing features with zeros the pretraining never saw (now the
same medians); the ruin simulation paid 4R while the file's own measured
odds are 3.56 (now consistent); the funnel now counts the combo path's
`skip`/`OPEN` lines, which it had been blind to on the source that
actually trades.

## 15h. The eagle's shape eye and the entry measurement

The operator's goal is eight wins a day; the two levers are selection and
entry. Both are now measured on the recorded data and wired:

- **`dataset/patterns.py` -- the pre-trend shape dataset.** 9,135 real bars
  labelled with the candle's anatomy (range vs typical, body, wicks), the
  structure around it (band width, level touches, runs, distance from the
  swings, momentum), the engine's own coiling pressure and shape flags --
  and, strictly from bars AFTER t, the next 1h/2h/4h/8h outcomes (max up,
  max down, first 2.5% direction, whether 5% was reached). The measured
  headline: **an expansion of 2x the coin's own typical candle carries a
  36.6% chance of a 5% move within eight bars, against 4.2% when the band
  is quiet** (base rate 19.0%). This is the shape side of where the eagle
  sits; the indicator's ripeness is the other side.
- **The eagle now ranks on it.** The scout attaches the expansion to every
  ripe row; `perch.ripest()` prefers the expanding coin among equally-ripe
  ones (closeness to the print still outranks expansion). The eagle was
  re-enabled (`stratton-oakmont-perch.timer`) and is flying: first pick MARSCOINUSDT,
  zero modules from a SELL print. Two new tests pin the ordering.
- **`dataset/entries.py` -- which entry survives the 1.25% stop.** Over
  the 10,702 real recorded paths, entering at the candle's extreme takes
  the stop rate from 37.3% to 27.0% and raises the target rate from 4.1%
  to 7.3% (median adverse excursion 0.89% -> 0.59%). No entry style can
  make the stop impossible -- the paths that stop at the extreme genuinely
  travelled 1.25% past it -- but the extreme entry is the measured best.
  The live book still runs `--entry now`; the operator owns that switch.

- **2026-09-06 19:09 UTC** -- on the operator's word, the book's entry
  moved from `now` to `edge`: a resting limit at the signal candle's own
  low (long) / high (short), eight bars to fill or the signal is dropped,
  no market fallback. Measured first, decided second: over the 10,702
  recorded paths the extreme entry stops 27.0% against 37.3% at the open
  and reaches the target 7.3% against 4.1% -- at the cost of the signals
  that never return to the extreme, which are misses, not losses.

- **2026-09-06 19:15 UTC** -- the eagle now picks the chart by a measured
  readiness, not merely by closeness to a print: closeness (30% print rate
  at one module), the tide (8x when it agrees), and the shape (expansion
  2x = 36.6% chance of a 5% move in 8 bars against 4.2% quiet) combine
  into one score, and when nothing is ripe the eagle parks on the
  most-expanding coin the scout walked instead of leaving the chart alone.
  First pick under the new rule: RAYUSDT. Four new tests pin the ordering
  and the fallback; 498 green both sides.

- **2026-09-06 19:26 UTC** -- the paper book now trades as if on the real
  venue: market fills walk the REAL Bitunix order book level by level (a
  thin book fills only part of the size, logged PARTIAL, with the volume-
  weighted average as the fill price), the real minimum order size is
  enforced with the venue's own words, and an unreadable book falls back
  to the half-spread model and says so (`paper_venue.py`). The learned
  filter hot-reloads: the book checks the artifact every minute and picks
  up the nightly rebuild without a restart. 503 tests green both sides.

## 15i. The full-hardening batch — funding, dwell, backups, and the report

The operator took every remaining engineering suggestion. Each is measured
where it can be, tested, and live on the server:

- **Funding accrual.** The paper book now pays/collects the REAL Bitunix
  funding rate per hour, cached 10 minutes, charged while a position is
  open, and folded into the PnL at every close site. Before this a winning
  trade could be reported on the entry price alone, hiding the funding that
  actually settles it.
- **Edge fill-rate (`dataset/entries.py`).** The `--entry edge` resting
  limit fills on about half the signals, usually within two bars; the rest
  are misses, not losses. That number now stands beside the 27% stop rate
  so the entry-mode choice is a rate decision, not a hope.
- **Model-age healthcheck.** `tools/healthcheck.py` fails while the learned
  filter artifact is older than 48 hours, so a silently frozen model is a
  red light instead of a surprise.
- **Scout dwell.** The scout now spends its limited window time by measured
  expansion, not by watchlist order: `scout.walk_order` sorts by expansion
  and `dwell_for` budgets each coin's dwell from it. The fastest-growing
  coins get looked at first and longest.
- **Nightly backup (`tools/backup.py`, `stratton-oakmont-backup.timer`).** One tar of
  the irreplaceable record — signals, outcomes, the book, both journals —
  written atomically, seven kept, 02:17 server time. The first one, fired
  at install, held 8.7 MB. No code can regenerate this data; this is the
  insurance.
- **The day report now reads the journal.** `tools/dayreport.py` adds a
  journal section straight from `data/paper.events.jsonl`: signals seen,
  model passed vs. refused (with the refused probability range), which skip
  branch ate the rest, exits journaled, and model hot-reloads. The model
  lines carry their probability now (`journal.py`), and reloads are
  journaled too.

- **2026-09-06 20:11 UTC** -- everything above deployed with
  `tools/sync.sh --apply`; the backup timer enabled; the book and the scout
  restarted onto the new code. The restart was safe: equity came back
  exactly $104.40 with all four trades, the model loaded (12,421 rows,
  threshold 0.123), healthcheck all clear, and the funnel still reports
  nothing was lost. 516 tests green locally and on the server.

## 15j. The reset, and the two-chart desktop

On the operator's word, the book started over, the phone app was verified
end to end, and the VNC desktop was made to show both charts at once:

- **2026-09-06 20:19 UTC -- reset.** `paper.json`, `paper.events.jsonl`
  and `casestudy.jsonl` were archived to their stamped names, and the book
  was restarted on the documented fresh state: $100.00, zero trades, the
  same learned filter (threshold 0.123) still loaded. The first attempt at
  the archive shell one-liner mangled its heredoc and stopped the book
  mid-reset; the recovery is documented here because it is the trap: do
  these steps with `ssh stratton 'cat > ...'` from a heredoc on the local side,
  never with a heredoc inside a double-quoted ssh command.
- **The app, verified.** `stratton-oakmont-panel` on 443 (cert good to 2026-11-29),
  state API answers the fresh book ($100.00 / 0 closed / running), the
  `/chart/0.jpg` and `/chart/1.jpg` screenshots return two different live
  pictures, and the noVNC desktop page plus the websockify bridge (6080)
  are up. Nothing was broken -- it was verified, not repaired.
- **2026-09-06 20:25 UTC -- the two-chart desktop (`tools/twocharts.py`).**
  Both chart tabs used to live in one Chrome window, so the VNC desktop
  showed only the active tab. The tool closes the scout's tab and reopens
  it from the saved layout URL in its own window, then puts the book's
  window left (0,0) and the scout's right (640,0), 640x780 each, on the
  1280x800 screen. Identity is protected by construction: window 0 is
  always the smallest CDP target id and is never touched, and the tool
  refuses to move anything unless window 0 is on the perched coin -- it
  did refuse once, correctly, while the book was mid-switch to the new
  coin. Idempotent; re-run after any Chrome restart. After the split the
  guard reports all clear, both 15m feeds are live, the scout keeps
  walking, and the book never saw an error.
- **ntfy.** The reset was pushed to all three configured topics (two env
  vars, one list) and confirmed delivered.
- **Backup now covers the archives.** `tools/backup.py` tars the reset
  archives too (`paper-archive-*`, `paper-events-archive-*`,
  `casestudy-*`, `*-before-*`); the post-reset backup holds all 19 of
  them, 8.8 MB.

## 15k. The clean pass, the fresh-eyes audit, and the beast

On the operator's word the whole codebase was cleaned, stressed, audited
by fresh eyes, and a second, aggressive paper book was thrown into the
market beside the first.

- **The clean pass.** ruff auto-fixed 82 real findings (unused imports,
  bundled imports, f-strings with no placeholder) and eleven unused
  locals were removed by hand after checking each right-hand side for
  side effects. pyflakes now reports nothing, mypy is clean on the typed
  support modules, compileall clean. What remains in ruff is this
  project's deliberate style (sys.path-before-imports, dense one-liners).
- **The stress.** The full suite ran five times clean (516 each) before
  the changes and once after (524). Then thirteen critical test files --
  sampler, selector, sweep, filter, journal, paper venue, semantics,
  backup, beast, patterns/entries, riskanalysis, scout, perch -- were
  run 50 times each: 650 runs, zero failures. The same 524 pass on the
  server.
- **The audit.** A fresh-eyes adversarial pass over the whole tree
  (`scratch/audit_report.md`) found nine findings and no criticals. The
  two real code bugs, both now fixed with regression tests:
  - `dataset/entries.py` read `fav` (the down move) for SELL rows in the
    edge-fill question, which is an up move; the answer was mirrored.
    Fixed to read `adv` unconditionally; the headline survives on
    correct data: price returns to the extreme within 8 bars on 49% of
    signals, median 2 bars.
  - The paper simulator mispriced two fills: the edge maker fill was
    charged half the spread (a maker fills AT its limit, never worse)
    and the late-market fallback was granted the last print (a taker
    crosses the book). Both now priced honestly; the pinned tests were
    rewritten to pin the honest semantics instead of the bug.
  The ops gaps: the nightly pipeline evaluated the filter but never
  RETRAINED it -- `stratton-oakmont-dataset.service` now ends with
  `selector.py --save-model`, and the save is atomic (tmp + rename), so
  a book hot-reloading the artifact can never read a half-write; the
  funnel's `since_restart` and the book's exit lock now follow `--book`
  so the beast's funnel mixes nothing and the beast unlocks its own
  lock; the panel's manual coin switch now stands down while the eagle
  owns the charts (it never stuck, and now it says so); the scout's
  wick measure answers the live 1.25% stop rather than a 1.0% straw
  man; a venue-cancelled order is no longer re-cancelled. Verified
  clean and left alone: the 13 learned-filter features match training
  end to end, the path_bars side orientation, the walk-forward split,
  the sampler labels, the money math.
- **The beast (`stratton-beast.service`).** The same engine, the same
  measured edges, the same 5% target and 1.25% stop -- and nothing held
  back: the whole wallet per trade (`--frac 1.0 --max-exposure 1.0`),
  the most leverage the stop allows (`--lev 75` with the liquidation
  ceiling still ruling), no daily budget (`--per-day 0`), no loss limit,
  and the learned filter's floor at 0.090 model probability against the
  book's 0.123 top-10% cut -- so it eats more of the measured tail. It
  has its own ledger (`data/beast.json`), its own lock, its own journal
  (`data/beast.events.jsonl`), its own funnel (`funnel.py --unit
  stratton-beast --book .../beast.json`) and its own daily ntfy report
  (`stratton-oakmont-report.service` second line, titled `Stratton Oakmont beast`). Paper, like
  the book; `--live` cannot appear in its unit and does not. The book's
  own files are untouched by construction, and a test proves two books
  can run side by side on one signal without either ledger seeing the
  other.
- **2026-09-06 21:36 UTC** -- all of it deployed and live: book, beast,
  scout and panel restarted onto the new code; the beast is running
  (`book beast.json`, floor 0.090, already reading the scout's plans);
  healthcheck all clear; both ledgers intact; ntfy pushed to all three
  topics.

- **2026-09-06 21:55 UTC -- the beast at FULL appetite, on the
  operator's word ("no limits, do or die").** Every gate the book keeps
  is dropped on the beast alone: `--min-agents 0 --min-atr 0
  --min-entry 0`, no `--with-trend`, all three colours, `--filter-min 0`
  (model loaded, refuses nothing), no daily budget, no loss limit,
  `--step-at 0` so the size never derisks, and the target is the
  operator's stated number: `--tp 8.8` at the ~57x stop ceiling =
  **502% of margin per win** (`--ct-exit 6.0` scaled with it). The book
  beside it keeps every measured gate, untouched, as the control. A
  test pins the flags: whole wallet, the cap, the 8.8% target, and that
  the run trades with every floor at zero.
- **The 10M sample actually builds now.** The nightly sampler was dying
  on the server at the sampler stage -- 10M x 26 float32 accumulated in
  chunk lists peaks at twice the final gigabyte on a box whose other
  3 GB is Chrome, so `features.npz` was never written and the
  `--samples` evaluation had nothing to read. `dataset/sampler.py` now
  accumulates through disk-backed memmaps (peak RAM is one chunk, not
  the whole sample) and saves streaming; the build ran by hand at
  21:56 and the nightly pipeline produces it from now on.

- **2026-09-06 22:00 UTC -- the watch begins.** The operator turned the
  agent fully on: check the system every half hour, fix anything broken,
  and add any market-derived idea that makes the beast wilder. Round 1:
  all nine services and eight timers up, guard all clear, both funnels
  "nothing was lost", ledgers $100.00 each.
  Two market questions were measured before anything was added:
  - **The expansion idea** (trade bars where expansion >= 2x): at the
    beast's 8.8% target / 1.25% stop the coarse bracket is 11.9% wins
    within 8 bars against a 61.1% stop touch -- the tight stop is the
    wrong tool for expansion volatility. NOT added.
  - **The beast's own setup, walked exactly** over all 10,702 recorded
    paths with the engine's own walk_path (spanning-bar-is-a-stop):
    tp 5 -> 4.1% target / 37.3% stop (-0.26%/trade); tp 8.8 -> 1.5% /
    37.7% (-0.34%/trade); tp 10 -> 1.1% / 37.7% (-0.36%/trade). The
    ungated stream bleeds at every target size -- which is exactly what
    the book's measured gates exist to stop, and exactly the price the
    operator chose to pay for "no limits". The 8.8% target stays: it is
    the operator's stated number, and the experiment prices it.
  What WAS added (appetite, on the operator's standing free hand):
  `--max-exposure 2.0` (two concurrent positions, each the whole
  wallet) and `--counter-frac 1.0` (full-size counters) on the beast;
  live at 22:04, banner reads "up to 200% of equity committed at once".

- **22:10 UTC, watch round 1** -- a lie in the beast's own banner was
  found and fixed: the colour gate was a hardcoded `who != orange`, so
  `--colour purple blue orange` changed nothing but the note. The gate
  is now the flag (`who in want`); the book keeps purple/blue and
  behaves exactly as before (its banner now says what it does:
  "colour purple/blue"), the beast's banner now reads "colour
  purple/blue/orange" and means it. In practice the indicator drops
  orange before printing, so no trade changes on either side -- the
  fix is the honesty of the claim. Two tests pin both directions
  (orange refused by default, accepted when asked). Both books
  restarted clean, ledgers intact, 25 server tests green.

- **22:16 UTC, watch round 2** -- all green again (guard 22:14:27 all
  clear, scout rows zero seconds old, zero errors everywhere). The
  200% exposure claim was pinned for real: a new deterministic test
  breaks two coins at once under the beast's exact flags and holds
  both trades to the target -- two positions, each the whole wallet
  ($100.00 margin each), opened a bar apart, no "exposure full"
  anywhere. 8/8 beast tests green locally and on the server.

- **22:19 UTC, watch round 3** -- all green (guard 22:17:19 all clear,
  zero errors). The journals prove the no-gates path works end to end:
  on the same CASHCATUSDT signal the book refused at the score gate
  (`sig_poor_score`) and the beast walked past it to a mechanical
  no-candle refusal -- gates gone, mechanics intact. One real gap
  closed: the nightly backup never included the beast's ledger.
  `tools/backup.py` now tars `beast.json` + `beast.events.jsonl`; the
  test backup fired at install holds them (9.1 MB, 7 kept). The 02:17
  timer picks this up automatically.

- **22:33 UTC, watch round 5 -- the beast could not trade at all, and
  now it can.** Tracing the beast's `no candle for the signal bar`
  refusals exposed a structural dead end: the scout's combo rows
  carried the print's numbers but NOT the candle it fired on, and
  `--entry edge` needs the candle's extreme to rest at. Every scout
  combo signal was therefore dropped at the no-candle gate -- the
  book's and the beast's edge entries only ever worked on the perched
  coin's own window. Now the scout reads the signal bar's own OHLC off
  the chart when the print lands (`scout.read_combo` attaches `bar`),
  `papertrade.scout_plans` carries it, and the plan's pseudo-chart is
  built with that candle -- so an edge entry rests at the extreme of a
  coin no window is watching. Pinned by a test: a scout signal on
  SCOUTEDUSDT fills at 0.99 (its candle's low) and runs to target,
  with zero windows on the coin. 9/9 beast tests, 81 surrounding
  tests green locally, 37 on the server after deploy; scout, book and
  beast restarted clean.

## 15l. The first night of the beast -- it died, exactly as priced

The night of 2026-09-06/07, watched end to end:

- **The system held.** All seven services up the whole night, guard all
  clear every round, zero error lines in any service journal, all
  timers fired on schedule (02:17 backup with the beast's ledger
  inside, 04:30 daily reports pushed to all three ntfy topics).
- **The book took nothing** -- $100.00, zero trades. The night's two
  fresh combo prints (score 25 and 20, three agents) sat below the
  book's floor of 40, and the book refused them, which is the floor
  doing its measured job.
- **The beast took both and died.** Two concurrent full-wallet
  positions at 23:20 UTC (the 200% exposure working live for the first
  time): BUY NEARUSDT and BUY BTRUSDT, both stopped at -1.25%,
  -$68.50 each -- equity -$37.00 after ten hours. Both prints were the
  exact tier the gates exist to refuse, which is the whole lesson the
  experiment was built to price: the ungated stream measured
  -0.34%/trade before the beast ever traded, and the beast paid it
  with interest. After the death it refused 38 more signals with
  "equity is gone" -- do or die, and it died.
- **08:31 UTC -- the corpse was archived** (`beast-archive-*.json`,
  `beast-events-archive-*.jsonl`, now also inside the nightly backup's
  archive list) and the beast restarted on a fresh $100 with the same
  no-limits flags. The book was never touched.

## 15m. The beast is gone -- rolled back on the operator's word

The operator judged the whole no-limits idea a mistake and asked for the
system back to the settings we fixed together. Done:

- **The beast no longer exists as a running thing.** `stratton-beast.service`
  stopped, disabled and deleted from `/etc/systemd/system` and from
  `services/`; the second report line is off `stratton-oakmont-report.service`;
  `beast.json`/`beast.events.jsonl` are archived with the rest
  (`beast-archive-*` stays inside the nightly backup as history); the
  README no longer lists it; tool help texts no longer name it. Its
  final corpse: two stopped trades, -$137, archived 08:45.
- **The book runs exactly the settings we fixed together** --
  `--source combo --scout --skip-window 1 --entry edge --tp 5
  --lev 40 --frac 0.5 --sl 1.25 --max-exposure 0.5 --min-agents 3
  --min-atr 2.5 --with-trend --per-coin-lev --min-entry 40 --per-day 8
  --day-start 17.5 --fill-bars 8 --ct-exit 3.5 --filter-min 0.123`.
  The book was never changed during the whole beast era, and it is
  still on it now: $100.00, zero trades, filter loaded, all clear.
- **What stays from that era, because it is not the beast:** the
  honesty fixes (maker fills at the limit, takers cross the book), the
  scout's signal-bar candle (without it the book's own edge entries on
  scout signals were dead), the nightly model retrain, the nightly
  backup of the archives, the two-chart VNC desktop, the colour gate
  honouring its flag. The beast-specific tests were renamed to
  `tests/test_second_book.py` -- they pin the engine, not the animal.
  529 tests green.

## 15n. The 50% rule -- the operator's 12-hour test

The operator's new rule for the paper book, live since 09:17 UTC on
2026-09-07:

- **Target: 50% of the margin, fixed, on every trade** (`--tp-margin
  0.50`). The price distance is 50/lev % of price, computed from the
  trade's OWN leverage, so the payment never moves whatever the coin.
- **Stop: the coin's own ATR** (`--sl-atr 1.0`) -- every trade stops at
  its own coin's scale instead of one number for all of them.
- **Leverage: the exchange's cap for that coin**, held inside the
  liquidation line for THAT stop (`--per-coin-lev`, and `sym_lev` now
  caps by the stop actually in force rather than always by `--sl`).
  The three are computed together so the 50% payment stays exact.
- Everything else is the setup we fixed together; `--tp 5 --sl 1.25`
  remain only as the fallback for a coin whose ATR cannot be read.

The machinery this exposed and fixed on the way: the combo loop's local
`_atr` was shadowing the ATR cache dict inside main(), so the first
flag that actually asked for a fresh ATR died with "float has no get"
-- renamed, and pinned by `test_tp_margin_pays_half_the_margin_with_adaptive_stop`
(50/lev % target, stop > 0, leverage inside the line for THAT stop,
pnl = margin*0.5 - fees, margin = half the wallet).

The asymmetry is stated, not hidden: at the stop's own scale a stop
costs roughly half the margin (lev is 1/(sl+maint) by construction)
while a win pays exactly half the margin, so the book needs a high hit
rate -- which is precisely what this easy target is meant to buy, and
what the 12 hours measure. Old flags are one edit away in
`services/stratton-oakmont-paper.service`.

## 15o. The orchestration pass -- the reunion of agents

No setting changed. What changed is how well the pieces agree:

- **The reunion check at the fill.** A combo print is an EVENT, not a
  price. An edge order rests up to eight bars, and in that time the
  three agents can stop agreeing -- the scout rewrites scout.json every
  sweep, so the row carrying the order's signal bar is still there only
  while the print still stands. The book now checks at the moment of
  the fill: if the print has cleared, the order is dropped ("the
  reunion is over") instead of entering a trade the agents have left.
  An unreadable scout file allows the fill -- a sync failure must not
  silently stop trading. Pinned both ways: a cleared print never fills,
  a standing print still does.
- **One readiness formula, two birds.** The perch's readiness score
  (closeness to the print x tide x expansion -- each weight measured)
  moved into `scout.readiness_of`, and the scout's own walk order now
  sorts by the SAME score before falling back to expansion: the ripest
  coin is visited first every sweep, so prints are caught closer to
  firing, and the eyesight and the perch can never disagree about which
  coin matters most.
- The perch is healthy and stable (hours on one coin -- no flip
  storm), the scout keeps its measured dwell, and the chain is as
  synced as the two-chart account limit allows: one coin watched
  continuously by the eagle, the rest ranked by the shared readiness.
  532 tests green locally, 55 on the server after deploy.

- **09:33 UTC, 2026-09-07** -- for the 12-hour throughput test the
  operator turned the daily budget off: `--per-day 0` (the pacing bar
  is gone; every signal that passes the measured gates trades, however
  many that is). Everything else -- the 50% rule, the reunion check,
  the filter -- untouched. The book restarted clean, no errors,
  healthcheck all clear.

- **ntfy, 09:40 then 13:08 UTC** -- first narrowed to the operator's
  own topic (`NTFY_TOPIC_SHARED` blanked, backup kept), then restored
  on the operator's word: all three subscriptions receive everything
  again (verified: own 1 + shared 2 = 3 topics, test push delivered to
  all three; book and guard restarted clean).

- **16:30 UTC -- the final build is live and verified on both
  charts.** After the operator's save (the VNC clipboard had to be
  reset from the server first -- xclip held the old text), both
  windows now run the new compiled build (hash
  `bmI9Ks46_jJEK0gESRibH9BelC31`, identical on both), the tier inputs
  read PEERLESS <= 5 and EXCELLENT <= 10, every scaled input is set
  (320/140/20/40/84/220/12/60), Tesla is wired on both windows, and
  the guard is all clear with zero errors across the books and the
  scout. The 12-13h test runs on the fully corrected indicator from
  this moment.

- **15:58 UTC -- the tier fix: PEERLESS means 5 again.** The
  operator watched the chart and the labels looked loose -- he was
  right about the symptom. The funnel shows NO leak (27,353 sweeps,
  every candidate refused with a named gate), but the scaling had
  over-reached once: the tier boundaries were DERIVED from winGood,
  so the x4 window scaling turned "PEERLESS" into "within 35
  candles" instead of the product's "<= 5". Fixed in
  `pine/Stratton_Oakmont_Sniper.pine`: PEERLESS <= 5 and EXCELLENT <= 10 candles
  as explicit inputs, independent of the 140-candle window. The
  second factor is `confirmOnly = true` on both charts -- prints
  wait for the candle close, which is consistent with the book's
  closed-bar reads but looks quieter than the product default. One
  two-line edit for the operator in the Pine editor, then save.

- **15:46 UTC, 2026-09-07 -- the real 12-13h test begins.** On the
  operator's word: the open LUNA2USDT position was closed by archival
  and BOTH ledgers were reset to a fresh $100.00 / zero trades --
  `paper.json` + `paper-guarded.json` with their journals archived.
  Running now, together: the new indicator build (1h-scaled windows,
  published and verified on both charts), the 50% rule with the
  model's own 0.485 floor, the guarded twin with the 0.75xATR trail,
  no daily budget, the reunion check, the half-hour heartbeat. ntfy
  narrowed again to the operator's single topic (backup kept); the
  first push confirmed 1 topic. Both books active, healthcheck all
  clear.

- **14:30 UTC -- the live charts are synced.** `tools/setpine.py`
  sets every scaled INPUT on both chart windows through CDP and saves
  the layout. Verified by reading the study back on each window:
  Higher timeframe 60, Max bars since pivot 320, Combo gap 140, Bars
  between combos 20, Event memory 40, Fast/Slow lengths 84/220,
  Impulse window 12 -- the chart's own word, not a wish. The remaining
  4h leftovers were swept: every other "4h"/"240" hit in the codebase
  is either a deliberate measurement horizon, a timeout, or the
  documented 4h->1h comparison kept in the dataset meta. The
  HARDCODED constants still wait for the operator's paste of the new
  .pine into the private TradingView script; until then the old build
  runs with the new inputs, which is most of the correction already.

- **14:10 UTC -- the indicator WAS built for 4h; its windows are now
  scaled to the 1h HTF.** The operator's suspicion, confirmed constant
  by constant: every wall-clock window in `pine/Stratton_Oakmont_Sniper.pine` is a
  4h-era bar count that kept its value when `htfTF` moved to "60" --
  so at 1h each window ran 4x shorter than designed. Rescaled x4:
  divergence horizons 25/280 -> 100/1120 (the canonical set 25..280
  becomes 100/140/280/560/1120), max bars since pivot 80 -> 320,
  combo gap 35 -> 140, cooldown 5 -> 20, phase HMA 55 -> 220, event
  memory 10 -> 40, volume window 20 -> 80, zone keep 8 -> 32, trend
  EMAs 21/55 -> 84/220, ADX 14 -> 56, MA defaults 21/55 -> 84/220,
  fib impulse 3 -> 12 candles, max_bars_back 500 -> 1500. Bar-SHAPE
  constants (pivot strengths, volume multiple, zone/touch ATR,
  edge tolerance, tier tightness 5/10/20) are timeframe-agnostic and
  untouched. A test pins every scaled value. NOTE: the live study on
  TradingView is the operator's private script -- it still runs the
  OLD build until the new .pine is pasted in and saved; the charts
  reload it and the guard re-checks it.

- **13:58 UTC -- the choose stage, measured and re-aimed.** The
  operator's instinct was right: the pre-trade selection had a problem
  -- the learned filter was still answering the OLD question (+5%
  before -1.25%) days after the book moved to the 50% rule.
  `dataset/choose.py` measured the choose stage under the LIVE rule:
  the indicator's own score/agents/tier select almost nothing (entry
  score AUC 0.442 -- noise), the OLD model still transferred decently
  (top-10% wins 57.2%, AUC 0.681), and the dominant feature is the
  coin's ATR (+0.525 coefficient; atr>=2 wins 58.6% against 27.7%
  below). The selector now labels rows with the live rule's own walk
  (`dataset/selector.py`, per-signal geometry via
  `dataset/pullback.py`): walk-forward AUC 0.690, top-10% wins 57.6%,
  top-5% 58.5% -- above the ~53% break-even. The live model was
  retrained on the new labels (16,595 rows) and its top-10% cut is
  now 0.485; both books run `--filter-min 0.485` (the same top-10%
  choice as before -- the number is the model's own cut, not a new
  taste). The nightly pipeline retrains with the same labels from now
  on. The honest ceiling: selection lifts the win rate to ~57-58%,
  and the real constraint is supply -- about 9% of signals qualify.

- **13:31 UTC -- the pullback protection, measured then run in
  parallel.** The operator's pain: the trade reached the target's
  doorstep (+1.7% of +2.5% on LUNA2) and the market pulled back to the
  stop. Measured over all 10,702 recorded paths
  (`dataset/pullback.py`, 50%-rule geometry): baseline expectancy
  -0.92% of margin per trade; break-even at +1% and at +1xATR make it
  worse; a 60%-target pullback exit rescues 844 stops but kills too
  many winners (-1.26%); the trailing stop -- after +1xATR the stop
  follows 0.75xATR behind the best, never below entry -- is the ONLY
  protection that beats the baseline (+0.12%). It answers exactly the
  described reversal: 589 recorded stops (5.5%) had been past 70% of
  the target before reversing. Implemented as `--trail-atr` (two tests
  pin it: the reversal closes ABOVE entry with the trail, at the full
  stop without it) and running as a PARALLEL paper book
  (`stratton-oakmont-paper-guarded.service`, own ledger `data/paper-guarded.json`,
  same 50% rule, same gates). The main book is untouched.

- **09:40 UTC** -- the app is synced with the new rule: `panel.py` reports
  `tp_margin` (0.50) and `sl_atr` (1.0) in its state, and the settings
  card now says what is actually running ("the target pays 50% of the
  margin... the stop is 1 x the coin's own ATR; the boxes are the
  fallbacks") instead of the old r-mode text. Panel, book and guard
  restarted clean. The half-hour watch is re-armed for the test.

- **09:44 UTC** -- the half-hour check is now institutional:
  `stratton-oakmont-testwatch.timer` fires every 30 minutes (at :00/:30), runs the
  healthcheck, the funnel and the scoreboard (`tools/testboard.py`),
  and pushes ONE compact line to the operator's single ntfy topic;
  exits nonzero when anything is unhealthy. First run: all clear,
  1 topic, status 0. The scoreboard lives at `tools/testboard.py`
  ("trades N, won/lost, per-hour rate, per-trade lev/stop/target and
  payment, skip histogram") and can be run by hand any time.

---

## 15p. The fee floor — why the scalper lost, measured (2026-09-09)

The VP scalper (`scalper/`, TESLA-free, the operator's system-prompt
strategy) had been recorded as a failure: 130-day farm backtest, 91 trades,
net **-74.34** on a $100 book, profit factor **0.11**, win rate 16.5%,
longest losing streak 16, `ruined: true`, final equity **0.00**. Every
strategy, every entry model, both biases and every volatility bucket was
negative. That pattern — nothing anywhere is positive — is not what a bad
signal looks like. A bad signal loses in *some* buckets. A tax loses in all
of them.

**It was a tax.** Decomposing every trade into its gross move and its
fees (`pnl + entry_fee + exit_fee`, over `risk_amount`):

| strategy | n | net avg R | **gross avg R** | fee cost in R |
|---|---:|---:|---:|---:|
| breakout | 10 | -0.263 | -0.234 | 0.029 |
| turtle | 6 | -1.332 | **+0.987** | 2.319 |
| vp | 64 | -0.596 | **-0.057** | 0.539 |
| **ALL** | **80** | **-0.610** | **-0.001** | **0.609** |

**Gross of fees the farm is dead flat: -0.001 R over 80 trades. Net it is
-0.610 R. The entire loss is transaction costs.** The signal stack is a
coin flip; the venue takes 0.61 R per trade from a 0.00 R edge.

### The arithmetic

A position that risks `d` (stop distance as a fraction of price) of its
notional pays the venue `2*(fee+slip)/1e4` of that notional to open and
close. The venue's share of every R is therefore

```
fee_R = 2 * (fee_bps + slippage_bps) / 1e4 / d
```

At Bitunix taker (6 bps) + 2 bps slippage:

| stop | fee cost |
|---|---|
| 0.05% | 3.20 R |
| **0.152%** (measured median) | **1.05 R** |
| 0.30% | 0.53 R |
| 0.50% | 0.32 R |
| 1.00% | 0.16 R |

The measured median stop was **0.152% of price**, so the median trade handed
**more than its entire risk budget** to the exchange before the market moved.
Median leverage was 50x (the cap), which does not change the ratio — leverage
scales risk and fees together.

### Why it is structural, not a tuning problem

Requiring the venue to take no more than 0.20 R implies a stop of **at least
0.80% of price**. Of the 88 backtest stops, **19 clear that bound and all 19
are `breakout`** — zero `vp`, zero `turtle`. The operator's core VP strategy
never produces a fee-viable stop, because its stop rule (1m swing + 0.2 ATR1m,
capped at `max_sl_atr_mult: 3.0`) with ATR1m around 0.1% of price cannot
reach 0.8%. **The stop rule and the fee floor are mutually exclusive.** A
1-minute scalper on this venue cannot pay for itself; that is a property of
the fee schedule, not of the code.

The ordering confirms it: `breakout` has wide stops → 0.03 R of friction →
avg -0.26 R; `vp` has 0.54 R of friction → -0.60 R; `turtle` has the tightest
sweep stops → 2.32 R of friction → -1.33 R, and is the one strategy that is
*positive* gross (+0.99 R, n=6). Friction ranks the strategies exactly.

### What was shipped

- `execution.round_trip_cost_r(stop_frac, fee_bps, slippage_bps)` and
  `min_viable_stop_frac(...)` — the cost model, with the measurement in the
  docstring.
- `execution.max_fee_r: 0.20` in `config/default.yaml` (validated in
  `config/loader.py`).
- The guard in **both** paths — `engine.py` (rejection reason `fee_gt_edge`)
  and `paper_trader/__init__.py` — so the live book cannot take a setup the
  backtest would refuse.
- `scalper/tests/test_fee_viability.py`, 6 tests pinning the arithmetic, the
  measured median, the maker/taker bound and the shipped config. Suite: 54
  pass.

### What this costs

The guard removes ~78% of the trade population (19 of 88 survive) and every
survivor is a breakout. Trade frequency falls to roughly **0.15/day**. The
+100%/day objective needs frequency; the fee floor forbids it. **The two
cannot both be satisfied on this venue at 1m stop distances.** The honest
routes out are (a) maker-only entries and profit exits — 6 bps taker to 2 bps
maker halves the required stop to 0.40%, (b) wider stops / a higher entry
timeframe, or (c) a venue with a better fee schedule. None of them is a
parameter tweak.

### Two more defects found in the same pass (2026-09-09)

**1. `engine.py` could not complete a backtest at all.** Line 625 tests
`d == SHORT` while the module imported only `LONG` from `structure`, so the
first short setup reaching the stale-signal check raised
`NameError: name 'SHORT' is not defined` and killed the run. The check was
added after the last report was generated (engine.py 20:29 vs report.json
17:50 on 2026-09-08), so **no backtest had ever run against the current
engine** — the -74.34 report describes older code. `paper_trader` imports
`SHORT` correctly, so the live book was never exposed; only the backtest and
research paths were dead. Fixed by importing `SHORT`; pinned by
`test_engine_module_resolves_every_name_it_uses`, which walks
`engine.run.__code__.co_names` and asserts every upper-case global resolves —
the class of bug a branch-coverage gap hides.

**2. Limit entries were charged the taker fee.** The model-priced entries
(OB mean threshold / FVG consequent encroachment / micro-POC) rest as limit
orders and fill at their level, which is a **maker** fill — but the engine
charged them `fee_bps` (6 bps, taker) and the config had no maker rate at
all. That overstated their cost by 4 bps = **0.26 R** at the measured 0.152%
median stop, i.e. the backtest was unfairly pessimistic about exactly the
feature that was added to stop chasing entries. Fixed: `execution.maker_fee_bps`
(2.0) in the config, limit fills charge it, and `round_trip_cost_r` grew
`entry_fee_bps` / `entry_slippage` so the guard prices the *actual* entry
type instead of assuming taker on both legs.

Effect on the fee floor, at the 0.20 R bound:

| entry type | round trip @0.152% stop | min viable stop |
|---|---:|---:|
| taker market | 1.053 R | 0.80% |
| **maker limit** | **0.658 R** | **0.50%** |

Maker entries cut the required stop from 0.80% to 0.50% of price. That is a
real improvement and still not enough: the VP strategy's 1m stops sit around
0.15%, so it remains three times too tight to pay for itself. Frequency and
fee-viability stay in conflict.

### Sizing, measured separately from the fee problem

Bootstrapping the measured R-distribution (n=80, gross of fees, sd 0.609,
losses stop-bounded but 8% run past -1R because friction is charged on top),
200 trades per path, 20k paths:

| risk/trade | ruin | median end equity |
|---|---:|---:|
| **25% (shipped)** | **11.0%** | **x0.129** |
| 10% | 0.0% | x0.678 |
| 5% | 0.0% | x0.898 |
| 2% | 0.0% | x0.978 |

At a **zero** edge, 25% risk per trade still destroys 87% of the book at the
median through volatility drag alone. An earlier Gaussian estimate of this
(sd 1.2) overstated the danger — the real distribution is tighter and
stop-bounded, so 25% is survivable *if* a genuine edge exists (at a
hypothetical +0.20 R the same bootstrap gives 0% ruin and a median of
x2820). The sizing is therefore not the primary fault: **the fees are.**
25% risk is a bet that the edge is real, and the measured edge is 0.00 R.

### The +100%/day target, arithmetically

Compounding +100% a day from $100: $12.8k in a week, $107bn in 30 days,
$1.15e20 in 60. Global M2 is ~$1e14 — the target passes *all money on earth*
in about 40 days. It is not an aggressive goal, it is a self-refuting one.
The defensible reading is the one a human scalper means by it: a 100% *day*
happens; 100% *every* day does not. The book should keep the daily target as
a **halt condition** (stop when the day is banked, which it already does) and
never as a sizing input.
