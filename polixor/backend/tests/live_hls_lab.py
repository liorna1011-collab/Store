"""
בדיקת קליטת שידור חי מול שידור HLS אמיתי שרץ מקומית.

הבדיקה מקימה שידור חי אמיתי (FFmpeg מפרסם HLS, שרת HTTP מגיש אותו),
מקליטה ממנו במנוע האמיתי, **מפילה את השידור באמצע**, מוודאה שהמערכת
עוברת ל-RECONNECTING, מחזירה את השידור לאוויר, ומאמתת שהחומר שהוקלט
לפני הנפילה לא אבד ושהמקטעים מתאחדים לקובץ אחד שניתן לנגן.

זהו שידור חי אמיתי מעל HTTP – אבל **לא** YouTube או Twitch. שידור מול
הפלטפורמות עצמן לא נבדק כאן ואינו יכול להיבדק בסביבה הזו.

הרצה:
    POLIXOR_DATA_DIR=/tmp/pxlive python3 tests/live_hls_lab.py
"""

from __future__ import annotations

import http.server
import shutil
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from polixor.config import PATHS, AppSettings          # noqa: E402
from polixor.services import live as live_svc          # noqa: E402
from polixor.services.live_capture import (            # noqa: E402
    CaptureConfig, StateMachine, describe_outcome, run_capture,
)
from polixor.util.ffmpeg import probe, validate_playable  # noqa: E402

PASS, FAIL = "\033[92m✓\033[0m", "\033[91m✗\033[0m"
results: list[tuple[bool, str]] = []

LAB = Path("/tmp/polixor_hls_lab")
PUB = LAB / "pub"
PORT = 8947
URL = f"http://127.0.0.1:{PORT}/live.m3u8"


def check(cond: bool, label: str, detail: str = "") -> bool:
    results.append((bool(cond), label))
    print(f"  {PASS if cond else FAIL} {label}" + (f"  — {detail}" if detail else ""))
    return bool(cond)


class Publisher:
    """שידור חי מקומי: FFmpeg מפרסם HLS בקצב אמת, בלולאה."""

    def __init__(self, source: Path) -> None:
        self.source = source
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        PUB.mkdir(parents=True, exist_ok=True)
        self.proc = subprocess.Popen(
            ["ffmpeg", "-hide_banner", "-loglevel", "error",
             "-re", "-stream_loop", "-1", "-i", str(self.source),
             "-c", "copy", "-f", "hls",
             "-hls_time", "2", "-hls_list_size", "6",
             "-hls_flags", "delete_segments+omit_endlist",
             str(PUB / "live.m3u8")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def stop(self) -> None:
        """מפיל את השידור – כמו סטרימר שנפל לו האינטרנט."""
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=10)
        for f in PUB.glob("*"):
            try:
                f.unlink()
            except OSError:
                pass

    @property
    def alive(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)


class Server(threading.Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.httpd: socketserver.TCPServer | None = None

    def run(self) -> None:
        handler = _handler_for(PUB)
        socketserver.TCPServer.allow_reuse_address = True
        self.httpd = socketserver.TCPServer(("127.0.0.1", PORT), handler)
        self.httpd.serve_forever()

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()


def _handler_for(directory: Path):
    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(directory), **kw)

        def log_message(self, *a):
            pass

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            super().end_headers()
    return H


def make_source(dest: Path) -> Path:
    """וידאו מקור לשידור: תמונה נעה + צליל, 40 שניות."""
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
         "-t", "40", "-c:v", "libx264", "-preset", "ultrafast",
         "-g", "50", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "96k", "-shortest", str(dest)],
        check=True)
    return dest


def main() -> None:
    if not shutil.which("ffmpeg"):
        print("FFmpeg אינו זמין – הבדיקה מדולגת.")
        sys.exit(2)

    LAB.mkdir(parents=True, exist_ok=True)
    PATHS.ensure()
    source = make_source(LAB / "src.mp4")

    server = Server()
    server.start()
    publisher = Publisher(source)
    publisher.start()
    time.sleep(6)          # להצטברות מקטעים ראשונים

    print(f"\n{'=' * 72}\n▶ שידור חי מקומי: {URL}\n{'=' * 72}")

    try:
        _run_checks(publisher, source)
    finally:
        publisher.stop()
        server.stop()

    passed = sum(1 for ok, _ in results if ok)
    total = len(results)
    print(f"\n{'=' * 72}\nסיכום: {passed}/{total} בדיקות עברו\n{'=' * 72}")
    for ok, label in results:
        if not ok:
            print(f"  {FAIL} {label}")
    sys.exit(0 if passed == total else 1)


def _run_checks(publisher: Publisher, source: Path) -> None:
    settings = AppSettings()
    work = PATHS.work / "hls_lab"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)

    # --- 1. זיהוי מדיה מהזרם עצמו ---
    print("\n— זיהוי זרם —")
    info = live_svc.StreamInfo(url=URL, manifest_url=URL)
    live_svc._probe_live_media(info)
    check(info.audio_checked, "הזרם נבדק בפועל ב-ffprobe")
    check(info.has_audio, "זוהה ערוץ אודיו בזרם החי")
    check(info.width == 640 and info.height == 360,
          "זוהתה רזולוציה נכונה מהזרם", f"{info.width}x{info.height}")

    # --- 2. עצירה יזומה סוגרת קובץ תקין ---
    print("\n— עצירה יזומה באמצע מקטע —")
    stop = threading.Event()
    threading.Timer(5.0, stop.set).start()
    seg = live_svc.record_segment(URL, work / "manual_stop.ts", seconds=60.0,
                                  cancel_event=threading.Event(), stop_event=stop)
    check(seg.seconds > 2.0, "נשמר חומר עד רגע העצירה", f"{seg.seconds:.1f}s")
    check(seg.path.exists() and seg.path.stat().st_size > 50_000,
          "הקובץ נסגר עם תוכן", f"{seg.path.stat().st_size:,} בתים")
    check(probe(seg.path).duration > 2.0,
          "הקובץ שנעצר ניתן לקריאה", f"{probe(seg.path).duration:.1f}s")

    # --- 3. נפילת שידור, חיבור מחדש, שמירת חומר ---
    print("\n— נפילת שידור וחיבור מחדש —")
    states: list[str] = []
    machine = StateMachine(lambda s, d: (states.append(s),
                                         print(f"    [{s}] {d}")))
    timeline: list[str] = []

    def drop() -> None:
        timeline.append("drop")
        print("\n    >>> השידור נופל\n")
        publisher.stop()

    def revive() -> None:
        timeline.append("revive")
        print("\n    >>> השידור חוזר לאוויר\n")
        publisher.start()

    stop2 = threading.Event()
    threading.Timer(10.0, drop).start()
    threading.Timer(26.0, revive).start()
    threading.Timer(52.0, stop2.set).start()

    outcome = run_capture(
        CaptureConfig(url=URL, work_dir=work / "capture",
                      segment_seconds=8.0, max_failures=10,
                      backoff=(3.0, 4.0, 6.0, 6.0, 6.0)),
        settings=settings,
        cancel_event=threading.Event(), stop_event=stop2,
        machine=machine,
        resolve=lambda u, s: u,        # מניפסט מקומי, בלי yt-dlp
        record=live_svc.record_segment)

    print(f"\n  מצבים: {' → '.join(states)}")
    print(f"  סיכום: {describe_outcome(outcome)}")

    check("reconnecting" in states,
          "המערכת עברה למצב RECONNECTING בזמן הנפילה")
    check(outcome.reconnects >= 1, "בוצע לפחות חיבור מחדש אחד",
          f"{outcome.reconnects}")
    check(states[-1] == "completed", "ההקלטה הסתיימה במצב COMPLETED",
          states[-1])
    check(len(outcome.segments) >= 2, "נשמרו כמה מקטעים",
          f"{len(outcome.segments)} מקטעים")

    before_drop = [s for s in outcome.segments
                   if s["started_at"] < time.time()]
    check(outcome.seconds > 8.0,
          "החומר שהוקלט לפני הנפילה נשמר",
          f"{outcome.seconds:.1f} שניות סה\"כ ב-{len(before_drop)} מקטעים")
    check(all(Path(s["path"]).exists() for s in outcome.segments),
          "כל קובצי המקטעים קיימים על הדיסק")

    # שידור חזר לאוויר => יש מקטעים משני צידי הנפילה
    check("revive" in timeline and outcome.reconnects >= 1,
          "השידור חזר וההקלטה המשיכה אחריו")

    # --- 4. איחוד למקור אחד ---
    print("\n— איחוד מקטעים למקור אחד —")
    dest = work / "capture.mp4"
    final = live_svc.concat_segments(outcome.paths, dest)
    minfo = probe(final)
    ok, msg = validate_playable(final)
    check(ok, "הקובץ המאוחד ניתן לניגון", msg)
    check(minfo.has_audio, "לקובץ המאוחד יש פס קול")
    check(minfo.duration > 8.0, "אורך הקובץ המאוחד סביר",
          f"{minfo.duration:.1f}s מול סכום מקטעים {outcome.seconds:.1f}s")
    check(abs(minfo.duration - outcome.seconds) < max(3.0, outcome.seconds * 0.2),
          "האורך המאוחד תואם לסכום המקטעים",
          f"{minfo.duration:.1f}s ≈ {outcome.seconds:.1f}s")
    check(minfo.video_codec == "h264", "קודק וידאו נשמר בהעתקה",
          minfo.video_codec)

    # --- 5. מקטע פגום אינו מפיל את האיחוד ---
    print("\n— עמידות: מקטע פגום ברשימה —")
    broken = work / "broken.ts"
    broken.write_bytes(b"\x00" * 300)
    dest2 = work / "capture_with_broken.mp4"
    final2 = live_svc.concat_segments(outcome.paths + [broken], dest2)
    check(final2.exists() and probe(final2).duration > 5.0,
          "מקטע פגום דולג והאיחוד הצליח",
          f"{probe(final2).duration:.1f}s")


if __name__ == "__main__":
    main()
