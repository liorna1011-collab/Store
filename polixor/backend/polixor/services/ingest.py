"""
ייבוא וידאו: זיהוי פלטפורמה, הורדה עם התקדמות אמיתית, וקבצים מקומיים.

מדיניות: משתמשים ב-yt-dlp רק למקורות ציבוריים שיש הרשאה להורידם.
אין כאן שום מנגנון לעקיפת DRM, הגבלות גיל/אזור, או תוכן בתשלום –
אם המקור חסום, המערכת מדווחת על כך ומציעה להעלות קובץ ידנית.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlparse

from ..config import PATHS, SECRETS, AppSettings
from ..errors import (
    BlockedAddressError,
    DiskSpaceError,
    InvalidSectionError,
    InvalidUrlError,
    JobCancelledError,
    LiveNotStartedError,
    LiveRequiresCaptureError,
    NotAVideoError,
    PlaylistNotSupportedError,
    PolixorError,
    SourceTooLongError,
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
    platform: str = ""             # youtube | twitch | kick | gdrive | direct | other
    content: str = "video"         # video | live | clip | short
    video_id: str = ""
    start_hint: Optional[float] = None
    # True כשהקישור עצמו מעיד על שידור חי (ערוץ). קישור /live/<id> של
    # YouTube יכול להיות גם שידור שהסתיים – ההכרעה אז מהבדיקה המרוחקת.
    live_certain: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "platform": self.platform,
                "platform_label": self.platform_label, "is_live": self.is_live,
                "live_certain": self.live_certain, "content": self.content,
                "normalized_url": self.normalized_url or self.url,
                "video_id": self.video_id, "start_hint": self.start_hint,
                "notes": list(self.notes)}


_YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be",
             "music.youtube.com", "www.youtube-nocookie.com", "youtube-nocookie.com"}
_TWITCH_HOSTS = {"twitch.tv", "www.twitch.tv", "m.twitch.tv", "clips.twitch.tv",
                 "player.twitch.tv"}
_KICK_HOSTS = {"kick.com", "www.kick.com", "m.kick.com"}
_GDRIVE_HOSTS = {"drive.google.com", "docs.google.com", "drive.usercontent.google.com"}

_VIDEO_EXT = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".ts", ".m4v", ".flv", ".m3u8"}

MAX_URL_LENGTH = 2048
# תווים בלתי נראים שמגיעים בהעתקה מאפליקציות (רווח ברוחב אפס, סימוני כיווניות)
_INVISIBLE = re.compile("[​-‏‪-‮⁠-⁩﻿­]")
_YT_ID = re.compile(r"^[A-Za-z0-9_-]{6,20}$")

# נתיבים של Twitch/Kick שהם דפי רשימה או הגדרות ולא ערוץ
_TWITCH_RESERVED = {"directory", "search", "settings", "subscriptions", "inventory",
                    "wallet", "drops", "downloads", "jobs", "p", "turbo", "friends",
                    "messages", "u", "prime", "store", "login", "signup"}
_KICK_RESERVED = {"categories", "category", "following", "browse", "search",
                  "dashboard", "settings", "login", "signup", "subscriptions"}


def _clean_input(raw: str) -> str:
    url = _INVISIBLE.sub("", raw or "")
    return url.strip().strip("<>\"'")


def _ytt(key: str, **params: Any) -> str:
    from .. import i18n

    return i18n.tr(f"ingest.{key}", **params)


def resolve_url(raw: str) -> ResolvedSource:
    """
    מזהה את הפלטפורמה ואת סוג התוכן מהקישור, בלי גישה לרשת.

    זורק InvalidUrlError לקישור לא תקין, NotAVideoError לדף שאינו סרטון
    (ערוץ, רשימה, חיפוש) ו-PlaylistNotSupportedError לרשימת השמעה.
    """
    url = _clean_input(raw)
    if not url:
        raise InvalidUrlError(message_key="errors.url_empty.message")
    if len(url) > MAX_URL_LENGTH:
        raise InvalidUrlError(message_key="errors.url_too_long.message")
    if re.search(r"\s", url):
        raise InvalidUrlError()
    if not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I):
        # מרשים הדבקה בלי סכימה ("youtube.com/watch?v=...")
        if re.match(r"^[\w.-]+\.[a-z]{2,}(:\d+)?/", url, re.I):
            url = "https://" + url
        else:
            raise InvalidUrlError()
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise InvalidUrlError(detail=str(exc)) from exc
    if parsed.scheme.lower() not in ("http", "https"):
        raise InvalidUrlError(message_key="errors.url_bad_scheme.message")
    if parsed.username or parsed.password:
        raise InvalidUrlError(message_key="errors.url_credentials.message",
                              hint_key="errors.url_credentials.hint")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise InvalidUrlError()
    path = parsed.path or ""
    qs = parse_qs(parsed.query or "")

    if host in _YT_HOSTS:
        return _resolve_youtube(url, host, path, qs)
    if host in _TWITCH_HOSTS:
        return _resolve_twitch(url, host, path, qs)
    if host in _KICK_HOSTS:
        return _resolve_kick(url, path, qs)

    # --- Google Drive ---
    if host in _GDRIVE_HOSTS:
        file_id = _extract_drive_id(url, parsed)
        if not file_id:
            raise InvalidUrlError(message_key="errors.gdrive_no_id.message",
                                  hint_key="errors.gdrive_no_id.hint")
        return ResolvedSource(
            kind=SourceKind.GDRIVE, url=url,
            normalized_url=f"https://drive.google.com/file/d/{file_id}/view",
            platform_label="Google Drive", platform="gdrive", gdrive_id=file_id,
            video_id=file_id, notes=[_ytt("gdrive_note")])

    # --- קישור ישיר לקובץ ---
    if Path(path).suffix.lower() in _VIDEO_EXT:
        return ResolvedSource(kind=SourceKind.DIRECT_URL, url=url, normalized_url=url,
                              platform_label=_ytt("direct_label"), platform="direct")

    # yt-dlp תומך בעוד מאות אתרים; ננסה בכל זאת, ונדווח אם לא נתמך
    return ResolvedSource(
        kind=SourceKind.UNKNOWN, url=url, normalized_url=url,
        platform_label=host or _ytt("unknown_label"), platform="other",
        notes=[_ytt("unknown_note")])


def _parse_t(value: str) -> Optional[float]:
    """t=90 / t=90s / t=1m30s / t=1h2m3s → שניות."""
    v = (value or "").strip().lower()
    if not v:
        return None
    if re.fullmatch(r"\d+(\.\d+)?s?", v):
        return float(v.rstrip("s"))
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", v)
    if m and any(m.groups()):
        h, mi, se = (int(x or 0) for x in m.groups())
        return float(h * 3600 + mi * 60 + se)
    return None


def _resolve_youtube(url: str, host: str, path: str, qs: dict) -> ResolvedSource:
    segs = [x for x in path.split("/") if x]
    start_hint = None
    for key in ("t", "start", "time_continue"):
        if qs.get(key):
            start_hint = _parse_t(qs[key][0])
            if start_hint is not None:
                break
    if start_hint is None and "#t=" in url:
        start_hint = _parse_t(url.split("#t=", 1)[1])

    def video(vid: str, content: str = "video", *, live: bool = False,
              certain: bool = False, normalized: Optional[str] = None) -> ResolvedSource:
        if not _YT_ID.match(vid):
            raise InvalidUrlError()
        norm = normalized or f"https://www.youtube.com/watch?v={vid}"
        return ResolvedSource(
            kind=SourceKind.YOUTUBE_LIVE if live else SourceKind.YOUTUBE_VOD,
            url=url, normalized_url=norm, platform_label="YouTube",
            platform="youtube", is_live=live, live_certain=certain,
            content="live" if live else content, video_id=vid, start_hint=start_hint)

    if host == "youtu.be":
        if not segs:
            raise NotAVideoError()
        return video(segs[0])
    if segs and segs[0] == "watch":
        vid = (qs.get("v") or [""])[0]
        if not vid:
            if qs.get("list"):
                raise PlaylistNotSupportedError()
            raise NotAVideoError()
        return video(vid)
    if segs and segs[0] == "playlist":
        raise PlaylistNotSupportedError()
    if len(segs) >= 2 and segs[0] in ("shorts", "embed", "v", "e"):
        return video(segs[1], "short" if segs[0] == "shorts" else "video")
    if len(segs) >= 2 and segs[0] == "live":
        # /live/<id>: שידור פעיל או הקלטה של שידור שהסתיים – הבדיקה
        # המרוחקת מכריעה. עד אז מתייחסים אליו כלייב.
        return video(segs[1], live=True, certain=False)
    # /@handle/live, /channel/<id>/live, /c/<name>/live, /user/<name>/live
    if segs and segs[-1] == "live" and (
            segs[0].startswith("@") or (len(segs) >= 3 and segs[0] in ("channel", "c", "user"))):
        return ResolvedSource(
            kind=SourceKind.YOUTUBE_LIVE, url=url, normalized_url=url,
            platform_label="YouTube", platform="youtube", is_live=True,
            live_certain=True, content="live", start_hint=start_hint)
    if segs and (segs[0].startswith("@") or segs[0] in ("channel", "c", "user",
                                                          "results", "feed", "hashtag")):
        raise NotAVideoError(message_key="errors.listing_page.message",
                             params={"page": "YouTube"})
    raise NotAVideoError()


def _resolve_twitch(url: str, host: str, path: str, qs: dict) -> ResolvedSource:
    segs = [x for x in path.split("/") if x]

    def make(kind: SourceKind, content: str, vid: str = "", *,
             norm: Optional[str] = None) -> ResolvedSource:
        live = kind == SourceKind.TWITCH_LIVE
        return ResolvedSource(kind=kind, url=url, normalized_url=norm or url,
                              platform_label="Twitch", platform="twitch",
                              is_live=live, live_certain=live, content=content,
                              video_id=vid)

    if host == "clips.twitch.tv":
        if not segs:
            raise NotAVideoError()
        return make(SourceKind.TWITCH_VOD, "clip", segs[0],
                    norm=f"https://clips.twitch.tv/{segs[0]}")
    if host == "player.twitch.tv":
        if qs.get("video"):
            vid = qs["video"][0].lstrip("v")
            return make(SourceKind.TWITCH_VOD, "video", vid,
                        norm=f"https://www.twitch.tv/videos/{vid}")
        if qs.get("channel"):
            ch = qs["channel"][0]
            return make(SourceKind.TWITCH_LIVE, "live", ch,
                        norm=f"https://www.twitch.tv/{ch}")
        raise NotAVideoError()
    if not segs:
        raise NotAVideoError()
    if segs[0] == "videos":
        if len(segs) >= 2 and segs[1].isdigit():
            return make(SourceKind.TWITCH_VOD, "video", segs[1],
                        norm=f"https://www.twitch.tv/videos/{segs[1]}")
        raise NotAVideoError()
    if segs[0].lower() in _TWITCH_RESERVED:
        raise NotAVideoError(message_key="errors.listing_page.message",
                             params={"page": "Twitch"})
    if len(segs) >= 3 and segs[1] == "clip":
        return make(SourceKind.TWITCH_VOD, "clip", segs[2],
                    norm=f"https://clips.twitch.tv/{segs[2]}")
    if len(segs) >= 3 and segs[1] in ("v", "video") and segs[2].isdigit():
        return make(SourceKind.TWITCH_VOD, "video", segs[2],
                    norm=f"https://www.twitch.tv/videos/{segs[2]}")
    if len(segs) == 1:
        return make(SourceKind.TWITCH_LIVE, "live", segs[0],
                    norm=f"https://www.twitch.tv/{segs[0]}")
    # /<ערוץ>/videos, /<ערוץ>/clips, /<ערוץ>/schedule – דפי רשימה
    raise NotAVideoError(message_key="errors.listing_page.message",
                         params={"page": "Twitch"})


def _resolve_kick(url: str, path: str, qs: dict) -> ResolvedSource:
    segs = [x for x in path.split("/") if x]
    note = [_ytt("kick_note")]

    def make(kind: SourceKind, content: str, vid: str = "", *,
             norm: Optional[str] = None) -> ResolvedSource:
        live = kind == SourceKind.KICK_LIVE
        return ResolvedSource(kind=kind, url=url, normalized_url=norm or url,
                              platform_label="Kick", platform="kick", is_live=live,
                              live_certain=live, content=content, video_id=vid,
                              notes=list(note))

    if not segs:
        raise NotAVideoError()
    if segs[0] in ("video", "videos"):
        if len(segs) >= 2:
            return make(SourceKind.KICK_VOD, "video", segs[1])
        raise NotAVideoError()
    if segs[0].lower() in _KICK_RESERVED:
        raise NotAVideoError(message_key="errors.listing_page.message",
                             params={"page": "Kick"})
    channel = segs[0]
    if qs.get("clip"):
        return make(SourceKind.KICK_VOD, "clip", qs["clip"][0])
    if len(segs) >= 3 and segs[1] == "videos":
        return make(SourceKind.KICK_VOD, "video", segs[2])
    if len(segs) >= 3 and segs[1] == "clips":
        return make(SourceKind.KICK_VOD, "clip", segs[2])
    if len(segs) == 1:
        return make(SourceKind.KICK_LIVE, "live", channel,
                    norm=f"https://kick.com/{channel}")
    raise NotAVideoError(message_key="errors.listing_page.message",
                         params={"page": "Kick"})


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
# הגנה מפני SSRF
# --------------------------------------------------------------------------
def _is_public_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    if ip.is_multicast or ip.is_unspecified or ip.is_loopback or ip.is_link_local:
        return False
    return bool(ip.is_global)


def check_url_allowed(resolved: ResolvedSource, settings: AppSettings) -> None:
    """
    חוסם קישורים שמפנים לכתובות פנימיות (localhost, רשת ביתית, CGNAT,
    link-local ושירות המטא-דאטה של ענן), כדי שהשרת לא ישמש לגישה לרשת
    המקומית.

    הבדיקה חלה על קישורים ישירים ועל אתרים לא מוכרים – פלטפורמות
    מוכרות (YouTube, Twitch, Kick, Drive) הן שרתים ציבוריים ידועים.
    מגבלה ידועה: הפניה (redirect) מאתר ציבורי לכתובת פנימית נבדקת
    רק בכתובת הראשונה.
    """
    if resolved.kind not in (SourceKind.UNKNOWN, SourceKind.DIRECT_URL):
        return
    if getattr(settings, "allow_private_urls", False):
        return
    host = (urlparse(resolved.normalized_url or resolved.url).hostname or "").lower()
    if not host:
        raise InvalidUrlError()
    if host in ("localhost", "localhost.localdomain") or host.endswith(".localhost") \
            or host.endswith(".local") or host.endswith(".internal"):
        raise BlockedAddressError(detail=host)
    try:
        ipaddress.ip_address(host.strip("[]"))
        addresses = [host.strip("[]")]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None)
        except (socket.gaierror, UnicodeError, OSError) as exc:
            raise InvalidUrlError(message_key="errors.unresolvable_host.message",
                                  params={"host": host}, detail=str(exc)) from exc
        addresses = sorted({info[4][0] for info in infos})
    blocked = [a for a in addresses if not _is_public_ip(a)]
    if blocked or not addresses:
        raise BlockedAddressError(detail=f"{host} → {', '.join(blocked or addresses)}")


# --------------------------------------------------------------------------
# מידע מקדים (בלי להוריד)
# --------------------------------------------------------------------------
def probe_remote(url: str, settings: AppSettings) -> dict[str, Any]:
    """
    שולף מטא-דאטה בלבד (כותרת, אורך, האם חי) כדי להציג למשתמש לפני
    ההורדה. לא מוריד מדיה.
    """
    resolved = resolve_url(url)
    check_url_allowed(resolved, settings)
    ydl_mod = _yt_dlp()

    opts = _base_ydl_opts(settings)
    opts.update({"skip_download": True, "quiet": True, "no_warnings": True})

    try:
        with ydl_mod.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(resolved.normalized_url or url, download=False,
                                    process=False)
    except Exception as exc:  # yt_dlp.utils.DownloadError ועוד
        raise classify_download_error(str(exc)) from exc

    if info is None:
        raise classify_download_error("no info")
    if info.get("_type") in ("playlist", "multi_video"):
        if resolved.platform == "youtube":
            raise PlaylistNotSupportedError()
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise NotAVideoError()
        info = entries[0]
    if info.get("_type") == "url" and info.get("url") and not info.get("duration"):
        # הפניה ל-extractor אחר (למשל עמוד שמכיל נגן) – מפענחים עד הסוף
        try:
            with ydl_mod.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(info["url"], download=False) or info
        except Exception as exc:
            raise classify_download_error(str(exc)) from exc
    return _describe_info(info, resolved, url)


def _describe_info(info: dict[str, Any], resolved: ResolvedSource, url: str) -> dict[str, Any]:
    live_status = info.get("live_status") or ""
    is_live = bool(info.get("is_live") or live_status == "is_live")
    thumb = str(info.get("thumbnail") or "")
    if not thumb.startswith("https://"):
        thumbs = [t.get("url", "") for t in (info.get("thumbnails") or [])
                  if str(t.get("url", "")).startswith("https://")]
        thumb = thumbs[-1] if thumbs else ""
    chapters = []
    for ch in info.get("chapters") or []:
        try:
            chapters.append({"start": float(ch.get("start_time") or 0.0),
                             "end": float(ch.get("end_time") or 0.0),
                             "title": str(ch.get("title") or "")})
        except (TypeError, ValueError):
            continue
    kind = resolved.kind
    if resolved.platform == "youtube":
        kind = SourceKind.YOUTUBE_LIVE if is_live else SourceKind.YOUTUBE_VOD
    notes = list(resolved.notes)
    if live_status == "was_live":
        notes.append(_ytt("was_live_note"))
    if live_status == "is_upcoming":
        notes.append(_ytt("upcoming_note"))
    return {
        "platform": resolved.platform_label,
        "platform_key": resolved.platform,
        "kind": kind.value,
        "title": info.get("title") or "",
        "uploader": info.get("uploader") or info.get("channel") or "",
        "duration": float(info.get("duration") or 0.0),
        "is_live": is_live,
        "live_status": live_status,
        "was_live": live_status == "was_live",
        "thumbnail": thumb,
        "filesize_approx": int(info.get("filesize_approx") or info.get("filesize") or 0),
        "notes": notes,
        "webpage_url": info.get("webpage_url") or url,
        "start_hint": resolved.start_hint,
        "id": str(info.get("id") or resolved.video_id or ""),
        "extractor": info.get("extractor_key") or info.get("ie_key") or "",
        "availability": info.get("availability") or "",
        "chapters": chapters,
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


def _yt_dlp():
    try:
        import yt_dlp
    except ImportError as exc:
        raise PolixorError(message_key="errors.ytdlp_missing.message",
                           hint_key="errors.ytdlp_missing.hint",
                           detail=str(exc)) from exc
    return yt_dlp


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


# קצב סיביות משוער לפי רזולוציה (Mbps) – להערכת מקום בדיסק כשהאתר
# אינו מדווח גודל
_BITRATE_BY_HEIGHT = ((2160, 20.0), (1440, 12.0), (1080, 6.0), (720, 3.5), (480, 1.8), (0, 1.0))


def estimate_size(info: dict[str, Any], seconds: float) -> int:
    """הערכת גודל ההורדה בבתים, לפי מה שהאתר מדווח או לפי קצב טיפוסי."""
    size = int(info.get("filesize") or info.get("filesize_approx") or 0)
    total = float(info.get("duration") or 0.0)
    if size and total > 0 and seconds < total:
        return int(size * seconds / total)
    if size:
        return size
    height = int(info.get("height") or 1080)
    mbps = next(b for h, b in _BITRATE_BY_HEIGHT if height >= h)
    return int(max(seconds, 60.0) * mbps * 1024 ** 2 / 8)


def _progress_text(got: int, total: int, speed: float, eta: Optional[float]) -> str:
    from .. import i18n

    parts = [human_size(got)]
    if total:
        parts.append(i18n.tr("pipeline.download.of_total", total=human_size(total)))
    if speed:
        parts.append(i18n.tr("pipeline.download.speed", speed=human_size(speed)))
    if eta:
        parts.append(i18n.tr("pipeline.download.eta", seconds=int(eta)))
    return "".join(parts)


def download(
    url: str,
    *,
    settings: AppSettings,
    dest_dir: Optional[Path] = None,
    on_progress: Optional[ProgressFn] = None,
    cancel_event: Optional[threading.Event] = None,
    live_duration: Optional[float] = None,
    section: Optional[tuple[float, float]] = None,
) -> DownloadResult:
    """
    מוריד וידאו עם דיווח התקדמות אמיתי (אחוזים + מהירות + גודל).

    שני שלבים: קודם מטא-דאטה בלבד, ואז ההורדה עצמה. בין השניים:
      * שידור חי לא יורד בלי הגבלה – `LiveRequiresCaptureError`
        (אלא אם `live_duration` מבקש חלון זמן מוגדר).
      * מקור ארוך מ-`max_source_hours` דורש `section`.
      * בדיקת מקום בדיסק לפי הערכת הגודל בפועל.
    `section=(start, end)` מוריד רק את הטווח הזה (download_ranges).
    """
    resolved = resolve_url(url)
    check_url_allowed(resolved, settings)
    dest = Path(dest_dir) if dest_dir else PATHS.sources
    dest.mkdir(parents=True, exist_ok=True)
    ydl_mod = _yt_dlp()

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
            if on_progress:
                on_progress(min(0.99, frac),
                            _progress_text(int(got), int(total),
                                           float(d.get("speed") or 0.0), d.get("eta")))
        elif status == "finished":
            state["final_path"] = d.get("filename")
            if on_progress:
                from .. import i18n

                on_progress(1.0, i18n.tr("pipeline.download.merging"))

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

    try:
        with ydl_mod.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(resolved.normalized_url or url, download=False)
            if info is None:
                raise classify_download_error("no info returned")
            if info.get("_type") in ("playlist", "multi_video"):
                entries = [e for e in (info.get("entries") or []) if e]
                if not entries:
                    raise NotAVideoError()
                info = entries[0]

            is_live = bool(info.get("is_live") or info.get("live_status") == "is_live")
            duration = float(info.get("duration") or 0.0)
            if is_live and not (live_duration and live_duration > 0):
                raise LiveRequiresCaptureError()

            span = duration
            if section is not None:
                start, end = float(section[0]), float(section[1])
                if start < 0 or end - start < 1.0 or (duration and start >= duration):
                    raise InvalidSectionError()
                if duration:
                    end = min(end, duration)
                span = end - start
                from yt_dlp.utils import download_range_func

                ydl.params["download_ranges"] = download_range_func(None, [(start, end)])
                ydl.params["force_keyframes_at_cuts"] = True
            elif duration and duration > settings.max_source_hours * 3600:
                raise SourceTooLongError(params={"hours": f"{settings.max_source_hours:g}"})

            if live_duration and live_duration > 0:
                # חלון זמן משידור חי (N השניות האחרונות)
                ydl.params["live_from_start"] = False
                ydl.params["download_ranges"] = _last_seconds_range(live_duration)
                ydl.params["force_keyframes_at_cuts"] = False
                span = live_duration

            # מקום בדיסק לפי הערכה אמיתית, עם מרווח לקובץ הממוזג
            need = int(estimate_size(info, span or 600.0) * 1.3) + 256 * 1024 ** 2
            require_free_space(dest, need)

            info = ydl.process_ie_result(info, download=True)
    except (JobCancelledError, PolixorError):
        raise
    except Exception as exc:
        msg = str(exc)
        if "No space left" in msg:
            raise DiskSpaceError(detail=msg) from exc
        if cancel_event is not None and cancel_event.is_set():
            raise JobCancelledError() from exc
        raise classify_download_error(msg) from exc

    if info is None:
        raise classify_download_error("no info returned")

    path = _resolve_downloaded_path(info, state.get("final_path"), dest)
    if path is None or not path.exists():
        raise PolixorError(message_key="errors.downloaded_file_missing.message",
                           hint_key="errors.downloaded_file_missing.hint")

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
            "section": list(section) if section else None,
            "thumbnail": info.get("thumbnail") if str(info.get("thumbnail") or "")
            .startswith("https://") else None,
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
        raise PolixorError(message_key="errors.file_not_found.message", detail=str(src))
    if src.suffix.lower() not in ALLOWED_UPLOAD_EXT:
        raise UnsupportedPlatformError(
            message_key="errors.unsupported_format.message",
            hint_key="errors.unsupported_format.hint",
            params={"ext": src.suffix or "(-)",
                    "formats": ", ".join(sorted(ALLOWED_UPLOAD_EXT))},
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
