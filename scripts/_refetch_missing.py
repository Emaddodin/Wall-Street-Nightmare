import sys, concurrent.futures as cf
from datetime import datetime
sys.path.insert(0, "scripts")
import fetch_duka_raw as F
S = "/private/tmp/claude-502/-Users-mac-Desktop-TBT-Engine/d82f36f1-821a-4e2e-923e-f4c66094b06f/scratchpad/miss.txt"
todo = [(datetime.strptime(l.split()[0], "%Y-%m-%d"), int(l.split()[1])) for l in open(S).read().splitlines() if l.strip()]
still = []
for rnd in range(4):
    todo = [t for t in todo if not F.path_for(*t).exists()]
    if not todo:
        break
    with cf.ThreadPoolExecutor(24) as ex:
        res = list(ex.map(F.fetch_one, todo))
    print(f"round {rnd}: {len(todo)} tried, fail={res.count('fail')}", flush=True)
print("remaining missing:", len([t for t in todo if not F.path_for(*t).exists()]))
