#!/usr/bin/env python3
"""Make the CapCut versions of the animation.

CapCut ignores the alpha channel of video files (transparent areas show as
black), so a transparent .mov/.webm cannot be used there. This splits every
rendered RGBA frame into two videos that rebuild the transparency inside
CapCut with its own tools:

  out/capcut/1-animation-greenscreen.mp4
      The solid parts of the animation (alpha >= 0.9) at full colour on pure
      green. Nothing is blended with the green, so CapCut's Chroma Key removes
      it without a coloured fringe. Carries the soundtrack.

  out/capcut/2-glow-screen.mp4
      Only the light: the soft glows, aura, light streak, sparks and embers
      (alpha < 0.9), premultiplied onto black. With CapCut's "Screen" blend
      mode the black disappears and only the light is added to the picture.

The two are exact complements: every pixel belongs to one or the other.

Needs out/frames/*.png and out/soundtrack.wav from `node tools/render.cjs`.
"""
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
FRAMES = ROOT / "out" / "frames"
WAV = ROOT / "out" / "soundtrack.wav"
OUT = ROOT / "out" / "capcut"
FPS = 60
W, H = 1920, 1080
GREEN = np.array([0, 255, 0], dtype=np.float32)
THRESHOLD = 0.9

TAGS = ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]


def encoder(path, with_audio):
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-"]
    if with_audio:
        cmd += ["-i", str(WAV), "-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "192k", "-shortest"]
    cmd += ["-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p", *TAGS,
            "-c:v", "libx264", "-preset", "slow", "-crf", "14", "-movflags", "+faststart", str(path)]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE)


def main():
    frames = sorted(FRAMES.glob("*.png"))
    if not frames:
        sys.exit("no frames in out/frames – run `node tools/render.cjs` first")
    OUT.mkdir(parents=True, exist_ok=True)
    core = encoder(OUT / "1-animation-greenscreen.mp4", with_audio=True)
    glow = encoder(OUT / "2-glow-screen.mp4", with_audio=False)
    for i, f in enumerate(frames):
        a = np.asarray(Image.open(f).convert("RGBA"), dtype=np.float32)
        rgb, alpha = a[..., :3], a[..., 3:4] / 255.0
        solid = alpha >= THRESHOLD
        core_px = np.where(solid, rgb, GREEN)
        glow_px = np.where(solid, 0.0, rgb * alpha)
        core.stdin.write(np.clip(core_px + 0.5, 0, 255).astype(np.uint8).tobytes())
        glow.stdin.write(np.clip(glow_px + 0.5, 0, 255).astype(np.uint8).tobytes())
        if i % 60 == 0:
            print(f"frame {i}/{len(frames)}", end="\r", flush=True)
    for p in (core, glow):
        p.stdin.close()
        p.wait()
    print(f"\nwrote {OUT.relative_to(ROOT)}/1-animation-greenscreen.mp4 and 2-glow-screen.mp4")


if __name__ == "__main__":
    main()
