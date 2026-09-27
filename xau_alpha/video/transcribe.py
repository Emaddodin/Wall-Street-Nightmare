"""Transcribe video audio tracks with faster-whisper (CPU, int8, 2 threads).

Usage: python3 transcribe.py <wav> <out_prefix> [model] [task] [lang]
  model: small (default) | large-v3
  task : transcribe (default) | translate
  lang : force language code (e.g. fa, en); default auto-detect
Writes <out_prefix>.<task>.<model>.txt with [start-end] segments.
"""
import os
import sys
import time
from faster_whisper import WhisperModel

wav, out_prefix = sys.argv[1], sys.argv[2]
model_name = sys.argv[3] if len(sys.argv) > 3 else "small"
task = sys.argv[4] if len(sys.argv) > 4 else "transcribe"
lang = sys.argv[5] if len(sys.argv) > 5 and sys.argv[5] != "auto" else None


def fmt(t):
    m, s = divmod(t, 60)
    return f"{int(m):02d}:{s:05.2f}"


t0 = time.time()
model = WhisperModel(model_name, device="cpu", compute_type="int8", cpu_threads=int(os.environ.get("WT", "2")))
segments, info = model.transcribe(
    wav,
    task=task,
    language=lang,
    beam_size=int(os.environ.get("WB", "3")),
    vad_filter=True,
    vad_parameters={"min_silence_duration_ms": 700},
    condition_on_previous_text=False,
)
out = f"{out_prefix}.{task}.{model_name}.txt"
with open(out, "w", encoding="utf-8") as fh:
    fh.write(
        f"# file={wav} model={model_name} task={task} lang={info.language} "
        f"p={info.language_probability:.2f} duration={info.duration:.1f}s\n"
    )
    if info.all_language_probs:
        top = sorted(info.all_language_probs, key=lambda x: -x[1])[:5]
        fh.write("# top_langs=" + ", ".join(f"{k}:{v:.2f}" for k, v in top) + "\n")
    for seg in segments:
        line = f"[{fmt(seg.start)} - {fmt(seg.end)}] {seg.text.strip()}"
        fh.write(line + "\n")
        fh.flush()
    fh.write(f"# elapsed={time.time()-t0:.0f}s\n")
print(out, info.language, round(info.language_probability, 2), f"{time.time()-t0:.0f}s")
