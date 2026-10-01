"""Build the deliverable transcripts <name>.transcript.txt from the raw whisper outputs.

Each output has:
  1. a header (source video, model, detected language, notes),
  2. ORIGINAL: the verbatim faster-whisper segments with video timestamps,
  3. ENGLISH: the source audio is English, so no translation is needed. This section
     is the same segments with a conservative phrase map applied to fix whisper
     mis-hearings. Fixes marked [cap] were confirmed against the burned-in captions
     (tzwj only). Fixes marked [ctx] are inferred from the trading context (OPINION).

Usage: python3 make_transcripts.py      (run from anywhere; paths are relative to this file)
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))

VIDEOS = {
    "scalp": dict(
        raw="scalp.transcribe.small.txt",
        video="scalp.MP4 (0:37, 1170x2532 HEVC phone screen recording of the YouTube app)",
        note="Language auto-detected en p=0.98. The audio is the soundtrack of the v617 YouTube video "
             "(v617 03:10-03:45), replayed on the owner's phone.",
    ),
    "tzwj": dict(
        raw="tzwj.transcribe.small.txt",
        video="TzwJTfYgK5FAujgWfLNa+fO1C_jia-DY.mp4 (16:16, 1644x1080, iPad MT5 screen recording, captions burned in)",
        note="Auto-detect on the first 30 s said Yoruba p=0.83 (English p=0.06) because of the West-African "
             "accent; that run was aborted (tzwj.transcribe.small.autolang_aborted.txt) and re-run with "
             "language forced to en. The content is English. Burned-in captions were used to confirm fixes.",
    ),
    "v617": dict(
        raw="v617.transcribe.small.txt",
        video="-6170051788186523182.MP4 (13:00, 1280x720, YouTube video by 'MR P FX')",
        note="Language auto-detected en p=0.91 (Yoruba 0.07).",
    ),
}

# (pattern, replacement, tag). Applied in order, case-insensitive.
FIXES = [
    (r"\bZ(?:al|au|ow|OW)\s?USD\b", "XAUUSD", "ctx"),
    (r"\bZOWUSD\b", "XAUUSD", "ctx"),
    (r"\bgood (have|has) been\b", r"gold \1 been", "ctx"),
    (r"\bthat is what good\b", "that is what gold", "ctx"),
    (r"\bset up on good\b", "setup on gold", "ctx"),
    (r"\bXnet\b|\bXNS\b", "Exness", "ctx"),
    (r"\bunlimited level there, gives me unlimited level\b", "unlimited leverage, gives me unlimited leverage", "ctx"),
    (r"\b(?:only|all) meter leverage\b", "unlimited leverage", "ctx"),
    (r"\bone to all limited leverage\b|\b1 to all limited leverage\b|\ball limited leverage\b", "unlimited leverage", "ctx"),
    (r"\bwired me out\b", "wiped me out", "ctx"),
    (r"\bthe exaggerated that changed\b", "the exact strategy that changed", "cap"),
    (r"\bnew boss\b", "new buys", "ctx"),
    (r"I'm gonna end up with a new buys, I can't afford to lose this account",
     "I'm a millionaire in dollars, so um, [I can afford to lose this account]", "cap"),
    (r"\bfuel cell\b", "few sell", "ctx"),
    (r"\bfew cells\b", "few sells", "ctx"),
    (r"\bcells\b", "sells", "ctx"),
    (r"\bcell\b", "sell", "ctx"),
    (r"\btreat\b", "trade", "ctx"),
    (r"\bfreakout\b", "fake out", "ctx"),
    (r"\bturned to an option\b", "turned to an uptrend", "cap"),
    (r"\bwash out\b", "watch out", "cap"),
    (r"\b(the|this|for the|for this) street\b", r"\1 trade", "ctx"),
    (r"\b(the|this) straights\b", r"\1 trades", "ctx"),
    (r"\b(the|this) straight\b(?! to| up| down)", r"\1 trade", "ctx"),
    (r"\bstraight\b(?= though)", "trade", "ctx"),
    (r"\blive (the|this)\b|\bliving (the|this)\b", r"leave \1\2", "ctx"),
    (r"\bshop down\b", "shut down", "ctx"),
    (r"\btraits\b", "trades", "ctx"),
    (r"\bthe chat\b", "the chart", "ctx"),
    (r"\bon the chat\b", "on the chart", "ctx"),
    (r"\bBritain space\b", "breathing space", "ctx"),
    (r"\bpeeps\b", "pips", "ctx"),
    (r"\blot side\b", "lot size", "ctx"),
    (r"\bconnecting candlestick\b", "engulfing(?) candlestick", "ctx"),
    (r"\bmaximum Zona in the spot\b|\bmaximum zone in the sport\b", "maximum zone around the spot", "ctx"),
    (r"\bgo my one meter imprim now\b", "go to my one-minute timeframe now", "ctx"),
    (r"\bstomped into\b|\bstomping into\b", "storming into", "ctx"),
]

SEG = re.compile(r"^\[(\d\d:\d\d\.\d\d) - (\d\d:\d\d\.\d\d)\] (.*)$")


def normalize(text):
    tags = []
    for pat, rep, tag in FIXES:
        new = re.sub(pat, rep, text, flags=re.IGNORECASE)
        if new != text:
            tags.append(tag)
            text = new
    return text, sorted(set(tags))


def build(name, cfg):
    raw_path = os.path.join(HERE, cfg["raw"])
    with open(raw_path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    header = [l for l in lines if l.startswith("#")]
    segs = [SEG.match(l) for l in lines]
    segs = [m for m in segs if m]
    out = []
    out.append(f"# TRANSCRIPT: {name}")
    out.append(f"# video: {cfg['video']}")
    out.append("# engine: faster-whisper 1.2.1, model Systran/faster-whisper-small, int8, CPU, VAD on")
    for h in header:
        out.append("# raw-whisper " + h.lstrip("# "))
    out.append(f"# note: {cfg['note']}")
    out.append("# Timestamps are video time [mm:ss.ss]. Evidence label: MEASURED (machine transcription;")
    out.append("#   whisper errors remain in ORIGINAL). Section ENGLISH = source is English, so the")
    out.append("#   translation is the original, with phrase fixes; [cap] = confirmed by burned-in caption,")
    out.append("#   [ctx] = inferred from trading context (OPINION).")
    out.append("")
    out.append("=" * 100)
    out.append("ORIGINAL (verbatim whisper output, language = English)")
    out.append("=" * 100)
    for m in segs:
        out.append(f"[{m.group(1)} - {m.group(2)}] {m.group(3)}")
    out.append("")
    out.append("=" * 100)
    out.append("ENGLISH (translation not required: source is English; normalized for whisper mis-hearings)")
    out.append("=" * 100)
    n_fixed = 0
    for m in segs:
        txt, tags = normalize(m.group(3))
        n_fixed += bool(tags)
        suffix = f"   {{{','.join(tags)}}}" if tags else ""
        out.append(f"[{m.group(1)} - {m.group(2)}] {txt}{suffix}")
    out.append("")
    out.append(f"# segments={len(segs)} segments_with_fixes={n_fixed}")
    dst = os.path.join(HERE, f"{name}.transcript.txt")
    with open(dst, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    print(dst, len(segs), n_fixed)


if __name__ == "__main__":
    for k, v in VIDEOS.items():
        build(k, v)
