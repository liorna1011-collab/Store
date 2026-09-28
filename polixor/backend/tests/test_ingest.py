"""
בדיקות לייבוא מקישור: זיהוי פלטפורמה, הגנת SSRF והורדה אמיתית.

  * טבלה של קישורי YouTube, ‏Twitch, ‏Kick, ‏Drive, קבצים ישירים וקישורים
    שגויים – סיווג, מזהה, זמן התחלה ושגיאה צפויה (בלי רשת).
  * SSRF: כתובות פנימיות נחסמות (localhost, רשת ביתית, link-local, מטא-דאטה
    של ענן, IPv6 פנימי, IPv4 בתוך IPv6), כתובות ציבוריות עוברות.
  * הורדה אמיתית דרך yt-dlp משרת HTTP מקומי: קובץ מלא, טווח (section),
    דיווח התקדמות וביטול.

אין כאן פנייה ל-YouTube/Twitch/Kick האמיתיים: הורדה מהם תלויה ברשת,
ולכן מסומנת ב-README כ-NOT VERIFIED בסביבה שבה אין אליהם גישה.

הרצה:  python3 tests/test_ingest.py
"""

from __future__ import annotations

import functools
import http.server
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxingest_"))
DATA = Path(os.environ["POLIXOR_DATA_DIR"])

from polixor.config import PATHS, AppSettings                       # noqa: E402

PATHS.ensure()

from polixor.errors import (                                        # noqa: E402
    BlockedAddressError, InvalidUrlError, JobCancelledError, NotAVideoError,
    PlaylistNotSupportedError, PolixorError,
)
from polixor.services import ingest                                 # noqa: E402
from polixor.util.ffmpeg import ffmpeg_bin, probe                   # noqa: E402

YT = "dQw4w9WgXcQ"
KICK_VOD = "3f1b2c3d-1111-2222-3333-444455556666"
DRIVE = "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"

# (קישור, platform, content, live, video_id, start_hint)
OK_CASES = [
    (f"https://www.youtube.com/watch?v={YT}", "youtube", "video", False, YT, None),
    (f"https://youtu.be/{YT}?t=90", "youtube", "video", False, YT, 90.0),
    (f"youtube.com/watch?v={YT}&t=1m30s", "youtube", "video", False, YT, 90.0),
    (f"https://m.youtube.com/watch?v={YT}&list=PL123", "youtube", "video", False, YT, None),
    (f"https://www.youtube.com/shorts/{YT}", "youtube", "short", False, YT, None),
    (f"https://www.youtube.com/live/{YT}", "youtube", "live", True, YT, None),
    (f"https://www.youtube.com/embed/{YT}", "youtube", "video", False, YT, None),
    (f"https://www.youtube-nocookie.com/embed/{YT}?start=30", "youtube", "video", False,
     YT, 30.0),
    (f"https://music.youtube.com/watch?v={YT}", "youtube", "video", False, YT, None),
    ("https://www.youtube.com/@somechannel/live", "youtube", "live", True, "", None),
    ("https://www.youtube.com/channel/UCabcdefghijklmnop/live", "youtube", "live", True,
     "", None),
    (f"‏https://youtu.be/{YT}‏", "youtube", "video", False, YT, None),
    (f"  <https://youtu.be/{YT}>  ", "youtube", "video", False, YT, None),
    ("https://www.twitch.tv/videos/1234567890", "twitch", "video", False, "1234567890", None),
    ("https://www.twitch.tv/videos/1234567890?t=1h2m3s", "twitch", "video", False,
     "1234567890", 3723.0),
    ("https://www.twitch.tv/somestreamer", "twitch", "live", True, "somestreamer", None),
    ("https://m.twitch.tv/somestreamer", "twitch", "live", True, "somestreamer", None),
    ("https://www.twitch.tv/somestreamer/clip/FunnyClip-abc", "twitch", "clip", False,
     "FunnyClip-abc", None),
    ("https://clips.twitch.tv/FunnyClip-abc", "twitch", "clip", False, "FunnyClip-abc", None),
    ("https://player.twitch.tv/?video=v1234567890&parent=x", "twitch", "video", False,
     "1234567890", None),
    ("https://player.twitch.tv/?channel=somestreamer", "twitch", "live", True,
     "somestreamer", None),
    ("https://kick.com/somestreamer", "kick", "live", True, "somestreamer", None),
    (f"https://kick.com/video/{KICK_VOD}", "kick", "video", False, KICK_VOD, None),
    (f"https://kick.com/somestreamer/videos/{KICK_VOD}", "kick", "video", False, KICK_VOD,
     None),
    ("https://kick.com/somestreamer?clip=clip_01ABC", "kick", "clip", False, "clip_01ABC", None),
    ("https://kick.com/somestreamer/clips/clip_01ABC", "kick", "clip", False, "clip_01ABC",
     None),
    (f"https://drive.google.com/file/d/{DRIVE}/view?usp=sharing", "gdrive", "video", False,
     DRIVE, None),
    (f"https://drive.google.com/open?id={DRIVE}", "gdrive", "video", False, DRIVE, None),
    ("https://example.com/video.mp4", "direct", "video", False, "", None),
    ("https://example.com/path/stream.m3u8?token=abc", "direct", "video", False, "", None),
    ("https://vimeo.com/123456", "other", "video", False, "", None),
]

ERR_CASES = [
    ("", InvalidUrlError), ("   ", InvalidUrlError), ("not a url", InvalidUrlError),
    ("ftp://example.com/a.mp4", InvalidUrlError), ("javascript:alert(1)", InvalidUrlError),
    ("file:///etc/passwd", InvalidUrlError),
    ("https://user:pass@example.com/a.mp4", InvalidUrlError),
    ("https://" + "a" * 3000 + ".com/x.mp4", InvalidUrlError),
    ("https://www.youtube.com/@somechannel", NotAVideoError),
    ("https://www.youtube.com/results?search_query=x", NotAVideoError),
    ("https://www.youtube.com/watch?v=", NotAVideoError),
    ("https://www.youtube.com/playlist?list=PL123", PlaylistNotSupportedError),
    ("https://www.twitch.tv/directory", NotAVideoError),
    ("https://www.twitch.tv/somestreamer/videos", NotAVideoError),
    ("https://kick.com/categories", NotAVideoError),
    ("https://kick.com/", NotAVideoError),
    ("https://drive.google.com/open?id=x", InvalidUrlError),
]

BLOCKED = ["http://localhost/a.mp4", "http://127.0.0.1/a.mp4", "http://10.0.0.5/a.mp4",
           "http://192.168.1.10:8080/a.mp4", "http://172.16.3.4/a.mp4",
           "http://169.254.169.254/latest/meta-data/x.mp4", "http://100.64.0.1/a.mp4",
           "http://[::1]/a.mp4", "http://[fd00::1]/a.mp4", "http://[fe80::1]/a.mp4",
           "http://[::ffff:127.0.0.1]/a.mp4", "http://0.0.0.0/a.mp4",
           "http://printer.local/a.mp4", "http://api.internal/a.mp4",
           "http://224.0.0.1/a.mp4"]
PUBLIC = ["http://8.8.8.8/a.mp4", "http://[2001:4860:4860::8888]/a.mp4"]

PASS, FAIL = "\033[92m✓\033[0m", "\033[91m✗\033[0m"
results: list[tuple[bool, str]] = []


def check(cond: bool, label: str, detail: str = "") -> bool:
    results.append((bool(cond), label))
    if not cond or detail:
        print(f"  {PASS if cond else FAIL} {label}" + (f"  — {detail}" if detail else ""))
    return bool(cond)


def test_resolver() -> None:
    print("\n▶ זיהוי קישורים")
    for url, platform, content, live, vid, start in OK_CASES:
        try:
            r = ingest.resolve_url(url)
        except PolixorError as exc:
            check(False, url, f"{exc.code}")
            continue
        got = (r.platform, r.content, r.is_live, r.video_id, r.start_hint)
        check(got == (platform, content, live, vid, start), url.strip()[:70], "" if got == (
            platform, content, live, vid, start) else f"{got}")
    for url, err in ERR_CASES:
        try:
            ingest.resolve_url(url)
            check(False, f"שגיאה: {url[:50]!r}", "לא נזרקה שגיאה")
        except PolixorError as exc:
            check(isinstance(exc, err), f"שגיאה: {url[:50]!r}",
                  "" if isinstance(exc, err) else type(exc).__name__)
    n = len(OK_CASES) + len(ERR_CASES)
    print(f"  {sum(1 for r in results[-n:] if r[0])}/{n} קישורים")


def test_ssrf() -> None:
    print("\n▶ SSRF")
    s = AppSettings()
    for url in BLOCKED:
        try:
            ingest.check_url_allowed(ingest.resolve_url(url), s)
            check(False, f"נחסם: {url}", "עבר")
        except (BlockedAddressError, InvalidUrlError):
            check(True, f"נחסם: {url}")
    for url in PUBLIC:
        try:
            ingest.check_url_allowed(ingest.resolve_url(url), s)
            check(True, f"ציבורי עובר: {url}")
        except PolixorError as exc:
            check(False, f"ציבורי עובר: {url}", exc.code)
    # פלטפורמות מוכרות לא תלויות ב-DNS
    ingest.check_url_allowed(ingest.resolve_url(f"https://youtu.be/{YT}"), s)
    check(True, "YouTube לא נחסם")
    allowed = AppSettings.from_dict({**s.to_dict(), "allow_private_urls": True})
    ingest.check_url_allowed(ingest.resolve_url("http://127.0.0.1/a.mp4"), allowed)
    check(True, "allow_private_urls מאפשר רשת מקומית (לשימוש מכוון בלבד)")


class _Quiet(http.server.SimpleHTTPRequestHandler):
    """שרת קבצים עם תמיכה ב-Range – כמו כל שרת וידאו אמיתי (FFmpeg מדלג בעזרתו)."""

    def log_message(self, *a):          # noqa: D401
        pass

    def send_head(self):
        rng = self.headers.get("Range", "")
        path = Path(self.translate_path(self.path))
        if not rng.startswith("bytes=") or not path.is_file():
            return super().send_head()
        size = path.stat().st_size
        a, _, b = rng[6:].split(",")[0].partition("-")
        start = int(a) if a else max(0, size - int(b))
        end = min(size - 1, int(b)) if (a and b) else size - 1
        if start >= size:
            self.send_error(416)
            return None
        f = path.open("rb")
        f.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(str(path)))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self._remaining = end - start + 1
        return f

    def copyfile(self, source, outputfile):
        remaining = getattr(self, "_remaining", None)
        if remaining is None:
            return super().copyfile(source, outputfile)
        while remaining > 0:
            chunk = source.read(min(65536, remaining))
            if not chunk:
                break
            outputfile.write(chunk)
            remaining -= len(chunk)


def test_download() -> None:
    print("\n▶ הורדה אמיתית משרת HTTP מקומי (yt-dlp)")
    try:
        ingest._yt_dlp()
    except PolixorError:
        check(False, "yt-dlp מותקן")
        return
    www = DATA / "www"
    www.mkdir(exist_ok=True)
    src = www / "sample.mp4"
    subprocess.run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=s=640x360:r=25:d=40",
                    "-f", "lavfi", "-i", "sine=f=440:d=40",
                    "-c:v", "libx264", "-preset", "ultrafast", "-g", "25",
                    "-c:a", "aac", "-shortest", str(src)], check=True)
    handler = functools.partial(_Quiet, directory=str(www))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/sample.mp4"
    s = AppSettings.from_dict({**AppSettings().to_dict(), "allow_private_urls": True})
    try:
        try:
            ingest.download(url, settings=AppSettings(), dest_dir=DATA / "dl0")
            check(False, "בלי allow_private_urls ההורדה נחסמת")
        except BlockedAddressError:
            check(True, "בלי allow_private_urls ההורדה נחסמת")

        seen: list[float] = []
        res = ingest.download(url, settings=s, dest_dir=DATA / "dl1",
                              on_progress=lambda f, _m: seen.append(f))
        info = probe(res.path)
        check(res.path.exists() and abs(info.duration - 40) < 1.0,
              "קובץ מלא ירד", f"{info.duration:.1f}s")
        check(bool(seen) and max(seen) >= 0.99, "דיווח התקדמות", f"{len(seen)} עדכונים")

        res = ingest.download(url, settings=s, dest_dir=DATA / "dl2", section=(10.0, 22.0))
        info = probe(res.path)
        check(abs(info.duration - 12.0) < 1.5, "טווח (section) בלבד ירד",
              f"{info.duration:.1f}s")

        cancel = threading.Event()
        cancel.set()
        try:
            ingest.download(url, settings=s, dest_dir=DATA / "dl3", cancel_event=cancel)
            check(False, "ביטול עוצר את ההורדה", "לא נעצרה")
        except JobCancelledError:
            check(True, "ביטול עוצר את ההורדה")
        left = [p for p in (DATA / "dl3").glob("*") if p.suffix in (".mp4", ".part")]
        check(not left, "אין קבצים חלקיים אחרי ביטול", str(left))
    finally:
        srv.shutdown()


def main() -> int:
    t0 = time.time()
    test_resolver()
    test_ssrf()
    test_download()
    ok = sum(1 for r in results if r[0])
    print(f"\n{ok}/{len(results)} בדיקות ייבוא עברו ({time.time() - t0:.1f}s)")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
