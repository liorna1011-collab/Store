"""
Project API – הזרימה המלאה: ייבוא → ניתוח → מצב והגדרות → יצירה → תוצאות.

פרויקט הוא שורת `Job` מורחבת (אותו מזהה), ולכן `/api/jobs` ממשיך
לעבוד לתאימות לאחור. הלקוח לעולם אינו מקור אמת: כל הגדרה שמגיעה
ממנו עוברת אימות ותיקון בשרת (`project_config.clamp_config`).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy import desc
from sqlalchemy.orm import Session

from .. import i18n
from ..config import PATHS, SETTINGS
from ..db import db_dependency
from ..errors import (
    AnalysisIncompleteError,
    InvalidSectionError,
    LiveRequiresCaptureError,
    PolixorError,
    ProjectBusyError,
    ProjectNotFoundError,
    SourceTooLongError,
    UploadMissingError,
)
from ..models import (
    Clip,
    ImagePlacement,
    Job,
    JobStage,
    JobStatus,
    Moment,
    ProjectPhase,
    RunScope,
    Source,
    StageTiming,
    SubtitleCue,
    TranscriptSegment,
    new_id,
)
from ..project_config import (
    CONTENT_LANGUAGES,
    MODES,
    clamp_config,
    deep_merge,
    default_config,
    options,
)
from ..schemas import (
    ClipOut,
    CreateProjectBody,
    GenerateBody,
    ProjectListOut,
    ProjectOut,
    ProjectPatch,
)
from ..services import ingest
from ..util.fs import is_within, rmtree_quiet, safe_filename
from ..worker import MANAGER
from .http import api_error, http_error
from .serializers import clip_to_out, project_to_out

log = logging.getLogger("polixor.api.projects")
router = APIRouter(prefix="/api/projects", tags=["projects"])

# הקלטת שידור חי: לפחות דקה, לכל היותר 6 שעות
LIVE_CAPTURE_RANGE = (60.0, 6 * 3600.0)
# קבצי ניתוח שנמחקים לפני ניתוח חוזר (המקור נשמר)
_ANALYSIS_ARTIFACTS = (
    "transcript_path", "audio_features_path", "silences_path", "timeline_full_path",
    "timeline_path", "visual_path", "faces_path", "layouts_path", "layout_summary",
    "camera_region", "candidates_path", "content_language", "notes",
    "clip_review_path",
)


def _get(db: Session, project_id: str) -> Job:
    job = db.get(Job, project_id)
    if job is None:
        raise http_error(ProjectNotFoundError())
    return job


def _out(db: Session, job: Job, **kw: Any) -> ProjectOut:
    db.refresh(job)
    return project_to_out(db, job, **kw)


def _is_busy(job: Job) -> bool:
    return MANAGER.is_running(job.id) or job.status in (JobStatus.QUEUED, JobStatus.RUNNING)


# --------------------------------------------------------------------------
# ברירות מחדל
# --------------------------------------------------------------------------
@router.get("/defaults")
def project_defaults(lang: Optional[str] = None) -> dict[str, Any]:
    ui = i18n.normalize_lang(lang) or i18n.get_lang()
    return {"config": default_config(ui), "options": options()}


# --------------------------------------------------------------------------
# יצירה
# --------------------------------------------------------------------------
@router.post("", response_model=ProjectOut)
def create_project(body: CreateProjectBody,
                   db: Session = Depends(db_dependency)) -> ProjectOut:
    ui_lang = i18n.normalize_lang(body.ui_language) or i18n.get_lang()
    content_lang = body.content_language if body.content_language in CONTENT_LANGUAGES \
        else "auto"
    settings = SETTINGS.get()
    preview = body.preview.model_dump(exclude_none=True) if body.preview else {}
    title = (body.title or "").strip()[:200]
    artifacts: dict[str, Any] = {}
    input_url = ""
    source_id: Optional[str] = None
    is_live_mode = False
    src = body.source

    if src.type == "upload":
        path = PATHS.sources / safe_filename(src.upload_token or "", max_length=160)
        if not src.upload_token or not path.exists():
            raise http_error(UploadMissingError())
        try:
            local = ingest.register_local_file(path, copy=False)
        except PolixorError as exc:
            raise http_error(exc) from exc
        source = Source(
            id=new_id(), kind=local.kind, url="", title=local.title,
            file_path=str(path), file_size=local.filesize, duration=local.duration,
            width=int(local.extra.get("width") or 0),
            height=int(local.extra.get("height") or 0),
            fps=float(local.extra.get("fps") or 0.0))
        db.add(source)
        db.flush()
        source_id = source.id
        artifacts.update({"source_path": str(path), "source_kind": "upload",
                          "platform": "upload"})
        title = title or local.title
    elif src.type == "url":
        try:
            resolved = ingest.resolve_url(src.url)
            ingest.check_url_allowed(resolved, settings)
        except PolixorError as exc:
            raise http_error(exc) from exc
        duration = float(preview.get("duration") or 0.0)
        if src.section is not None:
            start, end = float(src.section.start), float(src.section.end)
            if start < 0 or end - start < 5.0 or (duration and start >= duration):
                raise http_error(InvalidSectionError())
            if duration:
                end = min(end, duration)
            artifacts["section"] = {"start": round(start, 3), "end": round(end, 3)}
        live = bool(resolved.is_live or preview.get("is_live"))
        if live:
            secs = src.live_capture_seconds
            if not secs:
                raise http_error(LiveRequiresCaptureError())
            lo, hi = LIVE_CAPTURE_RANGE
            artifacts["live_capture_seconds"] = float(min(hi, max(lo, float(secs))))
            is_live_mode = True
        elif duration and duration > settings.max_source_hours * 3600 \
                and "section" not in artifacts:
            raise http_error(SourceTooLongError(
                params={"hours": f"{settings.max_source_hours:g}"}))
        input_url = resolved.normalized_url or src.url.strip()
        artifacts.update({"source_kind": resolved.kind.value,
                          "platform": resolved.platform})
        if resolved.start_hint is not None:
            artifacts["start_hint"] = resolved.start_hint
        title = title or preview.get("title") or resolved.platform_label
    else:
        raise api_error("missing_input")

    if preview:
        artifacts["preview"] = {k: v for k, v in preview.items()
                                if k in ("title", "thumbnail", "duration", "platform",
                                         "uploader", "is_live")}

    config = default_config(ui_lang, content_lang)
    job = Job(
        id=new_id(), source_id=source_id, title=title or "", input_url=input_url,
        status=JobStatus.QUEUED, stage=JobStage.PENDING,
        is_live_mode=is_live_mode,
        settings_snapshot=settings.to_dict(), artifacts=artifacts,
        completed_stages=[], message=i18n.tr("pipeline.status.queued", ui_lang),
        run_scope=RunScope.ANALYZE.value, phase=ProjectPhase.IMPORTING.value,
        mode=None, ui_language=ui_lang, content_language=content_lang,
        project_config=config, analysis=None, error_data={},
    )
    db.add(job)
    db.commit()
    MANAGER.submit(job.id)
    return _out(db, job)


# --------------------------------------------------------------------------
# קריאה
# --------------------------------------------------------------------------
@router.get("", response_model=ProjectListOut)
def list_projects(limit: int = Query(100, ge=1, le=500),
                  phase: Optional[str] = None,
                  db: Session = Depends(db_dependency)) -> ProjectListOut:
    rows = db.query(Job).order_by(desc(Job.created_at)).limit(limit).all()
    items = [project_to_out(db, j, include_analysis=False) for j in rows]
    if phase:
        wanted = {p.strip() for p in phase.split(",") if p.strip()}
        items = [p for p in items if p.phase in wanted]
    return ProjectListOut(items=items)


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: str, db: Session = Depends(db_dependency)) -> ProjectOut:
    return project_to_out(db, _get(db, project_id))


@router.get("/{project_id}/clips", response_model=list[ClipOut])
def project_clips(project_id: str, db: Session = Depends(db_dependency)) -> list[ClipOut]:
    _get(db, project_id)
    rows = (db.query(Clip).filter(Clip.job_id == project_id)
            .order_by(Clip.source_start, Clip.created_at).all())
    return [clip_to_out(db, c) for c in rows]


@router.get("/{project_id}/clip-review")
def get_clip_review(project_id: str, db: Session = Depends(db_dependency)) -> dict[str, Any]:
    """
    דוח הבחירה של מנוע הקליפים: לכל קליפ שנבחר – למה הוצע, הוו, ההקשר,
    הפאנץ', רכיבי הציון, הקנסות והגבולות; ולכל "כמעט" וכפילות – למה נדחו.
    """
    from ..services import analysis_store

    job = _get(db, project_id)
    path = (job.artifacts or {}).get("clip_review_path")
    review = analysis_store.load_clip_review(Path(path)) if path else None
    if review is None:
        return {"available": False}
    from ..services.clip_intel.review import localize

    return {"available": True, **localize(review)}


@router.get("/{project_id}/thumbnail")
def project_thumbnail(project_id: str, db: Session = Depends(db_dependency)):
    """
    תמונה ממוזערת מקובץ המקור המקומי, נשמרת במטמון. אין כאן פנייה
    לכתובות מרוחקות – תמונת תצוגה של YouTube וכדומה מוצגת ישירות
    מהדפדפן ולא עוברת דרך השרת.
    """
    from ..pipeline import extract_source_thumbnail

    job = _get(db, project_id)
    cache = PATHS.job_work_dir(job.id) / "source_thumb.jpg"
    if not cache.exists():
        if extract_source_thumbnail(job, cache) is None:
            raise api_error("thumb_missing")
    return FileResponse(cache, media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=3600"})


# --------------------------------------------------------------------------
# עדכון
# --------------------------------------------------------------------------
@router.patch("/{project_id}", response_model=ProjectOut)
def patch_project(project_id: str, body: ProjectPatch,
                  db: Session = Depends(db_dependency)) -> ProjectOut:
    job = _get(db, project_id)
    generating = _is_busy(job) and (job.run_scope == RunScope.GENERATE.value)
    if generating and (body.config is not None or body.mode is not None):
        raise http_error(ProjectBusyError())

    if body.title is not None:
        job.title = body.title.strip()[:200]
    if body.ui_language is not None:
        job.ui_language = i18n.normalize_lang(body.ui_language) or job.ui_language
    if body.content_language is not None and body.content_language in CONTENT_LANGUAGES:
        job.content_language = body.content_language
    cfg = dict(job.project_config or {})
    if body.config is not None:
        cfg = deep_merge(cfg, body.config)
    if body.mode is not None:
        if body.mode not in MODES:
            raise api_error("invalid_style")
        cfg["mode"] = body.mode
        job.mode = body.mode
    if body.content_language is not None:
        cfg["content_language"] = job.content_language
    job.project_config = clamp_config(cfg, ui_language=job.ui_language or "he")
    if body.config is not None and "mode" in (body.config or {}) \
            and job.project_config.get("mode") in MODES:
        job.mode = job.project_config["mode"]
    db.commit()
    return _out(db, job)


# --------------------------------------------------------------------------
# ניתוח חוזר, יצירה, ביטול
# --------------------------------------------------------------------------
@router.post("/{project_id}/analyze", response_model=ProjectOut)
def reanalyze(project_id: str, db: Session = Depends(db_dependency)) -> ProjectOut:
    """מריץ את הניתוח מחדש. קובץ המקור (שהורד או הועלה) נשמר."""
    job = _get(db, project_id)
    if _is_busy(job):
        raise http_error(ProjectBusyError())
    arts = dict(job.artifacts or {})
    for key in _ANALYSIS_ARTIFACTS:
        value = arts.pop(key, None)
        if isinstance(value, str) and value and key.endswith("_path"):
            p = Path(value)
            if is_within(p, [PATHS.work]):
                p.unlink(missing_ok=True)
    keep = {JobStage.DOWNLOAD.value, JobStage.CAPTURE.value}
    job.completed_stages = [s for s in (job.completed_stages or []) if s in keep]
    job.artifacts = arts
    job.analysis = None
    db.query(TranscriptSegment).filter(TranscriptSegment.job_id == job.id).delete()
    _queue(job, RunScope.ANALYZE, ProjectPhase.ANALYZING if arts.get("source_path")
           else ProjectPhase.IMPORTING)
    db.commit()
    MANAGER.submit(job.id)
    return _out(db, job)


@router.post("/{project_id}/generate", response_model=ProjectOut)
def generate(project_id: str, body: Optional[GenerateBody] = None,
             db: Session = Depends(db_dependency)) -> ProjectOut:
    job = _get(db, project_id)
    if _is_busy(job):
        raise http_error(ProjectBusyError())
    arts = job.artifacts or {}
    if job.analysis is None or not arts.get("timeline_full_path") \
            or not Path(arts["timeline_full_path"]).exists():
        raise http_error(AnalysisIncompleteError())
    cfg = dict(job.project_config or {})
    if body is not None and body.config:
        cfg = deep_merge(cfg, body.config)
    if body is not None and body.mode:
        if body.mode not in MODES:
            raise api_error("invalid_style")
        cfg["mode"] = body.mode
    cfg = clamp_config(cfg, ui_language=job.ui_language or "he")
    job.project_config = cfg
    job.mode = cfg.get("mode") or job.mode or "short"
    # ההגדרות הגלובליות (סגנון עריכה, מאסטרינג) כפי שהן עכשיו
    job.settings_snapshot = {**(job.settings_snapshot or {}),
                             **{k: v for k, v in SETTINGS.get().to_dict().items()
                                if k not in ("transcript_provider", "whisper_model",
                                             "transcribe_language")}}
    _queue(job, RunScope.GENERATE, ProjectPhase.GENERATING)
    db.commit()
    MANAGER.submit(job.id)
    return _out(db, job)


def _queue(job: Job, scope: RunScope, phase: ProjectPhase) -> None:
    job.run_scope = scope.value
    job.phase = phase.value
    job.status = JobStatus.QUEUED
    job.error = ""
    job.error_code = ""
    job.error_data = {}
    job.finished_at = None
    job.stage_progress = 0.0
    job.overall_progress = 0.0
    job.message = i18n.tr("pipeline.status.queued", job.ui_language)


@router.post("/{project_id}/cancel", response_model=ProjectOut)
def cancel_project(project_id: str, db: Session = Depends(db_dependency)) -> ProjectOut:
    job = _get(db, project_id)
    MANAGER.cancel(job.id)
    return _out(db, job)


# --------------------------------------------------------------------------
# מחיקה
# --------------------------------------------------------------------------
@router.delete("/{project_id}")
def delete_project(project_id: str, delete_files: bool = Query(True),
                   db: Session = Depends(db_dependency)) -> dict[str, Any]:
    job = _get(db, project_id)
    MANAGER.cancel(job.id)
    removed = 0
    clips = db.query(Clip).filter(Clip.job_id == job.id).all()
    if delete_files:
        roots = [PATHS.exports, PATHS.work, SETTINGS.get().resolved_export_dir()]
        for clip in clips:
            for p in (clip.file_path, clip.thumbnail_path):
                if p and Path(p).exists() and is_within(Path(p), roots):
                    try:
                        Path(p).unlink()
                        removed += 1
                    except OSError:
                        pass
        rmtree_quiet(PATHS.work / job.id)
        rmtree_quiet(PATHS.exports / job.id)
        rmtree_quiet(SETTINGS.get().resolved_export_dir() / job.id)
        rmtree_quiet(PATHS.captures / job.id)
        # קובץ המקור שייך לפרויקט (הועלה או הורד אליו) – אלא אם פרויקט
        # אחר עדיין משתמש בו
        src_path = (job.artifacts or {}).get("source_path")
        if src_path and is_within(Path(src_path), [PATHS.sources]):
            others = [j for j in db.query(Job).filter(Job.id != job.id).all()
                      if (j.artifacts or {}).get("source_path") == src_path]
            if not others:
                try:
                    Path(src_path).unlink(missing_ok=True)
                    removed += 1
                except OSError:
                    pass
    for clip in clips:
        db.query(SubtitleCue).filter(SubtitleCue.clip_id == clip.id).delete()
        db.query(ImagePlacement).filter(ImagePlacement.clip_id == clip.id).delete()
        db.delete(clip)
    db.query(TranscriptSegment).filter(TranscriptSegment.job_id == job.id).delete()
    db.query(Moment).filter(Moment.job_id == job.id).delete()
    db.query(StageTiming).filter(StageTiming.job_id == job.id).delete()
    source_id = job.source_id
    db.delete(job)
    db.flush()
    if source_id and not db.query(Job).filter(Job.source_id == source_id).count():
        src = db.get(Source, source_id)
        if src is not None:
            db.delete(src)
    db.commit()
    return {"deleted": True, "files_removed": removed}
