"""Extract keyframes (cheap: no full decode) and name them by video timestamp.

Usage: python3 extract_keyframes.py <video> <outdir> <scale_filter>
Output files: <outdir>/t_<MMmSSs>_<sec>.jpg
"""
import os
import subprocess
import sys

video, outdir, scale = sys.argv[1], sys.argv[2], sys.argv[3]
os.makedirs(outdir, exist_ok=True)
times = subprocess.run(
    ["ffprobe", "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey",
     "-show_entries", "frame=pts_time", "-of", "csv=p=0", "--", video],
    capture_output=True, text=True, check=True).stdout.split()
times = [float(t.strip(",")) for t in times if t.strip(",")]
tmp = os.path.join(outdir, "_tmp")
os.makedirs(tmp, exist_ok=True)
subprocess.run(
    ["ffmpeg", "-v", "error", "-y", "-skip_frame", "nokey", "-i", video,
     "-vsync", "vfr", "-vf", scale, "-q:v", "3", os.path.join(tmp, "%05d.jpg")],
    check=True)
files = sorted(os.listdir(tmp))
print(f"keyframes={len(times)} files={len(files)}")
for f, t in zip(files, times):
    m, s = divmod(int(t), 60)
    os.rename(os.path.join(tmp, f), os.path.join(outdir, f"t_{m:02d}m{s:02d}s_{t:07.2f}.jpg"))
for f in os.listdir(tmp):
    os.remove(os.path.join(tmp, f))
os.rmdir(tmp)
