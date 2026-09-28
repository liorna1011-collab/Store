"""
Golden Video — בדיקת קצה-אל-קצה ברמת הפריים והדגימה.

הרעיון: מייצרים וידאו מקור שבו **הזמן מקודד בתוך התמונה עצמה**.
ערוץ האדום עולה ליניארית לאורך הסרטון, ולכן מכל פריים בפלט אפשר
לשחזר מאיזה רגע במקור הוא הגיע. כך אפשר לבדוק לא רק „יצא MP4
תקין" אלא **שהחיתוכים באמת קרו במקומות שהתכנית אמרה**.

בנוסף, בארבע נקודות ידועות יש הבזק לבן בפינה **וצפצוף באודיו
באותו רגע בדיוק**. אם העריכה הזיזה וידאו ואודיו בצורה שונה, ההבזק
והצפצוף יתפצלו — וזו בדיוק בדיקת ה-A/V sync.

מה נבדק כאן:
  • דגימת פריימים — לא רק Metadata
  • החיתוכים נופלים במקום שהתכנית קבעה
  • סנכרון תמונה-קול נשמר אחרי העריכה
  • שלושה ייצואים רצופים מפיקים אותם תזמוני כתוביות (ללא Drift)
  • העוצמה בפלט עומדת ביעד שנמדד

הרצה:
    POLIXOR_DATA_DIR=/tmp/pxgold python3 tests/golden_video.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxgold_"))

from polixor.config import PATHS, AppSettings                    # noqa: E402

PATHS.ensure()

from polixor.db import init_db, session_scope                    # noqa: E402
from polixor.models import Clip, ClipStatus, Job, JobStatus, new_id  # noqa: E402
from polixor.pipeline import run_job                             # noqa: E402
from polixor.services import audio_mastering as am               # noqa: E402
from polixor.util.ffmpeg import ffmpeg_bin, probe                # noqa: E402

PASS, FAIL = "\033[92m✓\033[0m", "\033[91m✗\033[0m"
results: list[tuple[bool, str]] = []

W, H, FPS = 320, 180, 25
DURATION = 45.0
RAMP = 5.0                       # ערך אדום לשנייה → 0..225 ב-45 שניות
MARKERS = [8.0, 18.0, 28.0, 38.0]
FLASH = 0.12                     # אורך ההבזק/הצפצוף
TMP = Path(os.environ["POLIXOR_DATA_DIR"])


def check(cond: bool, label: str, detail: str = "") -> bool:
    results.append((bool(cond), label))
    print(f"  {PASS if cond else FAIL} {label}" + (f"  — {detail}" if detail else ""))
    return bool(cond)


# ==========================================================================
# יצירת המקור
# ==========================================================================
def build_source(dst: Path) -> Path:
    """
    וידאו שבו הזמן מקודד בערוץ האדום, עם הבזק וצפצוף בנקודות ידועות.

    האודיו: טון „דיבור" מאופנן עם הפסקות אמיתיות (כדי שיהיה אוויר
    מת לחתוך), וצפצוף 1kHz בכל נקודת סימון.
    """
    if dst.exists():
        return dst
    dst.parent.mkdir(parents=True, exist_ok=True)

    # -- וידאו: רמפה + הבזק בראש המסגרת, במרכז האופקי --
    # במרכז ולא בפינה: הפלט האנכי הוא חיתוך 9:16 מהמרכז, ופינה
    # שמאלית נחתכת ממנו לגמרי — ההבזק היה נעלם מהבדיקה.
    flash_enable = "+".join(
        f"between(t,{m},{m + FLASH})" for m in MARKERS)
    vf = (f"geq=r='clip({RAMP}*T,0,250)':g='40':b='120',"
          f"drawbox=x={W // 2 - 24}:y=6:w=48:h=40:color=white@1.0:t=fill:"
          f"enable='{flash_enable}'")

    # -- אודיו: נבנה ב-numpy ולא ב-amix --
    # amix מקטין את עוצמת הכניסות גם עם normalize=0, והצפצופים
    # יצאו חלשים מכדי להימדד. כאן הרמות מדויקות ולכן ניתנות לבדיקה.
    wav = _build_audio(dst.with_suffix(".wav"))

    cmd = [
        ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:d={DURATION}:r={FPS}",
        "-i", str(wav),
        "-vf", vf, "-map", "0:v", "-map", "1:a",
        "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "18", "-c:a", "aac", "-b:a", "128k",
        "-t", str(DURATION), str(dst),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if res.returncode != 0:
        raise RuntimeError(f"בניית המקור נכשלה: {res.stderr[-600:]}")
    return dst


def _build_audio(dst: Path, sr: int = 48000) -> Path:
    """„דיבור" עם הפסקות אמיתיות, וצפצוף 1kHz בכל נקודת סימון."""
    import wave

    n = int(sr * DURATION)
    t = np.arange(n, dtype=np.float64) / sr
    voice = (np.sin(2 * np.pi * 190.0 * t)
             * (0.5 + 0.5 * np.sin(2 * np.pi * 4.2 * t)))
    voice *= (np.mod(t, 4.0) < 2.6).astype(np.float64) * 0.28

    beep = np.zeros(n, dtype=np.float64)
    tone = np.sin(2 * np.pi * 1000.0 * t)
    for m in MARKERS:
        mask = (t >= m) & (t < m + FLASH)
        beep[mask] = tone[mask] * 0.45

    pcm = np.clip(voice + beep, -1.0, 1.0)
    with wave.open(str(dst), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes((pcm * 32767.0).astype(np.int16).tobytes())
    return dst


def write_fixture_transcript(dst: Path) -> Path:
    """
    תמלול שמתיישר עם קטעי הדיבור (2.6 שניות דיבור מכל 4).

    הטקסט אמיתי מבחינת מבנה — פתיחה חלשה, טענה, שיא רגשי,
    קריאה לפעולה — כדי שהבמאי יהיה לו על מה לעבוד.
    """
    lines = [
        "אז אה רגע אחד",
        "היום אני רוצה לדבר על משהו",
        "שלוש טעויות שעשיתי בשנה הראשונה",
        "הראשונה הייתה שלא ביקשתי עזרה",
        "וזה עלה לי בחצי שנה של עבודה",
        "ואז ואז הבנתי משהו חשוב",
        "זה היה הרגע הכי קשה שעברתי",
        "אבל ממנו למדתי את השיעור הגדול",
        "היום אני עושה את זה אחרת לגמרי",
        "אם זה עזר לכם תעקבו לעוד טיפים",
        "נתראה בסרטון הבא חברים",
    ]
    segments = []
    for i, text in enumerate(lines):
        start = i * 4.0
        end = start + 2.6
        if end > DURATION:
            break
        toks = text.split()
        per = (end - start) / len(toks)
        words = [{"start": round(start + k * per, 3),
                  "end": round(start + (k + 0.9) * per, 3), "text": tok}
                 for k, tok in enumerate(toks)]
        segments.append({"start": start, "end": end, "text": text,
                         "words": words, "language": "he"})
    dst.write_text(json.dumps({"language": "he", "segments": segments},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    return dst


# ==========================================================================
# דגימה מתוך הפלט
# ==========================================================================
def sample_frame(path: Path, t: float) -> np.ndarray:
    out = subprocess.run(
        [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-ss", str(t),
         "-i", str(path), "-frames:v", "1", "-f", "rawvideo",
         "-pix_fmt", "rgb24", "-"],
        capture_output=True, timeout=120).stdout
    if not out:
        return np.zeros((1, 1, 3), dtype=np.uint8)
    n = len(out)
    # הפלט עשוי להיות ברזולוציה אחרת מהמקור — מגלים אותה מה-probe
    return np.frombuffer(out, dtype=np.uint8), n


def frame_rgb(path: Path, t: float, w: int, h: int):
    raw, n = sample_frame(path, t)
    if n < w * h * 3:
        return None
    return raw[: w * h * 3].reshape(h, w, 3)


def decode_source_time(frame: np.ndarray) -> float:
    """
    משחזר את זמן המקור מערוץ האדום.

    דוגמים את מרכז הפריים ולא את הפינה, כי הפינה עשויה להיות
    הבזק לבן — והוא היה מזייף זמן מאוחר יותר.
    """
    h, w, _ = frame.shape
    cy, cx = h // 2, w // 2
    box = frame[max(0, cy - 12):cy + 12, max(0, cx - 12):cx + 12, 0]
    return float(box.mean()) / RAMP


def flash_times(path: Path, duration: float, w: int, h: int,
                step: float = 1.0 / FPS) -> list[float]:
    """הזמנים בפלט שבהם הפינה השמאלית-עליונה לבנה."""
    hits: list[float] = []
    t = 0.0
    while t < duration - 0.02:
        fr = frame_rgb(path, t, w, h)
        if fr is not None:
            # חלון שנופל כולו בתוך תיבת ההבזק (3%–26% מהגובה במקור)
            band = fr[int(0.07 * h): max(int(0.07 * h) + 2, int(0.20 * h)),
                      max(0, w // 2 - w // 12): w // 2 + w // 12]
            if band.mean() > 190:
                hits.append(round(t, 3))
        t += step
    return _merge_close(hits, 0.25)


def beep_times(path: Path) -> list[float]:
    """הזמנים בפלט שבהם יש אנרגיה חזקה סביב 1kHz."""
    with tempfile.TemporaryDirectory(prefix="beep_") as tmp:
        wav = Path(tmp) / "a.wav"
        subprocess.run(
            [ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
             "-i", str(path), "-vn", "-ac", "1", "-ar", "48000",
             "-c:a", "pcm_s16le", str(wav)],
            capture_output=True, timeout=300)
        if not wav.exists():
            return []
        import wave

        with wave.open(str(wav), "rb") as wf:
            sr = wf.getframerate()
            x = np.frombuffer(wf.readframes(wf.getnframes()),
                              dtype=np.int16).astype(np.float32) / 32768.0

    win = int(sr * 0.02)
    n = x.size // win
    if n < 4:
        return []
    blocks = x[: n * win].reshape(n, win) * np.hanning(win).astype(np.float32)
    spec = np.abs(np.fft.rfft(blocks, axis=1))
    freqs = np.fft.rfftfreq(win, 1.0 / sr)
    band = (freqs > 850) & (freqs < 1150)
    rest = (freqs > 120) & (freqs < 700)
    ratio = spec[:, band].mean(axis=1) / np.maximum(
        spec[:, rest].mean(axis=1), 1e-6)
    hits = [round(i * win / sr, 3) for i in np.flatnonzero(ratio > 6.0)]
    return _merge_close(hits, 0.25)


def plan_map_time(beats: list[dict], window_time: float):
    """
    זמן בחלון המקור → זמן בפלט, לפי רשימת הביטים שנשמרה בקליפ.

    מחזיר None אם הרגע הזה נחתך החוצה. זו אותה לוגיקה שהרנדרר
    מבצע, ולכן היא הקריטריון הנכון להשוות אליו את המדידה.
    """
    out = 0.0
    for b in beats:
        speed = float(b.get("speed", 1.0)) or 1.0
        if window_time < b["start"] - 1e-6:
            return None
        if window_time <= b["end"] + 1e-6:
            return out + (window_time - b["start"]) / speed
        out += (b["end"] - b["start"]) / speed
    return None


def _merge_close(times: list[float], gap: float) -> list[float]:
    out: list[float] = []
    for t in sorted(times):
        if not out or t - out[-1] > gap:
            out.append(t)
    return out


# ==========================================================================
# הרצת הפייפליין
# ==========================================================================
def run_pipeline(video: Path, overrides: dict) -> str:
    base = AppSettings().to_dict()
    base.update({
        "transcript_provider": "fixture",
        "ai_mode": "heuristic",
        "visual_sample_fps": 2.0,
        "video_quality": "low",
        "subtitles_enabled": True,
        "long_enabled": False,
        "short_enabled": True,
        "short_count": 1,
        "short_min_seconds": 15,
        "short_max_seconds": 40,
        "short_layout": "center",
        "director_enabled": True,
        "mastering_enabled": True,
    })
    base.update(overrides)
    settings = AppSettings.from_dict(base)

    job_id = new_id()
    with session_scope() as s:
        s.add(Job(id=job_id, title="golden", input_url="",
                  status=JobStatus.QUEUED,
                  settings_snapshot=settings.to_dict(),
                  artifacts={"source_path": str(video)},
                  completed_stages=[]))
    run_job(job_id, threading.Event())
    return job_id


# ==========================================================================
def main() -> int:
    init_db()
    src = build_source(TMP / "golden_source.mp4")
    # ספק התמלול מחפש ליד קובץ האודיו שחולץ, לא ליד המקור — ולכן
    # מוסרים לו את הנתיב במפורש.
    fixture = write_fixture_transcript(src.with_suffix(".transcript.json"))
    os.environ["POLIXOR_FIXTURE_TRANSCRIPT"] = str(fixture)

    info = probe(src)
    print(f"\n{'=' * 72}\n▶ Golden Video — מקור {info.width}×{info.height} "
          f"{info.duration:.1f}s\n{'=' * 72}")

    # ---- שפיות המקור: הקידוד עצמו עובד ----
    print("\n— המקור —")
    ok_ramp = True
    for t in (2.0, 20.0, 40.0):
        fr = frame_rgb(src, t, info.width, info.height)
        got = decode_source_time(fr) if fr is not None else -1.0
        ok_ramp = ok_ramp and abs(got - t) < 1.0
    check(ok_ramp, "הזמן מקודד בתמונה וניתן לשחזור מכל פריים")
    src_flashes = flash_times(src, info.duration, info.width, info.height)
    src_beeps = beep_times(src)
    check(len(src_flashes) == len(MARKERS),
          f"במקור {len(MARKERS)} הבזקים", str(src_flashes))
    check(len(src_beeps) == len(MARKERS),
          f"במקור {len(MARKERS)} צפצופים", str(src_beeps))
    pairs = [abs(f - b) for f, b in zip(src_flashes, src_beeps)]
    check(bool(pairs) and max(pairs) < 0.2,
          "במקור ההבזק והצפצוף מסונכרנים",
          f"סטייה מרבית {max(pairs) * 1000:.0f}ms" if pairs else "—")

    # ---- הרצה ----
    print("\n— הפייפליין —")
    try:
        job_id = run_pipeline(src, {})
        check(True, "הפייפליין הסתיים ללא שגיאה")
    except Exception as exc:                            # noqa: BLE001
        import traceback

        traceback.print_exc()
        check(False, f"הפייפליין נכשל: {type(exc).__name__}: {exc}")
        return _summary()

    with session_scope() as s:
        clips = [c for c in s.query(Clip).filter(Clip.job_id == job_id).all()
                 if c.status == ClipStatus.READY]
        rows = [(c.id, c.file_path, dict(c.render_params or {}),
                 c.source_start, c.source_end, c.duration) for c in clips]
    if not check(bool(rows), "נוצר לפחות קליפ אחד"):
        return _summary()

    clip_id, path_s, params, s_start, s_end, dur = rows[0]
    out = Path(path_s)
    oinfo = probe(out)
    check(out.exists() and oinfo.duration > 1.0,
          "הקליפ תקין וניתן לניגון", f"{oinfo.duration:.2f}s")

    # ---- דגימת פריימים: החיתוכים קרו במקום הנכון ----
    print("\n— דגימת פריימים —")
    beats = params.get("beats") or []
    check(bool(beats), "תכנית העריכה נשמרה", f"{len(beats)} ביטים")
    kept = [(s_start + b["start"], s_start + b["end"]) for b in beats]

    out_flash = flash_times(out, oinfo.duration, oinfo.width, oinfo.height)
    survived = [m for m in MARKERS
                if any(a - 0.05 <= m <= b + 0.05 for a, b in kept)]
    check(len(out_flash) == len(survived),
          "מספר ההבזקים בפלט תואם לסימונים ששרדו את החיתוך",
          f"{len(out_flash)} בפלט, {len(survived)} שרדו: {survived}")

    # --- הבדיקה החזקה: כל הבזק נוחת בדיוק במקום שהתכנית חוזה ---
    if len(out_flash) == len(survived) and survived:
        frame_s = 1.0 / max(1.0, oinfo.fps or FPS)
        worst, rows = 0.0, []
        for got, marker in zip(out_flash, survived):
            want = plan_map_time(beats, marker - s_start)
            if want is None:
                continue
            worst = max(worst, abs(got - want))
            rows.append(f"{marker:.0f}s→{want:.2f}s (נמדד {got:.2f}s)")
        check(worst <= 2.0 * frame_s,
              "כל סימון נוחת בפלט בדיוק במקום שהתכנית חוזה",
              f"סטייה מרבית {worst * 1000:.0f}ms · " + " · ".join(rows))
    else:
        check(False, "לא ניתן להתאים הבזקים לסימונים")

    # --- אורך הפלט מול מה שהתכנית הבטיחה ---
    planned = sum((b["end"] - b["start"]) / (float(b.get("speed", 1.0)) or 1.0)
                  for b in beats)
    check(abs(oinfo.duration - planned) <= 0.25,
          "אורך הפלט תואם לסכום הביטים בתכנית",
          f"{oinfo.duration:.2f}s מול {planned:.2f}s")

    # --- דגימה רציפה: כיוון הזמן ---
    # הערה על דיוק: ערך האדום עובר המרת טווח בקידוד ורעש קוונטיזציה,
    # ולכן הזמן המשוחזר ממנו מדויק רק לכ-±2 שניות. הוא משמש כאן
    # לבדיקת **סדר** בלבד. הבדיקה המדויקת של „איפה נחתך" נעשית
    # למעלה מול ההבזקים, שם הסטייה נמדדת במילישניות.
    times, decoded = [], []
    t = 0.25
    while t < oinfo.duration - 0.25:
        fr = frame_rgb(out, t, oinfo.width, oinfo.height)
        if fr is not None:
            times.append(t)
            decoded.append(decode_source_time(fr))
        t += 0.5
    check(len(decoded) > 8, "נדגמו מספיק פריימים מהפלט", f"{len(decoded)}")

    back = [(a, b) for a, b in zip(decoded, decoded[1:]) if b < a - 2.5]
    check(not back,
          "זמן המקור עולה לאורך הפלט (אין קטע שרונדר מחוץ לסדר)",
          str([(round(a, 1), round(b, 1)) for a, b in back[:3]])
          if back else f"{len(decoded)} דגימות, דיוק ±2s")

    if decoded:
        span = max(decoded) - min(decoded)
        check(span > 0.5 * (s_end - s_start),
              "הפלט מכסה את רוב טווח המקור שנבחר",
              f"{span:.1f}s מתוך {s_end - s_start:.1f}s")

    # ---- סנכרון תמונה-קול ----
    print("\n— סנכרון תמונה/קול —")
    out_beep = beep_times(out)
    check(bool(out_flash), "נמצאו הבזקים בפלט", str(out_flash))
    if out_flash and out_beep:
        worst = 0.0
        for f in out_flash:
            worst = max(worst, min(abs(f - b) for b in out_beep))
        frame_ms = 1000.0 / max(1.0, oinfo.fps or FPS)
        check(worst <= 2.5 * frame_ms / 1000.0,
              "ההבזק והצפצוף נשארו מסונכרנים אחרי העריכה",
              f"סטייה מרבית {worst * 1000:.0f}ms "
              f"(פריים = {frame_ms:.0f}ms)")
    else:
        check(False, "לא נמצאו הבזקים או צפצופים בפלט",
              f"flash={out_flash} beep={out_beep}")

    # ---- אודיו: מדידה אחרי ----
    print("\n— אודיו —")
    audio = params.get("audio") or {}
    if audio:
        check(audio.get("after_lufs") is not None,
              "העוצמה בפלט נמדדה אחרי הרינדור",
              f"{audio.get('before_lufs')} → {audio.get('after_lufs')} LUFS")
        if not audio.get("needs_review"):
            check(True, "האודיו עמד ביעד", audio.get("summary", ""))
        else:
            check(False, "האודיו לא עמד ביעד (סומן needs_review)",
                  "; ".join(audio.get("issues") or []))
    else:
        m = am.measure(out)
        check(m.ok, "נמדדה עוצמת הפלט", f"{m.lufs} LUFS")

    # ---- כתוביות: שלושה ייצואים, אותם תזמונים ----
    print("\n— רגרסיית כתוביות —")
    runs = []
    for _ in range(3):
        runs.append(_export_cues(clip_id))
    same = runs[0] == runs[1] == runs[2]
    check(bool(runs[0]), "נשמרו כתוביות", f"{len(runs[0])} כתוביות")
    check(same, "שלושה ייצואים רצופים מפיקים תזמונים זהים (ללא Drift)",
          "" if same else f"{runs[0][:2]} מול {runs[1][:2]}")

    if runs[0]:
        last = max(c[1] for c in runs[0])
        check(last <= oinfo.duration + 0.35,
              "אף כתובית לא חורגת מאורך הקליפ",
              f"אחרונה ב-{last:.2f}s מתוך {oinfo.duration:.2f}s")

    return _summary()


def _export_cues(clip_id: str) -> list[tuple[float, float, str]]:
    """קורא את הכתוביות דרך אותו מסלול שבו הממשק מייצא אותן."""
    from polixor.api.routes_clips import _cues_for_render

    with session_scope() as s:
        cues = _cues_for_render(s, clip_id, shift=0.0)
    return [(round(c.start, 3), round(c.end, 3), c.text) for c in cues]


def _summary() -> int:
    passed = sum(1 for ok, _ in results if ok)
    print(f"\n{'=' * 72}\nסיכום: {passed}/{len(results)} בדיקות עברו")
    bad = [label for ok, label in results if not ok]
    if bad:
        print("\nבדיקות שנכשלו:")
        for label in bad:
            print(f"  {FAIL} {label}")
    print("=" * 72)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
