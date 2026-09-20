"""Do movement and clean levels fight each other, or was that a story?"""
import json
_BOT = str(__import__("pathlib").Path(__file__).resolve().parent.parent)

m = json.load(open(_BOT + "/data/watch_measures.json"))
rows = [(s, d.get("reach", 0.0), d.get("smooth", 0.0),
         d.get("shapes", 0.0), d.get("force", 0.0))
        for s, d in m.items()]
rows = [r for r in rows if r[1] is not None and r[3] is not None]
n = len(rows)


def corr(a, b):
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va = sum((x - ma) ** 2 for x in a) ** 0.5
    vb = sum((x - mb) ** 2 for x in b) ** 0.5
    if va == 0 or vb == 0:
        return 0.0
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (va * vb)


reach = [r[1] for r in rows]
smooth = [r[2] for r in rows]
shapes = [r[3] for r in rows]
force = [r[4] for r in rows]

print(f"  {n} coins measured\n")
print(f"  {'':<34}{'correlation':>12}")
for nm, a, b in (("movement vs how many shapes", reach, shapes),
                 ("movement vs how hard they break", reach, force),
                 ("movement vs how straight it goes", reach, smooth),
                 ("shapes vs how hard they break", shapes, force),
                 ("straightness vs shapes", smooth, shapes)):
    print(f"  {nm:<34}{corr(a, b):>+12.3f}")

print("\n  the coins that do both, if any exist:")
print(f"  {'coin':<14}{'covers 10%':>12}{'shapes/day':>12}{'force':>7}")
both = [r for r in rows if r[1] >= 25 and r[3] >= 1.5]
both.sort(key=lambda r: -(r[1] * r[3]))
for s, rc, sm, sh, fo in both[:12]:
    print(f"  {s:<14}{rc:>11.1f}%{sh:>12.2f}{fo:>7.1f}")
print(f"  ({len(both)} coins clear both bars)")

print("\n  and by band, to see the shape of it:")
print(f"  {'movement':<16}{'coins':>7}{'shapes/day':>13}{'force':>8}")
for lo, hi in ((0, 10), (10, 20), (20, 30), (30, 45), (45, 200)):
    sel = [r for r in rows if lo <= r[1] < hi]
    if len(sel) < 5:
        continue
    print(f"  {f'{lo}-{hi if hi<200 else 100}%':<16}{len(sel):>7}"
          f"{sum(r[3] for r in sel)/len(sel):>13.2f}"
          f"{sum(r[4] for r in sel)/len(sel):>8.2f}")
