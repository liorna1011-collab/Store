"""נתיבי API למשימות: יצירה, מעקב, ביטול, חידוש, מחיקה."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import desc
from sqlalchemy.orm import Session

from .. import i18n
from ..config import PATHS, SETTINGS, AppSettings
from ..db import db_dependency
from ..errors import JobNotFoundError, LiveRequiresCaptureError, PolixorError
from ..models import Clip, ClipReview, Job, JobStage, JobStatus, Moment, Source, TranscriptSegment, new_id
from ..schemas import (
    CreateJobRequest,
    JobOut,
    ProbeResponse,
    ResolveRequest,
    ResolveResponse,
)
from ..services import ingest
from ..util.fs import human_size, rmtree_quiet, safe_filename, unique_path
from ..worker import MANAGER
from .http import api_error, http_error
from .serializers import job_to_out

log = logging.getLogger("polixor.api.jobs")
router = APIRouter(prefix="/api", tags=["jobs"])


# --------------------------------------------------------------------------
# מקורות
# --------------------------------------------------------------------------
@router.post("/sources/resolve", response_model=ResolveResponse)
def resolve_source(payload: ResolveRequest) -> ResolveResponse:
    """זיהוי פלטפורמה וסוג תוכן מקישור, בלי גישה לרשת."""
    try:
        r = ingest.resolve_url(payload.url)
        ingest.check_url_allowed(r, SETTINGS.get())
    except PolixorError as exc:
        raise _http(exc) from exc
    return ResolveResponse(
        kind=r.kind.value, platform=r.platform_label, is_live=r.is_live,
        normalized_url=r.normalized_url or r.url, notes=r.notes,
        platform_key=r.platform, content=r.content, live_certain=r.live_certain,
        start_hint=r.start_hint, video_id=r.video_id,
    )


@router.post("/sources/probe", response_model=ProbeResponse)
def probe_source(payload: ResolveRequest) -> ProbeResponse:
    """שליפת מטא-דאטה מהפלטפורמה (כותרת, אורך, האם חי). לא מוריד מדיה."""
    settings = SETTINGS.get()
    try:
        data = ingest.probe_remote(payload.url, settings)
    except PolixorError as exc:
        raise _http(exc) from exc
    limit = float(settings.max_source_hours)
    duration = float(data.get("duration") or 0.0)
    return ProbeResponse(**data, max_source_hours=limit,
                         needs_section=bool(duration and not data.get("is_live")
                                            and duration > limit * 3600))


@router.post("/upload")
def upload_file(file: UploadFile = File(...)) -> dict[str, Any]:
    """העלאת קובץ מקומי (MP4/MKV/MOV). מוחזר טוקן ליצירת משימה."""
    name = safe_filename(file.filename or "video.mp4", max_length=120)
    suffix = Path(name).suffix.lower()
    if suffix not in ingest.ALLOWED_UPLOAD_EXT:
        raise api_error("unsupported_format", ext=suffix or "(-)",
                        formats=", ".join(sorted(ingest.ALLOWED_UPLOAD_EXT)))

    PATHS.sources.mkdir(parents=True, exist_ok=True)
    dest = unique_path(PATHS.sources / name)

    # a plain (threadpool) handler: the copy and the ffprobe below never run on the event loop
    written = 0
    try:
        with dest.open("wb") as out:
            while chunk := file.file.read(4 * 1024 * 1024):
                out.write(chunk)
                written += len(chunk)
    except OSError as exc:
        dest.unlink(missing_ok=True)
        raise api_error("upload_write_failed") from exc
    finally:
        file.file.close()

    if written == 0:
        dest.unlink(missing_ok=True)
        raise api_error("empty_file")

    try:
        result = ingest.register_local_file(dest, copy=False)
    except PolixorError as exc:
        dest.unlink(missing_ok=True)
        raise _http(exc) from exc

    return {
        "upload_token": dest.name,
        "title": result.title,
        "duration": result.duration,
        "file_size": result.filesize,
        "file_size_human": human_size(result.filesize),
        "width": result.extra.get("width", 0),
        "height": result.extra.get("height", 0),
    }


# --------------------------------------------------------------------------
# משימות
# --------------------------------------------------------------------------
@router.post("/jobs", response_model=JobOut)
def create_job(payload: CreateJobRequest,
               db: Session = Depends(db_dependency)) -> JobOut:
    settings = SETTINGS.get()
    if payload.settings_override:
        merged = settings.to_dict()
        merged.update(payload.settings_override)
        settings = AppSettings.from_dict(merged)

    job_id = new_id()
    artifacts: dict[str, Any] = {}
    title = payload.title.strip()
    input_url = payload.url.strip()

    if payload.upload_token:
        path = PATHS.sources / safe_filename(payload.upload_token, max_length=160)
        if not path.exists():
            raise api_error("upload_missing")
        try:
            local = ingest.register_local_file(path, copy=False)
        except PolixorError as exc:
            raise _http(exc) from exc

        artifacts["source_path"] = str(path)
        title = title or local.title
        src = Source(
            id=new_id(), kind=local.kind, url="", title=local.title,
            file_path=str(path), file_size=local.filesize, duration=local.duration,
            width=int(local.extra.get("width") or 0),
            height=int(local.extra.get("height") or 0),
            fps=float(local.extra.get("fps") or 0.0),
        )
        db.add(src)
        db.flush()
        source_id = src.id
    elif input_url:
        try:
            resolved = ingest.resolve_url(input_url)
            ingest.check_url_allowed(resolved, settings)
        except PolixorError as exc:
            raise _http(exc) from exc
        source_id = None
        title = title or resolved.platform_label
        if resolved.live_certain and not payload.live_mode:
            # באג שתוקן: קישור לשידור חי במצב רגיל הורד בלי הגבלה.
            # עכשיו חייבים לבחור מצב שידור חי (עם משך הקלטה).
            raise _http(LiveRequiresCaptureError())
    else:
        raise api_error("missing_input")

    job = Job(
        id=job_id, source_id=source_id, title=title, input_url=input_url,
        status=JobStatus.QUEUED, stage=JobStage.PENDING,
        is_live_mode=bool(payload.live_mode),
        settings_snapshot=settings.to_dict(), artifacts=artifacts,
        completed_stages=[],
        message=i18n.tr("pipeline.status.queued"),
        ui_language=i18n.get_lang(),
    )
    db.add(job)
    db.commit()

    MANAGER.submit(job_id)
    db.refresh(job)
    return job_to_out(db, job)


@router.get("/jobs", response_model=list[JobOut])
def list_jobs(limit: int = Query(50, ge=1, le=500),
              status: Optional[str] = None,
              db: Session = Depends(db_dependency)) -> list[JobOut]:
    q = db.query(Job).order_by(desc(Job.created_at))
    if status:
        try:
            q = q.filter(Job.status == JobStatus(status))
        except ValueError:
            pass
    return [job_to_out(db, j) for j in q.limit(limit).all()]


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str, db: Session = Depends(db_dependency)) -> JobOut:
    job = db.get(Job, job_id)
    if job is None:
        raise _http(JobNotFoundError())
    return job_to_out(db, job)


@router.post("/jobs/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: str, db: Session = Depends(db_dependency)) -> JobOut:
    job = db.get(Job, job_id)
    if job is None:
        raise _http(JobNotFoundError())
    MANAGER.cancel(job_id)
    db.refresh(job)
    return job_to_out(db, job)


@router.post("/jobs/{job_id}/retry", response_model=JobOut)
def retry_job(job_id: str, from_start: bool = Query(False),
              db: Session = Depends(db_dependency)) -> JobOut:
    """
    מחדש משימה. כברירת מחדל ממשיך מהשלב האחרון שהושלם
    (הורדה ותמלול לא מבוצעים שוב). `from_start=true` מאפס הכול.
    """
    job = db.get(Job, job_id)
    if job is None:
        raise _http(JobNotFoundError())
    if MANAGER.is_running(job_id):
        raise api_error("already_running")

    if from_start:
        job.completed_stages = []
        job.artifacts = {k: v for k, v in (job.artifacts or {}).items()
                         if k == "source_path"}
        db.query(ClipReview).filter(ClipReview.job_id == job_id).delete()
        db.query(Clip).filter(Clip.job_id == job_id).delete()
        db.query(Moment).filter(Moment.job_id == job_id).delete()
        db.query(TranscriptSegment).filter(TranscriptSegment.job_id == job_id).delete()

    job.status = JobStatus.QUEUED
    job.error = ""
    job.error_code = ""
    job.error_data = {}
    job.message = i18n.tr("pipeline.status.queued", job.ui_language)
    job.finished_at = None
    db.commit()

    MANAGER.submit(job_id)
    db.refresh(job)
    return job_to_out(db, job)


@router.delete("/jobs/{job_id}")
def delete_job(job_id: str, delete_files: bool = Query(True),
               db: Session = Depends(db_dependency)) -> dict[str, Any]:
    job = db.get(Job, job_id)
    if job is None:
        raise _http(JobNotFoundError())
    MANAGER.cancel(job_id)

    removed = 0
    if delete_files:
        for clip in db.query(Clip).filter(Clip.job_id == job_id).all():
            for p in (clip.file_path, clip.thumbnail_path):
                if p and Path(p).exists():
                    try:
                        Path(p).unlink()
                        removed += 1
                    except OSError:
                        pass
        rmtree_quiet(PATHS.work / job_id)
        rmtree_quiet(PATHS.exports / job_id)

    db.query(TranscriptSegment).filter(TranscriptSegment.job_id == job_id).delete()
    db.query(Moment).filter(Moment.job_id == job_id).delete()
    db.query(ClipReview).filter(ClipReview.job_id == job_id).delete()
    db.query(Clip).filter(Clip.job_id == job_id).delete()
    db.delete(job)
    db.commit()
    return {"deleted": True, "files_removed": removed}


# --------------------------------------------------------------------------
# נתוני ניתוח
# --------------------------------------------------------------------------
@router.get("/jobs/{job_id}/timeline")
def job_timeline(job_id: str, db: Session = Depends(db_dependency)) -> dict[str, Any]:
    """ציר הציונים המוקטן – להצגת מפת העניין בממשק."""
    import json

    job = db.get(Job, job_id)
    if job is None:
        raise _http(JobNotFoundError())
    path = (job.artifacts or {}).get("timeline_path")
    if not path or not Path(path).exists():
        return {"available": False, "reason": i18n.tr("api.timeline_pending")}
    try:
        return {"available": True, **json.loads(Path(path).read_text("utf-8"))}
    except (OSError, json.JSONDecodeError):
        return {"available": False, "reason": i18n.tr("api.timeline_unreadable")}


@router.get("/jobs/{job_id}/transcript")
def job_transcript(job_id: str, limit: int = Query(5000, ge=1, le=50000),
                   db: Session = Depends(db_dependency)) -> dict[str, Any]:
    job = db.get(Job, job_id)
    if job is None:
        raise _http(JobNotFoundError())
    rows = (db.query(TranscriptSegment)
            .filter(TranscriptSegment.job_id == job_id)
            .order_by(TranscriptSegment.idx).limit(limit).all())
    return {
        "count": len(rows),
        "language": rows[0].language if rows else "",
        "segments": [
            {"idx": r.idx, "start": r.start, "end": r.end, "text": r.text,
             "words": r.words or []}
            for r in rows
        ],
    }


@router.get("/jobs/{job_id}/frame")
def job_frame(job_id: str, t: float = Query(0.0, ge=0.0),
              width: int = Query(960, ge=160, le=1920),
              db: Session = Depends(db_dependency)):
    """
    פריים בודד מקובץ המקור – משמש לבחירת אזור מצלמת הסטרימר
    במסך העריכה. נשמר במטמון לפי (משימה, זמן, רוחב).
    """
    from fastapi.responses import FileResponse

    from ..util.ffmpeg import extract_thumbnail

    job = db.get(Job, job_id)
    if job is None:
        raise _http(JobNotFoundError())
    src = (job.artifacts or {}).get("source_path")
    if not src or not Path(src).exists():
        raise api_error("source_missing")

    cache_dir = PATHS.job_work_dir(job_id) / "frames"
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / f"f_{int(t * 1000)}_{width}.jpg"
    if not out.exists():
        if extract_thumbnail(Path(src), out, at_seconds=t, width=width) is None:
            raise api_error("frame_failed")

    return FileResponse(out, media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=3600"})


@router.get("/jobs/{job_id}/moments")
def job_moments(job_id: str, db: Session = Depends(db_dependency)) -> dict[str, Any]:
    rows = (db.query(Moment).filter(Moment.job_id == job_id)
            .order_by(Moment.start).all())
    return {
        "count": len(rows),
        "moments": [
            {"id": m.id, "start": m.start, "end": m.end, "peak_time": m.peak_time,
             "score": m.score, "title": m.title, "description": m.description,
             "reason": m.reason, "category": m.category, "signals": m.signals}
            for m in rows
        ],
    }


def _http(exc: PolixorError) -> HTTPException:
    from .http import ERROR_STATUS

    if exc.code in ERROR_STATUS:
        return http_error(exc)
    status = {
        "job_not_found": 404, "clip_not_found": 404,
        "invalid_url": 400, "unsupported_platform": 400,
        "private_or_unavailable": 403, "drm_protected": 403, "restricted": 403,
        "disk_space": 507, "ffmpeg_missing": 503, "model_unavailable": 503,
        "ai_provider_failed": 502, "network_error": 503,
        # תמונות
        "image_not_found": 404, "image_key_missing": 503,
        "image_rate_limit": 429, "image_timeout": 504,
        "image_unavailable": 503, "image_failed": 502,
        "image_bad_response": 502, "image_rejected": 422,
        "image_not_ready": 409, "image_cancelled": 409,
        "invalid_role": 400, "invalid_placement": 400,
        # לייב
        "live_not_started": 409, "live_already_running": 409,
        "live_invalid_state": 409,
    }.get(exc.code, 400)
    return HTTPException(status_code=status, detail=exc.to_dict())
