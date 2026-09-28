"""
בדיקות טיפול בשגיאות – מריצות את מסלולי הכישלון האמיתיים
ומוודאות שהמשתמש מקבל הודעה ברורה בעברית ולא קריסה.

הרצה:  python3 tests/test_errors.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from polixor.config import AppSettings                        # noqa: E402
from polixor.errors import (                                  # noqa: E402
    DiskSpaceError, InvalidUrlError, JobCancelledError, NoAudioError,
    PolixorError, SourceTooShortError, classify_download_error,
)
from polixor.services import ingest                           # noqa: E402
from polixor.services.audio import analyze_audio              # noqa: E402
from polixor.util.ffmpeg import ffmpeg_bin, probe, run_ffmpeg, validate_playable  # noqa: E402
from polixor.util.fs import require_free_space                # noqa: E402

PASS, FAIL = "\033[92m✓\033[0m", "\033[91m✗\033[0m"
results: list[tuple[str, bool, str]] = []


def case(name: str):
    def deco(fn):
        def wrapper():
            try:
                fn()
                results.append((name, True, ""))
                print(f"  {PASS} {name}")
            except AssertionError as e:
                results.append((name, False, str(e)))
                print(f"  {FAIL} {name}: {e}")
            except Exception as e:  # noqa: BLE001
                results.append((name, False, f"{type(e).__name__}: {e}"))
                print(f"  {FAIL} {name}: {type(e).__name__}: {e}")
        wrapper.__name__ = fn.__name__
        return wrapper
    return deco


def expect_error(fn, error_type, label: str):
    try:
        fn()
    except error_type as exc:
        assert exc.message, f"{label}: אין הודעה למשתמש"
        assert any("֐" <= c <= "׿" for c in exc.message), \
            f"{label}: ההודעה אינה בעברית: {exc.message}"
        return exc
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(f"{label}: נזרקה שגיאה לא צפויה {type(exc).__name__}: {exc}")
    raise AssertionError(f"{label}: לא נזרקה שגיאה כלל")


# ==========================================================================
@case("קישור ריק / לא תקין מזוהה מראש")
def t_invalid_url():
    for bad in ("", "   ", "not a url", "ftp://x", "hello.world"):
        expect_error(lambda b=bad: ingest.resolve_url(b), InvalidUrlError,
                     f"קישור '{bad}'")


@case("קישור Google Drive ללא מזהה קובץ")
def t_gdrive_no_id():
    exc = expect_error(lambda: ingest.resolve_url("https://drive.google.com/drive/my-drive"),
                       InvalidUrlError, "Drive ללא מזהה")
    assert "Drive" in exc.message or "מזהה" in exc.message


@case("זיהוי פלטפורמות תקין")
def t_platform_detection():
    cases = {
        "https://www.youtube.com/watch?v=abc123": "youtube_vod",
        "https://youtu.be/abc123": "youtube_vod",
        "https://www.youtube.com/live/abc123": "youtube_live",
        "https://www.twitch.tv/videos/12345": "twitch_vod",
        "https://www.twitch.tv/somestreamer": "twitch_live",
        "https://kick.com/video/abc-def": "kick_vod",
        "https://kick.com/somestreamer": "kick_live",
        "https://drive.google.com/file/d/1A2B3C4D5E6F7G8H/view": "gdrive",
        "https://example.com/video.mp4": "direct_url",
    }
    for url, expected in cases.items():
        got = ingest.resolve_url(url).kind.value
        assert got == expected, f"{url}: ציפינו {expected}, קיבלנו {got}"


@case("מיפוי שגיאות yt-dlp להודעות בעברית")
def t_download_error_mapping():
    mapping = {
        "ERROR: Private video. Sign in if you've been granted access": "private_or_unavailable",
        "ERROR: Video unavailable": "private_or_unavailable",
        "This video is DRM protected": "drm_protected",
        "Sign in to confirm your age. This video may be age restricted": "restricted",
        "The uploader has not made this video available in your country": "restricted",
        "This live event will begin in 3 hours": "live_not_started",
        "OSError: [Errno 28] No space left on device": "disk_space",
        "ERROR: Unsupported URL: https://example.com/page": "unsupported_platform",
    }
    for raw, expected_code in mapping.items():
        err = classify_download_error(raw)
        assert err.code == expected_code, f"'{raw[:40]}' → {err.code} (ציפינו {expected_code})"
        assert any("֐" <= c <= "׿" for c in err.message), "הודעה לא בעברית"
        assert err.detail, "פרטי המקור נשמרו לצורך דיבוג"


@case("קובץ פגום מזוהה ולא מפיל את המערכת")
def t_corrupt_file():
    with tempfile.TemporaryDirectory() as d:
        bad = Path(d) / "corrupt.mp4"
        bad.write_bytes(b"\x00\x01\x02 this is not a video " * 500)
        expect_error(lambda: probe(bad), PolixorError, "קובץ פגום")


@case("קובץ חסר מזוהה")
def t_missing_file():
    expect_error(lambda: probe("/nonexistent/path/video.mp4"), PolixorError, "קובץ חסר")


@case("סוג קובץ לא נתמך נדחה")
def t_unsupported_upload():
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "doc.pdf"
        f.write_bytes(b"%PDF-1.4")
        exc = expect_error(lambda: ingest.register_local_file(f), PolixorError,
                           "קובץ PDF")
        assert exc.code == "unsupported_platform"


@case("וידאו ללא אודיו – הפייפליין ממשיך")
def t_video_without_audio():
    with tempfile.TemporaryDirectory() as d:
        silent = Path(d) / "silent.mp4"
        subprocess.run([
            ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=10:duration=4",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(silent),
        ], check=True, timeout=120)
        info = probe(silent)
        assert info.has_video and not info.has_audio, "הקובץ אכן ללא אודיו"
        ok, msg = validate_playable(silent)
        assert ok, f"קובץ ללא אודיו עדיין תקין לניגון: {msg}"


@case("קובץ אודיו ריק מזוהה")
def t_empty_audio():
    with tempfile.TemporaryDirectory() as d:
        wav = Path(d) / "tiny.wav"
        subprocess.run([
            ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "0.01",
            "-acodec", "pcm_s16le", str(wav),
        ], check=True, timeout=60)
        expect_error(lambda: analyze_audio(wav), NoAudioError, "אודיו קצר מדי")

        missing = Path(d) / "nope.wav"
        expect_error(lambda: analyze_audio(missing), NoAudioError, "אודיו חסר")


@case("חוסר מקום בדיסק מדווח עם מספרים אמיתיים")
def t_disk_space():
    with tempfile.TemporaryDirectory() as d:
        exc = expect_error(lambda: require_free_space(Path(d), 10 ** 15),
                           DiskSpaceError, "מקום בדיסק")
        assert "GB" in exc.message or "TB" in exc.message or "PB" in exc.message, \
            f"ההודעה כוללת גודל קריא: {exc.message}"


@case("FFmpeg נכשל – שגיאה ברורה, לא קריסה")
def t_ffmpeg_failure():
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "x.mp4"
        expect_error(
            lambda: run_ffmpeg(["-i", "/does/not/exist.mp4", str(out)], timeout=60),
            PolixorError, "FFmpeg על קלט חסר")


@case("ביטול עוצר את FFmpeg באמצע")
def t_cancel_ffmpeg():
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "long.mp4"
        cancel = threading.Event()
        threading.Timer(1.2, cancel.set).start()
        t0 = time.time()
        try:
            run_ffmpeg([
                "-f", "lavfi", "-i", "testsrc2=size=640x480:rate=30:duration=600",
                "-c:v", "libx264", "-preset", "veryslow", str(out),
            ], total_seconds=600, cancel_event=cancel, timeout=120)
            raise AssertionError("הביטול לא עצר את העיבוד")
        except JobCancelledError:
            elapsed = time.time() - t0
            assert elapsed < 20, f"הביטול לקח יותר מדי ({elapsed:.1f} שניות)"


@case("קובץ פלט ריק נפסל באימות")
def t_validate_rejects_empty():
    with tempfile.TemporaryDirectory() as d:
        empty = Path(d) / "empty.mp4"
        empty.write_bytes(b"")
        ok, msg = validate_playable(empty)
        assert not ok and msg, "קובץ ריק נפסל"

        missing = Path(d) / "missing.mp4"
        ok, msg = validate_playable(missing)
        assert not ok, "קובץ חסר נפסל"


@case("וידאו תקין עובר אימות")
def t_validate_accepts_good():
    with tempfile.TemporaryDirectory() as d:
        good = Path(d) / "good.mp4"
        subprocess.run([
            ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=15:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(good),
        ], check=True, timeout=120)
        ok, msg = validate_playable(good)
        assert ok, f"וידאו תקין נפסל: {msg}"


@case("שגיאות נושאות קוד יציב ורמז לפעולה")
def t_error_contract():
    from polixor import errors as E

    for cls in (E.InvalidUrlError, E.PrivateOrUnavailableError, E.DrmProtectedError,
                E.NoAudioError, E.FFmpegMissingError, E.DiskSpaceError,
                E.ModelUnavailableError, E.NoMomentsFoundError,
                E.SourceTooShortError, E.AiProviderError):
        e = cls()
        d = e.to_dict()
        assert d["code"] and d["code"] != "unknown_error", f"{cls.__name__}: חסר קוד"
        assert d["message"], f"{cls.__name__}: חסרה הודעה"
        assert any("֐" <= c <= "׿" for c in d["message"]), \
            f"{cls.__name__}: הודעה לא בעברית"


@case("אין הצעה לעקוף DRM או הגבלות")
def t_no_bypass_language():
    from polixor import errors as E

    forbidden = ("עקוף", "לעקוף", "bypass", "crack", "פיצוח")
    for cls in (E.DrmProtectedError, E.GeoOrAgeRestrictedError,
                E.PrivateOrUnavailableError):
        e = cls()
        text = f"{e.message} {e.hint}"
        # המילה "עוקף" מותרת רק בהקשר של "לא עוקף"
        for word in forbidden:
            if word in text:
                assert "לא עוקף" in text or "אינו עוקף" in text, \
                    f"{cls.__name__}: ניסוח בעייתי — {text}"


# ==========================================================================
def main() -> None:
    print("בדיקות טיפול בשגיאות:\n")
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn()
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n{passed}/{len(results)} בדיקות עברו")
    if passed < len(results):
        for n, ok, err in results:
            if not ok:
                print(f"  {FAIL} {n}: {err}")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
