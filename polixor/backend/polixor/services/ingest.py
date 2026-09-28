"""
ייבוא וידאו: זיהוי פלטפורמה, הורדה עם התקדמות אמיתית, וקבצים מקומיים.

מדיניות: משתמשים ב-yt-dlp רק למקורות ציבוריים שיש הרשאה להורידם.
אין כאן שום מנגנון לעקיפת DRM, הגבלות גיל/אזור, או תוכן בתשלום –
אם המקור חסום, המערכת מדווחת על כך ומציעה להעלות קובץ ידנית.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlparse

from ..config import PATHS, SECRETS, AppSettings
from ..errors import (
    DiskSpaceError,
    InvalidUrlError,
    JobCancelledError,
    LiveNotStartedError,
    PolixorError,
    UnsupportedPlatformError,
    classify_download_error,
)
from ..models import SourceKind
from ..util.fs import human_size, require_free_space, safe_filename

log = logging.getLogger("polixor.ingest")

ProgressFn = Callable[[float, str], None]


# --------------------------------------------------------------------------
# זיהוי פלטפורמה
# --------------------------------------------------------------------------
@dataclass
class ResolvedSource:
    kind: SourceKind
    url: str
    normalized_url: str = ""
    platform_label: str = ""
    is_live: bool = False
    notes: list[str] = field(default_factory=list)
    gdrive_id: str = ""


_YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be",
             "music.youtube.com", "www.youtube-nocookie.com"}
_TWITCH_HOSTS = {"twitch.tv", "www.twitch.tv", "m.twitch.tv", "clips.twitch.tv"}
_KICK_HOSTS = {"kick.com", "www.kick.com"}
_GDRIVE_HOSTS = {"drive.google.com", "docs.google.com", "drive.usercontent.google.com"}

_VIDEO_EXT = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".ts", ".m4v", ".flv"}


def resolve_url(raw: str) -> ResolvedSource:
    """מזהה את הפלטפורמה מהקישור. זורק InvalidUrlError אם לא ניתן לפענח."""
    url = (raw or "").strip()
    if not url:
        raise InvalidUrlError("לא הוזן קישור.")
    if not re.match(r"^https?://", url, re.I):
        # מרשים הדבקה בלי סכימה
        if re.match(r"^[\w.-]+\.[a-z]{2,}/", url, re.I):
            url = "https://" + url
        else:
            raise InvalidUrlError()

    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise InvalidUrlError(detail=str(exc)) from exc

    host = (parsed.hostname or "").lower()
    path = parsed.path or ""

    # --- YouTube ---
    if host in _YT_HOSTS:
        is_live = "/live/" in path or parsed.query.find("live") >= 0
        # /live/<id> הוא תמיד שידור חי או VOD של שידור
        if path.startswith("/live/"):
            is_live = True
        return ResolvedSource(
            kind=SourceKind.YOUTUBE_LIVE if is_live else SourceKind.YOUTUBE_VOD,
            url=url, normalized_url=url, platform_label="YouTube", is_live=is_live,
        )

    # --- Twitch ---
    if host in _TWITCH_HOSTS:
        if "/videos/" in path or path.startswith("/videos/"):
            kind, live = SourceKind.TWITCH_VOD, False
        elif "/clip/" in path or host == "clips.twitch.tv":
            kind, live = SourceKind.TWITCH_VOD, False
        else:
            # twitch.tv/<channel> => ערוץ, כנראה שידור חי
            segs = [s for s in path.split("/") if s]
            kind = SourceKind.TWITCH_LIVE if len(segs) == 1 else SourceKind.TWITCH_VOD
            live = kind == SourceKind.TWITCH_LIVE
        return ResolvedSource(kind=kind, url=url, normalized_url=url,
                              platform_label="Twitch", is_live=live)

    # --- Kick ---
    if host in _KICK_HOSTS:
        segs = [s for s in path.split("/") if s]
        if segs and segs[0] in ("video", "videos"):
            kind, live = SourceKind.KICK_VOD, False
        elif len(segs) >= 2 and segs[1] == "clips":
            kind, live = SourceKind.KICK_VOD, False
        else:
            kind, live = SourceKind.KICK_LIVE, True
        return ResolvedSource(
            kind=kind, url=url, normalized_url=url, platform_label="Kick", is_live=live,
            notes=["תמיכת Kick ב-yt-dlp משתנה עם שינויים באתר. "
                   "אם ההורדה נכשלת, ניתן להוריד ידנית ולהעלות את הקובץ."],
        )

    # --- Google Drive ---
    if host in _GDRIVE_HOSTS:
        file_id = _extract_drive_id(url, parsed)
        if not file_id:
            raise InvalidUrlError(
                "לא זוהה מזהה קובץ בקישור של Google Drive.",
                hint="השתמש בקישור מסוג https://drive.google.com/file/d/<ID>/view",
            )
        return ResolvedSource(
            kind=SourceKind.GDRIVE, url=url,
            normalized_url=f"https://drive.google.com/file/d/{file_id}/view",
            platform_label="Google Drive", gdrive_id=file_id,
            notes=["נדרש שיתוף המאפשר הורדה ('כל מי שיש לו הקישור'). "
                   "קבצים פרטיים דורשים חיבור חשבון."],
        )

    # --- קישור ישיר לקובץ ---
    if Path(path).suffix.lower() in _VIDEO_EXT:
        return ResolvedSource(kind=SourceKind.DIRECT_URL, url=url, normalized_url=url,
                              platform_label="קישור ישיר")

    # yt-dlp תומך בעוד מאות אתרים; ננסה בכל זאת, ונדווח אם לא נתמך
    return ResolvedSource(
        kind=SourceKind.UNKNOWN, url=url, normalized_url=url, platform_label=host or "לא ידוע",
        notes=["הפלטפורמה אינה מזוהה במפורש. התוכנה תנסה לייבא דרך yt-dlp."],
    )


def _extract_drive_id(url: str, parsed) -> str:
    m = re.search(r"/file/d/([A-Za-z0-9_-]{10,})", url)
    if m:
        return m.group(1)
    m = re.search(r"/d/([A-Za-z0-9_-]{10,})", url)
    if m:
        return m.group(1)
    qs = parse_qs(parsed.query or "")
    for key in ("id", "docid"):
        if qs.get(key):
            return qs[key][0]
    return ""


# --------------------------------------------------------------------------
# מידע מקדים (בלי להוריד)
# --------------------------------------------------------------------------
def probe_remote(url: str, settings: AppSettings) -> dict[str, Any]:
    """
    שולף מטא-דאטה בלבד (כותרת, אורך, האם חי) כדי להציג למשתמש לפני ההורדה.
    לא מוריד מדיה.
    """
    resolved = resolve_url(url)
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
            info = ydl.extract_info(resolved.normalized_url or url, download=False)
    except Exception as exc:  # yt_dlp.utils.DownloadError ועוד
        raise classify_download_error(str(exc)) from exc

    if info is None:
        raise classify_download_error("no info")
    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise classify_download_error("empty playlist")
        info = entries[0]

    return {
        "platform": resolved.platform_label,
        "kind": resolved.kind.value,
        "title": info.get("title") or "",
        "uploader": info.get("uploader") or info.get("channel") or "",
        "duration": float(info.get("duration") or 0.0),
        "is_live": bool(info.get("is_live") or info.get("live_status") == "is_live"),
        "live_status": info.get("live_status") or "",
        "thumbnail": info.get("thumbnail") or "",
        "filesize_approx": int(info.get("filesize_approx") or 0),
        "notes": resolved.notes,
        "webpage_url": info.get("webpage_url") or url,
    }


# --------------------------------------------------------------------------
# הורדה
# --------------------------------------------------------------------------
@dataclass
class DownloadResult:
    path: Path
    title: str
    uploader: str
    duration: float
    filesize: int
    is_live: bool
    kind: SourceKind
    extra: dict[str, Any] = field(default_factory=dict)


def _base_ydl_opts(settings: AppSettings) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "noplaylist": True,
        "nocheckcertificate": False,
        "retries": 5,
        "fragment_retries": 10,
        "socket_timeout": 30,
        "concurrent_fragment_downloads": 3,
        "http_headers": {"User-Agent": "Mozilla/5.0 (compatible; Polixor/1.0)"},
        # אין allow_unplayable_formats: תוכן מוגן DRM לא מעובד, בכוונה.
        "allow_unplayable_formats": False,
        "ignore_no_formats_error": False,
    }
    cookiefile = SECRETS.get("cookiefile_path")
    if cookiefile and Path(cookiefile).exists():
        # קובץ עוגיות שהמשתמש סיפק במודע, לחשבון שלו. לא נוגעים בדפדפן.
        opts["cookiefile"] = cookiefile
    return opts


def _format_selector(settings: AppSettings) -> str:
    """בוחר את הפורמט הטוב ביותר שלא יעלה על צורכי הייצוא."""
    target_h = 1080
    try:
        target_h = int(settings.long_resolution.split("x")[1])
    except (IndexError, ValueError):
        pass
    cap = max(target_h, 1080)
    return (
        f"bestvideo[height<={cap}][ext=mp4]+bestaudio[ext=m4a]/"
        f"bestvideo[height<={cap}]+bestaudio/"
        f"best[height<={cap}]/best"
    )


def download(
    url: str,
    *,
    settings: AppSettings,
    dest_dir: Optional[Path] = None,
    on_progress: Optional[ProgressFn] = None,
    cancel_event: Optional[threading.Event] = None,
    live_duration: Optional[float] = None,
) -> DownloadResult:
    """
    מוריד וידאו עם דיווח התקדמות אמיתי (אחוזים + מהירות + גודל).
    `live_duration`: אם מוגדר – מוריד רק N שניות משידור חי (מצב לייב).
    """
    resolved = resolve_url(url)
    dest = Path(dest_dir) if dest_dir else PATHS.sources
    dest.mkdir(parents=True, exist_ok=True)

    try:
        import yt_dlp
    except ImportError as exc:
        raise PolixorError("yt-dlp אינו מותקן.",
                           hint="הרץ: pip install -r requirements.txt",
                           detail=str(exc)) from exc

    state: dict[str, Any] = {"last_emit": 0.0, "final_path": None}

    def hook(d: dict[str, Any]) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise JobCancelledError()
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            got = d.get("downloaded_bytes") or 0
            frac = (got / total) if total else 0.0
            now = time.time()
            if now - state["last_emit"] < 0.25 and frac < 1.0:
                return
            state["last_emit"] = now
            speed = d.get("speed") or 0
            eta = d.get("eta")
            parts = [f"{human_size(got)}"]
            if total:
                parts.append(f"מתוך {human_size(total)}")
            if speed:
                parts.append(f"· {human_size(speed)}/ש'")
            if eta:
                parts.append(f"· נותרו ~{int(eta)} שנ'")
            if on_progress:
                on_progress(min(0.99, frac), " ".join(parts))
        elif status == "finished":
            state["final_path"] = d.get("filename")
            if on_progress:
                on_progress(1.0, "ההורדה הושלמה, ממזג זרמים…")

    outtmpl = str(dest / "%(id)s_%(title).60B.%(ext)s")
    opts = _base_ydl_opts(settings)
    opts.update({
        "outtmpl": outtmpl,
        "format": _format_selector(settings),
        "merge_output_format": "mp4",
        "progress_hooks": [hook],
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "windowsfilenames": True,
        "trim_file_name": 120,
    })

    if live_duration and live_duration > 0:
        # הורדת חלון זמן משידור חי (yt-dlp תומך ב-live_from_start + טווח)
        opts["live_from_start"] = False
        opts["download_ranges"] = _last_seconds_range(live_duration)
        opts["force_keyframes_at_cuts"] = False

    # בדיקת מקום פנוי לפני שמתחילים
    require_free_space(dest, 2 * 1024 ** 3)   # 2GB מינימום שמרני

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(resolved.normalized_url or url, download=True)
    except JobCancelledError:
        raise
    except Exception as exc:
        msg = str(exc)
        if "No space left" in msg:
            raise DiskSpaceError(detail=msg) from exc
        raise classify_download_error(msg) from exc

    if info is None:
        raise classify_download_error("no info returned")
    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise classify_download_error("empty playlist")
        info = entries[0]

    path = _resolve_downloaded_path(info, state.get("final_path"), dest)
    if path is None or not path.exists():
        raise PolixorError("קובץ הווידאו לא נמצא לאחר ההורדה.",
                           hint="נסה שוב, או הורד ידנית והעלה את הקובץ.")

    return DownloadResult(
        path=path,
        title=info.get("title") or path.stem,
        uploader=info.get("uploader") or info.get("channel") or "",
        duration=float(info.get("duration") or 0.0),
        filesize=path.stat().st_size,
        is_live=bool(info.get("is_live")),
        kind=resolved.kind,
        extra={
            "id": info.get("id"),
            "webpage_url": info.get("webpage_url"),
            "extractor": info.get("extractor_key"),
            "width": info.get("width"),
            "height": info.get("height"),
            "fps": info.get("fps"),
        },
    )


def _last_seconds_range(seconds: float):
    """טווח הורדה: N השניות האחרונות הזמינות (למצב לייב)."""
    def _ranges(info_dict, _ydl):
        dur = float(info_dict.get("duration") or 0.0)
        if dur <= 0:
            return [{"start_time": 0, "end_time": seconds}]
        start = max(0.0, dur - seconds)
        return [{"start_time": start, "end_time": dur}]
    return _ranges


def _resolve_downloaded_path(info: dict[str, Any], hook_path: Optional[str],
                             dest: Path) -> Optional[Path]:
    """מאתר את הקובץ הסופי גם אחרי מיזוג זרמים (ששינה סיומת)."""
    candidates: list[Path] = []
    rd = info.get("requested_downloads") or []
    for item in rd:
        for key in ("filepath", "_filename", "filename"):
            if item.get(key):
                candidates.append(Path(item[key]))
    for key in ("filepath", "_filename", "filename"):
        if info.get(key):
            candidates.append(Path(info[key]))
    if hook_path:
        candidates.append(Path(hook_path))

    for c in candidates:
        if c.exists():
            return c
        # מיזוג משנה סיומת (.f137.mp4 → .mp4)
        merged = c.with_suffix(".mp4")
        if merged.exists():
            return merged

    vid = info.get("id")
    if vid:
        matches = sorted(dest.glob(f"{vid}*"),
                         key=lambda p: p.stat().st_mtime if p.exists() else 0,
                         reverse=True)
        vids = [m for m in matches if m.suffix.lower() in _VIDEO_EXT]
        if vids:
            return vids[0]
    return None


# --------------------------------------------------------------------------
# קבצים מקומיים
# --------------------------------------------------------------------------
ALLOWED_UPLOAD_EXT = {".mp4", ".mkv", ".mov", ".webm", ".m4v", ".avi", ".ts"}


def register_local_file(src: Path, *, copy: bool = False) -> DownloadResult:
    """
    רושם קובץ שהמשתמש העלה או בחר מהמחשב.
    מוודא סיומת מותרת ומריץ ffprobe כדי לאמת שהקובץ אמיתי.
    """
    from ..util.ffmpeg import probe

    src = Path(src)
    if not src.exists():
        raise PolixorError("הקובץ לא נמצא.", hint=f"נתיב: {src}")
    if src.suffix.lower() not in ALLOWED_UPLOAD_EXT:
        raise UnsupportedPlatformError(
            f"סוג הקובץ {src.suffix} אינו נתמך.",
            hint="פורמטים נתמכים: " + ", ".join(sorted(ALLOWED_UPLOAD_EXT)),
        )

    target = src
    if copy:
        PATHS.sources.mkdir(parents=True, exist_ok=True)
        target = PATHS.sources / safe_filename(src.name, max_length=120)
        if target.resolve() != src.resolve():
            require_free_space(PATHS.sources, src.stat().st_size + 256 * 1024 ** 2)
            import shutil as _sh
            _sh.copy2(src, target)

    info = probe(target)
    return DownloadResult(
        path=target,
        title=src.stem,
        uploader="",
        duration=info.duration,
        filesize=info.size_bytes or target.stat().st_size,
        is_live=False,
        kind=SourceKind.UPLOAD,
        extra={"width": info.width, "height": info.height, "fps": info.fps},
    )
