"""
יצירת קליפים ממועמדים: תכנית עריכה, כתוביות, מסגור, רינדור, מאסטרינג,
מוזיקה ובדיקת איכות – לכל קליפ בנפרד.

הפייפליין מחליט *אילו* קטעים ייכנסו; המודול הזה אחראי *איך* כל קטע
הופך לקובץ MP4 שנבדק. הוא עובד מול אובייקט הקשר (`JobContext`) של
הפייפליין, אבל אינו תלוי בסדר השלבים.
"""

from __future__ import annotations

import logging
import time
import subprocess
from pathlib import Path
from typing import Any, Optional

from . import i18n
from .db import session_scope
from .errors import JobCancelledError, PolixorError
from .events import BUS
from .models import Clip, ClipKind, ClipStatus, SubtitleCue, new_id
from .services import (
    audio_mastering, caption_engine, director_bridge, editing, music_engine,
    pacing_engine, reframe, render, render_qa, selection, semantics,
    subtitle_clean, subtitle_render, subtitle_style, subtitles, video_director,
)
from .util import timing
from .util.ffmpeg import ffmpeg_bin
from .util.fs import safe_filename, unique_path

log = logging.getLogger("polixor.clips")


def aspect_of(resolution: str) -> str:
    """יחס המסך של רזולוציה ('1080x1920' → '9:16')."""
    try:
        w, h = (int(x) for x in str(resolution).lower().split("x"))
    except (ValueError, AttributeError):
        return "16:9"
    ratio = w / max(1, h)
    for name, value in (("9:16", 9 / 16), ("4:5", 4 / 5), ("1:1", 1.0),
                        ("16:9", 16 / 9)):
        if abs(ratio - value) < 0.02:
            return name
    return "16:9" if ratio >= 1.0 else "9:16"


def project_subtitle_style(ctx) -> Optional[dict[str, Any]]:
    """
    סגנון הכתוביות (v2) שהמשתמש בחר בפרויקט, או None למשימה ישנה.

    בפרויקט הסגנון הוא מקור האמת: הגדלים יחסיים לפריים ומומרים לפיקסלים
    רק בכתיבת ה-ASS, ולכן אין כאן התאמות נוספות לפי קצב העריכה.
    """
    if not getattr(ctx, "is_project", False):
        return None
    subs = (getattr(ctx, "config", None) or {}).get("subtitles") or {}
    style = subs.get("style")
    return dict(style) if isinstance(style, dict) and style else None


def style_margin_v(style, width: int, height: int) -> int:
    """המרחק מהקצה שהכתוביות תופסות, לבדיקת האזור הבטוח ב-QA."""
    if isinstance(style, dict) and subtitle_style.is_v2(style):
        return subtitle_render.style_margin_v(style, width, height)
    return int(getattr(style, "margin_v", 0) or 0)


def write_subtitles(cues: list, *, style, v2: bool, work_dir: Path, clip_id: str,
                    edit_plans: list, width: int, height: int, language: str,
                    title_text: str, multipart: bool) -> tuple[Optional[Path],
                                                               list[Optional[Path]]]:
    """קובץ ASS לקליפ (ולכל חלק בקליפ מרובה חלקים), בכותב המתאים לסגנון."""
    sub_path = work_dir / f"{clip_id}.ass"
    parts: list[Optional[Path]] = []
    if v2:
        subtitle_render.write_ass_v2(cues, sub_path, width=width, height=height,
                                     style=style, language=language,
                                     title_text=title_text)
        if multipart:
            parts = subtitle_render.write_ass_v2_parts(
                cues, work_dir / f"{clip_id}_part", edit_plans=edit_plans,
                width=width, height=height, style=style, language=language,
                title_text=title_text)
        return sub_path, parts
    subtitles.write_ass(cues, sub_path, width=width, height=height, style=style,
                        title_text=title_text)
    if multipart:
        # באג 2: כל חלק נצרב בנפרד, ולכן כל חלק מקבל ASS משלו
        # שהזמנים בו מתחילים באפס של החלק.
        parts = subtitles.write_ass_parts(
            cues, work_dir / f"{clip_id}_part", edit_plans=edit_plans,
            width=width, height=height, style=style, title_text=title_text)
    return sub_path, parts


# --------------------------------------------------------------------------
# רינדור קבוצת מועמדים
# --------------------------------------------------------------------------
def render_group(ctx, cands: list[selection.Candidate], *, short: bool) -> list[str]:
    """
    מייצא רשימת מועמדים. `short` קובע את סוג הקליפ ואת הרזולוציה:
    שורטים לפי `short_resolution` (9:16, 1:1, 4:5 או 16:9), וקליפים
    ארוכים ב-`long_resolution`. מחזיר את מזהי הקליפים שנוצרו.
    """
    total = len(cands)
    created: list[str] = []
    for i, cand in enumerate(cands):
        ctx.reporter.check_cancel()
        clip_id = render_candidate(ctx, cand, index=i, total=total, short=short)
        if clip_id:
            created.append(clip_id)
    return created


def render_candidate(ctx, cand: selection.Candidate, *, index: int, total: int,
                     short: bool) -> Optional[str]:
    """מייצא מועמד אחד. מחזיר את מזהה הקליפ, או None אם הייצוא נכשל."""
    s = ctx.settings
    base = index / max(1, total)
    clip_id = new_id()
    requested = s.short_resolution if short else s.long_resolution
    aspect = aspect_of(requested)
    vertical = aspect != "16:9"
    kind = (ClipKind.HIGHLIGHTS if cand.kind == "highlights"
            else (ClipKind.SHORT if short else ClipKind.LONG))

    segments = cand.segments or [(cand.start, cand.end)]
    src_w = int(ctx.source_info.get("width") or 1920)
    src_h = int(ctx.source_info.get("height") or 1080)
    out_w, out_h = render.target_resolution(requested, src_w, src_h, vertical=vertical)

    # -- תכנית העריכה: מה נחתך, איפה משנים זווית, מה מואץ --
    edit_style = editing.style_from_settings(s, vertical=short)
    t_dir = time.perf_counter()
    director_plans = direct_segments(
        ctx, segments, edit_style=edit_style, src_w=src_w, src_h=src_h,
        out_w=out_w, out_h=out_h)
    if director_plans:
        edit_plans = [director_bridge.to_edit_plan(p) for p in director_plans]
    else:
        edit_plans = [
            editing.build_edit_plan(
                clip_start=s0, clip_end=s1, peak_time=cand.peak_time,
                style=edit_style, audio=ctx.audio_feats, timeline=ctx.timeline,
                transcript=ctx.transcript, silences=ctx.silences)
            for s0, s1 in segments
        ]
    edited_duration = sum(p.out_duration for p in edit_plans)
    timing.record("render.director", time.perf_counter() - t_dir, cand.duration)

    v2_style = project_subtitle_style(ctx)
    with timing.substage("render.subtitles", media_seconds=cand.duration):
        cues = build_cues_for(ctx, cand, segments, vertical=vertical, play_width=out_w,
                              frame_height=out_h, edit_plans=edit_plans,
                              director_plans=director_plans, v2_style=v2_style)
    lang = subtitles.detect_cue_language(cues) or (
        ctx.transcript.language if ctx.transcript else "")

    if v2_style is not None:
        style = subtitle_style.clamp_style(v2_style, language=lang)
    else:
        style = subtitles.style_for_clip(s, vertical=vertical, language=lang,
                                         frame_height=out_h)
        style.animation = edit_style.caption_animation
        if director_plans:
            preset = caption_preset_for(ctx, director_plans[0])
            # הגודל כבר הותאם לפריים ב-style_for_clip; apply_preset רק
            # מחיל את אופי ה-preset (ראו באג „כיווץ כפול").
            style = caption_engine.apply_preset(style, preset, frame_w=out_w,
                                                frame_h=out_h, vertical=vertical,
                                                already_scaled=True)

    plan = None
    if vertical:
        plan = reframe.plan_reframe(
            ctx.visual_feats, clip_start=cand.start, clip_end=cand.end,
            layout=s.short_layout, manual_camera=ctx.camera_region,
            target_aspect=out_w / max(1, out_h),
            layouts=getattr(ctx, "layout_timeline", None),
            order=getattr(s, "reaction_order", "auto"),
            segments=segments, out_size=(out_w, out_h), src_size=(src_w, src_h))
        if plan and plan.note:
            log.info("reframe[%s]: %s", clip_id, plan.note)
        if plan is not None:
            # כל חלק מקבל את המסגור של הזמן שלו (בזמני החלון של החלק).
            # בלי זה, בקליפ מרובה חלקים כל חלק היה מקבל את מסגור
            # תחילת הקליפ.
            edit_plans = [editing.bind_reframe(
                ep, plan.slice(s0 - cand.start, s1 - cand.start)
                if len(segments) > 1 else plan)
                for ep, (s0, s1) in zip(edit_plans, segments)]

    # -- שורת DB לפני הרינדור, כדי שהממשק יראה התקדמות --
    create_clip_row(ctx, clip_id, cand, kind, aspect, style, plan, cues, lang,
                    edit_plans=edit_plans, edit_style=edit_style,
                    edited_duration=edited_duration, director_plans=director_plans,
                    extra_params={"resolution": requested,
                                  "layout_requested": s.short_layout if vertical else ""})

    stem = clip_basename(cand, index, short)
    sub_path: Optional[Path] = None
    sub_parts: list[Optional[Path]] = []
    if s.subtitles_enabled and cues:
        sub_path, sub_parts = write_subtitles(
            cues, style=style, v2=v2_style is not None, work_dir=ctx.work_dir,
            clip_id=clip_id, edit_plans=edit_plans, width=out_w, height=out_h,
            language=lang, title_text=cand.title if s.title_card_enabled else "",
            multipart=len(segments) > 1)
        subtitles.write_srt(cues, ctx.export_dir / f"{stem}.srt")

    out_path = unique_path(ctx.export_dir / f"{stem}.mp4")
    req = render.build_request(
        source=ctx.source_path, output=out_path, segments=segments,
        vertical=vertical, settings=s, reframe=plan,
        subtitle_path=sub_path, source_info=ctx.source_info,
        transitions=bool(cand.segments), work_dir=ctx.work_dir,
        edit_style=edit_style.name, edit_plans=list(edit_plans),
        # כשהמאסטרינג פעיל הוא מטפל באודיו אחרי הרינדור,
        # ולכן כאן רק שומרים על הסנכרון בלי ליטוש כפול.
        audio_chain=("aresample=async=1:first_pts=0"
                     if s.mastering_enabled else ""),
        resolution=requested, subtitle_parts=sub_parts)

    update_clip(clip_id, status=ClipStatus.RENDERING)
    try:
        with timing.substage("render.ffmpeg", media_seconds=edited_duration):
            result = render.render_clip(
                req,
                on_progress=lambda f, _b=base: ctx.reporter.progress(
                    _b + f / max(1, total),
                    i18n.tr("pipeline.render.clip_progress", i=index + 1, n=total)),
                cancel_event=ctx.cancel_event)
    except JobCancelledError:
        update_clip(clip_id, status=ClipStatus.FAILED,
                    error=i18n.tr("pipeline.render.cancelled"))
        raise
    except PolixorError as exc:
        log.warning("clip render failed: %s", exc.message)
        update_clip(clip_id, status=ClipStatus.FAILED, error=exc.message)
        ctx.reporter.log(i18n.tr("pipeline.render.clip_failed", i=index + 1,
                                 message=exc.message), level="error")
        return None

    finish_clip(ctx, clip_id, result, cand=cand, kind=kind, edit_plans=edit_plans,
                director_plans=director_plans, cues=cues, style=style,
                vertical=vertical)
    return clip_id


def finish_clip(ctx, clip_id: str, result, *, cand, kind: ClipKind,
                edit_plans: list, director_plans: list, cues: list, style,
                vertical: bool) -> ClipStatus:
    """מאסטרינג, מוזיקה ובדיקת איכות על הקובץ שנוצר, וסימון הסטטוס."""
    with timing.substage("render.mastering", media_seconds=result.duration):
        audio_check = master_clip_audio(ctx, result.path)
    with timing.substage("render.music", media_seconds=result.duration):
        music_info = _mix_music(ctx, result.path, director_plans=director_plans,
                                edit_plans=edit_plans, duration=result.duration)
    with timing.substage("render.qa", media_seconds=result.duration):
        qa_report = qa_clip(ctx, result.path, edit_plans=edit_plans, cues=cues,
                            style=style, vertical=vertical,
                            frame=(result.width, result.height))
    patch: dict[str, Any] = {}
    if getattr(result, "stats", None):
        patch["render_stats"] = result.stats
    if audio_check:
        patch["audio"] = audio_check
    if qa_report is not None:
        patch["qa"] = qa_report.to_dict()
    if music_info:
        patch["music"] = music_info
    if patch:
        merge_render_params(clip_id, patch)

    # §25: בעיה שנמצאה פירושה `needs_review`, לא „הושלם".
    flagged = bool(audio_check.get("needs_review")) or bool(
        qa_report is not None and qa_report.needs_review)
    status = ClipStatus.NEEDS_REVIEW if flagged else ClipStatus.READY

    update_clip(clip_id, status=status, file_path=str(result.path),
                file_size=result.size_bytes,
                thumbnail_path=str(result.thumbnail) if result.thumbnail else "",
                width=result.width, height=result.height, duration=result.duration)
    if flagged:
        reasons = list(audio_check.get("issues") or [])
        if qa_report is not None:
            reasons += [f.message for f in qa_report.errors]
        ctx.reporter.log(i18n.tr("pipeline.render.needs_review", title=cand.title,
                                 reasons=" · ".join(reasons[:3])), level="warning")
    BUS.emit("clip.ready", ctx.job_id, clip_id=clip_id, title=cand.title,
             kind=kind.value)
    return status


# --------------------------------------------------------------------------
# פרמטרי רינדור
# --------------------------------------------------------------------------
def merge_render_params(clip_id: str, patch: dict[str, Any]) -> None:
    """מוסיף שדות ל-render_params בלי לדרוס את מה שכבר שם."""
    with session_scope() as s:
        clip = s.get(Clip, clip_id)
        if clip is None:
            return
        params = dict(clip.render_params or {})
        params.update(patch)
        clip.render_params = params


# --------------------------------------------------------------------------
# מוזיקה
# --------------------------------------------------------------------------
def _mix_music(ctx, out_path: Path, *, director_plans: list,
               edit_plans: list, duration: float) -> dict[str, Any]:
    """
    מערבב מוזיקת רקע אל הקליפ **אחרי** המאסטרינג.

    הסדר חשוב: המאסטרינג מודד ומתקן את **הקול**. אילו המוזיקה
    הייתה נכנסת לפניו, הוא היה מנרמל את התערובת, והקול עצמו לא
    היה מגיע ליעד. כאן הקול כבר מוכן, והמוזיקה נכנסת מתחתיו.

    המערכת אינה מספקת מוזיקה — הקובץ מגיע מהמשתמש.
    """
    s = ctx.settings
    if not s.music_enabled or not s.music_path.strip():
        return {}
    music = Path(s.music_path).expanduser()
    if not music.exists():
        log.warning("music file not found: %s", music)
        return {"error": i18n.tr("pipeline.music.missing"), "active": False}

    beats = _beats_in_output_time(director_plans, edit_plans)
    plan = music_engine.plan_music(total_duration=duration, profile=s.music_profile,
                                   music_path=music, beats=beats)
    if not plan.is_active:
        return plan.to_dict()

    flt = music_engine.build_filter(plan, music_input=1, voice_label="0:a",
                                    out_label="mixed")
    mixed = out_path.with_name(out_path.stem + "_music.mp4")
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-y",
           "-i", str(out_path), "-i", str(music),
           "-filter_complex", flt, "-map", "0:v", "-map", "[mixed]",
           "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
           "-shortest", str(mixed)]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except Exception as exc:                            # noqa: BLE001
        log.warning("music mix failed: %s", exc)
        return {**plan.to_dict(), "error": str(exc), "active": False}

    if res.returncode != 0 or not mixed.exists():
        tail = (res.stderr or "").strip().splitlines()[-2:]
        log.warning("music mix failed: %s", " / ".join(tail))
        mixed.unlink(missing_ok=True)
        return {**plan.to_dict(), "error": " / ".join(tail), "active": False}

    mixed.replace(out_path)
    return plan.to_dict()


def _beats_in_output_time(director_plans: list, edit_plans: list) -> list:
    """
    ממפה את הביטים מזמני המקור לזמני הפלט.

    הביטים של הבמאי הם בזמני השידור. אחרי החיתוכים הקליפ קצר
    יותר, ועקומת מוזיקה שנבנתה על זמני המקור הייתה מחליקה ביחס
    לתמונה ככל שמתקדמים.
    """
    out: list[dict[str, Any]] = []
    offset = 0.0
    for dp, ep in zip(director_plans or [], edit_plans or []):
        base = dp.source_start
        for b in (dp.beats or []):
            try:
                s0 = ep.map_time(float(b["start"]) - base)
                s1 = ep.map_time(float(b["end"]) - base)
            except (KeyError, TypeError, ValueError):
                continue
            if s0 is None or s1 is None or s1 <= s0:
                continue
            out.append({"start": s0 + offset, "end": s1 + offset,
                        "role": b.get("role") or "main_idea"})
        offset += ep.out_duration
    return out


# --------------------------------------------------------------------------
# בדיקת איכות ומאסטרינג
# --------------------------------------------------------------------------
def qa_clip(ctx, out_path: Path, *, edit_plans: list, cues: list, style,
            vertical: bool, frame: tuple[int, int] = (0, 0)):
    """
    בדיקת איכות על הקובץ הסופי (§25).

    האורך הצפוי מגיע מתכנית העריכה ולא מהחלון המקורי — זה מה
    שמאפשר לתפוס פער בין מה שתוכנן למה שרונדר בפועל.
    """
    expected = sum(p.out_duration for p in (edit_plans or []) if p)
    try:
        return render_qa.check_render(
            out_path, expected_duration=expected,
            expect_audio=bool(ctx.source_info.get("has_audio", True)),
            vertical=vertical, cues=cues or None,
            safe_margin_v=style_margin_v(style, *frame) if all(frame)
            else int(getattr(style, "margin_v", 0) or 0))
    except Exception as exc:                            # noqa: BLE001
        log.warning("post-render QA failed: %s", exc, exc_info=True)
        return None


def master_clip_audio(ctx, out_path: Path) -> dict[str, Any]:
    """
    מאסטרינג על הקליפ **אחרי** הרינדור, ואז מדידה חוזרת.

    למה אחרי ולא בתוך הרינדור: `loudnorm` דו-מעברי מקבל את
    המדידה של האות שעליו הוא רץ. מדידה של השידור המלא אינה
    תקפה לקליפ בן חצי דקה שנחתך ממנו — הגבר שחושב לפיה מחטיא
    את היעד. כאן מודדים את הקליפ עצמו, מתקנים אותו, ומודדים שוב.

    הווידאו מועתק כמו שהוא, ולכן העלות היא קידוד אודיו בלבד.
    """
    if not ctx.settings.mastering_enabled:
        return {}
    try:
        result = audio_mastering.master_file(
            out_path, out_path.with_name(out_path.stem + "_mastered.mp4"),
            target=ctx.settings.mastering_target,
            allow_denoise=ctx.settings.mastering_denoise,
            allow_compress=ctx.settings.mastering_compress)
    except Exception as exc:                            # noqa: BLE001
        log.warning("clip mastering failed: %s", exc, exc_info=True)
        return {"error": str(exc), "needs_review": True}

    # הקובץ המעובד מחליף את המקורי רק כשהוא באמת נוצר ואומת.
    if result.processed and result.output and result.output.exists():
        if result.needs_review:
            # לא מחליפים פלט תקין בפלט שלא עמד ביעד
            log.warning("mastering missed the target, keeping the original "
                        "audio: %s", result.issues)
            result.output.unlink(missing_ok=True)
        else:
            result.output.replace(out_path)

    plan = result.plan
    row = {
        "target": plan.target.name,
        "before_lufs": (round(plan.before.lufs, 2)
                        if plan.before.lufs is not None else None),
        "after_lufs": (round(result.after.lufs, 2)
                       if result.after and result.after.lufs is not None else None),
        "after_true_peak": (round(result.after.true_peak, 2)
                            if result.after and result.after.true_peak is not None
                            else None),
        "chain": plan.filter_chain(),
        "steps": [st.to_dict() for st in plan.steps],
        "processed": result.processed,
        "verified": result.verified,
        "needs_review": result.needs_review,
        "issues": result.issues,
        "summary": result.summary(),
    }
    if result.needs_review:
        log.warning("clip audio needs review: %s", result.issues)
    return row


# --------------------------------------------------------------------------
# במאי
# --------------------------------------------------------------------------
def direct_segments(ctx, segments: list[tuple[float, float]], *, edit_style,
                    src_w: int, src_h: int, out_w: int, out_h: int) -> list:
    """
    מריץ את הבמאי על כל מקטע ומחזיר תכניות — בלי לבצע דבר.

    מחזיר רשימה ריקה כשהבמאי כבוי או כשאין תמלול: במצב כזה חוזרים
    לעורך ההיוריסטי, שאינו תלוי בטקסט. עדיף עורך פשוט שעובד על
    „במאי" שמנחש בלי חומר.
    """
    s = ctx.settings
    if not s.director_enabled or ctx.transcript is None:
        return []
    # „גולמי" הוא בקשה מפורשת לחיתוך ישיר בלי עריכה.
    if edit_style.name == "raw":
        return []

    style = s.director_style or pacing_engine.LEGACY_STYLE_MAP.get(
        edit_style.name, pacing_engine.DEFAULT_PROFILE)
    language = getattr(ctx, "language", None)
    plans = []
    for s0, s1 in segments:
        try:
            sem_analysis = semantics.analyze(
                ctx.transcript, ctx.audio_feats, start=s0, end=s1, settings=s,
                use_llm=False, language=language)
            plans.append(video_director.direct(
                semantics=sem_analysis, audio=ctx.audio_feats,
                transcript=ctx.transcript, source_start=s0, source_end=s1,
                width=src_w, height=src_h, out_width=out_w, out_height=out_h,
                fps=float(ctx.source_info.get("fps") or 30.0),
                has_audio=bool(ctx.source_info.get("has_audio", True)),
                style=style, settings=s, language=language))
        except Exception as exc:                        # noqa: BLE001
            # תקלה בבמאי לא אמורה להפיל ייצוא. חוזרים לעורך הקודם
            # ואומרים את זה ביומן, במקום לייצא קליפ שקט בלי עריכה.
            log.warning("director failed on segment %.2f–%.2f: %s",
                        s0, s1, exc, exc_info=True)
            return []
    return plans


def caption_preset_for(ctx, plan):
    """פריסט הכתוביות: בחירת המשתמש גוברת על גזירה מהקצב."""
    chosen = ctx.settings.caption_preset or director_bridge.caption_preset_for(plan)
    return caption_engine.get_preset(chosen)


# --------------------------------------------------------------------------
# כתוביות
# --------------------------------------------------------------------------
def build_cues_for(ctx, cand: selection.Candidate,
                   segments: list[tuple[float, float]], *, vertical: bool,
                   play_width: int = 1080, frame_height: int = 0,
                   edit_plans: Optional[list] = None,
                   director_plans: Optional[list] = None,
                   v2_style: Optional[dict[str, Any]] = None) -> list[subtitles.Cue]:
    """
    כתוביות לקליפ, ממופות דרך תכנית העריכה.

    כשהעורך מסיר אוויר מת, כל מה שאחרי החיתוך זז אחורה. הכתוביות
    חייבות לזוז איתו, אחרת הן מתנתקות מהדיבור. המיפוי נעשה לכל
    מקטע בנפרד, ואז מוסט לפי האורך **הערוך** של המקטעים שלפניו.
    """
    if not ctx.settings.subtitles_enabled or ctx.transcript is None:
        return []

    # מכסת התווים לכתובית נגזרת מרוחב הפריים ומגודל הגופן, כדי שהטקסט
    # ייכנס בדיוק לשתי שורות ולא יישבר לשלוש על-ידי libass.
    probe_style = subtitles.style_for_clip(
        ctx.settings, vertical=vertical, language="",
        frame_height=frame_height or play_width)
    max_chars = subtitles._max_chars_for(probe_style, play_width)
    preset = caption_preset_for(ctx, director_plans[0]) if director_plans else None

    cues: list[subtitles.Cue] = []
    offset = 0.0
    for i, (s0, s1) in enumerate(segments):
        # היסוס וגמגום יורדים מהכתובית (עותק; התמלול עצמו לא משתנה)
        sub_tr = (subtitle_clean.cleaned(ctx.transcript, s0, s1)
                  if getattr(ctx.settings, "subtitle_clean_disfluencies", True) else ctx.transcript)
        if v2_style is not None:
            # v2: קיבוץ בסיסי לפי פיסוק ושתיקות; השבירה לשורות נמדדת
            # מאוחר יותר, מול הגופן והפריים בפועל (subtitle_render).
            seg_cues = subtitle_render.base_cues(
                sub_tr, clip_start=s0, clip_end=s1,
                style=subtitle_style.clamp_style(v2_style))
        elif preset is not None:
            seg_cues = caption_engine.build_captions(
                sub_tr, clip_start=s0, clip_end=s1, preset=preset,
                frame_chars=min(max_chars, preset.max_chars))
        else:
            seg_cues = subtitles.build_cues(sub_tr, clip_start=s0,
                                            clip_end=s1, max_chars=max_chars)
        plan = edit_plans[i] if (edit_plans and i < len(edit_plans)) else None
        if plan is not None:
            seg_cues = subtitles.remap_cues(seg_cues, plan)
            seg_out = plan.out_duration
        else:
            seg_out = max(0.0, s1 - s0)

        # הדגשות: אחרי המיפוי, כדי שהן ינחתו על הזמן הערוך
        if preset is not None and director_plans and i < len(director_plans):
            spans = caption_engine.spans_from_decisions(
                director_plans[i].captions, clip_start=s0, mapper=plan)
            caption_engine.apply_emphasis(seg_cues, spans, preset,
                                          total_duration=seg_out)

        if offset > 0.0:
            for c in seg_cues:
                c.start += offset
                c.end += offset
                c.words = [{**w, "start": float(w.get("start", 0)) + offset,
                            "end": float(w.get("end", 0)) + offset}
                           for w in (c.words or [])]
        cues.extend(seg_cues)
        offset += seg_out
    return cues


def clip_basename(cand: selection.Candidate, index: int, short: bool) -> str:
    prefix = "short" if short else ("highlights" if cand.segments else "long")
    stamp = int(cand.start)
    title = safe_filename(cand.title or "clip", max_length=48)
    return f"{prefix}_{index + 1:02d}_{stamp}s_{title}"


def create_clip_row(ctx, clip_id: str, cand: selection.Candidate,
                    kind: ClipKind, aspect: str, style,
                    plan: Optional[reframe.ReframePlan],
                    cues: list[subtitles.Cue], lang: str,
                    *, edit_plans: Optional[list] = None,
                    edit_style=None, edited_duration: float = 0.0,
                    director_plans: Optional[list] = None,
                    extra_params: Optional[dict[str, Any]] = None) -> None:
    edit_stats = [p.stats() for p in (edit_plans or [])]
    edit_summary = ""
    if edit_plans and edit_style is not None:
        edit_summary = editing.describe_plan(edit_plans[0], edit_style)

    # „למה ה-AI עשה את זה?" — כל ההחלטות נשמרות, כולל אלה שלא
    # בוצעו, כדי שהמשתמש יוכל לראות ולשנות לפני הייצוא.
    director_json: list = []
    director_notes: list[str] = []
    for dp in (director_plans or []):
        director_json.append({
            "source": {"start": round(dp.source_start, 3),
                       "end": round(dp.source_end, 3)},
            "decisions": director_bridge.explain(dp),
            "hook": dp.hook.to_dict() if dp.hook else None,
            "pacing": dp.pacing.to_dict() if dp.pacing else None,
            "unimplemented": list(dp.unimplemented),
        })
        director_notes.extend(dp.notes)

    if isinstance(style, dict):
        style_dict = dict(style)
    else:
        style_dict = style.to_dict() if hasattr(style, "to_dict") else dict(style.__dict__)
    params: dict[str, Any] = {
        "category": cand.category,
        "signals": cand.signals,
        "title_source": cand.title_source,
        "peak_time": round(cand.peak_time, 3),
        "reframe_note": plan.note if plan else "",
        "tracked_ratio": plan.tracked_ratio if plan else 0.0,
        "camera_region": plan.camera_region if plan else None,
        "layout_plan": plan.to_dict() if plan is not None else None,
        "edit_style": edit_style.name if edit_style else "clean",
        "edit_style_label": edit_style.label if edit_style else "",
        "edit_summary": edit_summary,
        "edit_stats": edit_stats,
        "director": director_json,
        "director_notes": director_notes,
        "raw_duration": round(cand.duration, 3),
        "beats": [
            {"start": round(b.src_start, 3), "end": round(b.src_end, 3),
             "zoom": round(b.zoom, 3), "zoom_to": round(b.zoom_to, 3),
             "speed": round(b.speed, 3), "reason": b.reason}
            for p in (edit_plans or []) for b in p.beats
        ],
    }
    if extra_params:
        params.update(extra_params)

    with session_scope() as s:
        clip = Clip(
            id=clip_id, job_id=ctx.job_id, kind=kind, status=ClipStatus.PENDING,
            title=cand.title, description=cand.description, reason=cand.reason,
            score=cand.score, source_start=cand.start, source_end=cand.end,
            duration=edited_duration or cand.duration,
            segments_json=[[a, b] for a, b in (cand.segments or [])],
            aspect=aspect,
            layout=(plan.layout if plan else "center"),
            subtitles_enabled=bool(ctx.settings.subtitles_enabled and cues),
            subtitle_style=style_dict,
            render_params=params,
        )
        s.add(clip)
        s.flush()
        for idx, cue in enumerate(cues):
            s.add(SubtitleCue(
                clip_id=clip_id, idx=idx, start=cue.start, end=cue.end,
                text=cue.text, original_text=cue.text, language=lang,
                words=cue.words))
    BUS.emit("clip.created", ctx.job_id, clip_id=clip_id, title=cand.title,
             kind=kind.value)


def update_clip(clip_id: str, **fields: Any) -> None:
    with session_scope() as s:
        clip = s.get(Clip, clip_id)
        if clip is None:
            return
        job_id = clip.job_id
        for k, v in fields.items():
            setattr(clip, k, v)
    BUS.emit("clip.updated", job_id, clip_id=clip_id,
             status=fields.get("status").value if isinstance(
                 fields.get("status"), ClipStatus) else None)
