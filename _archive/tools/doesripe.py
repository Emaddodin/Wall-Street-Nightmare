"""Does 'one module short' actually reach a print, and is that print better?"""
_BOT = str(__import__("pathlib").Path(__file__).resolve().parent.parent)
import json
import sys
sys.path.insert(0, _BOT)
from papertrade import unpack_votes

SRC = _BOT + "/data/council_history.jsonl"
NEED, AHEAD = 3, 8          # the indicator's threshold, and two hours to fire
books = [json.loads(l) for l in open(SRC)]


def ripe_of(r, need=NEED):
    b, sv = r.get("snipB"), r.get("snipS")
    if b is None or sv is None:
        return None
    b, sv = int(b), int(sv)
    side = 1 if b > sv else -1 if sv > b else 0
    if not side:
        return None
    vote = max(b, sv)
    short = max(0, need - vote)
    tide = int(r.get("htf") or 0)
    lean = sum(1 for x in unpack_votes(r.get("members") and 0 or 0).values()
               if False)          # members already decoded below
    mem = r.get("members") or {}
    lean = sum(1 for x in mem.values() if x == side)
    near = 1.0 if short == 1 else 0.6 if short == 2 else 0.3
    ripe = near * 45 + (25 if tide == side else 0) + min(1.0, lean / 4.0) * 30
    return dict(side=side, vote=vote, short=short, tide=tide, lean=lean,
                ripe=ripe)


rows, printed_total, n = [], 0, 0
for b in books:
    rs = b["rows"]
    for i, r in enumerate(rs):
        n += 1
        fired = bool(r.get("snipBull")) or bool(r.get("snipBear"))
        printed_total += int(fired)
        g = ripe_of(r)
        if not g:
            continue
        # did the indicator print, our way, within the next AHEAD bars?
        hit = False
        for k in range(i + 1, min(i + 1 + AHEAD, len(rs))):
            q = rs[k]
            if (g["side"] == 1 and q.get("snipBull")) or \
               (g["side"] == -1 and q.get("snipBear")):
                hit = True
                break
        rows.append((g["ripe"], g["short"], g["tide"] == g["side"],
                     g["lean"], hit))

base = printed_total / max(n, 1) * 100
print(f"  {n} bars across {len(books)} coins")
print(f"  a signal prints on {base:.2f}% of bars; over eight bars that is "
      f"about {min(100, base*8):.1f}% by chance\n")

print(f"  {'reading':<22}{'bars':>9}{'printed within 2h':>20}{'lift':>8}")
chance = min(100.0, base * AHEAD)
for lo, hi, nm in ((0, 50, "under 50"), (50, 70, "50-70"),
                   (70, 85, "70-85"), (85, 101, "85-100")):
    sel = [r for r in rows if lo <= r[0] < hi]
    if len(sel) < 25:
        continue
    hit = sum(1 for r in sel if r[4]) / len(sel) * 100
    print(f"  {nm:<22}{len(sel):>9}{hit:>19.1f}%{hit/max(chance,1e-9):>7.2f}x")

print(f"\n  {'how many short':<22}{'bars':>9}{'printed within 2h':>20}")
for sh in (0, 1, 2, 3):
    sel = [r for r in rows if r[1] == sh]
    if len(sel) < 25:
        continue
    hit = sum(1 for r in sel if r[4]) / len(sel) * 100
    print(f"  {f'{sh} short':<22}{len(sel):>9}{hit:>19.1f}%")

print(f"\n  {'one short, and...':<22}{'bars':>9}{'printed within 2h':>20}")
for nm, fn in (("tide with it", lambda r: r[1] == 1 and r[2]),
               ("tide against", lambda r: r[1] == 1 and not r[2]),
               ("4+ council leaning", lambda r: r[1] == 1 and r[3] >= 4)):
    sel = [r for r in rows if fn(r)]
    if len(sel) < 25:
        continue
    hit = sum(1 for r in sel if r[4]) / len(sel) * 100
    print(f"  {nm:<22}{len(sel):>9}{hit:>19.1f}%")
