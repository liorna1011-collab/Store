"""
מנוע הייצוא: חיתוך, ביצוע תכנית העריכה, Reframe, כתוביות וקידוד.

שני מסלולי רינדור:

  מסלול פשוט      חיתוך רציף אחד בלי עריכה. שרשרת ‎-vf אחת.
  מסלול EDL       מבצע תכנית עריכה: מספר ביטים, כל אחד עם גודל פריים
                  ומהירות משלו, מחוברים ל-filter_complex אחד.

בשני המסלולים ה-seek נעשה עם ‎-ss לפני ‎-i, כך ש-FFmpeg לא מפענח את כל
השידור כדי להגיע לדקה 90. בתוך גרף הפילטרים פילטר ה-trim עובד על
החלון שכבר נחתך, ולכן הביטים והזמנים יחסיים לתחילת החלון.
"""

from __future__ import annotations

import logging
import platform
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from ..config import AppSettings
from ..errors import FFmpegFailedError, PolixorError
from ..util.ffmpeg import extract_thumbnail, probe, run_ffmpeg
from ..util.fs import require_free_space
from .editing import (
    Beat,
    EditPlan,
    StyleProfile,
    audio_polish_chain,
    bind_reframe,
    get_style,
    plan_to_filtergraph,
)
from .reframe import ReframePlan, build_vertical_filter

log = logging.getLogger("polixor.render")

ProgressFn = Optional[Callable[[float], None]]

QUALITY_CRF = {"low": 25, "medium": 21, "high": 18}
QUALITY_PRESET = {"low": "veryfast", "medium": "faster", "high": "medium"}
QUALITY_AUDIO_KBPS = {"low": "128k", "medium": "160k", "high": "192k"}


@dataclass
class RenderRequest:
    source: Path
    output: Path
    segments: list[tuple[float, float]]          # מקטע אחד = קליפ רציף
    width: int = 1920
    height: int = 1080
    aspect: str = "16:9"
    reframe: Optional[ReframePlan] = None
    subtitle_path: Optional[Path] = None
    audio_normalize: bool = True
    quality: str = "high"
    hw_accel: str = "none"
    transitions: bool = False                     # פייד קצר בין מקטעים
    fade_seconds: float = 0.25
    work_dir: Optional[Path] = None
    source_info: dict = field(default_factory=dict)

    # --- עריכה ---
    edit_style: str = "clean"
    edit_plans: list[Optional[EditPlan]] = field(default_factory=list)

    # --- אודיו ---
    # רשרשת שנגזרה ממדידת המקור (`audio_mastering`). כשהיא קיימת
    # היא מחליפה את הליטוש הקבוע של הסגנון: הליטוש הקבוע מפעיל
    # את אותם פילטרים על כל מקור, גם על כזה שלא צריך אותם.
    audio_chain: str = ""

    # --- כתוביות לקליפ מרובה חלקים ---
    # ב-`_render_multi` כל חלק מרונדר בנפרד וזמניו מתחילים באפס. ASS
    # אחד שזמניו על הציר המאוחד היה צורב בחלק השני את הכתוביות של
    # החלק הראשון. לכן לכל חלק יש קובץ משלו, כשהוא קיים.
    subtitle_parts: list[Optional[Path]] = field(default_factory=list)

    @property
    def is_vertical(self) -> bool:
        """כל יחס שאינו 16:9 נחתך מהמקור (9:16, 1:1, 4:5)."""
        return self.aspect != "16:9"

    def subtitle_for(self, index: int) -> Optional[Path]:
        if len(self.segments) > 1:
            if index < len(self.subtitle_parts):
                return self.subtitle_parts[index]
            return None
        return self.subtitle_path

    @property
    def style(self) -> StyleProfile:
        return get_style(self.edit_style)

    def plan_for(self, index: int) -> Optional[EditPlan]:
        if index < len(self.edit_plans):
            return self.edit_plans[index]
        return None

    @property
    def total_duration(self) -> float:
        """אורך הפלט הצפוי, אחרי העריכה."""
        total = 0.0
        for i, (s, e) in enumerate(self.segments):
            plan = self.plan_for(i)
            total += plan.out_duration if plan else max(0.0, e - s)
        return total

    @property
    def source_span(self) -> float:
        return sum(max(0.0, e - s) for s, e in self.segments)


@dataclass
class RenderResult:
    path: Path
    width: int
    height: int
    duration: float
    size_bytes: int
    thumbnail: Optional[Path] = None
    note: str = ""
    # מה עלה זמן בקידוד – לאבחון ייצוא איטי (גודל, fps, פריסה, ביטים, מקודד, ליבות)
    stats: dict = field(default_factory=dict)


def render_stats(req: "RenderRequest", seconds: float, duration: float) -> dict:
    import os

    codec = _video_codec_args(req)
    plan = req.reframe
    beats = sum(len(getattr(p, "beats", []) or []) for p in req.edit_plans if p is not None)
    fps = float(req.source_info.get("fps") or 0.0)
    return {
        "encode_seconds": round(seconds, 2),
        "output_seconds": round(duration, 2),
        "speed": round(duration / seconds, 3) if seconds > 0 else None,
        "encoder": codec[1] if len(codec) > 1 else "",
        "preset": codec[codec.index("-preset") + 1] if "-preset" in codec else "",
        "size": f"{req.width}x{req.height}",
        "fps": round(fps, 3),
        "source_size": f"{req.source_info.get('width', 0)}x{req.source_info.get('height', 0)}",
        "layout": getattr(plan, "layout", "") if plan is not None else "",
        "segments": len(req.segments),
        "beats": beats,
        "subtitles": bool(req.subtitle_path or any(req.subtitle_parts)),
        "cpu_count": os.cpu_count() or 0,
    }


# --------------------------------------------------------------------------
# קידוד
# --------------------------------------------------------------------------
def _video_codec_args(req: RenderRequest) -> list[str]:
    """The encoder for this machine (services/encoders.py: a test encode decides, CPU as fallback)."""
    from . import encoders

    crf = QUALITY_CRF.get(req.quality, 18)
    preset = QUALITY_PRESET.get(req.quality, "medium")
    return encoders.codec_args(encoders.choose(req.hw_accel), crf, preset)


def _hw_decode(req: RenderRequest) -> list[str]:
    from . import encoders

    return ["-hwaccel", "auto"] if encoders.choose(req.hw_accel) == "nvenc" else []


def _audio_args(req: RenderRequest) -> list[str]:
    return ["-c:a", "aac", "-b:a", QUALITY_AUDIO_KBPS.get(req.quality, "192k"),
            "-ar", "48000", "-ac", "2"]


def escape_filter_path(path: Path) -> str:
    """
    בריחה לנתיב בתוך ארגומנט פילטר של FFmpeg.
    ב-Windows הופכים C:\\dir ל-C\\:/dir, אחרת הנקודתיים נחשבת מפריד.
    """
    p = str(path)
    if platform.system() == "Windows":
        p = p.replace("\\", "/")
        if len(p) > 1 and p[1] == ":":
            p = p[0] + "\\:" + p[2:]
    else:
        p = p.replace("\\", "\\\\").replace(":", "\\:")
    return p.replace("'", "\\'")


# --------------------------------------------------------------------------
# שרשראות פילטרים
# --------------------------------------------------------------------------
def _geometry_filter(req: RenderRequest, *, label: str = "p",
                     reframe: Optional[ReframePlan] = None,
                     time_shift: float = 0.0) -> str:
    """
    המרה לפריים היעד בלבד. מוחל לפני setpts, כדי שביטוי מעקב הפנים
    יראה את זמן המקור הנכון.

    `reframe` – מסגור ספציפי (למשל של ביט אחד); `time_shift` מזיז את
    זמני המסגור כשה-seek מתחיל אחרי תחילת החלון (הידוק ראש במסלול
    הפשוט). `label` חייב להיות ייחודי בתוך גרף אחד.
    """
    if req.is_vertical:
        plan = reframe or req.reframe or ReframePlan(layout="center")
        if abs(time_shift) > 1e-6:
            plan = plan.shift(time_shift)
        src_w = int(req.source_info.get("width") or 1920)
        src_h = int(req.source_info.get("height") or 1080)
        return build_vertical_filter(plan, out_width=req.width,
                                     out_height=req.height,
                                     src_width=src_w, src_height=src_h,
                                     label=label)
    return (
        f"scale={req.width}:{req.height}:force_original_aspect_ratio=decrease:"
        f"flags=lanczos,"
        f"pad={req.width}:{req.height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
    )


def _post_filters(req: RenderRequest, *, with_fade: bool,
                  duration: float, include_polish: bool,
                  subtitle_path: Optional[Path] = None) -> list[str]:
    """פילטרים שמוחלים על התוצר הסופי: צבע, פייד, כתוביות."""
    parts: list[str] = []
    style = req.style

    if include_polish:
        if style.color_punch > 0.01:
            p = min(1.0, style.color_punch)
            parts.append(
                f"eq=contrast={1.0 + 0.09 * p:.3f}:saturation={1.0 + 0.16 * p:.3f}"
                f":brightness={0.012 * p:.4f}")
        if style.sharpen:
            parts.append("unsharp=5:5:0.45:5:5:0.0")

    if with_fade and duration > req.fade_seconds * 2.5:
        f = req.fade_seconds
        parts.append(f"fade=t=in:st=0:d={f:.3f}")
        parts.append(f"fade=t=out:st={max(0.0, duration - f):.3f}:d={f:.3f}")

    if subtitle_path is not None and subtitle_path.exists():
        parts.append(f"ass='{escape_filter_path(subtitle_path)}'")

    parts.append("format=yuv420p")
    return parts


DECLICK = 0.012


def _audio_filter(req: RenderRequest, *, with_fade: bool,
                  duration: float) -> str:
    parts: list[str] = []
    chain = (req.audio_chain if req.audio_chain
             else audio_polish_chain(req.style, req.audio_normalize))
    if chain:
        parts.append(chain)
    if req.audio_chain:
        # aresample נדרש כדי שהחיתוכים לא יזיזו את הסנכרון; רשרשת
        # המאסטרינג לא כוללת אותו כי היא נבנית גם לשימוש עצמאי.
        parts.append("aresample=async=1:first_pts=0")
    if with_fade and duration > req.fade_seconds * 2.5:
        f = req.fade_seconds
        parts.append(f"afade=t=in:st=0:d={f:.3f}")
        parts.append(f"afade=t=out:st={max(0.0, duration - f):.3f}:d={f:.3f}")
    elif len(req.segments) > 1 and duration > DECLICK * 4:
        # a hard cut between sentences joins two waveforms mid-cycle: an audible click.
        # A few milliseconds of fade at each side of the join removes it without a heard fade.
        parts.append(f"afade=t=in:st=0:d={DECLICK:.3f}")
        parts.append(f"afade=t=out:st={max(0.0, duration - DECLICK):.3f}:d={DECLICK:.3f}")
    return ",".join(p for p in parts if p)


# --------------------------------------------------------------------------
# ייצוא
# --------------------------------------------------------------------------
def render_clip(
    req: RenderRequest,
    *,
    on_progress: ProgressFn = None,
    cancel_event: Optional[threading.Event] = None,
) -> RenderResult:
    """מייצא קליפ. תומך במקטע יחיד (רציף) או בכמה מקטעים (Highlights)."""
    if not req.segments:
        raise PolixorError(message_key="processing.render.no_segments")
    if not req.source.exists():
        raise PolixorError(message_key="processing.render.no_source", hint=str(req.source))

    req.output.parent.mkdir(parents=True, exist_ok=True)
    require_free_space(req.output.parent,
                       int(max(req.total_duration, 1.0) * 1.6 * 1024 * 1024)
                       + 128 * 1024 ** 2)

    has_audio = bool(req.source_info.get("has_audio", True))

    from . import encoders

    t0 = time.monotonic()
    for attempt in (0, 1):
        kind = encoders.choose(req.hw_accel)
        try:
            if len(req.segments) == 1:
                _render_segment(req, 0, req.output, with_fade=req.transitions,
                                has_audio=has_audio, on_progress=on_progress,
                                cancel_event=cancel_event)
            else:
                _render_multi(req, has_audio=has_audio, on_progress=on_progress,
                              cancel_event=cancel_event)
            break
        except FFmpegFailedError as exc:
            if kind == "cpu" or attempt or (cancel_event is not None and cancel_event.is_set()):
                raise
            # the graphics-card encoder failed on a real clip: the same render on the CPU
            encoders.mark_broken(kind, str(exc))

    if not req.output.exists() or req.output.stat().st_size < 1024:
        raise FFmpegFailedError(message_key="processing.render.bad_output")

    encode_seconds = time.monotonic() - t0
    info = probe(req.output)
    thumb = extract_thumbnail(
        req.output, req.output.with_suffix(".jpg"),
        at_seconds=min(max(0.5, info.duration * 0.25), max(0.0, info.duration - 0.2)),
        width=720 if req.is_vertical else 960,
    )

    return RenderResult(
        path=req.output, width=info.width, height=info.height,
        duration=info.duration, size_bytes=info.size_bytes or req.output.stat().st_size,
        thumbnail=thumb,
        stats=render_stats(req, encode_seconds, info.duration),
    )


def _render_segment(req: RenderRequest, index: int, out: Path, *,
                    with_fade: bool, has_audio: bool,
                    on_progress: ProgressFn, cancel_event) -> None:
    """מייצא מקטע אחד – במסלול הפשוט או במסלול ה-EDL."""
    plan = req.plan_for(index)
    reframe = req.reframe if req.is_vertical else None
    if reframe is not None and reframe.is_time_dependent and (
            plan is None or not plan.has_bound_reframe):
        # מסגור שמתחלף באמצע הקליפ דורש ביטים נפרדים – מסלול ה-EDL
        start, end = req.segments[index]
        base = plan or EditPlan(beats=[Beat(0.0, max(0.05, end - start))],
                                window_start=start, window_end=end,
                                raw_duration=max(0.05, end - start))
        part = reframe if len(req.segments) == 1 else reframe.slice(
            start - req.segments[0][0], end - req.segments[0][0])
        plan = bind_reframe(base, part)
        if index < len(req.edit_plans):
            req.edit_plans[index] = plan
        else:
            req.edit_plans = list(req.edit_plans) + [None] * (index - len(req.edit_plans)) + [plan]
    if plan is not None and (not plan.is_trivial or
                             (len(plan.beats) > 1 and plan.has_bound_reframe)):
        _render_with_edl(req, index, plan, out, with_fade=with_fade,
                         has_audio=has_audio, on_progress=on_progress,
                         cancel_event=cancel_event)
    else:
        _render_plain(req, index, out, with_fade=with_fade,
                      has_audio=has_audio, on_progress=on_progress,
                      cancel_event=cancel_event)


def _render_plain(req: RenderRequest, index: int, out: Path, *,
                  with_fade: bool, has_audio: bool,
                  on_progress: ProgressFn, cancel_event) -> None:
    start, end = req.segments[index]
    plan = req.plan_for(index)
    beat_reframe = None
    head = 0.0
    if plan is not None and plan.beats:
        # תכנית טריוויאלית: ביט יחיד שאולי הידק את הראש/זנב
        b = plan.beats[0]
        head = b.src_start
        beat_reframe = b.reframe
        start, end = start + b.src_start, start + b.src_end
    duration = max(0.05, end - start)

    args: list[str] = _hw_decode(req)
    args += ["-ss", f"{max(0.0, start):.4f}", "-i", str(req.source),
             "-t", f"{duration:.4f}"]

    # ה-seek מתחיל ב-head, אבל המסגור מתוזמן לפי תחילת החלון
    if beat_reframe is not None:
        geometry = _geometry_filter(req, reframe=beat_reframe, time_shift=-head)
    elif len(req.segments) > 1 and req.reframe is not None:
        part = req.reframe.slice(req.segments[index][0] - req.segments[0][0],
                                 req.segments[index][1] - req.segments[0][0])
        geometry = _geometry_filter(req, reframe=part, time_shift=-head)
    else:
        geometry = _geometry_filter(req, time_shift=-head)
    vf = [geometry]
    vf += _post_filters(req, with_fade=with_fade, duration=duration,
                        include_polish=True, subtitle_path=req.subtitle_for(index))
    args += ["-vf", ",".join(p for p in vf if p)]
    args += _video_codec_args(req)

    if has_audio:
        af = _audio_filter(req, with_fade=with_fade, duration=duration)
        if af:
            args += ["-af", af]
        args += _audio_args(req)
    else:
        args += ["-an"]

    args += ["-movflags", "+faststart", "-map_metadata", "-1",
             "-metadata", "encoder=Polixor", str(out)]
    run_ffmpeg(args, total_seconds=duration, on_progress=on_progress,
               cancel_event=cancel_event)


def _render_with_edl(req: RenderRequest, index: int, plan: EditPlan, out: Path, *,
                     with_fade: bool, has_audio: bool,
                     on_progress: ProgressFn, cancel_event) -> None:
    """מבצע תכנית עריכה מלאה בגרף פילטרים אחד."""
    start, end = req.segments[index]
    window = max(0.05, end - start)
    style = req.style

    if req.is_vertical and req.reframe is not None and not plan.has_bound_reframe:
        part = req.reframe if len(req.segments) == 1 else req.reframe.slice(
            start - req.segments[0][0], end - req.segments[0][0])
        plan = bind_reframe(plan, part)

    graph, v_label, a_label = plan_to_filtergraph(
        plan,
        geometry_filter=_geometry_filter(req),
        out_width=req.width, out_height=req.height,
        style=style, has_audio=has_audio,
        geometry_builder=lambda i, b: _geometry_filter(
            req, label=f"g{i}", reframe=b.reframe),
    )

    out_duration = plan.out_duration
    post = _post_filters(req, with_fade=with_fade, duration=out_duration,
                         include_polish=False,   # הצבע כבר הוחל בגרף
                         subtitle_path=req.subtitle_for(index))
    if post:
        graph += f";{v_label}{','.join(post)}[vout]"
        v_label = "[vout]"

    if has_audio:
        af = _audio_filter(req, with_fade=with_fade, duration=out_duration)
        if af:
            graph += f";{a_label}{af}[aout]"
            a_label = "[aout]"

    args: list[str] = _hw_decode(req)
    args += ["-ss", f"{max(0.0, start):.4f}", "-i", str(req.source),
             "-t", f"{window:.4f}",
             "-filter_complex", graph,
             "-map", v_label]
    if has_audio:
        args += ["-map", a_label]
    args += _video_codec_args(req)
    if has_audio:
        args += _audio_args(req)
    else:
        args += ["-an"]
    args += ["-movflags", "+faststart", "-map_metadata", "-1",
             "-metadata", "encoder=Polixor", str(out)]

    log.info("EDL render: %d beats, %.1fs → %.1fs (%s)",
             len(plan.beats), plan.raw_duration, out_duration, style.name)
    run_ffmpeg(args, total_seconds=out_duration, on_progress=on_progress,
               cancel_event=cancel_event)


def _render_multi(req: RenderRequest, *, has_audio: bool,
                  on_progress: ProgressFn, cancel_event) -> None:
    """מייצא כל מקטע בנפרד ואז מחבר – מהיר ועמיד יותר מגרף ענק אחד."""
    work = Path(req.work_dir or req.output.parent) / f".{req.output.stem}_parts"
    work.mkdir(parents=True, exist_ok=True)

    total = max(0.01, req.total_duration)
    done = 0.0
    parts: list[Path] = []

    try:
        for i, seg in enumerate(req.segments):
            plan = req.plan_for(i)
            seg_out = plan.out_duration if plan else max(0.05, seg[1] - seg[0])
            part = work / f"part_{i:03d}.mp4"

            def part_progress(frac: float, _base=done, _dur=seg_out) -> None:
                if on_progress:
                    on_progress(min(0.98, (_base + frac * _dur) / total))

            _render_segment(req, i, part, with_fade=req.transitions, has_audio=has_audio,
                            on_progress=part_progress, cancel_event=cancel_event)
            parts.append(part)
            done += seg_out

        list_file = work / "concat.txt"
        list_file.write_text(
            "\n".join(f"file '{p.as_posix()}'" for p in parts) + "\n",
            encoding="utf-8",
        )
        run_ffmpeg(
            ["-f", "concat", "-safe", "0", "-i", str(list_file),
             "-c", "copy", "-movflags", "+faststart", str(req.output)],
            total_seconds=total,
            on_progress=lambda f: on_progress(min(1.0, 0.98 + 0.02 * f))
            if on_progress else None,
            cancel_event=cancel_event,
        )
    finally:
        for p in parts:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            (work / "concat.txt").unlink(missing_ok=True)
            work.rmdir()
        except OSError:
            pass


# --------------------------------------------------------------------------
# יעדי רזולוציה
# --------------------------------------------------------------------------
def target_resolution(requested: str, source_w: int, source_h: int,
                      *, vertical: bool) -> tuple[int, int]:
    """
    לא מגדילים מעבר לאיכות המקור: אם השידור ב-720p, שורט ייוצא
    ב-720x1280 ולא ב-1080x1920 מתוח.

    `vertical` פירושו „נחתך מהמקור" – כל יחס שאינו 16:9 (9:16, 1:1,
    4:5). התקרה זהה לכולם: גובה הפלט עד פי 16/9 מגובה המקור.
    """
    try:
        rw, rh = (int(x) for x in requested.lower().split("x"))
    except (ValueError, AttributeError):
        rw, rh = (1080, 1920) if vertical else (1920, 1080)

    if source_h > 0:
        if vertical:
            limit_h = min(rh, max(480, source_h * 16 // 9))
            scale = limit_h / rh
            rw = max(2, int(round(rw * scale)) // 2 * 2)
            rh = max(2, int(limit_h) // 2 * 2)
        else:
            if source_h < rh:
                scale = source_h / rh
                rw = max(2, int(rw * scale) // 2 * 2)
                rh = max(2, source_h // 2 * 2)
    return rw, rh


def aspect_name(width: int, height: int) -> str:
    ratio = width / max(1, height)
    for name, value in (("9:16", 9 / 16), ("4:5", 4 / 5), ("1:1", 1.0),
                        ("16:9", 16 / 9)):
        if abs(ratio - value) < 0.02:
            return name
    return "16:9" if ratio >= 1.0 else "9:16"


def build_request(
    *,
    source: Path,
    output: Path,
    segments: list[tuple[float, float]],
    vertical: bool,
    settings: AppSettings,
    reframe: Optional[ReframePlan],
    subtitle_path: Optional[Path],
    source_info: dict,
    transitions: bool,
    work_dir: Optional[Path] = None,
    edit_style: str = "clean",
    edit_plans: Optional[list[Optional[EditPlan]]] = None,
    audio_chain: str = "",
    resolution: Optional[str] = None,
    subtitle_parts: Optional[list[Optional[Path]]] = None,
) -> RenderRequest:
    """
    `resolution` קובע את גודל הפלט (למשל '1080x1350'). בלעדיו נבחרת
    רזולוציית השורט או הקליפ הארוך לפי `vertical`, כמו קודם.
    """
    src_w = int(source_info.get("width") or 1920)
    src_h = int(source_info.get("height") or 1080)
    requested = resolution or (settings.short_resolution if vertical
                               else settings.long_resolution)
    try:
        rw, rh = (int(x) for x in requested.lower().split("x"))
        cropped = aspect_name(rw, rh) != "16:9"
    except (ValueError, AttributeError):
        cropped = vertical
    w, h = target_resolution(requested, src_w, src_h, vertical=cropped)

    return RenderRequest(
        source=source, output=output, segments=segments,
        width=w, height=h, aspect=aspect_name(w, h) if cropped else "16:9",
        reframe=reframe, subtitle_path=subtitle_path,
        audio_normalize=settings.audio_normalize, quality=settings.video_quality,
        hw_accel=settings.hw_accel, transitions=transitions,
        work_dir=work_dir, source_info=source_info,
        edit_style=edit_style, edit_plans=list(edit_plans or []),
        audio_chain=audio_chain, subtitle_parts=list(subtitle_parts or []),
    )
