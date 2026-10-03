"""Fine-tune Kronos on gold M1 or M5 history, for the 30-minute forecast Gold Desk makes on M1 and M5.

Run it on your Mac or VPS (CPU works, Apple GPU / CUDA is faster):

    python3 kronos_finetune.py --csv data/dukascopy_xauusd_m1.csv.gz --tf M1 --size small --epochs 2 \\
        --out ~/.golddesk/kronos_ft_M1
    python3 kronos_finetune.py --csv data/dukascopy_xauusd_m1.csv.gz --tf M5 --out ~/.golddesk/kronos_ft_M5
    python3 kronos_finetune.py --csv ... --tf M1 --check-data        # only count the windows (no torch needed)

The CSV is M1 bars (time, open, high, low, close[, volume]; fetch_history.py writes them); --tf M5 resamples
them to M5. It follows the official Kronos recipe (finetune/train_predictor.py in shiyu-coder/Kronos):

  - windows of lookback + horizon + 1 bars (400 + 30 + 1 by default; the model reads 430 tokens), each normalised
    on its own: (x - mean) / (std + 1e-5), clipped to +/-5, over open, high, low, close, volume, amount
    (amount = close * volume when the CSV has none); time features minute, hour, weekday, day, month (UTC)
  - the tokenizer stays frozen: s1, s2 = tokenizer.encode(x, half=True)
  - teacher forcing on the shifted tokens: logits = model(s1[:, :-1], s2[:, :-1], stamp[:, :-1, :]) and
    loss = model.head.compute_loss(logits[0], logits[1], s1[:, 1:], s2[:, 1:])
  - AdamW (lr 4e-5, betas 0.9 / 0.95, weight decay 0.1), warmup then cosine decay, gradient clip 3.0
  - a chronological split: the last --val-days (or everything from --split on) is validation and no training
    window touches it. Validation loss is printed for the pretrained model and after every epoch; the best
    checkpoint is saved with model.save_pretrained(--out), and only if it beats the pretrained model.

Gold Desk then uses it by itself: if ~/.golddesk/kronos_ft_M1 (or _M5) holds a saved model, the M1 (M5) 30-minute
forecasts use it (GOLDDESK_KRONOS_FT_M1=off goes back to the pretrained one). Test it first on months it never
saw:  python3 kronos_backtest.py --csv ... --tf M1 --horizon 30 --last-days 30 --ft-dir ~/.golddesk/kronos_ft_M1

Plainly: a lower validation loss means the model imitates gold candles better. It does not mean the 30-minute
direction becomes tradable; kronos_backtest.py and the live calibration (kronos_calib.py) measure that, and so
far Kronos has been a coin flip on gold.
"""
from __future__ import annotations

import argparse
import inspect
import json
import math
import os
import random
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from kronos_backtest import SEC, aggregate, load_csv
from kronos_signal import DEFAULT_REPO, FT_META, MODELS, is_ft_dir

CLIP = 5.0


def features(rows: list) -> tuple:
    """rows [t, o, h, l, c, v] -> per bar [open, high, low, close, volume, amount] and [minute, hour, weekday,
    day, month] (UTC, as KronosPredictor computes them from the bar times)."""
    feats, stamps = [], []
    for r in rows:
        t, o, h, l, c = r[0], r[1], r[2], r[3], r[4]
        v = r[5] if len(r) > 5 and r[5] is not None else 0.0
        amt = r[6] if len(r) > 6 and r[6] is not None else c * v
        feats.append([o, h, l, c, v, amt])
        d = datetime.fromtimestamp(t, timezone.utc)
        stamps.append([d.minute, d.hour, d.weekday(), d.day, d.month])
    return feats, stamps


def windows(rows: list, length: int, split_t: int, stride: int, max_gap: int, train_from: int = 0) -> tuple:
    """Start indices of training windows (ending before split_t) and validation windows (starting at or after it).
    A window with a hole longer than max_gap seconds (missing data) is skipped."""
    n = len(rows)
    big = [0] * (n + 1)               # prefix count of big gaps between bar i and i + 1
    for i in range(n - 1):
        big[i + 1] = big[i] + (rows[i + 1][0] - rows[i][0] > max_gap)
    big[n] = big[n - 1] if n else 0
    tr, va = [], []
    for s in range(0, n - length + 1):
        e = s + length - 1
        if big[e] - big[s]:
            continue
        if rows[e][0] < split_t and rows[s][0] >= train_from:
            if s % stride == 0:
                tr.append(s)
        elif rows[s][0] >= split_t:
            va.append(s)
    return tr, va


def pick_device(want: str, torch) -> str:
    if want != "auto":
        return want
    if torch.cuda.is_available():
        return "cuda"
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        return "mps"
    return "cpu"


class KronosAPI:
    """The calls of the official recipe, checked up front with clear errors, adapted to the installed version."""

    def __init__(self, model, tokenizer, torch, teacher_force: bool = False):
        self.model, self.tok, self.torch = model, tokenizer, torch
        if not callable(getattr(tokenizer, "encode", None)):
            raise SystemExit("This Kronos tokenizer has no encode(); update the Kronos code (git pull in ../Kronos).")
        try:
            enc = inspect.signature(tokenizer.encode).parameters
        except (TypeError, ValueError):
            enc = {}
        if enc and "half" not in enc:
            raise SystemExit(f"KronosTokenizer.encode{tuple(enc)} takes no 'half' argument; this script needs the "
                             "Kronos version whose encode(x, half=True) returns the (s1, s2) token pair.")
        try:
            self.fwd = list(inspect.signature(model.forward).parameters)
        except (TypeError, ValueError):
            self.fwd = []
        if self.fwd and len(self.fwd) < 2:
            raise SystemExit(f"Kronos.forward{tuple(self.fwd)} does not take (s1_ids, s2_ids, ...).")
        self.stamp_kw = "stamp" if "stamp" in self.fwd else None
        self.stamp_pos = self.stamp_kw is None and len(self.fwd) >= 3
        self.teacher = bool(teacher_force and "use_teacher_forcing" in self.fwd and "s1_targets" in self.fwd)
        if teacher_force and not self.teacher:
            print("--teacher-force: this Kronos forward() has no use_teacher_forcing / s1_targets; ignored.")
        head = getattr(model, "head", None)
        self.head_loss = callable(getattr(head, "compute_loss", None))
        if not self.head_loss:
            print("model.head.compute_loss not found: using the mean cross-entropy of s1 and s2 tokens instead.")
        if not self.stamp_kw and not self.stamp_pos:
            print("Kronos.forward takes no time stamps in this version: training without them.")

    def encode(self, x):
        out = self.tok.encode(x, half=True)
        if not isinstance(out, (tuple, list)) or len(out) != 2:
            raise SystemExit("tokenizer.encode(x, half=True) did not return the (s1, s2) token pair.")
        return out[0], out[1]

    def loss(self, x, stamp):
        F = self.torch.nn.functional
        with self.torch.no_grad():
            s1, s2 = self.encode(x)
        args, kw = [s1[:, :-1], s2[:, :-1]], {}
        if self.stamp_kw:
            kw[self.stamp_kw] = stamp[:, :-1, :]
        elif self.stamp_pos:
            args.append(stamp[:, :-1, :])
        if self.teacher:
            kw.update(use_teacher_forcing=True, s1_targets=s1[:, 1:])
        logits = self.model(*args, **kw)
        if not isinstance(logits, (tuple, list)) or len(logits) < 2:
            raise SystemExit("Kronos forward() did not return (s1_logits, s2_logits).")
        if self.head_loss:
            out = self.model.head.compute_loss(logits[0], logits[1], s1[:, 1:], s2[:, 1:])
            return out[0] if isinstance(out, (tuple, list)) else out
        l1 = F.cross_entropy(logits[0].reshape(-1, logits[0].size(-1)), s1[:, 1:].reshape(-1))
        l2 = F.cross_entropy(logits[1].reshape(-1, logits[1].size(-1)), s2[:, 1:].reshape(-1))
        return (l1 + l2) / 2


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", required=True, help="M1 gold bars (.csv or .csv.gz)")
    ap.add_argument("--tf", default="M1", choices=["M1", "M5"])
    ap.add_argument("--size", default="small", choices=sorted(MODELS))
    ap.add_argument("--repo", default=str(DEFAULT_REPO), help="folder with the Kronos code (default ../Kronos)")
    ap.add_argument("--base-model", help="start from this model (Hugging Face name or folder) instead of --size")
    ap.add_argument("--tokenizer", help="tokenizer name or folder (default: the one of --size)")
    ap.add_argument("--out", help="where to save (default ~/.golddesk/kronos_ft_<TF>)")
    ap.add_argument("--lookback", type=int, default=400)
    ap.add_argument("--horizon", type=int, default=0, help="bars ahead (default 30 on M1, 6 on M5: 30 minutes)")
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--lr", type=float, default=4e-5)
    ap.add_argument("--weight-decay", type=float, default=0.1)
    ap.add_argument("--warmup", type=float, default=0.03, help="share of the steps spent warming up")
    ap.add_argument("--clip-grad", type=float, default=3.0)
    ap.add_argument("--stride", type=int, default=0, help="a training window every N bars (default 7 on M1, 2 on M5)")
    ap.add_argument("--max-windows", type=int, default=8000, help="training windows per epoch (random each epoch)")
    ap.add_argument("--val-windows", type=int, default=512, help="validation windows, evenly spaced")
    ap.add_argument("--val-days", type=float, default=30, help="the last N days are validation, never trained on")
    ap.add_argument("--split", help="YYYY-MM-DD: validation from this day on (overrides --val-days)")
    ap.add_argument("--train-days", type=float, default=0, help="train only on the N days before the split")
    ap.add_argument("--max-gap-hours", type=float, default=80, help="skip windows with a longer hole in the data")
    ap.add_argument("--device", default="auto", help="auto, cpu, mps, cuda")
    ap.add_argument("--teacher-force", action="store_true",
                    help="condition s2 on the true s1 tokens (the official recipe samples them; off by default)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--check-data", action="store_true", help="load and count the windows, then stop")
    a = ap.parse_args()

    sec = SEC[a.tf]
    a.horizon = a.horizon or 1800 // sec
    a.stride = a.stride or (7 if a.tf == "M1" else 2)
    out = Path(a.out or Path.home() / ".golddesk" / f"kronos_ft_{a.tf}").expanduser()
    length = a.lookback + a.horizon + 1
    ctx = MODELS[a.size][2]
    if length - 1 > ctx:
        raise SystemExit(f"lookback + horizon = {length - 1} is longer than Kronos-{a.size}'s context ({ctx}).")

    m1 = load_csv(a.csv)
    rows = m1 if a.tf == "M1" else aggregate(m1, sec)
    if len(rows) < length * 4:
        raise SystemExit(f"Only {len(rows)} {a.tf} bars in {a.csv}.")
    if a.split:
        split_t = int(datetime.strptime(a.split, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    else:
        split_t = int(rows[-1][0] - a.val_days * 86400)
    train_from = int(split_t - a.train_days * 86400) if a.train_days else 0
    tr, va = windows(rows, length, split_t, a.stride, int(a.max_gap_hours * 3600), train_from)
    if len(va) > a.val_windows:
        va = [va[round(i * (len(va) - 1) / (a.val_windows - 1))] for i in range(a.val_windows)]
    day = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")   # noqa: E731
    print(f"{len(rows)} {a.tf} bars, {day(rows[0][0])} to {day(rows[-1][0])}. Window {length} bars "
          f"({a.lookback} + {a.horizon} + 1). Validation from {day(split_t)}: {len(va)} windows. "
          f"Training before it: {len(tr)} windows (every {a.stride} bars), {min(len(tr), a.max_windows)} per epoch.")
    if not tr or not va:
        raise SystemExit("Need both training and validation windows: check --val-days / --split / --train-days.")
    if a.check_data:
        return

    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")     # ops MPS lacks run on the CPU
    try:
        import torch
    except ImportError:
        raise SystemExit("PyTorch is not installed. Run install_kronos.sh first.")
    repo = Path(a.repo).expanduser()
    if not (repo / "model" / "kronos.py").exists():
        raise SystemExit(f"Kronos code not found in {repo}. Run install_kronos.sh first (or pass --repo).")
    sys.path.insert(0, str(repo))
    try:
        from model import Kronos as KModel, KronosTokenizer
    except ImportError as e:
        raise SystemExit(f"Couldn't import Kronos from {repo}/model: {e}")
    for cls, meth in ((KModel, "from_pretrained"), (KModel, "save_pretrained"), (KronosTokenizer, "from_pretrained")):
        if not callable(getattr(cls, meth, None)):
            raise SystemExit(f"{cls.__name__}.{meth} is missing in this Kronos version (needs huggingface_hub's "
                             "PyTorchModelHubMixin); update the Kronos code.")

    random.seed(a.seed)
    torch.manual_seed(a.seed)
    device = pick_device(a.device, torch)
    mname, tname, _ = MODELS[a.size]
    base, tname = a.base_model or mname, a.tokenizer or tname
    print(f"Loading {base} and {tname} on {device}...")
    tok = KronosTokenizer.from_pretrained(tname).to(device)
    tok.eval()
    for p in tok.parameters():
        p.requires_grad_(False)                       # the tokenizer stays frozen
    model = KModel.from_pretrained(base).to(device)
    api = KronosAPI(model, tok, torch, a.teacher_force)

    feats, stamps = features(rows)
    data = torch.tensor(feats, dtype=torch.float32)
    stamp = torch.tensor(stamps, dtype=torch.float32)
    span = torch.arange(length)

    def batch(starts: list):
        idx = torch.tensor(starts)[:, None] + span[None, :]
        x = data[idx]
        mean = x.mean(dim=1, keepdim=True)
        std = x.std(dim=1, keepdim=True, unbiased=False)
        x = ((x - mean) / (std + 1e-5)).clamp(-CLIP, CLIP)
        return x.to(device), stamp[idx].to(device)

    def evaluate() -> float:
        model.eval()
        state = torch.get_rng_state()
        torch.manual_seed(a.seed)                     # the same draws each time, so the losses compare
        tot = n = 0
        with torch.no_grad():
            for j in range(0, len(va), a.batch):
                ch = va[j:j + a.batch]
                tot += float(api.loss(*batch(ch))) * len(ch)
                n += len(ch)
        torch.set_rng_state(state)
        model.train()
        return tot / max(1, n)

    t0 = time.time()
    base_val = evaluate()
    print(f"Pretrained validation loss: {base_val:.4f}  ({time.time() - t0:.0f} s)")
    per_epoch = min(len(tr), a.max_windows)
    steps_epoch = math.ceil(per_epoch / a.batch)
    total = steps_epoch * a.epochs
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, betas=(0.9, 0.95), weight_decay=a.weight_decay)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=a.lr, total_steps=total, pct_start=a.warmup,
                                                div_factor=10, anneal_strategy="cos")
    best, history = base_val, []
    model.train()
    for ep in range(1, a.epochs + 1):
        order = list(tr)
        random.Random(a.seed + ep).shuffle(order)
        order = order[:per_epoch]
        t_ep, run = time.time(), 0.0
        for k, j in enumerate(range(0, len(order), a.batch), 1):
            loss = api.loss(*batch(order[j:j + a.batch]))
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=a.clip_grad)
            opt.step()
            sched.step()
            run = 0.98 * run + 0.02 * float(loss) if k > 1 else float(loss)
            if k % 20 == 0 or k == steps_epoch:
                el = time.time() - t_ep
                print(f"  epoch {ep} step {k}/{steps_epoch}  loss {run:.4f}  lr {sched.get_last_lr()[0]:.2e}  "
                      f"{el / k:.2f} s/step, ~{el / k * (steps_epoch - k) / 60:.0f} min left", end="\r", flush=True)
        val = evaluate()
        history.append(round(val, 5))
        print(f"\nEpoch {ep}: validation loss {val:.4f} (pretrained {base_val:.4f}, best {best:.4f})")
        if val < best:
            best = val
            tmp = out.with_name(out.name + ".tmp")
            shutil.rmtree(tmp, ignore_errors=True)
            model.save_pretrained(str(tmp))
            if not (tmp / "config.json").exists():      # older huggingface_hub: write the model config ourselves
                cfg = getattr(model, "_hub_mixin_config", None) or getattr(model, "config", None)
                if isinstance(cfg, dict):
                    (tmp / "config.json").write_text(json.dumps(cfg, indent=1))
                else:
                    print(f"\nWarning: {tmp} has no config.json, so Kronos.from_pretrained can't reload it. "
                          "Update huggingface_hub (pip install -U huggingface_hub) and run again.")
            meta ={"tf": a.tf, "size": a.size, "base_model": base, "tokenizer": tname, "lookback": a.lookback,
                    "horizon": a.horizon, "epoch": ep, "val_loss": round(val, 5), "pretrained_val_loss":
                    round(base_val, 5), "val_from": day(split_t), "train_to": day(split_t - 1),
                    "train_from": day(max(train_from, rows[0][0])), "csv": str(a.csv), "lr": a.lr,
                    "teacher_force": api.teacher, "saved": int(time.time())}
            (tmp / FT_META).write_text(json.dumps(meta, indent=1))
            shutil.rmtree(out, ignore_errors=True)
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp.rename(out)
            print(f"Saved the best model so far to {out}")
    if best < base_val:
        days = (rows[-1][0] - split_t) / 86400
        print(f"Done. Fine-tuned validation loss {best:.4f} vs pretrained {base_val:.4f} (lower is better). "
              f"Gold Desk uses {out} for its {a.tf} 30-minute forecast from the next start.")
        print(f"Check it trades no worse on the validation months before relying on it: python3 kronos_backtest.py "
              f"--csv {a.csv} --tf {a.tf} --horizon {a.horizon} --last-days {days:.0f} --ft-dir {out}")
    else:
        print(f"Done. Fine-tuning did not beat the pretrained model on the validation months "
              f"({min(history):.4f} vs {base_val:.4f}); nothing saved.")
        if is_ft_dir(out):
            print(f"An earlier fine-tuned model is still in {out} and Gold Desk uses it; delete that folder to go "
                  "back to the pretrained one.")


if __name__ == "__main__":
    main()
