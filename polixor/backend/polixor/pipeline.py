"""
תזמור הפייפליין המלא: הורדה → אודיו → תמלול → ניתוח → בחירה → ייצוא.

כל שלב שומר צ'קפוינט ב-DB (`completed_stages` + `artifacts`), ולכן
משימה שנכשלה או בוטלה יכולה להתחדש מהנקודה האחרונה במקום מההתחלה.
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np

from .config import PATHS, AppSettings, SETTINGS
from .db import session_scope
from .errors import (
    JobCancelledError,
    NoAudioError,
    NoMomentsFoundError,
    PolixorError,
    SourceTooShortError,
)
from .events import BUS
from .models import (
    LIVE_STATE_LABELS_HE,
    Clip,
    ClipKind,
    ClipStatus,
    Job,
    JobStage,
    JobStatus,
    LiveState,
    Moment,
    Source,
    SourceKind,
    TranscriptSegment,
    new_id,
)
from .services import (
    audio_mastering, caption_engine, director_bridge, editing, ingest, llm,
    music_engine, pacing_engine, render, reframe, render_qa, scoring,
    selection, semantics, subtitles, video_director,
)
from .services import live as live_svc
from .services import live_capture
from .services.audio import AudioFeatures, analyze_audio
from .services.transcribe import Segment, TranscriptResult, Word, transcribe_audio
from .services.visual import VisualFeatures, analyze_video, estimate_camera_region
from .util.ffmpeg import (
    extract_audio_wav, ffmpeg_bin, probe, silence_intervals,
)
from .util.fs import safe_filename, unique_path
from .util.text import format_duration_he, truncate
from .worker import MANAGER, ProgressReporter

log = logging.getLogger("polixor.pipeline")


# --------------------------------------------------------------------------
# נקודת הכניסה
# --------------------------------------------------------------------------
def run_job(job_id: str, cancel_event: threading.Event) -> None:
    """מריץ משימה מלאה. נקרא מתוך תהליכון של JobManager."""
    with session_scope() as s:
        job = s.get(Job, job_id)
        if job is None:
            raise PolixorError("המשימה לא נמצאה.")
        settings = AppSettings.from_dict(job.settings_snapshot or {})
        is_live = bool(job.is_live_mode)
        input_url = job.input_url
        completed = set(job.completed_stages or [])
        artifacts = dict(job.artifacts or {})

    planned = _plan_stages(settings,
                           has_local_source=bool(artifacts.get("source_path")),
                           is_live=is_live)
    reporter = ProgressReporter(job_id, planned, cancel_event)

    ctx = JobContext(job_id=job_id, settings=settings, reporter=reporter,
                     cancel_event=cancel_event, artifacts=artifacts,
                     completed=completed, input_url=input_url)

    if is_live:
        _run_live(ctx)
    else:
        _run_once(ctx)


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

        self.source_path: Optional[Path] = None
        self.source_info: dict[str, Any] = {}
        self.audio_path: Optional[Path] = None
        self.transcript: Optional[TranscriptResult] = None
        self.audio_feats: Optional[AudioFeatures] = None
        self.visual_feats: Optional[VisualFeatures] = None
        self.timeline: Optional[scoring.Timeline] = None
        self.silences: list[tuple[float, float]] = []
        self.camera_region: Optional[dict[str, float]] = None
        self.notes: list[str] = []

    def done(self, stage: JobStage) -> bool:
        return stage.value in self.completed

    def mark(self, stage: JobStage, media_seconds: float = 0.0) -> None:
        self.completed.add(stage.value)
        self.reporter.finish_stage(stage, media_seconds=media_seconds)
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
                 is_live: bool = False) -> list[JobStage]:
    if is_live and not has_local_source:
        stages = [JobStage.CAPTURE]
    else:
        stages = [] if has_local_source else [JobStage.DOWNLOAD]
    stages += [JobStage.PROBE, JobStage.AUDIO]
    if settings.transcript_provider != "none":
        stages.append(JobStage.TRANSCRIBE)
    stages += [JobStage.ANALYZE, JobStage.SELECT]
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


# --------------------------------------------------------------------------
# ריצה רגילה (VOD / קובץ)
# --------------------------------------------------------------------------
def _run_once(ctx: JobContext) -> None:
    _stage_download(ctx)
    _stage_probe(ctx)
    _stage_audio(ctx)
    _stage_transcribe(ctx)
    _stage_analyze(ctx)
    candidates = _stage_select(ctx)
    _stage_render(ctx, candidates)
    _finalize_notes(ctx)


# --------------------------------------------------------------------------
# מצב שידור חי: קליטה → מקור → אותו פייפליין בדיוק
# --------------------------------------------------------------------------
def _run_live(ctx: JobContext) -> None:
    """
    קולט את השידור להקלטה מקומית, ואז מריץ עליה את הפייפליין הרגיל.

    אין כאן מנוע עריכה נפרד ללייב: ההקלטה הופכת ל-Source ועוברת
    בדיוק את אותם שלבים כמו קובץ שהועלה או VOD שהורד.
    """
    _stage_live_capture(ctx)
    _stage_probe(ctx)
    _stage_audio(ctx)
    _stage_transcribe(ctx)
    _stage_analyze(ctx)
    candidates = _stage_select(ctx)
    _stage_render(ctx, candidates)
    _finalize_notes(ctx)


def _stage_live_capture(ctx: JobContext) -> None:
    """
    מקליט את השידור עד שהמשתמש לוחץ 'עצור הקלטה' או שהשידור מסתיים.

    כל מקטע שנסגר נרשם מיד ב-DB, ולכן נפילה של הזרם או של השרת
    אינה מאבדת את מה שכבר הוקלט.
    """
    existing = ctx.artifacts.get("source_path")
    if existing and Path(existing).exists():
        ctx.source_path = Path(existing)
        ctx.reporter.finish_stage(JobStage.CAPTURE)
        return

    ctx.reporter.start_stage(JobStage.CAPTURE, "מזהה את השידור…")
    _set_live_state(ctx.job_id, LiveState.DETECTING.value, "מזהה שידור…")

    info = live_svc.detect_stream(ctx.input_url, ctx.settings)
    if not info.available:
        _set_live_state(ctx.job_id, LiveState.FAILED.value, info.reason)
        raise live_svc.LiveUnavailableError(
            info.reason or "השידור אינו זמין.",
            hint="ודא שהשידור באוויר ושהקישור ציבורי.")

    ctx.note(f"שידור זוהה: {info.platform}"
             + (f" · {info.title}" if info.title else "")
             + (f" · {info.resolution_label}" if info.resolution_label else ""))
    if not info.has_audio:
        ctx.note("לא זוהה ערוץ אודיו בזרם. הניתוח יתבסס על הווידאו בלבד.")

    stop_event = LIVE_STOPS.setdefault(ctx.job_id, threading.Event())
    machine = live_capture.StateMachine(
        lambda state, detail: _set_live_state(ctx.job_id, state, detail))

    segments_seen: list[dict[str, Any]] = []
    last_tick = {"t": 0.0}

    def on_segment(result) -> None:
        segments_seen.append({
            "path": str(result.path), "seconds": result.seconds,
            "complete": result.complete,
        })
        _persist_live_segments(ctx.job_id, segments_seen)
        ctx.reporter.log(
            f"נשמר מקטע {len(segments_seen)} · "
            f"{format_duration_he(result.seconds)}", level="info")

    def on_tick(total: float, _in_segment: float) -> None:
        now = time.time()
        if now - last_tick["t"] < 1.0:
            return
        last_tick["t"] = now
        _publish_live_tick(ctx.job_id, total)
        # אין יעד זמן ידוע לשידור חי, ולכן אין אחוז התקדמות אמיתי:
        # מדווחים את משך ההקלטה בפועל במקום מד התקדמות מדומה.
        ctx.reporter.progress(
            0.0, f"מקליט · {format_duration_he(total)}")

    outcome = live_capture.run_capture(
        live_capture.CaptureConfig(
            url=info.url, work_dir=PATHS.capture_dir(ctx.job_id),
            segment_seconds=float(ctx.settings.live_segment_seconds),
            max_seconds=float(ctx.settings.live_max_minutes) * 60.0,
        ),
        settings=ctx.settings,
        cancel_event=ctx.cancel_event,
        stop_event=stop_event,
        machine=machine,
        on_segment=on_segment,
        on_tick=on_tick,
    )

    LIVE_STOPS.pop(ctx.job_id, None)

    if not outcome.segments:
        _set_live_state(ctx.job_id, LiveState.FAILED.value,
                        outcome.error or "לא נאסף חומר.")
        raise live_svc.LiveUnavailableError(
            outcome.error or "לא נאסף חומר מההקלטה.",
            hint="ייתכן שהשידור הסתיים לפני שהצטבר חומר.")

    ctx.reporter.progress(0.9, "מאחד את ההקלטה לקובץ אחד…")
    dest = PATHS.sources / f"live_{ctx.job_id}.mp4"
    try:
        final = live_svc.concat_segments(outcome.paths, dest)
    except PolixorError:
        # האיחוד נכשל – אבל המקטעים עדיין על הדיסק, וזה נאמר במפורש
        ctx.note(f"איחוד ההקלטה נכשל. {len(outcome.segments)} מקטעים "
                 f"נשמרו בתיקייה {PATHS.capture_dir(ctx.job_id)}")
        raise

    media = probe(final)
    ctx.source_path = final
    ctx.artifacts["source_path"] = str(final)
    ctx.artifacts["live_capture"] = {
        "segments": len(outcome.segments),
        "reconnects": outcome.reconnects,
        "seconds": outcome.seconds,
        "stopped_by_user": outcome.stopped_by_user,
        "platform": info.platform,
        "title": info.title,
    }

    with session_scope() as s:
        job = s.get(Job, ctx.job_id)
        source = Source(
            id=new_id(),
            kind=_source_kind_for(info.kind),
            url=info.url,
            title=info.title or f"שידור חי · {info.platform}",
            uploader=info.uploader,
            file_path=str(final),
            file_size=final.stat().st_size,
            duration=media.duration,
            width=media.width, height=media.height, fps=media.fps,
            has_audio=media.has_audio, is_live=True,
            extra={"captured": True, "segments": len(outcome.segments),
                   "reconnects": outcome.reconnects},
        )
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
        ctx.note(f"הזרם נפל {outcome.reconnects} פעמים במהלך ההקלטה; "
                 "החומר שהוקלט לפני כל נפילה נשמר.")
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
    BUS.emit("live.state", job_id=job_id, state=state, detail=detail,
             label=LIVE_STATE_LABELS_HE.get(state, state))


def _persist_live_segments(job_id: str, segments: list[dict[str, Any]]) -> None:
    total = round(sum(float(s.get("seconds") or 0.0) for s in segments), 2)
    with session_scope() as s:
        job = s.get(Job, job_id)
        if job is None:
            return
        job.live_segments = list(segments)
        job.live_captured_seconds = total
        job.live_cycles = len(segments)
    BUS.emit("live.segment", job_id=job_id, segments=len(segments),
             seconds=total)


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

    ctx.reporter.start_stage(JobStage.DOWNLOAD, "מוריד את הווידאו…")
    t0 = time.time()

    result = ingest.download(
        ctx.input_url,
        settings=ctx.settings,
        dest_dir=PATHS.sources,
        on_progress=lambda frac, msg: ctx.reporter.progress(frac, msg),
        cancel_event=ctx.cancel_event,
        live_duration=live_window,
    )

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
            fps=float(result.extra.get("fps") or 0.0),
            extra=result.extra,
        )
        s.add(src)
        s.flush()
        job.source_id = src.id
        if not job.title:
            job.title = result.title or result.path.stem

    ctx.reporter.log(
        f"הורד: {result.title or result.path.name} "
        f"({format_duration_he(result.duration)})", level="info")
    ctx.mark(JobStage.DOWNLOAD, media_seconds=result.duration)
    log.info("download finished in %.1fs", time.time() - t0)


def _stage_probe(ctx: JobContext) -> None:
    ctx.reporter.start_stage(JobStage.PROBE, "בודק את קובץ הווידאו…")
    if ctx.source_path is None:
        sp = ctx.artifacts.get("source_path")
        if not sp:
            raise PolixorError("אין קובץ מקור למשימה.")
        ctx.source_path = Path(sp)

    info = probe(ctx.source_path)
    ctx.source_info = {
        "duration": info.duration, "width": info.width, "height": info.height,
        "fps": info.fps, "has_audio": info.has_audio, "has_video": info.has_video,
        "size": info.size_bytes,
    }
    ctx.artifacts["source_info"] = ctx.source_info

    if not info.has_video:
        raise PolixorError("הקובץ אינו מכיל וידאו.")
    if info.duration < 5.0:
        raise SourceTooShortError(
            f"אורך הווידאו {info.duration:.1f} שניות – קצר מדי לניתוח.")
    if not info.has_audio:
        ctx.note("לא נמצא ערוץ אודיו: הניתוח יתבסס על וידאו בלבד, "
                 "ולא ייווצרו תמלול או כתוביות.")

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
    ctx.reporter.start_stage(JobStage.AUDIO, "מחלץ אודיו…")
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
                cancel_event=ctx.cancel_event,
            )
        except JobCancelledError:
            raise
        except PolixorError as exc:
            ctx.note(f"חילוץ האודיו נכשל ({exc.message}). ממשיך עם וידאו בלבד.")
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
            ctx.note("אין אודיו לתמלול.")
        return

    cached = ctx.artifacts.get("transcript_path")
    if cached and Path(cached).exists():
        ctx.transcript = _load_transcript(Path(cached))
        if ctx.transcript is not None:
            return

    ctx.reporter.start_stage(JobStage.TRANSCRIBE, "מתמלל את הדיבור…")
    result = transcribe_audio(
        ctx.audio_path,
        settings=ctx.settings,
        on_progress=lambda f, msg: ctx.reporter.progress(f, msg),
        cancel_event=ctx.cancel_event,
        media_duration=float(ctx.source_info.get("duration") or 0.0),
        allow_fallback=True,
    )
    ctx.transcript = result
    if result.note:
        ctx.note(result.note)

    path = ctx.work_dir / "transcript.json"
    _save_transcript(result, path)
    ctx.artifacts["transcript_path"] = str(path)
    _persist_segments(ctx.job_id, result)

    ctx.reporter.log(
        f"תמלול: {len(result.segments)} מקטעים, שפה: {result.language or 'לא זוהתה'}",
        level="info")
    ctx.mark(JobStage.TRANSCRIBE,
             media_seconds=float(ctx.source_info.get("duration") or 0.0))


def _stage_analyze(ctx: JobContext) -> None:
    ctx.reporter.start_stage(JobStage.ANALYZE, "מנתח אודיו ווידאו…")
    duration = float(ctx.source_info.get("duration") or 0.0)

    # -- אודיו --
    if ctx.audio_path is not None:
        ctx.audio_feats = analyze_audio(
            ctx.audio_path,
            on_progress=lambda f: ctx.reporter.progress(f * 0.35,
                                                        "מנתח את פס הקול…"),
            cancel_event=ctx.cancel_event,
        )
        try:
            ctx.silences = silence_intervals(ctx.audio_path,
                                             cancel_event=ctx.cancel_event)
        except Exception as exc:  # noqa: BLE001
            log.warning("silencedetect failed: %s", exc)
            ctx.silences = []
    ctx.reporter.progress(0.40, "מנתח את הווידאו…")

    # -- וידאו --
    ctx.visual_feats = analyze_video(
        ctx.source_path,
        sample_fps=ctx.settings.visual_sample_fps,
        duration=duration,
        detect_faces=ctx.settings.short_enabled,
        on_progress=lambda f: ctx.reporter.progress(0.40 + f * 0.55,
                                                    "מנתח פריימים…"),
        cancel_event=ctx.cancel_event,
    )
    if ctx.visual_feats and not ctx.visual_feats.analyzed and ctx.visual_feats.note:
        ctx.note(ctx.visual_feats.note)

    # -- אזור מצלמה --
    manual = (ctx.settings.to_dict().get("camera_region")
              or ctx.artifacts.get("camera_region"))
    if manual:
        ctx.camera_region = manual
    elif ctx.visual_feats:
        ctx.camera_region = estimate_camera_region(ctx.visual_feats)
        if ctx.camera_region:
            ctx.artifacts["camera_region"] = ctx.camera_region
            ctx.note(f"זוהה אזור מצלמת סטרימר "
                     f"(ביטחון {ctx.camera_region.get('confidence', 0):.0%}). "
                     "ניתן לתקן ידנית במסך העריכה.")

    # -- שמירת נתוני פנים לשימוש חוזר בעריכה/ייצוא מחדש --
    _save_visual(ctx)

    # -- ציר זמן משולב --
    ctx.reporter.progress(0.97, "משלב אותות…")
    ctx.timeline = scoring.build_timeline(
        audio=ctx.audio_feats, visual=ctx.visual_feats, transcript=ctx.transcript,
        duration=duration, settings=ctx.settings,
    )
    _save_timeline_preview(ctx)
    ctx.mark(JobStage.ANALYZE, media_seconds=duration)


def _stage_select(ctx: JobContext, *, time_offset: float = 0.0
                  ) -> dict[str, list[selection.Candidate]]:
    ctx.reporter.start_stage(JobStage.SELECT, "בוחר את הרגעים המעניינים…")
    assert ctx.timeline is not None
    tl = ctx.timeline
    s = ctx.settings

    boundaries = selection.BoundaryFinder(ctx.transcript, ctx.silences, tl.duration)

    shorts = selection.build_short_candidates(
        tl, transcript=ctx.transcript, boundaries=boundaries, settings=s,
        limit=s.short_count if s.short_enabled else 0,
    ) if s.short_enabled else []
    ctx.reporter.progress(0.35, f"נמצאו {len(shorts)} מועמדים לשורטים")

    longs: list[selection.Candidate] = []
    highlights: Optional[selection.Candidate] = None
    if s.long_enabled and s.long_count > 0:
        if s.long_mode == "highlights":
            pool = shorts or selection.build_short_candidates(
                tl, transcript=ctx.transcript, boundaries=boundaries,
                settings=s, limit=12)
            highlights = selection.build_highlights_candidate(
                tl, transcript=ctx.transcript, boundaries=boundaries,
                settings=s, shorts=pool)
            if highlights is None:
                ctx.note("לא נמצאו מספיק רגעים לסרטון Highlights; "
                         "נוצר קליפ ארוך רציף במקום.")
                longs = selection.build_long_candidates(
                    tl, transcript=ctx.transcript, boundaries=boundaries,
                    settings=s, limit=s.long_count)
        else:
            longs = selection.build_long_candidates(
                tl, transcript=ctx.transcript, boundaries=boundaries,
                settings=s, limit=s.long_count)
    ctx.reporter.progress(0.55, f"נמצאו {len(longs)} מועמדים לקליפים ארוכים")

    # -- מודל שפה (אופציונלי) --
    all_cands = longs + shorts + ([highlights] if highlights else [])
    if llm.is_llm_enabled(s) and all_cands:
        ctx.reporter.progress(0.62, "משפר כותרות עם מודל שפה…")
        outcome = llm.refine_candidates(all_cands, ctx.transcript, s,
                                        cancel_event=ctx.cancel_event)
        if outcome.note:
            ctx.note(outcome.note)

        if s.ai_discover_moments and ctx.transcript and ctx.transcript.has_speech:
            ctx.reporter.progress(0.78, "מחפש רגעים שקטים שהאותות פספסו…")
            extra, disc = llm.discover_moments(
                ctx.transcript, s, max_moments=max(3, s.short_count // 2),
                cancel_event=ctx.cancel_event)
            if disc.note:
                ctx.note(disc.note)
            shorts = _merge_discovered(shorts, extra, tl, ctx, boundaries, s)
    else:
        ctx.note("מצב AI מקומי (היוריסטי): הכותרות והתיאורים נגזרים מהתמלול "
                 "ומהאותות, ללא מודל שפה. איכות הניסוח נמוכה יותר ממצב ענן.")

    longs, shorts = selection.enforce_total_limit(longs, shorts, s.max_clips_total)
    if not longs and not shorts and highlights is None:
        raise NoMomentsFoundError()

    _persist_moments(ctx.job_id, longs + shorts + ([highlights] if highlights else []),
                     time_offset=time_offset)
    ctx.reporter.progress(1.0, f"נבחרו {len(longs) + len(shorts) + (1 if highlights else 0)} קטעים")
    ctx.mark(JobStage.SELECT, media_seconds=tl.duration)

    return {"long": longs, "short": shorts,
            "highlights": [highlights] if highlights else []}


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
            title=m.get("title") or "רגע מהשידור",
            description=m.get("description", ""),
            reason=m.get("reason") or "אותר על-ידי מודל שפה מתוך התמלול.",
            category=m.get("category", "moment"),
            signals=tl.channel_breakdown(start, end),
            title_source="llm",
        )
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

    if longs and s.long_enabled:
        ctx.reporter.start_stage(JobStage.RENDER_LONG,
                                 f"מייצא {len(longs)} קליפים ארוכים…")
        _render_group(ctx, longs, vertical=False)
        ctx.mark(JobStage.RENDER_LONG,
                 media_seconds=sum(c.duration for c in longs))

    if shorts and s.short_enabled:
        ctx.reporter.start_stage(JobStage.RENDER_SHORT,
                                 f"מייצא {len(shorts)} שורטים…")
        _render_group(ctx, shorts, vertical=True)
        ctx.mark(JobStage.RENDER_SHORT,
                 media_seconds=sum(c.duration for c in shorts))


def _render_group(ctx: JobContext, cands: list[selection.Candidate],
                  *, vertical: bool) -> None:
    total = len(cands)
    for i, cand in enumerate(cands):
        ctx.reporter.check_cancel()
        base = i / total

        clip_id = new_id()
        kind = (ClipKind.HIGHLIGHTS if cand.kind == "highlights"
                else (ClipKind.SHORT if vertical else ClipKind.LONG))

        segments = cand.segments or [(cand.start, cand.end)]

        src_w0 = int(ctx.source_info.get("width") or 1920)
        src_h0 = int(ctx.source_info.get("height") or 1080)
        play_w, _play_h = render.target_resolution(
            ctx.settings.short_resolution if vertical else ctx.settings.long_resolution,
            src_w0, src_h0, vertical=vertical)

        # -- תכנית העריכה: מה נחתך, איפה משנים זווית, מה מואץ --
        edit_style = editing.style_from_settings(ctx.settings, vertical=vertical)
        out_w0, out_h0 = render.target_resolution(
            ctx.settings.short_resolution if vertical
            else ctx.settings.long_resolution, src_w0, src_h0,
            vertical=vertical)
        director_plans = _direct_segments(
            ctx, segments, vertical=vertical, edit_style=edit_style,
            src_w=src_w0, src_h=src_h0, out_w=out_w0, out_h=out_h0)

        if director_plans:
            edit_plans = [director_bridge.to_edit_plan(p)
                          for p in director_plans]
        else:
            edit_plans = [
                editing.build_edit_plan(
                    clip_start=s0, clip_end=s1, peak_time=cand.peak_time,
                    style=edit_style, audio=ctx.audio_feats,
                    timeline=ctx.timeline, transcript=ctx.transcript,
                    silences=ctx.silences,
                )
                for s0, s1 in segments
            ]
        edited_duration = sum(p.out_duration for p in edit_plans)

        cues = _build_cues_for(ctx, cand, segments, play_width=play_w,
                               frame_height=out_h0, edit_plans=edit_plans,
                               director_plans=director_plans)
        lang = subtitles.detect_cue_language(cues) or (ctx.transcript.language
                                                       if ctx.transcript else "")

        style = subtitles.style_for_clip(ctx.settings, vertical=vertical,
                                         language=lang, frame_height=out_h0)
        style.animation = edit_style.caption_animation
        if director_plans:
            preset = _caption_preset_for(ctx, director_plans[0])
            style = caption_engine.apply_preset(
                style, preset, frame_w=out_w0, frame_h=out_h0,
                vertical=vertical)
        plan = reframe.plan_reframe(
            ctx.visual_feats, clip_start=cand.start, clip_end=cand.end,
            layout=ctx.settings.short_layout if vertical else "center",
            manual_camera=ctx.camera_region,
        ) if vertical else None
        if plan and plan.note:
            log.info("reframe[%s]: %s", clip_id, plan.note)

        # -- שורת DB לפני הרינדור, כדי שהממשק יראה התקדמות --
        _create_clip_row(ctx, clip_id, cand, kind, vertical, style, plan, cues,
                         lang, edit_plans=edit_plans, edit_style=edit_style,
                         edited_duration=edited_duration,
                         director_plans=director_plans)

        src_w = int(ctx.source_info.get("width") or 1920)
        src_h = int(ctx.source_info.get("height") or 1080)
        w, h = render.target_resolution(
            ctx.settings.short_resolution if vertical else ctx.settings.long_resolution,
            src_w, src_h, vertical=vertical)

        sub_path: Optional[Path] = None
        if ctx.settings.subtitles_enabled and cues:
            sub_path = ctx.work_dir / f"{clip_id}.ass"
            subtitles.write_ass(
                cues, sub_path, width=w, height=h, style=style,
                title_text=cand.title if ctx.settings.title_card_enabled else "",
            )
            subtitles.write_srt(cues, ctx.export_dir /
                                f"{_clip_basename(cand, i, vertical)}.srt")

        out_path = unique_path(
            ctx.export_dir / f"{_clip_basename(cand, i, vertical)}.mp4")

        req = render.build_request(
            source=ctx.source_path, output=out_path, segments=segments,
            vertical=vertical, settings=ctx.settings, reframe=plan,
            subtitle_path=sub_path, source_info=ctx.source_info,
            transitions=bool(cand.segments), work_dir=ctx.work_dir,
            edit_style=edit_style.name, edit_plans=list(edit_plans),
            # כשהמאסטרינג פעיל הוא מטפל באודיו אחרי הרינדור,
            # ולכן כאן רק שומרים על הסנכרון בלי ליטוש כפול.
            audio_chain=("aresample=async=1:first_pts=0"
                         if ctx.settings.mastering_enabled else ""),
        )

        _update_clip(clip_id, status=ClipStatus.RENDERING)
        try:
            result = render.render_clip(
                req,
                on_progress=lambda f, _b=base: ctx.reporter.progress(
                    _b + f / total, f"מייצא קליפ {i + 1} מתוך {total}"),
                cancel_event=ctx.cancel_event,
            )
        except JobCancelledError:
            _update_clip(clip_id, status=ClipStatus.FAILED, error="בוטל")
            raise
        except PolixorError as exc:
            log.warning("clip render failed: %s", exc.message)
            _update_clip(clip_id, status=ClipStatus.FAILED, error=exc.message)
            ctx.reporter.log(f"ייצוא קליפ {i + 1} נכשל: {exc.message}", level="error")
            continue

        # מאסטרינג על מה שבאמת יצא, ואז בדיקת איכות על הקובץ הסופי.
        audio_check = _master_clip_audio(ctx, result.path)
        music_info = _mix_music(ctx, result.path,
                                director_plans=director_plans,
                                edit_plans=edit_plans,
                                duration=result.duration)
        qa_report = _qa_clip(ctx, result.path, edit_plans=edit_plans,
                             cues=cues, style=style, vertical=vertical)
        patch: dict[str, Any] = {}
        if audio_check:
            patch["audio"] = audio_check
        if qa_report is not None:
            patch["qa"] = qa_report.to_dict()
        if music_info:
            patch["music"] = music_info
        if patch:
            _merge_render_params(clip_id, patch)

        # §25: בעיה שנמצאה פירושה `needs_review`, לא „הושלם".
        flagged = bool(audio_check.get("needs_review")) or bool(
            qa_report is not None and qa_report.needs_review)
        status = ClipStatus.NEEDS_REVIEW if flagged else ClipStatus.READY

        _update_clip(
            clip_id, status=status,
            file_path=str(result.path), file_size=result.size_bytes,
            thumbnail_path=str(result.thumbnail) if result.thumbnail else "",
            width=result.width, height=result.height, duration=result.duration,
        )
        if flagged:
            reasons = list(audio_check.get("issues") or [])
            if qa_report is not None:
                reasons += [f.message for f in qa_report.errors]
            ctx.reporter.log(
                f"„{cand.title}”: הקליפ נוצר אבל דורש בדיקה — "
                + " · ".join(reasons[:3]), level="warning")
        BUS.emit("clip.ready", ctx.job_id, clip_id=clip_id,
                 title=cand.title, kind=kind.value)


def _merge_render_params(clip_id: str, patch: dict[str, Any]) -> None:
    """מוסיף שדות ל-render_params בלי לדרוס את מה שכבר שם."""
    with session_scope() as s:
        clip = s.get(Clip, clip_id)
        if clip is None:
            return
        params = dict(clip.render_params or {})
        params.update(patch)
        clip.render_params = params


def _mix_music(ctx: JobContext, out_path: Path, *, director_plans: list,
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
        return {"error": "קובץ המוזיקה לא נמצא.", "active": False}

    beats = _beats_in_output_time(director_plans, edit_plans)
    plan = music_engine.plan_music(
        total_duration=duration, profile=s.music_profile,
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


def _qa_clip(ctx: JobContext, out_path: Path, *, edit_plans: list,
             cues: list, style, vertical: bool):
    """
    בדיקת איכות על הקובץ הסופי (§25).

    האורך הצפוי מגיע מתכנית העריכה ולא מהחלון המקורי — זה מה
    שמאפשר לתפוס פער בין מה שתוכנן למה שרונדר בפועל.
    """
    expected = sum(p.out_duration for p in (edit_plans or []) if p)
    try:
        return render_qa.check_render(
            out_path,
            expected_duration=expected,
            expect_audio=bool(ctx.source_info.get("has_audio", True)),
            vertical=vertical,
            cues=cues or None,
            safe_margin_v=int(getattr(style, "margin_v", 0) or 0),
        )
    except Exception as exc:                            # noqa: BLE001
        log.warning("post-render QA failed: %s", exc, exc_info=True)
        return None


def _master_clip_audio(ctx: JobContext, out_path: Path) -> dict[str, Any]:
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
                       if result.after and result.after.lufs is not None
                       else None),
        "after_true_peak": (round(result.after.true_peak, 2)
                            if result.after
                            and result.after.true_peak is not None else None),
        "chain": plan.filter_chain(),
        "steps": [s.to_dict() for s in plan.steps],
        "processed": result.processed,
        "verified": result.verified,
        "needs_review": result.needs_review,
        "issues": result.issues,
        "summary": result.summary(),
    }
    if result.needs_review:
        log.warning("clip audio needs review: %s", result.issues)
    return row


def _direct_segments(ctx: JobContext, segments: list[tuple[float, float]], *,
                     vertical: bool, edit_style, src_w: int, src_h: int,
                     out_w: int, out_h: int) -> list:
    """
    מריץ את הבמאי על כל מקטע ומחזיר תכניות — בלי לבצע דבר.

    מחזיר רשימה ריקה כשהבמאי כבוי או כשאין תמלול: במצב כזה חוזרים
    לעורך ההיוריסטי, שאינו תלוי בטקסט. עדיף עורך פשוט שעובד על
    „במאי" שמנחש בלי חומר.
    """
    if not ctx.settings.director_enabled or ctx.transcript is None:
        return []

    # „גולמי" הוא בקשה מפורשת לחיתוך ישיר בלי עריכה. במאי שמחליט
    # החלטות על סגנון כזה פשוט מתעלם ממה שהמשתמש ביקש.
    if edit_style.name == "raw":
        return []

    style = ctx.settings.director_style or pacing_engine.LEGACY_STYLE_MAP.get(
        edit_style.name, pacing_engine.DEFAULT_PROFILE)
    plans = []
    for s0, s1 in segments:
        try:
            sem_analysis = semantics.analyze(
                ctx.transcript, ctx.audio_feats, start=s0, end=s1,
                settings=ctx.settings, use_llm=False)
            plans.append(video_director.direct(
                semantics=sem_analysis, audio=ctx.audio_feats,
                transcript=ctx.transcript, source_start=s0, source_end=s1,
                width=src_w, height=src_h, out_width=out_w, out_height=out_h,
                fps=float(ctx.source_info.get("fps") or 30.0),
                has_audio=bool(ctx.source_info.get("has_audio", True)),
                style=style, settings=ctx.settings))
        except Exception as exc:                        # noqa: BLE001
            # תקלה בבמאי לא אמורה להפיל ייצוא. חוזרים לעורך הקודם
            # ואומרים את זה ביומן, במקום לייצא קליפ שקט בלי עריכה.
            log.warning("director failed on segment %.2f–%.2f: %s",
                        s0, s1, exc, exc_info=True)
            return []
    return plans


def _caption_preset_for(ctx: JobContext, plan):
    """פריסט הכתוביות: בחירת המשתמש גוברת על גזירה מהקצב."""
    chosen = ctx.settings.caption_preset or director_bridge.caption_preset_for(
        plan)
    return caption_engine.get_preset(chosen)


def _build_cues_for(ctx: JobContext, cand: selection.Candidate,
                    segments: list[tuple[float, float]],
                    *, play_width: int = 1080, frame_height: int = 0,
                    edit_plans: Optional[list] = None,
                    director_plans: Optional[list] = None
                    ) -> list[subtitles.Cue]:
    """
    כתוביות לקליפ, ממופות דרך תכנית העריכה.

    כשהעורך מסיר אוויר מת, כל מה שאחרי החיתוך זז אחורה. הכתוביות
    חייבות לזוז איתו, אחרת הן מתנתקות מהדיבור. המיפוי נעשה לכל
    מקטע בנפרד, ואז מוסט לפי האורך **הערוך** של המקטעים שלפניו.

    כשיש תכנית של הבמאי, הקיבוץ נעשה במנוע הכתוביות החדש (מודע
    פיסוק ופריסטים) וההדגשות מגיעות מהחלטות הבמאי.
    """
    if not ctx.settings.subtitles_enabled or ctx.transcript is None:
        return []

    # מכסת התווים לכתובית נגזרת מרוחב הפריים ומגודל הגופן, כדי שהטקסט
    # ייכנס בדיוק לשתי שורות ולא יישבר לשלוש על-ידי libass.
    vertical = cand.kind == "short"
    probe_style = subtitles.style_for_clip(
        ctx.settings, vertical=vertical, language="",
        frame_height=frame_height or play_width)
    max_chars = subtitles._max_chars_for(probe_style, play_width)
    preset = (_caption_preset_for(ctx, director_plans[0])
              if director_plans else None)

    cues: list[subtitles.Cue] = []
    offset = 0.0
    for i, (s0, s1) in enumerate(segments):
        if preset is not None:
            seg_cues = caption_engine.build_captions(
                ctx.transcript, clip_start=s0, clip_end=s1, preset=preset,
                frame_chars=min(max_chars, preset.max_chars))
        else:
            seg_cues = subtitles.build_cues(ctx.transcript, clip_start=s0,
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


def _clip_basename(cand: selection.Candidate, index: int, vertical: bool) -> str:
    prefix = "short" if vertical else ("highlights" if cand.segments else "long")
    stamp = int(cand.start)
    title = safe_filename(cand.title or "clip", max_length=48)
    return f"{prefix}_{index + 1:02d}_{stamp}s_{title}"


def _create_clip_row(ctx: JobContext, clip_id: str, cand: selection.Candidate,
                     kind: ClipKind, vertical: bool,
                     style: subtitles.SubtitleStyle,
                     plan: Optional[reframe.ReframePlan],
                     cues: list[subtitles.Cue], lang: str,
                     *, edit_plans: Optional[list] = None,
                     edit_style=None, edited_duration: float = 0.0,
                     director_plans: Optional[list] = None) -> None:
    from .models import SubtitleCue

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

    with session_scope() as s:
        clip = Clip(
            id=clip_id, job_id=ctx.job_id, kind=kind, status=ClipStatus.PENDING,
            title=cand.title, description=cand.description, reason=cand.reason,
            score=cand.score, source_start=cand.start, source_end=cand.end,
            duration=edited_duration or cand.duration,
            segments_json=[[a, b] for a, b in (cand.segments or [])],
            aspect="9:16" if vertical else "16:9",
            layout=(plan.layout if plan else "center"),
            subtitles_enabled=bool(ctx.settings.subtitles_enabled and cues),
            subtitle_style=style.__dict__.copy(),
            render_params={
                "category": cand.category,
                "signals": cand.signals,
                "title_source": cand.title_source,
                "peak_time": round(cand.peak_time, 3),
                "reframe_note": plan.note if plan else "",
                "tracked_ratio": plan.tracked_ratio if plan else 0.0,
                "camera_region": plan.camera_region if plan else None,
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
            },
        )
        s.add(clip)
        s.flush()
        for idx, cue in enumerate(cues):
            s.add(SubtitleCue(
                clip_id=clip_id, idx=idx, start=cue.start, end=cue.end,
                text=cue.text, original_text=cue.text, language=lang,
                words=cue.words,
            ))
    BUS.emit("clip.created", ctx.job_id, clip_id=clip_id, title=cand.title,
             kind=kind.value)


def _update_clip(clip_id: str, **fields: Any) -> None:
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


# --------------------------------------------------------------------------
# שמירה ושחזור
# --------------------------------------------------------------------------
def _save_transcript(result: TranscriptResult, path: Path) -> None:
    data = {
        "language": result.language, "duration": result.duration,
        "provider": result.provider, "model": result.model, "note": result.note,
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
                        text=w.get("text", ""), probability=float(w.get("p", 1.0)))
                   for w in s.get("words", [])],
        )
        for s in data.get("segments", [])
    ]
    return TranscriptResult(
        segments=segs, language=data.get("language", ""),
        duration=float(data.get("duration", 0.0)),
        provider=data.get("provider", ""), model=data.get("model", ""),
        note=data.get("note", ""),
    )


def _persist_segments(job_id: str, result: TranscriptResult) -> None:
    with session_scope() as s:
        s.query(TranscriptSegment).filter(TranscriptSegment.job_id == job_id).delete()
        for i, seg in enumerate(result.segments):
            s.add(TranscriptSegment(
                job_id=job_id, idx=i, start=seg.start, end=seg.end, text=seg.text,
                language=seg.language or result.language,
                avg_logprob=seg.avg_logprob, no_speech_prob=seg.no_speech_prob,
                words=[w.to_dict() for w in seg.words],
            ))


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
                source_of_truth=c.title_source,
            ))


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
        "hop": tl.hop,
        "duration": tl.duration,
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


def _save_visual(ctx: JobContext) -> None:
    """
    שומר את מלבני הפנים שזוהו, כדי שייצוא מחדש (שינוי פריסה אנכית
    במסך העריכה) לא יחייב ניתוח חוזר של כל הווידאו.
    """
    vf = ctx.visual_feats
    if vf is None or not vf.analyzed or not vf.faces:
        return
    data = {
        "fps": vf.fps, "duration": vf.duration,
        "width": vf.width, "height": vf.height,
        "faces": [[[round(v, 4) for v in box] for box in frame]
                  for frame in vf.faces],
    }
    path = ctx.work_dir / "faces.json"
    path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    ctx.artifacts["faces_path"] = str(path)


def load_visual(job: Job) -> Optional[VisualFeatures]:
    """טוען נתוני פנים שנשמרו, לשימוש בייצוא מחדש."""
    path = (job.artifacts or {}).get("faces_path")
    if not path or not Path(path).exists():
        return None
    try:
        data = json.loads(Path(path).read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    faces = [[tuple(box) for box in frame] for frame in data.get("faces", [])]
    vf = VisualFeatures(
        fps=float(data.get("fps") or 1.0), duration=float(data.get("duration") or 0.0),
        width=int(data.get("width") or 0), height=int(data.get("height") or 0),
        faces=faces, analyzed=True,
    )
    vf.times = np.arange(len(faces), dtype=np.float32) / max(1e-6, vf.fps)
    return vf


def load_transcript_for_job(job: Job) -> Optional[TranscriptResult]:
    path = (job.artifacts or {}).get("transcript_path")
    if not path or not Path(path).exists():
        return None
    return _load_transcript(Path(path))


def _finalize_notes(ctx: JobContext) -> None:
    if not ctx.notes:
        return
    with session_scope() as s:
        job = s.get(Job, ctx.job_id)
        if job is not None:
            arts = dict(job.artifacts or {})
            arts["notes"] = ctx.notes
            job.artifacts = arts


# --------------------------------------------------------------------------
# חיבור למנהל המשימות
# --------------------------------------------------------------------------
MANAGER.set_runner(run_job)


def resume_interrupted_jobs() -> int:
    """
    בעליית השרת: משימות שהיו RUNNING בעת סגירה מסומנות ככשלות
    עם אפשרות חידוש (הן לא ימשיכו מעצמן, כדי לא להפתיע את המשתמש).
    """
    count = 0
    with session_scope() as s:
        jobs = s.query(Job).filter(Job.status == JobStatus.RUNNING).all()
        for job in jobs:
            job.status = JobStatus.FAILED
            job.error = "השרת נסגר באמצע העיבוד."
            job.error_code = "interrupted"
            job.message = "נקטע – ניתן לחדש מהשלב האחרון שהושלם."
            count += 1
    return count


def load_timeline_for_job(job: Job) -> Optional[scoring.Timeline]:
    """
    משחזר ציר זמן מקורב מקובץ התצוגה שנשמר בשלב הניתוח.

    הציר המלא (רזולוציה של 0.1 שנייה) אינו נשמר כי הוא כבד, אבל
    הגרסה המוקטנת מספיקה לעריכה: היא נדרשת רק כדי לזהות אילו
    שתיקות הן דרמטיות, וזו שאלה ברזולוציה של שניות.
    """
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
        times=np.asarray(times, dtype=np.float32),
    )
    for name in ("score", "vocal", "speech", "visual", "pause"):
        values = data.get(name)
        if values:
            setattr(tl, name, np.asarray(values, dtype=np.float32))
    return tl


def load_analysis_for_job(job: Job) -> dict[str, Any]:
    """
    טוען מחדש את נתוני הניתוח ששמורים על הדיסק, לשימוש בייצוא מחדש.

    התמלול והציר המוקטן נשמרים בשלב הניתוח, ולכן ייצוא מחדש מקבל
    את אותן החלטות עריכה כמו הריצה המקורית – כולל הגנה על שתיקות
    דרמטיות. אותות האודיו הגולמיים אינם נשמרים, אך הם נחוצים רק
    כשאין תמלול כלל.
    """
    return {
        "transcript": load_transcript_for_job(job),
        "timeline": load_timeline_for_job(job),
        "silences": None,
        "visual": load_visual(job),
    }
