from faster_whisper import WhisperModel
m = WhisperModel("small", device="cpu", compute_type="int8", cpu_threads=3)
segs, info = m.transcribe("mkp.wav", language="en", vad_filter=True, word_timestamps=False)
with open("mkp.transcript.txt", "w") as f:
    f.write(f"# lang={info.language} dur={info.duration:.1f}s\n")
    for s in segs:
        f.write(f"[{int(s.start//60):02d}:{s.start%60:05.2f} - {int(s.end//60):02d}:{s.end%60:05.2f}] {s.text.strip()}\n")
print("done")
