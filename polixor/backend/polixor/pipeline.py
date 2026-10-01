"""
תזמור הפייפליין: קליטה → אודיו → תמלול → ניתוח → בחירה → ייצוא.

שלושה היקפי ריצה (`Job.run_scope`):

  all       ההתנהגות הקודמת: הכול בריצה אחת (משימות שנוצרו ב-/api/jobs).
  analyze   קליטה/הורדה, בדיקת קובץ, אודיו, תמלול, ניתוח ופריסות. כל מה
            ששלב היצירה צריך נשמר לדיסק (`analysis_store`), ונכתב סיכום
            הניתוח לפרויקט. בסוף השלב הפרויקט עובר ל-"configure".
  generate  טוען את הניתוח השמור – בלי להוריד, לתמלל או לנתח מחדש – מוחק
            את התוצאות הקודמות (Regenerate מחליף ולא מכפיל), ובוחר
            ומרנדר לפי מצב הפרויקט: שורטים או סרטון ארוך.

כל שלב שומר צ'קפוינט (`completed_stages` + `artifacts`). ריצה חוזרת
אחרי נפילה ממשיכה מהשלב האחרון שהושלם: ניתוח שנשמר נטען, מועמדים
שנבחרו נטענים, וקליפים מסוג שכבר רונדר במלואו אינם מרונדרים שוב.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np

from . import clip_factory, i18n
from .clip_factory import _beats_in_output_time, _mix_music  # noqa: F401 (תאימות לבדיקות)
from .config import PATHS, AppSettings
from .db import session_scope
from .errors import (
    AnalysisIncompleteError,
    JobCancelledError,
    NoMomentsFoundError,
    PolixorError,
    SourceTooShortError,
)
from .events import BUS
from .models import (
    Clip,
    ClipKind,
    ImagePlacement,
    Job,
    JobStage,
    JobStatus,
    LiveState,
    Moment,
    ProjectPhase,
    RunScope,
    Source,
    SourceKind,
    SubtitleCue,
    TranscriptSegment,
    new_id,
)
from .profiles import resolve_profile
from .project_config import settings_for_project
from .services import analysis_store, ingest, llm, scoring, selection
from .services import live as live_svc
from .services import live_capture
from .services.audio import AudioFeatures, analyze_audio, integrated_loudness
from .services.layout_detect import LayoutTimeline, detect_layouts
from .services import clip_intel
from .services.transcribe import Segment, TranscriptResult, Word, transcribe_audio
from .services.visual import VisualFeatures, analyze_video, estimate_camera_region
from .util.ffmpeg import extract_audio_wav, extract_thumbnail, probe, silence_intervals
from .util.fs import rmtree_quiet
from .util import timing
from .util.text import format_duration_he
from .worker import MANAGER, ProgressReporter

log = logging.getLogger("polixor.pipeline")


def T(key: str, **params: Any) -> str:
    """הודעת פייפליין בשפת הפרויקט (הפעילה בהקשר הריצה)."""
    return i18n.tr(f"pipeline.{key}", **params)


# --------------------------------------------------------------------------
# נקודת הכניסה
# --------------------------------------------------------------------------
def run_job(job_id: str, cancel_event: threading.Event) -> None:
    """מריץ משימה לפי ההיקף שלה. נקרא מתוך תהליכון של JobManager."""
    with session_scope() as s:
        job = s.get(Job, job_id)
        if job is None:
            raise PolixorError(message_key="errors.job_not_found.message")
        scope = job.run_scope or RunScope.ALL.value
        settings = settings_for_job(job)
        is_live = bool(job.is_live_mode)
        input_url = job.input_url
        completed = set(job.completed_stages or [])
        artifacts = dict(job.artifacts or {})
        lang = job.ui_language or i18n.DEFAULT_LANG
        mode = job.mode or (job.project_config or {}).get("mode")
        config = dict(job.project_config or {})
        is_project = bool(job.phase)

    with i18n.use_lang(lang):
        planned = _plan_stages(settings,
                               has_local_source=bool(artifacts.get("source_path")),
                               is_live=is_live, scope=scope, mode=mode)
        reporter = ProgressReporter(job_id, planned, cancel_event)
        ctx = JobContext(job_id=job_id, settings=settings, reporter=reporter,
                         cancel_event=cancel_event, artifacts=artifacts,
                         completed=completed, input_url=input_url)
        ctx.scope, ctx.mode, ctx.config = scope, mode, config
        ctx.is_project, ctx.is_live = is_project, is_live

        with timing.collect() as subs:
            try:
                if scope == RunScope.ANALYZE.value:
                    _run_analyze(ctx)
                elif scope == RunScope.GENERATE.value:
                    _run_generate(ctx)
                else:
                    _run_all(ctx)
            finally:
                _save_substage_timings(job_id, scope, subs)


def _save_substage_timings(job_id: str, scope: str, subs: list[dict[str, Any]]) -> None:
    """שומר את מדידות תתי-השלבים של הריצה הזו בארטיפקטים (לדוח הביצועים)."""
    if not subs:
        return
    with session_scope() as s:
        job = s.get(Job, job_id)
        if job is None:
            return
        arts = dict(job.artifacts or {})
        runs = list(arts.get("substage_timings") or [])[-9:]
        runs.append({"scope": scope, "items": subs})
        arts["substage_timings"] = runs
        job.artifacts = arts


def settings_for_job(job: Job) -> AppSettings:
    """הגדרות הפייפליין למשימה: מהצילום שנשמר, ולפרויקט – גם ממה שנבחר בו."""
    base = AppSettings.from_dict(job.settings_snapshot or {})
    if job.phase or (job.run_scope or "all") != RunScope.ALL.value:
        cfg = dict(job.project_config or {})
        if job.mode:
            cfg["mode"] = job.mode
        return settings_for_project(base, cfg, content_language=job.content_language)
    return base


class JobContext:
    """מצב משותף לשלבי הפייפליין."""

    def __init__(self, *, job_id: str, settings: AppSettings,
                 reporter: ProgressReporter, cancel_event: threading.Event,
                 artifacts: dict[str, Any], completed: set[str],
                 input_url: str) -> None:
        self.job_id = job_id
        self.settings = settings
        self.reporter = reporter
        self.cancel_event = cancel_event
        self.artifacts = artifacts
        self.completed = completed
        self.input_url = input_url
        self.work_dir = PATHS.job_work_dir(job_id)
        self.export_dir = _export_dir_for(settings, job_id)

        self.scope = RunScope.ALL.value
        self.mode: Optional[str] = None
        self.config: dict[str, Any] = {}
        self.is_project = False
        self.is_live = False

        self.source_path: Optional[Path] = None
        self.source_info: dict[str, Any] = {}
        self.audio_path: Optional[Path] = None
        self.transcript: Optional[TranscriptResult] = None
        # התמלול כפי שהמזהה הפיק (לפני הגהה); ctx.transcript הוא האפקטיבי
        self.transcript_original: Optional[TranscriptResult] = None
        self.audio_feats: Optional[AudioFeatures] = None
        self.visual_feats: Optional[VisualFeatures] = None
        self.layout_timeline: Optional[LayoutTimeline] = None
        self.timeline: Optional[scoring.Timeline] = None
        self.silences: list[tuple[float, float]] = []
        self.camera_region: Optional[dict[str, float]] = None
        self.language: Optional[str] = None
        self.notes: list[str] = []

    def done(self, stage: JobStage) -> bool:
        return stage.value in self.completed

    def mark(self, stage: JobStage, media_seconds: float = 0.0) -> None:
        self.completed.add(stage.value)
        self.reporter.finish_stage(stage, media_seconds=media_seconds)
        self._persist()

    def unmark(self, *stages: JobStage) -> None:
        for st in stages:
            self.completed.discard(st.value)
        self._persist()

    def _persist(self) -> None:
        with session_scope() as s:
            job = s.get(Job, self.job_id)
            if job is not None:
                job.artifacts = dict(self.artifacts)
                job.completed_stages = sorted(self.completed)

    def note(self, text: str) -> None:
        if text and text not in self.notes:
            self.notes.append(text)
            self.reporter.log(text, level="info")


def _plan_stages(settings: AppSettings, *, has_local_source: bool,
                 is_live: bool = False, scope: str = "all",
                 mode: Optional[str] = None) -> list[JobStage]:
    """השלבים שירוצו בפועל – הבסיס לחישוב ההתקדמות הכוללת."""
    if scope == RunScope.GENERATE.value:
        if (mode or "short") == "longform":
            return [JobStage.SELECT, JobStage.RENDER_LONG]
        return [JobStage.SELECT, JobStage.RENDER_SHORT]

    if is_live and not has_local_source:
        stages = [JobStage.CAPTURE]
    else:
        stages = [] if has_local_source else [JobStage.DOWNLOAD]
    stages += [JobStage.PROBE, JobStage.AUDIO]
    if settings.transcript_provider != "none":
        stages.append(JobStage.TRANSCRIBE)
    stages.append(JobStage.ANALYZE)
    if scope == RunScope.ANALYZE.value:
        return stages
    stages.append(JobStage.SELECT)
    if settings.long_enabled and settings.long_count > 0:
        stages.append(JobStage.RENDER_LONG)
    if settings.short_enabled and settings.short_count > 0:
        stages.append(JobStage.RENDER_SHORT)
    return stages


def _export_dir_for(settings: AppSettings, job_id: str) -> Path:
    base = settings.resolved_export_dir()
    d = base / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _set_phase(job_id: str, phase: str) -> None:
    """מעדכן את שלב הפרויקט ומשדר project.updated."""
    with session_scope() as s:
        job = s.get(Job, job_id)
        if job is None or not job.phase or job.phase == phase:
            return
        job.phase = phase
        status = job.status.value
    BUS.emit("project.updated", job_id, project_id=job_id, phase=phase, status=status)


# --------------------------------------------------------------------------
# היקפי ריצה
# --------------------------------------------------------------------------
def _acquire(ctx: JobContext) -> None:
    """קליטת המקור: הקלטה חיה או הורדה (או קובץ שכבר קיים)."""
    if ctx.is_live:
        _stage_live_capture(ctx)
    else:
        _stage_download(ctx)


def _run_all(ctx: JobContext) -> None:
    """
    ריצה מלאה (התנהגות קודמת), עם המשך אמיתי אחרי נפילה.

    באג שתוקן: `done()` לא נקרא אף פעם, ולכן ריצה חוזרת בלי
    `from_start` ניתחה, בחרה ורינדרה הכול מחדש – ויצרה רגעים וקליפים
    כפולים. עכשיו כל שלב שהושלם נטען מהדיסק במקום לרוץ שוב.
    """
    _acquire(ctx)
    _stage_probe(ctx)
    _stage_audio(ctx)
    _stage_transcribe(ctx)
    if not (ctx.done(JobStage.ANALYZE) and _load_saved_analysis(ctx)):
        _stage_analyze(ctx)
    groups = None
    if ctx.done(JobStage.SELECT):
        groups = analysis_store.load_candidates(_art_path(ctx, "candidates_path"))
    if groups is None:
        _clear_results(ctx.job_id, moments=True, clip_kinds=None)
        ctx.unmark(JobStage.SELECT, JobStage.RENDER_LONG, JobStage.RENDER_SHORT)
        groups = _stage_select(ctx)
    _stage_render(ctx, groups)
    _finalize_notes(ctx)


def _run_analyze(ctx: JobContext) -> None:
    """שלב הניתוח של פרויקט: עד ניתוח שמור וסיכום לממשק."""
    _set_phase(ctx.job_id, ProjectPhase.IMPORTING.value)
    _acquire(ctx)
    _set_phase(ctx.job_id, ProjectPhase.ANALYZING.value)
    _stage_probe(ctx)
    _stage_audio(ctx)
    _stage_transcribe(ctx)
    if not (ctx.done(JobStage.ANALYZE) and _load_saved_analysis(ctx)):
        _stage_analyze(ctx)
    _store_analysis_summary(ctx)
    _finalize_notes(ctx)


def _run_generate(ctx: JobContext) -> None:
    """
    יצירה מתוך ניתוח שמור.

    אין כאן הורדה, תמלול או ניתוח: אם הניתוח חסר, זו שגיאה מפורשת
    ולא ניתוח שקט מחדש.
    """
    _set_phase(ctx.job_id, ProjectPhase.GENERATING.value)
    sp = ctx.artifacts.get("source_path")
    if not sp or not Path(sp).exists():
        raise PolixorError(message_key="errors.source_missing.message",
                           hint_key="errors.source_missing.hint")
    ctx.source_path = Path(sp)
    ctx.source_info = dict(ctx.artifacts.get("source_info") or {})
    if not ctx.source_info:
        raise AnalysisIncompleteError()
    ap = ctx.artifacts.get("audio_path")
    ctx.audio_path = Path(ap) if ap and Path(ap).exists() else None
    if not _load_saved_analysis(ctx):
        raise AnalysisIncompleteError()
    scoring.fuse_score(ctx.timeline, ctx.settings)

    ctx.reporter.start_stage(JobStage.SELECT, T("generate.clearing"))
    _clear_results(ctx.job_id, moments=True, clip_kinds=None)
    ctx.unmark(JobStage.SELECT, JobStage.RENDER_LONG, JobStage.RENDER_SHORT)

    if (ctx.mode or "short") == "longform":
        _generate_longform(ctx)
    else:
        groups = _stage_select(ctx)
        _stage_render(ctx, groups)
    _finalize_notes(ctx)


def _generate_longform(ctx: JobContext) -> None:
    """מחובר במודול long-form (ראו services/longform.py)."""
    from .longform_render import generate_longform

    generate_longform(ctx)


# --------------------------------------------------------------------------
# מצב שידור חי: קליטה → מקור → אותו פייפליין בדיוק
# --------------------------------------------------------------------------
def _stage_live_capture(ctx: JobContext) -> None:
    """
    מקליט את השידור עד שהמשתמש לוחץ 'עצור הקלטה', עד שהזמן שנבחר
    מסתיים, או עד שהשידור מסתיים.

    כל מקטע שנסגר נרשם מיד ב-DB, ולכן נפילה של הזרם או של השרת
    אינה מאבדת את מה שכבר הוקלט.
    """
    existing = ctx.artifacts.get("source_path")
    if existing and Path(existing).exists():
        ctx.source_path = Path(existing)
        if not ctx.done(JobStage.CAPTURE):
            ctx.reporter.finish_stage(JobStage.CAPTURE)
        return

    ctx.reporter.start_stage(JobStage.CAPTURE, T("capture.detecting"))
    _set_live_state(ctx.job_id, LiveState.DETECTING.value, T("capture.detecting_short"))

    info = live_svc.detect_stream(ctx.input_url, ctx.settings)
    if not info.available:
        _set_live_state(ctx.job_id, LiveState.FAILED.value, info.reason)
        raise live_svc.LiveUnavailableError(info.reason or None)

    ctx.note(T("capture.detected", platform=info.platform,
               title=(f" · {info.title}" if info.title else ""),
               resolution=(f" · {info.resolution_label}" if info.resolution_label else "")))
    if not info.has_audio:
        ctx.note(T("capture.no_audio"))

    stop_event = LIVE_STOPS.setdefault(ctx.job_id, threading.Event())
    machine = live_capture.StateMachine(
        lambda state, detail: _set_live_state(ctx.job_id, state, detail))

    segments_seen: list[dict[str, Any]] = []
    last_tick = {"t": 0.0}
    limit = float(ctx.artifacts.get("live_capture_seconds") or 0.0)

    def on_segment(result) -> None:
        segments_seen.append({"path": str(result.path), "seconds": result.seconds,
                              "complete": result.complete})
        _persist_live_segments(ctx.job_id, segments_seen)
        ctx.reporter.log(T("capture.segment_saved", n=len(segments_seen),
                           duration=format_duration_he(result.seconds)), level="info")

    def on_tick(total: float, _in_segment: float) -> None:
        now = time.time()
        if now - last_tick["t"] < 1.0:
            return
        last_tick["t"] = now
        _publish_live_tick(ctx.job_id, total)
        # כשנבחר משך הקלטה יש יעד ידוע ולכן אחוז אמיתי; בלעדיו מדווחים
        # את משך ההקלטה בפועל במקום מד התקדמות מדומה.
        frac = min(0.89, total / limit) if limit > 0 else 0.0
        ctx.reporter.progress(frac, T("capture.recording",
                                      duration=format_duration_he(total)))

    max_seconds = limit if limit > 0 else float(ctx.settings.live_max_minutes) * 60.0
    outcome = live_capture.run_capture(
        live_capture.CaptureConfig(
            url=info.url, work_dir=PATHS.capture_dir(ctx.job_id),
            segment_seconds=float(min(ctx.settings.live_segment_seconds,
                                      max_seconds or ctx.settings.live_segment_seconds)),
            max_seconds=max_seconds),
        settings=ctx.settings, cancel_event=ctx.cancel_event,
        stop_event=stop_event, machine=machine,
        on_segment=on_segment, on_tick=on_tick)

    LIVE_STOPS.pop(ctx.job_id, None)

    if not outcome.segments:
        _set_live_state(ctx.job_id, LiveState.FAILED.value,
                        outcome.error or T("capture.nothing"))
        raise live_svc.LiveUnavailableError(
            outcome.error or None,
            message_key="errors.live_nothing_captured.message",
            hint_key="errors.live_nothing_captured.hint")

    ctx.reporter.progress(0.9, T("capture.merging"))
    dest = PATHS.sources / f"live_{ctx.job_id}.mp4"
    try:
        final = live_svc.concat_segments(outcome.paths, dest)
    except PolixorError:
        ctx.note(T("capture.merge_failed", n=len(outcome.segments),
                   folder=str(PATHS.capture_dir(ctx.job_id))))
        raise

    media = probe(final)
    ctx.source_path = final
    ctx.artifacts["source_path"] = str(final)
    ctx.artifacts["live_capture"] = {
        "segments": len(outcome.segments), "reconnects": outcome.reconnects,
        "seconds": outcome.seconds, "stopped_by_user": outcome.stopped_by_user,
        "platform": info.platform, "title": info.title,
    }

    with session_scope() as s:
        job = s.get(Job, ctx.job_id)
        source = Source(
            id=new_id(), kind=_source_kind_for(info.kind), url=info.url,
            title=info.title or T("capture.title_fallback", platform=info.platform),
            uploader=info.uploader, file_path=str(final),
            file_size=final.stat().st_size, duration=media.duration,
            width=media.width, height=media.height, fps=media.fps,
            has_audio=media.has_audio, is_live=True,
            extra={"captured": True, "segments": len(outcome.segments),
                   "reconnects": outcome.reconnects,
                   "thumbnail": info.thumbnail})
        s.add(source)
        s.flush()
        if job is not None:
            job.source_id = source.id
            job.live_captured_seconds = outcome.seconds
            job.live_reconnects = outcome.reconnects
            if not job.title:
                job.title = source.title

    ctx.note(live_capture.describe_outcome(outcome))
    if outcome.reconnects:
        ctx.note(T("capture.reconnects", n=outcome.reconnects))
    ctx.mark(JobStage.CAPTURE, media_seconds=media.duration)


def _source_kind_for(kind: str) -> SourceKind:
    try:
        return SourceKind(kind)
    except ValueError:
        return SourceKind.UNKNOWN


# מפתח → אירוע עצירה, כדי ש-API יוכל לעצור הקלטה פעילה
LIVE_STOPS: dict[str, threading.Event] = {}


def request_live_stop(job_id: str) -> bool:
    """מבקש לעצור הקלטה פעילה. המקטע הנוכחי נסגר ונשמר."""
    ev = LIVE_STOPS.get(job_id)
    with session_scope() as s:
        job = s.get(Job, job_id)
        if job is not None:
            job.live_stop_requested = True
    if ev is None:
        return False
    ev.set()
    return True


def is_live_capturing(job_id: str) -> bool:
    return job_id in LIVE_STOPS


def _set_live_state(job_id: str, state: str, detail: str = "") -> None:
    with session_scope() as s:
        job = s.get(Job, job_id)
        if job is None:
            return
        job.live_state = state
        if detail:
            job.message = detail
        if state == LiveState.LIVE.value and job.live_started_at is None:
            job.live_started_at = datetime.now(timezone.utc)
        if state == LiveState.RECONNECTING.value:
            job.live_reconnects = int(job.live_reconnects or 0) + 1
        if state == LiveState.FAILED.value and detail:
            job.live_error = detail
        title = job.title or ""
    BUS.emit("live.state", job_id=job_id, state=state, detail=detail,
             label=i18n.tr(f"pipeline.live_state.{state}", default=state))
    if state == LiveState.FAILED.value:
        from .services import notifications

        notifications.notify("live_failed", params={"project": title, "reason": detail},
                             link=f"/projects/{job_id}", job_id=job_id,
                             group_key=f"job:{job_id}:live_failed")


def _persist_live_segments(job_id: str, segments: list[dict[str, Any]]) -> None:
    total = round(sum(float(s.get("seconds") or 0.0) for s in segments), 2)
    with session_scope() as s:
        job = s.get(Job, job_id)
        if job is None:
            return
        job.live_segments = list(segments)
        job.live_captured_seconds = total
        job.live_cycles = len(segments)
    BUS.emit("live.segment", job_id=job_id, segments=len(segments), seconds=total)


def _publish_live_tick(job_id: str, seconds: float) -> None:
    BUS.emit("live.tick", job_id=job_id, seconds=round(seconds, 1))


# --------------------------------------------------------------------------
# שלבים
# --------------------------------------------------------------------------
def _stage_download(ctx: JobContext, *, live_window: Optional[float] = None) -> None:
    existing = ctx.artifacts.get("source_path")
    if existing and Path(existing).exists():
        ctx.source_path = Path(existing)
        return

    ctx.reporter.start_stage(JobStage.DOWNLOAD, T("download.start"))
    t0 = time.time()
    section = ctx.artifacts.get("section") or None
    result = ingest.download(
        ctx.input_url, settings=ctx.settings, dest_dir=PATHS.sources,
        on_progress=lambda frac, msg: ctx.reporter.progress(frac, msg),
        cancel_event=ctx.cancel_event, live_duration=live_window,
        section=(float(section["start"]), float(section["end"])) if section else None)

    ctx.source_path = result.path
    ctx.artifacts["source_path"] = str(result.path)

    with session_scope() as s:
        job = s.get(Job, ctx.job_id)
        if job is None:
            return
        src = Source(
            id=new_id(), kind=result.kind, url=ctx.input_url,
            title=result.title, uploader=result.uploader,
            file_path=str(result.path), file_size=result.filesize,
            duration=result.duration, is_live=result.is_live,
            width=int(result.extra.get("width") or 0),
            height=int(result.extra.get("height") or 0),
            fps=float(result.extra.get("fps") or 0.0), extra=result.extra)
        s.add(src)
        s.flush()
        job.source_id = src.id
        if not job.title:
            job.title = result.title or result.path.stem

    ctx.reporter.log(T("download.done", title=result.title or result.path.name,
                       duration=format_duration_he(result.duration)), level="info")
    ctx.mark(JobStage.DOWNLOAD, media_seconds=result.duration)
    log.info("download finished in %.1fs", time.time() - t0)


def _stage_probe(ctx: JobContext) -> None:
    ctx.reporter.start_stage(JobStage.PROBE, T("probe.start"))
    if ctx.source_path is None:
        sp = ctx.artifacts.get("source_path")
        if not sp:
            raise PolixorError(message_key="errors.no_source.message")
        ctx.source_path = Path(sp)

    info = probe(ctx.source_path)
    ctx.source_info = {
        "duration": info.duration, "width": info.width, "height": info.height,
        "fps": info.fps, "has_audio": info.has_audio, "has_video": info.has_video,
        "size": info.size_bytes,
    }
    ctx.artifacts["source_info"] = ctx.source_info

    if not info.has_video:
        raise PolixorError(message_key="errors.not_video_file.message")
    if info.duration < 5.0:
        raise SourceTooShortError(
            message_key="errors.source_too_short_seconds.message",
            params={"seconds": f"{info.duration:.1f}"})
    if not info.has_audio:
        ctx.note(T("probe.no_audio"))

    with session_scope() as s:
        job = s.get(Job, ctx.job_id)
        if job is not None and job.source_id:
            src = s.get(Source, job.source_id)
            if src is not None:
                src.duration = info.duration or src.duration
                src.width = info.width or src.width
                src.height = info.height or src.height
                src.fps = info.fps or src.fps
                src.has_audio = info.has_audio
                src.file_size = info.size_bytes or src.file_size

    ctx.reporter.progress(1.0, f"{info.width}x{info.height} · "
                               f"{format_duration_he(info.duration)}")
    ctx.mark(JobStage.PROBE, media_seconds=info.duration)


def _stage_audio(ctx: JobContext) -> None:
    ctx.reporter.start_stage(JobStage.AUDIO, T("audio.start"))
    if not ctx.source_info.get("has_audio", True):
        ctx.audio_path = None
        ctx.mark(JobStage.AUDIO)
        return

    wav = ctx.work_dir / "audio16k.wav"
    if not wav.exists() or wav.stat().st_size < 1024:
        try:
            extract_audio_wav(
                ctx.source_path, wav,
                total_seconds=float(ctx.source_info.get("duration") or 0.0),
                on_progress=lambda f: ctx.reporter.progress(f),
                cancel_event=ctx.cancel_event)
        except JobCancelledError:
            raise
        except PolixorError as exc:
            ctx.note(T("audio.failed", message=exc.message))
            ctx.source_info["has_audio"] = False
            ctx.audio_path = None
            ctx.mark(JobStage.AUDIO)
            return

    ctx.audio_path = wav
    ctx.artifacts["audio_path"] = str(wav)
    ctx.mark(JobStage.AUDIO, media_seconds=float(ctx.source_info.get("duration") or 0.0))


def _stage_transcribe(ctx: JobContext) -> None:
    if ctx.settings.transcript_provider == "none" or ctx.audio_path is None:
        ctx.transcript = None
        if ctx.audio_path is None and ctx.source_info.get("has_audio", True):
            ctx.note(T("transcribe.no_audio"))
        return

    cached = ctx.artifacts.get("transcript_path")
    if cached and Path(cached).exists():
        ctx.transcript = _load_transcript(Path(cached))
        if ctx.transcript is not None:
            _resolve_language(ctx)
            return

    ctx.reporter.start_stage(JobStage.TRANSCRIBE, T("transcribe.start"))
    result = transcribe_audio(
        ctx.audio_path, settings=ctx.settings,
        on_progress=lambda f, msg: ctx.reporter.progress(f, msg),
        cancel_event=ctx.cancel_event,
        media_duration=float(ctx.source_info.get("duration") or 0.0),
        allow_fallback=True, checkpoint_dir=ctx.work_dir / "asr_parts")
    ctx.transcript = result
    if result.note:
        ctx.note(result.note)

    path = ctx.work_dir / "transcript.json"
    _save_transcript(result, path)
    ctx.artifacts["transcript_path"] = str(path)
    _persist_segments(ctx.job_id, result)
    _resolve_language(ctx)

    ctx.reporter.log(T("transcribe.summary", segments=len(result.segments),
                       language=result.language or T("transcribe.language_unknown")),
                     level="info")
    ctx.mark(JobStage.TRANSCRIBE,
             media_seconds=float(ctx.source_info.get("duration") or 0.0))


def _resolve_language(ctx: JobContext) -> None:
    """שפת התוכן: קובעת את חבילת השפה לניתוח ואת שפת הכתוביות והכותרות."""
    try:
        from .services.lang import resolve_language

        ctx.language = resolve_language(ctx.transcript)
    except ImportError:                                 # pragma: no cover
        ctx.language = (ctx.transcript.language if ctx.transcript else None) or None
    if ctx.language:
        ctx.artifacts["content_language"] = ctx.language


def _stage_analyze(ctx: JobContext) -> None:
    """
    ניתוח אודיו, וידאו ופריסות, ושמירת הכול לדיסק.

    הפנים מזוהות **תמיד** – לא רק כשהשורטים פעילים. בעבר הזיהוי היה
    תלוי ב-`short_enabled`, ולכן פרויקט שנותח במצב אחד לא יכול היה
    לעבור למצב אחר בלי ניתוח חוזר.
    """
    ctx.reporter.start_stage(JobStage.ANALYZE, T("analyze.start"))
    duration = float(ctx.source_info.get("duration") or 0.0)

    # -- אודיו --
    ctx.silences = []
    if ctx.audio_path is not None:
        with timing.substage("analyze.audio", media_seconds=duration):
            ctx.audio_feats = analyze_audio(
                ctx.audio_path,
                on_progress=lambda f: ctx.reporter.progress(f * 0.30, T("analyze.audio")),
                cancel_event=ctx.cancel_event)
        if ctx.audio_feats is not None and ctx.audio_feats.silences is not None:
            # נגזר מאותה קריאה של האודיו (בלי מעבר ffmpeg נוסף; זהה ל-silencedetect ±10ms)
            ctx.silences = list(ctx.audio_feats.silences)
        else:
            try:
                with timing.substage("analyze.silences", media_seconds=duration):
                    ctx.silences = silence_intervals(ctx.audio_path, cancel_event=ctx.cancel_event)
            except Exception as exc:  # noqa: BLE001
                log.warning("silencedetect failed: %s", exc)
                ctx.silences = []
    ctx.reporter.progress(0.35, T("analyze.video"))

    # -- וידאו --
    windowed = _visual_windowed(ctx, duration)
    shared = False
    if windowed:
        # מקור ארוך במצב מהיר: הניתוח החזותי המלא רץ אחרי הבחירה, רק על
        # חלונות המועמדים (ראו _ensure_visual_windows). כאן – פריסה גסה בלבד.
        ctx.visual_feats = None
        ctx.artifacts["visual_mode"] = "windows"
        ctx.note(T("analyze.visual_windows", minutes=f"{duration / 60:.0f}"))
    else:
        ctx.artifacts.pop("visual_mode", None)
        shared = _shared_scan(ctx, duration)
        if not shared:
            with timing.substage("analyze.visual", media_seconds=duration):
                ctx.visual_feats = analyze_video(
                    ctx.source_path, sample_fps=ctx.settings.visual_sample_fps,
                    duration=duration, detect_faces=True,
                    on_progress=lambda f: ctx.reporter.progress(0.35 + f * 0.30, T("analyze.frames")),
                    cancel_event=ctx.cancel_event)
        if ctx.visual_feats and not ctx.visual_feats.analyzed and ctx.visual_feats.note:
            ctx.note(ctx.visual_feats.note)

    # -- פריסות: מצלמת תגובה, מצלמה מלאה, מסך --
    ctx.reporter.progress(0.66, T("analyze.layouts"))
    try:
        if not shared:
            with timing.substage("analyze.layouts", media_seconds=duration):
                ctx.layout_timeline = detect_layouts(
                    ctx.source_path, duration=duration,
                    src_w=int(ctx.source_info.get("width") or 0),
                    src_h=int(ctx.source_info.get("height") or 0),
                    cancel_event=ctx.cancel_event,
                    **({"max_samples": COARSE_LAYOUT_SAMPLES, "keyframes_only": True}
                       if windowed else {}),
                    on_progress=lambda f: ctx.reporter.progress(0.66 + f * 0.28,
                                                                T("analyze.layouts")))
    except JobCancelledError:
        raise
    except Exception as exc:                            # noqa: BLE001
        log.warning("layout detection failed: %s", exc, exc_info=True)
        ctx.layout_timeline = None
    if ctx.layout_timeline is not None:
        if ctx.layout_timeline.note == "face_detector_unavailable":
            ctx.note(i18n.tr("layout.detector_unavailable"))
        summary = ctx.layout_timeline.summary()
        if summary.get("facecam_detected"):
            ctx.note(i18n.tr("layout.detected_facecam",
                             segments=summary["facecam_segments"],
                             seconds=f"{summary['facecam_seconds']:.0f}"))

    # -- אזור מצלמה --
    manual = (ctx.settings.to_dict().get("camera_region")
              or ctx.artifacts.get("camera_region_manual"))
    if manual:
        ctx.camera_region = manual
    elif ctx.visual_feats:
        ctx.camera_region = estimate_camera_region(ctx.visual_feats,
                                                   layouts=ctx.layout_timeline)
        if ctx.camera_region:
            ctx.artifacts["camera_region"] = ctx.camera_region
            ctx.note(i18n.tr("layout.detected_camera", confidence=
                             f"{ctx.camera_region.get('confidence', 0) * 100:.0f}"))

    # -- ציר זמן משולב --
    ctx.reporter.progress(0.96, T("analyze.fusing"))
    with timing.substage("analyze.timeline", media_seconds=duration):
        ctx.timeline = scoring.build_timeline(
            audio=ctx.audio_feats, visual=ctx.visual_feats, transcript=ctx.transcript,
            duration=duration, settings=ctx.settings, language=ctx.language)
    _save_analysis(ctx)
    _save_timeline_preview(ctx)
    ctx.mark(JobStage.ANALYZE, media_seconds=duration)


# פריסה גסה (פריימי מפתח) לכל השידור במצב חלונות – לסיכום הניתוח ולסרטון ארוך
COARSE_LAYOUT_SAMPLES = 240


def _shared_scan(ctx: JobContext, duration: float) -> bool:
    """
    ניתוח חזותי + פריסות בפענוח אחד (services/frame_scan). מחזיר False
    כשאי אפשר (אין גלאי פנים, מקור ארוך מאוד שבו הפריסות עוברות לפריימי
    מפתח) או כשנכשל – ואז הקורא מריץ את שני הניתוחים בנפרד כמו קודם.
    """
    from .services import frame_scan

    src_w = int(ctx.source_info.get("width") or 0)
    src_h = int(ctx.source_info.get("height") or 0)
    if ctx.source_path is None or not src_w or not src_h or not frame_scan.can_share(duration):
        return False
    try:
        with timing.substage("analyze.visual_layouts", media_seconds=duration):
            ctx.visual_feats, ctx.layout_timeline = frame_scan.scan_visual_and_layouts(
                ctx.source_path, duration=duration, src_w=src_w, src_h=src_h,
                sample_fps=ctx.settings.visual_sample_fps, cancel_event=ctx.cancel_event,
                on_progress=lambda f: ctx.reporter.progress(0.35 + f * 0.59, T("analyze.frames")))
    except JobCancelledError:
        raise
    except Exception as exc:                            # noqa: BLE001
        log.warning("shared frame scan failed, analysing separately: %s", exc, exc_info=True)
        ctx.visual_feats, ctx.layout_timeline = None, None
        return False
    return True


def _visual_windowed(ctx: JobContext, duration: float) -> bool:
    """
    האם להריץ ניתוח חזותי רק על חלונות המועמדים: מקור ארוך, פרופיל מהיר,
    מנוע בחירה לפי סיפור ותמלול עם דיבור (בלי תמלול אין על מה לבחור קודם).
    """
    s = ctx.settings
    if duration < float(s.long_source_minutes) * 60.0 or s.selection_engine != "intel":
        return False
    if ctx.transcript is None or not ctx.transcript.has_speech:
        return False
    return resolve_profile(s) == "fast"




def _ensure_visual_windows(ctx: JobContext, windows: list[tuple[float, float]]) -> None:
    """
    מצב חלונות: ניתוח חזותי ופריסה מפורטת לחלונות שעוד לא נותחו, ושמירה
    (ניתוח חוזר של אותו פרויקט משתמש במה שכבר נותח).
    """
    from .services.layout_detect import detect_layouts_range, merge_layout_windows
    from .services.visual import analyze_video_windows, merge_windows, subtract_windows

    duration = float(ctx.source_info.get("duration") or (ctx.timeline.duration if ctx.timeline else 0.0))
    windows = merge_windows([(max(0.0, a), min(duration, b)) for a, b in windows])
    have = list(ctx.visual_feats.coverage) if (ctx.visual_feats and ctx.visual_feats.coverage) else []
    todo = subtract_windows(windows, have)
    if not todo or ctx.source_path is None:
        return
    secs = sum(b - a for a, b in todo)
    ctx.reporter.progress(0.40, T("select.visual_windows", n=len(todo)))
    with timing.substage("select.visual_windows", media_seconds=secs, windows=len(todo)):
        ctx.visual_feats = analyze_video_windows(
            ctx.source_path, todo, sample_fps=ctx.settings.visual_sample_fps,
            duration=duration, base=ctx.visual_feats, cancel_event=ctx.cancel_event)
    parts = []
    with timing.substage("select.layout_windows", media_seconds=secs, windows=len(todo)):
        for a, b in todo:
            try:
                parts.append((a, b, detect_layouts_range(
                    ctx.source_path, a, b,
                    src_w=int(ctx.source_info.get("width") or 0),
                    src_h=int(ctx.source_info.get("height") or 0),
                    cancel_event=ctx.cancel_event)))
            except JobCancelledError:
                raise
            except Exception as exc:                  # noqa: BLE001
                log.warning("window layout detection failed: %s", exc)
    ctx.layout_timeline = merge_layout_windows(ctx.layout_timeline, parts, duration)
    manual = (ctx.settings.to_dict().get("camera_region")
              or ctx.artifacts.get("camera_region_manual"))
    if not manual and not ctx.artifacts.get("camera_region") and ctx.visual_feats:
        region = estimate_camera_region(ctx.visual_feats, layouts=ctx.layout_timeline)
        if region:
            ctx.camera_region = region
            ctx.artifacts["camera_region"] = region
    ctx.artifacts.update(analysis_store.save_visual(ctx.work_dir, ctx.visual_feats))
    if ctx.layout_timeline is not None:
        p = analysis_store.save_layouts(ctx.work_dir, ctx.layout_timeline)
        if p:
            ctx.artifacts["layouts_path"] = str(p)
            ctx.artifacts["layout_summary"] = ctx.layout_timeline.summary()


def _save_analysis(ctx: JobContext) -> None:
    """שומר לדיסק את כל מה ששלבי הבחירה והרינדור צריכים."""
    wd = ctx.work_dir
    p = analysis_store.save_audio(wd, ctx.audio_feats)
    if p:
        ctx.artifacts["audio_features_path"] = str(p)
    ctx.artifacts["silences_path"] = str(analysis_store.save_silences(wd, ctx.silences))
    p = analysis_store.save_timeline(wd, ctx.timeline)
    if p:
        ctx.artifacts["timeline_full_path"] = str(p)
    ctx.artifacts.update(analysis_store.save_visual(wd, ctx.visual_feats))
    if ctx.layout_timeline is not None:
        p = analysis_store.save_layouts(wd, ctx.layout_timeline)
        if p:
            ctx.artifacts["layouts_path"] = str(p)
            ctx.artifacts["layout_summary"] = ctx.layout_timeline.summary()


def _art_path(ctx: JobContext, key: str) -> Optional[Path]:
    value = ctx.artifacts.get(key)
    return Path(value) if value else None


def _load_saved_analysis(ctx: JobContext) -> bool:
    """טוען ניתוח שמור. מחזיר False אם חסר משהו חיוני (ציר הזמן)."""
    tl = analysis_store.load_timeline(_art_path(ctx, "timeline_full_path"))
    if tl is None:
        return False
    ctx.timeline = tl
    ctx.audio_feats = analysis_store.load_audio(_art_path(ctx, "audio_features_path"))
    ctx.silences = analysis_store.load_silences(_art_path(ctx, "silences_path")) or []
    ctx.visual_feats = analysis_store.load_visual(_art_path(ctx, "visual_path"),
                                                  _art_path(ctx, "faces_path"))
    ctx.layout_timeline = LayoutTimeline.from_dict(
        analysis_store.load_layouts_data(_art_path(ctx, "layouts_path")))
    if ctx.transcript is None:
        tp = _art_path(ctx, "transcript_path")
        if tp and tp.exists():
            ctx.transcript = _load_transcript(tp)
    if ctx.language is None:
        ctx.language = ctx.artifacts.get("content_language") or None
        if ctx.language is None and ctx.transcript is not None:
            _resolve_language(ctx)
    manual = ctx.settings.to_dict().get("camera_region")
    ctx.camera_region = manual or ctx.artifacts.get("camera_region")
    return True


def _stage_select(ctx: JobContext, *, time_offset: float = 0.0
                  ) -> dict[str, list[selection.Candidate]]:
    ctx.reporter.start_stage(JobStage.SELECT, T("select.start"))
    assert ctx.timeline is not None
    tl = ctx.timeline
    s = ctx.settings
    lang = ctx.language

    boundaries = selection.BoundaryFinder(ctx.transcript, ctx.silences, tl.duration)

    shorts: list[selection.Candidate] = []
    intel = None
    if s.short_enabled and s.short_count > 0:
        if s.selection_engine == "intel":
            intel = _select_intel(ctx, tl, time_offset=time_offset)
        if intel is not None:
            shorts = intel.selected
        else:
            shorts = selection.build_short_candidates(
                tl, transcript=ctx.transcript, boundaries=boundaries, settings=s,
                limit=s.short_count, language=lang)
    ctx.reporter.progress(0.35, T("select.shorts_found", n=len(shorts)))

    longs: list[selection.Candidate] = []
    highlights: Optional[selection.Candidate] = None
    if s.long_enabled and s.long_count > 0:
        if s.long_mode == "highlights":
            pool = shorts or selection.build_short_candidates(
                tl, transcript=ctx.transcript, boundaries=boundaries,
                settings=s, limit=12, language=lang)
            highlights = selection.build_highlights_candidate(
                tl, transcript=ctx.transcript, boundaries=boundaries,
                settings=s, shorts=pool, language=lang)
            if highlights is None:
                ctx.note(T("select.no_highlights"))
                longs = selection.build_long_candidates(
                    tl, transcript=ctx.transcript, boundaries=boundaries,
                    settings=s, limit=s.long_count, language=lang)
        else:
            longs = selection.build_long_candidates(
                tl, transcript=ctx.transcript, boundaries=boundaries,
                settings=s, limit=s.long_count, language=lang)
    ctx.reporter.progress(0.55, T("select.longs_found", n=len(longs)))

    # -- מודל שפה (אופציונלי) --
    all_cands = longs + shorts + ([highlights] if highlights else [])
    if llm.is_llm_enabled(s) and all_cands:
        ctx.reporter.progress(0.62, T("select.llm_titles"))
        outcome = llm.refine_candidates(all_cands, ctx.transcript, s,
                                        cancel_event=ctx.cancel_event)
        if outcome.note:
            ctx.note(outcome.note)

        # במנוע intel הרגעים של מודל השפה כבר נכנסו כהצעות ועברו את אותו רף
        if (intel is None and s.ai_discover_moments and ctx.transcript
                and ctx.transcript.has_speech):
            ctx.reporter.progress(0.78, T("select.llm_discover"))
            extra, disc = llm.discover_moments(
                ctx.transcript, s, max_moments=max(3, s.short_count // 2),
                cancel_event=ctx.cancel_event)
            if disc.note:
                ctx.note(disc.note)
            shorts = _merge_discovered(shorts, extra, tl, ctx, boundaries, s)
    else:
        ctx.note(T("select.heuristic_note"))

    longs, shorts = selection.enforce_total_limit(longs, shorts, s.max_clips_total)
    # במנוע intel אפס קליפים הוא תוצאה לגיטימית (אף רגע לא עבר את רף
    # האיכות) – ההסבר והרגעים שכמעט עברו נמצאים בדוח הבחירה
    if not longs and not shorts and highlights is None and intel is None:
        raise NoMomentsFoundError()

    chosen = longs + shorts + ([highlights] if highlights else [])
    # הפתיחה של כל שורט (הוו) נשמעת שוב במודל החזק גם כשהמעבר המהיר היה בטוח בה
    _proofread(ctx, [span for c in chosen for span in (c.segments or [(c.start, c.end)])],
               priority=[(c.start, min(c.end, c.start + HOOK_RECHECK_SECONDS)) for c in shorts])
    if ctx.artifacts.get("visual_mode") == "windows":
        # הרינדור צריך פנים ופריסה מפורטת לכל מה שנבחר (גם בחירה ישנה/ארוכים)
        spans: list[tuple[float, float]] = []
        for c in longs + shorts + ([highlights] if highlights else []):
            spans += list(c.segments) if c.segments else [(c.start, c.end)]
        _ensure_visual_windows(ctx, [(a - 2.0, b + 2.0) for a, b in spans])
    groups = {"long": longs, "short": shorts,
              "highlights": [highlights] if highlights else []}
    _persist_moments(ctx.job_id, longs + shorts + ([highlights] if highlights else []),
                     time_offset=time_offset)
    ctx.artifacts["candidates_path"] = str(
        analysis_store.save_candidates(ctx.work_dir, groups))
    ctx.reporter.progress(1.0, T("select.chosen",
                                 n=len(longs) + len(shorts) + (1 if highlights else 0)))
    ctx.mark(JobStage.SELECT, media_seconds=tl.duration)
    return groups


# כמה שניות מתחילת כל שורט נבדקות שוב במודל החזק (הוו)
HOOK_RECHECK_SECONDS = 8.0


def _proofread(ctx: JobContext, spans: list[tuple[float, float]], *,
               priority: Sequence[tuple[float, float]] = ()) -> None:
    """
    הגהת התמלול בטווחים שייצאו לקליפים (ראו services/transcript_correct):
    משפטים לא בטוחים מתומללים מחדש במודל החזק, ומתוקנים רק לפי ראיה מהאודיו
    או מאוצר המילים; השאר מסומנים לבדיקה בעורך. קובץ התמלול המקורי לא
    משתנה – התיקונים נשמרים בנפרד, ומשפטים שכבר נבדקו לא נבדקים שוב.
    """
    from .services import transcript_correct as tc

    base = ctx.transcript_original or ctx.transcript
    if base is None or not base.has_speech or not spans:
        return
    ctx.transcript_original = base
    path = ctx.work_dir / "transcript.corrections.json"
    previous = tc.load(path)
    retr = None
    if base.provider == "faster-whisper" and ctx.audio_path is not None \
            and ctx.settings.transcript_provider == "faster-whisper":
        retr = tc.WhisperRetranscriber(ctx.audio_path, ctx.settings,
                                       ctx.language or base.language or None, ctx.cancel_event)
    cloud = None
    if ctx.audio_path is not None and base.provider == "faster-whisper":
        from .services import transcribe_cloud

        if transcribe_cloud.available(ctx.settings):
            cloud = transcribe_cloud.CloudRetranscriber(
                ctx.audio_path, ctx.settings, ctx.language or base.language or None,
                ctx.cancel_event)
    with timing.substage("select.proofread",
                         media_seconds=sum(max(0.0, b - a) for a, b in spans)):
        data = tc.review_transcript(
            base, spans=[(a - 1.0, b + 1.0) for a, b in spans],
            retranscribe=retr if (retr is not None and retr.usable) else None,
            vocabulary=ctx.settings.asr_vocabulary,
            strong_model=retr.model_name if retr is not None else "", previous=previous,
            cloud=cloud, cloud_model=cloud.model_name if cloud is not None else "",
            priority=priority if (retr is not None and retr.usable) else ())
        tc.llm_choose(data, base, ctx.settings, vocabulary=ctx.settings.asr_vocabulary)
    ctx.artifacts["corrections_path"] = str(tc.save(path, data))
    ctx.transcript = tc.apply(base, data)
    _repair_timing(ctx, spans)
    st = data.get("stats") or {}
    checked = sum(st.values())
    if checked:
        ctx.note(i18n.tr("correct.note.summary", checked=checked,
                         corrected=st.get("corrected", 0), confirmed=st.get("confirmed", 0),
                         flagged=st.get("flagged", 0)))
    if data.get("priority_checked") and retr is not None:
        ctx.note(i18n.tr("correct.note.strong_openings", model=retr.model_name, clips=len(priority),
                         checked=data["priority_checked"],
                         seconds=f"{float(data.get('strong_wall_seconds') or 0.0):.1f}"))
    ctx.artifacts["proofread_stats"] = {
        "strong_model": retr.model_name if retr is not None else "",
        "strong_status": (retr.status or "used") if retr is not None else "not_applicable",
        "retranscribed_seconds": data.get("retranscribed_seconds", 0.0),
        "strong_wall_seconds": data.get("strong_wall_seconds", 0.0),
        "priority_checked": data.get("priority_checked", 0),
        "budget_skipped_windows": data.get("budget_skipped_windows", 0),
        **{k: int(v) for k, v in (data.get("stats") or {}).items()}}
    if retr is not None and retr.status == "unavailable":
        ctx.note(i18n.tr("correct.note.strong_unavailable", model=retr.model_name))
    if data.get("budget_skipped_windows"):
        ctx.note(i18n.tr("correct.note.budget", n=data["budget_skipped_windows"]))


def _repair_timing(ctx: JobContext, spans: list[tuple[float, float]]) -> None:
    """
    זמני המילים בקליפים שנבחרו מול האודיו (services/subtitle_align): התחלה
    או סוף בשקט, חפיפות, משך אפס, מילה ארוכה מדי, ומשפטים "מוזים" בשקט.
    הטקסט לא משתנה; התיקונים נשמרים בנפרד ומשפט שנבדק לא נבדק שוב.
    """
    from .services import subtitle_align as sa

    if not ctx.settings.subtitle_timing_repair or ctx.transcript is None \
            or ctx.audio_path is None or not ctx.transcript.has_speech:
        return
    path = ctx.work_dir / "transcript.timing.json"
    audio = ctx.audio_path
    aligner = None
    if ctx.settings.subtitle_forced_alignment and resolve_profile(ctx.settings) == "quality":
        from .services import forced_align

        aligner = forced_align.make_aligner(audio)
        if aligner is None:
            ctx.note(i18n.tr("correct.note.alignment_missing"))
    with timing.substage("select.timing",
                         media_seconds=sum(max(0.0, b - a) for a, b in spans)):
        data = sa.align_transcript(
            ctx.transcript, energy_fn=lambda a, b: sa.energy_for(audio, a, b),
            spans=[(a - 1.0, b + 1.0) for a, b in spans], previous=sa.load(path),
            aligner=aligner)
    ctx.artifacts["timing_path"] = str(sa.save(path, data))
    ctx.transcript = sa.apply(ctx.transcript, data)
    st = data.get("stats") or {}
    if st.get("words_retimed") or st.get("dropped"):
        ctx.note(i18n.tr("correct.note.timing", words=st.get("words_retimed", 0),
                         dropped=st.get("dropped", 0), flagged=st.get("flagged", 0)))


def _select_intel(ctx: JobContext, tl: scoring.Timeline, *, time_offset: float = 0.0):
    """
    בחירת שורטים לפי מבנה סיפור (services/clip_intel). מחזיר None כשאין
    תמלול עם דיבור – ואז הקורא חוזר לבחירה לפי אותות.
    """
    s = ctx.settings
    seeds: list[dict[str, Any]] = []
    if (llm.is_llm_enabled(s) and s.ai_discover_moments and ctx.transcript
            and ctx.transcript.has_speech):
        ctx.reporter.progress(0.1, T("select.llm_discover"))
        seeds, disc = llm.discover_moments(
            ctx.transcript, s, max_moments=max(3, s.short_count // 2),
            cancel_event=ctx.cancel_event)
        if disc.note:
            ctx.note(disc.note)
    with timing.substage("select.clip_intel", media_seconds=tl.duration):
        an = clip_intel.analyze_stories(
            tl, ctx.transcript, settings=s, language=ctx.language,
            extra_seeds=seeds, time_offset=time_offset)
    if an is None:
        ctx.note(i18n.tr("clip_intel.note.no_transcript"))
        return None
    windows = None
    if ctx.artifacts.get("visual_mode") == "windows":
        # סדר העבודה לשידור ארוך: סיפורים (תמלול+אודיו) → חלונות מועמדים →
        # ניתוח חזותי רק שם → דירוג סופי
        _ensure_visual_windows(ctx, an.windows(s.short_count * 2 + 2))
        windows = ctx.visual_feats.coverage if ctx.visual_feats else []
    result = clip_intel.finalize(an, limit=s.short_count, visual=ctx.visual_feats,
                                 visual_windows=windows)
    if windows is not None and ctx.visual_feats is not None:
        # קנס חזותי יכול לקדם מועמד שלא נותח – משלימים ומדרגים שוב
        missing = [(c.start - 2.0, c.end + 2.0) for c in result.selected
                   if not ctx.visual_feats.covers(c.start, c.end)]
        if missing:
            _ensure_visual_windows(ctx, missing)
            result = clip_intel.finalize(an, limit=s.short_count, visual=ctx.visual_feats,
                                         visual_windows=ctx.visual_feats.coverage)
    for n in result.notes:
        ctx.note(n)
    ctx.artifacts["clip_review_path"] = str(
        analysis_store.save_clip_review(ctx.work_dir, result.review))
    return result


def _merge_discovered(shorts: list[selection.Candidate],
                      extra: list[dict[str, Any]], tl: scoring.Timeline,
                      ctx: JobContext, boundaries: selection.BoundaryFinder,
                      s: AppSettings) -> list[selection.Candidate]:
    """מוסיף רגעים שמודל השפה מצא, בלי לייצר כפילויות."""
    if not extra:
        return shorts
    merged = list(shorts)
    for m in extra:
        start = max(0.0, float(m["start"]))
        end = min(tl.duration, float(m["end"]))
        if end - start < 3.0:
            continue
        start = boundaries.snap_start(start)
        end = boundaries.snap_end(end)
        start, end = selection._clamp_duration(
            start, end, float(s.short_min_seconds), float(s.short_max_seconds),
            (start + end) / 2.0, tl.duration)

        cand = selection.Candidate(
            start=round(start, 3), end=round(end, 3),
            peak_time=round((start + end) / 2.0, 3),
            score=float(min(1.0, max(0.05, m.get("interest", 0.5)))),
            kind="short",
            title=m.get("title") or T("select.llm_title_fallback"),
            description=m.get("description", ""),
            reason=m.get("reason") or T("select.llm_reason"),
            category=m.get("category", "moment"),
            signals=tl.channel_breakdown(start, end),
            title_source="llm")
        if any(cand.overlaps(c) > 0.35 for c in merged):
            continue
        merged.append(cand)
    merged.sort(key=lambda c: c.score, reverse=True)
    return merged[: s.short_count]


# --------------------------------------------------------------------------
# ייצוא
# --------------------------------------------------------------------------
def _stage_render(ctx: JobContext,
                  groups: dict[str, list[selection.Candidate]]) -> None:
    s = ctx.settings
    longs = groups.get("long", []) + groups.get("highlights", [])
    shorts = groups.get("short", [])

    if longs and s.long_enabled and not ctx.done(JobStage.RENDER_LONG):
        # קליפים ארוכים חלקיים מריצה קודמת נמחקים – הם יירנדרו מחדש
        _clear_results(ctx.job_id, moments=False,
                       clip_kinds=[ClipKind.LONG, ClipKind.HIGHLIGHTS])
        ctx.reporter.start_stage(JobStage.RENDER_LONG, T("render.longs", n=len(longs)))
        clip_factory.render_group(ctx, longs, short=False)
        ctx.mark(JobStage.RENDER_LONG, media_seconds=sum(c.duration for c in longs))

    if shorts and s.short_enabled and not ctx.done(JobStage.RENDER_SHORT):
        _clear_results(ctx.job_id, moments=False, clip_kinds=[ClipKind.SHORT])
        ctx.reporter.start_stage(JobStage.RENDER_SHORT, T("render.shorts", n=len(shorts)))
        clip_factory.render_group(ctx, shorts, short=True)
        ctx.mark(JobStage.RENDER_SHORT, media_seconds=sum(c.duration for c in shorts))


def _clear_results(job_id: str, *, moments: bool,
                   clip_kinds: Optional[list[ClipKind]]) -> int:
    """
    מוחק תוצאות קודמות של המשימה: רגעים, וקליפים (כל הסוגים כש-
    `clip_kinds` הוא None) כולל הקבצים שלהם. מחזיר כמה קליפים נמחקו.
    """
    removed = 0
    with session_scope() as s:
        q = s.query(Clip).filter(Clip.job_id == job_id)
        if clip_kinds is not None:
            q = q.filter(Clip.kind.in_(clip_kinds))
        clips = q.all()
        for clip in clips:
            for p in (clip.file_path, clip.thumbnail_path):
                if p:
                    try:
                        Path(p).unlink(missing_ok=True)
                    except OSError:
                        pass
                    if p.endswith(".mp4"):
                        try:
                            Path(p).with_suffix(".srt").unlink(missing_ok=True)
                        except OSError:
                            pass
            s.query(SubtitleCue).filter(SubtitleCue.clip_id == clip.id).delete()
            s.query(ImagePlacement).filter(ImagePlacement.clip_id == clip.id).delete()
            s.delete(clip)
            removed += 1
        if moments:
            s.query(Moment).filter(Moment.job_id == job_id).delete()
    return removed


# --------------------------------------------------------------------------
# סיכום הניתוח לפרויקט
# --------------------------------------------------------------------------
def _store_analysis_summary(ctx: JobContext) -> None:
    from .services.project_analysis import build_analysis

    summary = build_analysis(ctx)
    with session_scope() as s:
        job = s.get(Job, ctx.job_id)
        if job is not None:
            job.analysis = summary
    BUS.emit("project.updated", ctx.job_id, project_id=ctx.job_id,
             phase=ProjectPhase.ANALYZING.value, status="running", analysis=True)


# --------------------------------------------------------------------------
# שמירה ושחזור
# --------------------------------------------------------------------------
def _save_transcript(result: TranscriptResult, path: Path) -> None:
    data = {
        "language": result.language, "duration": result.duration,
        "provider": result.provider, "model": result.model, "note": result.note,
        "meta": result.meta,
        "segments": [
            {"start": s.start, "end": s.end, "text": s.text,
             "language": s.language, "avg_logprob": s.avg_logprob,
             "no_speech_prob": s.no_speech_prob,
             "words": [w.to_dict() for w in s.words]}
            for s in result.segments
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _load_transcript(path: Path) -> Optional[TranscriptResult]:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    segs = [
        Segment(
            start=float(s["start"]), end=float(s["end"]), text=s.get("text", ""),
            language=s.get("language", ""),
            avg_logprob=float(s.get("avg_logprob", 0.0)),
            no_speech_prob=float(s.get("no_speech_prob", 0.0)),
            words=[Word(start=float(w["start"]), end=float(w["end"]),
                        text=w.get("text", ""), probability=float(w.get("p", 1.0)),
                        flag=w.get("flag", ""), asr=w.get("asr", ""))
                   for w in s.get("words", [])])
        for s in data.get("segments", [])
    ]
    return TranscriptResult(
        segments=segs, language=data.get("language", ""),
        duration=float(data.get("duration", 0.0)),
        provider=data.get("provider", ""), model=data.get("model", ""),
        note=data.get("note", ""), meta=dict(data.get("meta") or {}))


def _persist_segments(job_id: str, result: TranscriptResult) -> None:
    with session_scope() as s:
        s.query(TranscriptSegment).filter(TranscriptSegment.job_id == job_id).delete()
        for i, seg in enumerate(result.segments):
            s.add(TranscriptSegment(
                job_id=job_id, idx=i, start=seg.start, end=seg.end, text=seg.text,
                language=seg.language or result.language,
                avg_logprob=seg.avg_logprob, no_speech_prob=seg.no_speech_prob,
                words=[w.to_dict() for w in seg.words]))


def _persist_moments(job_id: str, cands: list[selection.Candidate],
                     *, time_offset: float = 0.0) -> None:
    with session_scope() as s:
        for c in cands:
            s.add(Moment(
                id=new_id(), job_id=job_id,
                start=c.start + time_offset, end=c.end + time_offset,
                peak_time=c.peak_time + time_offset, score=c.score,
                title=c.title, description=c.description, reason=c.reason,
                category=c.category, signals=c.signals,
                source_of_truth=c.title_source))


def _save_timeline_preview(ctx: JobContext) -> None:
    """
    שומר גרסה מוקטנת של ציר הציונים לתצוגה בממשק (עד 1200 נקודות).
    כך אפשר להציג למשתמש איפה המערכת מצאה עניין.
    """
    tl = ctx.timeline
    if tl is None or tl.n == 0:
        return
    target = min(1200, tl.n)
    idx = np.linspace(0, tl.n - 1, target).astype(int)
    preview = {
        "hop": tl.hop, "duration": tl.duration,
        "times": [round(float(tl.times[i]), 2) for i in idx],
        "score": [round(float(tl.score[i]), 4) for i in idx],
        "vocal": [round(float(tl.vocal[i]), 3) for i in idx],
        "speech": [round(float(tl.speech[i]), 3) for i in idx],
        "visual": [round(float(tl.visual[i]), 3) for i in idx],
        "pause": [round(float(tl.pause[i]), 3) for i in idx],
    }
    path = ctx.work_dir / "timeline.json"
    path.write_text(json.dumps(preview), encoding="utf-8")
    ctx.artifacts["timeline_path"] = str(path)


def load_visual(job: Job) -> Optional[VisualFeatures]:
    """טוען נתונים חזותיים שנשמרו, לשימוש בייצוא מחדש."""
    arts = job.artifacts or {}
    return analysis_store.load_visual(
        Path(arts["visual_path"]) if arts.get("visual_path") else None,
        Path(arts["faces_path"]) if arts.get("faces_path") else None)


def load_layouts(job: Job) -> Optional[LayoutTimeline]:
    """ציר הפריסות שנשמר בניתוח, או None."""
    path = (job.artifacts or {}).get("layouts_path")
    return LayoutTimeline.from_dict(
        analysis_store.load_layouts_data(Path(path) if path else None))


def load_transcript_for_job(job: Job) -> Optional[TranscriptResult]:
    """התמלול האפקטיבי: המקור, עם תיקוני ההגהה שנשמרו (אם יש)."""
    from .services import transcript_correct as tc

    path = (job.artifacts or {}).get("transcript_path")
    if not path or not Path(path).exists():
        return None
    tr = _load_transcript(Path(path))
    corr = (job.artifacts or {}).get("corrections_path")
    if tr is not None and corr:
        tr = tc.apply(tr, tc.load(Path(corr)))
    tim = (job.artifacts or {}).get("timing_path")
    if tr is not None and tim:
        from .services import subtitle_align as sa

        tr = sa.apply(tr, sa.load(Path(tim)))
    return tr


def ensure_visual_for_range(job: Job, start: float, end: float) -> None:
    """
    מצב חלונות (מקור ארוך, פרופיל מהיר): ייצוא מחדש עם גבולות שחורגים
    מהחלון שנותח – מנתחים רק את החלק החסר, ושומרים (כמו ביצירה).
    """
    arts = dict(job.artifacts or {})
    if arts.get("visual_mode") != "windows":
        return
    vf = load_visual(job)
    if vf is not None and vf.covers(start, end):
        return
    settings = settings_for_job(job)
    ctx = JobContext(job_id=job.id, settings=settings,
                     reporter=_SilentReporter(), cancel_event=threading.Event(),
                     artifacts=arts, completed=set(job.completed_stages or []),
                     input_url=job.input_url or "")
    ctx.source_path = Path(arts.get("source_path") or "")
    ctx.source_info = dict(arts.get("source_info") or {})
    ctx.visual_feats = vf
    ctx.layout_timeline = load_layouts(job)
    _ensure_visual_windows(ctx, [(start - 2.0, end + 2.0)])
    job.artifacts = {**(job.artifacts or {}), **{k: ctx.artifacts[k] for k in
                                                 ("visual_path", "faces_path", "layouts_path",
                                                  "layout_summary", "camera_region")
                                                 if k in ctx.artifacts}}


class _SilentReporter:
    """מדווח ריק – לעבודה קטנה מחוץ למשימה (ייצוא מחדש)."""

    def progress(self, *a: Any, **k: Any) -> None:
        return None

    def log(self, *a: Any, **k: Any) -> None:
        return None


def _finalize_notes(ctx: JobContext) -> None:
    if not ctx.notes:
        return
    with session_scope() as s:
        job = s.get(Job, ctx.job_id)
        if job is not None:
            arts = dict(job.artifacts or {})
            arts["notes"] = ctx.notes
            job.artifacts = arts


def extract_source_thumbnail(job: Job, dst: Path) -> Optional[Path]:
    """תמונה ממוזערת מקובץ המקור המקומי (לא מכתובת מרוחקת)."""
    src = (job.artifacts or {}).get("source_path")
    if not src or not Path(src).exists():
        return None
    info = (job.artifacts or {}).get("source_info") or {}
    duration = float(info.get("duration") or 0.0)
    at = min(max(1.0, duration * 0.1), max(0.0, duration - 0.5)) if duration else 1.0
    return extract_thumbnail(Path(src), dst, at_seconds=at, width=640)


# --------------------------------------------------------------------------
# חיבור למנהל המשימות
# --------------------------------------------------------------------------
MANAGER.set_runner(run_job)


def resume_interrupted_jobs() -> int:
    """
    בעליית השרת:
      * משימות שהיו RUNNING בעת סגירה מסומנות ככשלות עם אפשרות חידוש
        (הן לא ימשיכו מעצמן, כדי לא להפתיע את המשתמש).
      * משימות שהיו QUEUED – כלומר עוד לא התחילו – מוגשות מחדש לתור.
        בלי זה הן היו נשארות „ממתינות בתור" לנצח.
    מחזיר את מספר המשימות שנקטעו.
    """
    from .errors import JobInterruptedError

    count = 0
    queued: list[str] = []
    with session_scope() as s:
        for job in s.query(Job).filter(Job.status == JobStatus.RUNNING).all():
            err = JobInterruptedError()
            job.status = JobStatus.FAILED
            with i18n.use_lang(job.ui_language or i18n.DEFAULT_LANG):
                job.error = err.message
                job.message = T("status.interrupted")
            job.error_code = err.code
            job.error_data = err.to_record()
            if job.phase:
                job.phase = ProjectPhase.FAILED.value
            count += 1
        queued = [j.id for j in s.query(Job).filter(Job.status == JobStatus.QUEUED).all()]
    for job_id in queued:
        MANAGER.submit(job_id)
    if queued:
        log.info("resubmitted %d queued jobs", len(queued))
    return count


def load_timeline_for_job(job: Job) -> Optional[scoring.Timeline]:
    """
    ציר הזמן של המשימה: המלא (אם נשמר), ואחרת הגרסה המוקטנת.

    הגרסה המוקטנת מספיקה לעריכה: היא נדרשת כדי לזהות אילו שתיקות
    דרמטיות, וזו שאלה ברזולוציה של שניות.
    """
    full = (job.artifacts or {}).get("timeline_full_path")
    if full:
        tl = analysis_store.load_timeline(Path(full))
        if tl is not None:
            return tl
    path = (job.artifacts or {}).get("timeline_path")
    if not path or not Path(path).exists():
        return None
    try:
        data = json.loads(Path(path).read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    times = data.get("times") or []
    if len(times) < 2:
        return None

    tl = scoring.Timeline(
        hop=float(times[1] - times[0]),
        duration=float(data.get("duration") or times[-1]),
        times=np.asarray(times, dtype=np.float32))
    for name in ("score", "vocal", "speech", "visual", "pause"):
        values = data.get(name)
        if values:
            setattr(tl, name, np.asarray(values, dtype=np.float32))
    return tl


def load_analysis_for_job(job: Job) -> dict[str, Any]:
    """
    טוען מחדש את נתוני הניתוח ששמורים על הדיסק, לשימוש בייצוא מחדש.

    באג שתוקן: אותות האודיו וקטעי השקט לא נשמרו, והפונקציה החזירה
    `silences=None`. עכשיו הכול נשמר בשלב הניתוח ונטען כאן.
    """
    arts = job.artifacts or {}
    silences = analysis_store.load_silences(
        Path(arts["silences_path"]) if arts.get("silences_path") else None)
    return {
        "transcript": load_transcript_for_job(job),
        "timeline": load_timeline_for_job(job),
        "silences": silences,
        "audio": analysis_store.load_audio(
            Path(arts["audio_features_path"]) if arts.get("audio_features_path") else None),
        "visual": load_visual(job),
        "layouts": load_layouts(job),
        "language": arts.get("content_language"),
    }


# תאימות לאחור: שמות פונקציות שעברו ל-clip_factory
_render_group = clip_factory.render_group
_qa_clip = clip_factory.qa_clip
_master_clip_audio = clip_factory.master_clip_audio
_direct_segments = clip_factory.direct_segments
_build_cues_for = clip_factory.build_cues_for
_clip_basename = clip_factory.clip_basename
_create_clip_row = clip_factory.create_clip_row
_update_clip = clip_factory.update_clip
_merge_render_params = clip_factory.merge_render_params


def _integrated_loudness(path: Optional[Path]) -> Optional[float]:
    if path is None:
        return None
    try:
        return integrated_loudness(path)
    except Exception:                                  # noqa: BLE001
        return None


def remove_work_files(job_id: str) -> None:
    rmtree_quiet(PATHS.work / job_id)
