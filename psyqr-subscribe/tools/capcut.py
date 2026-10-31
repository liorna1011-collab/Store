#!/usr/bin/env python3
"""One green-screen video for CapCut (and other editors that ignore transparency).

CapCut shows the transparent areas of a .mov/.webm as black. Its tool for
overlays is Chroma key, which removes one colour. This builds a single video
that keys perfectly with one click:

  - the frames come from the chroma-key render mode (`render.cjs --key`):
    every effect is drawn solid and the artwork has a hard edge, so there are
    no soft, half-transparent pixels to turn into a muddy fringe;
  - each pixel is either solid (shown at its full colour) or pure green;
    nothing is blended with the green, so keying leaves no coloured edge.

Output: out/psyqr-subscribe-he-capcut.mp4 (3840x2160, 60 fps, with sound).
Rendered in 4K so the colour detail survives MP4's half-resolution colour;
the editor scales it down to the project (e.g. 1080p) after keying.
"""
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
FRAMES = ROOT / "out" / "frames-key"
WAV = ROOT / "out" / "soundtrack.wav"
OUT = ROOT / "out" / "psyqr-subscribe-he-capcut.mp4"
FPS = 60
GREEN = np.array([0, 255, 0], dtype=np.float32)
THRESHOLD = 0.5


def main():
    frames = sorted(FRAMES.glob("*.png"))
    if not frames:
        sys.exit("no frames in out/frames-key – run `node tools/render.cjs --key`")
    W, H = Image.open(frames[0]).size
    enc = subprocess.Popen(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
         "-i", str(WAV), "-map", "0:v", "-map", "1:a",
         "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
         "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
         "-c:v", "libx264", "-preset", "slow", "-crf", "14",
         "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(OUT)],
        stdin=subprocess.PIPE)
    for i, f in enumerate(frames):
        a = np.asarray(Image.open(f).convert("RGBA"), dtype=np.float32)
        solid = a[..., 3:4] >= THRESHOLD * 255
        px = np.where(solid, a[..., :3], GREEN)
        enc.stdin.write(np.clip(px + 0.5, 0, 255).astype(np.uint8).tobytes())
        if i % 60 == 0:
            print(f"frame {i}/{len(frames)}", end="\r", flush=True)
    enc.stdin.close()
    enc.wait()
    print(f"\nwrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
