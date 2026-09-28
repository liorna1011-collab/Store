"""
עטיפה ל-FFmpeg/FFprobe: הרצה עם התקדמות אמיתית, ביטול, ובדיקת קבצים.

ההתקדמות נקראת מ-`-progress pipe:1` (out_time_ms), כך שהיא מבוססת על
זמן העיבוד בפועל ולא על הערכה.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from ..config import find_ffmpeg, find_ffprobe
from ..errors import FFmpegFailedError, FFmpegMissingError

ProgressCb = Optional[Callable[[float], None]]


@dataclass
class MediaInfo:
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    has_audio: bool = False
    has_video: bool = False
    audio_channels: int = 0
    audio_sample_rate: int = 0
    video_codec: str = ""
    audio_codec: str = ""
    size_bytes: int = 0
    raw: dict[str, Any] | None = None


def ffmpeg_bin() -> str:
    b = find_ffmpeg()
    if not b:
        raise FFmpegMissingError()
    return b


def ffprobe_bin() -> str:
    b = find_ffprobe()
    if not b:
        raise FFmpegMissingError("FFprobe לא נמצא במערכת.",
                                 hint="FFprobe מגיע יחד עם FFmpeg. ודא שהתיקייה ב-PATH.")
    return b


def probe(path: str | Path) -> MediaInfo:
    """מידע על קובץ מדיה. זורק FFmpegFailedError אם הקובץ פגום."""
    p = Path(path)
    if not p.exists():
        raise FFmpegFailedError(f"הקובץ לא נמצא: {p.name}")

    cmd = [
        ffprobe_bin(), "-v", "error",
        "-print_format", "json",
        "-show_format", "-show_streams",
        str(p),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if res.returncode != 0:
        raise FFmpegFailedError("לא ניתן לקרוא את קובץ הווידאו (ייתכן שהוא פגום).",
                                detail=res.stderr[-2000:])
    try:
        data = json.loads(res.stdout)
    except json.JSONDecodeError as exc:
        raise FFmpegFailedError("פלט ffprobe לא תקין.", detail=str(exc)) from exc

    info = MediaInfo(raw=data)
    fmt = data.get("format", {})
    try:
        info.duration = float(fmt.get("duration") or 0.0)
    except (TypeError, ValueError):
        info.duration = 0.0
    try:
        info.size_bytes = int(fmt.get("size") or p.stat().st_size)
    except (TypeError, ValueError, OSError):
        info.size_bytes = 0

    for stream in data.get("streams", []):
        ctype = stream.get("codec_type")
        if ctype == "video" and not info.has_video:
            info.has_video = True
            info.width = int(stream.get("width") or 0)
            info.height = int(stream.get("height") or 0)
            info.video_codec = stream.get("codec_name", "")
            info.fps = _parse_fps(stream.get("avg_frame_rate") or stream.get("r_frame_rate"))
            if info.duration <= 0:
                try:
                    info.duration = float(stream.get("duration") or 0.0)
                except (TypeError, ValueError):
                    pass
        elif ctype == "audio" and not info.has_audio:
            info.has_audio = True
            info.audio_codec = stream.get("codec_name", "")
            info.audio_channels = int(stream.get("channels") or 0)
            try:
                info.audio_sample_rate = int(stream.get("sample_rate") or 0)
            except (TypeError, ValueError):
                info.audio_sample_rate = 0
    return info


def _parse_fps(val: Optional[str]) -> float:
    if not val or val in ("0/0", "N/A"):
        return 0.0
    try:
        if "/" in val:
            num, den = val.split("/", 1)
            den_f = float(den)
            return float(num) / den_f if den_f else 0.0
        return float(val)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


class FFmpegRun:
    """הרצת FFmpeg עם דיווח התקדמות וביטול."""

    def __init__(
        self,
        args: Sequence[str],
        *,
        total_seconds: float = 0.0,
        on_progress: ProgressCb = None,
        cancel_event: Optional[threading.Event] = None,
        timeout: Optional[float] = None,
    ) -> None:
        self.args = list(args)
        self.total_seconds = max(0.0, float(total_seconds))
        self.on_progress = on_progress
        self.cancel_event = cancel_event
        self.timeout = timeout
        self.stderr_tail: list[str] = []

    def run(self) -> None:
        cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-y",
               "-progress", "pipe:1", "-loglevel", "error", *self.args]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        stop = threading.Event()

        def _drain_stderr() -> None:
            assert proc.stderr is not None
            for line in proc.stderr:
                line = line.rstrip("\n")
                if line:
                    self.stderr_tail.append(line)
                    if len(self.stderr_tail) > 40:
                        self.stderr_tail.pop(0)

        def _watch_cancel() -> None:
            while not stop.wait(0.25):
                if self.cancel_event is not None and self.cancel_event.is_set():
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                    return

        t_err = threading.Thread(target=_drain_stderr, daemon=True)
        t_err.start()
        t_cancel = threading.Thread(target=_watch_cancel, daemon=True)
        t_cancel.start()

        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.strip()
                if not line or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                if key == "out_time_ms" and self.total_seconds > 0 and self.on_progress:
                    try:
                        secs = int(value) / 1_000_000.0
                    except ValueError:
                        continue
                    self.on_progress(min(1.0, max(0.0, secs / self.total_seconds)))
                elif key == "progress" and value == "end" and self.on_progress:
                    self.on_progress(1.0)
            proc.wait(timeout=self.timeout)
        finally:
            stop.set()
            t_err.join(timeout=2)
            t_cancel.join(timeout=2)
            for pipe in (proc.stdout, proc.stderr):
                try:
                    if pipe:
                        pipe.close()
                except Exception:
                    pass

        if self.cancel_event is not None and self.cancel_event.is_set():
            from ..errors import JobCancelledError

            raise JobCancelledError()

        if proc.returncode != 0:
            tail = "\n".join(self.stderr_tail[-12:])
            raise FFmpegFailedError(
                "עיבוד הווידאו נכשל.",
                detail=f"cmd: {shlex.join(cmd[:14])} ...\n{tail}",
            )


def run_ffmpeg(
    args: Sequence[str],
    *,
    total_seconds: float = 0.0,
    on_progress: ProgressCb = None,
    cancel_event: Optional[threading.Event] = None,
    timeout: Optional[float] = None,
) -> None:
    FFmpegRun(args, total_seconds=total_seconds, on_progress=on_progress,
              cancel_event=cancel_event, timeout=timeout).run()


def extract_audio_wav(
    src: str | Path,
    dst: str | Path,
    *,
    sample_rate: int = 16000,
    channels: int = 1,
    total_seconds: float = 0.0,
    on_progress: ProgressCb = None,
    cancel_event: Optional[threading.Event] = None,
) -> Path:
    """מחלץ אודיו ל-WAV PCM 16-bit (הפורמט ש-Whisper וניתוח האות צורכים)."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        ["-i", str(src), "-vn", "-ac", str(channels), "-ar", str(sample_rate),
         "-acodec", "pcm_s16le", "-f", "wav", str(dst)],
        total_seconds=total_seconds, on_progress=on_progress, cancel_event=cancel_event,
    )
    return dst


def extract_thumbnail(
    src: str | Path,
    dst: str | Path,
    *,
    at_seconds: float = 0.0,
    width: int = 640,
) -> Optional[Path]:
    """פריים בודד כתמונה ממוזערת. מחזיר None אם נכשל (לא קריטי לפייפליין)."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        run_ffmpeg([
            "-ss", f"{max(0.0, at_seconds):.3f}", "-i", str(src),
            "-frames:v", "1",
            "-vf", f"scale={width}:-2:flags=bicubic",
            "-q:v", "3", str(dst),
        ], timeout=120)
        return dst if dst.exists() else None
    except Exception:
        return None


def silence_intervals(
    src: str | Path,
    *,
    noise_db: float = -35.0,
    min_duration: float = 0.6,
    cancel_event: Optional[threading.Event] = None,
) -> list[tuple[float, float]]:
    """מזהה קטעי שקט בעזרת silencedetect. משמש כאות לגבולות משפט."""
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-i", str(src),
           "-af", f"silencedetect=noise={noise_db}dB:d={min_duration}",
           "-f", "null", "-"]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    out: list[tuple[float, float]] = []
    start: Optional[float] = None
    for line in res.stderr.splitlines():
        m = re.search(r"silence_start:\s*(-?[\d.]+)", line)
        if m:
            start = float(m.group(1))
            continue
        m = re.search(r"silence_end:\s*(-?[\d.]+)", line)
        if m and start is not None:
            out.append((max(0.0, start), float(m.group(1))))
            start = None
    return out


def has_encoder(name: str) -> bool:
    try:
        res = subprocess.run([ffmpeg_bin(), "-hide_banner", "-encoders"],
                             capture_output=True, text=True, timeout=20)
        return bool(re.search(rf"\b{re.escape(name)}\b", res.stdout))
    except Exception:
        return False


def validate_playable(path: str | Path) -> tuple[bool, str]:
    """
    בדיקת תקינות של קובץ מיוצא: מפענח את כל הזרמים ומוודא שאין שגיאות.
    מחזיר (תקין, הודעה).
    """
    p = Path(path)
    if not p.exists():
        return False, "הקובץ לא נוצר"
    if p.stat().st_size < 1024:
        return False, "הקובץ ריק או קטן מדי"
    try:
        info = probe(p)
    except FFmpegFailedError as exc:
        return False, exc.message
    if not info.has_video:
        return False, "אין זרם וידאו בקובץ"
    if info.duration <= 0.05:
        return False, "אורך הקובץ אפסי"

    res = subprocess.run(
        [ffmpeg_bin(), "-hide_banner", "-nostdin", "-v", "error",
         "-i", str(p), "-f", "null", "-"],
        capture_output=True, text=True, timeout=1800,
    )
    if res.returncode != 0:
        return False, f"פענוח נכשל: {res.stderr.strip()[:200]}"
    if res.stderr.strip():
        # אזהרות פענוח קלות לא פוסלות את הקובץ, אבל שוות דיווח
        return True, f"תקין (עם אזהרות: {res.stderr.strip()[:120]})"
    return True, "תקין"
