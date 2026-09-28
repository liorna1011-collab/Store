"""המרה ממודלי DB לסכמות API, כולל הערכת זמן מבוססת מדידות בלבד."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import (
    Clip,
    Job,
    JobStage,
    STAGE_LABELS_HE,
    STAGE_ORDER,
    Source,
    StageTiming,
    SubtitleCue,
)
from ..schemas import ClipOut, CueOut, JobOut, StageTimingOut
from ..worker import STAGE_WEIGHTS


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
        stage_label=STAGE_LABELS_HE.get(job.stage.value, job.stage.value),
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


def _estimate_eta(session: Session, job: Job, timings: list[StageTiming],
                  source: Optional[Source]) -> tuple[Optional[float], str]:
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
    rows = (session.query(StageTiming.stage,
                          func.sum(StageTiming.seconds),
                          func.sum(StageTiming.media_seconds))
            .filter(StageTiming.media_seconds > 0)
            .group_by(StageTiming.stage).all())
    rates = {stage: (secs / med) for stage, secs, med in rows if med and med > 0}
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

    return round(remaining, 1), f"מבוסס על מדידות קודמות ({coverage:.0%} מהשלבים)"


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
