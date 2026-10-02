"""
יצירת סרטון Long-Form מפרויקט: תכנון (services/longform.py) ורינדור.

פלט אחד ב-16:9. כל חלק בתכנית נרנדר בנפרד עם fade בכניסה וביציאה
(`render._render_multi`), ובתוך החלק השתיקות בין משפטים מקוצרות לפי
ה-beats של התכנית – חיתוך בלי מעבר. כתוביות נכתבות לכל חלק בזמנים של
החלק (באג 2), ואחר כך מאסטרינג, מוזיקה ובדיקת איכות – בדיוק כמו בשורטים.
הפרקים וההסבר נשמרים ב-render_params של הקליפ.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from . import i18n
from .clip_factory import (
    create_clip_row, finish_clip, merge_render_params, project_subtitle_style,
    update_clip, write_subtitles, build_cues_for,
)
from .errors import JobCancelledError, NoMomentsFoundError, PolixorError
from .models import ClipKind, ClipStatus, JobStage, new_id
from .services import editing, render, selection, subtitle_style, subtitles
from .services.editing import Beat, EditPlan
from .services.longform import LongformPlan, LongformSettings, plan_longform
from .util.fs import safe_filename, unique_path

log = logging.getLogger("polixor.longform")


def edit_plans_for(plan: LongformPlan) -> list[EditPlan]:
    """תכנית עריכה לכל חלק: הביטים הם טווחי הדיבור, יחסית לתחילת החלק."""
    out: list[EditPlan] = []
    for i, (a, b) in enumerate(plan.segments):
        spans = plan.beats[i] if i < len(plan.beats) and plan.beats[i] else [(a, b)]
        beats = [Beat(src_start=round(x - a, 4), src_end=round(y - a, 4),
                      reason="longform") for x, y in spans if y > x]
        beats = editing.cap_beats(beats)
        kept = sum(bt.src_duration for bt in beats)
        out.append(EditPlan(beats=beats, style="longform", window_start=a, window_end=b,
                            raw_duration=b - a, removed_seconds=max(0.0, (b - a) - kept)))
    return out


def generate_longform(ctx) -> str:
    """מתכנן ומרנדר את הסרטון הארוך (המנוע הדטרמיניסטי – מצב מנוון). מחזיר את מזהה הקליפ."""
    s = ctx.settings
    rep = ctx.reporter
    rep.start_stage(JobStage.RENDER_LONG, i18n.tr("pipeline.longform.planning"))
    duration = float(ctx.source_info.get("duration") or 0.0)
    lf_settings = LongformSettings(target_seconds=float(s.longform_target_seconds),
                                   language=ctx.language)
    plan = plan_longform(ctx.transcript, duration=duration, silences=ctx.silences,
                         timeline=ctx.timeline, settings=lf_settings,
                         language=ctx.language)
    if not plan.segments:
        raise NoMomentsFoundError(message_key="longform.no_content")
    rep.check_cancel()
    source_title = str(ctx.source_info.get("title") or "").strip()
    title = (i18n.tr("longform.title", title=source_title) if source_title
             else i18n.tr("longform.title_default"))
    clip_id = render_plan(ctx, plan, title=title, description="\n".join(plan.explain()[:1]),
                          stem=f"longform_{safe_filename(source_title or 'video', max_length=48)}")
    ctx.mark(JobStage.RENDER_LONG, duration)
    return clip_id


def render_topic_videos(ctx, longforms: list[dict[str, Any]]) -> list[str]:
    """One long-form video per topic of the semantic topic map (services/semantic/longform_plan)."""
    rep = ctx.reporter
    rep.start_stage(JobStage.RENDER_LONG, i18n.tr("pipeline.longform.planning"))
    ids = []
    for k, lf in enumerate(longforms):
        rep.check_cancel()
        plan = LongformPlan.from_dict(lf["plan"])
        if not plan.segments:
            continue
        ids.append(render_plan(ctx, plan, title=lf.get("title") or i18n.tr("longform.title_default"),
                               description=lf.get("description") or "",
                               stem=f"topic_{k + 1:02d}_{safe_filename(lf.get('title') or 'topic', max_length=40)}",
                               extra={"topic": lf.get("topic"), "topic_shorts": lf.get("shorts") or [],
                                      "engine": "semantic", "keep_source": lf.get("source")},
                               progress=(k, len(longforms))))
    ctx.mark(JobStage.RENDER_LONG, sum(LongformPlan.from_dict(x["plan"]).output_seconds for x in longforms))
    return ids


def render_plan(ctx, plan: LongformPlan, *, title: str, description: str, stem: str,
                extra: dict[str, Any] | None = None, progress: tuple[int, int] = (0, 1)) -> str:
    """מרנדר תכנית Long-Form אחת (חלקים, ביטים, כתוביות, פרקים) לקליפ ארוך."""
    s = ctx.settings
    rep = ctx.reporter
    edit_plans = edit_plans_for(plan)
    segments = list(plan.segments)
    requested = s.long_resolution
    src_w = int(ctx.source_info.get("width") or 1920)
    src_h = int(ctx.source_info.get("height") or 1080)
    out_w, out_h = render.target_resolution(requested, src_w, src_h, vertical=False)
    cand = selection.Candidate(
        start=segments[0][0], end=segments[-1][1],
        peak_time=max(plan.sections, key=lambda x: x.score).start if plan.sections
        else segments[0][0],
        score=round(sum(x.score for x in plan.sections) / max(1, len(plan.sections)), 4),
        kind="long", title=title, description=description,
        reason=i18n.tr("longform.reason_text"), category="longform",
        segments=segments if len(segments) > 1 else [])

    if s.subtitles_enabled and (extra or {}).get("engine") != "semantic":
        # אותה הגהה כמו בשורטים: רק מה שנכנס לסרטון, רק לפי ראיה מהאודיו
        from .pipeline import _proofread

        _proofread(ctx, segments)
    v2_style = project_subtitle_style(ctx)
    cues = build_cues_for(ctx, cand, segments, vertical=False, play_width=out_w,
                          frame_height=out_h, edit_plans=edit_plans, v2_style=v2_style)
    lang = subtitles.detect_cue_language(cues) or (
        ctx.transcript.language if ctx.transcript else "")
    if v2_style is not None:
        style: Any = subtitle_style.clamp_style(v2_style, language=lang)
    else:
        style = subtitles.style_for_clip(s, vertical=False, language=lang,
                                         frame_height=out_h)

    clip_id = new_id()
    edit_style = editing.style_from_settings(s, vertical=False)
    create_clip_row(ctx, clip_id, cand, ClipKind.LONG, "16:9", style, None, cues, lang,
                    edit_plans=edit_plans, edit_style=edit_style,
                    edited_duration=plan.output_seconds,
                    extra_params={"resolution": requested,
                                  "longform": plan.to_dict(),
                                  "chapters": [c.to_dict() for c in plan.chapters],
                                  "youtube_chapters": plan.youtube_chapters_text(),
                                  "longform_explain": plan.explain(),
                                  **(extra or {})})

    sub_path, sub_parts = None, []
    if s.subtitles_enabled and cues:
        sub_path, sub_parts = write_subtitles(
            cues, style=style, v2=v2_style is not None, work_dir=ctx.work_dir,
            clip_id=clip_id, edit_plans=edit_plans, width=out_w, height=out_h,
            language=lang, title_text="", multipart=len(segments) > 1)
        subtitles.write_srt(cues, ctx.export_dir / f"{stem}.srt")
    if plan.chapters:
        (ctx.export_dir / f"{stem}_chapters.txt").write_text(
            plan.youtube_chapters_text() + "\n", encoding="utf-8")

    out_path = unique_path(ctx.export_dir / f"{stem}.mp4")
    req = render.build_request(
        source=ctx.source_path, output=out_path, segments=segments, vertical=False,
        settings=s, reframe=None, subtitle_path=sub_path, source_info=ctx.source_info,
        transitions=True, work_dir=ctx.work_dir, edit_style="longform",
        edit_plans=edit_plans,
        audio_chain=("aresample=async=1:first_pts=0" if s.mastering_enabled else ""),
        resolution=requested, subtitle_parts=sub_parts)

    update_clip(clip_id, status=ClipStatus.RENDERING)
    k, n = progress
    rep.progress(min(0.99, (k + 0.02) / max(1, n)), i18n.tr("pipeline.longform.rendering", parts=len(segments)))
    try:
        result = render.render_clip(
            req, on_progress=lambda f: rep.progress(
                min(0.99, (k + f) / max(1, n)), i18n.tr("pipeline.longform.rendering", parts=len(segments))),
            cancel_event=ctx.cancel_event)
    except JobCancelledError:
        update_clip(clip_id, status=ClipStatus.FAILED,
                    error=i18n.tr("pipeline.render.cancelled"))
        raise
    except PolixorError as exc:
        update_clip(clip_id, status=ClipStatus.FAILED, error=exc.message)
        raise

    finish_clip(ctx, clip_id, result, cand=cand, kind=ClipKind.LONG,
                edit_plans=edit_plans, director_plans=[], cues=cues, style=style,
                vertical=False)
    merge_render_params(clip_id, {"longform_output_seconds": round(result.duration, 2)})
    return clip_id
