#!/usr/bin/env python3
"""Build the clean bitmap layers for the Hebrew subscribe animation.

Input : assets/banner-original.png  (the supplied PsyQR Moments banner, 1045x250)
Output: assets/banner-base.png      full clean banner (reference)
        assets/banner-avatar.png    avatar ring + crown, animated on its own
        assets/banner-rest.png      plate, logo, bell frame (everything else)
        assets/preview-bg.png       dark backdrop for the preview video

Steps
  1. Removes the grey checkerboard baked into the outer glow. The grey ring is
     found by flood-filling desaturated pixels inward from the transparent
     outside, so grey tones inside the face, hair and lettering are never
     touched. In that ring, the grey component is subtracted and only the red
     glow is kept, faded smoothly away from the artwork.
  2. Paints the original button, including its English lettering, out of the
     bitmap. The player redraws it as vector art.
  3. Paints the bell icon and its wave arcs out of the bell frame.
  4. Removes stray specks outside the artwork, and fills pinholes inside it:
     partly transparent pixels that are enclosed by the artwork (bits of the
     hair and beard edge, the dark outline of the "PsyQR" lettering) become
     solid, darkened toward the plate colour. They are background-removal
     defects; left open, the video behind shows through them, and a chroma
     key turns them into visible specks.
  5. Splits the result into the avatar layer and the rest. The two layers are
     complementary (alpha * m and alpha * (1 - m)), so adding them back
     together reproduces the banner exactly.
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
A = ROOT / "assets"

# Geometry measured from the original artwork (banner pixel units).
BUTTON_ERASE = (579, 107, 907, 206)
BUTTON_RADIUS = 18
BELL_CENTER = (961.0, 154.0)
BELL_ERASE_R = 51.0
AVATAR = dict(cx=179.3, cy=133.3, rx=108.5, ry=98.5, max_x=280.0)  # ring + its glow
CROWN_BOX = (72, 16, 128, 76)
STRAYS = [(0, 110, 28, 141)]            # clipped glyph at the far left
SPECKS = [(984, 85, 998, 98)]           # bright speck outside the bell frame
PLATE = np.array([40, 6, 5], dtype=np.float32)
GLOW_RGB = np.array([255, 26, 22], dtype=np.float32)


def to_img(mask):
    return Image.fromarray((np.clip(mask, 0, 1) * 255).astype(np.uint8))


def dilate(mask, px):
    im = to_img(mask.astype(np.float32))
    for _ in range(px):
        im = im.filter(ImageFilter.MaxFilter(3))
    return np.asarray(im) > 127


def blur(arr, radius):
    return np.asarray(to_img(arr).filter(ImageFilter.GaussianBlur(radius)), dtype=np.float32) / 255.0


def clean_halo(a):
    """Replace the checkerboard-contaminated outer glow with a pure red glow."""
    h, w = a.shape[:2]
    r, g, b, al = a[..., 0], a[..., 1], a[..., 2], a[..., 3]
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    sat = (mx - mn) / (mx + 1e-3)
    greyish = (mn > 22) & (mx < 225) & (sat < 0.66)

    # Flood from the transparent outside through grey / transparent pixels only.
    passable = to_img((greyish | (al < 12)).astype(np.float32))
    padded = Image.new("L", (w + 2, h + 2), 255)
    padded.paste(passable, (1, 1))
    ImageDraw.floodfill(padded, (0, 0), 128)
    reached = np.asarray(padded)[1:-1, 1:-1] == 128
    halo = reached & greyish & (al >= 12)
    for x0, y0, x1, y1 in SPECKS:
        halo[y0:y1, x0:x1] = True

    region = halo | (dilate(halo, 3) & (al < 250))       # include the soft outer fringe
    core = (al > 200) & ~region
    falloff = np.clip(blur(core.astype(np.float32), 7) * 2.6, 0, 1)
    red_excess = np.clip(r - np.maximum(g, b), 0, 255) / 255.0   # the glow, minus the grey
    glow_a = np.where(region, np.clip(red_excess * 1.7, 0, 1) * (al / 255.0) * falloff, 0)
    glow_a = blur(glow_a, 1.6)
    soft = blur(region.astype(np.float32), 1.2)

    new_a = al / 255.0 * (1 - soft) + glow_a * soft
    share = (soft * glow_a / np.maximum(new_a, 1e-4))[..., None]
    out = a.copy()
    out[..., :3] = a[..., :3] * (1 - share) + GLOW_RGB * share
    out[..., 3] = np.clip(new_a * 255, 0, 255)
    return out


def erase_button_and_bell(a):
    h, w = a.shape[:2]
    rgb, alpha = a[..., :3], a[..., 3:4]
    big = Image.new("L", (w * 4, h * 4), 0)
    x0, y0, x1, y1 = BUTTON_ERASE
    ImageDraw.Draw(big).rounded_rectangle((x0 * 4, y0 * 4, x1 * 4, y1 * 4), radius=BUTTON_RADIUS * 4, fill=255)
    m = big.resize((w, h), Image.LANCZOS).filter(ImageFilter.GaussianBlur(1.6))
    m = (np.asarray(m, dtype=np.float32) / 255.0)[..., None]
    rgb = rgb * (1 - m) + PLATE * m
    alpha = alpha * (1 - m) + 255.0 * m

    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cx, cy = BELL_CENTER
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    mb = np.clip((BELL_ERASE_R - d) / 1.6 + 0.5, 0, 1)[..., None]
    t = np.clip(d / BELL_ERASE_R, 0, 1)[..., None]
    disc = np.array([34, 7, 7], np.float32) * (1 - t) + np.array([14, 5, 5], np.float32) * t
    rgb = rgb * (1 - mb) + disc * mb
    alpha = alpha * (1 - mb) + 255.0 * mb
    return np.concatenate([rgb, alpha], axis=-1)


def fill_pinholes(a):
    al = a[..., 3]
    low = al < 230
    padded = Image.new("L", (a.shape[1] + 2, a.shape[0] + 2), 255)
    padded.paste(to_img(low.astype(np.float32)), (1, 1))
    ImageDraw.floodfill(padded, (0, 0), 128)           # low-alpha pixels reachable from outside
    holes = low & ~(np.asarray(padded)[1:-1, 1:-1] == 128)
    k = (al / 255.0)[..., None]
    filled = a[..., :3] * k + np.array([14, 8, 8], np.float32) * (1 - k)
    out = a.copy()
    out[..., :3] = np.where(holes[..., None], filled, a[..., :3])
    out[..., 3] = np.where(holes, 255.0, al)
    print(f"filled {int(holes.sum())} enclosed pinhole pixels")
    return out


def avatar_mask(a):
    h, w = a.shape[:2]
    s = 4
    big = Image.new("L", (w * s, h * s), 0)
    d = ImageDraw.Draw(big)
    c = AVATAR
    d.ellipse(((c["cx"] - c["rx"]) * s, (c["cy"] - c["ry"]) * s, (c["cx"] + c["rx"]) * s, (c["cy"] + c["ry"]) * s), fill=255)
    d.rectangle((c["max_x"] * s, 0, w * s, h * s), fill=0)
    m = np.asarray(big.resize((w, h), Image.LANCZOS), dtype=np.float32) / 255.0
    # the crown, cut by its own shape (its white strokes, grown a few pixels)
    x0, y0, x1, y1 = CROWN_BOX
    crown = np.zeros((h, w), bool)
    sub = a[y0:y1, x0:x1]
    crown[y0:y1, x0:x1] = (sub[..., :3].min(-1) > 150) & (sub[..., 3] > 128)
    m = np.maximum(m, dilate(crown, 4).astype(np.float32))
    return blur(m, 2.0)


def save(arr, name):
    Image.fromarray(np.clip(arr + 0.5, 0, 255).astype(np.uint8), "RGBA").save(A / name, optimize=True)
    print(f"wrote assets/{name}")


def main():
    a = np.asarray(Image.open(A / "banner-original.png").convert("RGBA"), dtype=np.float32)
    a = clean_halo(a)
    a = erase_button_and_bell(a)
    for x0, y0, x1, y1 in STRAYS:
        a[y0:y1, x0:x1, 3] = 0
    a = fill_pinholes(a)
    save(a, "banner-base.png")

    m = avatar_mask(a)
    av, rest = a.copy(), a.copy()
    av[..., 3] = a[..., 3] * m
    rest[..., 3] = a[..., 3] * (1 - m)
    save(av, "banner-avatar.png")
    save(rest, "banner-rest.png")

    # Dark backdrop used only for the preview video.
    yy, xx = np.mgrid[0:1080, 0:1920].astype(np.float32)
    d = np.sqrt(((xx - 960) / 1150) ** 2 + ((yy - 500) / 700) ** 2)
    t = np.clip(d, 0, 1)[..., None]
    bg = np.array([42, 20, 24], np.float32) * (1 - t) + np.array([7, 5, 6], np.float32) * t
    Image.fromarray(bg.astype(np.uint8), "RGB").save(A / "preview-bg.png", optimize=True)
    print("wrote assets/preview-bg.png")


if __name__ == "__main__":
    main()
