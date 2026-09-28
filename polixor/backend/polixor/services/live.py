"""
קליטת שידור חי: זיהוי, הקלטה במקטעים, חיבור מחדש ואיחוד למקור אחד.

שיטת ההקלטה
------------
yt-dlp משמש לפענוח הקישור בלבד – הוא מחזיר כתובת מניפסט (HLS/DASH)
של הזרם. את ההקלטה עצמה מבצע FFmpeg ישירות מהמניפסט, במקטעים סגורים:

    ffmpeg -i <manifest> -c copy -f mpegts -t <SEGMENT> seg_000.ts

הבחירה במקטעים היא מה שמונע אובדן חומר: כל מקטע שנסגר נרשם מיד
ב-DB, כך שנפילה של הזרם, של הרשת או של התהליך משאירה בידינו את כל
מה שכבר הוקלט. כתובות מניפסט פגות תוקף, ולכן הכתובת נפתרת מחדש לפני
כל מקטע – וזה גם מנגנון החיבור מחדש.

mpegts נבחר כי הוא עמיד לקטיעה באמצע (בניגוד ל-MP4, שכותרתו נכתבת
בסוף), ומקטעים שלו ניתנים לאיחוד בהעתקה ללא קידוד מחדש.

הערה על הרשאות: אין כאן שום עקיפה של DRM או של הגבלות גישה. אם
הפלטפורמה אינה מאפשרת גישה לזרם, ההקלטה נכשלת עם שגיאה מפורשת.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from ..config import PATHS, AppSettings
from ..errors import (
    FFmpegFailedError,
    LiveNotStartedError,
    PolixorError,
    PrivateOrUnavailableError,
)
from ..util.ffmpeg import find_ffmpeg, find_ffprobe, probe
from .ingest import _base_ydl_opts, classify_download_error, resolve_url

log = logging.getLogger("polixor.live")

# אורך מקטע ברירת מחדל. קצר מדי = יותר תפרים; ארוך מדי = יותר
# חומר בסיכון אם התהליך נופל באמצע מקטע.
SEGMENT_SECONDS = 300.0
MIN_SEGMENT_SECONDS = 10.0

# חיבור מחדש: השהיה עולה, עם תקרה, ומספר ניסיונות מוגבל ברצף.
RECONNECT_BACKOFF = (2.0, 5.0, 10.0, 20.0, 30.0)
MAX_CONSECUTIVE_FAILURES = 6

# מקטע שנסגר מהר מדי אינו נחשב הצלחה – סימן שהזרם נפל מיד
MIN_USABLE_SEGMENT = 1.5


class LiveUnavailableError(PolixorError):
    code = "live_not_started"
    key = "live_unavailable"
    message = "השידור אינו זמין כרגע."
    hint = "ודא שהשידור באוויר ושהקישור ציבורי."


class LiveInvalidStateError(PolixorError):
    code = "live_invalid_state"
    message = "הפעולה אינה אפשרית במצב הנוכחי של ההקלטה."


# --------------------------------------------------------------------------
# זיהוי שידור
# --------------------------------------------------------------------------
@dataclass
class StreamInfo:
    """מה שידוע על הזרם לפני שמתחילים להקליט."""

    url: str = ""
    platform: str = ""
    kind: str = ""
    title: str = ""
    uploader: str = ""
    is_live: bool = False
    live_status: str = ""
    available: bool = False
    width: int = 0
    height: int = 0
    fps: float = 0.0
    has_audio: bool = False
    audio_checked: bool = False
    resolution_label: str = ""
    thumbnail: str = ""
    manifest_url: str = ""
    notes: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items()}
        # כתובת המניפסט אינה מוחזרת ללקוח: היא מכילה אסימוני גישה
        d.pop("manifest_url", None)
        return d


def _pick_format(info: dict[str, Any]) -> dict[str, Any]:
    """
    בוחר את הפורמט להקלטה: וידאו+אודיו יחד, ברזולוציה הגבוהה ביותר
    עד 1080p. זרם משולב עדיף – אין צורך למזג בזמן אמת.
    """
    formats = [f for f in (info.get("formats") or [])
               if f.get("url") and f.get("protocol") not in ("mhtml",)]
    if not formats:
        return {"url": info.get("url") or "",
                "width": info.get("width") or 0,
                "height": info.get("height") or 0,
                "fps": info.get("fps") or 0,
                "acodec": info.get("acodec") or "",
                "vcodec": info.get("vcodec") or ""}

    def score(f: dict[str, Any]) -> tuple[int, int, int]:
        has_v = f.get("vcodec") not in (None, "none")
        has_a = f.get("acodec") not in (None, "none")
        height = int(f.get("height") or 0)
        capped = height if height <= 1080 else 1080 - (height - 1080)
        return (int(has_v and has_a), int(has_v), capped)

    return max(formats, key=score)


def detect_stream(url: str, settings: AppSettings, *,
                  probe_media: bool = True) -> StreamInfo:
    """
    בודק קישור שידור חי ומחזיר מה שניתן לדעת עליו לפני הקלטה.

    `probe_media` מפעיל ffprobe קצר על הזרם עצמו כדי לאמת רזולוציה
    ונוכחות אודיו בפועל – ולא רק לפי מה שהמטא-דאטה מצהיר.
    """
    resolved = resolve_url(url)
    info_obj = StreamInfo(
        url=resolved.normalized_url or url,
        platform=resolved.platform_label,
        kind=resolved.kind.value,
        notes=list(resolved.notes),
    )

    try:
        import yt_dlp
    except ImportError as exc:
        raise PolixorError("yt-dlp אינו מותקן.",
                           hint="הרץ: pip install -r requirements.txt",
                           detail=str(exc)) from exc

    opts = _base_ydl_opts(settings)
    opts.update({"skip_download": True, "quiet": True, "no_warnings": True})

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(info_obj.url, download=False)
    except Exception as exc:
        err = classify_download_error(str(exc))
        info_obj.available = False
        info_obj.reason = err.message
        return info_obj

    if info is None:
        info_obj.reason = "לא התקבל מידע על הקישור."
        return info_obj
    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            info_obj.reason = "הקישור אינו מצביע על שידור."
            return info_obj
        info = entries[0]

    info_obj.title = info.get("title") or ""
    info_obj.uploader = info.get("uploader") or info.get("channel") or ""
    info_obj.live_status = info.get("live_status") or ""
    info_obj.is_live = bool(info.get("is_live")
                            or info_obj.live_status == "is_live")
    info_obj.thumbnail = info.get("thumbnail") or ""

    fmt = _pick_format(info)
    info_obj.manifest_url = fmt.get("url") or ""
    info_obj.width = int(fmt.get("width") or info.get("width") or 0)
    info_obj.height = int(fmt.get("height") or info.get("height") or 0)
    info_obj.fps = float(fmt.get("fps") or info.get("fps") or 0.0)
    info_obj.has_audio = fmt.get("acodec") not in (None, "none", "")

    if not info_obj.is_live:
        status = info_obj.live_status
        if status in ("is_upcoming", "not_live"):
            info_obj.reason = ("השידור טרם התחיל." if status == "is_upcoming"
                               else "הקישור אינו שידור חי פעיל.")
        else:
            info_obj.reason = "הקישור אינו שידור חי פעיל."
        info_obj.available = False
    else:
        info_obj.available = bool(info_obj.manifest_url)
        if not info_obj.available:
            info_obj.reason = "לא נמצא זרם שניתן לקרוא ממנו."

    if probe_media and info_obj.manifest_url:
        _probe_live_media(info_obj)

    if info_obj.width and info_obj.height:
        info_obj.resolution_label = f"{info_obj.width}×{info_obj.height}"
        if info_obj.fps:
            info_obj.resolution_label += f" · {info_obj.fps:.0f}fps"
    return info_obj


def _probe_live_media(info_obj: StreamInfo, timeout: float = 20.0) -> None:
    """ffprobe קצר על הזרם: מאמת רזולוציה ונוכחות אודיו בפועל."""
    exe = find_ffprobe()
    if not exe:
        info_obj.notes.append("ffprobe אינו זמין – פרטי הזרם לפי המטא-דאטה בלבד.")
        return
    cmd = [exe, "-v", "error", "-hide_banner",
           "-show_entries", "stream=codec_type,width,height,avg_frame_rate",
           "-of", "default=noprint_wrappers=1", "-i", info_obj.manifest_url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as exc:
        info_obj.notes.append(f"בדיקת הזרם לא הושלמה: {exc}")
        return
    if r.returncode != 0:
        info_obj.notes.append("לא ניתן היה לקרוא מהזרם לצורך אימות.")
        return

    out = r.stdout or ""
    info_obj.audio_checked = True
    info_obj.has_audio = "codec_type=audio" in out
    for line in out.splitlines():
        if line.startswith("width=") and not info_obj.width:
            info_obj.width = int(line.split("=", 1)[1] or 0)
        elif line.startswith("height=") and not info_obj.height:
            info_obj.height = int(line.split("=", 1)[1] or 0)


def resolve_manifest(url: str, settings: AppSettings) -> str:
    """
    פותר את כתובת הזרם מחדש. נקרא לפני כל מקטע, כי כתובות
    מניפסט פגות תוקף – וזו גם הדרך להתאושש מנפילה.
    """
    try:
        import yt_dlp
    except ImportError as exc:
        raise PolixorError("yt-dlp אינו מותקן.", detail=str(exc)) from exc

    opts = _base_ydl_opts(settings)
    opts.update({"skip_download": True, "quiet": True, "no_warnings": True})
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        raise classify_download_error(str(exc)) from exc
    if info is None:
        raise LiveUnavailableError()
    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise LiveUnavailableError()
        info = entries[0]
    if not (info.get("is_live") or info.get("live_status") == "is_live"):
        raise LiveNotStartedError("השידור אינו פעיל כרגע.")
    manifest = _pick_format(info).get("url") or ""
    if not manifest:
        raise LiveUnavailableError("לא נמצא זרם שניתן לקרוא ממנו.")
    return manifest


# --------------------------------------------------------------------------
# הקלטת מקטע בודד
# --------------------------------------------------------------------------
@dataclass
class SegmentResult:
    path: Path
    seconds: float
    complete: bool          # True => המקטע רץ עד סוף הזמן שהוקצב
    returncode: int = 0
    error: str = ""


def record_segment(manifest_url: str, dest: Path, *, seconds: float,
                   cancel_event: Optional[threading.Event] = None,
                   stop_event: Optional[threading.Event] = None,
                   on_tick: Optional[Callable[[float, int], None]] = None,
                   ffmpeg: Optional[str] = None) -> SegmentResult:
    """
    מקליט מקטע אחד מהזרם לקובץ mpegts, בהעתקת זרם ללא קידוד מחדש.

    עצירה יזומה נשלחת כ-'q' ל-stdin של FFmpeg, שסוגר את הקובץ בצורה
    תקינה במקום להישבר באמצע.

    `on_tick(elapsed, bytes_written)` נקרא כל חצי שנייה. מספר הבתים
    הוא מה שמאפשר לקורא לדעת שהזרם באמת זורם – ולא רק שהתהליך רץ.
    """
    exe = ffmpeg or find_ffmpeg()
    if not exe:
        raise FFmpegFailedError("FFmpeg אינו זמין להקלטת שידור.")

    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        exe, "-hide_banner", "-loglevel", "error", "-y",
        # חיבור מחדש ברמת HTTP – מכסה הפרעות קצרות בלי לסגור את המקטע
        "-reconnect", "1", "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
        "-rw_timeout", "15000000",
        "-i", manifest_url,
        "-t", f"{max(MIN_SEGMENT_SECONDS, seconds):.2f}",
        "-c", "copy", "-f", "mpegts",
        str(dest),
    ]

    started = time.time()
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, text=True)
    stopped_early = False
    try:
        while True:
            if proc.poll() is not None:
                break
            if cancel_event is not None and cancel_event.is_set():
                stopped_early = True
                _graceful_stop(proc)
                break
            if stop_event is not None and stop_event.is_set():
                stopped_early = True
                _graceful_stop(proc)
                break
            if on_tick:
                try:
                    written = dest.stat().st_size if dest.exists() else 0
                except OSError:
                    written = 0
                on_tick(time.time() - started, written)
            time.sleep(0.5)
        stderr = ""
        try:
            _, stderr = proc.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            stderr = "FFmpeg לא הגיב ונסגר בכפייה."
    finally:
        if proc.poll() is None:
            proc.kill()

    elapsed = time.time() - started
    actual = _duration_of(dest)
    seconds_recorded = actual if actual > 0 else (elapsed if dest.exists() else 0.0)
    rc = proc.returncode or 0
    # יציאה לא אפסית אחרי עצירה יזומה היא צפויה ואינה שגיאה
    complete = (not stopped_early) and rc == 0 and seconds_recorded >= seconds * 0.6

    return SegmentResult(
        path=dest, seconds=round(seconds_recorded, 2), complete=complete,
        returncode=rc,
        error="" if (rc == 0 or stopped_early) else _friendly_error(stderr),
    )


# תרגום שגיאות FFmpeg נפוצות למשפט אחד שמתאים להצגה למשתמש.
# הפלט הגולמי נשמר ביומן, אבל לא מוצג בממשק.
_ERROR_PATTERNS: tuple[tuple[str, str], ...] = (
    ("404", "הזרם אינו זמין עוד (404). ייתכן שהשידור הסתיים."),
    ("403", "הגישה לזרם נדחתה (403). ייתכן שכתובת הזרם פגה."),
    ("Connection refused", "החיבור לשרת השידור נדחה."),
    ("Connection reset", "החיבור לשרת השידור נותק."),
    ("Connection timed out", "פג הזמן בהמתנה לשרת השידור."),
    ("Operation timed out", "פג הזמן בהמתנה לנתונים מהזרם."),
    ("Name or service not known", "לא ניתן לאתר את שרת השידור."),
    ("Server returned 5", "שרת השידור החזיר שגיאה זמנית."),
    ("Invalid data found", "התקבלו נתונים פגומים מהזרם."),
    ("No space left", "נגמר המקום בדיסק בזמן ההקלטה."),
    ("End of file", "הזרם נגמר."),
)


def _friendly_error(stderr: str) -> str:
    raw = (stderr or "").strip()
    if not raw:
        return "החיבור לזרם נפל."
    log.debug("ffmpeg stderr: %s", raw[:800])
    for needle, message in _ERROR_PATTERNS:
        if needle.lower() in raw.lower():
            return message
    first = next((ln.strip() for ln in raw.splitlines() if ln.strip()), "")
    return f"ההקלטה נקטעה: {first[:120]}" if first else "החיבור לזרם נפל."


def _graceful_stop(proc: subprocess.Popen) -> None:
    """מבקש מ-FFmpeg לסיים ולסגור את הקובץ, ורק אז מפעיל כוח."""
    try:
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.write("q")
            proc.stdin.flush()
    except (OSError, ValueError):
        pass
    try:
        proc.wait(timeout=8)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        proc.terminate()
        proc.wait(timeout=6)
    except subprocess.TimeoutExpired:
        proc.kill()


def _duration_of(path: Path) -> float:
    if not path.exists() or path.stat().st_size < 1024:
        return 0.0
    try:
        return float(probe(path).duration)
    except Exception:
        return 0.0


# --------------------------------------------------------------------------
# איחוד מקטעים למקור אחד
# --------------------------------------------------------------------------
def concat_segments(paths: list[Path], dest: Path) -> Path:
    """
    מאחד מקטעים שהוקלטו לקובץ MP4 אחד, בהעתקת זרם.

    מקטע פגום מדולג ולא מפיל את האיחוד: עדיף להציל את מה שהוקלט
    מאשר לאבד את כל ההקלטה בגלל מקטע אחד שנקטע.
    """
    usable = [p for p in paths
              if p.exists() and p.stat().st_size > 2048 and _duration_of(p) > 0.05]
    if not usable:
        raise LiveUnavailableError(
            "לא נאסף חומר שניתן לעבד מההקלטה.",
            hint="ייתכן שהשידור הסתיים לפני שהצטבר חומר.")

    exe = find_ffmpeg()
    if not exe:
        raise FFmpegFailedError("FFmpeg אינו זמין לאיחוד ההקלטה.")

    dest.parent.mkdir(parents=True, exist_ok=True)
    if len(usable) == 1:
        cmd = [exe, "-hide_banner", "-loglevel", "error", "-y",
               "-i", str(usable[0]), "-c", "copy",
               "-movflags", "+faststart", str(dest)]
    else:
        listfile = dest.with_suffix(".concat.txt")
        listfile.write_text(
            "\n".join(f"file '{p.as_posix()}'" for p in usable), "utf-8")
        cmd = [exe, "-hide_banner", "-loglevel", "error", "-y",
               "-f", "concat", "-safe", "0", "-i", str(listfile),
               "-c", "copy", "-movflags", "+faststart", str(dest)]

    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not dest.exists():
        # נפילה חוזרת עם קידוד מחדש: איטי יותר, אבל מציל מקטעים
        # שפרמטרי הקידוד שלהם השתנו באמצע השידור.
        log.warning("concat copy failed, re-encoding: %s", (r.stderr or "")[:300])
        cmd_re = list(cmd)
        idx = cmd_re.index("-c")
        cmd_re[idx:idx + 2] = ["-c:v", "libx264", "-preset", "veryfast",
                               "-crf", "20", "-c:a", "aac", "-b:a", "160k"]
        r2 = subprocess.run(cmd_re, capture_output=True, text=True)
        if r2.returncode != 0 or not dest.exists():
            raise FFmpegFailedError(
                "איחוד מקטעי ההקלטה נכשל.",
                detail=(r2.stderr or r.stderr or "")[:500])
    return dest


def total_recorded_seconds(segments: list[dict[str, Any]]) -> float:
    return round(sum(float(s.get("seconds") or 0.0) for s in segments), 2)


def cleanup_segments(paths: list[Path]) -> int:
    removed = 0
    for p in paths:
        try:
            if p.exists():
                p.unlink()
                removed += 1
        except OSError:
            pass
    return removed


def free_space_for_live(minutes: float, height: int = 1080) -> int:
    """
    הערכת שטח דיסק נדרש להקלטה, לפי קצב סיביות טיפוסי לרזולוציה.
    משמש לאזהרה מראש, לא לחסימה.
    """
    mbps = 6.0 if height >= 1080 else (3.0 if height >= 720 else 1.5)
    return int(minutes * 60 * mbps * 1024 ** 2 / 8)


def have_ffmpeg() -> bool:
    return bool(find_ffmpeg()) and bool(find_ffprobe())
