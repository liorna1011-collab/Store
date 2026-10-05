"""המרה ממודלי DB לסכמות API, כולל הערכת זמן מבוססת מדידות בלבד."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import i18n
from ..errors import PolixorError
from ..models import (
    Clip,
    Job,
    JobStage,
    STAGE_ORDER,
    Source,
    StageTiming,
    SubtitleCue,
)
from ..schemas import (
    ClipOut, CueOut, JobOut, ProjectErrorOut, ProjectOut, ProjectSourceOut,
    StageTimingOut,
)
from ..worker import STAGE_WEIGHTS, derived_phase


def stage_label(stage: str, lang: Optional[str] = None) -> str:
    return i18n.tr(f"pipeline.stage.{stage}", lang, default=stage)


def job_to_out(session: Session, job: Job) -> JobOut:
    source = session.get(Source, job.source_id) if job.source_id else None

    counts_raw = (
        session.query(Clip.kind, Clip.status, func.count(Clip.id))
        .filter(Clip.job_id == job.id)
        .group_by(Clip.kind, Clip.status)
        .all()
    )
    counts: dict[str, int] = {"total": 0, "ready": 0, "failed": 0,
                              "long": 0, "short": 0, "highlights": 0}
    for kind, status, n in counts_raw:
        counts["total"] += n
        counts[kind.value] = counts.get(kind.value, 0) + n
        if status.value == "ready":
            counts["ready"] += n
        elif status.value == "failed":
            counts["failed"] += n

    timings = (session.query(StageTiming)
               .filter(StageTiming.job_id == job.id)
               .order_by(StageTiming.id).all())

    eta, basis = _estimate_eta(session, job, timings, source)

    return JobOut(
        id=job.id,
        title=job.title,
        input_url=job.input_url,
        status=job.status.value,
        stage=job.stage.value,
        stage_label=stage_label(job.stage.value),
        stage_progress=round(job.stage_progress, 4),
        overall_progress=round(job.overall_progress, 4),
        message=job.message,
        error=job.error,
        error_code=job.error_code,
        is_live_mode=job.is_live_mode,
        live_cycles=job.live_cycles,
        completed_stages=list(job.completed_stages or []),
        notes=list((job.artifacts or {}).get("notes") or []),
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        source=_source_dict(source, job),
        clip_counts=counts,
        timings=[StageTimingOut(stage=t.stage, seconds=round(t.seconds, 2),
                                media_seconds=round(t.media_seconds, 2))
                 for t in timings],
        eta_seconds=eta,
        eta_basis=basis,
    )


def _source_dict(source: Optional[Source], job: Job) -> Optional[dict[str, Any]]:
    if source is None:
        info = (job.artifacts or {}).get("source_info") or {}
        if not info:
            return None
        return {"duration": info.get("duration", 0), "width": info.get("width", 0),
                "height": info.get("height", 0), "has_audio": info.get("has_audio", True),
                "title": job.title, "kind": "upload"}
    return {
        "id": source.id, "kind": source.kind.value, "title": source.title,
        "uploader": source.uploader, "duration": source.duration,
        "file_size": source.file_size, "width": source.width, "height": source.height,
        "fps": source.fps, "has_audio": source.has_audio, "is_live": source.is_live,
        "url": source.url,
    }


def historical_rates(session: Session) -> dict[str, float]:
    """Processing seconds per media second, per stage, over every recorded run (one query)."""
    rows = (session.query(StageTiming.stage,
                          func.sum(StageTiming.seconds),
                          func.sum(StageTiming.media_seconds))
            .filter(StageTiming.media_seconds > 0)
            .group_by(StageTiming.stage).all())
    return {stage: (secs / med) for stage, secs, med in rows if med and med > 0}


def _estimate_eta(session: Session, job: Job, timings: list[StageTiming],
                  source: Optional[Source], rates: Optional[dict[str, float]] = None
                  ) -> tuple[Optional[float], str]:
    """
    הערכת זמן נותר – מוחזרת **רק** כשיש מדידות אמיתיות קודמות
    לאותם שלבים. אין נוסחאות ניחוש.
    """
    if job.status.value not in ("running", "queued"):
        return None, ""

    media = 0.0
    if source is not None:
        media = source.duration or 0.0
    if not media:
        media = float((job.artifacts or {}).get("source_info", {}).get("duration") or 0.0)
    if media <= 0:
        return None, ""

    # קצב היסטורי: שניות עיבוד לכל שנייה של מדיה, לכל שלב
    if rates is None:
        rates = historical_rates(session)
    if not rates:
        return None, ""

    done_stages = set(job.completed_stages or [])
    remaining = 0.0
    covered = 0.0
    total_weight = 0.0

    for stage in STAGE_ORDER:
        if stage in (JobStage.PENDING, JobStage.DONE):
            continue
        weight = STAGE_WEIGHTS.get(stage, 0.0)
        if weight <= 0:
            continue
        total_weight += weight
        if stage.value in done_stages:
            continue
        rate = rates.get(stage.value)
        if rate is None:
            continue
        covered += weight
        stage_estimate = rate * media
        if stage.value == job.stage.value:
            stage_estimate *= max(0.0, 1.0 - job.stage_progress)
        remaining += stage_estimate

    if covered <= 0 or total_weight <= 0:
        return None, ""
    coverage = covered / total_weight
    if coverage < 0.4:
        # אין מספיק מדידות כדי להעריך בכנות
        return None, ""

    return round(remaining, 1), i18n.tr("api.eta_basis", percent=f"{coverage:.0%}")


def clip_to_out(session: Session, clip: Clip) -> ClipOut:
    cue_count = (session.query(func.count(SubtitleCue.id))
                 .filter(SubtitleCue.clip_id == clip.id).scalar() or 0)
    return ClipOut(
        id=clip.id, job_id=clip.job_id, kind=clip.kind.value,
        status=clip.status.value, title=clip.title, description=clip.description,
        reason=clip.reason, score=round(clip.score, 4),
        source_start=round(clip.source_start, 3), source_end=round(clip.source_end, 3),
        duration=round(clip.duration, 3),
        segments=[[float(a), float(b)] for a, b in (clip.segments_json or [])],
        width=clip.width, height=clip.height, aspect=clip.aspect, layout=clip.layout,
        file_size=clip.file_size,
        has_file=bool(clip.file_path and Path(clip.file_path).exists()),
        has_thumbnail=bool(clip.thumbnail_path and Path(clip.thumbnail_path).exists()),
        subtitles_enabled=clip.subtitles_enabled,
        subtitle_style=clip.subtitle_style or {},
        render_params=clip.render_params or {},
        error=clip.error, cue_count=int(cue_count), created_at=clip.created_at,
    )


def cue_to_out(cue: SubtitleCue) -> CueOut:
    return CueOut(
        id=cue.id, idx=cue.idx, start=round(cue.start, 3), end=round(cue.end, 3),
        text=cue.text, original_text=cue.original_text, language=cue.language,
        words=cue.words or [], edited=cue.edited,
    )


# --------------------------------------------------------------------------
# פרויקטים
# --------------------------------------------------------------------------
_PLATFORM_BY_KIND = {
    "youtube_vod": "youtube", "youtube_live": "youtube",
    "twitch_vod": "twitch", "twitch_live": "twitch",
    "kick_vod": "kick", "kick_live": "kick",
    "gdrive": "gdrive", "upload": "upload", "direct_url": "direct",
}


def _clip_counts(session: Session, job_id: str,
                 rows: Optional[list[tuple[Any, int]]] = None) -> dict[str, int]:
    if rows is None:
        rows = (session.query(Clip.status, func.count(Clip.id))
                .filter(Clip.job_id == job_id).group_by(Clip.status).all())
    out = {"total": 0, "ready": 0, "failed": 0, "needs_review": 0, "rendering": 0}
    for status, n in rows:
        out["total"] += n
        key = status.value
        if key in out:
            out[key] += n
        elif key == "pending":
            out["rendering"] += n
    return out


def project_error(job: Job, lang: Optional[str] = None) -> Optional[ProjectErrorOut]:
    if job.status.value != "failed" and not job.error:
        return None
    loc = PolixorError.localize_record(job.error_data or None, lang)
    if loc:
        return ProjectErrorOut(**loc)
    if job.error:
        return ProjectErrorOut(code=job.error_code or "unknown_error",
                               message=job.error, hint="")
    return None


def project_source(job: Job, source: Optional[Source]) -> ProjectSourceOut:
    arts = job.artifacts or {}
    preview = arts.get("preview") or {}
    kind = source.kind.value if source is not None else str(arts.get("source_kind")
                                                          or ("upload" if not job.input_url
                                                              else "unknown"))
    platform = arts.get("platform") or _PLATFORM_BY_KIND.get(kind, preview.get("platform") or "")
    local = bool(arts.get("source_path") and Path(arts["source_path"]).exists())
    thumb = f"/api/projects/{job.id}/thumbnail" if local else None
    if thumb is None:
        remote = str(preview.get("thumbnail") or "")
        thumb = remote if remote.startswith("https://") else None
    info = arts.get("source_info") or {}
    duration = (source.duration if source is not None and source.duration else None) \
        or info.get("duration") or preview.get("duration")
    width = (source.width if source is not None and source.width else None) or info.get("width")
    height = (source.height if source is not None and source.height else None) or info.get("height")
    return ProjectSourceOut(
        kind=kind, platform=platform or "",
        url=(source.url if source is not None and source.url else job.input_url) or None,
        title=(source.title if source is not None and source.title else preview.get("title")) or None,
        uploader=(source.uploader if source is not None and source.uploader
                  else preview.get("uploader")) or None,
        duration=float(duration) if duration else None,
        thumbnail_url=thumb,
        width=int(width) if width else None, height=int(height) if height else None,
        is_live=bool(job.is_live_mode or (source.is_live if source is not None else False)
                     or preview.get("is_live")),
        section=arts.get("section") or None,
    )


_UNSET: Any = object()


def project_to_out(session: Session, job: Job, *, include_analysis: bool = True,
                   lang: Optional[str] = None, source: Any = _UNSET,
                   timings: Optional[list[StageTiming]] = None,
                   counts: Optional[list[tuple[Any, int]]] = None,
                   rates: Optional[dict[str, float]] = None) -> ProjectOut:
    from ..project_config import clamp_config

    if source is _UNSET:
        source = session.get(Source, job.source_id) if job.source_id else None
    if timings is None:
        timings = (session.query(StageTiming).filter(StageTiming.job_id == job.id)
                   .order_by(StageTiming.id).all())
    eta, _basis = _estimate_eta(session, job, timings, source, rates)
    config = clamp_config(job.project_config or {}, ui_language=job.ui_language or "he")
    mode = job.mode if job.phase else _legacy_mode(job)
    config["mode"] = mode
    from ..worker import MANAGER

    return ProjectOut(
        id=job.id, title=job.title or "", worker_active=MANAGER.is_running(job.id),
        created_at=job.created_at, updated_at=job.updated_at or job.created_at,
        phase=job.phase or derived_phase(job),
        status=job.status.value, stage=job.stage.value,
        stage_label=stage_label(job.stage.value, lang),
        stage_progress=round(job.stage_progress or 0.0, 4),
        overall_progress=round(job.overall_progress or 0.0, 4),
        message=job.message or None, eta_seconds=eta,
        error=project_error(job, lang),
        source=project_source(job, source),
        mode=mode, ui_language=job.ui_language or "he",
        content_language=job.content_language or "auto",
        config=config,
        analysis=(job.analysis if include_analysis else None),
        clip_counts=_clip_counts(session, job.id, counts),
        is_live=bool(job.is_live_mode), legacy=not bool(job.phase),
        notes=list((job.artifacts or {}).get("notes") or []),
        performance=performance_summary(job, timings) if include_analysis else None,
    )


def project_list_out(session: Session, jobs: list[Job], lang: Optional[str] = None) -> list[ProjectOut]:
    """
    The project list without a query per project: sources, clip counts and (for running
    projects only) stage timings are fetched once for the whole page – a constant number of
    queries for 10, 100 or 1000 projects.
    """
    ids = [j.id for j in jobs]
    if not ids:
        return []
    src_ids = {j.source_id for j in jobs if j.source_id}
    sources = {s_.id: s_ for s_ in session.query(Source).filter(Source.id.in_(src_ids)).all()} if src_ids else {}
    counts: dict[str, list[tuple[Any, int]]] = {i: [] for i in ids}
    for jid, status, n in (session.query(Clip.job_id, Clip.status, func.count(Clip.id))
                           .filter(Clip.job_id.in_(ids)).group_by(Clip.job_id, Clip.status).all()):
        counts[jid].append((status, n))
    active = [j.id for j in jobs if j.status.value in ("running", "queued")]
    timings: dict[str, list[StageTiming]] = {i: [] for i in ids}
    rates: Optional[dict[str, float]] = None
    if active:
        for t in (session.query(StageTiming).filter(StageTiming.job_id.in_(active))
                  .order_by(StageTiming.id).all()):
            timings[t.job_id].append(t)
        rates = historical_rates(session)
    return [project_to_out(session, j, include_analysis=False, lang=lang,
                           source=sources.get(j.source_id) if j.source_id else None,
                           timings=timings[j.id], counts=counts[j.id], rates=rates or {})
            for j in jobs]


def performance_summary(job: Job, timings: list[StageTiming]) -> Optional[dict[str, Any]]:
    """
    זמני עיבוד אמיתיים של הפרויקט: לכל שלב ולכל תת-שלב – שניות וקצב
    ביחס לזמן אמת (RTF = זמן עיבוד ÷ אורך החומר; 0.25 = פי 4 מזמן אמת).
    """
    from ..util.timing import rtf

    if not timings:
        return None
    stages = [{"stage": t.stage, "seconds": round(t.seconds, 2),
               "media_seconds": round(t.media_seconds, 2),
               "rtf": rtf(t.seconds, t.media_seconds)} for t in timings]
    runs = list((job.artifacts or {}).get("substage_timings") or [])
    subs: list[dict[str, Any]] = []
    for run in runs[-2:]:
        for i in run.get("items") or []:
            subs.append({**i, "scope": run.get("scope"),
                         "rtf": rtf(float(i.get("seconds", 0)), float(i.get("media_seconds", 0)))})
    source = float(((job.artifacts or {}).get("source_info") or {}).get("duration") or 0.0)
    total = sum(t.seconds for t in timings)
    return {"stages": stages, "substages": subs, "source_seconds": round(source, 2),
            "total_seconds": round(total, 2), "total_rtf": rtf(total, source)}


def _legacy_mode(job: Job) -> Optional[str]:
    snap = job.settings_snapshot or {}
    if snap.get("short_enabled") and not snap.get("long_enabled"):
        return "short"
    return None
