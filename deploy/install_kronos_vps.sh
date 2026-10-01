#!/usr/bin/env bash
# Install Kronos (github.com/shiyu-coder/Kronos, MIT) on the VPS next to the running bots.
# Deletes nothing and touches no service: everything goes in /opt/kronos with its own Python venv
# and CPU-only PyTorch (the default Linux PyTorch pulls ~3 GB of CUDA libraries this box can't use).
#
# From the Mac:   ssh stratton 'bash -s' < deploy/install_kronos_vps.sh
# Pick a size:    ssh stratton 'KRONOS_SIZE=small bash -s' < deploy/install_kronos_vps.sh
set -euo pipefail

DIR=/opt/kronos
SIZE=${KRONOS_SIZE:-base}            # base = biggest released Kronos (102M); small = 25M; mini = 4M

avail_mb=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)
disk_mb=$(df -Pm / | awk 'NR==2 {print $4}')
echo "== VPS: $(nproc) CPUs, ${avail_mb} MB RAM available, ${disk_mb} MB disk free"
if [ "$disk_mb" -lt 2500 ]; then
  echo "Not enough disk (need about 2.5 GB). Nothing was changed."; exit 1
fi
if [ "$SIZE" = base ] && [ "$avail_mb" -lt 1300 ]; then
  echo "Only ${avail_mb} MB RAM free: Kronos-base could starve the bots. Using Kronos-small instead."
  SIZE=small
fi

need=()
command -v git >/dev/null || need+=(git)
python3 -c "import venv, ensurepip" 2>/dev/null || need+=(python3-venv)
if [ ${#need[@]} -gt 0 ]; then
  echo "== Installing ${need[*]}"
  DEBIAN_FRONTEND=noninteractive apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y -q "${need[@]}"
fi

mkdir -p "$DIR"
[ -d "$DIR/Kronos/.git" ] || git clone -q --depth 1 https://github.com/shiyu-coder/Kronos.git "$DIR/Kronos"
[ -x "$DIR/venv/bin/python" ] || python3 -m venv "$DIR/venv"
echo "== Installing CPU PyTorch and Kronos's libraries into $DIR/venv"
"$DIR/venv/bin/pip" install -q --upgrade pip
"$DIR/venv/bin/pip" install -q torch --index-url https://download.pytorch.org/whl/cpu
"$DIR/venv/bin/pip" install -q "einops==0.8.1" huggingface_hub safetensors pandas tqdm

echo "== Downloading Kronos-$SIZE and forecasting the next 15 minutes of XAUUSD (low priority, the bots come first)"
export HF_HOME="$DIR/hf" KRONOS_SIZE="$SIZE"
nice -n 15 "$DIR/venv/bin/python" - <<'PY'
import json, os, resource, sys, time, urllib.request
sys.path.insert(0, "/opt/kronos/Kronos")
import pandas as pd, torch
from model import Kronos, KronosPredictor, KronosTokenizer

size = os.environ["KRONOS_SIZE"]
names = {"base": ("NeoQuasar/Kronos-base", "NeoQuasar/Kronos-Tokenizer-base", 512),
         "small": ("NeoQuasar/Kronos-small", "NeoQuasar/Kronos-Tokenizer-base", 512),
         "mini": ("NeoQuasar/Kronos-mini", "NeoQuasar/Kronos-Tokenizer-2k", 2048)}[size]
torch.set_num_threads(1)                        # one core for Kronos, one left for the bots
now = int(time.time())
url = f"https://my.litefinance.org/chart/get-history?symbol=XAUUSD&resolution=1&from={now - 3 * 86400}&to={now}"
d = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=20).read())
d = d.get("data", d)
n = len(d["t"]) - 1                             # drop the still-forming bar
rows = list(zip(d["t"], d["o"], d["h"], d["l"], d["c"], d["v"]))[:n][-400:]
df = pd.DataFrame([r[1:] for r in rows], columns=["open", "high", "low", "close", "volume"])
xt = pd.Series(pd.to_datetime([r[0] for r in rows], unit="s"))
yt = pd.Series(pd.to_datetime([rows[-1][0] + 60 * (i + 1) for i in range(15)], unit="s"))

t0 = time.time()
pred = KronosPredictor(Kronos.from_pretrained(names[0]), KronosTokenizer.from_pretrained(names[1]),
                       device="cpu", max_context=names[2])
load = time.time() - t0
t0 = time.time()
out = pred.predict(df=df, x_timestamp=xt, y_timestamp=yt, pred_len=15, T=1.0, top_p=0.9, sample_count=5, verbose=False)
fc = time.time() - t0
last, target = rows[-1][4], float(out["close"].iloc[-1])
peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
print(f"Kronos-{size} works. Last close {last:.2f} at {pd.to_datetime(rows[-1][0], unit='s')} UTC; "
      f"15-minute forecast {target:.2f} ({target - last:+.2f}).")
print(f"Model load {load:.1f} s, one forecast {fc:.1f} s, peak RAM {peak:.0f} MB.")
PY
echo "== Done. Kronos is in $DIR. Nothing else on the VPS was changed."
