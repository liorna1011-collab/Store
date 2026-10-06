"""
The web server's side of the worker processes: keep the right number alive, recover lost work.

Runs in the web process when POLIXOR_WORKER_MODE=process (a thread, every TICK seconds):

  * heartbeat – a `web` row in worker_processes (workers exit when no web server is left)
  * leases – tasks whose worker stopped renewing (killed, crashed, machine restarted) go back
    to the queue and the job is marked to resume from its checkpoints; after taskq.MAX_ATTEMPTS
    the job is marked "needs attention" (crash-loop guard) instead
  * processes – per role, as many live workers of the current code as configured: missing
    ones are started (`python -m polixor.workerd --role R`); a worker that exits at once is
    restarted with a growing delay, so a broken install never spins the CPU
  * pruning of finished tasks and relayed events

Worker counts (POLIXOR_WORKERS, e.g. "general:2,interactive:1"): by default `general` =
the "parallel projects" setting (1–4) and one `interactive` worker for re-renders.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import text

from ..db import get_engine, session_scope

log = logging.getLogger("polixor.workers")

TICK = 2.0
_stop = threading.Event()
_thread: Optional[threading.Thread] = None
_children: dict[int, dict[str, Any]] = {}       # pid -> {proc, role, started}
_backoff: dict[str, float] = {}
_next_spawn: dict[str, float] = {}
_web_id = ""


def wanted() -> dict[str, int]:
    from ..config import SETTINGS

    spec = os.environ.get("POLIXOR_WORKERS", "").strip()
    if spec:
        out: dict[str, int] = {}
        for part in spec.split(","):
            role, _, n = part.partition(":")
            try:
                out[role.strip()] = max(0, min(16, int(n or 1)))
            except ValueError:
                continue
        if out:
            return out
    return {"general": max(1, SETTINGS.get().concurrent_jobs), "interactive": 1}


def _heartbeat_web(build: str) -> None:
    now = time.time()
    with get_engine().begin() as c:
        c.execute(text(
            "INSERT INTO worker_processes (id, pid, role, build, state, task_id, started_at, heartbeat_at, info)"
            " VALUES (:id, :pid, 'web', :b, 'web', 0, :now, :now, '{}') ON CONFLICT(id) DO UPDATE SET"
            " heartbeat_at=:now"), {"id": _web_id, "pid": os.getpid(), "b": build, "now": now})


def _live(build: str) -> dict[str, int]:
    """Live workers of the current code per role (registered, or started by us and still starting)."""
    from .taskq import LEASE_SECONDS

    now = time.time()
    counts: dict[str, int] = {}
    seen: set[int] = set()
    with get_engine().connect() as c:
        for r in c.execute(text("SELECT pid, role, build, state FROM worker_processes WHERE role != 'web' AND "
                                "heartbeat_at > :t AND state != 'stopped'"), {"t": now - LEASE_SECONDS}).all():
            if r.build == build:
                counts[r.role] = counts.get(r.role, 0) + 1
                seen.add(int(r.pid))
    for pid, ch in list(_children.items()):
        if ch["proc"].poll() is not None:
            quick = time.time() - ch["started"] < 30
            role = ch["role"]
            _backoff[role] = min(120.0, max(5.0, _backoff.get(role, 2.5) * 2)) if quick else 0.0
            _next_spawn[role] = time.time() + _backoff[role]
            if quick:
                log.warning("worker %s (%s) exited after %.0fs (code %s) – next start in %.0fs", pid, role,
                            time.time() - ch["started"], ch["proc"].returncode, _backoff[role])
            del _children[pid]
            continue
        if pid not in seen and ch.get("build") == build:
            counts[ch["role"]] = counts.get(ch["role"], 0) + 1
    return counts


def spawn(role: str) -> int:
    env = dict(os.environ)
    env["POLIXOR_WORKER_MODE"] = "inline"
    env["POLIXOR_WORKER_ROLE"] = role
    cwd = Path(__file__).resolve().parents[2]            # .../backend (polixor is importable from here)
    kw: dict[str, Any] = {}
    if os.name != "nt":
        kw["start_new_session"] = True                    # a web restart (pkill polixor.main) leaves it running
    p = subprocess.Popen([sys.executable, "-m", "polixor.workerd", "--role", role], cwd=str(cwd), env=env,
                         stdin=subprocess.DEVNULL, **kw)
    from ..workerd import code_build

    _children[p.pid] = {"proc": p, "role": role, "started": time.time(), "build": code_build()}
    log.info("started worker %s (%s)", p.pid, role)
    return p.pid


def _recover() -> None:
    """Expired leases: resume the job from its checkpoints, or give up after MAX_ATTEMPTS."""
    from ..models import Job, JobStatus
    from . import taskq

    res = taskq.reclaim_expired()
    for t in res["requeued"]:
        if t["kind"] != "job":
            continue
        with session_scope() as s:
            job = s.get(Job, t["job_id"])
            if job is not None and job.status == JobStatus.RUNNING:
                arts = dict(job.artifacts or {})
                arts["resume_pending"] = True             # generation keeps its selection and finished clips
                job.artifacts = arts
    for t in res["gave_up"]:
        if t["kind"] == "job":
            from ..worker import mark_needs_attention

            with session_scope() as s:
                job = s.get(Job, t["job_id"])
                stage = job.stage.value if job is not None and job.stage else ""
            mark_needs_attention(t["job_id"], stage, reason="interrupted")
        elif t["kind"] == "reexport":
            from ..api.routes_clips import fail_interrupted_reexport

            fail_interrupted_reexport(t["ref"])


def tick(build: str, *, spawn_missing: bool = True) -> dict[str, Any]:
    _heartbeat_web(build)
    _recover()
    started: list[int] = []
    if spawn_missing:
        live = _live(build)
        for role, n in wanted().items():
            for _ in range(max(0, n - live.get(role, 0))):
                if time.time() < _next_spawn.get(role, 0):
                    break
                try:
                    started.append(spawn(role))
                except OSError:
                    log.exception("worker could not be started")
                    _next_spawn[role] = time.time() + 30
                    break
    return {"started": started}


def _loop(build: str) -> None:
    from ..workerd import code_build
    from . import taskq

    last_prune, last_build = 0.0, time.time()
    while not _stop.is_set():
        try:
            if time.time() - last_build > 10:
                # the code on disk is the version workers must run (a git pull while running):
                # old workers finish their task and exit, new ones are counted against this build
                last_build, build = time.time(), code_build()
            tick(build)
            if time.time() - last_prune > 600:
                last_prune = time.time()
                taskq.prune()
        except Exception:                                 # noqa: BLE001 – the supervisor never dies
            log.warning("worker supervisor pass failed", exc_info=True)
        _stop.wait(TICK)


def start() -> bool:
    """In the web process, when worker processes run the work. True when started."""
    global _thread, _web_id
    from . import taskq

    if not taskq.process_mode() or os.environ.get("POLIXOR_IN_WORKER") == "1":
        return False
    if _thread is not None and _thread.is_alive():
        if not _stop.is_set():
            return True
        _thread.join(TICK * 3)                            # a stop that is still finishing its last pass
    from ..workerd import code_build

    _web_id = f"web:{taskq.owner_id()}"
    build = code_build()
    _stop.clear()
    tick(build, spawn_missing=False)                      # heartbeat + lease recovery before the first resume
    _thread = threading.Thread(target=_loop, args=(build,), name="polixor-workers", daemon=True)
    _thread.start()
    return True


def stop(*, terminate_children: bool = False) -> None:
    """A web stop leaves the workers working (they exit on their own when no web server returns)."""
    _stop.set()
    if _thread is not None and _thread.is_alive():
        _thread.join(TICK * 3)
    if terminate_children:
        _backoff.clear()
        _next_spawn.clear()
        for pid, ch in list(_children.items()):
            try:
                ch["proc"].terminate()
                ch["proc"].wait(30)
            except Exception:                             # noqa: BLE001
                try:
                    ch["proc"].kill()
                except OSError:
                    pass
            _children.pop(pid, None)


__all__ = ["start", "stop", "tick", "spawn", "wanted"]
