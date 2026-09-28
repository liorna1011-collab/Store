"""
מנוע משימות ברקע.

* כל משימה רצה בתהליכון נפרד מתוך ThreadPoolExecutor (עיבוד וידאו הוא
  ברובו המתנה ל-FFmpeg/דיסק/רשת, ולכן תהליכונים מתאימים ולא תהליכים).
* ביטול הוא שיתופי: ה-pipeline בודק `cancel_event` בין שלבים, ו-FFmpeg
  מקבל terminate מיידי.
* התקדמות אמיתית: כל שלב מדווח 0..1, וה-reporter ממיר למשקל הכולל
  לפי השלבים שמתוכננים בפועל למשימה הזו.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import i18n
from .config import SETTINGS
from .db import session_scope
from .errors import JobCancelledError, PolixorError
from .events import BUS
from .models import Job, JobStage, JobStatus, ProjectPhase, RunScope, StageTiming, utcnow

log = logging.getLogger("polixor.worker")

# משקל יחסי של כל שלב בהתקדמות הכוללת (מנורמל לפי השלבים שבאמת ירוצו)
STAGE_WEIGHTS: dict[JobStage, float] = {
    JobStage.CAPTURE: 30.0,
    JobStage.DOWNLOAD: 22.0,
    JobStage.PROBE: 1.0,
    JobStage.AUDIO: 4.0,
    JobStage.TRANSCRIBE: 26.0,
    JobStage.ANALYZE: 16.0,
    JobStage.SELECT: 5.0,
    JobStage.RENDER_LONG: 12.0,
    JobStage.RENDER_SHORT: 14.0,
}


@dataclass
class JobHandle:
    job_id: str
    cancel_event: threading.Event = field(default_factory=threading.Event)
    future: Optional[Future] = None
    started_at: float = field(default_factory=time.time)


class ProgressReporter:
    """
    מעדכן את ה-DB ומפרסם אירועים. מוגבל בקצב כדי לא להציף
    את ה-WebSocket ואת ה-SQLite בכתיבות.
    """

    MIN_INTERVAL = 0.20     # שניות בין עדכונים
    MIN_DELTA = 0.004       # שינוי מינימלי בהתקדמות שמצדיק עדכון

    def __init__(self, job_id: str, planned_stages: list[JobStage],
                 cancel_event: threading.Event) -> None:
        self.job_id = job_id
        self.cancel_event = cancel_event
        self.planned = [s for s in planned_stages if s in STAGE_WEIGHTS]
        total = sum(STAGE_WEIGHTS[s] for s in self.planned) or 1.0
        self._weights = {s: STAGE_WEIGHTS[s] / total for s in self.planned}
        self._offset: dict[JobStage, float] = {}
        acc = 0.0
        for s in self.planned:
            self._offset[s] = acc
            acc += self._weights[s]

        self.stage: JobStage = JobStage.PENDING
        self._last_push = 0.0
        self._last_value = -1.0
        self._stage_started: float = 0.0
        self._lock = threading.RLock()

    # ---- ניהול שלבים ----
    def start_stage(self, stage: JobStage, message: str = "") -> None:
        with self._lock:
            self.stage = stage
            self._stage_started = time.time()
        self._write(stage_progress=0.0, message=message, force=True)

    def finish_stage(self, stage: JobStage, media_seconds: float = 0.0) -> None:
        elapsed = time.time() - self._stage_started if self._stage_started else 0.0
        self._write(stage_progress=1.0, force=True)
        with session_scope() as s:
            job = s.get(Job, self.job_id)
            if job is not None:
                done = list(job.completed_stages or [])
                if stage.value not in done:
                    done.append(stage.value)
                job.completed_stages = done
                s.add(StageTiming(job_id=self.job_id, stage=stage.value,
                                  seconds=round(elapsed, 3),
                                  media_seconds=round(media_seconds, 3)))

    def progress(self, value: float, message: Optional[str] = None) -> None:
        """value: 0..1 בתוך השלב הנוכחי."""
        self.check_cancel()
        self._write(stage_progress=min(1.0, max(0.0, float(value))), message=message)

    def log(self, message: str, level: str = "info") -> None:
        BUS.emit("job.log", self.job_id, message=message, level=level,
                 stage=self.stage.value)

    def check_cancel(self) -> None:
        if self.cancel_event.is_set():
            raise JobCancelledError()

    # ---- כתיבה בפועל ----
    def overall(self, stage_progress: float) -> float:
        base = self._offset.get(self.stage, 0.0)
        weight = self._weights.get(self.stage, 0.0)
        return min(1.0, max(0.0, base + weight * stage_progress))

    def _write(self, *, stage_progress: float, message: Optional[str] = None,
               force: bool = False) -> None:
        now = time.time()
        overall = self.overall(stage_progress)
        if not force:
            if (now - self._last_push) < self.MIN_INTERVAL and \
               abs(overall - self._last_value) < self.MIN_DELTA:
                return
        self._last_push = now
        self._last_value = overall

        with session_scope() as s:
            job = s.get(Job, self.job_id)
            if job is None:
                return
            job.stage = self.stage
            job.stage_progress = stage_progress
            job.overall_progress = overall
            if message is not None:
                job.message = message
            payload = {
                "stage": self.stage.value,
                "stage_progress": round(stage_progress, 4),
                "overall_progress": round(overall, 4),
                "message": job.message,
                "status": job.status.value,
            }
        BUS.emit("job.progress", self.job_id, **payload)


class JobManager:
    """רישום המשימות הפעילות, הרצה, ביטול והתאוששות."""

    def __init__(self) -> None:
        self._handles: dict[str, JobHandle] = {}
        self._lock = threading.RLock()
        workers = max(1, SETTINGS.get().concurrent_jobs)
        self._pool = ThreadPoolExecutor(max_workers=workers,
                                        thread_name_prefix="polixor-job")
        self._runner: Optional[Callable[[str, threading.Event], None]] = None

    def set_runner(self, fn: Callable[[str, threading.Event], None]) -> None:
        """מוזרק מ-pipeline כדי למנוע ייבוא מעגלי."""
        self._runner = fn

    # ---- הרצה ----
    def submit(self, job_id: str) -> JobHandle:
        with self._lock:
            existing = self._handles.get(job_id)
            if existing and existing.future and not existing.future.done():
                return existing
            handle = JobHandle(job_id=job_id)
            self._handles[job_id] = handle

        with i18n.use_lang(_job_language(job_id)):
            self._set_status(job_id, JobStatus.QUEUED, message=i18n.tr("pipeline.status.queued"))
        handle.future = self._pool.submit(self._run, handle)
        return handle

    def _run(self, handle: JobHandle) -> None:
        job_id = handle.job_id
        # כל ההודעות של הריצה – כולל שגיאות – בשפת הפרויקט
        with i18n.use_lang(_job_language(job_id)):
            if self._runner is None:
                self._fail(job_id, PolixorError(message_key="errors.engine_not_ready.message"))
                with self._lock:
                    self._handles.pop(job_id, None)
                return
            try:
                self._mark_started(job_id)
                self._runner(job_id, handle.cancel_event)
                self._finish(job_id, JobStatus.COMPLETED, "")
            except JobCancelledError:
                self._finish(job_id, JobStatus.CANCELLED, i18n.tr("pipeline.status.cancelled"))
            except PolixorError as exc:
                log.warning("job %s failed: %s", job_id, exc.message)
                self._fail(job_id, exc)
            except Exception as exc:  # noqa: BLE001 – רשת ביטחון אחרונה
                log.exception("job %s crashed", job_id)
                self._fail(job_id, PolixorError(
                    message_key="errors.unexpected.message",
                    detail=f"{exc}\n{traceback.format_exc()[-1500:]}"))
            finally:
                with self._lock:
                    self._handles.pop(job_id, None)

    # ---- ביטול ----
    def cancel(self, job_id: str) -> bool:
        with self._lock:
            handle = self._handles.get(job_id)
        if handle is None:
            # לא רץ כרגע – מסמנים כמבוטל אם היה בתור
            with session_scope() as s:
                job = s.get(Job, job_id)
                if job and job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
                    job.status = JobStatus.CANCELLED
                    with i18n.use_lang(job.ui_language or i18n.DEFAULT_LANG):
                        job.message = i18n.tr("pipeline.status.cancelled")
                    job.finished_at = utcnow()
                    phase = _phase_after(job, JobStatus.CANCELLED)
                    if phase:
                        job.phase = phase
                    BUS.emit("job.status", job_id, status="cancelled",
                             message=job.message)
                    _emit_project(job)
                    return True
            return False
        handle.cancel_event.set()
        with i18n.use_lang(_job_language(job_id)):
            BUS.emit("job.log", job_id, message=i18n.tr("pipeline.status.cancelling"),
                     level="warn")
        return True

    def is_running(self, job_id: str) -> bool:
        with self._lock:
            h = self._handles.get(job_id)
            return bool(h and h.future and not h.future.done())

    def active_ids(self) -> list[str]:
        with self._lock:
            return list(self._handles.keys())

    def cancel_event_for(self, job_id: str) -> Optional[threading.Event]:
        with self._lock:
            h = self._handles.get(job_id)
            return h.cancel_event if h else None

    def shutdown(self, wait: bool = False) -> None:
        with self._lock:
            for h in self._handles.values():
                h.cancel_event.set()
        self._pool.shutdown(wait=wait, cancel_futures=True)

    # ---- עדכוני סטטוס ----
    @staticmethod
    def _set_status(job_id: str, status: JobStatus, message: str = "") -> None:
        with session_scope() as s:
            job = s.get(Job, job_id)
            if job is None:
                return
            job.status = status
            if message:
                job.message = message
            if status == JobStatus.QUEUED and job.phase:
                job.phase = _phase_on_start(job)
            snapshot = _project_snapshot(job)
        BUS.emit("job.status", job_id, status=status.value, message=message)
        _emit_snapshot(snapshot)

    @staticmethod
    def _mark_started(job_id: str) -> None:
        message = i18n.tr("pipeline.status.starting")
        with session_scope() as s:
            job = s.get(Job, job_id)
            if job is None:
                return
            job.status = JobStatus.RUNNING
            job.error = ""
            job.error_code = ""
            job.error_data = {}
            job.started_at = job.started_at or utcnow()
            job.finished_at = None
            job.message = message
            if job.phase:
                job.phase = _phase_on_start(job)
            snapshot = _project_snapshot(job)
        BUS.emit("job.status", job_id, status="running", message=message)
        _emit_snapshot(snapshot)

    @staticmethod
    def _finish(job_id: str, status: JobStatus, message: str) -> None:
        with session_scope() as s:
            job = s.get(Job, job_id)
            if job is None:
                return
            if status == JobStatus.COMPLETED and not message:
                scope = job.run_scope or RunScope.ALL.value
                message = i18n.tr("pipeline.status.analysis_done"
                                  if scope == RunScope.ANALYZE.value else
                                  ("pipeline.status.generate_done"
                                   if scope == RunScope.GENERATE.value
                                   else "pipeline.status.completed"))
            job.status = status
            job.message = message
            job.finished_at = utcnow()
            if status == JobStatus.COMPLETED:
                job.stage = JobStage.DONE
                job.overall_progress = 1.0
                job.stage_progress = 1.0
            phase = _phase_after(job, status)
            if phase:
                job.phase = phase
            snapshot = _project_snapshot(job)
        BUS.emit("job.status", job_id, status=status.value, message=message,
                 overall_progress=1.0 if status == JobStatus.COMPLETED else None)
        _emit_snapshot(snapshot)

    @staticmethod
    def _fail(job_id: str, exc: PolixorError) -> None:
        with session_scope() as s:
            job = s.get(Job, job_id)
            if job is None:
                return
            job.status = JobStatus.FAILED
            job.error = exc.message
            job.error_code = exc.code
            job.error_data = exc.to_record()
            job.message = exc.message
            job.finished_at = utcnow()
            phase = _phase_after(job, JobStatus.FAILED)
            if phase:
                job.phase = phase
            snapshot = _project_snapshot(job)
        BUS.emit("job.status", job_id, status="failed", message=exc.message,
                 error=exc.to_dict())
        _emit_snapshot(snapshot)


# --------------------------------------------------------------------------
# שלב הפרויקט
# --------------------------------------------------------------------------
def _job_language(job_id: str) -> str:
    try:
        with session_scope() as s:
            job = s.get(Job, job_id)
            return (job.ui_language if job is not None and job.ui_language
                    else i18n.DEFAULT_LANG)
    except Exception:                                  # noqa: BLE001
        return i18n.DEFAULT_LANG


def _phase_on_start(job: Job) -> str:
    scope = job.run_scope or RunScope.ALL.value
    if scope == RunScope.GENERATE.value:
        return ProjectPhase.GENERATING.value
    if scope == RunScope.ANALYZE.value:
        has_source = bool((job.artifacts or {}).get("source_path"))
        return (ProjectPhase.ANALYZING if has_source else ProjectPhase.IMPORTING).value
    return job.phase or ""


def _phase_after(job: Job, status: JobStatus) -> str:
    """השלב אחרי שהריצה הסתיימה. משימה ישנה (בלי phase) נשארת כמו שהיא."""
    if not job.phase:
        return ""
    if status == JobStatus.COMPLETED:
        scope = job.run_scope or RunScope.ALL.value
        return (ProjectPhase.CONFIGURE if scope == RunScope.ANALYZE.value
                else ProjectPhase.DONE).value
    if status == JobStatus.CANCELLED:
        return ProjectPhase.CANCELLED.value
    if status == JobStatus.FAILED:
        return ProjectPhase.FAILED.value
    return job.phase


def _project_snapshot(job: Job) -> dict[str, str]:
    return {"id": job.id, "phase": job.phase or derived_phase(job),
            "status": job.status.value}


def _emit_snapshot(snap: Optional[dict[str, str]]) -> None:
    if snap:
        BUS.emit("project.updated", snap["id"], project_id=snap["id"],
                 phase=snap["phase"], status=snap["status"])


def _emit_project(job: Job) -> None:
    _emit_snapshot(_project_snapshot(job))


def derived_phase(job: Job) -> str:
    """שלב מחושב למשימה ישנה (בלי phase), כדי שתוצג כפרויקט."""
    if job.phase:
        return job.phase
    status = job.status.value if isinstance(job.status, JobStatus) else str(job.status)
    if status == "completed":
        return ProjectPhase.DONE.value
    if status == "failed":
        return ProjectPhase.FAILED.value
    if status == "cancelled":
        return ProjectPhase.CANCELLED.value
    stage = job.stage.value if isinstance(job.stage, JobStage) else str(job.stage)
    if stage in ("capture", "download"):
        return ProjectPhase.IMPORTING.value
    if stage in ("pending", "probe", "audio", "transcribe", "analyze"):
        return ProjectPhase.ANALYZING.value
    return ProjectPhase.GENERATING.value


MANAGER = JobManager()
