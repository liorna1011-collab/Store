"""
Job health: no job may sit in "processing" forever.

The worker records, in memory, the last moment each running job reported anything (a progress
tick, a stage start, a log line) and writes `jobs.heartbeat_at` every HEARTBEAT seconds for
every job its threads are running. A watchdog thread checks every CHECK_EVERY seconds:

  orphaned  the database says RUNNING/QUEUED but no worker thread has the job (the thread died
            outside the pipeline's error handling, or a submit was lost): it is submitted again –
            it continues from its checkpoints – at most AUTO_RESUME_LIMIT times, then it is
            marked "needs attention"
  stalled   a worker has the job but nothing was reported for longer than the stage's limit
            (ASR, a model call or a render that hangs): the job is asked to stop (FFmpeg is
            terminated) and is marked "needs attention" with the stage – Resume continues from
            the last checkpoint, with no second charge

Limits are generous on purpose (a slow machine is not a stuck one): quality is never traded for
speed, the watchdog only ends silence. POLIXOR_STALL_MINUTES scales them all (default 1.0 = the
values below).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Optional

from ..db import session_scope
from ..models import Job, JobStage, JobStatus, ProjectPhase, utcnow

log = logging.getLogger("polixor.health")

HEARTBEAT = 15.0
CHECK_EVERY = 30.0
ORPHAN_AFTER = 120.0                  # seconds without a heartbeat and without a worker thread

# minutes without any report before a stage counts as stalled
STAGE_LIMIT_MIN: dict[str, float] = {
    JobStage.DOWNLOAD.value: 30, JobStage.CAPTURE.value: 30, JobStage.PROBE.value: 10,
    JobStage.AUDIO.value: 30, JobStage.TRANSCRIBE.value: 40, JobStage.ANALYZE.value: 45,
    JobStage.SELECT.value: 45, JobStage.RENDER_SHORT.value: 30, JobStage.RENDER_LONG.value: 45,
}
DEFAULT_LIMIT_MIN = 45

_seen: dict[str, float] = {}
_seen_lock = threading.Lock()
_thread: Optional[threading.Thread] = None
_stop = threading.Event()
_events: list[dict[str, Any]] = []


def touch(job_id: str) -> None:
    """Called by the worker on every report of a running job."""
    with _seen_lock:
        _seen[job_id] = time.time()


def forget(job_id: str) -> None:
    with _seen_lock:
        _seen.pop(job_id, None)


def last_seen(job_id: str) -> Optional[float]:
    with _seen_lock:
        return _seen.get(job_id)


def _scale() -> float:
    try:
        return max(0.05, float(os.environ.get("POLIXOR_STALL_MINUTES", "1") or 1))
    except ValueError:
        return 1.0


def stage_limit_seconds(stage: str) -> float:
    return STAGE_LIMIT_MIN.get(stage, DEFAULT_LIMIT_MIN) * 60 * _scale()


def _record(kind: str, job_id: str, **data: Any) -> None:
    _events.append({"at": time.time(), "kind": kind, "job_id": job_id, **data})
    del _events[:-100]


def heartbeat(active: list[str]) -> None:
    if not active:
        return
    now = utcnow()
    with session_scope() as s:
        s.query(Job).filter(Job.id.in_(active)).update({Job.heartbeat_at: now}, synchronize_session=False)


def check(now: Optional[float] = None, *, orphans: bool = True, stalls: bool = True) -> dict[str, list[str]]:
    """
    One watchdog pass. Returns what it did (also kept for the admin view).

    With worker processes the work is split: each worker checks its own jobs for stalls (only it
    knows when they last reported), the web server checks for orphans (a job no live task has).
    """
    from ..worker import MANAGER, mark_needs_attention

    now = now or time.time()
    active = set(MANAGER.active_ids())
    resumed: list[str] = []
    stalled: list[str] = []
    with session_scope() as s:
        rows = s.query(Job.id, Job.status, Job.stage, Job.heartbeat_at, Job.updated_at, Job.is_live_mode).filter(
            Job.status.in_([JobStatus.RUNNING, JobStatus.QUEUED])).all()
    for jid, status, stage, hb, upd, live in rows:
        if jid in active:
            if not stalls:
                continue
            seen = last_seen(jid)
            if seen is None:
                touch(jid)
                continue
            limit = stage_limit_seconds(stage.value if stage else "")
            if now - seen > limit:
                stalled.append(jid)
                ev = MANAGER.cancel_event_for(jid)
                if ev is not None:
                    ev.set()                      # FFmpeg is terminated, the pipeline stops at its next check
                mark_needs_attention(jid, stage.value if stage else "", reason="stalled")
                _record("stalled", jid, stage=stage.value if stage else "", silent_seconds=round(now - seen))
            continue
        # no worker thread has it
        if not orphans:
            continue
        ref = hb or upd
        age = (utcnow().replace(tzinfo=None) - ref.replace(tzinfo=None)).total_seconds() if ref else ORPHAN_AFTER + 1
        if age < ORPHAN_AFTER:
            continue
        if live:
            mark_needs_attention(jid, stage.value if stage else "", reason="interrupted")
            _record("orphan_live", jid)
            continue
        if _resume_budget(jid):
            MANAGER.submit(jid)
            resumed.append(jid)
            _record("orphan_resumed", jid, stage=stage.value if stage else "")
        else:
            mark_needs_attention(jid, stage.value if stage else "", reason="interrupted")
            _record("orphan_attention", jid)
    if resumed or stalled:
        log.warning("watchdog: resumed %s, stalled %s", resumed, stalled)
    return {"resumed": resumed, "stalled": stalled}


def _resume_budget(job_id: str) -> bool:
    from ..pipeline import AUTO_RESUME_LIMIT

    with session_scope() as s:
        job = s.get(Job, job_id)
        if job is None:
            return False
        arts = dict(job.artifacts or {})
        n = int(arts.get("auto_resumes") or 0)
        if n >= AUTO_RESUME_LIMIT:
            return False
        arts["auto_resumes"] = n + 1
        arts["resume_pending"] = True
        job.artifacts = arts
        job.status = JobStatus.QUEUED
        return True


_mode = {"orphans": True, "stalls": True}


def _loop() -> None:
    from ..worker import MANAGER

    last_check = 0.0
    while not _stop.wait(HEARTBEAT):
        try:
            if _mode["stalls"]:
                heartbeat(MANAGER.active_ids())      # the process that runs the jobs vouches for them
            if time.time() - last_check >= CHECK_EVERY:
                last_check = time.time()
                check(**_mode)
        except Exception:                               # noqa: BLE001 – the watchdog never dies
            log.warning("watchdog pass failed", exc_info=True)


def start(*, orphans: bool = True, stalls: bool = True) -> None:
    global _thread
    _mode.update(orphans=orphans, stalls=stalls)
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="polixor-watchdog", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()


def report() -> dict[str, Any]:
    from ..worker import MANAGER

    now = time.time()
    with session_scope() as s:
        attention = [{"project_id": j.id, "title": j.title, "stage": j.stage.value if j.stage else "",
                      "code": j.error_code}
                     for j in s.query(Job).filter(Job.status == JobStatus.FAILED,
                                                  Job.error_code.in_(["stalled", "interrupted"]))
                     .order_by(Job.updated_at.desc()).limit(50).all()]
        running = [{"project_id": j.id, "stage": j.stage.value if j.stage else "",
                    "silent_seconds": round(now - (last_seen(j.id) or now)),
                    "limit_seconds": round(stage_limit_seconds(j.stage.value if j.stage else "")),
                    "heartbeat_at": j.heartbeat_at.isoformat() if j.heartbeat_at else None}
                   for j in s.query(Job).filter(Job.status == JobStatus.RUNNING).all()]
    return {"active_threads": MANAGER.active_ids(), "running": running, "needs_attention": attention,
            "events": list(_events)[-50:], "phases": [p.value for p in ProjectPhase],
            "orphan_after_seconds": ORPHAN_AFTER, "stall_scale": _scale()}


__all__ = ["touch", "forget", "check", "start", "stop", "report"]
