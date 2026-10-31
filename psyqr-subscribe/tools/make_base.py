#!/usr/bin/env python3
"""Build the clean base layer for the Hebrew subscribe animation.

Input : assets/banner-original.png  (the supplied PsyQR Moments banner, 1045x250)
Output: assets/banner-base.png

What it does
  1. Paints the whole original button (red body, play icon and the English
     "SUBSCRIBE" lettering) out of the bitmap, replacing it with the dark plate
     colour. Nothing of the English text survives in any pixel; the button is
     redrawn from scratch as vector art by the player.
  2. Paints the bell icon and its sound-wave arcs out of the round bell frame,
     so the bell can be redrawn as a vector that swings and rings.
  3. Removes a stray clipped glyph sitting outside the artwork at the far left
     (a leftover from background removal).

The avatar, logo, plate, bell frame ring and all surrounding glow are untouched.
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "assets" / "banner-original.png"
DST = ROOT / "assets" / "banner-base.png"

# Geometry measured from the original artwork (banner pixel units).
BUTTON_ERASE = (579, 107, 907, 206)   # outer box incl. the white neon edge
BUTTON_RADIUS = 18
BELL_CENTER = (961.0, 154.0)
BELL_ERASE_R = 51.0                   # inside the thin inner ring of the frame
STRAY_GLYPH = (0, 110, 28, 141)       # x0, y0, x1, y1 (exclusive)

PLATE = np.array([40, 6, 5], dtype=np.float32)


def feathered_mask(size, draw_fn, feather):
    m = Image.new("L", size, 0)
    draw_fn(ImageDraw.Draw(m))
    if feather > 0:
        m = m.filter(ImageFilter.GaussianBlur(feather))
    return np.asarray(m, dtype=np.float32) / 255.0


def main():
    im = Image.open(SRC).convert("RGBA")
    w, h = im.size
    a = np.asarray(im, dtype=np.float32)
    rgb, alpha = a[..., :3], a[..., 3:4]

    # 1. Button -> dark plate.
    # Draw at 4x for a smooth rounded edge, then downsample.
    big = Image.new("L", (w * 4, h * 4), 0)
    x0, y0, x1, y1 = BUTTON_ERASE
    ImageDraw.Draw(big).rounded_rectangle(
        (x0 * 4, y0 * 4, x1 * 4, y1 * 4), radius=BUTTON_RADIUS * 4, fill=255
    )
    m_btn = big.resize((w, h), Image.LANCZOS).filter(ImageFilter.GaussianBlur(1.6))
    m_btn = (np.asarray(m_btn, dtype=np.float32) / 255.0)[..., None]
    rgb = rgb * (1 - m_btn) + PLATE * m_btn
    alpha = alpha * (1 - m_btn) + 255.0 * m_btn

    # 2. Bell icon -> dark radial disc.
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cx, cy = BELL_CENTER
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    m_bell = np.clip((BELL_ERASE_R - d) / 1.6 + 0.5, 0, 1)[..., None]
    t = np.clip(d / BELL_ERASE_R, 0, 1)[..., None]
    inner = np.array([34, 7, 7], dtype=np.float32)
    outer = np.array([14, 5, 5], dtype=np.float32)
    disc = inner * (1 - t) + outer * t
    rgb = rgb * (1 - m_bell) + disc * m_bell
    alpha = alpha * (1 - m_bell) + 255.0 * m_bell

    out = np.concatenate([rgb, alpha], axis=-1)

    # 3. Stray glyph outside the artwork.
    sx0, sy0, sx1, sy1 = STRAY_GLYPH
    out[sy0:sy1, sx0:sx1, 3] = 0

    Image.fromarray(np.clip(out + 0.5, 0, 255).astype(np.uint8), "RGBA").save(
        DST, optimize=True
    )
    print(f"wrote {DST.relative_to(ROOT)}")

    # Dark backdrop used only for the preview video.
    yy, xx = np.mgrid[0:1080, 0:1920].astype(np.float32)
    d = np.sqrt(((xx - 960) / 1150) ** 2 + ((yy - 500) / 700) ** 2)
    t = np.clip(d, 0, 1)[..., None]
    c0, c1 = np.array([42, 20, 24], np.float32), np.array([7, 5, 6], np.float32)
    bg = c0 * (1 - t) + c1 * t
    bg_path = ROOT / "assets" / "preview-bg.png"
    Image.fromarray(bg.astype(np.uint8), "RGB").save(bg_path, optimize=True)
    print(f"wrote {bg_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
