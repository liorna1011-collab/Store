"""
וידאו בדיקה של שידור תגובה, עם פריסות ידועות מראש (ground truth).

התוכן נוצר כאן (צורות נעות, טקסטורה, "נגן וידאו" עם פסים שחורים),
והפנים מגיעות מתמונת האסטרונאוטית שנכללת ב-scikit-image
(`skimage.data.astronaut`, צילום NASA ברשות הציבור). פנים אמיתיות
נחוצות כי הזיהוי משתמש במסווג Haar – ציור סכמטי לא מפעיל אותו.

פריסות (בשניות):
   0 – 20    reaction  מצלמה בפינה ימנית-תחתונה, תוכן מלא מאחור
  20 – 34    camera    המצלמה ממלאת את הפריים
  34 – 48    screen    תוכן בלבד (נגן עם פסים שחורים), בלי מצלמה
  48 – 66    reaction  מצלמה בפינה שמאלית-עליונה, מסגרת בצבע
  66 – 80    reaction  זה לצד זה: תוכן משמאל, מצלמה בחצי הימני

הרצה:  python3 tests/make_reaction_video.py [out_dir]
פלט:   reaction_stream.mp4 + reaction_stream.truth.json
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

WIDTH, HEIGHT, FPS = 1280, 720, 25
DURATION = 80.0

# (start, end, kind, facecam box normalised or None)
LAYOUTS = [
    (0.0, 20.0, "reaction", (0.735, 0.645, 0.245, 0.33)),
    (20.0, 34.0, "camera", None),
    (34.0, 48.0, "screen", None),
    (48.0, 66.0, "reaction", (0.02, 0.03, 0.27, 0.36)),
    (66.0, 80.0, "reaction", (0.52, 0.0, 0.48, 1.0)),
]


def _face_image() -> np.ndarray:
    from skimage import data

    img = data.astronaut()[:, :, ::-1].copy()        # RGB → BGR
    # חיתוך סביב הפנים וקצת כתפיים
    return img[20:330, 90:400]


def _resize(img: np.ndarray, w: int, h: int) -> np.ndarray:
    import cv2

    return cv2.resize(img, (max(2, w), max(2, h)), interpolation=cv2.INTER_AREA)


_GRIDS: dict[tuple[int, int], tuple[np.ndarray, np.ndarray, np.ndarray]] = {}


def _grid(w: int, h: int):
    key = (w, h)
    if key not in _GRIDS:
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        noise = np.random.default_rng(7).normal(0, 5, (h, w, 1)).astype(np.float32)
        _GRIDS[key] = (yy, xx, noise)
    return _GRIDS[key]


def _content(t: float, w: int, h: int, seed: int = 0) -> np.ndarray:
    """תוכן „משחק": רקע גרדיאנט, צורות נעות וטקסטורה שזזה."""
    yy, xx, noise = _grid(w, h)
    phase = t * 0.7 + seed
    r = 40 + 30 * np.sin(xx / 97.0 + phase)
    g = 60 + 40 * np.sin(yy / 71.0 - phase * 1.3)
    b = 90 + 50 * np.sin((xx + yy) / 150.0 + phase * 0.5)
    img = np.stack([b, g, r], axis=2)
    img += np.roll(noise, int(t * 40) % w, axis=1)
    # צורות נעות
    for k in range(6):
        cx = (0.5 + 0.4 * math.sin(t * (0.6 + k * 0.17) + k)) * w
        cy = (0.5 + 0.35 * math.cos(t * (0.45 + k * 0.11) + 2 * k)) * h
        rad = 20 + 12 * k
        mask = (xx - cx) ** 2 + (yy - cy) ** 2 < rad ** 2
        img[mask] = (60 + 30 * k, 200 - 20 * k, 120 + 18 * k)
    return np.clip(img, 0, 255).astype(np.uint8)


def _player(t: float, w: int, h: int) -> np.ndarray:
    """„נגן וידאו" 16:9 בתוך הפריים, עם פסים שחורים מעל ומתחת."""
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    ph = int(h * 0.74)
    pw = int(ph * 16 / 9)
    if pw > w:
        pw = w
        ph = int(pw * 9 / 16)
    x0, y0 = (w - pw) // 2, (h - ph) // 2
    frame[y0:y0 + ph, x0:x0 + pw] = _content(t, pw, ph, seed=3)
    return frame


def _paste_cam(frame: np.ndarray, face: np.ndarray, box, t: float,
               border: tuple[int, int, int] | None, door: bool = False) -> None:
    """
    מצלמה שממלאת את כל החלון (כמו מצלמת רשת אמיתית): תמונת הפנים
    חתוכה ליחס החלון וזזה מעט. `door` מוסיף קו אנכי קבוע בתוך החדר
    (משקוף), כדי לבדוק שהזיהוי בוחר את גבול החלון ולא קו פנימי.
    """
    H, W = frame.shape[:2]
    x0, y0 = int(box[0] * W), int(box[1] * H)
    bw, bh = int(box[2] * W), int(box[3] * H)
    fh_src, fw_src = face.shape[:2]
    zoom = 1.12
    scale = max(bw / fw_src, bh / fh_src) * zoom
    big = _resize(face, int(fw_src * scale) + 1, int(fh_src * scale) + 1)
    sway = int(6 * math.sin(t * 1.3))
    bob = int(4 * math.sin(t * 0.9))
    ox = max(0, min(big.shape[1] - bw, (big.shape[1] - bw) // 2 + sway))
    oy = max(0, min(big.shape[0] - bh, (big.shape[0] - bh) // 2 + bob))
    cam = big[oy:oy + bh, ox:ox + bw].copy()
    if door:
        dx = int(bw * 0.10)
        cam[:, dx:dx + 4] = (30, 60, 90)
    frame[y0:y0 + bh, x0:x0 + bw] = cam
    if border is not None:
        b = 3
        frame[y0:y0 + b, x0:x0 + bw] = border
        frame[y0 + bh - b:y0 + bh, x0:x0 + bw] = border
        frame[y0:y0 + bh, x0:x0 + b] = border
        frame[y0:y0 + bh, x0 + bw - b:x0 + bw] = border


def render_frame(t: float, face: np.ndarray) -> np.ndarray:
    for start, end, kind, box in LAYOUTS:
        if start <= t < end:
            break
    if kind == "camera":
        frame = np.full((HEIGHT, WIDTH, 3), (48, 44, 40), dtype=np.uint8)
        fh = int(HEIGHT * 0.95)
        fw = int(fh * face.shape[1] / face.shape[0])
        x0 = (WIDTH - fw) // 2 + int(20 * math.sin(t * 0.8))
        frame[(HEIGHT - fh) // 2:(HEIGHT - fh) // 2 + fh, x0:x0 + fw] = _resize(face, fw, fh)
        return frame
    if kind == "screen":
        return _player(t, WIDTH, HEIGHT)
    if start >= 66.0:
        # זה לצד זה: התוכן בחצי השמאלי, מצלמה בימני
        frame = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
        cw = int(box[0] * WIDTH)
        frame[:, :cw] = _content(t, cw, HEIGHT, seed=7)
        _paste_cam(frame, face, box, t, border=None)
        return frame
    frame = _content(t, WIDTH, HEIGHT, seed=1 if start < 20 else 5)
    border = (40, 200, 255) if start >= 48 else (230, 230, 230)
    _paste_cam(frame, face, box, t, border=border, door=start >= 48)
    return frame


def make(out_dir: Path) -> tuple[Path, Path]:
    from polixor.util.ffmpeg import ffmpeg_bin

    out_dir.mkdir(parents=True, exist_ok=True)
    video = out_dir / "reaction_stream.mp4"
    truth = out_dir / "reaction_stream.truth.json"
    face = _face_image()
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{WIDTH}x{HEIGHT}",
           "-r", str(FPS), "-i", "-",
           "-f", "lavfi", "-i", f"sine=frequency=220:duration={DURATION}",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
           "-g", str(FPS * 2), "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "96k", "-shortest", str(video)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    assert proc.stdin is not None
    n = int(DURATION * FPS)
    for i in range(n):
        proc.stdin.write(render_frame(i / FPS, face).tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg failed to encode the reaction video")
    truth.write_text(json.dumps({
        "width": WIDTH, "height": HEIGHT, "duration": DURATION,
        "layouts": [{"start": s, "end": e, "kind": k,
                     "facecam": ({"x": b[0], "y": b[1], "w": b[2], "h": b[3]}
                                 if b else None)}
                    for s, e, k, b in LAYOUTS],
    }, indent=2), encoding="utf-8")
    return video, truth


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "./testdata")
    v, t = make(out)
    print(f"✓ {v}  ({v.stat().st_size / 1e6:.1f} MB)")
    print(f"✓ {t}")
