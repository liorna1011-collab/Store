"""
מייצר וידאו בדיקה סינתטי שנראה כמו שידור גיימינג, עם אירועים
במיקומים ידועים מראש.

כל התוכן נוצר כאן מאפס – צורות, גרדיאנטים, רעש וגלי סינוס – ולכן אין
בו שום חומר מוגן בזכויות יוצרים. זהו קלט הבדיקה של הפייפליין, והוא
בנוי כך שהעריכה תהיה מורגשת לעין: תנועה רציפה, HUD שמשתנה, ושינויי
סצנה חדים.

אירועים מתוכננים (בשניות):
  22-27   פרץ עוצמה גדול  – "צעקה/התרגשות", ההרג בנקודת השיא
  38-43   שקט מוחלט       – ואחריו דיבור שקט (רגע שקט מעניין)
  52-57   פרץ עוצמה שני   – ניצחון
  70-75   מודולציה מהירה  – "צחוק"
  20 / 45 / 68  חיתוכי סצנה חדים (החלפת "שלב")
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

WIDTH, HEIGHT, FPS = 1280, 720, 25
DURATION = 90.0
SR = 48000

SCENE_CUTS = [20.0, 45.0, 68.0]
LOUD_EVENTS = [(22.0, 27.0), (52.0, 57.0)]
SILENCE = (38.0, 43.0)
LAUGH = (70.0, 75.0)

# (רקע כהה, צבע הדגשה, צבע קרקע) לכל "שלב"
LEVELS = [
    ((14, 18, 34), (86, 140, 255), (26, 32, 58)),
    ((32, 14, 28), (255, 96, 132), (52, 24, 44)),
    ((10, 30, 24), (72, 220, 160), (18, 48, 38)),
    ((34, 26, 10), (255, 190, 72), (54, 42, 18)),
]


# --------------------------------------------------------------------------
# עזרי ציור
# --------------------------------------------------------------------------
def _rect(img, x, y, w, h, color, alpha=1.0):
    H, W = img.shape[:2]
    x0, y0 = max(0, int(x)), max(0, int(y))
    x1, y1 = min(W, int(x + w)), min(H, int(y + h))
    if x1 <= x0 or y1 <= y0:
        return
    patch = img[y0:y1, x0:x1].astype(np.float32)
    col = np.array(color, dtype=np.float32)
    img[y0:y1, x0:x1] = np.clip(patch * (1 - alpha) + col * alpha, 0, 255).astype(np.uint8)


def _disc(img, cx, cy, r, color, alpha=1.0):
    H, W = img.shape[:2]
    x0, y0 = max(0, int(cx - r)), max(0, int(cy - r))
    x1, y1 = min(W, int(cx + r + 1)), min(H, int(cy + r + 1))
    if x1 <= x0 or y1 <= y0:
        return
    yy, xx = np.mgrid[y0:y1, x0:x1]
    mask = ((xx - cx) ** 2 + (yy - cy) ** 2) <= r * r
    if not mask.any():
        return
    region = img[y0:y1, x0:x1].astype(np.float32)
    col = np.array(color, dtype=np.float32)
    region[mask] = region[mask] * (1 - alpha) + col * alpha
    img[y0:y1, x0:x1] = np.clip(region, 0, 255).astype(np.uint8)


# ספרות 5x7 לציור ניקוד בלי תלות בגופנים
_DIGITS = {
    "0": ("111", "101", "101", "101", "101", "101", "111"),
    "1": ("010", "110", "010", "010", "010", "010", "111"),
    "2": ("111", "001", "001", "111", "100", "100", "111"),
    "3": ("111", "001", "001", "111", "001", "001", "111"),
    "4": ("101", "101", "101", "111", "001", "001", "001"),
    "5": ("111", "100", "100", "111", "001", "001", "111"),
    "6": ("111", "100", "100", "111", "101", "101", "111"),
    "7": ("111", "001", "001", "010", "010", "010", "010"),
    "8": ("111", "101", "101", "111", "101", "101", "111"),
    "9": ("111", "101", "101", "111", "001", "001", "111"),
}


def _draw_number(img, text, x, y, scale, color):
    cursor = x
    for ch in text:
        glyph = _DIGITS.get(ch)
        if glyph is None:
            cursor += scale * 2
            continue
        for row, bits in enumerate(glyph):
            for col, bit in enumerate(bits):
                if bit == "1":
                    _rect(img, cursor + col * scale, y + row * scale,
                          scale, scale, color)
        cursor += scale * 4


def _draw_face(img, cx, cy, size):
    """
    פנים סכמטיות. אינן מיועדות להפעיל מסווג פנים אמיתי – הן רק
    מסמנות ויזואלית את אזור מצלמת הסטרימר.
    """
    h, w = img.shape[:2]
    half = size // 2
    x0, x1 = max(0, cx - half), min(w, cx + half)
    y0, y1 = max(0, cy - int(half * 1.2)), min(h, cy + int(half * 1.2))
    if x1 <= x0 or y1 <= y0:
        return

    fh, fw = y1 - y0, x1 - x0
    yy, xx = np.mgrid[0:fh, 0:fw]
    ny = (yy - fh / 2) / (fh / 2)
    nx = (xx - fw / 2) / (fw / 2)
    oval = (nx ** 2 + (ny * 0.85) ** 2) <= 1.0

    face = img[y0:y1, x0:x1]
    skin = np.array([176, 198, 220], dtype=np.float32)
    shade = np.clip(1.10 - 0.35 * (nx ** 2 + ny ** 2), 0.55, 1.12)[..., None]
    face[oval] = np.clip(skin * shade, 0, 255).astype(np.uint8)[oval]

    eye_w, eye_h = max(3, fw // 7), max(2, fh // 12)
    for sign in (-1, 1):
        ex = int(fw / 2 + sign * fw * 0.21) - eye_w // 2
        face[int(fh * 0.36):int(fh * 0.36) + eye_h,
             max(0, ex):min(fw, ex + eye_w)] = (40, 36, 34)
    mx0 = int(fw / 2 - fw * 0.16)
    face[int(fh * 0.68):int(fh * 0.68) + max(2, fh // 16),
         max(0, mx0):min(fw, mx0 + int(fw * 0.32))] = (76, 64, 96)


# --------------------------------------------------------------------------
# פריים
# --------------------------------------------------------------------------
def _scene_index(t: float) -> int:
    return sum(1 for c in SCENE_CUTS if t >= c)


def _event_intensity(t: float) -> float:
    for s, e in LOUD_EVENTS:
        if s <= t <= e:
            return float(np.clip(1.0 - abs((t - (s + e) / 2)) / ((e - s) / 2), 0, 1))
    return 0.0


def _make_frame(t: float) -> np.ndarray:
    level = _scene_index(t) % len(LEVELS)
    bg, accent, ground = LEVELS[level]
    burst = _event_intensity(t)

    img = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)

    # --- שמיים: גרדיאנט אנכי ---
    grad = np.linspace(0.0, 1.0, HEIGHT, dtype=np.float32)[:, None]
    for c in range(3):
        img[:, :, c] = (bg[c] * (1.0 - grad * 0.35) +
                        accent[c] * grad * 0.10).astype(np.uint8)

    # --- פרלקסה: שתי שכבות גבעות שנעות במהירויות שונות ---
    for layer, (speed, height_frac, alpha) in enumerate(
            ((18.0, 0.22, 0.45), (46.0, 0.14, 0.75))):
        offset = (t * speed) % 240
        base_y = int(HEIGHT * (0.62 + layer * 0.10))
        for k in range(-1, WIDTH // 120 + 2):
            hx = k * 120 - offset
            hh = int(HEIGHT * height_frac * (0.7 + 0.5 * math.sin(k * 1.7 + layer)))
            _rect(img, hx, base_y - hh, 124, HEIGHT, ground, alpha)

    # --- קרקע ---
    ground_y = int(HEIGHT * 0.78)
    _rect(img, 0, ground_y, WIDTH, HEIGHT - ground_y,
          tuple(int(c * 0.75) for c in ground))
    stripe = (t * 160) % 80
    for k in range(-1, WIDTH // 80 + 2):
        _rect(img, k * 80 - stripe, ground_y, 40, 6, accent, 0.30)

    # --- "אויבים" שנעים לעבר הדמות ---
    for k in range(5):
        phase = t * (0.75 + 0.22 * k) + k * 2.1
        ex = int(WIDTH * (0.5 + 0.45 * math.sin(phase)))
        ey = int(ground_y - 40 - 90 * abs(math.sin(phase * 1.6)))
        size = 26 + 8 * k
        _rect(img, ex - size // 2, ey - size // 2, size, size,
              tuple(min(255, int(c * 1.15)) for c in accent), 0.9)
        _rect(img, ex - size // 4, ey - size // 4, size // 2, size // 2,
              (250, 250, 255), 0.55)

    # --- הדמות ---
    px = int(WIDTH * (0.30 + 0.06 * math.sin(t * 1.3)))
    py = ground_y - 56 - int(34 * max(0.0, math.sin(t * 2.4)))
    _rect(img, px - 16, py - 44, 32, 44, (245, 248, 255))
    _rect(img, px - 11, py - 38, 22, 12, accent)
    _rect(img, px - 14, py, 28, 16, tuple(int(c * 0.8) for c in accent))

    # --- הבזק על אירוע עוצמה ---
    if burst > 0.01:
        flash = 0.08 + 0.22 * burst * (0.5 + 0.5 * math.sin(t * 22.0))
        img = np.clip(img.astype(np.float32) * (1 + flash), 0, 255).astype(np.uint8)
        _disc(img, px, py - 20, int(30 + 90 * burst), (255, 250, 220),
              0.25 * burst)

    # --- HUD: פס חיים + ניקוד ---
    _rect(img, 28, 26, 260, 22, (0, 0, 0), 0.45)
    hp = 0.85 - 0.45 * burst
    _rect(img, 32, 30, int(252 * max(0.05, hp)), 14,
          (90, 220, 120) if hp > 0.4 else (90, 90, 240))

    score = int(1200 + t * 37 + burst * 420)
    _rect(img, WIDTH - 250, 26, 210, 34, (0, 0, 0), 0.45)
    _draw_number(img, f"{score:05d}", WIDTH - 236, 32, 4,
                 (255, 245, 200) if burst > 0.2 else (210, 220, 240))

    # --- מצלמת הסטרימר ---
    cam_w, cam_h = 292, 214
    cam_x, cam_y = WIDTH - cam_w - 30, HEIGHT - cam_h - 30
    _rect(img, cam_x - 3, cam_y - 3, cam_w + 6, cam_h + 6,
          tuple(min(255, int(c * 1.4)) for c in accent), 0.9)
    _rect(img, cam_x, cam_y, cam_w, cam_h, (34, 36, 44))
    _rect(img, cam_x, cam_y, cam_w, cam_h, (92, 100, 116), 0.55)
    sway = int(14 * math.sin(t * 0.8))
    bob = int(6 * math.sin(t * 1.9))
    _draw_face(img, cam_x + cam_w // 2 + sway, cam_y + cam_h // 2 + bob, 132)
    _disc(img, cam_x + 16, cam_y + 16, 6, (255, 70, 70))   # נורת הקלטה

    return img


def write_video(path: Path) -> None:
    total = int(DURATION * FPS)
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgr24",
        "-s", f"{WIDTH}x{HEIGHT}", "-r", str(FPS), "-i", "-",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
        "-pix_fmt", "yuv420p", "-g", str(FPS * 2), str(path),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    assert proc.stdin is not None
    for i in range(total):
        proc.stdin.write(_make_frame(i / FPS).tobytes())
        if i % (FPS * 15) == 0:
            print(f"  frame {i}/{total}", flush=True)
    proc.stdin.close()
    if proc.wait() != 0:
        raise SystemExit("ffmpeg video encode failed")


# --------------------------------------------------------------------------
# אודיו
# --------------------------------------------------------------------------
def _speech_like(t: np.ndarray, f0: float, rate: float, seed: int = 7) -> np.ndarray:
    """גל דמוי-דיבור: הרמוניות עם מעטפת הברות."""
    sig = np.zeros_like(t)
    for k, amp in enumerate((1.0, 0.55, 0.32, 0.18), start=1):
        sig += amp * np.sin(2 * np.pi * f0 * k * t + k * 0.6)
    syllable = 0.5 + 0.5 * np.sin(2 * np.pi * rate * t)
    noise = np.random.default_rng(seed).normal(0, 0.06, t.shape)
    return (sig / 2.1) * syllable + noise * syllable


def write_audio(path: Path) -> None:
    n = int(DURATION * SR)
    t = np.arange(n, dtype=np.float32) / SR
    audio = np.zeros(n, dtype=np.float32)

    audio += 0.16 * _speech_like(t, 138.0, 3.4)

    for s, e in LOUD_EVENTS:
        m = (t >= s) & (t < e)
        ramp = np.zeros(n, dtype=np.float32)
        if m.sum() > 1:
            ramp[m] = np.hanning(int(m.sum()))
        audio += 0.62 * ramp * _speech_like(t, 232.0, 5.6, seed=11)

    ls, le = LAUGH
    m = (t >= ls) & (t < le)
    if m.sum() > 1:
        env = np.zeros(n, dtype=np.float32)
        env[m] = (0.5 + 0.5 * np.sin(2 * np.pi * 6.5 * t[m])) * np.hanning(int(m.sum()))
        audio += 0.50 * env * _speech_like(t, 330.0, 7.5, seed=13)

    ss, se = SILENCE
    audio[(t >= ss) & (t < se)] = 0.0

    m = (t >= se) & (t < se + 6.0)
    if m.sum() > 1:
        audio[m] += 0.20 * _speech_like(t[m], 152.0, 2.6, seed=17)

    audio = np.clip(audio, -0.97, 0.97)
    pcm = (audio * 32767).astype("<i2")

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(pcm.tobytes())


# --------------------------------------------------------------------------
# תמלול בדיקה (fixture)
# --------------------------------------------------------------------------
FIXTURE_LINES: list[tuple[float, float, str]] = [
    (1.0, 5.2, "שלום לכולם וברוכים הבאים לשידור של היום"),
    (5.6, 10.4, "היום אנחנו הולכים לנסות משהו שאף פעם לא עשיתי כאן"),
    (11.0, 16.0, "אבל קודם בואו נסתכל על מה שקרה אתמול במשחק"),
    (16.4, 19.8, "זה היה די רגיל בהתחלה, שום דבר מיוחד"),
    (20.4, 22.0, "רגע רגע רגע"),
    (22.2, 26.8, "אין מצב! ראיתם את זה? זה מטורף לגמרי!"),
    (27.2, 31.0, "אני נשבע לכם שאף פעם לא ראיתי דבר כזה"),
    (31.4, 36.5, "טוב, בואו נירגע רגע ונמשיך הלאה בשקט"),
    (36.8, 38.0, "אוקיי"),
    (43.2, 48.6, "תקשיבו, אני רוצה לספר לכם משהו שלא סיפרתי לאף אחד"),
    (49.0, 52.0, "לפני שנה חשבתי לעזוב את הסטרימינג לגמרי"),
    (52.4, 57.0, "וואו! בדיוק עכשיו? ניצחתי! עשיתי את זה!"),
    (57.4, 62.0, "That was actually insane, I can't believe it worked"),
    (62.4, 67.5, "בואו נדבר רגע על למה זה עבד ומה הייתה הטעות שלי"),
    (68.2, 70.0, "ואז קרה זה"),
    (70.2, 74.8, "חחחח אני מת מצחוק, תראו את הפרצוף שלו"),
    (75.2, 80.0, "אוקיי אוקיי, נרגעתי. בואו נסכם את הדברים"),
    (80.4, 86.0, "תודה שהייתם איתי היום, נתראה בשידור הבא"),
]


def write_fixture_transcript(path: Path) -> None:
    """
    תמלול מוכן שמתאים לאודיו הסינתטי.
    זהו **קלט בדיקה**, לא פלט של מודל זיהוי דיבור.
    """
    segments = []
    for start, end, text in FIXTURE_LINES:
        words = text.split()
        span = (end - start) / max(1, len(words))
        segments.append({
            "start": start, "end": end, "text": text,
            "language": "en" if text[0].isascii() and text[0].isalpha() else "he",
            "avg_logprob": -0.22, "no_speech_prob": 0.03,
            "words": [
                {"start": round(start + i * span, 3),
                 "end": round(start + (i + 1) * span - 0.06, 3),
                 "text": w, "p": 0.95}
                for i, w in enumerate(words)
            ],
        })
    path.write_text(json.dumps(
        {"language": "he", "segments": segments,
         "_note": "קובץ בדיקה סינתטי – לא נוצר על-ידי מודל תמלול"},
        ensure_ascii=False, indent=1), encoding="utf-8")


# --------------------------------------------------------------------------
def main() -> None:
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "./testdata")
    out_dir.mkdir(parents=True, exist_ok=True)

    video_raw = out_dir / "_video_only.mp4"
    audio_raw = out_dir / "_audio.wav"
    final = out_dir / "polixor_test_stream.mp4"

    print("· מייצר וידאו…")
    write_video(video_raw)
    print("· מייצר אודיו…")
    write_audio(audio_raw)
    print("· ממזג…")
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(video_raw), "-i", str(audio_raw),
        "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
        "-shortest", "-movflags", "+faststart", str(final),
    ], check=True)
    video_raw.unlink(missing_ok=True)
    audio_raw.unlink(missing_ok=True)

    write_fixture_transcript(final.with_suffix(".transcript.json"))
    print(f"\n✓ {final}  ({final.stat().st_size / 1024 / 1024:.1f} MB)")
    print(f"✓ {final.with_suffix('.transcript.json')}")


if __name__ == "__main__":
    main()
