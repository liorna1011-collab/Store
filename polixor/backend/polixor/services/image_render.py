"""
הכנסת תמונות שנוצרו לתוך הקליפ המרונדר.

זהו מעבר **נפרד** אחרי הרינדור הרגיל, ובכוונה: מנוע העריכה, ה-EDL
והכתוביות אינם נוגעים בתמונות כלל. אם המעבר הזה נכשל, הקליפ המקורי
נשאר שלם ותקין – הכשל נרשם ומדווח, ולא מפיל את הייצוא.

שני סוגי שיבוץ
--------------
1. **שיבוץ בציר הזמן** (פתיח / סיום / הכנסה מלאה) – מאריך את הקליפ.
   הווידאו נחתך בנקודת ההכנסה, נוצר קטע וידאו מהתמונה, והכול מחובר
   מחדש. משתמשים במסנן concat (ולא ב-demuxer) כדי שנקודת החיתוך תהיה
   מדויקת לפריים ולא תיצמד לפריים מפתח.

2. **שכבה מעל/מתחת** (בי-רול / שכבה / רקע) – אינו משנה את האורך.
   מתבצע בגרף פילטרים אחד עם overlay ו-enable='between(t,a,b)'.

כתוביות שכבר צרובות בתמונה נעות יחד עם הווידאו, ולכן נשארות מסונכרנות.
שורות הכתוביות ב-DB מוזזות בנפרד (`shift_cues_for_inserts`) כדי
שהעורך וייצוא ה-SRT יישארו תואמים.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

from ..errors import FFmpegFailedError, PolixorError
from ..util.ffmpeg import probe, run_ffmpeg

log = logging.getLogger("polixor.images.render")

# תמונה קצרה מזה לא נראית; ארוכה מזה משעממת ומנפחת את הקליפ
MIN_IMAGE_SECONDS = 0.3
MAX_IMAGE_SECONDS = 15.0
# מעבר רך בכניסה וביציאה מתמונה בציר הזמן
IMAGE_FADE = 0.25

POSITION_EXPR: dict[str, tuple[str, str]] = {
    "center": ("(W-w)/2", "(H-h)/2"),
    "top_left": ("{m}", "{m}"),
    "top_right": ("W-w-{m}", "{m}"),
    "bottom_left": ("{m}", "H-h-{m}"),
    "bottom_right": ("W-w-{m}", "H-h-{m}"),
}


@dataclass
class ImageRenderResult:
    path: Path
    duration: float
    inserts: list[dict[str, float]] = field(default_factory=list)
    composited: int = 0
    added_seconds: float = 0.0
    note: str = ""
    failed: list[str] = field(default_factory=list)


def _esc(path: Path) -> str:
    """בריחה לנתיב בתוך ארגומנט פילטר."""
    from .render import escape_filter_path
    return escape_filter_path(path)


def _clamp_duration(value: float) -> float:
    return min(MAX_IMAGE_SECONDS, max(MIN_IMAGE_SECONDS, float(value or 0.0)))


# --------------------------------------------------------------------------
# שכבות: בי-רול, שכבה, רקע
# --------------------------------------------------------------------------
def build_composite_graph(placements: Sequence[dict[str, Any]], *,
                          width: int, height: int) -> tuple[str, str, int]:
    """
    בונה גרף פילטרים לשכבות.

    מחזיר (גרף, תווית וידאו סופית, מספר קלטים נוספים). הקלט הראשון
    (`[0:v]`) הוא הווידאו; כל תמונה מגיעה כקלט נוסף בסדר הרשימה.
    """
    if not placements:
        return "", "[0:v]", 0

    parts: list[str] = []
    current = "[0:v]"
    stream = 1

    backgrounds = [p for p in placements if p["role"] == "background"]
    layers = [p for p in placements if p["role"] in ("broll", "overlay")]

    for p in backgrounds:
        label_bg = f"[bg{stream}]"
        label_out = f"[cb{stream}]"
        # הרקע ממלא את הפריים; הווידאו מוקטן ומונח מעליו במרכז
        parts.append(
            f"[{stream}:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1{label_bg}")
        parts.append(
            f"{current}scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"setsar=1[fgv{stream}]")
        parts.append(
            f"{label_bg}[fgv{stream}]overlay=(W-w)/2:(H-h)/2{label_out}")
        current = label_out
        stream += 1

    for p in layers:
        a = max(0.0, float(p["at_time"]))
        b = a + _clamp_duration(p["duration"])
        opacity = min(1.0, max(0.05, float(p.get("opacity", 1.0))))
        label_img = f"[im{stream}]"
        label_out = f"[cv{stream}]"

        if p["role"] == "broll":
            # בי-רול מכסה את כל הפריים; האודיו ממשיך לרוץ מתחת
            if p.get("fit", "cover") == "cover":
                scale = (f"scale={width}:{height}:"
                         f"force_original_aspect_ratio=increase,"
                         f"crop={width}:{height}")
            else:
                scale = (f"scale={width}:{height}:"
                         f"force_original_aspect_ratio=decrease,"
                         f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black")
            x_expr, y_expr = "(W-w)/2", "(H-h)/2"
        else:
            target_w = max(16, int(width * min(1.0, max(0.05,
                                                        float(p.get("scale", 0.35))))))
            scale = f"scale={target_w}:-2"
            margin = max(12, int(width * 0.035))
            xt, yt = POSITION_EXPR.get(p.get("position", "center"),
                                       POSITION_EXPR["center"])
            x_expr = xt.format(m=margin)
            y_expr = yt.format(m=margin)

        chain = [scale, "setsar=1", "format=rgba"]
        if opacity < 0.999:
            chain.append(f"colorchannelmixer=aa={opacity:.3f}")
        parts.append(f"[{stream}:v]{','.join(chain)}{label_img}")
        parts.append(
            f"{current}{label_img}overlay={x_expr}:{y_expr}:"
            f"enable='between(t,{a:.3f},{b:.3f})'{label_out}")
        current = label_out
        stream += 1

    return ";".join(parts), current, stream - 1


def composite_inputs(placements: Sequence[dict[str, Any]]) -> list[str]:
    """ארגומנטי הקלט של התמונות, בסדר שבו הגרף מצפה להם."""
    args: list[str] = []
    ordered = ([p for p in placements if p["role"] == "background"]
               + [p for p in placements if p["role"] in ("broll", "overlay")])
    for p in ordered:
        args += ["-loop", "1", "-t", "1", "-i", str(p["path"])]
    return args


# --------------------------------------------------------------------------
# קטע וידאו מתמונה
# --------------------------------------------------------------------------
def render_image_clip(image: Path, out: Path, *, seconds: float,
                      width: int, height: int, fps: float,
                      codec_args: Sequence[str],
                      audio_args: Optional[Sequence[str]],
                      fit: str = "cover", cancel_event=None) -> Path:
    """
    הופך תמונה סטילס לקטע וידאו בגיאומטריה ובקידוד של הקליפ.

    נוסף פס קול שקט כשהקליפ מכיל אודיו, אחרת החיבור היה מאבד את
    הסנכרון בין הווידאו לאודיו בכל הכנסה.
    """
    seconds = _clamp_duration(seconds)
    if fit == "contain":
        geometry = (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black")
    else:
        geometry = (f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                    f"crop={width}:{height}")

    vf = [geometry, "setsar=1", f"fps={max(1.0, fps):.4f}"]
    if seconds > IMAGE_FADE * 2.5:
        vf.append(f"fade=t=in:st=0:d={IMAGE_FADE:.2f}")
        vf.append(f"fade=t=out:st={seconds - IMAGE_FADE:.3f}:d={IMAGE_FADE:.2f}")
    vf.append("format=yuv420p")

    args = ["-loop", "1", "-t", f"{seconds:.3f}", "-i", str(image)]
    if audio_args is not None:
        args += ["-f", "lavfi", "-t", f"{seconds:.3f}",
                 "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    args += ["-vf", ",".join(vf), *codec_args]
    if audio_args is not None:
        args += list(audio_args)
    else:
        args += ["-an"]
    args += ["-t", f"{seconds:.3f}", "-movflags", "+faststart", str(out)]

    run_ffmpeg(args, total_seconds=seconds, cancel_event=cancel_event)
    if not out.exists():
        raise FFmpegFailedError("יצירת קטע וידאו מהתמונה נכשלה.")
    return out


# --------------------------------------------------------------------------
# שרשור: וידאו + תמונות בציר הזמן
# --------------------------------------------------------------------------
def build_timeline_plan(placements: Sequence[dict[str, Any]],
                        clip_duration: float) -> list[dict[str, Any]]:
    """
    ממיין את שיבוצי ציר הזמן ומחשב היכן כל תמונה נכנסת.

    `at` הוא זמן בקליפ **המקורי**. `shift` הוא כמה זמן נוסף מצטבר
    לפניו – מה שנדרש כדי להזיז את הכתוביות בהמשך.
    """
    intro = [p for p in placements if p["role"] == "intro"]
    outro = [p for p in placements if p["role"] == "outro"]
    inserts = sorted((p for p in placements if p["role"] == "insert"),
                     key=lambda p: float(p["at_time"]))

    plan: list[dict[str, Any]] = []
    shift = 0.0
    for p in intro:
        dur = _clamp_duration(p["duration"])
        plan.append({**p, "at": 0.0, "shift_before": shift, "seconds": dur})
        shift += dur
    for p in inserts:
        at = min(max(0.0, float(p["at_time"])), clip_duration)
        dur = _clamp_duration(p["duration"])
        plan.append({**p, "at": at, "shift_before": shift, "seconds": dur})
        shift += dur
    for p in outro:
        dur = _clamp_duration(p["duration"])
        plan.append({**p, "at": clip_duration, "shift_before": shift,
                     "seconds": dur})
        shift += dur
    return plan


def shift_cues_for_inserts(cues: Sequence[Any],
                           plan: Sequence[dict[str, Any]]) -> int:
    """
    מזיז שורות כתוביות לפי התמונות שנוספו לפניהן.

    מוחזר מספר השורות שהוזזו. שורה שנופלת בדיוק בנקודת ההכנסה
    נדחפת אחרי התמונה, כדי שלא תוצג מעליה.
    """
    if not plan or not cues:
        return 0
    points = sorted(
        ((float(p["at"]), float(p["seconds"])) for p in plan),
        key=lambda t: t[0])

    moved = 0
    for cue in cues:
        delta = sum(dur for at, dur in points if at <= float(cue.start) + 1e-6)
        if delta > 0:
            cue.start = float(cue.start) + delta
            cue.end = float(cue.end) + delta
            if getattr(cue, "words", None):
                cue.words = [
                    {**w, "start": float(w.get("start", 0.0)) + delta,
                     "end": float(w.get("end", 0.0)) + delta}
                    for w in cue.words if isinstance(w, dict)
                ]
            moved += 1
    return moved


def concat_parts(parts: Sequence[Path], out: Path, *,
                 codec_args: Sequence[str], audio_args: Optional[Sequence[str]],
                 width: int, height: int, fps: float,
                 cancel_event=None) -> Path:
    """
    מחבר קטעים בעזרת מסנן concat.

    concat כמסנן ולא כ-demuxer: הוא מקודד מחדש, אבל מבטיח תפר מדויק
    לפריים גם כשהקטעים נחתכו שלא בפריים מפתח – וזה מה שחשוב כאן.
    """
    usable = [p for p in parts if p.exists() and p.stat().st_size > 1024]
    if not usable:
        raise FFmpegFailedError("אין קטעים לחיבור.")
    if len(usable) == 1:
        return usable[0]

    args: list[str] = []
    for p in usable:
        args += ["-i", str(p)]

    has_audio = audio_args is not None
    pre: list[str] = []
    labels: list[str] = []
    for i in range(len(usable)):
        pre.append(f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio="
                   f"decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
                   f"setsar=1,fps={max(1.0, fps):.4f},format=yuv420p[v{i}]")
        labels.append(f"[v{i}]")
        if has_audio:
            pre.append(f"[{i}:a]aformat=sample_fmts=fltp:sample_rates=48000:"
                       f"channel_layouts=stereo[a{i}]")
            labels.append(f"[a{i}]")

    graph = ";".join(pre) + ";" + "".join(labels)
    graph += f"concat=n={len(usable)}:v=1:a={1 if has_audio else 0}[vout]"
    if has_audio:
        graph += "[aout]"

    args += ["-filter_complex", graph, "-map", "[vout]"]
    if has_audio:
        args += ["-map", "[aout]"]
    args += list(codec_args)
    if has_audio:
        args += list(audio_args or [])
    else:
        args += ["-an"]
    args += ["-movflags", "+faststart", str(out)]

    total = sum(_safe_duration(p) for p in usable)
    run_ffmpeg(args, total_seconds=total, cancel_event=cancel_event)
    if not out.exists():
        raise FFmpegFailedError("חיבור הקטעים עם התמונות נכשל.")
    return out


def _safe_duration(path: Path) -> float:
    try:
        return float(probe(path).duration)
    except Exception:
        return 0.0


def cut_range(source: Path, out: Path, *, start: float, end: float,
              codec_args: Sequence[str], audio_args: Optional[Sequence[str]],
              cancel_event=None) -> Optional[Path]:
    """חותך טווח מהקליפ בקידוד מחדש, לחיתוך מדויק לפריים."""
    duration = end - start
    if duration <= 0.04:
        return None
    args = ["-ss", f"{max(0.0, start):.4f}", "-i", str(source),
            "-t", f"{duration:.4f}", *codec_args]
    if audio_args is not None:
        args += list(audio_args)
    else:
        args += ["-an"]
    args += ["-movflags", "+faststart", str(out)]
    run_ffmpeg(args, total_seconds=duration, cancel_event=cancel_event)
    return out if out.exists() else None


# --------------------------------------------------------------------------
# נקודת הכניסה: החלת כל השיבוצים על קליפ מרונדר
# --------------------------------------------------------------------------
def apply_placements(base: Path, placements: Sequence[dict[str, Any]], *,
                     width: int, height: int, fps: float,
                     has_audio: bool, work_dir: Path, out: Path,
                     codec_args: Sequence[str],
                     audio_args: Optional[Sequence[str]] = None,
                     cancel_event=None) -> ImageRenderResult:
    """
    מחיל שיבוצי תמונות על קליפ שכבר רונדר.

    הקובץ המקורי אינו נמחק ואינו משתנה: התוצאה נכתבת ל-`out`. אם
    שלב כלשהו נכשל, נזרקת שגיאה והקורא יכול פשוט להמשיך עם המקור.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    audio = list(audio_args or []) if has_audio else None

    usable = [p for p in placements
              if p.get("role") != "thumbnail" and Path(p.get("path", "")).is_file()]
    skipped = [p for p in placements
               if p.get("role") != "thumbnail" and not Path(p.get("path", "")).is_file()]

    composite = [p for p in usable if p["role"] in ("broll", "overlay", "background")]
    timeline = [p for p in usable if p["role"] in ("intro", "outro", "insert")]

    result = ImageRenderResult(path=base, duration=_safe_duration(base),
                               failed=[f"תמונה חסרה: {p.get('image_id', '?')}"
                                       for p in skipped])
    if not composite and not timeline:
        return result

    current = base

    # ---- שלב 1: שכבות ----
    if composite:
        graph, v_label, extra = build_composite_graph(
            composite, width=width, height=height)
        staged = work_dir / f"{out.stem}_layers.mp4"
        args = ["-i", str(current)] + composite_inputs(composite)
        args += ["-filter_complex", graph, "-map", v_label]
        if has_audio:
            args += ["-map", "0:a?"]
        args += list(codec_args)
        args += list(audio or []) if has_audio else ["-an"]
        args += ["-movflags", "+faststart", str(staged)]
        run_ffmpeg(args, total_seconds=result.duration, cancel_event=cancel_event)
        if not staged.exists():
            raise FFmpegFailedError("החלת שכבות התמונה נכשלה.")
        current = staged
        result.composited = len(composite)

    # ---- שלב 2: ציר הזמן ----
    if timeline:
        clip_duration = _safe_duration(current)
        plan = build_timeline_plan(timeline, clip_duration)
        parts: list[Path] = []
        cursor = 0.0
        idx = 0

        for item in plan:
            at = float(item["at"])
            if at > cursor + 0.04:
                chunk = cut_range(
                    current, work_dir / f"{out.stem}_v{idx:02d}.mp4",
                    start=cursor, end=at, codec_args=codec_args,
                    audio_args=audio, cancel_event=cancel_event)
                if chunk:
                    parts.append(chunk)
                cursor = at
            img_part = render_image_clip(
                Path(item["path"]), work_dir / f"{out.stem}_i{idx:02d}.mp4",
                seconds=float(item["seconds"]), width=width, height=height,
                fps=fps, codec_args=codec_args, audio_args=audio,
                fit=item.get("fit", "cover"), cancel_event=cancel_event)
            parts.append(img_part)
            result.inserts.append({"at": at, "seconds": float(item["seconds"])})
            result.added_seconds += float(item["seconds"])
            idx += 1

        if cursor < clip_duration - 0.04:
            tail = cut_range(
                current, work_dir / f"{out.stem}_v{idx:02d}.mp4",
                start=cursor, end=clip_duration, codec_args=codec_args,
                audio_args=audio, cancel_event=cancel_event)
            if tail:
                parts.append(tail)

        final = concat_parts(parts, out, codec_args=codec_args,
                             audio_args=audio, width=width, height=height,
                             fps=fps, cancel_event=cancel_event)
        if final != out:
            import shutil
            shutil.copy2(final, out)
        current = out
    elif current != base:
        import shutil
        shutil.move(str(current), str(out))
        current = out

    result.path = current
    result.duration = _safe_duration(current)
    parts_note = []
    if result.composited:
        parts_note.append(f"{result.composited} שכבות")
    if result.inserts:
        parts_note.append(f"{len(result.inserts)} תמונות בציר הזמן "
                          f"(+{result.added_seconds:.1f} שניות)")
    result.note = " · ".join(parts_note)
    return result
